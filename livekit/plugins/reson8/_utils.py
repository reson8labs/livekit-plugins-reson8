from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from livekit.agents import LanguageCode, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr, TimedString

from .version import __version__

DEFAULT_API_URL = "https://api.reson8.dev"
INTEGRATION_HEADER = "X-Reson8-Integration"
INTEGRATION_NAME = "livekit-python"


class SupportedLanguages(StrEnum):
    """The languages Reson8 can recognize, valued by ISO 639-1 code.

    Members are strings (``SupportedLanguages.DUTCH == "nl"``), so they can be
    passed directly wherever a ``language`` code is accepted. Language selection
    is validated against this enum locally, so unsupported codes fail fast
    instead of after a round-trip to the API.
    """

    GERMAN = "de"
    ENGLISH = "en"
    SPANISH = "es"
    FRENCH = "fr"
    FRISIAN = "fy"
    ITALIAN = "it"
    DUTCH = "nl"
    POLISH = "pl"
    PORTUGUESE = "pt"
    SWEDISH = "sv"


def normalize_languages(value: str | Sequence[str] | None) -> str | None:
    """Normalize and validate a language selection into Reson8's query form.

    ``"nl"`` -> ``"nl"``; ``"nl,de"`` -> ``"nl,de"``; ``["nl", "de"]`` ->
    ``"nl,de"``; ``None``/``""``/``[]`` -> ``None`` (auto-detect).

    Raises ``ValueError`` if any code is not a member of :class:`SupportedLanguages`,
    so invalid selections fail locally rather than after a request to the API.
    """

    if value is None:
        return None

    codes = value.split(",") if isinstance(value, str) else list(value)
    codes = [c.strip().lower() for c in codes if c and c.strip()]
    if not codes:
        return None

    unsupported = [c for c in codes if c not in set(SupportedLanguages)]
    if unsupported:
        supported = ", ".join(sorted(SupportedLanguages))
        raise ValueError(
            f"unsupported language(s): {', '.join(unsupported)}. Reson8 supports: {supported}."
        )

    return ",".join(codes)


def to_ws_base(api_url: str) -> str:
    """Convert an http(s) API base URL into its ws(s) equivalent."""
    return api_url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"ApiKey {api_key}"}


def integration_headers() -> dict[str, str]:
    return {INTEGRATION_HEADER: f"{INTEGRATION_NAME}:{__version__}"}


def _confidence(word: dict[str, Any]) -> NotGivenOr[float]:
    """
    Reson8 reports word confidence as a probability in (0, 1].

    See https://docs.reson8.dev/glossary/.
    """

    confidence: float | None = word.get("confidence")
    if confidence is None or not confidence > 0:
        return NOT_GIVEN

    return min(confidence, 1.0)


def _word_time(word: dict[str, Any], key: str, *, offset: float) -> NotGivenOr[float]:
    if "start_ms" not in word:
        return NOT_GIVEN
    start: float = word.get("start_ms", 0)
    if key == "start":
        return offset + start / 1000.0
    duration: float = word.get("duration_ms", 0)
    return offset + (start + duration) / 1000.0


def build_speech_data(
    msg: dict[str, Any],
    *,
    language: str | None,
    start_time_offset: float = 0.0,
) -> stt.SpeechData:
    """Build a LiveKit ``SpeechData`` from a Reson8 transcript/turn payload.

    Handles the optional ``start_ms``/``duration_ms``/``words`` fields that are
    only present when the matching ``include_*`` options are enabled.
    """
    raw_words = msg.get("words") or []
    confidences = [_confidence(w) for w in raw_words]
    words = [
        TimedString(
            text=w.get("text", ""),
            start_time=_word_time(w, "start", offset=start_time_offset),
            end_time=_word_time(w, "end", offset=start_time_offset),
            confidence=c,
            start_time_offset=start_time_offset,
        )
        for w, c in zip(raw_words, confidences, strict=True)
    ]

    known = [c for c in confidences if isinstance(c, float)]
    confidence = sum(known) / len(known) if known else 1.0

    start_ms = msg.get("start_ms")
    duration_ms = msg.get("duration_ms") or 0
    if start_ms is not None:
        start_time = start_time_offset + start_ms / 1000.0
        end_time = start_time_offset + (start_ms + duration_ms) / 1000.0
    else:
        start_time = start_time_offset
        end_time = start_time_offset

    fallback = language if language and "," not in language else ""
    return stt.SpeechData(
        language=LanguageCode(msg.get("language") or fallback or ""),
        text=msg.get("text", ""),
        start_time=start_time,
        end_time=end_time,
        confidence=confidence,
        words=words,
    )
