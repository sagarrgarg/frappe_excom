# HLD-005 — What Still Needs Testing (living document)

Every phase of HLD-005 (the WhatsApp platform) adds rows here. A row stays until it has a result.

**Results:** ⏳ not run yet · ✅ pass · ❌ fail (fixed in commit …) · ⏭ skipped (reason)

**How it was tested:**
- **U** — automated test (`excom.excom.tests.test_whatsapp_platform`)
- **M** — migrate / patch on a site
- **L** — live with a real WhatsApp Business Account (test number)
- **D** — diagnostics screen (Admin → WhatsApp → Diagnose)

**Regression found and fixed:** commit f4d2759 (Phase 0) accidentally deleted the media functions from `whatsapp_service.py` (`media_reference`, `upload_media`, `send_media_message`, `send_sticker_message`). Image, document and sticker sends, template media headers and the delivery-retry watchdog would all have failed. They were restored in the Phase 2 commit. Test 0.26 (full suite, including `test_whatsapp_media`) covers this.

**Blocked right now:** MariaDB is stopped on the dev machine, so nothing below has run. Phase 0 was committed untested at the owner's request (2026-11-03).

---

## 0. How to run

```bash
# start services (needs sudo for MariaDB)
sudo systemctl start mariadb
cd ~/frappe-bench
redis-server config/redis_cache.conf --daemonize yes
redis-server config/redis_queue.conf --daemonize yes

# schema + patch
bench --site frappe.localhost migrate

# Phase 0 tests only
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_platform'}"

# full excom suite (regressions)
bench --site frappe.localhost execute excom.excom.tests.run.run
```

---

## Phase 0 — platform deadlines (commit: "WhatsApp platform deadlines (HLD-005 Phase 0)")

### Migrate and patch

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.1 | `bench migrate` applies the new fields and doctypes (Excom Consent, Excom WhatsApp Event) | M | no errors | ⏳ |
| 0.2 | Patch `upgrade_graph_api_version` | M | every stored `wa_version` / `meta_api_version` / `api_version` below v24 becomes v26.0; v24+ left alone | ⏳ |
| 0.3 | Channel Account form | M | new "WhatsApp Platform" section; Share Activity is blank / On / Off | ⏳ |
| 0.4 | Excom Consent permissions | M | an agent can read but not create; nobody can edit; delete is refused | ⏳ |

### Automated tests (31)

Version, errors and routing:

| # | Test | Result |
|---|---|---|
| 0.5 | graph version floor; calls use v26 even with v21 stored | ⏳ |
| 0.6 | error classes; rate-limit error is still an ExcomRateLimitError and keeps retry_after; 135000 never pauses a campaign | ⏳ |
| 0.7 | marketing goes to /marketing_messages when enabled (product policy; activity sharing only when chosen); utility always on /messages | ⏳ |
| 0.8 | 131063 retries once on /marketing_messages and remembers the category | ⏳ |
| 0.9 | held_for_quality_assessment is recorded on the message | ⏳ |

Consent gate:

| # | Test | Result |
|---|---|---|
| 0.10 | marketing opt-out blocks marketing only; STOP blocks every template; unknown category is gated as marketing | ⏳ |
| 0.11 | the gate sees an opt-out on a duplicate record; a merge keeps the opt-out and writes a ledger row | ⏳ |
| 0.12 | a business-scoped user id is sent as `recipient` | ⏳ |

Statuses and consent webhooks:

| # | Test | Result |
|---|---|---|
| 0.13 | status batch: every entry applied; never goes backwards; pricing stored | ⏳ |
| 0.14 | 131050 failure → marketing opt-out + ledger row | ⏳ |
| 0.15 | user_preferences stop → resume (scoped to the account) | ⏳ |
| 0.16 | unsigned payload cannot change consent | ⏳ |
| 0.17 | "Stop promotions" button stops marketing only; START only undoes the person's own STOP | ⏳ |

Identity:

| # | Test | Result |
|---|---|---|
| 0.18 | username-only sender gets an identity; the same user id with a number joins it | ⏳ |
| 0.19 | user_changed_number relinks; the old number is history only | ⏳ |
| 0.20 | recycled number → new identity, old record releases the number, both flagged | ⏳ |

Account and template events:

| # | Test | Result |
|---|---|---|
| 0.21 | template_category_update (done) changes routing; advance notice does not | ⏳ |
| 0.22 | number quality / capability / MM_LITE_TERMS_SIGNED update the account; account_alerts reads `alert_info` | ⏳ |

Ledger, broadcasts and analytics:

| # | Test | Result |
|---|---|---|
| 0.23 | consent ledger is append-only | ⏳ |
| 0.24 | broadcast: opted-out person skipped, campaign pauses on 132015, resume sends nobody twice | ⏳ |
| 0.25 | analytics overview no longer asks for conversation_analytics | ⏳ |
| 0.26 | **full excom suite still passes** (send_transactional, auto-ack, watchdog, subscribers, identity naming…) | ⏳ |

### Live with Meta (test WABA + test number)

Sending and webhooks:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.27 | Send a utility template | L | delivered; Excom Message gets `wa_pricing_category=utility` from the status webhook | ⏳ |
| 0.28 | Accept the Marketing Messages terms → webhook `account_update MM_LITE_TERMS_SIGNED` | L | "Marketing Messages Terms Signed" ticked; diagnostics shows ONBOARDED | ⏳ |
| 0.29 | Send a marketing template with "Send Marketing via Marketing Messages API" on | L | goes to /marketing_messages; status pricing category `marketing_lite` | ⏳ |
| 0.30 | Stop promotions in the WhatsApp app (test phone) | L | `user_preferences` stop → Stopped WhatsApp Marketing ticked; next marketing send refused before Meta; a utility send still goes | ⏳ |
| 0.31 | Resume promotions | L | opt-out lifted; ledger has Withdrawn then Granted | ⏳ |
| 0.32 | Reply STOP, then START | L | WhatsApp opt-out set, then cleared | ⏳ |
| 0.33 | Template quick-reply "Stop promotions" button tapped | L | marketing opt-out from `template_optout_button@<waba>` | ⏳ |

Identity:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.34 | A contact with a WhatsApp username (no number shared) messages in | L | identity created with WhatsApp User ID and username; the agent can reply (sent with `recipient`) | ⏳ |
| 0.35 | Test phone changes number (if possible) | L | user_changed_number relinks; old number kept as history | ⏳ |

Diagnostics:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 0.36 | Diagnose: Graph version, app secret, app subscribed, webhook fields (and their version), billing currency, Marketing Messages, health, quality | D | each check green, or red with the next step | ⏳ |
| 0.37 | Webhook field versions ≥ v24 in App Dashboard | D | no "payload version" warning | ⏳ |
| 0.38 | Analytics page | L | conversation card shows pricing-based counts and cost; no Meta error | ⏳ |

### Outside the code (deadlines)

| # | Item | Deadline | Result |
|---|---|---|---|
| 0.39 | WhatsApp billing in INR (migration creates a new WABA id and new template ids: update Business Account ID, re-subscribe the app, run template sync) | 2026-12-31 | ⏳ |
| 0.40 | Graph v21 retired — patch run on every site | 2027-01-21 | ⏳ |
| 0.41 | Business verification (messaging limit 250 → 2,000) | before the dealer list passes 250 | ⏳ |
| 0.42 | Marketing on /messages retired — every account switched to the Marketing Messages API | 2027-10-13 (2027-04-13 for new accounts) | ⏳ |
| 0.43 | DPDP consent: consent rows imported for existing dealers before the first broadcast | ~2027-05-13 | ⏳ |

---

## Phase 1 — messaging basics (commit: "WhatsApp messaging basics (HLD-005 Phase 1)")

```bash
bench --site frappe.localhost migrate   # WhatsApp Button flow fields, Excom Message type "Order"
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_messaging'}"
```

### Automated tests (22)

| # | Test | Result |
|---|---|---|
| 1.1 | reply buttons: 1–3, titles ≤ 20 and unique | ⏳ |
| 1.2 | list: at most 10 rows in total across sections | ⏳ |
| 1.3 | product list / product carousel (2–10) / Flow with first-screen data and draft mode / CTA URL must be http(s) | ⏳ |
| 1.4 | send: interactive payload, quoted reply (`context`), recipient | ⏳ |
| 1.5 | address form refused outside India | ⏳ |
| 1.6 | send to thread: refused when the 24 h window is closed; recorded in the inbox when open | ⏳ |
| 1.7 | STOP blocks interactive messages too | ⏳ |
| 1.8 | typing indicator + read receipt payload | ⏳ |
| 1.9 | named template: `parameter_name` on every parameter; quick-reply payloads per button | ⏳ |
| 1.10 | positional templates are sent exactly as before | ⏳ |
| 1.11 | template creation: `parameter_format: NAMED`, named examples, real FLOW button (flow_id, action, screen) | ⏳ |
| 1.12 | quick replies split by another button are rejected | ⏳ |
| 1.13 | cart order: stored as "Order" with items and total; dispatched with the order; a failing handler does not stop the next | ⏳ |
| 1.14 | button / list / template quick-reply taps are dispatched by id or payload, never by title | ⏳ |
| 1.15 | Flow answer (flow_token kept out of the inbox text), address form, location | ⏳ |
| 1.16 | Flow endpoint keeps partial answers in cache, logs nothing personal | ⏳ |
| 1.16a | Meta button limits (2 links + 1 call + up to 10 quick replies; 2 call buttons refused) | ⏳ |
| 1.16b | `{name: value}` variables: preview works; a missing name is refused locally | ⏳ |
| 1.16c | a single list section still gets a title (Meta requires it) | ⏳ |
| 1.16d | product tap (`interactive.type: product`, price ÷ 1000) and the second address-reply shape | ⏳ |
| 1.16e | unsigned events reach only handlers that opt in (`accepts_unsigned`) | ⏳ |
| 1.16f | a handler that commits and then fails does not stop the others | ⏳ |
| 1.16g | Flow template button: lowercase `navigate`, first screen required, Flow must be Published | ⏳ |
| 1.16h | `test_endpoint_guards` still passes (stale `save_flow_data` entry removed) | ⏳ |

### Live with Meta

Sending interactive messages:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 1.17 | Reply buttons, list, CTA link to the test phone (window open) | L | each shows correctly; the taps come back with our ids | ⏳ |
| 1.18 | Product, product list, **product carousel**, catalogue message (catalogue linked) | L | prices and images from the catalogue. The carousel payload shape is from Meta's docs and has not been seen live yet. | ⏳ |
| 1.19 | Add to cart → send order | L | Excom Message type Order with items and total | ⏳ |
| 1.20 | Flow (draft, test number) with prefilled dropdown data | L | the dropdown shows the per-message items; the answer arrives with our flow_token. **Check:** Meta's doc now says `flow_action_payload.data` is a JSON *string*; excom sends an object, which has worked historically. If Meta rejects it, switch to `json.dumps(data)` in `whatsapp_interactive.flow`. | ⏳ |
| 1.21 | Address form to a +91 test number | L | form opens; the reply is parsed into a 📍 address | ⏳ |
| 1.22 | Location request | L | the shared pin is parsed | ⏳ |
| 1.23 | Typing indicator | L | "typing…" shows until the reply | ⏳ |

Templates:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 1.24 | Create a NAMED template (e.g. `{{date}}`, `{{summary}}`) with 3 quick replies | L | Meta approves; a send with payloads delivers; a tap returns the payload | ⏳ |
| 1.25 | Template with a Flow button (published Flow) | L | approved; a tap opens the Flow with our flow_token | ⏳ |
| 1.26 | WhatsApp Flow → "Send test" | L | a real Flow message arrives (it used to send plain text and error) | ⏳ |

| 1.27 | Frontend shows an "Order" message | L | after `cd frontend && yarn build` (the type was added to `messages.ts`), an order renders as its item list | ⏳ |
| 1.28 | Run code before `bench migrate` | M | an order is still stored (as Interactive), not lost | ⏳ |

### Known gaps (not in Phase 1)

- **Endpoint (data_exchange) Flows:** not supported. Meta encrypts those requests (RSA/AES) and excom does not decrypt them. HLD D11 uses static Flows only.
- **Media-card carousel (our own images):** not built. The product carousel covers HLD-005.
- **Agent UI** to compose interactive messages: not built. Phase 2/3 features send them from code.
- **Named URL-button parameters** (`parameter_name` on a dynamic URL button) are not added. This only matters if a URL contains a named placeholder.
- **Flow endpoint:** POSTs are refused unless signed; encrypted requests are refused with "not supported yet".

---

## Catalogue sync and broadcast budget (kept from Phase 2; the rest of Phase 2 and all of Phase 3 were removed 2026-11-04)

The rate-update, Ask Best Price/offers and cart-to-Sales-Order rows that stood here were dropped with the code (see HLD-005 §1, "Scope change").

```bash
bench --site frappe.localhost migrate   # Catalog Sync Log, catalogue fields on the Channel Account
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_catalog'}"
```

**Setup before live tests:** on the WhatsApp Channel Account set Catalogue ID, Catalogue Price List, Product page URL (`…/{item_code}`), and optionally Default brand and "Sync catalogue nightly". Items need a public image.

### Automated tests (4)

| # | Test | Result |
|---|---|---|
| C.1 | item payload: price "30.00 INR", link, brand, compliance fields; private image or no product URL refused | ⏳ |
| C.2 | `items_batch` call (form-encoded, upsert), validation errors + `ids_of_invalid_requests`, sync log "Partly Failed" | ⏳ |
| C.3 | today's price like ERPNext: latest valid_from wins, future and customer-specific prices ignored | ⏳ |
| C.4 | broadcast messaging budget from the number's tier | ⏳ |
| C.5 | (static) every excom import resolves after the removal | ✅ ran |

### Live with Meta

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| C.6 | Catalogue sync (Admin → "sync now", or nightly) | L | items appear in Commerce Manager with price, link, image; rejected ones listed in Catalog Sync Log | ⏳ |
| C.7 | Broadcast to more people than the tier allows | L | stops at the limit; continues 24 h later; nobody gets it twice | ⏳ |
| C.8 | Two subscriber lists, one broadcast Reference | L | a person on both lists gets the message once | ⏳ |
| C.9 | Paused broadcast (Meta paused the template) | L | a manager clicks Resume; it continues where it stopped | ⏳ |
