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
| `sync.py` | Jobs (Structure / Metrics / Settle / Backfill), upserts, alerts on results. |
| `permissions.py` | Scope (company / workspace / brand / account) + rights (Ads Access Profile). |
| `alerts.py` | `raise_alert()` / `resolve_alert()`; delivery by Ads Notification Policy. |
| `settings.py` | `get_settings()` — Ads Settings with defaults. Add new keys to `DEFAULTS`. |
| `naming.py` | Deterministic names from natural keys. |
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
- Credentials stay in Password fields on their owning record (Meta: `Excom Meta Connection`).
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

**Tests**: every slice ships tests with recorded provider payloads. Prefix test records `QA ` and clean
them in `setUpClass`/`tearDownClass`. Add the module to the default list in
`excom/excom/tests/run.py`. Run:

    bench --site frappe.localhost execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_x'}"

Load DocType JSON changes without a migrate: `bench --site frappe.localhost execute excom.excom_ads.tests.dev.reload_all`.
Bench Redis must be up (see machine notes); no worker runs, so jobs queued by tests are never
executed — call the job function directly in tests.
