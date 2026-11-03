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

- **Carousel cards:** stored in a new child table, `WhatsApp Carousel Card`: body, body samples, text and URL or phone for button 1 and button 2, a sample file, and the Meta handle.
  - Frappe has no nested child tables. The button *types* are set once on the template (`card_button_1_type`, `card_button_2_type`), because Meta requires every card to have the same structure.
  - A card's sample is uploaded through the same resumable upload as the template header. It is uploaded again only when the file changes.
- **Copy-code length:**
  - **15 characters** on a limited-time offer (the LTO page's limit).
  - **20 characters** everywhere else (the coupon page's limit).
- **Catalogue id:** taken from `extras.catalog_id`, otherwise from the account's Catalogue ID.
- **Sync:** `_store_templates` round-trips every type: offer, carousel and cards, product header, copy-code example, voice-call TTL, call-permission flag, `library_template_name` and named body examples.
  - The Phase 4 fields are cleared before each fetched template is read in, so nothing stale survives a re-sync.
  - `upsert_doc_without_hooks` now also writes extra child tables.
- **Change detection:** `_has_content_changed` compares the new fields, the new button columns and the cards. Saving an unchanged carousel never posts it to Meta.

**Sending**

- **One JSON argument, `extras`:** `send_template_to_thread(..., extras)`, and `Excom Broadcast.wa_template_extras` (the same for every recipient). The keys are listed at the top of `whatsapp_template_types.py`.
- **Checked before Meta:** every missing or invalid value is refused by `frappe.throw` before anything reaches Meta: coupon code, offer end (past, or in seconds instead of ms), card count, card media, card text, URL ending, sections, product id, catalogue id, call TTL.
  - A broadcast is checked when it is saved.
  - A scheduled limited-time offer must not end before the broadcast starts.
- **Card media in broadcasts:** uploaded once per broadcast run, not once per recipient.
- **Inbox preview:** short lines under the body: offer and end time, code, carousel cards with their filled text, products, and the call permission / call button.

**Template library** (`services/whatsapp_template_library.py`)

- **Search:** `GET /message_template_library` with search, topic, use case, industry and language. Unknown filter values are refused.
- **Create:** `POST /{WABA}/message_templates` with `library_template_name`, the button inputs (sent as a JSON array) and the body inputs. The category is always Utility.
  - The template is then read back from Meta and stored through `_store_templates`, with hooks off. `after_insert` (which posts to Meta) never runs, so the template is not created twice.
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

**Not built:**
- Authentication (OTP) copy-code templates.
- Card media editing in the React admin grid.
- Per-recipient extras in broadcasts (a coupon code per person).
- A preview of the card images in the inbox.
