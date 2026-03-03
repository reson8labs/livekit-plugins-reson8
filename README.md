# livekit-plugins-reson8

[Reson8](https://reson8.dev) STT plugin for [LiveKit Agents](https://github.com/livekit/agents).

## Installation

```bash
pip install livekit-plugins-reson8
```

## Usage

```python
from livekit.plugins import reson8

stt = reson8.STT(
    api_key="your-api-key",   # or set RESON8_API_KEY
    language="nl",
)
```

### With a Voice Agent

```python
from livekit.agents.voice import VoiceAgent
from livekit.plugins import openai, reson8

agent = VoiceAgent(
    stt=reson8.STT(language="nl"),
    llm=openai.LLM(),
    tts=openai.TTS(),
)
```

## Configuration

| Parameter | Env var | Default |
|---|---|---|
| `api_key` | `RESON8_API_KEY` | *required* |
| `api_url` | `RESON8_API_URL` | `https://api.reson8.dev` |
| `language` | — | `nl` |
| `phrases` | — | `[]` |
| `bias_strength` | — | `1.0` |
| `sample_rate` | — | `16000` |
| `include_timestamps` | — | `False` |
| `include_words` | — | `False` |
| `include_confidence` | — | `False` |

## Running the example

```bash
cp .env.example .env
# Fill in your keys
python examples/voice_agent.py dev
```
