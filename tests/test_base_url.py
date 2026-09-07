from __future__ import annotations

import pytest

from livekit.plugins import reson8

HOSTED = "https://api.reson8.dev"
SELF_HOSTED = "https://stt.internal.example"
OTHER = "https://other.example"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESON8_BASE_URL", raising=False)
    monkeypatch.delenv("RESON8_API_URL", raising=False)


def test_defaults_to_the_hosted_api() -> None:
    assert reson8.STT(api_key="k")._base_url == HOSTED


def test_argument_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESON8_BASE_URL", OTHER)
    monkeypatch.setenv("RESON8_API_URL", OTHER)
    assert reson8.STT(api_key="k", base_url=SELF_HOSTED)._base_url == SELF_HOSTED


def test_reads_the_current_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESON8_BASE_URL", SELF_HOSTED)
    assert reson8.STT(api_key="k")._base_url == SELF_HOSTED


def test_honours_the_renamed_env_var_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A deployment on the old name must not be redirected to the public API.

    Passing the removed ``api_url=`` raises, but the env var fails open: without
    this fallback the endpoint would silently become api.reson8.dev, sending
    audio and the API key to the wrong host.
    """
    monkeypatch.setenv("RESON8_API_URL", SELF_HOSTED)

    with caplog.at_level("WARNING"):
        stt = reson8.STT(api_key="k")

    assert stt._base_url == SELF_HOSTED
    assert "RESON8_API_URL is deprecated" in caplog.text


def test_current_env_var_wins_over_the_renamed_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESON8_BASE_URL", SELF_HOSTED)
    monkeypatch.setenv("RESON8_API_URL", OTHER)
    assert reson8.STT(api_key="k")._base_url == SELF_HOSTED


def test_trailing_slash_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESON8_BASE_URL", f"{SELF_HOSTED}/")
    assert reson8.STT(api_key="k")._base_url == SELF_HOSTED


def test_the_removed_argument_fails_loudly() -> None:
    with pytest.raises(TypeError, match="api_url"):
        reson8.STT(api_key="k", api_url=SELF_HOSTED)  # type: ignore[call-arg]
