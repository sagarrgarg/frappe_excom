# High Level Design — Unified Sales, Service & Field Platform

**Document:** HLD-002 (supersedes HLD-001)
**Version:** 2.0
**Date:** 2026-08-30
**Status:** Draft for review
**Owner:** Sagar Ratan Garg

---

## Table of Contents

1. [Context, Scope, Actors and Scale](#part-1--context-scope-actors-and-scale)
2. [The Lifecycle Spine — First Touch to Maintenance](#part-2--the-lifecycle-spine--first-touch-to-maintenance)
3. [System Architecture — Sites, Apps, Integrations](#part-3--system-architecture--sites-apps-integrations)
4. [Domain Architecture — Contexts, Ownership, Handoffs](#part-4--domain-architecture--contexts-ownership-handoffs)
5. [Cross-Cutting Architecture](#part-5--cross-cutting-architecture)
6. [Roadmap, Risks and Decisions](#part-6--roadmap-risks-and-decisions)

---

# Part 1 — Context, Scope, Actors and Scale

## 1.1 What this platform is

A multi-company group running **four distinct sales motions** plus after-sales service,
on a Frappe stack, pan-India.

| Motion | Shape | Volume | Cycle | Primary surface |
|---|---|---|---|---|
| **Website / inbound enquiry** | Form → lead → qualify | High count, low value each | Days | Frappe CRM |
| **Generic enquiry** (phone, email, WhatsApp, referral) | Unstructured → lead | Medium | Days–weeks | Frappe CRM + Excom |
| **Export sales** | Long-cycle B2B, multi-currency, documentation | Low count, high value | Weeks–months | Frappe CRM → ERPNext |
| **Field / distribution sales** | Beat-based outlet coverage, repeat ordering | Very high | Daily cycle | `fieldforce` |
| **Tele sales** | Outbound call lists, dispositions | High | Hours–days | Excom + CRM |
| **After-sales service** | Tickets, AMC visits, warranty | Medium | Ongoing | Helpdesk / ERPNext + `fieldforce` |

The architectural problem is not any one motion. It is that **all six must share one
customer identity, one conversation history, and one org hierarchy**, while each keeps
the tool that actually fits its shape.

## 1.2 Design principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **One system of record per fact** | No fact is writable in two places |
| P2 | **Tools are chosen per motion, not per company** | CRM for funnels, `fieldforce` for beats, Helpdesk for tickets |
| P3 | **The funnel hands off; it does not fork** | CRM owns a party *until conversion*, then becomes read-only history |
| P4 | **Conversation is a separate layer from record** | Excom holds every message regardless of which app the record lives in |
| P5 | **Identity is the spine** | `Omni Identity` links a person/shop across CRM, ERPNext, Helpdesk, `fieldforce` |
| P6 | **HR is central; operations are federated** | One HRMS site, N operational sites |
| P7 | **Fail closed on visibility** | No hierarchy link ⇒ see nothing |
| P8 | **Extend upstream apps, never fork them** | Form Scripts and PRs, not patches |

## 1.3 Goals and success measures

| # | Goal | Measure |
|---|---|---|
| G1 | Single customer view across all motions | 1 identity per party; 0 unlinked conversations |
| G2 | First-touch to conversion is traceable | 100% of Customers carry a `first_touch` provenance |
| G3 | Field coverage | > 90% of planned outlets visited per cycle |
| G4 | Geo-verified visits | > 95% within outlet geofence |
| G5 | Inbound response time | Median first response < 30 min in business hours |
| G6 | Export deal hygiene | 0 deals without a next action > 7 days |
| G7 | Service SLA | > 95% first response within SLA |
| G8 | Hierarchy enforcement | 0 records visible outside the viewer's subtree |
| G9 | Scale | 500 SRs · 200k outlets · 60k msg/day · 200k-recipient campaigns |

## 1.4 Non-goals for v1

- Offline-first architecture (only three writes are network-tolerant — §5.6)
- Native WhatsApp Catalog / Meta Commerce Manager product messages
- Travel-time route optimisation (beats are authored by people who know the market)
- Replacing the HRMS, payroll or statutory compliance
- Forking Frappe CRM or Helpdesk
- A separate native mobile binary (PWA only)

## 1.5 Actors

| Actor | Home surface | Lifecycle stage |
|---|---|---|
| Website visitor | Public web form | First touch |
| Inbound executive | Frappe CRM | Touch → qualify |
| Export sales manager | Frappe CRM → ERPNext | Qualify → convert → order |
| Telecaller | Excom console + CRM | Touch → qualify |
| Sales Representative (SR) | Field app (PWA) | Order → serve |
| Area Sales Manager (ASM) | Manager dashboard | Oversight |
| Regional / Zonal Manager | Manager dashboard | Oversight |
| Service engineer | Field app (service mode) | Maintain |
| Support agent | Helpdesk + Excom | Serve → maintain |
| Customer service agent | Excom inbox | All stages |
| Accounts / back-office | ERPNext Desk | Order → invoice |
| HR / Admin | HRMS site | Cross-cutting |
| Distributor (external) | WhatsApp / portal | Order → fulfil |
| Retail outlet (external) | WhatsApp | Order → serve |
| Platform admin | Desk on each site | Cross-cutting |

## 1.6 Scale targets

| Dimension | v1 | Headroom |
|---|---|---|
| Operating companies (sites) | 3–5 | 20 |
| HRMS sites | 1 | 1 |
| SRs | 100–200 | 500 |
| Telecallers | 50 | 150 |
| Service engineers | 20 | 100 |
| Distributors (ERPNext Customers) | 500–2,000 | 10,000 |
| Retail outlets | 50,000 | 200,000 |
| Inbound leads / month | 3,000 | 20,000 |
| Export opportunities / year | 500 | 3,000 |
| Beat visits / day | 8,000 | 40,000 |
| GPS pings / day | 100,000 | 500,000 |
| `Excom Message` | 10–20M / yr | 50M |
| Campaign recipients | 50,000 | 200,000 |
| Service tickets / month | 2,000 | 15,000 |

## 1.7 Assumptions requiring confirmation

| # | Assumption | If wrong |
|---|---|---|
| A1 | Field orders are **secondary sales** (distributor fulfils) | `Outlet` collapses to `Customer`; `Secondary Order` → `Sales Order` |
| A2 | An outlet belongs to one distributor at a time | Needs an `Outlet Distributor` bridge with validity dates |
| A3 | One HRMS site serves all companies | Multi-HRMS needs a site dimension on the bridge |
| A4 | Export and inbound both live in CRM | If export needs ERPNext-native quoting from day one, skip CRM for that motion |
| A5 | Service uses Helpdesk for tickets, ERPNext for AMC visits | A single tool for both changes §4.6 |
| A6 | Beat cycles are weekly/fortnightly and human-planned | Dynamic beat generation is a separate engine |

---

# Part 2 — The Lifecycle Spine — First Touch to Maintenance

This is the organising idea of the platform. Every architectural decision below is
justified by where it sits on this spine.

## 2.1 The seven stages

```
 ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
 │ 1 TOUCH  │─▶│2 QUALIFY │─▶│3 CONVERT │─▶│ 4 ORDER  │─▶│ 5 FULFIL │─▶│ 6 SERVE  │─▶│7 RETAIN  │
 └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
  web form      call/email     Lead →        SO / Sec.     Delivery      Ticket        Repeat
  FB lead       WhatsApp       Customer      Order         Invoice       AMC visit     order
  enquiry       site visit                                 Payment       Warranty      Catalogue
  walk-in       demo                                                     Complaint     Campaign

 SYSTEM OF RECORD PER STAGE
 ┌──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐
 │ CRM      │ CRM      │ CRM→ERP  │ ERPNext  │ ERPNext  │ Helpdesk │ ERPNext  │
 │          │          │  handoff │fieldforce│          │ +ERPNext │ +excom   │
 └──────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘

 CONVERSATION LAYER — spans every stage, never changes owner
 ╔═══════════════════════════════════════════════════════════════════════════╗
 ║  EXCOM — Omni Identity · Thread · Message · Call · Catalogue · Broadcast   ║
 ╚═══════════════════════════════════════════════════════════════════════════╝

 IDENTITY SPINE — one identity from first touch to retention
 ═════════════════════════════════════════════════════════════════════════════
   Omni Identity ── links ──▶ CRM Lead · CRM Deal · Customer · Outlet · HD Ticket
```

## 2.2 Stage detail

### Stage 1 — TOUCH

| Channel | Entry mechanism | Lands as |
|---|---|---|
| Website form | Core Frappe `Web Form`, served by CRM's public page (`/crm-form/<route>`) | `CRM Lead` |
| Facebook / Instagram lead ad | `crm/lead_syncing` — `Facebook Page`, `Facebook Lead Form`, `Lead Sync Source` | `CRM Lead` |
| Inbound WhatsApp | Excom webhook → `Excom Thread` | `Omni Identity` (+ `CRM Lead` if new) |
| Inbound email | Excom Gmail poller | `Omni Identity` (+ `CRM Lead` if new) |
| Inbound call | Excom voice webhook | `Excom Call` (+ `CRM Lead` if new) |
| Export enquiry | Web form / email / trade portal | `CRM Lead` tagged `Export` |
| Walk-in / referral | Manual entry | `CRM Lead` |
| Field prospect | Field app — new outlet capture | `Outlet` (status `Prospect`) |

**First-touch provenance is captured once and never overwritten.** `source`, `campaign`,
`channel`, `first_touch_at`, `first_touch_by` travel with the identity all the way to
retention, so attribution survives conversion.

### Stage 2 — QUALIFY
Owner: inbound executive / telecaller / export manager, in CRM.
Assignment via core Frappe **Assignment Rule** (round-robin, load-based, or by
territory/product condition). Conversations continue in Excom and are visible in CRM
through the embedded panel (§3.5). Exit criteria are per motion — an export enquiry needs
specification and Incoterms; a distribution enquiry needs a serviceable pin code.

### Stage 3 — CONVERT
The single most important boundary in the platform.

```
CRM Lead  ──qualified──▶  CRM Deal  ──won──▶  ERPNext Customer     (+ Outlet, if retail)
                                                     │
                             CRM becomes READ-ONLY history for this party
                                                     ▼
                                  All further records live in ERPNext / fieldforce
```

On conversion the platform must, atomically from the user's point of view:
1. Create/locate the ERPNext `Customer` (existing `create_customer_in_erpnext` path)
2. Create the `Outlet` when the party is a retail shop
3. Re-point `Omni Identity Link` to the new `Customer`/`Outlet`, **retaining** the CRM links
4. Stamp `first_touch_*` provenance onto the `Customer`
5. Mark the CRM Deal `Converted` and lock it against further edits
6. Post a system message into the Excom thread recording the conversion

### Stage 4 — ORDER
Three paths, one customer:

| Path | Document | Captured by |
|---|---|---|
| Field / distribution | `Secondary Order` **[A1]** | Field app at the outlet |
| Export / project | `Quotation` → `Sales Order` | ERPNext Desk |
| Tele sales | `Sales Order` | Telecaller console |

### Stage 5 — FULFIL
ERPNext native: Delivery Note, Sales Invoice, Payment Entry. Excom sends the
confirmations, dispatch notices and payment reminders. No new components.

### Stage 6 — SERVE
| Need | System |
|---|---|
| Reactive complaint / query | **Helpdesk** `HD Ticket` (SLA, teams, escalation, KB) |
| Warranty claim | ERPNext `Warranty Claim` |
| Scheduled AMC | ERPNext `Maintenance Schedule` → `Maintenance Visit` |
| On-site engineer visit | **`fieldforce` service mode** — the same check-in / geo / photo / checkout model as a beat visit |
| The conversation | Excom, always |

**Field service reuses the field sales engine.** A `Maintenance Visit` and a `Beat Visit`
are the same shape: travel to a location, prove presence, do work, record outcome,
capture evidence. One app, two visit types.

### Stage 7 — RETAIN
Repeat-order prompts from beat cadence; catalogue and scheme campaigns via Excom
Broadcast; NPS/feedback after service; win-back lists for lapsed outlets (`Outlet` derived
fields `last_order_date`, `orders_last_90d`).

## 2.3 Lifecycle tracking — the `Party Journey` view

A single read model answering *"everything that ever happened with this party"*, assembled
across systems by `Omni Identity`:

```
Omni Identity  OMNI-0009912   "Sharma General Store"
├── first_touch : Web Form "Dealer Enquiry"  · 2026-02-11 · campaign=SPRING26
├── CRM Lead    CRM-LEAD-00412  Qualified   → Deal CRM-DEAL-00190  Won 2026-03-02
├── Customer    CUST-00877       created 2026-03-02
├── Outlet      OUT-2026-01422   Karol Bagh beat, class B
├── Orders      37 secondary orders · ₹4.2L · last 2026-08-24
├── Visits      112 beat visits · 91% coverage · 68% strike rate
├── Service     2 tickets (both closed) · 1 AMC visit due 2026-09-15
└── Conversation 1 Excom thread · 214 messages · 9 calls · 6 catalogues sent
```

This is a **view**, not a table. It is assembled on demand from the owning systems and
cached briefly. Materialising it would create the duplicate-master problem P1 forbids.

---

# Part 3 — System Architecture — Sites, Apps, Integrations

## 3.1 Site topology

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  HRMS SITE — hr.group.com                      ONE SITE, ALL COMPANIES        ║
║  ─────────────────────────────────────────────────────────────────────────    ║
║  frappe · indian_hrms_compliance                                              ║
║                                                                               ║
║  Employee · Employee Checkin (lat/lng/geolocation) · Shift Type               ║
║  Shift Location (checkin_radius) · Attendance · Leave · Payroll               ║
║  PF ECR · ESI · Form 16/24Q · POSH · LWF · PT · Gratuity                      ║
║  Data Consent · Data Retention Rule · DPDP Compliance Profile                 ║
║                                                                               ║
║  ══════ AUTHORITY: person, employment, attendance, statutory HR ══════        ║
╚═══════════════════════════════╤═══════════════════════════════════════════════╝
                                │
                REST / FrappeClient(api_key, api_secret)
                read-mostly · one write (check-in mirror) · never blocking
                                │
        ┌───────────────────────┼───────────────────────┬──────────────────┐
        ▼                       ▼                       ▼                  ▼
╔═══════════════════╗  ╔═══════════════════╗  ╔═══════════════════╗  ╔══════════╗
║ OPERATIONAL SITE  ║  ║ OPERATIONAL SITE  ║  ║ OPERATIONAL SITE  ║  ║   ...    ║
║   Company A       ║  ║   Company B       ║  ║   Company C       ║  ║          ║
║ ───────────────── ║  ║                   ║  ║                   ║  ║          ║
║ frappe            ║  ║ (same app set,    ║  ║ (same app set,    ║  ║          ║
║ erpnext           ║  ║  independently    ║  ║  independently    ║  ║          ║
║ crm               ║  ║  configured)      ║  ║  configured)      ║  ║          ║
║ excom             ║  ║                   ║  ║                   ║  ║          ║
║ fieldforce  (new) ║  ║                   ║  ║                   ║  ║          ║
║ helpdesk          ║  ║                   ║  ║                   ║  ║          ║
║ payments          ║  ║                   ║  ║                   ║  ║          ║
║ india_compliance  ║  ║                   ║  ║                   ║  ║          ║
║                   ║  ║                   ║  ║                   ║  ║          ║
║ WABA number A     ║  ║ WABA number B     ║  ║ WABA number C     ║  ║          ║
║ Voice account A   ║  ║ Voice account B   ║  ║ Voice account C   ║  ║          ║
╚═══════════════════╝  ╚═══════════════════╝  ╚═══════════════════╝  ╚══════════╝
```

**Why HR is central and operations are federated**

| | Rationale |
|---|---|
| HR central | An employee can transfer between group companies without a data migration. Statutory filings (PF, ESI, TDS) are per PAN/TAN and already modelled multi-company. Payroll is one process, one team, one audit trail |
| Operations federated | A WhatsApp Business Account is per brand. Legal entities must not share customer data. Company-level performance isolation. An operational outage in one company must not stop another. Independent upgrade windows |

**Cost of the split:** every employment fact arrives over HTTP. §5.5 defines the caching
and degradation contract that makes this safe.

## 3.2 App matrix per operational site

| App | Role | Lifecycle stages | Notes |
|---|---|---|---|
| `frappe` | Platform | all | Assignment Rule, Web Form, Email Template are core — available to every app |
| `erpnext` | **System of record** | 3–7 | Customer, Item, Item Price, Sales Person, Territory, Quotation, SO, Invoice, Maintenance, Warranty |
| `crm` | **Inbound funnel** | 1–3 only | Web forms, Facebook sync, lead/deal pipeline. **Read-only after conversion** |
| `excom` | **Conversation layer** | all | WhatsApp, Email, Instagram, Voice, Web Chat, Catalogue, Broadcast, Identity |
| `fieldforce` | **Field execution** | 4, 6 | Outlets, beats, visits, orders, tracking, service visits |
| `helpdesk` | **Reactive service** | 6 | Tickets, SLA, escalation, knowledge base |
| `payments` | Payment gateways | 5 | |
| `india_compliance` | GST / e-invoice / e-way | 5 | |

## 3.3 Layered view

```
┌────────────────────────────────────────────────────────────────────────────┐
│ L4  SURFACES                                                               │
│  CRM UI (inbound, export) │ Field App PWA │ Manager Dashboard              │
│  Excom Inbox │ Telecaller Console │ Helpdesk Agent UI │ ERPNext Desk        │
├────────────────────────────────────────────────────────────────────────────┤
│ L3  APPLICATION SERVICES                                                   │
│  Lead capture · Assignment · Conversion handoff · Beat planning            │
│  Visit lifecycle · Order capture · Track ingestion · Catalogue render      │
│  Call routing · Broadcast fan-out · SLA engine · AMC scheduling            │
├────────────────────────────────────────────────────────────────────────────┤
│ L2  DOMAIN                                                                 │
│  crm:        CRM Lead · CRM Deal · Web Form binding · Lead Sync Source     │
│  erpnext:    Customer · Item · Item Price · Sales Person · Territory       │
│              Quotation · Sales Order · Invoice · Maintenance · Warranty    │
│  fieldforce: Outlet · Beat · Journey Plan · Beat Visit · Field Track       │
│  excom:      Omni Identity · Thread · Message · Call · Catalogue           │
│  helpdesk:   HD Ticket · HD Team · HD SLA                                  │
├────────────────────────────────────────────────────────────────────────────┤
│ L1  PLATFORM   Frappe ORM · Auth · RQ · Realtime │ MariaDB │ Redis         │
├────────────────────────────────────────────────────────────────────────────┤
│ L0  EXTERNAL   WhatsApp Cloud · Exotel/Airtel · Gmail · Facebook Graph     │
│                Maps · FCM · HRMS site (REST) · Payment gateways            │
└────────────────────────────────────────────────────────────────────────────┘
```

## 3.4 Integration inventory

| # | Integration | Direction | Mode | Frequency | Failure mode |
|---|---|---|---|---|---|
| I1 | HRMS → site: employee master | pull | REST | nightly + on demand | cache, 24h |
| I2 | HRMS → site: shift + attendance | pull | REST | day start | cache, 4h |
| I3 | site → HRMS: field check-in mirror | push | REST, queued | per first check-in | retry 24h, then exception report |
| I4 | Web Form → CRM Lead | in-process | core Frappe | per submission | Frappe error log |
| I5 | Facebook Graph → CRM Lead | pull | REST | 5/10/15-min cron | `Failed Lead Sync Log` |
| I6 | CRM Deal → ERPNext Customer | in-process | conversion service | per conversion | blocks conversion, surfaced |
| I7 | Any record ↔ Excom identity | in-process | excom API | per create/update | queued, idempotent |
| I8 | Excom ↔ WhatsApp Cloud | both | HTTPS + webhook | per message | retry with backoff |
| I9 | Excom ↔ Exotel / Airtel | both | HTTPS + webhook | per call | reconcile job |
| I10 | Excom ↔ Gmail | both | API + poller | 1 min | token monitor |
| I11 | fieldforce → Excom (catalogue, call) | in-process | excom API | per action | degrade, hide buttons |
| I12 | fieldforce → Maps | pull | HTTPS | geocode only, cached | leave blank, capture on visit |
| I13 | Field app → fieldforce | push | REST | batched | client outbox |
| I14 | ERPNext Maintenance → fieldforce visit | in-process | hook | per schedule | retry |
| I15 | Helpdesk ↔ Excom thread | in-process | excom API | per ticket | degrade |
| I16 | fieldforce → FCM | push | HTTPS | per notification | drop |

## 3.5 Excom inside CRM and Helpdesk

Both CRM and Helpdesk are upstream apps and **must not be forked** (P8). Excom surfaces
inside them through supported extension points:

| Surface | Mechanism | Status |
|---|---|---|
| Action buttons on CRM Lead/Deal | `CRM Form Script` → `this.actions.push()`, inserted from `excom/setup.py::after_migrate` | Available today |
| Conversation summary in CRM side panel | Custom Field (HTML) + `CRM Fields Layout` section + `setFieldHtml()` (DOMPurify-sanitised) | Available today |
| Full excom inbox in a CRM dialog | `createDialog({size:'7xl', html:'<iframe …?embed=1>'})` — `dialogs.jsx` renders raw `v-html`, so an iframe survives | Available today |
| A real excom **tab** on CRM Lead/Deal | Requires upstream extensibility — **PR frappe/crm#2743** (open, Greptile 5/5) | Pending upstream |
| Helpdesk ticket ↔ excom thread | `HD Form Script` (Helpdesk has the same mechanism) | To be confirmed |

**`?embed=1` skin.** When excom is embedded, `data-skin="frappe"` remaps its CSS variables
to frappe-ui tokens so the panel looks native inside CRM/Helpdesk while the standalone app
keeps its own visual language. One CSS file, no component changes.

---

# Part 4 — Domain Architecture — Contexts, Ownership, Handoffs

## 4.1 Bounded contexts

```
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ PEOPLE  (HRMS site)  │  │  FUNNEL  (crm)       │  │ COMMERCE (erpnext)   │
│ Employee             │  │  CRM Lead            │  │ Customer, Supplier   │
│ Employee Checkin     │  │  CRM Deal            │  │ Item, Item Price     │
│ Shift Type/Location  │  │  Web Form binding    │  │ Sales Person (tree)  │
│ Attendance, Payroll  │  │  Lead Sync Source    │  │ Territory (tree)     │
│ Data Consent (staff) │  │  Assignment Rule*    │  │ Quotation, SO, Inv   │
└──────────┬───────────┘  └──────────┬───────────┘  │ Maintenance, Warranty│
           │ employee_id             │ on conversion└──────────┬───────────┘
           │                         └────────────────────────▶│
           │                                                    │
   ┌───────▼────────────────────────────────────────────────────▼─────────┐
   │  FIELD EXECUTION  (fieldforce)                                       │
   │  Outlet · Beat · Beat Outlet · Journey Plan · Beat Visit             │
   │  Field Track · Field Employee Link · Secondary Order · Service Visit │
   └───────────────────────────────┬──────────────────────────────────────┘
                                   │
   ┌───────────────────────────────▼──────────────────────────────────────┐
   │  ENGAGEMENT  (excom)   — spans every context, owns none of their data│
   │  Omni Identity · Thread · Message · Call · Catalogue · Broadcast     │
   └───────────────────────────────┬──────────────────────────────────────┘
                                   │
   ┌───────────────────────────────▼──────────────────────────────────────┐
   │  SERVICE  (helpdesk + erpnext)                                       │
   │  HD Ticket · HD Team · HD SLA │ Warranty Claim · Maintenance Schedule│
   └──────────────────────────────────────────────────────────────────────┘

   * Assignment Rule is core Frappe, usable by every context.
```

## 4.2 Ownership matrix

| Fact | Authority | Replicated | How others read it |
|---|---|---|---|
| Person, employment, payroll | HRMS site | No | REST, cached ids + display only |
| Shift, geofence radius | HRMS site | No | REST, cached 4h |
| Attendance for payroll | HRMS site | Mirrored | Site writes local visit, mirrors async |
| Pre-conversion lead/deal | `crm` | No | Native link; read-only post-conversion |
| First-touch provenance | `crm` at creation, **copied once** to Customer | Yes, immutable | Native field |
| Customer, Supplier | `erpnext` | No | Native link |
| Item, price, stock | `erpnext` | No | Native link |
| Sales Person tree, Territory tree | `erpnext` | No | Native link; nested-set query |
| Outlet | `fieldforce` | No | Native link |
| Beat, journey plan, visit | `fieldforce` | No | Native link |
| GPS trail | `fieldforce` | Redis hot + doc cold | API |
| Conversation, call, catalogue | `excom` | No | Excom API |
| Ticket, SLA | `helpdesk` | No | Helpdesk API |
| AMC schedule, warranty | `erpnext` | No | Native link |

**Provenance is the single sanctioned copy.** `first_touch_source`, `first_touch_campaign`,
`first_touch_at`, `first_touch_channel` are copied from the CRM Lead to the ERPNext
Customer at conversion and then never updated. This is a deliberate, bounded exception to
P1: without it, attribution dies at the conversion boundary.

## 4.3 The conversion handoff contract

The most failure-prone transition in the platform. Defined as a contract, not a script.

**Preconditions**
- Deal status is `Won`
- A valid `Omni Identity` exists
- No `Customer` already linked to this identity (else it is a re-order, not a conversion)

**Postconditions (all or none)**
1. `Customer` exists with provenance stamped
2. `Outlet` exists when `party_type = Retail`
3. `Omni Identity Link` includes the new Customer/Outlet **and retains** CRM Lead/Deal links
4. `CRM Deal.status = Converted`, locked
5. A system message is posted to the Excom thread
6. A `Conversion Log` row records old ids, new ids, actor, timestamp

**Failure handling** — the whole handoff runs in one transaction; on failure nothing is
committed and the operator sees the specific reason. Partial conversion is the worst
possible outcome and is designed out rather than retried.

**Idempotency** — keyed on `(crm_deal)`. A repeated call returns the existing Customer.

## 4.4 Identity spine

```
              ┌──────────────────────────────────────────┐
              │            Omni Identity                 │
              │  display_name, normalized_phone/email    │
              │  first_touch_* provenance                │
              └───┬────────────┬───────────┬─────────┬───┘
                  │            │           │         │
      Omni Identity Link (polymorphic: linked_doctype + linked_name)
                  │            │           │         │
             ┌────▼───┐  ┌─────▼────┐ ┌────▼───┐ ┌───▼──────┐
             │CRM Lead│  │ Customer │ │ Outlet │ │HD Ticket │
             │CRM Deal│  │ Supplier │ │        │ │          │
             └────────┘  └──────────┘ └────────┘ └──────────┘
                  │            │           │         │
              ┌───▼────────────▼───────────▼─────────▼───┐
              │       Excom Thread(s) · Messages · Calls │
              └──────────────────────────────────────────┘
```

`Omni Identity Link` is already polymorphic in excom, so adding CRM Lead, Outlet and
HD Ticket is **data, not schema**.

**Deduplication** is the hard part at 200k outlets plus 20k leads/year. Strategy:
normalised phone as the primary key, then `(name + pincode)` fuzzy match, with
`potential_duplicate_of` and `needs_review` (both already exist in `Omni Identity`) driving
a human merge queue. Never auto-merge across contexts.

## 4.5 Visibility model

Three axes, precedence order: **People (Sales Person subtree) → Geography (Territory
subtree) → Function (Team membership)**.

| Doctype | Rule |
|---|---|
| `CRM Lead` / `CRM Deal` | assigned-to, or owner in my Sales Person subtree |
| `Customer` | sales team member in my subtree, or territory in my subtree |
| `Outlet`, `Beat`, `Journey Plan` | owning Sales Person in my subtree, or territory in my subtree |
| `Beat Visit`, `Field Track`, `Secondary Order` | visiting Sales Person in my subtree |
| `Excom Thread` | my Excom teams **or** linked party's Sales Person in my subtree |
| `Excom Call` | participant, ring-set member, or subtree |
| `HD Ticket` | my HD team, or linked customer in my subtree |
| Dashboards | strictly subtree-aggregated |

**Fail closed.** No `Field Employee Link` ⇒ `1=0`. One role, `Group Global Viewer`, sees
everything; it is granted individually and audited.

**CRM's own hierarchy is switched off.** `CRM Sales Hierarchy` and `CRM Territory` remain
uninstalled/unused; the enforcement logic from `crm/permissions/org_hierarchy.py` is ported
onto ERPNext `Sales Person` so there is exactly one org tree.

## 4.6 Service architecture

| Need | System | Why |
|---|---|---|
| Reactive ticket, SLA, escalation, KB | `helpdesk` `HD Ticket` | Purpose-built; already in the bench |
| Warranty claim on a serial | ERPNext `Warranty Claim` | Links serial, item, AMC expiry |
| Scheduled AMC | ERPNext `Maintenance Schedule` → `Maintenance Visit` | Generates the visit calendar |
| Engineer on site | **`fieldforce` service visit** | Same check-in/geo/photo/checkout engine as a beat visit |
| The conversation | `excom` | One thread per identity, regardless of ticket count |

`Beat Visit` gains a `visit_type` (`Sales` / `Service`) and an optional
`maintenance_visit` link. Everything else — geofence, photos, outcome, tracking — is shared.
This is the highest-leverage reuse in the design: field service ships as a variant of
field sales rather than a second app.

---

# Part 5 — Cross-Cutting Architecture

## 5.1 Performance budget

| Interaction | p95 budget |
|---|---|
| Web form submit → lead created | < 1.5 s |
| CRM lead list (subtree-filtered) | < 1.2 s |
| Excom panel inside CRM | < 1.5 s |
| Field app cold start (4G) | < 3 s |
| Load today's beat | < 1.2 s |
| Check-in round trip | < 800 ms |
| Order submit (20 lines) | < 1.5 s |
| Catalogue send | < 3 s |
| Live map (200 SRs) | < 1 s |
| Coverage dashboard (12 months, 200-SR subtree) | < 2 s |
| Helpdesk ticket open with conversation | < 1.5 s |
| Inbound webhook ack (WhatsApp / voice) | < 300 ms |

## 5.2 The three load-bearing scale decisions

### 5.2.1 GPS ingestion — never one document per ping

```
500 SRs × 8 h × 1 ping/min = 240,000 pings/day
```

As documents that is 240k full lifecycles, **plus** each would fire excom's
`doc_events["*"]` wildcard (which today runs uncached queries on every save) — roughly
720,000 wasted queries/day from tracking alone.

```
device (buffers 5 min)
   └─▶ POST batch ─▶ Redis ZSET  track:{company}:{employee}:{date}   TTL 36h
                          │                      ← live map reads ONLY this
                          └─ flush every 15 min ─▶ Field Track: ONE doc per SR per day
                                                    (encoded polyline, ~2.4 KB)
```

**500 documents/day instead of 240,000.**

### 5.2.2 Broadcast fan-out — never one serial job

Today `execute_broadcast` loops every subscriber in one job with a synchronous HTTP call
each: at 100k recipients that is 8+ hours in one worker, no rate limiting, no retry, no
resume. Replaced by: chunk into `Excom Broadcast Batch` rows of 500 → one job per batch on
a dedicated `broadcast` queue → per-WABA token bucket → backoff and requeue on 429.
Restart resumes at batch granularity.

### 5.2.3 Dashboards — never aggregate live transactions

`Field Daily Snapshot` (one row per sales_person per day) is built nightly and read from a
**read replica**. Manager rollups become a range scan over a few thousand rows instead of
an aggregation over millions of visits.

## 5.3 Queue segregation

| Queue | Workers | Carries | Latency need |
|---|---|---|---|
| `short` | 4 | webhooks, track ingest, check-in mirror | < 1 s |
| `default` | 4 | doc events, UI jobs, catalogue render | seconds |
| `long` | 2 | employee sync, flush, snapshots, cycle close | minutes |
| `broadcast` | 6 | campaign batches | throughput |
| `voice` | 2 | call events, recording fetch | < 2 s |

Campaign traffic must never starve inbound conversation or call processing.

## 5.4 Data growth and retention

| Table | Yr-1 rows | Retention | Strategy |
|---|---|---|---|
| `CRM Lead` / `CRM Deal` | 25k / 8k | 5 years | Native |
| `Customer` | 2k | permanent | |
| `Outlet` | 50k–200k | permanent | Index `(territory,status)`, `(lat,lng)` |
| `Beat Visit` | 2.5M | 3 years | Index `(sales_person,visit_date)`, `(outlet,visit_date)` |
| `Field Track` | 180k | **90 days** | Retention rule purge |
| `Secondary Order` + items | 1.5M + 12M | 3 years | Partition items by year |
| `Excom Message` | 10–20M | 18 months | Archive table; indexed `(thread,creation)` |
| `Excom Broadcast Log` | 5–10M | 12 months | **Add index `(broadcast,status)`** |
| `Excom Call` | 2–5M | 24 mo; recordings 6 mo | Index `(omni_identity,creation)` |
| `HD Ticket` | 25k | 5 years | Native |

## 5.5 Availability and degradation

| Dependency down | Degraded behaviour | Must never happen |
|---|---|---|
| HRMS site | Shift/employee from 4h cache; check-ins queue for mirroring | SR blocked from starting the beat |
| CRM | Field and service unaffected (no dependency) | Field outage |
| Excom | CRM/field lose comms buttons only | Record loss |
| WhatsApp API | Queue and retry with backoff | Message loss |
| Voice provider | Click-to-call disabled with a clear message | Silent failure |
| Redis | Live map down; ingestion flushes direct | Ping loss beyond buffer |
| Read replica | Dashboards fall back to primary with a banner | Reporting load silently on primary |
| Device network | Three writes queue locally | Lost order; visit shown as not-made |

**Write tolerance is scoped to exactly three actions** — check-in, order submit,
check-out. GPS gaps are acceptable; dashboards may require connectivity. Roughly 5 days of
work, with `posawesome/frontend/src/offline/` as the in-bench reference.

## 5.6 Security

| Control | Implementation |
|---|---|
| Cross-site auth | Per-pair API key/secret, `Password` fieldtype, quarterly rotation, never logged |
| Record-level authz | `permission_query_conditions` subtree filter on every business doctype, fail closed |
| Field-level | `permlevel` on margin, landed cost, credit fields |
| Price integrity | Rates resolved server-side; client-supplied rates discarded |
| Discount authority | Server-enforced ceiling per role |
| Webhook auth | `hmac.compare_digest`, never `==` |
| Realtime scoping | `publish_realtime(..., user=...)` always — never an unscoped broadcast |
| Recording access | Authenticated streaming proxy; provider URLs never reach the client |
| Rate limiting | Per-user on ingest, check-in, order, catalogue endpoints |
| Photo handling | Type allow-list, size cap, EXIF stripped, private files |
| PII minimisation | No employee personal data cached on operational sites |
| Audit | `track_changes` on Beat, Journey Plan, Outlet, Customer; conversion log; data access log |

## 5.7 DPDP Act 2023 compliance

The HRMS already implements `Data Consent`, `Data Consent Purpose`, `Data Retention Rule`,
`Data Erasure Request`, `Data Access Log` and `DPDP Compliance Profile` for employees.
**Port the same model to customer scope** on operational sites rather than inventing one.

| Obligation | Mechanism |
|---|---|
| Notice and consent | `Outlet Consent` / identity-level consent: purpose, lawful basis, version, method, timestamp |
| Purpose limitation | Broadcast and outbound dial check consent before dispatch |
| Withdrawal | HMAC unsubscribe (exists), WhatsApp STOP keyword, voice DNC list |
| Retention | `Data Retention Rule` rows per doctype; scheduled purge |
| Access / correction | Export + redact per Omni Identity |
| Erasure | `Data Erasure Request` cascading to messages, calls, tracks |
| Access logging | On bulk export and PII views |
| Telecalling | DND/DNC scrub before the ring decision |
| Recording consent | Announcement at call start; consent event on the call record |

Consent capture belongs in **Phase 1**, not Phase 3. Retrofitting it onto 200k identities
after launch is materially harder than capturing it from first touch.

## 5.8 Observability

| Signal | Source |
|---|---|
| Funnel health | Leads by source/stage/age; unassigned > 2h; no-next-action > 7 days |
| Conversion integrity | Conversion Log failures; identities with Customer but no provenance |
| Sync health | `Field Sync Log` — last success, lag, error |
| Ingestion | Redis queue depth, flush duration, points/SR/day |
| Broadcast | Batch completion rate, 429 count per WABA, quality rating |
| Field exceptions | Geo exceptions, missing checkouts, zero-visit SRs |
| Coverage drift | Planned vs actual per beat cycle |
| Service | SLA breach rate, first-response time, reopen rate |
| Queues | RQ depth per queue, oldest job age |
| Errors | Frappe Error Log volume by title; alert on spikes |

---

# Part 6 — Roadmap, Risks and Decisions

## 6.1 Phasing

### Phase 0 — Stabilise · 1 week · blocks everything

| # | Item | Rationale |
|---|---|---|
| 0.1 | `.gitattributes` + `--renormalize`; merge `somil-dev` | Three core files were flipped to CRLF; every future merge conflicts until fixed |
| 0.2 | Voice: rewire SPA to `useFrappeEventListener` / `useFrappePostCall` | `window.frappe.realtime` and `.call` do not exist on the excom page — the call UI is inert in the SPA |
| 0.3 | Voice: per-user `publish_realtime` | Every logged-in user currently receives every caller's number |
| 0.4 | Fix `doc_events["*"]` cache | Map written to cache, never read → ~3 uncached queries on every save site-wide. Prerequisite for GPS tracking |
| 0.5 | Add missing indexes | §5.4 |

### Phase 1 — Spine · 5 weeks

- `fieldforce` app skeleton; `Field Employee Link`; HRMS bridge; `Field Sync Log`
- **Conversion handoff service** + `Conversion Log` + provenance stamping
- Identity linking for `CRM Lead`, `CRM Deal`, `Outlet`, `HD Ticket`
- Sales Person subtree permissions across CRM, ERPNext, fieldforce, excom, helpdesk
- Customer-scope consent model ported from HRMS
- Broadcast fan-out rebuild
- Excom panel inside CRM (Form Script: actions + side panel + `?embed=1` dialog)

### Phase 2 — Field execution · 6 weeks

- `Outlet` master, import pipeline, progressive geocoding
- `Beat`, `Beat Outlet`, `Journey Plan`, cycle generator
- `Beat Visit` lifecycle with geofence validation against HRMS shift rules
- Order capture (model per D1)
- Track ingestion, live map, `Field Track` flush and purge
- Write-tolerance queue for the three critical writes
- Coverage / productivity dashboards on the read replica

### Phase 3 — Service and engagement depth · 6 weeks

- Helpdesk ↔ excom thread binding; ticket from conversation
- `Maintenance Schedule` → `fieldforce` service visits (`visit_type = Service`)
- Excom shell + IA rebuild (rail, list pane, reading pane, context panel, call layer)
- Telecaller console: call lists, dispositions, callbacks, availability
- Catalogue: curated items, price resolution, PDF, WhatsApp send
- Multi-language templates keyed on `preferred_language`

### Phase 4 — Rollout · per company

One company → one zone → all SRs → next company. Each new company is a new site with the
same app set and its own WABA and voice account.

**≈ 4.5 months to a pilot-ready platform**, plus rollout time per company.

## 6.2 Work partition (avoids collisions)

| Stream | Owner | Files |
|---|---|---|
| Voice fixes | Somil | `App.tsx`, `ChannelTabsView.tsx`, `OmniIdentityPanel.tsx`, `LeftSidebar.tsx`, `channels/voice/*` |
| `fieldforce` backend | Backend | New app — zero overlap |
| HRMS bridge + conversion | Backend | New modules |
| Field app PWA | Frontend | New app frontend |
| Excom shell + IA | Frontend | New files: `tokens.css`, `AppShell.tsx`, `ListPane.tsx`, `ContextPanel.tsx` |
| CRM integration | Backend | `excom/setup.py` + Form Script string |

## 6.3 Risk register

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | Ordering model (D1) decided late | Order module rework | Med | Decide before Phase 2; beat model is model-agnostic |
| R2 | Outlet master quality — no GPS, duplicates | Geofencing unusable | **High** | Progressive geocoding on first visit; phone-based dedupe; import report |
| R3 | SR resistance to tracking | Low adoption, falsified data | **High** | Coverage-credit framing; shift-bounded; SR sees own trail; geo exceptions never auto-punish |
| R4 | Connectivity loss mid-visit | Lost orders, false "not visited" | High | Write-tolerance queue |
| R5 | Conversion handoff partially applied | Orphan customers, broken attribution | Med | Single transaction, all-or-none, `Conversion Log`, idempotent on deal |
| R6 | CRM scope creep past conversion | Two live party masters | **High** | Hard rule + guardrail list (§6.5); CRM read-only after conversion |
| R7 | WhatsApp tier too low | Campaigns throttled | Med | Request higher tier early; multiple WABAs; token bucket |
| R8 | HRMS outage blocks field work | Field force idle | Low | 4h cache; queued mirroring |
| R9 | Cross-site key leakage | Data exposure | Low | Per-pair keys, rotation, encrypted, never logged |
| R10 | Battery drain on low-end Android | Tracking disabled by reps | Med | Adaptive sampling; shift-bounded; measure in pilot |
| R11 | DPDP enforcement before consent capture | Regulatory exposure | Med | Consent in Phase 1 |
| R12 | Upstream CRM/Helpdesk upgrade breaks an extension point | Integration breaks | Med | Extend only via Form Scripts; pin versions; re-assert config in `after_migrate` |

## 6.4 Decisions

| # | Decision | Status |
|---|---|---|
| **D1** | Ordering model: primary / **secondary** / van | **Open** — LLD written for secondary as the general case |
| **D2** | Outlet identity: separate `Outlet` doctype vs ERPNext Customer | **Decided** — separate `Outlet` (§4.2 rationale) |
| **D3** | Write tolerance in Phase 2 | **Decided** — yes; R4 is the top adoption risk |
| **D4** | Pilot unit: one zone, all functions | **Decided** |
| **D5** | Frappe CRM | **Decided — KEEP, confined to stages 1–3.** Read-only after conversion |
| **D6** | Org hierarchy source | **Decided** — ERPNext `Sales Person`. CRM's hierarchy stays off |
| **D7** | Service tooling | **Decided** — Helpdesk for tickets, ERPNext for AMC/warranty, `fieldforce` for the visit |
| **D8** | WABA count and tier per company | **Open** — needs input; weeks of Meta lead time |
| **D9** | Field service in Phase 3 vs later | **Decided** — Phase 3, reusing the visit engine |

## 6.5 CRM guardrails (what keeps D5 safe)

| Do **not** use | Use instead | Why |
|---|---|---|
| `CRM Product` / `CRM Products` / product sync | ERPNext `Item` + `Item Price` | This is the real duplicate master; the sync-issue queue is its symptom |
| `CRM Sales Hierarchy` | ERPNext `Sales Person` | One org tree. Port CRM's 119-line enforcement onto it |
| `CRM Territory` | ERPNext `Territory` | Same |
| CRM telephony (Twilio/Exotel) | Excom `channels/voice` | CRM cannot ring a team |
| CRM as a post-conversion workspace | ERPNext + excom + fieldforce | The moment reps edit customers in both, R6 materialises |
| Forking CRM | Form Scripts + upstream PRs | AGPL and upgrade churn |

**Keep from CRM:** web forms (live in production), Facebook lead sync (551 lines,
CRM-only), the lead/deal pipeline UI for inbound and export, and its list/kanban ergonomics.

**Note:** auto-assignment and email templates are **core Frappe** (`Assignment Rule`,
`Email Template`) — they work in ERPNext, `fieldforce` and Helpdesk regardless of CRM.

## 6.6 Pilot success criteria

| Metric | Target |
|---|---|
| Beat coverage | > 90% |
| Geo-verified visits | > 95% |
| Order capture time | median < 3 min |
| Lost writes | 0 |
| Crash-free sessions | > 99% |
| Live-map lag | < 5 min |
| Outlets with GPS after 2 cycles | > 80% |
| SR daily active use | > 85% of roster |
| Inbound first response | median < 30 min |
| Conversion handoff failures | 0 |
| Records visible outside subtree | 0 |

---

## Appendix A — Glossary

| Term | Meaning |
|---|---|
| **First touch** | The earliest recorded interaction with a party, in any channel |
| **Conversion** | CRM Deal Won → ERPNext Customer created; the CRM record becomes read-only |
| **Beat** | A recurring set of outlets one SR visits on a given day of a cycle |
| **PJP / Journey Plan** | The calendar assigning beats to days |
| **Outlet** | A retail shop visited by an SR; not necessarily a billed party |
| **Distributor / Stockist** | The party that buys from the company and supplies outlets |
| **Primary / Secondary sale** | Company → distributor / distributor → outlet |
| **Coverage / Strike rate** | Visited ÷ planned / ordered ÷ visited |
| **Geo exception** | Check-in outside the outlet geofence |
| **WABA** | WhatsApp Business Account |
| **AMC** | Annual Maintenance Contract |

## Appendix B — Reused assets

| Asset | Location | Used for |
|---|---|---|
| `Employee Checkin` (lat/lng) | `indian_hrms_compliance` | Attendance authority; visit check-in model |
| `Shift Location` (`checkin_radius`) | `indian_hrms_compliance` | Geofence semantics |
| `Shift Type` (`allow_geolocation_tracking`) | `indian_hrms_compliance` | Tracking enablement |
| `Data Consent` / `Data Retention Rule` | `indian_hrms_compliance` | DPDP model → customer scope |
| `Assignment Rule` | core Frappe | Auto-assignment for every doctype |
| `Email Template` | core Frappe | Templates everywhere |
| `Web Form` + CRM builder | core Frappe + `crm/api/form.py` | Website enquiry capture |
| `crm/lead_syncing` | `crm` | Facebook lead ads |
| `crm/permissions/org_hierarchy.py` | `crm` | Subtree permission pattern → ERPNext Sales Person |
| `FrappeClient(site, key, secret)` | `crm/.../erpnext_crm_settings.py:333` | Cross-site bridge |
| `Sales Person` / `Territory` nested sets | `erpnext` | One org and geography tree |
| `Item` / `Item Price` (`customer`, `supplier`) | `erpnext` | Catalogue and pricing |
| `Maintenance Schedule` / `Visit` / `Warranty Claim` | `erpnext` | AMC and warranty |
| `HD Ticket` + SLA + KB | `helpdesk` | Reactive service |
| `Omni Identity` + polymorphic links | `excom` | Identity spine |
| `channels/voice/*` | `excom` (somil-dev) | Telecalling |
| Offline queue pattern | `posawesome/frontend/src/offline/` | Write tolerance |

---

*End of HLD-002.*
