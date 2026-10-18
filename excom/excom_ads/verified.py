"""Verified results: what ERPNext and the CRM recorded, attributed to Ads Campaigns. Source "Verified".

These figures are never added to platform-reported figures (or to GA4 "Analytics" figures). Two
ratios combine them on purpose and say so in their names: verified CPL = platform spend ÷ verified
leads, MER = verified net revenue ÷ total platform ad spend.

Leads, qualified, opportunities
  leads          Leads whose ads_campaign (the first ad touch, set only by attribution.py) is an
                 in-scope campaign, platform test leads excluded (attribution.kpi_lead_filters),
                 counted on the day the Lead was created.
  qualified      of those, Leads that reached an Opportunity: an Opportunity exists for the Lead
                 (opportunity_from = Lead, party_name = the Lead), or the Lead's status says it got
                 there (Opportunity / Quotation / Lost Quotation / Converted).
  opportunities  Opportunities carrying an in-scope ads_campaign (ERPNext copies the ads_* fields
                 from the Lead), or made from such a Lead; counted on the day they were created.

Orders and revenue — an order (Sales Order) or invoice (Sales Invoice) belongs to an Ads Campaign
by the FIRST rule that applies, and only to a campaign of the same company:
  (a) Order touch    an Ads Attribution Touch (touch_source Order) written for the Sales Order,
                     whose campaign link was resolved from the platform IDs the storefront captured.
                     An invoice uses the touch of the Sales Order on its first order-linked line.
  (b) UTM campaign   the document's native utm_campaign (v16; v15 has none), then the Order
                     touch's utm_campaign. It maps to the Ads Campaign of the same company whose
                     campaign name or platform campaign ID equals it (database collation: case- and
                     trailing-space-insensitive). No match, or more than one, → no campaign.
                     An invoice checks its own utm_campaign, then its Sales Order's.
  (c) CRM chain      Sales Order line → Quotation → the Lead it was made for, or its Opportunity
                     (the Opportunity's ads_campaign, else its Lead's). Test leads never attribute.
  Nothing else: in particular a Customer's original Lead is NOT used, because it would credit
  every later order of that customer to one old ad forever. Anything without a rule is
  "unattributed" and is reported as such, never spread over campaigns.

Money (needs the view_finance right; counts do not):
  order value    submitted Sales Orders' base_net_total (company currency), on the order date.
  net revenue    submitted Sales Invoices' base_net_total plus their submitted credit notes (returns
                 against the invoice, negative amounts), recognised in the ORIGINAL invoice's period
                 whatever the credit note's date. Cancelled documents never count. A credit note
                 without "return against" cannot be placed and is left out.
  new customers  attributed invoices whose customer's first ever submitted invoice in that company
                 falls in the range.

Everything runs inside the caller's ads scope (permissions.query_conditions on Ads Campaign) AND
their ERPNext permissions (role + User Permission match conditions of Lead, Opportunity, Sales
Order and Sales Invoice). A record type the user cannot read is "not available" (None), not 0.
Unattributed figures belong to the company, not to a workspace, brand or account: they are only
shown when the view and the user's scope are company-wide.
"""

import frappe
from frappe.utils import add_days, cint, flt, getdate

from excom.excom.services import crm_gateway as gw
from excom.excom_ads import permissions

SOURCE = "Verified"
SO, SI = "Sales Order", "Sales Invoice"
TOUCH = "Ads Attribution Touch"
CAMPAIGN = "Ads Campaign"
BATCH = 500
# Lead.status values once a lead reached an Opportunity or Quotation stage.
QUALIFIED_LEAD_STATUSES = (gw.OPPORTUNITY, gw.QUOTATION, f"Lost {gw.QUOTATION}", "Converted")
COUNT_KEYS = ("leads", "qualified", "opportunities", "orders", "invoices", "new_customers")
MONEY_KEYS = ("order_value", "revenue", "gross_revenue", "returns")
RULES = {
	"touch": "Order touch (storefront click IDs / UTM id)",
	"utm": "UTM campaign = campaign name or ID",
	"chain": "Lead → Opportunity → Quotation → Sales Order",
}


def _t(doctype: str) -> str:
	return f"`tab{doctype}`"


def _chunks(items, size: int = BATCH):
	items = list(items)
	for i in range(0, len(items), size):
		yield items[i : i + size]


# ─── permissions ──────────────────────────────────────────────────────────────

def erp_conditions(doctype: str, user: str | None = None) -> str | None:
	"""ERPNext read permission for `doctype` as SQL on `tab{doctype}`: None = may not read it at all,
	"" = no restriction, else the match conditions (User Permissions, permission query hooks)."""
	user = user or frappe.session.user
	if not frappe.db.exists("DocType", doctype) or not frappe.has_permission(doctype, "read", user=user):
		return None
	from frappe.model.db_query import DatabaseQuery

	q = DatabaseQuery(doctype, user=user)
	q.tables = [_t(doctype)]
	return q.build_match_conditions() or ""


def _and(*parts) -> str:
	return " and ".join(f"({p})" for p in parts if p)


# ─── campaign scope ───────────────────────────────────────────────────────────

def campaign_scope(filters: dict, campaigns: list | None = None) -> tuple[str, dict]:
	"""SQL selecting the names of in-scope Ads Campaigns (filters + the caller's ads scope)."""
	cond, params = ["1=1"], {}
	for f in ("company", "workspace", "brand", "provider", "account"):
		if filters.get(f):
			cond.append(f"vc.`{f}` = %(vc_{f})s")
			params[f"vc_{f}"] = filters[f]
	if filters.get("campaign"):
		cond.append("vc.name = %(vc_campaign)s")
		params["vc_campaign"] = filters["campaign"]
	if campaigns is not None:
		cond.append("vc.name in %(vc_names)s")
		params["vc_names"] = tuple(campaigns) or ("-",)
	if not frappe.has_permission(CAMPAIGN, "read"):
		cond.append("1=0")  # no role permission on campaigns: nothing is attributed for this user
	scope_sql = permissions.query_conditions(CAMPAIGN)
	if scope_sql:
		cond.append(scope_sql.replace(f"{_t(CAMPAIGN)}.", "vc."))
	return f"select vc.name from {_t(CAMPAIGN)} vc where {' and '.join(cond)}", params


def _campaign_info(names) -> dict:
	out = {}
	for chunk in _chunks(names):
		for r in frappe.get_all(CAMPAIGN, filters={"name": ["in", chunk]}, fields=["name", "campaign_name", "company", "brand", "account", "provider", "status"]):
			out[r.name] = r
	return out


def company_level(filters: dict) -> bool:
	"""Unattributed figures belong to a company: only for company-wide views of company-wide users."""
	scope = permissions.get_scope()
	if scope is None:
		return False
	if any(filters.get(f) for f in ("workspace", "brand", "provider", "account", "campaign")):
		return False
	return not any(scope.get(d) is not None for d in ("workspace", "brand", "account"))


def companies_for(filters: dict) -> list[str] | None:
	"""Companies the view covers (None = every company)."""
	scope = permissions.get_scope() or {}
	allowed = scope.get("company")
	if filters.get("company"):
		if allowed is not None and filters["company"] not in allowed:
			return []
		return [filters["company"]]
	return sorted(allowed) if allowed is not None else None


def _currencies() -> dict:
	return dict(frappe.get_all("Company", fields=["name", "default_currency"], as_list=True))


# ─── campaign resolution for orders and invoices ──────────────────────────────

def _utm_map(pairs: set) -> dict:
	"""{(company, value)} → {(company, value_key): campaign or None}. Rule (b): exact campaign name or
	platform campaign ID within the company; ambiguous or missing → None."""
	pairs = {(c, v.strip()) for c, v in pairs if c and v and v.strip()}
	if not pairs:
		return {}
	companies = sorted({c for c, _v in pairs})
	values = sorted({v for _c, v in pairs})
	found: dict[tuple, set] = {}
	for chunk in _chunks(values):
		rows = frappe.db.sql(
			f"select name, company, campaign_name, external_id from {_t(CAMPAIGN)} where company in %(c)s and (campaign_name in %(v)s or external_id in %(v)s)",
			{"c": tuple(companies), "v": tuple(chunk)},
			as_dict=True,
		)
		for r in rows:
			for key in {(r.campaign_name or "").strip().lower(), (r.external_id or "").strip().lower()}:
				if key:
					found.setdefault((r.company, key), set()).add(r.name)
	out = {}
	for c, v in pairs:
		hits = found.get((c, v.lower())) or set()
		out[(c, v.lower())] = next(iter(hits)) if len(hits) == 1 else None
	return out


def _chain_map(quotations: set) -> dict:
	"""Quotation → campaign through its Lead or Opportunity (rule c). Test leads never attribute."""
	quotations = {q for q in quotations if q}
	if not quotations:
		return {}
	qrows = []
	for chunk in _chunks(quotations):
		qrows += frappe.db.sql(f"select name, quotation_to, party_name, opportunity from {_t(gw.QUOTATION)} where name in %(n)s and docstatus < 2", {"n": tuple(chunk)}, as_dict=True)
	opp_names = {q.opportunity for q in qrows if q.opportunity}
	opps = {}
	lead_names = {q.party_name for q in qrows if q.quotation_to == gw.LEAD and q.party_name}
	for chunk in _chunks(opp_names):
		for o in frappe.db.sql(f"select name, ads_campaign, ads_is_test, opportunity_from, party_name from {_t(gw.OPPORTUNITY)} where name in %(n)s", {"n": tuple(chunk)}, as_dict=True):
			opps[o.name] = o
			if o.opportunity_from == gw.LEAD and o.party_name:
				lead_names.add(o.party_name)
	leads = {}
	for chunk in _chunks(lead_names):
		for lead in frappe.db.sql(f"select name, ads_campaign, ads_is_test from {_t(gw.LEAD)} where name in %(n)s", {"n": tuple(chunk)}, as_dict=True):
			leads[lead.name] = lead

	def from_lead(name):
		lead = leads.get(name)
		return lead.ads_campaign if lead and lead.ads_campaign and not cint(lead.ads_is_test) else None

	out = {}
	for q in qrows:
		camp = None
		if q.quotation_to == gw.LEAD:
			camp = from_lead(q.party_name)
		if not camp and q.opportunity in opps:
			o = opps[q.opportunity]
			if o.ads_campaign and not cint(o.ads_is_test):
				camp = o.ads_campaign
			elif o.opportunity_from == gw.LEAD:
				camp = from_lead(o.party_name)
		out[q.name] = camp
	return out


def resolve_sales_orders(names) -> dict:
	"""Sales Order → (campaign, rule) by rules (a), (b), (c); (None, None) when none applies.
	Reads the orders regardless of date: an invoice in October may bill a September order."""
	names = sorted({n for n in names if n})
	if not names:
		return {}
	has_utm = frappe.get_meta(SO).has_field("utm_campaign")
	rows = []
	for chunk in _chunks(names):
		rows += frappe.db.sql(
			f"""select so.name, so.company, {"so.utm_campaign" if has_utm else "null"} as utm_campaign,
				t.campaign as touch_campaign, t.utm_campaign as touch_utm,
				(select i.prevdoc_docname from `tabSales Order Item` i where i.parent = so.name and ifnull(i.prevdoc_docname, '') != '' order by i.idx limit 1) as quotation
			from {_t(SO)} so
			left join {_t(TOUCH)} t on t.reference_name = so.name and t.reference_doctype = %(so_dt)s and t.touch_source = 'Order'
			where so.name in %(n)s""",
			{"n": tuple(chunk), "so_dt": SO},
			as_dict=True,
		)
	camp_company = dict(frappe.get_all(CAMPAIGN, filters={"name": ["in", sorted({r.touch_campaign for r in rows if r.touch_campaign}) or ["-"]]}, fields=["name", "company"], as_list=True))
	utm = _utm_map({(r.company, r.utm_campaign or r.touch_utm or "") for r in rows})
	chain = _chain_map({r.quotation for r in rows})
	chain_company = dict(frappe.get_all(CAMPAIGN, filters={"name": ["in", sorted({c for c in chain.values() if c}) or ["-"]]}, fields=["name", "company"], as_list=True))
	out = {}
	for r in rows:
		res = (None, None)
		if r.touch_campaign and camp_company.get(r.touch_campaign) == r.company:
			res = (r.touch_campaign, "touch")
		else:
			value = (r.utm_campaign or r.touch_utm or "").strip()
			mapped = utm.get((r.company, value.lower())) if value else None
			if mapped:
				res = (mapped, "utm")
			elif r.quotation and chain.get(r.quotation) and chain_company.get(chain[r.quotation]) == r.company:
				res = (chain[r.quotation], "chain")
		out[r.name] = res
	return out


# ─── aggregation helpers ──────────────────────────────────────────────────────

class _Acc:
	"""Sums per (campaign, day) plus per-company unattributed sums."""

	def __init__(self):
		self.cells: dict[tuple, dict] = {}
		self.unattributed: dict[str, dict] = {}
		self.rules: dict[str, int] = {}

	def add(self, campaign: str, day, **values):
		cell = self.cells.setdefault((campaign, str(getdate(day))), {})
		for k, v in values.items():
			cell[k] = cell.get(k, 0) + flt(v)

	def add_unattributed(self, company: str, **values):
		cell = self.unattributed.setdefault(company, {})
		for k, v in values.items():
			cell[k] = cell.get(k, 0) + flt(v)


def _date_params(a, b) -> dict:
	return {"a": getdate(a), "b": getdate(b), "a_dt": f"{getdate(a)} 00:00:00", "b1_dt": f"{getdate(add_days(b, 1))} 00:00:00"}


def _leads(acc: _Acc, camp_sql: str, cparams: dict, a, b, perm: str) -> None:
	from excom.excom_ads.attribution import kpi_lead_filters

	T = _t(gw.LEAD)
	kpi = [f"{T}.`{k}` = %(kpi_{k})s" for k in kpi_lead_filters()]
	params = {**cparams, **_date_params(a, b), "lead_dt": gw.LEAD, "qs": QUALIFIED_LEAD_STATUSES, **{f"kpi_{k}": v for k, v in kpi_lead_filters().items()}}
	where = _and(f"{T}.ads_campaign in ({camp_sql})", *kpi, f"{T}.creation >= %(a_dt)s and {T}.creation < %(b1_dt)s", perm)
	rows = frappe.db.sql(
		f"""select {T}.ads_campaign as campaign, date({T}.creation) as day, count(*) as leads,
			sum(case when {T}.status in %(qs)s or exists (select 1 from {_t(gw.OPPORTUNITY)} q where q.opportunity_from = %(lead_dt)s and q.party_name = {T}.name) then 1 else 0 end) as qualified
		from {T} where {where} group by {T}.ads_campaign, date({T}.creation)""",
		params,
		as_dict=True,
	)
	for r in rows:
		acc.add(r.campaign, r.day, leads=r.leads, qualified=r.qualified)


def _opportunities(acc: _Acc, camp_sql: str, cparams: dict, a, b, perm: str) -> None:
	T = _t(gw.OPPORTUNITY)
	params = {**cparams, **_date_params(a, b), "lead_dt": gw.LEAD}
	camp = f"coalesce(nullif({T}.ads_campaign, ''), vl.ads_campaign)"
	where = _and(
		f"{camp} in ({camp_sql})",
		f"ifnull({T}.ads_is_test, 0) = 0 and ifnull(vl.ads_is_test, 0) = 0",
		f"{T}.creation >= %(a_dt)s and {T}.creation < %(b1_dt)s",
		perm,
	)
	rows = frappe.db.sql(
		f"""select {camp} as campaign, date({T}.creation) as day, count(*) as opportunities
		from {T} left join {_t(gw.LEAD)} vl on {T}.opportunity_from = %(lead_dt)s and vl.name = {T}.party_name
		where {where} group by {camp}, date({T}.creation)""",
		params,
		as_dict=True,
	)
	for r in rows:
		acc.add(r.campaign, r.day, opportunities=r.opportunities)


def _company_sql(T: str, companies, params: dict) -> str:
	if companies is None:
		return ""
	params["companies"] = tuple(companies) or ("-",)
	return f"{T}.company in %(companies)s"


def _orders(acc: _Acc, in_scope: set, companies, a, b, perm: str, track_unattributed: bool) -> None:
	T = _t(SO)
	params = _date_params(a, b)
	has_utm = frappe.get_meta(SO).has_field("utm_campaign")
	base = _and(f"{T}.docstatus = 1", f"{T}.transaction_date between %(a)s and %(b)s", _company_sql(T, companies, params), perm)
	totals = frappe.db.sql(f"select {T}.company, count(*) as orders, sum({T}.base_net_total) as order_value from {T} where {base} group by {T}.company", params, as_dict=True)
	signal = [f"exists (select 1 from {_t(TOUCH)} t where t.reference_name = {T}.name and t.reference_doctype = %(so_dt)s and t.touch_source = 'Order')",
		f"exists (select 1 from `tabSales Order Item` i where i.parent = {T}.name and ifnull(i.prevdoc_docname, '') != '')"]
	if has_utm:
		signal.append(f"ifnull({T}.utm_campaign, '') != ''")
	params["so_dt"] = SO
	cands = frappe.db.sql(f"select {T}.name, {T}.company, {T}.transaction_date, {T}.base_net_total from {T} where {base} and ({' or '.join(signal)})", params, as_dict=True)
	resolved = resolve_sales_orders([c.name for c in cands])
	attributed = {}
	for c in cands:
		camp, rule = resolved.get(c.name, (None, None))
		if not camp:
			continue
		attributed.setdefault(c.company, {"orders": 0, "order_value": 0.0})
		attributed[c.company]["orders"] += 1
		attributed[c.company]["order_value"] += flt(c.base_net_total)
		if camp in in_scope:
			acc.add(camp, c.transaction_date, orders=1, order_value=c.base_net_total)
			acc.rules[rule] = acc.rules.get(rule, 0) + 1
	if track_unattributed:
		for t in totals:
			done = attributed.get(t.company, {})
			acc.add_unattributed(t.company, orders=flt(t.orders) - done.get("orders", 0), order_value=flt(t.order_value) - done.get("order_value", 0))


def _invoices(acc: _Acc, in_scope: set, companies, a, b, perm: str, track_unattributed: bool) -> None:
	T = _t(SI)
	params = _date_params(a, b)
	has_utm = frappe.get_meta(SI).has_field("utm_campaign")
	csql = _company_sql(T, companies, params)
	base = _and(f"{T}.docstatus = 1", f"{T}.is_return = 0", f"{T}.posting_date between %(a)s and %(b)s", csql, perm)
	# Credit notes against the range's invoices, whatever their own date (recognised in the original's period).
	returns = f"""left join (select r.return_against, sum(r.base_net_total) as amount
			from {T} r join {T} vo on vo.name = r.return_against
			where r.docstatus = 1 and r.is_return = 1 and vo.docstatus = 1 and vo.posting_date between %(a)s and %(b)s
			group by r.return_against) vcn on vcn.return_against = {T}.name"""
	totals = frappe.db.sql(
		f"select {T}.company, count(*) as invoices, sum({T}.base_net_total) as gross, sum(ifnull(vcn.amount, 0)) as returns from {T} {returns} where {base} group by {T}.company",
		params,
		as_dict=True,
	)
	first_so = f"(select i.sales_order from `tabSales Invoice Item` i where i.parent = {T}.name and ifnull(i.sales_order, '') != '' order by i.idx limit 1)"
	signal = [f"exists (select 1 from `tabSales Invoice Item` i2 where i2.parent = {T}.name and ifnull(i2.sales_order, '') != '')"]
	if has_utm:
		signal.append(f"ifnull({T}.utm_campaign, '') != ''")
	cands = frappe.db.sql(
		f"""select {T}.name, {T}.company, {T}.customer, {T}.posting_date, {T}.base_net_total as gross, ifnull(vcn.amount, 0) as returns,
			{f"{T}.utm_campaign" if has_utm else "null"} as utm_campaign, {first_so} as sales_order
		from {T} {returns} where {base} and ({' or '.join(signal)})""",
		params,
		as_dict=True,
	)
	so_res = resolve_sales_orders({c.sales_order for c in cands})
	own_utm = _utm_map({(c.company, c.utm_campaign or "") for c in cands})
	attributed, picked = {}, []
	for c in cands:
		camp, rule = so_res.get(c.sales_order, (None, None)) if c.sales_order else (None, None)
		if rule != "touch":
			mine = own_utm.get((c.company, (c.utm_campaign or "").strip().lower())) if (c.utm_campaign or "").strip() else None
			if mine:
				camp, rule = mine, "utm"
		if not camp:
			continue
		net = flt(c.gross) + flt(c.returns)
		cell = attributed.setdefault(c.company, {"invoices": 0, "revenue": 0.0, "gross_revenue": 0.0, "returns": 0.0})
		cell["invoices"] += 1
		cell["revenue"] += net
		cell["gross_revenue"] += flt(c.gross)
		cell["returns"] += flt(c.returns)
		if camp in in_scope:
			acc.add(camp, c.posting_date, invoices=1, revenue=net, gross_revenue=c.gross, returns=c.returns)
			picked.append((camp, c))
	_new_customers(acc, picked, a, b)
	if track_unattributed:
		for t in totals:
			done = attributed.get(t.company, {})
			gross, ret = flt(t.gross), flt(t.returns)
			acc.add_unattributed(t.company, invoices=flt(t.invoices) - done.get("invoices", 0), revenue=gross + ret - done.get("revenue", 0), gross_revenue=gross - done.get("gross_revenue", 0), returns=ret - done.get("returns", 0))


def _new_customers(acc: _Acc, picked: list, a, b) -> None:
	"""A customer is new when their first ever submitted invoice in that company is in the range;
	credited to the campaign of that first invoice."""
	customers = sorted({c.customer for _camp, c in picked if c.customer})
	if not customers:
		return
	first = {}
	for chunk in _chunks(customers):
		for r in frappe.db.sql(
			f"""select customer, company, min(concat(posting_date, '|', name)) as first_key from {_t(SI)}
			where docstatus = 1 and is_return = 0 and customer in %(c)s group by customer, company""",
			{"c": tuple(chunk)},
			as_dict=True,
		):
			first[(r.customer, r.company)] = r.first_key
	seen = set()
	for camp, c in sorted(picked, key=lambda x: (str(x[1].posting_date), x[1].name)):
		key = first.get((c.customer, c.company))
		if key and key == f"{c.posting_date}|{c.name}" and (c.customer, c.company) not in seen:
			seen.add((c.customer, c.company))
			acc.add(camp, c.posting_date, new_customers=1)


def _unlinked_leads(companies, a, b, perm: str) -> int:
	"""Ad-touched leads (first ad touch recorded) whose campaign could not be resolved."""
	from excom.excom_ads.attribution import kpi_lead_filters

	T = _t(gw.LEAD)
	params = {**_date_params(a, b), **{f"kpi_{k}": v for k, v in kpi_lead_filters().items()}}
	company = ""
	if companies is not None:
		params["companies"] = tuple(companies) or ("-",)
		company = f"coalesce(va.company, {T}.company) in %(companies)s"
	where = _and(
		f"ifnull({T}.ads_first_touch, '') != '' and ifnull({T}.ads_campaign, '') = ''",
		*[f"{T}.`{k}` = %(kpi_{k})s" for k in kpi_lead_filters()],
		f"{T}.creation >= %(a_dt)s and {T}.creation < %(b1_dt)s",
		company,
		perm,
	)
	return cint(frappe.db.sql(f"select count(*) from {T} left join `tabAds Account` va on va.name = {T}.ads_account where {where}", params)[0][0])


# ─── public ───────────────────────────────────────────────────────────────────

def _mask(row: dict, available: dict, finance: bool) -> dict:
	"""Not readable → not available (None). Never 0."""
	if not available["leads"]:
		row["leads"] = row["qualified"] = None
	if not available["opportunities"]:
		row["opportunities"] = None
	if not available["orders"]:
		row["orders"] = row["order_value"] = None
	elif not finance:
		row["order_value"] = None
	if not available["revenue"]:
		for k in ("invoices", "revenue", "gross_revenue", "returns", "new_customers"):
			row[k] = None
	return row


def _ratios(row: dict) -> dict:
	spend = row.get("platform_spend")

	def div(x, y):
		return flt(x) / flt(y) if x is not None and y not in (None, 0) and flt(y) else None

	row["verified_cpl"] = div(spend, row.get("leads"))
	row["cost_per_qualified"] = div(spend, row.get("qualified"))
	row["qualified_rate"] = div(row.get("qualified"), row.get("leads"))
	row["mer"] = div(row.get("revenue"), spend) if row.get("revenue") is not None else None
	return row


def compute(filters: dict, from_date, to_date, spend: dict | None = None, campaigns: list | None = None, detail: bool = True, user: str | None = None) -> dict:
	"""The verified block. `spend` = {currency: platform spend in that company currency} for the
	ratios (the overview passes its own totals). `campaigns` limits to those campaign names."""
	user = user or frappe.session.user
	a, b = getdate(from_date), getdate(to_date)
	finance = permissions.has_capability("view_finance", user)
	perms = {k: erp_conditions(dt, user) for k, dt in (("leads", gw.LEAD), ("opportunities", gw.OPPORTUNITY), ("orders", SO), ("revenue", SI))}
	available = {k: v is not None for k, v in perms.items()}
	if not finance:
		available["revenue"] = False
	camp_sql, cparams = campaign_scope(filters, campaigns)
	in_scope = {r[0] for r in frappe.db.sql(camp_sql, cparams)}
	company_wide = campaigns is None and company_level(filters)
	companies = companies_for(filters)
	if not company_wide:
		# Only orders of the in-scope campaigns' companies can be attributed to them: read no others.
		mine = sorted({r[0] for r in frappe.db.sql(f"select distinct company from {_t(CAMPAIGN)} where name in ({camp_sql})", cparams) if r[0]})
		companies = [c for c in mine if companies is None or c in companies]
	acc = _Acc()
	if in_scope or company_wide:
		if perms["leads"] is not None and in_scope:
			_leads(acc, camp_sql, cparams, a, b, perms["leads"])
		if perms["opportunities"] is not None and in_scope:
			_opportunities(acc, camp_sql, cparams, a, b, perms["opportunities"])
		if perms["orders"] is not None:
			_orders(acc, in_scope, companies, a, b, perms["orders"], company_wide)
		if perms["revenue"] is not None and finance:
			_invoices(acc, in_scope, companies, a, b, perms["revenue"], company_wide)

	info = _campaign_info({c for c, _d in acc.cells})
	cur_of = _currencies()

	def blank(currency):
		row = {"currency": currency}
		for k in COUNT_KEYS:
			row[k] = 0.0
		for k in MONEY_KEYS:
			row[k] = 0.0 if finance else None
		return row

	def mask(row: dict) -> dict:
		return _mask(row, available, finance)

	totals, by_campaign, by_brand, series = {}, {}, {}, {}
	for (camp, day), cell in acc.cells.items():
		ci = info.get(camp) or frappe._dict()
		cur = cur_of.get(ci.company)
		for bucket, key in ((totals, cur), (by_campaign, (camp, cur)), (by_brand, (ci.brand or "", cur)), (series, (day, cur))):
			row = bucket.get(key)
			if row is None:
				row = bucket[key] = blank(cur)
			for k, v in cell.items():
				if row.get(k) is not None or k in COUNT_KEYS:
					row[k] = (row.get(k) or 0) + v
	for cur in (spend or {}):
		totals.setdefault(cur, blank(cur))
	total_rows = []
	for cur, row in totals.items():
		row["platform_spend"] = (spend or {}).get(cur)
		total_rows.append(_ratios(mask(row)))
	out = {
		"source": SOURCE,
		"from_date": str(a),
		"to_date": str(b),
		"available": available,
		"totals": total_rows,
		"rules_used": dict(acc.rules),
		"unattributed": None,
		"leads_unlinked": None,
	}
	if company_wide:
		un = {}
		for company, cell in acc.unattributed.items():
			cur = cur_of.get(company)
			row = un.setdefault(cur, {"currency": cur, "orders": 0.0, "order_value": 0.0, "invoices": 0.0, "revenue": 0.0, "gross_revenue": 0.0, "returns": 0.0})
			for k, v in cell.items():
				row[k] = row.get(k, 0) + v
		out["unattributed"] = [mask({**r, "leads": None, "qualified": None, "opportunities": None, "new_customers": None}) for r in un.values()]
		if available["leads"]:
			out["leads_unlinked"] = _unlinked_leads(companies, a, b, perms["leads"])
	if detail:
		camp_rows = []
		for (camp, cur), row in by_campaign.items():
			ci = info.get(camp) or frappe._dict()
			camp_rows.append(_ratios(mask({**row, "campaign": camp, "campaign_name": ci.campaign_name, "brand": ci.brand, "provider": ci.provider, "account": ci.account, "platform_spend": None})))
		camp_rows.sort(key=lambda r: (-(flt(r.get("revenue")) if r.get("revenue") is not None else 0), -flt(r.get("leads")), r["campaign"]))
		out["by_campaign"] = camp_rows
		out["by_brand"] = sorted((mask({**row, "brand": brand}) for (brand, cur), row in by_brand.items()), key=lambda r: -flt(r.get("leads")))
		out["series"] = sorted((mask({**row, "date": day}) for (day, cur), row in series.items()), key=lambda r: r["date"])
	return out


def by_campaign(names: list[str], from_date, to_date) -> dict[str, dict]:
	"""Explorer rows: the verified figures of each named campaign (inside the caller's scope)."""
	if not names:
		return {}
	block = compute({}, from_date, to_date, campaigns=names, detail=True)
	finance = permissions.has_capability("view_finance")
	keys = (*COUNT_KEYS, *MONEY_KEYS, "qualified_rate")
	found = {r["campaign"]: {k: r.get(k) for k in keys} for r in block.get("by_campaign") or []}
	# A campaign with nothing recorded has 0 verified results (where the user can read them), not "unknown".
	zero = _mask({**{k: 0.0 for k in COUNT_KEYS}, **{k: (0.0 if finance else None) for k in MONEY_KEYS}, "qualified_rate": None}, block["available"], finance)
	return {n: found.get(n) or dict(zero) for n in names}
