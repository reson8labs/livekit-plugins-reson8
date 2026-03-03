from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Union
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import ClientConnection

from livekit import rtc
from livekit.agents import stt, utils

RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0


@dataclass
class STTOptions:
    api_key: str
    api_url: str
    language: str
    phrases: list[str]
    bias_strength: float
    sample_rate: int
    include_timestamps: bool
    include_words: bool
    include_confidence: bool


class STT(stt.STT):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        language: str = "nl",
        phrases: list[str] | None = None,
        bias_strength: float = 1.0,
        sample_rate: int = 16000,
        include_timestamps: bool = False,
        include_words: bool = False,
        include_confidence: bool = False,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
            ),
        )
        self._opts = STTOptions(
            api_key=api_key or os.environ["RESON8_API_KEY"],
            api_url=api_url or os.environ.get("RESON8_API_URL", "https://api.reson8.dev"),
            language=language,
            phrases=phrases or [],
            bias_strength=bias_strength,
            sample_rate=sample_rate,
            include_timestamps=include_timestamps,
            include_words=include_words,
            include_confidence=include_confidence,
        )

    def stream(self) -> SpeechStream:
        return SpeechStream(stt=self, opts=self._opts)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: Union[str, None] = None,
    ) -> stt.SpeechEvent:
        stream = self.stream()
        frames = rtc.combine_audio_frames(buffer)
        stream.push_frame(frames)
        stream.end_input()

        event: stt.SpeechEvent | None = None
        async for ev in stream:
            if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                event = ev

        if event is None:
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(text="", language=language or self._opts.language)],
            )
        return event


class SpeechStream(stt.RecognizeStream):
    def __init__(self, *, stt: STT, opts: STTOptions) -> None:
        super().__init__(stt=stt)
        self._opts = opts

    async def _run(self) -> None:
        ws_url = self._build_ws_url()
        delay = RECONNECT_BASE_DELAY

        while True:
            try:
                ws = await websockets.connect(
                    ws_url,
                    additional_headers={
                        "Authorization": f"ApiKey {self._opts.api_key}",
                    },
                )
                delay = RECONNECT_BASE_DELAY
                await self._run_session(ws)
                break
            except websockets.ConnectionClosed:
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
            except Exception:
                break

    async def _run_session(self, ws: ClientConnection) -> None:
        async with ws:
            send_task = asyncio.create_task(self._send_loop(ws))
            recv_task = asyncio.create_task(self._recv_loop(ws))

            try:
                await asyncio.gather(send_task, recv_task)
            finally:
                for t in (send_task, recv_task):
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _send_loop(self, ws: ClientConnection) -> None:
        async for data in self._input_ch:
            if isinstance(data, rtc.AudioFrame):
                await ws.send(data.data.tobytes())
            elif isinstance(data, self._FlushSentinel):
                flush_id = str(uuid.uuid4())
                await ws.send(json.dumps({"type": "flush_request", "id": flush_id}))

    async def _recv_loop(self, ws: ClientConnection) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue

            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "transcript":
                is_final = msg.get("is_final", True)
                event_type = (
                    stt.SpeechEventType.FINAL_TRANSCRIPT
                    if is_final
                    else stt.SpeechEventType.INTERIM_TRANSCRIPT
                )
                self._event_ch.send_nowait(
                    stt.SpeechEvent(
                        type=event_type,
                        alternatives=[
                            stt.SpeechData(
                                text=msg.get("text", ""),
                                language=self._opts.language,
                            ),
                        ],
                    )
                )

    def _build_ws_url(self) -> str:
        base = self._opts.api_url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        params: dict[str, str] = {
            "language": self._opts.language,
            "encoding": "pcm_s16le",
            "sample_rate": str(self._opts.sample_rate),
            "channels": "1",
        }
        if self._opts.phrases:
            params["phrases"] = ",".join(self._opts.phrases)
            params["bias_strength"] = str(self._opts.bias_strength)
        if self._opts.include_timestamps:
            params["include_timestamps"] = "true"
        if self._opts.include_words:
            params["include_words"] = "true"
        if self._opts.include_confidence:
            params["include_confidence"] = "true"

        return f"{base}/v1/speech-to-text/realtime?{urlencode(params)}"
