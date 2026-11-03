# High Level Design — WhatsApp Rate Updates, Catalogue Orders and Discount Offers

**Document:** HLD-005
**Version:** 0.3 (Phase 0 implemented 2026-11-03)
**Date:** 2026-11-03
**Status:** Phase 0 built (platform deadlines); Phases 1–5 concept
**Owner:** Sagar Ratan Garg

---

## 1. Goal

When selling rates change, every opted-in dealer on a subscribed list gets **one** WhatsApp message.
It shows the new rates and three buttons: **View & Order**, **Full Catalogue** and **Ask Best Price**.

From there:
- Dealers can browse and order from the WhatsApp catalogue.
- Or they can ask for a better price for a quantity.
- The request reaches sales in **Raven**. Sales either takes the chat over or posts an offer such as "rate R for qty X".
- The system sends the offer on WhatsApp. Accepting it creates a draft Sales Order, and Indian dealers can optionally pay inside WhatsApp.

## 2. Starting point

### 2.1 excom today (code analysis, 2026-11-03)

**Already there:**
- Template sending; `send_transactional` with consent checks.
- Subscriber lists and rules; broadcasts with per-button click tracking.
- STOP keywords and an unsubscribe link.
- WhatsApp Flow builder, team inbox, and an HMAC-checked webhook.

**Missing:**
- **Catalogue:** no catalogue sync of any kind.
- **Outgoing messages:** no interactive sender (buttons, list, product, carousel, catalog, flow); quick-reply buttons carry no payload.
- **Incoming messages:**
  - `type:"order"` is stored as empty text (`utils/webhook.py:303`).
  - No inbound dispatcher hook for other code.
- **Integrations:** no Raven link, no discount or offer doctype.
- **API version:** Graph API default is **v21.0**, which expires 2027-01-21.
- **Known bugs:**
  - `whatsapp_flow.py:494` `send_test` returns an undefined `msg`.
  - `api/flow_endpoint.py:124` writes to a doctype that does not exist.

**Other apps on the bench:**
- `frappe_whatsapp` uses the **same doctype names** as excom, and both are installed on `frappe.localhost`.
- Selling rates change through the desk, data import, the API, POS Awesome, Webshop and ERPNext auto-update. ERPNext auto-update uses `db.set_value`, which skips hooks.
- No Customer has a default price list.

**Raven 3.0:**
- A bot can post messages with a document card.
- Threads have to be created by hand.
- Messages can't carry buttons.
- Thread replies can be picked up with a `Raven Message` `after_insert` hook.
- A "Message Action" (web only) can call a custom function.

### 2.2 WhatsApp platform facts that shape this design (checked 2026-11-03)

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

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | excom is the only WhatsApp system. Uninstall `frappe_whatsapp` and `whatsapp_chat` from excom sites. | Same doctype names. |
| D2 | **Phase 0 platform upgrade**: Graph v26; read `pricing`; Marketing Messages API onboarding; INR billing; error-class handling; BSUID identity. | P1, P2, P4, P8 are hard deadlines and affect every later phase. |
| D3 | Detect rate changes with a **5-minute scan** of Item Price `modified`, compared with the **last rate we told customers**. | Catches every path, including `db.set_value`; batches imports; reverting a rate sends nothing. |
| D4 | Changes collect in one draft **Rate Update** per price list. It auto-publishes after **30 quiet minutes**, or on "Publish now". There is a minimum gap of **one broadcast per list per 4 hours**. | One message per batch. Protects against `131049` and cost. |
| D5 | **Each WhatsApp number (Channel Account) has one catalogue price list.** A Subscriber List belongs to one Channel Account and uses that price list. Export (USD) buyers get a separate number, catalogue and lists later. | P11: one catalogue and one public price per WhatsApp account. |
| D6 | **Customer-specific prices are applied at order time, never in the broadcast.** The draft Sales Order is re-priced from the customer's price list and pricing rules, and the confirmation states the final rate. | The catalogue can only show one price. |
| D7 | On publish, **sync the changed items to the Meta catalogue** (`items_batch`, UPDATE price and availability). Wait for `check_batch_request_status` to succeed, then send. | Carousel and product views take the price from the catalogue (P10). |
| D8 | **The broadcast is a MARKETING template sent through `/marketing_messages`**: image header, named body variables, footer, and **exactly 3 quick replies** (View & Order / Full Catalogue / Ask Best Price). Each carries the payload `RU:<rate update>:<action>`. | P2, P9: renders on desktop and web, and a tap opens the 24h window. |
| D9 | **The header image is a rate card generated for each Rate Update**: a table of the top changed items with old → new rate. The body adds a one-line summary plus "and N more". | Template variables can't carry line breaks; the image can. |
| D10 | After a tap (inside the window, as service messages exempt from the marketing cap): **View & Order** → *interactive product carousel* for 2–10 changed items, otherwise *product_list* for up to 30. **Full Catalogue** → *catalog_message*. **Ask Best Price** → *Flow*. A **typing indicator** shows while we prepare the reply. | P10, P13, P6. |
| D11 | "Ask Best Price" is a **static Flow (JSON 7.3) with no endpoint**. The dropdown is filled per message with up to 200 items of the dealer's list, each showing its current rate. `flow_token` is a signed Rate Request draft ID. The flow is also attached to the broadcast payload, so the tap needs no extra round trip. | P12: no encryption endpoint to run, and no throttling risk. |
| D12 | Cart order → **draft Sales Order**, re-priced in ERPNext (D6), then an acknowledgement. If no shipping address is on file, an **address message** is sent (India). Never auto-submit. | P14, P13. |
| D13 | Discount request → **Rate Request** doc, one item per request. A Raven bot posts a card and opens a thread. Thread commands: `offer <rate> for <qty>[, …] [valid …]`, `chat`, `approve`/`reject`. A web "Send Offer" message action calls the same function. | Raven messages can't carry buttons. Thread replies work on web and mobile. |
| D14 | Guardrails: more than 5% below list needs a Sales Manager's `approve`. Offers are valid 24h by default and can be accepted once. Offers never become Item Prices. | Controlled discounting. |
| D15 | **Offer delivery.** Inside the window: **reply buttons** (Accept / Decline / Talk to sales) with a quotation PDF as the document header, quoting the request message. Outside the window: the **offer template**, submitted as UTILITY ("your requested quote RR-…"). It is always routed by its *actual* category from `template_category_update`. | P13, P3. Meta may reclassify it as marketing. |
| D16 | Accept → draft Sales Order at the offer rate. **Optional (Phase 4):** for Indian dealers, send `order_details` (review and pay, Razorpay) with an expiry equal to the offer validity. Confirm payment through the lookup API, then create a Payment Entry. Credit and NEFT stay the default. Export buyers get a CTA-URL payment link. | P15. |
| D17 | `chat` assigns the excom thread to that user. Automatic replies to free text stop; button taps still work. **Later phase:** "Talk to sales" sends a `voice_call` button. The user's call is free and rings the agent's browser over WebRTC. | P16. |
| D18 | Unknown sender (no Customer link): no Sales Order. Goes to Raven as "unknown customer". | Never guess the customer. |
| D19 | **Identity**: Omni Identity stores the BSUID (`user_id`, unique per portfolio) as the primary WhatsApp key. Phone/`wa_id` is a nullable second key; username is shown only. Match inbound on BSUID first, then phone. Relink on `user_changed_*` using `previous_user_id`. Broadcasts go **to the phone number**. | P8. |
| D20 | **Consent**: a per-dealer, per-category record (rate updates, offers) with source, time, notice version, method and evidence. Opt-outs come from `user_preferences`, `131050`, STOP keywords and the unsubscribe link. The footer says "Reply STOP to stop rate updates". | P19, DPDP. |
| D21 | **Sending engine**: a queued job; a **portfolio budget check** before each wave; an **80/s per-number** limiter (20/s if coexistence); **6s spacing per user**; waves of 250 (configurable); handling by error class (§8). A send counts as *accepted* until a status webhook arrives. | P5–P7. |
| D22 | Don't enable the **Meta Business Agent** or **Coexistence** in v1. The sales team works in the excom inbox. | P20; coexistence limits throughput to 20/s and disables broadcast lists. |
| D23 | Template insights stay **switched on** (`is_enabled_for_insights`). Store `pricing.category`/`type` on every Excom Message for cost reports. | Cost and click reporting. |

## 4. End-to-end flow

```
Item Price changes ─(5-min scan)→ Rate Update (draft, per price list)
        │  30 min quiet / "Publish now"  (≥ 4 h since last broadcast to the list)
        ▼
items_batch → Meta catalogue (wait for success) ─→ render rate-card image
        ▼
Sending engine: waves of 250 via /marketing_messages (named params, 3 quick replies)
        ▼
Dealer sees: [rate-card image] "Rates updated 3 Nov · Sugar M30 ₹4,210 (▼40) and 6 more"
             [View & Order] [Full Catalogue] [Ask Best Price]        footer: Reply STOP to stop
   │ (tap opens 24 h window — everything below is service messaging)
   ├─ View & Order ──→ product carousel (2–10) / product_list (≤30) → cart → order webhook
   │                         → draft Sales Order (re-priced) → ack (+ address message if needed)
   ├─ Full Catalogue ─→ catalog_message
   └─ Ask Best Price ─→ Flow (dropdown of items + current rate, qty, unit, target, note)
                               ▼ nfm_reply
                         Rate Request + ack "RR-0012 received"
                               ▼
                         Raven #rate-requests: card + thread (@sales person)
              ┌────────────────┼──────────────────┐
     "offer 4150 for 500"    "chat"          no reply 30 min → reminder
     (>5% off → approve)   take over chat
              ▼
     window open:  reply buttons + quote PDF [Accept][Decline][Talk to sales]
     window closed: offer template (routed by actual category)
              ▼ Accept (valid, unused)
     draft Sales Order at offer rate → "Accepted → SO-0124" in Raven thread
     (Phase 4, India: order_details review-and-pay → payment lookup → Payment Entry)
```

## 5. Message design per step

| Step | Message type | Window? | Category / cost (India, before GST) |
|---|---|---|---|
| Rate broadcast | Marketing template via `/marketing_messages`. Header image is the rate card; 3 quick replies. | No | Marketing ≈ ₹0.863 |
| View & Order | Interactive product carousel (2–10) or product_list (up to 30, up to 10 sections) | Yes | Service: first 1,000/month free, then ≈ ₹0.115 |
| Full Catalogue | catalog_message (thumbnail = top changed item) | Yes | Service |
| Ask Best Price | Flow message (navigate, prefilled data) | Yes | Service |
| Request acknowledgement | Text, quoting the Flow reply | Yes | Service |
| Order acknowledgement | Text or reply buttons (+ address_message if no address) | Yes | Service |
| Offer (window open) | Reply buttons + document header (quote PDF) | Yes | Service |
| Offer (window closed) | Offer template, UTILITY if Meta accepts it | No | Utility ≈ ₹0.115 / marketing ≈ ₹0.863 |
| Offer expired / correction | Text quoting the original (sent messages can't be edited) | Yes | Service |
| Pay (Phase 4) | `order_details` review and pay → `order_status` | Yes | Service |
| Talk to sales (later) | `voice_call` button; the user's call is free | Yes | Service |

**Templates to get approved** (named parameters):
- `rate_update` (marketing): header image; body `{{date}}`, `{{summary}}`, `{{more}}`, ending with fixed text (Meta rejects a body that ends with a variable); footer; 3 quick replies.
- `rate_offer` (submitted as utility): body uses any of `{{request}}`, `{{item}}`, `{{qty}}`, `{{rate}}`, `{{list_rate}}`, `{{offer}}` (all slabs as text), `{{valid_till}}`, ending with fixed text; quick replies Accept / Decline / Talk to sales. A positional template gets request, item, offer, valid till as `{{1}}`–`{{4}}`.
- `order_ack` (utility): order reference and status.

## 6. "Ask Best Price" Flow

- **Flow JSON 7.3, no endpoint.** One screen plus a confirmation screen.
- **Fields:**
  - Item: Dropdown filled from `${data.items}`, up to 200 options. Each option has id = item code, title up to 30 characters, and description "₹4,210/qtl today".
  - Quantity: TextInput number.
  - Unit: RadioButtons, filled from the item's UOMs.
  - Target rate: TextInput number, optional.
  - Note: TextArea, optional.
- **Data:** at send time, `flow_action_payload.data` = the dealer list's items (changed items first) plus a signed `flow_token` encoding (identity, rate update, nonce).
- **Reply:** `nfm_reply.response_json` is parsed. The token is checked, and the Rate Request is created with `list_rate` taken from ERPNext, not from the Flow.
- **Later:** an endpoint version is only worth it if live per-UOM repricing is needed. That brings RSA/AES encryption, replies under 1 second, and throttling risk.

## 7. Catalogue sync

- **One catalogue per WhatsApp Channel Account**, linked through Commerce Manager. The number has `is_catalog_visible=true` and `is_cart_enabled=true`.
- **Field mapping:**
  - `retailer_id` = Item `item_code` (variants use the variant code, with `item_group_id` = the template).
  - title, description, brand "FUN MACHINES" / company brand, condition = new, availability from stock.
  - price `"<rate> INR"` from the Channel Account's catalogue price list.
  - `link` = product page (Webshop route).
  - `image_link` = a versioned URL (a new URL whenever the image changes).
  - `wa_compliance_category=DEFAULT`, `origin_country=IN`, `internal_label` = item group.
- **API:** `POST /{catalog_id}/items_batch` (up to 3,000 per call). Poll `check_batch_request_status` and store `validation_status` per item.
- **Schedule:** incremental UPDATE on publish, plus a nightly full reconcile. Disabled items are archived.
- **Policy risk:** toy water guns and colour powders may be flagged. Describe them as "toy water blaster" / "herbal Holi colour", exclude look-alike-firearm SKUs, and appeal rejections in Commerce Manager.

## 8. Sending engine

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

## 9. Identity and consent

- **Omni Identity:** `wa_bsuid` (unique per portfolio), `wa_phone` (nullable), `wa_username`, `wa_parent_bsuid`, and a history child table of past identifiers.
- **Excom Consent** (new) per identity and category: status, source (form / sales visit / WhatsApp reply / import), timestamp, notice version, capture method, evidence (file or message), withdrawn_on.
- **Subscribing:** adding someone to a subscriber list requires active consent in the list's category.
- **Opt-out sources:** `user_preferences` stop/resume, `131050`, STOP keywords, the unsubscribe link. Each updates both the consent record and the subscriber status.

## 10. Webhook fields to subscribe

- **Core:** `messages` (including `system`, `order`, `nfm_reply`, button replies), `message_template_status_update`, `message_template_quality_update`, `template_category_update`, `message_template_components_update`.
- **Account health:** `phone_number_quality_update`, `phone_number_name_update`, `account_update`, `account_alerts`, `account_review_update`, `business_capability_update`, `security`.
- **Consent and usernames:** `user_preferences`, `business_username_updates`.
- **Later phases:** `payment_configuration_update` (payments), `calls` (calling).
- **Handler requirements:** check `X-Hub-Signature-256` over the raw body, return 200 quickly, de-duplicate, order by timestamp.

## 11. Data model (module "Excom Commerce")

| Doctype | Key fields |
|---|---|
| Excom Commerce Settings (single) | raven_channel, raven_bot, auto_publish_delay_min (30), min_hours_between_broadcasts (4), wave_size (250), max_discount_pct_without_approval (5), approver_role, offer_validity_hours (24), reply_reminder_min (30), templates (rate_update / rate_offer / order_ack), request_flow |
| Excom Channel Account (+) | wa_catalog_id, catalog_price_list, marketing_api_onboarded, graph_version, portfolio_limit (cached), throughput_mps |
| Excom Subscriber List (+) | channel_account (price list comes from it), consent_category, item_group filter |
| Excom Published Rate | item_code, price_list, rate, uom, published_on, rate_update |
| Excom Rate Update (+ child item) | price_list, status Draft/Syncing/Sending/Published/Paused/Failed, last_change_on, rate_card_image, catalog_batch_handles, waves; items: item_code, old_rate, new_rate, change, catalog_status |
| Excom Rate Request | customer, omni_identity, excom_thread, rate_update, item_code, qty, uom, list_rate, target_rate, note, status Open/Claimed/Pending Approval/Offered/Accepted/Declined/Expired/Handled in Chat, claimed_by, offers (child: rate, qty), valid_till, approved_by, raven_message, raven_thread, sales_order, payment_reference |
| Excom Consent | omni_identity, category, status, source, notice_version, method, evidence, granted_on, withdrawn_on |
| Excom Catalog Sync Log | channel_account, handle, status, item results |

## 12. Changes to existing excom code

1. **Platform upgrade:**
   - Graph v26; read `pricing` instead of `conversation`.
   - New messaging-limit field; the error-class table in §8.
   - `/marketing_messages` routing by template category.
2. **Identity:** BSUID on Omni Identity; inbound matching; handling of `user_changed_*` system messages.
3. **Interactive sender:** reply buttons, list, CTA URL, flow (with `flow_action_payload`), product, product_list, product carousel, catalog_message, address message, typing indicator.
4. **Templates:** named parameters, quick-reply payloads, header images from a URL or media ID.
5. **Inbound dispatcher hook** (`excom_inbound_handlers`) for button replies, `nfm_reply`, `order` and `system` messages.
6. **Fix bugs:** the Flow `send_test` bug and `flow_endpoint.save_flow_data`.
7. **Catalogue sync** through `items_batch`.
8. **Raven bridge**, active only when Raven is installed.

## 13. Build order

| Phase | Scope |
|---|---|
| **0. Platform** (deadline 2027-01-21) | Graph v26, pricing fields, error classes, BSUID identity, consent record, webhook subscriptions, Marketing Messages API onboarding, INR billing |
| **1. Messaging basics** | Interactive sender, named-parameter templates with payloads, inbound dispatcher, the two Flow bugs |
| **2. Rate broadcast** | Published Rate, Rate Update scan and publish, catalogue sync, rate-card image, sending engine, View & Order / Full Catalogue, cart → draft Sales Order |
| **3. Negotiation** | Flow, Rate Request, Raven bot and thread, offers (window/template), Accept → Sales Order, approvals, reminders, expiry |
| **4. Payments (India, optional)** | Razorpay configuration, `order_details` / `order_status`, payment lookup → Payment Entry, address message |
| **5. Later** | Export catalogue and number (USD), `voice_call` "Talk to sales" with browser WebRTC, Marketing Messages click webhooks, reports (cost per broadcast, response time, average discount, win rate) |

## 14. Go-live prerequisites (outside code)

- **Meta business verification** (raises the limit 250 → 2,000), the display name approved, two-step verification on the number.
- **Billing:** WhatsApp account billing in **INR** with a payment method (before 2026-12-31).
- **Marketing Messages API** terms accepted (App Dashboard → WhatsApp → Quickstart).
- **Catalogue:** created in Commerce Manager and linked. Every item has an image, a product page URL and India compliance fields; the grievance and customer-care details are filled in.
- **Templates approved:** `rate_update`, `rate_offer`, `order_ack`. The Flow published.
- **Consent:** existing dealers' consent recorded (import with evidence) before the first broadcast.
- **Raven:** Raven users for sales; `#rate-requests` created.
- **Phase 4 only:** a Razorpay account linked as a WhatsApp payment configuration.

## 15. Cost model (India, before 18% GST)

| Item | 1,000 dealers |
|---|---|
| One rate broadcast | ≈ ₹863 (≈ ₹1,018 with GST) |
| Follow-ups (taps, Flow, offers, acknowledgements) | First 1,000 service messages per number per month free, then ≈ ₹0.115 each |
| Offer template outside the window | ≈ ₹0.115 (utility) or ≈ ₹0.863 (if Meta reclassifies it) |
| Daily broadcasts for a month (22 working days) | ≈ ₹19,000 |
| With the 4h minimum gap and real batching | Usually far less |

The India rates come from several Indian Business Solution Providers that agree with each other; Meta's own rate card loads dynamically and could not be read. Check the rate card in WhatsApp Manager before budgeting.

## 16. Adjustable defaults and open points

**Defaults:**
- Batching: 30 min
- Minimum gap between broadcasts: 4 h
- Wave size: 250
- Discount without approval: 5%
- Offer validity: 24 h
- Reply reminder: 30 min
- Items per request: 1
- Sales Orders: draft only

**Still unverified:**
- Whether a Flow button can share a template with quick replies (not needed by this design).
- How many sections the in-chat multi-product message allows.
- Exact India tier thresholds for utility pricing.
- How Meta's catalogue review treats water guns and colour powders.
- Whether "Stop promotions" exists as an API button type (we use STOP and `user_preferences` instead).

## 17. Sources

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

## 18. Phase 0 — what was built (2026-11-03)

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

## 19. Phase 2 — what was built (2026-11-03)

- **Code layout:** doctypes live in the existing "Excom" module, and the code in `excom/excom/services/commerce/`:
  - `rates.py`: scan, collect, auto-publish, publish.
  - `catalog.py`: `items_batch` sync, check status, nightly full sync.
  - `rate_card.py`: Pillow PNG.
  - `handlers.py`: View & Order / Full Catalogue / Ask Best Price.
  - `orders.py`: cart → draft Sales Order.

  Using the existing module avoids a new module folder.
- **Rate updates:**
  - **First scan** of a price list records today's prices as the baseline and announces nothing.
  - **Only applicable prices count:** customer-specific prices and prices not valid today are ignored.
  - **The broadcast** is an Excom Broadcast linked to the Rate Update. It gets the consent gate, pause/resume, metrics, the three quick-reply payloads (`RU:<name>:view|catalog|ask`), waves (wave size and pause in settings), and a stop at the messaging limit (tier minus people messaged in the last 24 h).
- **Ask Best Price** sends a configurable text reply until Phase 3 replaces it with the request form.
- **Orders:**
  - **Pricing:** re-priced by ERPNext (`set_missing_values`) from the customer's default price list, else the settings price list, else the catalogue price list.
  - **Owner:** created as the settings' order owner, not the webhook's Guest session.
  - **Address:** a missing address is asked for with WhatsApp's address form (+91 numbers only).

## 20. Phase 3 — what was built (2026-11-03)

- **Request form:** `services/commerce/requests.py` sends the Ask Best Price form, a static Flow whose first screen gets `items` with today's rates, changed items first, up to 200.
  - **Signed token:** `RR1:<identity>:<rate update>:<nonce>:<hmac>`. An answer is only accepted from the contact it was sent to, and only once.
  - **Fallback:** without a form set up, Ask Best Price still sends the text reply.
- **Raven:** `raven_bridge.py` posts the request card (linked to the Excom Rate Request) and opens a thread as the bot, with the channel's members added.
  - A `Raven Message` `after_insert` hook passes human thread replies to `offers.on_command` in the background.
  - Without Raven, or without a channel or bot set, everything except the Raven posts still works.
  - **Trust:** a message linked to a request proves nothing (anyone can post one). A command counts only inside the request's own thread, opened on a card excom's bot posted in the configured channel. `offer` and `chat` need a sales role (Excom Agent/Admin, Sales User/Manager); `approve` / `reject` need the approver role.
  - The thread holds the channel's members plus the customer's sales person. The card warns when the contact has no Customer.
- **Offers:** `offers.py` handles parsing (`4,150` is 4150), claiming, the guardrail, approve/reject by the approver role, and sending.
  - **Guardrail:** the deepest slab vs list rate. Above the limit → approval. No list rate, or a limit of 0 → always approval. More than 50 % below list → the command must end with `confirm` (a typo guard).
  - **Staged, then live:** a new offer waits in `pending_offer` and becomes the live offer only once WhatsApp accepted the message. Each sent offer gets the next `offer_version`, and `valid_till` counts from the send. A failed send or a `reject` leaves the dealer's live offer as it was.
  - **Window open:** reply buttons `RR:<request>:accept|decline|talk:<version>`.
  - **Window closed:** the offer template with the same payloads.
  - **Accept:** only the live version, once (atomic status change), even while a newer offer waits for approval. → a draft Sales Order with `ignore_pricing_rule`, at the best slab the requested qty reaches; the reply says when the qty was raised to a slab minimum.
  - **Late taps:** an older version, an expired, declined or chat-handled offer each get a short reply instead of silence.
  - **Trust:** an offer tap from any other contact is ignored.
  - **`chat`:** uses the inbox access rule; a conversation held by someone else is refused and the holder named. The thread gets the user and their team.
- **Price list for a request:** Customer → Customer Group → the account's catalogue price list. A UOM the item is not sold in falls back to the stock UOM.
- **Scheduler** (every 5 min): expire offers past `valid_till` and tell the dealer when the window is open; one reminder for Open, Claimed or Pending Approval requests older than `reply_reminder_min`, mentioning whoever claimed it, the sales person, or the approvers.
