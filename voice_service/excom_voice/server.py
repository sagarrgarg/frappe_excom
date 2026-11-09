"""WebSocket server: /stream for media streams, GET /health for probes."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response

from .call import CallSession
from .config import Settings
from .frappe_client import FrappeClient
from .speech.base import TTS, STTProvider

log = logging.getLogger(__name__)

STREAM_PATH = "/stream"
HEALTH_PATH = "/health"


def process_request(connection: ServerConnection, request: Request) -> Response | None:
    path = urlsplit(request.path).path.rstrip("/") or "/"
    if path == HEALTH_PATH:
        return connection.respond(HTTPStatus.OK, "ok\n")
    if path != STREAM_PATH:
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
    return None


class VoiceApp:
    def __init__(self, settings: Settings, frappe: FrappeClient, stt: STTProvider, tts: TTS) -> None:
        self.settings = settings
        self.frappe = frappe
        self.stt = stt
        self.tts = tts

    async def handler(self, connection: ServerConnection) -> None:
        path = connection.request.path if connection.request else STREAM_PATH
        session = CallSession(connection, path, self.frappe, self.stt, self.tts, self.settings)
        try:
            await session.run()
        except Exception:
            log.exception("call session crashed")

    async def serve(self, host: str | None = None, port: int | None = None, **kwargs: Any) -> Server:
        return await serve(
            self.handler,
            host if host is not None else self.settings.host,
            port if port is not None else self.settings.port,
            process_request=process_request,
            compression=None,
            max_size=2**20,
            **kwargs,
        )


def build_app(settings: Settings) -> VoiceApp:
    frappe = FrappeClient(
        settings.frappe_url,
        settings.voice_key,
        timeout=settings.frappe_timeout_seconds,
        end_retries=settings.end_retries,
        end_retry_backoff=settings.end_retry_backoff_seconds,
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
