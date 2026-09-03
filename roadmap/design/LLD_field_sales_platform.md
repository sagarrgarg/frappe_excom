# Low Level Design — `fieldforce` App + Excom Integration

**Document:** LLD-001
**Version:** 1.0
**Date:** 2026-08-30
**Companion to:** HLD-001
**Status:** Draft for review

> **Baseline assumption (HLD A1/D1):** orders are **secondary sales** — the SR books an
> order at a retail Outlet which the Distributor fulfils. If the model turns out to be
> primary-only, `Outlet` remains as the visit target, `Secondary Order` is replaced by
> ERPNext `Sales Order`, and nothing else in this document changes. Divergences are
> marked **[D1-variant]**.

---

## Table of Contents

1. [Data Model](#part-1--data-model)
2. [API Design](#part-2--api-design)
3. [Processing, Jobs and Integration](#part-3--processing-jobs-and-integration)
4. [Client Design — Field App](#part-4--client-design--field-app)
5. [Security, Migration, Testing and Rollout](#part-5--security-migration-testing-and-rollout)

---

# Part 1 — Data Model

## 1.0 App layout

```
apps/fieldforce/
├── fieldforce/
│   ├── hooks.py
│   ├── install.py
│   ├── setup.py                      # seed roles, defaults
│   ├── permissions/
│   │   ├── __init__.py
│   │   └── hierarchy.py              # Sales Person subtree conditions
│   ├── fieldforce/
│   │   └── doctype/
│   │       ├── outlet/
│   │       ├── outlet_contact/                 (child)
│   │       ├── beat/
│   │       ├── beat_outlet/                    (child)
│   │       ├── journey_plan/
│   │       ├── journey_plan_day/               (child)
│   │       ├── beat_visit/
│   │       ├── visit_photo/                    (child)
│   │       ├── field_track/
│   │       ├── field_employee_link/
│   │       ├── field_sync_log/
│   │       ├── secondary_order/                [D1-variant]
│   │       ├── secondary_order_item/           (child)
│   │       ├── outlet_consent/
│   │       ├── no_order_reason/
│   │       └── field_settings/                 (single)
│   ├── api/
│   │   ├── beat.py  visit.py  order.py  track.py  outlet.py  dashboard.py
│   ├── services/
│   │   ├── cycle_generator.py  geofence.py  pricing.py
│   │   ├── hrms_bridge.py  track_store.py  kpi.py
│   ├── tasks/
│   │   ├── flush_tracks.py  close_cycle.py  sync_employees.py  snapshot.py
│   └── patches/
└── frontend/                          # field app PWA
```

## 1.1 Entity relationship overview

```
 Territory ──┐                    ┌── Excom Omni Identity
 (erpnext)   │                    │   (excom)
             ▼                    ▼
 Customer ──< Outlet >──────── omni_identity
 (distributor)  │  │
                │  └──< Outlet Contact  (child)
                │  └──< Outlet Consent
                │
                ├──< Beat Outlet >── Beat ──> Sales Person (erpnext, nested set)
                │                      │
                │                Journey Plan ──< Journey Plan Day
                │                      │
                └──< Beat Visit ───────┘
                        │  │
                        │  └──< Visit Photo (child)
                        │
                        └──> Secondary Order ──< Secondary Order Item ──> Item
                                                                          (erpnext)

 Field Employee Link ──> User ──> Sales Person        Field Track (1 per emp per day)
        │
        └─ employee_id ──▶ HRMS site Employee
```

## 1.2 `Outlet`

The retail shop. Master data, moderate volume (target 200k), heavily read.

| Field | Type | Options / Notes | Req | Idx |
|---|---|---|---|---|
| `naming_series` | Select | `OUT-.YYYY.-.#####` | ✓ | |
| `outlet_name` | Data | Shop name as painted on the board | ✓ | ✓ |
| `outlet_code` | Data | External/legacy code, unique if set | | ✓ |
| `status` | Select | `Active` / `Temporarily Closed` / `Permanently Closed` / `Prospect` | ✓ | ✓ |
| `company` | Link Company | | ✓ | ✓ |
| **Commercial** | | | | |
| `distributor` | Link Customer | The party that supplies this outlet | ✓ | ✓ |
| `territory` | Link Territory | Drives visibility and reporting | ✓ | ✓ |
| `outlet_class` | Link Outlet Class | A/B/C/D grading; drives visit frequency | | ✓ |
| `channel` | Select | `Grocery` / `Chemist` / `Cosmetics` / `Modern Trade` / `HoReCa` / `Other` | | |
| `credit_terms_days` | Int | Informational only; distributor owns credit | | |
| **Contact** | | | | |
| `owner_name` | Data | | | |
| `primary_phone` | Data (Phone) | E.164 normalised on validate | ✓ | ✓ |
| `alternate_phone` | Data (Phone) | | | |
| `whatsapp_number` | Data (Phone) | Defaults to `primary_phone` | | ✓ |
| `email` | Data (Email) | | | |
| `contacts` | Table | → `Outlet Contact` | | |
| **Address / geo** | | | | |
| `address_line_1` | Data | | ✓ | |
| `address_line_2` | Data | | | |
| `landmark` | Data | Critical in Indian addressing | | |
| `city` | Data | | ✓ | ✓ |
| `state` | Link | ERPNext state list | ✓ | ✓ |
| `pincode` | Data | 6 digits, validated | ✓ | ✓ |
| `latitude` | Float (precision 8) | | | ✓ |
| `longitude` | Float (precision 8) | | | ✓ |
| `geo_source` | Select | `Not Captured` / `Field Capture` / `Geocoded` / `Manual` | ✓ | |
| `geo_captured_on` | Datetime | | | |
| `geofence_radius_m` | Int | Default from `Field Settings`, per-outlet override | | |
| **Linkage** | | | | |
| `omni_identity` | Link Omni Identity | Created/linked on save | | ✓ |
| **Compliance** | | | | |
| `gstin` | Data | Validated if present | | |
| `fssai_license` | Data | Relevant for food channels | | |
| **Derived (read-only, updated by jobs)** | | | | |
| `last_visit_date` | Date | | | ✓ |
| `last_order_date` | Date | | | |
| `last_order_value` | Currency | | | |
| `visits_last_90d` | Int | | | |
| `orders_last_90d` | Int | | | |

**Naming:** `OUT-.YYYY.-.#####`
**Indexes (composite):** `(territory, status)`, `(distributor, status)`,
`(latitude, longitude)` for bounding-box queries, `(city, pincode)`

**Validation**

```python
def validate(self):
    self.normalize_phones()            # E.164, default country IN
    self.validate_pincode()            # ^[1-9][0-9]{5}$
    self.validate_gstin()              # checksum if present
    self.validate_geo()                # lat -90..90, lng -180..180, both or neither
    self.set_geofence_default()
    self.link_omni_identity()          # excom API, idempotent

def on_update(self):
    if self.has_value_changed("distributor"):
        self.log_distributor_change()  # audit; affects order routing
```

**Omni Identity linkage** — call excom rather than writing its tables:

```python
from excom.excom.services.identity_sync import find_or_create_identity
identity = find_or_create_identity(
    display_name=self.outlet_name,
    phone=self.whatsapp_number or self.primary_phone,
    email=self.email,
    link={"linked_doctype": "Outlet", "linked_name": self.name},
)
self.omni_identity = identity
```

### `Outlet Contact` (child)

| Field | Type | Notes |
|---|---|---|
| `contact_name` | Data | |
| `designation` | Select | `Owner` / `Manager` / `Purchase` / `Accounts` |
| `phone` | Data (Phone) | |
| `is_primary` | Check | Exactly one enforced on parent validate |
| `preferred_language` | Link Language | Drives template selection |

## 1.3 `Beat`

| Field | Type | Options / Notes | Req |
|---|---|---|---|
| `naming_series` | Select | `BEAT-.####` | ✓ |
| `beat_name` | Data | e.g. "Karol Bagh — Tuesday" | ✓ |
| `company` | Link Company | | ✓ |
| `territory` | Link Territory | | ✓ |
| `sales_person` | Link Sales Person | Owner; drives visibility | ✓ |
| `is_active` | Check | Default 1 | |
| **Cycle** | | | |
| `frequency` | Select | `Weekly` / `Fortnightly` / `Monthly` | ✓ |
| `day_of_week` | Select | Mon…Sun | ✓ |
| `week_of_cycle` | Int | 1–4; used when frequency ≠ Weekly | |
| `start_time` | Time | Planned market open | |
| **Content** | | | |
| `outlets` | Table | → `Beat Outlet` | ✓ |
| **Derived** | | | |
| `outlet_count` | Int | Computed on validate | |
| `avg_coverage_pct` | Percent | Rolling 4 cycles, set by job | |
| `avg_productivity_pct` | Percent | Rolling 4 cycles, set by job | |

**Versioning:** `track_changes = 1`. Beat composition changes are commercially
significant (they move incentive targets) and must be auditable.

**Validation**

```python
def validate(self):
    self.validate_no_duplicate_outlets()
    self.validate_outlets_share_territory()   # warn, not block
    self.resequence()                          # 10,20,30… leaves insertion room
    self.outlet_count = len(self.outlets)
```

### `Beat Outlet` (child)

| Field | Type | Notes |
|---|---|---|
| `outlet` | Link Outlet | Required |
| `outlet_name` | Data | Fetched, read-only |
| `sequence` | Int | Authored order; not computed |
| `visit_frequency` | Select | `Every Cycle` / `Alternate` / `Monthly` — high-value outlets more often |
| `preferred_slot` | Select | `Morning` / `Afternoon` / `Evening` |
| `is_active` | Check | Default 1 |
| `notes` | Small Text | e.g. "closed 1–4 pm" |

## 1.4 `Journey Plan`

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Data | `JP-.YYYY.-.####` |
| `sales_person` | Link Sales Person | Required |
| `company` | Link Company | |
| `from_date` / `to_date` | Date | Typically a month |
| `status` | Select | `Draft` / `Approved` / `Active` / `Closed` |
| `approved_by` | Link User | ASM |
| `schedule` | Table | → `Journey Plan Day` |
| `planned_visits` | Int | Derived |

### `Journey Plan Day` (child)

| Field | Type | Notes |
|---|---|---|
| `visit_date` | Date | |
| `beat` | Link Beat | Blank = leave/holiday/market-off |
| `day_type` | Select | `Beat` / `Weekly Off` / `Holiday` / `Meeting` / `Leave` |
| `planned_outlets` | Int | Snapshot at generation — beats change later |

**Generation** (`services/cycle_generator.py`):

```
for each date in [from_date, to_date]:
    if date is a holiday (ERPNext Holiday List of the sales person)  -> Holiday
    elif date is the sales person's weekly off                       -> Weekly Off
    else:
        beats = active beats where sales_person = X and day_of_week = weekday(date)
        filter by week_of_cycle when frequency != Weekly
        if exactly one -> Beat
        if more than one -> flag for manual resolution (never auto-pick)
```

The generator is **idempotent**: rerunning for the same range updates unstarted days and
never touches days that already have visits.

## 1.5 `Beat Visit`

The core transaction. Highest write volume among the business doctypes
(~2.5M rows/year at target scale).

| Field | Type | Options / Notes | Req | Idx |
|---|---|---|---|---|
| `naming_series` | Data | `VIS-.YYYY.-.######` | ✓ | |
| `visit_date` | Date | | ✓ | ✓ |
| `sales_person` | Link Sales Person | | ✓ | ✓ |
| `employee_id` | Data | HRMS Employee id, denormalised | | |
| `user` | Link User | Who actually recorded it | ✓ | |
| `company` | Link Company | | ✓ | ✓ |
| `beat` | Link Beat | Null for off-beat visits | | ✓ |
| `journey_plan` | Link Journey Plan | | | |
| `outlet` | Link Outlet | | ✓ | ✓ |
| `planned_sequence` | Int | From `Beat Outlet.sequence` | | |
| `actual_sequence` | Int | Order actually visited that day | | |
| **Check-in** | | | | |
| `checkin_time` | Datetime | | | ✓ |
| `checkin_latitude` | Float(8) | | | |
| `checkin_longitude` | Float(8) | | | |
| `checkin_accuracy_m` | Float | Device-reported accuracy | | |
| `distance_from_outlet_m` | Float | Haversine, computed server-side | | ✓ |
| `geo_exception` | Check | `distance > geofence_radius_m` | | ✓ |
| `geo_exception_reason` | Small Text | SR explanation, required when flagged | | |
| **Check-out** | | | | |
| `checkout_time` | Datetime | | | |
| `checkout_latitude` / `checkout_longitude` | Float(8) | | | |
| `duration_minutes` | Int | Computed | | |
| **Outcome** | | | | |
| `status` | Select | `Planned` / `Checked In` / `Completed` / `Not Visited` | ✓ | ✓ |
| `outcome` | Select | `Order` / `No Order` / `Shop Closed` / `Owner Absent` / `Not Visited` | | ✓ |
| `no_order_reason` | Link No Order Reason | Required when outcome = `No Order` | | |
| `secondary_order` | Link Secondary Order | **[D1-variant]** `Sales Order` | | ✓ |
| `order_value` | Currency | Denormalised for reporting | | |
| `order_lines` | Int | Distinct SKUs — feeds lines-per-call | | |
| **Market intelligence** | | | | |
| `competitor_notes` | Small Text | | | |
| `stock_check_done` | Check | | | |
| `photos` | Table | → `Visit Photo` | | |
| **Integrity** | | | | |
| `is_offline_capture` | Check | Set when replayed from the client queue | | |
| `client_generated_id` | Data | UUID from device; **unique** — the dedupe key | | ✓ U |
| `device_time` | Datetime | Device clock at capture; skew analysis | | |
| `synced_at` | Datetime | Server receipt time | | |

**Naming:** `VIS-.YYYY.-.######`
**Unique constraint:** `client_generated_id`
**Composite indexes:** `(sales_person, visit_date)`, `(outlet, visit_date)`,
`(company, visit_date, status)`, `(beat, visit_date)`

**Excluded from the excom `doc_events["*"]` wildcard.**

**State machine**

```
Planned ──checkin──▶ Checked In ──checkout──▶ Completed
   │                      │
   │                      └──(no checkout by cycle close)──▶ Completed (auto, flagged)
   └──(cycle close, never opened)──▶ Not Visited
```

**Server-side check-in logic**

```python
def perform_checkin(visit, lat, lng, accuracy, client_id, device_time):
    outlet = frappe.get_cached_doc("Outlet", visit.outlet)
    if outlet.latitude and outlet.longitude:
        d = haversine_m(lat, lng, outlet.latitude, outlet.longitude)
        visit.distance_from_outlet_m = d
        radius = outlet.geofence_radius_m or settings.default_geofence_radius_m
        visit.geo_exception = 1 if d > radius else 0
    else:
        # First visit to an ungeocoded outlet: capture, do not flag
        outlet.db_set({"latitude": lat, "longitude": lng,
                       "geo_source": "Field Capture",
                       "geo_captured_on": now_datetime()})
        visit.distance_from_outlet_m = 0
    visit.status = "Checked In"
    visit.checkin_time = now_datetime()
    ...
```

**Design note — progressive geocoding.** Most outlet masters arrive without coordinates
(HLD R2). Rather than blocking, the first check-in *becomes* the outlet's location. After
one cycle, 80%+ of outlets are geocoded from real visits, which is more accurate than
address geocoding.

### `Visit Photo` (child)

| Field | Type | Notes |
|---|---|---|
| `photo` | Attach Image | Compressed client-side to ≤ 800 KB |
| `photo_type` | Select | `Shelf` / `Storefront` / `Competitor` / `Damage` / `Other` |
| `caption` | Data | |
| `captured_at` | Datetime | |

## 1.6 `Field Track`

**One document per employee per day.** This is the load-bearing design decision (HLD §4.2).

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Data | `TRK-.YYYY.-.######` |
| `employee_id` | Data | HRMS id |
| `sales_person` | Link Sales Person | |
| `user` | Link User | |
| `company` | Link Company | |
| `track_date` | Date | |
| `route_points` | Long Text | **Encoded polyline** (Google algorithm, precision 5) |
| `point_count` | Int | |
| `first_ping` / `last_ping` | Datetime | |
| `distance_km` | Float | Cumulative haversine over decoded points |
| `idle_minutes` | Int | Time within a 50 m radius for > 10 min |
| `stop_count` | Int | Detected stops (clustering) |
| `max_speed_kmph` | Float | Outlier detection |
| `battery_start` / `battery_end` | Int | Diagnoses tracking drop-offs |
| `flush_count` | Int | How many flushes composed this row |

**Unique constraint:** `(employee_id, track_date)`
**Index:** `(sales_person, track_date)`
**Retention:** 90 days, purged by `Data Retention Rule`.
**Excluded from the `doc_events["*"]` wildcard.**

Hot store (not a doctype):

```
Redis key : track:{company}:{employee_id}:{YYYY-MM-DD}
Type      : sorted set, score = epoch seconds, member = "lat,lng,acc,batt"
TTL       : 36 hours
Read by   : live map endpoint only
```

## 1.7 `Secondary Order` **[D1-variant]**

If D1 resolves to primary sales, delete this doctype and point
`Beat Visit.secondary_order` at ERPNext `Sales Order`. Nothing else changes.

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Data | `SO2-.YYYY.-.######` |
| `outlet` | Link Outlet | ✓ |
| `distributor` | Link Customer | Fetched from outlet; the fulfiller |
| `beat_visit` | Link Beat Visit | |
| `sales_person` | Link Sales Person | |
| `company` | Link Company | |
| `order_date` | Date | |
| `expected_delivery_date` | Date | |
| `price_list` | Link Price List | Resolved from distributor/outlet class |
| `currency` | Link Currency | |
| `items` | Table | → `Secondary Order Item` |
| `total_qty` | Float | |
| `net_total` | Currency | |
| `discount_amount` | Currency | Within the SR's authority band |
| `grand_total` | Currency | |
| `status` | Select | `Draft` / `Submitted` / `Acknowledged` / `Fulfilled` / `Cancelled` |
| `distributor_ack_on` | Datetime | |
| `client_generated_id` | Data | Unique; offline dedupe |
| `is_offline_capture` | Check | |

**Docstatus:** submittable. Submit locks the order and notifies the distributor.
**Indexes:** `(outlet, order_date)`, `(distributor, status)`, `(sales_person, order_date)`

### `Secondary Order Item` (child)

| Field | Type | Notes |
|---|---|---|
| `item_code` | Link Item | |
| `item_name` | Data | Fetched |
| `uom` | Link UOM | |
| `conversion_factor` | Float | |
| `qty` | Float | |
| `rate` | Currency | **Server-resolved; never trusted from the client** |
| `discount_percentage` | Percent | Capped by authority band |
| `amount` | Currency | Computed |
| `scheme_applied` | Data | Trade scheme identifier, if any |

**Price resolution** (`services/pricing.py`) — precedence, all server-side:

```
1. Item Price where (item_code, price_list, customer=distributor) and selling=1
                    and valid_from <= today <= valid_upto
2. Item Price where (item_code, price_list) and selling=1        [qty break by packing_unit]
3. Item.standard_rate
4. else -> reject the line with PRICE_NOT_FOUND
```

## 1.8 Supporting doctypes

### `Field Employee Link`

| Field | Type | Notes |
|---|---|---|
| `employee_id` | Data | HRMS Employee name; **unique** |
| `employee_name` | Data | Cached display only |
| `user` | Link User | Company-site login; unique |
| `sales_person` | Link Sales Person | |
| `hrms_site` | Data | Base URL |
| `designation` | Data | Cached |
| `reporting_to_employee_id` | Data | Cached; cross-checked against the Sales Person tree |
| `shift_type` | Data | Cached name |
| `is_active` | Check | |
| `last_synced` | Datetime | |
| `sync_error` | Small Text | |

**Never cache** salary, bank, PAN, Aadhaar, address or any personal data. Ids and display
name only — this keeps the company sites out of DPDP scope for employee PII.

### `Field Sync Log`

| Field | Type | Notes |
|---|---|---|
| `sync_type` | Select | `Employee` / `Shift` / `Attendance Mirror` |
| `started_at` / `finished_at` | Datetime | |
| `status` | Select | `Success` / `Partial` / `Failed` |
| `records_processed` / `records_failed` | Int | |
| `error_detail` | Long Text | |

### `Outlet Consent`

Mirrors `Data Consent` from `indian_hrms_compliance`, scoped to outlets.

| Field | Type | Notes |
|---|---|---|
| `outlet` | Link Outlet | |
| `omni_identity` | Link Omni Identity | |
| `purpose` | Link Data Consent Purpose | `Order Updates` / `Promotions` / `Catalogue` / `Telecalling` |
| `channel` | Select | `WhatsApp` / `SMS` / `Email` / `Voice` |
| `lawful_basis` | Select | `Consent` / `Legitimate Use` |
| `consent_status` | Select | `Granted` / `Withdrawn` / `Expired` |
| `consent_method` | Select | `In-Person` / `WhatsApp Opt-in` / `Web Form` / `Verbal (recorded)` |
| `consent_version` | Data | Notice version shown |
| `granted_on` / `withdrawn_on` / `expires_on` | Datetime | |
| `captured_by` | Link User | |
| `proof_attachment` | Attach | |

**Enforcement point:** every broadcast and every outbound dial checks
`has_consent(omni_identity, purpose, channel)` before dispatch.

### `No Order Reason`

Small master, reportable. Seeded: `No Stock Space`, `Credit Outstanding`, `Owner Absent`,
`Sufficient Stock`, `Price Objection`, `Competitor Scheme`, `Shop Closed`, `Other`.

### `Field Settings` (Single)

| Field | Type | Default |
|---|---|---|
| `default_geofence_radius_m` | Int | 100 |
| `allow_visit_outside_geofence` | Check | 1 (record + flag, do not block) |
| `require_geo_exception_reason` | Check | 1 |
| `track_interval_moving_s` | Int | 60 |
| `track_interval_stationary_s` | Int | 300 |
| `track_flush_interval_min` | Int | 15 |
| `track_retention_days` | Int | 90 |
| `max_discount_percent` | Percent | 5 |
| `allow_off_beat_visits` | Check | 1 |
| `hrms_site_url` | Data | |
| `hrms_api_key` | Data | |
| `hrms_api_secret` | Password | |
| `employee_sync_cron` | Data | `0 2 * * *` |
| `photo_max_kb` | Int | 800 |

## 1.9 Index summary

```sql
-- fieldforce
CREATE INDEX idx_outlet_terr_status  ON `tabOutlet` (territory, status);
CREATE INDEX idx_outlet_dist_status  ON `tabOutlet` (distributor, status);
CREATE INDEX idx_outlet_geo          ON `tabOutlet` (latitude, longitude);
CREATE UNIQUE INDEX uq_visit_client  ON `tabBeat Visit` (client_generated_id);
CREATE INDEX idx_visit_sp_date       ON `tabBeat Visit` (sales_person, visit_date);
CREATE INDEX idx_visit_outlet_date   ON `tabBeat Visit` (outlet, visit_date);
CREATE INDEX idx_visit_co_date_st    ON `tabBeat Visit` (company, visit_date, status);
CREATE UNIQUE INDEX uq_track_emp_day ON `tabField Track` (employee_id, track_date);
CREATE INDEX idx_so2_outlet_date     ON `tabSecondary Order` (outlet, order_date);

-- excom gaps identified in the HLD
CREATE INDEX idx_bcastlog_bcast_st   ON `tabExcom Broadcast Log` (broadcast, status);
CREATE INDEX idx_msg_identity_dt     ON `tabExcom Message` (omni_identity, creation);
CREATE INDEX idx_msg_status_dt       ON `tabExcom Message` (delivery_status, creation);
CREATE INDEX idx_oi_norm_phone       ON `tabOmni Identity` (normalized_phone);
CREATE INDEX idx_oi_norm_email       ON `tabOmni Identity` (normalized_email);
```

---

# Part 2 — API Design

## 2.0 Conventions

- Transport: Frappe whitelisted methods over `/api/method/...`
- Auth: session cookie (field app) or `token api_key:api_secret` (server-to-server)
- All endpoints are **type-annotated**; `require_type_annotated_api_methods = True`
  is enabled in `fieldforce/hooks.py` so Frappe coerces arguments
- Every write endpoint is idempotent on `client_generated_id`
- Errors return a stable machine code, not just a message

```python
{"exc_type": "FieldForceError",
 "_server_messages": "...",
 "data": {"code": "GEOFENCE_MISSING_COORDS", "detail": "...", "retryable": false}}
```

**Error codes**

| Code | HTTP | Retryable | Meaning |
|---|---|---|---|
| `NOT_CHECKED_IN` | 417 | no | Attendance not marked for today |
| `VISIT_ALREADY_OPEN` | 409 | no | Another visit is open for this user |
| `VISIT_NOT_FOUND` | 404 | no | |
| `DUPLICATE_CLIENT_ID` | 200 | — | Idempotent replay; returns the original |
| `GEOFENCE_REASON_REQUIRED` | 400 | no | Outside radius without an explanation |
| `PRICE_NOT_FOUND` | 400 | no | No price for item + price list |
| `DISCOUNT_EXCEEDS_AUTHORITY` | 403 | no | |
| `OUTLET_INACTIVE` | 400 | no | |
| `HRMS_UNAVAILABLE` | 503 | **yes** | Falls back to cache |
| `RATE_LIMITED` | 429 | **yes** | Back off per `Retry-After` |

## 2.1 Day bootstrap

```
GET fieldforce.api.beat.get_my_day(date: str = "") -> dict
```

One call returns everything the field app needs to render the day. Designed to avoid
N+1 round trips on a 4G connection.

```jsonc
{
  "date": "2026-08-31",
  "sales_person": "SP-0042",
  "employee_id": "HR-EMP-00123",
  "attendance": {
    "checked_in": true,
    "checkin_time": "2026-08-31 09:12:04",
    "shift": "General",
    "shift_start": "09:00:00",
    "shift_end": "18:00:00",
    "source": "hrms",            // or "cache" when HRMS is unreachable
    "cache_age_s": 0
  },
  "beat": {
    "name": "BEAT-0117",
    "beat_name": "Karol Bagh — Tuesday",
    "planned_outlets": 24
  },
  "outlets": [
    {
      "visit": "VIS-2026-000881",     // pre-created, status Planned
      "outlet": "OUT-2026-01422",
      "outlet_name": "Sharma General Store",
      "sequence": 10,
      "status": "Planned",
      "owner_name": "Rakesh Sharma",
      "primary_phone": "+919812345678",
      "address_line_1": "12/4 Ajmal Khan Road",
      "landmark": "opp. Metro Gate 3",
      "latitude": 28.6519, "longitude": 77.1903,
      "geofence_radius_m": 100,
      "last_visit_date": "2026-08-24",
      "last_order_value": 4820.00,
      "outstanding_flag": false,
      "omni_identity": "OMNI-0009912"
    }
  ],
  "settings": {
    "track_interval_moving_s": 60,
    "track_interval_stationary_s": 300,
    "track_flush_interval_min": 15,
    "photo_max_kb": 800,
    "max_discount_percent": 5,
    "require_geo_exception_reason": true
  },
  "server_time": "2026-08-31 09:15:22"
}
```

`server_time` lets the client compute clock skew and stamp `device_time` meaningfully.

## 2.2 Visit lifecycle

```
POST fieldforce.api.visit.checkin(
        visit: str, latitude: float, longitude: float,
        accuracy_m: float, client_generated_id: str,
        device_time: str, battery: int = 0) -> dict
```

```python
@frappe.whitelist(methods=["POST"])
@rate_limit(key="user", limit=240, seconds=3600)
def checkin(visit: str, latitude: float, longitude: float,
            accuracy_m: float, client_generated_id: str,
            device_time: str, battery: int = 0) -> dict:
    existing = frappe.db.get_value(
        "Beat Visit", {"client_generated_id": client_generated_id}, "name")
    if existing:                                   # idempotent replay
        return _visit_payload(existing, replayed=True)

    _assert_attendance_marked()                    # HRMS, cached 4h
    _assert_no_open_visit(frappe.session.user)
    ...
```

Response:

```jsonc
{ "visit": "VIS-2026-000881", "status": "Checked In",
  "checkin_time": "2026-08-31 09:31:10",
  "distance_from_outlet_m": 34.2, "geo_exception": false,
  "outlet_geo_captured": false, "replayed": false }
```

```
POST fieldforce.api.visit.checkout(
        visit: str, latitude: float, longitude: float,
        outcome: str, no_order_reason: str = "",
        competitor_notes: str = "", geo_exception_reason: str = "",
        client_generated_id: str = "") -> dict
```

Validation on checkout:

| Condition | Result |
|---|---|
| `outcome = "No Order"` and no `no_order_reason` | 400 `NO_ORDER_REASON_REQUIRED` |
| `geo_exception` and no reason and setting requires it | 400 `GEOFENCE_REASON_REQUIRED` |
| `outcome = "Order"` and no linked order | 400 `ORDER_MISSING` |
| Visit not in `Checked In` | 409 `INVALID_STATE` |

```
POST fieldforce.api.visit.add_photo(visit: str, photo_type: str, file_url: str,
                                    caption: str = "") -> dict
POST fieldforce.api.visit.create_adhoc(outlet: str, latitude: float,
                                       longitude: float, ...) -> dict
```

`create_adhoc` supports off-beat visits when `Field Settings.allow_off_beat_visits` is on.
It creates the `Beat Visit` with `beat = None` and marks `actual_sequence` after the last
planned visit of the day.

## 2.3 Ordering

```
GET  fieldforce.api.order.get_catalogue(outlet: str, search: str = "",
        item_group: str = "", limit: int = 50, offset: int = 0) -> dict
```

Returns items **already priced for this outlet's context** — the client never computes
price:

```jsonc
{ "price_list": "Distributor North",
  "currency": "INR",
  "items": [
    { "item_code": "SKU-1001", "item_name": "Detergent 1kg",
      "uom": "Nos", "conversion_factor": 1,
      "rate": 118.50, "mrp": 145.00,
      "last_ordered_qty": 12, "last_ordered_on": "2026-08-24",
      "scheme": "10+1", "image": "/files/sku1001.jpg", "in_stock": true }
  ],
  "has_more": true }
```

`last_ordered_qty` is the highest-leverage field in the payload — reorder-from-last is
how field orders are actually taken.

```
POST fieldforce.api.order.submit(
        outlet: str, visit: str, items: list[dict],
        client_generated_id: str, expected_delivery_date: str = "",
        remarks: str = "") -> dict
```

Server behaviour:

1. Idempotency check on `client_generated_id`
2. Resolve price list from distributor → outlet class → company default
3. For each line: resolve rate server-side; **any client-sent rate is discarded**
4. Validate discount against `max_discount_percent`
5. Create + submit `Secondary Order` **[D1-variant: `Sales Order`]**
6. Update `Beat Visit`: `outcome = Order`, `order_value`, `order_lines`
7. Enqueue distributor notification through excom

```jsonc
{ "order": "SO2-2026-004417", "grand_total": 18620.00,
  "lines": 14, "status": "Submitted", "replayed": false }
```

## 2.4 Track ingestion

```
POST fieldforce.api.track.ingest(points: list[dict]) -> dict
```

```jsonc
{ "points": [
    {"t": 1756636800, "lat": 28.6519, "lng": 77.1903, "acc": 12.4, "bat": 78},
    {"t": 1756636860, "lat": 28.6522, "lng": 77.1911, "acc": 9.8,  "bat": 78}
] }
```

- Batched by the client (default every 5 minutes, ~5 points)
- Max 200 points per request; larger payloads rejected with `PAYLOAD_TOO_LARGE`
- Writes to Redis only; **never** creates a document on the request path
- Rate limit: 60 requests/hour/user

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
        pipe.zadd(key, {f"{pt['lat']},{pt['lng']},{pt.get('acc',0)},{pt.get('bat',0)}":
                        int(pt["t"])})
    pipe.expire(key, 36 * 3600)
    pipe.execute()
    return {"accepted": len(points), "next_interval_s": _interval_for(link)}
```

`next_interval_s` lets the server throttle chatty devices without a client release.

## 2.5 Manager and dashboard

```
GET fieldforce.api.dashboard.live_map(territory: str = "", beat: str = "") -> dict
```

Reads **Redis only**. Subtree-filtered by the caller's Sales Person node.

```jsonc
{ "as_of": "2026-08-31 14:22:10",
  "agents": [
    { "employee_id": "HR-EMP-00123", "name": "Amit Kumar",
      "lat": 28.6531, "lng": 77.1888, "last_ping": "2026-08-31 14:21:40",
      "staleness_s": 30, "status": "In Visit",
      "current_outlet": "Sharma General Store",
      "visits_done": 11, "visits_planned": 24, "orders": 7 }
  ] }
```

```
GET fieldforce.api.dashboard.coverage(from_date: str, to_date: str,
        sales_person: str = "", territory: str = "") -> dict
GET fieldforce.api.dashboard.productivity(...) -> dict
GET fieldforce.api.dashboard.exceptions(date: str = "") -> dict
```

Coverage and productivity read the **nightly snapshot table on the read replica**, never
aggregate live transactions. `exceptions` reads live (small result set): geo exceptions,
zero-visit SRs, visits without checkout, orders above discount authority.

## 2.6 Excom integration (in-process)

`fieldforce` calls excom's whitelisted API; it never writes excom tables.

| Purpose | Call |
|---|---|
| Send catalogue | `excom.excom.api.catalogue.send_to_identity(catalogue, omni_identity, account)` |
| Start a call | `excom.excom.api.voice.initiate_call(to_number, thread_id)` |
| Open thread | `excom.excom.api.chat.get_threads(search=...)` filtered by `omni_identity` |
| Order confirmation | `excom.excom.api.chat.send_message(thread_id, message, message_type)` |
| Consent check | `fieldforce.services.consent.has_consent(omni_identity, purpose, channel)` |

**Contract rule:** if excom is not installed, `fieldforce` degrades — catalogue and call
buttons hide; nothing errors. Checked via `"excom" in frappe.get_installed_apps()`.

## 2.7 HRMS bridge

```python
# services/hrms_bridge.py
class HRMSBridge:
    def __init__(self):
        s = frappe.get_single("Field Settings")
        self.client = FrappeClient(s.hrms_site_url, api_key=s.hrms_api_key,
                                   api_secret=s.get_password("hrms_api_secret"))

    def get_employees(self, company: str, modified_after: str) -> list[dict]:
        return self.client.get_list("Employee",
            filters={"company": company, "status": "Active",
                     "modified": [">", modified_after]},
            fields=["name", "employee_name", "designation", "user_id",
                    "reports_to", "default_shift", "company"],
            limit_page_length=0)

    def get_attendance_state(self, employee_id: str, date: str) -> dict:
        # cached 4h; on failure the caller falls back to the cache
        ...

    def mirror_checkin(self, employee_id: str, ts: str,
                       lat: float, lng: float, device_id: str) -> None:
        self.client.insert({"doctype": "Employee Checkin",
                            "employee": employee_id, "log_type": "IN",
                            "time": ts, "latitude": lat, "longitude": lng,
                            "device_id": device_id})
```

Rules:

- **Read-mostly.** The only write is the check-in mirror, and it is queued.
- **Never blocks.** Every read has a cached fallback with a stated TTL.
- **Never caches personal data** — ids, names, designation, shift only.
- Timeout 10 s, 2 retries with backoff, then cache fallback and a `Field Sync Log` row.

---

# Part 3 — Processing, Jobs and Integration

## 3.1 Scheduler map

`fieldforce/hooks.py`:

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
    },
    "hourly_long": ["fieldforce.tasks.geocode.geocode_pending_outlets"],
}
```

## 3.2 Track flush

```
every 15 min:
  for each key matching track:*:*:{today}:
      points = ZRANGEBYSCORE(key, last_flushed_score, +inf)
      if not points: continue
      doc = get_or_create Field Track (employee_id, track_date)
      merged = decode(doc.route_points) + points
      merged = dedupe_and_sort(merged)
      doc.route_points = encode_polyline(merged)
      doc.point_count  = len(merged)
      doc.distance_km  = cumulative_haversine(merged)
      doc.idle_minutes = compute_idle(merged, radius_m=50, min_minutes=10)
      doc.stop_count   = detect_stops(merged)
      doc.last_ping    = max(t)
      doc.save(ignore_permissions=True)     # flags.ignore_doc_events = True
      set last_flushed_score
```

Notes:

- Runs on the `long` queue; a slow flush must not delay webhooks.
- `flags.ignore_doc_events = True` — this is the single largest writer in the system.
- Encoded polyline at precision 5 ≈ 5 bytes/point → 480 points/day ≈ 2.4 KB per SR-day.
- The job is restartable: `last_flushed_score` is stored per key in Redis.

## 3.3 Cycle close

```
03:00 daily, for yesterday:
  for each Journey Plan Day with day_type = "Beat":
      for each Beat Outlet due in this cycle:
          if no Beat Visit exists     -> create with status Not Visited, outcome Not Visited
          if status = "Checked In"    -> force Completed, flag missing_checkout
  recompute per-beat rolling KPIs (last 4 cycles)
  lock the day: visits become read-only except by supervisor override
```

## 3.4 Daily snapshot

Precompute what dashboards read. Live aggregation over 2.5M visits/year will not meet the
2-second budget.

`Field Daily Snapshot` — one row per (sales_person, date):

```
planned_outlets, visited_outlets, productive_outlets,
coverage_pct, productivity_pct,
order_count, order_value, order_lines,
lines_per_call, value_per_call,
first_checkin, last_checkout, time_in_market_min,
selling_time_min, travel_time_min,
geo_exceptions, distance_km
```

Manager rollups aggregate this table over a Sales Person subtree — a range scan on
`(sales_person, date)` across at most a few thousand rows.

## 3.5 Employee sync

```
02:00 daily:
  since = last successful sync - 1 day        # overlap absorbs clock skew
  for each company on this site:
      employees = HRMSBridge.get_employees(company, since)
      for e in employees:
          link = get_or_create Field Employee Link(employee_id=e.name)
          link.update(employee_name, designation, shift, reporting_to, is_active)
          if e.user_id and not link.user:
              link.user = e.user_id
          if link.user and not link.sales_person:
              link.sales_person = find_or_create_sales_person(link)
          link.save()
      reconcile_hierarchy()        # warn where reports_to and the Sales Person tree differ
  write Field Sync Log
```

`reconcile_hierarchy` **reports** mismatches rather than auto-correcting: the HR reporting
line and the sales reporting line are legitimately different in some organisations, and
silently rewriting a commercial hierarchy from HR data would be wrong.

## 3.6 Check-in mirror

```
on Beat Visit checkin (first of the day only):
    enqueue short: mirror_checkin_to_hrms(visit)
        try   -> HRMSBridge.mirror_checkin(...); mark mirrored
        except-> mark mirror_failed, retried every 30 min for 24 h,
                 then raise a Field Sync Log entry
```

The local visit is authoritative for field reporting; HRMS is authoritative for payroll.
Divergence is surfaced in a daily exception report rather than resolved automatically.

## 3.7 Queue configuration

`Procfile` / supervisor:

```
worker_short:     bench worker --queue short      (4 processes)
worker_default:   bench worker --queue default    (4)
worker_long:      bench worker --queue long       (2)
worker_broadcast: bench worker --queue broadcast  (6)
worker_voice:     bench worker --queue voice      (2)
```

| Job | Queue | Timeout |
|---|---|---|
| Track ingest (inline) | — | request path, no job |
| Track flush | long | 600 s |
| Check-in mirror | short | 30 s |
| Order → distributor notify | short | 60 s |
| Employee sync | long | 1800 s |
| Snapshot build | long | 1800 s |
| Cycle close | long | 1800 s |
| Broadcast batch | broadcast | 900 s |
| Catalogue render | default | 300 s |

## 3.8 Broadcast fan-out (excom change)

```python
def execute_broadcast(broadcast_name: str):
    recipients = _recipient_ids(broadcast_name)          # cursor, not list
    for i, chunk in enumerate(_chunks(recipients, 500)):
        batch = frappe.get_doc({
            "doctype": "Excom Broadcast Batch",
            "broadcast": broadcast_name, "batch_index": i,
            "recipient_count": len(chunk), "status": "Pending",
        }).insert(ignore_permissions=True)
        frappe.enqueue("excom.excom.services.broadcast_service.run_batch",
                       queue="broadcast", batch=batch.name, timeout=900)

def run_batch(batch: str):
    doc = frappe.get_doc("Excom Broadcast Batch", batch)
    if doc.status == "Done":
        return                                            # idempotent
    doc.db_set("status", "Running")
    bucket = TokenBucket(account=doc.account, rate_per_sec=settings.wa_rate)
    for oi in _batch_recipients(doc):
        bucket.acquire()
        try:
            _send(doc.broadcast, oi)
        except RateLimited as e:
            _requeue(doc, oi, delay=e.retry_after)
        except Exception:
            _log_failure(doc, oi)
    doc.db_set("status", "Done")
```

`Excom Broadcast Batch`: `broadcast`, `batch_index`, `recipient_count`, `sent`, `failed`,
`status` (`Pending`/`Running`/`Done`/`Failed`), `attempts`, `last_error`.

---

# Part 4 — Client Design — Field App

## 4.1 Stack and shell

React 18 + TypeScript + Tailwind + `frappe-react-sdk` — deliberately the same stack as
excom so components and design tokens are shared and there is one build pipeline.

Served from the company site at `/field`, registered via `website_route_rules`:

```python
website_route_rules = [{"from_route": "/field/<path:app_path>", "to_route": "field"}]
add_to_apps_screen = [{"name": "fieldforce", "title": "Field Sales",
                       "route": "/field", "logo": "/assets/fieldforce/logo.svg"}]
```

PWA: `vite-plugin-pwa`, `injectManifest`, scope `/field/`, precached shell so a cold start
on 4G stays inside the 3 s budget.

## 4.2 Screen map

```
/field                       Day view — the home screen
/field/outlet/:visitId       Visit detail (check-in, order, photos, checkout)
/field/order/:visitId        Order capture (full screen, item search)
/field/outlet/:id/profile    Outlet profile, history, conversations
/field/catalogue             Catalogue builder and send
/field/summary               My day: coverage, orders, distance
/field/sync                  Pending queue and conflicts (visible, not hidden)
```

**Day view** — the only screen an SR sees for most of the day:

```
┌────────────────────────────────────┐
│ Karol Bagh — Tuesday        ⋮      │
│ 11 / 24 done   ·   7 orders        │
│ ▓▓▓▓▓▓▓░░░░░░░░  46%               │
├────────────────────────────────────┤
│ ⏳ 3 pending sync            [↻]   │  ← only when queue is non-empty
├────────────────────────────────────┤
│ ✓ 10  Sharma General Store         │
│       ₹4,820 · 14 lines            │
├────────────────────────────────────┤
│ ▶ 20  Gupta Provision       120 m  │  ← next; distance live
│       Last: 24 Aug · ₹3,110        │
│       [Check In]    [Call]  [Cat.] │
├────────────────────────────────────┤
│   30  New Bharat Store             │
│   40  Anand Kirana                 │
└────────────────────────────────────┘
```

Design rules, derived from how the app is actually used (one hand, sunlight, hurry):

- Primary action is always a single tap from the day view
- Distance to the next outlet is live — it is the SR's navigation cue
- Last visit and last order value are shown inline; they drive the sales conversation
- Sync state is **visible**, never hidden. A hidden queue destroys trust the first time
  an order goes missing
- Minimum touch target 44 px; high-contrast palette for outdoor readability

## 4.3 State and storage

```
├── IndexedDB
│   ├── day_cache        today's get_my_day payload (TTL until midnight)
│   ├── outbox           queued writes (see §4.4)
│   ├── track_buffer     GPS points awaiting upload
│   └── item_cache       catalogue for the day's price list (TTL 24 h)
├── localStorage         session prefs, last sync timestamp
└── memory               React state
```

`item_cache` is the difference between a 3-second and a 30-second order. Prefetch the
distributor's price list once at day start (typically 500–2,000 SKUs, ~300 KB gzipped).

## 4.4 Write-tolerance queue

**Only three write types are queued.** Everything else requires connectivity.

| Action | Queued | Rationale |
|---|---|---|
| Check-in | ✓ | Loss makes a real visit look like a miss |
| Order submit | ✓ | Loss is revenue and rep trust |
| Check-out | ✓ | Loss leaves the visit permanently open |
| Photo upload | ✓ (low priority) | Large; retried opportunistically |
| GPS ping | buffered, not queued | A gap is acceptable |
| Catalogue send | ✗ | Requires the server anyway |
| Dashboard | ✗ | Read-only, non-critical |

**Outbox record**

```ts
interface OutboxItem {
  id: string;                  // uuid v4 == client_generated_id
  type: 'checkin' | 'order' | 'checkout' | 'photo';
  endpoint: string;
  payload: Record<string, unknown>;
  created_at: number;          // device epoch ms
  attempts: number;
  last_error?: string;
  status: 'pending' | 'sending' | 'failed' | 'done';
  depends_on?: string;         // order depends on its check-in
}
```

**Replay algorithm**

```
on (online | app foreground | every 60 s while online):
    items = outbox.where(status in [pending, failed])
                  .orderBy(created_at asc)          # strict FIFO
    for item in items:
        if item.depends_on and not done(item.depends_on): continue
        if item.attempts >= 8: mark 'failed', surface on /field/sync; continue
        mark 'sending'
        try:
            res = POST item.endpoint, item.payload    # payload carries client id
            mark 'done'; reconcile local state from res
        except NetworkError:
            mark 'pending'; break                      # stop, preserve order
        except ServerError(retryable):
            item.attempts++; backoff = min(2**attempts, 300) s; mark 'pending'
        except ServerError(non_retryable):
            mark 'failed'; surface with the server's message
```

**Idempotency contract.** Every queued write carries `client_generated_id`. The server
looks it up first and returns the original result with `"replayed": true` rather than
creating a duplicate. This is why `client_generated_id` is a **unique index** — the
database, not application logic, is the guarantee.

**Ordering.** Strict FIFO with `depends_on` prevents an order arriving before its
check-in. On a network error the loop breaks rather than skipping ahead.

**Conflict cases**

| Case | Resolution |
|---|---|
| Same `client_generated_id` replayed | Server returns the original; client marks done |
| Check-in replayed after cycle close | Accepted, flagged `late_sync`, visible in exceptions |
| Order for a visit closed on another device | Rejected `INVALID_STATE`; surfaced on `/field/sync` |
| Device clock skew > 5 min | Server stamps authoritative time; `device_time` retained for analysis |

## 4.5 GPS collection

```ts
const opts = { enableHighAccuracy: true, maximumAge: 15000, timeout: 20000 };

// adaptive: 60 s moving, 300 s stationary, off outside shift
function nextInterval(lastFix: Fix, current: Fix): number {
  const moved = haversine(lastFix, current);
  return moved < 30 ? settings.track_interval_stationary_s
                    : settings.track_interval_moving_s;
}
```

- Buffer in IndexedDB; upload every `track_flush_interval_min` or when the buffer hits 50
- Discard fixes with `accuracy > 100 m` — noise inflates distance
- Stop entirely outside shift hours (read from the day payload)
- Show the SR their own trail on `/field/summary` — transparency is the mitigation for
  HLD R3, and it is also genuinely useful to them for expense claims

## 4.6 Performance techniques

| Technique | Effect |
|---|---|
| Single `get_my_day` bootstrap | 1 request instead of ~8 |
| Item cache prefetched at day start | Order screen works instantly |
| Route-level code splitting | Day view bundle < 150 KB gzipped |
| Client-side image compression before upload | 4 MB photo → < 800 KB |
| Virtualised item list | 2,000 SKUs scroll without jank |
| Optimistic UI on all three queued writes | No spinner between tap and next action |
| `stale-while-revalidate` on outlet profile | Instant open, refresh behind |

---

# Part 5 — Security, Migration, Testing and Rollout

## 5.1 Roles and permissions

| Role | Outlet | Beat | Journey Plan | Visit | Order | Track | Dashboard |
|---|---|---|---|---|---|---|---|
| Field Sales Rep | R (subtree) | R | R | **CRU** own | **CRU** own | R own | own only |
| Area Sales Manager | RW subtree | **CRUD** | **CRUD** | RW subtree | R subtree | R subtree | subtree |
| Regional Manager | R subtree | RW subtree | R subtree | R subtree | R subtree | R subtree | subtree |
| Field Admin | CRUD | CRUD | CRUD | RW | R | R | all |
| Field Global Viewer | R all | R all | R all | R all | R all | R all | all |

Field-level (`permlevel = 1`): landed cost, margin, and distributor credit fields are
hidden from Field Sales Rep.

## 5.2 Subtree permission implementation

```python
# fieldforce/permissions/hierarchy.py
_SP_FIELD = {"Outlet": None, "Beat": "sales_person", "Journey Plan": "sales_person",
             "Beat Visit": "sales_person", "Secondary Order": "sales_person",
             "Field Track": "sales_person"}

def _subtree_sales_persons(user: str):
    """Sales Person names in the caller's subtree, using ERPNext's indexed lft/rgt."""
    sp = frappe.db.get_value("Field Employee Link", {"user": user}, "sales_person")
    if not sp:
        return None
    lft, rgt = frappe.db.get_value("Sales Person", sp, ["lft", "rgt"])
    SP = frappe.qb.DocType("Sales Person")
    return frappe.qb.from_(SP).select(SP.name).where(
        (SP.lft >= lft) & (SP.rgt <= rgt))

def get_permission_query_conditions(doctype: str, user: str | None = None) -> str:
    user = user or frappe.session.user
    roles = set(frappe.get_roles(user))
    if user == "Administrator" or roles & {"System Manager", "Field Global Viewer"}:
        return ""
    sub = _subtree_sales_persons(user)
    if sub is None:
        return "1=0"                      # no link -> see nothing, fail closed
    field = _SP_FIELD[doctype]
    if field:
        DT = frappe.qb.DocType(doctype)
        cond = DT[field].isin(sub)
    else:                                  # Outlet: via territory + distributor
        cond = _outlet_conditions(user, sub)
    return cond.get_sql(with_namespace=True, quote_char="`",
                        secondary_quote_char="'")
```

**Fail closed.** A user with no `Field Employee Link` sees nothing. The opposite default —
Frappe CRM's `enable_sales_hierarchy = 0`, where everyone sees everything until an admin
notices — is explicitly rejected.

Registered in `hooks.py` for each doctype, with a matching `has_permission` so single-doc
access uses the same rule.

## 5.3 Security checklist

| Item | Implementation |
|---|---|
| Cross-site credentials | Per-pair API key/secret in `Field Settings` (`Password` fieldtype, encrypted); quarterly rotation; never logged |
| Webhook auth | `hmac.compare_digest`, never `==` |
| Realtime scoping | `frappe.publish_realtime(..., user=...)` on every emit — never an unscoped broadcast |
| Rate limits | `ingest` 60/h, `checkin` 240/h, `order.submit` 120/h, `get_catalogue` 600/h, all per user |
| Price integrity | Rates resolved server-side; client-supplied rates discarded |
| Discount authority | Enforced server-side against `Field Settings.max_discount_percent` |
| Photo upload | Type allow-list, size cap, EXIF stripped, private files |
| PII minimisation | No employee personal data cached on company sites |
| Audit | `track_changes` on Beat, Journey Plan, Outlet; `Data Access Log` on bulk export |
| Consent gate | Checked before every broadcast send and every outbound dial |

## 5.4 Migration and data onboarding

**Step 1 — Outlet master import.** The hardest part of any SFA rollout.

```
CSV columns: outlet_name, outlet_code, distributor_code, territory,
             owner_name, phone, address_line_1, landmark, city, state,
             pincode, outlet_class, channel, [latitude], [longitude]

Pipeline:
  1. Validate distributor exists (by code) -> else reject row
  2. Normalise phone to E.164 (default IN)
  3. Dedupe within the file: phone, then (name + pincode) fuzzy
  4. Dedupe against existing: Omni Identity phone match
  5. Insert Outlet, link/create Omni Identity
  6. Where lat/lng absent -> queue for geocoding; where geocoding fails ->
     leave blank and let first check-in capture it (§1.5)
  7. Emit an import report: accepted / rejected / merged, with reasons
```

Expect 20–40% of rows to need manual attention on first import. Plan for it; do not
model it as an edge case.

**Step 2 — Beat construction.** Import beats from existing route sheets where they exist;
otherwise build from `Outlet.territory` + ASM input. Beats are authored, never inferred.

**Step 3 — Employee link.** Run `sync_employees`, then map users to Sales Person nodes.
Reconcile HR reporting lines against the sales tree and resolve differences with the
sales leadership, not automatically.

**Step 4 — Backfill.** Optionally load 90 days of historical orders so
`last_order_date` / `last_order_value` are populated on day one — the app looks credible
to reps immediately rather than empty.

**Rollback:** `fieldforce` is a separate app; `bench uninstall-app fieldforce` removes
field data without touching ERPNext or excom. This is a primary reason for the separate app.

## 5.5 Test plan

**Unit (pytest, `bench run-tests --app fieldforce`)**

| Module | Cases |
|---|---|
| `geofence` | Haversine accuracy; boundary at exactly radius; missing coords; antimeridian |
| `pricing` | Party-specific price; price-list fallback; standard rate; qty break; no price → error; discount cap |
| `cycle_generator` | Weekly/fortnightly/monthly; holiday; weekly off; duplicate beats; idempotent rerun |
| `track_store` | Encode/decode round trip; dedupe; distance; idle detection; malformed points |
| `hierarchy` | Root; leaf; no link (fail closed); Global Viewer; multi-branch subtree |
| `hrms_bridge` | Timeout → cache; partial failure; malformed response; key rotation |

**Integration**

| Scenario | Assertion |
|---|---|
| Full visit: checkin → order → checkout | Visit completed, order submitted, KPIs updated |
| Replay of all three writes | Exactly one visit, one order; `replayed: true` |
| Check-in without attendance | 417 `NOT_CHECKED_IN` |
| Check-in 500 m away | Recorded, `geo_exception = 1`, reason required |
| Check-in at an ungeocoded outlet | Outlet coordinates captured, no exception raised |
| Order with a tampered client rate | Server rate used; client value ignored |
| Cycle close with an open visit | Forced complete, `missing_checkout` flagged |
| HRMS down at day start | Day loads from cache; `source: "cache"` |

**Load**

| Test | Target |
|---|---|
| Track ingest | 500 concurrent SRs × 5-min batches, sustained 8 h, p95 < 200 ms |
| Flush job | 500 SR-days in < 5 min |
| Live map | 200 agents, p95 < 1 s |
| Order submit | 50 concurrent, 20 lines each, p95 < 1.5 s |
| Broadcast | 100k recipients, batched, no worker starvation on other queues |
| Coverage dashboard | 12-month range over a 200-SR subtree, p95 < 2 s |

**Field acceptance (pilot)** — run on real devices, in real markets:
low-end Android; 2G/3G fallback; direct sunlight readability; one-handed operation;
battery consumption over a full 8-hour beat.

## 5.6 Rollout

| Stage | Scope | Duration | Exit criteria |
|---|---|---|---|
| Alpha | 3 SRs, 1 beat, internal | 1 week | No lost writes; check-in works in the field |
| Beta | 20 SRs, 1 zone, 1 ASM | 3 weeks | HLD §5.5 pilot metrics met |
| Company rollout | All SRs, one company | 6 weeks | Coverage > 85% sustained 4 cycles |
| Multi-company | Remaining sites | per company | Same, per site |

**Rollout guardrails**

- Never roll out during a month-end sales push
- Two beat cycles of parallel running (app + existing paper/WhatsApp process) before cutover
- A named super-user per zone, trained first, who supports peers
- A weekly exception review for the first month — geo exceptions, missing checkouts,
  sync failures — treated as **process** problems, not rep discipline problems
- A visible rollback: if coverage drops for two consecutive cycles, pause and diagnose

## 5.7 Definition of done (per phase)

- [ ] Doctypes created with indexes and permissions; migration is idempotent
- [ ] Whitelisted methods type-annotated and rate-limited
- [ ] Unit tests pass; integration scenarios in §5.5 pass
- [ ] Load targets met on staging with production-shaped data
- [ ] `Field Track` and `Beat Visit` excluded from the excom `doc_events["*"]` wildcard
- [ ] Consent gate enforced on every outbound path
- [ ] Retention rules configured and verified with a dry-run purge
- [ ] Runbook written: sync failure, queue backlog, HRMS outage, WhatsApp throttle
- [ ] Rollback verified on staging (`uninstall-app` leaves ERPNext and excom intact)

---

## Appendix A — Dependency notes

| Dependency | Version | Notes |
|---|---|---|
| `frappe` | v15 / v16 | `require_type_annotated_api_methods` enabled |
| `erpnext` | v15 / v16 | `Sales Person`, `Territory`, `Item`, `Item Price` |
| `excom` | current | Catalogue, voice, identity, broadcast |
| `indian_hrms_compliance` | current | Remote only; accessed over REST |
| Redis | 6+ | Sorted sets, pipelines, TTL |
| MariaDB | 10.6+ | Read replica recommended from ~200 SRs |

## Appendix B — Reference implementations in this bench

| Need | Look at |
|---|---|
| Cross-site FrappeClient | `crm/fcrm/doctype/erpnext_crm_settings/erpnext_crm_settings.py:333` |
| Nested-set subtree permissions | `crm/permissions/org_hierarchy.py` |
| Geofenced check-in | `indian_hrms_compliance/hr/doctype/shift_location` |
| Offline queue in a Frappe PWA | `posawesome/frontend/src/offline/` |
| WhatsApp media send | `excom/excom/services/whatsapp_service.py:74` |
| Provider abstraction | `excom/excom/channels/voice/providers/base.py` |
| Delivery tracking + watchdog | `excom/excom/services/delivery_watchdog.py` |

---

*End of LLD-001.*
