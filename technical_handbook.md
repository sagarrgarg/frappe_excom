# Excom Technical Handbook

## Purpose

`excom` is a Frappe app for a unified external communication ecosystem.
This handbook captures what is being built, why, and how technical decisions are made.

Companion deep-dive handbook:

- `whatsapp_handbook.md` for WhatsApp-specific API and implementation requirements.

## Current Stage

Stage: Foundation implementation in progress.

Core WhatsApp communication flows are now implemented and being hardened for delayed execution and operational observability.

## Implementation Update (2026-02-20)

First backend foundation has been implemented:

- Added `Excom Channel` DocType as a system-managed channel registry.
- Locked UI edits by combining:
  - read-only DocType permissions for `System Manager` (read/report/export/print only)
  - controller-level write guards that allow writes only in install/migrate/patch contexts
- Added a post-model patch to seed default channel record:
  - `name = whatsapp`
  - `channel_label = WhatsApp`
  - `is_enabled = 1`

Why this change:

- Establish a stable channel source-of-truth early.
- Support the requirement that one channel can map to multiple provider accounts (handled at integration account DocTypes, not in channel master key fields).
- Prevent accidental UI drift in foundational configuration records.

Impacted modules:

- `excom/excom/doctype/excom_channel/`
- `excom/patches/v1_0/create_default_excom_channel.py`
- `excom/patches.txt`

Migration implications:

- Requires `bench --site <site> migrate` (or app install) to create the DocType table and seed `whatsapp`.
- Existing data is not modified because this is an additive change.

## Implementation Update (2026-02-20, WhatsApp Delay + Log Queue)

WhatsApp notification execution now supports delayed dispatch using `WhatsApp Notification Log` as both queue and audit table.

What changed:

- `WhatsApp Notification` now supports:
  - `enable_delay`
  - `delay_value`
  - `delay_unit` (`Minutes/Hours/Days`)
  - `print_format` override when `attach_document_print` is enabled
- Delayed DocType-event notifications are queued as `Pending` rows in `WhatsApp Notification Log` instead of sending immediately.
- Added lifecycle fields in `WhatsApp Notification Log`:
  - `notification`, `status`, `scheduled_for`, `pending_since`, `pending_for_seconds`
  - `processed_on`, `reference_doctype`, `reference_name`, `to_number`, `reason`
  - `request_payload`, `response_payload`, `meta_data`
- Added scheduler worker to process due pending logs and update pending duration.
- Added cancellation-safe checks before delayed send:
  - cancel if source document is deleted
  - cancel if source document is cancelled (`docstatus = 2`)
  - cancel if notification config is missing/disabled

Why it changed:

- Replicates n8n-style workflow psychology (quick trigger, delayed execution, state re-check at send time) in a Frappe-native way.
- Makes delayed operations auditable and operable from one table.
- Enables dynamic print format behavior by `reference_doctype` with optional per-notification override.

Impacted modules:

- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.json`
- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.js`
- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.py`
- `excom/excom/doctype/whatsapp_notification_log/whatsapp_notification_log.json`
- `excom/excom/doctype/whatsapp_notification_log/whatsapp_notification_log.py`
- `excom/excom/utils/__init__.py`
- `excom/hooks.py`

Migration implications:

- Requires `bench --site <site> migrate` to apply new DocType fields.
- Existing log rows remain valid; new fields are additive.
- Scheduler now includes pending-log processing for delayed WhatsApp notifications.

## Implementation Update (2026-02-24, Delayed Notification Queue Wiring)

`send_template_message` previously did not check `enable_delay`; all DocType-event notifications were sent immediately. The scheduler processor existed but no code path created Pending logs with `scheduled_for`.

What changed:

- `send_template_message` now checks `self.enable_delay` and `notification_type == "DocType Event"`.
- When both are true, `_queue_delayed_notification()` creates a `WhatsApp Notification Log` with `status="Pending"`, `scheduled_for`, `reference_doctype`, `reference_name`, `to_number`, and `notification`.
- The scheduler processor (`process_pending_whatsapp_notification_logs`) picks up due logs and calls `send_template_message` with `force_send=True`, which bypasses the delay branch and sends immediately.
- Added `notification_log_name` and `force_send` parameters for processor compatibility.

Impacted modules:

- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.py`

Migration implications:

- None. Behaviour is additive; notifications without `enable_delay` unchanged.

## Sources Studied

- Chatwoot (`chatwoot/chatwoot`): mature omnichannel support inbox, automation, assignments, reports.
- Rocket.Chat (`RocketChat/Rocket.Chat`): secure team communication platform with strong channel model and extensibility.
- Raven (`The-Commit-Company/raven`): Frappe-native messaging product patterns and integration-first UX.
- Mint (`The-Commit-Company/mint`): Frappe + React architecture style, focused domain workflows, and clean app boundaries.

## Product Scope for Excom

Excom will unify external channels into one operator workspace:

- WhatsApp
- Email
- Web chat/widget
- Social connectors (later phase)
- SMS/voice connectors (later phase)

Core outcomes:

- One customer identity across channels.
- One conversation timeline across channels.
- One rule engine for routing, automation, and SLAs.
- One audit-ready event log for compliance and analytics.

## Reference Capability Matrix

### From Chatwoot

- Omnichannel inbox and conversation assignment.
- Labels, canned responses, notes, teams, automation rules.
- Reporting and performance metrics.
- Help center and self-serve support surfaces.

### From Rocket.Chat

- Channel abstractions with access control.
- Real-time collaboration patterns.
- Security-first posture and enterprise readiness.
- Extensibility model for integrations and apps.

### From Raven and Mint (Architecture Direction)

- Frappe-native backend and permission system.
- React frontend with clear module boundaries.
- Shared utility layer and reusable hooks.
- Domain-first workflows rather than generic abstractions.

## Target Architecture (v1 Blueprint)

### 1) Channel Connectors Layer

Responsibility:

- Normalize inbound/outbound payloads from providers.
- Handle provider auth, retries, delivery receipts, webhook verification.

Notes:

- Provider-specific logic must stay in adapter modules.
- Exposed to domain via normalized events only.

### 2) Conversation Core

Responsibility:

- Contact resolution and identity merge.
- Conversation thread lifecycle.
- Message persistence and timeline ordering.

Notes:

- This is the source of truth for conversation state.

### 3) Workflow and Automation

Responsibility:

- Assignment and queue routing.
- Rule evaluation (conditions, priorities, actions).
- SLA timers and escalation events.

Notes:

- Deterministic and auditable outcomes are mandatory.

### 4) Agent Workspace

Responsibility:

- Unified inbox, thread view, composer, internal notes.
- AI assist surface (later phase, optional).
- Team workload and handoff UX.

Notes:

- Keep interaction latency low; support optimistic UI for sending.

### 5) Governance and Analytics

Responsibility:

- Immutable event trail.
- Performance dashboards and operational metrics.
- Retention policies and export/compliance workflows.

## Data Model Strategy: Reuse First, Add Last

Excom will minimize new DocTypes and reuse existing ones wherever possible.

### Existing DocTypes (Migrated to Excom)

All WhatsApp DocTypes have been migrated from the legacy WhatsApp app into Excom:

- `WhatsApp Account`
- `WhatsApp Message`
- `WhatsApp Templates`
- `WhatsApp Notification`
- `WhatsApp Flow`
- `WhatsApp Profiles`
- `WhatsApp Notification Log`
- `WhatsApp Recipient List`
- `WhatsApp Recipient`
- `Bulk WhatsApp Message`
- `WhatsApp Button`
- `WhatsApp Flow Field`
- `WhatsApp Flow Screen`
- `WhatsApp Message Fields`
- `WhatsApp Settings`

From `whatsapp_chat`:

- `WhatsApp Contact`

From WhatsApp Chatbot app:

- `WhatsApp Chatbot`
- `WhatsApp Keyword Reply`
- `WhatsApp Chatbot Flow`
- `WhatsApp Chatbot Session`
- `WhatsApp Agent Transfer`

From `frappe` / `erpnext` / `crm` (installed stack dependent):

- `Contact`
- `Lead`
- `Opportunity`
- `Customer` (when lifecycle reaches customer state)

From `helpdesk` (installed stack dependent):

- Ticket DocTypes (for example `HD Ticket`) and linked timeline/comment artifacts

### Compatibility with Frappe CRM and Helpdesk

Excom will treat CRM and Helpdesk as first-class downstream systems, not optional add-ons.

CRM compatibility baseline:

- Resolve external identities into a single `Contact` first.
- Link conversations to `Lead`/`Opportunity` when sales context exists.
- Add routing hooks for sales ownership and stage-aware prioritization.

Helpdesk compatibility baseline:

- Allow conversation-to-ticket linking without message duplication.
- Push key events (first response, resolution, reopen, SLA breach) to ticket timeline.
- Support ticket-first and conversation-first workflows.

Shared compatibility rules:

- Use references/links between records instead of copying long text payloads.
- Keep permissions aligned with CRM/Helpdesk ownership models.
- Preserve bidirectional traceability (`Conversation <-> Contact <-> Lead/Deal <-> Ticket`).

### Contact Profile Strategy (AI Behavioral Summary)

Each resolved `Contact` will have a Contact Profile for future decisioning.

Reuse-first storage approach:

- Store profile fields on `Contact` via custom fields (no separate profile DocType in phase 1).
- Recommended fields:
  - `excom_profile_summary` (Long Text)
  - `excom_profile_tags` (Data or JSON text)
  - `excom_profile_sentiment` (Select)
  - `excom_profile_last_updated_on` (Datetime)
  - `excom_profile_confidence` (Float)

Profile generation approach:

- Generate summary asynchronously from conversation history and CRM/Helpdesk interactions.
- Keep profile editable by humans; AI is assistive, not authoritative.
- Track regeneration time and confidence to avoid stale assumptions.

### New DocTypes Policy

- Phase 1 target: zero new core message/contact/channel DocTypes.
- New DocTypes are allowed only when cross-channel requirements cannot be represented safely using existing models.
- If new DocTypes are needed, add the minimum possible and keep them integration-oriented.

### Minimal New DocTypes (If Strictly Required)

1. `Excom Conversation Link` (optional)
   - Purpose: map multiple provider thread/message IDs into one unified operator thread without duplicating message bodies.
2. `Excom Routing Rule` (optional)
   - Purpose: channel-agnostic routing and escalation orchestration when existing notification/chatbot rules are insufficient.

No other new DocTypes should be introduced until these are proven insufficient.

## Suggested Module Boundaries

- `excom/connectors/` provider adapters and webhook handlers.
- `excom/domain/` orchestration services that wrap existing DocTypes.
- `excom/workflows/` routing, SLAs, and automation logic.
- `excom/api/` whitelisted endpoints and DTO validation.
- `excom/analytics/` reporting on top of existing message/session tables.
- `excom/compat/` CRM and Helpdesk mapping services and sync contracts.
- `excom/frontend/` operator UI app.

## Engineering Constraints

- Keep Doctype controllers minimal; business logic must live in service modules.
- Prefer `frappe.db.get_value`, `frappe.get_doc`, and `frappe.get_all` over raw SQL unless profiling proves a need.
- Use asynchronous jobs for connector retries and heavy processing.
- Preserve idempotency in webhook and message ingest paths.
- WhatsApp functionality has been migrated from the legacy WhatsApp app into Excom. All WhatsApp DocTypes now belong to the Excom module.
- AI-generated contact profiles must be deterministic per input window and recomputed through background jobs.

## Security and Compliance Baseline

- Verify all webhooks and signatures before processing.
- Encrypt sensitive connector credentials at rest.
- Record every message state transition as an event.
- Ensure role-based access for conversations and channels.

## Performance Baseline

- Inbound webhook handling target: fast accept, async processing.
- Message send path: queue-based with provider retry policy.
- Timeline fetch: indexed by conversation and message timestamp.

## Migration Implications

Current change set includes schema and runtime behavior updates.

- New `WhatsApp Notification` fields for delay and print-format override.
- New `WhatsApp Notification Log` fields for queue lifecycle and observability.
- Scheduler now processes pending delayed logs and updates elapsed pending time.
- Existing immediate notification flow remains backward-compatible when delay is disabled.

## Build Phases (Recommended)

1. Foundation (reuse-first): service layer over existing WhatsApp/chat/chatbot DocTypes.
2. CRM/Helpdesk compatibility layer: `Contact` resolution, `Lead/Opportunity` linking, ticket linking.
3. Unified inbox MVP: read models and APIs without introducing new message/contact DocTypes.
4. Routing and SLA: first reuse existing notification/chatbot rules, then add `Excom Routing Rule` only if gaps remain.
5. Cross-channel stitching: introduce `Excom Conversation Link` only if unified timeline cannot be achieved via existing references.
6. AI Contact Profile: enrich each `Contact` with summary fields and confidence-scored behavior tags.
7. Analytics and governance: build dashboards from existing message and event sources; add storage only if required.

## Removed Logic

- Removed proposal of many net-new Excom DocTypes (`Excom Channel`, `Excom Contact`, `Excom Message`, `Excom SLA Event`, etc.).
- Reason: avoid schema duplication and leverage mature existing models from installed WhatsApp ecosystem apps.
- Historical relevance: if future channels (email/web/social) expose unavoidable modeling gaps, revisit with strict ADR and migration notes.
- Added requirement that each `Contact` carries an AI-generated profile summary using `Contact` extension fields instead of a new profile DocType.

## Implementation Update (2026-02-23, Omni Identity)

### What is Omni Identity

A universal person anchor that represents one real-world person across all communication channels and ERP entities. It is NOT a replacement for Contact, Customer, or Lead. Those remain first-class ERP entities. Omni Identity sits above them as a cross-channel resolution layer.

### DocTypes Added

1. **Omni Identity** — main record
   - `display_name` — human-readable label
   - `primary_phone`, `primary_email`, `primary_whatsapp` — contact coordinates
   - `normalized_phone`, `normalized_email` — hidden, system-computed for dedup
   - `hash_fingerprint` — SHA-256 of sorted normalized identifiers (unique, hidden)
   - `status` — Active / Merged / Archived
   - `channels` — Table of Omni Identity Channel (channel-specific IDs)
   - `linked_entities` — Table of Omni Identity Link (Contact, Lead, Customer, etc.)
   - `is_master`, `merged_into`, `merge_group_id` — merge/dedup system
   - `ai_profile_summary` — AI-generated behavioral summary

2. **Omni Identity Channel** (child table)
   - `channel_type` — Link to Excom Channel (WhatsApp, Instagram, etc.)
   - `channel_user_id` — provider-specific user ID (phone number, handle, etc.)
   - `verified`, `last_seen`

3. **Omni Identity Link** (child table)
   - `linked_doctype` — Link to DocType (Lead, Contact, Customer, Supplier, etc.)
   - `linked_name` — Dynamic Link to the actual record
   - `role` — Decision Maker / Billing / Technical / Primary Contact / Influencer / Unknown

### Identity Resolution Flow (v1)

When a new message arrives:

1. Normalize phone/email
2. Search Omni Identity:
   - Match `normalized_phone`
   - OR match `channel_user_id` in Omni Identity Channel
   - OR match `normalized_email`
3. If found → attach channel, update last_seen
4. If not → create new Omni Identity with channel entry
5. Then: link or create Contact/Lead as needed

API: `excom.excom.doctype.omni_identity.omni_identity.resolve_identity`
Merge: `excom.excom.doctype.omni_identity.omni_identity.merge_identities`

### Merge Mechanics

- Source identity's channels and links are copied to the master
- Source status becomes "Merged", `merged_into` points to master
- Both records share the same `merge_group_id`
- Source `is_master` is set to 0
- All future lookups skip Merged records

### Design Constraints

- Omni Identity does not store messages (Excom Message does)
- Omni Identity does not replace Contact (it links to Contact)
- Normalized fields are hidden from UI, computed on validate
- `hash_fingerprint` is unique to prevent accidental duplicate creation
- Track changes enabled for full audit trail
- Omni Identity Alias child table added for alternate phones/emails; searched during resolution as fallback


## Implementation Update (2026-02-23, Excom Thread and Message)

### What Changed

Introduced the conversation and message layer that replaces WhatsApp Message and WhatsApp Profiles with channel-agnostic doctypes.

### New DocTypes

**Excom Thread** — conversation anchor, one per identity x channel x account. Fields include omni_identity, channel, account (Dynamic Link), thread_key (unique), status (Open/Pending/Closed), assigned_to, priority, unread_count, last_message_at/last_inbound_at/last_outbound_at, last_message_preview, last_message_direction, display_name, primary_phone (denormalized). Indexed on thread_key, last_message_at, and (omni_identity, channel, account).

**Excom Message** — immutable event log replacing WhatsApp Message. Fields include thread, omni_identity, channel, account (Dynamic Link), direction (Inbound/Outbound), message_type, delivery_status, content_text, content_json, media_file, provider_message_id (idempotency key), provider_timestamp, reply_to, created_by_user, template, failure_reason, reference_doctype/reference_name. Indexed on provider_message_id and (thread, creation). Track changes disabled (immutable log).

**Omni Identity Alias** — child table on Omni Identity for alternate numbers, emails, country codes. Fields: alias_type, alias_value_raw, alias_value_normalized, verified, source, last_seen.

### Service Layer

File: `excom/excom/services/thread_service.py`

- `upsert_thread()` — find or create thread by thread_key
- `ingest_inbound_message()` — full pipeline: resolve identity, upsert thread, check idempotency, insert message, update thread counters
- `send_outbound_message()` — call provider API, insert message, update thread timestamps
- `update_delivery_status()` — update delivery_status on Excom Message by provider_message_id

### Inbox Correctness Rule (Non-Negotiable)

Inbox reads ONLY from `tabExcom Thread` ordered by `last_message_at DESC`. Zero joins, zero subqueries, O(1) per row. Messages loaded only when a thread is opened, queried by `(thread, creation)` index.

### Webhook and Chat API

Webhook handler routes all inbound messages through `ingest_inbound_message()`. Status callbacks update `delivery_status` on Excom Message. Chat API queries Excom Thread for sidebar and Excom Message for message list. Send operations go through `send_outbound_message()`.

### Migration

Patch `migrate_whatsapp_to_excom_messages` converts existing WhatsApp Profiles and Messages into Omni Identity + Excom Thread + Excom Message records. Original WhatsApp Message table preserved as archive.

## Implementation Update (2026-02-24, Frontend UI Overhaul)

### What Changed

Complete replacement of the WhatsApp-style 2-panel chat UI with a modern 4-panel omnichannel communication dashboard adapted from Figma designs.

### Architecture Change: 2-Panel to 4-Panel Layout

Old layout (removed):
- `ChatSidebar` + `ChatWindow` (WhatsApp clone)
- Components: `ChatSidebar.tsx`, `ChatWindow.tsx`, `MessageBubble.tsx`, `ContactItem.tsx`, `Avatar.tsx`, `EmptyState.tsx`
- Page: `ChatLayout.tsx`
- Theme: WhatsApp green dark (`chat-*` color palette)
- Icons: `react-icons`
- Dates: `dayjs`

New layout (implemented):
- `LeftSidebar` + `ChatThreadList` + `ChannelTabsView` + `OmniIdentityPanel` + `AIAssistantDrawer`
- Full mobile experience: `MobileApp`, `MobileConversationList`, `MobileChannelView`, `MobileContactView`, `MobileAIDrawer`, `CallScreen`
- Theme: zinc/blue-purple dark gradient
- UI library: shadcn/ui (Radix primitives)
- Icons: `lucide-react`
- Dates: `date-fns`

### New Component Library (shadcn/ui)

Created `frontend/src/components/ui/` with reusable primitives:
- `button.tsx` (CVA variant system)
- `input.tsx`
- `badge.tsx`
- `scroll-area.tsx` (Radix)
- `tabs.tsx` (Radix)
- `separator.tsx` (Radix)
- `dropdown-menu.tsx` (Radix)
- `tooltip.tsx` (Radix)
- `utils.ts` (`cn()` helper using `clsx` + `tailwind-merge`)

### New Desktop Components

- **LeftSidebar**: Channel filter dropdown, search input, conversation statistics, branding
- **ChatThreadList**: Conversation cards with avatar, channel icons, unread badges, ERP entity tags, multi-channel indicators
- **ChannelTabsView**: Channel tabs header, account selector, message list with delivery status, AI status badge, message input with attachments
- **OmniIdentityPanel**: Contact card, active channels and accounts with access badges, contact information, ERP integration details, quick actions
- **AIAssistantDrawer**: Suggested replies, conversation summary, recommended actions with priority, quick insights

### New Mobile Components

- **MobileApp**: Root mobile component with view stack (list, conversation, call, contact) and bottom tab navigation
- **MobileConversationList**: Thread list with search, channel icons, AI/human status badges
- **MobileChannelView**: Conversation view with channel tabs, account selector, message list, input area
- **MobileContactView**: Full-screen contact info with ERP integration details
- **MobileAIDrawer**: AI suggestions overlay for mobile
- **CallScreen**: Voice/video call UI with controls and status

### Data Model Adaptation

New TypeScript types added alongside existing ones:
- `UnifiedContact` — aggregates threads by omni_identity into a single contact with all channels, accounts, and messages
- `Account` — represents a channel account with access control
- `Message` — enhanced message type with sender info, delivery status, media support
- `Conversation` — denormalized view for the OmniIdentityPanel

### Hook Transformation

- `useContacts.ts` (`useThreads`) — now returns both raw `ExcomThread[]` and transformed `UnifiedContact[]` by grouping threads by `omni_identity`
- `useMessages.ts` — now returns both raw `ExcomMessage[]` and transformed `Message[]` with mapped delivery status and message types

### Dependencies Added

- `lucide-react` (replacing `react-icons`)
- `date-fns` (replacing `dayjs`)
- `class-variance-authority` + `tailwind-merge` + `clsx` (shadcn/ui variant system)
- `@radix-ui/react-scroll-area`, `@radix-ui/react-tabs`, `@radix-ui/react-separator`, `@radix-ui/react-dropdown-menu`, `@radix-ui/react-dialog`, `@radix-ui/react-slot`, `@radix-ui/react-tooltip`

### Deleted Files

- `src/components/ChatSidebar.tsx`
- `src/components/ChatWindow.tsx`
- `src/components/MessageBubble.tsx`
- `src/components/ContactItem.tsx`
- `src/components/Avatar.tsx`
- `src/components/EmptyState.tsx`
- `src/pages/ChatLayout.tsx`

### Migration Implications

- Frontend-only change; no backend schema modifications
- Old `react-icons` and `dayjs` packages can be removed from `package.json` in a follow-up cleanup
- Tailwind config completely replaced; any custom `chat-*` color references in other files will need updating
- `App.tsx` no longer uses React Router for page routing; the 4-panel layout is rendered directly with state-driven navigation

## Implementation Update (2026-02-24, Phase 0 — Critical Fixes and Identity Hardening)

### What Changed

Six items implemented as Phase 0 of the Excom roadmap. All are in `roadmap/phase_0_critical_fixes.md`.

**0.1 — Bulk WhatsApp Message status bug fixed**
- File: `excom/excom/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py`
- `self.status == "In Progress"` (comparison) corrected to `self.db_set("status", "In Progress")` (assignment) in `create_single_message()`.

**0.2 & 0.3 — Scheduler and doc_events activated in hooks.py**
- `scheduler_events` now calls all WhatsApp notification trigger functions: hourly, daily, weekly, monthly, and their long variants. The `process_pending_whatsapp_notification_logs` and `trigger_whatsapp_notifications_all` run on every `all` tick.
- `doc_events` now wires `run_server_script_for_doc_event` to all six standard document lifecycle events on every DocType. WhatsApp Notification automation for DocType events is now functional.

**0.4 — Webhook async processing**
- File: `excom/excom/utils/webhook.py`
- `post()` now logs the raw payload synchronously (audit), commits, then enqueues `_process_webhook_payload` as a background job via `frappe.enqueue`. Returns `Response("", 200)` immediately.
- All message parsing, media download, identity resolution, and thread ingestion moved to `_process_webhook_payload(data_str)`.
- In test mode (`frappe.flags.in_test`), the job runs inline (synchronous).

**0.5 — Idempotency guard confirmed**
- `thread_service.ingest_inbound_message()` already checks `frappe.db.exists("Excom Message", {"provider_message_id": ...})` before any write. This continues to work correctly inside the background job.

**0.6 — Identity resolution hardened (8-step chain)**
- File: `excom/excom/doctype/omni_identity/omni_identity.py`
- Added Step 5: email alias lookup in `Omni Identity Alias` (was missing).
- Added Step 6: ERPNext reverse lookup via `Contact Phone` — if a Contact with this phone is already linked to an Omni Identity, bridge to that identity.
- Added Step 7: ERPNext reverse lookup via `Contact Email` — same bridge via email.
- Added auto-alias registration: when a found identity has a different primary phone/email than the inbound signal, auto-append the new value as an alias (source=Auto) so future lookups resolve directly via step 2/5.
- Added `_find_potential_duplicate()` helper: checks exact display_name match and last-10-digit phone match. When triggered, sets `needs_review=1` and `potential_duplicate_of` on the new record for supervisor review.
- New schema fields on `Omni Identity`: `needs_review` (Check, hidden), `potential_duplicate_of` (Link to Omni Identity, hidden).
- Added `Auto` as a source option on `Omni Identity Alias`.

### Complete 8-Step Resolution Chain

```
1. normalized_phone on Omni Identity
2. alias phone/WhatsApp in Omni Identity Alias
3. channel_user_id in Omni Identity Channel
4. normalized_email on Omni Identity
5. alias email in Omni Identity Alias              [NEW]
6. Contact Phone -> Omni Identity Link bridge      [NEW]
7. Contact Email -> Omni Identity Link bridge      [NEW]
8. Create new identity; flag potential duplicates  [NEW]
```

### Why This Matters

The ERPNext reverse lookup (steps 6-7) is Excom's structural advantage over Rocket.Chat, Chatwoot, and every standalone messaging platform. A Contact record in ERPNext typically has both phone and email, acting as the cross-channel bridge. A person who messages via WhatsApp (phone match) and later sends an email (no phone match) will be resolved to the same Omni Identity as long as a Contact with both identifiers is already linked.

### Impacted Modules

- `excom/excom/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py` — status assignment fix
- `excom/hooks.py` — scheduler_events and doc_events activated
- `excom/excom/utils/webhook.py` — async post(), `_process_webhook_payload` extracted, top-level `datetime` import
- `excom/excom/doctype/omni_identity/omni_identity.py` — hardened `resolve_identity`, new `_find_potential_duplicate` helper
- `excom/excom/doctype/omni_identity/omni_identity.json` — new `needs_review` and `potential_duplicate_of` fields
- `excom/excom/doctype/omni_identity_alias/omni_identity_alias.json` — added `Auto` source option

### Migration Implications

- `bench --site <site> migrate` required to apply new Omni Identity fields (`needs_review`, `potential_duplicate_of`).
- Alias source field now has four options (Inbound, Auto, Manual, Import). Existing aliases with old source values remain valid.
- All other changes are runtime-only (hooks, scheduler, service logic). No existing data is modified.

---

## Implementation Update (2026-02-24, Linked ERP Entities in Sidebar)

Linked ERP Entities from the Omni Identity doctype are now displayed in the right sidebar when a conversation is opened.

### What Changed

- **Backend**: Added `get_linked_entities(omni_identity)` whitelisted API in `excom.excom.api.chat` that fetches all Omni Identity Link child table rows and returns `{linked_doctype, linked_name, role, title}` for each. The `title` is derived from the linked doc (e.g. `lead_name`, `customer_name`) for human-readable display.
- **Frontend**: Added `useLinkedEntities(omniIdentity)` hook that calls the API. OmniIdentityPanel and MobileContactView now render a "Linked ERP Entities" section with loading/empty/list states. Each entity is shown as a clickable card (doctype badge, title, role) that opens the Frappe Form in a new tab via `/app/{doctype}/{docname}`.

### Impacted Modules

- `excom/excom/api/chat.py` — new `get_linked_entities` method
- `frontend/src/hooks/useLinkedEntities.ts` — new hook
- `frontend/src/components/OmniIdentityPanel.tsx` — Linked ERP Entities section
- `frontend/src/components/mobile/MobileContactView.tsx` — same section for mobile

---

## Implementation Update (2026-02-24, Phase 1 — Backend Architecture Hardening)

Complete implementation of Phase 1 from the roadmap: schema improvements, validation hardening, performance indexes, unified error handling, WhatsApp service layer extraction, event bus, and settings consolidation.

### What Changed

#### 1.1 Schema Improvements
- **WhatsApp Message** — added lifecycle timestamp fields (`queued_at`, `sent_at`, `delivered_at`, `read_at`, `failed_at`, `failure_reason`), raw body field (`body`), and media metadata fields (`media_id`, `media_url`, `media_mime_type`, `media_sha256`, `media_caption`, `media_filename`)
- **WhatsApp Account** — added health/rate-limit fields (`rate_limit_per_second`, `rate_limit_per_day`, `last_health_check`, `health_status`, `token_expires_at`, `webhook_url`). Marked `token`, `url`, `version`, `phone_id` as required.
- **WhatsApp Templates** — added approval lifecycle fields (`submitted_at`, `approved_at`, `rejected_at`, `rejection_reason`, `paused_at`). Webhook handler now populates these on template status events from Meta.
- **Bulk WhatsApp Message** — added `failed_count` and `completed_at` fields
- **WhatsApp Notification** — added missing field definitions for `enable_delay`, `delay_value`, `delay_unit`

#### 1.2 Validation Improvements
- **E.164 phone validation** — new `excom/excom/utils/phone.py` with `validate_phone_number()` and `normalize_phone()` utilities. Enforces 7-15 digit length, country code requirement.
- **WhatsApp Templates** — button count validation: max 3 Quick Reply, max 2 CTA (Visit Website/Call Phone)
- **WhatsApp Notification** — delay field validation: `delay_value` must be positive int, `delay_unit` required when delay is enabled
- **Bulk WhatsApp Message** — full validation: template requirement when `use_template` checked, `template_variables` JSON format validation, scheduled_time past-date warning, `failed_count` tracking in `create_single_message`
- **WhatsApp Recipient List** — deduplication logic in `validate()` and `import_list_from_doctype()`. Duplicate phone numbers are silently removed with a user notification.

#### 1.3 Performance
- **Database indexes** — patch `add_performance_indexes.py` adds 11 composite indexes across `tabWhatsApp Message`, `tabWhatsApp Notification Log`, `tabExcom Message`, and `tabExcom Thread`
- **Chat API** — verified `get_threads` is a zero-join, zero-subquery single table scan. Already optimal.

#### 1.4 Architecture
- **Unified error handling** — new `excom/excom/utils/errors.py` with `ExcomError` base class and subclasses: `ExcomValidationError`, `ExcomProviderError`, `ExcomRateLimitError`, `ExcomIdentityError`. All include structured context and `.log()` for Error Log persistence.
- **Event bus** — `thread_service.py` now calls `frappe.publish_realtime()` for four events: `excom:message_received`, `excom:message_sent`, `excom:message_status_updated`, `excom:thread_updated`. Frontend can subscribe for real-time inbox updates.
- **WhatsApp service layer** — new `excom/excom/services/whatsapp_service.py` with `send_text_message()`, `send_template_message()`, `send_media_message()`, `wa_update_delivery_status()`. Single entry point for all WhatsApp Cloud API calls. Includes credential resolution for both Excom Channel Account and legacy WhatsApp Account. Thread service's `_send_whatsapp()` replaced with service layer calls.
- **Settings consolidation** — `WhatsApp Account.on_update()` now syncs `is_default_incoming`/`is_default_outgoing` flags to `WhatsApp Settings` single DocType bidirectionally. Also auto-computes `webhook_url`.

### Impacted Modules
- `excom/excom/doctype/whatsapp_message/whatsapp_message.json` — 13 new fields
- `excom/excom/doctype/whatsapp_account/whatsapp_account.json` — 7 new fields, 4 fields marked required
- `excom/excom/doctype/whatsapp_account/whatsapp_account.py` — validate + sync_to_settings
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.json` — 6 new fields
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.py` — button count validation
- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.json` — 3 new field definitions
- `excom/excom/doctype/whatsapp_notification/whatsapp_notification.py` — delay field validation
- `excom/excom/doctype/bulk_whatsapp_message/bulk_whatsapp_message.json` — 2 new fields
- `excom/excom/doctype/bulk_whatsapp_message/bulk_whatsapp_message.py` — full validation + failed_count tracking
- `excom/excom/doctype/whatsapp_recipient_list/whatsapp_recipient_list.py` — deduplication
- `excom/excom/utils/phone.py` — new file
- `excom/excom/utils/errors.py` — new file
- `excom/excom/services/whatsapp_service.py` — new file
- `excom/excom/services/thread_service.py` — event bus + service layer integration, removed `_send_whatsapp`
- `excom/excom/utils/webhook.py` — template lifecycle timestamps, WA message status timestamps
- `excom/patches/v1_0/add_performance_indexes.py` — new patch
- `excom/patches.txt` — registered new patch

### Migration Implications
- `bench --site <site> migrate` required to apply all schema changes (new fields on WhatsApp Message, Account, Templates, Notification, Bulk WhatsApp Message) and run the index patch.
- All new fields are additive — no existing data is broken.
- The `token`, `url`, `version`, `phone_id` fields on WhatsApp Account are now required. Existing accounts missing these values will need to be updated before they can be saved again.

---

## Phase 2: Frontend Completion and Real-Time UX (2026-02-24)

### What Changed

#### 2.1 Realtime Message Updates
- Created `useRealtimeMessages(threadId, onNewMessage)` hook — subscribes to `excom:message_received`, `excom:message_sent`, `excom:message_status_updated` via `useFrappeEventListener`.
- Created `useRealtimeThreads(onThreadUpdate)` hook — subscribes to `excom:thread_updated` to auto-refresh thread list.
- Wired into `App.tsx` (thread list), `ChannelTabsView.tsx` and `MobileChannelView.tsx` (message list). Messages and threads now update in realtime without polling.

#### 2.2 Contact Data Enrichment
- Extended `get_threads` API to JOIN `tabOmni Identity` (for `primary_email`, `image/avatar_url`) and `tabUser` (for `assigned_to_name`, `assigned_to_avatar`).
- Added `_enrich_company()` helper that batch-fetches company names from linked Contact/Lead/Customer via `Omni Identity Link`.
- `ExcomThread` type extended with `primary_email`, `avatar_url`, `company`, `assigned_to_name`, `assigned_to_avatar`.
- `useContacts.ts` now populates `contactAvatar`, `contactInfo.email`, `contactInfo.company`, `assignedTo.name/avatar` from API data instead of hardcoded empty strings.

#### 2.3 File Attachment Support
- Extended `send_message` API with `message_type` and `media_url` parameters.
- Created `useFileUpload` hook using `useFrappeFileUpload` — handles file picker, drag-and-drop, upload progress.
- Wired Paperclip and Image buttons in `ChannelTabsView.tsx` (with drag-and-drop zone) and `MobileChannelView.tsx`.
- Media messages (image, document, video, audio) are uploaded via Frappe File API then sent via `send_outbound_message`.

#### 2.4 AI Assistant — Stub API
- Created `get_ai_suggestions(thread_id)` API that computes data from message history:
  - `suggested_replies`: Last 3 unique outbound messages as suggestions.
  - `summary`: Message count + last activity timestamp.
  - `next_actions`: Derived from linked ERP entity statuses (Lead follow-ups, Customer reviews).
  - `insights`: Average response time, engagement ratio, best contact time from inbound message timestamps.
- Created `useAISuggestions` hook.
- Replaced all hardcoded data in `AIAssistantDrawer.tsx` and `MobileAIDrawer.tsx` with live API data.
- "Generate More Suggestions" button calls `refresh()`.
- Suggested reply click calls `onUseSuggestion` callback.
- "Start" on actions opens relevant Frappe form in new tab.

#### 2.5 Quick Action Buttons
- **View in ERPNext**: Opens first linked entity's form (or Omni Identity if none linked).
- **Send Email**: Opens `mailto:` link using `contactInfo.email`.
- **Schedule Meeting**: Opens `/app/event/new` with `party_type=Contact&party=<name>` if a Contact link exists.
- **Take Over**: Created `assign_thread(thread_id, user)` API. Wired button in both `ChannelTabsView.tsx` and `MobileChannelView.tsx`.

#### 2.6 Mobile Navigation Completion
- Added `MobileContactsList.tsx` component — searchable alphabetical list of all Omni Identities with channel badges.
- Added "Calls" tab placeholder with "Coming Soon" empty state.
- Updated `MobileApp.tsx` with `activeTab` state and `handleTabSwitch` for Chats/Calls/Contacts bottom navigation.
- Added view types `contacts_tab` and `calls_tab`.

#### 2.7 Account Switching
- Fixed sync issue: `ChannelTabsView` now accepts `activeAccountId` prop from parent and syncs internal state via `useEffect`.
- When an account card is clicked in `OmniIdentityPanel`, the parent's `selectedAccountId` updates, which propagates to `ChannelTabsView`, which updates its internal state and triggers `useMessages` re-fetch.
- Channel tab also switches to match the selected account's channel.

#### 2.8 Response Time Calculation
- Created `get_response_metrics(omni_identity)` API (dedicated endpoint).
- `get_conversation_stats` already returns `avg_response_time_seconds` computed from actual message timestamps.
- `formatResponseTime` utility already formats seconds into "~Xm" / "~Xh" strings.
- Verified wiring in `OmniIdentityPanel` and `MobileContactView`.

#### 2.9 Message Input UX Polish
- **Optimistic Send**: Messages appear immediately in the chat with "Sending..." indicator. Removed on API success (real message replaces via refresh) or restored to input on failure.
- **Error Recovery**: On send failure, message text is restored to input field and error toast shown.
- **Enter/Shift+Enter**: Enter sends, Shift+Enter preserved for newline. `e.preventDefault()` added to stop form submission.
- **Character Limit**: WhatsApp 4096 character limit enforced. Counter appears when >90% of limit reached, turns red at limit.

### New Files
- `frontend/src/hooks/useRealtimeMessages.ts`
- `frontend/src/hooks/useRealtimeThreads.ts`
- `frontend/src/hooks/useFileUpload.ts`
- `frontend/src/hooks/useAISuggestions.ts`
- `frontend/src/components/mobile/MobileContactsList.tsx`

### Modified Files
- `excom/excom/api/chat.py` — extended `get_threads`, `send_message`; added `get_ai_suggestions`, `assign_thread`, `get_response_metrics`
- `frontend/src/App.tsx` — realtime thread updates, account switching props
- `frontend/src/types/index.ts` — extended `ExcomThread` interface
- `frontend/src/hooks/useContacts.ts` — enriched contact data mapping
- `frontend/src/components/ChannelTabsView.tsx` — realtime, file upload, optimistic send, char count, account sync, take over
- `frontend/src/components/mobile/MobileChannelView.tsx` — realtime, file upload, optimistic send, take over
- `frontend/src/components/AIAssistantDrawer.tsx` — live AI data
- `frontend/src/components/mobile/MobileAIDrawer.tsx` — live AI data
- `frontend/src/components/mobile/MobileApp.tsx` — tab navigation, contacts/calls views
- `frontend/src/components/OmniIdentityPanel.tsx` — functional quick action buttons
- `frontend/src/components/mobile/MobileContactView.tsx` — functional quick action buttons

### Migration Implications
- No schema migrations required. All changes are API and frontend.
- New API endpoints are additive and backward-compatible.

---

## Phase 3: Core Data Structures & Productivity Tools (2026-02-28)

### 3.1 Canned Responses

**New DocType: `Excom Canned Response`**
- Fields: `title` (Data, reqd), `shortcode` (Data, reqd, unique), `content` (Small Text, reqd), `category` (Select: General/Sales/Support/Billing), `channel` (Select: All/WhatsApp/Email/Instagram), `is_global` (Check, default 1)
- Controller validates shortcode: strips leading `/`, lowercases, replaces spaces with `_`
- Permissions: System Manager (full CRUD), All (create, read, write for personal responses)

**New API: `get_canned_responses(search, channel)`**
- Returns global responses + current user's personal responses (is_global=0 && owner=session.user)
- Filters by channel (includes "All" responses) and search (matches title, shortcode, content)

**Frontend Integration:**
- `useCannedResponses(search, channel)` hook — fetches when popover is open (null search = skip)
- `CannedResponsePopover` component — appears above input when user types `/`, supports arrow key navigation + Enter/Tab to select + Esc to close
- Wired into `ChannelTabsView` and `MobileChannelView` input areas
- Selecting a canned response replaces the entire input text with the response content

### 3.2 Internal Notes

**DocType Change: `Excom Message`**
- Added `is_internal` (Check, default 0) field — marks messages as internal team-only notes

**New API: `send_internal_note(thread_id, content)`**
- Creates an Excom Message with `is_internal=1`, `direction=Outbound`, `delivery_status=Read`
- Does NOT trigger any outbound channel delivery (no WhatsApp/email sent)
- Publishes `excom:message_received` realtime event with `is_internal: True`

**Backend Change: `get_messages`**
- Now includes `m.is_internal` in SELECT — all notes returned inline with regular messages

**Frontend Integration:**
- `Message` interface extended with `isInternal?: boolean`
- `useMessages` hook maps `is_internal` → `isInternal`
- Message/Note toggle added to input area (both desktop and mobile)
- Note mode: amber-themed UI, Lock icon, "Only visible to your team" indicator
- Internal notes render as centered amber-colored cards with "Internal Note" badge
- Send button changes to amber StickyNote icon in note mode

### 3.3 ERP Invoices Tab

**New API: `get_related_invoices(omni_identity)`**
- Fetches `Omni Identity Link` to find linked Customers and Suppliers
- Queries `Sales Invoice` (for Customers) and `Purchase Invoice` (for Suppliers)
- Returns `{sales_invoices: [...], purchase_invoices: [...]}` with posting_date, grand_total, outstanding_amount, status, currency
- Excludes cancelled invoices (docstatus != 2), limits to 20 most recent per type

**Frontend Integration:**
- `useRelatedInvoices(omniIdentity)` hook
- `OmniIdentityPanel`: converted to tabbed interface ("Profile" + "Invoices" tabs)
  - Profile tab contains all existing content
  - Invoices tab shows Sales and Purchase Invoices with status badges (Paid=green, Overdue=red, Unpaid=orange)
  - Each invoice links to ERPNext form, shows outstanding amounts
  - Tab badge shows total invoice count
- `MobileContactView`: invoices section added before Quick Actions (flat list, no tabs)

### New Files (Phase 3)
- `excom/excom/doctype/excom_canned_response/excom_canned_response.json`
- `excom/excom/doctype/excom_canned_response/excom_canned_response.py`
- `excom/excom/doctype/excom_canned_response/__init__.py`
- `excom/excom/doctype/excom_canned_response/test_excom_canned_response.py`
- `frontend/src/hooks/useCannedResponses.ts`
- `frontend/src/hooks/useRelatedInvoices.ts`
- `frontend/src/components/CannedResponsePopover.tsx`

### Modified Files (Phase 3)
- `excom/excom/doctype/excom_message/excom_message.json` — added `is_internal` field
- `excom/excom/api/chat.py` — added `get_canned_responses`, `send_internal_note`, `get_related_invoices`; updated `get_messages` SELECT
- `frontend/src/types/index.ts` — added `isInternal` to Message, `is_internal` to ExcomMessage
- `frontend/src/hooks/useMessages.ts` — maps `is_internal`
- `frontend/src/components/ChannelTabsView.tsx` — canned response popover, note mode toggle, internal note rendering
- `frontend/src/components/mobile/MobileChannelView.tsx` — canned response popover, note mode toggle, internal note rendering
- `frontend/src/components/OmniIdentityPanel.tsx` — tabbed Profile/Invoices UI
- `frontend/src/components/mobile/MobileContactView.tsx` — invoices section

### Migration Implications (Phase 3)
- `bench migrate` required — creates `tabExcom Canned Response` table and adds `is_internal` column to `tabExcom Message`
- All new APIs are additive and backward-compatible
- No existing data affected

---

## Phase 3.1: Email Channel Integration (Gmail API)

### What Changed
Added full email channel integration using Gmail API as the storage backend. Email bodies are NEVER stored in the Frappe database. Only lightweight metadata pointers are persisted locally.

### Why It Changed
Email is a critical communication channel for B2B and customer support workflows. The Gmail API approach was chosen over IMAP/POP because:
1. Bodies remain in Gmail, eliminating DB bloat and storage costs
2. Gmail's search syntax is vastly superior to local full-text search
3. OAuth2 via Connected App provides secure, token-refreshing authentication
4. History API enables efficient incremental sync (no re-scanning)

### Architecture Decisions
- **Gmail as storage backend** — Excom only stores metadata (Message-ID, Thread-ID, From, Subject, Date, 150-char snippet)
- **On-demand body fetch** — Full body retrieved from Gmail API when agent opens an email, cached in session memory only
- **Deleted email handling** — Stored metadata shown with "no longer available" notice if Gmail returns 404
- **Search delegation** — All search goes through Gmail API query syntax, no local full-text indexing
- **Thread key format** — `email:{account_name}:{gmail_thread_id}` maps Gmail threads to Excom Threads

### Impacted Modules
- `Excom Channel Account` DocType — new email/Gmail OAuth2 fields
- `Excom Message` DocType — added "Email" to message_type options
- `thread_service.py` — email channel guard on `send_outbound_message()`
- `hooks.py` — email polling added to scheduler_events["all"]

### New Files (Phase 3.1)
- `excom/excom/services/gmail_service.py` — Gmail API wrapper (OAuth2, metadata, body, send, history, search)
- `excom/excom/channels/email/__init__.py`
- `excom/excom/channels/email/inbound.py` — Polling job (incremental + initial sync)
- `excom/excom/channels/email/outbound.py` — Send via Gmail API
- `excom/excom/api/email.py` — API endpoints (get_email_body, search_emails, send_email, etc.)
- `excom/patches/v1_0/seed_email_channel.py` — Seed email Excom Channel record
- `frontend/src/components/EmailMessageCard.tsx` — Expandable email card component
- `frontend/src/components/EmailCompose.tsx` — Email compose form
- `frontend/src/hooks/useEmailBody.ts` — On-demand body fetch hook

### Modified Files (Phase 3.1)
- `excom/excom/doctype/excom_channel_account/excom_channel_account.json` — email OAuth2 fields
- `excom/excom/doctype/excom_channel_account/excom_channel_account.js` — authorize button handler
- `excom/excom/doctype/excom_message/excom_message.json` — "Email" added to message_type
- `excom/excom/services/thread_service.py` — email channel guard
- `excom/excom/api/chat.py` — content_json in get_messages for email
- `excom/hooks.py` — email polling scheduler
- `excom/patches.txt` — seed_email_channel patch registered
- `frontend/src/types/index.ts` — isEmail, contentJson, rawDirection fields
- `frontend/src/hooks/useMessages.ts` — email type mapping
- `frontend/src/components/ChannelTabsView.tsx` — EmailMessageCard + EmailCompose integration

### Migration Implications (Phase 3.1)
- `bench migrate` required — adds email fields to `tabExcom Channel Account`, "Email" option to message_type, seeds email channel
- Patch `seed_email_channel` creates the `Excom Channel` record for email
- No existing data affected — all changes are additive

---

## Email Authorization & Sync Fix (2026-02-24)

### What Changed
Fixed the email channel account authorization and sync flow — after OAuth2 authorization via Google, the `email_authorized` flag was never being set to `1`, causing the scheduler to skip the account entirely.

### Root Cause
1. **`email_authorized` never updated** — The OAuth2 callback stores the token in Frappe's Token Cache, but nothing detected this and set `email_authorized = 1` on the Excom Channel Account.
2. **Redirect URI had port 8002** — The Connected App's `redirect_uri` included `:8002`, which doesn't work through the Cloudflare tunnel (`dev.mevabite.com` maps to `localhost:8002` without exposing the port).
3. **No manual sync trigger** — After authorization, the user had to wait for the scheduler with no way to trigger an immediate sync.

### What Was Fixed
- **`ExcomChannelAccount.check_email_authorization()`** — New whitelisted method that checks Frappe's `has_token()` API and updates `email_authorized` field. Called from client script on form refresh to auto-detect authorization after OAuth callback.
- **`ExcomChannelAccount._sync_email_authorized()`** — Called from `on_update` to keep `email_authorized` in sync with actual token state.
- **Client script** — Auto-calls `check_email_authorization` on `refresh` for email accounts. If authorization is newly detected, reloads the form. Added "Check Gmail Connection" and "Sync Now" buttons under "Email" button group.
- **`poll_all_email_accounts()`** — Uses `frappe.cache` for rate-limiting instead of unreliable `modified` timestamp. Respects `email_poll_interval_minutes` setting per account.
- **Connected App redirect URI** — Fixed from `https://dev.mevabite.com:8002/...` to `https://dev.mevabite.com/...`.

### Impacted Modules
- `excom/excom/doctype/excom_channel_account/excom_channel_account.py`
- `excom/excom/doctype/excom_channel_account/excom_channel_account.js`
- `excom/excom/channels/email/inbound.py`

### Important: Google Cloud Console
The redirect URI must also be updated in Google Cloud Console OAuth2 credentials to match: `https://dev.mevabite.com/api/method/frappe.integrations.doctype.connected_app.connected_app.callback/<connected_app_name>`

---

## Auto-Cleanup of Stale Inbound-Only Omni Identities (2026-02-24)

### What Changed
Added a configurable daily cleanup job that removes Omni Identities created by email sync when no Frappe user has ever replied to them. This prevents the database from accumulating noise from inbound-only contacts that were never engaged.

### Architecture Decision: Why Threads Stay
Excom Thread is the CRM entity — it carries status, assignment, priority, unread count, and drives the thread list sidebar. Removing it would break assignment tracking, the conversation list, real-time notifications, and Phase 3.5/4 features (teams, routing, SLA). The storage overhead per thread is ~500 bytes. Message stubs (~200 bytes each, no body) enable dedup during sync, local search, unread count, and internal notes (which don't exist in Gmail). Bodies remain in Gmail and are fetched on-demand via the API.

### New DocType
- **Excom Settings** (Single) — `auto_cleanup_enabled` (Check), `cleanup_retention_days` (Int, default 30), `cleanup_channels` (Small Text, default "email")

### New Files
- `excom/excom/doctype/excom_settings/` — Single DocType definition and controller
- `excom/excom/tasks/__init__.py`
- `excom/excom/tasks/cleanup.py` — `cleanup_stale_identities()` daily job

### Cleanup Logic
1. Read Excom Settings — if `auto_cleanup_enabled` is off, return immediately
2. Compute cutoff date = now − `cleanup_retention_days`
3. Find Omni Identities where:
   - Has an `Omni Identity Channel` with `channel_type` in the configured cleanup channels
   - `creation < cutoff`
   - `status = 'Active'`
   - NO `Excom Message` exists with `direction = 'Outbound'` and `created_by_user` is set (agent reply)
4. For each stale identity: delete messages → threads → identity (cascade order)
5. On next email sync, Gmail recreates them if the source emails still exist

### Protection Rule
Even a single outbound message from any Frappe user to an Omni Identity (on any channel) permanently protects it from cleanup.

### Impacted Modules
- `hooks.py` — `cleanup_stale_identities` added to `scheduler_events["daily"]`

### Migration Implications
- `bench migrate` required — creates the `Excom Settings` Single DocType table
- No existing data affected — cleanup is disabled by default

---

## Phase 3 Remaining Features (2026-03-02)

### A. Message Features (3.6)

#### A1. Message Pinning
- **Fields added to `Excom Message`**: `is_pinned` (Check), `pinned_by` (Link to User)
- **API endpoints** (`chat.py`): `pin_message`, `unpin_message`, `get_pinned_messages`
- **Frontend**: Right-click context menu on message bubbles, collapsible pinned messages section above message list, `usePinnedMessages` hook
- **Realtime**: `excom:message_pinned` event

#### A2. Reply/Quote
- **API**: `send_message` now accepts `reply_to` parameter, `send_outbound_message` pipes it through
- **`get_messages`**: SQL JOINs to `Excom Message` (as `rt`) and `User` (as `ru`) to return `reply_to_content`, `reply_to_direction`, `reply_to_sender`
- **Frontend**: Reply bar above input showing quoted message, quoted block above message bubble, context menu "Reply" action

#### A3. Message Reactions
- **Field added to `Excom Message`**: `reactions` (JSON, default `{}`)
- **API**: `toggle_reaction(message_name, emoji)` — stores as `{"emoji": ["user1", "user2"]}`
- **Frontend**: `ReactionBar` component below message bubbles, emoji picker in context menu (8 common emojis)
- **Realtime**: `excom:message_reaction` event

#### Shared: `MessageContextMenu` component
- Right-click (context menu) on any message bubble
- Actions: React, Reply, Pin/Unpin
- Emoji sub-picker for reactions

### B. Conversation Tags (3.4)

#### New DocTypes
- **`Excom Tag`**: `tag_name` (Data, unique), `color` (Color), `description` (Small Text). Named by field `tag_name`.
- **`Excom Thread Tag`**: Child table (`istable=1`): `tag` (Link to Excom Tag), `added_by` (Link to User), `added_on` (Datetime)
- **`Excom Thread`**: Added `tags` (Table, Excom Thread Tag) field

#### API endpoints (`chat.py`)
- `get_tags()` — all Excom Tag records
- `add_thread_tag(thread_id, tag_name)` — auto-creates tag if missing
- `remove_thread_tag(thread_id, tag_name)`
- `get_thread_tags(thread_id)`
- `get_threads` enriched with `_enrich_tags` batch fetcher

#### Frontend
- **Tag chips on thread cards** (`ChatThreadList.tsx`): colored dots/badges with tag name
- **`TagManager` component**: popover in `ChannelTabsView` header for adding/removing tags
- **Tag filter in `LeftSidebar`**: click-to-toggle tag filter chips
- **Hooks**: `useTags()`, `useThreadTags(threadId)` in `hooks/useTags.ts`
- **Types**: `ThreadTag` interface added to `ExcomThread` and `UnifiedContact`

### C. Web Chat Widget (3.2)

#### C1. Backend
- **Channel**: `webchat` added to `setup.py` CHANNELS seed list
- **`Excom Visitor Session` DocType**: `session_token` (unique), `status`, `channel_account`, `thread`, `omni_identity`, visitor fields, metadata
- **API** (`api/webchat.py`, all `allow_guest=True`):
  - `get_config(account_id)` — widget branding/settings
  - `create_session(account_id, visitor_name, ...)` — creates Omni Identity + Thread + Session, returns token
  - `send_visitor_message(session_token, content)` — ingests as inbound message
  - `get_visitor_messages(session_token, after)` — returns conversation history
  - `end_session(session_token)` — marks session ended

#### C2. Channel Account Config
- **Fields added to `Excom Channel Account`** (depends_on `webchat`):
  - `webchat_widget_title`, `webchat_welcome_message`, `webchat_offline_message`
  - `webchat_color` (Color), `webchat_position` (Select)
  - `webchat_prechat_fields` (JSON config)

#### C3. Embeddable Widget
- **Self-contained vanilla JS IIFE** at `excom/public/widget/excom-chat.js` (~14KB)
- **Shadow DOM isolation** — all styles scoped, no conflicts with host page
- **Embed code**:
  ```html
  <script src="{site}/assets/excom/widget/excom-chat.js"
    data-account="{channel_account_name}"
    data-site="{site_url}">
  </script>
  ```
- **Features**: floating chat button, pre-chat form (name/email/phone), message list with timestamps, polling every 3s, optimistic message rendering, `localStorage` session persistence
- **Architecture**: No build step required — plain JS with DOM manipulation and Shadow DOM

### Migration Requirements
- `bench migrate` — creates `Excom Tag`, `Excom Thread Tag`, `Excom Visitor Session` DocTypes, adds fields to `Excom Message`, `Excom Thread`, `Excom Channel Account`
- `bench migrate` seeds the `webchat` channel

---

## Realtime Fix & Widget Upgrade (Post-Phase 3)

### What Changed
1. **`publish_realtime` ordering** — All `publish_realtime` calls across `thread_service.py`, `chat.py`, `webchat.py`, and `email/inbound.py` now use `after_commit=True` and are placed **before** the explicit `frappe.db.commit()`. This guarantees that the commit triggers the realtime broadcast, fixing silent failures in background-job contexts (RQ workers) where publish-after-commit didn't properly reach Socket.IO via Redis pubsub.

2. **Frontend polling fallback** — `useRealtimeMessages` (10s) and `useRealtimeThreads` (15s) now include interval-based polling so messages and thread lists stay current even if Socket.IO is temporarily disconnected.

3. **Preact web chat widget** — The vanilla JS IIFE widget has been replaced with a proper Preact + esbuild build. Source lives in `excom/public/widget/src/` with `build.mjs` for bundling. The output remains `excom-chat.js` with Shadow DOM isolation.

### Why It Changed
Inbound messages processed in background jobs (via `frappe.enqueue`) were not reliably publishing realtime events. The root cause was `publish_realtime` being called after `frappe.db.commit()` without `after_commit=True`, which in background workers could fail silently. Moving the publish before the commit with `after_commit=True` ensures the commit itself triggers the broadcast.

### Impacted Modules
- `excom/services/thread_service.py` — inbound ingestion + delivery status updates
- `excom/api/chat.py` — internal notes
- `excom/api/webchat.py` — visitor messages
- `excom/channels/email/inbound.py` — email ingestion
- `frontend/src/hooks/useRealtimeMessages.ts` — polling fallback
- `frontend/src/hooks/useRealtimeThreads.ts` — polling fallback
- `excom/public/widget/` — Preact source + build tooling

---

## Mobile OAuth site URL (2026-03-28)

### What changed
- `excom.excom.api.mobile.get_client_id` no longer uses `frappe.utils.get_url()` for `site_url`. That helper appends `webserver_port` when the configured host has no explicit port, which produced URLs like `https://dev.example.com:8002` behind a reverse proxy while browsers use `https://dev.example.com`.
- New `_mobile_public_base_url()` mirrors Frappe host resolution **without** the port-suffix block, prefers `X-Forwarded-Proto` + `Host` when resolving from the request, and supports overrides.

### Why it changed
Mobile PKCE and native apps must use the same public origin users type in the browser (TLS on 443), not the internal Werkzeug/gunicorn port.

### Impacted modules
- `excom/excom/api/mobile.py`
- `excom/excom/doctype/excom_settings/` — optional field `mobile_public_site_url` (and validation)

### Migration
- `bench migrate` to add `mobile_public_site_url` on Excom Settings.
- Optional: set `mobile_public_site_url` in `site_config.json` without migrating (same key as conf override).

---

## Frappe push relay browser path (2026-03-28)

### What changed
- New `excom.excom.api.notification.get_frappe_relay_push_config` whitelisted API: same-origin fetch of Firebase web config + VAPID from the **configured** `push_relay_server_url`, plus automatic `notification_relay.api.auth.get_credential` when **Push Notification Settings** has no `api_key` / secret yet (mirrors desk “first relay use” registration).
- `frontend/public/frappe-push-notification.js` (bundled in Excom) calls this API instead of hitting `notification_relay.api.get_config` directly from the browser (Raven pattern).

### Why it changed
- Mis-setting `push_relay_server_url` to the **same** ERPNext host makes the browser call `get_config` on a site without the `notification_relay` app → HTTP 417 / “App notification_relay is not installed”.
- Server-side fetch gives clearer errors and avoids CORS assumptions. It does **not** remove the need for a valid relay base URL: `push_relay_server_url` must still point at a host that actually runs the relay.

### Impacted modules
- `excom/excom/api/notification.py`
- `frontend/public/frappe-push-notification.js` → production bundle under `excom/public/excom/assets/`

---

## WhatsApp TEXT header variables — full implementation (2026-03-29)

### What changed

#### Phase 1 — Slot counting and fetch normalization
- **WhatsApp Templates** — new JSON field `header_variable_samples` (array of strings), populated from Meta on **Fetch templates** via `example.header_text`, mirroring body samples.
- **Meta fetch** — HEADER/BODY/BUTTONS `type` and header `format` are compared case-insensitively so lowercase `text` from the API still sets `header_type`/`header` correctly.
- **Slot counting** — `header_variable_slot_count()` uses `{{n}}` in `header` first; if none, uses the length of `header_variable_samples`. The inbox list API (`get_whatsapp_templates`) and send path (`_build_template_components`) use the same helper so UI field count matches Meta (fixes “needs 4 variables, you sent 3” when the header slot was dropped).
- **Broadcast** — rejection when the template has any TEXT header variables now uses `header_variable_slot_count` (includes samples), not only placeholders in `header` text.

#### Phase 2 — Doctype layout, server-side hardening, frontend parity
- **Doctype JSON layout** — `header_variable_samples` moved from body section to header section (next to `header` field). `sample` (Attach) renamed to “Header Media Sample”, now only visible for IMAGE/DOCUMENT (`depends_on` excludes TEXT). `header_variable_samples` made read-only (populated only via Meta fetch).
- **`get_header()` fix** — Removed broken TEXT fallback that split `self.sample` (a file path!) by comma as header examples. Now uses only `header_variable_samples` JSON for TEXT header examples.
- **`fetch()` linked accounts persistence** — `_merge_template_linked_account()` was appending to in-memory doc but `upsert_doc_without_hooks` only persisted `buttons` children. Added `_persist_linked_accounts()` called after upsert to insert any missing linked account rows.
- **API `header_sample_variables`** — `get_whatsapp_templates` now returns `header_sample_variables` array alongside `sample_variables` (body). Previously the frontend used body samples as header variable hints.
- **Template preview** — `_build_template_preview` now includes header text with variables filled in (header values first, then body), not just body text.
- **Case normalization** — All `header_type` comparisons in `send_template_to_thread` and `_build_template_components` use `.upper()` for consistent behavior regardless of how Meta returns the format string.
- **Frontend** — `WhatsAppTemplatePicker` shows header text in preview with variable substitution, uses `header_sample_variables` for input hints, displays total variable count (header + body) in template list cards, and normalizes all `header_type` checks with `.toUpperCase()`.

### Impacted modules
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.json` — field order, sample visibility, header_variable_samples read-only
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.py` — get_header(), _persist_linked_accounts(), fetch()
- `excom/excom/whatsapp_template_utils.py` — is_text_header, get_header_variable_samples, header_variable_slot_count
- `excom/excom/api/chat.py` — get_whatsapp_templates, _build_template_components, _build_template_preview, send_template_to_thread
- `excom/excom/doctype/excom_broadcast/excom_broadcast.py` — header variable detection
- `frontend/src/components/WhatsAppTemplatePicker.tsx` — header preview, sample hints, case normalization
- `excom/patches/v1_0/add_whatsapp_template_header_variable_samples.py`, `excom/patches.txt`

### Migration
- `bench migrate` adds the `header_variable_samples` column and applies field layout changes. Re-run **Fetch templates** from Meta so `header_variable_samples` fills for existing TEXT-header templates.

---

## WhatsApp named parameter support and failure capture (2026-03-29)

### What changed

#### Named placeholder detection
- **Root cause**: `ordered_placeholder_numbers` only matched `{{1}}`, `{{2}}` (digit-only). Meta API v21+ supports named params like `{{sale_start_date}}`. Templates using named params had 0 detected variables — the inbox showed no header inputs, and the send path thought no body params were needed.
- **`whatsapp_template_utils.py`**: Added `ordered_placeholder_names(text)` matching `{{...}}` (named or positional), `placeholder_count(text)`, and `body_variable_slot_count(template)`. `header_variable_slot_count` now uses general placeholder detection. `ordered_placeholder_numbers` kept for backward compatibility.
- **`chat.py`**: `get_whatsapp_templates` uses `body_variable_slot_count` instead of `ordered_placeholder_numbers`. `_build_template_components` uses both slot count helpers. Body component is emitted when `b_n > 0` (not when positional list is non-empty).

#### Preview with named params
- **`_replace_placeholders(text, values)`**: Replaces all `{{...}}` patterns in first-appearance order, works with both `{{1}}` and `{{customer_name}}`.
- **Frontend**: `replacePlaceholders()` JS equivalent using global regex; header and body previews both use it.

#### Failure capture
- **Before**: On `ExcomProviderError` or `ExcomRateLimitError`, `frappe.throw()` fired before creating `Excom Message` — `failure_reason` was never stored, user only saw a toast.
- **After**: `Excom Message` is always created with `delivery_status = "Failed"` and `failure_reason` populated, then `frappe.throw()` propagates the error to the frontend. Users can audit failed sends from the message timeline.

#### Meta fetch — named header examples
- `_extract_header_samples()` handles both `header_text` (positional) and `header_text_named_params` (named) from Meta’s example block, so `header_variable_samples` is always populated regardless of which parameter style the template uses.

#### Broadcast
- `_wa_template_variable_count` now uses `body_variable_slot_count` (handles named params) instead of counting body samples.

### Impacted modules
- `excom/excom/whatsapp_template_utils.py` — `ordered_placeholder_names`, `placeholder_count`, `body_variable_slot_count`, `_get_field`
- `excom/excom/api/chat.py` — `get_whatsapp_templates`, `_build_template_components`, `_replace_placeholders`, `_build_template_preview`, `send_template_to_thread` (failure capture)
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.py` — `_extract_header_samples`, fetch() named params
- `excom/excom/doctype/excom_broadcast/excom_broadcast.py` — `_wa_template_variable_count`
- `frontend/src/components/WhatsAppTemplatePicker.tsx` — `replacePlaceholders`, preview

### Migration
- No schema changes. Code-only. Re-run **Fetch templates** from Meta after deploy to refresh `header_variable_samples` for any templates using named parameters.

---

## Broadcast Schedule DateTimePicker (March 2026)

### What changed
- Replaced raw `<input type="datetime-local">` in BroadcastPage Step 3 with a custom `DateTimePicker` component.
- New reusable UI components: `ui/popover.tsx` (Radix wrapper), `ui/date-time-picker.tsx` (calendar + time picker).
- Added `@radix-ui/react-popover` dependency.

### Why
- Native `datetime-local` input is ugly on dark themes, inconsistent across browsers, and hard to use on mobile.
- The new picker provides a visual calendar grid, scrollable hour/minute columns, "Now" and "Clear" quick actions, and a confirm step to prevent accidental selections.
- Matches the existing zinc dark theme and blue accent color system.

### Architecture
- `DateTimePicker` is a self-contained component using `date-fns` (already installed) for date math.
- The `min` prop disables past dates in the calendar grid. Time validation happens on confirm.
- Output format is `YYYY-MM-DDTHH:MM` (datetime-local spec), which `frappeDatetimeFromLocalInput` already converts to Frappe datetime format (`YYYY-MM-DD HH:MM:SS`).
- The `Popover` primitive wraps Radix with the project's dark theme styling and can be reused across the app.

### Server-side schedule handling (verified correct)
- `broadcast.py` `create_broadcast`: validates `scheduled_at > now_datetime()` before creating doc.
- `excom_broadcast.py` `before_submit`: re-validates future datetime on submit.
- `broadcast_schedule.py` `process_due_scheduled_broadcasts`: runs every minute via `scheduler_events["all"]`, atomically transitions Scheduled -> Queued via conditional SQL UPDATE, then enqueues `execute_broadcast`.
- The datetime format pipeline is: browser local datetime -> `frappeDatetimeFromLocalInput` -> `"YYYY-MM-DD HH:MM:00"` -> Frappe `get_datetime()` -> stored as site-timezone datetime.

### Impacted modules
- `frontend/src/components/BroadcastPage.tsx` — import + usage of DateTimePicker
- `frontend/src/components/ui/date-time-picker.tsx` — new component
- `frontend/src/components/ui/popover.tsx` — new Radix wrapper
- `frontend/package.json` — `@radix-ui/react-popover` added

---

## WABA Auto-Linking and Template Update Fix (March 2026)

### What changed

#### Auto-link accounts by WABA Business ID
- **Before**: Users had to manually add linked WhatsApp accounts to each template via Table MultiSelect. Primary/secondary distinction added confusion.
- **After**: On validate, `_auto_link_same_business_accounts()` queries all active WhatsApp channel accounts sharing the same `wa_business_id` and auto-links them. The `linked_whatsapp_accounts` field is now read-only.
- **fetch()**: Grouped by `wa_business_id` so each WABA is queried once from Meta. All accounts under that business ID are linked to every template in one pass. Fixed the bug where `return` inside the first account's loop prevented fetching for other accounts.

#### Fix update_template() 400 error
- **Root cause 1**: `get_header()` wrapped `header_text` as `[h_samples]` producing `[["val"]]`, but Meta expects a flat list `["val"]` (unlike `body_text` which is nested `[["v1","v2"]]`). Fixed to `header["example"] = {"header_text": h_samples}`.
- **Root cause 2**: `update_template()` fired on every save, even metadata-only changes (adding linked accounts). Now guarded by `_has_content_changed()` which checks only content fields (`template`, `header`, `footer`, `category`, `header_type`, samples, buttons).
- **Root cause 3**: Error handling was `except Exception as e: raise e` with no details. Now uses `_extract_meta_error()` to parse Meta's response body (`error_user_msg`, `message`, `error_user_title`) and shows a clear error in Frappe UI + logs to Error Log.

#### Centralized Meta error extraction
- `_extract_meta_error()` helper replaces ad-hoc error parsing in `after_insert`, `update_template`, `on_trash`, and `fetch`. Safely handles missing response, non-JSON bodies, and partial error objects.

### Architecture decisions
- Templates belong to a WABA (business ID), not a phone number. Multiple phone numbers under the same WABA share identical templates. This is Meta's model and Excom now mirrors it.
- `_has_content_changed()` checks `_TEMPLATE_CONTENT_FIELDS` (frozenset) plus button child table diffs. Only content changes trigger Meta API calls.
- `linked_whatsapp_accounts` made read-only to prevent manual edits that could desync from the auto-link logic.

### Impacted modules
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.py` — auto-link, content change detection, header_text format, error handling, fetch grouping
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.json` — `linked_whatsapp_accounts` read_only

### Migration
- Run `bench migrate` after deploy (JSON schema change on `linked_whatsapp_accounts`).
- Run **Fetch templates** to re-link all accounts by business ID automatically.

---

## VIDEO and LOCATION Header Support (March 2026)

### What changed
- Added VIDEO and LOCATION to the `header_type` Select options on WhatsApp Templates DocType.
- `sample` Attach field now shows for IMAGE, VIDEO, and DOCUMENT (not TEXT or LOCATION).
- Backend `get_header()` handles LOCATION as `{"type":"HEADER","format":"LOCATION"}` with no example payload.
- `validate()` triggers media upload for VIDEO headers alongside IMAGE/DOCUMENT.
- `_build_template_components()` in `chat.py` builds `video.link` parameters for VIDEO headers and `location` objects for LOCATION headers.
- New `header_location` parameter on `send_template_to_thread` API: JSON with `latitude`, `longitude`, `name`, `address`.
- Frontend `WhatsAppTemplatePicker` adds VIDEO upload UI (MP4), LOCATION input form (lat/lng/name/address), template card badges for both types.

### Meta API formats
- **VIDEO send**: `{"type":"header","parameters":[{"type":"video","video":{"link":"..."}}]}`
- **LOCATION send**: `{"type":"header","parameters":[{"type":"location","location":{"latitude":"...","longitude":"...","name":"...","address":"..."}}]}`
- **LOCATION create**: `{"type":"header","format":"location"}` (no parameters at creation time)

### Impacted modules
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.json` -- header_type options, sample depends_on
- `excom/excom/doctype/whatsapp_templates/whatsapp_templates.py` -- validate media upload, get_header LOCATION
- `excom/excom/api/chat.py` -- send_template_to_thread, _build_template_components, _build_template_preview
- `excom/excom/doctype/excom_broadcast/excom_broadcast.py` -- error message update
- `frontend/src/components/WhatsAppTemplatePicker.tsx` -- VIDEO/LOCATION UI
- `frontend/src/components/BroadcastPage.tsx` -- needsMedia includes VIDEO

### Migration
- Run `bench migrate` after deploy (header_type options changed).

---

## Phase 8: Sticker Message Support

### What changed
Full sticker support added: new DocType for sticker management, send/receive sticker messages via WhatsApp, frontend sticker picker UI.

### Architecture

#### Excom Sticker DocType (`excom/excom/doctype/excom_sticker/`)
- Fields: `sticker_name`, `pack` (grouping), `is_animated`, `enabled`, `sticker_file` (Attach, .webp), `media_id` (auto-populated from Meta upload), `whatsapp_account`, `file_size_kb`.
- Validation: `.webp` format only, static <= 100 KB, animated <= 500 KB.
- Auto-upload: on `after_insert` and when `sticker_file` changes, the controller uploads the file to Meta's media API (`POST /{phone_id}/media`) and stores the returned `media_id`.
- `media_id` is the preferred sending mechanism (faster delivery, no re-download by Meta).

#### Outbound sticker flow
1. Frontend `StickerPicker` calls `send_message` with `message_type="Sticker"` and `sticker_name`.
2. `chat.send_message` loads the Excom Sticker doc and passes `sticker_name` to `thread_service.send_outbound_message`.
3. `thread_service` routes to `whatsapp_service.send_sticker_message`.
4. `send_sticker_message` builds the Meta payload: `{"type":"sticker","sticker":{"id":"<media_id>"}}` (preferred) or `{"sticker":{"link":"<url>"}}` (fallback).
5. An `Excom Message` with `message_type="Sticker"` and `media_file` is created.

#### Inbound sticker flow
1. Webhook `type_map` now maps `"sticker"` to `"Sticker"`.
2. Sticker payloads contain media ID just like images; they hit the existing `_download_media` branch (tuple includes `"sticker"`), downloading the webp file and storing it as a Frappe File.
3. `Excom Message` is created with `message_type="Sticker"` and `media_file` pointing to the downloaded webp.

#### Frontend
- `StickerPicker` component: grid of sticker thumbnails grouped by pack, with search. Click sends immediately.
- Message bubble renders stickers as `<img>` with `w-32 h-32 object-contain` (desktop) or `w-24 h-24` (mobile).
- Sticker button (yellow `Sticker` icon) in composer toolbar, visible only for WhatsApp channels.

#### API
- `get_stickers(pack="")`: Returns all enabled stickers with optional pack filter + list of available packs.
- `send_message` updated: accepts `sticker_name` param for `Sticker` message type.

### Meta API format
- **Send (by media ID)**: `{"messaging_product":"whatsapp","to":"...","type":"sticker","sticker":{"id":"<MEDIA_ID>"}}`
- **Send (by URL)**: `{"messaging_product":"whatsapp","to":"...","type":"sticker","sticker":{"link":"<URL>"}}`
- **Supported formats**: `.webp` only. Static max 100 KB, animated max 500 KB.

### Impacted modules
- `excom/excom/doctype/excom_sticker/` (new) -- DocType JSON, controller
- `excom/excom/doctype/excom_message/excom_message.json` -- added "Sticker" to message_type options
- `excom/excom/services/whatsapp_service.py` -- new `send_sticker_message()`
- `excom/excom/services/thread_service.py` -- routes Sticker type to sticker service
- `excom/excom/api/chat.py` -- `send_message` accepts sticker_name, new `get_stickers` endpoint
- `excom/excom/utils/webhook.py` -- sticker in type_map + media download branch
- `frontend/src/components/StickerPicker.tsx` (new) -- sticker picker UI
- `frontend/src/components/ChannelTabsView.tsx` -- sticker button + sticker bubble rendering
- `frontend/src/components/mobile/MobileChannelView.tsx` -- sticker button + sticker bubble rendering
- `frontend/src/hooks/useMessages.ts` -- "sticker" type mapping

### Migration
- Run `bench migrate` after deploy (new Excom Sticker DocType + Sticker added to Excom Message message_type).
- Patch: `excom.patches.v1_0.add_sticker_message_type`.

---

## Phase 9: WhatsApp & Platform Analytics Dashboard

### What changed
Comprehensive analytics dashboard integrating Meta's WhatsApp Analytics APIs with internal Excom metrics. Provides messaging volume, conversation analytics, pricing/cost breakdowns, and agent performance — all visualized with recharts.

### Architecture

#### Meta Analytics APIs used
All APIs target `GET /<WABA_ID>?fields=<metric>.<params>` on Meta's Graph API:
- **Messaging Analytics** (`analytics`): Messages sent/delivered per phone number, by day/month, with country breakdowns.
- **Conversation Analytics** (`conversation_analytics`): Conversation counts and costs by category (MARKETING, UTILITY, AUTHENTICATION, SERVICE), type (REGULAR, FREE_TIER, FREE_ENTRY_POINT), and direction.
- **Pricing Analytics** (`pricing_analytics`): Volume and cost breakdowns by pricing category and type, including tier information.
- **Template Analytics** (`template_analytics`): Per-template sent/delivered/read/clicked metrics via `/<WABA_ID>/template_analytics`.

#### Service Layer (`excom/excom/services/whatsapp_analytics.py`)
- `_resolve_waba_credentials()`: Extracts WABA ID, token, base_url, version from Excom Channel Account.
- `_call_analytics_api()`: Generic GET request builder for analytics endpoints.
- `get_messaging_analytics()`: Fetches `analytics` field with configurable granularity (DAY/HALF_HOUR/MONTH).
- `get_conversation_analytics()`: Fetches `conversation_analytics` with dimensions and category filters.
- `get_template_analytics()`: Fetches template performance via dedicated endpoint.
- `get_pricing_analytics()`: Fetches `pricing_analytics` with dimension breakdowns.
- `get_account_overview()`: Aggregates all three metrics into a single dashboard payload.

#### API Layer (`excom/excom/api/analytics.py`)
Whitelisted endpoints for the frontend:
- `get_analytics_overview(account_name, days)` — Combined overview from Meta + internal.
- `get_messaging_analytics(account_name, start, end, granularity)` — Raw messaging data.
- `get_conversation_analytics(account_name, start, end, granularity, dimensions, categories)` — Conversation data.
- `get_template_analytics(account_name, template_ids, start, end)` — Template performance.
- `get_pricing_analytics(account_name, start, end, granularity, dimensions)` — Cost data.
- `get_internal_metrics(days)` — Internal Excom DB metrics (message volume by day/channel/type, active threads, avg response time, agent performance).
- `get_wa_accounts()` — Account picker data.

#### Frontend (`frontend/src/components/AnalyticsPage.tsx`)
Four-tab dashboard built with recharts:
1. **Overview**: KPI cards (messages sent, conversations, active threads, avg response time) + area/pie/bar charts.
2. **Messages**: Meta messaging analytics (sent vs delivered) + internal volume (inbound vs outbound).
3. **Conversations**: Category pie chart, daily stacked bar, breakdown table.
4. **Costs**: Daily spending trend, cost by category bar chart, pricing breakdown table.

Features: period selector (7/14/30/90 days), account switcher (multi-account), refresh button, responsive grid layout.

#### Dependencies
- `recharts` added to frontend for chart visualization.

### Impacted modules
- `excom/excom/services/whatsapp_analytics.py` (new) — Meta API analytics service
- `excom/excom/api/analytics.py` (new) — Whitelisted API endpoints
- `frontend/src/components/AnalyticsPage.tsx` (new) — Dashboard UI
- `frontend/src/components/LeftSidebar.tsx` — Analytics nav button
- `frontend/src/App.tsx` — Analytics page routing
- `frontend/package.json` — recharts dependency

### Migration
- No schema changes. No `bench migrate` required for analytics.
- Meta's template analytics requires opt-in: `POST /<WABA_ID>?is_enabled_for_insights=true` (one-time).

---

## Phase 10: Delivery Watchdog & Message Retry

### What changed
Added automatic delivery failure detection and a WhatsApp-style retry mechanism for failed messages.

### Architecture

#### Delivery Watchdog (Background Job)
- **Service**: `excom/excom/services/delivery_watchdog.py`
- **Scheduler**: Runs every minute via Frappe's `scheduler_events["all"]`
- **Logic**: Queries outbound WhatsApp messages in `Queued` or `Sent` status where `provider_timestamp` is older than 10 minutes (up to 24 hours back). Marks them as `Failed` with a descriptive `failure_reason`. Publishes `excom:message_status_updated` realtime event for each.
- **Broadcast awareness**: Increments `failed_count` on the parent `Excom Broadcast` when a broadcast message times out.
- **Batch limit**: Processes up to 200 stale messages per run to avoid long-running jobs.

#### Meta Error Webhook Capture
- **File**: `excom/excom/utils/webhook.py` → `_update_message_status()`
- **Change**: Now extracts `errors[0]` from Meta's status webhook (error code, title, details) and passes `failure_reason` to `update_delivery_status()`.

#### Delivery Status Update Enhancement
- **File**: `excom/excom/services/thread_service.py` → `update_delivery_status()`
- **Change**: Added `failure_reason` parameter. When status is `Failed` and a reason is provided, sets `failure_reason` field on the `Excom Message`.

#### Retry API
- **Endpoint**: `excom.excom.api.chat.retry_message`
- **Method**: POST with `message_name` parameter
- **Logic**: Loads the failed `Excom Message`, determines the message type (Text, Image, Video, Audio, Document, Sticker, Template), re-sends via the same provider function, and updates the existing message record with the new `provider_message_id` and status. No duplicate message is created.
- **Error handling**: Catches `ExcomProviderError` and `ExcomRateLimitError`, re-marks as Failed with the error as `failure_reason`.

#### Frontend
- **Types**: `Message.status` extended with `"failed"` and `"queued"`. Added `failureReason` field.
- **useMessages.ts**: Maps `Failed` → `"failed"`, `Queued` → `"queued"` delivery statuses. Maps `failure_reason` from backend.
- **DeliveryIcon**: Shows red `AlertCircle` for failed, spinning `Loader2` for queued.
- **DeliveryTimer**: Live countdown component shown below `Sent`/`Queued` messages. Displays "Checking delivery… M:SS" with a spinning icon. Counts down from 10 minutes based on message timestamp. Disappears when timer reaches 0 (watchdog will mark Failed).
- **Message bubbles** (desktop + mobile): Failed messages get a distinct red-tinted bubble (`bg-red-950/40 border border-red-500/40`). Below the bubble, the failure reason is shown truncated, with a "Retry" button (spinning loader during retry).
- **Retry handler**: Calls `retry_message` API, refreshes message list on success or failure.

### Impacted modules
- `excom/excom/services/delivery_watchdog.py` (new) — Watchdog + retry service
- `excom/excom/services/thread_service.py` — `update_delivery_status()` failure_reason param
- `excom/excom/utils/webhook.py` — Meta error detail extraction
- `excom/excom/api/chat.py` — `retry_message` endpoint, `failure_reason` in get_messages
- `excom/hooks.py` — Scheduler registration
- `frontend/src/types/index.ts` — Extended status/type unions, failureReason field
- `frontend/src/hooks/useMessages.ts` — Failed/Queued status mapping
- `frontend/src/components/ChannelTabsView.tsx` — DeliveryIcon, retry button, failed bubble styling
- `frontend/src/components/mobile/MobileChannelView.tsx` — Same for mobile

### Migration
- No schema changes required. `failure_reason` and `delivery_status` fields already exist on `Excom Message`.
- The scheduler job auto-registers on next `bench restart`.

---

## Phase 11: Email Attachment Direct Download

### What changed
Wired email attachment chips in the inbox to download files directly from Gmail API to the user's browser — no server-side storage.

### Architecture
- **Pointer-based (unchanged)**: Excom stores only Gmail message IDs. Email bodies and attachments live in Gmail and are fetched on-demand.
- **Download flow**: Frontend `fetch()` → Frappe API → `gmail_service.get_attachment()` → Gmail API → binary bytes → `frappe.local.response` (download) → browser blob → `<a download>` click → user's filesystem.
- **No files touch the server disk**. Bytes stream through Python memory only.

#### Backend
- **`excom/excom/api/email.py`** → `get_email_attachment()`: Added `filename` parameter so the browser receives the correct `Content-Disposition` header with the original filename.

#### Frontend
- **`frontend/src/components/EmailMessageCard.tsx`** → `AttachmentChip` component:
  - Replaces the static `<div>` chips with clickable `<button>` elements
  - On click: fetches the binary via `fetch()`, creates a blob URL, triggers download via hidden `<a download>` element
  - Shows spinner during download, download icon on hover
  - Fallback: opens in new tab if fetch fails

### Impacted modules
- `excom/excom/api/email.py` — filename parameter on download endpoint
- `frontend/src/components/EmailMessageCard.tsx` — AttachmentChip component, Download icon

---

## Phase 12: Advanced Filters, Spam/Archive/Delete Actions

### What changed
1. Server-side channel + account filtering (was client-side channel-only)
2. Date range filter for conversations
3. Swipe-to-reveal actions: Spam, Archive, Delete (System Manager only)

### Architecture

#### Backend — `excom/excom/api/chat.py`
- **`get_threads()`**: Added `channel`, `account`, `date_from`, `date_to` parameters. Filtering now happens server-side in SQL for accuracy. Query condition also excludes `Spam` status alongside `Closed`.
- **`mark_spam(thread_id)`**: Sets thread status to `Spam`, flags `Omni Identity.is_spam = 1` so future messages from this sender can be auto-filtered.
- **`archive_thread(thread_id)`**: Sets thread status to `Closed` (hides from inbox).
- **`delete_thread(thread_id)`**: Deletes all `Excom Message` records for the thread, then deletes the `Excom Thread`. Restricted to System Manager role.
- **`get_channel_accounts()`**: Returns active channel accounts for the filter dropdown.

#### Schema changes
- **`Excom Thread`**: Added `Spam` to status options (`Open|Pending|Closed|Spam`).
- **`Omni Identity`**: Added `is_spam` (Check field) under new "Flags" section.
- **Patch**: `add_spam_status_and_identity_flag` reloads both doctypes.

#### Frontend
- **`useContacts.ts`**: `useThreads()` now passes `channel`, `account`, `date_from`, `date_to` to the API.
- **`App.tsx`**: Added `selectedAccountFilter`, `dateFrom`, `dateTo` state. Removed client-side channel filtering (now server-side).
- **`LeftSidebar.tsx`**: Channel dropdown now shows per-account sub-options. Added collapsible date range filter with native date inputs and clear button.
- **`ChatThreadList.tsx`**: Right-click (context menu) reveals action buttons that slide in from the right:
  - **Spam** (amber): Marks sender as spam, hides thread
  - **Archive** (blue): Archives thread (status → Closed)
  - **Delete** (red, System Manager only): Permanently deletes thread and messages
  - Click the swiped card again to dismiss actions

### Impacted modules
- `excom/excom/api/chat.py` — new endpoints, enhanced get_threads
- `excom/excom/doctype/excom_thread/excom_thread.json` — Spam status option
- `excom/excom/doctype/omni_identity/omni_identity.json` — is_spam field
- `excom/patches.txt` + new patch file
- `frontend/src/hooks/useContacts.ts` — new filter params
- `frontend/src/App.tsx` — state, props
- `frontend/src/components/LeftSidebar.tsx` — account filter, date range filter
- `frontend/src/components/ChatThreadList.tsx` — swipe actions

### Migration
- `bench migrate` required for Excom Thread (Spam status) and Omni Identity (is_spam field).

---

## Phase 13: Email UI Overhaul & Conversation Context Menu

### What changed

#### Email Message Card (`EmailMessageCard.tsx`) — full rewrite
1. **Attachment cards** replaced plain text chips with rich cards: colored file-type icon (PDF=red, image=green, spreadsheet=green, video=purple, audio=pink, archive=amber), filename, extension badge, file size, and a circular download button with hover state.
2. **Email action toolbar**: Reply, Reply All, Forward buttons in expanded view. Star toggle and Print/Open-in-new-tab button on the right.
3. **Sender avatar**: Initials circle next to the sender name in the header for visual consistency with the thread list.
4. **Better metadata layout**: From/To/Cc/Date with right-aligned labels in a structured grid.
5. **Attachment section footer**: Shows total count and total size.

#### Conversation Context Menu (`ChatThreadList.tsx`) — full rewrite
1. **Replaced broken swipe mechanism** with a floating right-click context menu.
2. Context menu appears at cursor position via `onContextMenu`, with boundary clamping.
3. Menu shows contact name/info header, then action groups separated by dividers.
4. Actions: Mark as read (placeholder), Copy contact, Mark as spam, Archive, Delete (System Manager only).
5. Closes on outside click, Escape key, or scroll.
6. Loading spinners on destructive actions.

### Impacted modules
- `frontend/src/components/EmailMessageCard.tsx` — complete rewrite
- `frontend/src/components/ChatThreadList.tsx` — complete rewrite (swipe → context menu)

### Migration
- None (frontend-only changes).

---

## Phase 14 — UI Fixes & Feature Additions (2026-03-29)

### What changed

Seven items addressed in this phase:

#### 1. Sticker Dimension Validation
- Added `_validate_dimensions()` to `ExcomSticker.validate()` using `PIL.Image`.
- Validates that sticker files are exactly 512×512 pixels at upload time (before Meta API call).
- Throws a clear user-facing error with actual dimensions if wrong.

#### 2. StickerPicker Z-Index Fix
- `StickerPicker` was clipped by `overflow-hidden` on parent containers.
- Fixed by portaling it to `document.body` via `createPortal` with `position: fixed`.
- Button rect is captured via `useRef` to anchor the popup correctly.

#### 3. Collapsible Conversation List
- Added `isCollapsed` and `onToggleCollapse` props to `ChatThreadList`.
- When collapsed: renders a 48px strip with a ChevronRight expand button + unread badge.
- When expanded: header now shows a ChevronLeft collapse button.
- State persisted to `localStorage` as `excom_thread_list_collapsed`.

#### 4. Email Signature DocType + API
- New DocType: `Excom Email Signature` — per-user HTML signature with position option.
- Fields: `user` (Link, unique), `signature_html` (Text Editor), `is_active` (Check), `position` (Select).
- Two new API endpoints in `excom/api/email.py`: `get_my_signature()` and `save_my_signature()`.
- Migration patch: `excom.patches.v1_0.add_email_signature_doctype`.

#### 5. Settings Page Full Rework
- `SettingsPage.tsx` rewritten as a split-pane layout with left-nav + content panel.
- Sections: General (OAuth), Email Signatures, Notifications, Branding, Accounts, Keyboard Shortcuts, Canned Responses, Auto-Reply.
- Notification section includes sound toggle (persisted to `localStorage` as `excom_sound_enabled`).

#### 6. Email Signature in Compose
- `EmailCompose.tsx` now fetches the user's active signature via `get_my_signature()`.
- Appends it below the compose body when sending, with an inline preview shown in the UI.

#### 7. Auto-Assignment on Thread Open
- `get_messages()` in `chat.py` now auto-claims unassigned threads when a user opens them.
- Calls `_auto_claim_thread(thread, frappe.session.user)` after fetching messages.
- Returns `auto_claimed_by` in the response.
- Frontend (`useMessages.ts`) handles the new `{messages, auto_claimed_by}` response shape (backwards-compat with old array shape).
- `ChannelTabsView.tsx` shows a toast "Thread assigned to you" and refreshes the thread list.

#### 8. Channel/Account Filter Rebuild
- Removed the broken DropdownMenu-based channel filter from `LeftSidebar.tsx`.
- Replaced with two-tier chip-based filter:
  - Tier 1: horizontal pill chips for All / WhatsApp / Email / Instagram / Calls.
  - Tier 2: smaller account chips shown when a specific channel is selected.
- Channel keys now use proper capitalization matching the DB: "WhatsApp", "Email", etc.

#### 9. Notification Sound When Focused
- Removed `!document.hasFocus()` guard from `playNotificationSound()` call in `useNotifications.ts`.
- Sound is now controlled by `excom_sound_enabled` localStorage key (default: on).
- Desktop notification (browser pop-up) still only fires when tab is not focused.

### Impacted modules
- `excom/excom/doctype/excom_sticker/excom_sticker.py` — dimension validation
- `excom/excom/doctype/excom_email_signature/` — new DocType
- `excom/excom/api/email.py` — signature API endpoints
- `excom/excom/api/chat.py` — auto-assign on `get_messages`
- `excom/patches.txt` + `excom/patches/v1_0/add_email_signature_doctype.py`
- `frontend/src/components/ChannelTabsView.tsx` — sticker portal, auto-assign toast, onRefreshThreads prop
- `frontend/src/components/ChatThreadList.tsx` — collapse/expand feature
- `frontend/src/components/LeftSidebar.tsx` — two-tier chip filter
- `frontend/src/components/SettingsPage.tsx` — complete rewrite
- `frontend/src/components/EmailCompose.tsx` — signature integration
- `frontend/src/hooks/useMessages.ts` — new response shape handling
- `frontend/src/hooks/useNotifications.ts` — sound without focus guard
- `frontend/src/App.tsx` — isThreadListCollapsed state, onRefreshThreads prop

### Migration
- Run `bench migrate` to create the `Excom Email Signature` table.

---

## Gmail OAuth2 Token Fix (2026-03-29)

### Problem
Email accounts were failing with "No valid OAuth2 token for {account}. Please re-authorize."
after their Google access tokens expired. Root causes identified:

1. **No refresh_token stored**: The Token Cache had an `access_token` but an empty
   `refresh_token`. Google only returns a `refresh_token` on first-ever authorization,
   or when `prompt=consent` is explicitly requested. Without a `refresh_token`, Frappe's
   `get_active_token()` cannot auto-refresh an expired token.

2. **User context mismatch in OAuth callback**: The authorize button was calling
   `initiate_web_application_flow(user=email_connected_user)` which created the Token Cache
   state for one user. But Frappe's callback validates `token_cache.user == frappe.session.user`,
   causing "Invalid token state!" when the logged-in user differed from `email_connected_user`.

3. **Aggressive email_authorized = 0 flip**: When token refresh failed, `get_access_token`
   was setting `email_authorized = 0`, which removed the account from polling. The user saw
   the checkbox flap between 0 and 1 on every save.

4. **`_force_token_refresh` bug**: Used `connected_app.token_endpoint` (wrong field name)
   instead of `connected_app.token_uri`, so the manual refresh always failed silently.

5. **Authorize button hidden when authorized**: The `depends_on` condition was
   `!doc.email_authorized` — meaning once authorized, there was no button to re-authorize.

### What changed

#### `excom/excom/doctype/excom_channel_account/excom_channel_account.py`
- Added `authorize_email_account()` whitelist method which:
  - Sets `email_connected_user = frappe.session.user` before initiating OAuth, ensuring
    the Frappe callback's state check always passes.
  - Adds `prompt=consent` to the Connected App's `query_parameters` if not present,
    guaranteeing Google always returns a fresh `refresh_token`.
  - Returns the authorization URL (does not redirect; client redirects via JS).

#### `excom/excom/doctype/excom_channel_account/excom_channel_account.js`
- `email_authorize_button`: Replaced the raw `initiate_web_application_flow` call with
  a call to the new `authorize_email_account` backend method.
- Removed `user: frm.doc.email_connected_user` from the OAuth call (was the cause of
  the "Invalid token state" error).
- Removed requirement that `email_connected_user` is filled before authorizing.
- Added orange dashboard headline when account is not authorized.

#### `excom/excom/doctype/excom_channel_account/excom_channel_account.json`
- Changed `email_authorize_button` `depends_on` from `!doc.email_authorized` to just
  `channel == email` — the button now always appears so re-authorization is always possible.

#### `excom/excom/services/gmail_service.py`
- `get_access_token()`: Removed `frappe.db.set_value("email_authorized", 0)` on failure.
  The flag is no longer flipped by runtime sync failures — only by the `_sync_email_authorized`
  controller method which reads token existence.
- `_force_token_refresh()`: Fixed field name from `connected_app.token_endpoint` to
  `connected_app.token_uri`. Also changed to use `get_token_cache()` instead of
  `get_active_token()` to avoid double-refresh attempt; saves `expires_in` from response.

### Impacted modules
- `excom/excom/doctype/excom_channel_account/excom_channel_account.py`
- `excom/excom/doctype/excom_channel_account/excom_channel_account.js`
- `excom/excom/doctype/excom_channel_account/excom_channel_account.json`
- `excom/excom/services/gmail_service.py`

### Migration
- Run `bench migrate` — the `email_authorize_button` field `depends_on` change is a DocType
  metadata change picked up automatically.

### Re-authorization steps (for existing accounts)
1. Open the `Excom Channel Account` form for the email account.
2. Click **Authorize Gmail Access** — the button now always appears regardless of current
   authorization status.
3. Complete the Google OAuth consent screen (you will see a permissions dialog because
   `prompt=consent` forces a fresh consent even for previously authorized accounts).
4. After redirect back to the form, `email_authorized` will be checked and syncing will resume.

### Anti-patterns to avoid
- Do NOT remove `prompt=consent` from the Connected App query parameters — without it,
  re-authorization will not issue a `refresh_token`, making tokens non-renewable.
- Do NOT pass `user=email_connected_user` to `initiate_web_application_flow` — use the
  session user and let the backend bind `email_connected_user` automatically.
- Do NOT set `email_authorized = 0` in runtime sync code — only the controller
  `_sync_email_authorized` should manage this flag.

---

## Email Threading, Timezone & Notification Fixes (2026-03-29)

### Problem 1: Email timestamps displayed in wrong timezone
Email `provider_timestamp` was created using `datetime.utcfromtimestamp()` which produces
a UTC-based naive datetime. Frappe stores all naive datetimes as if they're in the system
timezone (Asia/Kolkata). So a 10:00 UTC email was stored as 10:00 and displayed as 10:00 IST
instead of 15:30 IST.

Additionally, the frontend parsed Frappe datetime strings with `new Date()` which interprets
them in the browser's local timezone, causing further discrepancy when server and browser
timezones differ.

**Fix:**
- Backend: Changed `datetime.utcfromtimestamp()` → `datetime.fromtimestamp()` in
  `channels/email/inbound.py` so the email date is correctly converted to server timezone.
- Frontend: Created `utils/datetime.ts` with `parseFrappeDateTime()` that detects the
  server timezone from `frappe.boot.time_zone.system` and adjusts Date objects so
  `formatDistanceToNow` always shows correct relative times.
- Applied `parseFrappeDateTime` in `useContacts.ts` and `useMessages.ts`.

### Problem 2: Auto-update and notification tone not firing
Socket.IO realtime events (`excom:message_received`) drive both thread list refresh and
notification tones. When Socket.IO is unavailable, the 15s polling fallback refreshes data
but never triggered notification sounds.

**Fix:**
- `useNotifications.ts`: Added unread count tracking. When `totalUnread` increases between
  renders (detected via polling), plays the notification sound and shows a desktop notification.
  This means notifications work even with no socket connection.
- `useRealtimeThreads.ts`: Reduced polling interval from 15s → 10s. Added a
  `visibilitychange` listener to refresh immediately when the user switches back to the tab.

### Problem 3: Same person's emails split across multiple threads
Email inbound used `email:{account}:{gmail_thread_id}` as the thread key, creating a
separate Excom Thread for every Gmail conversation. Two emails with different subjects from
the same person appeared as separate entries.

**Fix:**
- Changed `_ingest_email_metadata` in `channels/email/inbound.py` to call `upsert_thread()`
  which uses `email:{account}:{omni_identity}` — one thread per person per account,
  matching the WhatsApp threading model.
- `gmail_thread_id` is still stored per-message in `content_json` so Gmail reply threading
  works correctly (outbound.py reads it from the latest message).
- Updated `channels/email/outbound.py` to extract `gmail_thread_id` from the most recent
  message's `content_json` instead of parsing it from `thread_key`.
- Migration patch `merge_email_threads_by_identity` consolidates all existing duplicate
  email threads per identity into a single thread.

### Impacted modules
- `excom/excom/channels/email/inbound.py` — threading + timezone
- `excom/excom/channels/email/outbound.py` — gmail_thread_id extraction
- `excom/patches/v1_0/merge_email_threads_by_identity.py` — data migration
- `excom/patches.txt` — patch registration
- `frontend/src/utils/datetime.ts` — new timezone-aware parser
- `frontend/src/hooks/useContacts.ts` — parseFrappeDateTime
- `frontend/src/hooks/useMessages.ts` — parseFrappeDateTime
- `frontend/src/hooks/useNotifications.ts` — polling-based notification
- `frontend/src/hooks/useRealtimeThreads.ts` — faster polling + visibility listener

### Migration
- Run `bench migrate` — applies the thread merge patch automatically.

### Anti-patterns to avoid
- Do NOT use `datetime.utcfromtimestamp()` for storing dates in Frappe — always use
  `datetime.fromtimestamp()` or `frappe.utils.now_datetime()` which are server-tz-aware.
- Do NOT use `new Date(frappeDateString)` directly on the frontend — always use
  `parseFrappeDateTime()` from `utils/datetime.ts`.
- Do NOT store `gmail_thread_id` in the `thread_key` — it prevents per-person grouping.
  Store it only in message `content_json`.

---

## Roadmap Cleanup — 2026-03-30

### What changed
Deleted completed phase spec files and obsolete audit documents from the repository.
Phases 0, 1, and 2 are fully implemented and no longer need active spec tracking.

### Files removed
- `roadmap/phase_0_critical_fixes.md` — Phase 0 COMPLETED (all 9 critical fixes verified)
- `roadmap/phase_0_validation_report.md` — Phase 0 validation (9/9 pass)
- `roadmap/phase_1_backend_hardening.md` — Phase 1 COMPLETED (schema, validation, indexes, service layer, event bus)
- `roadmap/phase_2_frontend_completion.md` — Phase 2 COMPLETED (realtime, enrichment, attachments, mobile nav)
- `yet_to_improve.md` — Pre-Phase 0 audit; 23/30 items fixed in Phase 0-1, remaining 7 addressed by Phase 2 or tracked in Phase 7 (security)
- `frontend_gaps_handbook.md` — Frontend gap tracker; nearly all fixed in Phase 2, remaining items (CallScreen, online presence, AI status) tracked in future phases

### Files updated
- `roadmap/README.md` — Stripped completed phases, updated dependency graph, removed obsolete references

### Why
Spec files for completed work add noise and risk stale documentation. Active roadmap now covers only Phase 3+ (remaining work). Historical decisions are preserved in this handbook.

### Email polling fix (same session)
Fixed `poll_all_email_accounts` in `channels/email/inbound.py`: added missing `job_id` parameter
to `frappe.enqueue()` call. Frappe requires `job_id` when `deduplicate=True`; the missing param
caused a `ValidationError` on every scheduler tick, silently preventing automatic email sync.
Also re-enabled the Frappe scheduler (`bench enable-scheduler`) which was disabled for the site.

### Phase 3 verification and cleanup (same session)
Full end-to-end codebase audit confirmed all Phase 3 subsections are implemented:
- **3.1 Email** — gmail_service, inbound/outbound adapters, 5 API endpoints, scheduler hook, 3 frontend components
- **3.2 Web Chat** — Preact widget (`public/widget/`), guest API (`api/webchat.py`), CORS middleware, Visitor Session doctype, channel seed, pre-chat form. MVP-complete; deferred: file upload in widget, sound, Socket.IO (uses 3s polling), routing module, offline handling
- **3.3 Canned Responses** — `Excom Canned Response` doctype, `get_canned_responses` API, `CannedResponsePopover` with `/` trigger
- **3.4 Tags** — `Excom Tag` + `Excom Thread Tag` doctypes, 4 tag APIs, `TagManager` component, sidebar filtering via `useTags`
- **3.5 Internal Notes** — `is_internal` field, `send_internal_note` API, message/note toggle, amber styling
- **3.6 Message Features** — `reactions`, `is_pinned`/`pinned_by`, `reply_to` fields; emoji picker, reaction bar, pin/unpin API + context menu, reply/quote preview. Desktop fully wired; mobile not at full parity

Deleted `roadmap/phase_3_omnichannel_expansion.md`, updated `roadmap/README.md`.

---

## Phase A: Security Essentials (COMPLETED)

### What changed

**A.1 — Webhook HMAC Validation** (`excom/excom/utils/webhook.py`)
- `post()` now validates `X-Hub-Signature-256` against `wa_app_secret` stored on Excom Channel Account (Password field, encrypted at rest). Tries all active WhatsApp accounts; rejects with 403 if no secret matches.
- Graceful degradation: if no accounts have `wa_app_secret` set, HMAC is skipped (prevents breaking existing accounts during rollout).
- `get()` now returns explicit HTTP 403 for missing or unmatched verify tokens (previously used `frappe.throw` which returned 417).
- New field: `wa_app_secret` (Password) on Excom Channel Account JSON.

**A.2 — Input Sanitization** (`excom/excom/api/chat.py`)
- `_sanitize_message()` helper: runs `frappe.utils.sanitize_html()` + enforces per-channel max length (WhatsApp: 4096, Email: 100k, WebChat: 10k).
- Applied to `send_message()` and `send_internal_note()` before any storage or dispatch.

**A.3 — Rate Limiting** (`excom/excom/api/chat.py`)
- `send_message`: 30 req/min per user via `@rate_limit(key="user", limit=30, seconds=60)`.
- `send_internal_note`: 30 req/min per user.
- `get_messages`: 120 req/min per user.
- `get_threads`: 60 req/min per user.
- Uses Frappe's built-in `frappe.rate_limiter.rate_limit` (Redis-backed).

**A.4 — Role-Based Access** (`excom/excom/doctype/excom_thread/excom_thread.py`, `hooks.py`)
- Added `has_permission()` method on ExcomThread controller: Excom User can only access threads assigned to them or their team. Excom Manager / System Manager have full access.
- Registered `has_permission` and `permission_query_conditions` hooks in `hooks.py` so Frappe list views, APIs, and direct access all respect the permission model.
- Existing role structure preserved: Excom Manager = full access, Excom User = restricted to own/team threads.

**A.5 — Token Expiry Monitoring** (`excom/excom/tasks/token_monitor.py`, `hooks.py`)
- New daily scheduled task `check_token_expiry()`: checks email OAuth tokens (Token Cache) and WhatsApp token presence.
- Sends Frappe Notification Log alerts to all Excom Manager users when tokens are missing or revoked.
- Registered in `hooks.py` under `scheduler_events.daily`.

### Impacted modules
- `excom/excom/utils/webhook.py` — HMAC validation, 403 rejection
- `excom/excom/api/chat.py` — sanitization, rate limiting
- `excom/excom/doctype/excom_thread/excom_thread.py` — has_permission, permission_query_conditions
- `excom/excom/doctype/excom_channel_account/excom_channel_account.json` — wa_app_secret field
- `excom/hooks.py` — permission hooks, scheduler task
- `excom/excom/tasks/token_monitor.py` — new file

### Migration implications
- `bench migrate` adds `wa_app_secret` column to Excom Channel Account. Existing accounts work unchanged — HMAC validation is skipped until a secret is configured.

---

## Codebase Audit — Syntax & Semantic Error Fixes (2026-03-30)

### What changed

Systematic audit of all frontend ↔ backend field mappings, API permission checks, and type definitions. Found and fixed 30+ issues across 10 files.

### Category 1: Wrong Field Names (Silent Failures)

| File | Bug | Fix |
|------|-----|-----|
| `api/analytics.py` (lines 310, 320) | `enabled: 1` filter — field does not exist on Excom Channel Account | Changed to `status: "Active"` |
| `api/broadcast.py` (lines 319, 324) | `channel_type` filter + fields — field does not exist | Changed to `channel` |
| `services/delivery_watchdog.py` (line 147) | `template_doc.language` — field is `language_code` | Changed to `language_code`, default `"en_US"` |
| `utils/template_utils.py` (line 15) | `"WhatsApp Template"` (singular) — DocType is `"WhatsApp Templates"` | Fixed to plural; also added missing `import frappe` |

### Category 2: Missing Permission Checks (Security)

Added `_check_excom_access()` to **every** `@frappe.whitelist()` endpoint that was missing it:

- **`api/chat.py`**: `get_messages`, `send_message`, `get_stickers`, `mark_read`, `mark_unread`, `get_linked_entities`, `get_conversation_stats`, `get_ai_suggestions`, `assign_thread`, `transfer_thread`, `claim_thread`, `get_transfer_history`, `get_response_metrics`, `get_canned_responses`, `send_internal_note`, `pin_message`, `unpin_message`, `get_pinned_messages`, `toggle_reaction`, `get_tags`, `add_thread_tag`, `remove_thread_tag`, `get_thread_tags`, `get_related_documents` (24 endpoints)
- **`api/email.py`**: All 8 endpoints (`get_email_body`, `get_email_attachment`, `search_emails`, `send_email`, `check_gmail_connection`, `manual_sync`, `get_my_signature`, `save_my_signature`)
- **`api/subscribers.py`**: All 10 endpoints (`get_subscriber_lists`, `get_subscribers`, `add_subscriber`, `add_subscriber_by_contact`, `remove_subscriber`, `unsubscribe_subscriber`, `resubscribe`, `import_from_doctype`, `create_subscriber_list`, `search_identities`)
- **`api/merge_suggestions.py`**: All 4 endpoints (`get_merge_suggestions`, `approve_merge`, `dismiss_suggestion`, `get_suggestion_count`)
- **`api/broadcast.py`**: 5 read endpoints (`get_broadcasts`, `get_broadcast_detail`, `get_broadcast_logs`, `preview_broadcast_email`, `get_subscriber_lists_for_broadcast`, `get_channel_accounts_for_broadcast`)

### Category 3: Weak Permission Check

- **`api/analytics.py`**: `_check_excom_access()` only blocked Guest users. Strengthened to require `System Manager`, `Excom Manager`, or `Excom User` role — consistent with `chat.py`.

### Category 4: Frontend Type Gaps

- **`hooks/useMessages.ts`**: `mapMessageType()` was missing `Video`, `Location`, `Interactive`, `Flow`, `Reaction`, `Contact`, `Button`. Video messages were silently rendered as text bubbles. Fixed by adding all 14 backend message types.
- **`types/index.ts`**: `Message.type` union updated to include all message types.

### Other Cleanup

- **`api/broadcast.py`**: Removed dead `from typing import Optional` import.
- **`api/broadcast.py`**: Fixed docstring `channel_type` → `channel`.

### Impacted modules
- `excom/excom/api/chat.py` — 24 endpoints secured
- `excom/excom/api/email.py` — 8 endpoints secured
- `excom/excom/api/broadcast.py` — field fix + 5 endpoints secured + dead import removed
- `excom/excom/api/subscribers.py` — 10 endpoints secured
- `excom/excom/api/merge_suggestions.py` — 4 endpoints secured
- `excom/excom/api/analytics.py` — field fix + permission strengthened
- `excom/excom/services/delivery_watchdog.py` — language field fix
- `excom/excom/utils/template_utils.py` — DocType name + missing import
- `frontend/src/hooks/useMessages.ts` — message type mapping
- `frontend/src/types/index.ts` — type union update

---

## Two roles: Excom Admin and Excom Agent (2026-09-08)

The three-tier model (Excom Admin / Excom Manager / Excom User) is gone. There are two roles now, and
the middle tier is not coming back:

| Role | What it means |
|---|---|
| **Excom Agent** | Works the inbox: answer, note, tag, transfer, claim, promote a conversation to a lead, edit Lead / Opportunity / Prospect / Contact / Address. Cannot touch channels, credentials, teams or settings. |
| **Excom Admin** | Everything an agent does, plus running the system and the people: teams and members, reassignment, the audit log, channels, Meta connections, WhatsApp tokens, templates, the embed and Excom Settings. |

`System Manager` continues to imply Excom Admin.

**Visibility is still a separate axis.** Which conversations and leads a person sees comes from the
team tree (`Excom Team Member.role`), not from these roles. Holding Excom Admin does grant the
blanket bypass, because the tier that owns the system has to be able to support it.

### Migration
`excom.patches.v1_0.two_role_model` runs on migrate:
- `Excom User` → `Excom Agent` for every holder.
- `Excom Manager` → `Excom Agent`. **Nobody is promoted.** A patch that silently handed the WhatsApp
  tokens and the Meta connection to every desk head would be the wrong default, so the admin tier is
  granted by a person. The demoted names are written to the error log under
  "Excom: former managers are now agents" so they can be reviewed and re-granted.
- Custom DocPerm and DocPerm rows naming the old roles are deleted, `setup/crm_permissions.py` is
  re-applied, and both old Role records are removed.

### Where the roles are decided in code
- `excom/excom/api/chat.py` — `EXCOM_ROLES`, `ADMIN_ROLES`, `MANAGER_ROLES` (the last two are the
  same set now; the name is kept because "may run people and desks" reads better at the call sites).
- `excom/excom/doctype/excom_thread/excom_thread.py` — `MANAGER_ROLES` (the blanket bypass) and
  `EXCOM_ROLES` (may open Excom at all).
- `excom/setup/crm_permissions.py` — the CRM permission matrix, `AGENT` and `ADMIN`.
- `excom/setup/__init__.py` — `ROLES`, seeded on install and repaired on every migrate.
