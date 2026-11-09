"""One phone call: media stream <-> STT / Frappe / TTS."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from websockets.exceptions import ConnectionClosed

from . import phrases
from .config import Settings
from .frappe_client import FrappeClient, FrappeError
from .speech.base import TTS, SpeechError, STTProvider, STTSession
from .telephony import Mark, Media, Start, Stop, Telephony, telephony_for

log = logging.getLogger(__name__)

# After the last audio is sent, how long past its estimated end we still
# wait for the provider's mark/playedStream before assuming it played.
MARK_GRACE_SECONDS = 1.5
BYTES_PER_SECOND = 8000  # 8 kHz mu-law, one byte per sample

Send = Callable[[dict[str, Any]], Awaitable[None]]


class Speaker:
    """Plays queued lines one after another; can be interrupted.

    "Speaking" means: a line is queued or being synthesised, or audio was sent
    whose mark has not come back yet (with a time-based fallback in case the
    mark never arrives).
    """

    def __init__(
        self,
        send: Send,
        tel: Telephony,
        tts: TTS,
        on_say: Callable[[str], None],
        on_error: Callable[[BaseException], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._tel = tel
        self._tts = tts
        self._on_say = on_say
        self._on_error = on_error
        self._clock = clock
        self.voice = ""
        self.language = "en"
        self._queue: deque[str] = deque()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._runner: asyncio.Task[None] | None = None
        self._marks: set[str] = set()
        self._play_until = 0.0
        self._count = 0

    def start(self) -> None:
        self._runner = asyncio.create_task(self._run())

    def say(self, text: str) -> None:
        if text and text.strip():
            self._queue.append(text.strip())
            self._wake.set()

    def busy(self) -> bool:
        if self._task is not None or self._queue:
            return True
        return bool(self._marks) and self._clock() < self._play_until + MARK_GRACE_SECONDS

    def on_mark(self, name: str) -> None:
        self._marks.discard(name)

    async def interrupt(self) -> bool:
        """Barge-in: drop queued lines, stop synthesis, clear provider audio."""
        was_busy = self.busy()
        self._queue.clear()
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.wait({task})
        if was_busy:
            self._marks.clear()
            self._play_until = 0.0
            await self._send(self._tel.clear())
        return was_busy

    async def wait_idle(self, limit: float) -> None:
        # Polled, not event-driven: busy() also turns false by elapsed time
        # when a provider never echoes the mark.
        deadline = self._clock() + limit
        while self.busy() and self._clock() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        for task in (self._task, self._runner):
            if task is not None:
                task.cancel()
        for task in (self._task, self._runner):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _run(self) -> None:
        while True:
            while not self._queue:
                self._wake.clear()
                await self._wake.wait()
            text = self._queue.popleft()
            task = asyncio.create_task(self._play(text))
            self._task = task
            try:
                await asyncio.wait({task})
            finally:
                self._task = None
            if not task.cancelled() and task.exception() is not None:
                self._on_error(task.exception())

    async def _play(self, text: str) -> None:
        self._on_say(text)
        self._count += 1
        name = f"say-{self._count}"
        frame = self._tel.max_frame
        async for chunk in self._tts.synthesize(text, self.voice, self.language):
            for i in range(0, len(chunk), frame):
                piece = chunk[i : i + frame]
                await self._send(self._tel.media(piece))
                self._play_until = max(self._play_until, self._clock()) + len(piece) / BYTES_PER_SECOND
        self._marks.add(name)
        await self._send(self._tel.mark(name))


class CallSession:
    def __init__(
        self,
        ws: Any,
        path: str,
        frappe: FrappeClient,
        stt_provider: STTProvider,
        tts: TTS,
        settings: Settings,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ws = ws
        self._path = path
        self._frappe = frappe
        self._stt_provider = stt_provider
        self._tts = tts
        self._settings = settings
        self._clock = clock

        self.tel: Telephony | None = None
        self.token = ""
        self.language = "en"
        self.max_seconds = settings.default_max_seconds
        self.turn_limit = settings.default_turn_limit
        self.turns = 0
        self.transcript: list[dict[str, Any]] = []
        self.reason = ""

        self._t0 = 0.0
        self._stt: STTSession | None = None
        self._stt_failed = False
        self._speaker: Speaker | None = None
        self._done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._closing_reason = ""
        self._tasks: set[asyncio.Task[Any]] = set()
        self._conversation: asyncio.Task[None] | None = None
        self._segments: list[str] = []
        self._pending: list[str] = []
        self._pending_ready = asyncio.Event()
        self._log_id = "?"

    # ---------------------------------------------------------------- helpers

    def _now_ms(self) -> int:
        return int((self._clock() - self._t0) * 1000)

    def _record(self, speaker: str, text: str) -> None:
        self.transcript.append({"speaker": speaker, "text": text, "at_ms": self._now_ms()})
        log.debug("[%s] %s: %s", self._log_id, speaker, text)

    async def _send(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("[%s] task failed: %s", self._log_id, type(exc).__name__, exc_info=exc)
            self._finish("error")

    def _finish(self, reason: str) -> None:
        if not self._done.done():
            self._done.set_result(reason)

    def _speaker_error(self, exc: BaseException) -> None:
        if isinstance(exc, ConnectionClosed):
            self._finish(self._closing_reason or "caller_hangup")
            return
        log.error("[%s] playback failed: %s", self._log_id, type(exc).__name__)
        # TTS is down, so an apology can't be spoken either: just end.
        self._closing_reason = self._closing_reason or "error"
        self._finish(self._closing_reason)

    # ------------------------------------------------------------------- run

    async def run(self) -> None:
        try:
            start = await asyncio.wait_for(self._await_start(), timeout=self._settings.start_timeout_seconds)
        except (TimeoutError, ConnectionClosed):
            start = None
        if start is None:
            log.warning("stream closed or timed out before a start event")
            await self._close_ws()
            return
        assert self.tel is not None
        self._t0 = self._clock()
        self._log_id = start.call_id or start.stream_id or "?"
        if not start.token:
            log.warning("[%s] %s stream without a token; closing", self._log_id, self.tel.name)
            await self._close_ws()
            return
        self.token = start.token
        log.info("[%s] %s stream %s started", self._log_id, self.tel.name, start.stream_id)

        self._speaker = Speaker(
            self._send,
            self.tel,
            self._tts,
            on_say=lambda text: self._record("agent", text),
            on_error=self._speaker_error,
            clock=self._clock,
        )
        self._speaker.start()
        self._spawn(self._read_loop())
        self._conversation = self._spawn(self._converse())
        try:
            self.reason = await self._done
        finally:
            await self._cleanup()

    async def _await_start(self) -> Start | None:
        async for raw in self._ws:
            msg = _loads(raw)
            if msg is None:
                continue
            if self.tel is None and msg.get("event") == "start":
                self.tel = telephony_for(self._path, msg)
            elif self.tel is None:
                continue  # Twilio's "connected" comes first
            event = self.tel.parse(msg)
            if isinstance(event, Start):
                return event
            if isinstance(event, Stop):
                return None
        return None

    async def _read_loop(self) -> None:
        assert self.tel is not None and self._speaker is not None
        try:
            async for raw in self._ws:
                msg = _loads(raw)
                if msg is None:
                    continue
                event = self.tel.parse(msg)
                if isinstance(event, Media):
                    await self._feed(event.audio)
                elif isinstance(event, Mark):
                    self._speaker.on_mark(event.name)
                elif isinstance(event, Stop):
                    break
        except ConnectionClosed:
            pass
        self._finish(self._closing_reason or "caller_hangup")

    async def _feed(self, audio: bytes) -> None:
        if self._stt is None or self._stt_failed or not audio:
            return
        try:
            await self._stt.feed(audio)
        except SpeechError as exc:
            self._stt_failed = True
            log.error("[%s] STT feed failed: %s", self._log_id, exc)
            self._spawn(self._end_with(phrases.apology(self.language), "error", interrupt=True))

    async def _converse(self) -> None:
        assert self._speaker is not None
        try:
            info = await self._frappe.start(self.token)
        except FrappeError as exc:
            log.error("[%s] voice.start failed: %s", self._log_id, exc)
            await self._end_with(phrases.apology("en"), "error")
            return
        if not info.ok:
            log.info("[%s] voice.start declined the call", self._log_id)
            await self._end_with(info.say, "agent_end")
            return

        self.language = info.language or "en"
        self.max_seconds = info.max_seconds or self._settings.default_max_seconds
        self.turn_limit = info.turn_limit or self._settings.default_turn_limit
        self._speaker.voice = info.voice
        self._speaker.language = self.language
        log.info(
            "[%s] call %s agent %r lang %s max %ss turns %s",
            self._log_id,
            info.call,
            info.agent_name,
            self.language,
            self.max_seconds,
            self.turn_limit,
        )
        self._spawn(self._watchdog())
        self._speaker.say(info.greeting)

        try:
            self._stt = await self._stt_provider.open(self.language)
        except SpeechError as exc:
            log.error("[%s] STT open failed: %s", self._log_id, exc)
            await self._end_with(phrases.apology(self.language), "error")
            return
        self._spawn(self._listen(self._stt))
        await self._turn_loop()

    async def _turn_loop(self) -> None:
        assert self._speaker is not None
        while True:
            text = await self._next_turn_text()
            self.turns += 1
            try:
                reply = await self._frappe.turn(self.token, text)
            except FrappeError as exc:
                log.error("[%s] voice.turn failed: %s", self._log_id, exc)
                await self._end_with(phrases.apology(self.language), "error")
                return
            if reply.end_call:
                await self._end_with(reply.say, "agent_end")
                return
            self._speaker.say(reply.say)
            if self.turns >= self.turn_limit:
                await self._end_with(phrases.goodbye(self.language), "turn_limit")
                return

    async def _next_turn_text(self) -> str:
        await self._pending_ready.wait()
        self._pending_ready.clear()
        text = " ".join(self._pending)
        self._pending.clear()
        return text

    async def _listen(self, stt: STTSession) -> None:
        assert self._speaker is not None
        words_only = self._settings.barge_in_on == "words"
        try:
            async for ev in stt.events():
                if ev.kind == "speech_started" and not words_only:
                    await self._barge_in()
                elif ev.kind == "partial":
                    if ev.text:
                        await self._barge_in()
                elif ev.kind == "final":
                    if ev.text:
                        await self._barge_in()
                        self._segments.append(ev.text)
                elif ev.kind == "utterance_end":
                    self._utterance_done()
        except SpeechError as exc:
            log.error("[%s] STT failed: %s", self._log_id, exc)
            await self._end_with(phrases.apology(self.language), "error", interrupt=True)

    async def _barge_in(self) -> None:
        assert self._speaker is not None
        if self._closing_reason or not self._speaker.busy():
            return
        try:
            interrupted = await self._speaker.interrupt()
        except ConnectionClosed:
            return  # the read loop will see the hangup
        if interrupted:
            log.info("[%s] barge-in: cleared agent audio", self._log_id)

    def _utterance_done(self) -> None:
        text = " ".join(self._segments).strip()
        self._segments.clear()
        if not text:
            return
        self._record("caller", text)
        if self._closing_reason:
            return
        self._pending.append(text)
        self._pending_ready.set()

    async def _watchdog(self) -> None:
        await asyncio.sleep(max(0, self.max_seconds - (self._clock() - self._t0)))
        log.info("[%s] max_seconds (%s) reached", self._log_id, self.max_seconds)
        await self._end_with(phrases.goodbye(self.language), "max_seconds", interrupt=True)

    async def _end_with(self, text: str, reason: str, *, interrupt: bool = False) -> None:
        """Say a last line, let it play, then end with ``reason``. First caller wins."""
        assert self._speaker is not None
        if self._closing_reason:
            return
        self._closing_reason = reason
        current = asyncio.current_task()
        if self._conversation is not None and self._conversation is not current:
            self._conversation.cancel()
        if interrupt:
            with contextlib.suppress(ConnectionClosed):
                await self._speaker.interrupt()
        self._speaker.say(text)
        await self._speaker.wait_idle(self._settings.drain_timeout_seconds)
        self._finish(reason)

    async def _close_ws(self) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close()

    async def _cleanup(self) -> None:
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=5)
        if self._speaker is not None:
            await self._speaker.stop()
        if self._stt is not None:
            with contextlib.suppress(Exception):
                await self._stt.close()
        if self._segments:  # words the caller said that never became a turn
            self._record("caller", " ".join(self._segments))
            self._segments.clear()
        # Closing the socket ends Twilio's <Connect> / Plivo's <Stream>.
        await self._close_ws()
        duration = max(0, round(self._clock() - self._t0))
        log.info("[%s] ended: %s after %ss, %d turns", self._log_id, self.reason, duration, self.turns)
        await self._frappe.end(self.token, self.transcript, duration, self.reason or "error")


def _loads(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None
