"""The three Frappe calls: voice.start, voice.turn, voice.end."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

START = "excom.excom_ai.voice.start"
TURN = "excom.excom_ai.voice.turn"
END = "excom.excom_ai.voice.end"


class FrappeError(Exception):
    """Any failure talking to Frappe: transport, timeout, non-2xx, bad body. ``status`` is the HTTP
    status when there was a response."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status

    @property
    def refused(self) -> bool:
        """Frappe answered that this token or key is not valid (403/404): nothing to apologise for."""
        return self.status in (403, 404)

    @property
    def retryable(self) -> bool:
        return not self.status or self.status in (408, 425, 429) or self.status >= 500


@dataclass
class StartInfo:
    ok: bool
    say: str = ""
    call: str = ""
    agent_name: str = ""
    language: str = "en"
    # The language the agent speaks (Frappe sends "en" when there is no voice for ``language``).
    reply_language: str = ""
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
        end_retry_seconds: float = 120.0,
        end_retry_backoff: float = 1.0,
        end_retry_max_backoff: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Excom-Voice-Key": voice_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._end_retry_seconds = end_retry_seconds
        self._end_backoff = end_retry_backoff
        self._end_max_backoff = end_retry_max_backoff
        self._reports: set[asyncio.Task[bool]] = set()

    async def aclose(self) -> None:
        for task in self._reports:
            task.cancel()
        await self._client.aclose()

    async def ping(self) -> bool:
        """Frappe answers (its own /api/method/ping; no key, nothing written)."""
        try:
            resp = await self._client.get("/api/method/ping")
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(f"/api/method/{method}", json=body)
        except httpx.HTTPError as exc:
            raise FrappeError(f"{method}: {type(exc).__name__}") from exc
        if resp.status_code // 100 != 2:
            raise FrappeError(f"{method}: HTTP {resp.status_code}", resp.status_code)
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
                reply_language=str(m.get("reply_language") or ""),
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
        """Report the call end. Retried with exponential backoff for ``end_retry_seconds`` (a
        Frappe restart or a deploy must not lose the transcript and the callback); never raises."""
        body = {
            "token": token,
            "transcript": transcript,
            "duration_seconds": duration_seconds,
            "reason": reason,
        }
        deadline = time.monotonic() + self._end_retry_seconds
        attempt = 0
        while True:
            attempt += 1
            try:
                await self._post(END, body)
                return True
            except FrappeError as exc:
                delay = min(self._end_backoff * 2 ** (attempt - 1), self._end_max_backoff)
                if not exc.retryable or time.monotonic() + delay > deadline:
                    log.error("voice.end gave up after %d attempts: %s", attempt, exc)
                    return False
                log.warning("voice.end attempt %d failed: %s; retrying in %.1fs", attempt, exc, delay)
                await asyncio.sleep(delay)

    def report_end(
        self,
        token: str,
        transcript: list[dict[str, Any]],
        duration_seconds: int,
        reason: str,
    ) -> asyncio.Task[bool]:
        """end() in the background: the call's socket is already closed, and its retries must not
        hold the call's handler. drain() waits for these at shutdown."""
        task = asyncio.create_task(self.end(token, transcript, duration_seconds, reason))
        self._reports.add(task)
        task.add_done_callback(self._reports.discard)
        return task

    @property
    def pending_reports(self) -> int:
        return len(self._reports)

    async def drain(self, limit: float) -> None:
        """Wait (at most ``limit`` seconds) for end reports still being retried."""
        if self._reports:
            log.info("waiting up to %.0fs for %d end report(s)", limit, len(self._reports))
            await asyncio.wait(set(self._reports), timeout=limit)
