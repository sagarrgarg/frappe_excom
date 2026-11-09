"""The three Frappe calls: voice.start, voice.turn, voice.end."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

START = "excom.excom_ai.voice.start"
TURN = "excom.excom_ai.voice.turn"
END = "excom.excom_ai.voice.end"


class FrappeError(Exception):
    """Any failure talking to Frappe: transport, timeout, non-2xx, bad body."""


@dataclass
class StartInfo:
    ok: bool
    say: str = ""
    call: str = ""
    agent_name: str = ""
    language: str = "en"
    voice: str = ""
    greeting: str = ""
    max_seconds: int = 0
    turn_limit: int = 0


@dataclass
class TurnReply:
    say: str
    end_call: bool


class FrappeClient:
    def __init__(
        self,
        base_url: str,
        voice_key: str,
        *,
        timeout: float = 10.0,
        end_retries: int = 3,
        end_retry_backoff: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Excom-Voice-Key": voice_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._end_retries = end_retries
        self._end_backoff = end_retry_backoff

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(f"/api/method/{method}", json=body)
        except httpx.HTTPError as exc:
            raise FrappeError(f"{method}: {type(exc).__name__}") from exc
        if resp.status_code // 100 != 2:
            raise FrappeError(f"{method}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise FrappeError(f"{method}: response is not JSON") from exc
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            raise FrappeError(f"{method}: response has no message object")
        return message

    async def start(self, token: str) -> StartInfo:
        m = await self._post(START, {"token": token})
        if not m.get("ok"):
            return StartInfo(ok=False, say=str(m.get("say") or ""))
        try:
            return StartInfo(
                ok=True,
                call=str(m.get("call") or ""),
                agent_name=str(m.get("agent_name") or ""),
                language=str(m.get("language") or "en"),
                voice=str(m.get("voice") or ""),
                greeting=str(m.get("greeting") or ""),
                max_seconds=int(m.get("max_seconds") or 0),
                turn_limit=int(m.get("turn_limit") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise FrappeError(f"{START}: malformed response") from exc

    async def turn(self, token: str, text: str) -> TurnReply:
        m = await self._post(TURN, {"token": token, "text": text})
        return TurnReply(say=str(m.get("say") or ""), end_call=bool(m.get("end_call")))

    async def end(
        self,
        token: str,
        transcript: list[dict[str, Any]],
        duration_seconds: int,
        reason: str,
    ) -> bool:
        """Report the call end. Best effort with retries; never raises."""
        body = {
            "token": token,
            "transcript": transcript,
            "duration_seconds": duration_seconds,
            "reason": reason,
        }
        for attempt in range(1, self._end_retries + 1):
            try:
                await self._post(END, body)
                return True
            except FrappeError as exc:
                log.warning("voice.end attempt %d/%d failed: %s", attempt, self._end_retries, exc)
                if attempt < self._end_retries:
                    await asyncio.sleep(self._end_backoff * 2 ** (attempt - 1))
        log.error("voice.end gave up after %d attempts", self._end_retries)
        return False
