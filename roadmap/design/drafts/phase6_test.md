## Phase 6 — WhatsApp Business Calling (commit: "WhatsApp Business Calling (HLD-005 Phase 6)")

Draft rows for TEST_005.

```bash
bench --site frappe.localhost migrate   # Excom WhatsApp Call Permission; calling fields on the Channel Account; ICE servers in Excom Settings
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_calling'}"
cd frontend && npx vitest run src/lib/__tests__/whatsapp-webrtc.test.ts
```

**Setup before live tests:**
- A test WhatsApp number at the **2,000 tier or above** (public test numbers are exempt), with a payment method on the WABA.
- The app subscribed to the **`calls`** webhook field.
- Two phones: phone A is the "customer"; phone B is a second customer, or a second agent's browser on another machine.
- Admin → WhatsApp calling → Read from Meta, then turn calling on.
- Agents on the number's team, "Taking calls" on, microphone allowed.

### Automated tests (22)

| # | Test | Result |
|---|---|---|
| 6.1 | settings payload: holidays kept from storage when not sent; explicit holidays replace them; past ones dropped; "09:30" → "0930"; disabled with no week leaves call_hours out | ⏳ |
| 6.2 | settings validation: 3 ranges a day, overlap, open ≥ close, bad time or day, no time zone, empty week, 21 holidays, bad date, bad country code | ⏳ |
| 6.3 | save POSTs `/{phone id}/settings` with the stored holidays and keeps the rate | ⏳ |
| 6.4 | read back stores SIP status and restrictions; warns about SIP and a tier below 2,000 | ⏳ |
| 6.5 | incoming connect → Excom Call (WhatsApp, Inbound, Ringing, ring set, cta payload), WhatsApp thread, window opened, timeline stub; ringing pushed to the ready agents only, with the offer; no number or SDP in `provider_events`; a retried webhook rings nobody again | ⏳ |
| 6.6 | nobody ready → `reject` sent, call Missed, no ringing | ⏳ |
| 6.7 | answer: `pre_accept` then `accept` with the same SDP and `biz_opaque_callback_data`; In Progress; the other agent gets `cancel`; a second answer is refused | ⏳ |
| 6.8 | concurrent answer loses the Redis claim (no Meta call); the same agent may retry | ⏳ |
| 6.9 | a failed accept releases the claim; the call stays Ringing | ⏳ |
| 6.10 | terminate: Completed with duration, reconciled, busy cleared, terminate signal; FAILED keeps the error code; unanswered → Missed; reconcile finds closed calls only | ⏳ |
| 6.11 | BSUID-only caller: identity by user id, no number on the call; calling back uses `recipient` for both the permission check and `connect` | ⏳ |
| 6.12 | permission request: 1 per 24 h, 2 per 7 days; the interactive payload; inbox preview "[Allow calls]"; a second request the same day refused before Meta | ⏳ |
| 6.13 | permission request refused when the 24 h window is closed | ⏳ |
| 6.14 | `call_permission_reply` parsed (accept 7 days / permanent / declined / automatic withdrawal); the inbound hook sets temporary → no_permission → permanent | ⏳ |
| 6.15 | GET /call_permissions parsed; a lapsed temporary permission counts as none; permanent cannot be requested again; 2 unanswered warn | ⏳ |
| 6.16 | outbound: permission checked, `connect` with the offer, `to` and the call name; answer SDP signalled to the agent; ACCEPTED → In Progress; terminate → Completed with cost (6-second pulses) | ⏳ |
| 6.17 | outbound REJECTED → Busy, kept after terminate; unanswered count 1 | ⏳ |
| 6.18 | no permission → refused before Meta, no call row | ⏳ |
| 6.19 | Meta 138006 on connect → permission row no_permission with the error, call Failed | ⏳ |
| 6.20 | `voice_call` / `call_permission_request` builders and limits | ⏳ |
| 6.21 | `/calls` action payloads, terminate status forms (string or list), cost, scrubbing, ICE server parsing, descriptor; registry returns the WhatsApp adapter | ⏳ |
| 6.22 | (static) pure functions above, import targets, endpoint guards and permission bypasses, run against the worktree without a database | ✅ ran |
| 6.23 | (frontend) ICE gathering wait (complete / timeout), CRLF, single audio, no-candidate refusal, ICE server normalising | ✅ ran |

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
| 6.36 | Nobody answers for 60 s | L | Meta ends it; Missed; in the missed-call queue | ⏳ |
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
| 6.44 | Call phone A with permission | L | phone A rings; answer → audio both ways; "x/100 calls today" goes up; cost estimate on the call | ⏳ |
| 6.45 | Phone A declines | L | toast "They declined the call."; Busy | ⏳ |
| 6.46 | Agent cancels while ringing | L | phone A stops ringing; Canceled | ⏳ |
| 6.47 | Call without permission (Decline on phone A) | L | refused before Meta, with the reason | ⏳ |
| 6.48 | DTMF from the keypad on an IVR-less call | L | in-band tones; Meta sends no DTMF webhook (none expected) | ⏳ |

### Known gaps (not in Phase 6)

**Not built:**
- **SIP** (enabling it turns off browser calls), voicemail settings, recording and transcription, transfer, and conferencing.
- **`account_settings_update`** (Meta's settings-change webhook) is not handled. Phase 5 may own that field; if it adds the handler, it should call `whatsapp_call_settings.apply_remote(account, value["phone_number_settings"])`.

**Not handled yet:**
- **A reload while ringing** loses the offer (it is not re-fetched). Wait for the next call or ask the caller to call again.
- **A number whose calling status Excom never read** rejects its first call (nobody's browser has the line yet). Read from Meta once in Admin, or the first call marks it ENABLED.
- **Identity merges** do not move `Excom WhatsApp Call Permission` rows; the next GET /call_permissions rebuilds them.

**UNVERIFIED (live testing will settle these):**
- When Meta sends the business-initiated connect webhook with the answer SDP: when the call starts ringing, or only when the person answers. The driver handles either.
- Whether `call_hours` with only `{"status": "DISABLED"}` is accepted; Excom leaves call_hours out instead.
- Whether a cancelled outbound call counts towards Meta's "4 unanswered" revocation.
