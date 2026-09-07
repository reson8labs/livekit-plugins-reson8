from __future__ import annotations

from conftest import MakeOpts

from livekit.plugins.reson8.stt import AudioOptions, BiasingOptions, TranscriptOptions


def test_query_params_defaults(make_opts: MakeOpts) -> None:
    params = make_opts().query_params(streaming=True)
    assert params == {"encoding": "pcm_s16le", "sample_rate": "16000", "channels": "1"}


def test_query_params_includes_language_when_set(make_opts: MakeOpts) -> None:
    params = make_opts(language="nl").query_params(streaming=True)
    assert params["language"] == "nl"


def test_query_params_passes_through_multiple_languages(make_opts: MakeOpts) -> None:
    params = make_opts(language="nl,de").query_params(streaming=True)
    assert params["language"] == "nl,de"


def test_query_params_omits_language_when_none(make_opts: MakeOpts) -> None:
    params = make_opts(language=None).query_params(streaming=True)
    assert "language" not in params


def test_query_params_streaming_omits_confidence(make_opts: MakeOpts) -> None:
    # /turns reports no confidence, so the flag is batch-only
    opts = make_opts(transcript=TranscriptOptions(language=True, confidence=True))
    params = opts.query_params(streaming=True)
    assert params.get("include_language") == "true"
    assert "include_confidence" not in params


def test_query_params_batch_sends_both_language_and_confidence(make_opts: MakeOpts) -> None:
    opts = make_opts(transcript=TranscriptOptions(language=True, confidence=True))
    params = opts.query_params(streaming=False)
    assert params.get("include_confidence") == "true"
    assert params.get("include_language") == "true"


def test_query_params_passes_through_transcript_detail_and_model(make_opts: MakeOpts) -> None:
    opts = make_opts(
        transcript=TranscriptOptions(timestamps=True, words=True),
        biasing=BiasingOptions(custom_model_id="m1"),
    )
    params = opts.query_params(streaming=True)
    assert params["include_timestamps"] == "true"
    assert params["include_words"] == "true"
    assert params["custom_model_id"] == "m1"


def test_query_params_describes_the_audio(make_opts: MakeOpts) -> None:
    opts = make_opts(audio=AudioOptions(sample_rate=8000, encoding="mulaw", num_channels=2))
    params = opts.query_params(streaming=True)
    assert params["encoding"] == "mulaw"
    assert params["sample_rate"] == "8000"
    # Reson8 spells it "channels" on the wire; the framework's word is num_channels
    assert params["channels"] == "2"
