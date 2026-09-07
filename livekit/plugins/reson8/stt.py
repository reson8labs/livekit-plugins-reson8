from __future__ import annotations

import asyncio
import json
import os
import uuid
import weakref
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, cast

import aiohttp
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.stt import SpeechData
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given

from livekit import rtc

from ._utils import (
    DEFAULT_API_URL,
    ERROR_MESSAGE_HEADER,
    PRERECORDED_PATH,
    TURNS_PATH,
    auth_headers,
    build_speech_data,
    build_url,
    integration_headers,
    normalize_languages,
    problem_code,
    status_error,
)
from .log import logger

KEEPALIVE_INTERVAL = 30.0
_SEND_CHUNK_MS = 100


def _check_probability(name: str, value: float | None) -> None:
    if value is not None and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")


def _check_turn_thresholds(eager: float, final: float) -> None:
    if eager > final:
        raise ValueError(
            f"eager_turn_probability ({eager}) must be below "
            f"final_turn_probability ({final}); raise final_turn_probability "
            f"or lower eager_turn_probability"
        )
    if eager == final:
        logger.warning(
            "eager_turn_probability and final_turn_probability are both %s, so the "
            "turn commits on the same event as the preflight transcript and "
            "preemptive generation gets no lead time",
            final,
        )


@dataclass(frozen=True)
class TurnOptions:
    """``None`` leaves the server's default. Frozen: ``STT.stream`` shallow-copies
    the options, so a mutable section would be shared by every live stream."""

    # /turns does not report its effective config, so the defaults are mirrored
    # here to check one threshold when only the other is set. Keep in sync with
    # https://docs.reson8.dev/api/speech-to-text/turns/
    SERVER_DEFAULT_EAGER: ClassVar[float] = 0.5
    SERVER_DEFAULT_FINAL: ClassVar[float] = 0.92

    eager_turn_probability: float | None = None
    final_turn_probability: float | None = None

    def __post_init__(self) -> None:
        eager, final = self.eager_turn_probability, self.final_turn_probability
        _check_probability("eager_turn_probability", eager)
        _check_probability("final_turn_probability", final)
        _check_turn_thresholds(
            self.SERVER_DEFAULT_EAGER if eager is None else eager,
            self.SERVER_DEFAULT_FINAL if final is None else final,
        )

    def query_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.eager_turn_probability is not None:
            params["eager_turn_probability"] = str(self.eager_turn_probability)
        if self.final_turn_probability is not None:
            params["final_turn_probability"] = str(self.final_turn_probability)
        return params


@dataclass
class STTOptions:
    language: str | None
    sample_rate: int
    encoding: str
    channels: int
    custom_model_id: str | None
    include_timestamps: bool
    include_words: bool
    include_confidence: bool
    include_language: bool
    turn: TurnOptions = field(default_factory=TurnOptions)

    def query_params(self, *, streaming: bool) -> dict[str, str]:
        params: dict[str, str] = {
            "encoding": self.encoding,
            "sample_rate": str(self.sample_rate),
            "channels": str(self.channels),
        }
        # When omitted, Reson8 auto-detects the spoken language; otherwise this
        # pins recognition to the given code(s) (comma-joined for multiple).
        if self.language:
            params["language"] = self.language
        if self.custom_model_id:
            params["custom_model_id"] = self.custom_model_id
        if self.include_timestamps:
            params["include_timestamps"] = "true"
        if self.include_words:
            params["include_words"] = "true"
        if self.include_language:
            params["include_language"] = "true"

        if streaming:
            params.update(self.turn.query_params())
        elif self.include_confidence:
            params["include_confidence"] = "true"

        return params


class STT(stt.STT):
    """Reson8 speech-to-text.

    A single model that adapts to how LiveKit uses it:

    * **Streaming** (:meth:`stream`) connects to the turn-aware
      ``/v1/speech-to-text/turns`` endpoint. Reson8 detects conversational turn
      boundaries server-side and emits a turn-end *candidate* once it believes a
      turn is complete. That candidate surfaces as a preflight transcript the
      agent can act on speculatively, and is then either confirmed as a final
      transcript or cancelled when the speaker keeps talking. Ideal for
      low-latency voice agents.
    * **Batch** (:meth:`recognize`) sends pre-recorded audio to
      ``/v1/speech-to-text/prerecorded`` and returns the full transcript.

    Leave ``language`` as ``None`` (the default) to auto-detect the spoken
    language, or pass one or more :data:`SupportedLanguage` codes to pin
    recognition.

    ``eager_turn_probability`` and ``final_turn_probability`` are the main lever
    on end-of-turn latency, and :meth:`SpeechStream.flush` commits a turn on
    demand without touching either::

        # the server's 0.92 default is tuned for conversational speech and is
        # slow to commit a one-word answer
        stt = reson8.STT(language="es", final_turn_probability=0.7)

        # or leave it alone and commit when you already know they are done
        stream = stt.stream()
        stream.flush()

    See https://docs.reson8.dev/api/speech-to-text/turns/
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        language: str | Sequence[str] | None = None,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
        channels: int = 1,
        custom_model_id: str | None = None,
        include_timestamps: bool = False,
        include_words: bool = False,
        include_confidence: bool = False,
        include_language: bool = False,
        eager_turn_probability: float | None = None,
        final_turn_probability: float | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        """
        Args:
            api_key: Reson8 API key. Falls back to the ``RESON8_API_KEY`` env var.
            api_url: Reson8 API base URL. Falls back to ``RESON8_API_URL`` or
                ``https://api.reson8.dev``.
            language: One or more :data:`SupportedLanguage` codes to pin
                recognition to. Pass a single code (``"nl"``), a comma-string
                (``"nl,de"``), or a list (``["nl", "de"]``). Leave as ``None`` to
                auto-detect. Raises ``ValueError`` for unsupported codes.
            sample_rate: Input sample rate in Hz.
            encoding: Audio encoding sent to Reson8.
            channels: Number of audio channels.
            custom_model_id: Optional custom model id used to bias recognition.
            include_timestamps: Include ``start``/``end`` times on results.
            include_words: Include word-level results.
            include_confidence: Include confidence scores (batch recognition).
            include_language: Report the detected language code.
            eager_turn_probability: Confidence (0-1) at which the preflight
                transcript is emitted. Server default ``0.5``.
            http_session: Optional session to use for requests. Defaults to the
                shared session managed by the agent framework.
            final_turn_probability: Confidence (0-1) at which the turn commits.
                Server default ``0.92``, tuned for conversational speech; a
                one-word confirmation can take over a second to cross it.
        """

        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
                offline_recognize=True,
            ),
        )

        api_key = api_key or os.environ.get("RESON8_API_KEY")
        if not api_key:
            raise ValueError(
                "Reson8 API key is required, either as argument or RESON8_API_KEY env var"
            )

        self._api_key = api_key
        self._api_url = (api_url or os.environ.get("RESON8_API_URL", DEFAULT_API_URL)).rstrip("/")
        self._opts = STTOptions(
            language=normalize_languages(language),
            sample_rate=sample_rate,
            encoding=encoding,
            channels=channels,
            custom_model_id=custom_model_id,
            include_timestamps=include_timestamps,
            include_words=include_words,
            include_confidence=include_confidence,
            include_language=include_language,
            turn=TurnOptions(
                eager_turn_probability=eager_turn_probability,
                final_turn_probability=final_turn_probability,
            ),
        )
        self._session = http_session
        self._streams = weakref.WeakSet[SpeechStream]()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()

        return self._session

    @property
    def model(self) -> str:
        return self._opts.custom_model_id or "default"

    @property
    def provider(self) -> str:
        return "reson8"

    def update_options(
        self,
        *,
        language: NotGivenOr[str | Sequence[str] | None] = NOT_GIVEN,
        custom_model_id: NotGivenOr[str | None] = NOT_GIVEN,
        include_timestamps: NotGivenOr[bool] = NOT_GIVEN,
        include_words: NotGivenOr[bool] = NOT_GIVEN,
        include_confidence: NotGivenOr[bool] = NOT_GIVEN,
        include_language: NotGivenOr[bool] = NOT_GIVEN,
    ) -> None:
        if is_given(language):
            self._opts.language = normalize_languages(language)
        if is_given(custom_model_id):
            self._opts.custom_model_id = custom_model_id
        if is_given(include_timestamps):
            self._opts.include_timestamps = include_timestamps
        if is_given(include_words):
            self._opts.include_words = include_words
        if is_given(include_confidence):
            self._opts.include_confidence = include_confidence
        if is_given(include_language):
            self._opts.include_language = include_language

        for stream in self._streams:
            stream.update_options(
                language=language,
                custom_model_id=custom_model_id,
                include_timestamps=include_timestamps,
                include_words=include_words,
                include_language=include_language,
            )

    def stream(
        self,
        *,
        language: NotGivenOr[str | Sequence[str]] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        opts = replace(self._opts)
        if is_given(language):
            opts.language = normalize_languages(language)
        stream = SpeechStream(
            stt=self,
            opts=opts,
            api_key=self._api_key,
            api_url=self._api_url,
            conn_options=conn_options,
            http_session=self._session,
        )
        self._streams.add(stream)
        return stream

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str | Sequence[str]] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = normalize_languages(language) if is_given(language) else self._opts.language
        frames = rtc.combine_audio_frames(buffer)

        opts = replace(self._opts, language=lang, encoding="pcm_s16le")
        opts.sample_rate = frames.sample_rate
        opts.channels = frames.num_channels

        url = build_url(self._api_url, PRERECORDED_PATH, opts.query_params(streaming=False))

        try:
            async with self._ensure_session().post(
                url,
                data=frames.data.tobytes(),
                headers={
                    **auth_headers(self._api_key),
                    **integration_headers(),
                    "Content-Type": "application/octet-stream",
                },
                timeout=aiohttp.ClientTimeout(total=30, sock_connect=conn_options.timeout),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise status_error(resp.status, detail=problem_code(text))
        except asyncio.TimeoutError:
            raise APITimeoutError("Reson8 did not respond in time") from None
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"Failed to reach Reson8 ({type(e).__name__})") from None

        try:
            body = json.loads(text)
        except ValueError:
            raise APIConnectionError("Reson8 returned a malformed response body") from None

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=str(uuid.uuid4()),
            alternatives=[build_speech_data(body, language=lang)],
        )


class SpeechStream(stt.RecognizeStream):
    """Turn-aware streaming session against the ``/turns`` endpoint."""

    def __init__(
        self,
        *,
        stt: STT,
        opts: STTOptions,
        api_key: str,
        api_url: str,
        conn_options: APIConnectOptions,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts
        self._api_key = api_key
        self._api_url = api_url
        self._session = http_session
        self._request_id = str(uuid.uuid4())
        self._reconnect_event = asyncio.Event()
        self._speaking = False
        # the most recent turn-end candidate, promoted to a final transcript
        # once the server confirms the turn ended
        self._candidate: SpeechData | None = None

    def update_options(
        self,
        *,
        language: NotGivenOr[str | Sequence[str] | None] = NOT_GIVEN,
        custom_model_id: NotGivenOr[str | None] = NOT_GIVEN,
        include_timestamps: NotGivenOr[bool] = NOT_GIVEN,
        include_words: NotGivenOr[bool] = NOT_GIVEN,
        include_language: NotGivenOr[bool] = NOT_GIVEN,
    ) -> None:
        if is_given(language):
            self._opts.language = normalize_languages(language)
        if is_given(custom_model_id):
            self._opts.custom_model_id = custom_model_id
        if is_given(include_timestamps):
            self._opts.include_timestamps = include_timestamps
        if is_given(include_words):
            self._opts.include_words = include_words
        if is_given(include_language):
            self._opts.include_language = include_language
        self._reconnect_event.set()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if not self._session:
            self._session = utils.http_context.http_session()

        return self._session

    async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        url = build_url(
            self._api_url, TURNS_PATH, self._opts.query_params(streaming=True), websocket=True
        )

        connect = self._ensure_session().ws_connect(
            url,
            headers={**auth_headers(self._api_key), **integration_headers()},
            heartbeat=KEEPALIVE_INTERVAL,
        )

        try:
            return await asyncio.wait_for(
                cast("Awaitable[aiohttp.ClientWebSocketResponse]", connect),
                self._conn_options.timeout,
            )
        except aiohttp.WSServerHandshakeError as e:
            reason = e.headers.get(ERROR_MESSAGE_HEADER) if e.headers else None
            raise status_error(e.status, detail=reason) from None
        except asyncio.TimeoutError:
            raise APITimeoutError("Timed out connecting to Reson8") from None
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"Failed to connect to Reson8 ({type(e).__name__})") from None

    async def _run(self) -> None:
        closing_ws = False

        @utils.log_exceptions(logger=logger)
        async def send_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            nonlocal closing_ws

            samples_per_channel = self._opts.sample_rate * _SEND_CHUNK_MS // 1000
            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self._opts.sample_rate,
                num_channels=self._opts.channels,
                samples_per_channel=samples_per_channel,
            )

            try:
                async for data in self._input_ch:
                    flushing = isinstance(data, self._FlushSentinel)
                    if isinstance(data, rtc.AudioFrame):
                        frames = audio_bstream.write(data.data.tobytes())
                    else:
                        frames = audio_bstream.flush()

                    for frame in frames:
                        await ws.send_bytes(frame.data.tobytes())

                    if flushing:
                        await ws.send_str(json.dumps({"type": "flush_request"}))
            except (aiohttp.ClientError, ConnectionError):
                if closing_ws or self._ensure_session().closed:
                    return

                raise

            closing_ws = True
            await ws.close()

        @utils.log_exceptions(logger=logger)
        async def recv_task(ws: aiohttp.ClientWebSocketResponse) -> None:
            while True:
                msg = await ws.receive()
                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    if closing_ws or self._ensure_session().closed:
                        return

                    raise APIConnectionError(
                        f"Reson8 connection closed unexpectedly (code={ws.close_code})"
                    )

                if msg.type is not aiohttp.WSMsgType.TEXT:
                    continue

                try:
                    parsed = json.loads(msg.data)
                except (ValueError, TypeError):
                    logger.warning(
                        "Ignoring unparseable Reson8 message",
                        extra={"lk.pii.message": msg.data},
                    )
                    continue

                self._process_message(parsed)

        while True:
            ws: aiohttp.ClientWebSocketResponse | None = None
            try:
                ws = await self._connect_ws()
                tasks = [
                    asyncio.create_task(send_task(ws)),
                    asyncio.create_task(recv_task(ws)),
                ]
                tasks_group = asyncio.gather(*tasks)
                wait_reconnect = asyncio.create_task(self._reconnect_event.wait())

                try:
                    done, _ = await asyncio.wait(
                        (tasks_group, wait_reconnect),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        if task is not wait_reconnect:
                            task.result()

                    if wait_reconnect not in done:
                        break

                    self._reconnect_event.clear()
                    logger.debug("Reconnecting to Reson8 to apply updated options")
                finally:
                    await utils.aio.gracefully_cancel(*tasks, wait_reconnect)
                    tasks_group.cancel()
                    tasks_group.exception()
            finally:
                if ws is not None:
                    await ws.close()

    def _process_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")

        if msg_type == "turn_start":
            self._candidate = None
            self._start_speaking()

        elif msg_type == "turn_end_candidate":
            # eager end-of-turn: surface as a preflight transcript that the
            # agent can act on speculatively before the turn is confirmed.
            self._start_speaking()
            self._candidate = build_speech_data(
                msg,
                language=self._opts.language,
                start_time_offset=self.start_time_offset,
            )
            if self._candidate.text:
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
                        request_id=self._request_id,
                        alternatives=[self._candidate],
                    )
                )

        elif msg_type == "turn_continuation":
            # the speaker resumed: the previous candidate is no longer final.
            self._candidate = None

        elif msg_type == "turn_end":
            candidate = self._candidate
            self._candidate = None
            if candidate is not None:
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        request_id=self._request_id,
                        alternatives=[candidate],
                    )
                )
            if self._speaking:
                self._speaking = False
                self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))

        else:
            logger.debug("ignoring unhandled Reson8 message type: %r", msg_type)

    def _start_speaking(self) -> None:
        if self._speaking:
            return
        self._speaking = True
        self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
