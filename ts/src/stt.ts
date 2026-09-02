import {
  APIConnectionError,
  APIStatusError,
  APITimeoutError,
  type APIConnectOptions,
  type AudioBuffer,
  log,
  mergeFrames,
  stt,
} from '@livekit/agents';
import type { AudioFrame } from '@livekit/rtc-node';
import { randomUUID } from 'node:crypto';
import { WebSocket, type RawData } from 'ws';
import {
  DEFAULT_API_URL,
  authHeaders,
  integrationHeaders,
  buildSpeechData,
  type Reson8Transcript,
  toWsBase,
} from './utils.js';

const SERVER_DEFAULT_EAGER_TURN_PROBABILITY = 0.5;
const SERVER_DEFAULT_FINAL_TURN_PROBABILITY = 0.92;

/** Reson8's two turn-boundary confidence thresholds. */
export interface TurnOptions {
  /**
   * Confidence (0-1) at which the preflight transcript is emitted. Server
   * default `0.5`.
   */
  eagerTurnProbability?: number;
  /**
   * Confidence (0-1) at which the turn commits. Server default `0.92`, tuned
   * for conversational speech; a one-word confirmation can take over a second
   * to cross it.
   */
  finalTurnProbability?: number;
}

function checkProbability(name: string, value: number | undefined): void {
  if (value === undefined) return;
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`${name} must be between 0 and 1, got ${value}`);
  }
}

function validateTurnOptions(turn: TurnOptions): void {
  checkProbability('eagerTurnProbability', turn.eagerTurnProbability);
  checkProbability('finalTurnProbability', turn.finalTurnProbability);

  const eager = turn.eagerTurnProbability ?? SERVER_DEFAULT_EAGER_TURN_PROBABILITY;
  const final = turn.finalTurnProbability ?? SERVER_DEFAULT_FINAL_TURN_PROBABILITY;

  if (eager > final) {
    throw new Error(
      `eagerTurnProbability (${eager}) must be below finalTurnProbability (${final}); ` +
        'raise finalTurnProbability or lower eagerTurnProbability',
    );
  }
  if (eager === final) {
    log().warn(
      `eagerTurnProbability and finalTurnProbability are both ${final}, so the turn commits ` +
        'on the same event as the preflight transcript and preemptive generation gets no lead time',
    );
  }
}

function formatProbability(value: number): string {
  return Number.isInteger(value) ? value.toFixed(1) : String(value);
}

/** Resolved Reson8 STT options shared by the {@link STT} and {@link SpeechStream}. */
export interface STTOptions {
  language: string | null;
  sampleRate: number;
  encoding: string;
  channels: number;
  customModelId: string | null;
  includeTimestamps: boolean;
  includeWords: boolean;
  includeConfidence: boolean;
  includeLanguage: boolean;
  turn: TurnOptions;
}

/** Options accepted by the {@link STT} constructor. */
export interface STTConstructorOptions {
  /** Reson8 API key. Falls back to the `RESON8_API_KEY` env var. */
  apiKey?: string;
  /** Reson8 API base URL. Falls back to `RESON8_API_URL` or `https://api.reson8.dev`. */
  apiUrl?: string;
  /**
   * Any language code (e.g. `"en"`, `"nl"`, `"es"`, ...). Leave unset to
   * auto-detect the spoken language.
   */
  language?: string | null;
  /** Input sample rate in Hz. */
  sampleRate?: number;
  /** Audio encoding sent to Reson8. */
  encoding?: string;
  /** Number of audio channels. */
  channels?: number;
  /** Optional custom model id used to bias recognition. */
  customModelId?: string | null;
  /** Include `start`/`end` times on results. */
  includeTimestamps?: boolean;
  /** Include word-level results. */
  includeWords?: boolean;
  /** Include confidence scores (batch recognition). */
  includeConfidence?: boolean;
  /** Report the detected language code (streaming). */
  includeLanguage?: boolean;
  /**
   * Confidence (0-1) at which the preflight transcript is emitted. Server
   * default `0.5`. Must be below {@link finalTurnProbability}.
   */
  eagerTurnProbability?: number;
  /**
   * Confidence (0-1) at which the turn commits. Server default `0.92`. Lower it
   * to commit sooner, at the risk of cutting off longer utterances.
   */
  finalTurnProbability?: number;
}

/** Options that may be changed at runtime via `updateOptions`. */
export type UpdatableOptions = Partial<
  Pick<
    STTConstructorOptions,
    | 'language'
    | 'customModelId'
    | 'includeTimestamps'
    | 'includeWords'
    | 'includeConfidence'
    | 'includeLanguage'
  >
>;

function queryParams(opts: STTOptions, { streaming }: { streaming: boolean }): URLSearchParams {
  const params = new URLSearchParams({
    encoding: opts.encoding,
    sample_rate: String(opts.sampleRate),
    channels: String(opts.channels),
  });
  // Any language is supported. When omitted, Reson8 auto-detects the spoken
  // language; otherwise this pins recognition to the given code.
  if (opts.language) params.set('language', opts.language);
  if (opts.customModelId) params.set('custom_model_id', opts.customModelId);
  if (opts.includeTimestamps) params.set('include_timestamps', 'true');
  if (opts.includeWords) params.set('include_words', 'true');
  if (streaming) {
    if (opts.includeLanguage) params.set('include_language', 'true');

    const { eagerTurnProbability, finalTurnProbability } = opts.turn;
    if (eagerTurnProbability !== undefined) {
      params.set('eager_turn_probability', formatProbability(eagerTurnProbability));
    }
    if (finalTurnProbability !== undefined) {
      params.set('final_turn_probability', formatProbability(finalTurnProbability));
    }
  } else if (opts.includeConfidence) {
    params.set('include_confidence', 'true');
  }
  return params;
}

/**
 * Reson8 speech-to-text.
 *
 * A single model that adapts to how LiveKit uses it:
 *
 * - **Streaming** ({@link stream}) connects to the turn-aware
 *   `/v1/speech-to-text/turns` endpoint. Reson8 detects conversational turn
 *   boundaries server-side and emits a turn-end *candidate* once it believes a
 *   turn is complete. That candidate surfaces as a preflight transcript the
 *   agent can act on speculatively, and is then either confirmed as a final
 *   transcript or cancelled when the speaker keeps talking. Ideal for
 *   low-latency voice agents.
 * - **Batch** ({@link STT.recognize}) sends pre-recorded audio to
 *   `/v1/speech-to-text/prerecorded` and returns the full transcript.
 *
 * Any language is supported. Leave `language` unset (the default) to
 * auto-detect the spoken language, or pass any language code to pin it.
 */
export class STT extends stt.STT {
  label = 'reson8.STT';

  readonly #apiKey: string;
  readonly #apiUrl: string;
  readonly #opts: STTOptions;
  readonly #streams = new Set<WeakRef<SpeechStream>>();

  constructor(opts: STTConstructorOptions = {}) {
    super({ streaming: true, interimResults: true });

    const apiKey = opts.apiKey ?? process.env.RESON8_API_KEY;
    if (!apiKey) {
      throw new Error(
        'Reson8 API key is required, either as argument or RESON8_API_KEY env var',
      );
    }
    this.#apiKey = apiKey;
    this.#apiUrl = (opts.apiUrl ?? process.env.RESON8_API_URL ?? DEFAULT_API_URL).replace(
      /\/+$/,
      '',
    );
    this.#opts = {
      language: opts.language ?? null,
      sampleRate: opts.sampleRate ?? 16000,
      encoding: opts.encoding ?? 'pcm_s16le',
      channels: opts.channels ?? 1,
      customModelId: opts.customModelId ?? null,
      includeTimestamps: opts.includeTimestamps ?? false,
      includeWords: opts.includeWords ?? false,
      includeConfidence: opts.includeConfidence ?? false,
      includeLanguage: opts.includeLanguage ?? false,
      turn: {
        ...(opts.eagerTurnProbability !== undefined && {
          eagerTurnProbability: opts.eagerTurnProbability,
        }),
        ...(opts.finalTurnProbability !== undefined && {
          finalTurnProbability: opts.finalTurnProbability,
        }),
      },
    };

    validateTurnOptions(this.#opts.turn);
  }

  override get model(): string {
    return this.#opts.customModelId ?? 'default';
  }

  override get provider(): string {
    return 'reson8';
  }

  updateOptions(opts: UpdatableOptions): void {
    if (opts.language !== undefined) this.#opts.language = opts.language;
    if (opts.customModelId !== undefined) this.#opts.customModelId = opts.customModelId;
    if (opts.includeTimestamps !== undefined) this.#opts.includeTimestamps = opts.includeTimestamps;
    if (opts.includeWords !== undefined) this.#opts.includeWords = opts.includeWords;
    if (opts.includeConfidence !== undefined) this.#opts.includeConfidence = opts.includeConfidence;
    if (opts.includeLanguage !== undefined) this.#opts.includeLanguage = opts.includeLanguage;

    for (const ref of this.#streams) {
      const stream = ref.deref();
      if (stream === undefined) {
        this.#streams.delete(ref);
        continue;
      }
      stream.updateOptions(opts);
    }
  }

  stream(options?: { language?: string; connOptions?: APIConnectOptions }): SpeechStream {
    const opts: STTOptions = { ...this.#opts, turn: { ...this.#opts.turn } };
    if (options?.language !== undefined) opts.language = options.language;
    const stream = new SpeechStream(
      this,
      opts,
      this.#apiKey,
      this.#apiUrl,
      options?.connOptions,
    );
    for (const ref of this.#streams) {
      if (ref.deref() === undefined) this.#streams.delete(ref);
    }
    this.#streams.add(new WeakRef(stream));
    return stream;
  }

  protected async _recognize(
    buffer: AudioBuffer,
    abortSignal?: AbortSignal,
  ): Promise<stt.SpeechEvent> {
    const language = this.#opts.language;
    const frame = mergeFrames(buffer);
    const opts: STTOptions = {
      ...this.#opts,
      language,
      encoding: 'pcm_s16le',
      sampleRate: frame.sampleRate,
      channels: frame.channels,
    };

    const url = `${this.#apiUrl}/v1/speech-to-text/prerecorded?${queryParams(opts, {
      streaming: false,
    })}`;

    let body: Reson8Transcript;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: {
          ...authHeaders(this.#apiKey),
          ...integrationHeaders(),
          'Content-Type': 'application/octet-stream',
        },
        body: new Uint8Array(frame.data.buffer, frame.data.byteOffset, frame.data.byteLength),
        signal: abortSignal,
      });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        throw new APIStatusError({
          message: text || resp.statusText,
          options: { statusCode: resp.status },
        });
      }
      body = (await resp.json()) as Reson8Transcript;
    } catch (e) {
      if (e instanceof APIStatusError) throw e;
      const name = (e as { name?: string }).name;
      if (name === 'AbortError' || name === 'TimeoutError') {
        throw new APITimeoutError({});
      }
      throw new APIConnectionError({ options: { retryable: true } });
    }

    return {
      type: stt.SpeechEventType.FINAL_TRANSCRIPT,
      requestId: randomUUID(),
      alternatives: [buildSpeechData(body, { language })],
    };
  }
}

const ABORTED = Symbol('aborted');

/** Turn-aware streaming session against the `/turns` endpoint. */
export class SpeechStream extends stt.SpeechStream {
  label = 'reson8.SpeechStream';

  readonly #opts: STTOptions;
  readonly #apiKey: string;
  readonly #apiUrl: string;
  readonly #requestId = randomUUID();

  #speaking = false;
  /**
   * The most recent turn-end candidate, promoted to a final transcript once the
   * server confirms the turn ended.
   */
  #candidate: stt.SpeechData | null = null;
  #connAbort: AbortController | null = null;
  #reconnectRequested = false;

  constructor(
    stt: STT,
    opts: STTOptions,
    apiKey: string,
    apiUrl: string,
    connOptions?: APIConnectOptions,
  ) {
    super(stt, opts.sampleRate, connOptions);
    this.#opts = opts;
    this.#apiKey = apiKey;
    this.#apiUrl = apiUrl;
  }

  updateOptions(opts: UpdatableOptions): void {
    if (this.closed) return;
    if (opts.language !== undefined) this.#opts.language = opts.language;
    if (opts.customModelId !== undefined) this.#opts.customModelId = opts.customModelId;
    if (opts.includeTimestamps !== undefined) this.#opts.includeTimestamps = opts.includeTimestamps;
    if (opts.includeWords !== undefined) this.#opts.includeWords = opts.includeWords;
    if (opts.includeLanguage !== undefined) this.#opts.includeLanguage = opts.includeLanguage;

    this.#reconnectRequested = true;
    this.#connAbort?.abort();
  }

  #buildUrl(): string {
    const base = toWsBase(this.#apiUrl);
    return `${base}/v1/speech-to-text/turns?${queryParams(this.#opts, { streaming: true })}`;
  }

  readonly #onMessage = (raw: RawData, isBinary: boolean): void => {
    if (isBinary) return;

    let msg: Reson8Transcript;
    try {
      msg = JSON.parse(raw.toString()) as Reson8Transcript;
    } catch {
      return;
    }

    this.#processMessage(msg);
  };

  async #connect(): Promise<WebSocket> {
    const ws = new WebSocket(this.#buildUrl(), {
      headers: { ...authHeaders(this.#apiKey), ...integrationHeaders() },
    });
    ws.on('message', this.#onMessage);
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => {
        ws.off('open', onOpen);
        ws.off('error', onError);
      };
      const onOpen = () => {
        cleanup();
        resolve();
      };
      const onError = (err: Error) => {
        cleanup();
        reject(err);
      };
      ws.once('open', onOpen);
      ws.once('error', onError);
    });
    return ws;
  }

  protected async run(): Promise<void> {
    while (!this.input.closed) {
      let ws: WebSocket;
      this.#reconnectRequested = false;
      try {
        ws = await this.#connect();
      } catch {
        throw new APIConnectionError({
          message: 'failed to connect to Reson8',
          options: { retryable: true },
        });
      }

      const connAbort = new AbortController();
      this.#connAbort = connAbort;
      if (this.#reconnectRequested) {
        this.#connAbort = null;
        ws.off('message', this.#onMessage);
        ws.close();
        continue;
      }
      let closing = false;
      let unexpected = false;

      ws.once('close', () => {
        if (!closing) unexpected = true;
        connAbort.abort();
      });
      ws.once('error', () => {
        if (!closing) unexpected = true;
        connAbort.abort();
      });

      const abortPromise = new Promise<typeof ABORTED>((resolve) => {
        if (connAbort.signal.aborted) resolve(ABORTED);
        else connAbort.signal.addEventListener('abort', () => resolve(ABORTED), { once: true });
      });

      while (true) {
        const result = await Promise.race([this.input.next(), abortPromise]);
        if (result === ABORTED) break;
        const { done, value } = result as IteratorResult<
          AudioFrame | typeof SpeechStream.FLUSH_SENTINEL
        >;
        if (done) {
          closing = true;
          break;
        }
        if (value === SpeechStream.FLUSH_SENTINEL) {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'flush_request' }));
          }
          continue;
        }
        if (ws.readyState === WebSocket.OPEN) {
          const frame = value;
          ws.send(new Uint8Array(frame.data.buffer, frame.data.byteOffset, frame.data.byteLength));
        }
      }

      ws.off('message', this.#onMessage);
      this.#connAbort = null;

      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }

      if (closing) break;
      if (this.#reconnectRequested) continue;
      throw new APIConnectionError({
        message: unexpected
          ? 'Reson8 connection closed unexpectedly'
          : 'Reson8 connection ended unexpectedly',
        options: { retryable: true },
      });
    }
  }

  #processMessage(msg: Reson8Transcript): void {
    switch (msg.type) {
      case 'turn_start':
        this.#candidate = null;
        this.#startSpeaking();
        break;

      case 'turn_end_candidate': {
        // Eager end-of-turn: surface as a preflight transcript that the agent
        // can act on speculatively before the turn is confirmed.
        this.#startSpeaking();
        const candidate = buildSpeechData(msg, {
          language: this.#opts.language,
          startTimeOffset: this.startTimeOffset,
        });
        this.#candidate = candidate;
        if (candidate.text) {
          this.queue.put({
            type: stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
            requestId: this.#requestId,
            alternatives: [candidate],
          });
        }
        break;
      }

      case 'turn_continuation':
        // The speaker resumed: the previous candidate is no longer final.
        this.#candidate = null;
        break;

      case 'turn_end': {
        const candidate = this.#candidate;
        this.#candidate = null;
        if (candidate !== null) {
          this.queue.put({
            type: stt.SpeechEventType.FINAL_TRANSCRIPT,
            requestId: this.#requestId,
            alternatives: [candidate],
          });
        }
        if (this.#speaking) {
          this.#speaking = false;
          this.queue.put({ type: stt.SpeechEventType.END_OF_SPEECH });
        }
        break;
      }
    }
  }

  #startSpeaking(): void {
    if (this.#speaking) return;
    this.#speaking = true;
    this.queue.put({ type: stt.SpeechEventType.START_OF_SPEECH });
  }
}
