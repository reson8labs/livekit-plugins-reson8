# livekit-plugins-reson8

[Reson8](https://reson8.dev) STT plugin for [LiveKit Agents](https://github.com/livekit/agents).

A single `reson8.STT` class that adapts to how LiveKit uses it:

- **Streaming** (`stream()`, used by voice agents) connects to the turn-aware
  endpoint. Reson8 detects conversational turn boundaries server-side: it emits
  a *preflight* transcript (an eager guess that the turn is over) that your agent
  can start responding to. A later guess replaces it, and the last one becomes
  the final transcript when the turn ends. Great for low-latency voice agents.
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

Supported languages (`reson8.SupportedLanguage`, or `reson8.SUPPORTED_LANGUAGES` at runtime):

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

stt = reson8.STT(
    language="nl",
    turn=reson8.TurnOptions(final_probability=0.7),
    transcript=reson8.TranscriptOptions(words=True, language=True),
    biasing=reson8.BiasingOptions(custom_model_id="my-model"),
)
```

### With a Voice Agent

```python
from livekit.agents import AgentSession
from livekit.plugins import openai, reson8

session = AgentSession(
    stt=reson8.STT(),          # streaming + turn detection, language auto-detected
    llm=openai.LLM(),
    tts=openai.TTS(),
    # "stt" hands turn detection to Reson8 and lets the agent start
    # generating on our preflight transcript instead of the confirmation.
    turn_handling={
        "turn_detection": "stt",
        "preemptive_generation": {"enabled": True},
    },
)
```

By default LiveKit runs its own turn detector. Set `turn_handling` as above to
hand turn-taking to Reson8 instead.

### Transcribing a file

```python
event = await reson8.STT().recognize(audio_buffer)
print(event.alternatives[0].text)
```

## Configuration

| Parameter | Env var | Default |
|---|---|---|
| `api_key` | `RESON8_API_KEY` | *required* |
| `base_url` | `RESON8_BASE_URL` | `https://api.reson8.dev` |
| `language` | — | `None` (auto-detect; one or more `SupportedLanguage` codes, e.g. `"nl,de"` or `["nl", "de"]`) |
| `turn` | — | `TurnOptions()` |
| `audio` | — | `AudioOptions()` |
| `transcript` | — | `TranscriptOptions()` |
| `biasing` | — | `BiasingOptions()` |
| `http_session` | — | the session managed by the agent framework |

### `TurnOptions`

The main lever on end-of-turn latency. `None` leaves the server's default.

| Field | Default | |
|---|---|---|
| `eager_probability` | `None` (server: `0.5`) | confidence at which the preflight transcript is emitted |
| `final_probability` | `None` (server: `0.92`) | confidence at which the turn commits |
| `report_probabilities` | `False` | report the end-of-turn probability as it is evaluated, on `SpeechData.metadata` |

`report_probabilities` is how you pick the two thresholds from real audio
rather than by guesswork: each transcript carries the most recent reading.

```python
stt = reson8.STT(turn=reson8.TurnOptions(report_probabilities=True))

# on a transcript event
event.alternatives[0].metadata
# {"probability": 0.63, "raw_eot_probability": 0.58, "vad_probability": 0.91, "timestamp_ms": 1200}
```

### `AudioOptions`

Describes the audio sent to Reson8; it does not convert it. LiveKit supplies
16-bit PCM, so `encoding` should stay at its default unless the frames you push
really are something else.

| Field | Default | |
|---|---|---|
| `sample_rate` | `16000` | streaming input is resampled to this |
| `encoding` | `"pcm_s16le"` | one of `pcm_s16le`, `mulaw`, `alaw` |
| `num_channels` | `1` | 1 to 10 |

### `TranscriptOptions`

| Field | Default | |
|---|---|---|
| `words` | `False` | word-level results, each with its own timing |
| `language` | `False` | the detected language code |
| `confidence` | `False` | per-word confidence, batch recognition only |
| `filler_mode` | `None` (server: `natural`) | `clean` removes filler words, `natural` lets the model decide, `verbatim` preserves them |

### `BiasingOptions`

Use `phrases` for a handful of terms on a single request, a `custom_model_id`
for a vocabulary that is larger or reused across requests, and `patterns` for
structured tokens whose shape you know up front.

Biasing is not free: phrases and patterns can *degrade* transcription of audio
that does not contain them, and stronger biasing introduces irrelevant terms.

| Field | Default | |
|---|---|---|
| `custom_model_id` | `None` | a custom model to bias toward, for a vocabulary too large for `phrases` or reused across requests |
| `phrases` | `None` | terms to bias toward, at most 250; needs no custom model |
| `strength` | `None` (server: `0.45`) | additive boost on the model's trained calibration. Raise only when expected terminology is not being recovered |
| `patterns` | `None` | shapes for short alphanumeric tokens to recover, e.g. `"AMZ[0-9]{6}"` or `"[0-9]{4,6}"` |

```python
# bias toward vocabulary the model would otherwise miss
stt = reson8.STT(biasing=reson8.BiasingOptions(phrases=["Reson8", "LiveKit"]))

# or recover a structured token, so its digits are not heard as words
stt = reson8.STT(biasing=reson8.BiasingOptions(patterns=["AMZ[0-9]{6}", "[0-9]{4,6}"]))
```

See [custom models](https://docs.reson8.dev/speech-to-text/features/custom-models/)
and [patterns](https://docs.reson8.dev/speech-to-text/features/patterns/).

`STT.update_options(...)` takes the same sections and changes them at runtime;
active streaming sessions reconnect automatically to apply them. `AudioOptions`
is fixed for the life of a stream, since the input resampler is built when the
stream opens.

## Turn detection

Reson8 decides turn boundaries by confidence: it emits the preflight transcript
at `turn.eager_probability` and commits the turn at `turn.final_probability`.
Lower `final_probability` to commit sooner, at the risk of cutting off longer
utterances.

`flush()` commits the current turn immediately, keeping `final_probability`
intact. LiveKit never calls it for you.

See [Turns](https://docs.reson8.dev/speech-to-text/turns/) for how
turn events work server-side.

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
