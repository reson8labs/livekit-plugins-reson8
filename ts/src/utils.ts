import { createTimedString, type stt } from '@livekit/agents';

export const DEFAULT_API_URL = 'https://api.reson8.dev';

/** Convert an http(s) API base URL into its ws(s) equivalent. */
export function toWsBase(apiUrl: string): string {
  return apiUrl
    .replace(/\/+$/, '')
    .replace(/^https:\/\//, 'wss://')
    .replace(/^http:\/\//, 'ws://');
}

export function authHeaders(apiKey: string): Record<string, string> {
  return { Authorization: `ApiKey ${apiKey}` };
}

/** A single word in a Reson8 transcript/turn payload. */
export interface Reson8Word {
  text?: string;
  start_ms?: number;
  duration_ms?: number;
  confidence?: number | null;
}

/** A Reson8 transcript or turn message. */
export interface Reson8Transcript {
  type?: string;
  text?: string;
  language?: string | null;
  start_ms?: number | null;
  duration_ms?: number | null;
  words?: Reson8Word[];
}

/**
 * Reson8 returns confidence as a natural log-probability (<= 0). Convert it to
 * a probability in (0, 1] for LiveKit's confidence fields.
 */
function toProbability(logProb: number | null | undefined): number | undefined {
  if (logProb === undefined || logProb === null) return undefined;
  const p = Math.exp(logProb);
  return Number.isFinite(p) ? p : 1.0;
}

function wordTime(word: Reson8Word, key: 'start' | 'end', offset: number): number | undefined {
  if (word.start_ms === undefined || word.start_ms === null) return undefined;
  const start = word.start_ms;
  if (key === 'start') return offset + start / 1000;
  return offset + (start + (word.duration_ms ?? 0)) / 1000;
}

/**
 * Build a LiveKit {@link stt.SpeechData} from a Reson8 transcript/turn payload.
 *
 * Handles the optional `start_ms`/`duration_ms`/`words` fields that are only
 * present when the matching `include*` options are enabled.
 */
export function buildSpeechData(
  msg: Reson8Transcript,
  { language, startTimeOffset = 0 }: { language?: string | null; startTimeOffset?: number },
): stt.SpeechData {
  const rawWords = msg.words ?? [];
  const words = rawWords.map((w) =>
    createTimedString({
      text: w.text ?? '',
      startTime: wordTime(w, 'start', startTimeOffset),
      endTime: wordTime(w, 'end', startTimeOffset),
      confidence: toProbability(w.confidence),
      startTimeOffset,
    }),
  );

  const wordProbs = rawWords
    .filter((w) => w.confidence !== undefined && w.confidence !== null)
    .map((w) => toProbability(w.confidence))
    .filter((p): p is number => p !== undefined);
  const confidence =
    wordProbs.length > 0 ? wordProbs.reduce((a, b) => a + b, 0) / wordProbs.length : 1.0;

  let startTime = startTimeOffset;
  let endTime = startTimeOffset;
  if (msg.start_ms !== undefined && msg.start_ms !== null) {
    startTime = startTimeOffset + msg.start_ms / 1000;
    endTime = startTimeOffset + (msg.start_ms + (msg.duration_ms ?? 0)) / 1000;
  }

  return {
    language: (msg.language || language || '') as stt.SpeechData['language'],
    text: msg.text ?? '',
    startTime,
    endTime,
    confidence,
    words,
  };
}
