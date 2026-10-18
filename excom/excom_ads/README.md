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
| `providers/google_analytics.py`, `analytics.py`, `api_analytics.py` | GA4: Data API `runReport` / Admin API client; Ads Analytics Property discovery + enabling, daily Ads Web Snapshot sync, Website tab read API (`web_report`). |
| `verified.py` | Verified (ERPNext) results: leads / qualified / opportunities, orders and net revenue attributed to Ads Campaigns, verified CPL and MER. |
| `quality.py` | Spend Mismatch (account vs campaign spend per day) and Data Stale checks, Overview data-quality note. |
| `imports.py`, `providers/manual.py` | Manual ad networks: Ads Metric Import (CSV / XLSX → snapshots); the Manual provider declares every operation unsupported. |
| `reports.py`, `report_schedule.py` | Ads Report Template → HTML + Excel + CSV (+ PDF where an engine exists), rendered as each recipient; schedules, Ads Report Delivery log, preview / send now / download endpoints. |
| `escalation.py` | Escalation of Open (unacknowledged) alerts past their policy's threshold, once. |
| `leads_dashboard.py` | Leads screen: ad-lead feed with SLA state, platform vs verified leads by campaign, lead → order funnel. |
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

**Three sources, never summed.** Every figure has one: `Platform` (what the ad network reports,
including Manual imports), `Verified` (ERPNext / CRM records, `verified.py`) or `Analytics` (GA4,
`analytics.py`). The UI shows `SourceBadge` on each. Two ratios mix sources on purpose and say so in
their names: verified CPL = platform spend ÷ verified leads; MER = verified net revenue ÷ platform spend.

**Verified results** (`verified.py`; module docstring has the exact rules)
- Leads: `ads_campaign` set by attribution, test leads out (`attribution.kpi_lead_filters()`), counted on
  the Lead's creation day; qualified = reached an Opportunity; opportunities = `ads_campaign` or made from such a Lead.
- An order / invoice belongs to a campaign of the SAME company by the first rule that applies:
  (a) Order touch of the Sales Order (an invoice uses its first order-linked line's order),
  (b) UTM campaign — the document's native `utm_campaign` (v16 only), then the order touch's — equal to
  exactly one campaign's name or platform ID (DB collation: case-insensitive), (c) Sales Order line →
  Quotation → its Lead / Opportunity. A Customer's original Lead is never used. No rule → "unattributed".
- Net revenue = submitted invoices' `base_net_total` + their submitted credit notes (`return_against`),
  in the original invoice's period; cancelled documents never count. Money needs `view_finance`; counts don't.
- Runs inside the ads scope AND ERPNext permissions (`erp_conditions`: role read + match conditions).
  A doctype the user cannot read is `None` ("not available"). Unattributed figures are company-level:
  only for company-wide views of company-wide users.

**GA4** (`providers/google_analytics.py`; Data API v1beta, Admin API v1beta; sources in the module docstring)
- The Ads Connection's Scopes must list `https://www.googleapis.com/auth/analytics.readonly`; the
  service account (or OAuth user) is a Viewer on the property. Property = `GA4-{id}`, scoped by workspace/brand.
- Days are in the property's time zone. Metrics: hourly re-read of `ga4_lookback_days`; Settle: daily
  `ga4_settle_days` (GA4 data changes for 24-48 h, key-event attribution up to 12 days); Backfill:
  `ga4_backfill_days`, newest window first, resumable. A re-pulled window replaces its rows.
- Quota: `returnPropertyQuota` on every report; under `MIN_HOURLY_TOKENS` left the job stops (Rate
  Limited, next run continues); 429 RESOURCE_EXHAUSTED → Rate Limited alert. `keyEvents` (not the
  deprecated `conversions`). Users are not additive across days / sources: labelled, not hidden.
- `sessionCampaignId` links an Ads Campaign by exact platform campaign ID only (no name guessing).

**Data quality** (`quality.py`): nightly Spend Mismatch per account per settle-window day (gap above
`spend_mismatch_pct` % and `spend_mismatch_min_amount`; a missing level counts), hourly Data Stale for
connected accounts (`data_stale_hours`) and GA4 properties (`ga4_stale_hours`). Both resolve themselves.

**Manual accounts**: provider `Manual` on Ads Account. Sync, the scheduler and Data Stale skip them;
`dashboard.freshness` lists them under `manual` with their last import. Figures come from Ads Metric
Import (needs `manage_connections`): all-or-nothing row validation, re-import replaces the file's dates,
campaign mirrors matched by ID, then unique name, else created (`m-<hash>` for name-only rows).

**Reports** (`reports.py`, `report_schedule.py`)
- One source of truth: sections are `dashboard.overview` (KPIs vs previous period, trend, network,
  brand, top campaigns, billing, freshness), `budgets.plan_vs_actual`, open Ads Alerts and
  `leads_dashboard.verified_leads_by_campaign`, for the template's filters and an explicit range
  (complete days to yesterday, in the template's time zone). Same range + filters on the screen = same numbers.
- Recipient scope: a report is always rendered **as a person** (`reports.as_user`). A user recipient
  whose ads scope does not cover the template's company / workspace / brand / account is Skipped
  (never sent a partial report); everyone else gets it computed in their own scope and rights
  (billing only with `view_finance`, verified leads only as far as they can open leads). External
  addresses get the owner's view, only while the owner holds `export_data`, and never billing;
  adding one needs `export_data`. An external address that belongs to a user is treated as that user.
- Every report prints a freshness statement: disconnected / failing, stale, sync off, partial sync,
  rows without an exchange rate, manual imports, recent days still settling.
- Idempotent delivery: one Ads Report Delivery per template × period key × recipient × channel,
  claimed (committed) before sending; Sent / Skipped rows are never redone; Failed rows retry up to
  `MAX_ATTEMPTS` inside the period's grace window; a WhatsApp send found stuck is not retried.
  Failures raise "Report Delivery Failed" (resolved by a later clean period). Emails go through
  Email Queue (one email per recipient); WhatsApp through `outbound.send_transactional` with
  Ads Settings → Report WhatsApp Template, the file as a DOCUMENT header (private File, uploaded to
  Meta by id, deleted after the send).
- PDF only where an engine exists: Frappe v16's Chrome generator with `chromium_path` in
  common_site_config, or wkhtmltopdf. Nothing is downloaded or installed; otherwise the report says so.
- CSV cells that start with = + - @ are written as text (formula injection); Excel via
  `frappe.utils.xlsxutils.make_xlsx`, one sheet with the sections stacked (same rows as the CSV).

**Escalation** (`escalation.py`): Ads Notification Policy → `escalate_after_minutes` + "Escalate To".
An alert the policy matches that is still Open that long after it was notified goes once (conditional
UPDATE on `Ads Alert.escalated`) to the escalation recipients who may see it, on the policy's channels;
quiet hours hold non-critical alerts. Escalation recipients never receive normal deliveries.

**Leads screen** (`leads_dashboard.py`): Lead / Opportunity / Quotation / Sales Order only through
`frappe.get_list` (role permissions, User Permissions and Excom's lead visibility rule apply), plus
the ads scope (account, or campaign for brand cuts). SLA = Excom Source `sla_first_response`;
answered by the first outbound message or auto-acknowledgement after the lead arrived (as
`tasks/crm_sla.py`). Platform and verified leads per campaign side by side, CPL both ways, never summed.

