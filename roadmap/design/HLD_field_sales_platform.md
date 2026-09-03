# High Level Design — Distribution Field Sales Platform + Excom

**Document:** HLD-001
**Version:** 1.0
**Date:** 2026-08-30
**Status:** Draft for review
**Owner:** Sagar Ratan Garg

---

## Table of Contents

1. [Context, Scope and Assumptions](#part-1--context-scope-and-assumptions)
2. [System Architecture](#part-2--system-architecture)
3. [Domain Architecture](#part-3--domain-architecture)
4. [Cross-Cutting Architecture](#part-4--cross-cutting-architecture)
5. [Roadmap, Risks and Open Decisions](#part-5--roadmap-risks-and-open-decisions)

---

# Part 1 — Context, Scope and Assumptions

## 1.1 Business context

A multi-company distribution group runs a field sales operation across India. Sales
Representatives (SRs) travel fixed routes ("beats") of retail outlets, taking orders,
capturing market intelligence and maintaining outlet relationships. A telecalling team
covers outlets that field visits cannot reach economically. Both teams need customer
conversation history, product catalogues and pricing at hand.

Today the group runs:

- **`indian_hrms_compliance`** on a dedicated HRMS site — multi-company, full India
  statutory HR (PF ECR, ESI, Form 16 / 24Q, POSH, LWF, PT, gratuity), plus
  `Employee Checkin` with GPS and `Shift Location` with geofencing.
- **ERPNext + `excom`** per operating company, each on its own site — parties, items,
  pricing, orders, and an omnichannel conversation layer (WhatsApp, Email, Instagram,
  Web Chat, Voice).

The platform described here adds the missing layer: **field execution**. Beats, journey
plans, geo-verified visits, order capture, live tracking and the productivity metrics
that distribution management runs on.

## 1.2 Goals

| # | Goal | Success measure |
|---|---|---|
| G1 | Plan and execute beats | > 90% of planned outlets visited per beat cycle |
| G2 | Geo-verify every visit | > 95% of visits within outlet geofence |
| G3 | Capture orders in the field | Median order capture < 3 minutes |
| G4 | Live visibility of the field force | Manager sees positions with < 5 min lag |
| G5 | Unify conversations per outlet | One thread per outlet across all channels |
| G6 | Enforce hierarchy visibility | A user sees only their subtree, by default |
| G7 | Share curated catalogues | Rep sends priced catalogue in < 30 seconds |
| G8 | Scale to pan-India | 500 SRs, 200k outlets, 60k messages/day |

## 1.3 Non-goals (explicitly out of scope for v1)

- Full offline-first operation. Only three writes are network-tolerant (§4.6).
- Native WhatsApp Catalog / Meta Commerce Manager product messages.
- Route optimisation by travel time. Beats are human-planned and sequence-ordered.
  A beat is a **relationship and coverage** construct, not a logistics one — it repeats
  on a cycle, it is owned by a person, and its purpose is selling, not delivery.
- Van sales / vehicle stock issue (deferred pending the ordering-model decision, §5.4).
- Replacing the HRMS. Attendance, payroll and statutory HR stay where they are.
- A separate mobile binary. The field app is a PWA on the company site.

## 1.4 Actors

| Actor | Primary surface | Key needs |
|---|---|---|
| **Sales Representative (SR)** | Field app (PWA, phone) | Today's beat, check-in, order, catalogue, call |
| **Area Sales Manager (ASM)** | Manager dashboard (web) | Live map, coverage, productivity, exceptions |
| **Regional / Zonal Manager** | Manager dashboard | Rolled-up subtree metrics, trends |
| **Telecaller** | Telecaller console (web) | Call list, dispositions, callbacks, order entry |
| **Telecalling supervisor** | Manager dashboard | Queue health, agent availability, call outcomes |
| **Customer Service agent** | Excom inbox | Unified conversation across channels |
| **Distributor** (external) | Portal / WhatsApp | Order confirmations, statements, catalogues |
| **Retail outlet owner** (external) | WhatsApp | Catalogue, order confirmation, offers |
| **HR / Admin** | HRMS site | Employee master, shifts, attendance |
| **Platform admin** | Desk | Configuration, roles, integrations |

## 1.5 Scale assumptions

Design targets. Every capacity decision in Part 4 derives from these.

| Dimension | v1 target | Design headroom |
|---|---|---|
| Operating companies (sites) | 3–5 | 20 |
| SRs per company | 100–200 | 500 |
| Telecallers | 50 | 150 |
| Distributors (ERPNext Customers) | 500–2,000 | 10,000 |
| Retail outlets | 50,000 | 200,000 |
| Beat visits / day | 8,000 | 40,000 |
| GPS pings / day (pre-aggregation) | 100,000 | 500,000 |
| `Excom Message` rows | 10M / year | 50M |
| Broadcast recipients / campaign | 50,000 | 200,000 |
| Concurrent field app users | 200 | 600 |

## 1.6 Constraints

| Constraint | Impact |
|---|---|
| Frappe Framework v15 / v16, MariaDB, Python 3.10 | Doctype-based modelling; RQ for async |
| HRMS on a separate site, multi-company | All employment facts arrive over HTTP |
| Excom deployed per company, own WABA number | No cross-company conversation sharing |
| Single timezone (IST) | No multi-TZ scheduling machinery |
| WhatsApp Cloud API tiering and per-second throughput caps | Broadcasts must be rate-limited and batched |
| DPDP Act 2023 | Consent, retention, erasure, access logging |
| Field devices: low-to-mid Android, patchy 4G | PWA must be light; three writes need a queue |

## 1.7 Key assumptions requiring confirmation

These are recorded so that the LLD can be corrected cheaply if wrong.

| # | Assumption | If wrong |
|---|---|---|
| A1 | Orders booked by SRs are **secondary sales** (distributor fulfils), so an `Outlet` is distinct from an ERPNext `Customer` | If primary-only, `Outlet` collapses into `Customer`; `Secondary Order` becomes `Sales Order`. Beat model unaffected |
| A2 | A retail outlet belongs to exactly one distributor at a time | Many-to-many needs an `Outlet Distributor` bridge with validity dates |
| A3 | An SR belongs to one company | Cross-company SRs need a company dimension on `Field Employee Link` |
| A4 | Beat cycles are weekly or fortnightly, human-planned | Dynamic beat generation is a separate engine |
| A5 | GPS trail retention of 90 days is sufficient | Longer retention changes storage sizing |

---

# Part 2 — System Architecture

## 2.1 Deployment topology

```
                        ┌───────────────────────────────────────┐
                        │        HRMS SITE  (hr.group.com)      │
                        │  indian_hrms_compliance (multi-co)    │
                        │                                       │
                        │  Employee · Employee Checkin (GPS)    │
                        │  Shift Type · Shift Location (geo)    │
                        │  Attendance · Leave · Payroll         │
                        │  Data Consent · Data Retention Rule   │
                        │  DPDP Compliance Profile              │
                        │                                       │
                        │  ══ AUTHORITY: person, employment ══  │
                        └───────────────┬───────────────────────┘
                                        │
                        REST over HTTPS │ FrappeClient(api_key, api_secret)
                        read-mostly     │ pull-based, never push
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
        ┌───────────▼──────┐ ┌──────────▼───────┐ ┌─────────▼────────┐
        │  COMPANY A SITE  │ │  COMPANY B SITE  │ │  COMPANY C SITE  │
        │                  │ │                  │ │                  │
        │  ERPNext         │ │  (identical      │ │  (identical      │
        │  excom           │ │   app set)       │ │   app set)       │
        │  fieldforce      │ │                  │ │                  │
        │                  │ │                  │ │                  │
        │  Customer/Outlet │ │                  │ │                  │
        │  Item/Item Price │ │                  │ │                  │
        │  Sales Person ▲  │ │                  │ │                  │
        │  Territory    ▲  │ │                  │ │                  │
        │  Beat/Visit      │ │                  │ │                  │
        │  Orders          │ │                  │ │                  │
        │  Threads/Msgs    │ │                  │ │                  │
        │  WABA number A   │ │  WABA number B   │ │  WABA number C   │
        └──────────────────┘ └──────────────────┘ └──────────────────┘
                    ▲ nested sets, indexed on lft/rgt
```

**Rationale for the split**

- The HRMS is already multi-company and statutory. Duplicating employment data into
  each company site would create a compliance liability and a reconciliation problem.
- Field execution is *commercial*: it references Customers, Outlets, Items, Prices and
  produces orders. All of those exist only on the company site. Placing beats on the
  HRMS site would make every visit a cross-site join.
- Excom is per company because a WhatsApp Business Account is per brand, and
  conversations must not leak across legal entities.

## 2.2 New application: `fieldforce`

Field execution ships as a **new Frappe app** installed on each company site, not as
additions to `excom` or `erpnext`.

| Reason | Detail |
|---|---|
| Separation of concerns | Excom is the engagement layer; field execution is an operations layer |
| Independent release cadence | Field app iterates weekly during rollout; excom does not |
| Optional install | A company doing only telecalling installs excom without `fieldforce` |
| Blast radius | High-write tracking tables stay out of the excom schema |
| Uninstall safety | Removing field execution must not touch conversation history |

Dependencies: `fieldforce` requires `erpnext` and `excom`. It never writes to excom
doctypes directly; it calls excom's whitelisted API.

## 2.3 Layered view

```
┌──────────────────────────────────────────────────────────────────────┐
│  L4  SURFACES                                                        │
│  Field App (PWA)  │ Manager Dashboard │ Telecaller Console           │
│  Excom Inbox      │ ERPNext Desk (back-office)                       │
├──────────────────────────────────────────────────────────────────────┤
│  L3  APPLICATION SERVICES                                            │
│  Beat planning · Visit lifecycle · Order capture · Track ingestion   │
│  Catalogue render/send · Call routing · Broadcast fan-out            │
├──────────────────────────────────────────────────────────────────────┤
│  L2  DOMAIN                                                          │
│  fieldforce: Beat · Journey Plan · Visit · Outlet · Track            │
│  excom:      Omni Identity · Thread · Message · Team · Catalogue     │
│  erpnext:    Customer · Item · Item Price · Sales Person · Territory │
├──────────────────────────────────────────────────────────────────────┤
│  L1  PLATFORM                                                        │
│  Frappe (ORM, auth, jobs, realtime) · MariaDB · Redis · RQ           │
├──────────────────────────────────────────────────────────────────────┤
│  L0  EXTERNAL                                                        │
│  WhatsApp Cloud API · Exotel/Airtel Voice · Gmail API · Maps · FCM   │
│  HRMS site (REST)                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

## 2.4 Component responsibilities

| Component | Owns | Must not |
|---|---|---|
| `fieldforce.beat` | Beat masters, journey plans, cycle generation | Write to ERPNext masters |
| `fieldforce.visit` | Visit lifecycle, geofence validation, outcomes | Decide pricing |
| `fieldforce.track` | GPS ingestion, aggregation, live state | Create one document per ping |
| `fieldforce.order` | Order capture and validation at the outlet | Post accounting entries |
| `fieldforce.sync` | HRMS bridge, employee cache, attendance checks | Cache salary or personal data |
| `excom.channels.*` | Message send/receive per channel | Know about beats |
| `excom.catalogue` | Curated catalogue, price resolution, render, send | Own item or price data |
| `erpnext` | Parties, items, prices, stock, accounting | Know about beats or threads |

## 2.5 Integration points

| # | Integration | Direction | Mode | Frequency |
|---|---|---|---|---|
| I1 | HRMS → company: employee master | pull | REST | nightly + on-demand |
| I2 | HRMS → company: shift + attendance state | pull | REST | at day start, cached 4h |
| I3 | Company → HRMS: field check-in event | push | REST | per check-in, queued |
| I4 | `fieldforce` → `excom`: send catalogue / start call | in-process | whitelisted API | per action |
| I5 | `excom` → WhatsApp Cloud API | push | HTTPS | per message |
| I6 | WhatsApp → `excom` | push | webhook | per event |
| I7 | `excom` ↔ Exotel/Airtel | both | HTTPS + webhook | per call |
| I8 | `fieldforce` → Maps provider | pull | HTTPS | geocoding only, cached |
| I9 | Field app → `fieldforce` | push | REST | batched |
| I10 | `fieldforce` → FCM | push | HTTPS | per notification |

**I3 design note:** the field check-in is written locally first and mirrored to HRMS
asynchronously. A HRMS outage must never block an SR from starting their beat.

## 2.6 Technology choices

| Concern | Choice | Alternative rejected |
|---|---|---|
| Field app shell | PWA (React, existing excom stack) | Native app — two more build pipelines, store review latency |
| Live position store | Redis sorted sets, TTL 36h | MariaDB per-ping rows — 144k inserts/day |
| Track history | One document per SR per day, encoded polyline | Per-ping documents; time-series DB (extra infrastructure) |
| Offline queue | IndexedDB + replay, three write types only | Full offline-first sync engine — weeks, and mostly unused |
| Cross-site auth | API key/secret per site pair | Shared DB — couples release cycles, breaks tenancy |
| Catalogue render | Server-side Jinja print format → PDF | Client render — inconsistent across devices |
| Reporting | Read replica + nightly snapshot table | Live aggregation over transactions — will not hold at scale |

---

# Part 3 — Domain Architecture

## 3.1 Bounded contexts

```
┌───────────────────────┐   ┌───────────────────────┐   ┌──────────────────────┐
│   PEOPLE  (HRMS site) │   │  COMMERCE  (erpnext)  │   │ ENGAGEMENT (excom)   │
│                       │   │                       │   │                      │
│ Employee              │   │ Customer (distributor)│   │ Omni Identity        │
│ Employee Checkin      │   │ Item, Item Price      │   │ Excom Thread         │
│ Shift Type/Location   │   │ Sales Person (tree)   │   │ Excom Message        │
│ Attendance, Leave     │   │ Territory (tree)      │   │ Excom Team           │
│ Data Consent (staff)  │   │ Sales Order, Invoice  │   │ Excom Call           │
└──────────┬────────────┘   └───────────┬───────────┘   │ Excom Catalogue      │
           │                            │               └──────────┬───────────┘
           │  employee_id               │ customer, item_code      │ omni_identity
           │                            │                          │
           └────────────┬───────────────┴──────────────────────────┘
                        │
           ┌────────────▼─────────────────────────────────┐
           │   FIELD EXECUTION  (fieldforce)              │
           │                                              │
           │   Outlet · Beat · Beat Outlet                │
           │   Journey Plan · Beat Visit                  │
           │   Field Track · Field Employee Link          │
           │   Secondary Order (see A1)                   │
           └──────────────────────────────────────────────┘
```

`fieldforce` is a **downstream** context. It holds foreign keys into all three upstream
contexts and owns none of their data.

## 3.2 Ownership matrix

One writer per fact. Violating this is the single most common cause of data drift in
multi-site deployments.

| Fact | Authority | Replicated? | Access pattern for others |
|---|---|---|---|
| Person, employment, salary | HRMS site | No | REST read, cached ids + display only |
| Shift definition, geofence radius | HRMS site | No | REST read, cached 4h |
| Attendance / check-in for payroll | HRMS site | Mirrored | Company writes local visit, mirrors async |
| Distributor (Customer) | Company ERPNext | No | Native link |
| Item, price, stock | Company ERPNext | No | Native link |
| Sales Person tree, Territory tree | Company ERPNext | No | Native link; nested-set query |
| Outlet (retail shop) | `fieldforce` | No | Native link |
| Beat, journey plan, visit | `fieldforce` | No | Native link |
| GPS trail | `fieldforce` | Redis (hot) + doc (cold) | API |
| Conversation, message, call | `excom` | No | Excom API |
| Catalogue | `excom` | No | Excom API |

## 3.3 The Outlet — why it is not a Customer

A retail outlet in secondary distribution is not a party you invoice. Modelling it as an
ERPNext `Customer` would:

- inflate the Customer master from ~2,000 distributors to ~200,000 shops, degrading every
  Customer link field, naming series, and report in ERPNext;
- imply receivables, credit limits and tax registrations that do not exist for a shop
  you never bill;
- break the distributor relationship, which is the actual commercial edge.

`Outlet` is therefore a `fieldforce` doctype with a **link to the distributor Customer**.

```
Customer (Distributor)  1 ──────< Outlet  >────── 1 Territory
                                   │
                                   ├──< Beat Outlet >── Beat
                                   ├──< Beat Visit
                                   └──── omni_identity ──> Excom Omni Identity
```

If assumption A1 is wrong and all orders are primary, `Outlet` remains valid as a
*visit target* and simply carries `customer` = the party being sold to. The beat model
does not change. This is why the general model was chosen.

## 3.4 The beat as a domain object

A beat is **not a route**. It is a recurring commitment by a person to a set of outlets.
Its defining properties are ownership, cycle and coverage — not distance or arrival time.

| Property | Meaning | Consequence for the model |
|---|---|---|
| Ownership | One Sales Person owns the beat | Visibility follows the Sales Person tree |
| Cycle | Repeats weekly / fortnightly / monthly | A cycle generator produces Journey Plans |
| Sequence | Outlets have a human-set order | `sequence` is authored, not computed |
| Coverage | Every outlet must be visited each cycle | Coverage % is the primary KPI |
| Stability | Beats change rarely and deliberately | Changes are versioned and audited |
| Frequency per outlet | High-value outlets may be visited more often | `visit_frequency` on the beat-outlet link |

Derived vocabulary used throughout the LLD:

```
Coverage        = outlets visited / outlets planned in the cycle
Productivity    = outlets ordered / outlets visited      (a.k.a. strike rate)
Lines per call  = distinct SKUs ordered / productive calls
Value per call  = order value / productive calls
Beat adherence  = visits made in planned sequence / total visits
Time in market  = last checkout - first checkin
Selling time    = sum(visit durations); travel time = time in market - selling time
```

## 3.5 Visit lifecycle

```
                 ┌─────────┐
                 │ PLANNED │  created by cycle generator from Journey Plan
                 └────┬────┘
                      │ SR opens outlet in field app
                 ┌────▼─────────┐
        geofence │ CHECKED_IN   │  captures lat/lng, distance_from_outlet
        validated└────┬─────────┘
                      │
        ┌─────────────┼─────────────┬───────────────┐
        ▼             ▼             ▼               ▼
  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌────────────┐
  │ ORDERED  │  │ NO_ORDER │  │  CLOSED   │  │ NOT_VISITED│
  │ + order  │  │ + reason │  │ (shop shut)│  │ (cycle end)│
  └────┬─────┘  └────┬─────┘  └─────┬─────┘  └────────────┘
       └─────────────┴──────────────┘
                     │ SR checks out
              ┌──────▼───────┐
              │  COMPLETED   │  duration computed, KPIs updated
              └──────────────┘
```

Rules:

- A visit outside the geofence is recorded but flagged `geo_exception`, not rejected.
  Rejection would push reps to falsify rather than explain.
- `NOT_VISITED` is applied by the cycle-close job, never by the SR.
- A visit cannot be edited after cycle close; corrections require a supervisor override
  that is itself audited.

## 3.6 Identity across contexts

```
  HRMS Employee (HR-EMP-00123)
        │  employee_id  (the join key; never re-used, never re-issued)
        ▼
  Field Employee Link  ──▶ User (login)  ──▶ Sales Person (tree node)
        │                                          │
        │                                    Beat.sales_person
        └──▶ Excom Team Member ──▶ Excom Thread.assigned_to
```

An outlet's conversation identity:

```
  Outlet ──▶ omni_identity ──▶ Omni Identity ──< Omni Identity Channel
                                        │        (phone / whatsapp / email)
                                        └──────< Omni Identity Link
                                                 (Customer, Outlet, Lead, ...)
```

`Omni Identity Link` is already polymorphic (`linked_doctype` + `linked_name`), so linking
an Outlet requires no schema change to excom — only data.

## 3.7 Visibility model

Three axes, resolved in precedence order:

1. **People** — Sales Person subtree (`lft`/`rgt`, already indexed in ERPNext)
2. **Geography** — Territory subtree
3. **Function** — Excom Team membership

| Doctype | Rule |
|---|---|
| `Outlet`, `Beat`, `Journey Plan` | owner's Sales Person node in my subtree, OR territory in my territory subtree |
| `Beat Visit`, `Field Track` | visiting Sales Person in my subtree |
| `Secondary Order` / `Sales Order` | owning Sales Person in my subtree |
| `Excom Thread` | my teams (existing rule) **OR** linked outlet's Sales Person in my subtree |
| `Excom Call` | participant, or ring-set member, or in my subtree |
| Dashboards | aggregate strictly within subtree |

**Default is restrictive.** A role `Field Global Viewer` exists for the small set of head
office users who legitimately need everything. Frappe CRM's approach — a global toggle
defaulting to "everyone sees everything" — is explicitly rejected.

---

# Part 4 — Cross-Cutting Architecture

## 4.1 Performance budget

| Interaction | Budget (p95) | Notes |
|---|---|---|
| Field app cold start | < 3 s on 4G | Precached shell, deferred data |
| Load today's beat | < 1.2 s | Single endpoint, denormalised payload |
| Check-in round trip | < 800 ms | Fire-and-forget mirror to HRMS |
| Order submit (20 lines) | < 1.5 s | Server-side price resolution |
| Catalogue send | < 3 s | Render cached by price context |
| Manager live map (200 SRs) | < 1 s | Redis read, no MariaDB |
| Coverage dashboard | < 2 s | Snapshot table on read replica |
| Inbound WhatsApp webhook ack | < 300 ms | Enqueue and return |

## 4.2 GPS ingestion — the defining scale decision

Naive per-ping documents do not survive this workload.

```
500 SRs × 8 h × 1 ping/min = 240,000 pings/day
```

At one Frappe document per ping that is 240k full document lifecycles per day —
validation hooks, version rows, `modified` index churn — **plus** they would each fire
excom's `doc_events["*"]` wildcard, which currently performs uncached queries on every
save (a defect tracked separately). The result would be roughly 720,000 additional
queries per day from tracking alone.

**Chosen design — two stores:**

```
Field app (buffers 5 min, ~5 points)
     │  POST /api/method/fieldforce.api.track.ingest   [array]
     ▼
 short queue worker
     ├──▶ Redis  ZADD track:{company}:{employee}:{date}  score=epoch  member=lat,lng,acc
     │       TTL 36 h        ← LIVE MAP reads only this
     │
     └──▶ (every 15 min) flush job
              ▼
        Field Track — ONE document per employee per day
          route_points   Long Text (encoded polyline)
          distance_km, first_ping, last_ping, idle_minutes, stop_count
```

**500 documents/day instead of 240,000.** Live map latency is Redis latency. History is
one row per SR-day, trivially indexable and archivable.

Supporting rules:

- Encode the trail as a polyline rather than raw JSON — roughly 10× smaller.
- Adaptive sampling: 60 s moving, 300 s stationary, off outside shift hours.
- `Field Track` and `Beat Visit` are **excluded from the `doc_events["*"]` wildcard**.
- Retain 90 days via a `Data Retention Rule`; the purge engine already exists in HRMS
  and is ported to the company site.

## 4.3 Broadcast fan-out

The current implementation loops all subscribers in a single background job with a
synchronous HTTP call per recipient. At 100k recipients this occupies one worker for
hours with no rate limiting, no retry and no resume point.

```
execute_broadcast(name)
  ├─ chunk recipients into batches of 500 → Broadcast Batch rows
  ├─ enqueue one job per batch on the dedicated `broadcast` queue
  │     └─ per batch:
  │          token bucket per WABA account (respects per-second cap)
  │          send → log → on 429/5xx exponential backoff, requeue recipient
  └─ reconciler rolls batch counters into the parent
```

Resume-on-restart comes free: a batch is either `Pending`, `Running` or `Done`.

## 4.4 Queue segregation

Campaign traffic must never starve inbound conversation processing or call events.

| Queue | Workers | Carries | Latency need |
|---|---|---|---|
| `short` | 4 | webhooks, track ingestion, check-in mirror | < 1 s |
| `default` | 4 | doc events, UI-triggered jobs | seconds |
| `long` | 2 | employee sync, reconcilers, snapshots | minutes |
| `broadcast` | 6 | campaign batches | throughput-bound |
| `voice` | 2 | call events, recording fetch | < 2 s |

## 4.5 Data growth and retention

| Table | Year-1 rows | Retention | Strategy |
|---|---|---|---|
| `Beat Visit` | 2.5M | 3 years | Index `(sales_person, visit_date)`, `(outlet, visit_date)` |
| `Field Track` | 180k | 90 days | Purge by rule |
| `Secondary Order` + items | 1.5M + 12M | 3 years | Partition items by year |
| `Excom Message` | 10–20M | 18 months | Archive table; already indexed `(thread, creation)` |
| `Excom Broadcast Log` | 5–10M | 12 months | **Add index `(broadcast, status)`** |
| `Excom Call` | 2–5M | 24 months; recordings 6 months | Index `(omni_identity, creation)` |
| `Omni Identity` | 250k | — | **Index normalized phone and email** |

## 4.6 Availability and degradation

| Dependency down | Degraded behaviour | Must not happen |
|---|---|---|
| HRMS site | Field app uses cached shift + employee data (4h TTL); check-ins queue for mirroring | SR blocked from starting the beat |
| WhatsApp API | Catalogue/broadcast queue and retry | Data loss; user-facing error |
| Voice provider | Click-to-call disabled with a clear message | Silent failure |
| Redis | Live map unavailable; ingestion falls back to direct flush | Ping loss beyond the buffer |
| Read replica | Dashboards fall back to primary with a banner | Reporting load hitting primary unannounced |
| Network on device | Three writes queue locally (check-in, order, check-out) | Lost order; visit recorded as not-made |

**Write tolerance, scoped.** Only check-in, order capture and check-out are queued
client-side. GPS gaps are acceptable. Dashboards may require connectivity. This is a
deliberate 5-day scope rather than a full offline-first engine.

## 4.7 Security

| Control | Implementation |
|---|---|
| Authn (field app) | Frappe session on the company site; PWA, no separate identity provider |
| Authn (cross-site) | API key/secret per site pair, rotated quarterly, stored encrypted |
| Authz | Role + `permission_query_conditions` subtree filter on every field doctype |
| Field-level | `permlevel` on cost/margin fields; SRs see price, not margin |
| Webhook auth | Shared-secret token compared with `hmac.compare_digest`; never a plain `==` |
| Realtime scoping | `frappe.publish_realtime(..., user=...)` always. Never an unscoped broadcast |
| Recording access | Streamed through an authenticated proxy; provider URLs never reach the client |
| Rate limiting | Per-user on ingestion and order endpoints |
| Audit | Visit edits, beat changes, override actions, and data access are logged |

## 4.8 Compliance (DPDP Act 2023)

Reuse the HRMS module rather than reinventing it. `Data Consent`, `Data Retention Rule`,
`Data Erasure Request`, `Data Access Log` and `DPDP Compliance Profile` already exist and
are employee-scoped; port the same model to customer/outlet scope on the company site.

| Obligation | Mechanism |
|---|---|
| Notice and consent | `Outlet Consent` mirroring `Data Consent` — purpose, lawful basis, version, method, timestamp |
| Purpose limitation | Broadcast and telecalling check consent purpose before send/dial |
| Withdrawal | Existing HMAC unsubscribe (`excom/api/unsubscribe.py`), WhatsApp STOP keyword, voice DNC |
| Retention | `Data Retention Rule` rows per doctype; scheduled purge |
| Access / correction | Export + redact path per Omni Identity |
| Erasure | `Data Erasure Request` workflow, cascading to messages and tracks |
| Access logging | `Data Access Log` on bulk export and PII views |
| Telecalling | DND / internal DNC scrub before the ring decision |
| Recording consent | Announcement at call start; consent event stored on the call record |

## 4.9 Observability

| Signal | Where |
|---|---|
| Sync health (HRMS bridge) | `Field Sync Log` — last success, lag, error |
| Ingestion lag | Redis queue depth, flush job duration |
| Broadcast throughput | Batch completion rate, 429 count per WABA |
| Visit exceptions | Count of `geo_exception` per SR per day |
| Coverage drift | Planned vs actual per beat cycle |
| Queue health | RQ depth per queue, oldest job age |
| Error budget | Frappe Error Log volume by title, alert on spikes |

---

# Part 5 — Roadmap, Risks and Open Decisions

## 5.1 Phasing

### Phase 0 — Stabilise (1 week) · blocks everything

| # | Item | Why now |
|---|---|---|
| 0.1 | `.gitattributes` + `git add --renormalize`, merge `somil-dev` | Three core files were converted to CRLF; every future merge conflicts until fixed |
| 0.2 | Voice: rewire SPA to `useFrappeEventListener` / `useFrappePostCall` | `window.frappe.realtime` and `.call` do not exist on the excom page; the call UI is inert in the SPA |
| 0.3 | Voice: per-user `publish_realtime` | Every logged-in user currently receives every caller's number |
| 0.4 | Fix `doc_events["*"]` cache | The map is written to cache and never read; ~3 uncached queries on every document save site-wide |
| 0.5 | Add missing indexes | Listed in §4.5 |

0.4 is a prerequisite for GPS tracking, not merely a cleanup: adding a high-write
doctype on top of a per-save uncached query multiplies the defect.

### Phase 1 — Foundation (4 weeks)

- `fieldforce` app skeleton; `Field Employee Link`; HRMS bridge and sync log
- Sales Person subtree `permission_query_conditions` across commerce, field and excom
- `Outlet` master + import tooling + geocoding + Omni Identity linkage
- Customer-side consent model ported from HRMS
- Broadcast fan-out rebuild
- Catalogue PoC (curated items, price resolution, PDF, WhatsApp document send)

### Phase 2 — Field execution (6 weeks)

- `Beat`, `Beat Outlet`, `Journey Plan`, cycle generator
- `Beat Visit` lifecycle with geofence validation against HRMS shift rules
- Order capture (model per decision D1)
- Track ingestion pipeline, live map, `Field Track` flush and purge
- Write-tolerance queue for the three critical writes
- Coverage / productivity dashboards on the read replica

### Phase 3 — Engagement depth (6 weeks)

- Excom shell + IA rebuild (rail, list pane, reading pane, context panel, call layer)
- Telecaller console: call lists, dispositions, callbacks, wrap-up, availability
- Multi-language template sets keyed on `preferred_language`
- Voice C3 items on demand (IVR, warm transfer, voicemail)

### Phase 4 — Rollout

One company, one zone, ~20 SRs → measure against the §1.2 success measures → full
company → additional companies (each a new site with the same app set).

**Approximately four months to a pilot-ready field platform**, plus rollout time per
company.

## 5.2 Team and ownership

| Stream | Owner | Parallelisable with |
|---|---|---|
| Phase 0 voice fixes | Somil (his files) | Everything else, once 0.1 lands |
| `fieldforce` backend | Backend engineer | Excom shell work |
| Field app PWA | Frontend engineer | Backend, behind a stable API contract |
| Excom shell + IA | Frontend engineer | `fieldforce` backend |
| HRMS bridge | Backend engineer | — |

**File-level partition during Phase 0/1:** Somil owns `App.tsx`,
`ChannelTabsView.tsx`, `OmniIdentityPanel.tsx`, `LeftSidebar.tsx` and all voice modules.
New work goes into new files (`fieldforce/*`, `styles/tokens.css`, `AppShell.tsx`,
`ListPane.tsx`) so the two streams never collide.

## 5.3 Risk register

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | Ordering model (D1) decided late | Rework of the order module | Medium | Decide before Phase 2 starts; beat model is model-agnostic |
| R2 | Outlet master quality (no GPS, duplicates) | Geofencing unusable | **High** | Capture-on-first-visit workflow; dedupe by phone via Omni Identity; treat geo as progressive enrichment |
| R3 | SR resistance to tracking | Low adoption, falsified data | **High** | Position as coverage credit, not surveillance; track only during shift; show the SR their own data; never punish geo exceptions automatically |
| R4 | Connectivity loss during visit | Lost orders, false "not visited" | High | Write-tolerance queue (§4.6) |
| R5 | WhatsApp tier too low for campaign size | Campaigns throttled | Medium | Request higher tier early (weeks of lead time); multiple WABA numbers; token bucket |
| R6 | HRMS site outage blocks field work | Field force idle | Low | Cache shift/employee 4h; check-ins queue for mirroring |
| R7 | GPS trail treated as per-ping documents | Site degradation | Low (design fixed) | §4.2 is normative, not advisory |
| R8 | Cross-site key leakage | Data exposure | Low | Per-pair keys, quarterly rotation, encrypted at rest, no keys in logs |
| R9 | Battery drain on low-end Android | Reps disable tracking | Medium | Adaptive sampling; shift-bounded; measure on target devices in pilot |
| R10 | DPDP enforcement before consent capture is complete | Regulatory exposure | Medium | Consent model in Phase 1, not Phase 3 |

## 5.4 Open decisions

| # | Decision | Options | Blocks | Recommendation |
|---|---|---|---|---|
| **D1** | Ordering model | (a) Primary: SR books distributor's order → `Sales Order`  (b) Secondary: SR books retailer's order fulfilled by distributor → `Secondary Order` + distributor stock  (c) Van sales → invoice + vehicle warehouse | Phase 2 order module | Confirm before Phase 2 kickoff. LLD is written for (b) as the general case |
| **D2** | Outlet identity | (a) `Outlet` doctype linked to distributor Customer  (b) Outlet as ERPNext Customer | Outlet master, scale sizing | (a) — see §3.3 |
| **D3** | Write tolerance | (a) Build in Phase 2  (b) Ship online-only, revisit after pilot | Phase 2 scope (~5 days) | (a) — R4 is the top adoption risk |
| **D4** | Pilot unit | (a) One zone, all functions  (b) One function, nationally | Phase 4 plan | (a) — exercises the integration seams where the risk lives |
| **D5** | Frappe CRM | (a) Drop; ERPNext + excom only  (b) Keep for pipeline | Phase 1 gate | Evaluate at the Phase 1 gate with catalogue PoC evidence. Current lean: drop — it duplicates six ERPNext objects and needs a sync bridge |
| **D6** | WABA numbers per company | count and tier | Broadcast sizing | Needs input; weeks of Meta lead time |

## 5.5 Success criteria for the pilot

| Metric | Target | Measured by |
|---|---|---|
| Beat coverage | > 90% | `Beat Visit` vs `Journey Plan` |
| Geo-verified visits | > 95% within radius | `distance_from_outlet_m` |
| Order capture time | median < 3 min | check-in → order submit |
| App crash-free sessions | > 99% | client telemetry |
| Manager live-map lag | < 5 min | ping timestamp vs display |
| Lost writes | 0 | client queue reconciliation report |
| SR daily active use | > 85% of roster | session count |
| Data quality: outlets with GPS | > 80% after 2 cycles | `Outlet.latitude` populated |

## 5.6 What this design deliberately does not do

- **No route optimisation.** Beats are authored by people who know the market. Optimising
  sequence by travel time breaks relationship patterns (shopkeeper availability, credit
  collection order, market timings) that the ASM encodes deliberately.
- **No delivery modelling.** This platform books orders and measures coverage. Physical
  fulfilment is the distributor's, and is tracked in ERPNext where it belongs.
- **No second party master.** Outlets are not Customers, and neither is duplicated.
- **No offline-first architecture.** Three writes are tolerant; everything else assumes
  connectivity.
- **No surveillance framing.** Tracking is bounded to shift hours, visible to the SR,
  and used for coverage credit.

---

## Appendix A — Glossary

| Term | Meaning |
|---|---|
| **Beat** | A recurring set of outlets one SR visits on a given day of a cycle |
| **PJP / Journey Plan** | The calendar assigning beats to days for an SR |
| **Outlet** | A retail shop visited by an SR; not necessarily a billed party |
| **Distributor / Stockist** | The party that buys from the company and supplies outlets |
| **Primary sale** | Company → distributor |
| **Secondary sale** | Distributor → outlet, booked by the company's SR |
| **Strike rate / productivity** | Outlets ordered ÷ outlets visited |
| **Coverage** | Outlets visited ÷ outlets planned |
| **Geo exception** | A check-in outside the outlet's geofence radius |
| **WABA** | WhatsApp Business Account |
| **SR / ASM** | Sales Representative / Area Sales Manager |

## Appendix B — Reused assets inventory

| Asset | Location | Used for |
|---|---|---|
| `Employee Checkin` (lat/lng/geolocation) | `indian_hrms_compliance` | Attendance authority; model for visit check-in |
| `Shift Location` (`checkin_radius`) | `indian_hrms_compliance` | Geofence semantics |
| `Shift Type` (`allow_geolocation_tracking`) | `indian_hrms_compliance` | Tracking enablement per shift |
| `Data Consent` / `Data Retention Rule` | `indian_hrms_compliance` | DPDP model, ported to customer scope |
| `FrappeClient(site, key, secret)` | pattern in `crm` | Cross-site bridge |
| `Sales Person` / `Territory` nested sets | `erpnext` | Hierarchy and visibility |
| `Item` / `Item Price` (`customer`, `supplier`) | `erpnext` | Catalogue and pricing |
| `Omni Identity` + polymorphic links | `excom` | Outlet conversation identity |
| `Excom Broadcast Log`, `delivery_watchdog` | `excom` | Catalogue delivery tracking |
| `channels/voice/*` | `excom` (somil-dev) | Telecalling |
| Offline queue pattern | `posawesome/frontend/src/offline/` | Write-tolerance reference |

---

*End of HLD-001.*
