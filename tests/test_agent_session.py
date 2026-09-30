"""Our turn detection driving a real AgentSession.

The wire tests stop at SpeechEvents. These go one layer up: a full
``AgentSession`` with fake LLM/TTS, fed by the stand-in Reson8 server, asserting
that a committed turn actually produces a spoken reply.

``turn_detection="stt"`` is what routes our preflight into livekit's preemptive
generation. Without it livekit runs its own turn detector and our signal is
silently ignored, so it is asserted here rather than left to the docs.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fake_agent import REPLY, CaptureOutput, FakeLLM, FakeTTS, SilenceInput
from fake_reson8 import FINAL_TEXT, FULL_TURN, stand_in
from livekit.agents import Agent, AgentSession

from livekit.plugins import reson8

TURN_HANDLING: dict[str, Any] = {
    "turn_detection": "stt",
    "preemptive_generation": {"enabled": True},
}


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="You are a helpful assistant.")


class Result:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.replies: list[str] = []
        self.session: AgentSession | None = None
        self.output = CaptureOutput()


async def run_session(url: str, done: asyncio.Event, **session_kwargs: Any) -> Result:
    result = Result()
    session = AgentSession(
        stt=reson8.STT(api_key="test-key", api_url=url, language="en"),
        llm=FakeLLM(),
        tts=FakeTTS(),
        turn_handling=TURN_HANDLING,
        **session_kwargs,
    )
    result.session = session

    session.on(
        "user_input_transcribed",
        lambda ev: result.transcripts.append(ev.transcript) if ev.is_final else None,
    )
    session.on(
        "conversation_item_added",
        lambda ev: (
            result.replies.append(ev.item.text_content or "")
            if ev.item.role == "assistant"
            else None
        ),
    )

    await session.start(agent=Assistant())
    session.input.audio = SilenceInput()
    session.output.audio = result.output

    try:
        async with asyncio.timeout(20):
            await done.wait()
            while not result.replies:
                await asyncio.sleep(0.05)
    finally:
        await session.aclose()

    return result


async def test_turn_drives_the_agent():
    async with stand_in(FULL_TURN) as (url, _, done):
        result = await run_session(url, done)

    assert result.session is not None
    assert result.session.turn_detection == "stt"
    assert result.transcripts == [FINAL_TEXT]
    assert REPLY in result.replies[0]
    assert result.output.frames > 0


async def test_runs_without_a_vad_model():
    """Reson8 detects turns server-side, so a VAD is not required."""
    async with stand_in(FULL_TURN) as (url, _, done):
        result = await run_session(url, done, vad=None)

    assert result.session is not None
    assert result.session.vad is None
    assert result.transcripts == [FINAL_TEXT]
    assert REPLY in result.replies[0]
