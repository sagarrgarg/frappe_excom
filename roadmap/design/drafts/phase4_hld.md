## Phase 4 — what was built (2026-11-04): template types and Meta's template library

Not yet run against a database or Meta (MariaDB was down). Static checks, the frontend typecheck and DB-free runs of the pure functions passed.

**One module for the rules and shapes:** `services/whatsapp_template_types.py`. It holds Meta's limits, the create payload for each new type, the Meta → excom sync, and the send-time components built from one `extras` object. Every JSON shape Meta's docs do not show is marked UNVERIFIED there. The WhatsApp Templates controller and `api/chat.py` call into it, so plain templates go through exactly the code they did before.

**Template types** (`WhatsApp Templates.template_type`):

| Type | Created on Meta as | Sent with |
|---|---|---|
| Standard (+ **Copy Code** button) | `COPY_CODE` button with an example code | `copy_code` button, `coupon_code` (required) |
| Standard (+ **Voice Call** button) | `VOICE_CALL` button, `ttl_minutes` 1440–43200 | optional `voice_call` button: `ttl_minutes` 1–43200, `payload` ≤ 512 |
| Standard (+ **Ask Permission to Call**) | `call_permission_request` component | body values only |
| Limited-Time Offer | `LIMITED_TIME_OFFER` {text ≤ 16, has_expiration}; Marketing; no footer; image/video/no header; one URL button, copy code first | `limited_time_offer.expiration_time_ms` (required with a countdown, must be in the future) |
| Media Carousel | body + `CAROUSEL` of 2–10 cards: image/video header (resumable-upload handle), optional card body ≤ 160 (all or none), ≤ 2 buttons (quick reply / URL / phone) | `carousel.cards[]`: same number of cards as approved; header media id, card body values, quick-reply payloads, URL endings |
| Product Carousel | body + `CAROUSEL` of exactly 2 product cards, one SPM or URL button | 2–10 cards: `product_retailer_id` + `catalog_id` |
| Catalog | body (+ footer), `CATALOG` button; no header | optional `thumbnail_product_retailer_id` |
| Multi-Product | text header (required), body, `MPM` button | `sections` ≤ 10, products ≤ 30, title ≤ 24; thumbnail defaults to the first product |
| Single-Product | `PRODUCT` header, body ≤ 160, `SPM` button | `product` header: `product_retailer_id` + `catalog_id` |

- **Carousel cards:** stored in a new child table, `WhatsApp Carousel Card`: body, body samples, text and URL or phone for button 1 and button 2, a sample file, the upload handle and Meta's sample link.
  - Frappe has no nested child tables. The button *types* are set once on the template (`card_button_1_type`, `card_button_2_type`), because Meta requires every card to have the same structure. A product card has one button, so `card_button_2_type` is cleared and never checked on a product carousel.
  - A card's sample is uploaded through the same resumable upload as the template header, only when the template is about to be posted and the file changed (or has no handle yet).
  - Handles, links, card URLs and URL examples are Small Text: Meta's handles and sample links are ~190+ characters, more than a Data field (140) holds.
  - `header_handle` holds only a handle from our own upload. Template sync puts Meta's preview link in `header_sample_url` instead. A content edit of a carousel whose cards have only that link is refused until the samples are attached again.
  - Every card body variable needs a sample (no made-up "sample1" goes to Meta's review).
- **Copy-code length:**
  - **15 characters** on a limited-time offer (the LTO page's limit).
  - **20 characters** everywhere else (the coupon page's limit).
- **Catalogue id:** taken from `extras.catalog_id`, otherwise from the account's Catalogue ID.
- **Sync:** `_store_templates` round-trips every type: offer, carousel and cards, product header, copy-code example, voice-call TTL, call-permission flag, `library_template_name` and named body examples.
  - The Phase 4 fields, the header, the footer and the buttons are cleared before each fetched template is read in, so nothing Meta no longer has survives a re-sync.
  - A card whose content Meta did not change keeps its local sample and handle.
  - Each template is stored on its own (savepoint): one that cannot be stored is logged and skipped, the others are stored, and the error is listed in the fetch result.
  - `upsert_doc_without_hooks` now also writes extra child tables.
- **Change detection:** `_has_content_changed` compares the new fields, the new button columns and the cards. Saving an unchanged carousel never posts it to Meta.
- **When Meta is called:** after every local check. `validate` only decides and uploads samples; the create is posted in `after_insert`, a content update in `on_update` (flag `meta_update`). A local failure (a value too long, a missing field) therefore never leaves Meta changed, and a Meta refusal rolls the local save back.
- **Named or numbered:** Meta's `parameter_format` covers the body, the text header and the card bodies together, so card bodies count, and mixing `{{1}}` with `{{name}}` anywhere in one template is refused.
- **Voice Call text** is optional (Meta's default "Call Now" is filled in). Copy Code text is fixed by Meta. Every other button still needs its text.

**Sending**

- **One JSON argument, `extras`:** `send_template_to_thread(..., extras)`, and `Excom Broadcast.wa_template_extras` (the same for every recipient). The keys are listed at the top of `whatsapp_template_types.py`.
- **Checked before Meta:** every missing or invalid value is refused by `frappe.throw` before anything reaches Meta: coupon code, offer end (past, or in seconds instead of ms), card count, card media, card text, URL ending, sections, product id, catalogue id, call TTL.
  - A broadcast is checked when it is saved.
  - A scheduled limited-time offer must not end before the broadcast starts.
- **Card media in broadcasts:** uploaded once per broadcast run, not once per recipient (the template header media too).
- **Offer ends during a run:** a `TemplateTypeError` while sending (e.g. the offer ended during a wave pause or the wait for the messaging limit) pauses the broadcast with a comment, like Meta's campaign stoppers, instead of failing every remaining recipient. `wa_template_extras` can be corrected on the paused broadcast (allow on submit, checked again), then Resume.
- **Malformed extras** (a section or card that is not an object, `voice_call` as text, `ttl_minutes: "abc"`, a bad date) give a clear `TemplateTypeError`, never a server error.
- **Paths that cannot pass extras** (notifications, `send_transactional`, the resend of a failed message) refuse a template that needs them with a clear message (`refuse_without_extras`); a notification cannot be saved with one. `send_transactional` skips it with `template_needs_extras`.
- **Media permission:** `send_template_to_thread` checks that the agent can read every private header and card file (`frappe.has_permission("File", "read")`). A broadcast checks its owner when it is saved, because its job runs later as the owner or Administrator. `whatsapp_service._local_file_bytes` refuses `..` and any path that resolves outside the site folder.
- **Inbox preview:** short lines under the body: offer and end time, code, carousel cards with their filled text, products, and the call permission / call button.

**Template library** (`services/whatsapp_template_library.py`)

- **Search:** `GET /message_template_library` with search, topic, use case, industry and language. Unknown filter values are refused.
- **Search results:** a form button shows as `FORMS`; it is listed as `FLOW`. Only URL and phone buttons need an input; quick replies, forms and the rest are listed without one.
- **Create:** `POST /{WABA}/message_templates` with `library_template_name`, the button inputs (sent as a JSON array, URL and PHONE_NUMBER only) and the body inputs. The category is always Utility.
  - The template is then read back from Meta and stored through `_store_templates`, with hooks off. `after_insert` (which posts to Meta) never runs, so the template is not created twice.
  - Once Meta has created it, nothing fails the call: a failed read-back or store is logged, and the result carries a `note` (shown in the toast) that the next sync fills it in.
  - A name already used here is refused, because sync matches templates by name alone.
- **Endpoints:** `api.chat.search_template_library` and `api.chat.create_template_from_library`. Both are admin only (`_check_admin_access`).

**Frontend**

- **Shared inputs:** `TemplateExtrasForm.tsx` holds the inputs for both screens: coupon code, offer end (date-time picker), cards (media upload or product id, card text, payloads, URL endings), thumbnail, MPM sections, product id, and the call-button minutes and reference.
- **Where it is used:**
  - **Inbox:** the template picker.
  - **Broadcasts:** the broadcast wizard, which shows the values on its review screen.
- **Library browser:** Admin → WhatsApp templates → **Meta library** (`admin/TemplateLibrary.tsx`).
  - **Find:** search and filters, then a preview with Meta's sample values.
  - **Create:** fill in the name and the URL / phone buttons, then create.
- **Creating the new types:** done in the admin template form or in Desk. Card media is attached in Desk, because the admin grid does not show Attach columns.

**For Phase 6 (calling):**
- **Voice Call button:** `button_type = "Voice Call"`, with `ttl_minutes` on the row.
- **Send-time override:** `extras.voice_call = {ttl_minutes, payload}`. The payload comes back as `cta_payload` on the call webhooks.
- **Call permission:** a template with `call_permission_request = 1` sends Meta's call-permission request. Its reply arrives as `interactive.call_permission_reply`. Phase 4 does not parse that reply; Phase 6 has to.

**Upgrading a site:** patch `whatsapp_template_types_backfill` maps the raw button types older syncs stored (`COPY_CODE`, `CATALOG`, `VOICE_CALL`…) to excom's names, fills the Copy Code / Voice Call default text and sets the template type it can see (Catalog, MPM, SPM buttons, PRODUCT header). Offers and carousels were never stored by the old sync, so after `bench migrate` an admin runs **Sync templates** once (the patch prints which templates need it). The patch does not call Meta.

**UNVERIFIED shapes** (each one is in `whatsapp_template_types.py` or `whatsapp_template_library.py`):
1. **Card body at create time:** `{"type":"BODY","text","example":{"body_text":[[…]]}}`.
2. **Voice-call send:** Meta's example has no `index`; excom sends one (`VOICE_CALL_SEND_INDEX`).
3. **Call permission component:** the type is sent lowercase, `call_permission_request`; on sync either case is read.
4. **URL buttons on product cards:** the send parameters are copied from media cards.
5. **Catalogue send without a thumbnail:** the button component is left out.
6. **Library button inputs:** sent as a JSON array, not the string the guide shows.
7. **Library categories:** only UTILITY is allowed (AUTHENTICATION is not known to work).
8. **Library search paging:** the response is assumed to use standard Graph `data` / `paging`.
9. **GET response for the new types:** assumed to mirror the create shapes in capitals, with `COPY_CODE.example` as a string or a list.
10. **Casing:** uppercase create types (`COPY_CODE`, `VOICE_CALL`, `CAROUSEL`, `LIMITED_TIME_OFFER`, `PRODUCT`).
11. **Upload handle lifetime:** a card keeps its handle from the last upload and reuses it on later content updates; how long Meta accepts a handle is not documented.
12. **Library templates with only non-input buttons** (quick reply, form): created without `library_template_button_inputs`.

**Not built:**
- Authentication (OTP) copy-code templates.
- Card media editing in the React admin grid.
- Per-recipient extras in broadcasts (a coupon code per person).
- A preview of the card images in the inbox.
