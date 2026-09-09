# Excom

Omnichannel communication platform for Frappe/ERPNext. Brings WhatsApp, Email, and Instagram into a single real-time inbox with unified contact identity, team assignment, and full ERP context.

## What It Does

- **Unified Inbox** — All channels in one place. WhatsApp, Email (via Gmail API), Instagram DMs, with a single conversation timeline per contact.
- **Omni Identity** — Automatically links phone, email, and WhatsApp to one contact profile tied to ERPNext Customer/Lead/Supplier.
- **Team Assignment** — Route conversations to teams, transfer between agents, claim from the general queue.
- **Broadcast Messaging** — Send bulk WhatsApp templates and emails to subscriber lists with delivery tracking.
- **ERP Context** — See linked quotations, sales orders, invoices, and purchase documents inline while chatting.
- **Email Signatures, Canned Responses, Tags, Stickers** — Productivity tools for agents built in.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Frappe Framework, ERPNext |
| Frontend | React 18, TypeScript, Tailwind CSS, frappe-react-sdk |
| Database | MariaDB (via Frappe ORM) |
| Realtime | Frappe Socket.IO |
| External APIs | Gmail API (OAuth2), WhatsApp Cloud API, Instagram Graph API |

## Installation

Requires [Frappe Bench](https://github.com/frappe/bench) v5.x+ and **yarn** (for frontend builds).

```bash
cd $PATH_TO_YOUR_BENCH

bench get-app https://github.com/sagarrgarg/frappe_excom.git
bench install-app excom
bench migrate
```

### Frontend Build

The React frontend requires a separate build step after installation or any frontend change:

```bash
cd apps/excom/frontend
yarn install
yarn build
```

Or use bench to build all app assets at once:

```bash
bench build --app excom
```

## Development

```bash
bench start                                      # Start dev server
bench build --app excom                          # Build frontend
bench migrate                                    # Apply DocType schema changes
bench run-tests --app excom                      # Run all tests
bench run-tests --app excom --module excom.tests.test_<name>  # Single module
bench console                                    # Python REPL with Frappe context
```

### Configuration

After installation, configure your channel accounts in the Excom settings:

1. **WhatsApp** — Add your WhatsApp Business API credentials (Phone Number ID, Business ID, Access Token, App Secret for webhook HMAC validation)
2. **Email** — Connect Gmail accounts via OAuth2 (Connected App setup)
3. **Instagram** — Connect via Facebook Graph API credentials

Access the inbox at `https://your-site.com/excom`.

## Project Structure

```
excom/excom/
├── api/           # @frappe.whitelist() REST endpoints
├── channels/      # Channel adapters (email/, whatsapp/, instagram/)
├── doctype/       # Frappe DocTypes (Thread, Message, Channel Account, etc.)
├── services/      # Business logic layer
├── tasks/         # Scheduled background jobs
├── utils/         # Shared utilities

frontend/src/
├── components/    # React components
├── hooks/         # Custom React hooks (API calls, realtime, state)
├── types/         # TypeScript interfaces
├── utils/         # Frontend utilities
```

## Issues

Found a bug or have a feature request? Open an issue:

https://github.com/sagarrgarg/frappe_excom/issues

## License

MIT




# High Level Design — Voice Channel, Browser-First

**Document:** HLD-004
**Version:** 1.0
**Date:** 2026-09-09
**Status:** Draft for review
**Supersedes:** `roadmap/phase_C_voice_channel.md` v1 (PSTN-only), and the Exotel implementation on branch `somil-dev`
**Reference implementation studied:** `somil-dev` @ `87ea9c3` — Exotel click-to-call, agent's mobile rings
**Primary provider:** Plivo (WebRTC Browser SDK)
**Decision:** the agent's **browser is the phone**. PSTN bridging survives as a fallback transport, not as the product.

---

## Table of Contents

1. [Premise and What Changed](#1-premise-and-what-changed)
2. [The Two-Transport Model](#2-the-two-transport-model)
3. [Component Architecture](#3-component-architecture)
4. [Provider Abstraction](#4-provider-abstraction)
5. [Data Model](#5-data-model)
6. [Call Flows](#6-call-flows)
7. [Routing Engine](#7-routing-engine)
8. [Presence and Registration](#8-presence-and-registration)
9. [Recording, Transcript and Summary](#9-recording-transcript-and-summary)
10. [Threading — Calls in the Timeline](#10-threading--calls-in-the-timeline)
11. [Security](#11-security)
12. [What the Reference Branch Got Wrong](#12-what-the-reference-branch-got-wrong)
13. [Failure Modes](#13-failure-modes)
14. [Open Decisions](#14-open-decisions)

---

## 1. Premise and What Changed

### The original premise (phase_C v1, `somil-dev`)

> Agent clicks *Call* in Excom → the provider rings **the agent's own mobile phone** → the agent picks up their handset → the provider bridges them to the customer.

That is click-to-call over PSTN. It works, it is what Frappe CRM and Helpdesk ship, and `somil-dev` has it running against Exotel. But the agent is answering a phone, not using Excom. Excom sees a call record; it does not see the call.

### The new premise

> The agent wears a headset. Excom shows a softphone. Calls ring **in the browser tab** and are answered there. Audio never touches a handset.

This changes four things structurally:

| | PSTN bridge (v1) | WebRTC (v2) |
|---|---|---|
| Who holds the agent leg | The carrier, to `User.mobile_no` | The browser, over a SIP-over-WSS registration |
| What identifies an agent to the provider | A phone number | A provisioned **SIP endpoint** with credentials |
| What can be controlled mid-call | Almost nothing | Mute, hold, DTMF, hangup, device switch, quality metrics |
| What must be online for a call to land | The agent's phone network | The agent's browser tab, registered |

The last row is the whole cost of the change. A phone number is always reachable. A browser tab is not. **Therefore we do not delete the PSTN path — we demote it to a transport.**

### Rules carried forward from v1 (still binding)

1. Everything lives in `excom`. No separate app.
2. **Excom is the brain, the provider is a dumb pipe.** Who rings, in what order, whether to record — all computed in Excom from live data. The provider console holds a static skeleton, configured once.
3. **No vendor name is user-visible.** The UI says "Calls". Provider identity lives in one Select field and one adapter module.
4. Teams ring, not hand-maintained agent lists.
5. Provider is chosen **per `Excom Channel Account`**, not globally. One site can run Plivo on the sales line and keep Exotel on a legacy support line.

---

## 2. The Two-Transport Model

`Excom Call.transport` is `Browser` or `Phone`. It is the single field that keeps this design honest.

```
                       Agent sets "Take calls:"
                                 |
        +------------------------+------------------------+
        |                        |                        |
   In browser               On my phone                  Off
        |                        |                        |
  transport=Browser        transport=Phone          not in ring set
  SIP endpoint rings       mobile_no rings          missed-call queue
  answered in tab          answered on handset
```

An agent may have **both** enabled. The routing engine then puts the SIP endpoint *and* the mobile number into the same parallel `<Dial>`, and first answer wins. This is one XML document, not two code paths:

```xml
<Dial timeout="25" callerId="+918041234567" action="...">
  <User>sip:excom_priya_0912@phone.plivo.com</User>   <!-- her browser -->
  <Number>+919812345678</Number>                       <!-- her mobile  -->
</Dial>
```

**Why this matters more than it looks.** It is the answer to every failure mode below — mic denied, tab closed, laptop asleep, poor Wi-Fi, agent in transit, WebRTC blocked by a corporate firewall, an adverse India regulatory ruling — without a single conditional in the routing engine. Transport is a property of a *destination*, not of the system.

---

## 3. Component Architecture

```
excom/excom/channels/voice/
├── __init__.py
├── routing.py            # decision engine — provider-agnostic, cache-backed, zero writes
├── handler.py            # webhook processing — async, does all the writing
├── outbound.py           # dial, hangup, transfer
├── presence.py           # registration + availability, Redis-backed
├── recording.py          # authenticated recording proxy + retention
├── summary.py            # transcript -> LLM summary pipeline
├── reconcile.py          # scheduled CDR backfill (duration, cost, hangup cause)
└── providers/
    ├── base.py           # VoiceProvider ABC + SoftphoneProvider ABC + CallDecision + CallEvent
    ├── plivo.py          # PlivoXML render, Calls API, Endpoint API, JWT minting, V3 signature
    ├── exotel.py         # ported from somil-dev, PSTN only
    └── airtel.py         # stub

excom/excom/api/voice.py         # whitelisted endpoints, every one access-checked
excom/excom/doctype/excom_call/
excom/excom/doctype/excom_voice_endpoint/

frontend/src/
├── lib/softphone.ts             # Plivo Browser SDK lifecycle — one singleton, outside React
├── hooks/useSoftphone.ts        # React binding: state machine, events, actions
├── hooks/useCallHistory.ts
└── components/voice/
    ├── SoftphoneProvider.tsx    # mounts once in AppShell, survives navigation
    ├── IncomingCallToast.tsx    # screen pop with resolved identity + ERP context
    ├── ActiveCallBar.tsx        # persistent in-call bar: timer, mute, hold, DTMF, hangup, notes
    ├── CallCard.tsx             # a call rendered in the thread timeline
    ├── RecordingPlayer.tsx      # proxied audio, waveform, download (separate permission)
    ├── DeviceSettings.tsx       # mic/speaker picker, test tone, permission state
    └── MissedCallQueue.tsx
```

### The one hard rule about the SDK

`plivo-browser-sdk` maintains a **SIP registration over a WebSocket**. It must be instantiated **once**, above the router, and must survive route changes. Putting it inside a page component means every navigation drops the registration and the agent silently stops receiving calls.

`SoftphoneProvider.tsx` mounts in `AppShell` next to `InboxProvider`. The SDK object itself lives in a module-level singleton in `lib/softphone.ts`, so React StrictMode double-mounting cannot create two registrations.

---

## 4. Provider Abstraction

Two ABCs, because WebRTC is a capability, not a given. Exotel's browser SDK sits behind a separate VoIP agreement; Airtel IQ has none today. Only Plivo implements the second interface.

```python
class VoiceProvider(ABC):
    """PSTN call control. Every provider implements this."""
    def render_decision(decision: CallDecision) -> str        # -> provider call-control doc
    def initiate_call(dest: Destination, caller_id, opts) -> ProviderCallRef
    def hangup(provider_call_id) -> None
    def transfer(provider_call_id, dest: Destination) -> None
    def fetch_call_details(provider_call_id) -> CallDetails
    def fetch_recording_stream(recording_ref) -> Iterator[bytes]
    def normalize_event(raw) -> CallEvent
    def verify_webhook(request) -> bool
    def capabilities() -> set[str]

class SoftphoneProvider(ABC):
    """Browser calling. Only providers with a WebRTC SDK implement this."""
    def provision_endpoint(user, account) -> EndpointRef      # idempotent
    def deprovision_endpoint(endpoint_ref) -> None
    def mint_access_token(endpoint_ref, ttl_seconds) -> str   # short-lived JWT
    def sdk_descriptor() -> dict                              # what the browser needs to boot
```

`CallDecision` is produced by `routing.py` and is **identical regardless of provider**:

```python
CallDecision(
    destinations=[Destination(kind="sip",  ref="sip:excom_priya_0912@phone.plivo.com", user="priya@x.com"),
                  Destination(kind="pstn", ref="+919812345678",                        user="priya@x.com"),
                  Destination(kind="pstn", ref="+919800000001",                        user="amit@x.com")],
    parallel=True,
    ring_seconds=25,
    record=True,
    record_channels="stereo",
    consent_prompt=None,
    max_conversation_seconds=3600,
    ring_set=["priya@x.com", "amit@x.com"],   # for the screen pop — users, not numbers
)
```

`render_decision()` is the only provider-shaped part. Plivo emits PlivoXML; Exotel emits its Connect JSON. Same input.

### Capability matrix

| Capability | Plivo | Exotel | Airtel IQ |
|---|---|---|---|
| `webrtc` — browser softphone | **yes**, `plivo-browser-sdk` on public npm | gated: separate VoIP agreement + MVN/KYC | no |
| `click_to_call` — PSTN bridge | yes | yes | yes |
| `parallel_ring` | yes, native in `<Dial>` | opt-in feature, cap 10 | unverified |
| `mixed_parallel` — SIP + PSTN in one dial | **yes** | no | no |
| `signed_webhooks` | yes — HMAC-SHA256 V3 | no, token in query string | unverified |
| `dual_channel_recording` | yes — `recordChannelType="stereo"` | yes | unverified |
| `mid_call_transfer` | yes | limited | unverified |
| `provider_transcription` | English only, short clips — **not used** | no | no |

Capability flags gate the UI. Unsupported actions grey out with a reason; they never throw.

---

## 5. Data Model

### `Excom Call` — the one new call doctype

Autoname `hash`. `provider_call_id` **unique-indexed** — this is what makes duplicate webhooks free.

| Section | Fields |
|---|---|
| identity | `omni_identity` (Link), `thread` (Link), `display_name`, `customer_number` |
| routing | `channel_account` (Link), `business_number`, `direction` (Inbound / Outbound), `transport` (Browser / Phone), `ring_set` (JSON), `sticky_agent` (Link User), `answered_by` (Link User), `agent` (Link User), `team` (Link Excom Team), `ivr_selection` |
| state | `status` (Ringing / In Progress / Completed / Missed / Failed / Busy / No Answer / Canceled), `outcome`, `ring_seconds`, `duration`, `talk_time`, `cost`, `hangup_cause`, `hangup_source`, `reconciled` (Check) |
| recording | `recording_id`, `recording_url`, `recording_status` (None / Pending / Ready / Failed / Purged), `recording_channels`, `recording_duration_ms`, `consent_played` (Check) |
| intelligence | `transcript` (Long Text), `transcript_json` (Code — diarised turns with timestamps), `transcript_status`, `transcript_engine`, `summary` (Small Text), `summary_status`, `sentiment`, `next_action` |
| provider | `provider`, `provider_call_id` (unique), `provider_events` (JSON), `quality_score`, `quality_json` (mediaMetrics from the browser SDK) |
| relations | `reference_doctype`, `reference_name`, `notes` (Text Editor), `created_by_user` |

**Why `ring_set` is stored.** Excom computed who should ring, so Excom can tell everyone else *"Picked up by Priya"* instead of leaving four screen pops hanging. It is a decision record, not a log.

**Why quality metrics land here.** The browser SDK emits `mediaMetrics` (jitter, packet loss, MOS). When an agent says "the line was terrible", this is the only place that can answer.

### `Excom Voice Endpoint` — the second new doctype

One row per (User x Channel Account). Auto-provisioned; never hand-edited.

| Field | Type | Notes |
|---|---|---|
| `user` | Link User | |
| `channel_account` | Link Excom Channel Account | |
| `provider` | Data | denormalised from the account |
| `endpoint_id` | Data | provider's endpoint id |
| `sip_uri` | Data | `sip:excom_priya_0912@phone.plivo.com` |
| `endpoint_username` | Data | |
| `endpoint_password` | Password | encrypted at rest; **never** sent to the browser |
| `alias` | Data | `Excom · Priya · Sales Line` |
| `status` | Select | Provisioning / Active / Suspended / Deprovisioned |
| `last_registered_at` | Datetime | written by the registration heartbeat |
| `last_seen_ip` | Data | |

**Why a doctype and not custom fields on `User`.** An agent can work two lines belonging to two provider accounts. That is one-to-many, so it needs rows. It also keeps us inside the repo rule against touching core doctypes.

**Why the browser never gets the password.** The SDK supports `login(username, password)`, but shipping a permanent SIP credential into a tab means it lives in devtools, in a heap snapshot, and in any XSS. We mint a **short-lived JWT** server-side instead (`mint_access_token`; Plivo allows 3 min – 24 h, we use 60 min with silent refresh at 50).

### Changes to existing doctypes

**`Excom Channel Account` — new `voice_section`** (`depends_on: channel == "voice"`)

| Field | Type | Notes |
|---|---|---|
| `voice_provider` | Select | Plivo / Exotel / Airtel IQ |
| `voice_number` | Data | the business DID customers see |
| `voice_auth_id` | Data | Plivo Auth ID / Exotel SID |
| `voice_auth_token` | Password | |
| `voice_api_base` | Data | regional cluster |
| `voice_app_id` | Data | Plivo Application the endpoints attach to |
| `voice_webhook_token` | Data | belt-and-braces alongside signature validation |
| `voice_allow_browser_calls` | Check | master switch for WebRTC on this line |
| `voice_allow_phone_calls` | Check | master switch for the PSTN fallback |
| `voice_ring_strategy` | Select | Sticky then Team / Team only / Sticky only |
| `voice_sticky_ring_seconds` | Int | default 20 |
| `voice_team_ring_seconds` | Int | default 30 |
| `voice_record_policy` | Select | Never / Inbound only / Outbound only / All |
| `voice_recording_channels` | Select | Mixed / Dual (stereo) |
| `voice_consent_prompt` | Data | URL of the announcement clip, blank = none |
| `voice_allow_international` | Check | account-level ceiling |
| `voice_max_call_seconds` | Int | default 3600 |
| `voice_status` | HTML | credential check, endpoint count, copyable webhook URLs |

**`Excom Message`** — `message_type` gains `Call`; `content_json` holds `{"call": "<Excom Call name>"}`. This is the timeline stub.

**`Excom Channel`** — a `voice` row seeded by `setup/__init__.py::seed_channels()`, label **"Calls"**.

**`Excom Account Team`** — `voice_priority` (Int), ring order when a line serves several teams.

**`Excom Settings`** — new calling tab: `default_record_policy`, `default_allow_international`, `blocked_country_codes`, `daily_international_minutes_cap`, `recording_retention_days`, `require_recording_consent`, `transcription_engine`, `summary_enabled`.

### What we deliberately do *not* add

- No agent-list doctype. Teams ring: `Excom Channel Account.allowed_teams` -> `Excom Team Member` -> `User`.
- No availability doctype. Presence is ephemeral high-write state -> Redis with TTL.
- No call-event doctype. Frappe's `Integration Request` already is the webhook audit trail.
- No IVR doctype in v1. The provider's Gather applet captures the keypress; Excom receives `digits` and routes on it.

---

## 6. Call Flows

### 6.1 Inbound to a browser

```
Customer dials +91 80 4123 4567
        |
        v
   Plivo receives the call, invokes the Application's answer_url
        |
        v  GET /api/method/excom.excom.api.voice.route     [SYNCHRONOUS · target p99 < 800 ms]
   routing.py
     |- resolve account by DID                              (cache)
     |- resolve Omni Identity by caller number              (cache)
     |- sticky agent = last agent who handled this identity
     |- filter by presence: registered AND available AND enabled   (Redis)
     |- build destinations: SIP endpoints + mobiles, ordered
     |- publish `excom:call_ringing` -> ONLY the ring set    <- screen pop fires here
     |- enqueue persist_inbound_call(...)                    <- all writes are async
     |- return PlivoXML
        |
        v
   <Response>
     <Record recordSession="true" startOnDialAnswer="true"
             recordChannelType="stereo" callbackUrl=".../recording_ready"/>
     <Dial timeout="25" callerId="+918041234567" action=".../dial_action"
           callbackUrl=".../dial_events">
       <User>sip:excom_priya_0912@phone.plivo.com</User>
       <Number>+919812345678</Number>
     </Dial>
   </Response>
        |
        v
   Browser SDK fires onIncomingCall(callUUID) in Priya's tab
   IncomingCallToast shows: name, company, linked Lead, last 3 conversations
   Priya clicks Answer -> client.answer(callUUID) -> audio flows
        |
        v
   dial_events webhook -> answered_by resolved -> publish `excom:call_answered`
   to everyone ELSE in the ring set -> their toasts collapse to "Picked up by Priya"
```

**Two things fire before any database write.** The screen pop and the XML response. Everything else is `frappe.enqueue`d. A naive implementation that writes first and responds second drops calls under load.

### 6.2 Outbound from a browser

```
Agent clicks Call on a thread / contact / lead
        |
        v  POST ...voice.dial  {to_number, thread, transport}
   outbound.py
     |- _check_excom_access()  +  _check_identity_access()
     |- rate limit per agent
     |- international policy: agent -> account -> global, blocklist evaluated first
     |- daily minute cap check (Redis counter)
     |- returns {mode: "browser", dial_string, call_hint}
        |
        v
   Browser: client.call(dial_string, extraHeaders)
     extraHeaders carry X-PH-thread, X-PH-identity, X-PH-excom-user
        |
        v
   Plivo invokes the Application answer_url for the outgoing leg
   routing.py sees a SIP-originated call, reads the extraHeaders, returns:
   <Response>
     <Record recordSession="true" .../>
     <Dial callerId="+918041234567"><Number>+919876543210</Number></Dial>
   </Response>
        |
        v
   Customer's phone rings. Agent hears ringback in the headset.
```

**Why the thread id rides in SIP headers.** The outbound leg is created by the browser, not by our server, so we have no call UUID to correlate on until the answer_url fires. `extraHeaders` (Plivo prefixes them `X-PH-`) arrive on that webhook and let us bind the call to the right thread on the first event instead of guessing by phone number.

**When `transport == "Phone"`** the same endpoint instead calls the Calls API server-side (`from` = agent mobile, `to` = customer) — exactly the `somil-dev` behaviour — and returns `{mode: "phone"}`. The UI says "Your phone will ring." One endpoint, two modes.

### 6.3 Call lifecycle events

| Provider event | Handler writes | Realtime published to |
|---|---|---|
| answer_url hit | *(nothing — async)* | ring set: `excom:call_ringing` |
| `dial_events` DialAction=answer | `answered_by`, `status=In Progress`, `transport` | ring set: `excom:call_answered` |
| `dial_action` DialStatus | `status`, `duration`, `hangup_cause` | ring set: `excom:call_ended` |
| `hangup_url` | `duration`, `bill_duration`, `cost`, `end_time` | thread watchers: `excom:thread_updated` |
| `recording_ready` | `recording_id`, `recording_status=Ready` | thread watchers |
| transcription done | `transcript`, `transcript_json` | thread watchers |
| summary done | `summary`, timeline note | thread watchers |
| reconcile job | CDR backfill, `reconciled=1` | — |

Every one of these is idempotent on `provider_call_id`.

---

## 7. Routing Engine

No hand-maintained agent lists. The chain already exists:

```
Excom Channel Account "Sales Line" (+91 80 4123 4567)
└── allowed_teams: [Sales, Presales]        <- exists
        └── Excom Team Member                <- exists
                └── User                     <- exists
                        ├── Excom Voice Endpoint   (browser leg)   <- new
                        └── User.mobile_no         (phone leg)     <- core
```

Add someone to the team and they start ringing. Remove them and they stop. One place.

### Two stages

**Stage 1 — the person they know.**

```
sticky = last agent who handled this Omni Identity  (any channel — WhatsApp, email, call)
eligible if:  User.enabled
          AND still a member of one of the line's teams
          AND presence says available
          AND has at least one reachable destination (registered endpoint OR mobile)

eligible     -> ring sticky alone for voice_sticky_ring_seconds
not eligible -> fall straight through to Stage 2
```

Resolution is by **Omni Identity, not phone number**. A customer calling from a different handset, or last handled over WhatsApp, still reaches the rep who knows them. This is the single most valuable behaviour in the phase and it is nearly free — the identity layer already exists.

**Stage 2 — the team.** Everyone else on the line's teams, in parallel, first answer wins, ordered by `Excom Account Team.voice_priority`.

### The response-time budget

Plivo's defaults are 2 s connect / 40 s read, configurable via URL fragment (`#ct=2000&rc=3&rp=ct,rt`) — far more forgiving than Exotel's 5 s. **We do not spend that budget.** The caller hears silence for every millisecond of it. Target p99 **< 800 ms**, enforced by:

- Team -> destinations map served from `frappe.cache()`, invalidated on team / endpoint / presence change.
- Zero writes on the path. No record creation, no identity writes, no outbound provider calls.
- `fallback_url` points at a cache-only variant that returns the whole team without the sticky lookup.

---

## 8. Presence and Registration

Three independent facts, all required before a browser destination enters a ring set:

| Fact | Source | Storage |
|---|---|---|
| **Registered** — the SIP socket is up | `onLogin` / `onLogout` / `onConnectionChange` from the SDK, heartbeat every 60 s | Redis `excom:voice:reg:<user>:<account>`, TTL 150 s |
| **Available** — the agent said they are taking calls | explicit toggle in the UI | Redis `excom:voice:avail:<user>`, TTL 12 h |
| **Not already on a call** | active `Excom Call` for that agent | Redis, cleared on hangup |

Registration is the one that catches the real failures: a closed laptop stops heartbeating and drops out of the ring set within 150 seconds without anyone changing a setting.

**Presence is never a doctype.** It is high-write ephemeral runtime state, and putting it in MariaDB means a write on every heartbeat from every agent.

---

## 9. Recording, Transcript and Summary

Three stages, each independently retryable, each provider-neutral after the first.

```
Plivo <Record recordSession startOnDialAnswer recordChannelType="stereo">
        |
        v  recording_ready webhook -> recording_id, recording_url, duration
   Excom Call.recording_status = Ready
        |
        v  enqueue transcribe(call)
   TranscriptionProvider (pluggable)          <- NOT the telephony provider
   stereo -> 2 channels -> speaker-attributed turns
        |
        v  transcript, transcript_json = [{speaker, start_ms, end_ms, text}, ...]
        |
        v  enqueue summarise(call)
   Phase B LLM client
        |
        v  summary, next_action, sentiment -> posted into the thread as a call card
```

### Why not Plivo's transcription

Plivo's own transcription is **English only** and aimed at short clips. This is an Indian sales desk: calls are Hindi, Hinglish, and code-switched inside a single sentence. Using it would produce blank or garbage transcripts on most real calls.

`TranscriptionProvider` is its own small ABC with a `transcribe(audio, hints) -> Transcript` method, configured in `Excom Settings.transcription_engine`. Candidates to evaluate: **Sarvam AI** (built for Indian languages, India-hosted), **Deepgram Nova** (fast, handles code-switching), **AssemblyAI**, self-hosted **Whisper large-v3**. Data residency is a selection criterion, not a nice-to-have.

### Why stereo recording is not optional

`recordChannelType="stereo"` puts the agent on one channel and the customer on the other. Speaker attribution then comes from the file, not from a diarisation model that guesses. It is the difference between a usable transcript and a wall of text. It costs nothing.

### Playback

Provider recording URLs are **public by default on Plivo** — anyone with the URL hears the call. The first act of provisioning is to turn on HTTP Basic Auth for recording media in Plivo Voice Settings. Then:

- Playback goes through `excom.excom.api.voice.get_recording`, which checks thread access and streams bytes with `Response(..., direct_passthrough=True)`.
- Credentials are passed as an `auth=` tuple. **Never** built into the URL — that leaks the token into every traceback and log line.
- **Download is a separate permission from playback.**
- Retention purge wired into the existing `tasks/cleanup.py` machinery against `Excom Settings.recording_retention_days`.

---

## 10. Threading — Calls in the Timeline

A call is an `Excom Message` of type `Call` inside the same `Excom Thread` as that contact's WhatsApp and email. One scroll shows the whole relationship.

The card renders progressively as the pipeline completes:

```
+--------------------------------------------------+
| v Incoming call · Priya Sharma · 4m 12s          |
| > ---------------------------- 0:00 / 4:12    v  |
|                                                  |
| Summary                                          |
| Asked about bulk pricing for 500 units. Wants    |
| a quote by Friday. Mentioned a competitor at     |
| Rs 42/unit. Priya committed to a revised slab.   |
|                                                  |
| Next action · Send revised price slab by Fri     |
| > Transcript (14 turns)                          |
+--------------------------------------------------+
```

The `voice` channel is a first-class `Excom Channel`, so it inherits filters, tags, assignment, transfer, close-outcome and the team-visibility rules with no extra work. **A call thread is a thread.**

---

## 11. Security

| Control | Implementation |
|---|---|
| Webhook authenticity | **Plivo V3 signature** — HMAC-SHA256 over URL + sorted POST params + nonce, headers `X-Plivo-Signature-V3` / `-Nonce`. Validate before touching the payload. Query-string token kept as a second factor, not the only one. |
| Provider IP allowlist | Second layer for providers that do not sign (Exotel). |
| Endpoint credentials | SIP password in a `Password` field, encrypted at rest, **never** leaves the server. Browser gets a short-lived JWT (60 min, refreshed at 50). |
| Token minting | `voice.get_softphone_token` — access-checked, rate-limited, mints only for the calling user's own endpoint. Never accepts a `user` parameter. |
| Every endpoint access-checked | `_check_excom_access()` on line one; `_check_thread_access()` / `_check_identity_access()` where a thread or contact is named; `_check_manager_access()` on provisioning and settings. Guardrail #2 is not optional. |
| Realtime scoping | Call events publish **only to the computed ring set** — never a broadcast. |
| Recording access | Basic Auth on the provider side, proxied playback with a permission check, separate download permission, retention purge. |
| Toll-fraud controls | Blocklist evaluated *before* any allowlist; per-agent daily international minute cap in Redis, alert at threshold, hard block at limit; every denied attempt logged with agent, number, reason. This is what stops a compromised account generating a five-figure bill overnight. |
| Rate limiting | `user_rate_limit` on dial, token minting and recording fetch. |
| Idempotency | `provider_call_id` unique index. Providers retry webhooks; duplicates must be free. |
| Audit | Every inbound webhook recorded as an `Integration Request`. |
| PII in logs | Call ids only. Never numbers, never transcript text. |
| Consent | `voice_consent_prompt` plays an announcement where required; `consent_played` recorded on the call. |

### Browser prerequisites (not optional, and they bite late)

- **HTTPS everywhere.** `getUserMedia` requires a secure context. `http://` will never get a microphone.
- **WSS reachable.** Corporate firewalls that block outbound WebSocket or UDP break registration. Needs a network sign-off per pilot site.
- **Microphone permission** is per-origin and per-browser. The first-run flow must handle denial gracefully — that is exactly when transport falls back to Phone.

---

## 12. What the Reference Branch Got Wrong

`somil-dev` is a working Exotel integration and the call-lifecycle logic in `handler.py` is a reasonable starting point. These are the things that must **not** be carried across.

| # | Issue in `somil-dev` | Why it matters | Fix |
|---|---|---|---|
| 1 | `handler.py` publishes `excom_incoming_call` to **every enabled System User**, then broadcasts unscoped as well | Every user in the ERP sees the caller's number and identity on screen. This breaks the team-visibility model the entire app is built on. | Publish only to `CallDecision.ring_set`. |
| 2 | `api/voice.py` — `initiate_call`, `get_recording`, `get_active_call`, `get_call_history` have **no access check** | Any authenticated Frappe user can dial out on the company line and read any call history. Violates guardrail #2 outright. | `_check_excom_access()` on line one of every endpoint; thread / identity checks where relevant. |
| 3 | Webhook auth is a token in the query string, compared with `!=` | Leaks via logs, referrers, proxies. Not constant-time. | Plivo V3 HMAC + `hmac.compare_digest` + IP allowlist. |
| 4 | `frappe.set_user("Administrator")` and `frappe.flags.ignore_permissions = True` set in the request handler and never reset | Leaks elevated context into whatever runs next on that worker. | Elevate only inside the enqueued job, in a scoped context manager. |
| 5 | `resolve_ring_destination` falls back to "any 10 System Managers", then "any 10 enabled users" | A misconfigured line rings ten random people, including finance. | No fallback. No destination -> play a message, log a missed call, notify the line's team. Loudly. |
| 6 | Sticky lookup does `primary_phone LIKE %<last 10 digits>%` | Unindexed leading-wildcard scan on the synchronous path; also matches the wrong contact. | Normalised E.164 lookup via `utils/phone.py`, cached. |
| 7 | Agent resolution does `mobile_no LIKE %digits%` and takes `[0]` | Two agents with the same trailing digits silently swap call ownership. | Match the destination we dialled from our own `ring_set` — we know exactly who we rang. |
| 8 | Realtime events emitted under four names (`excom_incoming_call`, `excom:incoming_call`, both scoped and broadcast) | Belt-and-braces that became the interface. | One event name per fact, `excom:call_*`, matching the existing convention. |
| 9 | `is_end_of_call` heuristic guesses whether the answer_url hit is really a status callback | Fragile — it was patched twice on the branch. | Plivo has distinct URLs per event (`answer_url`, `dial_action`, `hangup_url`, `callbackUrl`). No guessing. |
| 10 | `roadmap/phase_C_voice_channel.md` deleted on the branch | The design rationale went with it. | Design lives in the repo. This document replaces it. |

The frontend components on the branch (`ActiveCallWidget`, `IncomingCallScreenPop`, `CallMessageCard`, `mobile/CallScreen`) are useful as layout references but are written against hardcoded Tailwind colours (`slate-900`, `emerald-500`). P1 replaced that with the `--ex-*` token system. They get rewritten, not ported.

---

## 13. Failure Modes

| Failure | Detection | Behaviour |
|---|---|---|
| Mic permission denied | `onMediaPermission` | The agent's browser destination is dropped; falls back to Phone transport if their mobile is set; UI shows a fix-it banner. |
| Tab closed / laptop asleep | Registration heartbeat expires (150 s) | Agent leaves the ring set. No missed calls attributed to them. |
| Firewall blocks WSS / UDP | `onWebSocketConnected` never fires; `onWebrtcNotSupported` | Softphone shows "Browser calling unavailable on this network", auto-switches to Phone. |
| Nobody in the ring set | routing returns empty destinations | Play the after-hours message, log a **Missed** call, push the missed-call queue, notify the team. Never a random fallback. |
| answer_url slow or down | Plivo retries, then `fallback_url` | `fallback_url` serves a cache-only team list. Worst case the whole team rings. |
| Duplicate webhooks | unique `provider_call_id` | Upsert, no-op. |
| CDR arrives late | `reconciled = 0` after 90 s | Scheduled `reconcile_pending_calls` backfills duration, cost, hangup cause. Without this, completed calls show `duration = 0` — the bug in all three existing Frappe telephony integrations. |
| Transcription fails | `transcript_status = Failed` | Card still shows recording and duration. Retry from the UI. The summary degrades; the call record does not. |
| Two tabs open | SDK registers twice, one wins | Detect on `onLogin`, warn, keep the newest registration. |

---

## 14. Open Decisions

| # | Decision | Recommendation | Needs |
|---|---|---|---|
| D1 | **India regulatory posture on WebRTC -> PSTN.** Plivo requires both legs to originate and terminate within India, and TRAI's domestic VoIP-PSTN position is restrictive. A browser leg is IP, not PSTN. | Get this in **writing from Plivo India** before any code. It is the one finding that can invalidate the phase. The Phone transport is the hedge. | Plivo India sales + compliance |
| D2 | Provider for the pilot | **Plivo.** The only vendor with a public-npm browser SDK, signed webhooks and mixed SIP + PSTN parallel dial. | — |
| D3 | Keep Exotel? | Yes, as a **PSTN-only provider on its own channel account**. Port `somil-dev`'s adapter; do not extend it. | — |
| D4 | Transcription engine | Evaluate Sarvam AI and Deepgram on 20 real recorded calls before committing. Indian-language accuracy and data residency decide it. | Recorded call sample |
| D5 | Does the summary need Phase B first? | Yes for summary, no for transcript. Ship transcript in V2, summary when the LLM client lands — or stub `summary.py` against one direct API call. | Phase B decision |
| D6 | Number series | 140-series is promotional-only; landline series for service and transactional. Sales calls to existing leads are transactional. | Confirm with Plivo + legal |
| D7 | Recording consent announcement | Default **on** for inbound, configurable per account. | Legal |
| D8 | Mobile app parity | `apps/mobile` is a hooks/lib skeleton. Browser calling on a mobile browser is unreliable in the background — Plivo recommends native SDKs. Mobile agents use Phone transport in V1–V2. | — |

---

## Appendix A — Plivo Facts Used in This Design

| Fact | Source |
|---|---|
| `plivo-browser-sdk` on public npm, v2.2.21, Apache-2.0 | npm registry |
| Login via `login(username, password)` **or** JWT access token; token refresh supported | Plivo Browser SDK reference |
| Events: `onIncomingCall`, `onCallAnswered`, `onCallConnected`, `onCallTerminated`, `onCallFailed`, `onMediaPermission`, `mediaMetrics`, `onWebSocketConnected`, `onWebrtcNotSupported` | Plivo Browser SDK reference |
| Options: `clientRegion` (asia POP), `enableNoiseReduction`, `closeProtection`, `allowMultipleIncomingCalls`, `registrationRefreshTimer` | Plivo Browser SDK reference |
| Endpoint API: create / list / update / delete SIP endpoints; username 1–25 chars + auto 12-digit suffix; SIP URI `sip:<username>@phone.plivo.com`; attach `app_id` | Plivo Endpoint API |
| JWT for client SDK login, expiry 3 min – 24 h | Plivo Endpoint API |
| `<Dial>` with multiple `<User>` / `<Number>` rings **in parallel**, first answer wins | Plivo XML routing |
| `<Dial>` attributes: `action`, `callbackUrl`, `timeout`, `timeLimit`, `callerId`, `confirmSound`, `dialMusic`, `sipHeaders` | Plivo XML routing |
| Dial callbacks: `DialStatus`, `DialALegUUID`, `DialBLegUUID`, `DialAction`, `DialBLegDuration`, `DialHangupCause` | Plivo XML routing |
| `<Record recordSession startOnDialAnswer recordChannelType="stereo" callbackUrl>`; callback gives `RecordUrl`, `RecordingID`, `RecordingDuration` | Plivo XML record |
| Recording URLs are **public by default**; HTTP Basic Auth is opt-in in Voice Settings | Plivo Recordings API |
| Recording storage free for 90 days, then $0.0004/min/month | Plivo Recordings API |
| Plivo transcription is **English only**, short clips | Plivo support |
| Webhook signature V3: HMAC-SHA256 over URL + sorted POST params + nonce, `X-Plivo-Signature-V3`; `plivo.utils.validate_v3_signature()` | Plivo signature validation |
| Webhook timeouts: 2 s connect, 40 s read, 55 s total; retries configurable up to 5 via URL fragment | Plivo callback configurations |
| Calls API: `answer_url`, `hangup_url`, `ring_url`, `fallback_url`, `ring_timeout`, `time_limit`, `machine_detection`, `sip_headers`; `to` accepts a SIP URI | Plivo Calls API |
| India: both legs must originate and terminate in India; India-registered business required to rent Indian numbers; caller ID must be a Plivo-rented Indian number; 140-series promotional, landline series transactional | Plivo India calling regulations |
| `plivo` PyPI package v4.62.0, deps `requests`, `six`, `decorator`, `lxml`, `PyJWT` | PyPI |

