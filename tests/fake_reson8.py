"""Stand-in for the Reson8 turns endpoint.

Mirrors the ``fake_*`` modules in livekit/agents' own suite: a scripted server
the real client talks to over a loopback socket, so transport and turn handling
get covered without network access or an API key.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import websockets

from livekit import rtc

SAMPLE_RATE = 16000
FRAME_SAMPLES = SAMPLE_RATE // 10

FULL_TURN: list[dict[str, Any]] = [
    {"type": "turn_start"},
    {"type": "turn_end_candidate", "text": "what is the weather"},
    # the speaker kept going, so the candidate above must be discarded
    {"type": "turn_continuation"},
    {"type": "turn_end_candidate", "text": "what is the weather in Amsterdam"},
    {"type": "turn_end"},
]
FINAL_TEXT = "what is the weather in Amsterdam"


class Recorded:
    """What the stand-in server saw the client send."""

    def __init__(self) -> None:
        self.path: str = ""
        self.auth: str | None = None
        self.audio_bytes: int = 0


@contextlib.asynccontextmanager
async def stand_in(script: list[dict[str, Any]] = FULL_TURN, *, step: float = 0.02):
    """Serve ``script`` to the first client that connects.

    Yields ``(api_url, recorded, done)``. ``done`` is set once the script has
    been sent, which callers use to keep pushing audio until the turn is over.
    """
    recorded = Recorded()
    done = asyncio.Event()

    async def handler(ws: Any) -> None:
        recorded.path = ws.request.path
        recorded.auth = ws.request.headers.get("Authorization")

        async def drain() -> None:
            async for msg in ws:
                if isinstance(msg, bytes):
                    recorded.audio_bytes += len(msg)

        drainer = asyncio.create_task(drain())
        try:
            for msg in script:
                await asyncio.sleep(step)
                await ws.send(json.dumps(msg))
            await asyncio.sleep(0.1)
        except websockets.ConnectionClosed:
            pass
        finally:
            done.set()
            drainer.cancel()

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", recorded, done


def silence_frame() -> rtc.AudioFrame:
    return rtc.AudioFrame(
        data=b"\x00\x00" * FRAME_SAMPLES,
        sample_rate=SAMPLE_RATE,
        num_channels=1,
        samples_per_channel=FRAME_SAMPLES,
    )
