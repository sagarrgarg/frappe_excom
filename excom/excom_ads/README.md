# Excom Ads — conventions

The Marketing OS lives inside excom as the **Excom Ads** module (`excom/excom_ads/`). It adds ads,
attribution, analytics, billing and ad-bill accounting on top of Excom (people, conversations,
Meta credentials, lead intake) and Webshop (storefront, orders). Plan: see the "Marketing OS for
Frappe — Product & Build Plan" doc.

Supported: Frappe/ERPNext **v15 and v16**, MariaDB, Python ≥ 3.10. Test on `frappe.localhost`
(v16, `~/frappe-bench`) and `test15.localhost` (v15, `~/frappe-bench-v15`, same app checkout).

## Layout

| Path | What |
|---|---|
| `doctype/` | Every Ads DocType. Names start with `Ads `. Never ship a DocType named `WhatsApp …`. |
| `providers/base.py` | `AdsProvider` contract, `Capability`, error classes, provider registry (`PROVIDERS`). |
| `providers/<name>.py` | One module per platform. Read-only in release 1. |
| `google_auth.py` | Google credentials (service account JWT / OAuth refresh) and the one Google HTTP function, shared by Ads and later GA4 / Search Console / Merchant. |
| `google_connect.py`, `api_google.py` | Ads Connection test, account discovery (manager tree) and enabling; admin-only endpoints. |
| `sync.py` | Jobs (Structure / Metrics / Settle / Backfill), upserts, alerts on results. |
| `permissions.py` | Scope (company / workspace / brand / account) + rights (Ads Access Profile). |
| `alerts.py` | `raise_alert()` / `resolve_alert()`; delivery by Ads Notification Policy. |
| `settings.py` | `get_settings()` — Ads Settings with defaults. Add new keys to `DEFAULTS`. |
| `naming.py` | Deterministic names from natural keys. |
| `attribution.py` | Leads ↔ ads: one Ads Attribution Touch per observed touch (lead form, click-to-WhatsApp, website, order); the Lead/Opportunity keeps its first; `relink_pending` links leads that arrived before their mirror. |
| `attribution_schema.py` | `ads_*` Custom Fields on Lead/Opportunity + resolver indexes, applied by `setup.seed()`. |
| `billing.py` | Billing reading → Ads Billing Snapshot (per account per day), headroom / days-left calculations, top-up ledger estimate (Ads Billing Record), Low Headroom alerts, `get_billing` API. |
| `budgets.py` | Ads Budget Plan (maker-checker) vs actual spend, `get_plan_vs_actual` / `get_plans` API, Overspend alert. |
| `bill_sources.py` | Platform invoice reads: Meta `/{business_id}/business_invoices`, Google `InvoiceService.ListInvoices`; normalised bills, PDF download (platform hosts only). |
| `bills.py` | Ad bill pipeline: Ads Bill Document → draft Purchase Invoice (India Compliance GST, TDS, debit notes), auto-submit rules, maker-checker `approve`, GSTR-2B status, TDS certificate reminders. |
| `bill_gst.py` | The only place that calls India Compliance (place of supply, tax template, GST breakup, document-number rule, GST Inward Supply reads). |
| `bill_suppliers.py` | Platform legal entities (one Supplier each), entity matching, propose / create suppliers on confirmation. |
| `bill_tasks.py`, `api_bills.py` | Bill scheduler entry points (enqueue only) and whitelisted bill endpoints. |
| `tasks.py` | Scheduler entry points. They only enqueue. |
| `api.py` / `api/` | Whitelisted endpoints. |
| `tests/` | `test_*.py`, run through `excom.excom.tests.run.run`; `tests/dev.py` reloads DocTypes. |

## Rules

**Data**
- Platform state is the truth. Mirrors (campaign, ad group, ad) are named `{account}-{external_id}`;
  facts are named from a hash of their natural key. Re-running any job writes the same rows.
- Platform-reported and verified (ERPNext) figures are separate fields and are never summed.
- A value the platform does not report is **not available**, never 0. Number columns cannot hold
  NULL, so pair them with a status or flag field (see `Ads Account.billing_status`, `has_spend_cap`).
- Money: store the platform currency on the row; company-currency copy uses ERPNext's dated rate.
  Meta account/budget amounts are minor units (`providers/meta.money`); insights spend is major units.
- Dates are the ad account's reporting date (its own time zone). `sync.account_today(acc)`.

**Scope and security**
- Every Ads DocType that holds data carries `company`, `workspace`, `brand` and/or `account`
  (fetched from the account; `brand` uses `fetch_if_empty` so allocated rows keep their brand).
- Register every new non-child Ads DocType in `hooks.py` under both `permission_query_conditions`
  and `has_permission` → `excom.excom_ads.permissions.*`. The permission tests check this.
- Code that reads with `frappe.get_all` or SQL on behalf of a user applies
  `permissions.scope_filters(doctype)` (or `query_conditions`) itself. Whitelisted methods call
  `permissions.require_access()` and, for state changes, `permissions.require("<right>")`.
- Finance records (billing snapshots, top-ups, bill documents, account maps, TDS logs) are listed in
  `permissions.FINANCE_DOCTYPES`: they also need `view_finance`. Finance *fields* on other
  DocTypes use `permlevel: 1` (read for System Manager, Excom Admin, Accounts User/Manager).
- Roles: `System Manager`, `Excom Admin` (full), `Excom Marketing` (read inside scope). Do not add
  roles; add a right to Ads Access Profile instead.
- Credentials stay in Password fields on their owning record (Meta: `Excom Meta Connection`; Google and
  LinkedIn: `Ads Connection`, which only System Manager and Excom Admin can open).
  Read with `get_password()` server-side only; never return them, log them or put them in errors.
- Webhooks: verify the signature before anything else, store the raw event, enqueue processing,
  idempotent on the provider event ID.
- Excom CRM gates (`frontend/scripts/crm-gates.sh`) apply here too: Lead/Opportunity/Prospect/
  Quotation doctype names only via `excom.excom.services.crm_gateway` (`gw.LEAD`…), and attribution
  only through `excom.excom.services.crm_compat.set_attribution` (v15 Lead Source/Campaign vs v16
  UTM Source/Medium/Campaign).

**Site isolation** (user rule): no code runs on a site where excom is not installed. No import-time
side effects, no monkey-patching of frappe/erpnext. Optional apps (india_compliance, webshop,
ai_interface) are checked with `"<app>" in frappe.get_installed_apps()` before use.

**Jobs**
- Long work runs in `frappe.enqueue(..., queue="long", job_id=..., deduplicate=True)`.
- Errors are `AdsProviderError` with a class: Retryable, Rate Limited (next run retries), Permission,
  Token, Invalid (need a person → alert). A job that wrote nothing and hit errors is **Failed**, never
  Success or Partial. Token errors stop metric pulls for that account until structure recovers.
- Network calls go through one module-level function per provider (e.g. `meta.http_get`) so tests
  can replace it. Tests never touch the network.

**Alerts**: `alerts.raise_alert(type, severity, title, message, ref=doc, dedupe_key=...)` and
`alerts.resolve_alert(key)` when the condition clears. Alert types are the Select options on
Ads Alert; add new ones there.

**Billing and budgets**
- No platform API exposes a prepaid balance (Meta `balance` is the bill amount due; Google billing
  resources exist only on monthly invoicing). `provider.read_billing()` returns snapshot fields plus a
  `*_avail` status per figure (Available / Not Set / Unsupported / Permission Missing / No Data /
  Stale / Error). A value leaves the API only when its status is Available or Stale.
- The Structure sync writes one Ads Billing Snapshot per account per account-local day
  (`naming.billing_snapshot_name`), updated in place; a failed read keeps the last good value as Stale.
  A billing failure never fails the structure job.
- `reported_*` = exactly what the platform returned; `calc_*` = ours; the ledger estimate is always
  labelled "Estimated from our ledger". Never add one to another.
- Controllers that set scope fields from a link in `validate()` call `permissions.assert_in_scope(self)`:
  Frappe checks create/write before fetch_from runs (and may pre-fill company from a user default).
- Budget plans count only when submitted. Submit = approve: needs `manage_budgets` and a user who
  neither created nor last edited the draft (System Manager override is recorded). Change = Cancel → Amend.

**Ad bills → Purchase Invoices** (`bills.py`; accounting + GST, so conservative)
- Sources: Meta and Google *monthly invoicing* through their invoice APIs (Ads Bill Source), or a
  manual Ads Bill Document (PDF + typed header) for card / prepaid receipts. A Purchase Invoice is
  never built from spend data. One Ads Bill Document per platform invoice id
  (`bills.bill_document_name`); refetching never duplicates. Figures re-issued by the platform after a
  draft exists wait in `platform_update` until Rebuild.
- One Supplier per legal entity (Facebook India vs Meta Ireland, Google India vs Google Asia Pacific,
  LinkedIn India vs Singapore). `api_bills.propose_suppliers` / `create_suppliers(confirm=1)`; never on install.
- Ads Bill Account Map (platform account / billing setup → company, GSTIN billing address, supplier,
  cost centre, item, Tax Withholding Category) also records the GSTIN the platform holds; a
  difference raises GSTIN Mismatch.
- GST only through India Compliance (`bill_gst.py`): CGST+SGST same state, IGST otherwise; Overseas
  supplier → reverse charge, IC sets ITC classification "Import Of Service" (needs IC's *Enable
  Overseas Transactions*). Naming series ≤ 16 characters (GST document-number rule).
- TDS: the map's (else the supplier's) Tax Withholding Category; ERPNext computes it on the net value.
  Never hard-code a rate: the section/rate is the tax adviser's decision (see the category's notes).
- Auto-submit (Ads Settings switch, default off) only for an untouched API bill whose every check
  passes (`bills.check`). Everything else is Needs Review / Draft Created; `approve_bill` needs
  `approve_bills`, Purchase Invoice submit permission, and a person who neither typed the bill
  (`prepared_by`) nor last edited the draft.
- Alerts carry no amounts (Ads Alert is not a finance record); amounts stay on the bill document.
- Release 2: AI extraction of a manual bill's PDF plugs into `bills.EXTRACTORS`; extracted figures are
  treated like typed ones (never auto-submitted).

**Leads and attribution**
- Intake (`excom/services/intake.py`) asks `attribution.intake_context()` what ad a payload names and calls
  `attribution.on_intake()` in a savepoint: a failure is logged, the lead is still created. WhatsApp inbound
  calls `on_whatsapp_message()` for messages with `referral`. Never write `ads_*` fields any other way.
- Touch rows are named `naming.touch_name(touch_source, observation_id)`; the observation id is the intake
  log's dedupe key, `wa:<wamid>` or `so:<Sales Order>`. Replays land on the same row and never blank a value.
- Platform test leads (`ads_is_test`) are kept out of every lead KPI (`attribution.kpi_lead_filters()`).
- Google Ads lead forms post to `/api/method/excom.excom.api.intake.google_lead_form`; the per-form key is
  checked in constant time before anything is stored, and is never stored.

**Tests**: every slice ships tests with recorded provider payloads. Prefix test records `QA ` and clean
them in `setUpClass`/`tearDownClass`. Add the module to the default list in
`excom/excom/tests/run.py`. Run:

    bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_x'}"

Load DocType JSON changes without a migrate: `bench --site frappe.localhost execute excom.excom_ads.tests.dev.reload_all`.
Bench Redis must be up (see machine notes); no worker runs, so jobs queued by tests are never
executed — call the job function directly in tests.

**Google Ads** (`providers/google_ads.py`, API v26 over REST, GAQL through `googleAds:search`)
- Auth: a service account added as a user on the Google Ads manager account (preferred), or an OAuth
  refresh token from an Internal consent screen. `login-customer-id` = the manager account. Developer
  tokens were retired by Google on 2026-09-09; the header is sent only if one is configured.
- Money is `*_micros` / 1e6. Ads are keyed `{ad_group_id}~{ad_id}`. Performance Max has no ad groups
  or ads (account and campaign rows only). `leads` = conversions of new-lead conversion-action
  categories, `purchases` = PURCHASE; the full per-category breakdown is in `metrics_json`.
  Tests replace `google_auth.http_request`.
