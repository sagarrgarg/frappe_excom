"""Leads screen (plan §23): live feed of ad leads with SLA timers, platform vs verified leads by
campaign, and the lead → opportunity → quotation → order funnel.

Permissions. Every CRM read is `frappe.get_list` on the gateway's doctype names, so ERPNext's role
permissions, User Permissions and Excom's own lead visibility rule (services/crm_visibility.py:
owner / assignee / team) all apply; nothing here bypasses them. On top, the ads scope: a user
restricted by company / workspace / brand / account sees only leads whose first ad touch is an
ad account (or, for a brand cut, a campaign) inside that scope. A lead whose ad is not linked to
a mirror yet has no account, so only unrestricted users see it.

Figures. Platform-reported leads (Ads Metric Snapshot, the network's own attribution) and
verified leads (ERPNext Leads whose first ad touch is the campaign, created in the period,
platform test leads excluded via attribution.kpi_lead_filters) sit side by side and are never
summed. CPL is shown both ways and labelled: platform spend ÷ platform leads, platform spend ÷
verified leads.

SLA. Excom's first-response target per intake source (Excom Source.sla_first_response). A lead is
answered by its first outbound message after it was created, or its auto-acknowledgement, the
same rule as the SLA ladder (tasks/crm_sla.py). The first reply by a person is shown apart.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime

from excom.excom.services import crm_gateway as gw
from excom.excom_ads import attribution, dashboard, permissions

V16 = cint(frappe.__version__.split(".")[0]) >= 16
FEED_FIELDS = [
	"name", "lead_name", "company_name", "status", "lead_owner", "creation", "company", "intake_source", "first_touch_channel", "auto_ack_sent_at", "omni_identity",
	"ads_touch_source", "ads_provider", "ads_account", "ads_campaign", "ads_campaign_name", "ads_ad_group_name", "ads_advertisement", "ads_ad_name",
	"ads_is_test", "ads_is_organic", "ads_link_status", "ads_first_touch_at",
]
MAX_PAGE = 200
COHORT_MAX = 5000
SLA_MAX = 2000
CHUNK = 500
STAGES = ("leads", "opportunities", "quotations", "orders")


def _count(alias: str = "n"):
	# v16 refuses SQL functions as field strings; v15 has no dict syntax.
	return {"COUNT": "*", "as": alias} if V16 else f"count(*) as {alias}"


def _chunks(items, size: int = CHUNK):
	items = list(items)
	for i in range(0, len(items), size):
		yield items[i : i + size]


def _has_ads_fields() -> bool:
	return frappe.get_meta(gw.LEAD).has_field("ads_campaign")


# ─── scope → lead filters ─────────────────────────────────────────────────────

def lead_filters(filters: dict, a=None, b=None, include_test: bool = False) -> list:
	"""get_list filters for ad leads inside the caller's ads scope and the screen's filters."""
	scope = permissions.get_scope()
	if scope is None:
		raise frappe.PermissionError(_("You have no access to ads data."))
	f: list = [["ads_touch_source", "is", "set"]]
	if not include_test:
		f += [[k, "=", v] for k, v in attribution.kpi_lead_filters().items()]
	if a:
		f.append(["creation", ">=", f"{getdate(a)} 00:00:00"])
	if b:
		f.append(["creation", "<", f"{add_days(getdate(b), 1)} 00:00:00"])
	dims = {k: v for k, v in filters.items() if k in ("company", "workspace", "brand", "provider", "account") and v}
	if filters.get("campaign"):
		camp = frappe.get_list("Ads Campaign", filters={"name": filters["campaign"], **dims}, pluck="name", limit_page_length=1)
		f.append(["ads_campaign", "in", camp or ["-"]])
	elif dims.get("brand") or scope.get("brand") is not None:
		# Brand is allocated per campaign: a brand cut needs the campaign, not the account.
		camps = frappe.get_list("Ads Campaign", filters=dims, pluck="name", limit_page_length=0)
		f.append(["ads_campaign", "in", camps or ["-"]])
	elif dims or scope:
		accs = [r.name for r in dashboard._accounts_in_scope(dims, ["name"])]
		f.append(["ads_account", "in", accs or ["-"]])
	return f


def _readable(doctype: str) -> bool:
	return bool(frappe.has_permission(doctype, "read"))


# ─── verified counts (also used by reports.py) ────────────────────────────────

def verified_leads_by_campaign(campaigns: list | None, a, b, filters: dict | None = None) -> dict:
	"""{"available", "reason", "counts": {campaign: n}} of verified leads created in [a, b], for the
	current user (their lead visibility applies). Test leads excluded."""
	if not _has_ads_fields():
		return {"available": False, "reason": _("ads attribution fields are not set up"), "counts": {}}
	if not _readable(gw.LEAD):
		return {"available": False, "reason": _("no permission to read leads"), "counts": {}}
	if campaigns is not None and not campaigns:
		return {"available": True, "reason": None, "counts": {}}
	f = lead_filters(filters or {}, a, b)
	counts: dict[str, int] = {}
	groups = _chunks(campaigns) if campaigns is not None else [None]
	try:
		for chunk in groups:
			ff = f + ([["ads_campaign", "in", chunk]] if chunk is not None else [["ads_campaign", "is", "set"]])
			for r in frappe.get_list(gw.LEAD, filters=ff, fields=["ads_campaign", _count()], group_by="ads_campaign", limit_page_length=0):
				counts[r.ads_campaign] = counts.get(r.ads_campaign, 0) + cint(r.n)
	except frappe.PermissionError:
		return {"available": False, "reason": _("no permission to read leads"), "counts": {}}
	return {"available": True, "reason": None, "counts": counts}


# ─── SLA ──────────────────────────────────────────────────────────────────────

def _sla_targets() -> dict:
	if not frappe.db.exists("DocType", "Excom Source"):
		return {}
	return {r.name: cint(r.sla_first_response) for r in frappe.get_all("Excom Source", fields=["name", "sla_first_response"]) if cint(r.sla_first_response)}


def _outbound_since(identities: list, since) -> dict:
	"""identity → [(created, by_person)] of outbound messages since `since`, oldest first. Only
	timestamps leave this function: the caller can already open these leads."""
	out: dict[str, list] = {}
	for chunk in _chunks([i for i in identities if i]):
		rows = frappe.db.sql(
			"""select omni_identity, creation, created_by_user from `tabExcom Message`
			where omni_identity in %(ids)s and direction = 'Outbound' and creation >= %(since)s and ifnull(is_internal, 0) = 0
			order by creation asc limit 20000""",
			{"ids": tuple(chunk), "since": since},
			as_dict=True,
		)
		for r in rows:
			person = bool(r.created_by_user) and r.created_by_user not in ("Administrator", "Guest")
			out.setdefault(r.omni_identity, []).append((get_datetime(r.creation), person))
	return out


def annotate_sla(rows: list[dict], now=None) -> None:
	"""Adds first_response_at / first_response_secs / first_person_reply_at / sla_target_secs /
	sla_status / sla_due_at to each lead row."""
	now = get_datetime(now) if now else now_datetime()
	targets = _sla_targets()
	since = min((get_datetime(r["creation"]) for r in rows), default=None)
	msgs = _outbound_since([r.get("omni_identity") for r in rows], since) if since else {}
	for r in rows:
		created = get_datetime(r["creation"])
		after = [m for m in msgs.get(r.get("omni_identity")) or [] if m[0] >= created]
		first = after[0][0] if after else None
		ack = get_datetime(r["auto_ack_sent_at"]) if r.get("auto_ack_sent_at") else None
		if ack and ack >= created and (first is None or ack < first):
			first = ack
		person = next((m[0] for m in after if m[1]), None)
		target = targets.get(r.get("intake_source")) or 0
		r["first_response_at"] = first
		r["first_response_secs"] = int((first - created).total_seconds()) if first else None
		r["first_person_reply_at"] = person
		r["sla_target_secs"] = target or None
		r["sla_due_at"] = None
		if not target:
			r["sla_status"] = "No SLA"
		elif first:
			r["sla_status"] = "Met" if (first - created).total_seconds() <= target else "Missed"
		elif (now - created).total_seconds() > target:
			r["sla_status"] = "Breached"
		else:
			r["sla_status"] = "Pending"
			r["sla_due_at"] = created + timedelta(seconds=target)


# ─── endpoints ────────────────────────────────────────────────────────────────

def _unavailable(reason: str) -> dict:
	return {"available": False, "reason": reason}


@frappe.whitelist()
def feed(from_date, to_date, start: int = 0, page_length: int = 50, include_test: int = 0, **kwargs) -> dict:
	"""Newest ad leads first, with ad, owner, SLA state and first response."""
	permissions.require_access()
	a, b = dashboard._dates(from_date, to_date)
	if not _has_ads_fields():
		return _unavailable(_("Ads attribution fields are not set up on Lead yet (run a migrate)."))
	if not _readable(gw.LEAD):
		return _unavailable(_("You cannot read leads. Ask an admin for CRM access."))
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), MAX_PAGE)
	try:
		rows = frappe.get_list(gw.LEAD, filters=lead_filters(dashboard._clean_filters(kwargs), a, b, include_test=bool(cint(include_test))), fields=FEED_FIELDS, order_by="creation desc, name desc", start=start, page_length=page_length + 1)
	except frappe.PermissionError:
		return _unavailable(_("You cannot read leads. Ask an admin for CRM access."))
	more = len(rows) > page_length
	rows = rows[:page_length]
	annotate_sla(rows)
	accounts = {r.name: r.account_name for r in frappe.get_list("Ads Account", filters={"name": ["in", list({r.ads_account for r in rows if r.ads_account}) or ["-"]]}, fields=["name", "account_name"], limit_page_length=0)}
	for r in rows:
		r["account_name"] = accounts.get(r.ads_account)
		r["doctype"] = gw.LEAD
	return {"available": True, "rows": rows, "start": start, "has_more": more, "now": now_datetime(), "from_date": str(a), "to_date": str(b)}


@frappe.whitelist()
def summary(from_date, to_date, **kwargs) -> dict:
	"""Platform vs verified leads by campaign (never summed), funnel, SLA over the period's leads."""
	permissions.require_access()
	a, b = dashboard._dates(from_date, to_date)
	filters = dashboard._clean_filters(kwargs)
	out = {"from_date": str(a), "to_date": str(b), "by_campaign": _by_campaign(filters, a, b)}
	if not _has_ads_fields() or not _readable(gw.LEAD):
		out.update({"verified_available": False, "reason": _("You cannot read leads, so verified figures are not available."), "funnel": None, "sla": None, "totals": None})
		return out
	try:
		out.update(_cohort(filters, a, b))
	except frappe.PermissionError:
		out.update({"verified_available": False, "reason": _("You cannot read leads, so verified figures are not available."), "funnel": None, "sla": None, "totals": None})
		return out
	out["verified_available"] = True
	return out


def _by_campaign(filters: dict, a, b) -> list[dict]:
	where, params = dashboard._where(filters, a, b, "Campaign")
	rows = frappe.db.sql(
		f"""select s.campaign, c.campaign_name, s.provider, s.account, s.company_currency as currency,
			sum(s.spend_company_currency) as spend, sum(s.leads) as platform_leads, sum(s.clicks) as clicks
		from `tab{dashboard.SNAP}` s left join `tabAds Campaign` c on c.name = s.campaign
		where {where} and s.campaign is not null
		group by s.campaign, c.campaign_name, s.provider, s.account, s.company_currency
		order by spend desc limit 200""",
		params,
		as_dict=True,
	)
	by = {r.campaign: r for r in rows}
	ver = verified_leads_by_campaign(None, a, b, filters)
	counts = ver["counts"] if ver["available"] else {}
	missing = [c for c in counts if c not in by]
	if missing:
		for m in frappe.get_list("Ads Campaign", filters={"name": ["in", missing]}, fields=["name", "campaign_name", "provider", "account"], limit_page_length=0):
			by[m.name] = frappe._dict(campaign=m.name, campaign_name=m.campaign_name, provider=m.provider, account=m.account, currency=None, spend=None, platform_leads=None, clicks=None)
	out = []
	for r in by.values():
		spend = flt(r.spend) if r.spend is not None else None
		pl = flt(r.platform_leads) if r.platform_leads is not None else None
		vl = counts.get(r.campaign, 0) if ver["available"] else None
		out.append({
			"campaign": r.campaign, "campaign_name": r.campaign_name, "provider": r.provider, "account": r.account, "currency": r.currency,
			"spend": spend, "clicks": flt(r.clicks) if r.clicks is not None else None,
			"platform_leads": pl, "verified_leads": vl,
			"cpl_platform": (spend / pl) if spend is not None and pl else None,
			"cpl_verified": (spend / vl) if spend is not None and vl else None,
		})
	out.sort(key=lambda r: (-(r["spend"] or 0), -(r["verified_leads"] or 0), r["campaign_name"] or ""))
	return out


def _cohort(filters: dict, a, b) -> dict:
	f = lead_filters(filters, a, b)
	leads = frappe.get_list(gw.LEAD, filters=f, fields=["name", "creation", "intake_source", "auto_ack_sent_at", "omni_identity", "ads_is_organic", "ads_link_status", "lead_name", "ads_campaign_name", "lead_owner"], order_by="creation desc", limit_page_length=COHORT_MAX + 1)
	truncated = len(leads) > COHORT_MAX
	leads = leads[:COHORT_MAX]
	names = [r.name for r in leads]
	tests = frappe.get_list(gw.LEAD, filters=lead_filters(filters, a, b, include_test=True) + [["ads_is_test", "=", 1]], fields=[_count()], limit_page_length=1)
	totals = {
		"verified_leads": len(leads),
		"organic": sum(1 for r in leads if cint(r.ads_is_organic)),
		"unlinked": sum(1 for r in leads if r.ads_link_status in ("Pending", "Partial")),
		"test_leads_excluded": cint(tests[0].n) if tests else 0,
		"truncated": truncated,
	}
	sla_rows = [dict(r) for r in leads[:SLA_MAX]]
	annotate_sla(sla_rows)
	counts = {"Met": 0, "Missed": 0, "Breached": 0, "Pending": 0, "No SLA": 0}
	secs = []
	for r in sla_rows:
		counts[r["sla_status"]] += 1
		if r["first_response_secs"] is not None:
			secs.append(r["first_response_secs"])
	secs.sort()
	breaches = [{k: r.get(k) for k in ("name", "lead_name", "creation", "lead_owner", "ads_campaign_name", "sla_target_secs", "omni_identity")} for r in sla_rows if r["sla_status"] == "Breached"][:20]
	sla = {"counts": counts, "median_first_response_secs": secs[len(secs) // 2] if secs else None, "measured": len(sla_rows), "breaches": breaches}
	return {"totals": totals, "funnel": funnel(names), "sla": sla}


def funnel(lead_names: list[str]) -> list[dict]:
	"""How many of these leads reached an opportunity, a quotation and a submitted sales order,
	through ERPNext's own links. A stage the user may not read is None (not available), not 0."""
	stages = [{"stage": "leads", "label": _("Leads (verified)"), "count": len(lead_names)}]
	reached_opp: dict[str, str] = {}  # opportunity → lead
	opp_ok = _readable(gw.OPPORTUNITY)
	if opp_ok:
		try:
			for chunk in _chunks(lead_names):
				for o in frappe.get_list(gw.OPPORTUNITY, filters={"opportunity_from": gw.LEAD, "party_name": ["in", chunk]}, fields=["name", "party_name"], limit_page_length=0):
					reached_opp[o.name] = o.party_name
		except frappe.PermissionError:
			opp_ok = False
	stages.append({"stage": "opportunities", "label": _("Reached an opportunity"), "count": len(set(reached_opp.values())) if opp_ok else None})
	quote_lead: dict[str, str] = {}
	q_ok = _readable(gw.QUOTATION)
	if q_ok:
		try:
			for chunk in _chunks(lead_names):
				for q in frappe.get_list(gw.QUOTATION, filters={"quotation_to": gw.LEAD, "party_name": ["in", chunk], "docstatus": ["<", 2]}, fields=["name", "party_name"], limit_page_length=0):
					quote_lead[q.name] = q.party_name
			for chunk in _chunks(list(reached_opp)):
				for q in frappe.get_list(gw.QUOTATION, filters={"opportunity": ["in", chunk], "docstatus": ["<", 2]}, fields=["name", "opportunity"], limit_page_length=0):
					quote_lead.setdefault(q.name, reached_opp[q.opportunity])
		except frappe.PermissionError:
			q_ok = False
	stages.append({"stage": "quotations", "label": _("Got a quotation"), "count": len(set(quote_lead.values())) if q_ok else None})
	order_leads: set[str] = set()
	so_ok = q_ok and _readable("Sales Order")
	if so_ok and quote_lead:
		try:
			order_leads = {quote_lead[q] for q in quotations_with_orders(list(quote_lead))}
		except frappe.PermissionError:
			so_ok = False
	stages.append({"stage": "orders", "label": _("Placed an order"), "count": len(order_leads) if so_ok else None})
	return stages


def quotations_with_orders(quotations: list[str]) -> set[str]:
	"""Quotations that a submitted Sales Order the user can open was made from."""
	out: set[str] = set()
	for chunk in _chunks(quotations):
		orders = frappe.get_list("Sales Order", filters=[["Sales Order Item", "prevdoc_docname", "in", chunk], ["docstatus", "=", 1]], pluck="name", limit_page_length=0)
		if orders:
			# Lines of orders the user can open: only the quotation link is read.
			out |= set(frappe.get_all("Sales Order Item", filters={"parenttype": "Sales Order", "parent": ["in", orders], "prevdoc_docname": ["in", chunk]}, pluck="prevdoc_docname"))
	return out
