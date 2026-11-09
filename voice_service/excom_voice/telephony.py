"""Twilio and Plivo media-stream protocols, normalised to a few events.

Twilio: https://www.twilio.com/docs/voice/media-streams/websocket-messages
Plivo:  https://docs.plivo.com/docs/voice-agents/audio-streaming/concepts/audio-streaming-reference
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit


@dataclass
class Start:
    token: str
    stream_id: str
    call_id: str


@dataclass
class Media:
    audio: bytes


@dataclass
class Mark:
    name: str


@dataclass
class Stop:
    pass


Event = Start | Media | Mark | Stop | None


def _b64(payload: Any) -> bytes:
    if not isinstance(payload, str):
        return b""
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return b""


def _enc(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")


class Telephony:
    """Base class: parse inbound messages, build outbound ones."""

    name = "base"
    # Largest raw-audio frame per outbound message. Plivo caps WebSocket
    # messages at 64 KB; 8000 bytes (1 s of mulaw) is ~10.7 KB in base64.
    max_frame = 8000

    def __init__(self, query_token: str = "") -> None:
        self.stream_id = ""
        self.call_id = ""
        self._query_token = query_token

    def parse(self, msg: dict[str, Any]) -> Event:  # pragma: no cover - abstract
        raise NotImplementedError

    def media(self, audio: bytes) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def mark(self, name: str) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def clear(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError


class Twilio(Telephony):
    name = "twilio"

    def parse(self, msg: dict[str, Any]) -> Event:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start") or {}
            params = start.get("customParameters") or {}
            self.stream_id = str(start.get("streamSid") or msg.get("streamSid") or "")
            self.call_id = str(start.get("callSid") or "")
            token = str(params.get("token") or self._query_token or "")
            return Start(token=token, stream_id=self.stream_id, call_id=self.call_id)
        if event == "media":
            media = msg.get("media") or {}
            # <Connect><Stream> only carries the inbound track; be strict anyway.
            if media.get("track", "inbound") != "inbound":
                return None
            return Media(_b64(media.get("payload")))
        if event == "mark":
            return Mark(str((msg.get("mark") or {}).get("name") or ""))
        if event == "stop":
            return Stop()
        return None  # connected, dtmf, unknown

    def media(self, audio: bytes) -> dict[str, Any]:
        return {"event": "media", "streamSid": self.stream_id, "media": {"payload": _enc(audio)}}

    def mark(self, name: str) -> dict[str, Any]:
        return {"event": "mark", "streamSid": self.stream_id, "mark": {"name": name}}

    def clear(self) -> dict[str, Any]:
        return {"event": "clear", "streamSid": self.stream_id}


def parse_extra_headers(raw: Any) -> dict[str, str]:
    """Plivo's extra_headers: documented as "k1=v1;k2=v2"; accept a dict too."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    out: dict[str, str] = {}
    if isinstance(raw, str):
        for part in raw.split(";"):
            key, sep, value = part.partition("=")
            if sep:
                out[key.strip()] = value.strip()
    return out


def _header_token(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        # Some SIP stacks prefix custom headers (X-PH-); match loosely.
        if key.lower().removeprefix("x-ph-") == "token":
            return value
    return ""


class Plivo(Telephony):
    name = "plivo"

    def parse(self, msg: dict[str, Any]) -> Event:
        event = msg.get("event")
        if event == "start":
            start = msg.get("start") or {}
            self.stream_id = str(start.get("streamId") or msg.get("streamId") or "")
            self.call_id = str(start.get("callId") or "")
            token = ""
            for raw in (
                msg.get("extra_headers"),
                start.get("extra_headers"),
                msg.get("extraHeaders"),
                start.get("extraHeaders"),
            ):
                token = _header_token(parse_extra_headers(raw))
                if token:
                    break
            return Start(token=token or self._query_token, stream_id=self.stream_id, call_id=self.call_id)
        if event == "media":
            media = msg.get("media") or {}
            if media.get("track", "inbound") != "inbound":
                return None
            return Media(_b64(media.get("payload")))
        if event == "playedStream":
            return Mark(str(msg.get("name") or ""))
        if event == "stop":  # not documented over the socket today; harmless
            return Stop()
        return None  # clearedAudio, dtmf, unknown

    def media(self, audio: bytes) -> dict[str, Any]:
        return {
            "event": "playAudio",
            "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": _enc(audio)},
        }

    def mark(self, name: str) -> dict[str, Any]:
        return {"event": "checkpoint", "streamId": self.stream_id, "name": name}

    def clear(self) -> dict[str, Any]:
        return {"event": "clearAudio", "streamId": self.stream_id}


def telephony_for(path: str, first_msg: dict[str, Any] | None = None) -> Telephony:
    """Pick the provider from ?provider=..., else sniff the first message."""
    query = parse_qs(urlsplit(path).query)
    provider = (query.get("provider") or [""])[0].lower()
    token = (query.get("token") or [""])[0]
    if not provider and first_msg is not None:
        start = first_msg.get("start") or {}
        if "streamId" in start or "extra_headers" in first_msg:
            provider = "plivo"
    if provider == "plivo":
        return Plivo(query_token=token)
    return Twilio(query_token=token)
