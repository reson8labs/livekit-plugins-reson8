from __future__ import annotations

import pytest

from livekit.plugins import reson8
from livekit.plugins.reson8 import STT


def test_language_list_is_normalized_to_comma_string() -> None:
    stt = STT(api_key="x", language=["nl", "de"])
    assert stt._opts.language == "nl,de"


def test_literal_codes_accepted_as_language() -> None:
    stt = STT(api_key="x", language=["nl", "de"])
    assert stt._opts.language == "nl,de"


def test_language_none_auto_detects() -> None:
    stt = STT(api_key="x")
    assert stt._opts.language is None


def test_unsupported_language_raises_before_network() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        STT(api_key="x", language="xx")


def test_update_options_validates_language() -> None:
    stt = STT(api_key="x", language="nl")
    with pytest.raises(ValueError, match="unsupported language"):
        stt.update_options(language="xx")


def test_supported_languages_exported() -> None:
    assert set(reson8.SUPPORTED_LANGUAGES) == {
        "de",
        "en",
        "es",
        "fr",
        "fy",
        "it",
        "nl",
        "pl",
        "pt",
        "sv",
    }
