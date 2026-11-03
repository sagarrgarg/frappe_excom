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
