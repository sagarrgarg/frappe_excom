"""Built-in recipes: what the ERP is asked for a Sales agent's knowledge pack (HLD-007 §6.3).

Each recipe takes the build context and returns one section:

    {"section": "customers", "title": "Customers in scope",
     "facts": [{"text": "5 customers abroad in 3 countries", "source": "Customer: gst_category Overseas …",
                "doctype": "Customer", "names": [...]}],
     "data": {...numbers and names the readiness rules and the brief read...},
     "privacy": "names"}          # aggregates | names | contacts: what may reach a prompt

They read as the person building the agent: a section whose records that person may not read is
skipped and says so. Aggregates use SQL (v16's query builder cannot sum child rows by parent
filters); before each, read permission on every doctype involved is checked.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, nowdate

from excom.excom_ai.builder import geo
from excom.excom_ai.builder.resolve import customers_in_countries, item_group_tree

RECIPES: dict[str, dict] = {}


def recipe(key: str, title: str, packs=("sales",), privacy: str = "aggregates", order: int = 50):
	def wrap(fn):
		RECIPES[key] = {"key": key, "title": title, "packs": packs, "privacy": privacy, "fn": fn, "order": order}
		return fn

	return wrap


class Unreadable(Exception):
	pass


def need(*doctypes):
	for dt in doctypes:
		if not frappe.has_permission(dt, "read"):
			raise Unreadable(dt)


def fact(text: str, source: str, doctype: str | None = None, names: list | None = None, level: str = "info") -> dict:
	return {"text": text, "source": source, "doctype": doctype, "names": (names or [])[:25], "level": level}


def run(key: str, ctx) -> dict:
	r = RECIPES[key]
	out = {"section": key, "title": r["title"], "privacy": r["privacy"], "facts": [], "data": {}}
	try:
		facts, data = r["fn"](ctx)
		out.update(facts=facts, data=data)
	except Unreadable as e:
		out["facts"] = [fact(_("Skipped: you cannot read {0}.").format(e.args[0]), "permissions", level="warning")]
		out["skipped"] = True
	return out


def has(doctype: str, field: str) -> bool:
	return frappe.get_meta(doctype).has_field(field)


def since() -> str:
	return add_months(nowdate(), -12)


def label(doctype: str, name: str) -> str:
	"""A record's human name where its ID is a code (GGIL customers are CE08, not their names)."""
	field = {"Customer": "customer_name", "Supplier": "supplier_name", "Item": "item_name"}.get(doctype)
	return (frappe.db.get_value(doctype, name, field) if field else None) or name


def money(v, currency="INR") -> str:
	v = flt(v)
	if currency == "INR" and v >= 1e7:
		return f"₹{v / 1e7:.2f} Cr"
	if currency == "INR" and v >= 1e5:
		return f"₹{v / 1e5:.1f} L"
	if currency == "INR":
		return f"₹{v:,.0f}"
	return f"{currency} {v:,.0f}"


# ── customers ────────────────────────────────────────────────────────────────


def export_customers(ctx) -> list[str]:
	"""Customers abroad: GST category Overseas, an export customer group, a foreign default currency,
	or an address in the scope's countries."""
	conds, params = [], {"cur": ctx.currency}
	if has("Customer", "gst_category"):
		conds.append("gst_category = 'Overseas'")
	conds.append("(customer_group LIKE '%%export%%' OR customer_group LIKE '%%overseas%%')")
	conds.append("(IFNULL(default_currency, '') NOT IN ('', %(cur)s))")
	names = [r[0] for r in frappe.db.sql(f"SELECT name FROM `tabCustomer` WHERE disabled = 0 AND ({' OR '.join(conds)})", params)]
	return names


@recipe("customers", "Customers in scope", privacy="names", order=10)
def customers(ctx):
	need("Customer", "Address")
	s = ctx.scope
	names = set()
	how = []
	if "export" in s.concepts:
		names |= set(export_customers(ctx))
		how.append("GST category Overseas, an export customer group, or a foreign currency")
	if s.countries:
		names |= set(customers_in_countries(s.countries))
		how.append(_("an address in {0} countries in scope").format(len(s.countries)))
	if s.customer_groups:
		names |= set(frappe.get_all("Customer", {"customer_group": ["in", s.customer_groups], "disabled": 0}, pluck="name"))
		how.append(_("customer groups {0}").format(", ".join(s.customer_groups)))
	if s.territories:
		names |= set(frappe.get_all("Customer", {"territory": ["in", s.territories], "disabled": 0}, pluck="name"))
		how.append(_("territories {0}").format(", ".join(s.territories)))
	names -= set(ctx.excluded.get("Customer", []))
	internal = set(frappe.get_all("Customer", {"is_internal_customer": 1}, pluck="name")) if has("Customer", "is_internal_customer") else set()
	sister = set(frappe.get_all("Customer", {"customer_group": ["like", "%sister%"]}, pluck="name"))
	real = sorted(names - internal - sister)
	countries = {}
	if real:
		for c, n in frappe.db.sql(
			"""SELECT a.country, COUNT(DISTINCT dl.link_name) FROM `tabAddress` a JOIN `tabDynamic Link` dl
			ON dl.parent = a.name AND dl.parenttype = 'Address' AND dl.link_doctype = 'Customer'
			WHERE dl.link_name IN %(n)s GROUP BY a.country""", {"n": tuple(real)}):
			countries[c or "?"] = n
	in_markets = [c for c in real if c in set(customers_in_countries(s.countries))] if s.countries else []
	named = ", ".join(label("Customer", c) for c in real[:5])
	facts = [fact(_("{0} customers in scope, in {1} countries{2}").format(len(real), len([c for c in countries if c != "?"]),
		f": {named}" if named else ""),
		"; ".join(how) or _("no customer filter in the keywords"), "Customer", real)]
	if s.countries:
		facts.append(fact(_("{0} of them in the target markets").format(len(in_markets)), _("Address country"), "Customer",
			in_markets, level="warning" if not in_markets else "info"))
	if names & (internal | sister):
		facts.append(fact(_("{0} sister or internal companies left out").format(len(names & (internal | sister))),
			_("is_internal_customer, or a customer group named Sister"), "Customer", sorted(names & (internal | sister))))
	return facts, {"customers": real, "countries": countries, "in_markets": in_markets,
		"sister": sorted(internal | sister)}


# ── sales history ────────────────────────────────────────────────────────────


@recipe("sales", "Sales history (12 months)", privacy="names", order=20)
def sales(ctx):
	need("Sales Invoice")
	s = ctx.scope
	facts, data = [], {}
	if "export" in s.concepts:
		cond = ["si.currency != %(cur)s"]
		if has("Sales Invoice", "gst_category"):
			cond.append("si.gst_category = 'Overseas'")
		where = f"si.docstatus = 1 AND si.company = %(co)s AND ({' OR '.join(cond)})"
		p = {"cur": ctx.currency, "co": ctx.company, "since": since()}
		total = frappe.db.sql(f"SELECT COUNT(*), SUM(si.base_grand_total) FROM `tabSales Invoice` si WHERE {where} AND si.posting_date >= %(since)s", p)[0]
		ever = frappe.db.sql(f"SELECT COUNT(*) FROM `tabSales Invoice` si WHERE {where}", p)[0][0]
		top = frappe.db.sql(f"""SELECT si.customer, si.currency, SUM(si.base_grand_total) t FROM `tabSales Invoice` si
			WHERE {where} AND si.posting_date >= %(since)s GROUP BY si.customer, si.currency ORDER BY t DESC LIMIT 10""", p, as_dict=True)
		groups = frappe.db.sql(f"""SELECT sii.item_group, SUM(sii.base_net_amount) t FROM `tabSales Invoice Item` sii
			JOIN `tabSales Invoice` si ON si.name = sii.parent WHERE {where} AND si.posting_date >= %(since)s
			GROUP BY sii.item_group ORDER BY t DESC LIMIT 5""", p, as_dict=True)
		data["export"] = {"invoices_12m": cint(total[0]), "value_12m": flt(total[1]), "invoices_ever": cint(ever),
			"top_customers": [dict(r) for r in top], "item_groups": [dict(r) for r in groups]}
		facts.append(fact(_("Export invoices: {0} ever, {1} in the last 12 months worth {2}").format(
			ever, cint(total[0]), money(total[1])), _("Sales Invoice, submitted, foreign currency or GST category Overseas"),
			"Sales Invoice"))
		if top:
			facts.append(fact(_("Top export customers: {0}").format(", ".join(f"{label('Customer', r.customer)} ({money(r.t)})" for r in top[:5])),
				_("Sales Invoice, last 12 months"), "Customer", [r.customer for r in top]))
		if groups:
			facts.append(fact(_("Mostly {0}").format(", ".join(r.item_group or "?" for r in groups[:3])),
				_("Sales Invoice Item by item group"), "Item Group", [r.item_group for r in groups]))
	if ctx.groups:
		p = {"g": tuple(ctx.groups), "co": ctx.company, "since": since(), "cur": ctx.currency}
		row = frappe.db.sql("""SELECT SUM(sii.base_net_amount), COUNT(DISTINCT si.customer),
			SUM(si.currency != %(cur)s) FROM `tabSales Invoice Item` sii JOIN `tabSales Invoice` si ON si.name = sii.parent
			WHERE si.docstatus = 1 AND si.company = %(co)s AND si.posting_date >= %(since)s AND sii.item_group IN %(g)s""", p)[0]
		abroad = frappe.db.sql("""SELECT COUNT(DISTINCT si.name) FROM `tabSales Invoice Item` sii
			JOIN `tabSales Invoice` si ON si.name = sii.parent WHERE si.docstatus = 1 AND si.company = %(co)s
			AND sii.item_group IN %(g)s AND si.currency != %(cur)s""", p)[0][0]
		data["products"] = {"value_12m": flt(row[0]), "customers_12m": cint(row[1]), "exported_invoices": cint(abroad)}
		facts.append(fact(_("{0}: {1} sold in 12 months to {2} customers; {3} export invoices ever").format(
			", ".join(ctx.scope.item_groups), money(row[0]), cint(row[1]), cint(abroad)),
			_("Sales Invoice Item in these item groups"), "Item Group", ctx.scope.item_groups,
			level="warning" if not abroad and "export" in s.concepts else "info"))
	return facts, data


# ── exclusions ───────────────────────────────────────────────────────────────


@recipe("exclusions", "Never contacted cold", privacy="none", order=30)
def exclusions(ctx):
	"""Every existing customer (an agent never cold-mails one), sister companies, people's open leads
	and opportunities, do-not-contact identities and domains. Kept in code, never sent to prompts."""
	need("Customer", "Lead", "Opportunity")
	customers = frappe.get_all("Customer", {"disabled": 0}, pluck="name")
	domains = set()
	for e in frappe.get_all("Contact Email", {"parenttype": "Contact"}, pluck="email_id", limit=20000):
		if e and "@" in e:
			domains.add(e.split("@", 1)[1].lower())
	leads = frappe.get_all("Lead", {"status": ["not in", ("Converted", "Do Not Contact")], "lead_owner": ["is", "set"]},
		pluck="name", limit=50000)
	opps = frappe.get_all("Opportunity", {"status": ["in", ("Open", "Replied", "Quotation")]}, pluck="name", limit=50000)
	dnc = frappe.db.count("Omni Identity", {"do_not_contact": 1}) if frappe.db.table_exists("Omni Identity") else 0
	dnc_domains = [r.domain for r in frappe.get_single("Excom AI Settings").dnc_domains or []]
	facts = [fact(_("{0} customers, {1} leads and {2} open opportunities owned by people, {3} do-not-contact people "
		"and {4} blocked domains are never written to cold").format(len(customers), len(leads), len(opps), dnc, len(dnc_domains)),
		_("Customer, Lead, Opportunity, Omni Identity, Excom AI Settings"))]
	return facts, {"customers": len(customers), "leads": len(leads), "opportunities": len(opps), "dnc": dnc,
		"dnc_domains": dnc_domains, "customer_domains": len(domains)}


# ── products ─────────────────────────────────────────────────────────────────


@recipe("products", "Products", privacy="names", order=40)
def products(ctx):
	need("Item")
	if not ctx.groups and not ctx.scope.brands and not ctx.scope.item_codes:
		return [fact(_("No product in the keywords: the agent offers the whole range"), _("keywords"), level="warning")], {}
	filters = {"disabled": 0, "is_sales_item": 1}
	or_filters = {}
	if ctx.groups:
		or_filters["item_group"] = ["in", ctx.groups]
	if ctx.scope.brands:
		or_filters["brand"] = ["in", ctx.scope.brands]
	if ctx.scope.item_codes:
		or_filters["name"] = ["in", ctx.scope.item_codes]
	fields = ["name", "item_name", "item_group", "image"] + [f for f in ("weight_per_unit", "gst_hsn_code", "country_of_origin")
		if has("Item", f)]
	items = [i for i in frappe.get_list("Item", filters=filters, or_filters=or_filters, fields=fields, limit_page_length=5000)
		if i.name not in set(ctx.excluded.get("Item", []))]
	n = len(items)
	photo = sum(1 for i in items if i.image)
	weight = sum(1 for i in items if flt(i.get("weight_per_unit")))
	hsn = sum(1 for i in items if i.get("gst_hsn_code"))
	facts = [fact(_("{0} items: {1} with a photo, {2} with a weight, {3} with an HS code").format(n, photo, weight, hsn),
		_("Item in {0}").format(", ".join(ctx.groups or ctx.scope.brands or ["the listed items"])), "Item",
		[i.name for i in items], level="warning" if n and (photo < n or weight < n or hsn < n) else "info")]
	return facts, {"items": [i.name for i in items], "count": n, "with_photo": photo, "with_weight": weight,
		"with_hsn": hsn, "ready": [i.name for i in items if i.image and flt(i.get("weight_per_unit")) and i.get("gst_hsn_code")]}


# ── price basis ──────────────────────────────────────────────────────────────


@recipe("prices", "Price basis", privacy="aggregates", order=50)
def prices(ctx):
	need("Price List", "Item Price")
	wanted = set(ctx.scope.currencies)
	for c in ctx.scope.countries:
		cur = geo.currency_of(c)
		if cur:
			wanted.add(cur)
	if "export" in ctx.scope.concepts:
		wanted.add("USD")
	lists = frappe.get_all("Price List", {"selling": 1, "enabled": 1}, ["name", "currency"])
	items = ctx.item_codes or []
	rows, facts = [], []
	for pl in lists:
		if ctx.scope.price_lists and pl.name not in ctx.scope.price_lists and pl.currency not in wanted:
			continue
		if not ctx.scope.price_lists and pl.currency not in wanted and pl.currency == ctx.currency:
			continue
		total = frappe.db.count("Item Price", {"price_list": pl.name})
		covered = frappe.db.count("Item Price", {"price_list": pl.name, "item_code": ["in", items]}) if items else 0
		tiers = frappe.db.count("Item Price", {"price_list": pl.name, "min_qty": [">", 1]}) if has("Item Price", "min_qty") else 0
		rows.append({"price_list": pl.name, "currency": pl.currency, "prices": total, "covered": covered, "tiers": tiers})
	foreign = [r for r in rows if r["currency"] != ctx.currency]
	if foreign:
		for r in foreign:
			facts.append(fact(_("{0} ({1}): {2} prices, {3} of the items in scope, {4} with quantity tiers").format(
				r["price_list"], r["currency"], r["prices"], r["covered"], r["tiers"]), _("Price List, Item Price"), "Price List",
				[r["price_list"]], level="warning" if items and r["covered"] < len(items) else "info"))
	else:
		facts.append(fact(_("No selling price list in {0}").format(", ".join(sorted(wanted)) or _("a foreign currency")),
			_("Price List (selling)"), "Price List", level="blocker"))
	return facts, {"lists": rows, "currencies": sorted(wanted)}


# ── terms ────────────────────────────────────────────────────────────────────


@recipe("terms", "Terms", privacy="aggregates", order=60)
def terms(ctx):
	need("Payment Terms Template", "Quotation", "Sales Invoice")
	templates = frappe.get_all("Payment Terms Template", pluck="name")
	inco_q = frappe.db.count("Quotation", {"incoterm": ["is", "set"]}) if has("Quotation", "incoterm") else 0
	inco_si = frappe.db.count("Sales Invoice", {"incoterm": ["is", "set"], "docstatus": 1}) if has("Sales Invoice", "incoterm") else 0
	with_gst = frappe.db.count("Sales Invoice", {"is_export_with_gst": 1, "docstatus": 1}) if has("Sales Invoice", "is_export_with_gst") else 0
	facts = [
		fact(_("{0} payment terms templates").format(len(templates)) if templates else _("No payment terms template"),
			_("Payment Terms Template"), "Payment Terms Template", templates, level="info" if templates else "blocker"),
		fact(_("Incoterms used on {0} quotations and {1} invoices").format(inco_q, inco_si), _("Quotation, Sales Invoice: incoterm"),
			level="info" if inco_q + inco_si > 5 else "warning"),
		fact(_("{0} export invoices with GST paid (the rest under LUT)").format(with_gst), _("Sales Invoice: is_export_with_gst")),
	]
	return facts, {"payment_terms": templates, "incoterm_uses": inco_q + inco_si, "export_with_gst": with_gst}


# ── logistics ────────────────────────────────────────────────────────────────


@recipe("logistics", "Logistics", privacy="names", order=70)
def logistics(ctx):
	need("Supplier")
	transporters = frappe.get_all("Supplier", {"is_transporter": 1, "disabled": 0}, pluck="name") if has("Supplier", "is_transporter") else []
	pattern = ["%forward%", "%logistic%", "%freight%", "%customs%", "%cargo%", "%clearing%", "%shipping%"]
	forwarders = set()
	for p in pattern:
		forwarders |= set(frappe.get_all("Supplier", {"disabled": 0, "supplier_group": ["like", p]}, pluck="name"))
		forwarders |= set(frappe.get_all("Supplier", {"disabled": 0, "supplier_name": ["like", p]}, pluck="name"))
	customs = sorted(n for n in forwarders if any(w in n.lower() for w in ("custom", "clearing", "cha")))
	used = []
	if has("Sales Invoice", "transporter") and frappe.has_permission("Sales Invoice", "read"):
		used = [r[0] for r in frappe.db.sql("""SELECT DISTINCT IFNULL(s.supplier_name, si.transporter)
			FROM `tabSales Invoice` si LEFT JOIN `tabSupplier` s ON s.name = si.transporter WHERE si.docstatus = 1
			AND IFNULL(si.transporter, '') != '' AND si.currency != %s""", ctx.currency)]
	ports = []
	if has("Sales Invoice", "port_code"):
		ports = [r[0] for r in frappe.db.sql("SELECT DISTINCT port_code FROM `tabSales Invoice` WHERE IFNULL(port_code, '') != ''")]
	facts = [
		fact(_("{0} transporters on record").format(len(transporters)), _("Supplier: is_transporter"), "Supplier", transporters),
		fact(_("{0} forwarders or customs agents found ({1} customs)").format(len(forwarders), len(customs)),
			_("Supplier group or name: forwarder, logistics, freight, customs, cargo"), "Supplier", sorted(forwarders),
			level="info" if forwarders and customs else "blocker"),
		fact(_("Carriers on export invoices: {0}").format(", ".join(used) or _("none recorded")), _("Sales Invoice: transporter"),
			"Supplier", used),
	]
	if ports:
		facts.append(fact(_("Ports used: {0}").format(", ".join(ports[:10])), _("Sales Invoice: port_code")))
	return facts, {"transporters": transporters, "forwarders": sorted(forwarders), "customs": customs, "carriers": used,
		"ports": ports}


# ── pipeline ─────────────────────────────────────────────────────────────────


@recipe("pipeline", "Pipeline", privacy="aggregates", order=80)
def pipeline(ctx):
	need("Lead", "Opportunity", "Quotation")
	home = frappe.db.get_value("Company", ctx.company, "country") or "India"
	data, facts = {}, []
	if ctx.scope.countries:
		n = frappe.db.count("Lead", {"country": ["in", ctx.scope.countries]})
		data["leads_in_markets"] = n
		facts.append(fact(_("{0} leads in the target markets").format(n), _("Lead: country"), "Lead",
			level="warning" if not n else "info"))
	if ctx.scope.teams and has("Lead", "excom_team"):
		tagged = frappe.db.count("Lead", {"excom_team": ["in", ctx.scope.teams]})
		at_home = frappe.db.count("Lead", {"excom_team": ["in", ctx.scope.teams], "country": home})
		data.update(team_leads=tagged, team_leads_at_home=at_home)
	if "export" in ctx.scope.concepts:
		teams = [t for t in frappe.get_all("Excom Team", pluck="name") if "export" in t.lower()] if has("Lead", "excom_team") else []
		tagged = frappe.db.count("Lead", {"excom_team": ["in", teams]}) if teams else 0
		at_home = frappe.db.count("Lead", {"excom_team": ["in", teams], "country": home}) if teams else 0
		abroad = frappe.db.count("Lead", {"country": ["not in", [home, ""]], "status": ["!=", "Converted"]})
		quotes = frappe.db.count("Quotation", {"currency": ["!=", ctx.currency], "docstatus": ["<", 2]})
		opps = frappe.db.count("Opportunity", {"currency": ["!=", ctx.currency], "status": "Open"}) if has("Opportunity", "currency") else 0
		data.update(export_team_leads=tagged, export_team_leads_at_home=at_home, leads_abroad=abroad, foreign_quotes=quotes,
			foreign_opportunities=opps)
		facts.append(fact(_("{0} leads abroad; {1} foreign-currency quotations; {2} open opportunities").format(abroad, quotes, opps),
			_("Lead: country; Quotation, Opportunity: currency")))
		if tagged:
			facts.append(fact(_("{0} leads tagged to {1}, {2} of them in {3}").format(tagged, ", ".join(teams), at_home, home),
				_("Lead: excom_team, country"), "Lead", level="warning" if at_home > tagged / 2 else "info"))
	return facts, data


# ── conversations ────────────────────────────────────────────────────────────


@recipe("conversations", "Conversations", privacy="aggregates", order=90)
def conversations(ctx):
	need("Excom Thread")
	accounts = ctx.accounts or []
	if not accounts:
		return [fact(_("No number or mailbox of the desk in scope yet"), _("Excom Channel Account"), level="warning")], {}
	total = frappe.db.count("Excom Thread", {"account": ["in", accounts]})
	recent = frappe.db.count("Excom Thread", {"account": ["in", accounts], "last_message_at": [">=", since()]})
	open_ = frappe.db.count("Excom Thread", {"account": ["in", accounts], "status": "Open"})
	share_open = (open_ / total) if total else 0
	facts = [fact(_("{0} conversations on {1} numbers and mailboxes, {2} in the last 12 months").format(total, len(accounts), recent),
		_("Excom Thread"), "Excom Channel Account", accounts, level="info" if recent >= 30 else "warning"),
		fact(_("{0:.1%} of them still Open").format(share_open), _("Excom Thread: status"),
			level="warning" if share_open > 0.9 else "info")]
	return facts, {"threads": total, "recent": recent, "open_share": round(share_open, 3)}


# ── compliance ───────────────────────────────────────────────────────────────


@recipe("compliance", "Compliance", privacy="aggregates", order=100)
def compliance(ctx):
	facts, data = [], {}
	if frappe.db.exists("DocType", "GST Settings") and has("GST Settings", "enable_overseas_transactions"):
		on = cint(frappe.db.get_single_value("GST Settings", "enable_overseas_transactions"))
		data["overseas_enabled"] = on
		facts.append(fact(_("Overseas transactions are {0} in GST Settings").format(_("on") if on else _("off")),
			_("GST Settings"), level="info" if on else "warning"))
	lut = any(has("Company", f) for f in ("lut_number", "lut_no", "lut")) or frappe.db.exists("DocType", "LUT")
	data["lut_field"] = bool(lut)
	facts.append(fact(_("A place for the LUT and its expiry") if lut else _("Nowhere to record the LUT and its expiry"),
		_("Company fields, LUT doctype"), level="info" if lut else "blocker"))
	register = "docpulse" in frappe.get_installed_apps()
	data["document_register"] = register
	facts.append(fact(_("Certificate register installed") if register else _("No certificate register (docpulse) on this site"),
		_("installed apps"), level="info" if register else "warning"))
	return facts, data


def for_pack(pack: str) -> list[str]:
	return [k for k, r in sorted(RECIPES.items(), key=lambda kv: kv[1]["order"]) if pack in r["packs"]]
