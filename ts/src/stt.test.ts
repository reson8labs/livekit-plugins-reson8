import { initializeLogger, log, stt } from '@livekit/agents';
import { AudioFrame } from '@livekit/rtc-node';
import { once } from 'node:events';
import type { AddressInfo } from 'node:net';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WebSocketServer, type WebSocket as ServerSocket } from 'ws';
import { STT } from './stt.js';

process.on('unhandledRejection', (reason) => {
  if (reason instanceof Error && reason.name.startsWith('API')) return;
  throw reason;
});

initializeLogger({ pretty: false, level: 'silent' });

const TEST_CONN = { maxRetry: 0, retryIntervalMs: 0, timeoutMs: 5_000 };

type WireEntry = { kind: 'audio'; bytes: number } | { kind: 'json'; data: unknown };

interface Handshake {
  url: string;
  authorization?: string;
}

async function startServer(hooks: { greet?: (ws: ServerSocket) => void } = {}) {
  const wss = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await once(wss, 'listening');
  const { port } = wss.address() as AddressInfo;

  const wire: WireEntry[] = [];
  const handshakes: Handshake[] = [];
  const ready: ServerSocket[] = [];
  let waiting: ((ws: ServerSocket) => void) | undefined;

  wss.on('connection', (ws, req) => {
    handshakes.push({ url: req.url ?? '', authorization: req.headers.authorization });
    ws.on('message', (data, isBinary) => {
      if (isBinary) wire.push({ kind: 'audio', bytes: (data as Buffer).byteLength });
      else wire.push({ kind: 'json', data: JSON.parse(data.toString()) });
    });

    hooks.greet?.(ws);
    if (waiting) {
      const resolve = waiting;
      waiting = undefined;
      resolve(ws);
    } else {
      ready.push(ws);
    }
  });

  return {
    apiUrl: `http://127.0.0.1:${port}`,
    wire,
    handshakes,
    jsonSent: () => wire.filter((e) => e.kind === 'json').map((e) => (e as { data: unknown }).data),
    socket: (): Promise<ServerSocket> =>
      ready.length > 0
        ? Promise.resolve(ready.shift()!)
        : new Promise((resolve) => {
            waiting = resolve;
          }),
    close: async () => {
      for (const client of wss.clients) client.terminate();
      wss.close();
      await once(wss, 'close');
    },
  };
}

type Server = Awaited<ReturnType<typeof startServer>>;

function send(ws: ServerSocket, payload: Record<string, unknown>): void {
  ws.send(JSON.stringify(payload));
}

function frame(samplesPerChannel = 1600, sampleRate = 16_000): AudioFrame {
  return new AudioFrame(new Int16Array(samplesPerChannel), sampleRate, 1, samplesPerChannel);
}

function expires(ms: number, describeFailure: () => string): Promise<never> {
  return new Promise((_, reject) => {
    setTimeout(() => reject(new Error(describeFailure())), ms).unref();
  });
}

async function collect(
  stream: AsyncIterableIterator<stt.SpeechEvent>,
  count: number,
): Promise<stt.SpeechEvent[]> {
  const events: stt.SpeechEvent[] = [];
  const timeout = expires(
    3_000,
    () => `expected ${count} events, got [${events.map((e) => e.type).join(', ')}]`,
  );
  while (events.length < count) {
    const result = await Promise.race([stream.next(), timeout]);
    if (result.done) break;
    events.push(result.value);
  }
  return events;
}

async function until(predicate: () => boolean, describeFailure: () => string): Promise<void> {
  const timeout = expires(3_000, describeFailure);
  while (!predicate()) {
    await Promise.race([new Promise((r) => setTimeout(r, 10).unref()), timeout]);
  }
}

const open: Array<{ close: () => void }> = [];
let server: Server | undefined;

afterEach(async () => {
  for (const stream of open.splice(0)) {
    try {
      stream.close();
    } catch {
      // already closed by the test itself
    }
  }
  await server?.close();
  server = undefined;
});

async function connectedStream(options: Record<string, unknown> = {}) {
  const active = await startServer();
  server = active;

  const client = new STT({ apiKey: 'test-key', apiUrl: active.apiUrl, ...options });
  const stream = client.stream({ connOptions: TEST_CONN });
  open.push(stream);

  const ws = await active.socket();
  return { server: active, client, stream, ws };
}

describe('handshake', () => {
  it('authenticates and carries the audio format as query params', async () => {
    const { server } = await connectedStream();

    expect(server.handshakes).toHaveLength(1);
    expect(server.handshakes[0]!.authorization).toBe('ApiKey test-key');

    const url = new URL(server.handshakes[0]!.url, 'http://127.0.0.1');
    expect(url.pathname).toBe('/v1/speech-to-text/turns');
    expect(url.searchParams.get('encoding')).toBe('pcm_s16le');
    expect(url.searchParams.get('sample_rate')).toBe('16000');
    expect(url.searchParams.get('channels')).toBe('1');
    expect(url.searchParams.has('include_words')).toBe(false);
    expect(url.searchParams.has('language')).toBe(false);
  });

  it('pins the language when one is given', async () => {
    const { server } = await connectedStream({ language: 'nl' });
    const url = new URL(server.handshakes[0]!.url, 'http://127.0.0.1');
    expect(url.searchParams.get('language')).toBe('nl');
  });
});

describe('flush', () => {
  it('sends a flush_request so the turn commits on demand', async () => {
    const { server, stream } = await connectedStream();

    stream.pushFrame(frame());
    stream.flush();

    await until(
      () => server.jsonSent().length > 0,
      () => `no control message reached the server; wire was ${JSON.stringify(server.wire)}`,
    );

    expect(server.jsonSent()).toEqual([{ type: 'flush_request' }]);
    expect(server.wire[0]!.kind).toBe('audio');
  });

  it('forwards audio as binary frames, never as JSON', async () => {
    const { server, stream } = await connectedStream();

    stream.pushFrame(frame(1600));
    await until(
      () => server.wire.length > 0,
      () => 'no audio reached the server',
    );

    expect(server.wire[0]).toEqual({ kind: 'audio', bytes: 3200 });
  });
});

describe('turn lifecycle', () => {
  it('promotes a confirmed candidate to a final transcript', async () => {
    const { stream, ws } = await connectedStream();

    send(ws, { type: 'turn_start' });
    send(ws, { type: 'turn_end_candidate', text: 'hello world' });
    send(ws, { type: 'turn_end' });

    const events = await collect(stream, 4);
    expect(events.map((e) => e.type)).toEqual([
      stt.SpeechEventType.START_OF_SPEECH,
      stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
      stt.SpeechEventType.FINAL_TRANSCRIPT,
      stt.SpeechEventType.END_OF_SPEECH,
    ]);
    expect(events[1]!.alternatives?.[0]!.text).toBe('hello world');
    expect(events[2]!.alternatives?.[0]!.text).toBe('hello world');
  });

  it('discards the candidate when the speaker resumes', async () => {
    const { stream, ws } = await connectedStream();

    send(ws, { type: 'turn_start' });
    send(ws, { type: 'turn_end_candidate', text: 'hello' });
    send(ws, { type: 'turn_continuation' });
    send(ws, { type: 'turn_end' });

    const events = await collect(stream, 3);
    expect(events.map((e) => e.type)).toEqual([
      stt.SpeechEventType.START_OF_SPEECH,
      stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
      stt.SpeechEventType.END_OF_SPEECH,
    ]);
  });

  it('opens the turn once even when several messages could open it', async () => {
    const { stream, ws } = await connectedStream();

    send(ws, { type: 'turn_start' });
    send(ws, { type: 'turn_end_candidate', text: 'a' });
    send(ws, { type: 'turn_end_candidate', text: 'a b' });
    send(ws, { type: 'turn_end' });

    const events = await collect(stream, 5);
    expect(events.filter((e) => e.type === stt.SpeechEventType.START_OF_SPEECH)).toHaveLength(1);
  });

  it('suppresses the preflight for an empty candidate, but still finalises it', async () => {
    const { stream, ws } = await connectedStream();

    send(ws, { type: 'turn_start' });
    send(ws, { type: 'turn_end_candidate', text: '' });
    send(ws, { type: 'turn_end' });

    const events = await collect(stream, 3);
    expect(events.map((e) => e.type)).toEqual([
      stt.SpeechEventType.START_OF_SPEECH,
      stt.SpeechEventType.FINAL_TRANSCRIPT,
      stt.SpeechEventType.END_OF_SPEECH,
    ]);
    expect(events[1]!.alternatives?.[0]!.text).toBe('');
  });

  it('ignores unparseable payloads and unknown message types', async () => {
    const { stream, ws } = await connectedStream();

    ws.send('not json at all');
    send(ws, { type: 'something_we_do_not_handle' });
    send(ws, { type: 'turn_start' });
    send(ws, { type: 'turn_end_candidate', text: 'still here' });
    send(ws, { type: 'turn_end' });

    const events = await collect(stream, 4);
    expect(events.map((e) => e.type)).toEqual([
      stt.SpeechEventType.START_OF_SPEECH,
      stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
      stt.SpeechEventType.FINAL_TRANSCRIPT,
      stt.SpeechEventType.END_OF_SPEECH,
    ]);
  });
});

describe('updateOptions', () => {
  it('redials when options change after the URL is built but before it opens', async () => {
    const seen: string[] = [];
    let release: (() => void) | undefined;
    const wss = new WebSocketServer({
      host: '127.0.0.1',
      port: 0,
      verifyClient: (info, done) => {
        seen.push(info.req.url ?? '');
        if (seen.length === 1) release = () => done(true);
        else done(true);
      },
    });
    await once(wss, 'listening');
    const { port } = wss.address() as AddressInfo;

    try {
      const client = new STT({
        apiKey: 'test-key',
        apiUrl: `http://127.0.0.1:${port}`,
        language: 'en',
      });
      const stream = client.stream({ connOptions: TEST_CONN });
      open.push(stream);

      await until(
        () => seen.length === 1 && release !== undefined,
        () => 'server never saw the first upgrade request',
      );
      client.updateOptions({ language: 'nl' });
      release!();

      await until(
        () => seen.length >= 2,
        () => `expected a redial, saw ${seen.length} upgrade request(s)`,
      );

      expect(new URL(seen[0]!, 'http://127.0.0.1').searchParams.get('language')).toBe('en');
      expect(new URL(seen[1]!, 'http://127.0.0.1').searchParams.get('language')).toBe('nl');
    } finally {
      for (const client of wss.clients) client.terminate();
      wss.close();
      await once(wss, 'close');
    }
  });

  it('does not redial a stream that has already been closed', async () => {
    const { server, client, stream } = await connectedStream({ language: 'en' });
    expect(server.handshakes).toHaveLength(1);

    stream.close();
    client.updateOptions({ language: 'nl' });

    await new Promise((resolve) => setTimeout(resolve, 150).unref());
    expect(server.handshakes).toHaveLength(1);
  });
});

describe('connection timing', () => {
  it('keeps messages the server sends the instant the socket opens', async () => {
    const active = await startServer({
      greet: (ws) => {
        send(ws, { type: 'turn_start' });
        send(ws, { type: 'turn_end_candidate', text: 'immediate' });
        send(ws, { type: 'turn_end' });
      },
    });
    server = active;

    const client = new STT({ apiKey: 'test-key', apiUrl: active.apiUrl });
    const stream = client.stream({ connOptions: TEST_CONN });
    open.push(stream);

    // Nothing is pushed from this side: the entire turn arrived before the
    // stream had sent a single byte of audio.
    const events = await collect(stream, 4);
    expect(events.map((e) => e.type)).toEqual([
      stt.SpeechEventType.START_OF_SPEECH,
      stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
      stt.SpeechEventType.FINAL_TRANSCRIPT,
      stt.SpeechEventType.END_OF_SPEECH,
    ]);
    expect(events[2]!.alternatives?.[0]!.text).toBe('immediate');
  });
});

describe('turn thresholds', () => {
  it('sends both thresholds as query params on the streaming endpoint', async () => {
    const { server } = await connectedStream({
      eagerTurnProbability: 0.35,
      finalTurnProbability: 0.7,
    });
    const url = new URL(server.handshakes[0]!.url, 'http://127.0.0.1');

    expect(url.searchParams.get('eager_turn_probability')).toBe('0.35');
    expect(url.searchParams.get('final_turn_probability')).toBe('0.7');
  });

  it('omits them when unset, leaving the server on its own defaults', async () => {
    const { server } = await connectedStream();
    const url = new URL(server.handshakes[0]!.url, 'http://127.0.0.1');

    expect(url.searchParams.has('eager_turn_probability')).toBe(false);
    expect(url.searchParams.has('final_turn_probability')).toBe(false);
  });

  it('keeps an integral threshold in float form on the wire', async () => {
    const { server } = await connectedStream({ finalTurnProbability: 1 });
    const url = new URL(server.handshakes[0]!.url, 'http://127.0.0.1');

    expect(url.searchParams.get('final_turn_probability')).toBe('1.0');
  });

  it('rejects a threshold outside 0-1', () => {
    for (const bad of [-0.1, 1.1, Number.NaN]) {
      expect(() => new STT({ apiKey: 'k', eagerTurnProbability: bad })).toThrow(
        /eagerTurnProbability must be between 0 and 1/,
      );
      expect(() => new STT({ apiKey: 'k', finalTurnProbability: bad })).toThrow(
        /finalTurnProbability must be between 0 and 1/,
      );
    }
  });

  it('rejects an eager threshold at or above final', () => {
    expect(() => new STT({ apiKey: 'k', eagerTurnProbability: 0.5, finalTurnProbability: 0.4 })).toThrow(
      /must be below finalTurnProbability/,
    );

    expect(() => new STT({ apiKey: 'k', eagerTurnProbability: 0.95 })).toThrow(
      /must be below finalTurnProbability \(0\.92\)/,
    );
  });

  it('warns, but does not throw, when the two thresholds are equal', () => {
    const warn = vi.spyOn(log(), 'warn').mockImplementation(() => log());
    try {
      expect(() => new STT({ apiKey: 'k', eagerTurnProbability: 0.5, finalTurnProbability: 0.5 })).not.toThrow();
      expect(() => new STT({ apiKey: 'k', finalTurnProbability: 0.5 })).not.toThrow();

      expect(warn).toHaveBeenCalledTimes(2);
      expect(warn.mock.calls[0]![0]).toMatch(/no lead time/);

      warn.mockClear();
      new STT({ apiKey: 'k' });
      new STT({ apiKey: 'k', eagerTurnProbability: 0.35, finalTurnProbability: 0.7 });

      expect(warn).not.toHaveBeenCalled();
    } finally {
      warn.mockRestore();
    }
  });

  it('does not leak thresholds onto the batch endpoint', async () => {
    const realFetch = globalThis.fetch;
    let seenUrl: string | undefined;
    globalThis.fetch = (async (input: Parameters<typeof fetch>[0]) => {
      seenUrl = String(input);
      return new Response(JSON.stringify({ text: 'batched' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }) as typeof fetch;

    try {
      const client = new STT({
        apiKey: 'k',
        eagerTurnProbability: 0.35,
        finalTurnProbability: 0.7,
        includeConfidence: true,
      });
      const event = await client.recognize(frame(1600));
      expect(event.alternatives?.[0]!.text).toBe('batched');

      const url = new URL(seenUrl!);
      expect(url.pathname).toBe('/v1/speech-to-text/prerecorded');
      expect(url.searchParams.has('eager_turn_probability')).toBe(false);
      expect(url.searchParams.has('final_turn_probability')).toBe(false);

      expect(url.searchParams.get('include_confidence')).toBe('true');
      expect(url.searchParams.has('include_language')).toBe(false);
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
