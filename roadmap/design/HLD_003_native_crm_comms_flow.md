# High Level Design — Native CRM + Communications Flow

**Document:** HLD-003
**Version:** 1.0
**Date:** 2026-09-02
**Status:** Draft for review
**Owner:** Sagar Ratan Garg
**Supersedes:** the CRM-choice portion of HLD-002 §3 and `crm_flow_blueprint.html`
**Decision:** ERPNext **native** CRM is the system of record. Frappe CRM (`apps/crm`) is not used.

---

## Table of Contents

1. [Decision and Premises](#part-1--decision-and-premises)
2. [Data Model — Native Spine + Excom Overlay](#part-2--data-model--native-spine--excom-overlay)
3. [The Excom CRM Module — a UI, not a second CRM](#part-3--the-excom-crm-module--a-ui-not-a-second-crm)
4. [Inbound Communications Flow](#part-4--inbound-communications-flow)
5. [The Intake Spine — S1 to S5](#part-5--the-intake-spine--s1-to-s5)
6. [The Six Pipelines on Native Fields](#part-6--the-six-pipelines-on-native-fields)
7. [Teams, Assignment and Visibility](#part-7--teams-assignment-and-visibility)
8. [The Conversion Chain](#part-8--the-conversion-chain)
9. [Outbound Communications](#part-9--outbound-communications)
10. [SLA, Notifications and Escalation](#part-10--sla-notifications-and-escalation)
11. [Multi-Company and Permissions](#part-11--multi-company-and-permissions)
12. [Build Plan](#part-12--build-plan)
13. [Open Decisions](#part-13--open-decisions)

---

# Part 1 — Decision and Premises

## 1.1 Why native

| # | Finding | Consequence |
|---|---|---|
| D1 | `CRM Lead` / `CRM Deal` have **no `company` field**; `ERPNext CRM Settings` is a singleton with one `erpnext_company` | Frappe CRM cannot model a multi-company group |
| D2 | `CRM Deal` carries `deal_value` (one number); ERPNext `Opportunity` carries an `items` child table with `item_code`/`qty`/`rate`/`amount` | 4 of 6 pipelines are item-level; the deal must hold what was quoted |
| D3 | Native Lead → Opportunity → Quotation → Sales Order is one document chain | The atomic CRM→ERP handoff — the most failure-prone component — ceases to exist |
| D4 | Frappe CRM's differentiators (conversation timeline, WhatsApp, telephony) duplicate excom | Paying a dual-data-model tax for features already owned |
| D5 | `excom/hooks.py` **already** hooks `Lead`, `Customer`, `Supplier`, `Contact`, `Party Link` → `identity_hooks.on_entity_created`; `on_customer_updated` already merges identities on `lead_name` conversion | The identity spine is already built for native CRM. Nothing to rewrite; only `Opportunity` and `Prospect` are missing |

The accepted cost is the Desk UI. Part 3 answers it.

## 1.2 Premises

| # | Premise | If wrong |
|---|---|---|
| P1 | One ERPNext site hosts multiple `Company` records | Site-per-company needs a central intake site — re-open §11 |
| P2 | Excom and ERPNext are installed on the same site | Cross-site REST for every identity lookup; latency budget changes |
| P3 | Six customer types (§6) cover the sales motions | A seventh type is one Select option + one stage set |
| P4 | `Excom Call` (Phase C) is not yet built | Voice rows in this document are design-ahead, not current state |
| P5 | Excom's React frontend is the primary agent surface; Desk is the admin/back-office surface | If reps must live in Desk, Part 3 shrinks to Customize Form work |

## 1.3 Principles

1. **No shadow schema.** The Excom CRM module reads and writes native fieldnames. If a value is not on `Lead`/`Opportunity`/`Prospect`, it does not exist.
2. **One identity, many records.** `Omni Identity` is the join key; ERP documents come and go beneath it.
3. **The conversation never changes owner.** Excom owns every message on every channel, at every stage.
4. **Configuration before code.** Custom Field, Property Setter, Assignment Rule and Notification first; Python only where they cannot reach.
5. **Provenance is immutable.** First touch is stamped once and survives to retention.

---

# Part 2 — Data Model — Native Spine + Excom Overlay

## 2.1 The native spine (unchanged doctypes)

```
                  ┌──────────────┐
                  │  Prospect    │  organisation-level account container
                  │  (company)   │  child tables: leads[], opportunities[]
                  └──────┬───────┘
                         │
     ┌───────────────────┼────────────────────┐
     ▼                   ▼                    ▼
┌──────────┐      ┌──────────────┐     ┌──────────────┐
│  Lead    │─────▶│ Opportunity  │────▶│  Quotation   │
│ (company)│      │  (company)   │     │              │
└────┬─────┘      │  items[]     │     └──────┬───────┘
     │            │  sales_stage │            │
     │            └──────────────┘            ▼
     │                                 ┌──────────────┐
     └────────make_customer───────────▶│   Customer   │◀── Sales Order
                                       └──────────────┘
```

Native transition helpers already present — **do not reimplement**:

| Helper | Location | Use |
|---|---|---|
| `make_opportunity(source_name)` | `erpnext/crm/doctype/lead/lead.py:350` | Lead → Opportunity |
| `make_customer(source_name)` | `lead.py:310` | Lead → Customer |
| `make_quotation(source_name)` | `lead.py:381` and `opportunity.py:407` | → Quotation |
| `make_lead_from_communication(communication)` | `lead.py:472` | Promote a conversation into a Lead |

## 2.2 Custom fields — the complete list

All added via **Custom Field** (fixtures in excom), never by editing ERPNext JSON.

### On `Lead`

| Fieldname | Type | Options / Notes |
|---|---|---|
| `customer_type` | Select | `\nDistributor\nRetailer\nExport Importer\nOEM\nCorporate Gifting\nOnline B2C` — blank until S4 |
| `omni_identity` | Link → `Omni Identity` | Written by `identity_hooks`; read-only in UI |
| `first_touch_at` | Datetime | Read-only, set once |
| `first_touch_channel` | Select | `WhatsApp\nEmail\nCall\nInstagram\nFacebook\nWeb Chat\nWeb Form\nMarketplace\nManual` |
| `first_touch_by` | Link → `User` | Read-only; blank for machine intake |
| `source_reference` | Data | Marketplace enquiry id (IndiaMART `QUERY_ID`, TradeIndia inquiry id) |
| `exhibition` | Data | Tag value, e.g. `Exhibition:AmbienteFrankfurt2026` |
| `auto_ack_sent_at` | Datetime | Proof the ≤5 min SLA was met by machine |
| `intake_stage` | Select | `Captured\nDeduped\nResponded\nClassified\nQualified` — drives the queue board |

Native fields reused rather than duplicated: `company`, `territory`, `country`, `state`, `city`, `source` (→ `Lead Source`), `campaign_name`, `request_type`, `qualification_status`, `qualified_by`, `qualified_on`, `lead_owner`, `whatsapp_no`, `mobile_no`, `email_id`, `industry`, `market_segment`, `annual_revenue`, `no_of_employees`, `job_title`, `status`.

### On `Opportunity`

| Fieldname | Type | Options / Notes |
|---|---|---|
| `customer_type` | Select | Same options; **mandatory**, fetched from Lead |
| `pipeline_stage` | Select | Superset of all stage names (§6.1). **Select, not Link** — Frappe Kanban requires a Select field (`kanban_view.js:353`) |
| `omni_identity` | Link → `Omni Identity` | Fetched from Lead / resolved on create |
| `stage_entered_at` | Datetime | Reset on every `pipeline_stage` change → stage-ageing reports |
| `next_action_at` | Datetime | Drives the "Today" queue; mandatory while status is `Open` |
| `gate_flags` | Small Text (JSON) | Which §6 gates have cleared, e.g. `{"territory":1,"onboarding":0}` |
| `event_date` | Date | Corporate Gifting — the reverse-scheduling anchor |
| `design_by` | Select | OEM — `Customer\nUs` |
| `sample_round` | Int | OEM — bounded at 3 |
| `incoterm` | Link → `Incoterm` | Export (native doctype in ERPNext) |
| `proposed_pincodes` | Small Text | Distributor — territory-gate input |

Native fields reused: `company`, `opportunity_from` + `party_name`, `opportunity_type`, `status`, `sales_stage`, `probability`, `expected_closing`, `currency` + `conversion_rate`, `opportunity_amount`, `items[]`, `territory`, `customer_group`, `contact_person`, `source`, `campaign`, `first_response_time`, `competitors[]`, `lost_reasons[]`, `order_lost_reason`, `opportunity_owner`, `country`/`state`/`city`.

> **`pipeline_stage` vs `sales_stage`.** `sales_stage` is a Link to the configurable `Sales Stage` doctype and feeds native funnel reports. `pipeline_stage` is the per-type operating stage and the kanban column. `pipeline_stage` writes `sales_stage` and `probability` through a mapping table so native reporting stays correct without agents maintaining two fields.

### On `Prospect`

| Fieldname | Type | Notes |
|---|---|---|
| `customer_type` | Select | Account-level type |
| `omni_identity` | Link → `Omni Identity` | The account's identity |

### On `Customer`

| Fieldname | Type | Notes |
|---|---|---|
| `first_touch_at`, `first_touch_channel`, `source_reference` | as above | Copied on conversion — attribution survives to retention |
| `customer_type` | Select | Mirrors the winning Opportunity |

## 2.3 The excom overlay (existing, unchanged)

```
Omni Identity ──1:N──▶ Omni Identity Channel  (channel_type, channel_user_id, verified, last_seen)
      │
      ├───1:N──▶ Omni Identity Link  (linked_doctype, linked_name, role)
      │              └── role ∈ Unknown | Decision Maker | Billing | Technical | Primary Contact | Influencer
      │
      └───1:N──▶ Excom Thread (omni_identity, channel, account_doctype, account, status,
                               assigned_to, assigned_team, priority, last_inbound_at, …)
                        └───1:N──▶ Excom Message
```

**The two joins that make this work, both already in the schema:**

1. `Omni Identity Link.linked_doctype` + `linked_name` — a Dynamic Link. Point it at `Lead`, `Opportunity`, `Prospect`, `Customer`. Multiple links per identity: a party can be a Lead *and* an Opportunity *and* a Customer over time, with full history retained.
2. `Excom Thread.account_doctype` + `account` — a Dynamic Link. This is the thread's *current* ERP context; it is re-pointed as the record advances.

**No new excom doctypes are required for CRM.** The overlay is: two hook additions (`Opportunity`, `Prospect`), one API module, one frontend module.

---

# Part 3 — The Excom CRM Module — a UI, not a second CRM

## 3.1 What it is and is not

| Is | Is not |
|---|---|
| A React module inside `apps/excom/frontend`, alongside the inbox | A new Frappe app |
| Reads/writes native `Lead` / `Opportunity` / `Prospect` fieldnames over the API | A doctype of its own |
| A sales cockpit optimised for pipelines and conversations | A replacement for Desk — Desk stays for quotations, orders, back-office |
| Field-driven: adding a Custom Field surfaces it without a frontend release | A hardcoded form |

The bet: reps live in excom, back-office lives in Desk, and there is exactly one database behind both.

## 3.2 Why inside excom rather than a new app

The expensive parts of a CRM frontend already exist and are already built here:

| Need | Already in excom |
|---|---|
| App shell, auth, routing, theme | `frontend/src/App.tsx` (page-state `AppPage`), `LeftSidebar.tsx`, `useBranding` |
| Realtime | `useRealtimeMessages`, `useRealtimeThreads` (socketio on :9000) |
| Identity + ERP context panel | `OmniIdentityPanel.tsx`, `useLinkedEntities`, `useRelatedInvoices` |
| Conversation UI | `ChatThreadList`, `ChannelTabsView`, `EmailCompose`, `WhatsAppTemplatePicker`, `CannedResponsePopover` |
| Teams, tags, analytics | `TeamManagementPage`, `TagManager`, `AnalyticsPage` |
| Mobile | `components/mobile/` |

A pipeline board and a record cockpit are the only genuinely new surfaces. Everything else is reuse — which is precisely why this path is cheap here and would not be in a greenfield app.

## 3.3 Pages

Add four entries to `AppPage`:

### 3.3.1 `crm-intake` — the Lead queue

The S1–S5 spine as a work queue, not a list view. Columns are `intake_stage`. Each card shows channel icon, source, age against SLA, and the auto-ack state.

- Red badge when `now - creation` exceeds the source SLA and `auto_ack_sent_at` is null
- Bulk-classify: multi-select → set `customer_type` → the cards leave the queue
- One-click "Open conversation" — the thread is one join away via `omni_identity`

### 3.3.2 `crm-pipeline` — the typed board

Kanban over `Opportunity`, columns = `pipeline_stage`, one saved board per `customer_type`.

- Card: party, `opportunity_amount` + currency, `stage_entered_at` age, owner avatar, gate chips from `gate_flags`, last-inbound age from the linked thread
- Drag between columns → validates gates → writes `pipeline_stage`, `stage_entered_at`, mapped `sales_stage` + `probability`
- Blocked drags surface the failing gate inline rather than a generic error
- Board filters: company, territory, owner, team

### 3.3.3 `crm-record` — the cockpit

Two panes, no tab-hunting:

```
┌──────────────────────────────┬───────────────────────────┐
│ LEFT — the record            │ RIGHT — the conversation  │
│                              │                           │
│ Header: party · type · stage │  Excom thread, live        │
│ Gate strip (§6 chips)        │  all channels merged       │
│ Fields, filtered by type     │  composer with templates   │
│ Items table (Opportunity)    │  call button (Phase C)     │
│ Next action + owner          │                           │
│ Activity: stage log, tasks   │  Identity panel:           │
│ Documents: Quotation, SO     │   linked entities, invoices│
└──────────────────────────────┴───────────────────────────┘
```

The right pane is the existing conversation component, unmodified. The left pane is new and renders from a field schema the server supplies (§3.4), so a Custom Field added in Desk appears here on next load.

### 3.3.4 `crm-today` — the rep's day

One list, ordered: overdue `next_action_at`, then SLA-breaching threads, then today's actions, then unassigned queue items for the rep's teams. This is the page a telecaller keeps open.

## 3.4 API surface — `excom/excom/api/crm.py`

New module, alongside the existing `chat.py` / `teams.py` / `analytics.py`.

| Endpoint | Purpose |
|---|---|
| `get_field_schema(doctype, customer_type)` | Returns visible/required/readonly field metadata for the type. The frontend renders from this — no hardcoded field lists |
| `get_intake_queue(filters)` | Lead queue with SLA state and thread age, one query |
| `get_pipeline(customer_type, filters)` | Opportunities grouped by `pipeline_stage`, with thread-freshness joined |
| `get_record(doctype, name)` | Record + identity + linked threads + documents in one round trip |
| `set_stage(name, stage)` | Validates gates → writes `pipeline_stage`, `stage_entered_at`, `sales_stage`, `probability` |
| `classify_lead(name, customer_type, …)` | S4 write + assignment trigger |
| `promote_thread(thread, customer_type)` | Thread → Lead, wrapping native `make_lead_from_communication` |
| `convert(name, target)` | Wraps native `make_opportunity` / `make_customer` / `make_quotation`, then re-points identity links and threads |
| `get_gate_status(name)` | Per-type gate evaluation for the chip strip |

**Rule:** every endpoint calls `frappe.has_permission` and honours company/territory User Permissions. No `ignore_permissions` outside the webhook intake path.

## 3.5 Server-side logic — `excom/excom/services/crm_flow.py`

| Function | Responsibility |
|---|---|
| `resolve_or_create_lead(identity, channel, payload)` | The intake ladder (§4.3) |
| `stamp_provenance(doc, channel, source, reference)` | Write-once first-touch fields |
| `evaluate_gates(opportunity)` | Per-type gate rules → `gate_flags` |
| `advance_stage(opportunity, stage)` | Gate check, stage write, stage-change log, thread system message |
| `on_conversion(source, target)` | Re-point `Omni Identity Link`, re-point open threads' `account_doctype`/`account`, copy provenance, post system message |
| `map_stage_to_sales_stage(customer_type, stage)` | Keeps native funnel reporting correct |

Hooks to add in `excom/hooks.py`:

```python
"Opportunity": {
    "after_insert": "excom.excom.services.crm_flow.on_opportunity_created",
    "on_update":    "excom.excom.services.crm_flow.on_opportunity_updated",
},
"Prospect": {
    "after_insert": "excom.excom.services.identity_hooks.on_entity_created",
},
```

and extend `identity_hooks.on_entity_created` / `identity_sync` with `sync_single_opportunity` and `sync_single_prospect`, mirroring the existing `sync_single_lead`.

---

# Part 4 — Inbound Communications Flow

## 4.1 The universal path

```
  SOURCE            ADAPTER                 EXCOM CORE                     CRM
  ──────            ───────                 ──────────                     ───
  WhatsApp  ──▶ webhook (HMAC)  ──┐
  Email     ──▶ Gmail poller     ──┤
  Instagram ──▶ webhook          ──┤
  Facebook  ──▶ webhook          ──┼─▶ resolve identity ─▶ Excom Thread ─┬─▶ existing ERP record?
  Web Chat  ──▶ websocket        ──┤    (phone/email/       + Message    │     └─ yes → attach, notify owner
  Voice     ──▶ telephony (P-C)  ──┤     channel id)                     │     └─ no  → create Lead
  IndiaMART ──▶ push webhook     ──┤                                     │
  TradeIndia──▶ poller (5 min)   ──┤                                     └─▶ auto-ack (marketplace only)
  Web Form  ──▶ Web Form hook    ──┤
  Exhibition──▶ bulk capture     ──┤
  Cold call ──▶ agent action     ──┘
```

Every path converges on **resolve identity → thread → decide record**. Marketplace paths additionally fire an auto-acknowledgement before any human sees the lead.

## 4.2 Per-source contract

| Source | Adapter | Creates | First response | Auto-ack |
|---|---|---|---|---|
| **IndiaMART** | Whitelisted webhook, HMAC-checked, `source_reference` = `QUERY_ID` | `Lead` + `Excom Thread` | **≤ 5 min** | Yes — WhatsApp template, ≤ 60 s |
| **TradeIndia** | Scheduled poller, 5-min cadence | `Lead` + thread | ≤ 15 min | Yes — WhatsApp/email |
| **Meta lead ads** | Facebook webhook → lead form fetch | `Lead` + thread | ≤ 30 min | Optional |
| **Instagram / FB DM + comments** | Existing excom channel | Thread only | ≤ 30 min | No |
| **Website form** | Frappe `Web Form` → hook, UTM → `campaign_name` | `Lead` | ≤ 1 h | Yes — email confirmation |
| **Web chat** | Existing `webchat.py`, `Excom Visitor Session` | Thread; Lead on identity capture | live | n/a |
| **Inbound WhatsApp** | Existing webhook | Thread; Lead if unknown identity | ≤ 30 min | No |
| **Inbound email** | Existing Gmail poller | Thread; Lead if unknown identity | ≤ 1 h | No |
| **Inbound call** (Phase C) | Telephony webhook → `Excom Call` | Thread; Lead if unknown | live | Missed-call SMS |
| **Cold call** | Agent action in `crm-today` | `Lead` + call record | n/a (outbound) | n/a |
| **Exhibition** | Bulk capture: name, phone, type guess, card photo | `Lead` batch, `exhibition` stamped | ≤ 48 h post-show | Batch broadcast |
| **Physical visit** | Manual / field app | `Lead` (or `Outlet` prospect) | same day | No |

## 4.3 Identity resolution ladder

Executed in order; first match wins. Extends the existing resolver used by `identity_sync`.

```
1. Channel identity   → Omni Identity Channel.channel_user_id == payload sender id
                        (exact, per channel — a WhatsApp WAID, an IG scoped id)
2. Normalized phone   → Omni Identity.normalized_phone == E.164(payload phone)
3. Normalized email   → Omni Identity.normalized_email == lower(trim(payload email))
4. Fingerprint        → hash_fingerprint match
5. No match           → create Omni Identity (status Active, is_master 1)
                        + Omni Identity Channel row
```

Then, for record selection:

```
identity.linked_entities, by precedence:
  Customer     → thread.account = Customer     ; notify account owner ; NO lead created
  Opportunity  → thread.account = Opportunity  ; notify opportunity_owner
  Lead (open)  → thread.account = Lead         ; notify lead_owner ; merge provenance as a touch
  Lead (Converted / Do Not Contact) → treat as no open record
  none         → create Lead (§4.4)
```

**Rule R1 holds:** an existing customer enquiring again never creates a duplicate lead.

## 4.4 Lead creation from a conversation

Two modes:

**Automatic** — marketplace, web form, and Meta lead ads carry explicit buying intent. A Lead is created immediately, `status = Lead`, `intake_stage = Captured`.

**Agent-promoted** — WhatsApp/IG/email/chat conversations create a thread only. The agent clicks *Promote to Lead*, which calls `promote_thread()` → native `make_lead_from_communication`. Rationale: not every DM is a lead, and auto-creating from every inbound message poisons the funnel and every conversion metric downstream.

On creation, always:
1. `stamp_provenance()` — `first_touch_at`, `first_touch_channel`, `first_touch_by`, `source`, `campaign_name`, `source_reference` (write-once)
2. `company` — from the channel account's mapping (§11.2)
3. `omni_identity` link both ways: `Lead.omni_identity` and an `Omni Identity Link` row (`linked_doctype = Lead`)
4. `Excom Thread.account_doctype = "Lead"`, `account = <name>`
5. Assignment Rule fires (§7)

## 4.5 Auto-acknowledgement

The competitive mechanism. Marketplace leads are sold to several suppliers at once; first response usually wins the conversation.

```
webhook received
   └─▶ create Lead (ignore_permissions, system user)
   └─▶ enqueue short-queue job:
          ├─ WhatsApp template send to Lead.mobile_no  (pre-approved utility template)
          ├─ fallback to email if no WhatsApp consent
          ├─ write auto_ack_sent_at
          └─ post outbound Excom Message into the thread
```

Constraints: the template must be pre-approved by Meta; content is a courteous acknowledgement plus a qualifying question, never a price. The reply lands in the same thread, so the rep opens a conversation already in progress. `auto_ack_sent_at` is the audit trail for the ≤5 min SLA.

---

# Part 5 — The Intake Spine — S1 to S5

Identical for every lead regardless of source. `intake_stage` tracks position.

| Stage | Field writes | Owner | Exit condition |
|---|---|---|---|
| **S1 Capture** | provenance fields, `company`, `omni_identity`, `intake_stage = Captured` | machine | record + thread exist |
| **S2 Dedupe** | link to existing identity, or `needs_review = 1` on ambiguity | machine (`identity_hooks`) | identity resolved |
| **S3 First response** | `auto_ack_sent_at` and/or first outbound message | machine, then human | outbound message exists on the thread |
| **S4 Classify** | `customer_type`, `request_type`, `territory`, `country`, `industry` | human (or web-form self-declaration, verified) | `customer_type` set |
| **S5 Qualify** | `qualification_status`, `qualified_by`, `qualified_on`, `status` | human | → Opportunity, or Do Not Contact, or nurture |

**The classification gate (R3):** an `Opportunity` cannot be created from a Lead whose `customer_type` is blank. Enforced server-side in `crm_flow.py`, not only in the UI — Desk users are bound by the same rule.

**S5 outcomes:**

| Outcome | Native write | Then |
|---|---|---|
| Qualified | `qualification_status = Qualified`, `status = Opportunity` | `make_opportunity()` → §6 pipeline |
| Not now | `qualification_status = In Process` | Nurture: thread stays open, `next_action_at` set |
| Junk / spam | `status = Do Not Contact`, `Omni Identity.is_spam = 1` | Thread → `Spam`; identity suppressed from broadcasts |
| Duplicate | merge into master identity | Thread re-parented; lead disabled |

---

# Part 6 — The Six Pipelines on Native Fields

## 6.1 The `pipeline_stage` superset

One Select field holds every stage across all types; per-type visibility is enforced by `get_field_schema()` and by `advance_stage()`. Kanban boards filter by `customer_type`, so a rep only ever sees their own type's columns.

| Stage | Types | Maps to `sales_stage` | `probability` |
|---|---|---|---|
| Qualified | all | Prospecting | 10 |
| Territory Check | Distributor, Retailer | Qualification | 20 |
| Pitch & Price Slab | Distributor | Needs Analysis | 30 |
| Sample Kit | Distributor | Value Proposition | 40 |
| Spec Confirmed | Export, OEM | Needs Analysis | 30 |
| Compliance Check | Export | Qualification | 35 |
| Feasibility & Costing | OEM | Perception Analysis | 35 |
| Curation & Mockup | Gifting | Value Proposition | 35 |
| NDA / Brief | OEM | Qualification | 20 |
| Sampling Loop | OEM | Proposal/Price Quote | 50 |
| Quote / Export Quote | Export, OEM, Gifting, Distributor | Proposal/Price Quote | 50 |
| Sample Shipment | Export | Id. Decision Makers | 55 |
| Negotiation | Distributor, Export, OEM | Negotiation/Review | 70 |
| Approval | Gifting | Negotiation/Review | 70 |
| Pro Forma | Export | Negotiation/Review | 80 |
| Advance / LC | Export, Gifting | Negotiation/Review | 85 |
| Agreement & Onboarding | Distributor | Negotiation/Review | 85 |
| Commercial Terms | OEM | Negotiation/Review | 85 |
| Won | all | — (`status = Converted`) | 100 |

## 6.2 Distributor

**Stages:** Qualified → Territory Check → Pitch & Price Slab → Sample Kit → Negotiation → Agreement & Onboarding → Won
**Owner:** channel sales · **Cycle:** 2–6 weeks · **Touch cadence:** every 3 days

| Gate | Rule | Implementation |
|---|---|---|
| Territory | No active distributor holds the `proposed_pincodes`; overlap needs a named manager override, logged | Query `Customer` where `customer_group = Distributor` and territory intersects; override writes a comment |
| Onboarding | GST verified **and** agreement attached **and** deposit received **and** credit limit approved | 4 boolean checks in `evaluate_gates`; all must be true before Won |

**Fields shown:** `proposed_pincodes`, `territory`, `items[]` (opening order), `annual_revenue`, `no_of_employees`.
**On Won:** `Customer` with `customer_group = Distributor`, distributor price list, credit limit, territory. Outlets map beneath it in fieldforce.

## 6.3 Retailer

A routing decision, not a long pipeline.

**Stages:** Qualified → Territory Check → Won (Routed | Direct)

| Branch | Condition | Action |
|---|---|---|
| Routed | pincode served by an active distributor | Create `Outlet` under that distributor, notify them via excom, `status = Converted`, `order_lost_reason` unset, tag `Routed` |
| Direct | pincode uncovered | `Customer` with retail price list; flag the pincode as distributor-gap intel for channel sales |

**Gate — channel conflict:** a covered-territory retailer is never sold direct. Enforced in `evaluate_gates`; override requires the channel manager role.

## 6.4 Export / International Importer

**Stages:** Qualified → Spec Confirmed → Compliance Check → Export Quote → Sample Shipment → Negotiation → Pro Forma → Advance/LC → Won
**Owner:** export desk · **Cycle:** 4–16 weeks · **Response:** ≤ 24 h (time zones)

| Gate | Rule |
|---|---|
| Compliance | Destination-country certifications confirmed feasible **before** any quote leaves. `country` drives the checklist |
| Payment | Quote carries `incoterm` + `currency`; Won only on verified advance or accepted LC draft |

**Fields:** `country`, `incoterm`, `currency` + `conversion_rate`, `items[]` with specs, `competitors[]`.
**Quoting:** ERPNext `Quotation` via `make_quotation()`. The Opportunity holds `items[]`; the Quotation is the document sent. Compliance team participates in the same excom thread — no separate email chain.

## 6.5 OEM — Private Label + Custom Designed

One pipeline, branched on `design_by` (`Customer` = they bring the design; `Us` = we design it).

**Stages:** Qualified → NDA/Brief → Spec Confirmed → Feasibility & Costing → Sampling Loop → Commercial Terms → Won
**Cycle:** 6–20 weeks · **Owner:** OEM desk + production

| Gate | Rule |
|---|---|
| Feasibility (internal) | Production signs off MOQ, tooling cost and lead time **before** the customer sees a price. A quote production cannot honour is worse than a slow quote |
| Sampling | Every round has written feedback; `sample_round` bounded at 3, then escalate to a call — approve, one paid round, or part ways |
| Commercial | Tooling/plate charges and MOQ agreed in writing before contract |

**On Won:** custom SKUs created as `Item` records; `Sales Order` carries tooling as a separate line.

## 6.6 Corporate Gifting

Reverse-scheduled from `event_date` — the date is the constraint everything else bends around.

**Stages:** Qualified → Curation & Mockup → Quote → Approval → Advance → Won (In Production)
**Cycle:** 1–4 weeks

| Gate | Rule |
|---|---|
| Date feasibility | At Qualified: production lead time + logistics must fit before `event_date`, else exit as Lost — Timeline, politely, with a next-occasion nudge |
| Advance | Branded/customised production never starts before advance clears |

**On Won:** `Sales Order` with `delivery_date = event_date − buffer`.
**Seasonal:** excom broadcast to past gifting customers 8 weeks before Diwali / New Year, segmented by `customer_type = Corporate Gifting` on `Customer`.

## 6.7 Online B2C — the bypass

No Opportunity is created. An individual buying one unit is not a pipeline; putting them in one buries the real deals.

- Lead classified `Online B2C` → `status = Converted` or `Do Not Contact` as appropriate, thread stays open
- Order via store/marketplace; identity and thread retained for support and repeat-purchase campaigns
- **Promotion rule:** if the buyer asks about bulk, dealership, or branding, one click re-classifies to a real type and creates the Opportunity, provenance intact

---

# Part 7 — Teams, Assignment and Visibility

## 7.1 Three distinct concerns

| Concern | Mechanism | Scope |
|---|---|---|
| **Who gets new work** | Core `Assignment Rule` | Lead / Opportunity routing |
| **Who can see what** | User Permissions + `Sales Person` tree | Data visibility |
| **Who handles the conversation** | `Excom Team` + `Excom Account Team` | Thread routing |

These must agree, or a rep owns a deal whose conversation lands on someone else's screen. §7.4 is the reconciliation.

## 7.2 Assignment Rules — one per pipeline

| Rule | Document type | Assign condition | Rule | Users |
|---|---|---|---|---|
| Intake — Unclassified | Lead | `not customer_type` | Round Robin | inbound desk |
| Distributor Desk | Opportunity | `customer_type == "Distributor"` | Round Robin | channel sales |
| Retailer Desk | Opportunity | `customer_type == "Retailer"` | Load Balancing | telecallers |
| Export Desk | Opportunity | `customer_type == "Export Importer"` | Round Robin | export team |
| OEM Desk | Opportunity | `customer_type == "OEM"` | Load Balancing | OEM team |
| Gifting Desk | Opportunity | `customer_type == "Corporate Gifting"` | Round Robin | gifting team |

Refine with territory or company in the condition, e.g. `customer_type == "Export Importer" and country in ("USA","CAN")`; higher `priority` wins. `unassign_condition` releases on `status == "Do Not Contact"`. Holiday lists are respected natively.

**Load Balancing** (fewest open assignments) suits high-volume telecalling; **Round Robin** suits long-cycle desks where fairness matters more than instantaneous load.

## 7.3 Sticky ownership — the one piece needing code

Assignment Rules skip already-assigned documents, so sticky assignment simply runs first:

```
on Lead after_insert (crm_flow.py):
    prior = last owner of any prior Lead/Opportunity/Customer on this Omni Identity
    if prior and prior is active and not on holiday:
        ToDo assign to prior
        # Assignment Rule then finds it assigned and skips it
    # else: Assignment Rule handles it as normal
```

Roughly 20 lines. It mirrors the excom Phase C call-routing rule, so calls and deals land on the same human — which is the entire point of an omnichannel layer.

## 7.4 Reconciling ERP assignment with excom threads

When a Lead/Opportunity is assigned, `on_opportunity_updated` sets the linked thread's `assigned_to` to match, and `assigned_team` to that user's `Excom Team`. When a thread is manually transferred in excom (`Excom Thread Transfer Log` already exists), the reverse does **not** fire automatically — a conversation handoff is not an ownership change. Ownership changes only in the CRM record; the thread follows.

## 7.5 Visibility

| Level | Mechanism |
|---|---|
| Company | User Permission on `Company` — hard boundary, §11 |
| Territory | User Permission on `Territory` for regional managers |
| Own records only | Role Permission with `if_owner`, plus `Sales Person` tree for roll-up reporting |
| Manager roll-up | `Sales Person` tree (`parent_sales_person`) drives commission and funnel reports |

ERPNext's `Sales Person` tree is the native hierarchy. Populate it once; it doubles as the commission structure on Sales Orders after conversion.

---

# Part 8 — The Conversion Chain

## 8.1 The chain

```
Lead ──make_opportunity()──▶ Opportunity ──make_quotation()──▶ Quotation ──▶ Sales Order ──▶ Delivery ──▶ Invoice
  │                              │                                              ▲
  └──────make_customer()─────────┴──────────────────────────────────────────────┘
```

Because all of this is one data model, there is no sync, no cross-app transaction, and no half-converted state to reconcile. HLD-002's atomic-handoff requirement (R5) is satisfied structurally rather than by code.

## 8.2 What `on_conversion` must still do

Native mapping handles ERP fields. Excom-side bookkeeping remains:

| Step | Action |
|---|---|
| 1 | Add `Omni Identity Link` for the new record (`Opportunity` / `Customer`), **retaining** the prior Lead link — full history |
| 2 | Re-point open threads: `account_doctype` / `account` → the new record |
| 3 | Copy provenance (`first_touch_*`, `source_reference`) onto `Customer` |
| 4 | Post a system message into the thread: *"Lead converted to Opportunity OPP-0042"* |
| 5 | Carry `customer_type` forward to Opportunity and Customer |
| 6 | Preserve ownership: `opportunity_owner` = converting Lead's `lead_owner` unless an Assignment Rule overrides |

Note `identity_hooks.on_customer_updated` **already** merges the Lead's identity into the Customer's on `lead_name` — that path exists and needs only extension to Opportunity.

## 8.3 Loss handling

`status = Lost` requires `order_lost_reason` from the native `Opportunity Lost Reason` doctype, plus `lost_reasons[]` detail rows. Seed per-type reasons: Price, Timeline, Territory conflict, Spec infeasible, Unresponsive, Chose competitor. Free text is allowed *in addition*, never instead — this is the raw material for every future "why are we losing importers?" question.

The thread is **not** closed on loss. It moves to `Pending` with a `next_action_at` 90 days out — lost deals are the best-qualified nurture list you will ever have.

---

# Part 9 — Outbound Communications

## 9.1 Per-stage communication map

| Stage | Trigger | Channel | Mechanism |
|---|---|---|---|
| Lead created (marketplace) | machine, ≤ 60 s | WhatsApp template | Auto-ack (§4.5) |
| Lead created (web form) | machine | Email | Confirmation template |
| Classified | manual | any | Canned response per type |
| Quote sent | `Quotation` submit | Email + WhatsApp | Print format attached; posted into the thread |
| Sample dispatched | manual | WhatsApp | Tracking template |
| Negotiation stalled | `next_action_at` overdue 3 d | task, not customer message | Internal notification |
| Won | `status = Converted` | WhatsApp + Email | Welcome, account manager introduction |
| Lost | `status = Lost` | none immediately | 90-day nurture entry |
| Post-conversion | Sales Order / Delivery / Invoice | WhatsApp | Existing excom notification templates |

## 9.2 Everything lands in the thread

Any outbound message — auto-ack, quotation email, broadcast — is written as an `Excom Message` on the party's thread. The consequence: a rep opening `crm-record` sees the complete history including machine-sent messages, so nobody re-sends what a scheduler already sent an hour ago.

## 9.3 Campaigns and broadcasts

Existing excom broadcast machinery (`Excom Broadcast`, `Excom Subscriber List`, `Excom Subscriber Rule`) drives segmented outreach. CRM-relevant segments as subscriber rules:

- `customer_type = Corporate Gifting` on Customer → seasonal gifting campaign
- Lost opportunities older than 90 days → re-engagement
- `exhibition = <name>` → post-show follow-up batch
- Distributor Customers with no order in 60 days → reactivation

`Excom Subscriber Rule` already evaluates on doc events via `on_doc_event_for_rules` in the `"*"` hook, so these segments stay current without a scheduled rebuild.

## 9.4 Consent and suppression

- `Lead.unsubscribed` and `Contact.unsubscribed` are honoured before every broadcast send
- `Omni Identity.is_spam` suppresses all outbound
- WhatsApp marketing templates require opt-in; utility templates (order, dispatch, payment) do not. Auto-ack must be registered as a **utility** template tied to the enquiry the customer initiated
- Unsubscribe is per-identity, not per-thread (`unsubscribe.py` exists)

---

# Part 10 — SLA, Notifications and Escalation

## 10.1 SLA definitions

| Metric | Measured | Target by source |
|---|---|---|
| First response | `creation` → first outbound message on the thread | IndiaMART 5 min · TradeIndia 15 min · Meta 30 min · web form 1 h · organic 4 h |
| Classification | `creation` → `customer_type` set | 4 business hours |
| Qualification | `creation` → `qualification_status` final | 2 business days |
| Stage ageing | `stage_entered_at` → now | Per stage, per type |
| Next action overdue | `next_action_at` < now | Immediate |
| Reply latency (open opp) | `last_inbound_at` → next outbound | 4 business hours |

Native `Opportunity.first_response_time` (Duration) stores the measured value for reporting.

## 10.2 Implementation

ERPNext has no CRM SLA doctype (that lives in Frappe CRM and Helpdesk). Use, in order of preference:

1. **Frappe `Notification`** (document-event and scheduled types) for most alerts — configuration only
2. **A scheduled task** in `excom/tasks/crm_sla.py`, hourly, for breach detection and escalation, reusing the existing `delivery_watchdog.py` pattern
3. **Realtime badges** in the excom UI, computed client-side from timestamps already in the payload — zero extra queries

## 10.3 Escalation ladder

```
breach + 0     → in-app notification to owner (existing Excom Notification)
breach + 30m   → WhatsApp to owner's User.mobile_no
breach + 2h    → notification to the team's Excom Team manager
breach + 1 day → unassign, return to the intake queue, log the reason
```

The last step matters: an unresponsive rep must not be able to sit on a marketplace lead that cost money.

---

# Part 11 — Multi-Company and Permissions

## 11.1 The company boundary

`Lead`, `Opportunity`, `Prospect`, `Customer` and every downstream document carry `company` natively. This is the boundary Frappe CRM could not express (D1).

| Layer | Enforcement |
|---|---|
| Data | User Permission on `Company` per user |
| Intake | `company` derived from the receiving channel account (§11.2), never guessed |
| UI | Company filter on every board; users with one permitted company never see the control |
| Reporting | Native — every ERPNext sales report is already company-scoped |

## 11.2 Channel → company mapping

`Excom Channel Account` gains a `company` Custom Field. A WhatsApp number, an inbox, an IG account, a marketplace subscription each belong to exactly one operating company; the inbound adapter reads the receiving account and stamps `Lead.company`. Provenance is therefore structural, not manual.

**Edge case — one identity, several companies.** A buyer who enquires to two group companies gets one `Omni Identity` with two Leads under different `company` values, and separate threads (different channel accounts). The identity panel shows both, subject to the viewer's company permissions. This is correct: one human, two commercial relationships.

## 11.3 Roles

| Role | Sees | Can |
|---|---|---|
| Sales Executive | own Leads/Opportunities (`if_owner`) | advance stages, message, quote |
| Desk Lead | team's records via `Sales Person` subtree | reassign within team, override nothing |
| Channel Manager | all Distributor + Retailer, own company | override territory gate |
| Export Manager | all Export, own company | override compliance gate |
| Sales Head | all types, own company | override any gate, view analytics |
| Group Admin | all companies | configuration |

Gate overrides are always logged as a comment on the document naming the overriding user. A gate without an audit trail is a suggestion, not a gate.

---

# Part 12 — Build Plan

Sequenced so each phase is independently useful. No phase depends on a later one.

## Phase N1 — Schema and configuration (2–3 days, no frontend)

- Custom Fields on `Lead`, `Opportunity`, `Prospect`, `Customer` (§2.2) as excom fixtures
- `Lead Source` rows: IndiaMART Direct, IndiaMART Buy Lead, TradeIndia, Meta Lead Ad, Website, Exhibition, Cold Call, Referral, Walk-in
- `Opportunity Type`, `Sales Stage`, `Opportunity Lost Reason` rows
- Six Assignment Rules; `Sales Person` tree; User Permissions on Company
- Customize Form on Lead/Opportunity: hide unused native fields, set the type-driven mandatory depends-on
- **Deliverable:** the whole flow is operable in Desk. Everything after this is ergonomics.

## Phase N2 — Excom ↔ CRM wiring (3–5 days)

- `Opportunity` and `Prospect` hooks; `sync_single_opportunity`, `sync_single_prospect`
- `services/crm_flow.py`: provenance, gates, stage advance, conversion bookkeeping, sticky assignment
- `api/crm.py`: `get_field_schema`, `get_record`, `promote_thread`, `set_stage`, `convert`
- Thread ↔ record re-pointing on conversion
- **Deliverable:** conversations and CRM records are joined in both directions; promote-to-lead works from the inbox.

## Phase N3 — Marketplace intake + auto-ack (3–4 days)

- IndiaMART webhook (HMAC-verified, idempotent on `source_reference`)
- TradeIndia poller, 5-min cadence
- Auto-ack job + approved WhatsApp utility template
- Duplicate suppression: same `source_reference` never creates two Leads
- **Deliverable:** the ≤5 min response SLA is met by machine. This phase pays for itself first.

## Phase N4 — CRM UI in excom (8–12 days)

- `crm-intake` queue, `crm-pipeline` kanban, `crm-record` cockpit, `crm-today`
- Schema-driven field rendering; gate chips; drag-to-advance
- Mobile views for field reps
- **Deliverable:** reps stop using Desk for daily work.

## Phase N5 — SLA, escalation, analytics (3–5 days)

- `tasks/crm_sla.py` breach detection and escalation ladder
- Notifications for stage ageing and overdue actions
- Funnel analytics per type/source/company in the existing `AnalyticsPage`
- **Deliverable:** management metrics; nothing sits unattended.

**Total: 19–29 days.** N1 alone makes the flow real; N3 delivers the highest immediate commercial return.

## What is deliberately not built

| Deferred | Build when |
|---|---|
| Lead scoring | Volume exceeds human triage |
| Auto-nurture drip sequences | Unresponsive-lead volume justifies it |
| Forecasting / weighted pipeline | Management asks twice (native `probability` × amount covers v1) |
| WhatsApp chatbot qualification | After Phase B AI layer proves out |
| Distributor self-service portal | A different product |
| Reverse-scheduling engine (gifting) | Start with a warning banner; automate when volume hurts |
| Custom quotation UI | Desk `Quotation` is genuinely good; do not rebuild it |

---

# Part 13 — Open Decisions

| # | Decision | Why it matters | Recommendation |
|---|---|---|---|
| **Q1** | One ERPNext site with multiple Companies, or site-per-company? | Premise P1. Site-per-company breaks the single intake mouth and forces a central intake site | **Consolidate.** The single funnel is the design's core value |
| **Q2** | Retailer channel policy: is direct-to-retail ever allowed in covered territory? | Changes the §6.3 gate from hard to advisory | Confirm with channel sales before N1 |
| **Q3** | OEM: one pipeline with `design_by`, or two? | Splitting yields two half-maintained pipelines | **Keep merged** unless owned by different teams *and* priced by different logic |
| **Q4** | IndiaMART: direct enquiries, purchased Buy Leads, or both? | Different intent quality → different SLA and junk expectations | Separate `Lead Source` rows if both |
| **Q5** | Does the Opportunity own `items[]`, or does quoting start at Quotation? | Affects §6 field sets and pipeline value accuracy | **Opportunity owns items** — otherwise pipeline value is a guess |
| **Q6** | Auto-ack on organic WhatsApp/IG too, or marketplace only? | Auto-replying to a returning customer reads as robotic | **Marketplace only** in v1 |
| **Q7** | Is `Outlet` a `Customer` or its own doctype? | HLD-002 A1 open item; §6.3 routing depends on it | Resolve with the fieldforce design |
| **Q8** | What happens to `apps/crm` on this bench? | Two CRM data models installed on one site invites confusion | Uninstall from the site after N1 sign-off; keep the fork repo for reference |

---

## Appendix A — Field reference (verified against installed source)

**Lead** — `naming_series, lead_name, company_name, email_id, lead_owner, status, salutation, gender, source, customer, campaign_name, phone, mobile_no, whatsapp_no, phone_ext, type, market_segment, industry, request_type, company, website, territory, unsubscribed, title, language, first_name, middle_name, last_name, no_of_employees, qualified_by, qualified_on, qualification_status, job_title, annual_revenue, notes, disabled, city, state, country`
`status` ∈ Lead, Open, Replied, Opportunity, Quotation, Lost Quotation, Interested, Converted, Do Not Contact
`qualification_status` ∈ Unqualified, In Process, Qualified

**Opportunity** — `opportunity_from, party_name, customer_name, title, opportunity_type, status, order_lost_reason, expected_closing, currency, opportunity_amount, sales_stage, probability, items, territory, customer_group, contact_person, contact_email, contact_mobile, source, campaign, company, transaction_date, lost_reasons, first_response_time, base_opportunity_amount, base_total, total, conversion_rate, competitors, no_of_employees, annual_revenue, industry, market_segment, opportunity_owner, website, whatsapp, phone, notes, city, state, country`
`status` ∈ Open, Quotation, Converted, Lost, Replied, Closed

**Opportunity Item** — `item_code, item_name, item_group, brand, description, qty, uom, rate, amount, base_rate, base_amount, image`

**Prospect** — `company_name, industry, market_segment, customer_group, territory, no_of_employees, annual_revenue, website, prospect_owner, company, notes, opportunities[], leads[]`

**Omni Identity** — `display_name, status, primary_phone, primary_email, primary_whatsapp, normalized_phone, normalized_email, hash_fingerprint, channels[], linked_entities[], is_master, merged_into, merge_group_id, needs_review, potential_duplicate_of, is_spam, aliases[], ai_profile_summary`

**Omni Identity Link** — `linked_doctype, linked_name, role` (Unknown | Decision Maker | Billing | Technical | Primary Contact | Influencer)

**Excom Thread** — `omni_identity, display_name, primary_phone, channel, account_doctype, account, thread_key, status, assigned_to, assigned_team, priority, unread_count, last_message_direction, last_message_preview, tags[], last_message_at, last_inbound_at, last_outbound_at`

**Excom Team** — `team_name, parent_team, description, members[]` · **Excom Team Member** — `user, role`

## Appendix B — Existing excom services reused

| Service | Reused for |
|---|---|
| `services/identity_hooks.py` | Lead/Customer/Contact identity creation; already hooks native CRM |
| `services/identity_sync.py` | Entity-graph walk; extend with Opportunity + Prospect |
| `services/thread_service.py` | Thread creation and re-pointing |
| `services/whatsapp_service.py` | Auto-ack and template sends |
| `services/gmail_service.py` | Inbound email → thread |
| `services/broadcast_service.py` | Campaigns and segments |
| `services/subscriber_rules.py` | Dynamic CRM segments via the `"*"` doc-event hook |
| `services/delivery_watchdog.py` | Pattern to copy for `crm_sla.py` |
| `api/chat.py`, `api/teams.py`, `api/analytics.py` | Conversation, team and metric endpoints |
| `frontend/src/components/OmniIdentityPanel.tsx`, `hooks/useLinkedEntities.ts`, `hooks/useRelatedInvoices.ts` | The record cockpit's right pane |
