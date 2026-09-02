from __future__ import annotations

from typing import Any

import pytest
from livekit.agents import APIConnectionError

from livekit import rtc
from livekit.plugins.reson8 import STT, __version__
from livekit.plugins.reson8 import stt as stt_module
from livekit.plugins.reson8._utils import INTEGRATION_HEADER, integration_headers

EXPECTED = f"livekit-python:{__version__}"


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"text": "hello"}


@pytest.fixture
def captured_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Stand in for ``httpx.AsyncClient`` and return the headers it was handed."""

    captured: dict[str, str] = {}

    class _RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _RecordingClient:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def post(self, url: str, *, headers: dict[str, str], **kwargs: Any) -> _FakeResponse:
            captured.update(headers)
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _RecordingClient)
    return captured


def test_integration_headers_names_the_plugin_and_its_version():
    assert integration_headers() == {INTEGRATION_HEADER: EXPECTED}


async def test_prerecorded_request_is_attributed(captured_headers):
    stt = STT(api_key="secret")
    frame = rtc.AudioFrame(
        data=b"\x00\x00" * 160,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=160,
    )

    await stt._recognize_impl(frame)

    assert captured_headers[INTEGRATION_HEADER] == EXPECTED
    assert captured_headers["Authorization"] == "ApiKey secret"
    assert captured_headers["Content-Type"] == "application/octet-stream"


async def test_turns_handshake_is_attributed(make_stream, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    async def fake_connect(url: str, *, additional_headers: dict[str, str]) -> None:
        captured.update(additional_headers)
        raise OSError("no network in tests")

    monkeypatch.setattr(stt_module.websockets, "connect", fake_connect)

    stream = make_stream()
    stream._api_key = "secret"
    stream._api_url = "https://api.reson8.dev"

    with pytest.raises(APIConnectionError):
        await stream._run()

    assert captured[INTEGRATION_HEADER] == EXPECTED
    assert captured["Authorization"] == "ApiKey secret"
