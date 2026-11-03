# High Level Design — WhatsApp Business Platform in excom

**Document:** HLD-005
**Version:** 0.5 (2026-11-04: Phases 4–6 built and reviewed)
**Date:** 2026-11-04
**Status:** Phases 0, 1, 4, 5, 6 and catalogue sync built and code-reviewed; none run against a database or Meta yet
**Owner:** Sagar Ratan Garg

---

## 1. Goal

excom covers the **whole WhatsApp Business Platform** (Cloud API) as general building blocks: every
message and template type, the number and account settings, calling, catalogue sync, broadcasts,
consent and analytics. It holds **no business-specific workflow**. A business process (price
broadcasts, offers, approvals, orders) is built on top in its own app, through excom's sending
functions and the inbound event hook `excom_inbound_handlers`.

### Scope change (2026-11-04)

The rate-update, Ask-Best-Price/offer and cart-to-Sales-Order features (first built as Phases 2 and
3, commits bc8b762 and a8fe0d8) were **removed** from excom: they were one business's workflow, not
WhatsApp features. They were never migrated on any site. What stayed:

- **Catalogue sync** (ERPNext Items + today's price → Meta catalogue), now `services/whatsapp_catalog.py`; its settings (product URL, brand, origin country, nightly sync) are on the WhatsApp Channel Account.
- **Broadcast features:** button payloads, a Reference document (broadcasts of one document reach a person once), waves, the messaging-limit budget, pause/resume.
- **Inbound parsing** of orders, product taps, address and location replies, Flow answers — passed to `excom_inbound_handlers` as events.

Online payments (WhatsApp Pay / `order_details`) are **out of scope**: the business is paid by bank transfer.

## 2. Starting point

### WhatsApp platform facts (checked 2026-11-03)

| # | Fact | Consequence |
|---|---|---|
| P1 | Graph **v21.0 ends 2027-01-21**; current is **v26.0**. From v24, status webhooks drop `conversation` in favour of `pricing`, and messaging limits are read from `whatsapp_business_manager_messaging_limit`. | Upgrade excom before anything else. |
| P2 | **Marketing on Cloud API `/messages` is being retired.** Accounts created on/after 2027-04-13 can't send marketing there; it errors for everyone from 2027-10-13. The **Marketing Messages API** (`/marketing_messages`, GA 2025-11-19) adds delivery optimisation, TTL, GIF headers and click webhooks. | Send rate broadcasts through `/marketing_messages`; utility, service and auth stay on `/messages`. |
| P3 | Per-message pricing. India (before GST): **marketing ≈ ₹0.863**, utility ≈ ₹0.115, auth ≈ ₹0.115. From **2026-10-01**: service messages free for the first **1,000 per number per month**, then the utility rate; utility templates inside the 24h window are charged. Click-to-WhatsApp ad entry window: 7 days free. | Budget about ₹863 + GST per 1,000 dealers per broadcast. Follow-ups are cheap. |
| P4 | **India accounts must bill in INR by 2026-12-31**, or messages stop on 2027-01-01. A payment method is required. | Prerequisite. |
| P5 | Messaging limits are **per business portfolio**: 250 → 2,000 (business verification) → 10K → 100K → unlimited. They count unique users messaged outside a window per rolling 24h. | Business verification is needed before the dealer list passes 250. |
| P6 | **Pacing.** New or unrated templates and smaller portfolios are delivered in batches: `held_for_quality_assessment`, and dropped on bad feedback with `132015` (template) or `135000` (portfolio). A per-user cap on marketing from *all* businesses gives `131049` — wait 24h or more, and messages sent inside an open window are exempt. Opted-out users give `131050`. | The sending engine must understand held, dropped and capped messages, and send in waves. |
| P7 | Throughput is **80 messages/second per number**. To the same user it's **1 message per 6 seconds**, with bursts borrowed (`131056`). | Use two rate limiters. |
| P8 | Users may have **usernames and business-scoped user IDs (BSUIDs)**. `user_id` is always in webhooks; **`wa_id`/phone can be missing**. Changes arrive as system messages `user_changed_number` and `user_changed_user_id`. Meta still recommends sending marketing **to phone numbers**. | Omni Identity keys on BSUID, with phone as the second key. |
| P9 | Templates: up to 10 buttons. **With 4 or more buttons, or quick replies mixed with other types, the template doesn't render on WhatsApp desktop.** Quick replies must sit together; each has its own `payload`. Named parameters are supported. Header can be text, image, video, document or location. | Use 3 quick replies only. Many dealers use WhatsApp Web or desktop. |
| P10 | The **product card carousel** (2–10 cards) and **multi-product** (up to 30 items) messages take their **price from the Meta catalogue**. Catalog, MPM and carousel templates can't hold quick replies. The **interactive product carousel** works inside the window. | The broadcast stays a quick-reply template; product views are sent after the tap. |
| P11 | **One catalogue per WhatsApp account** and **one public price per item**. Must use `items_batch` (`/batch` is unavailable from v27). Required fields include `link` (own domain), `image_link` (a new URL when the image changes), brand, `wa_compliance_category`, and `origin_country`. Price format is `"123.00 INR"`. | One price list per WhatsApp number. Export (USD) buyers need their own number and catalogue. |
| P12 | **Flows**: JSON **7.3**. A static Flow can be **filled in per message** through `flow_action_payload.data`. Dropdowns allow up to **200** options with a description each. The reply arrives as `nfm_reply.response_json` carrying our `flow_token`. | "Ask Best Price" is a static Flow with no endpoint. |
| P13 | Interactive messages (window only): reply buttons (3), list (10 rows), CTA URL, media carousel (2–10), location request, **address message (India only)**, flow, product, product_list, catalog_message, `voice_call` button, call-permission request. Also a **typing indicator** and read receipts. | Choose the message type per step (§5). |
| P14 | The **order webhook** includes `catalog_id` and `product_items` (retailer id, quantity, item price, currency). Carts **cannot be edited** by the business. | Re-price in ERPNext. Counter-offers go as a new message. |
| P15 | **WhatsApp Payments India**: `order_details` "review and pay" in INR, amounts in paise, any agreed item amount, `expiration` of 300s or more. Gateways: Razorpay, PayU, BillDesk, Zaakpay, or UPI intent. UPI is capped at ₹5,00,000, with web/cards above that. Then `order_status` updates; confirm payment through the lookup API. | Optional phase: pay accepted offers inside WhatsApp. |
| P16 | **Calling API** has been GA since 2025-07. Calls the user places are **free**. Business-placed calls need the user's permission and the 2,000 tier; billed per minute. WebRTC to an agent's browser is possible. | "Talk to sales" can become a free call in a later phase. |
| P17 | You **cannot edit or delete a sent message** through the API. | Corrections go as a new message quoting the original. |
| P18 | The Groups API allows at most 8 people, with no interactive or commerce messages. | Not usable for dealer broadcasts. |
| P19 | Opt-in must name the business and be honoured on or off WhatsApp. `user_preferences` webhooks report marketing stop/resume. **DPDP Rules 2025**: consent, notice and erasure duties apply from about **May 2027**. TRAI DLT doesn't apply to WhatsApp today. | Keep a per-category consent record with evidence. |
| P20 | Meta's AI-provider restriction (2026-01-15) targets general-purpose assistants. A business using AI for **its own customers** is fine. The Meta Business Agent becomes the main responder once enabled. | Don't enable the Meta Business Agent. Our own drafting is allowed. |

## 3. What is built

- **Phase 0 — platform deadlines:** Graph v26, Marketing Messages API routing, error classes, BSUIDs, consent ledger, account/template health webhooks, pricing analytics (§10).
- **Phase 1 — messaging basics:** interactive builders with Meta's limits (reply buttons, list, CTA URL, Flow, location and address request, product, product list, product carousel, catalogue message), typing indicator and read receipts, named template parameters and Flow buttons, inbound dispatcher.
- **Catalogue sync** (from Phase 2): `items_batch` upserts with Meta's required fields checked first, batch status polled, sync logs, nightly sync per account.

## 4. Roadmap

| Phase | What | Status |
|---|---|---|
| 4 | Template types: copy-code (coupon), limited-time offer, media-card carousel, product-card carousel, catalogue, multi-product; Meta's template library | built (§11), reviewed, untested |
| 5 | Number and account: business profile (with photo), block users, QR codes and short links, ice breakers / commands / welcome message, two-step PIN and registration, webhook URL per number, commerce settings | built (§12), reviewed, untested |
| 6 | WhatsApp Business Calling: incoming and outgoing calls in the browser (WebRTC), call permission requests, call buttons, calling settings | built (§13), reviewed, untested |
| later | Authentication (OTP) templates, contact cards, sending reactions, Groups API, Conversions API | not planned yet |

## 5. Sending engine


- **Queue:** one RQ job per wave; a wave is up to 250 recipients by default.
- **Budget:** before each wave, check the portfolio limit against unique users messaged outside a window in the last 24h. Recipients with an open window don't count.
- **Rate limits:** a per-number token bucket (80/s, or 20/s with coexistence) and per-user spacing of at least 6s.
- **After each wave:** watch `message_template_quality_update` and `phone_number_quality_update`.

**Error handling:**

| Error | Action |
|---|---|
| `130429`, `4`, `80007` | Global exponential backoff |
| `131056` | Back off that recipient only (wait 4^n seconds) |
| `131049` | No retry within 24h; mark "capped" |
| `131050` | Opt-out; never retry |
| `131026` | Undeliverable; flag the identity |
| `131047` | Window closed; use a template |
| `132015`, `135000` | Template paused / portfolio pacing drop: **pause the campaign**, alert |
| `368`, `131031`, `131048` | Account problem: **stop all sending**, alert admins |
| `131063` | Template category changed: refresh the category and resend via the right endpoint |
| `held_for_quality_assessment` | Show as "held by Meta"; not an error |

- **Accepted is not delivered:** final status comes only from status webhooks, de-duplicated on wamid + status and ordered by event timestamp.

## 6. Identity and consent


- **Omni Identity:** `wa_bsuid` (unique per portfolio), `wa_phone` (nullable), `wa_username`, `wa_parent_bsuid`, and a history child table of past identifiers.
- **Excom Consent** (new) per identity and category: status, source (form / sales visit / WhatsApp reply / import), timestamp, notice version, capture method, evidence (file or message), withdrawn_on.
- **Subscribing:** adding someone to a subscriber list requires active consent in the list's category.
- **Opt-out sources:** `user_preferences` stop/resume, `131050`, STOP keywords, the unsubscribe link. Each updates both the consent record and the subscriber status.

## 7. Webhook fields to subscribe


- **Core:** `messages` (including `system`, `order`, `nfm_reply`, button replies), `message_template_status_update`, `message_template_quality_update`, `template_category_update`, `message_template_components_update`.
- **Account health:** `phone_number_quality_update`, `phone_number_name_update`, `account_update`, `account_alerts`, `account_review_update`, `business_capability_update`, `security`.
- **Consent and usernames:** `user_preferences`, `business_username_updates`.
- **Calling (Phase 6):** `calls`. Calls are processed only from signed payloads.
- **Handler requirements:** check `X-Hub-Signature-256` over the raw body, return 200 quickly, de-duplicate, order by timestamp.

## 8. Go-live prerequisites (outside code)


- **Meta business verification** (raises the limit 250 → 2,000), the display name approved, two-step verification on the number.
- **Billing:** WhatsApp account billing in **INR** with a payment method (before 2026-12-31).
- **Marketing Messages API** terms accepted (App Dashboard → WhatsApp → Quickstart).
- **Catalogue:** created in Commerce Manager and linked. Every item has an image, a product page URL and India compliance fields; the grievance and customer-care details are filled in (only if the catalogue is used).
- **Consent:** existing contacts' consent recorded (import with evidence) before the first broadcast.

## 9. Cost model (India, before 18% GST)

- **Marketing template:** ≈ ₹0.86 per delivered message; **utility / authentication:** ≈ ₹0.115 (free inside an open service window).
- **Service messages** (replies inside the 24 h window): free.
- Check the rate card in WhatsApp Manager before budgeting; Meta's own page loads dynamically and could not be read.

## 10. Phase 0 — what was built (2026-11-03)


Reviewed by three independent passes: Meta API conformance, security/compliance, and correctness. Every finding was fixed.

**API version, endpoints and errors**

- **Graph version:** every Meta call uses `graph_version()` (`excom/excom/utils/graph.py`). Stored versions below v24.0 are called as v26.0, and a patch (`upgrade_graph_api_version`) rewrites the saved values.
- **Marketing routing:** marketing templates go to `/marketing_messages` when the account ticks "Send Marketing via Marketing Messages API". Error 131063 retries once there. Error 131062 falls back from the user id to the phone number.
  - `product_policy` is configurable.
  - `message_activity_sharing` is sent only when the account explicitly chose On or Off.
- **Error classes:** one classifier (`services/whatsapp_errors.py`).
  - **Campaign stoppers:** 132015/132016 pause a broadcast; 368, 131031, 131048, token errors and the like stop sending on the account.
  - **135000** is a plain bad request: it fails that one send and never pauses a campaign.
  - **Retries:** broadcasts retry once, and only on rate limits. Retrying after a Meta-side error could send a marketing message twice.

**Consent**

- **One gate:** `assert_template_allowed` runs on every template send. It checks the identity being messaged *and* every identity that holds the number or user id (primary fields and aliases).
  - **Blocks everything:** STOP and do-not-contact block every template.
  - **Blocks marketing only:** Meta "Stop promotions", 131050 and the template opt-out button.
  - **Broadcasts and unknown categories** are always checked as marketing.
- **Consent record** (`Excom Consent`, append-only, server-stamped time and user). Rows are written by:
  - STOP / START replies
  - Meta preferences and 131050
  - subscriber lists, unsubscribe links and rules
  - merges
  - manual changes to the opt-out ticks
- **Resume rules:**
  - START only undoes a STOP the person sent on WhatsApp.
  - A Meta "resume" only lifts an opt-out that came from the same WhatsApp account.
- **Re-adding someone who withdrew** from a list needs a manager, and the record notes that staff asserted it. Automatic rules never re-add them.

**Identity**

- **User id first:** the WhatsApp business-scoped user id (with the account it belongs to) is matched before the phone number.
- **Recycled numbers:** a known number arriving with a different user id from the same account is a new person. The old record releases the number (kept as history), and both records are flagged for review.
- **Number changes:** a relink keeps the old number as history only, so its next owner does not land on the old record.
- **Merges** carry over the strictest opt-out, the aliases and the user ids.

**Webhooks**

- **Statuses:** every status in a batch is applied, and a status never moves backwards. Pricing category/type and the failure code are stored.
- **New handlers:** template category (advance notice vs done), template quality, number quality and limits, capability, account update (incl. `MM_LITE_TERMS_SIGNED`), account alerts (`alert_info`), user preferences, and informational fields.
  - Events are stored in `Excom WhatsApp Event`, scrubbed of numbers, user ids and message text.
  - Admins get one Critical alert per 6 h.
- **Unsigned payloads:** a site without an app secret still accepts unsigned payloads, but they cannot change consent, move identities or alert admins.

**Analytics and diagnostics**

- **Analytics:** built from `pricing_analytics`. Meta removed `conversation_analytics` on 2026-11-01.
- **Diagnostics** check:
  - Graph version
  - INR billing, with the steps after migration: new WABA id, re-subscribe the app, template sync
  - app secret
  - app subscribed to the WABA
  - webhook fields and their version (v24 or later)
  - Marketing Messages onboarding status
  - account health
  - number quality

**Still outside the code:** INR billing migration, business verification, accepting the Marketing Messages terms, and subscribing the webhook fields. Diagnostics flags all of these.

## 11. Phase 4 — what was built (2026-11-04): template types and Meta's template library

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

## 12. Phase 5 — what was built (2026-11-04): WhatsApp number and account

Draft for HLD-005 §10 (to merge after review). Not tested yet: MariaDB was down, so the automated tests were written but not run (see `phase5_test.md`).

**Where it lives**

- **Service:** `services/whatsapp_account.py`, one function per Meta operation, using the account's token and `graph_version()`. Meta's limits are checked first and refused with `AccountSettingError` (a `ValidationError`), so a bad value never reaches Meta. Meta's refusals raise `WhatsAppAPIError` with the code (`whatsapp_errors.raise_for_error`).
- **API:** `api/whatsapp_account.py`. Every endpoint is admin-only (`_check_admin_access`), except blocking from a conversation, which uses thread access like `mark_spam`. Failures are logged in Error Log with Meta's message and code only. The token, the PIN and verification codes are never logged.
- **Admin UI:** Admin → Channels → **WhatsApp number** (`WhatsAppNumberAdmin.tsx`). It has a number picker and seven tabs.
- **Inbox:** the conversation ⋯ menu has **Block on WhatsApp** / **Unblock on WhatsApp**.
- **New fields:**
  - Channel Account "WhatsApp Number Settings":
    - PIN (Password, read-only)
    - data localisation region
    - welcome-message setting
    - register / deregister log
    - the two webhook overrides set
  - Omni Identity: `wa_blocked_numbers` (Channel Account → blocked on: a block is per number), summarised in `wa_blocked`, `wa_blocked_on`, `wa_blocked_account` (the latest).
- **New doctype:** `Excom WhatsApp QR Code`.

**Business profile**

- **Read and update:** about, address, description, email, websites and vertical. Only changed fields are sent.
- **Limits:** about 1–139, address 256, description 512, email 128 (valid format), at most 2 websites (http(s), ≤ 256 each). Vertical must be one of Meta's 21 values or empty.
- **Photo:** uses the Resumable Upload API.
  1. `POST /{app_id}/uploads?file_name&file_length&file_type` starts a session.
  2. `POST /upload:<id>` sends the bytes, with `Authorization: OAuth …` and `file_offset: 0`.
  3. `profile_picture_handle` is set on the profile.
  - Only JPEG or PNG is accepted, up to 5 MB.
  - It needs the App ID on the account.

**Block users**

- `POST` / `DELETE /{phone}/block_users`, plus a paged `GET` of the block list.
- **What is sent:** the identity's user id (BSUID) when it belongs to this WhatsApp account, otherwise `+<number>`. Parent user ids are refused.
- **24-hour rule:** checked before calling Meta, from `Excom Thread.last_inbound_at` for that person on that number. The refusal says when they last wrote.
- **Partial failures** (`failed_users` beside a 139100 error) are logged per person with Meta's code and class. 131047 gives the "not in the last 24 hours" message.
- **On success** the block is recorded on the Omni Identity for that number; unblocking clears that number only.

**QR codes and short links**

- `Excom WhatsApp QR Code` mirrors `/{phone}/message_qrdls`. Creating a record creates the code on Meta (PNG or SVG), editing the message updates it, and deleting the record deletes the code.
- Meta is asked in `before_save`, after our own checks, because Frappe runs `before_insert` before `validate`.
- **Image:** Meta's image link is a CDN link, so a private copy is kept as a File on the record. **Image** fetches a fresh one.
- **Limits:** message ≤ 140; 2,000 codes per number (checked locally).
- **Sync from Meta** pages through the list (25 per page). It adds new codes, updates changed messages and drops codes deleted on Meta, without pushing anything back.

**Ice breakers, commands, welcome**

- `POST /{phone}/conversational_automation` sends `prompts` and `commands`; `GET ?fields=conversational_automation` reads them back.
- **Limits:**
  - ice breakers: at most 4, ≤ 80 characters, distinct
  - commands: at most 30; name ≤ 32 (no `/`, no spaces, unique), description 1–256
  - no emoji in either
- **`enable_welcome_message`** is sent only when the account's setting is On or Off. If Meta refuses the call, it is sent again without the setting, and the admin is told.
- **Inbound:**
  - A text that starts with `/name` is still stored as text. It is dispatched to `excom_inbound_handlers` as kind `command` with `{"command", "args"}` only when `name` is one of the number's commands, kept on the Channel Account (`wa_command_names`) each time they are saved or read here. Any other `/word` text is kind `text`, with the same payload as a hint.
  - A `request_welcome` message is stored in the thread ("👋 Opened the chat"), logged as an `Excom WhatsApp Event`, and dispatched as kind `welcome`.
  - A welcome request does **not** open the 24-hour window: `ingest_inbound_message(opens_window=False)` leaves `last_inbound_at` alone.

**Two-step PIN and registration**

- **Calls:** set PIN (`POST /{phone} {"pin"}`), register (`pin`, optional `data_localization_region`), deregister, request code (SMS/VOICE + language) and verify code (the code goes in the form body, never the URL).
- **PIN:** stored encrypted only after Meta accepts it. Register uses the stored PIN by default.
- **10 per 72 h:** every register / deregister call is logged on the account. The 11th within 72 h is refused locally, because it would get 133016 and a 72-hour block. A call with no answer (timeout, connection error) is logged too, since Meta may have counted it. The region is stored only after Meta accepts the registration.
  - The log is committed at once, so a refused request still counts.
  - 133016 and 136024 (already verified) get their own messages.
- **PIN deprecation:** the UI notes Meta's gradual PIN phase-out from 2026-09-23, and that a PIN is still sent until Meta changes the API.

**Webhook overrides**

- **Read:** `GET /{phone}?fields=webhook_configuration` gives the number, WABA and app URLs; `GET /{waba}/subscribed_apps` gives the apps.
- **Number:** set `webhook_configuration.override_callback_uri` + `verify_token`; clear by sending an empty URL.
- **WABA:** set by `POST /{waba}/subscribed_apps` with URL and token; clear by POSTing with no body. DELETE is never used, since it would unsubscribe the app.
- **Default URL:** this site's `/api/method/excom.excom.utils.webhook.webhook`, with the account's verify token (generated on first use and committed at once, so Meta's verification request can see it).
  - Another URL needs its own token. Only https URLs of ≤ 200 characters are accepted.
- **Fix:** `utils.webhook.get` now also compares the account's decrypted verify token. The column of a Password field holds only asterisks, so a token set on a Channel Account never matched before. Meta's check of an override URL depends on this.

**Commerce**

- `/{phone}/whatsapp_commerce_settings` reads and writes `is_cart_enabled` / `is_catalog_visible` as query parameters.
- `POST /{waba}/product_catalogs` links a catalogue and fills `wa_catalog_id` when it is empty. A `GET` lists the linked catalogues.
- Unlinking is not possible through the API; the UI says so.

**Unverified (built defensively, check live)**

- **Welcome:** `request_welcome` and `enable_welcome_message` are no longer in Meta's docs. Whether `enable_welcome_message` is returned by the read call is also unknown.
- **Photo:** size and dimension rules are unknown (5 MB is our guard). The upload session is opened with a Bearer header; Meta's page shows `access_token` in the query.
- **Profile:** whether an empty string clears address, description, email or vertical.
- **Ice breakers:** whether `prompts: []` clears them; which characters a command name may use (we allow letters, digits, `-` and `_`).
- **Registration:** whether `verify_code` accepts `code` in the form body (Meta shows `code=…`). The field names `status`, `code_verification_status`, `platform_type` and `name_status` on the number.
- **Commerce:** which permissions commerce settings need.

## 13. Phase 6 — WhatsApp Business Calling: what was built (2026-11-04)

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

## 14. Sources


**Method:** desk research of Meta's developer documentation and changelogs (developers.facebook.com/docs/whatsapp and /documentation/business-messaging/whatsapp), fetched 2026-11-03. REA was not used, since it analyses files and running apps, not an online service.

**Topics covered:**
- Graph API and Cloud API changelogs
- Pricing and updates-to-pricing; non-template messages
- Marketing Messages API (overview, features, onboarding, pricing, changelog)
- Templates: components, categorisation, review, pausing, pacing, portfolio pacing; per-user limits; carousels; limited-time offers; coupons
- Catalogues (overview, commerce settings, items_batch, product fields)
- Interactive messages; the order webhook
- Payments India (`order_details`, `order_status`, onboarding)
- Flows (components, sending, responses, changelog)
- Calling
- Groups
- BSUIDs; messaging limits; throughput; error codes
- Webhooks setup and `user_preferences`
- Coexistence; official business accounts
- WhatsApp Business Messaging Policy (updated 2026-09-23)
- The DPDP Rules 2025 notification

**Third-party sources** (used only where Meta's pages could not be read): Indian Business Solution Provider rate cards (Gallabox, MSG91, ecorpit) and ppc.land on the Cloud API marketing sunset.
