"""Budget plans vs actual spend, and the Overspend alert.

Only approved (submitted) Ads Budget Plans count. Actuals are platform-reported (Ads Metric
Snapshot) in company currency, read with the dashboard's level rule: account-level rows for totals
(they include deleted campaigns), campaign-level rows whenever a brand cut is needed, because brand
is allocated per campaign. Everything runs inside the caller's scope.
"""

import frappe
from frappe import _
from frappe.utils import add_months, date_diff, flt, get_first_day, get_last_day, getdate

from excom.excom_ads import permissions

PLAN = "Ads Budget Plan"
LINE = "Ads Budget Plan Line"
SNAP = "Ads Metric Snapshot"
MAX_MONTHS = 24


def overspend_key(plan: str, line: str) -> str:
	return f"overspend:{plan}:{line}"


def _months(a, b) -> list[str]:
	out, m = [], get_first_day(a)
	while m <= b:
		out.append(str(m)[:7])
		m = add_months(m, 1)
	return out


def _planned(filters: dict, a, b) -> list[dict]:
	"""Approved plan lines in [a, b], in scope. Effective brand: line, else plan, else the line's account."""
	cond = ["p.docstatus = 1", "l.parenttype = %(plan_dt)s", "l.month between %(a)s and %(b)s"]
	params = {"plan_dt": PLAN, "a": a, "b": b}
	brand_sql = "coalesce(nullif(l.brand, ''), nullif(p.brand, ''), nullif(acc.brand, ''), '')"
	for f, col in (("company", "p.company"), ("workspace", "p.workspace"), ("provider", "l.provider"), ("account", "l.account")):
		if filters.get(f):
			cond.append(f"{col} = %({f})s")
			params[f] = filters[f]
	if filters.get("brand"):
		cond.append(f"{brand_sql} = %(brand)s")
		params["brand"] = filters["brand"]
	scope_sql = permissions.query_conditions(PLAN)
	if scope_sql:
		cond.append(scope_sql.replace(f"`tab{PLAN}`.", "p."))
	return frappe.db.sql(
		f"""select l.name as line, p.name as plan, l.month, l.provider, {brand_sql} as brand, l.account, p.currency,
			l.planned_spend, l.target_leads, l.target_purchases, l.target_revenue, l.target_cpl, l.target_roas
		from `tab{LINE}` l
		join `tab{PLAN}` p on p.name = l.parent
		left join `tabAds Account` acc on acc.name = l.account
		where {' and '.join(cond)}""",
		params,
		as_dict=True,
	)


def plan_vs_actual(filters: dict | None, from_date, to_date) -> dict:
	"""Planned vs actual spend (and platform-reported leads, purchases, revenue) by month × network
	× brand. Whole months: the range is widened to the first and last day of its months."""
	from excom.excom_ads import dashboard

	filters = {k: v for k, v in (filters or {}).items() if k in dashboard.FILTER_FIELDS and v}
	if not from_date or not to_date:
		frappe.throw(_("From and To dates are required"))
	a, b = get_first_day(getdate(from_date)), get_last_day(getdate(to_date))
	if b < a:
		frappe.throw(_("To date is before From date"))
	if b > add_months(a, MAX_MONTHS):
		frappe.throw(_("Pick a range of at most {0} months").format(MAX_MONTHS))
	today = getdate()

	planned = _planned(filters, a, b)
	by_brand = bool(filters.get("brand")) or any(p.brand for p in planned)
	level = dashboard._level(filters)
	if by_brand:
		level = "Campaign"  # brand is allocated per campaign; account rows cannot split it

	actual = []
	if a <= today:
		where, params = dashboard._where(filters, a, min(b, today), level)
		brand_col = "coalesce(s.brand, '')" if by_brand else "''"
		actual = frappe.db.sql(
			f"""select date_format(s.date, '%%Y-%%m') as month, s.provider, {brand_col} as brand, s.company_currency as currency,
				sum(s.spend_company_currency) as spend,
				sum(case when s.exchange_rate = 0 then s.spend else 0 end) as spend_unconverted,
				sum(s.leads) as leads, sum(s.purchases) as purchases, sum(s.purchase_value * s.exchange_rate) as revenue
			from `tab{SNAP}` s where {where}
			group by date_format(s.date, '%%Y-%%m'), s.provider, {brand_col}, s.company_currency""",
			params,
			as_dict=True,
		)

	rows: dict[tuple, dict] = {}

	def row(month, provider, brand, currency):
		key = (month, provider or "", brand or "", currency or "")
		if key not in rows:
			rows[key] = {"month": month, "provider": provider, "brand": brand or "", "currency": currency, "planned_spend": None, "target_leads": None, "target_purchases": None, "target_revenue": None, "_cpl_w": 0.0, "_cpl_s": 0.0, "_roas_w": 0.0, "_roas_s": 0.0, "lines": 0,
				"actual_spend": None, "spend_unconverted": None, "actual_leads": None, "actual_purchases": None, "actual_revenue": None}
		return rows[key]

	for p in planned:
		r = row(str(p.month)[:7], p.provider, p.brand if by_brand else "", p.currency)
		r["lines"] += 1
		spend = flt(p.planned_spend)
		for k, v in (("planned_spend", spend), ("target_leads", flt(p.target_leads)), ("target_purchases", flt(p.target_purchases)), ("target_revenue", flt(p.target_revenue))):
			r[k] = flt(r[k]) + v
		if flt(p.target_cpl):
			r["_cpl_w"] += spend
			r["_cpl_s"] += spend * flt(p.target_cpl)
		if flt(p.target_roas):
			r["_roas_w"] += spend
			r["_roas_s"] += spend * flt(p.target_roas)
	for x in actual:
		r = row(x.month, x.provider, x.brand, x.currency)
		r.update({"actual_spend": flt(x.spend), "spend_unconverted": flt(x.spend_unconverted), "actual_leads": flt(x.leads), "actual_purchases": flt(x.purchases), "actual_revenue": flt(x.revenue)})

	out = []
	for r in sorted(rows.values(), key=lambda r: (r["month"], r["provider"] or "", r["brand"])):
		month_start = getdate(f"{r['month']}-01")
		started = month_start <= today
		if started and r["actual_spend"] is None:
			# The month has begun and nothing was spent on this network / brand: a real zero.
			r.update({"actual_spend": 0.0, "spend_unconverted": 0.0, "actual_leads": 0.0, "actual_purchases": 0.0, "actual_revenue": 0.0})
		out.append(_finish(r, month_start, today))
	return {
		"from_date": str(a),
		"to_date": str(b),
		"months": _months(a, b),
		"level": level,
		"by_brand": by_brand,
		"source": "Platform",
		"plans": sorted({p.plan for p in planned}),
		"rows": out,
		"totals": _totals(out),
	}


def _div(a, b):
	return flt(a) / flt(b) if a is not None and flt(b) else None


def _finish(r: dict, month_start, today) -> dict:
	planned = r["planned_spend"]
	if planned is not None:
		if today >= get_last_day(month_start):
			to_date = planned
		elif today < month_start:
			to_date = 0.0
		else:
			days = date_diff(get_last_day(month_start), month_start) + 1
			to_date = planned * (date_diff(today, month_start) + 1) / days
		r["planned_to_date"] = round(to_date, 2)
		# Targets entered as CPL / ROAS only: spend-weighted, never a plain average.
		r["target_cpl"] = _div(planned, r["target_leads"]) if flt(r["target_leads"]) else (r["_cpl_s"] / r["_cpl_w"] if r["_cpl_w"] else None)
		r["target_roas"] = _div(r["target_revenue"], planned) if flt(r["target_revenue"]) else (r["_roas_s"] / r["_roas_w"] if r["_roas_w"] else None)
	else:
		r["planned_to_date"] = r["target_cpl"] = r["target_roas"] = None
	for k in ("_cpl_w", "_cpl_s", "_roas_w", "_roas_s"):
		r.pop(k, None)
	act = r["actual_spend"]
	r["variance"] = (act - planned) if act is not None and planned is not None else None
	r["variance_pct"] = _div(r["variance"], planned) * 100 if r["variance"] is not None and flt(planned) else None
	r["pacing"] = _div(act, r["planned_to_date"]) if act is not None and r["planned_to_date"] else None
	r["actual_cpl"] = _div(act, r["actual_leads"]) if act is not None else None
	r["actual_roas"] = _div(r["actual_revenue"], act) if act is not None else None
	return r


def _totals(rows: list[dict]) -> list[dict]:
	out: dict[str, dict] = {}
	sums = ("planned_spend", "planned_to_date", "target_leads", "target_purchases", "target_revenue", "actual_spend", "spend_unconverted", "actual_leads", "actual_purchases", "actual_revenue")
	for r in rows:
		t = out.setdefault(r["currency"] or "", {"currency": r["currency"], **{k: None for k in sums}})
		for k in sums:
			if r.get(k) is not None:
				t[k] = flt(t[k]) + flt(r[k])
	for t in out.values():
		t["variance"] = (t["actual_spend"] - t["planned_spend"]) if t["actual_spend"] is not None and t["planned_spend"] is not None else None
		t["variance_pct"] = _div(t["variance"], t["planned_spend"]) * 100 if t["variance"] is not None and flt(t["planned_spend"]) else None
		t["pacing"] = _div(t["actual_spend"], t["planned_to_date"]) if t["actual_spend"] is not None and t["planned_to_date"] else None
		t["target_cpl"] = _div(t["planned_spend"], t["target_leads"])
		t["actual_cpl"] = _div(t["actual_spend"], t["actual_leads"])
		t["target_roas"] = _div(t["target_revenue"], t["planned_spend"])
		t["actual_roas"] = _div(t["actual_revenue"], t["actual_spend"])
	return list(out.values())


# ─── endpoints ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_plan_vs_actual(from_date, to_date, **kwargs) -> dict:
	from excom.excom_ads.dashboard import _clean_filters

	permissions.require_access()
	return plan_vs_actual(_clean_filters(kwargs), from_date, to_date)


@frappe.whitelist()
def get_plans(from_date=None, to_date=None, **kwargs) -> dict:
	"""Plans in scope (draft, approved, cancelled) for the Budgets tab. Approving happens on the Desk."""
	permissions.require_access()
	f = {k: kwargs[k] for k in ("company", "workspace", "brand") if kwargs.get(k)}
	if from_date and to_date:
		f["period_from"] = ["<=", get_last_day(getdate(to_date))]
		f["period_to"] = [">=", get_first_day(getdate(from_date))]
	rows = frappe.get_list(PLAN, filters=f, fields=["name", "plan_name", "status", "docstatus", "company", "workspace", "brand", "period_from", "period_to", "currency", "total_planned", "owner", "approved_by", "approved_at", "approval_override", "amended_from"], order_by="period_from desc, modified desc", limit_page_length=100)
	user = frappe.session.user
	can = permissions.has_capability("manage_budgets")
	for r in rows:
		r["can_approve"] = bool(can and r.docstatus == 0 and (r.owner != user or permissions.is_super(user)))
	return {"plans": rows, "can_manage": can, "can_create": bool(can and frappe.has_permission(PLAN, "create"))}


# ─── overspend alert ──────────────────────────────────────────────────────────

def _line_actual(p, today) -> float:
	"""Month-to-date platform spend (company currency) for one plan line's scope."""
	month_start = get_first_day(today)
	cond = ["s.date between %(a)s and %(b)s", "s.company = %(company)s", "s.workspace = %(workspace)s", "s.provider = %(provider)s"]
	params = {"a": month_start, "b": today, "company": p.company, "workspace": p.workspace, "provider": p.provider}
	if p.account:
		cond.append("s.account = %(account)s")
		params["account"] = p.account
	if p.brand:
		cond.append("s.brand = %(brand)s")
		params["brand"] = p.brand
	cond.append("s.level = %(level)s")
	params["level"] = "Campaign" if p.brand else "Account"
	return flt(frappe.db.sql(f"select coalesce(sum(s.spend_company_currency), 0) from `tab{SNAP}` s where {' and '.join(cond)}", params)[0][0])


def check_overspend(today=None) -> dict:
	"""Overspend when month-to-date spend exceeds the approved plan line prorated to today by more
	than Ads Settings > overspend threshold. Resolved when it no longer does, or the month is over."""
	from excom.excom_ads.alerts import raise_alert, resolve_alert
	from excom.excom_ads.settings import get_settings

	today = getdate(today)
	month = get_first_day(today)
	pct = flt(get_settings().overspend_threshold_pct)
	days_in_month = date_diff(get_last_day(month), month) + 1
	elapsed = date_diff(today, month) + 1
	lines = frappe.db.sql(
		f"""select l.name as line, p.name as plan, p.plan_name, p.company, p.workspace, p.currency, l.provider, l.account, l.planned_spend,
			coalesce(nullif(l.brand, ''), nullif(p.brand, ''), nullif(acc.brand, '')) as brand
		from `tab{LINE}` l join `tab{PLAN}` p on p.name = l.parent
		left join `tabAds Account` acc on acc.name = l.account
		where p.docstatus = 1 and l.parenttype = %s and l.month = %s""",
		(PLAN, month),
		as_dict=True,
	)
	active, raised = set(), 0
	for p in lines:
		key = overspend_key(p.plan, p.line)
		active.add(key)
		planned = flt(p.planned_spend)
		prorated = planned * elapsed / days_in_month
		actual = _line_actual(p, today)
		limit = prorated * (1 + pct / 100)
		if actual > limit and actual > 0:
			scope = " · ".join(x for x in (p.provider, p.brand, p.account) if x)
			sev = "High" if actual > planned else "Medium"
			raise_alert(
				"Overspend",
				sev,
				f"Overspend: {p.plan_name} · {scope}",
				f"Month-to-date spend {p.currency} {actual:,.2f} is above the approved plan prorated to day {elapsed} of {days_in_month} ({p.currency} {prorated:,.2f}, full month {p.currency} {planned:,.2f}) by more than {pct:g}%. Platform-reported spend.",
				ref=frappe._dict(doctype=PLAN, name=p.plan, company=p.company, workspace=p.workspace, brand=p.brand),
				dedupe_key=key,
				scope={"account": p.account, "brand": p.brand},
			)
			raised += 1
		else:
			resolve_alert(key, "Spend is back within the plan")
	# Lines of past months or cancelled plans: their alerts are no longer actionable.
	resolved = 0
	for key in frappe.get_all("Ads Alert", filters={"alert_type": "Overspend", "status": ["!=", "Resolved"], "dedupe_key": ["like", "overspend:%"]}, pluck="dedupe_key"):
		if key not in active:
			resolved += resolve_alert(key, "Plan month is over or the plan was cancelled")
	return {"lines": len(lines), "raised": raised, "resolved_stale": resolved}


def has_current_plans() -> bool:
	if not frappe.db.table_exists(PLAN):
		return False
	month = get_first_day(getdate())
	return bool(frappe.db.sql(f"select 1 from `tab{LINE}` l join `tab{PLAN}` p on p.name = l.parent where p.docstatus = 1 and l.parenttype = %s and l.month = %s limit 1", (PLAN, month)))

