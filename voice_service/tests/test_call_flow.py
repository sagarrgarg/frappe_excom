"""End-to-end call flows over a real WebSocket on 127.0.0.1, for both
Twilio and Plivo, with fake speech providers and a fake Frappe."""

from __future__ import annotations

import asyncio

import httpx

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
    assert ok.status_code == 200 and ok.text.strip() == "ok"
    assert missing.status_code == 404
