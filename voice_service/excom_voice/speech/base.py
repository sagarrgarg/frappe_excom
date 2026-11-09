"""Pluggable speech interfaces.

STT: ``await provider.open(language)`` returns a session; feed it raw 8 kHz
mu-law bytes and iterate ``session.events()``. Event kinds:

- ``speech_started``: the caller began talking (used for barge-in);
- ``partial``: interim text, may change;
- ``final``: a finalised segment of text (several may make one turn);
- ``utterance_end``: the caller stopped; the finals since the last
  utterance_end form one turn.

TTS: ``tts.synthesize(text, voice, language)`` is an async iterator of raw
8 kHz mu-law chunks (no container/header).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

EventKind = Literal["speech_started", "partial", "final", "utterance_end"]


class SpeechError(Exception):
    """An STT or TTS provider failed."""


@dataclass
class STTEvent:
    kind: EventKind
    text: str = ""


class STTSession(Protocol):
    async def feed(self, audio: bytes) -> None: ...

    def events(self) -> AsyncIterator[STTEvent]: ...

    async def close(self) -> None: ...


class STTProvider(Protocol):
    async def open(self, language: str) -> STTSession: ...


class TTS(Protocol):
    def synthesize(self, text: str, voice: str, language: str) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...
