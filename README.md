# livekit-plugins-reson8

[Reson8](https://reson8.dev) STT plugin for [LiveKit Agents](https://github.com/livekit/agents),
available for both Python and Node.js / TypeScript.

A single `STT` class that adapts to how LiveKit uses it:

- **Streaming** (used by voice agents) connects to the turn-aware endpoint.
  Reson8 detects conversational turn boundaries server-side: it emits a
  *preflight* transcript (an eager guess that the turn is over) that your agent
  can start responding to, then confirms it as a final transcript — or cancels
  it if the speaker keeps talking. Great for low-latency voice agents.
- **Batch** transcribes pre-recorded audio and returns the full transcript.

**Any language** is supported. Leave `language` unset to auto-detect the spoken
language, or pass any language code (e.g. `"en"`, `"nl"`, `"es"`, ...) to pin it.

## Packages

| Language | Directory | Package |
|---|---|---|
| Python | [`python/`](python/) | `livekit-plugins-reson8` |
| TypeScript | [`ts/`](ts/) | `@reson8/agents-plugin-reson8` |

Both packages implement the same Reson8 protocol and read the same
`RESON8_API_KEY` / `RESON8_API_URL` environment variables. See each directory's
README for installation and usage.
