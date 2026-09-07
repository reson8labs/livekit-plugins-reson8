from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, get_args
from urllib.parse import urlencode

from livekit.agents import APIStatusError, LanguageCode, create_api_error_from_http, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr, TimedString

from .version import __version__

DEFAULT_API_URL = "https://api.reson8.dev"
TURNS_PATH = "/v1/speech-to-text/turns"
PRERECORDED_PATH = "/v1/speech-to-text/prerecorded"
INTEGRATION_HEADER = "X-Reson8-Integration"
INTEGRATION_NAME = "livekit-python"

ERROR_MESSAGE_HEADER = "X-Error-Message"

# https://docs.reson8.dev/api/speech-to-text/turns/ and /api/speech-to-text/prerecorded/
_STATUS_HINTS = {
    400: "Invalid query parameter, or unknown custom_model_id",
    401: "Missing or invalid credentials, check the provided api_key or RESON8_API_KEY",
    402: "Credit limit exceeded, see https://docs.reson8.dev/limits/",
    413: "The request body exceeds the size limit",
    429: "Concurrent connection limit exceeded, see https://docs.reson8.dev/limits/",
}


SupportedLanguage = Literal["de", "en", "es", "fr", "fy", "it", "nl", "pl", "pt", "sv"]
"""The languages Reson8 can recognize, as ISO 639-1 codes.

See https://docs.reson8.dev/speech-to-text/features/languages/.
"""

SUPPORTED_LANGUAGES: tuple[str, ...] = get_args(SupportedLanguage)
"""``SupportedLanguage`` as a runtime tuple, for validation and error messages."""


def normalize_languages(value: str | Sequence[str] | None) -> str | None:
    """Normalize and validate a language selection into Reson8's query form.

    ``"nl"`` -> ``"nl"``; ``"nl,de"`` -> ``"nl,de"``; ``["nl", "de"]`` ->
    ``"nl,de"``; ``None``/``""``/``[]`` -> ``None`` (auto-detect).

    Raises ``ValueError`` if any code is not a :data:`SupportedLanguage`, so
    invalid selections fail locally rather than after a request to the API.
    """

    if value is None:
        return None

    codes = value.split(",") if isinstance(value, str) else list(value)
    codes = [c.strip().lower() for c in codes if c and c.strip()]
    if not codes:
        return None

    unsupported = [c for c in codes if c not in SUPPORTED_LANGUAGES]
    if unsupported:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise ValueError(
            f"unsupported language(s): {', '.join(unsupported)}. Reson8 supports: {supported}."
        )

    return ",".join(codes)


def build_url(api_url: str, path: str, params: dict[str, str], *, websocket: bool = False) -> str:
    base = api_url.rstrip("/")
    if websocket:
        base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)

    return f"{base}{path}?{urlencode(params)}"


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"ApiKey {api_key}"}


def integration_headers() -> dict[str, str]:
    return {INTEGRATION_HEADER: f"{INTEGRATION_NAME}:{__version__}"}


def problem_code(body: str) -> str | None:
    """Read the ``code`` field out of a ``problem+json`` error body."""

    try:
        parsed = json.loads(body)
    except ValueError:
        return None

    code = parsed.get("code") if isinstance(parsed, dict) else None
    return code if isinstance(code, str) else None


def status_error(status_code: int, *, detail: str | None = None) -> APIStatusError:
    """
    Map a Reson8 rejection onto an actionable error.

    Bodies are not attached, to keep provider payloads out of telemetry.

    ``APIStatusError`` marks non-transient 4xx as non-retryable, so an
    exhausted credit balance or a bad key fails fast instead of backing off.
    """

    hint = _STATUS_HINTS.get(status_code)
    message = ": ".join(p for p in (detail, hint) if p)
    return create_api_error_from_http(message, status=status_code)


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
