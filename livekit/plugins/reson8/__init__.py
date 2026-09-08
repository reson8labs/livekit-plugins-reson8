"""Reson8 plugin for LiveKit Agents.

Support for speech-to-text with [Reson8](https://reson8.dev), including
server-side turn detection.

See https://docs.reson8.dev/integrations/livekit/ for more information.
"""

from livekit.agents import Plugin

from ._utils import SUPPORTED_LANGUAGES, Encoding, FillerMode, SupportedLanguage
from .log import logger
from .stt import (
    STT,
    AudioOptions,
    BiasingOptions,
    SpeechStream,
    TranscriptOptions,
    TurnOptions,
)
from .version import __version__

__all__ = [
    "STT",
    "SUPPORTED_LANGUAGES",
    "AudioOptions",
    "BiasingOptions",
    "Encoding",
    "FillerMode",
    "SpeechStream",
    "SupportedLanguage",
    "TranscriptOptions",
    "TurnOptions",
    "__version__",
]


class Reson8Plugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(Reson8Plugin())

_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__ = {}

for n in NOT_IN_ALL:
    __pdoc__[n] = False
