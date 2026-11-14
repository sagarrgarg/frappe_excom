"""Test doubles: a fake Frappe (httpx MockTransport), fake speech, and fake
Twilio / Plivo clients that talk to the real server on 127.0.0.1."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State

from excom_voice.config import Settings
from excom_voice.frappe_client import FrappeClient
from excom_voice.server import VoiceApp
from excom_voice.speech.fake import FakeSTT, FakeTTS

FRAPPE_URL = "https://frappe.test"
VOICE_KEY = "test-voice-key"

START_OK = {
    "ok": True,
    "call": "CALL-0001",
    "agent_name": "Asha",
    "language": "en",
    "voice": "",
    "greeting": "Hello, this is Asha from Excom.",
    "max_seconds": 120,
    "turn_limit": 10,
}


@dataclass
class FakeFrappe:
    """Scripted Frappe. Each handler returns a dict (the "message"), an int
    (an HTTP error status), or raises httpx.ConnectError."""

    start: Any = field(default_factory=lambda: dict(START_OK))
    start_delay: float = 0.0
    turn: Callable[[str], Any] = lambda text: {"say": f"You said {text}.", "end_call": False}
    turn_delay: float = 0.0
    end_failures: int = 0
    end_status: int = 502
    ping_ok: bool = True
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    headers: list[httpx.Headers] = field(default_factory=list)
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    in_flight_turns: int = 0
    max_in_flight_turns: int = 0

    def calls(self, method: str) -> list[dict[str, Any]]:
        return [body for m, body in self.requests if m == method]

    @property
    def turn_texts(self) -> list[str]:
        return [b["text"] for b in self.calls("turn")]

    @property
    def end_body(self) -> dict[str, Any]:
        ends = self.calls("end")
        assert ends, "voice.end was not called"
        return ends[-1]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/method/ping":
            self.requests.append(("ping", {}))
            return httpx.Response(200 if self.ping_ok else 502, json={"message": "pong"})
        method = request.url.path.rsplit(".", 1)[-1]
        body = json.loads(request.content)
        self.requests.append((method, body))
        self.headers.append(request.headers)
        if method == "start":
            if self.start_delay:
                await asyncio.sleep(self.start_delay)
            result = self.start
        elif method == "turn":
            self.in_flight_turns += 1
            self.max_in_flight_turns = max(self.max_in_flight_turns, self.in_flight_turns)
            try:
                if self.turn_delay:
                    await asyncio.sleep(self.turn_delay)
                result = self.turn(body["text"])
            finally:
                self.in_flight_turns -= 1
        elif method == "end":
            if self.end_failures > 0:
                self.end_failures -= 1
                return httpx.Response(self.end_status, text="bad gateway")
            self.ended.set()
            result = {"ok": True}
        else:
            return httpx.Response(404)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, int):
            return httpx.Response(result, json={"exc_type": "Error"})
        return httpx.Response(200, json={"message": result})


class ProviderClient:
    """Plays the telephony provider's side of the media stream."""

    def __init__(self, provider: str, ws: ClientConnection, *, auto_mark: bool = True) -> None:
        self.provider = provider
        self.ws = ws
        self.auto_mark = auto_mark
        self.sent: list[dict[str, Any]] = []
        self.closed = asyncio.Event()
        self.stream_id = "MZ123" if provider == "twilio" else "a1b2c3-stream"
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                self.sent.append(msg)
                if self.auto_mark and msg["event"] in ("mark", "checkpoint"):
                    await self.echo_mark(msg)
        except ConnectionClosed:
            pass
        finally:
            self.closed.set()

    async def echo_mark(self, msg: dict[str, Any]) -> None:
        if self.provider == "twilio":
            await self.send(
                {"event": "mark", "streamSid": self.stream_id, "sequenceNumber": "9", "mark": msg["mark"]}
            )
        else:
            await self.send(
                {
                    "event": "playedStream",
                    "sequenceNumber": 9,
                    "streamId": self.stream_id,
                    "name": msg["name"],
                }
            )

    async def send(self, msg: dict[str, Any]) -> None:
        if self.ws.state is not State.OPEN:
            return
        try:
            await self.ws.send(json.dumps(msg))
        except ConnectionClosed:
            pass

    async def start(self, token: str = "tok-123") -> None:
        if self.provider == "twilio":
            await self.send({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            await self.send(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "start": {
                        "streamSid": self.stream_id,
                        "accountSid": "AC1",
                        "callSid": "CA1",
                        "tracks": ["inbound"],
                        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                        "customParameters": {"token": token, "provider": "twilio"},
                    },
                    "streamSid": self.stream_id,
                }
            )
        else:
            await self.send(
                {
                    "event": "start",
                    "sequenceNumber": 1,
                    "start": {
                        "callId": "plivo-call-1",
                        "streamId": self.stream_id,
                        "accountId": "MA1",
                        "tracks": ["inbound"],
                        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
                    },
                    "extra_headers": f"token={token}",
                }
            )

    async def audio(self, data: bytes) -> None:
        payload = base64.b64encode(data).decode()
        if self.provider == "twilio":
            msg = {
                "event": "media",
                "sequenceNumber": "2",
                "media": {"track": "inbound", "chunk": "1", "timestamp": "5", "payload": payload},
                "streamSid": self.stream_id,
            }
        else:
            msg = {
                "event": "media",
                "sequenceNumber": 2,
                "streamId": self.stream_id,
                "media": {"track": "inbound", "timestamp": "1700000000000", "chunk": 1, "payload": payload},
                "extra_headers": "token=tok-123",
            }
        await self.send(msg)

    async def say(self, text: str) -> None:
        await self.audio(b"SAY:" + text.encode())

    async def stop(self) -> None:
        if self.provider == "twilio":
            await self.send(
                {
                    "event": "stop",
                    "sequenceNumber": "50",
                    "stop": {"accountSid": "AC1", "callSid": "CA1"},
                    "streamSid": self.stream_id,
                }
            )
        await self.ws.close()

    # ----- inspection helpers

    def media_messages(self) -> list[dict[str, Any]]:
        kind = "media" if self.provider == "twilio" else "playAudio"
        return [m for m in self.sent if m["event"] == kind]

    def said(self) -> list[str]:
        out = []
        for m in self.media_messages():
            data = base64.b64decode(m["media"]["payload"])
            if data.startswith(b"TTS:"):
                out.append(data[4:].decode())
        return out

    def clears(self) -> list[dict[str, Any]]:
        kind = "clear" if self.provider == "twilio" else "clearAudio"
        return [m for m in self.sent if m["event"] == kind]

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 3.0) -> None:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.01)

    async def wait_said(self, text: str, timeout: float = 3.0) -> None:
        await self.wait_for(lambda: text in self.said(), timeout)


@dataclass
class Harness:
    url: str
    frappe: FakeFrappe
    stt: FakeSTT
    tts: FakeTTS
    settings: Settings
    clients: list[ProviderClient] = field(default_factory=list)

    app: VoiceApp | None = None

    async def connect(self, provider: str, *, auto_mark: bool = True, **kwargs: Any) -> ProviderClient:
        url = self.url + ("/stream?provider=plivo" if provider == "plivo" else "/stream")
        ws = await connect(url, **kwargs)
        client = ProviderClient(provider, ws, auto_mark=auto_mark)
        self.clients.append(client)
        return client

    async def wait_ended(self, timeout: float = 5.0) -> dict[str, Any]:
        async with asyncio.timeout(timeout):
            await self.frappe.ended.wait()
        return self.frappe.end_body


def make_settings(**overrides: Any) -> Settings:
    base = {
        "frappe_url": FRAPPE_URL,
        "voice_key": VOICE_KEY,
        "stt_provider": "fake",
        "tts_provider": "fake",
        "end_retry_backoff_seconds": 0.01,
        "end_retry_seconds": 2.0,
        "start_timeout_seconds": 2.0,
        "drain_timeout_seconds": 3.0,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def frappe() -> FakeFrappe:
    return FakeFrappe()


@pytest.fixture
def tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
async def harness(frappe: FakeFrappe, tts: FakeTTS, settings: Settings) -> AsyncIterator[Harness]:
    client = FrappeClient(
        settings.frappe_url,
        settings.voice_key,
        end_retry_seconds=settings.end_retry_seconds,
        end_retry_backoff=settings.end_retry_backoff_seconds,
        transport=httpx.MockTransport(frappe.handle),
    )
    stt = FakeSTT()
    app = VoiceApp(settings, client, stt, tts)
    server = await app.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    h = Harness(f"ws://127.0.0.1:{port}", frappe, stt, tts, settings, app=app)
    try:
        yield h
    finally:
        for c in h.clients:
            await c.ws.close()
        server.close()
        await server.wait_closed()
        await client.aclose()


@pytest.fixture(params=["twilio", "plivo"])
def provider(request: pytest.FixtureRequest) -> str:
    return request.param
