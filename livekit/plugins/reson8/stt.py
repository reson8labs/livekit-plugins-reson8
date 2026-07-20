from __future__ import annotations

import asyncio
import json
import os
import uuid
import weakref
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.stt import SpeechData
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given
from websockets.asyncio.client import ClientConnection

from livekit import rtc

from ._utils import DEFAULT_API_URL, auth_headers, build_speech_data, to_ws_base


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

    def query_params(self, *, streaming: bool) -> dict[str, str]:
        params: dict[str, str] = {
            "encoding": self.encoding,
            "sample_rate": str(self.sample_rate),
            "channels": str(self.channels),
        }
        # Any language is supported. When omitted, Reson8 auto-detects the
        # spoken language; otherwise this pins recognition to the given code.
        if self.language:
            params["language"] = self.language
        if self.custom_model_id:
            params["custom_model_id"] = self.custom_model_id
        if self.include_timestamps:
            params["include_timestamps"] = "true"
        if self.include_words:
            params["include_words"] = "true"
        if streaming:
            if self.include_language:
                params["include_language"] = "true"
        elif self.include_confidence:
            params["include_confidence"] = "true"
        return params


class STT(stt.STT[Any]):
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

    Any language is supported. Leave ``language`` as ``None`` (the default) to
    auto-detect the spoken language, or pass any language code to pin it.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        language: str | None = None,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
        channels: int = 1,
        custom_model_id: str | None = None,
        include_timestamps: bool = False,
        include_words: bool = False,
        include_confidence: bool = False,
        include_language: bool = False,
    ) -> None:
        """
        Args:
            api_key: Reson8 API key. Falls back to the ``RESON8_API_KEY`` env var.
            api_url: Reson8 API base URL. Falls back to ``RESON8_API_URL`` or
                ``https://api.reson8.dev``.
            language: Any language code (e.g. ``"en"``, ``"nl"``, ``"es"``, ...).
                Leave as ``None`` to auto-detect the spoken language.
            sample_rate: Input sample rate in Hz.
            encoding: Audio encoding sent to Reson8.
            channels: Number of audio channels.
            custom_model_id: Optional custom model id used to bias recognition.
            include_timestamps: Include ``start``/``end`` times on results.
            include_words: Include word-level results.
            include_confidence: Include confidence scores (batch recognition).
            include_language: Report the detected language code (streaming).
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
            language=language,
            sample_rate=sample_rate,
            encoding=encoding,
            channels=channels,
            custom_model_id=custom_model_id,
            include_timestamps=include_timestamps,
            include_words=include_words,
            include_confidence=include_confidence,
            include_language=include_language,
        )
        self._streams = weakref.WeakSet[SpeechStream]()

    @property
    def model(self) -> str:
        return self._opts.custom_model_id or "default"

    @property
    def provider(self) -> str:
        return "reson8"

    def update_options(
        self,
        *,
        language: NotGivenOr[str | None] = NOT_GIVEN,
        custom_model_id: NotGivenOr[str | None] = NOT_GIVEN,
        include_timestamps: NotGivenOr[bool] = NOT_GIVEN,
        include_words: NotGivenOr[bool] = NOT_GIVEN,
        include_confidence: NotGivenOr[bool] = NOT_GIVEN,
        include_language: NotGivenOr[bool] = NOT_GIVEN,
    ) -> None:
        if is_given(language):
            self._opts.language = language
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
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        opts = replace(self._opts)
        if is_given(language):
            opts.language = language
        stream = SpeechStream(
            stt=self,
            opts=opts,
            api_key=self._api_key,
            api_url=self._api_url,
            conn_options=conn_options,
        )
        self._streams.add(stream)
        return stream

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = language if is_given(language) else self._opts.language
        frames = rtc.combine_audio_frames(buffer)

        opts = replace(self._opts, language=lang, encoding="pcm_s16le")
        opts.sample_rate = frames.sample_rate
        opts.channels = frames.num_channels

        url = (
            f"{self._api_url}/v1/speech-to-text/prerecorded?"
            + f"{urlencode(opts.query_params(streaming=False))}"
        )

        try:
            async with httpx.AsyncClient(timeout=conn_options.timeout) as client:
                resp = await client.post(
                    url,
                    content=frames.data.tobytes(),
                    headers={
                        **auth_headers(self._api_key),
                        "Content-Type": "application/octet-stream",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
        except httpx.TimeoutException as e:
            raise APITimeoutError() from e
        except httpx.HTTPStatusError as e:
            raise APIStatusError(
                e.response.text or str(e),
                status_code=e.response.status_code,
            ) from e
        except httpx.HTTPError as e:
            raise APIConnectionError() from e

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
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts
        self._api_key = api_key
        self._api_url = api_url
        self._request_id = str(uuid.uuid4())
        self._reconnect_event = asyncio.Event()
        self._speaking = False
        # the most recent turn-end candidate, promoted to a final transcript
        # once the server confirms the turn ended
        self._candidate: SpeechData | None = None

    def update_options(
        self,
        *,
        language: NotGivenOr[str | None] = NOT_GIVEN,
        custom_model_id: NotGivenOr[str | None] = NOT_GIVEN,
        include_timestamps: NotGivenOr[bool] = NOT_GIVEN,
        include_words: NotGivenOr[bool] = NOT_GIVEN,
        include_language: NotGivenOr[bool] = NOT_GIVEN,
    ) -> None:
        if is_given(language):
            self._opts.language = language
        if is_given(custom_model_id):
            self._opts.custom_model_id = custom_model_id
        if is_given(include_timestamps):
            self._opts.include_timestamps = include_timestamps
        if is_given(include_words):
            self._opts.include_words = include_words
        if is_given(include_language):
            self._opts.include_language = include_language
        self._reconnect_event.set()

    def _build_url(self) -> str:
        base = to_ws_base(self._api_url)
        return (
            f"{base}/v1/speech-to-text/turns?{urlencode(self._opts.query_params(streaming=True))}"
        )

    async def _run(self) -> None:
        closing = False

        async def send_task(ws: ClientConnection) -> None:
            async for data in self._input_ch:
                if isinstance(data, rtc.AudioFrame):
                    await ws.send(data.data.tobytes())
                # turn boundaries are detected server-side; flush sentinels are
                # not part of the turns protocol and are intentionally ignored.

            nonlocal closing
            closing = True
            await ws.close()

        async def recv_task(ws: ClientConnection) -> None:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                self._process_message(msg)

        while True:
            try:
                ws = await websockets.connect(
                    self._build_url(),
                    additional_headers=auth_headers(self._api_key),
                )
            except (websockets.InvalidStatus, websockets.InvalidHandshake, OSError) as e:
                raise APIConnectionError("failed to connect to Reson8") from e

            self._num_retries = 0
            tasks = [
                asyncio.create_task(send_task(ws)),
                asyncio.create_task(recv_task(ws)),
            ]
            wait_reconnect = asyncio.create_task(self._reconnect_event.wait())
            try:
                waiters: list[asyncio.Future[Any]] = [
                    asyncio.gather(*tasks),
                    wait_reconnect,
                ]
                done, _ = await asyncio.wait(
                    waiters,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is not wait_reconnect:
                        task.result()

                if wait_reconnect.done() and not closing:
                    self._reconnect_event.clear()
                    continue
                break
            except websockets.ConnectionClosedError as e:
                if closing:
                    break
                raise APIConnectionError("Reson8 connection closed unexpectedly") from e
            finally:
                await utils.aio.gracefully_cancel(*tasks, wait_reconnect)
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

    def _start_speaking(self) -> None:
        if self._speaking:
            return
        self._speaking = True
        self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
