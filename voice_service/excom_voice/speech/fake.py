"""Deterministic fake STT/TTS for tests and local dry runs (no network).

FakeSTT reads the "audio" it is fed as a tiny script, so a test can drive
it through the real media stream:

- ``b"SAY:hello there"`` -> speech_started, partial, final("hello there"),
  utterance_end;
- ``b"SPEECH"`` -> speech_started only (a barge-in with no words yet);
- ``b"FAIL"`` -> the session errors (as if the provider dropped);
- anything else (silence) -> nothing.

FakeTTS yields ``b"TTS:" + text`` followed by mu-law silence (0xFF), split
into ``chunks`` pieces with ``delay`` seconds between them, so tests can see
what was said and can interrupt a long utterance.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .base import SpeechError, STTEvent

_DONE = object()


class FakeSTTSession:
    def __init__(self, language: str) -> None:
        self.language = language
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self.closed = False

    async def feed(self, audio: bytes) -> None:
        if self.closed:
            raise SpeechError("fake STT closed")
        if audio.startswith(b"SAY:"):
            text = audio[4:].decode("utf-8", "replace")
            for ev in (
                STTEvent("speech_started"),
                STTEvent("partial", text),
                STTEvent("final", text),
                STTEvent("utterance_end"),
            ):
                await self._queue.put(ev)
        elif audio.startswith(b"SPEECH"):
            await self._queue.put(STTEvent("speech_started"))
        elif audio.startswith(b"FAIL"):
            await self._queue.put(SpeechError("fake STT failure"))

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, STTEvent)
            yield item

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self._queue.put(_DONE)


class FakeSTT:
    def __init__(self) -> None:
        self.sessions: list[FakeSTTSession] = []

    async def open(self, language: str) -> FakeSTTSession:
        session = FakeSTTSession(language)
        self.sessions.append(session)
        return session


class FakeTTS:
    def __init__(self, chunks: int = 1, delay: float = 0.0, pad: int = 160) -> None:
        self.chunks = chunks
        self.delay = delay
        self.pad = pad
        self.calls: list[tuple[str, str, str]] = []

    async def synthesize(self, text: str, voice: str, language: str) -> AsyncIterator[bytes]:
        self.calls.append((text, voice, language))
        yield b"TTS:" + text.encode("utf-8")
        for _ in range(self.chunks):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield b"\xff" * self.pad

    async def aclose(self) -> None:
        return None
