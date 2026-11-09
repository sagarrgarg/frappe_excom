# Excom voice service

A small standalone service that lets an Excom AI agent answer phone calls
(HLD-007 §11.4, Phase 3). It runs on its own VM, not inside Frappe, and has no
Frappe imports.

It bridges audio and text only. Claude is called by Frappe, never by this service.

```
caller ──PSTN──▶ Twilio / Plivo ──wss media stream──▶ excom-voice ──HTTPS──▶ Frappe (excom.excom_ai.voice.*)
                                                      │      ▲
                                    8 kHz mu-law ─────▼      │──── 8 kHz mu-law
                                         Deepgram STT        Deepgram TTS (Aura)
```

For each call the service:

1. reads the caller's 8 kHz mu-law audio and streams it to speech-to-text;
2. detects the end of the caller's turn (final transcript plus utterance end);
3. asks Frappe what to say (`voice.turn`);
4. turns the reply into 8 kHz mu-law speech and streams it back;
5. handles barge-in: when the caller starts speaking over the agent, it sends
   Twilio `clear` or Plivo `clearAudio` and stops the current synthesis;
6. reports the transcript to Frappe at the end (`voice.end`), always once per
   call, with retries.

## Layout

| Path | What |
|---|---|
| `excom_voice/server.py` | WebSocket server: `/stream` (media), `GET /health` |
| `excom_voice/call.py` | One call: turn-taking, barge-in, limits, end report (`CallSession`, `Speaker`) |
| `excom_voice/telephony.py` | Twilio and Plivo message formats |
| `excom_voice/frappe_client.py` | `voice.start` / `voice.turn` / `voice.end` |
| `excom_voice/speech/base.py` | The STT and TTS interfaces |
| `excom_voice/speech/deepgram.py` | Deepgram nova-3 streaming STT and Aura-2 TTS (default) |
| `excom_voice/speech/fake.py` | Fake STT/TTS for tests and dry runs |
| `excom_voice/phrases.py` | The apology and goodbye lines, per language |
| `deploy/excom-voice.service` | systemd unit example |
| `Dockerfile`, `.env.example` | Container and configuration |

## Setup

Python 3.12. Two runtime dependencies: `websockets` and `httpx`.

```bash
python3.12 -m venv venv
venv/bin/pip install .
cp .env.example .env    # fill in, then:
set -a; . ./.env; set +a
venv/bin/excom-voice    # or: python -m excom_voice
```

Docker:

```bash
docker build -t excom-voice .
docker run --env-file .env -p 127.0.0.1:8080:8080 --stop-timeout 960 excom-voice
```

systemd: see `deploy/excom-voice.service`.

**TLS.** Twilio requires `wss://`, and Plivo should get it too. Terminate TLS
in front of the service (Caddy, nginx), and proxy WebSocket upgrades for
`/stream`, for example with Caddy:

```
voice.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

**Shutdown.** SIGTERM stops new calls and waits for calls in progress to
finish, so a deploy does not cut people off. Allow up to `max_seconds` for
this (the unit sets `TimeoutStopSec=960`).

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `FRAPPE_URL` | (required) | Base URL of the Frappe site, for example `https://erp.example.com` |
| `VOICE_KEY` | (required) | Shared secret, sent as `X-Excom-Voice-Key` |
| `DEEPGRAM_API_KEY` | (required for Deepgram) | Deepgram key, used for both STT and TTS |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | Listening address |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs transcript text. Audio is never logged |
| `STT_PROVIDER` / `TTS_PROVIDER` | `deepgram` | `fake` for dry runs |
| `DEEPGRAM_STT_MODEL` | `nova-3` | STT model |
| `DEEPGRAM_ENDPOINTING_MS` | `500` | Silence (ms) before Deepgram marks `speech_final` |
| `DEEPGRAM_UTTERANCE_END_MS` | `1000` | Gap (ms) for `UtteranceEnd`; Deepgram's minimum is 1000 |
| `BARGE_IN_ON` | `speech_started` | `words` interrupts only on recognised words (fewer false barge-ins on a noisy line) |
| `FRAPPE_TIMEOUT_SECONDS` | `10` | Timeout per Frappe call |
| `DEFAULT_MAX_SECONDS` | `900` | Used when `voice.start` sends no `max_seconds` |
| `DEFAULT_TURN_LIMIT` | `40` | Used when `voice.start` sends no `turn_limit` |

## How Frappe and the provider connect

1. A call comes in. Frappe's routing picks the AI agent and creates a
   single-use token `T` for it.
2. Frappe answers the provider's webhook with markup that opens a media
   stream to this service:

   Twilio (TwiML):

   ```xml
   <Response>
     <Connect>
       <Stream url="wss://voice.example.com/stream">
         <Parameter name="token" value="T"/>
         <Parameter name="provider" value="twilio"/>
       </Stream>
     </Connect>
   </Response>
   ```

   Plivo (XML):

   ```xml
   <Response>
     <Stream bidirectional="true" keepCallAlive="true"
             contentType="audio/x-mulaw;rate=8000"
             extraHeaders="token=T">wss://voice.example.com/stream?provider=plivo</Stream>
   </Response>
   ```

   Put nothing after `<Connect>` or `<Stream>` (or only `<Hangup/>`). When this
   service closes the socket, both providers move on to the next instruction,
   and if there is none the call ends.
3. The provider sends a `start` event carrying the token. The service calls
   `voice.start`, plays the greeting, then loops: caller turn, `voice.turn`,
   reply.
4. When the call ends for any reason, the service closes the socket and calls
   `voice.end` with the transcript.

### Provider detection

`?provider=plivo` on the stream URL selects Plivo. Without it, the first
`start` event is inspected: `start.streamId` or a top-level `extra_headers`
means Plivo, otherwise Twilio. Twilio does not allow a query string on the
stream URL, so Twilio is the default.

### The Frappe contract

All calls are `POST {FRAPPE_URL}/api/method/<method>` with a JSON body and the
header `X-Excom-Voice-Key: {VOICE_KEY}`. Frappe wraps results as
`{"message": {...}}`.

| Method | Body | Result |
|---|---|---|
| `excom.excom_ai.voice.start` | `{"token"}` | `{"ok": true, "call", "agent_name", "language", "voice", "greeting", "max_seconds", "turn_limit"}` or `{"ok": false, "say"}` |
| `excom.excom_ai.voice.turn` | `{"token", "text"}` | `{"say", "end_call"}` |
| `excom.excom_ai.voice.end` | `{"token", "transcript": [{"speaker", "text", "at_ms"}], "duration_seconds", "reason"}` | ignored |

How the service behaves:

- **`ok: false`**: it says `say`, closes, and reports `voice.end` with reason
  `agent_end`.
- **`voice` empty**: it uses a default Aura-2 voice for the language (`en`
  Thalia, `es` Celeste, `fr`, `de`, `it`, `nl`, `ja`). Deepgram has no Hindi
  voice, so `hi` falls back to the English voice. Frappe should send a voice
  (or a different TTS provider is needed) for Hindi.
- **One turn at a time**: while `voice.turn` is pending (1 to 4 s) nothing is
  played. Anything the caller finishes saying meanwhile is queued, and the
  queued pieces are joined with spaces into the next turn.
- **Overlap**: a reply is queued for playback and the next turn may start
  while it plays. Barge-in drops queued and playing audio.
- **`end_call: true`**: it says `say`, waits for the provider to confirm
  playback (mark or checkpoint, with a time-based fallback), then closes the
  socket. Reason `agent_end`.
- **`max_seconds`** (counted from the `start` event): it interrupts whatever
  is happening, says a short goodbye, and ends with reason `max_seconds`.
- **`turn_limit`**: after the reply to the `turn_limit`-th turn, it says the
  goodbye and ends with reason `turn_limit`.
- **HTTP failure of `start` or `turn`** (transport error, timeout, non-2xx,
  or no `message` object): it says the apology in the call's language, or in
  English when `start` failed, and ends with reason `error`.
- **STT failure**: apology, then reason `error`. **TTS failure**: it ends with
  reason `error` straight away, since nothing can be said.
- **Caller hangs up** (Twilio `stop`, or the socket closes): reason
  `caller_hangup`, unless an agent-side end was already under way.
- **`voice.end`**: sent exactly once per call that had a token, after the
  socket is closed. It is tried 3 times with backoff. Transcript entries are
  `{"speaker": "caller"|"agent", "text", "at_ms"}`, where `at_ms` counts from
  the `start` event. An agent line is recorded when it starts playing, even
  if barge-in cuts it short. Caller words never sent as a turn (for example
  on hangup) are still recorded.
- **No token**: a stream without a token is closed without calling Frappe, so
  no STT is opened and no cost is incurred.

## Speech providers

The interfaces are in `excom_voice/speech/base.py`:

- **STT:** `await provider.open(language)` returns a session with
  `feed(bytes)`, `events()` and `close()`. The events are `speech_started`,
  `partial(text)`, `final(text)` and `utterance_end`. All `final` segments
  since the last `utterance_end` form one caller turn.
- **TTS:** `synthesize(text, voice, language)` is an async iterator of raw
  8 kHz mu-law chunks.

To add a provider, implement these and wire it into `build_app` in
`server.py`.

**Deepgram STT.** It connects to `wss://api.deepgram.com/v1/listen` with
`Authorization: Token <key>` and these parameters: `model=nova-3`,
`encoding=mulaw`, `sample_rate=8000`, `channels=1`, `language=<call language>`
(or `multi` when empty), `interim_results=true` (needed for
`utterance_end_ms`), `endpointing=500`, `utterance_end_ms=1000`,
`vad_events=true` (for `SpeechStarted`), `smart_format=true` and
`punctuate=true`.

Turn end follows Deepgram's guidance: a `Results` with `speech_final=true`
ends the turn, and an `UtteranceEnd` ends it when no `speech_final` came (for
example on a noisy line). The service sends `KeepAlive` after 4 s without
audio, and `CloseStream` at the end.

**Deepgram TTS.** It sends `POST https://api.deepgram.com/v1/speak?model=<voice>&encoding=mulaw&sample_rate=8000&container=none`
with the body `{"text": ...}`. The response is chunked, and chunks are
forwarded as they arrive. Text over Aura's 2000-character limit is split on
sentence ends.

## Protocol references (checked 2026-11-10)

**Twilio Media Streams**

Sources: [WebSocket messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
and [TwiML `<Stream>`](https://www.twilio.com/docs/voice/twiml/stream).

- **From Twilio:** `connected`; then `start` (`start.streamSid`,
  `start.callSid`, `start.customParameters`, and `mediaFormat` always
  `audio/x-mulaw`, 8000 Hz, mono); then `media` (`media.track`,
  `media.payload` as base64 raw mu-law); `mark` (`mark.name`); `dtmf`; and
  `stop`.
- **To Twilio:** `{"event":"media","streamSid","media":{"payload"}}` (raw
  mu-law 8 kHz, base64, no file headers),
  `{"event":"mark","streamSid","mark":{"name"}}` (echoed back when played),
  and `{"event":"clear","streamSid"}` (empties the buffer).
- **Bidirectional `<Connect><Stream>`:** it carries the inbound track only.
- **Custom parameters:** `<Parameter>` arrives in
  `start.customParameters`, with name plus value under 500 characters. The
  stream URL may not contain a query string.
- **Closing:** "Twilio executes the remaining TwiML instructions only after
  your server closes the WebSocket connection."

**Plivo Audio Streaming**

Sources: [protocol reference](https://docs.plivo.com/docs/voice-agents/audio-streaming/concepts/audio-streaming-reference),
[audio streaming guide](https://www.plivo.com/docs/voice/audio-streaming/audio-streaming)
and the [`<Stream>` XML element](https://docs.plivo.com/docs/voice/xml/the-stream-element).

- **`start` event:** carries `start.callId`, `start.streamId`,
  `start.accountId`, `start.tracks` and `start.mediaFormat`. `extra_headers`
  is a **top-level string** `"k1=v1;k2=v2"` (not inside `start`). The service
  also accepts a dict, the key inside `start`, `extraHeaders`, an `X-PH-`
  prefix, and `?token=` on the URL.
- **Other inbound events:** `media` (`media.payload` base64, about 20 ms per
  chunk, plus `streamId`), `dtmf`, `playedStream` (`name`, the reply to a
  checkpoint) and `clearedAudio`. No `stop` event is sent over the socket:
  the socket just closes.
- **Outbound events:**
  `{"event":"playAudio","media":{"contentType":"audio/x-mulaw","sampleRate":8000,"payload"}}`
  (no `streamId`; the content type and rate must match the Stream XML),
  `{"event":"checkpoint","streamId","name"}` and
  `{"event":"clearAudio","streamId"}`.
- **Message size:** at most 64 KB per WebSocket message. The service sends
  at most 8000 bytes of audio (1 s) per message.
- **`keepCallAlive="true"`** (bidirectional only): later XML waits until the
  stream ends. `extraHeaders` format: `key1=value1;key2=value2`.

**Deepgram**

Sources: [live streaming reference](https://developers.deepgram.com/reference/speech-to-text/listen-streaming),
[end-of-speech detection](https://developers.deepgram.com/docs/understanding-end-of-speech-detection),
[TTS REST](https://developers.deepgram.com/reference/text-to-speech/speak-request),
[TTS overview](https://developers.deepgram.com/docs/text-to-speech) (2000-character limit, chunked streaming)
and [TTS voices](https://developers.deepgram.com/docs/tts-models).

## Tests

No network is used. Fake Twilio and Plivo clients talk to the real server on
127.0.0.1, with fake STT/TTS and a fake Frappe (`httpx.MockTransport`).

```bash
uv run --no-project --python 3.12 --with websockets --with httpx --with pytest --with pytest-asyncio \
    env PYTHONPATH=. pytest -q
uvx ruff check .
```

The tests cover:

- the greeting on start, a turn and its reply, and barge-in `clear`/`clearAudio`;
- `end_call`, errors from `start` and `turn` (apology, reason `error`), a declined start and an STT failure;
- the end report on hangup (including during a pending turn) and its retries;
- turn queueing and joining, `max_seconds`, `turn_limit`, a missing token and `/health`;
- unit tests for the message formats and the Deepgram request shapes.

Each flow runs for both Twilio and Plivo.
