from __future__ import annotations


def test_query_params_defaults(make_opts):
    params = make_opts().query_params(streaming=True)
    assert params == {"encoding": "pcm_s16le", "sample_rate": "16000", "channels": "1"}


def test_query_params_includes_language_when_set(make_opts):
    params = make_opts(language="nl").query_params(streaming=True)
    assert params["language"] == "nl"


def test_query_params_passes_through_multiple_languages(make_opts):
    params = make_opts(language="nl,de").query_params(streaming=True)
    assert params["language"] == "nl,de"


def test_query_params_omits_language_when_none(make_opts):
    params = make_opts(language=None).query_params(streaming=True)
    assert "language" not in params


def test_query_params_streaming_omits_confidence(make_opts):
    opts = make_opts(include_language=True, include_confidence=True)
    params = opts.query_params(streaming=True)

    assert params.get("include_language") == "true"
    assert "include_confidence" not in params


def test_query_params_batch_sends_both_language_and_confidence(make_opts):
    opts = make_opts(include_language=True, include_confidence=True)
    params = opts.query_params(streaming=False)

    assert params.get("include_confidence") == "true"
    assert params.get("include_language") == "true"


def test_query_params_passes_through_timestamps_words_and_model(make_opts):
    opts = make_opts(include_timestamps=True, include_words=True, custom_model_id="m1")
    params = opts.query_params(streaming=True)
    assert params["include_timestamps"] == "true"
    assert params["include_words"] == "true"
    assert params["custom_model_id"] == "m1"
