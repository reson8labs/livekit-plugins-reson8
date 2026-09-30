"""Minimal LLM/TTS/IO fakes for driving an AgentSession offline.

livekit/agents keeps equivalents (``fake_llm.py``, ``fake_tts.py``,
``fake_io.py``) in its own test suite, but those aren't shipped in the wheel,
so the small subset we need lives here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fake_reson8 import SAMPLE_RATE, silence_frame
from livekit.agents import llm, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.voice import io

from livekit import rtc

REPLY = "It is sunny in Amsterdam."


class _Stream(llm.LLMStream):
    async def _run(self) -> None:
        self._event_ch.send_nowait(
            llm.ChatChunk(id="fake", delta=llm.ChoiceDelta(role="assistant", content=REPLY))
        )


class FakeLLM(llm.LLM):
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **kwargs: Any,
    ) -> llm.LLMStream:
        return _Stream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class _Chunked(tts.ChunkedStream):
    async def _run(self, output_emitter: Any) -> None:
        output_emitter.initialize(
            request_id="fake",
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(b"\x00\x00" * SAMPLE_RATE)
        output_emitter.flush()


class FakeTTS(tts.TTS):
    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=1,
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return _Chunked(tts=self, input_text=text, conn_options=conn_options)


class SilenceInput(io.AudioInput):
    """A microphone that never stops streaming, like a real one."""

    def __init__(self) -> None:
        super().__init__(label="silence")

    async def __anext__(self) -> rtc.AudioFrame:
        await asyncio.sleep(0.02)
        return silence_frame()


class CaptureOutput(io.AudioOutput):
    """Counts the frames the agent actually spoke."""

    def __init__(self) -> None:
        super().__init__(
            label="capture",
            capabilities=io.AudioOutputCapabilities(pause=False),
            sample_rate=SAMPLE_RATE,
        )
        self.frames = 0

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        self.frames += 1

    def flush(self) -> None:
        super().flush()
        self.on_playback_finished(playback_position=1.0, interrupted=False)

    def clear_buffer(self) -> None:
        pass
