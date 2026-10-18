"""Read API for the Marketing screens: overview, campaign explorer, accounts, activity, alerts.

Every query runs inside the caller's scope (permissions.query_conditions for SQL, frappe.get_list for
records). Ratios are recomputed from summed bases, never averaged. Spend is reported in company
currency; rows without an exchange rate are counted separately instead of being treated as zero.
Platform-reported figures are labelled as such; verified (ERPNext) figures come from their own
functions and are never added to them.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, get_datetime, getdate, now_datetime

from excom.excom_ads import permissions

SNAP = "Ads Metric Snapshot"
MAX_RANGE_DAYS = 400
STALE_HOURS = 6
FILTER_FIELDS = ("company", "workspace", "brand", "provider", "account", "campaign")
NUMERIC = {"spend", "spend_unconverted", "rows_unconverted", "impressions", "clicks", "link_clicks", "leads", "purchases", "purchase_value", "conversations"}

SUMS = """
	sum(s.spend_company_currency) as spend,
	sum(case when s.exchange_rate = 0 then s.spend else 0 end) as spend_unconverted,
	sum(case when s.exchange_rate = 0 then 1 else 0 end) as rows_unconverted,
	sum(s.impressions) as impressions,
	sum(s.clicks) as clicks,
	sum(s.link_clicks) as link_clicks,
	sum(s.leads) as leads,
	sum(s.purchases) as purchases,
	sum(s.purchase_value * s.exchange_rate) as purchase_value,
	sum(s.messaging_conversations) as conversations,
	max(s.synced_at) as metrics_synced_at
"""


# ─── filters ──────────────────────────────────────────────────────────────────

def _dates(from_date, to_date):
	if not from_date or not to_date:
		frappe.throw(_("From and To dates are required"))
	a, b = getdate(from_date), getdate(to_date)
	if b < a:
		frappe.throw(_("To date is before From date"))
	if date_diff(b, a) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of at most {0} days").format(MAX_RANGE_DAYS))
	return a, b


def _level(filters: dict) -> str:
	"""Totals come from account-level rows (they include deleted campaigns). Brand and campaign
	cuts need campaign-level rows, because brand is allocated per campaign."""
	scope = permissions.get_scope() or {}
	if filters.get("brand") or filters.get("campaign") or scope.get("brand") is not None:
		return "Campaign"
	return "Account"


def _where(filters: dict, from_date, to_date, level: str):
	"""SQL WHERE + params for snapshots, including the caller's scope."""
	sql = ["s.level = %(level)s", "s.date between %(from_date)s and %(to_date)s"]
	params = {"level": level, "from_date": from_date, "to_date": to_date}
	for f in FILTER_FIELDS:
		v = filters.get(f)
		if v:
			sql.append(f"s.`{f}` = %({f})s")
			params[f] = v
	scope_sql = permissions.query_conditions(SNAP)
	if scope_sql:
		sql.append(scope_sql.replace(f"`tab{SNAP}`.", "s."))
	return " and ".join(sql), params


def _clean_filters(kwargs: dict) -> dict:
	return {f: kwargs.get(f) for f in FILTER_FIELDS if kwargs.get(f)}


def _derive(row: dict) -> dict:
	"""Ratios from summed bases. A ratio with a zero base is None ("not available"), not 0."""
	r = {k: flt(v) if k in NUMERIC else v for k, v in row.items()}

	def div(a, b, mult=1.0):
		return (flt(a) / flt(b) * mult) if flt(b) else None

	r["ctr"] = div(r.get("clicks"), r.get("impressions"), 100)
	r["cpc"] = div(r.get("spend"), r.get("clicks"))
	r["cpm"] = div(r.get("spend"), r.get("impressions"), 1000)
	r["cpl"] = div(r.get("spend"), r.get("leads"))
	r["cost_per_purchase"] = div(r.get("spend"), r.get("purchases"))
	r["roas"] = div(r.get("purchase_value"), r.get("spend"))
	return r


def _totals(where, params) -> list[dict]:
	rows = frappe.db.sql(f"select s.company_currency as currency, {SUMS} from `tab{SNAP}` s where {where} group by s.company_currency", params, as_dict=True)
	return [_derive(r) for r in rows if r.currency or flt(r.spend) or flt(r.impressions)]


# ─── overview ─────────────────────────────────────────────────────────────────

@frappe.whitelist()
def overview(from_date, to_date, **kwargs) -> dict:
	permissions.require_access()
	a, b = _dates(from_date, to_date)
	filters = _clean_filters(kwargs)
	level = _level(filters)
	where, params = _where(filters, a, b, level)
	days = date_diff(b, a) + 1
	pa, pb = add_days(a, -days), add_days(a, -1)
	pwhere, pparams = _where(filters, pa, pb, level)

	series = frappe.db.sql(f"select s.date, s.company_currency as currency, {SUMS} from `tab{SNAP}` s where {where} group by s.date, s.company_currency order by s.date", params, as_dict=True)
	by_provider = frappe.db.sql(f"select s.provider, s.company_currency as currency, {SUMS} from `tab{SNAP}` s where {where} group by s.provider, s.company_currency order by spend desc", params, as_dict=True)
	cwhere, cparams = _where(filters, a, b, "Campaign")
	by_brand = frappe.db.sql(f"select coalesce(s.brand, '') as brand, s.company_currency as currency, {SUMS} from `tab{SNAP}` s where {cwhere} group by s.brand, s.company_currency order by spend desc", cparams, as_dict=True)
	top_campaigns = frappe.db.sql(
		f"""select s.campaign, c.campaign_name, c.status, s.provider, s.account, s.company_currency as currency, {SUMS}
		from `tab{SNAP}` s left join `tabAds Campaign` c on c.name = s.campaign
		where {cwhere} group by s.campaign, c.campaign_name, c.status, s.provider, s.account, s.company_currency order by spend desc limit 10""",
		cparams,
		as_dict=True,
	)
	return {
		"from_date": str(a),
		"to_date": str(b),
		"level": level,
		"source": "Platform",
		"totals": _totals(where, params),
		"previous": {"from_date": str(pa), "to_date": str(pb), "totals": _totals(pwhere, pparams)},
		"series": [_derive(r) | {"date": str(r.date)} for r in series],
		"by_provider": [_derive(r) for r in by_provider],
		"by_brand": [_derive(r) for r in by_brand],
		"top_campaigns": [_derive(r) for r in top_campaigns],
		"freshness": freshness(filters),
		"alerts": alert_counts(filters),
		"billing": billing_summary(filters) if permissions.has_capability("view_finance") else None,
	}


def _accounts_in_scope(filters: dict, fields: list[str]) -> list[dict]:
	f = {k: v for k, v in filters.items() if k in ("company", "workspace", "brand", "provider") and v}
	if filters.get("account"):
		f["name"] = filters["account"]
	return frappe.get_list("Ads Account", filters=f, fields=fields, limit_page_length=0)


def freshness(filters: dict | None = None) -> dict:
	"""When each account last synced. A figure is only as fresh as its oldest source."""
	rows = _accounts_in_scope(filters or {}, ["name", "account_name", "provider", "sync_enabled", "status", "last_metrics_sync", "last_structure_sync", "error_class", "last_error"])
	now = now_datetime()
	stale, broken, oldest = [], [], None
	for r in rows:
		if not r.sync_enabled:
			continue
		last = get_datetime(r.last_metrics_sync) if r.last_metrics_sync else None
		if last and (oldest is None or last < oldest):
			oldest = last
		if not last or (now - last).total_seconds() > STALE_HOURS * 3600:
			stale.append({"account": r.name, "account_name": r.account_name, "last_metrics_sync": r.last_metrics_sync})
		if r.error_class:
			broken.append({"account": r.name, "account_name": r.account_name, "error_class": r.error_class})
	return {"as_of": oldest, "stale": stale, "errors": broken, "accounts": len(rows), "stale_after_hours": STALE_HOURS}


def alert_counts(filters: dict | None = None) -> dict:
	f = {"status": ["in", ["Open", "Acknowledged"]]}
	for k in ("company", "workspace", "brand", "account"):
		if (filters or {}).get(k):
			f[k] = filters[k]
	# Counted here, not with count() in get_list: v16 refuses SQL functions as field strings and v15
	# lacks the dict syntax. Open alerts are few, so this stays cheap.
	out: dict = {}
	for sev in frappe.get_list("Ads Alert", filters=f, pluck="severity", limit_page_length=0):
		out[sev] = out.get(sev, 0) + 1
	return out


def billing_summary(filters: dict | None = None) -> list[dict]:
	"""What the platform reports, per account. Headroom only where a cap exists; nothing is estimated."""
	permissions.require("view_finance")
	out = []
	for r in _accounts_in_scope(filters or {}, ["name", "account_name", "provider", "currency", "payment_model", "billing_status", "billing_reported_at", "reported_amount_spent", "reported_spend_cap", "has_spend_cap", "reported_amount_due"]):
		reported = r.billing_status == "Reported"
		out.append(
			{
				"account": r.name,
				"account_name": r.account_name,
				"provider": r.provider,
				"currency": r.currency,
				"payment_model": r.payment_model,
				"billing_status": r.billing_status,
				"as_of": r.billing_reported_at,
				"amount_spent": flt(r.reported_amount_spent) if reported else None,
				"spend_cap": flt(r.reported_spend_cap) if reported and r.has_spend_cap else None,
				"headroom": (flt(r.reported_spend_cap) - flt(r.reported_amount_spent)) if reported and r.has_spend_cap else None,
				"amount_due": flt(r.reported_amount_due) if reported else None,
			}
		)
	return out


# ─── campaign explorer ────────────────────────────────────────────────────────

EXPLORER = {
	# level -> (mirror doctype, snapshot link field, name field, parent field)
	"Campaign": ("Ads Campaign", "campaign", "campaign_name", None),
	"Ad Group": ("Ads Ad Group", "ad_group", "ad_group_name", "campaign"),
	"Ad": ("Ads Advertisement", "advertisement", "ad_name", "ad_group"),
}


ORDERABLE = {"spend", "impressions", "clicks", "leads", "purchases", "purchase_value", "title"}


@frappe.whitelist()
def explorer(from_date, to_date, level: str = "Campaign", parent: str | None = None, search: str | None = None, status: str | None = None, start: int = 0, page_length: int = 50, order_by: str = "spend", **kwargs) -> dict:
	"""One level of the account → campaign → ad group → ad tree, with metrics for the range.

	One scoped SQL query: mirrors left-joined to their summed snapshots, sorted and paged in the
	database, so a large account never loads every ad into memory."""
	permissions.require_access()
	if level not in EXPLORER:
		frappe.throw(_("Unknown level {0}").format(level))
	doctype, link, name_field, parent_field = EXPLORER[level]
	if not frappe.has_permission(doctype, "read"):
		raise frappe.PermissionError
	a, b = _dates(from_date, to_date)
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), 200)
	filters = _clean_filters(kwargs)

	cond, params = ["1=1"], {}
	for f in ("company", "workspace", "brand", "provider", "account"):
		if filters.get(f):
			cond.append(f"m.`{f}` = %(m_{f})s")
			params[f"m_{f}"] = filters[f]
	if parent and parent_field:
		cond.append(f"m.`{parent_field}` = %(m_parent)s")
		params["m_parent"] = parent
	if status:
		cond.append("m.status = %(m_status)s")
		params["m_status"] = status
	if search:
		cond.append(f"(m.`{name_field}` like %(m_search)s or m.external_id like %(m_search)s)")
		params["m_search"] = f"%{search}%"
	scope_sql = permissions.query_conditions(doctype)
	if scope_sql:
		cond.append(scope_sql.replace(f"`tab{doctype}`.", "m."))
	mwhere = " and ".join(cond)

	swhere, sparams = _where({k: v for k, v in filters.items() if k != "campaign"}, a, b, level)
	params.update(sparams)
	extra = {"Campaign": "m.objective, m.daily_budget, m.lifetime_budget, m.currency", "Ad Group": "m.campaign, m.daily_budget, m.lifetime_budget, m.optimization_goal, m.currency", "Ad": "m.campaign, m.ad_group, m.creative_name"}[level]
	order = order_by if order_by in ORDERABLE else "spend"
	order_sql = "m.`{0}` asc".format(name_field) if order == "title" else f"(agg.{order} is null), agg.{order} desc"
	total = frappe.db.sql(f"select count(*) from `tab{doctype}` m where {mwhere}", params)[0][0]
	rows = frappe.db.sql(
		f"""select m.name, m.`{name_field}` as title, m.status, m.effective_status, m.provider, m.account, m.brand,
			m.external_id, m.synced_at, m.not_returned_since, {extra}, agg.*
		from `tab{doctype}` m
		left join (select s.`{link}` as entity, max(s.company_currency) as metrics_currency, {SUMS} from `tab{SNAP}` s where {swhere} and s.`{link}` is not null group by s.`{link}`) agg
			on agg.entity = m.name
		where {mwhere}
		order by {order_sql}, m.name
		limit %(start)s, %(page_length)s""",
		{**params, "start": start, "page_length": page_length},
		as_dict=True,
	)
	metric_keys = (*NUMERIC, "metrics_synced_at")
	out = []
	for r in rows:
		has = r.get("entity") is not None
		row = {k: v for k, v in r.items() if k not in metric_keys and k not in ("entity", "metrics_currency")}
		# Metric amounts are in company currency; budgets on the row stay in the account's currency.
		row["metrics"] = _derive({k: r.get(k) for k in metric_keys}) | {"currency": r.get("metrics_currency")} if has else None
		out.append(row)
	return {"level": level, "total": total, "rows": out, "source": "Platform", "from_date": str(a), "to_date": str(b)}


# ─── accounts, activity, alerts ───────────────────────────────────────────────

@frappe.whitelist()
def accounts(**kwargs) -> list[dict]:
	permissions.require_access()
	fields = ["name", "account_name", "provider", "external_id", "workspace", "company", "brand", "status", "platform_status", "disable_reason", "currency", "timezone_name", "payment_model", "sync_enabled", "last_structure_sync", "last_metrics_sync", "metrics_synced_from", "metrics_synced_until", "error_class", "consecutive_failures", "last_error", "meta_connection"]
	rows = _accounts_in_scope(_clean_filters(kwargs), fields)
	billing = {b["account"]: b for b in billing_summary(_clean_filters(kwargs))} if permissions.has_capability("view_finance") else {}
	for r in rows:
		r["billing"] = billing.get(r.name)
		r["can_manage"] = permissions.has_capability("manage_connections")
	return rows


@frappe.whitelist()
def activity(account: str | None = None, status: str | None = None, start: int = 0, page_length: int = 50) -> list[dict]:
	permissions.require_access()
	f = {}
	if account:
		f["account"] = account
	if status:
		f["status"] = status
	return frappe.get_list("Ads Sync Job", filters=f, fields=["name", "account", "job_type", "status", "triggered_by", "window_from", "window_to", "started_at", "finished_at", "api_calls", "records_written", "error_class", "error"], order_by="creation desc", start=cint(start), page_length=min(max(cint(page_length), 1), 200))


@frappe.whitelist()
def alerts(status: str | None = "open", severity: str | None = None, start: int = 0, page_length: int = 50) -> list[dict]:
	permissions.require_access()
	f = {}
	if status == "open":
		f["status"] = ["in", ["Open", "Acknowledged", "Snoozed"]]
	elif status:
		f["status"] = status
	if severity:
		f["severity"] = severity
	return frappe.get_list("Ads Alert", filters=f, fields=["name", "title", "alert_type", "severity", "status", "message", "company", "brand", "account", "reference_doctype", "reference_name", "occurrences", "first_seen", "last_seen", "snoozed_until", "acknowledged_by"], order_by="last_seen desc", start=cint(start), page_length=min(max(cint(page_length), 1), 200))


@frappe.whitelist()
def filter_options() -> dict:
	"""What the filter bar can offer this user: only things inside their scope."""
	permissions.require_access()
	return {
		"companies": sorted({r.company for r in frappe.get_list("Ads Workspace", fields=["company"], limit_page_length=0) if r.company}),
		"workspaces": frappe.get_list("Ads Workspace", fields=["name", "company"], limit_page_length=0),
		"brands": frappe.get_list("Ads Brand", fields=["name", "workspace"], limit_page_length=0),
		"accounts": frappe.get_list("Ads Account", fields=["name", "account_name", "provider", "workspace", "brand"], limit_page_length=0),
		"providers": sorted({r.provider for r in frappe.get_list("Ads Account", fields=["provider"], limit_page_length=0)}),
		"rights": {c: permissions.has_capability(c) for c in permissions.CAPABILITIES},
	}
