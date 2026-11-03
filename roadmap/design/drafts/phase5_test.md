## Phase 5 — WhatsApp number and account (commit: "WhatsApp number and account settings (HLD-005 Phase 5)")

Draft rows for TEST_005 (to merge after review).

```bash
bench --site frappe.localhost migrate   # Excom WhatsApp QR Code; number-settings fields on the Channel Account; wa_blocked on Omni Identity
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_account'}"
# regressions touched: webhook parsing, ingest, endpoint guards
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_messaging,excom.excom.tests.test_whatsapp_platform,excom.excom.tests.test_endpoint_guards,excom.excom.tests.test_import_targets,excom.excom.tests.test_inbound_as_guest'}"
```

**Setup before live tests:** on the WhatsApp Channel Account, set the App ID (needed for the profile photo) and the Business Account ID. The site must be reachable over **https** for the webhook override.

### Automated tests (29)

| # | Test | Result |
|---|---|---|
| 5.1 | profile limits (about 1–139, address 256, description 512, email, ≤ 2 websites with http(s), vertical enum) refused before Meta | ⏳ |
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
| 5.14 | ice breaker / command limits (4 × 80, 30 commands, name 32 without spaces, description 1–256, no emoji, no duplicates, "/" stripped) | ⏳ |
| 5.15 | conversational body shape; `enable_welcome_message` only when set; Meta's refusal retried without it (with a warning); no retry otherwise | ⏳ |
| 5.16 | conversational read-back (`?fields=conversational_automation`) | ⏳ |
| 5.17 | `parse_command` ("/name args", case, what is not a command) | ⏳ |
| 5.18 | inbound `/tickets …` stored as Text, dispatched as kind `command`; `request_welcome` stored, dispatched as `welcome`, event logged, 24 h window not opened | ⏳ |
| 5.19 | PIN must be 6 digits; set → `POST /{phone} {"pin"}`; stored encrypted, not in the column | ⏳ |
| 5.20 | register needs a PIN; body with pin + region (upper-cased; unknown region refused); deregister without a body; attempts logged | ⏳ |
| 5.21 | 10 register / deregister calls per 72 h: older attempts expire; 133016 recorded and explained; the 11th refused with no call | ⏳ |
| 5.22 | the PIN never appears in Error Log after a refused register | ⏳ |
| 5.23 | request_code (query: code_method, language; bad values refused; 136024 explained) and verify_code (code in the body, not the URL) | ⏳ |
| 5.24 | number webhook: default URL + account verify token; `webhook.get` accepts that token (and refuses a wrong one); clear sends `""`; http, > 200 chars, foreign URL without a token are refused | ⏳ |
| 5.25 | WABA webhook: set with URL + token; clear = POST with no body | ⏳ |
| 5.26 | webhook read-back (number, app, subscribed apps) | ⏳ |
| 5.27 | commerce settings as query parameters, no body; read parsed | ⏳ |
| 5.28 | link catalogue (form `catalog_id`, digits only; fills `wa_catalog_id`), list linked catalogues | ⏳ |
| 5.29 | Guest cannot call the settings endpoints (no Meta call) | ⏳ |
| 5.30 | (static) py_compile, undefined names, import targets, endpoint guards on the new API, DB-free smoke run of the limits and request shapes, frontend `tsc --noEmit` | ✅ ran |

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
| 5.39 | Ice breakers and commands → open a fresh chat on the test phone | L | ice breakers show; typing "/" lists the commands; a command reaches handlers as kind `command` | ⏳ |
| 5.40 | Welcome message On, then open a first chat | L | **UNVERIFIED:** does Meta accept `enable_welcome_message`? Does `request_welcome` arrive? (Community reports say it does not.) If it arrives, it is shown in the inbox, with no 24 h window. | ⏳ |

Registration and webhook:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.41 | Set PIN → Register (test number) | L | success; one attempt in the log. Check the field names in "Number status". | ⏳ |
| 5.42 | Request code (SMS) / verify | L | on an unverified number the code arrives and verifies; on a verified one, the 136024 message. Check that `verify_code` accepts the code in the form body. | ⏳ |
| 5.43 | Number webhook override to this site, then Clear | L | Meta accepts it (its verify GET passes with the account token); the read-back shows it; after Clear, events go to the app URL | ⏳ |
| 5.44 | WABA override, then Clear | L | same, at WABA level; the app stays subscribed after Clear | ⏳ |

Commerce:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.45 | Commerce: hide / show the catalogue, cart off / on | L | the shop icon and cart button follow | ⏳ |
| 5.46 | Link a catalogue ID | L | listed under Linked catalogue; catalogue sync uses it | ⏳ |

### Known gaps (not in Phase 5)

- **Unlinking a catalogue:** no API. Do it in WhatsApp Manager or Commerce Manager (the UI says so).
- **Blocked people are not checked before sending.** A send to a blocked identity is refused by Meta, not by excom's consent gate. `wa_blocked` is not carried over on identity merges.
- **Block list sync:** the block list is read live from Meta. Blocks made in WhatsApp Manager are not copied onto identities.
- **Resumable upload resume:** an interrupted photo upload is restarted, not resumed (`GET /upload:<id>` is not used).
- **QR images of synced codes** are fetched only when the admin presses "Image".
- **Removed from the docs:** the welcome message has no Meta documentation any more; we rely on it only as best effort.
