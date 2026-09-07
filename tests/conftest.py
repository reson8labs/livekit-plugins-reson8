from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, cast

import aiohttp
import pytest
from aiohttp import web
from livekit.agents import stt

from livekit.plugins.reson8._utils import ERROR_MESSAGE_HEADER, PRERECORDED_PATH, TURNS_PATH
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


class EventLog:
    """Drains a live stream in the background and records what it emitted."""

    def __init__(self, stream: SpeechStream) -> None:
        self.events: list[stt.SpeechEvent] = []
        self._stream = stream
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for event in self._stream:
            self.events.append(event)

    async def wait_for(self, count: int, timeout: float = 5.0) -> list[stt.SpeechEvent]:
        async def _poll() -> None:
            while len(self.events) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=timeout)
        return self.events

    async def assert_quiet(self, seconds: float = 0.5) -> None:
        """Nothing new arrives in the given window."""

        before = len(self.events)
        await asyncio.sleep(seconds)
        assert len(self.events) == before, f"unexpected events: {self.events[before:]}"

    async def aclose(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task


def emitted(stream: SpeechStream) -> list[stt.SpeechEvent]:
    """The events a ``make_stream()`` stream has sent so far."""
    return cast(FakeChan, stream._event_ch).events


class MakeOpts(Protocol):
    def __call__(self, **overrides: object) -> STTOptions: ...


class MakeStream(Protocol):
    def __call__(self, **overrides: object) -> SpeechStream: ...


@pytest.fixture
def make_opts() -> MakeOpts:
    return _make_opts


@pytest.fixture
def make_stream() -> MakeStream:
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
        stream._speech_duration = 0.0
        stream._event_ch = FakeChan()  # type: ignore[assignment]
        return stream

    return _make


@dataclass
class Recorded:
    """What a fake Reson8 server saw, and how to talk back."""

    api_url: str
    handshake_headers: dict[str, str] = field(default_factory=dict)
    post_headers: dict[str, str] = field(default_factory=dict)
    post_body: bytes = b""
    query: dict[str, str] = field(default_factory=dict)
    audio: list[bytes] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    _ws: web.WebSocketResponse | None = None
    connected: asyncio.Event = field(default_factory=asyncio.Event)
    connections: int = 0

    async def send(self, message: object) -> None:
        """Push a server -> client message once a stream has connected."""

        await asyncio.wait_for(self.connected.wait(), timeout=5)
        assert self._ws is not None
        await self._ws.send_str(json.dumps(message))

    async def wait_for_connections(self, count: int) -> None:
        async def _poll() -> None:
            while self.connections < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=5)

    async def wait_for_text(self, count: int = 1) -> None:
        async def _poll() -> None:
            while len(self.text) < count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_poll(), timeout=5)


class StartServer(Protocol):
    """The callable handed out by the ``reson8_server`` fixture."""

    async def __call__(
        self,
        *,
        post_status: int = ...,
        post_body: str = ...,
        ws_status: int | None = ...,
        ws_error_message: str | None = ...,
    ) -> Recorded: ...


@pytest.fixture
async def reson8_server() -> AsyncIterator[StartServer]:
    """
    Run a real loopback Reson8 stand-in.

    Exercises the actual aiohttp request and websocket-upgrade paths, so the
    tests cover status handling and header attribution rather than a mock.
    """

    runners: list[web.AppRunner] = []

    async def _start(
        *,
        post_status: int = 200,
        post_body: str = '{"text": "hello"}',
        ws_status: int | None = None,
        ws_error_message: str | None = None,
    ) -> Recorded:
        rec = Recorded(api_url="")

        async def prerecorded(request: web.Request) -> web.StreamResponse:
            rec.post_headers = dict(request.headers)
            rec.query = dict(request.query)
            rec.post_body = await request.read()

            return web.Response(status=post_status, text=post_body, content_type="application/json")

        async def accept_turns(request: web.Request) -> web.StreamResponse:
            rec.handshake_headers = dict(request.headers)
            rec.query = dict(request.query)

            ws = web.WebSocketResponse()
            await ws.prepare(request)
            rec._ws = ws
            rec.connections += 1
            rec.connected.set()

            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.BINARY:
                    rec.audio.append(msg.data)
                elif msg.type is aiohttp.WSMsgType.TEXT:
                    rec.text.append(msg.data)

            return ws

        async def reject_turns(request: web.Request) -> web.StreamResponse:
            rec.handshake_headers = dict(request.headers)
            rec.query = dict(request.query)
            headers = {ERROR_MESSAGE_HEADER: ws_error_message} if ws_error_message else {}
            # a rejected upgrade carries no body, only this header
            return web.Response(status=ws_status or 500, headers=headers, text="")

        app = web.Application()
        app.router.add_post(PRERECORDED_PATH, prerecorded)
        app.router.add_get(TURNS_PATH, reject_turns if ws_status is not None else accept_turns)

        runner = web.AppRunner(app)
        await runner.setup()
        runners.append(runner)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        rec.api_url = f"http://127.0.0.1:{runner.addresses[0][1]}"
        return rec

    yield _start

    for runner in runners:
        await runner.cleanup()


@pytest.fixture
async def client_session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session
