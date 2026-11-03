# HLD-005 — What Still Needs Testing (living document)

Every phase of HLD-005 (the WhatsApp platform) adds rows here. A row stays until it has a result.

**Results:** ⏳ not run yet · ✅ pass · ❌ fail (fixed in commit …) · ⏭ skipped (reason)

**How it was tested:**
- **U** — automated test (`excom.excom.tests.test_whatsapp_platform`)
- **M** — migrate / patch on a site
- **L** — live with a real WhatsApp Business Account (test number)
- **D** — diagnostics screen (Admin → WhatsApp → Diagnose)

**Regression found and fixed:** commit f4d2759 (Phase 0) accidentally deleted the media functions from `whatsapp_service.py` (`media_reference`, `upload_media`, `send_media_message`, `send_sticker_message`). Image, document and sticker sends, template media headers and the delivery-retry watchdog would all have failed. They were restored in the Phase 2 commit. Test 0.26 (full suite, including `test_whatsapp_media`) covers this.

**Blocked right now:** MariaDB is stopped on the dev machine, so nothing below has run (except the rows marked ✅: static checks and the frontend unit tests). Phase 0 was committed untested at the owner's request (2026-11-03).

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

---

## Phase 4 — template types and template library (commit: "WhatsApp template types and template library (HLD-005 Phase 4)")

```bash
bench --site frappe.localhost migrate   # WhatsApp Carousel Card; template type, offer, card and library fields; Copy Code / Catalog / MPM / SPM / Voice Call buttons; Excom Broadcast.wa_template_extras
                                         # review fixes: card handle / URL fields → Small Text, header_sample_url, button URL fields → Small Text,
                                         # button_label optional for Copy Code / Voice Call, wa_template_extras allow on submit, patch whatsapp_template_types_backfill
# then, once: WhatsApp Templates → Sync templates (reads in offers and carousels synced before Phase 4)
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_template_types'}"
```

### Migrate

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.1 | `bench migrate` | M | new doctype WhatsApp Carousel Card; new fields on WhatsApp Templates, WhatsApp Button, Excom Broadcast; no errors | ⏳ |
| 4.2 | Existing templates after migrate | M | Template Type shows Standard (or is blank and saves as Standard); plain templates send exactly as before | ⏳ |
| 4.3 | `reclaim_shared_doctypes` after migrate (frappe_whatsapp installed too) | M | no "shared doctype fields missing" Error Log for template_type / cards / example_code | ⏳ |
| 4.3a | Patch `whatsapp_template_types_backfill` on a site with templates synced before Phase 4 | M | raw `COPY_CODE` / `CATALOG` / `VOICE_CALL` buttons now show Copy Code / Catalog / Voice Call; catalogue / MPM / SPM templates have their type; the migrate log lists templates to re-sync; those save in Desk after Sync templates | ⏳ |
| 4.3b | Card columns after migrate | M | `header_handle`, `handle_sample`, `header_sample_url`, `button_N_value`, `button_N_example` are `text`; WhatsApp Button `website_url` / `example_url` are `text` | ⏳ |

### Automated tests (18 + 19 from the review)

| # | Test | Result |
|---|---|---|
| 4.4 | copy-code create payload; example ≤ 20 characters; one copy-code button | ⏳ |
| 4.5 | LTO create payload (`LIMITED_TIME_OFFER`, no footer); offer text ≤ 16, Marketing only, image/video header, code ≤ 15 | ⏳ |
| 4.6 | media carousel create payload (card header handle, card body example, card buttons); 2–10 cards, body on all or none, no top-level header, card body ≤ 160 | ⏳ |
| 4.7 | card samples go through the resumable upload once; unchanged samples are not uploaded again | ⏳ |
| 4.8 | product carousel (exactly 2 cards at create), catalogue, MPM (text header required), SPM (PRODUCT header), voice call (TTL 1440–43200), call-permission component | ⏳ |
| 4.9 | Catalog / MPM / SPM buttons refused on other types; no quick replies on catalogue templates | ⏳ |
| 4.10 | plain templates: type Standard, payload unchanged | ⏳ |
| 4.11 | sync round-trip for every type; re-sync as plain clears the offer and the cards | ⏳ |
| 4.12 | an unchanged synced carousel is not posted to Meta; a changed card is | ⏳ |
| 4.13 | send components per type: LTO expiry + copy code + URL, media cards (media uploaded once per run), product cards (2 created → 4 sent, catalogue from the account), catalogue thumbnail, MPM sections, SPM product header with named body, voice-call override | ⏳ |
| 4.14 | refused before Meta: no / long coupon, no / past offer end, wrong card count, missing card text or media, MPM over 10 sections / 30 products, missing product, missing catalogue | ⏳ |
| 4.15 | send to thread with `extras`: copy-code component sent, code in the inbox preview; bad `extras` refused | ⏳ |
| 4.16 | previews: carousel cards with filled text, MPM product count, call permission | ⏳ |
| 4.17 | broadcast validation: missing coupon refused; a scheduled offer that ends before the start is refused | ⏳ |
| 4.18 | library search: `/v26.0/message_template_library`, only the filters given, paging cursor, button inputs listed; unknown topic refused | ⏳ |
| 4.19 | library create: one POST with `library_template_name` and the button inputs as a JSON array; stored APPROVED with hooks off (no second POST), linked to the account; a name already used is refused | ⏳ |
| 4.20 | library refusals: non-Utility category, bad name, bad URL, URL variable without an example, bad phone, unknown button type; endpoints refuse non-admins | ⏳ |
| 4.21 | `test_whatsapp_messaging`, `test_template_sync`, `test_endpoint_guards`, `test_import_targets` still pass | ⏳ |
| 4.22 | (static) py_compile, undefined-name check, import targets, frontend `tsc --noEmit` | ✅ ran |
| 4.22a | `_check()` now runs the controller's `validate` and Frappe's own mandatory / select / length checks; handles and links in the fixtures are real length (≥ 190) | ⏳ |
| 4.22b | real-length handle and 300-character card URL through `insert()`: stored, posted once after the local checks (C1) | ⏳ |
| 4.22c | saving an existing Standard template unchanged posts nothing; a content field over 140 is refused locally with no Meta post; a real content change posts one update (C1) | ⏳ |
| 4.22d | sync round-trip: Meta's sample link lands in `header_sample_url`, never in `header_handle` (C2) | ⏳ |
| 4.22e | one unreadable template in a fetch is skipped and reported; the next one is stored (C2) | ⏳ |
| 4.22f | synced carousel: unchanged save posts nothing; a content edit with only Meta's link is refused ("attach the sample image again"); with samples attached it posts the new handles; re-sync keeps unchanged cards' samples and drops a changed card's (M1) | ⏳ |
| 4.22g | named card bodies: `body_text_named_params`, `parameter_format: NAMED`, `parameter_name` at send; mixed `{{1}}` / `{{name}}` refused; card body without samples refused (M2) | ⏳ |
| 4.22h | re-sync as plain clears the header, footer and buttons (no stale Copy Code row) (L2) | ⏳ |
| 4.22i | LTO with only a URL button (index 0); Copy Code as the third button (index 2) | ⏳ |
| 4.22j | product carousel ignores the hidden card button 2; Voice Call text defaults to "Call Now"; other buttons still need text (L4) | ⏳ |
| 4.22k | malformed extras (sections / cards not objects, `voice_call` text, `ttl_minutes: "abc"`, bad date, extras not an object) → `TemplateTypeError` (LOW) | ⏳ |
| 4.22l | preview fills `{name: value}` card values in placeholder order (L1) | ⏳ |
| 4.22m | `get_whatsapp_templates` returns the cards | ⏳ |
| 4.22n | broadcast run to 3 people: each card image uploaded once (`media_cache`) | ⏳ |
| 4.22o | offer ends after the first send: broadcast Paused with a comment, 1 sent, 0 failed, no Failed logs (M4) | ⏳ |
| 4.22p | private file the agent cannot read refused (header and card media, inbox and broadcast owner); `..` paths refused by the check and by `_local_file_bytes`; inbox send refused before Meta (M5) | ⏳ |
| 4.22q | notification / transactional paths refuse templates that need extras; the voice-call template passes (L3) | ⏳ |
| 4.22r | library: FORMS button listed as FLOW with no input, skipped in the create payload (M3); a failed read-back returns success with a `note` and no error message (L5); after_insert never runs (replaces the old empty `meta_posts` check) | ⏳ |
| 4.22s | patch: raw button types mapped, Catalog type set, default texts filled, idempotent; skipped without the tables / columns (`test_patches`) (H1) | ⏳ |
| 4.22t | library endpoints refuse a signed-in Excom Agent (not just Guest) | ⏳ |

### Live with Meta (test WABA + test number)

Creating templates:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.23 | Create a coupon template (quick reply + Copy Code) | L | Meta accepts; after approval Sync keeps the example code | ⏳ |
| 4.24 | Create an LTO with an image header, countdown, Copy Code + URL | L | accepted; the offer shows a countdown on the phone | ⏳ |
| 4.25 | Create a media carousel, 3 image cards with card text and quick reply + URL buttons (media attached in Desk) | L | accepted. **Check:** the card BODY shape (UNVERIFIED) | ⏳ |
| 4.26 | Create a product carousel (2 SPM cards), a catalogue template, an MPM template and an SPM template (catalogue linked) | L | all accepted | ⏳ |
| 4.27 | Create a template with a Voice Call button and one with "Ask Permission to Call" | L | accepted. **Check:** the component casing (sent lowercase) and which categories Meta allows | ⏳ |
| 4.28 | Sync from Meta after 4.23–4.27 | L | every type comes back with its type, cards and buttons. **Check:** the GET shapes match | ⏳ |

Sending:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.29 | Inbox: send the coupon, the LTO (end in 2 h) and the media carousel to the test phone | L | code copies; countdown shows; cards scroll; a quick-reply tap returns our payload | ⏳ |
| 4.30 | Inbox: product carousel with 4 products, MPM with 2 sections, SPM, catalogue with a thumbnail | L | products and prices from the catalogue. **Check:** the catalogue button with no thumbnail (component left out) | ⏳ |
| 4.31 | Voice-call template with a TTL and payload override | L | the call button works. **Check:** whether Meta accepts the `index` on the voice_call component; `cta_payload` arrives on the call webhook (Phase 6) | ⏳ |
| 4.32 | Broadcast an LTO + coupon to a list of 3 | L | everyone gets the same code and end; card media uploaded once (one /media call per card) | ⏳ |

Template library:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.33 | Admin → WhatsApp templates → Meta library: search "delivery", filter by topic | L | results with previews | ⏳ |
| 4.34 | Create `delivery_update_1` with a URL button ending in `{{1}}` | L | status APPROVED at once; it appears in the template list and the inbox picker. **Check:** the button inputs are accepted as a JSON array | ⏳ |
| 4.35 | Create a library form template (`delivery_failed_2_form`, FORMS / Flow button) | L | created without button inputs. **Check:** Meta accepts the payload without `library_template_button_inputs` | ⏳ |
| 4.36 | Library search with a bad token | L | the browser shows Meta's error, not "No library templates" | ⏳ |

Review fixes, live:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.37 | Edit the body of a carousel created in 4.25, after a Sync | L | refused until each card's sample is attached again; then accepted. **Check:** Meta accepts the reused handle of a card whose content did not change | ⏳ |
| 4.38 | Sync after 4.25 | L | card samples stay; `header_sample_url` holds Meta's link; nothing is "Value too big" | ⏳ |
| 4.39 | Broadcast an LTO whose end passes during a wave pause | L | Paused with a comment; set a later `offer_expiration_ms` on the paused broadcast; Resume sends to the rest only | ⏳ |
| 4.40 | Agent sends a template with another record's private file as header | L | refused: "You do not have access to the attachment" | ⏳ |
| 4.41 | Media carousel whose cards did not sync, in the inbox picker | L | the form says the cards did not sync instead of two empty cards | ⏳ |

### Review fixes (code review of 81340d1)

- **C1:** card `header_handle`, `handle_sample`, `button_N_value`, `button_N_example` and new `header_sample_url` are Small Text; WhatsApp Button `website_url` / `example_url` are Small Text; `library_template_name` is Data(512). Meta is posted only after Frappe's own checks (`after_insert` / `on_update` with the `meta_update` flag), so a local failure never leaves Meta changed.
- **C2:** sync stores Meta's sample link in `header_sample_url`, never as the handle; `_store_templates` stores each template under a savepoint, logs and skips one that fails, and lists it in the fetch result. The library create path uses the same.
- **H1:** patch `whatsapp_template_types_backfill` (idempotent, skips sites without the columns); admins run Sync templates once after migrate (not triggered automatically: it calls Meta for every WABA).
- **M1:** an update uses only an uploaded handle; a card with only Meta's link needs its sample attached again (clear message); sync keeps local samples of cards Meta did not change.
- **M2:** card bodies count for named / positional; mixing is refused; card samples are required.
- **M3:** library `FORMS` → `FLOW`; only URL / PHONE_NUMBER take inputs; other known types are left out of the create payload.
- **M4:** `TemplateTypeError` pauses the broadcast with a comment; `wa_template_extras` is editable while paused.
- **M5:** `_local_file_bytes` refuses `..` and paths outside the site; the inbox send checks the agent's File read permission on header and card media; a broadcast checks its owner when saved.
- **LOW:** type checks on extras; preview card values in placeholder order (L1); stale header / footer / buttons cleared on re-sync (L2); notification, transactional and resend paths refuse templates that need extras (L3); hidden product-card button 2 ignored, Voice Call text optional (L4); library read-back failure is a note (L5); `skip_meta_create` removed (L6).
- **UX:** media carousel with no synced cards says so; a library search error is shown.
- **Not changed:** a synced template still has no `language` (Link), so a Desk save needs it filled (existing behaviour; tests set `ignore_mandatory`).

### Known gaps (not in Phase 4)

- **Card media in the React admin:** the admin grid hides Attach columns, so a card's sample is attached in Desk.
- **Per-recipient extras:** a broadcast sends the same extras to everyone, so it cannot give each person their own coupon code.
- **Authentication (OTP) templates:** copy-code OTP templates are not built.
- **Card images in the inbox:** the stored message shows the cards as text lines, not images.
- **Library edits:** a library template edited in WhatsApp Manager goes to review. Sync picks up the new status, but excom does not edit library templates itself.
- **Sync matches templates by name only:** this is existing behaviour. Library creation refuses a name that is already used, so a second language cannot overwrite the first.

---

## Phase 5 — WhatsApp number and account (commit: "WhatsApp number and account settings (HLD-005 Phase 5)")

Draft rows for TEST_005 (to merge after review).

```bash
bench --site frappe.localhost migrate   # Excom WhatsApp QR Code; number-settings fields (incl. wa_command_names) on the Channel Account; wa_blocked* and wa_blocked_numbers on Omni Identity
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_account'}"
# regressions touched: webhook parsing, ingest, endpoint guards
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_messaging,excom.excom.tests.test_whatsapp_platform,excom.excom.tests.test_endpoint_guards,excom.excom.tests.test_import_targets,excom.excom.tests.test_inbound_as_guest'}"
```

**Setup before live tests:** on the WhatsApp Channel Account, set the App ID (needed for the profile photo) and the Business Account ID. The site must be reachable over **https** for the webhook override.

### Automated tests (42)

| # | Test | Result |
|---|---|---|
| 5.1 | profile limits (about 1–139, address 256, description 512, email ≤ 128 (129 refused), ≤ 2 websites with http(s), vertical enum) refused before Meta | ⏳ |
| 5.2 | profile read (fields list) and update (only the changed fields + `messaging_product`) | ⏳ |
| 5.3 | profile photo: `/{app}/uploads` with file_name/length/type → `/upload:<id>` with `OAuth` + `file_offset: 0` + raw bytes → `profile_picture_handle` | ⏳ |
| 5.4 | photo refused without an App ID or without an uploaded File | ⏳ |
| 5.5 | block entry: `+<digits>` for numbers, `user_id` for BSUIDs, parent ids refused | ⏳ |
| 5.6 | block refused when the last message is older than 24 h or there is none (no call); allowed within 24 h; identity flagged | ⏳ |
| 5.7 | block uses the BSUID of this WhatsApp account | ⏳ |
| 5.8 | partial failure (`failed_users` + 139100): "24 hours" message, identity not flagged, Error Log per person | ⏳ |
| 5.9 | unblock: DELETE with the same body, no 24 h rule, flag cleared | ⏳ |
| 5.10 | block from a conversation (thread API) + block-list paging and identity matching | ⏳ |
| 5.11 | QR create / update / delete push to `message_qrdls` with the exact bodies; image copied to a File | ⏳ |
| 5.12 | QR limits: message > 140, format other than PNG/SVG, the 2,000 cap (all refused before Meta) | ⏳ |
| 5.13 | QR sync from Meta: two pages, new codes added, changed message updated, deleted code dropped, nothing pushed back | ⏳ |
| 5.14 | ice breaker / command limits (4 × 80, 30 commands, name 32 without spaces, description 1–256, no emoji incl. ⭐ ☎️ ✨, no duplicates, "/" stripped); accents, Devanagari and arrows allowed | ⏳ |
| 5.15 | conversational body shape; `enable_welcome_message` only when set; a code 100 refusal retried without it (with a warning); no retry without the setting or on another code (190); command names kept on the account | ⏳ |
| 5.16 | conversational read-back (`?fields=conversational_automation`); the command names read are kept | ⏳ |
| 5.17 | `parse_command` ("/name args", case, what is not a command) | ⏳ |
| 5.18 | inbound `/tickets …` (a configured command) stored as Text, dispatched as kind `command`; `request_welcome` stored, dispatched as `welcome`, event logged, 24 h window not opened | ⏳ |
| 5.19 | PIN must be 6 digits; set → `POST /{phone} {"pin"}`; stored encrypted, not in the column | ⏳ |
| 5.20 | register needs a PIN; body with pin + region (upper-cased; unknown region refused; stored after success); deregister without a body; attempts logged | ⏳ |
| 5.21 | 10 register / deregister calls per 72 h: older attempts expire; 133016 recorded and explained; the 11th refused with no call | ⏳ |
| 5.22 | the PIN never appears in Error Log after a refused register | ⏳ |
| 5.23 | request_code (query: code_method, language; bad values refused; 136024 explained) and verify_code (code in the body, not the URL) | ⏳ |
| 5.24 | number webhook: default URL + account verify token, committed before the Meta call (one commit, token readable when Meta is called; reused without a new commit); `webhook.get` accepts that token with `hub.mode=subscribe` and refuses a wrong token, another or no mode, and all-asterisk tokens; clear sends `""`; http, > 200 chars, foreign URL without a token are refused | ⏳ |
| 5.25 | WABA webhook: set with URL + token; clear = POST with no body | ⏳ |
| 5.26 | webhook read-back (number, app, subscribed apps) | ⏳ |
| 5.27 | commerce settings as query parameters, no body; read parsed | ⏳ |
| 5.28 | link catalogue (form `catalog_id`, digits only; fills `wa_catalog_id`), list linked catalogues | ⏳ |
| 5.29 | Guest cannot call the settings endpoints (no Meta call) | ⏳ |
| 5.47 | profile photo: a real binary PNG (not valid UTF-8) is sent as the same `bytes` | ⏳ |
| 5.48 | profile photo refused for a File the user may not read and that is not attached to this number (no call); allowed once attached to the number | ⏳ |
| 5.49 | phone number id, WABA id and app id must be digits only (no call) | ⏳ |
| 5.50 | a block is per number: blocked on another number does not show on this conversation; unblocking here keeps the other block; the summary fields follow the latest block | ⏳ |
| 5.51 | Excom Agent: block status / block / unblock work on their own conversation; all three are refused (PermissionError, no call) on a conversation they cannot see; the admin `block` endpoint is refused | ⏳ |
| 5.52 | QR: a code made on Meta is deleted there when the insert / request rolls back | ⏳ |
| 5.53 | QR `refresh_qr_image`: fresh `qr_image_url.format(PNG)` read; the old File is deleted; one File attached; `qr_image` points at it | ⏳ |
| 5.54 | QR delete when Meta says the code does not exist (100): the local record goes; any other refusal (190) keeps it | ⏳ |
| 5.55 | inbound plain `hello` → kind `text`, payload None; `/help me` (not a configured command) → kind `text` with `{"command": "help", "args": "me"}` as a hint; no commands known → text | ⏳ |
| 5.56 | `request_welcome` on a thread with an open window leaves `last_inbound_at` as it was | ⏳ |
| 5.57 | register region: not stored when Meta refuses; stored on success; left out → stored one sent; `""` → none sent and the stored one cleared | ⏳ |
| 5.58 | register / deregister timeout or connection error counts as an attempt ("no answer") in the 72 h budget | ⏳ |
| 5.59 | Registration screen: Meta read fails (190) → `meta_error` set, budget and `has_pin` still returned | ⏳ |
| 5.30 | (static) py_compile, undefined names, import targets, endpoint guards on the new API, DB-free smoke run of the limits and request shapes, frontend `tsc --noEmit`; after the review fixes also DB-free runs of the emoji check, id check, command dispatch, per-number block map, register region / timeout budget, the welcome retry rule and `webhook.get` (mode, asterisks, same-length decrypt only) | ✅ ran |

### Live with Meta (test WABA + test number)

Profile and blocking:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.31 | Profile tab: edit About, add 2 websites, set category | L | WhatsApp shows the new profile. Check whether clearing a field with "" works. | ⏳ |
| 5.32 | Upload a profile photo (PNG and JPEG) | L | the photo changes in WhatsApp. Check that the Bearer header works for `/uploads`; if not, send `access_token` as Meta's page shows. | ⏳ |
| 5.33 | Block from the inbox ⋯ menu after the test phone writes | L | the phone can no longer message; the identity shows "Blocked on WhatsApp" | ⏳ |
| 5.34 | Block someone who last wrote > 24 h ago | L | refused locally with the date they last wrote | ⏳ |
| 5.35 | Blocked users tab lists them; Unblock works | L | the phone can message again | ⏳ |

QR codes and conversational components:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.36 | Create a QR code (PNG and SVG), scan it | L | the chat opens with the prefilled message; the image is stored on the record | ⏳ |
| 5.37 | Edit the message; delete a code | L | the new text appears after a scan; a deleted code shows "This QR code has expired" | ⏳ |
| 5.38 | Code made in WhatsApp Manager → Sync from Meta | L | it appears; one deleted there disappears | ⏳ |
| 5.39 | Ice breakers and commands → open a fresh chat on the test phone | L | ice breakers show; typing "/" lists the commands; a saved command reaches handlers as kind `command`, any other "/word" as `text` | ⏳ |
| 5.40 | Welcome message On, then open a first chat | L | **UNVERIFIED:** does Meta accept `enable_welcome_message`? Does `request_welcome` arrive? (Community reports say it does not.) If it arrives, it is shown in the inbox, with no 24 h window. | ⏳ |

Registration and webhook:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.41 | Set PIN → Register (test number) | L | success; one attempt in the log. Check the field names in "Number status". | ⏳ |
| 5.42 | Request code (SMS) / verify | L | on an unverified number the code arrives and verifies; on a verified one, the 136024 message. Check that `verify_code` accepts the code in the form body. | ⏳ |
| 5.43 | Number webhook override to this site (on an account with no verify token yet), then Clear | L | Meta accepts it (its verify GET, on another worker, passes with the freshly committed account token); the read-back shows it; after Clear, events go to the app URL | ⏳ |
| 5.44 | WABA override, then Clear | L | same, at WABA level; the app stays subscribed after Clear | ⏳ |

Commerce:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.45 | Commerce: hide / show the catalogue, cart off / on | L | the shop icon and cart button follow | ⏳ |
| 5.46 | Link a catalogue ID | L | listed under Linked catalogue; catalogue sync uses it | ⏳ |
| 5.60 | With a bad access token, open each tab | L | each tab shows the read error; Save / Save all / commerce switches / Create / number Clear stay off with a reason; Registration still shows the register count | ⏳ |
| 5.61 | Blocked users: Load more, then Refresh | L | the list starts again from the first page | ⏳ |

### Known gaps (not in Phase 5)

- **Unlinking a catalogue:** no API. Do it in WhatsApp Manager or Commerce Manager (the UI says so).
- **Blocked people are not checked before sending.** A send to a blocked identity is refused by Meta, not by excom's consent gate. `wa_blocked*` / `wa_blocked_numbers` are not carried over on identity merges.
- **Commands edited in WhatsApp Manager** count for inbound dispatch only after Admin → WhatsApp number has read them (or saved).
- **Block list sync:** the block list is read live from Meta. Blocks made in WhatsApp Manager are not copied onto identities.
- **Resumable upload resume:** an interrupted photo upload is restarted, not resumed (`GET /upload:<id>` is not used).
- **QR images of synced codes** are fetched only when the admin presses "Image".
- **Removed from the docs:** the welcome message has no Meta documentation any more; we rely on it only as best effort.

### Review fixes (code review of 32271a2)

1. **Verify token committed before Meta's check.** `webhook_verify_token()` commits right after making a new token, so Meta's verification GET (another worker) can see it (5.24).
2. **Tests that could not pass:** the 128-character email is now 129 (5.1). The photo upload read the File with `get_content()`, which decodes a PNG as windows-1250 text; `_local_file_bytes` (shared with media sends) now reads raw bytes with `get_content(encodings=())`, and the upload refuses anything but bytes (5.3, 5.47).
3. **Admin screen and failed reads.** Every read shows its error with Retry. Save to WhatsApp, Save all (ice breakers and commands), the commerce switches, QR Create and the number override's Clear stay off until the data has loaded, with a line saying why. `get_registration` returns excom's own budget, PIN flag and region even when Meta cannot be read (`meta_error`) (5.59, 5.60).
4. **Commands.** An inbound `/name` text is kind `command` only when `name` is one of the number's commands, kept on the Channel Account (`wa_command_names`) whenever they are saved or read in Admin → WhatsApp number. Any other `/word` text stays kind `text` with the parse in `payload` as a hint; a plain text has payload None (5.18, 5.55).
5. **Blocks are per number.** `wa_blocked_numbers` on Omni Identity maps Channel Account → blocked on; `wa_blocked*` summarise it (any block, the latest). `thread_block_status` reports the conversation's own number; unblocking clears only that number (5.50, 5.51).
6. **Profile photo** must be a File the user may read, or one attached to this Channel Account (5.48).
7. **Meta ids** (phone number id, WABA id, app id, catalogue id) must be digits only before they go into a URL (5.49).
8. **Webhook GET** requires `hub.mode=subscribe` and decrypts only accounts whose masked token has the same length as the one offered (`compare_digest` kept).
9. **Register region** is stored only after Meta accepts; "None" in the screen sends `""` and clears it; leaving it out uses the stored one (5.57).
10. **A register / deregister with no answer** (timeout, connection error: `MetaUnreachable`) counts as an attempt (5.58).
11. **Welcome retry** only on Meta code 100 (5.15).
12. **QR records:** a code made on Meta is deleted there again if the transaction rolls back (`frappe.db.after_rollback`) (5.52); `refresh_qr_image` deletes the old File before attaching the new one (5.53); delete when the code is already gone on Meta (5.54); the screen links / shows only `/files/` and `/private/files/` URLs.
13. **Emoji check** uses Unicode category So plus U+2600–27BF, U+2B00–2BFF, U+1F000–1FAFF, U+20E3, U+FE0F and tags (5.14).
14. **Confirmations** for Register, Set PIN, the admin Block and an override pointing at another URL; Blocked users Refresh after Load more starts from the first page (5.61).

---

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
