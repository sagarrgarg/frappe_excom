"""Unit tests: protocol parsing, Deepgram request shapes, Frappe client."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from excom_voice import phrases
from excom_voice.config import Settings
from excom_voice.frappe_client import FrappeClient, FrappeError
from excom_voice.server import VoiceApp
from excom_voice.speech.base import SpeechError
from excom_voice.speech.deepgram import (
    DEFAULT_VOICES,
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
    assert DeepgramTTS.languages == frozenset(DEFAULT_VOICES)
    assert "hi" not in DeepgramTTS.languages and "pt" not in DeepgramTTS.languages


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
        "https://f.test",
        "k",
        transport=httpx.MockTransport(handler),
        end_retry_seconds=1,
        end_retry_backoff=0,
    )
    reply = await client.turn("T", "hello")
    assert (reply.say, reply.end_call) == ("Hi", True)
    with pytest.raises(FrappeError):
        await client.start("T")
    assert await client.end("T", [], 3, "error") is False  # 403: refused, not retried, no raise
    await client.aclose()


def test_frappe_error_kinds() -> None:
    assert FrappeError("x", 404).refused and FrappeError("x", 403).refused
    assert not FrappeError("x", 500).refused and not FrappeError("x").refused
    assert FrappeError("x").retryable and FrappeError("x", 502).retryable and FrappeError("x", 429).retryable
    assert not FrappeError("x", 403).retryable and not FrappeError("x", 417).retryable


async def test_end_gives_up_after_its_retry_window() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    client = FrappeClient(
        "https://f.test",
        "k",
        transport=httpx.MockTransport(handler),
        end_retry_seconds=0.1,
        end_retry_backoff=0.02,
    )
    assert await client.end("T", [], 3, "error") is False
    assert 2 <= len(calls) <= 4  # 0.02, 0.04 apart, then the next wait would pass the window
    await client.aclose()


# ------------------------------------------------------------------ config, phrases, server


def test_config_defaults_and_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("HOST", "FRAPPE_URL", "VOICE_KEY", "ALLOW_INSECURE_FRAPPE", "DEFAULT_MAX_SECONDS", "MAX_CALLS")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_env().host == "127.0.0.1"
    assert Settings.from_env().default_max_seconds == 600  # the same default as Frappe's
    key = "k" * 32
    ok = Settings(
        frappe_url="https://erp.example.com", voice_key=key, stt_provider="fake", tts_provider="fake"
    )
    assert ok.check() == []
    plain = replace(ok, frappe_url="http://erp.example.com")
    assert any("https" in p for p in plain.check())
    assert replace(plain, allow_insecure_frappe=True).check() == []
    assert any("32" in p for p in replace(ok, voice_key="short").check())
    monkeypatch.setenv("ALLOW_INSECURE_FRAPPE", "1")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.1, 10.0.0.2")
    env = Settings.from_env()
    assert env.allow_insecure_frappe and env.trusted_proxies == ("10.0.0.1", "10.0.0.2")


def test_every_phrase_table_covers_the_same_languages() -> None:
    tables = [phrases.APOLOGY, phrases.GOODBYE, phrases.STILL_THERE, phrases.IDLE_GOODBYE]
    langs = set(tables[0])
    assert {"en", "es", "pt", "hi", "fr", "de", "it", "nl", "ja"} <= langs
    assert all(set(t) == langs for t in tables)
    assert set(DEFAULT_VOICES) <= langs, "every language with a voice has its lines"
    assert phrases.apology("pt-BR") == phrases.APOLOGY["pt"]
    assert phrases.goodbye("xx") == phrases.GOODBYE["en"]


def test_client_ip_believes_forwarded_for_only_from_the_proxy() -> None:
    from types import SimpleNamespace

    from websockets.datastructures import Headers
    from websockets.http11 import Request

    app = VoiceApp(Settings(), None, None, None)  # type: ignore[arg-type]
    req = Request("/stream", Headers({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}))
    via_proxy = SimpleNamespace(remote_address=("127.0.0.1", 5000))
    direct = SimpleNamespace(remote_address=("198.51.100.7", 5000))
    assert app.client_ip(via_proxy, req) == "203.0.113.9"  # type: ignore[arg-type]
    assert app.client_ip(direct, req) == "198.51.100.7"  # type: ignore[arg-type]
