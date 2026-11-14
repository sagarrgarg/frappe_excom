"""Readiness: is everything aligned before the first customer arrives? (HLD-007 §7)

A rule is a code check over the agent and its latest knowledge pack. It returns (passed, detail);
passed None means "a person has to look" (an Info gap). Rules are records (Excom AI Readiness
Rule) so severity, wording and the fix link can be edited and a rule switched off; the check
itself is code, named by `handler`.

The check runs when the agent is built, before Shadow → Live, every night, and when the knowledge
pack changes. Open blockers keep the agent from going Live; a waived gap (an Excom Admin, with a
reason) no longer counts.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, nowdate

CHECKS: dict[str, callable] = {}

# key, title, severity, applies_when, fix_hint, fix_route
SALES_RULES = [
	("sales.price_basis", "Price basis", "Blocker", "",
		"Make a selling price list in the market's currency that covers every item in scope, with quantity tiers.",
		"/app/price-list"),
	("sales.payment_terms", "Payment terms", "Blocker", "export",
		"Create a Payment Terms Template for export, for example 30% on booking and 70% on dispatch.",
		"/app/payment-terms-template/new"),
	("sales.product_facts", "Product facts", "Blocker", "",
		"Add a photo, weight and HS code to every item in scope, or untick the items that are not ready.", "/app/item"),
	("sales.incoterm", "Incoterm", "Warning", "export", "Choose a default incoterm and named place for quotations.",
		"/app/incoterm"),
	("sales.freight_customs", "Freight and customs", "Blocker", "export",
		"Record at least one freight forwarder and one customs agent as suppliers, with reference freight rates.",
		"/app/supplier/new"),
	("sales.lut", "LUT", "Blocker", "export", "Give the LUT number and expiry a home: a field on Company, or the document register.",
		"/app/company"),
	("sales.export_documents", "Export documents", "Info", "export",
		"Check that the quotation and proforma print formats show currency, incoterm, HS code and the bank's SWIFT details.",
		"/app/print-format"),
	("sales.exclusions", "Exclusion list", "Blocker", "", "The exclusion list is built from customers, leads and do-not-contact.",
		""),
	("sales.audience", "Audience basis", "Warning", "",
		"No customers in or near the target markets: the audience comes from the playbook and market data, not lookalikes.", ""),
	("sales.mailbox", "Mailbox", "Blocker", "",
		"Connect the agent's mailbox (Stalwart), with SPF, DKIM and DMARC, and warm it up for at least 14 days.",
		"/excom/admin/accounts"),
	("sales.whatsapp", "WhatsApp", "Warning", "whatsapp",
		"An approved first-contact template, and the number's quality rating green.", "/excom/admin/accounts"),
	("sales.playbook", "Playbook", "Blocker", "",
		"Approve a playbook with Certifications, Banned claims and Objections filled in.",
		"/app/excom-ai-playbook"),
	("sales.certificates", "Certificates", "Warning", "",
		"Keep every certificate the playbook claims on record with its expiry (the document register).", ""),
	("sales.people", "People", "Blocker", "", "Set an active owner and backup, and the team.", ""),
	("sales.people_finder", "People Finder", "Blocker", "",
		"The agent emails only named people from the People Finder: switch on Apollo or Prospeo with a key and a budget, "
		"or untick 'Email only named people'.", "/excom/admin/ai-setup"),
	("sales.lead_hygiene", "Lead hygiene", "Warning", "export", "Re-tag the desk's leads that are not really export leads.",
		"/app/lead"),
	("sales.thread_hygiene", "Thread hygiene", "Warning", "",
		"Close conversations when they end, so reply times mean something.", "/excom"),
]


def check(key: str):
	def wrap(fn):
		CHECKS[key] = fn
		return fn

	return wrap


def facts_of(agent) -> dict:
	name = agent.knowledge or frappe.db.get_value("Excom AI Knowledge", {"agent": agent.name}, "name", order_by="version desc")
	if not name:
		return {}
	return json.loads(frappe.db.get_value("Excom AI Knowledge", name, "facts") or "{}")


def data(facts: dict, section: str) -> dict:
	return (facts.get(section) or {}).get("data") or {}


def scope_concepts(agent) -> set[str]:
	from excom.excom_ai.builder.resolve import CONCEPTS

	out = set()
	for row in agent.keyword_rows or []:
		if row.included and row.resolved_as == "Concept":
			out |= {k for k, c in CONCEPTS.items() if row.keyword in c["words"] or row.keyword == k}
	return out


# ── checks ───────────────────────────────────────────────────────────────────


@check("sales.price_basis")
def price_basis(agent, facts):
	lists = data(facts, "prices").get("lists") or []
	count = data(facts, "products").get("count") or 0
	foreign = [p for p in lists if p.get("currency") != frappe.db.get_value("Company", agent.company, "default_currency")]
	if not foreign:
		return False, _("No selling price list in {0}.").format(", ".join(data(facts, "prices").get("currencies") or ["the market's currency"]))
	best = max(foreign, key=lambda p: p.get("covered") or 0)
	if count and (best.get("covered") or 0) < count:
		return False, _("{0} ({1}) prices {2} of the {3} items in scope.").format(best["price_list"], best["currency"],
			best.get("covered") or 0, count)
	if not best.get("tiers"):
		return True, _("{0} covers the items, but has no quantity tiers.").format(best["price_list"])
	return True, _("{0} ({1}) covers the items, with tiers.").format(best["price_list"], best["currency"])


@check("sales.payment_terms")
def payment_terms(agent, facts):
	t = data(facts, "terms").get("payment_terms") or []
	return (True, _("{0} templates").format(len(t))) if t else (False, _("No Payment Terms Template on the site."))


@check("sales.product_facts")
def product_facts(agent, facts):
	p = data(facts, "products")
	n = p.get("count") or 0
	if not n:
		return False, _("No items in scope.")
	ok = n == p.get("with_photo") == p.get("with_weight") == p.get("with_hsn")
	return ok, _("{0} items: {1} with a photo, {2} with a weight, {3} with an HS code.").format(
		n, p.get("with_photo"), p.get("with_weight"), p.get("with_hsn"))


@check("sales.incoterm")
def incoterm(agent, facts):
	n = data(facts, "terms").get("incoterm_uses") or 0
	return n >= 5, _("Incoterms used on {0} documents.").format(n)


@check("sales.freight_customs")
def freight_customs(agent, facts):
	lg = data(facts, "logistics")
	fw, cu = lg.get("forwarders") or [], lg.get("customs") or []
	return bool(fw and cu), _("{0} forwarders, {1} customs agents; carriers on export invoices: {2}.").format(
		len(fw), len(cu), ", ".join(lg.get("carriers") or []) or _("none"))


@check("sales.lut")
def lut(agent, facts):
	ok = bool(data(facts, "compliance").get("lut_field"))
	return ok, _("A place for the LUT exists.") if ok else _("No field for the LUT anywhere; only is_export_with_gst on invoices.")


@check("sales.export_documents")
def export_documents(agent, facts):
	formats = frappe.get_all("Print Format", {"doc_type": ["in", ("Quotation", "Sales Invoice")], "disabled": 0,
		"standard": "No"}, pluck="name")
	export = [f for f in formats if "export" in f.lower()] or formats
	return None, _("To check by hand: {0}{1}.").format(", ".join(export[:4]) or _("the standard print formats"),
		_(" and {0} more").format(len(export) - 4) if len(export) > 4 else "")


@check("sales.exclusions")
def exclusions(agent, facts):
	ex = data(facts, "exclusions")
	n = (ex.get("customers") or 0) + (ex.get("leads") or 0)
	return n > 0, _("{0} customers, {1} people's leads, {2} do-not-contact.").format(ex.get("customers") or 0,
		ex.get("leads") or 0, ex.get("dnc") or 0)


@check("sales.audience")
def audience(agent, facts):
	c = data(facts, "customers")
	if not agent.markets:
		return True, _("No target markets: the audience is the current customers.")
	n = len(c.get("in_markets") or [])
	return n > 0, _("{0} customers in the target markets.").format(n)


@check("sales.mailbox")
def mailbox(agent, facts):
	emails = [r for r in agent.channels or [] if frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "email"]
	if not emails:
		return False, _("No mailbox on the agent.")
	from excom.excom_ai.mail import warmed_up

	for r in emails:
		status = frappe.db.get_value("Excom Channel Account", r.channel_account, "status")
		if status != "Active":
			return False, _("{0} is {1}.").format(r.channel_account, status)
		if r.paused:
			return False, _("{0} is paused: {1}").format(r.channel_account, r.pause_reason or "")
		if not warmed_up(r):
			return False, _("{0} has not been warming up for 14 days.").format(r.channel_account)
		health = frappe.parse_json(r.health) if r.get("health") else None
		if not health:
			return False, _("{0}: the mailbox health has not been checked yet.").format(r.channel_account)
		missing = [k.upper() for k in ("spf", "dkim", "dmarc", "mx") if not health.get(k)]
		if missing or health.get("blocklisted"):
			return False, _("{0}: {1}").format(r.channel_account, ", ".join(missing) + (" missing" if missing else "")
				+ (_("; the domain is on a blocklist") if health.get("blocklisted") else ""))
	return True, _("{0}: connected, warmed up, SPF, DKIM and DMARC pass.").format(", ".join(r.channel_account for r in emails))


@check("sales.whatsapp")
def whatsapp(agent, facts):
	was = [r.channel_account for r in agent.channels or []
		if frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "whatsapp"]
	if not was:
		return True, _("WhatsApp not in scope.")
	if not frappe.db.table_exists("WhatsApp Templates"):
		return False, _("No WhatsApp templates on this site.")
	meta = frappe.get_meta("WhatsApp Templates")
	filters = {"status": "APPROVED"} if meta.has_field("status") else {}
	n = frappe.db.count("WhatsApp Templates", filters)
	quality = {a: frappe.db.get_value("Excom Channel Account", a, "wa_quality_rating")
		if frappe.get_meta("Excom Channel Account").has_field("wa_quality_rating") else None for a in was}
	bad = [a for a, q in quality.items() if q and str(q).upper() not in ("GREEN", "HIGH")]
	if not n:
		return False, _("No approved template for first contact.")
	if bad:
		return False, _("Quality rating not green on {0}.").format(", ".join(bad))
	return True, _("{0} approved templates.").format(n)


PLAYBOOK_NEEDS = ("Certifications", "Banned claims", "Objections")


@check("sales.playbook")
def playbook(agent, facts):
	links = [r.playbook for r in agent.playbooks or []]
	approved = [p for p in links if frappe.db.get_value("Excom AI Playbook", p, "status") == "Approved"]
	if not approved:
		return False, _("No approved playbook.") if links else _("No playbook linked.")
	kinds = {s.kind for s in frappe.get_all("Excom AI Playbook Section", {"parent": ["in", approved]}, ["kind", "content"])
		if (s.content or "").strip()}
	missing = [k for k in PLAYBOOK_NEEDS if k not in kinds]
	return not missing, _("Missing: {0}.").format(", ".join(missing)) if missing else _("Approved, with claims and objections.")


@check("sales.certificates")
def certificates(agent, facts):
	ok = bool(data(facts, "compliance").get("document_register"))
	return ok, _("Certificate register installed.") if ok else _("Certificate expiry dates are not on this site (docpulse).")


@check("sales.people")
def people(agent, facts):
	from excom.excom_ai.engine.ownership import available

	problems = []
	for label, user in ((_("owner"), agent.owner_user), (_("backup"), agent.backup_user)):
		ok, why = available(user)
		if not ok:
			problems.append(f"{label} {why}")
	if not agent.team:
		problems.append(_("no team"))
	return not problems, "; ".join(problems) or _("Owner and backup active; team {0}.").format(agent.team)


@check("sales.people_finder")
def people_finder(agent, facts):
	from excom.excom_ai import research, tools

	if not research.people_finder_only(agent):
		return True, _("Writes to any verified address (Email only named people is off).")
	on = [research.PROVIDER_LABEL.get(k, k) for k in research.PEOPLE_PROVIDERS if tools.available(k)]
	if not on:
		return False, _("Email only named people is ticked, but neither Apollo nor Prospeo is switched on: it can email nobody.")
	return True, _("Emails only named people found by {0}.").format(" or ".join(on))


@check("sales.lead_hygiene")
def lead_hygiene(agent, facts):
	p = data(facts, "pipeline")
	tagged, home = p.get("export_team_leads") or 0, p.get("export_team_leads_at_home") or 0
	if not tagged:
		return True, _("No leads tagged to an export desk.")
	return home <= tagged / 2, _("{0} of the {1} export desk leads are at home.").format(home, tagged)


@check("sales.thread_hygiene")
def thread_hygiene(agent, facts):
	share = data(facts, "conversations").get("open_share")
	if share is None:
		return True, _("No conversations yet.")
	return share <= 0.9, _("{0:.1%} of the desk's conversations are still Open.").format(share)


# ── running ──────────────────────────────────────────────────────────────────


def applies(rule, agent, concepts: set[str]) -> bool:
	when = [w.strip().lower() for w in (rule.applies_when or "").split(",") if w.strip()]
	if not when:
		return True
	channels = {frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") for r in agent.channels or []}
	return any(w in concepts or w in channels for w in when)


def run(agent_name: str) -> dict:
	"""Check every enabled rule of the agent's type. Returns {state, blockers, warnings, infos}."""
	from excom.excom_ai.engine import registry

	registry.load()  # the packs register their checks on import
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	facts = facts_of(agent)
	concepts = scope_concepts(agent)
	seen = set()
	counts = {"Blocker": 0, "Warning": 0, "Info": 0}
	for rule in frappe.get_all("Excom AI Readiness Rule", {"agent_type": agent.agent_type, "enabled": 1},
			["name", "rule_key", "severity", "handler", "applies_when", "fix_hint"]):
		if not applies(rule, agent, concepts):
			continue
		fn = CHECKS.get(rule.rule_key) or (frappe.get_attr(rule.handler) if rule.handler else None)
		if not fn:
			continue
		try:
			passed, detail = fn(agent, facts)
		except Exception as e:
			passed, detail = False, _("The check failed: {0}").format(e)
			frappe.log_error(title=f"Excom AI readiness: {rule.rule_key}", message=frappe.get_traceback())
		seen.add(rule.name)
		gap = frappe.db.get_value("Excom AI Readiness Gap", {"agent": agent.name, "rule": rule.name}, ["name", "status"], as_dict=True)
		if passed:
			if gap and gap.status == "Open":
				frappe.db.set_value("Excom AI Readiness Gap", gap.name, {"status": "Fixed", "detail": detail,
					"checked_on": now_datetime()}, update_modified=False)
			continue
		severity = "Info" if passed is None else rule.severity
		if gap:
			values = {"detail": detail, "checked_on": now_datetime(), "severity": severity}
			if gap.status == "Fixed":
				values["status"] = "Open"
			frappe.db.set_value("Excom AI Readiness Gap", gap.name, values, update_modified=False)
			status = values.get("status", gap.status)
		else:
			frappe.get_doc({"doctype": "Excom AI Readiness Gap", "agent": agent.name, "rule": rule.name, "status": "Open",
				"detail": detail, "checked_on": now_datetime()}).insert(ignore_permissions=True)
			if severity != rule.severity:
				frappe.db.set_value("Excom AI Readiness Gap", {"agent": agent.name, "rule": rule.name}, "severity", severity)
			status = "Open"
		if status == "Open":
			counts[severity] += 1
	# A rule that no longer applies (or was switched off) leaves no open gap behind.
	for g in frappe.get_all("Excom AI Readiness Gap", {"agent": agent.name, "status": "Open", "rule": ["not in", list(seen) or ["-"]]},
			pluck="name"):
		frappe.db.set_value("Excom AI Readiness Gap", g, "status", "Fixed", update_modified=False)
	state = "Blocked" if counts["Blocker"] else "Warnings" if counts["Warning"] else "Ready"
	frappe.db.set_value("Excom AI Agent", agent.name, {"readiness_state": state, "readiness_checked_on": now_datetime()},
		update_modified=False)
	return {"state": state, "blockers": counts["Blocker"], "warnings": counts["Warning"], "infos": counts["Info"]}


def gap_to_todo(gap_name: str, user: str) -> str:
	"""One click: a ToDo for a named person to fix a gap."""
	gap = frappe.get_doc("Excom AI Readiness Gap", gap_name)
	rule = frappe.get_doc("Excom AI Readiness Rule", gap.rule)
	agent_name = frappe.db.get_value("Excom AI Agent", gap.agent, "agent_name")
	todo = frappe.get_doc({
		"doctype": "ToDo", "allocated_to": user, "reference_type": "Excom AI Readiness Gap", "reference_name": gap.name,
		"description": f"<b>{frappe.utils.escape_html(agent_name)} (AI): {frappe.utils.escape_html(rule.title)}</b><br>"
			f"{frappe.utils.escape_html(gap.detail or '')}<br>{frappe.utils.escape_html(rule.fix_hint or '')}",
		"priority": "High" if gap.severity == "Blocker" else "Medium",
		"assigned_by": frappe.session.user,
	}).insert(ignore_permissions=True)
	gap.db_set("todo", todo.name, update_modified=False)
	return todo.name


def seed(agent_type: str = "Sales", rules: list | None = None) -> None:
	"""Create a type's rules once (Sales by default; a pack passes its own list of
	(key, title, severity, applies_when, fix_hint, fix_route), its checks registered with @check(key)
	and handler path module.function). Never overwrites edits."""
	if not frappe.db.exists("Excom AI Agent Type", agent_type):
		return
	if rules is not None:
		for key, title, severity, when, hint, route, handler in rules:
			if not frappe.db.exists("Excom AI Readiness Rule", {"rule_key": key}):
				frappe.get_doc({"doctype": "Excom AI Readiness Rule", "rule_key": key, "agent_type": agent_type, "title": title,
					"severity": severity, "applies_when": when, "fix_hint": hint, "fix_route": route, "handler": handler}
					).insert(ignore_permissions=True)
		return
	for key, title, severity, when, hint, route in SALES_RULES:
		if frappe.db.exists("Excom AI Readiness Rule", {"rule_key": key}):
			# a link that moved is corrected; nothing else a person edited is touched
			frappe.db.set_value("Excom AI Readiness Rule", {"rule_key": key, "fix_route": "/excom/admin/channels"},
				"fix_route", route)
			continue
		frappe.get_doc({"doctype": "Excom AI Readiness Rule", "rule_key": key, "agent_type": agent_type, "title": title,
			"severity": severity, "applies_when": when, "fix_hint": hint, "fix_route": route,
			"handler": f"excom.excom_ai.builder.readiness.{key.split('.')[1]}"}).insert(ignore_permissions=True)
