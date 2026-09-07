from __future__ import annotations

from typing import Any

import httpx
import pytest
import websockets
from livekit.agents import APIConnectionError, APIStatusError
from websockets.datastructures import Headers
from websockets.http11 import Response

from livekit import rtc
from livekit.plugins.reson8 import STT
from livekit.plugins.reson8 import stt as stt_module
from livekit.plugins.reson8._utils import ERROR_MESSAGE_HEADER, problem_code, status_error


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"code": "session_rejected"}', "session_rejected"),
        ('{"code": "unauthorized", "title": "Unauthorized"}', "unauthorized"),
        ("", None),
        ("not json", None),
        ("[]", None),
        ('{"title": "no code here"}', None),
        ('{"code": 402}', None),
    ],
)
def test_problem_code(body, expected):
    assert problem_code(body) == expected


def test_status_error_explains_credit_exhaustion():
    err = status_error(402)
    assert isinstance(err, APIStatusError)
    assert err.status_code == 402
    assert "Credit limit exceeded" in err.message
    assert "https://docs.reson8.dev/limits/" in err.message


def test_status_error_keeps_the_server_reason_and_the_hint():
    err = status_error(401, detail="invalid signature")
    assert "invalid signature" in err.message
    assert "RESON8_API_KEY" in err.message


def test_status_error_renders_unmapped_statuses():
    err = status_error(500)
    assert err.status_code == 500
    assert "Internal Server Error" in err.message


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [
        (401, False),  # a bad key will keep being a bad key
        (402, False),  # so will an empty balance
        (400, False),
        (429, True),  # concurrency frees up
        (500, True),
    ],
)
def test_status_error_retryability(status_code, retryable):
    assert status_error(status_code).retryable is retryable


def _reject_upgrade(status_code: int, reason: str | None) -> Any:
    headers = Headers()
    if reason is not None:
        headers[ERROR_MESSAGE_HEADER] = reason

    async def fake_connect(url: str, **kwargs: Any) -> None:
        raise websockets.InvalidStatus(Response(status_code, "", headers, b""))

    return fake_connect


async def test_rejected_upgrade_surfaces_the_status(make_stream, monkeypatch):
    monkeypatch.setattr(
        stt_module.websockets, "connect", _reject_upgrade(402, "organization out of credits")
    )

    stream = make_stream()
    stream._api_key = "secret"
    stream._api_url = "https://api.reson8.dev"

    with pytest.raises(APIStatusError) as excinfo:
        await stream._run()

    err = excinfo.value
    assert err.status_code == 402
    assert err.retryable is False
    assert "organization out of credits" in err.message


async def test_rejected_upgrade_without_a_reason_still_explains_itself(make_stream, monkeypatch):
    monkeypatch.setattr(stt_module.websockets, "connect", _reject_upgrade(401, None))

    stream = make_stream()
    stream._api_key = "secret"
    stream._api_url = "https://api.reson8.dev"

    with pytest.raises(APIStatusError) as excinfo:
        await stream._run()

    assert "RESON8_API_KEY" in excinfo.value.message


async def test_unreachable_host_is_a_connection_error(make_stream, monkeypatch):
    async def fake_connect(url: str, **kwargs: Any) -> None:
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr(stt_module.websockets, "connect", fake_connect)

    stream = make_stream()
    stream._api_key = "secret"
    stream._api_url = "https://api.reson8.dev"

    with pytest.raises(APIConnectionError, match="OSError"):
        await stream._run()


@pytest.fixture
def reject_post(monkeypatch: pytest.MonkeyPatch):
    """Stand in for ``httpx.AsyncClient`` and fail the request with a real response."""

    def _install(status_code: int, body: str) -> None:
        class _RejectingClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _RejectingClient:
                return self

            async def __aexit__(self, *exc: Any) -> bool:
                return False

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                return httpx.Response(
                    status_code,
                    text=body,
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr("httpx.AsyncClient", _RejectingClient)

    return _install


def _frame() -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * 160,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=160,
    )


async def test_rejected_batch_request_reports_the_problem_code(reject_post):
    reject_post(402, '{"code": "session_rejected"}')

    with pytest.raises(APIStatusError) as excinfo:
        await STT(api_key="secret")._recognize_impl(_frame())

    err = excinfo.value
    assert err.status_code == 402
    assert err.retryable is False
    assert "session_rejected" in err.message
    assert "https://docs.reson8.dev/limits/" in err.message


async def test_rejected_batch_request_without_a_body(reject_post):
    reject_post(413, "")

    with pytest.raises(APIStatusError) as excinfo:
        await STT(api_key="secret")._recognize_impl(_frame())

    assert "exceeds the size limit" in excinfo.value.message


async def test_batch_timeout_is_a_timeout_error(monkeypatch: pytest.MonkeyPatch):
    class _TimingOutClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _TimingOutClient:
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def post(self, url: str, **kwargs: Any) -> None:
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("httpx.AsyncClient", _TimingOutClient)

    with pytest.raises(stt_module.APITimeoutError):
        await STT(api_key="secret")._recognize_impl(_frame())
