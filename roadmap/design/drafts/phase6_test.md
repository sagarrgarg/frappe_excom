## Phase 6 — WhatsApp Business Calling (commit: "WhatsApp Business Calling (HLD-005 Phase 6)")

Draft rows for TEST_005.

```bash
bench --site frappe.localhost migrate   # Excom WhatsApp Call Permission; calling fields on the Channel Account; ICE servers in Excom Settings
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_calling'}"
cd frontend && npx vitest run src/lib/__tests__/whatsapp-webrtc.test.ts src/lib/__tests__/whatsapp-driver.test.ts
```

**Setup before live tests:**
- A test WhatsApp number at the **2,000 tier or above** (public test numbers are exempt), with a payment method on the WABA.
- The app subscribed to the **`calls`** webhook field.
- Two phones: phone A is the "customer"; phone B is a second customer, or a second agent's browser on another machine.
- Admin → WhatsApp calling → Read from Meta, then turn calling on.
- Agents on the number's team, "Taking calls" on, microphone allowed.

### Automated tests (37 Python tests in test_whatsapp_calling; 2 vitest files)

| # | Test | Result |
|---|---|---|
| 6.1 | settings payload: holidays kept from storage when not sent; explicit holidays replace them; past ones dropped; "09:30" → "0930"; DISABLED sends the whole call_hours with the stored week and time zone, and is refused with none | ⏳ |
| 6.2 | settings validation: 3 ranges a day, overlap, open ≥ close, bad time or day, no time zone, empty week, 21 holidays, bad date, bad country code | ⏳ |
| 6.3 | save POSTs `/{phone id}/settings` with the stored holidays and keeps the rate | ⏳ |
| 6.4 | read back stores SIP status and restrictions; warns about SIP and a tier below 2,000 | ⏳ |
| 6.5 | incoming connect → Excom Call (WhatsApp, Inbound, Ringing, ring set, cta payload), WhatsApp thread, window opened, timeline stub; ringing pushed to the ready agents only, with the offer; no number or SDP in `provider_events`; a retried webhook rings nobody again | ⏳ |
| 6.6 | nobody ready → `reject` sent, call Missed, no ringing | ⏳ |
| 6.7 | answer: `pre_accept` then `accept` with the same SDP and `biz_opaque_callback_data`; In Progress; the whole ring set gets `cancel` (the answering agent's other tabs too); a second answer is refused | ⏳ |
| 6.8 | concurrent answer loses the Redis claim (no Meta call); the same agent may retry | ⏳ |
| 6.9 | a failed accept releases the claim (Redis and the row's `agent`); the call stays Ringing | ⏳ |
| 6.10 | terminate: Completed with duration, reconciled, busy cleared, terminate signal; FAILED keeps the error code; FAILED after talking (duration > 0) is Completed with the code; unanswered → Missed; reconcile finds closed calls only | ⏳ |
| 6.11 | BSUID-only caller: identity by user id, no number on the call; calling back uses `recipient` for both the permission check and `connect` | ⏳ |
| 6.12 | permission request: 1 per 24 h, 2 per 7 days; the interactive payload; inbox preview "[Allow calls]"; a second request the same day refused before Meta | ⏳ |
| 6.13 | permission request refused when the 24 h window is closed | ⏳ |
| 6.14 | `call_permission_reply` parsed (accept 7 days / permanent / declined / automatic withdrawal); the inbound hook sets temporary → no_permission → permanent | ⏳ |
| 6.15 | GET /call_permissions parsed; a lapsed temporary permission counts as none; permanent cannot be requested again; 2 unanswered warn | ⏳ |
| 6.16 | outbound: permission checked, `connect` with the offer, `to` and the call name; answer SDP signalled to the agent; ACCEPTED → In Progress; terminate → Completed with cost (6-second pulses) | ⏳ |
| 6.16b | outbound ACCEPTED reopens the 24 h window (`last_inbound_at`) | ⏳ |
| 6.17 | outbound REJECTED → Busy, kept after terminate; agent not busy; unanswered count 1; a retried terminate counts once and sends no second "call ended" | ⏳ |
| 6.18 | no permission → refused before Meta, no call row | ⏳ |
| 6.19 | Meta 138006 on connect → permission row no_permission with the error, call Failed, agent not busy; 138012 keeps Meta's next allowed time and says it | ⏳ |
| 6.20 | `voice_call` / `call_permission_request` builders and limits | ⏳ |
| 6.21 | `/calls` action payloads, terminate status forms (string or list), cost, scrubbing, ICE server parsing, descriptor; registry returns the WhatsApp adapter, and still Plivo / Twilio for voice lines | ⏳ |
| 6.24 | unsigned `calls` payload: no call, no ring, no reject; recorded "unsigned, ignored" without number or SDP | ⏳ |
| 6.25 | unknown `phone_number_id` is ignored even with a default incoming account (no fallback) | ⏳ |
| 6.26 | answer by a non-ring-set agent → PermissionError; hang-up by a non-member or by a ringing member → PermissionError, nothing sent; a ringing member cannot hang up A's In Progress call; A's own hang-up sends one `terminate`, closes the row (Completed, agent) and frees A; the later terminate adds the duration | ⏳ |
| 6.27 | a manager's hang-up on a ringing incoming call sends `reject` (not `terminate`), Missed; the terminate after it sends no second "call ended" | ⏳ |
| 6.28 | decline: a non-member is ignored; the last decline (timeout) sends one `reject`, Missed "Nobody answered" | ⏳ |
| 6.29 | terminate before connect → Missed row; the late connect rings nobody and `ringing_for` is empty | ⏳ |
| 6.29b | answer after terminate → refused before Meta; terminate while Meta accepts → stays Missed, nobody answered, agent not busy | ⏳ |
| 6.29c | lines only for line agents; a number with no calling status counts, DISABLED does not; the first call marks it ENABLED and sends `excom:wa_call_lines_changed` to its agents | ⏳ |
| 6.29d | `ringing_for` (reload while ringing): the pop with the offer and the seconds left; not for a non-member or an agent who declined | ⏳ |
| 6.29e | call_icons sent only when edited (`call_icons_edited`); a save leaves the stored countries alone | ⏳ |
| 6.29f | DISABLED call hours via save: the stored week and time zone are sent; with none stored, refused before Meta and nothing stored | ⏳ |
| 6.29g | request window from Meta's counters (current_usage, limit_expiration_time) plus Excom's sends after that reading; earlier sends not counted twice | ⏳ |
| 6.29h | "Check permission again" asks Meta at most once a minute per number and contact; start_call / request_permission always ask | ⏳ |
| 6.29i | ICE credential only from the Password field; a pasted one moves there on save, or the save is refused when it conflicts | ⏳ |
| 6.22 | (static) pure functions above, import targets, endpoint guards and permission bypasses, run against the worktree without a database | ✅ ran |
| 6.23 | (frontend) ICE gathering wait (complete / timeout), CRLF, single audio, no-candidate refusal, ICE server normalising | ✅ ran |
| 6.23b | (frontend, fake RTCPeerConnection / getUserMedia / fetch) hang-up during the mic prompt or the ICE wait while answering: no `answer` posted, `decline` posted, tracks stopped, peer closed; hang-up while the answer is in flight → `hangup` after it returns; the same for placing a call (no `start_call`; `hangup` once start_call returns); connection failure posts `hangup`; `pagehide` beacon with the CSRF token | ✅ ran |
| 6.23c | (frontend) no track on the sender until accept (incoming) / ACCEPTED (outgoing); "connected" not taken from the ICE state; Meta's answer applied once when signal and response both carry it | ✅ ran |
| 6.23d | (frontend) two offers on one number kept by call id: cancel / terminate drop only their own, answering one posts that call; a dismissed offer's cancel says nothing; timeout decline flagged | ✅ ran |
| 6.23e | (frontend, coordinator) a WhatsApp offer refused during a live Plivo call is dropped; its cancel / terminate, and even a stray `incomingCancelled` / `ended` from the WhatsApp line, leave the Plivo call up; a WhatsApp cancel ends the WhatsApp call that is ringing; driver options logged with credentials redacted | ✅ ran |

### Live with Meta (two phones, test number at the 2K tier)

Settings:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 6.30 | Read from Meta, then Save with call hours and 2 holidays | L | GET shows the same hours and both holidays; saving again without touching holidays keeps them | ⏳ |
| 6.31 | Save on a number below the 2K tier | L | Meta's 138015 is shown in words | ⏳ |
| 6.32 | Call icon hidden | L | the icon disappears from chat (may take up to 7 days; opening chat info refreshes it); a voice_call button still works | ⏳ |

Incoming:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 6.33 | Phone A calls the business; two agents ready | L | both browsers ring with "WhatsApp call"; agent 1 answers; two-way audio within ~2 s, start not clipped; agent 2's pop says "Picked up by …" | ⏳ |
| 6.34 | Phone A hangs up | L | the call ends in the browser; Excom Call Completed with Meta's duration; the timeline shows "Call · …" | ⏳ |
| 6.35 | Agent hangs up | L | phone A sees the call end; one `terminate` sent, no error | ⏳ |
| 6.36 | Nobody answers | L | the pops count down 25 s and decline; Excom rejects; Missed ("Nobody answered"); in the missed-call queue | ⏳ |
| 6.36b | Reload the agent's tab while it rings | L | the pop comes back with the seconds left; Answer works | ⏳ |
| 6.36c | Close the tab during a call | L | phone A sees the call end within seconds (beacon `hangup`) | ⏳ |
| 6.36d | Agent 1 on a Plivo/Twilio call; phone A calls on WhatsApp and agent 2 answers | L | agent 1's call is untouched; no pop on agent 1 | ⏳ |
| 6.36e | Hang up during the microphone prompt (Answer, then Hang up before allowing) | L | phone A keeps ringing for the others (or is declined when nobody else); the mic indicator goes off | ⏳ |
| 6.37 | Nobody "Taking calls" | L | phone A sees the call declined at once; Missed | ⏳ |
| 6.38 | Both agents decline | L | `reject` sent after the second decline | ⏳ |
| 6.39 | Agent on a strict network (UDP blocked) | L | without TURN: "audio could not connect"; with TURN configured in Excom Settings: audio works | ⏳ |
| 6.40 | voice_call button sent, phone A taps it | L | the call rings agents; `ivr_selection` holds the button payload | ⏳ |
| 6.41 | Username-only caller (no number shared) | L | the call lands on the identity by user id | ⏳ |

Outgoing:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 6.42 | "Ask permission to call" (window open) | L | phone A gets the request; Allow → the menu shows "Allowed calls until …" after "Check permission again" or the reply | ⏳ |
| 6.43 | Ask again the same day | L | the menu item is disabled with the next possible time | ⏳ |
| 6.44 | Call phone A with permission | L | phone A rings and its timer does not start before it is picked up (no media before ACCEPTED); answer → audio both ways; "x/100 calls today" goes up; cost estimate on the call | ⏳ |
| 6.45 | Phone A declines | L | toast "They declined the call."; Busy | ⏳ |
| 6.46 | Agent cancels while ringing | L | phone A stops ringing; Canceled | ⏳ |
| 6.47 | Call without permission (Decline on phone A) | L | refused before Meta, with the reason | ⏳ |
| 6.48 | DTMF from the keypad on an IVR-less call | L | in-band tones; Meta sends no DTMF webhook (none expected) | ⏳ |

### Known gaps (not in Phase 6)

**Not built:**
- **SIP** (enabling it turns off browser calls), voicemail settings, recording and transcription, transfer, and conferencing.
- **`account_settings_update`** (Meta's settings-change webhook) is not handled. Phase 5 may own that field; if it adds the handler, it should call `whatsapp_call_settings.apply_remote(account, value["phone_number_settings"])`.

**Not handled yet:**
- **Identity merges** do not move `Excom WhatsApp Call Permission` rows; the next GET /call_permissions rebuilds them.
- **The TURN credential is static and shared** by every agent's browser (time-limited TURN credentials are not built).
- **A tab closed while `start_call` is in flight** cannot end that call (the browser has no call id yet); Meta rings it out.
- **The same agent answering in two tabs at once**: the claim lets the same user retry, so both tabs could send an answer. The other tab is told to stop as soon as the first accept succeeds.

**UNVERIFIED (live testing will settle these):**
- Whether a cancelled outbound call counts towards Meta's "4 unanswered" revocation.
- The exact wording of 138012's `error_data.details` (Excom reads a Unix time or an ISO time from it).

### Review fixes (code review of 817ea1a)

- **H1** driver: one state object per call with a token, checked after every await; stream and peer closed when the call was abandoned; no `answer` / `start_call` for an abandoned call; `hangup` when it was abandoned after the post.
- **H2** offers the coordinator refuses are dropped (`driver.dismiss`); `incomingCancelled` acts only on the ringing call of that line with that call id; `ended` only from the line holding the call.
- **H3** lines include numbers whose calling status is unknown (only DISABLED is left out); `excom:wa_call_lines_changed` on a status change (first call, save, read) and a 5-minute revalidate.
- **H4** unsigned `calls` webhooks are recorded and ignored.
- **M1** hang-up only by the agent on the call or a manager; ring-set members decline; ringing incoming hang-up sends `reject`.
- **M2** `RING_SECONDS` 25; the pop declines at 0 (`timeout=1`).
- **M3** outbound "connected" only on ACCEPTED; `replaceTrack(null)` until accept, both directions.
- **M4** exact `wa_phone_id` match; unknown numbers recorded and ignored.
- **M5** claim (`agent` while Ringing) committed before the Graph calls; reverted on failure; In Progress and terminate re-read the row under the lock.
- **M6** the driver posts `hangup` / `decline` when it ends a call itself; `pagehide` beacon with the CSRF token in the form (the voice heartbeat sign-off beacon now carries it too).
- **M7** pending offers keyed by call id.
- **M8** `cancel` to the answering agent's other tabs.
- **M9** call hours always sent whole (or refused); `call_icons` only when edited.
- **Low:** duplicate terminates skipped (`reconciled`); busy set before `connect` and cleared on failure / early close; own hang-up closes the row; FAILED after talking is Completed; ACCEPTED reopens the window; Meta's request counters used; 138012's next time kept; 138021/138022 texts name the person's WhatsApp; credentials redacted in the log, refused/moved from the ICE JSON; `ice_servers()` keeps the request's messages; `_OFFER` serves `ringing` (reload while ringing), `UNANSWERED_REVOKE` drives the warning; permission refresh throttled and not on focus; WhatsApp target prefers the WhatsApp number / ids; the outbound answer is applied once.
