## Phase 5 — WhatsApp number and account (commit: "WhatsApp number and account settings (HLD-005 Phase 5)")

Draft rows for TEST_005 (to merge after review).

```bash
bench --site frappe.localhost migrate   # Excom WhatsApp QR Code; number-settings fields (incl. wa_command_names) on the Channel Account; wa_blocked* and wa_blocked_numbers on Omni Identity
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_account'}"
# regressions touched: webhook parsing, ingest, endpoint guards
bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_messaging,excom.excom.tests.test_whatsapp_platform,excom.excom.tests.test_endpoint_guards,excom.excom.tests.test_import_targets,excom.excom.tests.test_inbound_as_guest'}"
```

**Setup before live tests:** on the WhatsApp Channel Account, set the App ID (needed for the profile photo) and the Business Account ID. The site must be reachable over **https** for the webhook override.

### Automated tests (42)

| # | Test | Result |
|---|---|---|
| 5.1 | profile limits (about 1–139, address 256, description 512, email ≤ 128 (129 refused), ≤ 2 websites with http(s), vertical enum) refused before Meta | ⏳ |
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
| 5.14 | ice breaker / command limits (4 × 80, 30 commands, name 32 without spaces, description 1–256, no emoji incl. ⭐ ☎️ ✨, no duplicates, "/" stripped); accents, Devanagari and arrows allowed | ⏳ |
| 5.15 | conversational body shape; `enable_welcome_message` only when set; a code 100 refusal retried without it (with a warning); no retry without the setting or on another code (190); command names kept on the account | ⏳ |
| 5.16 | conversational read-back (`?fields=conversational_automation`); the command names read are kept | ⏳ |
| 5.17 | `parse_command` ("/name args", case, what is not a command) | ⏳ |
| 5.18 | inbound `/tickets …` (a configured command) stored as Text, dispatched as kind `command`; `request_welcome` stored, dispatched as `welcome`, event logged, 24 h window not opened | ⏳ |
| 5.19 | PIN must be 6 digits; set → `POST /{phone} {"pin"}`; stored encrypted, not in the column | ⏳ |
| 5.20 | register needs a PIN; body with pin + region (upper-cased; unknown region refused; stored after success); deregister without a body; attempts logged | ⏳ |
| 5.21 | 10 register / deregister calls per 72 h: older attempts expire; 133016 recorded and explained; the 11th refused with no call | ⏳ |
| 5.22 | the PIN never appears in Error Log after a refused register | ⏳ |
| 5.23 | request_code (query: code_method, language; bad values refused; 136024 explained) and verify_code (code in the body, not the URL) | ⏳ |
| 5.24 | number webhook: default URL + account verify token, committed before the Meta call (one commit, token readable when Meta is called; reused without a new commit); `webhook.get` accepts that token with `hub.mode=subscribe` and refuses a wrong token, another or no mode, and all-asterisk tokens; clear sends `""`; http, > 200 chars, foreign URL without a token are refused | ⏳ |
| 5.25 | WABA webhook: set with URL + token; clear = POST with no body | ⏳ |
| 5.26 | webhook read-back (number, app, subscribed apps) | ⏳ |
| 5.27 | commerce settings as query parameters, no body; read parsed | ⏳ |
| 5.28 | link catalogue (form `catalog_id`, digits only; fills `wa_catalog_id`), list linked catalogues | ⏳ |
| 5.29 | Guest cannot call the settings endpoints (no Meta call) | ⏳ |
| 5.47 | profile photo: a real binary PNG (not valid UTF-8) is sent as the same `bytes` | ⏳ |
| 5.48 | profile photo refused for a File the user may not read and that is not attached to this number (no call); allowed once attached to the number | ⏳ |
| 5.49 | phone number id, WABA id and app id must be digits only (no call) | ⏳ |
| 5.50 | a block is per number: blocked on another number does not show on this conversation; unblocking here keeps the other block; the summary fields follow the latest block | ⏳ |
| 5.51 | Excom Agent: block status / block / unblock work on their own conversation; all three are refused (PermissionError, no call) on a conversation they cannot see; the admin `block` endpoint is refused | ⏳ |
| 5.52 | QR: a code made on Meta is deleted there when the insert / request rolls back | ⏳ |
| 5.53 | QR `refresh_qr_image`: fresh `qr_image_url.format(PNG)` read; the old File is deleted; one File attached; `qr_image` points at it | ⏳ |
| 5.54 | QR delete when Meta says the code does not exist (100): the local record goes; any other refusal (190) keeps it | ⏳ |
| 5.55 | inbound plain `hello` → kind `text`, payload None; `/help me` (not a configured command) → kind `text` with `{"command": "help", "args": "me"}` as a hint; no commands known → text | ⏳ |
| 5.56 | `request_welcome` on a thread with an open window leaves `last_inbound_at` as it was | ⏳ |
| 5.57 | register region: not stored when Meta refuses; stored on success; left out → stored one sent; `""` → none sent and the stored one cleared | ⏳ |
| 5.58 | register / deregister timeout or connection error counts as an attempt ("no answer") in the 72 h budget | ⏳ |
| 5.59 | Registration screen: Meta read fails (190) → `meta_error` set, budget and `has_pin` still returned | ⏳ |
| 5.30 | (static) py_compile, undefined names, import targets, endpoint guards on the new API, DB-free smoke run of the limits and request shapes, frontend `tsc --noEmit`; after the review fixes also DB-free runs of the emoji check, id check, command dispatch, per-number block map, register region / timeout budget, the welcome retry rule and `webhook.get` (mode, asterisks, same-length decrypt only) | ✅ ran |

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
| 5.39 | Ice breakers and commands → open a fresh chat on the test phone | L | ice breakers show; typing "/" lists the commands; a saved command reaches handlers as kind `command`, any other "/word" as `text` | ⏳ |
| 5.40 | Welcome message On, then open a first chat | L | **UNVERIFIED:** does Meta accept `enable_welcome_message`? Does `request_welcome` arrive? (Community reports say it does not.) If it arrives, it is shown in the inbox, with no 24 h window. | ⏳ |

Registration and webhook:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.41 | Set PIN → Register (test number) | L | success; one attempt in the log. Check the field names in "Number status". | ⏳ |
| 5.42 | Request code (SMS) / verify | L | on an unverified number the code arrives and verifies; on a verified one, the 136024 message. Check that `verify_code` accepts the code in the form body. | ⏳ |
| 5.43 | Number webhook override to this site (on an account with no verify token yet), then Clear | L | Meta accepts it (its verify GET, on another worker, passes with the freshly committed account token); the read-back shows it; after Clear, events go to the app URL | ⏳ |
| 5.44 | WABA override, then Clear | L | same, at WABA level; the app stays subscribed after Clear | ⏳ |

Commerce:

| # | Check | How | Expected | Result |
|---|---|---|---|---|
| 5.45 | Commerce: hide / show the catalogue, cart off / on | L | the shop icon and cart button follow | ⏳ |
| 5.46 | Link a catalogue ID | L | listed under Linked catalogue; catalogue sync uses it | ⏳ |
| 5.60 | With a bad access token, open each tab | L | each tab shows the read error; Save / Save all / commerce switches / Create / number Clear stay off with a reason; Registration still shows the register count | ⏳ |
| 5.61 | Blocked users: Load more, then Refresh | L | the list starts again from the first page | ⏳ |

### Known gaps (not in Phase 5)

- **Unlinking a catalogue:** no API. Do it in WhatsApp Manager or Commerce Manager (the UI says so).
- **Blocked people are not checked before sending.** A send to a blocked identity is refused by Meta, not by excom's consent gate. `wa_blocked*` / `wa_blocked_numbers` are not carried over on identity merges.
- **Commands edited in WhatsApp Manager** count for inbound dispatch only after Admin → WhatsApp number has read them (or saved).
- **Block list sync:** the block list is read live from Meta. Blocks made in WhatsApp Manager are not copied onto identities.
- **Resumable upload resume:** an interrupted photo upload is restarted, not resumed (`GET /upload:<id>` is not used).
- **QR images of synced codes** are fetched only when the admin presses "Image".
- **Removed from the docs:** the welcome message has no Meta documentation any more; we rely on it only as best effort.

### Review fixes (code review of 32271a2)

1. **Verify token committed before Meta's check.** `webhook_verify_token()` commits right after making a new token, so Meta's verification GET (another worker) can see it (5.24).
2. **Tests that could not pass:** the 128-character email is now 129 (5.1). The photo upload read the File with `get_content()`, which decodes a PNG as windows-1250 text; `_local_file_bytes` (shared with media sends) now reads raw bytes with `get_content(encodings=())`, and the upload refuses anything but bytes (5.3, 5.47).
3. **Admin screen and failed reads.** Every read shows its error with Retry. Save to WhatsApp, Save all (ice breakers and commands), the commerce switches, QR Create and the number override's Clear stay off until the data has loaded, with a line saying why. `get_registration` returns excom's own budget, PIN flag and region even when Meta cannot be read (`meta_error`) (5.59, 5.60).
4. **Commands.** An inbound `/name` text is kind `command` only when `name` is one of the number's commands, kept on the Channel Account (`wa_command_names`) whenever they are saved or read in Admin → WhatsApp number. Any other `/word` text stays kind `text` with the parse in `payload` as a hint; a plain text has payload None (5.18, 5.55).
5. **Blocks are per number.** `wa_blocked_numbers` on Omni Identity maps Channel Account → blocked on; `wa_blocked*` summarise it (any block, the latest). `thread_block_status` reports the conversation's own number; unblocking clears only that number (5.50, 5.51).
6. **Profile photo** must be a File the user may read, or one attached to this Channel Account (5.48).
7. **Meta ids** (phone number id, WABA id, app id, catalogue id) must be digits only before they go into a URL (5.49).
8. **Webhook GET** requires `hub.mode=subscribe` and decrypts only accounts whose masked token has the same length as the one offered (`compare_digest` kept).
9. **Register region** is stored only after Meta accepts; "None" in the screen sends `""` and clears it; leaving it out uses the stored one (5.57).
10. **A register / deregister with no answer** (timeout, connection error: `MetaUnreachable`) counts as an attempt (5.58).
11. **Welcome retry** only on Meta code 100 (5.15).
12. **QR records:** a code made on Meta is deleted there again if the transaction rolls back (`frappe.db.after_rollback`) (5.52); `refresh_qr_image` deletes the old File before attaching the new one (5.53); delete when the code is already gone on Meta (5.54); the screen links / shows only `/files/` and `/private/files/` URLs.
13. **Emoji check** uses Unicode category So plus U+2600–27BF, U+2B00–2BFF, U+1F000–1FAFF, U+20E3, U+FE0F and tags (5.14).
14. **Confirmations** for Register, Set PIN, the admin Block and an override pointing at another URL; Blocked users Refresh after Load more starts from the first page (5.61).
