from __future__ import annotations

import json

import pytest
from livekit.agents.types import APIConnectOptions

from livekit import rtc
from livekit.plugins import reson8
from livekit.plugins.reson8.stt import TurnOptions


def test_thresholds_reach_the_query_string(make_opts):
    opts = make_opts(turn=TurnOptions(eager_turn_probability=0.35, final_turn_probability=0.7))
    params = opts.query_params(streaming=True)
    assert params["eager_turn_probability"] == "0.35"
    assert params["final_turn_probability"] == "0.7"


def test_thresholds_are_omitted_for_batch(make_opts):
    """The thresholds only exist on the turns endpoint."""
    opts = make_opts(turn=TurnOptions(eager_turn_probability=0.35, final_turn_probability=0.7))
    params = opts.query_params(streaming=False)
    assert "eager_turn_probability" not in params
    assert "final_turn_probability" not in params


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_out_of_range_probability_raises(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        reson8.STT(api_key="k", final_turn_probability=value)


@pytest.mark.parametrize(
    ("eager", "final"),
    [
        (0.5, 0.4),  # inverted
        (0.9, 0.7),
        (0.95, None),  # above the server's 0.92 default
    ],
)
def test_inverted_thresholds_raise(eager, final):
    with pytest.raises(ValueError, match="must be below"):
        TurnOptions(eager_turn_probability=eager, final_turn_probability=final)


@pytest.mark.parametrize(
    ("eager", "final"),
    [
        (0.5, 0.5),  # both explicit, identical
        (None, 0.5),  # 0.5 == the server's default eager
    ],
)
def test_equal_thresholds_warn(eager, final, caplog):
    """Equal thresholds are legal but pointless: the preflight has no lead."""
    with caplog.at_level("WARNING"):
        TurnOptions(eager_turn_probability=eager, final_turn_probability=final)
    assert caplog.records


@pytest.mark.parametrize(("eager", "final"), [(None, None), (0.35, 0.7)])
def test_sane_thresholds_are_quiet(eager, final, caplog):
    with caplog.at_level("WARNING"):
        TurnOptions(eager_turn_probability=eager, final_turn_probability=final)
    assert not caplog.records


async def test_flush_sends_flush_request(reson8_server, client_session):
    """LiveKit's flush sentinel must become a flush_request on the wire.

    Without this the caller has no way to commit a turn early, and is stuck
    waiting for final_turn_probability to be crossed.
    """
    server = await reson8_server()
    stream = reson8.STT(
        api_key="k",
        api_url=server.api_url,
        language="es",
        http_session=client_session,
    ).stream(conn_options=APIConnectOptions(max_retry=0))

    stream.push_frame(
        rtc.AudioFrame(
            data=b"\x00\x00" * 1600,
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=1600,
        )
    )
    stream.flush()

    try:
        await server.wait_for_text()
    finally:
        await stream.aclose()

    assert json.loads(server.text[0]) == {"type": "flush_request"}
    assert server.audio, "no audio frame reached the server"
    assert server.query["language"] == "es"
