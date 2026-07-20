from __future__ import annotations

import math

import pytest
from livekit.agents.types import NOT_GIVEN

from livekit.plugins.reson8._utils import (
    _to_probability,
    _word_time,
    auth_headers,
    build_speech_data,
    to_ws_base,
)


def test_auth_headers():
    assert auth_headers("secret") == {"Authorization": "ApiKey secret"}


@pytest.mark.parametrize(
    ("api_url", "expected"),
    [
        ("https://api.reson8.dev", "wss://api.reson8.dev"),
        ("http://localhost:8080", "ws://localhost:8080"),
        ("https://api.reson8.dev/", "wss://api.reson8.dev"),
    ],
)
def test_to_ws_base(api_url, expected):
    assert to_ws_base(api_url) == expected


def test_to_probability_zero_log_prob_is_certain():
    assert _to_probability(0.0) == 1.0


def test_to_probability_none_is_not_given():
    assert _to_probability(None) is NOT_GIVEN


def test_to_probability_converts_log_prob_to_probability():
    assert _to_probability(math.log(0.5)) == pytest.approx(0.5)


def test_word_time_missing_start_ms_is_not_given():
    assert _word_time({}, "start", offset=1.0) is NOT_GIVEN


def test_word_time_applies_offset_and_duration():
    word = {"start_ms": 1000, "duration_ms": 500}
    assert _word_time(word, "start", offset=2.0) == pytest.approx(3.0)
    assert _word_time(word, "end", offset=2.0) == pytest.approx(3.5)


def test_build_speech_data_minimal():
    data = build_speech_data({"text": "hello"}, language="en")
    assert data.text == "hello"
    assert data.language == "en"
    assert data.confidence == 1.0
    assert data.words == []
    assert data.start_time == 0.0
    assert data.end_time == 0.0


def test_build_speech_data_message_language_wins():
    data = build_speech_data({"text": "hi", "language": "fr"}, language="en")
    assert data.language == "fr"


def test_build_speech_data_falls_back_to_passed_language():
    data = build_speech_data({"text": "hi"}, language="es")
    assert data.language == "es"


def test_build_speech_data_language_empty_when_unknown():
    data = build_speech_data({"text": "hi"}, language=None)
    assert data.language == ""


def test_build_speech_data_start_end_from_offsets():
    msg = {"text": "hi", "start_ms": 1000, "duration_ms": 500}
    data = build_speech_data(msg, language="en", start_time_offset=2.0)
    assert data.start_time == pytest.approx(3.0)
    assert data.end_time == pytest.approx(3.5)


def test_build_speech_data_confidence_is_mean_of_word_probabilities():
    msg = {
        "text": "hi there",
        "words": [
            {"text": "hi", "confidence": 0.0},  # exp(0) == 1.0
            {"text": "there", "confidence": math.log(0.5)},  # 0.5
        ],
    }
    data = build_speech_data(msg, language="en")
    assert data.confidence == pytest.approx(0.75)


def test_build_speech_data_words_carry_timings_and_confidence():
    msg = {
        "text": "hi",
        "words": [{"text": "hi", "start_ms": 0, "duration_ms": 200, "confidence": 0.0}],
    }
    data = build_speech_data(msg, language="en", start_time_offset=1.0)
    assert data.words is not None
    word = data.words[0]
    assert word == "hi"  # TimedString subclasses str
    assert word.start_time == pytest.approx(1.0)
    assert word.end_time == pytest.approx(1.2)
    assert word.confidence == pytest.approx(1.0)
