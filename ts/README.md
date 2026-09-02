# @reson8-labs/agents-plugin-reson8

[Reson8](https://reson8.dev) STT plugin for
[LiveKit Agents for Node.js](https://github.com/livekit/agents-js).

A single `reson8.STT` class that adapts to how LiveKit uses it:

- **Streaming** (`stream()`, used by voice agents) connects to the turn-aware
  endpoint. Reson8 detects conversational turn boundaries server-side: it emits
  a *preflight* transcript (`PREFLIGHT_TRANSCRIPT` — an eager guess that the turn
  is over) that your agent can start responding to, then confirms it as a final
  transcript — or cancels it if the speaker keeps talking. Great for low-latency
  voice agents.
- **Batch** (`recognize()`) transcribes pre-recorded audio and returns the full
  transcript.

## Any language

Reson8 supports **any language**. Leave `language` unset to **auto-detect** the
spoken language, or pass any language code (e.g. `"en"`, `"nl"`, `"es"`,
`"de"`, `"fr"`, ...) to pin it.

```ts
new reson8.STT();                  // auto-detects the spoken language
new reson8.STT({ language: 'en' }); // English
new reson8.STT({ language: 'es' }); // Spanish
new reson8.STT({ language: 'nl' }); // Dutch
```

## Installation

```bash
npm install @reson8-labs/agents-plugin-reson8
# peer dependencies (provided by your agent app):
npm install @livekit/agents @livekit/rtc-node
```

## Usage

```ts
import * as reson8 from '@reson8-labs/agents-plugin-reson8';

const stt = new reson8.STT({
  apiKey: 'your-api-key', // or set RESON8_API_KEY
  // language is auto-detected when omitted; pass any code to pin it
});
```

### With a Voice Agent

```ts
import { voice } from '@livekit/agents';
import * as openai from '@livekit/agents-plugin-openai';
import * as reson8 from '@reson8-labs/agents-plugin-reson8';

const session = new voice.AgentSession({
  stt: new reson8.STT(), // streaming + turn detection, any language
  llm: new openai.LLM(),
  tts: new openai.TTS(),
});
```

### Transcribing a file

```ts
const event = await new reson8.STT().recognize(audioBuffer);
console.log(event.alternatives[0].text);
```

## Configuration

| Option | Env var | Default |
|---|---|---|
| `apiKey` | `RESON8_API_KEY` | *required* |
| `apiUrl` | `RESON8_API_URL` | `https://api.reson8.dev` |
| `language` | — | `null` (auto-detect; accepts any language code) |
| `sampleRate` | — | `16000` |
| `encoding` | — | `"pcm_s16le"` |
| `channels` | — | `1` |
| `customModelId` | — | `null` (custom model for recognition biasing) |
| `includeTimestamps` | — | `false` |
| `includeWords` | — | `false` |
| `includeConfidence` | — | `false` (batch recognition) |
| `includeLanguage` | — | `false` (report detected language while streaming) |

`STT.updateOptions({ ... })` changes settings at runtime; active streaming
sessions reconnect automatically to apply them.

## Development

```bash
npm install
npm run typecheck
npm run build
```
