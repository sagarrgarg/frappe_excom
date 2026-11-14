"""Run the voice service: ``python -m excom_voice`` (or ``excom-voice``)."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .config import Settings
from .server import build_app


async def _main(settings: Settings) -> None:
    app = build_app(settings)
    server = await app.serve()
    log = logging.getLogger("excom_voice")
    log.info("listening on %s:%s", settings.host, settings.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    # Stop accepting new calls; let calls in progress finish, then give their end
    # reports their retry window (systemd/docker give us TimeoutStopSec /
    # stop_grace_period, which must exceed max_seconds + drain + END_RETRY_SECONDS).
    log.info("shutting down; waiting for active calls to finish")
    server.close(close_connections=False)
    await server.wait_closed()
    await app.frappe.drain(settings.end_retry_seconds + settings.frappe_timeout_seconds)
    await app.frappe.aclose()
    await app.tts.aclose()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # websockets logs frames at DEBUG (audio payloads!) and every health probe
    # at INFO; keep library loggers at WARNING whatever LOG_LEVEL says.
    for name in ("websockets", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    problems = settings.check()
    if problems:
        for problem in problems:
            logging.error("config: %s", problem)
        sys.exit(2)
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
