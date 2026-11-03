# HLD-005 — What Still Needs Testing (living document)

Every phase of HLD-005 adds rows here. A row stays until it has a result.

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

## Phase 2 — rate broadcast (commit: "WhatsApp rate updates, catalogue sync and orders (HLD-005 Phase 2)")

```bash
bench --site frappe.localhost migrate   # Commerce Settings, Rate Update (+ items), Published Rate, Catalog Sync Log, new fields
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_commerce'}"
```

### Setup before live tests

1. **WhatsApp number:** set "Catalogue ID" and "Catalogue Price List" on the Excom Channel Account.
2. **Subscriber list:** pick its "WhatsApp Number".
3. **Excom Commerce Settings:**
   - Enabled.
   - Rate Update Template: MARKETING, IMAGE header, body like "Rates updated {{date}}: {{summary}}, {{more}}. Tap a button below." (must not end with a variable), and 3 quick replies in the order View & Order, Full Catalogue, Ask Best Price.
   - Brand and Product page URL.
4. **Customers** who order on WhatsApp need their Omni Identity linked to the Customer, and a default price list.

### Automated tests (19)

| # | Test | Result |
|---|---|---|
| 2.1 | first scan of a price list is a silent baseline (Published Rates, no message) | ⏳ |
| 2.2 | a change is collected (old / new / change); changing back drops it | ⏳ |
| 2.3 | customer-specific and not-yet-valid prices are ignored | ⏳ |
| 2.4 | summary is one line, biggest change first, "and N more" count | ⏳ |
| 2.5 | auto-publish waits for 30 quiet minutes and 4 h since the last broadcast | ⏳ |
| 2.6 | publish: catalogue sync → rate card → broadcast with RU payloads and named variables → Published Rates move | ⏳ |
| 2.7 | catalogue failure stops the publish (nothing sent, rates stay) | ⏳ |
| 2.8 | catalogue item payload (price "30.00 INR", link, brand, compliance fields, no private image) | ⏳ |
| 2.9 | items_batch call (PRODUCT_ITEM, UPDATE, allow_upsert) + per-item errors + sync log | ⏳ |
| 2.10 | View & Order shape: 1 → product, 2–10 → carousel, more → product list | ⏳ |
| 2.11 | View & Order tap → carousel quoted under the tap, in the thread | ⏳ |
| 2.12 | cart order → draft Sales Order at ERPNext's price (not the cart's); unknown items listed in the reply | ⏳ |
| 2.13 | order from a contact with no Customer → no Sales Order, comment on the thread | ⏳ |
| 2.14 | messaging budget from the tier | ⏳ |
| 2.14a | a scheduled price (valid_from = today, saved days ago) is announced on the day | ⏳ |
| 2.14b | several valid rows: the latest valid_from wins (as ERPNext) | ⏳ |
| 2.14c | a price changed back while publishing is caught (catalogue and told price stay right) | ⏳ |
| 2.14d | publish runs once (Draft/Failed → Syncing is atomic) | ⏳ |
| 2.14e | every `from excom… import` in the app resolves (`test_import_targets`, no database needed) | ⏳ |

### Live with Meta

Catalogue and rates:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 2.15 | Catalogue sync (Admin → "sync now", or nightly) | L | items appear in Commerce Manager with price, link, image; rejected ones listed in Catalog Sync Log | ⏳ |
| 2.16 | Change 2 prices in ERPNext (also via Data Import) | L | one Draft Rate Update with both, within 5 minutes | ⏳ |
| 2.17 | Wait 30 min (or "Publish now") | L | catalogue shows the new prices before the message; rate card image as header; the summary line reads well | ⏳ |
| 2.18 | Change a price again within 4 h | L | it waits for the 4 h gap | ⏳ |

The message and taps:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 2.19 | Dealer taps View & Order | L | product carousel (or list) with the new prices, quoted under the tap, typing indicator first | ⏳ |
| 2.20 | Dealer taps Full Catalogue | L | catalogue message with the top changed item as thumbnail | ⏳ |
| 2.21 | Dealer taps Ask Best Price | L | the interim text reply (Phase 3 replaces it with the request form) | ⏳ |
| 2.22 | Dealer adds to cart and sends | L | draft Sales Order at the dealer's own price list; reply with the order number; address form if none on file | ⏳ |
| 2.23 | Broadcast to more people than the tier allows | L | stops at the limit as "Paused"; queued again next day, nobody gets it twice | ⏳ |
| 2.24 | Opted-out dealer on the list | L | skipped (consent gate), counted in the Skipped logs | ⏳ |

### Review fixes to check live

- **Two lists, one dealer:** a dealer on two lists gets the rate update **once**. The broadcasts of one update run one after another and skip whoever a sibling reached.
- **Waves:** with a pause, each wave is a new job. A long list never hits the job timeout.
- **Messaging limit:** a broadcast continues by itself 24 h after reaching the limit.
- **Resume:** Paused (Meta paused the template, or the account) → a manager clicks **Resume** on the broadcast.
- **Failed updates:** a Failed rate update can be published again ("Retry publish").
- **Catalogue sync:** long syncs run on the long queue (nightly and Admin "sync now"), and the sync logs survive a failed publish.
- **Order price list:** ERPNext's own choice (customer → customer group → selling settings). A failed Sales Order gives the dealer an acknowledgement and sales a comment.
- **Address form:** the reply is saved as the customer's shipping address when they have none.

### Known gaps (Phase 2)

- **Deleted prices:** a deleted Item Price is not announced and does not mark the item out of stock (only the nightly sync, through `availability` from the Item, does).
- **Media-card rate list:** the rate card is one image. A carousel of own images is not built.
- **Many WABAs, one price list:** a price list shown by several WhatsApp numbers is synced and broadcast per number; the Rate Update records them all together.
- **Item order in the cart** follows the cart. The customer's note on the order is added as a comment.

---

## Phase 3 — Ask Best Price, offers in Raven (commit: "WhatsApp rate requests and offers via Raven (HLD-005 Phase 3)")

```bash
bench --site frappe.localhost migrate   # Excom Rate Request (+ Rate Offer), negotiation settings
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_negotiation'}"
```

### Setup before live tests

1. **Request form:** a published WhatsApp Flow.
   - First screen id (default `REQUEST`) with a Dropdown bound to `${data.items}`.
   - It returns `item`, `qty` and optionally `uom`, `target_rate`, `note`.
   - Set it as "Request Form" in Excom Commerce Settings.
2. **Raven:**
   - A private channel (e.g. `sales-rate-requests`) with the sales team as members.
   - A Raven Bot.
   - Both entered in settings.
3. **Approvals:** the approver role (default Sales Manager) and the discount limit (default 5 %).
4. **Offer template** (for offers after the 24 h window): any of `{{request}}` `{{item}}` `{{qty}}` `{{rate}}` `{{list_rate}}` `{{offer}}` `{{valid_till}}`, ending with fixed text, plus 3 quick replies: Accept, Decline, Talk to sales.
5. **Raven channel id:** the channel's *name* in Raven Channel (it carries the workspace prefix), not its display name.

### Automated tests (21)

| # | Test | Result |
|---|---|---|
| 3.1 | `offer` command parsing: slabs, `valid 2d` / `6h`, `@` / `x`, `4,150` = 4150, `confirm`, nonsense refused | ✅ ran the parser directly (no DB) |
| 3.2 | flow token is signed; a tampered token is refused | ⏳ |
| 3.3 | form answer → Rate Request (customer, qty, list rate from the price list, target, note) + ack + Raven post | ⏳ |
| 3.4 | another contact's token and a replayed answer create nothing | ⏳ |
| 3.5 | deeper than 5 %: Pending Approval; a non-approver cannot approve; an approver's `approve` sends it | ⏳ |
| 3.6 | a small discount goes straight to the dealer (reply buttons `RR:…:accept:1`) | ⏳ |
| 3.7 | window closed and no offer template: the failure is told in the Raven thread | ⏳ |
| 3.8 | Accept → draft Sales Order at the offer rate and qty; a second Accept does nothing | ⏳ |
| 3.9 | an offer tap from another contact is ignored; an expired offer → Expired + reply | ⏳ |
| 3.10 | best slab for the requested qty | ⏳ |
| 3.11 | `chat` assigns the WhatsApp thread and marks the request Handled in Chat | ⏳ |
| 3.12 | scheduler: unanswered offers expire, unanswered requests get one reminder | ⏳ |
| 3.12a | no list rate, or a 0 % limit → always approval | ⏳ |
| 3.12b | more than 50 % below list waits for `confirm` | ⏳ |
| 3.12c | an old message's Accept cannot accept a newer offer; the newer one's can | ⏳ |
| 3.12d | a failed send keeps the live offer (version, rates) and clears the staged one | ⏳ |
| 3.12e | `reject` keeps the live offer; Accept of the live version still works while a new one waits | ⏳ |
| 3.12f | a user without a sales role cannot offer (nothing claimed) | ⏳ |
| 3.12g | `chat` on a conversation someone else holds is refused | ⏳ |
| 3.12h | Raven: a forged card, another channel, a bot message or the wrong thread runs no command | ⏳ |
| 3.12i | (static) every excom import resolves; no undefined names in the Phase 3 modules | ✅ ran |

### Live (WhatsApp + Raven)

Requests and offers:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 3.13 | Dealer taps Ask Best Price on a rate update | L | the form opens with their items and today's rates, changed items first | ⏳ |
| 3.14 | Dealer submits the form | L | WhatsApp ack "Request RR-… received"; Raven card in the channel with a thread, sales person mentioned | ⏳ |
| 3.15 | Sales types `offer 29 for 500` in the thread | L | "Claimed by …"; dealer gets the offer with Accept / Decline / Talk to sales | ⏳ |
| 3.16 | Sales offers 10 % off | L | waits; approver mentioned; `approve` sends it | ⏳ |
| 3.17 | Dealer taps Accept | L | draft Sales Order at the offer rate; WhatsApp reply with the number; Raven "Accepted → SO" | ⏳ |

Dealer's other answers:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 3.18 | Dealer taps Talk to sales | L | ack to the dealer; Raven mentions whoever claimed it | ⏳ |
| 3.19 | Sales types `chat` | L | the WhatsApp thread is assigned to them in the excom inbox | ⏳ |
| 3.20 | Offer made after 24 h of silence | L | sent as the offer template; the taps work the same | ⏳ |
| 3.21 | Nobody answers for 30 min | L | one reminder in the thread | ⏳ |
| 3.22 | Someone outside the thread posts a message linked to RR-… with `offer 1 for 1` | L | nothing happens | ⏳ |
| 3.23 | Dealer taps Accept on an offer that was replaced | L | "replaced by a newer one" reply; no order | ⏳ |
| 3.24 | Offer expires while the chat is open | L | dealer told it expired; Raven note | ⏳ |

### Known gaps (Phase 3)

- **Quote PDF:** no PDF is attached as the offer's document header (text only).
- **No Raven form:** there is no web-only "Send Offer" message action; thread commands work on web and mobile.
- **Payment:** WhatsApp payments for accepted offers (`order_details`) are Phase 4.
