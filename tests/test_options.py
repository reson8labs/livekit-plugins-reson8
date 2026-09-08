from __future__ import annotations

from collections.abc import Callable, Sequence

import aiohttp
import pytest
from conftest import MakeOpts, StartServer
from livekit.agents.types import APIConnectOptions

from livekit import rtc
from livekit.plugins import reson8
from livekit.plugins.reson8.stt import AudioOptions, BiasingOptions, TranscriptOptions

BuildBiasing = Callable[[Sequence[str]], BiasingOptions]


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


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(
            lambda: AudioOptions(encoding="flac"),  # type: ignore[arg-type]
            r"(?i)unsupported encoding",
            id="encoding",
        ),
        pytest.param(lambda: AudioOptions(num_channels=0), "between 1 and 10", id="no-channels"),
        pytest.param(lambda: AudioOptions(num_channels=11), "between 1 and 10", id="too-many"),
        pytest.param(lambda: AudioOptions(sample_rate=0), "must be positive", id="zero-rate"),
        pytest.param(lambda: AudioOptions(sample_rate=-16000), "must be positive", id="negative"),
    ],
)
def test_bad_audio_options_are_rejected(build: Callable[[], AudioOptions], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


@pytest.mark.parametrize("num_channels", [1, 10])
def test_the_documented_channel_bounds_are_accepted(num_channels: int) -> None:
    assert AudioOptions(num_channels=num_channels).num_channels == num_channels


@pytest.mark.parametrize("encoding", ["pcm_s16le", "mulaw", "alaw"])
def test_every_offered_encoding_is_accepted(encoding: str) -> None:
    assert AudioOptions(encoding=encoding).encoding == encoding  # type: ignore[arg-type]


def test_auto_encoding_is_not_offered() -> None:
    with pytest.raises(ValueError, match=r"(?i)unsupported encoding"):
        AudioOptions(encoding="auto")  # type: ignore[arg-type]


def _frame(num_channels: int) -> rtc.AudioFrame:
    samples = 160
    return rtc.AudioFrame(
        data=b"\x00\x00" * samples * num_channels,
        sample_rate=16000,
        num_channels=num_channels,
        samples_per_channel=samples,
    )


async def test_batch_describes_the_buffer_it_posts(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    """recognize() reports the audio it actually sends, not the configured shape."""

    server = await reson8_server()
    stt = reson8.STT(
        api_key="k",
        base_url=server.api_url,
        audio=AudioOptions(sample_rate=48000, num_channels=1),
        http_session=client_session,
    )

    await stt.recognize(_frame(2), conn_options=APIConnectOptions(max_retry=0))

    assert server.query["sample_rate"] == "16000"
    assert server.query["channels"] == "2"
    assert server.query["encoding"] == "pcm_s16le"


async def test_batch_rejects_a_buffer_with_too_many_channels(
    reson8_server: StartServer, client_session: aiohttp.ClientSession
) -> None:
    """The buffer's own shape is validated too, before anything is posted."""

    server = await reson8_server()
    stt = reson8.STT(api_key="k", base_url=server.api_url, http_session=client_session)

    with pytest.raises(ValueError, match="between 1 and 10"):
        await stt.recognize(_frame(11), conn_options=APIConnectOptions(max_retry=0))

    assert not server.post_headers, "nothing should have been posted"


def test_phrases_reach_the_wire_comma_joined(make_opts: MakeOpts) -> None:
    opts = make_opts(biasing=BiasingOptions(phrases=["reson8", "livekit"]))
    assert opts.query_params(streaming=True)["phrases"] == "reson8,livekit"


def test_patterns_reach_the_wire_comma_joined(make_opts: MakeOpts) -> None:
    opts = make_opts(biasing=BiasingOptions(patterns=["AMZ[0-9]{6}", "[A-Z]{2} [0-9]{3}"]))
    assert opts.query_params(streaming=True)["patterns"] == "AMZ[0-9]{6},[A-Z]{2} [0-9]{3}"


def test_strength_is_sent_as_bias_strength(make_opts: MakeOpts) -> None:
    opts = make_opts(biasing=BiasingOptions(strength=0.8))
    assert opts.query_params(streaming=True)["bias_strength"] == "0.8"


def test_zero_strength_is_sent_rather_than_treated_as_unset(make_opts: MakeOpts) -> None:
    # 0 is a value the server accepts, so it must survive the emission check;
    # `if self.strength` would drop it and silently fall back to the default
    opts = make_opts(biasing=BiasingOptions(strength=0))
    assert opts.query_params(streaming=True)["bias_strength"] == "0"


def test_filler_mode_reaches_the_wire(make_opts: MakeOpts) -> None:
    opts = make_opts(transcript=TranscriptOptions(filler_mode="verbatim"))
    assert opts.query_params(streaming=True)["filler_mode"] == "verbatim"


@pytest.mark.parametrize("streaming", [True, False])
def test_biasing_applies_to_both_endpoints(make_opts: MakeOpts, streaming: bool) -> None:
    """Unlike diarization, all of these are documented on turns and prerecorded."""

    opts = make_opts(
        biasing=BiasingOptions(phrases=["a"], strength=0.5),
        transcript=TranscriptOptions(filler_mode="clean"),
    )
    params = opts.query_params(streaming=streaming)

    assert params["phrases"] == "a"
    assert params["bias_strength"] == "0.5"
    assert params["filler_mode"] == "clean"


@pytest.mark.parametrize("streaming", [True, False])
def test_patterns_apply_to_both_endpoints(make_opts: MakeOpts, streaming: bool) -> None:
    opts = make_opts(biasing=BiasingOptions(patterns=["[0-9]{4}"]))
    assert opts.query_params(streaming=streaming)["patterns"] == "[0-9]{4}"


def test_omitted_biasing_sends_nothing(make_opts: MakeOpts) -> None:
    params = make_opts().query_params(streaming=True)

    for key in ("phrases", "patterns", "bias_strength", "filler_mode", "custom_model_id"):
        assert key not in params


def test_too_many_phrases_raises() -> None:
    with pytest.raises(ValueError, match="at most 250 entries"):
        BiasingOptions(phrases=[f"p{i}" for i in range(251)])


def test_the_documented_phrase_maximum_is_accepted() -> None:
    phrases = [f"p{i}" for i in range(250)]
    assert BiasingOptions(phrases=phrases).phrases == phrases


COMMA_JOINED_FIELDS = [
    pytest.param(lambda values: BiasingOptions(phrases=values), id="phrases"),
    pytest.param(lambda values: BiasingOptions(patterns=values), id="patterns"),
]


def test_a_comma_inside_a_phrase_raises() -> None:
    with pytest.raises(ValueError, match="may contain a comma"):
        BiasingOptions(phrases=["fine", "not,fine"])


@pytest.mark.parametrize("build", COMMA_JOINED_FIELDS)
def test_an_empty_entry_raises(build: BuildBiasing) -> None:
    with pytest.raises(ValueError, match="empty entry"):
        build(["ok", "  "])


def test_negative_strength_raises() -> None:
    with pytest.raises(ValueError, match="must be non-negative"):
        BiasingOptions(strength=-0.1)


def test_unsupported_filler_mode_raises() -> None:
    with pytest.raises(ValueError, match=r"(?i)unsupported filler_mode"):
        TranscriptOptions(filler_mode="loud")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: BiasingOptions(phrases="Reson8"), id="phrases"),
        pytest.param(lambda: BiasingOptions(patterns="[0-9]{4}"), id="patterns"),
    ],
)
def test_a_bare_string_is_rejected(build: Callable[[], BiasingOptions]) -> None:
    with pytest.raises(ValueError, match="takes a sequence of strings"):
        build()


@pytest.mark.parametrize(
    "pattern",
    ["[0-9]{4,6}", "(INV)?[0-9]{4,5}", "AMZ[0-9]{6}", "[A-Z]{2}[0-9]{2} [A-Z]{3}"],
)
def test_braced_ranges_survive_validation(pattern: str) -> None:
    assert BiasingOptions(patterns=[pattern]).patterns == [pattern]


@pytest.mark.parametrize("pattern", ["a,b", "AMZ[0-9]{6},X"])
def test_a_comma_outside_braces_still_raises(pattern: str) -> None:
    with pytest.raises(ValueError, match="outside"):
        BiasingOptions(patterns=[pattern])
