# Low Level Design — Unified Sales, Service & Field Platform

**Document:** LLD-002 (supersedes LLD-001)
**Version:** 2.0
**Date:** 2026-08-30
**Companion to:** HLD-002
**Status:** Draft for review

> **Baseline (HLD A1/D1):** field orders are **secondary sales** — the SR books an order at
> a retail Outlet which the Distributor fulfils. If the model is primary-only, `Outlet`
> remains the visit target, `Secondary Order` becomes ERPNext `Sales Order`, and nothing
> else changes. Divergences are marked **[D1-variant]**.

---

## Table of Contents

1. [Lifecycle State Machines and Handoff Contracts](#part-1--lifecycle-state-machines-and-handoff-contracts)
2. [Data Model](#part-2--data-model)
3. [API Design](#part-3--api-design)
4. [Processing, Jobs and Cross-Site Integration](#part-4--processing-jobs-and-cross-site-integration)
5. [Client Design](#part-5--client-design)
6. [Security, Migration, Testing and Rollout](#part-6--security-migration-testing-and-rollout)

---

# Part 1 — Lifecycle State Machines and Handoff Contracts

## 1.1 Master lifecycle state machine

```
                          ╔═══════════════════════════════════════╗
                          ║        OMNI IDENTITY (immortal)       ║
                          ╚═══════════════════════════════════════╝
                                          │
   ┌──────────────────────────────────────┼──────────────────────────────────────┐
   │                                      │                                      │
   ▼  STAGE 1-2  (crm)                    ▼  STAGE 3 boundary                    ▼  STAGE 4-7
┌─────────────────────────┐      ┌──────────────────────┐      ┌───────────────────────────┐
│ CRM Lead                │      │ CONVERSION HANDOFF   │      │ Customer (erpnext)        │
│  New → Contacted →      │─────▶│  transactional       │─────▶│ Outlet (fieldforce)       │
│  Qualified → Nurture    │ Won  │  all-or-none         │      │ Orders · Visits · Tickets │
│         │               │      │  idempotent on deal  │      │ AMC · Invoices            │
│         └─ Junk/Lost    │      └──────────────────────┘      └───────────────────────────┘
│ CRM Deal                │                 │                              │
│  Qualification →        │                 ▼                              ▼
│  Demo → Negotiation →   │        Conversion Log              CRM records become
│  Won / Lost             │        (audit, reversible)         READ-ONLY history
└─────────────────────────┘
```

## 1.2 Lead state machine (`CRM Lead`)

| From | Event | To | Guard | Side effects |
|---|---|---|---|---|
| — | web form / FB / manual / inbound msg | `New` | — | Identity created/linked; provenance stamped; Assignment Rule fires |
| `New` | first outbound contact | `Contacted` | assigned owner exists | `first_response_at` stamped |
| `Contacted` | qualification met | `Qualified` | motion-specific criteria (§1.3) | Deal created |
| `Contacted` | not ready, revisit later | `Nurture` | `next_action_date` set | Added to nurture campaign |
| any | invalid / spam | `Junk` | reason required | Identity flagged `is_spam` |
| any | declined | `Lost` | `lost_reason` required | — |
| `Nurture` | re-engaged | `Contacted` | — | — |

**Guard on `Qualified`:** a Deal must not be created without an `Omni Identity`. This is
what keeps conversation history attached through the funnel.

## 1.3 Qualification criteria per motion

Encoded as a server-side validation, not documentation:

| Motion | Required before `Qualified` |
|---|---|
| Website / generic enquiry | contact number verified · serviceable pincode · product interest |
| Export | country · Incoterm · target quantity · currency · specification attached |
| Distribution / dealer | pincode maps to a Territory with an assigned distributor · shop name · GST or declared unregistered |
| Tele sales | disposition = `Interested` · callback completed · consent captured |

## 1.4 Deal state machine (`CRM Deal`)

```
Qualification ──▶ Demo/Sampling ──▶ Quotation ──▶ Negotiation ──▶ Won ──▶ [CONVERTED]
      │                 │                │              │
      └─────────────────┴────────────────┴──────────────┴──▶ Lost (reason required)
```

Rules:
- No stage transition without a `next_action_date`, else the deal appears on the
  "no next action" exception report after 7 days (HLD G6)
- `Won` is not terminal — **`Converted` is**. A Won deal that has not converted is an
  exception, surfaced daily
- Export deals additionally require an attached quotation reference before `Negotiation`

## 1.5 The conversion handoff contract

The highest-risk transition in the platform. Implemented as one service, one transaction.

### Contract

```
convert_deal(crm_deal: str, party_type: str, options: dict) -> ConversionResult
```

**Preconditions**
| # | Check | Failure code |
|---|---|---|
| C1 | Deal exists and `status = Won` | `DEAL_NOT_WON` |
| C2 | Deal has an `Omni Identity` | `IDENTITY_MISSING` |
| C3 | No Customer already linked to that identity | `ALREADY_CONVERTED` |
| C4 | Caller has `create` on `Customer` | `PERMISSION_DENIED` |
| C5 | Required fields per `party_type` present | `INCOMPLETE_PARTY_DATA` |

**Postconditions — all or none**
| # | Effect |
|---|---|
| E1 | `Customer` created (or matched) with `customer_group`, `territory`, `default_currency` |
| E2 | `first_touch_source`, `first_touch_campaign`, `first_touch_channel`, `first_touch_at`, `first_touch_by` copied to the Customer — **once, immutably** |
| E3 | `Outlet` created when `party_type = Retail` — distributor resolved from territory |
| E4 | `Omni Identity Link` gains Customer (+ Outlet); **CRM Lead and Deal links are retained** |
| E5 | `CRM Deal.status = Converted`; deal locked |
| E6 | `Conversion Log` row written |
| E7 | System message posted into the Excom thread |
| E8 | If `party_type = Retail`, the outlet is queued for beat assignment |

**Idempotency:** keyed on `crm_deal`. A repeat call returns the existing result with
`replayed: true`.

**Atomicity:** one database transaction. Partial conversion — a Customer with no identity
link, or an Outlet with no distributor — is the worst possible outcome and is designed out
rather than repaired later.

### Reference implementation

```python
# fieldforce/services/conversion.py
@frappe.whitelist(methods=["POST"])
def convert_deal(crm_deal: str, party_type: str = "Distributor",
                 options: dict | None = None) -> dict:
    options = options or {}
    existing = frappe.db.get_value("Conversion Log", {"crm_deal": crm_deal},
                                   ["customer", "outlet", "name"], as_dict=True)
    if existing:
        return {**existing, "replayed": True}

    deal = frappe.get_doc("CRM Deal", crm_deal)
    _assert(deal.status == "Won", "DEAL_NOT_WON")
    identity = _identity_for_deal(deal)
    _assert(identity, "IDENTITY_MISSING")
    _assert(not _customer_for_identity(identity), "ALREADY_CONVERTED")

    savepoint = "conv_" + frappe.generate_hash(length=8)
    frappe.db.savepoint(savepoint)
    try:
        customer = _create_customer(deal, identity, party_type, options)
        outlet = _create_outlet(deal, identity, customer, options) \
                 if party_type == "Retail" else None
        _stamp_provenance(customer, identity)                       # E2, once
        _link_identity(identity, customer, outlet)                  # E4, additive
        deal.db_set({"status": "Converted", "converted_on": now_datetime(),
                     "erpnext_customer": customer}, update_modified=False)
        log = _write_conversion_log(deal, identity, customer, outlet)
        _post_system_message(identity, customer, outlet)            # E7
        if outlet:
            frappe.enqueue("fieldforce.tasks.beat.queue_for_assignment",
                           queue="long", outlet=outlet)
        return {"customer": customer, "outlet": outlet,
                "conversion_log": log, "replayed": False}
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
```

### `Conversion Log`

| Field | Type | Notes |
|---|---|---|
| `crm_lead` / `crm_deal` | Data | Source ids (Data, not Link — CRM may be uninstalled later) |
| `omni_identity` | Link | |
| `customer` | Link Customer | |
| `outlet` | Link Outlet | Nullable |
| `party_type` | Select | `Distributor` / `Retail` / `Export` / `Institutional` |
| `converted_by` | Link User | |
| `converted_on` | Datetime | |
| `provenance_json` | Long Text | Snapshot of first-touch fields at conversion |
| `status` | Select | `Success` / `Reversed` |
| `reversal_reason` | Small Text | |

Unique index on `crm_deal`.

**Reversal** (rare, supervisor-only): marks the log `Reversed`, unlocks the deal, and
disables — never deletes — the Customer. Deleting a converted Customer would orphan orders.

## 1.6 Visit state machine

```
        ┌─────────┐
        │ PLANNED │  created by the cycle generator from the Journey Plan
        └────┬────┘
             │ SR opens the outlet in the field app
        ┌────▼──────────┐
        │  CHECKED_IN   │  lat/lng captured, distance computed, geofence evaluated
        └────┬──────────┘
   ┌─────────┼──────────┬──────────────┬───────────────┐
   ▼         ▼          ▼              ▼               ▼
┌───────┐ ┌────────┐ ┌────────┐ ┌────────────┐ ┌──────────────┐
│ORDERED│ │NO_ORDER│ │ CLOSED │ │OWNER_ABSENT│ │SERVICE_DONE  │ (visit_type=Service)
└───┬───┘ └───┬────┘ └───┬────┘ └─────┬──────┘ └──────┬───────┘
    └─────────┴──────────┴────────────┴───────────────┘
                         │ checkout
                  ┌──────▼───────┐
                  │  COMPLETED   │  duration computed, KPIs updated
                  └──────────────┘

  Cycle close (03:00): PLANNED → NOT_VISITED ; CHECKED_IN → COMPLETED (flagged)
```

Invariants:
- Only one visit may be `CHECKED_IN` per user at a time
- A geofence violation is **recorded and flagged**, never rejected — rejection produces
  falsification, not compliance
- `NOT_VISITED` is applied only by the cycle-close job
- After cycle close a visit is read-only except by supervisor override, which is audited

## 1.7 Service visit lifecycle

```
HD Ticket (reactive)          Maintenance Schedule (planned)      Warranty Claim
      │  needs site visit             │ generates due dates              │
      └──────────────┬────────────────┴──────────────────────────────────┘
                     ▼
            Beat Visit (visit_type = Service)
              same check-in / geo / photo / checkout engine
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
   Resolved     Parts Needed   Escalated
        │            │             │
        └────────────┴─────────────┘
                     ▼
        Ticket updated · Maintenance Visit closed · customer notified via excom
```

`Beat Visit.visit_type ∈ {Sales, Service}` and optional links `hd_ticket`,
`maintenance_visit`, `warranty_claim`. This is why field service costs days rather than a
second application.

## 1.8 Cross-motion routing rules

Where an inbound touch goes, decided server-side at creation:

```python
def route_inbound(identity, payload) -> dict:
    if payload.get("country") and payload["country"] != "India":
        return {"motion": "Export", "team": "Export Desk", "doctype": "CRM Lead"}
    if _matches_existing_customer(identity):
        return {"motion": "Service", "team": _support_team(identity),
                "doctype": "HD Ticket"}
    if payload.get("shop_name") or payload.get("gstin"):
        return {"motion": "Distribution", "team": _territory_team(payload["pincode"]),
                "doctype": "CRM Lead"}
    return {"motion": "Generic", "team": "Inbound Desk", "doctype": "CRM Lead"}
```

**An existing customer's message is a service interaction, not a new lead.** Getting this
wrong floods the funnel with support traffic and destroys conversion metrics.

---

# Part 2 — Data Model

## 2.0 App layout

```
apps/fieldforce/fieldforce/
├── hooks.py  install.py  setup.py
├── permissions/            hierarchy.py
├── services/               conversion.py  cycle_generator.py  geofence.py
│                           pricing.py  hrms_bridge.py  track_store.py
│                           kpi.py  routing.py  consent.py
├── api/                    beat.py  visit.py  order.py  track.py
│                           outlet.py  dashboard.py  service.py  conversion.py
├── tasks/                  flush_tracks.py  close_cycle.py  sync_employees.py
│                           snapshot.py  geocode.py  retention.py  beat.py
└── fieldforce/doctype/
    ├── outlet/  outlet_contact/  outlet_consent/
    ├── beat/  beat_outlet/  journey_plan/  journey_plan_day/
    ├── beat_visit/  visit_photo/  no_order_reason/
    ├── field_track/  field_employee_link/  field_sync_log/
    ├── conversion_log/  field_daily_snapshot/
    ├── secondary_order/  secondary_order_item/          [D1-variant]
    └── field_settings/                                   (single)
```

## 2.1 Entity relationships

```
 CRM Lead ──▶ CRM Deal ──conversion──▶ Customer ──< Outlet >── Territory
     │            │                        │          │  │
     └────────────┴──── Omni Identity ─────┴──────────┘  │
                              │                          ├──< Outlet Contact
                              │                          ├──< Outlet Consent
                              ▼                          │
                     Excom Thread/Message                ├──< Beat Outlet >── Beat
                              ▲                          │                     │
                              │                          │              Journey Plan
                     HD Ticket ┘                         │                     │
                                                         └──< Beat Visit ──────┘
                                                                  │  │
                                                                  │  └──< Visit Photo
                                                                  └──▶ Secondary Order
                                                                          └──< Item

 Field Employee Link ──▶ User ──▶ Sales Person (tree)      Field Track (1/emp/day)
        └─ employee_id ──▶ [HRMS site] Employee
```

## 2.2 `Outlet`

| Field | Type | Notes | Req | Idx |
|---|---|---|---|---|
| `naming_series` | Select | `OUT-.YYYY.-.#####` | ✓ | |
| `outlet_name` | Data | Name on the shop board | ✓ | ✓ |
| `outlet_code` | Data | Legacy/external code | | ✓ |
| `status` | Select | `Prospect` / `Active` / `Temporarily Closed` / `Permanently Closed` | ✓ | ✓ |
| `company` | Link Company | | ✓ | ✓ |
| `distributor` | Link Customer | Supplying party | ✓ | ✓ |
| `territory` | Link Territory | Visibility + reporting | ✓ | ✓ |
| `outlet_class` | Select | `A`/`B`/`C`/`D` — drives visit frequency | | ✓ |
| `channel` | Select | `Grocery`/`Chemist`/`Cosmetics`/`Modern Trade`/`HoReCa`/`Other` | | |
| `owner_name` | Data | | | |
| `primary_phone` | Data (Phone) | E.164 on validate | ✓ | ✓ |
| `whatsapp_number` | Data (Phone) | Defaults to primary | | ✓ |
| `email` | Data (Email) | | | |
| `contacts` | Table | → `Outlet Contact` | | |
| `address_line_1` | Data | | ✓ | |
| `landmark` | Data | Critical in Indian addressing | | |
| `city` / `state` / `pincode` | Data / Link / Data | pincode `^[1-9][0-9]{5}$` | ✓ | ✓ |
| `latitude` / `longitude` | Float(8) | | | ✓ |
| `geo_source` | Select | `Not Captured`/`Field Capture`/`Geocoded`/`Manual` | ✓ | |
| `geofence_radius_m` | Int | Per-outlet override | | |
| `omni_identity` | Link Omni Identity | | | ✓ |
| `gstin` / `fssai_license` | Data | Validated if present | | |
| **Provenance (immutable)** | | | | |
| `first_touch_source` | Data | e.g. `Web Form`, `Facebook`, `Field Prospect` | | |
| `first_touch_campaign` | Data | | | |
| `first_touch_channel` | Data | | | |
| `first_touch_at` | Datetime | | | |
| `crm_lead` / `crm_deal` | Data | Source ids, not Links | | |
| **Derived (job-updated)** | | | | |
| `last_visit_date` | Date | | | ✓ |
| `last_order_date` / `last_order_value` | Date / Currency | | | |
| `visits_last_90d` / `orders_last_90d` | Int | | | |

Composite indexes: `(territory,status)`, `(distributor,status)`, `(latitude,longitude)`,
`(city,pincode)`.

```python
def validate(self):
    self.normalize_phones()          # E.164, default IN
    self.validate_pincode()
    self.validate_gstin()
    self.validate_geo()              # both coords or neither
    self.set_geofence_default()
    self.link_omni_identity()        # excom API, idempotent
    self.freeze_provenance()         # first_touch_* immutable after first save
```

## 2.3 `Beat`, `Beat Outlet`

| `Beat` field | Type | Notes |
|---|---|---|
| `naming_series` | Select | `BEAT-.####` |
| `beat_name` | Data | "Karol Bagh — Tuesday" |
| `company` / `territory` | Link | |
| `sales_person` | Link Sales Person | Owner; drives visibility |
| `is_active` | Check | |
| `frequency` | Select | `Weekly`/`Fortnightly`/`Monthly` |
| `day_of_week` | Select | Mon…Sun |
| `week_of_cycle` | Int | 1–4 when frequency ≠ Weekly |
| `start_time` | Time | |
| `outlets` | Table | → `Beat Outlet` |
| `outlet_count` | Int | Derived |
| `avg_coverage_pct` / `avg_productivity_pct` | Percent | Rolling 4 cycles |

`track_changes = 1` — beat composition moves incentive targets and must be auditable.

| `Beat Outlet` field | Type | Notes |
|---|---|---|
| `outlet` | Link Outlet | |
| `sequence` | Int | Authored (10,20,30…), never computed |
| `visit_frequency` | Select | `Every Cycle`/`Alternate`/`Monthly` |
| `preferred_slot` | Select | `Morning`/`Afternoon`/`Evening` |
| `is_active` | Check | |
| `notes` | Small Text | "closed 1–4 pm" |

## 2.4 `Journey Plan`, `Journey Plan Day`

| Field | Type | Notes |
|---|---|---|
| `sales_person` | Link | |
| `from_date` / `to_date` | Date | Typically a month |
| `status` | Select | `Draft`/`Approved`/`Active`/`Closed` |
| `approved_by` | Link User | ASM |
| `schedule` | Table | → `Journey Plan Day` |
| `planned_visits` | Int | Derived |

| `Journey Plan Day` | Type | Notes |
|---|---|---|
| `visit_date` | Date | |
| `beat` | Link Beat | Null for non-beat days |
| `day_type` | Select | `Beat`/`Weekly Off`/`Holiday`/`Meeting`/`Leave` |
| `planned_outlets` | Int | Snapshot at generation |

## 2.5 `Beat Visit`

| Field | Type | Notes | Idx |
|---|---|---|---|
| `naming_series` | Data | `VIS-.YYYY.-.######` | |
| `visit_type` | Select | **`Sales` / `Service`** | ✓ |
| `visit_date` | Date | | ✓ |
| `sales_person` | Link | | ✓ |
| `employee_id` | Data | HRMS id, denormalised | |
| `user` | Link User | | |
| `company` | Link | | ✓ |
| `beat` / `journey_plan` | Link | Null for ad-hoc | ✓ |
| `outlet` | Link Outlet | | ✓ |
| `planned_sequence` / `actual_sequence` | Int | | |
| `checkin_time` | Datetime | | ✓ |
| `checkin_latitude` / `checkin_longitude` | Float(8) | | |
| `checkin_accuracy_m` | Float | | |
| `distance_from_outlet_m` | Float | Haversine, server-computed | ✓ |
| `geo_exception` | Check | | ✓ |
| `geo_exception_reason` | Small Text | Required when flagged | |
| `checkout_time` | Datetime | | |
| `checkout_latitude` / `checkout_longitude` | Float(8) | | |
| `duration_minutes` | Int | Derived | |
| `status` | Select | `Planned`/`Checked In`/`Completed`/`Not Visited` | ✓ |
| `outcome` | Select | `Order`/`No Order`/`Shop Closed`/`Owner Absent`/`Service Done`/`Not Visited` | ✓ |
| `no_order_reason` | Link No Order Reason | | |
| `secondary_order` | Link | **[D1-variant]** `Sales Order` | ✓ |
| `order_value` / `order_lines` | Currency / Int | Denormalised for reporting | |
| **Service fields** | | | |
| `hd_ticket` | Data | Helpdesk ticket id | ✓ |
| `maintenance_visit` | Link Maintenance Visit | | |
| `warranty_claim` | Link Warranty Claim | | |
| `service_outcome` | Select | `Resolved`/`Parts Needed`/`Escalated`/`Revisit Required` | |
| `parts_used` | Table | → `Visit Part` | |
| **Integrity** | | | |
| `competitor_notes` | Small Text | | |
| `photos` | Table | → `Visit Photo` | |
| `is_offline_capture` | Check | | |
| `client_generated_id` | Data | **Unique** — the dedupe key | ✓ U |
| `device_time` / `synced_at` | Datetime | Clock-skew analysis | |

Composite indexes: `(sales_person,visit_date)`, `(outlet,visit_date)`,
`(company,visit_date,status)`, `(beat,visit_date)`, `(visit_type,visit_date)`.

**Excluded from excom's `doc_events["*"]` wildcard.**

## 2.6 `Field Track`

**One document per employee per day** — the load-bearing decision.

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Data | `TRK-.YYYY.-.######` |
| `employee_id` | Data | |
| `sales_person` / `user` / `company` | Link | |
| `track_date` | Date | |
| `route_points` | Long Text | **Encoded polyline**, precision 5 |
| `point_count` | Int | |
| `first_ping` / `last_ping` | Datetime | |
| `distance_km` | Float | Cumulative haversine |
| `idle_minutes` | Int | > 10 min inside a 50 m radius |
| `stop_count` | Int | Cluster detection |
| `max_speed_kmph` | Float | Outlier detection |
| `battery_start` / `battery_end` | Int | Diagnoses tracking drop-off |
| `flush_count` | Int | |

Unique `(employee_id, track_date)`; index `(sales_person, track_date)`; retention 90 days.
Hot store: Redis ZSET `track:{company}:{employee_id}:{date}`, TTL 36 h, read only by the
live map.

## 2.7 `Secondary Order` **[D1-variant]**

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Data | `SO2-.YYYY.-.######` |
| `outlet` | Link Outlet | |
| `distributor` | Link Customer | Fetched from outlet |
| `beat_visit` | Link Beat Visit | |
| `sales_person` / `company` | Link | |
| `order_date` / `expected_delivery_date` | Date | |
| `price_list` | Link Price List | Resolved server-side |
| `currency` | Link Currency | |
| `items` | Table | → `Secondary Order Item` |
| `total_qty` / `net_total` / `discount_amount` / `grand_total` | Float / Currency | |
| `status` | Select | `Draft`/`Submitted`/`Acknowledged`/`Fulfilled`/`Cancelled` |
| `distributor_ack_on` | Datetime | |
| `client_generated_id` | Data | Unique |
| `is_offline_capture` | Check | |

Submittable. Indexes `(outlet,order_date)`, `(distributor,status)`,
`(sales_person,order_date)`.

| `Secondary Order Item` | Type | Notes |
|---|---|---|
| `item_code` / `item_name` | Link Item / Data | |
| `uom` / `conversion_factor` | Link / Float | |
| `qty` | Float | |
| `rate` | Currency | **Server-resolved; client value discarded** |
| `discount_percentage` | Percent | Capped by authority |
| `amount` | Currency | Computed |
| `scheme_applied` | Data | Trade scheme id |

**Price precedence** (`services/pricing.py`):

```
1. Item Price (item, price_list, customer=distributor, selling=1, valid today)
2. Item Price (item, price_list, selling=1)  + qty break by packing_unit
3. Item.standard_rate
4. else reject line -> PRICE_NOT_FOUND
```

## 2.8 Supporting doctypes

### `Field Employee Link`
`employee_id` (unique) · `employee_name` · `user` (unique) · `sales_person` · `hrms_site` ·
`designation` · `reporting_to_employee_id` · `shift_type` · `is_active` · `last_synced` ·
`sync_error`.
**Never cache** salary, bank, PAN, Aadhaar or address — ids and display only. This keeps
operational sites out of DPDP scope for employee PII.

### `Field Sync Log`
`sync_type` (`Employee`/`Shift`/`Attendance Mirror`) · `started_at` · `finished_at` ·
`status` · `records_processed` · `records_failed` · `error_detail`.

### `Outlet Consent`
Mirrors HRMS `Data Consent`, scoped to outlets: `outlet` · `omni_identity` · `purpose` ·
`channel` · `lawful_basis` · `consent_status` · `consent_method` · `consent_version` ·
`granted_on` · `withdrawn_on` · `expires_on` · `captured_by` · `proof_attachment`.
Enforcement: every broadcast and every outbound dial calls
`has_consent(omni_identity, purpose, channel)` first.

### `Field Daily Snapshot`
One row per `(sales_person, date)`: `planned_outlets` · `visited_outlets` ·
`productive_outlets` · `coverage_pct` · `productivity_pct` · `order_count` ·
`order_value` · `order_lines` · `lines_per_call` · `value_per_call` · `first_checkin` ·
`last_checkout` · `time_in_market_min` · `selling_time_min` · `travel_time_min` ·
`geo_exceptions` · `distance_km` · `service_visits`.

### `Field Settings` (Single)
`default_geofence_radius_m` 100 · `allow_visit_outside_geofence` 1 ·
`require_geo_exception_reason` 1 · `track_interval_moving_s` 60 ·
`track_interval_stationary_s` 300 · `track_flush_interval_min` 15 ·
`track_retention_days` 90 · `max_discount_percent` 5 · `allow_off_beat_visits` 1 ·
`hrms_site_url` · `hrms_api_key` · `hrms_api_secret` (Password) ·
`employee_sync_cron` `0 2 * * *` · `photo_max_kb` 800 · `enable_service_visits` 1.

## 2.9 Index DDL

```sql
-- fieldforce
CREATE INDEX idx_outlet_terr_status  ON `tabOutlet` (territory, status);
CREATE INDEX idx_outlet_dist_status  ON `tabOutlet` (distributor, status);
CREATE INDEX idx_outlet_geo          ON `tabOutlet` (latitude, longitude);
CREATE UNIQUE INDEX uq_visit_client  ON `tabBeat Visit` (client_generated_id);
CREATE INDEX idx_visit_sp_date       ON `tabBeat Visit` (sales_person, visit_date);
CREATE INDEX idx_visit_outlet_date   ON `tabBeat Visit` (outlet, visit_date);
CREATE INDEX idx_visit_type_date     ON `tabBeat Visit` (visit_type, visit_date);
CREATE UNIQUE INDEX uq_track_emp_day ON `tabField Track` (employee_id, track_date);
CREATE UNIQUE INDEX uq_conv_deal     ON `tabConversion Log` (crm_deal);
CREATE INDEX idx_so2_outlet_date     ON `tabSecondary Order` (outlet, order_date);

-- excom gaps
CREATE INDEX idx_bcastlog_bcast_st   ON `tabExcom Broadcast Log` (broadcast, status);
CREATE INDEX idx_msg_identity_dt     ON `tabExcom Message` (omni_identity, creation);
CREATE INDEX idx_msg_status_dt       ON `tabExcom Message` (delivery_status, creation);
CREATE INDEX idx_oi_norm_phone       ON `tabOmni Identity` (normalized_phone);
CREATE INDEX idx_oi_norm_email       ON `tabOmni Identity` (normalized_email);
```

---

# Part 3 — API Design

## 3.0 Conventions

- Frappe whitelisted methods; `require_type_annotated_api_methods = True`
- Auth: session (field app) or `token api_key:api_secret` (server-to-server)
- Every write is idempotent on `client_generated_id`
- Stable machine error codes, not just messages

| Code | HTTP | Retry | Meaning |
|---|---|---|---|
| `NOT_CHECKED_IN` | 417 | no | Attendance not marked today |
| `VISIT_ALREADY_OPEN` | 409 | no | Another visit is open |
| `DUPLICATE_CLIENT_ID` | 200 | — | Idempotent replay; original returned |
| `GEOFENCE_REASON_REQUIRED` | 400 | no | Outside radius, no explanation |
| `PRICE_NOT_FOUND` | 400 | no | |
| `DISCOUNT_EXCEEDS_AUTHORITY` | 403 | no | |
| `DEAL_NOT_WON` | 400 | no | Conversion precondition |
| `IDENTITY_MISSING` | 400 | no | Conversion precondition |
| `ALREADY_CONVERTED` | 409 | no | |
| `INCOMPLETE_PARTY_DATA` | 400 | no | |
| `CONSENT_MISSING` | 403 | no | Outbound blocked |
| `HRMS_UNAVAILABLE` | 503 | **yes** | Cache fallback used |
| `RATE_LIMITED` | 429 | **yes** | Honour `Retry-After` |

## 3.1 Lifecycle / conversion

```
POST fieldforce.api.conversion.convert_deal(
        crm_deal: str, party_type: str = "Distributor",
        options: dict | None = None) -> dict

GET  fieldforce.api.conversion.preview(crm_deal: str) -> dict
POST fieldforce.api.conversion.reverse(conversion_log: str, reason: str) -> dict
GET  fieldforce.api.conversion.party_journey(omni_identity: str) -> dict
```

`preview` runs the precondition checks and returns what *would* be created — used by the
CRM Form Script button so the operator sees the outcome before committing.

`party_journey` assembles the HLD §2.3 view on demand:

```jsonc
{
  "identity": {"name": "OMNI-0009912", "display_name": "Sharma General Store"},
  "first_touch": {"source": "Web Form", "campaign": "SPRING26",
                  "channel": "website", "at": "2026-02-11 10:42:00"},
  "crm": {"lead": "CRM-LEAD-00412", "deal": "CRM-DEAL-00190",
          "deal_status": "Converted", "won_on": "2026-03-02"},
  "commerce": {"customer": "CUST-00877", "outlet": "OUT-2026-01422",
               "orders": 37, "order_value": 420350.0,
               "last_order": "2026-08-24"},
  "field": {"visits": 112, "coverage_pct": 91.0, "strike_rate_pct": 68.0,
            "last_visit": "2026-08-24"},
  "service": {"tickets": 2, "open": 0, "next_amc": "2026-09-15"},
  "conversation": {"threads": 1, "messages": 214, "calls": 9, "catalogues_sent": 6}
}
```

Assembled, cached 60 s, never materialised (HLD P1).

## 3.2 Day bootstrap

```
GET fieldforce.api.beat.get_my_day(date: str = "") -> dict
```

One call, everything the field app needs — avoids N+1 on 4G.

```jsonc
{
  "date": "2026-08-31",
  "sales_person": "SP-0042", "employee_id": "HR-EMP-00123",
  "attendance": {"checked_in": true, "checkin_time": "2026-08-31 09:12:04",
                 "shift": "General", "shift_start": "09:00:00",
                 "shift_end": "18:00:00",
                 "source": "hrms", "cache_age_s": 0},
  "beat": {"name": "BEAT-0117", "beat_name": "Karol Bagh — Tuesday",
           "planned_outlets": 24},
  "outlets": [
    {"visit": "VIS-2026-000881", "outlet": "OUT-2026-01422",
     "outlet_name": "Sharma General Store", "sequence": 10,
     "status": "Planned", "visit_type": "Sales",
     "owner_name": "Rakesh Sharma", "primary_phone": "+919812345678",
     "address_line_1": "12/4 Ajmal Khan Road", "landmark": "opp. Metro Gate 3",
     "latitude": 28.6519, "longitude": 77.1903, "geofence_radius_m": 100,
     "last_visit_date": "2026-08-24", "last_order_value": 4820.0,
     "outstanding_flag": false, "omni_identity": "OMNI-0009912"}
  ],
  "service_visits": [
    {"visit": "VIS-2026-000902", "visit_type": "Service",
     "outlet": "OUT-2026-00911", "hd_ticket": "HD-TKT-00231",
     "priority": "High", "sla_due": "2026-08-31 16:00:00"}
  ],
  "settings": {"track_interval_moving_s": 60, "track_interval_stationary_s": 300,
               "track_flush_interval_min": 15, "photo_max_kb": 800,
               "max_discount_percent": 5, "require_geo_exception_reason": true},
  "server_time": "2026-08-31 09:15:22"
}
```

## 3.3 Visit lifecycle

```
POST fieldforce.api.visit.checkin(visit, latitude, longitude, accuracy_m,
                                  client_generated_id, device_time, battery=0) -> dict
POST fieldforce.api.visit.checkout(visit, latitude, longitude, outcome,
                                   no_order_reason="", competitor_notes="",
                                   geo_exception_reason="",
                                   service_outcome="", client_generated_id="") -> dict
POST fieldforce.api.visit.add_photo(visit, photo_type, file_url, caption="") -> dict
POST fieldforce.api.visit.create_adhoc(outlet, latitude, longitude, ...) -> dict
```

```python
@frappe.whitelist(methods=["POST"])
@rate_limit(key="user", limit=240, seconds=3600)
def checkin(visit: str, latitude: float, longitude: float, accuracy_m: float,
            client_generated_id: str, device_time: str, battery: int = 0) -> dict:
    existing = frappe.db.get_value("Beat Visit",
                                   {"client_generated_id": client_generated_id}, "name")
    if existing:
        return _visit_payload(existing, replayed=True)      # idempotent
    _assert_attendance_marked()          # HRMS, 4h cache, never blocks on outage
    _assert_no_open_visit(frappe.session.user)
    ...
```

Checkout validation:

| Condition | Result |
|---|---|
| `outcome = No Order` without reason | `NO_ORDER_REASON_REQUIRED` |
| `geo_exception` without reason (when required) | `GEOFENCE_REASON_REQUIRED` |
| `outcome = Order` without a linked order | `ORDER_MISSING` |
| `visit_type = Service` without `service_outcome` | `SERVICE_OUTCOME_REQUIRED` |
| Visit not `Checked In` | `INVALID_STATE` |

## 3.4 Ordering

```
GET  fieldforce.api.order.get_catalogue(outlet, search="", item_group="",
                                        limit=50, offset=0) -> dict
POST fieldforce.api.order.submit(outlet, visit, items, client_generated_id,
                                 expected_delivery_date="", remarks="") -> dict
```

`get_catalogue` returns items **already priced for this outlet's context**; the client
never computes price.

```jsonc
{"price_list": "Distributor North", "currency": "INR",
 "items": [{"item_code": "SKU-1001", "item_name": "Detergent 1kg",
            "uom": "Nos", "conversion_factor": 1,
            "rate": 118.50, "mrp": 145.00,
            "last_ordered_qty": 12, "last_ordered_on": "2026-08-24",
            "scheme": "10+1", "image": "/files/sku1001.jpg", "in_stock": true}],
 "has_more": true}
```

`last_ordered_qty` is the highest-leverage field in the payload — reorder-from-last is how
field orders are actually taken.

`submit` server behaviour: idempotency check → resolve price list → resolve each rate
server-side (**client rates discarded**) → validate discount → create+submit order →
update the visit → enqueue distributor notification via excom.

## 3.5 Track ingestion

```
POST fieldforce.api.track.ingest(points: list[dict]) -> dict
```

```python
@frappe.whitelist(methods=["POST"])
@rate_limit(key="user", limit=60, seconds=3600)
def ingest(points: list[dict]) -> dict:
    link = _employee_link(frappe.session.user)
    if not link or not link.is_active:
        return {"accepted": 0, "reason": "NO_ACTIVE_EMPLOYEE_LINK"}
    key = f"track:{link.company}:{link.employee_id}:{nowdate()}"
    pipe = frappe.cache().pipeline()
    for pt in points[:200]:
        if not _valid_point(pt):
            continue
        pipe.zadd(key, {f"{pt['lat']},{pt['lng']},{pt.get('acc',0)},"
                        f"{pt.get('bat',0)}": int(pt["t"])})
    pipe.expire(key, 36 * 3600)
    pipe.execute()
    return {"accepted": len(points), "next_interval_s": _interval_for(link)}
```

Redis only on the request path — **never a document**. `next_interval_s` lets the server
throttle chatty devices without a client release.

## 3.6 Dashboards

```
GET fieldforce.api.dashboard.live_map(territory="", beat="") -> dict     # Redis only
GET fieldforce.api.dashboard.coverage(from_date, to_date, sales_person="",
                                      territory="") -> dict              # snapshot/replica
GET fieldforce.api.dashboard.productivity(...) -> dict                   # snapshot/replica
GET fieldforce.api.dashboard.funnel(from_date, to_date) -> dict          # CRM + conversion
GET fieldforce.api.dashboard.exceptions(date="") -> dict                 # live, small
```

`funnel` is the lifecycle report: leads by source → qualified → deals → converted →
first order, with conversion rate and median days per stage. It reads CRM plus
`Conversion Log` plus first-order dates — the only place the two worlds are joined for
reporting.

`exceptions`: geo exceptions · zero-visit SRs · visits without checkout · orders above
discount authority · Won deals not converted · leads unassigned > 2 h · deals with no next
action > 7 days.

## 3.7 Service

```
POST fieldforce.api.service.create_visit_from_ticket(hd_ticket, outlet,
                                                     scheduled_date, engineer) -> dict
POST fieldforce.api.service.create_visits_from_schedule(maintenance_schedule) -> dict
POST fieldforce.api.service.close_visit(visit, service_outcome, parts_used,
                                        customer_feedback="") -> dict
```

`close_visit` updates the HD Ticket (or Maintenance Visit), records parts, and posts a
completion message into the Excom thread.

## 3.8 Excom and CRM integration

| Purpose | Call |
|---|---|
| Send catalogue | `excom.excom.api.catalogue.send_to_identity(catalogue, omni_identity, account)` |
| Start a call | `excom.excom.api.voice.initiate_call(to_number, thread_id)` |
| Post system message | `excom.excom.api.chat.send_message(thread_id, message, message_type)` |
| Fetch threads | `excom.excom.api.chat.get_threads(...)` |
| Consent gate | `fieldforce.services.consent.has_consent(omni_identity, purpose, channel)` |

**Degradation contract:** if `excom` is absent (`"excom" in frappe.get_installed_apps()`),
comms buttons hide and nothing errors. Same for `crm` and `helpdesk`.

**CRM Form Script**, inserted from `excom/setup.py::after_migrate`, idempotently:

```js
class CRMDeal {
  onLoad() {
    this.actions.push({
      label: __("Convert to Customer"),
      onClick: () => call("fieldforce.api.conversion.preview",
                          {crm_deal: this.doc.name})
        .then(p => formDialog({title: __("Convert"), fields: p.fields,
                               onSubmit: v => call(
                                 "fieldforce.api.conversion.convert_deal",
                                 {crm_deal: this.doc.name, ...v})}))
    })
    this.actions.push({
      label: __("Conversations"),
      onClick: () => createDialog({
        title: __("Excom"), size: "7xl",
        html: `<iframe src="/excom/identity/${this.doc.omni_identity}?embed=1"
                 class="w-full h-[70vh] border-0"></iframe>`})
    })
  }
}
```

Side-panel summary uses a Custom Field (HTML) + a `CRM Fields Layout` section +
`setFieldHtml()` — note that path is DOMPurify-sanitised, so it carries **read-only HTML,
not an iframe**. The iframe lives in the dialog, where `dialogs.jsx` renders raw `v-html`.

---

# Part 4 — Processing, Jobs and Cross-Site Integration

## 4.1 Scheduler map

```python
scheduler_events = {
    "cron": {
        "*/15 * * * *": ["fieldforce.tasks.flush_tracks.flush_all"],
        "*/30 * * * *": ["fieldforce.tasks.mirror.retry_failed_checkin_mirrors"],
        "0 2 * * *":    ["fieldforce.tasks.sync_employees.sync_all"],
        "30 2 * * *":   ["fieldforce.tasks.snapshot.build_daily_snapshot"],
        "0 3 * * *":    ["fieldforce.tasks.close_cycle.close_yesterday"],
        "0 4 * * 0":    ["fieldforce.tasks.cycle_generator.generate_next_month"],
        "0 5 * * *":    ["fieldforce.tasks.retention.purge_expired_tracks"],
        "0 6 * * *":    ["fieldforce.tasks.exceptions.daily_exception_report"],
        "0 7 * * *":    ["fieldforce.tasks.service.generate_due_amc_visits"],
    },
    "hourly_long": ["fieldforce.tasks.geocode.geocode_pending_outlets"],
}
```

## 4.2 Track flush

```
every 15 min, on the `long` queue:
  for each key track:*:*:{today}:
      points = ZRANGEBYSCORE(key, last_flushed_score, +inf)
      if empty: continue
      doc      = get_or_create Field Track (employee_id, track_date)
      merged   = dedupe_sort(decode(doc.route_points) + points)
      doc.route_points  = encode_polyline(merged)
      doc.point_count   = len(merged)
      doc.distance_km   = cumulative_haversine(merged)
      doc.idle_minutes  = idle(merged, radius_m=50, min_minutes=10)
      doc.stop_count    = detect_stops(merged)
      doc.last_ping     = max(t)
      doc.flags.ignore_doc_events = True          # largest writer in the system
      doc.save(ignore_permissions=True)
      set last_flushed_score
```

Encoded polyline at precision 5 ≈ 5 bytes/point → ~480 points/day ≈ **2.4 KB per SR-day**.
Restartable: `last_flushed_score` is kept per key in Redis.

## 4.3 Cycle close (03:00)

```
for each Journey Plan Day (yesterday, day_type = Beat):
    for each Beat Outlet due this cycle:
        no visit          -> create Not Visited / outcome Not Visited
        status Checked In -> force Completed, flag missing_checkout
recompute rolling beat KPIs (last 4 cycles)
lock the day (read-only except supervisor override, audited)
```

## 4.4 Daily snapshot (02:30)

Builds `Field Daily Snapshot` per `(sales_person, date)` so manager rollups are a range
scan over thousands of rows rather than an aggregation over millions of visits. Runs on
the `long` queue; reads may be served from the replica.

## 4.5 Employee sync (02:00)

```
since = last_success - 1 day                 # overlap absorbs clock skew
for each company on this site:
    employees = HRMSBridge.get_employees(company, since)
    for e in employees:
        link = get_or_create Field Employee Link(employee_id=e.name)
        link.update(employee_name, designation, shift, reporting_to, is_active)
        link.user = link.user or e.user_id
        link.sales_person = link.sales_person or find_or_create_sales_person(link)
        link.save()
    reconcile_hierarchy()      # REPORT differences; never auto-rewrite the sales tree
write Field Sync Log
```

HR reporting lines and sales reporting lines are legitimately different in some
organisations. Silently rewriting a commercial hierarchy from HR data would be wrong, so
mismatches are surfaced, not resolved.

## 4.6 HRMS bridge

```python
class HRMSBridge:
    def __init__(self):
        s = frappe.get_single("Field Settings")
        self.client = FrappeClient(s.hrms_site_url, api_key=s.hrms_api_key,
                                   api_secret=s.get_password("hrms_api_secret"))
        self.timeout = 10

    def get_employees(self, company: str, modified_after: str) -> list[dict]: ...
    def get_attendance_state(self, employee_id: str, date: str) -> dict:   # 4h cache
        ...
    def get_shift(self, shift_type: str) -> dict:                          # 24h cache
        ...
    def mirror_checkin(self, employee_id, ts, lat, lng, device_id) -> None: ...
```

| Rule | Detail |
|---|---|
| Read-mostly | The only write is the queued check-in mirror |
| Never blocks | Every read has a cached fallback with a stated TTL |
| Never caches PII | Ids, display name, designation, shift only |
| Failure | 10 s timeout, 2 backoff retries, then cache + `Field Sync Log` row |
| Auth | Per-pair API key/secret, `Password` fieldtype, quarterly rotation |

## 4.7 Check-in mirror

```
on first Beat Visit checkin of the day:
    enqueue(short): mirror_checkin_to_hrms(visit)
        success -> mark mirrored
        failure -> mark mirror_failed; retry every 30 min for 24 h;
                   then raise a Field Sync Log entry
```

The local visit is authoritative for field reporting; HRMS is authoritative for payroll.
Divergence appears in the daily exception report rather than being auto-resolved.

## 4.8 Broadcast fan-out (excom change)

```python
def execute_broadcast(broadcast_name: str):
    for i, chunk in enumerate(_chunks(_recipient_cursor(broadcast_name), 500)):
        batch = frappe.get_doc({"doctype": "Excom Broadcast Batch",
                                "broadcast": broadcast_name, "batch_index": i,
                                "recipient_count": len(chunk),
                                "status": "Pending"}).insert(ignore_permissions=True)
        frappe.enqueue("excom.excom.services.broadcast_service.run_batch",
                       queue="broadcast", batch=batch.name, timeout=900)

def run_batch(batch: str):
    doc = frappe.get_doc("Excom Broadcast Batch", batch)
    if doc.status == "Done":
        return                                              # idempotent
    doc.db_set("status", "Running")
    bucket = TokenBucket(account=doc.account, rate_per_sec=settings.wa_rate)
    for oi in _batch_recipients(doc):
        if not has_consent(oi, doc.purpose, doc.channel):    # DPDP gate
            _log(doc, oi, "Skipped", "CONSENT_MISSING"); continue
        bucket.acquire()
        try:
            _send(doc.broadcast, oi)
        except RateLimited as e:
            _requeue(doc, oi, delay=e.retry_after)
        except Exception:
            _log_failure(doc, oi)
    doc.db_set("status", "Done")
```

## 4.9 Queue configuration

```
worker_short:     bench worker --queue short      (4)
worker_default:   bench worker --queue default    (4)
worker_long:      bench worker --queue long       (2)
worker_broadcast: bench worker --queue broadcast  (6)
worker_voice:     bench worker --queue voice      (2)
```

| Job | Queue | Timeout |
|---|---|---|
| Track ingest | — (request path) | — |
| Track flush | long | 600 s |
| Check-in mirror | short | 30 s |
| Order → distributor notify | short | 60 s |
| Conversion (interactive) | — (request path, transactional) | — |
| Employee sync / snapshot / cycle close | long | 1800 s |
| Broadcast batch | broadcast | 900 s |
| Catalogue render | default | 300 s |
| AMC visit generation | long | 600 s |

---

# Part 5 — Client Design

## 5.1 Surfaces and stacks

| Surface | Stack | Route | Users |
|---|---|---|---|
| Field app | React 18 + TS + Tailwind + `frappe-react-sdk` (PWA) | `/field` | SR, service engineer |
| Manager dashboard | Same stack, desktop layout | `/field/manage` | ASM, RM, ZM |
| Excom inbox | Existing excom SPA | `/excom` | CS agents, telecallers |
| Telecaller console | Excom shell, call-list mode | `/excom/calls` | Telecallers |
| CRM | Frappe CRM (Vue), unforked | `/crm` | Inbound, export |
| Helpdesk | Helpdesk (Vue), unforked | `/helpdesk` | Support |
| Back-office | ERPNext Desk | `/app` | Accounts, ops |

Field app registration:

```python
website_route_rules = [{"from_route": "/field/<path:app_path>", "to_route": "field"}]
add_to_apps_screen = [{"name": "fieldforce", "title": "Field Sales",
                       "route": "/field", "logo": "/assets/fieldforce/logo.svg"}]
```

## 5.2 Field app screen map

```
/field                        Day view (home)
/field/outlet/:visitId        Visit detail — checkin, order, photos, checkout
/field/order/:visitId         Order capture (full screen)
/field/service/:visitId       Service visit — ticket context, parts, outcome
/field/outlet/:id/profile     Outlet profile, order history, conversations
/field/catalogue              Catalogue builder and send
/field/summary                My day — coverage, orders, distance, own GPS trail
/field/sync                   Pending queue and conflicts (visible, never hidden)
/field/manage/*               Manager: live map, coverage, productivity, exceptions
```

**Day view** — the only screen an SR sees for most of the day:

```
┌────────────────────────────────────┐
│ Karol Bagh — Tuesday        ⋮      │
│ 11 / 24 done   ·   7 orders        │
│ ▓▓▓▓▓▓▓░░░░░░░░  46%               │
├────────────────────────────────────┤
│ ⏳ 3 pending sync            [↻]   │  ← only when the outbox is non-empty
├────────────────────────────────────┤
│ 🔧 1 service visit due 16:00       │  ← service work inline with the beat
├────────────────────────────────────┤
│ ✓ 10  Sharma General Store         │
│       ₹4,820 · 14 lines            │
├────────────────────────────────────┤
│ ▶ 20  Gupta Provision       120 m  │  ← next; distance live
│       Last: 24 Aug · ₹3,110        │
│       [Check In]    [Call]  [Cat.] │
├────────────────────────────────────┤
│   30  New Bharat Store             │
└────────────────────────────────────┘
```

Design rules derived from how the app is actually used — one hand, sunlight, hurry:
primary action always one tap from the day view; live distance to the next outlet; last
visit and last order inline (they drive the sales conversation); **sync state visible, never
hidden**; 44 px minimum touch targets; high-contrast palette.

## 5.3 Client storage

```
IndexedDB
├── day_cache      today's get_my_day payload      (TTL to midnight)
├── outbox         queued writes                   (§5.4)
├── track_buffer   GPS points awaiting upload
└── item_cache     price-list catalogue            (TTL 24 h)
localStorage       session prefs, last sync ts
memory             React state
```

`item_cache` is the difference between a 3-second and a 30-second order. Prefetched once
at day start — typically 500–2,000 SKUs, ~300 KB gzipped.

## 5.4 Write-tolerance queue

Only three write types are queued. Everything else requires connectivity.

| Action | Queued | Rationale |
|---|---|---|
| Check-in | ✓ | Loss makes a real visit look like a miss |
| Order submit | ✓ | Loss is revenue and rep trust |
| Check-out | ✓ | Loss leaves the visit permanently open |
| Photo upload | ✓ (low priority) | Large; retried opportunistically |
| GPS ping | buffered, not queued | A gap is acceptable |
| Catalogue send / dashboards | ✗ | Require the server anyway |

```ts
interface OutboxItem {
  id: string;                 // uuid v4 == client_generated_id
  type: 'checkin' | 'order' | 'checkout' | 'photo';
  endpoint: string;
  payload: Record<string, unknown>;
  created_at: number;
  attempts: number;
  last_error?: string;
  status: 'pending' | 'sending' | 'failed' | 'done';
  depends_on?: string;        // an order depends on its check-in
}
```

```
on (online | foreground | every 60 s while online):
  items = outbox.where(status in [pending, failed]).orderBy(created_at asc)   # strict FIFO
  for item in items:
      if item.depends_on and not done(item.depends_on): continue
      if item.attempts >= 8: mark 'failed'; surface on /field/sync; continue
      mark 'sending'
      try:
          res = POST item.endpoint, item.payload      # carries client_generated_id
          mark 'done'; reconcile local state from res
      except NetworkError:      mark 'pending'; break          # preserve order
      except Retryable:         attempts++; backoff min(2**n,300)s; mark 'pending'
      except NonRetryable:      mark 'failed'; show the server message
```

**Idempotency contract.** Every queued write carries `client_generated_id`; the server
looks it up first and returns the original with `replayed: true`. This is why
`client_generated_id` is a **unique index** — the database, not application logic, is the
guarantee.

| Conflict | Resolution |
|---|---|
| Same client id replayed | Server returns the original |
| Check-in replayed after cycle close | Accepted, flagged `late_sync`, appears in exceptions |
| Order for a visit closed on another device | Rejected `INVALID_STATE`, shown on `/field/sync` |
| Device clock skew > 5 min | Server time is authoritative; `device_time` kept for analysis |

## 5.5 GPS collection

```ts
const opts = { enableHighAccuracy: true, maximumAge: 15000, timeout: 20000 };

function nextInterval(last: Fix, cur: Fix): number {
  return haversine(last, cur) < 30
    ? settings.track_interval_stationary_s     // 300
    : settings.track_interval_moving_s;        // 60
}
```

Buffer in IndexedDB; upload every `track_flush_interval_min` or at 50 points. Discard
fixes with `accuracy > 100 m`. Stop outside shift hours. **Show the SR their own trail** on
`/field/summary` — transparency is the mitigation for HLD R3, and it is genuinely useful to
them for expense claims.

## 5.6 Performance techniques

| Technique | Effect |
|---|---|
| Single `get_my_day` bootstrap | 1 request instead of ~8 |
| Item cache prefetch at day start | Order screen opens instantly |
| Route-level code splitting | Day-view bundle < 150 KB gzipped |
| Client-side image compression | 4 MB photo → < 800 KB |
| Virtualised item list | 2,000 SKUs scroll smoothly |
| Optimistic UI on queued writes | No spinner between tap and next action |
| `stale-while-revalidate` on profiles | Instant open, refresh behind |

---

# Part 6 — Security, Migration, Testing and Rollout

## 6.1 Roles matrix

| Role | Outlet | Beat | JP | Visit | Order | Track | CRM Lead | Ticket | Dash |
|---|---|---|---|---|---|---|---|---|---|
| Field Sales Rep | R sub | R | R | CRU own | CRU own | R own | — | R linked | own |
| Service Engineer | R sub | — | R | CRU own (Service) | — | R own | — | RW assigned | own |
| Area Sales Manager | RW sub | CRUD | CRUD | RW sub | R sub | R sub | R sub | R sub | sub |
| Regional Manager | R sub | RW sub | R sub | R sub | R sub | R sub | R sub | R sub | sub |
| Inbound Executive | — | — | — | — | — | — | CRUD own | — | own |
| Export Manager | — | — | — | — | R | — | CRUD sub | — | sub |
| Telecaller | R sub | — | — | — | CRU | — | RW assigned | — | own |
| Support Agent | R | — | — | R | R | — | R | CRUD team | team |
| Field Admin | CRUD | CRUD | CRUD | RW | R | R | R | R | all |
| Group Global Viewer | R all | R all | R all | R all | R all | R all | R all | R all | all |

Field-level (`permlevel = 1`): margin, landed cost, distributor credit — hidden from
Field Sales Rep and Telecaller.

## 6.2 Subtree permissions

```python
# fieldforce/permissions/hierarchy.py
_SP_FIELD = {"Beat": "sales_person", "Journey Plan": "sales_person",
             "Beat Visit": "sales_person", "Secondary Order": "sales_person",
             "Field Track": "sales_person", "Outlet": None}

def _subtree(user: str):
    sp = frappe.db.get_value("Field Employee Link", {"user": user}, "sales_person")
    if not sp:
        return None
    lft, rgt = frappe.db.get_value("Sales Person", sp, ["lft", "rgt"])
    SP = frappe.qb.DocType("Sales Person")
    return frappe.qb.from_(SP).select(SP.name).where((SP.lft >= lft) & (SP.rgt <= rgt))

def get_permission_query_conditions(doctype: str, user: str | None = None) -> str:
    user = user or frappe.session.user
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or roles & {"System Manager", "Group Global Viewer"}:
        return ""
    sub = _subtree(user)
    if sub is None:
        return "1=0"                       # FAIL CLOSED
    field = _SP_FIELD[doctype]
    DT = frappe.qb.DocType(doctype)
    cond = DT[field].isin(sub) if field else _outlet_conditions(user, sub)
    return cond.get_sql(with_namespace=True, quote_char="`", secondary_quote_char="'")
```

Registered in `hooks.py` for every business doctype, with a matching `has_permission` so
single-document access uses the identical rule. The same pattern is applied to `Customer`,
`CRM Lead`, `CRM Deal` and `HD Ticket` from `fieldforce`, giving one hierarchy across all
apps.

## 6.3 Security checklist

| Item | Implementation |
|---|---|
| Cross-site credentials | Per-pair key/secret, `Password` fieldtype, quarterly rotation, never logged |
| Webhook auth | `hmac.compare_digest`, never `==` |
| Realtime scoping | `publish_realtime(..., user=...)` on every emit |
| Rate limits | ingest 60/h · checkin 240/h · order 120/h · catalogue 600/h · conversion 60/h |
| Price integrity | Server-resolved rates; client values discarded |
| Discount authority | Server-enforced ceiling |
| Consent gate | Checked before every broadcast send and outbound dial |
| Photo upload | Type allow-list, size cap, EXIF stripped, private files |
| PII minimisation | No employee personal data on operational sites |
| Conversion audit | `Conversion Log` immutable; reversal recorded, never deleted |
| Data access log | Bulk exports and PII views |

## 6.4 Migration and onboarding

**Step 1 — Outlet master import** (the hardest part of any SFA rollout)

```
CSV: outlet_name, outlet_code, distributor_code, territory, owner_name, phone,
     address_line_1, landmark, city, state, pincode, outlet_class, channel,
     [latitude], [longitude]

Pipeline:
 1. validate distributor exists          -> else reject row
 2. normalise phone to E.164 (default IN)
 3. dedupe within file: phone, then (name + pincode) fuzzy
 4. dedupe against existing: Omni Identity phone match
 5. insert Outlet; link/create Omni Identity
 6. missing coords -> queue geocode; geocode fail -> leave blank,
    let the first check-in capture it (progressive geocoding)
 7. emit import report: accepted / rejected / merged, with reasons
```

Expect **20–40% of rows to need manual attention** on the first import. Plan for it; it is
not an edge case.

**Step 2 — Beat construction.** Import from existing route sheets where they exist;
otherwise build from `Outlet.territory` plus ASM input. Beats are authored, never inferred.

**Step 3 — Employee link.** Run `sync_employees`, map users to Sales Person nodes,
reconcile HR vs sales reporting lines with sales leadership.

**Step 4 — CRM backfill.** For existing customers that originated in CRM, run a one-time
`Conversion Log` backfill so `party_journey` is complete from day one. Where provenance is
unknown, stamp `first_touch_source = "Legacy"` rather than leaving it blank.

**Step 5 — Historical orders.** Optionally load 90 days so `last_order_date` and
`last_order_value` are populated — the app then looks credible to reps immediately.

**Rollback:** `fieldforce` is a separate app. `bench uninstall-app fieldforce` removes
field data without touching ERPNext, CRM, excom or Helpdesk. This is a primary reason for
the separate app.

## 6.5 Test plan

**Unit** (`bench run-tests --app fieldforce`)

| Module | Cases |
|---|---|
| `geofence` | Haversine accuracy; exact-radius boundary; missing coords; antimeridian |
| `pricing` | Party-specific; price-list fallback; standard rate; qty break; not found; discount cap |
| `cycle_generator` | Weekly/fortnightly/monthly; holiday; weekly off; duplicate beats; idempotent rerun |
| `track_store` | Encode/decode round trip; dedupe; distance; idle detection; malformed points |
| `hierarchy` | Root; leaf; **no link → 1=0**; Global Viewer; multi-branch |
| `conversion` | All five preconditions; all-or-none rollback; idempotent replay; reversal |
| `routing` | Export vs service vs distribution vs generic classification |
| `consent` | Granted / withdrawn / expired / missing per purpose and channel |
| `hrms_bridge` | Timeout → cache; partial failure; malformed response |

**Integration**

| Scenario | Assertion |
|---|---|
| Web form → lead → qualify → deal → convert → first order | Single identity throughout; provenance intact on Customer |
| Conversion failure mid-way (Outlet creation raises) | Nothing committed; deal still `Won`; clear error |
| Conversion replay | Same Customer returned; `replayed: true`; one `Conversion Log` |
| Full visit: checkin → order → checkout | Visit completed, order submitted, KPIs updated |
| Replay of all three writes | Exactly one visit and one order |
| Check-in without attendance | 417 `NOT_CHECKED_IN` |
| Check-in 500 m away | Recorded, `geo_exception=1`, reason required |
| Check-in at an ungeocoded outlet | Outlet coordinates captured; no exception raised |
| Order with a tampered client rate | Server rate used |
| Broadcast to a withdrawn-consent identity | Skipped, logged `CONSENT_MISSING` |
| Ticket → service visit → close | Ticket updated; excom message posted |
| HRMS down at day start | Day loads from cache; `source: "cache"` |
| Cycle close with an open visit | Forced complete, `missing_checkout` flagged |

**Load**

| Test | Target |
|---|---|
| Track ingest | 500 SRs × 5-min batches, 8 h sustained, p95 < 200 ms |
| Flush job | 500 SR-days in < 5 min |
| Live map | 200 agents, p95 < 1 s |
| Order submit | 50 concurrent × 20 lines, p95 < 1.5 s |
| Broadcast | 100k recipients batched; no starvation of `short`/`voice` |
| Coverage dashboard | 12 months over a 200-SR subtree, p95 < 2 s |
| Conversion | 20 concurrent, no deadlocks |

**Field acceptance (pilot):** real low-end Android devices, 2G/3G fallback, direct
sunlight readability, one-handed operation, battery over a full 8-hour beat.

## 6.6 Rollout

| Stage | Scope | Duration | Exit criteria |
|---|---|---|---|
| Alpha | 3 SRs, 1 beat, internal | 1 wk | No lost writes; check-in works in the field |
| Beta | 20 SRs, 1 zone, 1 ASM | 3 wk | HLD §6.6 pilot metrics met |
| Company | All SRs, one company | 6 wk | Coverage > 85% sustained 4 cycles |
| Multi-company | Remaining sites | per company | Same, per site |

Guardrails: never roll out during a month-end push · two beat cycles of parallel running
before cutover · a named super-user per zone trained first · weekly exception review for
the first month, treated as **process** problems not rep discipline · visible rollback if
coverage drops two consecutive cycles.

## 6.7 Definition of done (per phase)

- [ ] Doctypes with indexes and permissions; migration idempotent
- [ ] Whitelisted methods type-annotated and rate-limited
- [ ] Unit + integration suites pass
- [ ] Load targets met on staging with production-shaped data
- [ ] `Field Track` and `Beat Visit` excluded from the excom `doc_events["*"]` wildcard
- [ ] Consent gate enforced on every outbound path
- [ ] Conversion handoff verified atomic and idempotent
- [ ] Retention rules configured; dry-run purge verified
- [ ] Runbook: sync failure, queue backlog, HRMS outage, WhatsApp throttle, conversion failure
- [ ] Rollback verified on staging (`uninstall-app` leaves other apps intact)

---

## Appendix A — Open items blocking finalisation

| # | Item | Blocks | Owner |
|---|---|---|---|
| D1 | Ordering model: primary / secondary / van | `Secondary Order` vs `Sales Order` | Business |
| D8 | WABA count and messaging tier per company | Campaign sizing | Business |
| A2 | One outlet ↔ one distributor? | `Outlet Distributor` bridge | Business |
| A5 | Helpdesk for tickets + ERPNext for AMC | §1.7 service flow | Ops |
| — | Helpdesk Form Script extension point | Excom-in-Helpdesk embed | Engineering |

## Appendix B — Reference implementations in this bench

| Need | Look at |
|---|---|
| Cross-site FrappeClient | `crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py:333` |
| Nested-set subtree permissions | `crm/permissions/org_hierarchy.py` |
| Geofenced check-in + radius | `indian_hrms_compliance/hr/doctype/shift_location` |
| GPS check-in fields | `indian_hrms_compliance/hr/doctype/employee_checkin` |
| DPDP consent + retention model | `indian_hrms_compliance` — `data_consent`, `data_retention_rule` |
| Offline queue in a Frappe PWA | `posawesome/frontend/src/offline/` |
| WhatsApp media send | `excom/excom/services/whatsapp_service.py:74` |
| Provider abstraction | `excom/excom/channels/voice/providers/base.py` |
| Delivery tracking + watchdog | `excom/excom/services/delivery_watchdog.py` |
| CRM Form Script insertion from Python | `crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py:180` |
| Web form → lead binding | `crm/api/form.py` |
| Facebook lead sync | `crm/lead_syncing/` |

---

*End of LLD-002.*
