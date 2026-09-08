from __future__ import annotations

import asyncio
from typing import NoReturn

import aiohttp
import pytest
from conftest import StartServer
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError
from livekit.agents.types import APIConnectOptions

from livekit import rtc
from livekit.plugins import reson8
from livekit.plugins.reson8._utils import problem_message, status_error

NO_RETRY = APIConnectOptions(max_retry=0)


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
def test_problem_message(body: str, expected: str | None) -> None:
    assert problem_message(body) == expected


def test_status_error_explains_credit_exhaustion() -> None:
    err = status_error(402)
    assert isinstance(err, APIStatusError)
    assert err.status_code == 402
    assert "https://docs.reson8.dev/limits/" in err.message


def test_status_error_keeps_the_server_reason_and_the_hint() -> None:
    err = status_error(401, detail="invalid signature")
    assert "invalid signature" in err.message
    assert "RESON8_API_KEY" in err.message


def test_status_error_renders_unmapped_statuses() -> None:
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
def test_status_error_retryability(status_code: int, retryable: bool) -> None:
    assert status_error(status_code).retryable is retryable


def _frame() -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * 160,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=160,
    )


def _stt(api_url: str, session: aiohttp.ClientSession) -> reson8.STT:
    return reson8.STT(api_key="secret", base_url=api_url, http_session=session)


async def test_rejected_upgrade_surfaces_the_status(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server(ws_status=402, ws_error_message="organization out of credits")
    stream = _stt(server.api_url, client_session).stream(conn_options=NO_RETRY)

    with pytest.raises(APIStatusError) as excinfo:
        await stream._run()

    err = excinfo.value
    assert err.status_code == 402
    assert err.retryable is False
    assert "organization out of credits" in err.message
    await stream.aclose()


async def test_rejected_upgrade_without_a_reason_still_explains_itself(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server(ws_status=401)
    stream = _stt(server.api_url, client_session).stream(conn_options=NO_RETRY)

    with pytest.raises(APIStatusError) as excinfo:
        await stream._run()

    assert "RESON8_API_KEY" in excinfo.value.message
    await stream.aclose()


async def test_unreachable_host_is_a_connection_error(
    client_session: aiohttp.ClientSession,
) -> None:
    stream = _stt("http://127.0.0.1:1", client_session).stream(conn_options=NO_RETRY)

    with pytest.raises(APIConnectionError, match="Failed to connect to Reson8"):
        await stream._run()

    await stream.aclose()


async def test_rejected_batch_request_reports_the_problem_message(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server(post_status=402, post_body='{"code": "session_rejected"}')

    with pytest.raises(APIStatusError) as excinfo:
        await _stt(server.api_url, client_session).recognize(_frame(), conn_options=NO_RETRY)

    err = excinfo.value
    assert err.status_code == 402
    assert err.retryable is False
    assert "session_rejected" in err.message
    assert "https://docs.reson8.dev/limits/" in err.message


async def test_rejected_batch_request_without_a_body(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server(post_status=413, post_body="")

    with pytest.raises(APIStatusError) as excinfo:
        await _stt(server.api_url, client_session).recognize(_frame(), conn_options=NO_RETRY)

    assert "exceeds the size limit" in excinfo.value.message


async def test_batch_timeout_is_a_timeout_error(
    reson8_server: StartServer,
    client_session: aiohttp.ClientSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = await reson8_server()

    def timing_out_post(*args: object, **kwargs: object) -> NoReturn:
        raise asyncio.TimeoutError

    monkeypatch.setattr(client_session, "post", timing_out_post)

    with pytest.raises(APITimeoutError):
        await _stt(server.api_url, client_session).recognize(_frame(), conn_options=NO_RETRY)


def test_problem_message_surfaces_the_detail() -> None:
    body = '{"title": "Invalid Query Parameter", "status": 400, '
    body += '"detail": "channels must be between 1 and 10, got: 11", '
    body += '"code": "invalid_query_parameter"}'

    assert problem_message(body) == (
        "invalid_query_parameter: channels must be between 1 and 10, got: 11"
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"code": "session_rejected"}', "session_rejected"),
        ('{"detail": "Credit limit exceeded"}', "Credit limit exceeded"),
        ("", None),
        ("not json", None),
        ("[]", None),
        ("{}", None),
        ('{"code": 402, "detail": null}', None),
    ],
)
def test_problem_message_handles_partial_bodies(body: str, expected: str | None) -> None:
    assert problem_message(body) == expected


async def test_a_rejected_request_reports_which_parameter_was_wrong(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server(
        post_status=400,
        post_body='{"code": "invalid_query_parameter", "detail": "Invalid encoding: mp3"}',
    )

    with pytest.raises(APIStatusError) as excinfo:
        await _stt(server.api_url, client_session).recognize(_frame(), conn_options=NO_RETRY)

    assert "Invalid encoding: mp3" in excinfo.value.message
