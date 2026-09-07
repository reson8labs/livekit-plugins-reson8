from __future__ import annotations

import aiohttp
from conftest import EventLog, StartServer
from livekit.agents import stt
from livekit.agents.types import APIConnectOptions

from livekit import rtc
from livekit.plugins import reson8

SpeechEventType = stt.SpeechEventType
NO_RETRY = APIConnectOptions(max_retry=0)

# 0.1s of 16kHz mono audio
FRAME = rtc.AudioFrame(
    data=b"\x00\x00" * 1600,
    sample_rate=16000,
    num_channels=1,
    samples_per_channel=1600,
)


async def test_turn_lifecycle_over_the_wire(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    """A whole turn, in the order the agent session depends on.

    FINAL must land before END_OF_SPEECH, because in ``turn_detection="stt"``
    mode END_OF_SPEECH is what commits the user turn, and the transcript has to
    be accumulated by then.
    """

    server = await reson8_server()
    stream = reson8.STT(
        api_key="k",
        base_url=server.api_url,
        language="en",
        http_session=client_session,
    ).stream(conn_options=NO_RETRY)
    log = EventLog(stream)

    stream.push_frame(FRAME)
    stream.flush()
    await server.wait_for_text()

    await server.send({"type": "turn_start"})
    await server.send({"type": "turn_end_candidate", "text": "hello world"})
    await server.send({"type": "turn_end"})

    try:
        events = await log.wait_for(5)
        assert [e.type for e in events] == [
            SpeechEventType.START_OF_SPEECH,
            SpeechEventType.PREFLIGHT_TRANSCRIPT,
            SpeechEventType.FINAL_TRANSCRIPT,
            SpeechEventType.END_OF_SPEECH,
            SpeechEventType.RECOGNITION_USAGE,
        ]

        assert events[1].alternatives[0].text == "hello world"
        assert events[2].alternatives[0].text == "hello world"
        assert events[2].alternatives[0].language == "en"

        usage = events[4].recognition_usage
        assert usage is not None
        assert usage.audio_duration == 0.1
    finally:
        await log.aclose()
        await stream.aclose()


async def test_usage_is_not_reported_without_audio(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    """A turn that carried no audio must not emit a zero-duration usage event."""

    server = await reson8_server()
    stream = reson8.STT(api_key="k", base_url=server.api_url, http_session=client_session).stream(
        conn_options=NO_RETRY
    )
    log = EventLog(stream)

    await server.connected.wait()
    await server.send({"type": "turn_start"})
    await server.send({"type": "turn_end_candidate", "text": "hi"})
    await server.send({"type": "turn_end"})

    try:
        events = await log.wait_for(4)
        assert SpeechEventType.RECOGNITION_USAGE not in [e.type for e in events]
    finally:
        await log.aclose()
        await stream.aclose()


async def test_reconnect_discards_in_flight_turn_state(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    """
    A reconnect must not carry turn state across connections.

    ``update_options`` redials, and the new session starts with no turn in
    progress: a leftover candidate would be promoted by an unrelated turn_end,
    and a leftover speaking flag would swallow the next START_OF_SPEECH.
    """

    server = await reson8_server()
    stt_impl = reson8.STT(api_key="k", base_url=server.api_url, http_session=client_session)
    stream = stt_impl.stream(conn_options=NO_RETRY)
    log = EventLog(stream)

    await server.connected.wait()
    await server.send({"type": "turn_start"})
    await server.send({"type": "turn_end_candidate", "text": "stale text"})
    opening = await log.wait_for(2)
    assert opening[1].type == SpeechEventType.PREFLIGHT_TRANSCRIPT

    stream.update_options(transcript=reson8.TranscriptOptions(words=True))
    await server.wait_for_connections(2)

    try:
        await server.send({"type": "turn_end"})
        await log.assert_quiet()

        await server.send({"type": "turn_start"})
        await server.send({"type": "turn_end_candidate", "text": "fresh text"})
        await server.send({"type": "turn_end"})

        events = await log.wait_for(6)
        assert [e.type for e in events[2:]] == [
            SpeechEventType.START_OF_SPEECH,
            SpeechEventType.PREFLIGHT_TRANSCRIPT,
            SpeechEventType.FINAL_TRANSCRIPT,
            SpeechEventType.END_OF_SPEECH,
        ]
        assert events[4].alternatives[0].text == "fresh text"
    finally:
        await log.aclose()
        await stream.aclose()
