import { describe, expect, it } from 'vitest';
import { buildSpeechData, wordConfidence } from './utils.js';

describe('wordConfidence', () => {
  it('passes through the documented range', () => {
    expect(wordConfidence({ confidence: 0.99 })).toBeCloseTo(0.99);
  });

  it('is undefined when missing', () => {
    expect(wordConfidence({ text: 'hi' })).toBeUndefined();
    expect(wordConfidence({ text: 'hi', confidence: null })).toBeUndefined();
  });

  it('clamps above the range', () => {
    expect(wordConfidence({ confidence: 1.5 })).toBe(1.0);
  });

  it.each([0, -0.5, Number.NaN])('is undefined for non-positive %s', (value) => {
    expect(wordConfidence({ confidence: value })).toBeUndefined();
  });
});

describe('buildSpeechData confidence', () => {
  it('is the mean of word probabilities', () => {
    const data = buildSpeechData(
      {
        text: 'hi there',
        words: [
          { text: 'hi', confidence: 0.99 },
          { text: 'there', confidence: 0.97 },
        ],
      },
      { language: 'en' },
    );
    expect(data.confidence).toBeCloseTo(0.98);
  });

  it('carries word confidence and timings', () => {
    const data = buildSpeechData(
      { text: 'hi', words: [{ text: 'hi', start_ms: 0, duration_ms: 200, confidence: 0.9 }] },
      { language: 'en', startTimeOffset: 1.0 },
    );
    const word = data.words![0]!;
    expect(word.confidence).toBeCloseTo(0.9);
    expect(word.startTime).toBeCloseTo(1.0);
    expect(word.endTime).toBeCloseTo(1.2);
  });

  it('defaults to 1.0 without word confidences', () => {
    const data = buildSpeechData({ text: 'hi', words: [{ text: 'hi' }] }, { language: 'en' });
    expect(data.confidence).toBe(1.0);
  });
});
