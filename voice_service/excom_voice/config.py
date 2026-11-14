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


def _bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


# The same minimum as the Frappe side (excom_ai.voice.MIN_KEY_LENGTH): a shorter key accepts nothing.
MIN_VOICE_KEY_LENGTH = 32


@dataclass(frozen=True)
class Settings:
    frappe_url: str = ""
    voice_key: str = ""
    deepgram_api_key: str = ""
    # Loopback by default: TLS is terminated in front (Caddy), which proxies to this address.
    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    # FRAPPE_URL must be https (the voice key travels on every request); 1 allows http for local dev.
    allow_insecure_frappe: bool = False

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
    # voice.end is the only thing that saves the transcript and the callback: it is retried in the
    # background with exponential backoff (1 s, 2 s, 4 s ... at most 30 s apart) for this long.
    end_retry_seconds: float = 120.0
    end_retry_backoff_seconds: float = 1.0
    end_retry_max_backoff_seconds: float = 30.0

    # Call guards (Frappe's max_seconds/turn_limit win when present). 600 is also Frappe's default.
    default_max_seconds: int = 600
    default_turn_limit: int = 30
    start_timeout_seconds: float = 10.0
    # How long to wait for the last words to play before closing the stream.
    drain_timeout_seconds: float = 20.0
    # Silence (no caller speech, nothing playing, no reply pending) this long: "are you still
    # there?" once, then the same again: a goodbye and the call ends (reason "idle"). 0 turns it off.
    idle_timeout_seconds: float = 20.0
    # Caller audio that arrives before speech-to-text is connected is kept (the latest this much)
    # and sent once it is.
    pre_stt_buffer_seconds: float = 2.0

    # Abuse guards: /stream is reachable from the internet.
    max_calls: int = 20  # concurrent streams; over it the handshake is refused (503), no TTS spent
    connects_per_ip_per_minute: int = 60  # 0 turns it off; over it the handshake is refused (429)
    # Peers whose X-Forwarded-For is believed (the TLS proxy in front); comma-separated.
    trusted_proxies: tuple[str, ...] = ("127.0.0.1", "::1")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            frappe_url=os.environ.get("FRAPPE_URL", "").rstrip("/"),
            voice_key=os.environ.get("VOICE_KEY", ""),
            deepgram_api_key=os.environ.get("DEEPGRAM_API_KEY", ""),
            host=os.environ.get("HOST", "127.0.0.1"),
            port=_int("PORT", 8080),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            allow_insecure_frappe=_bool("ALLOW_INSECURE_FRAPPE"),
            stt_provider=os.environ.get("STT_PROVIDER", "deepgram").lower(),
            tts_provider=os.environ.get("TTS_PROVIDER", "deepgram").lower(),
            deepgram_stt_model=os.environ.get("DEEPGRAM_STT_MODEL", "nova-3"),
            deepgram_endpointing_ms=_int("DEEPGRAM_ENDPOINTING_MS", 500),
            deepgram_utterance_end_ms=_int("DEEPGRAM_UTTERANCE_END_MS", 1000),
            barge_in_on=os.environ.get("BARGE_IN_ON", "speech_started").lower(),
            frappe_timeout_seconds=_float("FRAPPE_TIMEOUT_SECONDS", 10.0),
            end_retry_seconds=_float("END_RETRY_SECONDS", 120.0),
            default_max_seconds=_int("DEFAULT_MAX_SECONDS", 600),
            default_turn_limit=_int("DEFAULT_TURN_LIMIT", 30),
            idle_timeout_seconds=_float("IDLE_TIMEOUT_SECONDS", 20.0),
            pre_stt_buffer_seconds=_float("PRE_STT_BUFFER_SECONDS", 2.0),
            max_calls=_int("MAX_CALLS", 20),
            connects_per_ip_per_minute=_int("CONNECTS_PER_IP_PER_MINUTE", 60),
            trusted_proxies=tuple(
                p.strip() for p in os.environ.get("TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if p.strip()
            ),
        )

    def check(self) -> list[str]:
        """Return a list of configuration problems (empty when fine)."""
        problems = []
        if not self.frappe_url:
            problems.append("FRAPPE_URL is not set")
        elif not self.frappe_url.startswith("https://") and not self.allow_insecure_frappe:
            problems.append("FRAPPE_URL must be https:// (ALLOW_INSECURE_FRAPPE=1 allows http for local dev)")
        if not self.voice_key:
            problems.append("VOICE_KEY is not set")
        elif len(self.voice_key) < MIN_VOICE_KEY_LENGTH:
            problems.append(f"VOICE_KEY must be at least {MIN_VOICE_KEY_LENGTH} characters")
        if self.max_calls < 1:
            problems.append("MAX_CALLS must be at least 1")
        if "deepgram" in (self.stt_provider, self.tts_provider) and not self.deepgram_api_key:
            problems.append("DEEPGRAM_API_KEY is not set")
        return problems
