"""Deepgram streaming STT (nova-3) and Aura TTS, both 8 kHz mu-law.

STT: wss://api.deepgram.com/v1/listen
     https://developers.deepgram.com/reference/speech-to-text/listen-streaming
     https://developers.deepgram.com/docs/understanding-end-of-speech-detection
TTS: POST https://api.deepgram.com/v1/speak (streamed, chunked response)
     https://developers.deepgram.com/reference/text-to-speech/speak-request
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .base import SpeechError, STTEvent

log = logging.getLogger(__name__)

LISTEN_URL = "wss://api.deepgram.com/v1/listen"
SPEAK_URL = "https://api.deepgram.com"

# Aura-2 default voice per language. Deepgram has no Hindi TTS voice (as of
# 2026-11); Hindi falls back to the English voice unless Frappe sends one.
DEFAULT_VOICES = {
    "en": "aura-2-thalia-en",
    "es": "aura-2-celeste-es",
    "fr": "aura-2-agathe-fr",
    "de": "aura-2-julius-de",
    "it": "aura-2-livia-it",
    "nl": "aura-2-rhea-nl",
    "ja": "aura-2-fujin-ja",
}
FALLBACK_VOICE = DEFAULT_VOICES["en"]
# Aura's documented per-request limit is 2000 characters.
MAX_TTS_CHARS = 1900
KEEPALIVE_SECONDS = 4.0  # Deepgram closes a stream after ~10 s without data

_DONE = object()


def base_language(language: str) -> str:
    return (language or "").split("-")[0].lower()


def default_voice(language: str) -> str:
    lang = base_language(language)
    voice = DEFAULT_VOICES.get(lang)
    if voice is None:
        log.warning("no default TTS voice for language %r; using %s", language, FALLBACK_VOICE)
        return FALLBACK_VOICE
    return voice


def split_text(text: str, limit: int = MAX_TTS_CHARS) -> list[str]:
    """Split on sentence ends so each piece stays under the TTS limit."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?।])\s+", text):
        while len(sentence) > limit:  # one giant sentence: hard cut on a space
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def listen_url(language: str, *, model: str, endpointing_ms: int, utterance_end_ms: int) -> str:
    lang = (language or "").strip() or "multi"
    params = {
        "model": model,
        "encoding": "mulaw",
        "sample_rate": 8000,
        "channels": 1,
        "language": lang,
        "interim_results": "true",  # required by utterance_end_ms
        "endpointing": endpointing_ms,
        "utterance_end_ms": max(1000, utterance_end_ms),  # Deepgram: 1000 or more
        "vad_events": "true",  # SpeechStarted, for barge-in
        "smart_format": "true",
        "punctuate": "true",
    }
    return f"{LISTEN_URL}?{urlencode(params)}"


class DeepgramSTTSession:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._closing = False
        self._last_send = time.monotonic()
        self._reader = asyncio.create_task(self._read())
        self._keepalive = asyncio.create_task(self._keep_alive())

    async def feed(self, audio: bytes) -> None:
        if self._closing:
            return
        try:
            await self._ws.send(audio)
        except ConnectionClosed as exc:
            raise SpeechError("Deepgram STT connection closed") from exc
        self._last_send = time.monotonic()

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, STTEvent)
            yield item

    async def _keep_alive(self) -> None:
        with contextlib.suppress(ConnectionClosed, asyncio.CancelledError):
            while True:
                await asyncio.sleep(1.0)
                if time.monotonic() - self._last_send >= KEEPALIVE_SECONDS:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    self._last_send = time.monotonic()

    async def _read(self) -> None:
        put = self._queue.put_nowait
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Results":
                    alts = (msg.get("channel") or {}).get("alternatives") or [{}]
                    text = (alts[0].get("transcript") or "").strip()
                    if msg.get("is_final"):
                        if text:
                            put(STTEvent("final", text))
                        if msg.get("speech_final"):
                            put(STTEvent("utterance_end"))
                    elif text:
                        put(STTEvent("partial", text))
                elif kind == "UtteranceEnd":
                    put(STTEvent("utterance_end"))
                elif kind == "SpeechStarted":
                    put(STTEvent("speech_started"))
                elif kind == "Error":
                    put(SpeechError(f"Deepgram STT error: {msg.get('description') or msg}"))
        except (ConnectionClosed, ValueError) as exc:
            if not self._closing:
                put(SpeechError(f"Deepgram STT stream failed: {type(exc).__name__}"))
        else:
            if not self._closing:
                put(SpeechError("Deepgram STT stream closed"))
        finally:
            put(_DONE)

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._keepalive.cancel()
        with contextlib.suppress(ConnectionClosed):
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(self._reader), timeout=2.0)
        await self._ws.close()
        self._reader.cancel()


class DeepgramSTT:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        endpointing_ms: int = 500,
        utterance_end_ms: int = 1000,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpointing_ms = endpointing_ms
        self._utterance_end_ms = utterance_end_ms

    async def open(self, language: str) -> DeepgramSTTSession:
        url = listen_url(
            language,
            model=self._model,
            endpointing_ms=self._endpointing_ms,
            utterance_end_ms=self._utterance_end_ms,
        )
        try:
            ws = await connect(
                url,
                additional_headers={"Authorization": f"Token {self._api_key}"},
                open_timeout=5,
                compression=None,
            )
        except (TimeoutError, OSError, WebSocketException) as exc:
            raise SpeechError(f"Deepgram STT connect failed: {type(exc).__name__}") from exc
        return DeepgramSTTSession(ws)


class DeepgramTTS:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=SPEAK_URL,
            headers={"Authorization": f"Token {api_key}"},
            timeout=httpx.Timeout(15.0, connect=5.0),
            transport=transport,
        )

    async def synthesize(self, text: str, voice: str, language: str) -> AsyncIterator[bytes]:
        model = voice or default_voice(language)
        params = {"model": model, "encoding": "mulaw", "sample_rate": 8000, "container": "none"}
        for piece in split_text(text):
            try:
                async with self._client.stream(
                    "POST", "/v1/speak", params=params, json={"text": piece}
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        raise SpeechError(f"Deepgram TTS HTTP {resp.status_code}")
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.HTTPError as exc:
                raise SpeechError(f"Deepgram TTS failed: {type(exc).__name__}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
