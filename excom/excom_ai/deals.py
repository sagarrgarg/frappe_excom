"""Deal support (HLD-007 §8.1, Phase 1G): quotation drafts, quote follow-ups, reactivation and
order updates, every one under Review.

Prices never come from the model. A price question becomes:

1. the model reads which of the agent's products and quantities the buyer asked about (codes
   from the agent's own list only);
2. ERPNext prices them: a draft Quotation on the agent's price list, so the price list, its
   currency and any pricing rules (quantity tiers) apply exactly as for a person's quotation;
3. two proposals for a person: the Quotation draft, and a reply whose price lines are written by
   code from that quotation. The code checks confirm every price is in the price list.

Quote follow-ups ask, once, about a quotation that has had no answer for 5 working days. Reactivation
suggests a check-in with customers in scope who have not ordered for 6 months, as a ToDo for their
own account owner (the agent never writes to a customer cold). Order updates tell a buyer the
agent brought in when their order ships or is delivered, from the order's own facts.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, fmt_money, getdate, nowdate

from excom.excom_ai.engine import proposals, registry, runner, tasks
from excom.excom_ai.engine.prompts import untrusted

QUOTE_FOLLOW_UP_DAYS = 5
DORMANT_MONTHS = 6

ASK_SCHEMA = {
	"type": "object",
	"properties": {
		"lines": {"type": "array", "items": {"type": "object", "properties": {
			"item_code": {"type": "string"}, "qty": {"type": "number"}}, "required": ["item_code", "qty"], "additionalProperties": False}},
		"destination": {"type": "string"},
		"intro": {"type": "string"},
		"language": {"type": "string"},
	},
	"required": ["lines", "destination", "intro", "language"],
	"additionalProperties": False,
}


def catalogue(agent, limit: int = 150) -> list[dict]:
	"""The agent's products a buyer may ask about: its items and the items of its groups."""
	codes = [p.item for p in agent.products or [] if p.item]
	groups = [p.item_group for p in agent.products or [] if p.item_group]
	rows = frappe.get_all("Item", {"name": ["in", codes or ["-"]], "disabled": 0}, ["name", "item_name"]) if codes else []
	if groups:
		from excom.excom_ai.builder.resolve import item_group_tree

		tree = [g for grp in groups for g in item_group_tree(grp)]
		rows += frappe.get_all("Item", {"item_group": ["in", tree], "disabled": 0, "is_sales_item": 1}, ["name", "item_name"],
			limit=limit)
	return [{"item_code": r.name, "item_name": r.item_name} for r in rows][:limit]


def price_list_of(agent, item_code: str | None = None) -> str | None:
	group = frappe.db.get_value("Item", item_code, "item_group") if item_code else None
	for p in agent.products or []:
		if p.price_list and (p.item == item_code or (group and p.item_group == group)):
			return p.price_list
	return next((p.price_list for p in agent.products or [] if p.price_list), None)


def price(agent, party_type: str, party: str, lines: list[dict]) -> frappe._dict:
	"""ERPNext's own prices for these lines on the agent's price list: a Quotation built in memory
	(not saved), with the price list and pricing rules applied as for a person's quotation."""
	price_list = price_list_of(agent, lines[0]["item_code"] if lines else None)
	if not price_list:
		frappe.throw(_("The agent has no price list for these products."))
	currency = frappe.db.get_value("Price List", price_list, "currency")
	company = agent.company or frappe.defaults.get_global_default("company")
	q = frappe.get_doc({"doctype": "Quotation", "quotation_to": party_type, "party_name": party, "company": company,
		"selling_price_list": price_list, "currency": currency, "transaction_date": nowdate(),
		"valid_till": add_days(nowdate(), 30), "ignore_pricing_rule": 0,
		"items": [{"item_code": ln["item_code"], "qty": flt(ln["qty"]) or 1} for ln in lines]})
	company_currency = frappe.db.get_value("Company", company, "default_currency")
	if currency == company_currency:
		q.conversion_rate = 1
	else:
		rate = frappe.db.get_value("Currency Exchange", {"from_currency": currency, "to_currency": company_currency},
			"exchange_rate", order_by="date desc")
		if not rate:
			frappe.throw(_("No exchange rate from {0} to {1}: add a Currency Exchange so prices can be worked out.").format(
				currency, company_currency))
		q.conversion_rate = rate
	q.plc_conversion_rate = q.conversion_rate
	q.run_method("set_missing_values")
	q.run_method("calculate_taxes_and_totals")
	missing = [it.item_code for it in q.items if not flt(it.rate)]
	return frappe._dict(price_list=price_list, currency=currency, doc=q, missing=missing,
		lines=[frappe._dict(item_code=it.item_code, item_name=it.item_name, qty=it.qty, rate=flt(it.rate), amount=flt(it.amount))
			for it in q.items])


def price_lines(priced, language: str = "en") -> str:
	"""The price part of the reply, written by code from ERPNext's numbers."""
	per = {"es": "por pieza", "pt": "por peça", "fr": "la pièce"}.get(language[:2] if language else "", "each")
	lines = [f"{ln.item_name}: {fmt_money(ln.rate, currency=priced.currency)} {per} ({frappe.utils.cint(ln.qty)} pcs)"
		for ln in priced.lines if ln.rate]
	return "\n".join(lines)


@registry.task_kind("draft_quote", priority=registry.PRIORITY["write"], effort="write")
def draft_quote(task, agent) -> str:
	"""A buyer asked about prices: a Quotation draft and a priced reply, both for a person to approve."""
	thread = frappe.get_doc("Excom Thread", task.thread)
	email = frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email") if thread.omni_identity else None
	lead = frappe.db.get_value("Lead", {"email_id": email}) if email else None
	opp = frappe.db.get_value("Opportunity", {"party_name": lead}) if lead else None
	if not lead:
		return _("no lead for this conversation: a person quotes")
	history = frappe.get_all("Excom Message", {"thread": thread.name}, ["direction", "content_text"], order_by="creation desc", limit=6)
	convo = "\n".join(f"{'Buyer' if m.direction == 'Inbound' else 'Us'}: {m.content_text or ''}" for m in reversed(history))
	items = catalogue(agent)
	if not items:
		return _("the agent has no products to quote")
	r = runner.ai(task, agent, output_schema=ASK_SCHEMA,
		pack_instructions=_("Read which products and quantities the buyer asked about. Use only item codes from the "
			"catalogue. If no quantity is given, use the smallest sensible order (one carton). intro is one or two "
			"sentences in the buyer's language thanking them and introducing the prices, with no numbers at all."),
		prompt="Catalogue:\n" + "\n".join(f"{i['item_code']}: {i['item_name']}" for i in items)
			+ "\n\n" + untrusted(convo[:4000], _("the conversation")))
	allowed = {i["item_code"] for i in items}
	lines = [ln for ln in r.data.get("lines") or [] if ln.get("item_code") in allowed][:10]
	if not lines:
		return _("could not tell which products: a person quotes")
	priced = price(agent, "Lead", lead, lines)
	language = r.data.get("language") or ""
	body = "\n\n".join(filter(None, [r.data.get("intro"), price_lines(priced, language),
		_("My colleague will confirm the terms and the delivery to {0}.").format(r.data.get("destination") or _("you"))]))
	quote = proposals.propose(task, agent, "create_quotation", subject=("Opportunity", opp) if opp else ("Lead", lead),
		thread=thread.name, risk="Medium", payload={"party_type": "Lead", "party": lead, "opportunity": opp,
			"price_list": priced.price_list, "lines": [dict(ln) for ln in priced.lines]},
		preview_html="<br>".join(frappe.utils.escape_html(x) for x in price_lines(priced, language).splitlines()))
	from excom.excom_ai import checks

	results = checks.check(agent, email, "re", body, kind="Reply", account=thread.account, language=language or None)
	reply = proposals.propose(task, agent, "reply", policy_action="prices_and_terms", thread=thread.name,
		channel_account=thread.account, subject=("Excom Thread", thread.name), kind="Reply", to_email=email,
		payload={"subject": "", "body": body, "language": language, "intent": "negotiate", "quote_proposal": quote,
			"price_list": priced.price_list},
		preview_html="".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in body.split("\n\n")), checks=results,
		checks_ok=results["ok"], risk="Medium" if results["ok"] else "High")
	return _("{0} lines priced from {1}; quotation {2}, reply {3}").format(len(priced.lines), priced.price_list, quote, reply)


@registry.executor("create_quotation")
def create_quotation(proposal, agent):
	"""The approved quote becomes a draft Quotation (never submitted: a person sends it)."""
	p = frappe.parse_json(proposal.payload or "{}")
	priced = price(agent, p.get("party_type") or "Lead", p["party"], [{"item_code": ln["item_code"], "qty": ln["qty"]}
		for ln in p.get("lines") or []])
	q = priced.doc
	if p.get("opportunity"):
		q.opportunity = p["opportunity"]
	q.flags.ignore_permissions = True
	q.insert(ignore_permissions=True)
	return ("Quotation", q.name)


# ── quote follow-ups, reactivation, order updates ────────────────────────────


def agent_leads(agent_name: str) -> list[str]:
	return frappe.get_all("Lead", {"ai_agent": agent_name}, pluck="name") if frappe.get_meta("Lead").has_field("ai_agent") else []


@registry.task_kind("quote_follow_up", priority=registry.PRIORITY["write"], effort="write")
def quote_follow_up(task, agent) -> str:
	q = frappe.get_doc("Quotation", task.subject_name)
	lead = q.party_name if q.quotation_to == "Lead" else None
	email = frappe.db.get_value("Lead", lead, "email_id") if lead else None
	identity = frappe.db.get_value("Omni Identity", {"primary_email": email}) if email else None
	thread = frappe.db.get_value("Excom Thread", {"omni_identity": identity, "ai_agent": agent.name}) if identity else None
	if not thread:
		return _("no conversation to follow up on")
	schema = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False}
	r = runner.ai(task, agent, output_schema=schema,
		pack_instructions=_("Write a two-sentence follow-up on a quotation the buyer has not answered: calm, no pressure, one "
			"question, no prices or numbers, in the language of the conversation."),
		prompt=_("Quotation {0} sent on {1} for {2}.").format(q.name, q.transaction_date, ", ".join(i.item_name for i in q.items[:5])))
	from excom.excom_ai import checks

	results = checks.check(agent, email, "re", r.data["body"], kind="Reply", account=frappe.db.get_value("Excom Thread", thread, "account"))
	name = proposals.propose(task, agent, "reply", policy_action="follow_up_email", thread=thread, subject=("Quotation", q.name),
		kind="Reply", to_email=email, payload={"subject": "", "body": r.data["body"], "intent": "quote_follow_up"},
		checks=results, checks_ok=results["ok"])
	return _("follow-up on {0}: {1}").format(q.name, name)


@registry.task_kind("order_update", priority=registry.PRIORITY["send"], effort="classify")
def order_update(task, agent) -> str:
	"""Tell the buyer about their order, from the order's own facts (no model)."""
	so = frappe.get_doc("Sales Order", task.subject_name)
	params = frappe.parse_json(task.params or "{}") or {}
	customer = so.customer
	lead = frappe.db.get_value("Customer", customer, "lead_name")
	email = frappe.db.get_value("Lead", lead, "email_id") if lead else None
	identity = frappe.db.get_value("Omni Identity", {"primary_email": email}) if email else None
	thread = frappe.db.get_value("Excom Thread", {"omni_identity": identity, "ai_agent": agent.name}) if identity else None
	if not thread:
		return _("no conversation for {0}").format(so.name)
	what = params.get("event") or so.status
	body = _("Your order {0}: {1}.").format(so.po_no or so.name, {"delivered": _("it has been delivered"),
		"shipped": _("it is on its way"), "ready": _("it is ready to ship")}.get(what, what))
	name = proposals.propose(task, agent, "reply", intent="order_update", thread=thread, subject=("Sales Order", so.name),
		kind="Reply", to_email=email, payload={"subject": "", "body": body, "intent": "order_update"})
	return _("order update for {0}: {1}").format(so.name, name)


@registry.planner("sales")
def plan(agent) -> list[dict]:
	"""Deal work for a working agent: quote follow-ups, order updates, and once a month
	reactivation suggestions for the customers' own owners."""
	if agent.status not in ("Live", "Shadow"):
		return []
	specs = []
	leads = agent_leads(agent.name)
	if leads:
		since = add_days(nowdate(), -QUOTE_FOLLOW_UP_DAYS)
		for q in frappe.get_all("Quotation", {"quotation_to": "Lead", "party_name": ["in", leads], "status": ["in", ("Draft", "Open")],
				"transaction_date": ["<=", since], "docstatus": ["<", 2]}, pluck="name", limit=10):
			specs.append({"kind": "quote_follow_up", "subject": ("Quotation", q)})
		customers = frappe.get_all("Customer", {"lead_name": ["in", leads]}, pluck="name")
		for so in frappe.get_all("Sales Order", {"customer": ["in", customers or ["-"]], "docstatus": 1,
				"status": ["in", ("To Bill", "Completed")], "modified": [">=", add_days(nowdate(), -2)]}, ["name", "status"], limit=10):
			specs.append({"kind": "order_update", "subject": ("Sales Order", so.name), "dedupe_key": f"order:{so.name}:{so.status}",
				"params": {"event": "delivered" if so.status in ("To Bill", "Completed") else so.status}})
	if getdate(nowdate()).day == 1:
		specs.append({"kind": "reactivation", "dedupe_key": f"reactivation:{nowdate()[:7]}"})
	return specs


@registry.task_kind("reactivation", priority=registry.PRIORITY["refresh"], effort="classify")
def reactivation(task, agent) -> str:
	"""Customers in the agent's scope who have not ordered for 6 months: a check-in suggested to each
	customer's own account owner, as a ToDo. The agent never writes to a customer cold."""
	import json

	facts = json.loads(frappe.db.get_value("Excom AI Knowledge", agent.knowledge, "facts") or "{}") if agent.knowledge else {}
	customers = ((facts.get("customers") or {}).get("data") or {}).get("customers") or []
	cutoff = add_months(nowdate(), -DORMANT_MONTHS)
	made = 0
	for c in customers[:50]:
		last = frappe.db.get_value("Sales Invoice", {"customer": c, "docstatus": 1}, "posting_date", order_by="posting_date desc")
		if last and getdate(last) >= getdate(cutoff):
			continue
		owner = frappe.db.get_value("Customer", c, "account_manager") if frappe.get_meta("Customer").has_field("account_manager") else None
		owner = owner or agent.acting_owner or agent.owner_user
		proposals.propose(task, agent, "create_todo", subject=("Customer", c), risk="Low",
			payload={"allocated_to": owner, "text": _("Check in with {0}: last order {1}.").format(
				frappe.db.get_value("Customer", c, "customer_name") or c, last or _("never"))})
		made += 1
	return _("{0} check-ins suggested").format(made)
