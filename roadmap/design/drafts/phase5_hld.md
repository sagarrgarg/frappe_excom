## Phase 5 — what was built (2026-11-04): WhatsApp number and account

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
  - Omni Identity: `wa_blocked`, `wa_blocked_on`, `wa_blocked_account`.
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
- **On success** the block is recorded on the Omni Identity; unblocking clears it.

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
  - A text that starts with `/name` is still stored as text. It is dispatched to `excom_inbound_handlers` as kind `command` with `{"command", "args"}`.
  - A `request_welcome` message is stored in the thread ("👋 Opened the chat"), logged as an `Excom WhatsApp Event`, and dispatched as kind `welcome`.
  - A welcome request does **not** open the 24-hour window: `ingest_inbound_message(opens_window=False)` leaves `last_inbound_at` alone.

**Two-step PIN and registration**

- **Calls:** set PIN (`POST /{phone} {"pin"}`), register (`pin`, optional `data_localization_region`), deregister, request code (SMS/VOICE + language) and verify code (the code goes in the form body, never the URL).
- **PIN:** stored encrypted only after Meta accepts it. Register uses the stored PIN by default.
- **10 per 72 h:** every register / deregister call is logged on the account. The 11th within 72 h is refused locally, because it would get 133016 and a 72-hour block.
  - The log is committed at once, so a refused request still counts.
  - 133016 and 136024 (already verified) get their own messages.
- **PIN deprecation:** the UI notes Meta's gradual PIN phase-out from 2026-09-23, and that a PIN is still sent until Meta changes the API.

**Webhook overrides**

- **Read:** `GET /{phone}?fields=webhook_configuration` gives the number, WABA and app URLs; `GET /{waba}/subscribed_apps` gives the apps.
- **Number:** set `webhook_configuration.override_callback_uri` + `verify_token`; clear by sending an empty URL.
- **WABA:** set by `POST /{waba}/subscribed_apps` with URL and token; clear by POSTing with no body. DELETE is never used, since it would unsubscribe the app.
- **Default URL:** this site's `/api/method/excom.excom.utils.webhook.webhook`, with the account's verify token (generated on first use).
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
