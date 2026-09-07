from __future__ import annotations

import aiohttp
from conftest import StartServer
from livekit.agents.types import APIConnectOptions

from livekit import rtc
from livekit.plugins import reson8
from livekit.plugins.reson8 import __version__
from livekit.plugins.reson8._utils import INTEGRATION_HEADER, integration_headers

EXPECTED = f"livekit-python:{__version__}"

NO_RETRY = APIConnectOptions(max_retry=0)


def test_integration_headers_names_the_plugin_and_its_version() -> None:
    assert integration_headers() == {INTEGRATION_HEADER: EXPECTED}


async def test_prerecorded_request_is_attributed(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server()
    stt = reson8.STT(api_key="secret", base_url=server.api_url, http_session=client_session)

    frame = rtc.AudioFrame(
        data=b"\x00\x00" * 160,
        sample_rate=16000,
        num_channels=1,
        samples_per_channel=160,
    )
    await stt.recognize(frame, conn_options=NO_RETRY)

    assert server.post_headers[INTEGRATION_HEADER] == EXPECTED
    assert server.post_headers["Authorization"] == "ApiKey secret"
    assert server.post_headers["Content-Type"] == "application/octet-stream"


async def test_turns_handshake_is_attributed(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    server = await reson8_server()
    stt = reson8.STT(api_key="secret", base_url=server.api_url, http_session=client_session)

    stream = stt.stream(conn_options=NO_RETRY)
    try:
        await server.connected.wait()
    finally:
        await stream.aclose()

    assert server.handshake_headers[INTEGRATION_HEADER] == EXPECTED
    assert server.handshake_headers["Authorization"] == "ApiKey secret"
