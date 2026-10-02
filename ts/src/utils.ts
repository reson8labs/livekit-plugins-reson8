import { createTimedString, type stt } from '@livekit/agents';
import { version } from './version.js';

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

export const INTEGRATION_HEADER = 'X-Reson8-Integration';
export function integrationHeaders(): Record<string, string> {
  return { [INTEGRATION_HEADER]: `livekit-js:${version}` };
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
 * Reson8 reports word confidence as a probability in (0, 1].
 *
 * See https://docs.reson8.dev/glossary/.
 */
export function wordConfidence(word: Reson8Word): number | undefined {
  const confidence = word.confidence;

  if (confidence === undefined || confidence === null || !(confidence > 0)) return undefined;

  return Math.min(confidence, 1.0);
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
  const confidences = rawWords.map(wordConfidence);
  const words = rawWords.map((w, i) =>
    createTimedString({
      text: w.text ?? '',
      startTime: wordTime(w, 'start', startTimeOffset),
      endTime: wordTime(w, 'end', startTimeOffset),
      confidence: confidences[i],
      startTimeOffset,
    }),
  );

  const known = confidences.filter((c): c is number => c !== undefined);
  const confidence = known.length > 0 ? known.reduce((a, b) => a + b, 0) / known.length : 1.0;

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
