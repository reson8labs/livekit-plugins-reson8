# @reson8-labs/agents-plugin-reson8

[Reson8](https://reson8.dev) STT plugin for
[LiveKit Agents for Node.js](https://github.com/livekit/agents-js).

A single `reson8.STT` class that adapts to how LiveKit uses it:

- **Streaming** (`stream()`, used by voice agents) connects to the turn-aware
  [Turns](https://docs.reson8.dev/speech-to-text/turns/) endpoint. Reson8
  detects conversational turn boundaries server-side: it emits a *preflight*
  transcript (`PREFLIGHT_TRANSCRIPT` — an eager guess that the turn is over)
  that your agent can start responding to, then confirms it as a final
  transcript — or cancels it if the speaker keeps talking. Great for
  low-latency voice agents.
- **Batch** (`recognize()`) transcribes
  [pre-recorded](https://docs.reson8.dev/speech-to-text/prerecorded/) audio and
  returns the full transcript.

## Model

Use `rumble-2`, Reson8's latest model:

```ts
new reson8.STT({ model: 'rumble-2' });
```
See [Models](https://docs.reson8.dev/speech-to-text/models/).

## Languages

`rumble-2` supports `af` `ar` `az` `be` `bg` `bn` `bs` `ca` `ceb` `cs` `da` `de`
`el` `en` `es` `et` `eu` `fa` `fi` `fil` `fr` `fy` `gl` `hi` `hr` `hu` `id` `is`
`it` `ja` `jv` `ko` `lt` `lv` `mk` `mr` `ms` `mt` `nb` `nl` `pl` `pt` `ro` `ru`
`sk` `sl` `sq` `sr` `sv` `ta` `th` `tr` `uk` `vi` `yue` `zh` — see
[Languages](https://docs.reson8.dev/speech-to-text/features/languages/).

Leave `language` unset to auto-detect each utterance, or pin a code for better
quality. A comma-separated list restricts auto-detection to those languages.

```ts
new reson8.STT({ model: 'rumble-2' });                    // auto-detect
new reson8.STT({ model: 'rumble-2', language: 'nl' });    // Dutch
new reson8.STT({ model: 'rumble-2', language: 'nl,en' }); // auto-detect, restricted to these
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
  model: 'rumble-2',
  // language is auto-detected when omitted; pass a supported code to pin it
});
```

### With a Voice Agent

```ts
import { voice } from '@livekit/agents';
import * as openai from '@livekit/agents-plugin-openai';
import * as reson8 from '@reson8-labs/agents-plugin-reson8';

const session = new voice.AgentSession({
  stt: new reson8.STT({ model: 'rumble-2' }), // streaming + server-side turn detection
  llm: new openai.LLM(),
  tts: new openai.TTS(),
});
```

### Transcribing a file

```ts
const event = await new reson8.STT({ model: 'rumble-2' }).recognize(audioBuffer);
console.log(event.alternatives[0].text);
```

## Configuration

| Option | Env var | Default |
|---|---|---|
| `apiKey` | `RESON8_API_KEY` | *required* |
| `apiUrl` | `RESON8_API_URL` | `https://api.reson8.dev` |
| `model` | — | `null` (server default; `"rumble-2"`, see [Model](#model)) |
| `language` | — | `null` (auto-detect; one or more [supported codes](https://docs.reson8.dev/speech-to-text/features/languages/), e.g. `"nl"` or `"nl,en"`) |
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
