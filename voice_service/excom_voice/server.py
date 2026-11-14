"""WebSocket server: /stream for media streams, GET /health for probes."""

from __future__ import annotations

import logging
import time
from collections import deque
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

from .call import CallSession
from .config import Settings
from .frappe_client import FrappeClient
from .speech.base import TTS, STTProvider

log = logging.getLogger(__name__)

STREAM_PATH = "/stream"
HEALTH_PATH = "/health"
# /health?frappe=1 asks Frappe at most this often (a load balancer may probe every few seconds).
HEALTH_CACHE_SECONDS = 30.0
RATE_WINDOW_SECONDS = 60.0
# Close code for a stream refused after the handshake (over the call cap): "try again later".
TRY_AGAIN_LATER = 1013


class VoiceApp:
    def __init__(self, settings: Settings, frappe: FrappeClient, stt: STTProvider, tts: TTS) -> None:
        self.settings = settings
        self.frappe = frappe
        self.stt = stt
        self.tts = tts
        self.active = 0  # streams being handled (calls, and connections waiting for their start)
        self._connects: dict[str, deque[float]] = {}
        self._frappe_ok: tuple[float, bool] | None = None

    # ------------------------------------------------------------ handshake

    def client_ip(self, connection: ServerConnection, request: Request) -> str:
        """The caller's address: the peer, or (when the peer is the TLS proxy in front) the last
        X-Forwarded-For entry, which that proxy wrote."""
        peer = connection.remote_address[0] if connection.remote_address else ""
        if peer in self.settings.trusted_proxies:
            forwarded = request.headers.get("X-Forwarded-For", "")
            last = forwarded.split(",")[-1].strip()
            if last:
                return last
        return peer

    def _over_rate(self, ip: str) -> bool:
        limit = self.settings.connects_per_ip_per_minute
        if limit <= 0:
            return False
        now = time.monotonic()
        if len(self._connects) > 10_000:  # bounded memory under a spray of addresses
            self._connects = {
                k: v for k, v in self._connects.items() if v and now - v[-1] < RATE_WINDOW_SECONDS
            }
        seen = self._connects.setdefault(ip, deque())
        while seen and now - seen[0] >= RATE_WINDOW_SECONDS:
            seen.popleft()
        if len(seen) >= limit:
            return True
        seen.append(now)
        return False

    async def frappe_reachable(self) -> bool:
        now = time.monotonic()
        if self._frappe_ok is None or now - self._frappe_ok[0] >= HEALTH_CACHE_SECONDS:
            self._frappe_ok = (now, await self.frappe.ping())
        return self._frappe_ok[1]

    async def process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        url = urlsplit(request.path)
        path = url.path.rstrip("/") or "/"
        if path == HEALTH_PATH:
            body = f"ok\ncalls: {self.active}/{self.settings.max_calls}\n"
            if parse_qs(url.query).get("frappe", [""])[0] in ("1", "true", "yes"):
                if not await self.frappe_reachable():
                    return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, body + "frappe: unreachable\n")
                body += "frappe: ok\n"
            return connection.respond(HTTPStatus.OK, body)
        if path != STREAM_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        ip = self.client_ip(connection, request)
        if self._over_rate(ip):
            log.warning(
                "stream from %s refused: over %d connects a minute",
                ip,
                self.settings.connects_per_ip_per_minute,
            )
            return connection.respond(HTTPStatus.TOO_MANY_REQUESTS, "too many requests\n")
        if self.active >= self.settings.max_calls:
            log.warning("stream from %s refused: %d calls already (MAX_CALLS)", ip, self.active)
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "busy\n")
        return None

    # --------------------------------------------------------------- streams

    async def handler(self, connection: ServerConnection) -> None:
        if self.active >= self.settings.max_calls:  # several handshakes passed the check at once
            await connection.close(TRY_AGAIN_LATER, "busy")
            return
        self.active += 1
        try:
            path = connection.request.path if connection.request else STREAM_PATH
            session = CallSession(connection, path, self.frappe, self.stt, self.tts, self.settings)
            try:
                await session.run()
            except Exception:
                log.exception("call session crashed")
        finally:
            self.active -= 1

    async def serve(self, host: str | None = None, port: int | None = None, **kwargs: Any) -> Server:
        return await serve(
            self.handler,
            host if host is not None else self.settings.host,
            port if port is not None else self.settings.port,
            process_request=self.process_request,
            compression=None,
            max_size=2**20,
            **kwargs,
        )


def build_app(settings: Settings) -> VoiceApp:
    frappe = FrappeClient(
        settings.frappe_url,
        settings.voice_key,
        timeout=settings.frappe_timeout_seconds,
        end_retry_seconds=settings.end_retry_seconds,
        end_retry_backoff=settings.end_retry_backoff_seconds,
        end_retry_max_backoff=settings.end_retry_max_backoff_seconds,
    )
    stt: STTProvider
    tts: TTS
    if settings.stt_provider == "fake":
        from .speech.fake import FakeSTT

        stt = FakeSTT()
    else:
        from .speech.deepgram import DeepgramSTT

        stt = DeepgramSTT(
            settings.deepgram_api_key,
            model=settings.deepgram_stt_model,
            endpointing_ms=settings.deepgram_endpointing_ms,
            utterance_end_ms=settings.deepgram_utterance_end_ms,
        )
    if settings.tts_provider == "fake":
        from .speech.fake import FakeTTS

        tts = FakeTTS()
    else:
        from .speech.deepgram import DeepgramTTS

        tts = DeepgramTTS(settings.deepgram_api_key)
    return VoiceApp(settings, frappe, stt, tts)
