## Phase 6 — WhatsApp Business Calling: what was built (2026-11-04)

Draft for HLD-005 §10. Not reviewed yet; MariaDB was down, so no Frappe test has run (see `phase6_test.md`).

**Where it plugs in**

- **A WhatsApp number is a voice line.** `providers.for_account()` returns `WhatsAppCallingAdapter` (`channels/voice/providers/whatsapp.py`) for any WhatsApp Channel Account. It implements both `VoiceProvider` (hang up, normalise a webhook, call details) and `SoftphoneProvider` (descriptor with ICE servers; no endpoint to provision and no token to mint).
- **Who rings** reuses `routing.line_agents()` (the number's teams; none = every agent) and `presence` (heartbeat + "Taking calls" + not busy). The last agent the person spoke to comes first, and everyone ready rings in parallel.
- **The record** is an `Excom Call` with provider `WhatsApp`. It sits on the person's **WhatsApp thread**, so the call shows beside the chat, with a `Call` stub message in the timeline. The end of a call goes through `handler.apply_event()`, the same path Plivo and Twilio use.
- **The browser** gets a `whatsapp-driver.ts` behind the `SoftphoneDriver` seam (sdk `whatsapp-webrtc`). There is no vendor SDK: the driver uses `RTCPeerConnection` directly. A new optional `signal()` on the driver carries the SDP and call progress that Excom relays from Meta's webhooks over Frappe realtime.
- **Orchestration** is in `channels/voice/whatsapp_calling.py`, calling settings are in `whatsapp_call_settings.py`, and the endpoints are in `api/whatsapp_calling.py`.

**Incoming call (the person calls the business; free)**

1. **Connect webhook** (`calls` field, SDP offer):
   - Signed payloads only. An unsigned `calls` change is recorded in the WhatsApp event log ("unsigned, ignored") and not acted on.
   - The number must match a Channel Account's `wa_phone_id` exactly; there is no fallback to the default account. An unknown number is recorded and ignored.
   - An Excom Call is created (Inbound, Ringing). It is idempotent on the `wacid`; a connect that arrives after its terminate rings nobody.
   - The call bumps the thread and opens the 24 h window, whether or not it is answered.
   - The offer is cached for 3 minutes. A browser that reloads while the call rings asks `ringing` and gets the pop back (with the seconds left).
2. **Ringing.** `excom:call_ringing` is published only to the ready agents, carrying the offer, the call and the thread.
   - If nobody is ready, the call is rejected at once and logged as Missed.
   - The first call on a number whose calling status Excom does not know marks it ENABLED and sends `excom:wa_call_lines_changed` to its agents.
   - Agents' browsers carry every active WhatsApp number they work whose calling status is not DISABLED (unknown counts), and fetch the list again on `excom:wa_call_lines_changed` and every 5 minutes.
   - The pop counts down 25 s (`RING_SECONDS`, inside Meta's 30–60 s) and at 0 declines for that agent (`decline`, `timeout=1`).
3. **Answer.** The answering browser builds the answer: `setRemoteDescription(offer)`, a sender with **no track** on it (`replaceTrack(null)`: WhatsApp counts the first media packet as accepted), `createAnswer`, ICE gathering complete (4 s cap, no trickle), CRLF. It posts the answer to `answer`.
   - Every step re-checks that the call is still current. A hang-up during the microphone prompt or the ICE wait posts nothing but a `decline`, and closes the peer and the microphone. A hang-up while the answer is in flight posts `hangup` once the answer returns.
4. **First answer wins.** A Redis `SET NX` claim, then `SELECT … FOR UPDATE` on the row; the claim is the row's `agent` (status still Ringing), committed **before** Meta is asked, so no lock is held across the Graph calls. Excom then sends `pre_accept` and `accept` with the same SDP (`biz_opaque_callback_data` = the Excom Call name).
   - If either action fails, the claim is given back (`agent` cleared, Redis released) so a colleague can answer.
   - Marking it In Progress re-reads the row under the lock: if a terminate closed it meanwhile, it stays closed and the agent is told the caller hung up.
   - The microphone track goes on the sender only after `accept` returns 200.
5. **The others stop ringing.** `excom:call_answered` goes to the whole ring set, and a `cancel` signal goes to the whole ring set **including the answering agent**, so the agent's other tabs stop too (the tab that answered holds the call and ignores it). A WhatsApp offer the softphone does not show (the agent is on another call, on any line) is dropped by the driver, so its later cancel cannot end that call.
6. **Decline.** One agent's Decline only silences that agent. When every agent in the ring set has declined (or timed out), Excom sends `reject`.
7. **Hang-up.** Only the agent on the call (`answered_by` / `agent`) or a manager (System Manager, Excom Admin — the same reach as a Plivo/Twilio `end_call`) may hang up; a ring-set colleague may only decline. While an incoming call still rings, hang-up sends `reject`; once connected, `terminate`. A successful hang-up closes the row at once (Completed / Missed / Canceled) and frees the agent; the terminate webhook adds the duration later.
   - The driver also ends the call at Meta when the connection fails or the answer cannot be applied, and on `pagehide` it sends a beacon (`hangup`, or `decline` for an unanswered call) with the CSRF token in the form.
8. **End.** The `terminate` webhook ends the call.
   - Status: COMPLETED with a duration is Completed; without one it is Missed (No Answer for outbound); FAILED is Failed, unless the call had a duration (then Completed, with the error code kept). The error code from `errors[]` goes into `hangup_cause`.
   - The terminate webhook is the call detail record: the row is locked, re-read and marked `reconciled`. A retried terminate (already reconciled) is skipped, so the unanswered count and the ended event happen once; a terminate for a call already closed with nothing new (no duration, no error) records itself without a second "call ended".
   - The reconcile sweep treats only closed WhatsApp calls as found.

**Outgoing call (the business calls; needs permission; billed per minute)**

1. **Permission** is cached per (number, contact) in the new doctype `Excom WhatsApp Call Permission`:
   - `GET /call_permissions` (`user_wa_id`, or `recipient` for a BSUID) at most every 5 minutes, and always before a call.
   - A temporary permission past its expiry counts as none, because Meta sends no webhook when it lapses.
2. **Request.** "Ask permission to call" sends the `call_permission_request` interactive message:
   - only inside the 24 h window;
   - only when the count allows it and Meta's `can_perform_action` agrees. The count is Meta's (`limits[].current_usage`, and `limit_expiration_time` as the next possible time) plus requests Excom sent after that reading; without Meta's limits, Excom's own count (1 per 24 h, 2 per 7 days);
   - never when the permission is permanent (138017).
   - The send times are stored. They are cleared after a connected call, because Meta resets its counters then.
3. **Reply.** `call_permission_reply` arrives as an inbound message ("📞 Allowed calls for 7 days" / "Declined calls" / "withdrawn (unanswered calls)"). It is handed to `whatsapp_calling.on_inbound` through `excom_inbound_handlers`, from signed payloads only, and sets temporary/permanent/no_permission with the expiry.
4. **Call.** The browser creates the offer (gathering complete) and posts `start_call`.
   - Excom checks consent (STOP / do-not-contact), the permission and the daily cap.
   - It inserts the Excom Call, then sends `connect` with `biz_opaque_callback_data` = the call name, so a webhook that beats the response still finds the row.
   - The agent is marked busy before `connect` and freed if it fails, or if a REJECTED / terminate webhook closed the call before the response came back.
   - Meta's refusals are kept on the permission row: 138006 means no permission, 138012 means the daily cap (with Meta's next allowed time, shown to the agent), 138009 means the request limit.
   - Whom to call: the WhatsApp number, else a WhatsApp channel id, else the BSUID; the plain phone number only on this person's own WhatsApp thread.
5. **Progress.**
   - The connect webhook (Meta's answer SDP, which arrives before RINGING) and the RINGING / ACCEPTED / REJECTED statuses are pushed to that agent's browser as `excom:wa_call_signal`.
   - The browser applies the answer once (a flag set before the async apply), keeps the microphone off the sender, and shows "connected" only on **ACCEPTED**, when the track goes on (`replaceTrack`, no renegotiation).
   - ACCEPTED marks the call In Progress and reopens the 24 h window (the thread's `last_inbound_at`). REJECTED makes it Busy ("They declined the call.").
   - Unanswered calls are counted in a row. The UI warns from 2, because WhatsApp revokes the permission at 4.
   - Cost is estimated from the number's "rate per minute" in 6-second pulses.

**Buttons**

- `whatsapp_interactive.voice_call()`: body required; display text ≤ 20; `ttl_minutes` 1–43200; payload ≤ 512. The payload comes back as `cta_payload` and is stored in the call's `ivr_selection`.
- `whatsapp_interactive.call_permission_request()`: body optional, no header or footer.
- Both have an inbox preview. The conversation menu has "Send a call button".
- The template `voice_call` button belongs to Phase 4.

**Settings per number** (Admin → WhatsApp calling)

- Fields: status, call icon (shown or hidden), countries the icon shows in, callback permission, call hours (time zone, ≤ 2 ranges a day, "HHMM", no overlap), holidays (≤ 20; past ones dropped).
- `call_icons` is sent only when the countries were edited on the screen (`call_icons_edited`), so a save never wipes a restriction set in WhatsApp Manager.
- Call hours are always sent whole, DISABLED included (with the stored week and time zone); with none stored the save is refused with a sentence.
- A change of calling status (save or read) sends `excom:wa_call_lines_changed` to the number's agents.
- "Read from Meta" stores the GET response, including SIP status and restrictions, on the Channel Account (section "WhatsApp Calling", read-only there).
- "Save to Meta" validates and POSTs the whole `calling` object. The holidays are always resent, because Meta replaces `call_hours` on every POST.
- Warnings:
  - the number is below the 2,000 tier (138015);
  - SIP is on (Graph `/calls` then answers 131055, so the browser cannot take calls);
  - Meta has restricted calling;
  - only a public STUN server is configured;
  - there is no PSTN leg.
- **ICE servers:** Excom Settings → Calls → WhatsApp calling. A JSON list, with the TURN credential in an encrypted Password field. A credential pasted into the JSON is moved into that field on save (or the save is refused if it conflicts) and is never used from the JSON. They reach the browser only in the line descriptor; the browser log redacts credentials. The default is a public STUN server.
  - The TURN credential is **static and shared** by every agent's browser. Time-limited TURN credentials (TURN REST API) are a known gap.

**Privacy**

- `provider_events` stores each webhook through `webhook._scrub` and also blanks the numbers, user ids and the SDP, which holds IP addresses. The list is bounded to 40 entries.
- The SDP is cached for 3 minutes and never written to the database.

**Frontend**

- **Lines and drivers:** `useSoftphone` fetches `whatsapp_calling.lines` and boots one WhatsApp driver per number, with no token. Every line shares one set of coordinator hooks, so the hooks no longer depend on which line booted last.
- **Presence:** the heartbeat and the sign-off beacon cover WhatsApp numbers too.
- **Ringing:** the ringing pop forwards the offer to the driver.
- **Labels:** IncomingCallToast shows "WhatsApp call" and opens the WhatsApp thread. ActiveCallBar shows "WhatsApp".
- **Availability toggle:** shows on WhatsApp-only sites.
- **Conversation header:** "WhatsApp call" with a menu: call; ask permission (n of 2 this week); send a call button; permission status and today's calls against the cap; check again. The permission is not re-read on window focus; `permission` is rate-limited (30/min per user) and asks Meta at most once a minute per number and contact.

**Not built:** SIP, voicemail settings, call recording or transcription, transfer, the `account_settings_update` webhook (Phase 5's field; `whatsapp_call_settings.apply_remote()` is ready for it), time-limited TURN credentials, and calling-permission templates (Phase 4).
