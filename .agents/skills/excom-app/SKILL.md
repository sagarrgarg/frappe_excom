---
name: excom-app
description: Deep architectural reference, doctype schemas, API protocols, channel conventions, security boundaries, and development rules for the EXCOM omnichannel communication platform in Frappe. Use this whenever exploring, maintaining, or extending the Excom app.
---

# EXCOM Platform — Architecture, Data Model & Development Skill

## 1. System Overview & Core Philosophy
`excom` is an enterprise-grade Omnichannel Communication Platform built inside the Frappe Framework. It unifies customer interactions across **WhatsApp**, **Email**, **Live Webchat**, and **Voice Telephony (Calls)** into a single operator workspace and unified timeline.

### Core Architectural Pillars:
1. **The Universal Person Anchor (`Omni Identity`)**:
   - Represents a real-world person across all communication channels.
   - Sits above ERPNext `Customer`, `Lead`, `Contact`, and `Supplier` records as a resolution layer (does not replace them).
   - Resolves identities via multi-step cascade: Phone -> Alias Phone -> Channel User ID -> Email -> Alias Email -> ERPNext Contact Phone/Email reverse bridge.
2. **Channel Model (`Excom Channel` & `Excom Channel Account`)**:
   - `Excom Channel`: System-managed registry (read-only, write-guarded, seeded via patches: `whatsapp`, `email`, `webchat`, `voice`).
   - `Excom Channel Account`: Integration instances (e.g. 5 WhatsApp numbers, 3 Email accounts, 2 Voice DIDs). Multi-tenant & multi-account by design.
3. **The "Dumb-Pipe" Rule (Provider Agnosticism)**:
   - Excom owns all business logic (routing, SLAs, assignments, queues, recordings, storage).
   - External providers (Meta Graph API, Exotel, Twilio, SendGrid, Airtel IQ) are strictly execution pipes.
4. **The "Two-Places" Rule**:
   - Vendor names (e.g. `Exotel`) must never leak into general doctypes, API endpoints, socket events, or React UI components.
   - A vendor name appears only in its provider adapter module (`channels/<channel>/providers/<vendor>.py`) and the `provider` Select field on the Channel Account.

---

## 2. Complete DocType Catalog & Data Model

### A. Identity & Resolution
- **`Omni Identity`**: Core person record (`full_name`, `primary_phone`, `primary_email`, `avatar_url`, `is_spam`, `needs_review`, `potential_duplicate_of`).
  - Child: `Omni Identity Channel` (`channel_type`, `channel_user_id`, `verified`).
  - Child: `Omni Identity Link` (`linked_doctype`, `linked_name`, `role`, `title`).
  - Child: `Omni Identity Alias` (`alias_type`, `alias_value_raw`, `alias_value_normalized`, `source`, `verified`).

### B. Communication & Threads
- **`Excom Thread`**: A conversation container linking an `Omni Identity` with a `Channel Account` (`status`: Open, Pending, Resolved, Snoozed, Spam; `assigned_to`, `team`, `priority`, `last_message_at`, `unread_count`).
  - Child: `Excom Thread Tag` (`tag`).
  - Child: `Excom Thread Transfer Log` (`from_user`, `to_user`, `from_team`, `to_team`, `transferred_at`, `reason`).
- **`Excom Message`**: Individual message record (`thread`, `omni_identity`, `channel_type`, `channel_account`, `sender_type`: Agent/Contact/Bot/System, `message_type`: Text/Image/Audio/Video/Document/Template/Call/Sticker, `content`, `delivery_status`: Queued/Sent/Delivered/Read/Failed, `attachments`, `reactions`).
- **`Excom Call`** *(Voice Engine)*: Dedicated call record (`provider_call_id`, `direction`, `from_number`, `to_number`, `business_number`, `agent`, `team`, `status`: Ringing/In-progress/Completed/Missed/Failed, `duration`, `talk_time`, `cost`, `recording_url`, `thread`).

### C. Access Control & Teams
- **`Excom Team`**: Departmental inbox groups (e.g. "Sales Tier 1", "Customer Support", "Billing").
  - Child: `Excom Team Member` (`user`, `role`: Member/Lead).
- **`Excom Account Team`**: Child table on `Excom Channel Account` gating which teams can access which business number/inbox (`team`, `can_view`, `can_reply`, `can_broadcast`).

### D. Automation, Templates & Broadcasts
- **`Excom Notification`** & **`Excom Notification Log`**: Trigger-based automated messaging (DocType events, delayed execution queues, print format attachments).
- **`Excom Broadcast`** & **`Excom Broadcast Log`**: Bulk campaign dispatch with rate limiting and recipient segmentation (`Excom Subscriber`, `Excom Subscriber List`, `Excom Subscriber Rule`).
- **`Excom Canned Response`**: Pre-saved quick-reply snippets with shortcut triggers (`/pricing`, `/welcome`).
- **`WhatsApp Templates`** & **`WhatsApp Flow`**: Meta-approved interactive WhatsApp templates and dynamic multi-screen forms.

---

## 3. Directory Layout & Module Structure

```
excom/excom/
├── boot.py                         # Injects user teams, channels, permissions into frappe.boot
├── hooks.py                        # Doc events, scheduler crons, jinja filters, assets
├── api/                            # Whitelisted REST endpoints
│   ├── chat.py                     # Main workspace API (threads, messages, typing, presence, search)
│   ├── voice.py                    # Voice desk API (click-to-call, recording proxy, active call)
│   ├── broadcast.py                # Campaign scheduling & dispatch
│   ├── email.py                    # Email sync & send
│   ├── notification.py             # Automation notification logs & triggers
│   ├── teams.py                    # Team roster & presence management
│   ├── identity_sync.py            # ERPNext Contact/Customer <-> Omni Identity sync
│   └── webchat.py                  # Public guest widget endpoints
├── channels/                       # Provider adapters & protocol engines
│   ├── whatsapp/                   # Meta Cloud API, webhooks, media uploads
│   ├── email/                      # IMAP/SMTP sync & parsing
│   └── voice/                      # Telephony engine (Exotel, Airtel IQ, routing, recording)
│       ├── handler.py              # Inbound webhook receiver
│       ├── outbound.py             # Click-to-call PSTN initiator
│       ├── routing.py              # Sticky-then-team (<5s cache-only router)
│       ├── recording.py            # Authenticated audio stream proxy
│       └── providers/              # VoiceProvider ABC + vendor adapters
├── doctype/                        # Schema JSONs, controllers, patches
└── services/ / tasks/ / utils/     # Queue workers, formatters, phone parsing
```

---

## 4. Real-time Event System & Socket.IO Conventions

Excom relies heavily on `frappe.publish_realtime` to power live reactive UI updates without page polling.

### Key Real-time Events:
- `excom_new_message`: Pushed to thread participants when an inbound/outbound message lands.
- `excom_message_status_update`: Sent when message changes status (`delivered`, `read`, `failed`).
- `excom_thread_update`: Sent on assignment, status change (Open -> Resolved), or tag updates.
- `excom_typing_status`: Ephemeral typing indicators between agents and customers.
- `excom_incoming_call`: Published **before DB write** to trigger instant screen pop on agent desk.
- `excom_call_status_update`: Live status updates (`ringing`, `connected`, `ended`, `picked_up_by_other`).

---

## 5. Critical Development Guidelines & Safety Rules

1. **NEVER modify foundational channel schemas without additive migration guards**:
   - `Excom Channel` is read-only and write-locked; only seed through migration patches.
2. **5-Second Timeout Budget on Webhooks & Routing**:
   - Telecom webhooks (Exotel / Twilio / Meta) fail if response takes > 5 seconds.
   - All routing and webhook handlers must query cache / lightweight DB and enqueue heavy workloads (record creation, media download, notifications) via `frappe.enqueue`.
3. **No Raw Vendor Credentials in URLs or Logs**:
   - Always pass secrets via HTTP Basic Auth headers or config tuples. Never embed tokens in URL query strings.
4. **Preserve Backward Compatibility in `Excom Message`**:
   - Do not bloat `delivery_status` with channel-specific states.
   - For non-chat interactions (e.g. Calls), store high-frequency mutable states in `Excom Call` and maintain a lightweight stub in `Excom Message`.
5. **Always Sanitize & E.164-Format Phone Numbers**:
   - Use `excom/utils/phone.py` for country parsing, prefix normalization, and validation.
---

## 6. Phased Execution, Testing & Quality Governance Rule

### Strict Multi-Phase Protocol:
1. **Strict Phase Isolation**: Always execute work **Phase-by-Phase**. Never start Phase 2 (Frontend / UI / Widgets) while Phase 1 (Backend Core / Infrastructure / Data Models / Routing / Adapters) is incomplete.
2. **Phase 1 Completion & Testing Gate**:
   - Every DocType, Hook, API endpoint, Background Job, and Provider Adapter in Phase 1 must be implemented with zero regressions to existing code.
   - Comprehensive automated unit and integration tests must be written and executed (e.g. testing `VoiceProvider` ABC, `ExotelAdapter`, `routing.py` <5s cache budget, `outbound.py`, `reconcile` background worker, and `recording` auth proxy).
   - Only after all Phase 1 tests pass 100% and exit criteria are verified, demonstrate the results and transition to Phase 2.
3. **Continuous Regression Testing**: Before and after every commit on the `somil-dev` branch, run validation tests to guarantee existing WhatsApp, Email, and Chat functionality remain completely unaffected.