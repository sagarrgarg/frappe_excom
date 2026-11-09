"""Unit tests: protocol parsing, Deepgram request shapes, Frappe client."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from excom_voice.frappe_client import FrappeClient, FrappeError
from excom_voice.speech.base import SpeechError
from excom_voice.speech.deepgram import (
    DeepgramSTTSession,
    DeepgramTTS,
    default_voice,
    listen_url,
    split_text,
)
from excom_voice.telephony import (
    Mark,
    Media,
    Plivo,
    Start,
    Stop,
    Twilio,
    parse_extra_headers,
    telephony_for,
)

# ------------------------------------------------------------------ telephony


def test_twilio_parse_and_build() -> None:
    t = Twilio()
    start = t.parse(
        {
            "event": "start",
            "start": {"streamSid": "MZ1", "callSid": "CA1", "customParameters": {"token": "T"}},
            "streamSid": "MZ1",
        }
    )
    assert start == Start(token="T", stream_id="MZ1", call_id="CA1")
    payload = base64.b64encode(b"\x7f\xff").decode()
    assert t.parse({"event": "media", "media": {"track": "inbound", "payload": payload}}) == Media(
        b"\x7f\xff"
    )
    assert t.parse({"event": "mark", "mark": {"name": "say-1"}}) == Mark("say-1")
    assert t.parse({"event": "stop", "stop": {}}) == Stop()
    assert t.parse({"event": "connected"}) is None
    assert t.media(b"\xff") == {"event": "media", "streamSid": "MZ1", "media": {"payload": "/w=="}}
    assert t.mark("m") == {"event": "mark", "streamSid": "MZ1", "mark": {"name": "m"}}
    assert t.clear() == {"event": "clear", "streamSid": "MZ1"}


@pytest.mark.parametrize(
    "msg",
    [
        {"extra_headers": "token=T;lang=en"},
        {"extra_headers": {"token": "T"}},
        {"extra_headers": "X-PH-token=T"},
        {"extraHeaders": "token=T"},
    ],
)
def test_plivo_token_from_extra_headers(msg: dict) -> None:
    p = Plivo()
    start = p.parse({"event": "start", "start": {"streamId": "S1", "callId": "C1"}, **msg})
    assert start == Start(token="T", stream_id="S1", call_id="C1")


def test_plivo_token_with_equals_sign_and_query_fallback() -> None:
    assert parse_extra_headers("token=ab=cd;x=1") == {"token": "ab=cd", "x": "1"}
    p = telephony_for("/stream?provider=plivo&token=Q")
    assert isinstance(p, Plivo)
    assert p.parse({"event": "start", "start": {"streamId": "S"}}).token == "Q"


def test_plivo_build() -> None:
    p = Plivo()
    p.parse({"event": "start", "start": {"streamId": "S1", "callId": "C1"}, "extra_headers": "token=T"})
    assert p.media(b"\xff") == {
        "event": "playAudio",
        "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": "/w=="},
    }
    assert p.mark("m") == {"event": "checkpoint", "streamId": "S1", "name": "m"}
    assert p.clear() == {"event": "clearAudio", "streamId": "S1"}
    assert p.parse({"event": "playedStream", "streamId": "S1", "name": "m"}) == Mark("m")
    assert p.parse({"event": "clearedAudio", "streamId": "S1"}) is None


def test_provider_detection() -> None:
    assert isinstance(telephony_for("/stream"), Twilio)
    assert isinstance(telephony_for("/stream?provider=plivo"), Plivo)
    plivo_start = {"event": "start", "start": {"streamId": "S", "callId": "C"}, "extra_headers": ""}
    assert isinstance(telephony_for("/stream", plivo_start), Plivo)
    twilio_start = {"event": "start", "start": {"streamSid": "MZ"}, "streamSid": "MZ"}
    assert isinstance(telephony_for("/stream", twilio_start), Twilio)


# ------------------------------------------------------------------ deepgram


def test_listen_url_params() -> None:
    q = parse_qs(urlsplit(listen_url("es", model="nova-3", endpointing_ms=500, utterance_end_ms=800)).query)
    flat = {k: v[0] for k, v in q.items()}
    assert flat == {
        "model": "nova-3",
        "encoding": "mulaw",
        "sample_rate": "8000",
        "channels": "1",
        "language": "es",
        "interim_results": "true",
        "endpointing": "500",
        "utterance_end_ms": "1000",  # clamped to Deepgram's minimum
        "vad_events": "true",
        "smart_format": "true",
        "punctuate": "true",
    }
    assert "language=multi" in listen_url("", model="nova-3", endpointing_ms=500, utterance_end_ms=1000)


def test_default_voice() -> None:
    assert default_voice("en") == "aura-2-thalia-en"
    assert default_voice("es-MX") == "aura-2-celeste-es"
    assert default_voice("hi") == "aura-2-thalia-en"  # no Hindi Aura voice


def test_split_text_respects_limit() -> None:
    assert split_text("  short  ") == ["short"]
    assert split_text("") == []
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    pieces = split_text(text, limit=300)
    assert all(len(p) <= 300 for p in pieces)
    assert " ".join(pieces) == text
    long_word_run = "word " * 200
    assert all(len(p) <= 100 for p in split_text(long_word_run, limit=100))


async def test_deepgram_tts_request_and_streaming() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\xff" * 4000)

    tts = DeepgramTTS("dg-key", transport=httpx.MockTransport(handler))
    audio = b"".join([c async for c in tts.synthesize("Hello there.", "", "es")])
    await tts.aclose()
    assert audio == b"\xff" * 4000
    req = seen[0]
    assert req.url.path == "/v1/speak"
    assert dict(req.url.params) == {
        "model": "aura-2-celeste-es",
        "encoding": "mulaw",
        "sample_rate": "8000",
        "container": "none",
    }
    assert req.headers["Authorization"] == "Token dg-key"
    assert json.loads(req.content) == {"text": "Hello there."}


async def test_deepgram_tts_error_raises() -> None:
    tts = DeepgramTTS("k", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(SpeechError):
        async for _ in tts.synthesize("hi", "aura-2-thalia-en", "en"):
            pass
    await tts.aclose()


class FakeDGSocket:
    """Stands in for Deepgram's websocket: replays canned server messages."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = [json.dumps(m) for m in messages]
        self.sent: list[object] = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._messages:
            yield m
        raise ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass


def _results(text: str, is_final: bool, speech_final: bool = False) -> dict:
    return {
        "type": "Results",
        "is_final": is_final,
        "speech_final": speech_final,
        "channel": {"alternatives": [{"transcript": text, "confidence": 0.9}]},
    }


async def test_deepgram_stt_event_mapping() -> None:
    ws = FakeDGSocket(
        [
            {"type": "Metadata", "request_id": "r"},
            {"type": "SpeechStarted", "channel": [0], "timestamp": 0.1},
            _results("do you", False),
            _results("Do you ship", True),
            _results("to Texas?", True, speech_final=True),
            {"type": "UtteranceEnd", "channel": [0], "last_word_end": 2.1},
        ]
    )
    session = DeepgramSTTSession(ws)  # type: ignore[arg-type]
    events = []
    with pytest.raises(SpeechError):  # the socket then closes unexpectedly
        async for ev in session.events():
            events.append((ev.kind, ev.text))
    assert events == [
        ("speech_started", ""),
        ("partial", "do you"),
        ("final", "Do you ship"),
        ("final", "to Texas?"),
        ("utterance_end", ""),
        ("utterance_end", ""),
    ]
    await session.feed(b"\xff")
    assert ws.sent[-1] == b"\xff"
    await session.close()
    assert json.loads(ws.sent[-1]) == {"type": "CloseStream"}


# ------------------------------------------------------------------ frappe client


async def test_frappe_client_unwraps_message_and_raises_on_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Excom-Voice-Key"] == "k"
        if request.url.path.endswith("voice.turn"):
            return httpx.Response(200, json={"message": {"say": "Hi", "end_call": True}})
        if request.url.path.endswith("voice.start"):
            return httpx.Response(200, json={"exc": "boom"})  # no "message"
        return httpx.Response(403)

    client = FrappeClient(
        "https://f.test", "k", transport=httpx.MockTransport(handler), end_retries=2, end_retry_backoff=0
    )
    reply = await client.turn("T", "hello")
    assert (reply.say, reply.end_call) == ("Hi", True)
    with pytest.raises(FrappeError):
        await client.start("T")
    assert await client.end("T", [], 3, "error") is False  # 403 twice, no raise
    await client.aclose()
