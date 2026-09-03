# Phase C: Voice Channel

Priority: HIGH
Estimated Effort: 12-18 days
Dependency: Phases 0-3 (complete), Phase A (security in place)

---

## Objective

Add calls as a first-class Excom channel, sitting alongside WhatsApp, Email and Web Chat in the same threads, with the same identity layer and the same teams.

**Excom is the brain. The provider is a dumb pipe.** It supplies phone numbers and executes instructions. Every decision — who rings, in what order, whether to record, what the IVR does — is computed in Excom from live data.

**No vendor name is ever visible.** The UI says "Calls". Provider identity exists in one Select field and one adapter module. Swapping Exotel for Airtel IQ means writing one file.

---

## C.0 Design Rules

1. Everything lives in `excom`. No separate app.
2. One new standalone doctype. Everything else is fields on doctypes that already exist.
3. Nobody is enumerated twice. Teams ring, not hand-maintained agent lists.
4. Agent phone numbers come from core `User.mobile_no`. No new user config.
5. The provider console holds a static skeleton flow, configured once, never touched again.

---

## C.1 Code Layout

Mirrors the existing `channels/email/` and `channels/whatsapp/` convention.

```
excom/excom/channels/voice/
├── __init__.py
├── routing.py          # the decision engine (provider-agnostic)
├── handler.py          # inbound webhook, async, does the writing
├── outbound.py         # click-to-call, hangup, transfer
├── recording.py        # authenticated recording proxy
└── providers/
    ├── base.py         # VoiceProvider ABC + CallDecision + CallEvent
    ├── exotel.py       # Connect Dynamic URL adapter
    └── airtel.py       # stub

excom/excom/api/voice.py   # whitelisted endpoints
```

`excom/setup.py::seed_channels()` gains:

```python
{"name": "voice", "channel_label": "Calls",
 "allows_multiple_accounts": 1, "is_enabled": 1,
 "description": "Voice calling channel."}
```

---

## C.2 Provider Abstraction

```python
class VoiceProvider(ABC):
    def execute_ring(decision: CallDecision) -> ProviderResponse
    def initiate_call(from_number, to_number, caller_id, opts) -> ProviderCallRef
    def hangup(provider_call_id) -> None
    def fetch_call_details(provider_call_id) -> CallDetails
    def fetch_recording(provider_call_id) -> stream
    def normalize_event(raw_payload) -> CallEvent
    def capabilities() -> set[str]
```

`CallDecision` is produced by `routing.py` and is identical regardless of provider:

```python
CallDecision(
    destinations=[...],      # E.164, ordered
    parallel=True,
    ring_seconds=25,
    record=True,
    max_conversation_seconds=3600,
    ring_set=[users...],     # for notifications
)
```

**Two Exotel delivery adapters, same decision object:**

| Adapter | Mechanism | Status |
|---|---|---|
| `ExotelConnectAdapter` | One JSON response, `parallel_ringing: true` | Documented REST, build now |
| `ExotelLegsAdapter` | N independent legs, bridge on first answer | gRPC/ExoML, needs Exotel sales access — add later |

Capability flags gate the UI: `webrtc`, `transfer`, `hold`, `dual_recording`. Unsupported actions grey out rather than break.

Complexity: Medium

---

## C.3 Data Model

### One new doctype: `Excom Call`

Autoname `hash`. `provider_call_id` unique-indexed, so a provider switch never fractures naming.

```
identity_section    omni_identity (Link), thread (Link), display_name,
                    customer_number
routing_section     channel_account (Link), business_number,
                    direction (Inbound|Outbound),
                    ring_set (JSON), sticky_agent (Link User),
                    answered_by (Link User), team (Link Excom Team),
                    ivr_selection (Data)
state_section       status (Ringing|In Progress|Completed|Missed|Failed|
                            Busy|No Answer|Canceled),
                    outcome, ring_seconds, duration, cost
recording_section   recording_url, recording_status, recording_channels
provider_section    provider, provider_call_id (unique),
                    provider_events (JSON), reconciled (Check)
relations_section   reference_doctype, reference_name, notes,
                    created_by_user
```

`ring_set` is our own decision record — it drives the "picked up by X" fan-out.

### `Excom Channel Account` — new `voice_section`

`depends_on: channel == "voice"`, so it is invisible when configuring other channels.

| Field | Type | Notes |
|---|---|---|
| voice_provider | Select | Exotel / Airtel IQ |
| voice_number | Data | Business number customers see |
| voice_account_sid | Data | |
| voice_api_base | Data | Regional cluster |
| voice_api_key | Data | |
| voice_api_secret | Password | |
| voice_webhook_token | Data | Mirrors `wa_webhook_verify_token` |
| voice_ring_strategy | Select | Sticky then Team / Team only / Sticky only |
| voice_sticky_ring_seconds | Int | default 20 |
| voice_team_ring_seconds | Int | default 30, provider cap 60 |
| voice_record_policy | Select | Never / Inbound only / Outbound only / All |
| voice_recording_channels | Select | Mixed / Dual |
| voice_allow_international | Check | Account-level ceiling |
| voice_max_call_seconds | Int | provider cap 4500 |
| voice_no_answer_action | Select | Log missed only / Voicemail (Phase 2) |
| voice_status | HTML | Credential check + copyable webhook URLs |

### `Excom Account Team` — one field added

| voice_priority | Int | Ring order when a line serves multiple teams |
|---|---|---|

### `Excom Message` — two changes

- `message_type` gains `Call`
- `content_json` holds `{"call": "<Excom Call name>"}`

This is the timeline stub. Calls render inline with WhatsApp and email in the same thread.

### `Excom Settings` — new `calling_tab`

Global defaults; account-level always wins.

| Field | Type |
|---|---|
| default_record_policy | Select |
| default_allow_international | Check |
| blocked_country_codes | Small Text |
| daily_international_minutes_cap | Int |
| recording_retention_days | Int |
| require_recording_consent | Check |

### Nothing else

Agent numbers: `User.mobile_no` (core). Eligibility: `User.enabled` (core).
Availability and spend counters: `frappe.cache()`, not the database.

Complexity: Low

---

## C.4 Routing Engine

No hand-maintained agent lists. `Excom Channel Account.allowed_teams` already declares which teams work a line; those are the teams that ring.

```
Excom Channel Account "Sales Line" (+91-80-xxxx)
└── allowed_teams: [Sales, Presales]     ← already exists
        └── Excom Team.members            ← already exists
                └── User.mobile_no        ← already exists
```

Add someone to the team and they start ringing. Remove them and they stop. One place.

### Two stages

**Stage 1 — the person they know**

```
sticky = last agent who handled this Omni Identity
eligible if:  User.enabled = 1
          AND still a member of one of the line's teams
          AND has a mobile number
          AND marked available

eligible     → ring sticky alone, voice_sticky_ring_seconds
not eligible → fall straight through to Stage 2
```

Resolution is by **Omni Identity, not phone number** — so a customer calling from a different number, or last handled over WhatsApp, still reaches the rep who knows them.

**Stage 2 — the team**

```
ring everyone else on the line's teams, in parallel, first answer wins
```

### Notifications

Excom computed the ring set, so it can pop every team member's screen before the provider finishes dialling.

| Event | Realtime event | Audience |
|---|---|---|
| Call arrives | `excom:call_ringing` | Everyone in ring set |
| Answered | `excom:call_answered` | Everyone else — "Picked up by Priya" |
| Caller hung up | `excom:call_missed` | Everyone — "Caller hung up · call back" |
| Nobody answered | `excom:call_missed` | Everyone + missed-call queue |

Complexity: Medium

---

## C.5 The 5-Second Rule

The routing endpoint sits on the critical path of a live call. Exotel waits **5 seconds**, then falls to the Fallback URL, then drops to "we didn't dial anyone".

**Hard constraints on `/api/method/excom.excom.api.voice.route`:**

- No record creation. No identity writes. No outbound provider calls.
- Team → numbers map served from `frappe.cache()`, invalidated on team change.
- Everything else `frappe.enqueue()`d after the response is returned.
- Fallback URL points at a cache-only variant returning the raw team list.

A naive implementation that logs first and responds second will drop calls under load. This is the single most important non-obvious constraint in the phase.

Complexity: Medium

---

## C.6 Provider Flow Skeleton

Configured once in the provider console. Contains no teams, no numbers, no names, no business logic.

```
Incoming call
  → Greeting
  → Gather        (IVR prompt, collects keypress)
  → Connect       [Dynamic URL → Excom]   ← every decision happens here
  → Passthru      [async → Excom]         ← logging only
```

### Endpoint contracts

**Routing** — synchronous, 5s budget, zero writes:

```
GET /api/method/excom.excom.api.voice.route?key=<token>&stage=1
    ← CallSid, CallFrom, CallTo, Direction, digits, CustomField

→ {"destination": ["+9198..."], "parallel_ringing": true,
   "max_ringing_duration": 20, "record": true}
```

**Events** — async, does all the writing:

```
GET /api/method/excom.excom.api.voice.events?key=<token>
→ upsert Excom Call, publish realtime, enqueue reconcile
```

**Plus:** `initiate`, `hangup`, `get_recording`, `set_availability`.

Complexity: Low

---

## C.7 IVR

**v1: provider-managed.** The Gather applet plays the prompt and captures the keypress; Excom receives it as `digits` and routes on it. Menu-to-team mapping lives in Excom from day one.

**The routing endpoint accepts and ignores `digits` in v1.** That is the IVR seam — turning it on later changes no signatures and requires no re-architecture.

**Phase 3: Excom-managed tree.** An `Excom IVR Flow` doctype rendered into whatever shape the provider needs. Only prompt audio and DTMF capture stay carrier-side, because the carrier holds the phone leg.

Complexity: Low (v1) / Medium (Phase 3)

---

## C.8 Calling Over the Web

**Click-to-call over PSTN.** Agent clicks in Excom, their own phone rings, answering connects them to the customer. This is what Frappe CRM and Helpdesk ship and it is production-grade.

**WebRTC softphone is deferred to Phase 3**, behind a capability flag. Exotel's SDK is not on public npm, requires a separate VoIP agreement with Veeno Communications, an MVN with IP capability, and additional KYC. It is also the most vendor-locked component in the stack — an Airtel switch would mean a full rewrite.

When `capabilities()` returns `webrtc`, the UI shows a softphone panel; otherwise "Your phone will ring." One adapter method and one React component.

Complexity: Low (v1) / High (Phase 3)

---

## C.9 Recording

Admin-controlled at three levels, most specific wins: **account → team → agent**.

- `record` is set per call in the routing response, so policy is dynamic, not fixed in the provider.
- Dual-channel option (separate agent/customer tracks) — prerequisite for future transcription.
- Consent announcement toggle — legally required in several jurisdictions.
- **Authenticated proxy playback.** Provider recording URLs need Basic Auth and must never reach a browser. Streamed through `excom.excom.api.voice.get_recording` with a permission check.
- Retention purge wired into the existing `cleanup_channels` machinery.
- Download permission is separate from playback permission.

Complexity: Low

---

## C.10 International Controls

Three server-side layers. A UI-only check is not a control.

1. **Per agent** — permission stored per user, checked at dial time.
2. **Per account** — `voice_allow_international` caps everyone on that line regardless.
3. **Global default** — `Excom Settings` for anyone without an explicit setting.

Backed by:
- Country detection via existing `utils/phone.py` (E.164 parse), not string prefixes.
- Blocked-country list evaluated **before** any allowlist.
- Daily international minute cap per agent, counter in cache. Alert at threshold, hard block at limit. This is what stops a compromised account generating a five-figure bill overnight.
- Every denied attempt logged with agent, number, reason.

Complexity: Low

---

## C.11 Frontend

**Active-call widget** — floating, draggable, survives navigation:
- Caller identity, photo, linked Customer/Lead/Supplier, resolved before answering
- Live status: Ringing → Connecting → In progress with timer → Ended with duration
- Accept / Reject / Mute / Hold / Transfer / Hangup, capability-gated
- Note-taking during the call, saved onto the call record
- Last 3 conversations across any channel, plus open ERP documents

**Incoming screen pop** — browser notification + widget the moment the phone rings. Realtime publishes *before* the DB write so the pop is instant under load.

**Calls in the unified timeline** — a call renders as a card inside the same thread as that contact's WhatsApp and email: direction, duration, outcome, inline recording player, notes, call-back button. One scroll shows the whole relationship.

**Call views:**
- Calls tab in `LeftSidebar.tsx` with filters (direction, outcome, agent, team, date, recorded-only)
- **Missed-call queue** as a first-class worklist with claim/assign/callback
- Per-identity call history on `OmniIdentityPanel.tsx`
- Mobile: `components/mobile/CallScreen.tsx` is already scaffolded

Complexity: High

---

## C.12 Security and Operations

- Webhook auth: token in query string (no provider signs requests) **plus** a provider IP allowlist, since a URL-borne token leaks via logs and referrers.
- Every inbound webhook recorded as an `Integration Request` for replay and debugging. No separate event doctype — Frappe already does this.
- Idempotent upsert on `provider_call_id`. Duplicate webhooks are free.
- Rate limiting on click-to-call per agent.
- Credentials passed via `auth=` tuple, **never** embedded in the URL. Frappe CRM's version builds `https://key:token@host/...` and leaks the API token into every traceback — do not copy this.
- Recording access, download, and international dialling are three distinct permissions.
- **Async reconcile job.** Duration, price and end-time populate ~2 minutes after the call. A scheduled task backfills unreconciled calls. None of the three existing Frappe implementations do this, which is why their logs show `duration = 0` on completed calls.

Complexity: Medium

---

## C.13 Phasing

**C1 — Calls exist (4-6 days)**
Provider abstraction, Exotel Connect adapter, `voice_section` config, routing engine, routing + events endpoints, `Excom Call`, timeline stub, recording storage + proxied playback, async reconcile.

**C2 — Calls are usable (5-7 days)**
Click-to-call, active-call widget, screen pop, ring-set notifications, missed-call queue, international controls + spend caps, three-level recording policy, availability toggle, call history views.

**C3 — Later, on demand**
Excom-managed IVR tree, WebRTC softphone, Exotel Legs adapter, warm transfer, voicemail, call analytics dashboard, Airtel IQ adapter, live transcription via the audio-stream hook.

---

## C.14 Provider Onboarding Checklist

Do these before writing code — several have lead times.

- [ ] Complete KYC. **Outbound calls do not work until this clears.**
- [ ] Confirm regional cluster (`api.exotel.com` vs `api.in.exotel.com`)
- [ ] Request `parallel_ringing` be enabled — it is an opt-in feature, capped at 10 numbers
- [ ] Ask about Legs API access and whether race-to-answer bridging is supported
- [ ] Confirm Voice API version (v1 documented; v2 is CCM/agent-context; v3 beta)
- [ ] Obtain Exotel egress IP ranges for the webhook allowlist
- [ ] Agent emails in the provider console must match Frappe user emails exactly
- [ ] Build the skeleton flow and assign it to the ExoPhone

---

## C.15 Decisions Taken

| Decision | Rationale |
|---|---|
| One doctype, not five | Routing and agent config collapse into existing `allowed_teams` + `User.mobile_no` |
| `Excom Call` stays separate from `Excom Message` | Analytics needs indexed columns; calls mutate 4-6 times; duration/cost/outcome have no home in the message schema |
| No `Excom Call Event` doctype | `Integration Request` already is the webhook audit trail |
| Teams ring, not agent lists | "I don't want to keep adding people everywhere" |
| Sticky-then-team, parallel | Matches how the business actually works |
| Click-to-call before WebRTC | WebRTC is beta, gated, and the most vendor-locked piece |
| Connect adapter before Legs adapter | Connect is documented REST today; Legs needs sales access |
| Availability in cache, not a doctype | Ephemeral high-write runtime state |
| Voicemail deferred to C3 | Missed-call queue covers the need; voicemail adds a storage/playback path |

---

## Anti-Scope

Not building unless real usage demands it:

- Call queues with position announcements
- Predictive/power dialler
- Call scoring or QA workflows
- Supervisor barge-in / whisper
- Voicemail transcription
- Multi-provider failover
- Skills-based routing
