# High Level Design — WhatsApp Business Platform in excom

**Document:** HLD-005
**Version:** 0.4 (2026-11-04: refocused on the general WhatsApp platform)
**Date:** 2026-11-04
**Status:** Phases 0–1 built, catalogue sync built; Phases 4–6 in progress
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
| 4 | Template types: copy-code (coupon), limited-time offer, media-card carousel, product-card carousel, catalogue, multi-product; Meta's template library | in progress |
| 5 | Number and account: business profile (with photo), block users, QR codes and short links, ice breakers / commands / welcome message, two-step PIN and registration, webhook URL per number, commerce settings | in progress |
| 6 | WhatsApp Business Calling: incoming and outgoing calls in the browser (WebRTC), call permission requests, call buttons, calling settings | in progress |
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
- **Later phases:** `calls` (Phase 6, calling).
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

## 11. Sources


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
