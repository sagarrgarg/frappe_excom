"""End-to-end call flows over a real WebSocket on 127.0.0.1, for both
Twilio and Plivo, with fake speech providers and a fake Frappe."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from websockets.exceptions import InvalidStatus

from excom_voice import phrases

from .conftest import START_OK, VOICE_KEY, Harness


async def test_start_plays_greeting(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start("tok-abc")
    await client.wait_said(START_OK["greeting"])

    start = harness.frappe.calls("start")
    assert start == [{"token": "tok-abc"}]
    assert harness.frappe.headers[0]["X-Excom-Voice-Key"] == VOICE_KEY
    # Default voice for the language is chosen by the TTS (empty voice passed through).
    assert harness.tts.calls[0] == (START_OK["greeting"], "", "en")
    assert harness.stt.sessions[0].language == "en"

    media = client.media_messages()[0]
    if provider == "twilio":
        assert media["streamSid"] == client.stream_id
        assert set(media["media"]) == {"payload"}
        marks = [m for m in client.sent if m["event"] == "mark"]
        assert marks and marks[0]["streamSid"] == client.stream_id and marks[0]["mark"]["name"]
    else:
        assert media["media"]["contentType"] == "audio/x-mulaw"
        assert media["media"]["sampleRate"] == 8000
        marks = [m for m in client.sent if m["event"] == "checkpoint"]
        assert marks and marks[0]["streamId"] == client.stream_id and marks[0]["name"]


async def test_utterance_becomes_turn_and_reply(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])

    await client.say("Do you ship gulal to Texas?")
    await client.wait_said("You said Do you ship gulal to Texas?.")
    assert harness.frappe.turn_texts == ["Do you ship gulal to Texas?"]
    assert harness.frappe.calls("turn")[0]["token"] == "tok-123"


async def test_barge_in_sends_clear_and_stops_speech(harness: Harness, provider: str) -> None:
    harness.tts.chunks = 100  # a long greeting: ~2.5 s of synthesis
    harness.tts.delay = 0.025
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])

    await client.audio(b"SPEECH")  # the caller starts talking over the agent
    await client.wait_for(lambda: len(client.clears()) == 1)
    clear = client.clears()[0]
    if provider == "twilio":
        assert clear == {"event": "clear", "streamSid": client.stream_id}
    else:
        assert clear == {"event": "clearAudio", "streamId": client.stream_id}

    # Synthesis stopped: no more audio after the clear.
    count = len(client.media_messages())
    await asyncio.sleep(0.2)
    assert len(client.media_messages()) == count
    assert count < 1 + harness.tts.chunks

    # Silence from the caller while the agent is quiet sends no further clears.
    await client.audio(b"SPEECH")
    await asyncio.sleep(0.1)
    assert len(client.clears()) == 1


async def test_no_clear_when_agent_is_quiet(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await asyncio.sleep(0.1)  # the mark comes back: playback finished
    await client.audio(b"SPEECH")
    await asyncio.sleep(0.1)
    assert client.clears() == []


async def test_end_call_says_reply_then_closes(harness: Harness, provider: str) -> None:
    harness.frappe.turn = lambda text: {"say": "Thanks, goodbye!", "end_call": True}
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("That's all, thanks")

    end = await harness.wait_ended()
    await asyncio.wait_for(client.closed.wait(), 2)
    assert client.said()[-1] == "Thanks, goodbye!"
    assert end["reason"] == "agent_end"
    assert end["token"] == "tok-123"
    assert [(t["speaker"], t["text"]) for t in end["transcript"]] == [
        ("agent", START_OK["greeting"]),
        ("caller", "That's all, thanks"),
        ("agent", "Thanks, goodbye!"),
    ]
    at = [t["at_ms"] for t in end["transcript"]]
    assert at == sorted(at) and all(isinstance(x, int) for x in at)
    assert isinstance(end["duration_seconds"], int)
    # Nothing is sent after the final line's mark: just the close.
    assert client.sent[-1]["event"] in ("mark", "checkpoint")


async def test_start_http_error_apologises_and_ends(harness: Harness, provider: str) -> None:
    harness.frappe.start = 500
    client = await harness.connect(provider)
    await client.start()
    end = await harness.wait_ended()
    await asyncio.wait_for(client.closed.wait(), 2)
    assert client.said() == [phrases.apology("en")]
    assert end["reason"] == "error"
    assert end["transcript"][0]["text"] == phrases.apology("en")
    assert harness.stt.sessions == []  # no paid STT stream for a failed start


async def test_start_network_error_apologises_and_ends(harness: Harness) -> None:
    harness.frappe.start = httpx.ConnectError("refused")
    client = await harness.connect("twilio")
    await client.start()
    end = await harness.wait_ended()
    assert client.said() == [phrases.apology("en")]
    assert end["reason"] == "error"


async def test_turn_error_apologises_in_call_language(harness: Harness, provider: str) -> None:
    harness.frappe.start = {**START_OK, "language": "es", "greeting": "Hola, soy Asha."}
    harness.frappe.turn = lambda text: 503
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said("Hola, soy Asha.")
    await client.say("Hola")
    end = await harness.wait_ended()
    await asyncio.wait_for(client.closed.wait(), 2)
    assert client.said()[-1] == phrases.apology("es")
    assert end["reason"] == "error"
    assert ("caller", "Hola") in [(t["speaker"], t["text"]) for t in end["transcript"]]


async def test_start_declined_says_message_and_ends(harness: Harness, provider: str) -> None:
    harness.frappe.start = {"ok": False, "say": "Our office is closed. Goodbye."}
    client = await harness.connect(provider)
    await client.start()
    end = await harness.wait_ended()
    await asyncio.wait_for(client.closed.wait(), 2)
    assert client.said() == ["Our office is closed. Goodbye."]
    assert end["reason"] == "agent_end"


async def test_caller_hangup_reports_transcript(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("Send me a price list")
    await client.wait_said("You said Send me a price list.")
    await client.stop()

    end = await harness.wait_ended()
    assert end["reason"] == "caller_hangup"
    assert [(t["speaker"], t["text"]) for t in end["transcript"]] == [
        ("agent", START_OK["greeting"]),
        ("caller", "Send me a price list"),
        ("agent", "You said Send me a price list."),
    ]
    assert len(harness.frappe.calls("end")) == 1


async def test_hangup_during_pending_turn_still_reports(harness: Harness) -> None:
    harness.frappe.turn_delay = 2.0
    client = await harness.connect("plivo")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("Hello?")
    await client.wait_for(lambda: harness.frappe.in_flight_turns == 1)
    await client.ws.close()  # Plivo just closes the socket
    end = await harness.wait_ended()
    assert end["reason"] == "caller_hangup"
    assert ("caller", "Hello?") in [(t["speaker"], t["text"]) for t in end["transcript"]]


async def test_end_is_retried(harness: Harness) -> None:
    harness.frappe.end_failures = 2
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.stop()
    end = await harness.wait_ended()
    assert end["reason"] == "caller_hangup"
    assert len(harness.frappe.calls("end")) == 3


async def test_stt_failure_apologises_and_ends(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.audio(b"FAIL")
    end = await harness.wait_ended()
    assert client.said()[-1] == phrases.apology("en")
    assert end["reason"] == "error"


async def test_turns_never_overlap_and_queued_text_is_joined(harness: Harness) -> None:
    harness.frappe.turn_delay = 0.4
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("first")
    await client.wait_for(lambda: harness.frappe.in_flight_turns == 1)
    await client.say("second")
    await client.say("third")
    await client.wait_for(lambda: len(harness.frappe.turn_texts) == 2)
    await client.wait_said("You said second third.")
    assert harness.frappe.turn_texts == ["first", "second third"]
    assert harness.frappe.max_in_flight_turns == 1


async def test_max_seconds_says_goodbye_and_ends(harness: Harness, provider: str) -> None:
    harness.frappe.start = {**START_OK, "max_seconds": 1}
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    end = await harness.wait_ended(timeout=4)
    await asyncio.wait_for(client.closed.wait(), 2)
    assert client.said()[-1] == phrases.goodbye("en")
    assert end["reason"] == "max_seconds"
    assert 1 <= end["duration_seconds"] <= 3


async def test_max_seconds_interrupts_pending_turn(harness: Harness) -> None:
    harness.frappe.start = {**START_OK, "max_seconds": 1}
    harness.frappe.turn_delay = 5.0
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("Tell me everything")
    end = await harness.wait_ended(timeout=4)
    assert end["reason"] == "max_seconds"
    assert client.said()[-1] == phrases.goodbye("en")


async def test_turn_limit_ends_after_reply(harness: Harness, provider: str) -> None:
    harness.frappe.start = {**START_OK, "turn_limit": 1}
    client = await harness.connect(provider)
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.say("one")
    end = await harness.wait_ended()
    assert client.said()[-2:] == ["You said one.", phrases.goodbye("en")]
    assert end["reason"] == "turn_limit"
    assert harness.frappe.turn_texts == ["one"]


async def test_missing_token_closes_without_calls(harness: Harness, provider: str) -> None:
    client = await harness.connect(provider)
    await client.start(token="")
    await asyncio.wait_for(client.closed.wait(), 2)
    assert harness.frappe.requests == []


async def test_close_before_start_is_harmless(harness: Harness) -> None:
    client = await harness.connect("twilio")
    await client.ws.close()
    await asyncio.sleep(0.1)
    assert harness.frappe.requests == []


async def test_health(harness: Harness) -> None:
    base = harness.url.replace("ws://", "http://")
    async with httpx.AsyncClient() as http:
        ok = await http.get(base + "/health")
        missing = await http.get(base + "/nope")
        assert harness.frappe.requests == []  # a plain probe never reaches Frappe
        reachable = await http.get(base + "/health?frappe=1")
        harness.frappe.ping_ok = False
        cached = await http.get(base + "/health?frappe=1")
        assert harness.app is not None
        harness.app._frappe_ok = None  # the cache expired
        down = await http.get(base + "/health?frappe=1")
    assert ok.status_code == 200 and ok.text.splitlines()[0] == "ok"
    assert "calls: 0/20" in ok.text
    assert missing.status_code == 404
    assert reachable.status_code == 200 and "frappe: ok" in reachable.text
    assert cached.status_code == 200, "cached: Frappe is asked at most every 30 s"
    assert [m for m, _ in harness.frappe.requests] == ["ping", "ping"]
    assert down.status_code == 503 and "frappe: unreachable" in down.text


# ---------------------------------------------------------------- abuse and cost guards


@pytest.mark.parametrize("status", [403, 404])
async def test_refused_token_closes_without_speech(harness: Harness, provider: str, status: int) -> None:
    harness.frappe.start = status  # a forged, reused or expired token
    client = await harness.connect(provider)
    await client.start("forged")
    await client.audio(b"SAY:hello")
    await asyncio.wait_for(client.closed.wait(), 2)
    await asyncio.sleep(0.1)
    assert harness.tts.calls == [], "no paid speech for a token Frappe does not know"
    assert harness.stt.sessions == []
    assert [m for m, _ in harness.frappe.requests] == ["start"], "nothing to report either"


async def test_call_cap_refuses_the_handshake(harness: Harness) -> None:
    assert harness.app is not None
    harness.app.settings = replace(harness.settings, max_calls=1)
    first = await harness.connect("twilio")
    await first.start()
    await first.wait_said(START_OK["greeting"])
    with pytest.raises(InvalidStatus) as refused:
        await harness.connect("twilio")
    assert refused.value.response.status_code == 503
    assert len(harness.frappe.calls("start")) == 1 and len(harness.tts.calls) == 1
    await first.stop()
    await harness.wait_ended()
    await asyncio.sleep(0.05)
    second = await harness.connect("twilio")  # a slot is free again
    await second.start()
    await second.wait_said(START_OK["greeting"])


async def test_connects_per_ip_are_rate_limited(harness: Harness) -> None:
    assert harness.app is not None
    harness.app.settings = replace(harness.settings, connects_per_ip_per_minute=2)
    for _ in range(2):
        c = await harness.connect("twilio")
        await c.ws.close()
    with pytest.raises(InvalidStatus) as refused:
        await harness.connect("twilio")
    assert refused.value.response.status_code == 429
    assert harness.frappe.requests == []


# ---------------------------------------------------------------- end report


async def test_end_retries_with_backoff_in_the_background(harness: Harness) -> None:
    harness.frappe.end_failures = 4
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.stop()
    await asyncio.wait_for(client.closed.wait(), 2)
    end = await harness.wait_ended()
    assert end["reason"] == "caller_hangup"
    assert len(harness.frappe.calls("end")) == 5
    assert harness.app is not None
    await harness.app.frappe.drain(1)
    assert harness.app.frappe.pending_reports == 0


async def test_end_is_not_retried_when_refused(harness: Harness) -> None:
    harness.frappe.end_failures, harness.frappe.end_status = 1, 403
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(START_OK["greeting"])
    await client.stop()
    await asyncio.sleep(0.3)
    assert len(harness.frappe.calls("end")) == 1


# ---------------------------------------------------------------- silence


async def test_silence_asks_once_then_says_goodbye(harness: Harness, provider: str) -> None:
    assert harness.app is not None
    harness.app.settings = replace(harness.settings, idle_timeout_seconds=0.3)
    client = await harness.connect(provider)
    await client.start()
    end = await harness.wait_ended(timeout=4)
    assert client.said() == [START_OK["greeting"], phrases.still_there("en"), phrases.idle_goodbye("en")]
    assert end["reason"] == "idle"
    assert harness.frappe.calls("turn") == []


async def test_speech_resets_the_silence_timer(harness: Harness) -> None:
    assert harness.app is not None
    harness.app.settings = replace(harness.settings, idle_timeout_seconds=0.4)
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said(phrases.still_there("en"), timeout=3)
    await client.say("Yes, I'm here")
    await client.wait_said("You said Yes, I'm here.")
    await asyncio.sleep(0.2)
    assert phrases.idle_goodbye("en") not in client.said(), "the caller answered: asked again first"
    end = await harness.wait_ended(timeout=5)
    assert end["reason"] == "idle"
    assert client.said().count(phrases.still_there("en")) == 2


# ---------------------------------------------------------------- early audio and language


async def test_audio_before_stt_connects_is_kept(harness: Harness, provider: str) -> None:
    harness.frappe.start_delay = 0.3  # Frappe is slow to start the call: STT is not open yet
    client = await harness.connect(provider)
    await client.start()
    await client.say("hello early")
    await client.wait_said("You said hello early.")
    assert harness.frappe.turn_texts == ["hello early"]


async def test_early_audio_is_bounded(harness: Harness) -> None:
    assert harness.app is not None
    harness.app.settings = replace(harness.settings, pre_stt_buffer_seconds=0.02)  # 160 bytes
    harness.frappe.start_delay = 0.3
    client = await harness.connect("twilio")
    await client.start()
    await client.say("dropped")
    await client.audio(b"\xff" * 160)  # newer audio pushes the older out
    await client.wait_said(START_OK["greeting"])
    await asyncio.sleep(0.2)
    assert harness.frappe.turn_texts == []


async def test_language_without_a_voice_speaks_english(harness: Harness) -> None:
    harness.tts.languages = frozenset({"en", "es"})
    harness.frappe.start = {**START_OK, "language": "hi", "greeting": "Hello, this is Asha."}
    harness.frappe.turn = lambda text: 503
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said("Hello, this is Asha.")
    await client.say("namaste")
    await harness.wait_ended()
    # The English line, not Hindi text read by an English voice.
    assert client.said()[-1] == phrases.apology("en")
    assert {lang for _t, _v, lang in harness.tts.calls} == {"en"}
    assert harness.stt.sessions[0].language == "hi", "the caller is still heard in their language"


async def test_language_with_a_voice_from_frappe_keeps_its_lines(harness: Harness) -> None:
    harness.tts.languages = frozenset({"en"})
    harness.frappe.start = {**START_OK, "language": "hi", "voice": "custom-hi", "greeting": "नमस्ते"}
    harness.frappe.turn = lambda text: 503
    client = await harness.connect("twilio")
    await client.start()
    await client.wait_said("नमस्ते")
    await client.say("namaste")
    await harness.wait_ended()
    assert client.said()[-1] == phrases.apology("hi")
    assert harness.tts.calls[-1][1:] == ("custom-hi", "hi")
