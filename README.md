# livekit-plugins-reson8

[Reson8](https://reson8.dev) STT plugin for [LiveKit Agents](https://github.com/livekit/agents).

A single `reson8.STT` class that adapts to how LiveKit uses it:

- **Streaming** (`stream()`, used by voice agents) connects to the turn-aware
  endpoint. Reson8 detects conversational turn boundaries server-side: it emits
  a *preflight* transcript (an eager guess that the turn is over) that your agent
  can start responding to, then confirms it as a final transcript — or cancels it
  if the speaker keeps talking. Great for low-latency voice agents.
- **Batch** (`recognize()`) transcribes pre-recorded audio and returns the full
  transcript.

## Languages

Leave `language` unset to **auto-detect** the spoken language, or pin recognition
to one or more supported codes. You can pass a single code, a comma-string, or a
list — a list is normalized to Reson8's comma-joined form and any unsupported code
raises `ValueError` locally.

```python
reson8.STT()                        # auto-detects the spoken language
reson8.STT(language="en")           # English only
reson8.STT(language="nl,de")        # Dutch or German
reson8.STT(language=["nl", "de"])   # same, as a list
```

Supported languages (`reson8.SupportedLanguages`):

| Code | Language |
|---|---|
| `de` | German |
| `en` | English |
| `es` | Spanish |
| `fr` | French |
| `fy` | Frisian |
| `it` | Italian |
| `nl` | Dutch |
| `pl` | Polish |
| `pt` | Portuguese |
| `sv` | Swedish |

## Installation

```bash
pip install livekit-plugins-reson8
```

## Usage

```python
from livekit.plugins import reson8

stt = reson8.STT(
    api_key="your-api-key",   # or set RESON8_API_KEY
    # language is auto-detected when omitted; pass one or more supported codes to pin it
)
```

### With a Voice Agent

```python
from livekit.agents.voice import VoiceAgent
from livekit.plugins import openai, reson8

agent = VoiceAgent(
    stt=reson8.STT(),          # streaming + turn detection, language auto-detected
    llm=openai.LLM(),
    tts=openai.TTS(),
)
```

### Transcribing a file

```python
event = await reson8.STT().recognize(audio_buffer)
print(event.alternatives[0].text)
```

## Configuration

| Parameter | Env var | Default |
|---|---|---|
| `api_key` | `RESON8_API_KEY` | *required* |
| `api_url` | `RESON8_API_URL` | `https://api.reson8.dev` |
| `language` | — | `None` (auto-detect; one or more of `SupportedLanguages`, e.g. `"nl,de"` or `["nl", "de"]`) |
| `sample_rate` | — | `16000` |
| `encoding` | — | `"pcm_s16le"` |
| `channels` | — | `1` |
| `custom_model_id` | — | `None` (custom model for recognition biasing) |
| `include_timestamps` | — | `False` |
| `include_words` | — | `False` |
| `include_confidence` | — | `False` (batch recognition) |
| `include_language` | — | `False` (report detected language while streaming) |

`STT.update_options(...)` changes settings at runtime; active streaming sessions
reconnect automatically to apply them.

## Running the example

```bash
cp .env.example .env
# Fill in your keys
python examples/voice_agent.py dev
```

## Development

We use [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage
the environment and dev dependencies, so you'll need to have it installed.

`uv sync` sets everything up — it reads the pinned Python  version from
`.python-version`, creates a `.venv/`, and installs the dev tools (Ruff and mypy).

Run the tooling through `uv run`, which uses the project environment without you
having to activate anything:

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # type-check
```
