from __future__ import annotations

import pytest

from livekit.plugins import reson8
from livekit.plugins.reson8 import STT


def test_language_list_is_normalized_to_comma_string():
    stt = STT(api_key="x", language=["nl", "de"])
    assert stt._opts.language == "nl,de"


def test_enum_members_accepted_as_language():
    stt = STT(
        api_key="x", language=[reson8.SupportedLanguages.DUTCH, reson8.SupportedLanguages.GERMAN]
    )
    assert stt._opts.language == "nl,de"


def test_language_none_auto_detects():
    stt = STT(api_key="x")
    assert stt._opts.language is None


def test_unsupported_language_raises_before_network():
    with pytest.raises(ValueError, match="unsupported language"):
        STT(api_key="x", language="xx")


def test_update_options_validates_language():
    stt = STT(api_key="x", language="nl")
    with pytest.raises(ValueError, match="unsupported language"):
        stt.update_options(language="xx")


def test_supported_languages_exported():
    assert reson8.SupportedLanguages.DUTCH == "nl"
    assert reson8.SupportedLanguages.DUTCH.name == "DUTCH"
    assert {lang.value for lang in reson8.SupportedLanguages} == {
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
