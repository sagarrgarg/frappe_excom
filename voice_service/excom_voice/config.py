"""Settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    frappe_url: str = ""
    voice_key: str = ""
    deepgram_api_key: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    stt_provider: str = "deepgram"
    tts_provider: str = "deepgram"
    deepgram_stt_model: str = "nova-3"
    deepgram_endpointing_ms: int = 500
    deepgram_utterance_end_ms: int = 1000
    # "speech_started": any detected speech interrupts the agent (spec default);
    # "words": wait for the first recognised word (fewer false barge-ins on noise).
    barge_in_on: str = "speech_started"

    # Frappe HTTP
    frappe_timeout_seconds: float = 10.0
    end_retries: int = 3
    end_retry_backoff_seconds: float = 1.0

    # Call guards (Frappe's max_seconds/turn_limit win when present)
    default_max_seconds: int = 900
    default_turn_limit: int = 40
    start_timeout_seconds: float = 10.0
    # How long to wait for the last words to play before closing the stream.
    drain_timeout_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            frappe_url=os.environ.get("FRAPPE_URL", "").rstrip("/"),
            voice_key=os.environ.get("VOICE_KEY", ""),
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=_int("PORT", 8080),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            stt_provider=os.environ.get("STT_PROVIDER", "deepgram").lower(),
            tts_provider=os.environ.get("TTS_PROVIDER", "deepgram").lower(),
            deepgram_stt_model=os.environ.get("DEEPGRAM_STT_MODEL", "nova-3"),
            deepgram_endpointing_ms=_int("DEEPGRAM_ENDPOINTING_MS", 500),
            deepgram_utterance_end_ms=_int("DEEPGRAM_UTTERANCE_END_MS", 1000),
            barge_in_on=os.environ.get("BARGE_IN_ON", "speech_started").lower(),
            frappe_timeout_seconds=_float("FRAPPE_TIMEOUT_SECONDS", 10.0),
            default_max_seconds=_int("DEFAULT_MAX_SECONDS", 900),
            default_turn_limit=_int("DEFAULT_TURN_LIMIT", 40),
        )

    def check(self) -> list[str]:
        """Return a list of configuration problems (empty when fine)."""
        problems = []
        if not self.frappe_url:
            problems.append("FRAPPE_URL is not set")
        if not self.voice_key:
            problems.append("VOICE_KEY is not set")
        if "deepgram" in (self.stt_provider, self.tts_provider) and not self.deepgram_api_key:
            problems.append("DEEPGRAM_API_KEY is not set")
        return problems
