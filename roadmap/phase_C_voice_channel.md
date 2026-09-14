# Phase C: Voice Channel — Browser-First

**Version:** 2.0 (2026-09-09) — supersedes v1, which planned PSTN click-to-call only
**Design:** `roadmap/design/HLD_004_voice_webrtc.md`
**Priority:** HIGH
**Estimated effort:** 30–42 days
**Depends on:** Phases 0–3 (done), Phase A security (done), P1 UI (done)
**Soft-depends on:** Phase B LLM client — for call summaries only

---

## Objective

Calls become a first-class Excom channel, in the same threads, with the same identity layer and the same teams as WhatsApp and email — **answered in the browser**.

An agent puts on a headset, opens Excom, and takes calls. Every call is recorded, transcribed, summarised and filed onto the contact's timeline without anyone typing a note.

**Provider: Plivo**, chosen because it is the only vendor with a public-npm WebRTC browser SDK, cryptographically signed webhooks, and the ability to ring a browser endpoint and a mobile number in the same parallel dial. Exotel and Airtel IQ stay configurable as PSTN-only providers on their own channel accounts.

### What v1 said and why it changed

v1 planned click-to-call over PSTN: the agent's own mobile rings, they answer the handset, the carrier bridges them to the customer. That is built and working on branch `somil-dev` against Exotel. It is a real feature and it is not being thrown away — but the agent is answering a phone, not using Excom.

v2 makes the browser the phone and demotes the PSTN bridge to a **fallback transport**. Both live behind one routing decision; see HLD-004 §2.

---

## The One Thing That Can Kill This Phase

**India's regulatory position on connecting a WebRTC leg to the domestic PSTN.**

Plivo's own India documentation requires that both call legs originate and terminate within India, and TRAI's long-standing position restricts domestic VoIP-to-PSTN interconnection. A browser leg is IP, not PSTN.

**Get written confirmation from Plivo India — sales *and* compliance — before writing code.** Ask three questions explicitly:

1. Can a Plivo WebRTC endpoint registered from a browser in India originate a call to an Indian mobile or landline, with an India DID as caller ID?
2. Can an inbound call to an Indian Plivo DID be `<Dial>`-ed to a WebRTC `<User>` endpoint?
3. Which media POP anchors that call, and does it satisfy the both-legs-in-India requirement?

If the answer to 1 or 2 is no, the phase becomes **V1 + V2-Phone only**: the `somil-dev` PSTN model on a better foundation, with recording, transcript and summary — still a large win, and no rework, because transport is a property of a destination in this design.

This is question **G1** in the gates below. Nothing else starts until it is answered.

---

## Requirements

### R1 — Commercial and regulatory (lead time: 2–4 weeks, start now)

| # | Requirement | Owner | Blocks |
|---|---|---|---|
| R1.1 | Plivo account, India-registered business entity | Business | everything |
| R1.2 | KYC complete and approved. **Outbound does not work until this clears.** | Business | V1 |
| R1.3 | Written answer to the three WebRTC-PSTN questions above | Business | **G1 — everything** |
| R1.4 | Indian DID rented — correct series for the use case (landline series for service/transactional; 140-series is promotional-only; 160-series is BFSI-only) | Business | V1 |
| R1.5 | Confirm which media POP / `clientRegion` serves India and its latency | Business + Eng | V1 |
| R1.6 | Recording-consent legal position per state / customer type | Legal | V2 |
| R1.7 | Data-residency requirement for recordings and transcripts — decides the transcription vendor | Legal | V2 |
| R1.8 | Per-minute pricing for India domestic in/out, plus recording storage beyond 90 days | Business | V1 |
| R1.9 | Monthly spend ceiling and a billing alert on the Plivo account | Business | V1 |

### R2 — Plivo configuration (half a day, once R1 clears)

| # | Requirement |
|---|---|
| R2.1 | A Plivo **Application** with `answer_url`, `hangup_url`, `fallback_url` pointing at the Excom site |
| R2.2 | The DID assigned to that Application |
| R2.3 | **HTTP Basic Auth enabled for recording media** in Voice Settings — recordings are public URLs by default |
| R2.4 | Auth ID + Auth Token stored in `Excom Channel Account` (`Password` field, never in `site_config.json` in plaintext) |
| R2.5 | Webhook retry policy tuned via URL fragment (`#ct=2000&rc=3&rp=ct,rt`) |
| R2.6 | Plivo egress IP ranges obtained for the allowlist |
| R2.7 | Sandbox / test credentials for CI |

### R3 — Infrastructure

| # | Requirement | Why |
|---|---|---|
| R3.1 | **Site served over HTTPS with a valid certificate** | `getUserMedia` needs a secure context. No microphone on `http://`, ever. |
| R3.2 | Outbound **WSS** and **UDP** permitted from every agent's network to Plivo media POPs | Registration and audio. A blocked port is a silently dead softphone. |
| R3.3 | Per-site network check before each pilot rollout | Corporate firewalls are the number-one cause of "it rings but there's no audio" |
| R3.4 | Redis available for presence keys (already a bench dependency) | Registration heartbeat, availability, spend counters |
| R3.5 | Background workers with a `short` queue not saturated | Every call write is enqueued |
| R3.6 | Realtime (Socket.IO) healthy — already used by the inbox | Screen pop, call state |
| R3.7 | Object storage or disk headroom for transcripts | Recordings stay at Plivo; transcripts come to us |

### R4 — Hardware and browser (per agent)

| # | Requirement |
|---|---|
| R4.1 | USB or Bluetooth headset with a boom mic. Laptop mics produce unusable recordings and echo. |
| R4.2 | Chrome or Edge (current or previous 10 versions), or Safari (current or previous 5) |
| R4.3 | Microphone permission granted to the Excom origin |
| R4.4 | Wired ethernet or strong Wi-Fi; ~100 kbps sustained per concurrent call |
| R4.5 | `User.mobile_no` populated — this is the fallback transport, and it is not optional |

### R5 — Software dependencies

| # | Dependency | Version | Where |
|---|---|---|---|
| R5.1 | `plivo` (Python SDK) | 4.62.0 | `pyproject.toml` — brings `requests`, `six`, `decorator`, `lxml`, `PyJWT` |
| R5.2 | `plivo-browser-sdk` | 2.2.21 | `frontend/package.json` |
| R5.3 | Transcription SDK / HTTP client | TBD — see D4 | after the bake-off |
| R5.4 | Phase B LLM client | — | summaries only; transcript ships without it |

### R6 — People and process

| # | Requirement |
|---|---|
| R6.1 | 2–3 pilot agents on the Export or Distributor desk who take real calls daily |
| R6.2 | An admin who owns the Plivo console |
| R6.3 | Agent training: headset, availability toggle, what "Take calls: in browser / on my phone / off" means |
| R6.4 | A written call-recording notice for customers, and the announcement audio clip |
| R6.5 | An escalation path for the pilot's first bad week |

---

## Build Plan

### V1 — Calls exist and ring in the browser (12–16 days)

The narrowest thing that proves the premise: a real call, answered in a tab, filed on a thread.

| # | Work | Complexity |
|---|---|---|
| V1.1 | `voice` channel seeded; `Excom Channel Account.voice_section`; `message_type` gains `Call` | Low |
| V1.2 | `Excom Call` doctype, `provider_call_id` unique-indexed | Low |
| V1.3 | `Excom Voice Endpoint` doctype + idempotent provisioning against the Plivo Endpoint API | Medium |
| V1.4 | `providers/base.py` — `VoiceProvider` + `SoftphoneProvider` ABCs, `CallDecision`, `Destination`, `CallEvent` | Medium |
| V1.5 | `providers/plivo.py` — PlivoXML render, Calls API, Endpoint API, JWT minting, **V3 signature validation** | High |
| V1.6 | `routing.py` — sticky-then-team, presence filter, cache-backed, zero writes, p99 < 800 ms | High |
| V1.7 | `api/voice.py` — `route`, `dial_events`, `dial_action`, `hangup`, `dial`, `get_softphone_token`. Access check on line one of every authenticated endpoint. | Medium |
| V1.8 | `handler.py` — async persistence, idempotent upsert, thread + timeline stub | Medium |
| V1.9 | `presence.py` — registration heartbeat, availability toggle, Redis TTLs | Medium |
| V1.10 | `lib/softphone.ts` + `SoftphoneProvider.tsx` — singleton SDK, login by JWT, silent refresh, event bridge | High |
| V1.11 | `IncomingCallToast` + `ActiveCallBar` — answer, reject, mute, hangup, timer, resolved identity | High |
| V1.12 | `reconcile.py` + scheduler entry — CDR backfill so `duration` is never 0 | Low |
| V1.13 | Tests: signature validation, routing decision, idempotency, endpoint guards | Medium |

**V1 exit:** an inbound call to the DID rings a registered agent's browser, is answered there, and appears as a card on the right thread with the right duration.

### V2 — Calls are usable (10–14 days)

| # | Work | Complexity |
|---|---|---|
| V2.1 | Outbound dial from thread / contact / lead, both transports, one endpoint | Medium |
| V2.2 | Recording: `<Record>` config, `recording_ready` webhook, authenticated playback proxy, download as a separate permission, retention purge | Medium |
| V2.3 | `TranscriptionProvider` ABC + the chosen engine; stereo channel split into speaker-attributed turns | High |
| V2.4 | `summary.py` — LLM summary, next action, sentiment; posted to the timeline | Medium |
| V2.5 | `CallCard` in the thread timeline: player, transcript disclosure, summary, call-back | Medium |
| V2.6 | Missed-call queue as a first-class worklist with claim / assign / call back | Medium |
| V2.7 | Ring-set notifications: "Picked up by Priya", "Caller hung up · call back" | Low |
| V2.8 | International controls: agent -> account -> global, blocklist first, daily minute cap, denial log | Medium |
| V2.9 | `DeviceSettings` — mic/speaker picker, test tone, permission repair flow | Medium |
| V2.10 | Admin: voice section in `AdminLayout`, credential check, endpoint roster, copyable webhook URLs | Medium |
| V2.11 | Call history on `OmniIdentityPanel` and a Calls view in the inbox with filters | Medium |
| V2.12 | Quality metrics captured from `mediaMetrics` | Low |

**V2 exit:** the pilot desk works a full day on it, and the summaries are good enough that nobody types a call note.

### V3 — On demand (8–12 days, not scheduled)

Warm transfer and consult. Hold with music. Excom-managed IVR tree. Airtel IQ adapter. Real-time transcription over Plivo's audio-streaming hook. Voicemail. Call analytics dashboard. Native mobile softphone.

---

## Gates

| Gate | Question | Until answered |
|---|---|---|
| **G1** | Does Plivo support WebRTC <-> Indian PSTN for our entity, in writing? | **Nothing starts.** |
| **G2** | KYC cleared and DID live? | No outbound testing |
| **G3** | Does the routing endpoint hold p99 < 800 ms with the team cache cold? | V1 does not ship |
| **G4** | Does the chosen transcription engine beat 85% on 20 real Hinglish calls? | V2.4 summary does not ship; transcript ships alone |
| **G5** | Network check passed at the pilot site (WSS + UDP)? | That site uses Phone transport |
| **G6** | Security review of `api/voice.py` — access check on every endpoint, signature validation, scoped realtime? | V1 does not ship |

---

## Non-Negotiables

Each of these exists because the reference branch got it wrong. See HLD-004 §12.

1. **Realtime call events go only to the computed ring set.** Never a broadcast. Every user in the ERP seeing a caller's number breaks the visibility model the whole app is built on.
2. **Every `@frappe.whitelist()` endpoint starts with an access check.** Guardrail #2 is not optional because it is telephony.
3. **Webhooks are verified by HMAC signature**, not a token in the query string.
4. **No "ring some random System Managers" fallback.** No destination means a missed call and a loud notification.
5. **The routing endpoint writes nothing.** Every write is enqueued after the response.
6. **The browser never receives a SIP password.** Short-lived JWT only.
7. **Recording URLs are never handed to a browser.** Proxied, permission-checked, `auth=` tuple.
8. **`provider_call_id` is unique-indexed.** Duplicate webhooks must be free.
9. **The reconcile job ships in V1.** Without it, completed calls show `duration = 0` — the bug in every existing Frappe telephony integration.

---

## Anti-Scope

Not building unless real usage demands it: call queues with position announcements, predictive or power dialler, call scoring and QA workflows, supervisor barge-in and whisper, skills-based routing, multi-provider failover, voicemail transcription, conference calling.

---

## Decisions Taken

| Decision | Rationale |
|---|---|
| Browser is the phone; PSTN is a transport | The agent should be *in* Excom during the call, not on a handset next to it |
| Both transports in one parallel `<Dial>` | One code path answers every WebRTC failure mode |
| Plivo for the pilot | Only vendor with a public browser SDK, signed webhooks and mixed SIP + PSTN dial |
| Provider per channel account, not global | A site can run Plivo on sales and keep Exotel on support |
| Two new doctypes, not five | `Excom Call` and `Excom Voice Endpoint`. Routing collapses into existing `allowed_teams` + `User.mobile_no` |
| Presence in Redis, not a doctype | Ephemeral high-write runtime state |
| Not Plivo's transcription | English-only, short clips. This desk speaks Hinglish. |
| Stereo recording from day one | Speaker attribution comes from the file, not from a model guessing |
| Transcript ships before summary | Summary depends on Phase B; the transcript does not |
| `somil-dev` is a reference, not a base | Its architecture is sound; its security and realtime scoping are not |
