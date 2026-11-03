## Phase 4 — template types and template library (commit: "WhatsApp template types and template library (HLD-005 Phase 4)")

```bash
bench --site frappe.localhost migrate   # WhatsApp Carousel Card; template type, offer, card and library fields; Copy Code / Catalog / MPM / SPM / Voice Call buttons; Excom Broadcast.wa_template_extras
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_template_types'}"
```

### Migrate

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 4.1 | `bench migrate` | M | new doctype WhatsApp Carousel Card; new fields on WhatsApp Templates, WhatsApp Button, Excom Broadcast; no errors | ⏳ |
| 4.2 | Existing templates after migrate | M | Template Type shows Standard (or is blank and saves as Standard); plain templates send exactly as before | ⏳ |
| 4.3 | `reclaim_shared_doctypes` after migrate (frappe_whatsapp installed too) | M | no "shared doctype fields missing" Error Log for template_type / cards / example_code | ⏳ |

### Automated tests (18)

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

### Known gaps (not in Phase 4)

- **Card media in the React admin:** the admin grid hides Attach columns, so a card's sample is attached in Desk.
- **Per-recipient extras:** a broadcast sends the same extras to everyone, so it cannot give each person their own coupon code.
- **Authentication (OTP) templates:** copy-code OTP templates are not built.
- **Card images in the inbox:** the stored message shows the cards as text lines, not images.
- **Library edits:** a library template edited in WhatsApp Manager goes to review. Sync picks up the new status, but excom does not edit library templates itself.
- **Sync matches templates by name only:** this is existing behaviour. Library creation refuses a name that is already used, so a second language cannot overwrite the first.
