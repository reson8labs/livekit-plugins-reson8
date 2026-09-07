from __future__ import annotations

from conftest import MakeStream, emitted
from livekit.agents import stt

SpeechEventType = stt.SpeechEventType


def test_turn_lifecycle_emits_speech_events(make_stream: MakeStream) -> None:
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "hello world"})
    stream._process_message({"type": "turn_end"})

    assert [e.type for e in emitted(stream)] == [
        SpeechEventType.START_OF_SPEECH,
        SpeechEventType.PREFLIGHT_TRANSCRIPT,
        SpeechEventType.FINAL_TRANSCRIPT,
        SpeechEventType.END_OF_SPEECH,
    ]
    final = emitted(stream)[2]
    assert final.alternatives[0].text == "hello world"


def test_turn_continuation_discards_candidate(make_stream: MakeStream) -> None:
    stream = make_stream(language="en")

    stream._process_message({"type": "turn_start"})
    stream._process_message({"type": "turn_end_candidate", "text": "maybe done"})
    stream._process_message({"type": "turn_continuation"})
    stream._process_message({"type": "turn_end"})

    # the continuation cleared the candidate, so no FINAL_TRANSCRIPT is emitted,
    # but the turn still ends with END_OF_SPEECH
    assert [e.type for e in emitted(stream)] == [
        SpeechEventType.START_OF_SPEECH,
        SpeechEventType.PREFLIGHT_TRANSCRIPT,
        SpeechEventType.END_OF_SPEECH,
    ]


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
