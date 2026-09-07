from __future__ import annotations

import pytest
from livekit.agents.types import NOT_GIVEN

from livekit.plugins.reson8._utils import (
    _confidence,
    _word_time,
    auth_headers,
    build_speech_data,
    build_url,
    normalize_languages,
)


def test_auth_headers() -> None:
    assert auth_headers("secret") == {"Authorization": "ApiKey secret"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ([], None),
        ("nl", "nl"),
        ("NL", "nl"),
        ("nl,de", "nl,de"),
        (["nl", "de"], "nl,de"),
        (" nl , de ", "nl,de"),
        (["nl", "", "de"], "nl,de"),
    ],
)
def test_normalize_languages(value: str | list[str] | None, expected: str | None) -> None:
    assert normalize_languages(value) == expected


@pytest.mark.parametrize("value", ["xx", "nl,xx", ["nl", "xx"], "english"])
def test_normalize_languages_rejects_unsupported(value: str | list[str]) -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        normalize_languages(value)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.reson8.dev", "wss://api.reson8.dev/turns?a=1"),
        ("http://localhost:8080", "ws://localhost:8080/turns?a=1"),
        ("https://api.reson8.dev/", "wss://api.reson8.dev/turns?a=1"),
    ],
)
def test_build_url_swaps_the_scheme_for_websockets(base_url: str, expected: str) -> None:
    assert build_url(base_url, "/turns", {"a": "1"}, websocket=True) == expected


def test_build_url_leaves_http_alone() -> None:
    url = build_url("https://api.reson8.dev", "/prerecorded", {"a": "1", "b": "2"})
    assert url == "https://api.reson8.dev/prerecorded?a=1&b=2"


def test_build_url_encodes_params() -> None:
    url = build_url("https://api.reson8.dev", "/turns", {"language": "nl,de"})
    assert url == "https://api.reson8.dev/turns?language=nl%2Cde"


def test_confidence_passes_through_documented_range() -> None:
    assert _confidence({"confidence": 0.99}) == pytest.approx(0.99)


def test_confidence_missing_is_not_given() -> None:
    assert _confidence({"text": "hi"}) is NOT_GIVEN


def test_confidence_clamps_above_range() -> None:
    assert _confidence({"confidence": 1.5}) == 1.0


@pytest.mark.parametrize("value", [0.0, -0.5, float("nan")])
def test_confidence_non_positive_is_not_given(value: float) -> None:
    assert _confidence({"confidence": value}) is NOT_GIVEN


def test_word_time_missing_start_ms_is_not_given() -> None:
    assert _word_time({}, "start", offset=1.0) is NOT_GIVEN


def test_word_time_applies_offset_and_duration() -> None:
    word = {"start_ms": 1000, "duration_ms": 500}
    assert _word_time(word, "start", offset=2.0) == pytest.approx(3.0)
    assert _word_time(word, "end", offset=2.0) == pytest.approx(3.5)


def test_build_speech_data_minimal() -> None:
    data = build_speech_data({"text": "hello"}, language="en")
    assert data.text == "hello"
    assert data.language == "en"
    assert data.confidence == 1.0
    assert data.words == []
    assert data.start_time == 0.0
    assert data.end_time == 0.0


def test_build_speech_data_message_language_wins() -> None:
    data = build_speech_data({"text": "hi", "language": "fr"}, language="en")
    assert data.language == "fr"


def test_build_speech_data_falls_back_to_passed_language() -> None:
    data = build_speech_data({"text": "hi"}, language="es")
    assert data.language == "es"


def test_build_speech_data_language_empty_when_unknown() -> None:
    data = build_speech_data({"text": "hi"}, language=None)
    assert data.language == ""


def test_build_speech_data_multi_fallback_does_not_leak() -> None:
    # With multiple pinned candidates there is no single dominant language to
    # assume when the server omits one, so the comma-string must not leak.
    data = build_speech_data({"text": "hi"}, language="nl,de")
    assert data.language == ""


def test_build_speech_data_server_language_wins_over_multi_fallback() -> None:
    data = build_speech_data({"text": "hi", "language": "nl"}, language="nl,de")
    assert data.language == "nl"


def test_build_speech_data_start_end_from_offsets() -> None:
    msg = {"text": "hi", "start_ms": 1000, "duration_ms": 500}
    data = build_speech_data(msg, language="en", start_time_offset=2.0)
    assert data.start_time == pytest.approx(3.0)
    assert data.end_time == pytest.approx(3.5)


def test_build_speech_data_confidence_is_mean_of_word_probabilities() -> None:
    msg = {
        "text": "hi there",
        "words": [
            {"text": "hi", "confidence": 0.99},
            {"text": "there", "confidence": 0.97},
        ],
    }
    data = build_speech_data(msg, language="en")

    assert data.confidence == pytest.approx(0.98)


def test_build_speech_data_words_carry_timings_and_confidence() -> None:
    msg = {
        "text": "hi",
        "words": [{"text": "hi", "start_ms": 0, "duration_ms": 200, "confidence": 0.9}],
    }
    data = build_speech_data(msg, language="en", start_time_offset=1.0)
    assert data.words is not None
    word = data.words[0]
    assert word == "hi"  # TimedString subclasses str
    assert word.start_time == pytest.approx(1.0)
    assert word.end_time == pytest.approx(1.2)
    assert word.confidence == pytest.approx(0.9)
