"""Turn lifecycle over a real websocket.

The other stream tests drive ``_process_message`` directly, which leaves the
transport untested: URL and query-string construction, auth headers, and audio
upload. These run the real ``SpeechStream`` against a stand-in server, so a
regression in any of those fails here instead of at a customer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fake_reson8 import FINAL_TEXT, FULL_TURN, silence_frame, stand_in
from livekit.agents import stt

from livekit.plugins import reson8

SpeechEventType = stt.SpeechEventType


async def run_stream(url: str, done: asyncio.Event, **kwargs: Any) -> list[stt.SpeechEvent]:
    stream = reson8.STT(api_key="test-key", api_url=url, **kwargs).stream()

    async def push() -> None:
        # A real agent streams continuously; ending input early would close the
        # socket before the server finishes the turn.
        while not done.is_set():
            stream.push_frame(silence_frame())
            await asyncio.sleep(0.01)
        stream.end_input()

    pusher = asyncio.create_task(push())
    events: list[stt.SpeechEvent] = []
    try:
        async with asyncio.timeout(15):
            async for event in stream:
                events.append(event)
    finally:
        pusher.cancel()
        await stream.aclose()
    return events


async def test_turn_lifecycle_over_websocket():
    async with stand_in(FULL_TURN) as (url, _, done):
        events = await run_stream(url, done, language="en")

    assert [e.type for e in events] == [
        SpeechEventType.START_OF_SPEECH,
        SpeechEventType.PREFLIGHT_TRANSCRIPT,
        SpeechEventType.PREFLIGHT_TRANSCRIPT,
        SpeechEventType.FINAL_TRANSCRIPT,
        SpeechEventType.END_OF_SPEECH,
    ]


async def test_continuation_supersedes_the_earlier_candidate():
    async with stand_in(FULL_TURN) as (url, _, done):
        events = await run_stream(url, done, language="en")

    finals = [e for e in events if e.type == SpeechEventType.FINAL_TRANSCRIPT]
    assert [e.alternatives[0].text for e in finals] == [FINAL_TEXT]


async def test_request_carries_auth_and_options():
    async with stand_in(FULL_TURN) as (url, recorded, done):
        await run_stream(url, done, language="en", custom_model_id="model-123")

    assert recorded.path.startswith("/v1/speech-to-text/turns?")
    assert recorded.auth == "ApiKey test-key"
    for expected in ("language=en", "custom_model_id=model-123", "encoding=pcm_s16le"):
        assert expected in recorded.path


async def test_audio_frames_reach_the_server():
    async with stand_in(FULL_TURN) as (url, recorded, done):
        await run_stream(url, done, language="en")

    assert recorded.audio_bytes > 0
    assert recorded.audio_bytes % 2 == 0  # pcm_s16le


async def test_connection_error_surfaces_as_api_error():
    from livekit.agents import APIConnectionError
    from livekit.agents.types import APIConnectOptions

    # nothing is listening on this port
    stream = reson8.STT(api_key="k", api_url="http://127.0.0.1:1").stream(
        conn_options=APIConnectOptions(max_retry=0)
    )
    with pytest.raises(APIConnectionError):
        async with asyncio.timeout(15):
            async for _ in stream:
                pass
    await stream.aclose()
