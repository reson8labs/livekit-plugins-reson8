from __future__ import annotations

import pytest
from livekit.agents import stt

from livekit.plugins.reson8.stt import SpeechStream, STTOptions


def _make_opts(**overrides: object) -> STTOptions:
    defaults: dict[str, object] = {
        "language": None,
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "channels": 1,
        "custom_model_id": None,
        "include_timestamps": False,
        "include_words": False,
        "include_confidence": False,
        "include_language": False,
    }
    defaults.update(overrides)
    return STTOptions(**defaults)  # type: ignore[arg-type]


class FakeChan:
    """Minimal stand-in for the stream's event channel that records events."""

    def __init__(self) -> None:
        self.events: list[stt.SpeechEvent] = []

    def send_nowait(self, event: stt.SpeechEvent) -> None:
        self.events.append(event)


@pytest.fixture
def make_opts():
    return _make_opts


@pytest.fixture
def make_stream():
    """Build a SpeechStream without running its base __init__.

    The real ``SpeechStream.__init__`` (via ``RecognizeStream``) spawns
    background asyncio tasks that open a websocket. The turn state machine in
    ``_process_message`` is synchronous and only touches a handful of
    attributes, so we bypass __init__ and set just those — keeping these tests
    network-free and loop-free.
    """

    def _make(**overrides: object) -> SpeechStream:
        stream = SpeechStream.__new__(SpeechStream)
        stream._opts = _make_opts(**overrides)
        stream._request_id = "req-test"
        stream._speaking = False
        stream._candidate = None
        stream._start_time_offset = 0.0
        stream._event_ch = FakeChan()  # type: ignore[assignment]
        return stream

    return _make
