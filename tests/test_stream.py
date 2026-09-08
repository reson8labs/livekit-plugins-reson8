from __future__ import annotations

from conftest import MakeStream, emitted
from livekit.agents import stt

SpeechEventType = stt.SpeechEventType


def test_start_of_speech_emitted_once(make_stream: MakeStream) -> None:
    stream = make_stream()

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_start"})

    starts = [e for e in emitted(stream) if e.type == SpeechEventType.START_OF_SPEECH]
    assert len(starts) == 1


def test_empty_candidate_text_is_not_surfaced_as_preflight(make_stream: MakeStream) -> None:
    stream = make_stream()

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": ""})

    assert all(e.type != SpeechEventType.PREFLIGHT_TRANSCRIPT for e in emitted(stream))


def test_unhandled_message_type_produces_no_events(make_stream: MakeStream) -> None:
    stream = make_stream()

    stream._process_message({"type": "something_new"})

    assert emitted(stream) == []


def test_a_repeated_candidate_does_not_re_emit_preflight(make_stream: MakeStream) -> None:
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})

    preflights = [e for e in emitted(stream) if e.type == SpeechEventType.PREFLIGHT_TRANSCRIPT]
    assert len(preflights) == 1


def test_a_revised_candidate_does_re_emit_preflight(make_stream: MakeStream) -> None:
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "order"})
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})

    preflights = [e for e in emitted(stream) if e.type == SpeechEventType.PREFLIGHT_TRANSCRIPT]
    assert [e.alternatives[0].text for e in preflights] == ["order", "order lunch"]


def test_the_last_candidate_still_becomes_the_final(make_stream: MakeStream) -> None:
    # deduping the preflight must not stop turn_end promoting the candidate
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})
    stream._process_message({"type": "turn_end"})

    finals = [e for e in emitted(stream) if e.type == SpeechEventType.FINAL_TRANSCRIPT]
    assert [e.alternatives[0].text for e in finals] == ["order lunch"]


def test_probability_readings_are_attached_to_the_candidate(make_stream: MakeStream) -> None:
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message(
        {
            "type": "turn_end_probability",
            "probability": 0.63,
            "raw_eot_probability": 0.58,
            "vad_probability": 0.91,
            "timestamp_ms": 1200,
        }
    )
    stream._process_message({"type": "turn_end_candidate", "text": "order lunch"})
    preflight = next(e for e in emitted(stream) if e.type == SpeechEventType.PREFLIGHT_TRANSCRIPT)

    metadata = preflight.alternatives[0].metadata
    assert metadata is not None
    assert metadata["probability"] == 0.63
    assert metadata["vad_probability"] == 0.91
    assert "type" not in metadata


def test_a_probability_reading_emits_no_event(make_stream: MakeStream) -> None:
    stream = make_stream()

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_probability", "probability": 0.4})

    assert [e.type for e in emitted(stream)] == [SpeechEventType.START_OF_SPEECH]


def test_readings_do_not_leak_across_turns(make_stream: MakeStream) -> None:
    stream = make_stream()

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_probability", "probability": 0.4})
    stream._process_message({"type": "turn_end_candidate", "text": "first"})
    stream._process_message({"type": "turn_end"})

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "second"})

    preflights = [e for e in emitted(stream) if e.type == SpeechEventType.PREFLIGHT_TRANSCRIPT]
    assert preflights[0].alternatives[0].metadata == {"probability": 0.4}
    assert preflights[1].alternatives[0].metadata is None
