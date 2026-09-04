# Excom Functional Test Checklist (P2 §T2 — living document)

Re-run every phase. First run: **2026-09-03** against `erpnextkgopl.local` (real data, read paths + reversible
writes only — no outbound WhatsApp/email/broadcast sends). Results columns: ✅ pass · ❌ fail (fixed in commit) ·
⏭ not run (reason) · 📝 gap ticketed.

Legend for how a row was exercised: **B** = headless browser (gstack browse), **A** = API call with a real session,
**S** = synthetic data script (created + deleted in the same run), **R** = code review.

## UI parity — the 42 controls (old → new)

| # | Control (legacy) | New location | Tier | How | Result |
|---|---|---|---|---|---|
| 1 | Search conversations | List › search box, `q:` free text | T1 | B | ✅ 48/48 "garg" matches DB |
| 2 | Channel chips (All/WA/Email/IG/Calls) | `+ Filter › Channel`, `channel:` chip, Calls/Comments views | T2 | B | ✅ |
| 3 | Account sub-filter | `+ Filter › Account`, `account:` chip | T2 | B | ✅ |
| 4 | Tag filter | `+ Filter › Tag`, `tag:` chip (client-side AND) | T2 | B | ✅ |
| 5 | Date filter presets + custom | `+ Filter › Date`, `after:`/`before:` chips | T2 | B | ✅ |
| 6 | Team filter | `+ Filter › Team`, `team:` chip (`__general__` = unassigned) | T2 | B | ✅ |
| 7 | Broadcast filter + status | `+ Filter › Broadcast / Delivery status` | T2 | B | ✅ |
| 8 | Total / unread counters | Rail badge (unread), view count | T1 | B | ✅ |
| 9 | New Chat | Rail **New**, phone FAB, ⌘N, ⌘K | T1 | B | ✅ Esc now closes (ISSUE-003) |
| 10 | Subscriber Lists / Teams / Merge / Rules / Broadcasts / Analytics / Settings | Rail + avatar menu + phone More + ⌘K | T2/T3 | B | ✅ all 8 routes, 0 JS errors |
| 11 | Report issue | Avatar menu, More | T3 | B | ✅ |
| 12 | Thread list rows (avatar, name, channels, preview, tags, broadcast status, unread) | `ThreadRow` | T1 | B | ✅ |
| 13 | Row context menu: read/unread, copy, spam, archive, delete (SM) | Row hover cluster + `⋯`; `e`/`a` keys; swipe on touch | T2 | B | ✅ |
| 14 | Collapse thread list / details panel | Width decides; `⌘.` toggles details | — | B | ✅ |
| 15 | Header: avatar, name, last seen, assignee | `RecordHeader` + `ContextStrip` | T1 | B | ✅ |
| 16 | TagManager | `⋯ › Tags…` (Modal) | T3 | B | ✅ add + remove round-trip |
| 17 | AI Active / Human badge | Composer notice + Take over | T1 | B | ✅ (no AI threads in data) |
| 18 | Transfer | `⋯ › Transfer…` | T3 | B | ✅ dialog loads teams/members (not submitted) |
| 19 | AI Assist | Record tab **AI**; `⋯ › AI assist` | T3 | B | ✅ suggestions/summary/insights render |
| 20 | Channel tabs | Merged feed + channel filter chips + Group toggle | — | B | ✅ |
| 21 | Account selector + "viewing via" banner | **Reply via ▾** | T1 | B | ✅ lists both accounts with identifiers |
| 22 | Pinned messages bar | One-line strip, expands | T2 | B | ✅ across all identity threads (ISSUE-004) |
| 23 | Message bubbles: text/image/video/audio/document/sticker/template/failed | `MessageBubble` | T1 | B+stress | ✅ |
| 24 | Email card expand/reply/forward/attachments | `EmailMessageCard` (re-skinned) | T1 | B | ⏭ no email threads visible in data |
| 25 | Internal note render | Amber note bubble | T1 | B | ✅ |
| 26 | Reply-to quote | Bubble quote block + reply bar | T2 | B | ✅ |
| 27 | Reactions bar + context menu react | Hover **React**, right-click menu | T2 | B+A | ✅ toggle adds/removes per user |
| 28 | Pin/unpin | Hover **Pin**, context menu | T2 | B | ✅ |
| 29 | Delivery icons + 10-min timer | Meta line | T1 | B | ✅ |
| 30 | Failed message + Retry | Rose bubble + Retry | T1 | B | ✅ (failed sticker in data) |
| 31 | Optimistic send | Feed | T1 | R | ⏭ no sends (real contacts) |
| 32 | Compose email (to/cc/subject) | Reply via = Email → fields in place | T1 | B | ⏭ no email account thread in view |
| 33 | Message / Note toggle | Composer radio | T1 | B | ✅ |
| 34 | Attach / Image / Template / Sticker / Canned | Composer `+` menu, `/` shortcut | T2 | B | ✅ menu entries; pickers open |
| 35 | Char limit counter | Composer | T2 | R | ✅ |
| 36 | 24h window / template required | Reply via state + composer swap | T1 | B | ✅ "Template required" shown, send blocked with reason |
| 37 | Identity panel (profile, channels, linked ERP, summary, transactions, quick actions) | Details pane / sheet | T1/T2 | B | ✅ |
| 38 | Account switch from identity panel | Details › account → sets Reply via | T2 | B | ✅ |
| 39 | Mobile: chats/calls/contacts tabs | Phone tabs (Today · Inbox · Pipeline · More) + Contacts page + Calls view | T1 | B | ✅ (Calls = saved view; call placement is Phase C) |
| 40 | Settings sections (8) + Appearance | `/settings?section=` | T3 | B | ✅ |
| 41 | Keyboard: ⌘K, ⌘N, j/k, ⏎, e, a, /, ⌘⏎, ⌘., g-chords | `useHotkeys` | T4 | B | ✅ j/j/⏎ opened 3rd row; ⌘K → Teams |
| 42 | Archived threads (new) | View **Archived**, unarchive | T2 | B+A | ✅ |

## Backend functional sweep

| Area | Case | How | Result |
|---|---|---|---|
| WhatsApp | Inbound ingest creates identity + thread + message | S | ✅ |
| WhatsApp | Replay of same `provider_message_id` → one message | S | ✅ second call returned "" and count stayed 1 |
| WhatsApp | 24h window check per thread | A | ✅ |
| WhatsApp | Template send / media send / outbound text | — | ⏭ real contacts — pilot (T1) covers |
| Email | Gmail poll, send, signature, attachments | — | ⏭ no Gmail account authorised on this site |
| Web chat | Config / session / message / poll / end | A(guest) | ✅ config 404s on bad account (was 500, ISSUE-006); session endpoints token-gated |
| Identity | Merge re-parents threads + messages, marks source Merged | S | ❌ → ✅ ISSUE-007 |
| Identity | Unmerge | R | 📝 no unmerge exists — P2 backlog (needs merge log) |
| Threads | Assign, transfer, tags, pin, notes, archive/unarchive, spam | A/B | ✅ |
| Threads | Visibility: non-manager reads only own-team/assigned threads | A | ❌ → ✅ ISSUE-002/002b (19 endpoints) |
| Threads | Long thread: latest page first, `before` paging | S+B | ❌ → ✅ ISSUE-005 (2,000 msgs: 7–28 ms/page) |
| Counters | `last_message_at` vs newest message; negative unread; unread without inbound | SQL | ✅ 0 / 0 / 0 |
| Data | Orphan messages / threads without identity / duplicate provider ids | SQL | ✅ 0 / 0 / 0 |
| Broadcasts | Wizard steps, list, detail, logs, metrics | B | ✅ (not submitted) |
| Notifications | Push relay config | B | ⚠ 417 on this dev site (`push_relay_server_url` unset) — environment, not code |
| Teams/Settings | Team CRUD gated to managers; canned; accounts | A/B | ✅ create_team 403 for Excom User |

## Security (T5)

| # | Check | Result |
|---|---|---|
| S1 | Rate limits keyed correctly | ❌ → ✅ ISSUE-001: `rate_limit(key="user")` was IP-keyed; now `user_rate_limit` per session user. Guest endpoints IP-limited (ISSUE-006). Inbound webhook + flow_endpoint intentionally unlimited (Meta bursts, HMAC-trusted). |
| S2 | Webhook HMAC | ✅ valid accepted / invalid rejected. **Accept-on-missing-signature only when no account has `wa_app_secret`** (`utils/webhook.py:44-68`) — ticketed P3 §3.9: require secret on Active accounts. |
| S3 | Replay idempotency | ✅ |
| S4 | Permissions | ❌ → ✅ ISSUE-002/002b. `ignore_permissions` in API paths reviewed: all behind `_check_excom_access`/`_check_manager_access`. `record.get_notes` on Excom Settings readable by Excom User (feedback comments) — accepted. |
| S5 | Guest endpoints | ✅ webchat session token required per call; unsubscribe token expiry-validated (400 on bad token); mobile.get_client_id public by design (PKCE client id). |
| S6 | Token expiry monitor | ✅ `tasks/token_monitor.check_token_expiry` scheduled (hooks.py:198). |
| S7 | Stored XSS | ✅ `sanitize_html` on ingest strips `onerror`; React renders content as text (`<img src="x">` shown literally, 0 img nodes, title unchanged); notes server-escaped; email bodies in `sandbox="allow-same-origin"` iframe (no scripts). |

## Responsive / low-DPI (T3) — 2026-09-03, record page + inbox

360 · 390 · 414 · 640 · 768 · 834 · 1024 · 1280 · 1366 · 1440 · 1920 @1×, 390 + 1366 @2×: no horizontal scroll, composer
inside viewport, breakpoints phone/tablet/laptop/wide as specified. Screenshots: `.gstack/qa-reports/screenshots/matrix-*.png`.

## Load / realtime (T4)

- 2,000-message thread: `get_messages` 7–28 ms per 100; 500 rows 16 ms; first paint with 100 bubbles, "Load earlier" pages 100 at a time.
- Realtime: socket events + 10 s poll (paused when tab hidden) + focus revalidate. Disconnect/reconnect and packet-loss runs: ⏭ needs a throttled real browser — pilot week.

## Open / deferred

- 📝 Unmerge (T6) — no API; needs a merge log. P2 backlog.
- 📝 HMAC: enforce `wa_app_secret` on Active WhatsApp accounts (P3 §3.9).
- 📝 Email + outbound WhatsApp paths — pilot week with real accounts.
- 📝 E8: README claimed Instagram as a shipped channel; corrected to "planned" (no `channels/instagram` code).


## P3 — Native CRM (first run 2026-09-03, synthetic data, cleaned up)

| Case | How | Result |
|---|---|---|
| Intake → Lead with provenance (`first_touch_*`, `source_reference`, `company`, `intake_source`), attribution via `set_attribution` (Lead Source + Campaign on v15), identity link both ways, thread on the auto-ack account, 5 system messages | S | ✅ |
| Replay: same `dedupe_key` twice → one log row, one Lead | S | ✅ (unique index) |
| Re-touch: existing open Lead reused, no duplicate (R1) | S | ✅ |
| R3: Opportunity from unclassified Lead refused server-side | S+B | ✅ |
| Classify → Convert → Opportunity carries `customer_type`/`omni_identity`, Lead status → Opportunity, link retained | S+B | ✅ |
| Gates evaluated per type; blocked stage refused with the failing gate named; override path (managers, reason logged) | S+B | ✅ (`p3-gate-blocked.png`) |
| advance_stage writes `pipeline_stage`, `stage_entered_at`, mapped `sales_stage`/`probability`, Excom Stage Change Log rows, thread system message | S+B | ✅ |
| Details tab renders from `get_field_schema` (10 sections) — E2 | B | ✅ (`p3-details-opp.png`) |
| Today / Intake / Pipeline pages at 1366 + 390; pipeline board per type, phone list | B | ✅ (`p3-pipeline-export-1366.png`) |
| Webhook dispatch: leadgen + feed + messages in one payload → each handled/logged, none 500 | S | ✅ |
| HMAC: unsigned rejected once any secret exists (WA accounts or Meta sources); degraded acceptance logged | R | ✅ |
| Guardrails G1–G6 | S | ⚠ site: bridge enabled, `crm_deal` fields present, `crm` installed — awaiting decision |
| SLA ladder fenced to post-go-live records, in-app only by default | S | ✅ (0 escalations on legacy data after fence; an unfenced dry run touched 80 legacy leads and was fully reverted) |
| IndiaMART / TradeIndia / Meta live pulls, auto-ack send, website form from a real origin | — | ⏭ need vendor keys + a registered source |

## Inbox row identity + ownership (2026-09-03, synthetic `QA Kinds Person`, cleaned up)

| Case | Sev | Result |
|---|---|---|
| Row shows what the contact is (Customer / Supplier / Employee / Opp · type / Lead · type) from Omni Identity links; `Unknown` when no ERP record | S | ✅ `Lead · Distributor` chip; `kind:` chip + Customers/Suppliers/Leads/Unknown views |
| Row shows the team it belongs to | S | ✅ team name before the assignee |
| Assignee whose User is disabled → shown as `Owner disabled` and listed under Unassigned | S | ✅ `assigned_to_enabled` from `tabUser.enabled`; intake queue drops disabled `lead_owner` too |
| Talk = claim: first outbound on an unassigned (or disabled-owner) chat assigns the chat and the linked open Lead (`lead_owner` + ToDo) | S | ✅ `_claim_on_talk` on send_message / send_template_to_thread / send_email |
| Details tab: Link fields are a searchable picker (frappe.desk.search.search_link), Select stays select | S | ✅ no more "Could not find Salutation: m" |

## Admin area `/admin` (2026-09-03, synthetic data, cleaned up)

| Case | Sev | Result |
|---|---|---|
| Manager-only gate (Excom Manager / System Manager); others see a notice | S | ✅ `hasRole` client + `_check_manager_access` on every `api/admin.py` endpoint |
| Teams: create, rename (doc rename), parent team, description, delete with chats moved to another team / no team | S | ✅ |
| Team members: add (enabled users only), Member↔Manager toggle, remove; disabled users flagged with "reassign chats" | S | ✅ |
| Team → channel-account access (writes `Excom Channel Account.allowed_teams`) | S | ✅ |
| Users & roles: grant/revoke Excom User / Excom Manager; open chats + leads owned; reassign all work to a user or to nobody (ToDos follow) | S | ✅ |
| Generic editor: Channel accounts (passwords write-only, masked as set/unset), Templates (+ Sync from Meta), Intake sources, Canned responses, Tags, Stickers (Attach upload), Email signatures, Notification rules (child tables), Excom Settings (Single) | S | ✅ schema-driven; `depends_on` honoured for the simple `eval:` forms |
| Read-only logs: transfer log, stage change log, notification log | S | ✅ |
| Legacy `/teams` route redirects to `/admin/teams`; Rail avatar menu + More show "Admin" for managers | S | ✅ |

## Closure scene (2026-09-03, synthetic `QA Close Person`, cleaned up)

| Case | Sev | Result |
|---|---|---|
| ⋯ → Close… (shortcut `e`): outcome Resolved / Converted / Lost / Not interested / Duplicate / Spam + reason chips + note | S | ✅ |
| Close archives every open thread of the contact with `closure_outcome/reason/closed_by/closed_at`; row shows outcome chip in Archived; record shows a closed banner | S | ✅ |
| Doc-level activity log: Comment on the linked Lead / Opportunity / Customer (Desk timeline) and on each thread; Activity tab shows comment + closure rows | S | ✅ |
| Negative outcome + "also close CRM": Lead → Do Not Contact (pipeline stage Closed Lost), Opportunity → Lost via declare_enquiry_lost with the reason as an Opportunity Lost Reason | S | ✅ |
| Reopen (⋯ → Reopen): threads back to Open, closure cleared, Lead/Opportunity reopened, comment logged | S | ✅ |
| Resolved / Converted never touch the CRM record's status | S | ✅ |

## Direct actions, context menus, detail mode (2026-09-03)

| Case | Sev | Result |
|---|---|---|
| Record header ≥ 720 px wide: Transfer / Tags / Assign / Classify / Promote / Convert / Quote / Close as icon buttons; the rest under ⋯; narrower → all under ⋯ | S | ✅ |
| Right-click on an inbox row or the record header opens Excom's own menu (same items as ⋯); long-press on touch (700 ms) does the same | S | ✅ code; long-press per Radix ContextMenu |
| Hold Ctrl / ⌘ and hover: rows, header actions, rail items and message meta show a detail panel (full contact, record, owner, exact times, what a button does) | S | ✅ code |
| Admin → Auto-assignment rules: Frappe Assignment Rule CRUD scoped to Excom-related doctypes (Excom Thread / Intake Log / Broadcast / Omni Identity / Lead / Opportunity / Prospect / Customer / Contact); HD Ticket rules hidden and unreadable; scope change blocked; Prompt-named docs get a Name field | S | ✅ synthetic rule created / read / deleted |

## Automated (2026-09-03)

| Suite | Run with | Covers |
|---|---|---|
| `excom.excom.tests.test_core_flows` (12) | `bench --site <site> execute excom.excom.tests.run.run` | open never claims · talk claims chat + lead · disabled owner = unassigned · reassign user work · 6h retry window · close Lost → Do Not Contact + timeline + reopen · admin needs manager · team access check · per-user rate limit · IndiaMART / TradeIndia / Meta payloads → mapping + Lead |
| `frontend/src/lib/__tests__` (6) | `cd frontend && yarn test` | Unassigned view incl. disabled owners · kind filter · URL ↔ chip round-trip · search chips · SWR retry policy (429 / 4xx / 5xx) |
| Browser (headless, 2026-09-03) | manual | Close dialog opens from the header icon; touch long-press (pointerType touch) opens the row menu |

## P4 — Owning the spine (2026-09-03)

| Case | Sev | Result |
|---|---|---|
| Manifest existence + completeness against installed v15 | S | ✅ `crm_manifest.check` → OK (part of the test suite) |
| Manifest existence against upstream v16 JSON (Lead, Opportunity, Prospect, Customer, Contact, Quotation, Sales Stage, Party Link, Company, Territory, UTM *, Assignment Rule, ToDo) | S | ✅ OK; diff recorded in `v16_upgrade_runbook.md` |
| Gateway contract suite (create/provenance/attribution, identity link + precedence, close/reopen, promote once, duplicate email, reassign, intake list, convert + stage, stage map) | S | ✅ 9/9 native |
| Same suite against shadow `Excom Lead` | S | ✅ 9/9 after moving classification into the gateway |
| Live v16 scratch bench | S | ⏭ needs ~15 GB free disk |
| **Defect (P0)**: P3 custom field `Customer.customer_type` shadowed ERPNext's native field → every Customer save failed validation ("cannot be Individual") | P0 | ✅ fixed 2026-09-03: patch `fix_customer_type_clobber` removes the custom field, ours is now `excom_customer_type`; `crm_schema.apply()` refuses to shadow native fields; found by the P4 v16 diff |

## Instagram / Messenger via Graph API (2026-09-03)

| Case | Sev | Result |
|---|---|---|
| Conversation payload → thread per IGSID/PSID, identity keyed by the platform id, image attachment → Image message, own messages skipped, second ingest is a no-op | S | ✅ test_meta_dm |
| 24h window: open → RESPONSE send; closed → refused; closed + HUMAN_AGENT approved → MESSAGE_TAG/HUMAN_AGENT | S | ✅ |
| Webhook `entry.messaging[]` accelerator ingests, echoes skipped | S | ✅ |
| Live poll against a real page | S | ⏭ needs page id + token on an Instagram / Messenger channel account |

## Meta Business connection (2026-09-03)

| Case | Sev | Result |
|---|---|---|
| Discover from Graph payloads → Page, Instagram, Lead Form, WhatsApp Number rows; re-discover keeps enabled/link state | S | ✅ test_meta_connect (mocked Graph) |
| Enable Page → Messenger account (page token) · Instagram → Instagram account · Lead Form → Intake Source pulling with the page token · WhatsApp Number → WhatsApp account with system token / app secret / verify token; enabling twice reuses; disable → Inactive / enabled=0 | S | ✅ |
| Webhook: verify token and HMAC app secret from the connection accepted | S | ✅ code (`_candidate_secrets`, GET verify) |
| Live discovery against a real Business Manager | S | ⏭ needs a system-user token |

## Website token webhook + embed code (2026-09-04)

| Case | Sev | Result |
|---|---|---|
| `website_webhook?token=…` JSON → Intake Log Processed + Lead; same `submission_id` → duplicate; form-encoded without id → hash dedupe (second call duplicate); wrong token → 401; disallowed Origin → 403 | S | ✅ HTTP-level run on the dev site (synthetic source, cleaned) |
| Admin → web chat account shows the script tag with Copy; Website source shows token, endpoints, HTML / JS / curl, Generate / Regenerate token | S | ✅ |

## Identifiers, merge kinds, lead visibility, manual leads (2026-09-04)

| Case | Sev | Result |
|---|---|---|
| WhatsApp accounts fetch `display_phone_number` + verified name from Meta (on save when missing, daily); Reply via / New conversation / admin show the number, not the phone-number id; Ctrl+click the number in Reply via copies it | S | ✅ code; live fetch needs a token |
| Merge suggestions show the ERP records behind both identities (Lead / Opportunity / Customer chips, or "No ERP record") | S | ✅ |
| Lead visibility: source → team managers until assigned; members only their own; Excom Managers all; no-source leads open to managers | S | ✅ test_visibility_filters |
| Leads page: **+ New lead** (name, phone, email, company, type, source, notes) → identity + Lead via the gateway → record pane → Start conversation; typing the same phone again opens the existing lead | S | ✅ |

## Notes = Comments, email editor, read-only masters, reopen (2026-09-04)

| Case | Sev | Result |
|---|---|---|
| Internal note in the chat and a note in the Notes tab are the same Frappe Comment on the party's open record (else the thread); both surfaces read the same list; old `is_internal` messages converted by patch `internal_notes_to_comments` | S | ✅ test_chat_note_and_tab_note_are_the_same_comment |
| A customer message on a Closed chat reopens it, clears the closure fields and leaves "Reopened by a new customer message" on the thread and record; the CRM status is not touched | S | ✅ test_customer_message_reopens_closed_chat |
| Customer / Supplier / Employee are read-only in Details unless Excom Manager or System Manager; server refuses edits too | S | ✅ test_master_records_read_only_for_agents |
| Email: rich-text editor with HTML source toggle; To / Cc / Bcc chips with contact + colleague suggestions; "schedule for later" parks the mail as a Scheduled Excom Message that the every-minute scheduler sends as the author; cancel from the bubble | S | ✅ test_email_schedule (Gmail mocked) |
| Website source with Allowed Origins: POST without Origin → 403, wrong Origin → 403, allowed Origin or Referer → 200; IP list from a non-listed IP → 403; no lists → token alone; same rule on submit_enquiry | P1 | ✅ fixed 2026-09-04 (HTTP-level run on the dev site) |
| One Source list: saving an Excom Intake Source creates the Lead Source / UTM Source row; a manual lead with a source gets `intake_source` + attribution stamped; existing Lead Sources became Manual/Channel rows via `unify_sources` (26 on the dev copy) | S | ✅ test_intake_source_mirrors_attribution_and_stamps_leads |
