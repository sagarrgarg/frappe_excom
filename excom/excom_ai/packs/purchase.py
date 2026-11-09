"""The Purchase pack (HLD-007 §8.3, Phase 2C): RFQs, supplier quotations, comparison, PO chasers and
supplier discovery, every message to a supplier under Review.

    draft_rfq           a Material Request (or items and quantities) and chosen suppliers: a draft
                        Request for Quotation (create_rfq) and one email per supplier (send_rfq). The
                        items, quantities and dates in the email are written by code; the model writes
                        only the polite words around them, and they may hold no number at all.
    rfq_follow_up       one reminder when a supplier has not answered for FOLLOW_UP_DAYS.
    respond (inbound)   a supplier answered on the RFQ conversation: the email and its PDF, XLSX or
                        CSV attachments (PDFs through ai_interface vision) are read, the offer is
                        extracted under a strict schema and mapped to the RFQ's lines, and a draft
                        Supplier Quotation is proposed (create_supplier_quotation). A line the model or
                        the mapping is not sure of is listed for a person and left out, never guessed;
                        any such line keeps the proposal waiting for a person.
    comparison          each quoted rate against the last Purchase Order and Purchase Invoice rates,
                        the buying price list and, when material_price_control is installed, its cost
                        rules: better, worse or about the same, in the proposal's preview.
    chase_po            a submitted Purchase Order whose schedule date is near or past with items still
                        to come: a polite po_chase email (Review by default, Auto allowed), sent in the
                        supplier's office hours; it stops when the goods are received.
    discover_suppliers  for the biggest spend groups of the last 12 months, candidates found with the
                        discovery tools into Excom AI Company rows, read with the website profiler and
                        the email finder; each becomes a create_supplier proposal that a person approves
                        (never Auto).

Never: create or submit a purchase order, submit an RFQ or a Supplier Quotation, or promise a
quantity or a price. Sends go out through the paced sender (sequencer.send_due), as the agent's user.
"""

from __future__ import annotations

import io
import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	add_months,
	cint,
	flt,
	fmt_money,
	get_datetime,
	getdate,
	now_datetime,
	nowdate,
)

from excom.excom_ai import checks, sequencer, tools
from excom.excom_ai.builder import geo, readiness
from excom.excom_ai.builder.recipes import fact, money, need, recipe
from excom.excom_ai.engine import notify, proposals, registry, runner, tasks
from excom.excom_ai.engine.prompts import untrusted

FOLLOW_UP_DAYS = 4  # a supplier who has not answered an RFQ for this long gets one reminder
FOLLOW_UP_UNTIL_DAYS = 30  # older RFQs are left to people
CHASE_AHEAD_DAYS = 2  # a PO is chased from this many days before its schedule date
CHASE_EVERY_DAYS = 7  # and again a week later while goods are still to come
CHASE_BACK_DAYS = 120  # orders late for longer than this are a person's problem, not a reminder's
MAX_SUPPLIERS = 6
MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
SAME_BAND_PCT = 1.0  # within 1% of the reference is "about the same"
DISCOVERY_GROUPS, DISCOVERY_PER_GROUP = 3, 5
DISCOVERY_SOURCES = ("serper", "osm")
SOURCE_LABEL = {"osm": "OSM", "serper": "Serper"}
PER_TICK = 10

DIGITS = re.compile(r"\d")
# The model's words may never commit us: no order, no accepted price, no promise.
COMMITMENT = re.compile(
	r"\b(we (will|shall|'ll|would like to|are going to) (order|buy|purchase|place)|(confirm|place|placing|accept)\w*"
	r" (the |an |our |this |your )?(order|offer|price|quotation)|purchase order|guarantee\w*|agreed|we accept)\b", re.I)
THREAT = re.compile(r"\b(cancel\w*|penalt\w*|legal|blacklist\w*|lawyer|court)\b", re.I)

RFQ_ASK = ("Please send your unit price for each item, any price breaks by quantity, the lead time, how long your offer "
	"is valid and your payment terms. This is a request for a quotation, not an order.")
FALLBACK = {
	"rfq": ("We would like your quotation for the items below.", "We look forward to your quotation by reply to this email."),
	"follow_up": ("We wanted to check whether our request for quotation below reached you.",
		"Your quotation by reply to this email would help us a great deal."),
	"chase": ("We are writing about our purchase order below, which is now due.",
		"We would appreciate an update on the dispatch."),
}
WORDING_RULES = """You write only the polite words of a short business email to a supplier; code adds the greeting,
the items, the quantities and the dates. Rules:
- intro: one or two sentences; closing: one sentence. Plain text.
- Never write any number, quantity, price, date, reference or item name.
- Never promise an order, a quantity, a price or a date, and never threaten.
- No greeting line, no sign-off and no name: the mail server adds the signature.
- Write in the language you are asked for (English when none is given)."""
WORDING_SCHEMA = {
	"type": "object",
	"properties": {"intro": {"type": "string"}, "closing": {"type": "string"}},
	"required": ["intro", "closing"],
	"additionalProperties": False,
}
WORDING_ASK = {
	"rfq": "We are asking this supplier for a quotation for some items.",
	"follow_up": "We asked this supplier for a quotation some days ago and have had no answer: a gentle reminder.",
	"chase": "Our purchase order with this supplier is due and some goods have not arrived: ask, politely, when they "
		"will be dispatched.",
}

QUOTE_RULES = """Read a supplier's reply to our request for quotation and extract their offer exactly as written.
- One entry in lines per price the supplier gives. A quantity break (another price from a minimum quantity) is its
  own entry with that min_qty; min_qty is 0 when no minimum is given.
- line_id is the id of our requested line the price is for, only when you are sure; otherwise "" and unclear true.
- rate is the unit price per the supplier's unit (uom), as written. Never compute, convert or guess a price: if it
  is not stated plainly, rate 0 and unclear true.
- unclear is true, with why, whenever the item, the unit, the currency or the price is in doubt.
- currency: the ISO code of the prices, "" if not stated. valid_till: YYYY-MM-DD when the offer states a date or a
  validity period (count from today), else "". lead_time_days: delivery lead time in days, 0 if not stated.
- payment_terms and notes: short, in the supplier's words (taxes, freight, minimum order, packing).
- is_quotation is false when the reply gives no prices (a question, an acknowledgement, a refusal)."""
QUOTE_SCHEMA = {
	"type": "object",
	"properties": {
		"is_quotation": {"type": "boolean"},
		"currency": {"type": "string"},
		"valid_till": {"type": "string"},
		"payment_terms": {"type": "string"},
		"lead_time_days": {"type": "integer"},
		"notes": {"type": "string"},
		"lines": {"type": "array", "items": {"type": "object", "properties": {
			"line_id": {"type": "string"},
			"supplier_text": {"type": "string"},
			"rate": {"type": "number"},
			"min_qty": {"type": "number"},
			"uom": {"type": "string"},
			"lead_time_days": {"type": "integer"},
			"unclear": {"type": "boolean"},
			"why": {"type": "string"},
		}, "required": ["line_id", "supplier_text", "rate", "min_qty", "uom", "lead_time_days", "unclear", "why"],
			"additionalProperties": False}},
	},
	"required": ["is_quotation", "currency", "valid_till", "payment_terms", "lead_time_days", "notes", "lines"],
	"additionalProperties": False,
}
VISION_PROMPT = ("Transcribe this supplier's quotation as plain text. Keep every table row on one line with its "
	"columns separated by ' | ': item, description, quantity, unit, unit price, currency, amount. Also copy any "
	"quantity breaks, lead time, validity, payment terms and notes. Do not summarise or compute anything.")
PO_REPLY_SCHEMA = {
	"type": "object",
	"properties": {"summary": {"type": "string"}, "dispatch_date": {"type": "string"}},
	"required": ["summary", "dispatch_date"],
	"additionalProperties": False,
}
UNITS = {"nos": "nos", "no": "nos", "pcs": "nos", "pc": "nos", "piece": "nos", "pieces": "nos", "unit": "nos",
	"units": "nos", "unit(s)": "nos", "each": "nos", "ea": "nos", "number": "nos", "kg": "kg", "kgs": "kg", "kilogram": "kg",
	"kilograms": "kg", "g": "gram", "gm": "gram", "gms": "gram", "gram": "gram", "grams": "gram", "l": "litre",
	"ltr": "litre", "litre": "litre", "liter": "litre", "litres": "litre", "m": "meter", "mtr": "meter", "meter": "meter",
	"metre": "meter"}


# ── small helpers ────────────────────────────────────────────────────────────


def company_of(agent) -> str:
	return agent.company or frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


def _qty(value) -> str:
	v = flt(value)
	return str(int(v)) if v == int(v) else f"{v:g}"


def _html(text: str) -> str:
	return "".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in (text or "").split("\n\n") if p.strip()).replace("\n", "<br>")


def _payload(proposal) -> dict:
	return frappe.parse_json(proposal.payload or "{}") or {}


def supplier_email(supplier: str, contact: str | None = None) -> str | None:
	"""The supplier's address: the contact named, else the supplier's primary contact, else any contact of theirs."""
	if contact:
		email = frappe.db.get_value("Contact", contact, "email_id")
		if email:
			return email.strip().lower()
	email = frappe.db.get_value("Supplier", supplier, "email_id")
	if email:
		return email.strip().lower()
	row = frappe.db.sql("""SELECT c.email_id FROM `tabContact` c JOIN `tabDynamic Link` dl ON dl.parent = c.name
		AND dl.parenttype = 'Contact' AND dl.link_doctype = 'Supplier' WHERE dl.link_name = %s AND IFNULL(c.email_id, '') != ''
		ORDER BY c.is_primary_contact DESC, c.creation ASC LIMIT 1""", supplier)
	return row[0][0].strip().lower() if row else None


def supplier_zone(supplier: str | None, agent) -> str:
	"""The supplier's country's main time zone, else the company's, else the site's."""
	country = frappe.db.get_value("Supplier", supplier, "country") if supplier else None
	country = country or frappe.db.get_value("Company", company_of(agent), "country")
	return (geo.main_timezone(country) if country else None) or frappe.utils.get_system_timezone() or "UTC"


def rfq_accounts(agent) -> list[str]:
	"""The agent's mailboxes that may start a conversation (the RFQ mailbox)."""
	return sequencer.outreach_accounts(agent)


def room(agent, account: str) -> int:
	"""Emails left today on this mailbox. RFQs and chasers go to suppliers the company already deals
	with, so the cold-mail warm-up ramp does not apply; the mailbox's daily cap still does."""
	from excom.excom_ai import mail

	row = next((r for r in agent.channels or [] if r.channel_account == account), None)
	if not row or row.paused:
		return 0
	return max(0, (cint(row.daily_cap) or 40) - mail.sent_today(account))


def supplier_identity(supplier: str, email: str) -> str:
	"""The supplier's person record for this address (made, linked to the supplier, when missing)."""
	name = frappe.db.get_value("Omni Identity", {"primary_email": email, "status": ["!=", "Merged"]})
	if name:
		return name
	doc = frappe.get_doc({"doctype": "Omni Identity", "display_name": frappe.db.get_value("Supplier", supplier,
		"supplier_name") or supplier, "primary_email": email, "status": "Active", "is_master": 1,
		"linked_entities": [{"linked_doctype": "Supplier", "linked_name": supplier, "role": "Primary Contact"}]})
	doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _cannot_ask(supplier: str) -> str | None:
	s = frappe.db.get_value("Supplier", supplier, ["disabled", "prevent_rfqs", "on_hold", "hold_type"], as_dict=True)
	if not s:
		return _("no such supplier")
	if s.disabled:
		return _("disabled")
	if s.prevent_rfqs:
		return _("RFQs blocked by the scorecard")
	if s.on_hold and s.hold_type in ("All", ""):
		return _("on hold")
	return None


def _wording(task, agent, kind: str, supplier: str, cache: dict | None = None) -> tuple[str, str, list[str]]:
	"""The polite words (intro, closing) from the model, once per language in a run (`cache`).
	Anything with a number, a commitment or a threat in it is replaced by fixed words, and the
	reason is returned for the checks."""
	language = frappe.db.get_value("Supplier", supplier, "language") or "en"
	if cache is not None and language in cache:
		return cache[language]
	r = runner.ai(task, agent, pack_instructions=WORDING_RULES, output_schema=WORDING_SCHEMA,
		prompt=f"{WORDING_ASK[kind]}\nLanguage: {language}.")
	intro, closing = (r.data.get("intro") or "").strip(), (r.data.get("closing") or "").strip()
	notes, out = [], []
	for text, fallback in zip((intro, closing), FALLBACK[kind], strict=True):
		text = _strip_sign_off(text)
		why = None
		if not text:
			why = _("no wording")
		elif DIGITS.search(text):
			why = _("the model wrote a number")
		elif COMMITMENT.search(text):
			why = _("the model made a commitment")
		elif THREAT.search(text):
			why = _("the model wrote a threat")
		if why:
			notes.append(f"{why}: {text[:80]}" if text else why)
			text = fallback
		out.append(text)
	result = (out[0], out[1], notes)
	if cache is not None:
		cache[language] = result
	return result


def _strip_sign_off(text: str) -> str:
	lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
	while lines and lines[-1].strip().lower().rstrip(",.!") in checks.SIGN_OFFS:
		lines.pop()
	return "\n".join(lines).strip()


def _check(agent, to: str, subject: str, body: str, account: str | None, lines: int, notes: list[str]) -> dict:
	"""The code checks (§12.5) for a message to a supplier, plus whether the model's words held."""
	results = checks.check(agent, to, subject, body, kind="Supplier", account=account, word_limit=150 + 25 * lines)
	results["wording"] = {"ok": not notes, "why": "; ".join(notes) or _("the model's words only")}
	results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	return results


# ── RFQ: lines and suppliers ─────────────────────────────────────────────────


def rfq_lines(material_request: str | None = None, items: list | None = None) -> list[dict]:
	"""What to ask for: the Material Request's lines still to order, or the items and quantities given."""
	from erpnext.stock.get_item_details import get_conversion_factor

	out = []
	if material_request:
		mr = frappe.get_doc("Material Request", material_request)
		for it in mr.items:
			qty = flt(it.qty) - flt(it.ordered_qty) / (flt(it.conversion_factor) or 1)
			if qty <= 0:
				continue
			out.append({"item_code": it.item_code, "item_name": it.item_name, "qty": qty, "uom": it.uom,
				"stock_uom": it.stock_uom, "conversion_factor": flt(it.conversion_factor) or 1,
				"schedule_date": str(it.schedule_date or mr.schedule_date or add_days(nowdate(), 14)),
				"warehouse": it.warehouse, "material_request": mr.name, "material_request_item": it.name,
				"description": it.description})
		return out
	for row in items or []:
		code, qty = row.get("item_code"), flt(row.get("qty"))
		item = frappe.db.get_value("Item", code, ["item_name", "stock_uom", "purchase_uom", "disabled", "is_purchase_item",
			"description"], as_dict=True) if code else None
		if not item or item.disabled or not item.is_purchase_item or qty <= 0:
			continue
		uom = row.get("uom") or item.purchase_uom or item.stock_uom
		factor = 1 if uom == item.stock_uom else flt(get_conversion_factor(code, uom).get("conversion_factor")) or 1
		out.append({"item_code": code, "item_name": item.item_name, "qty": qty, "uom": uom, "stock_uom": item.stock_uom,
			"conversion_factor": factor, "schedule_date": str(getdate(row.get("schedule_date") or add_days(nowdate(), 14))),
			"warehouse": row.get("warehouse"), "material_request": None, "material_request_item": None,
			"description": item.description})
	return out


def suggest_suppliers(item_codes: list[str], company: str, limit: int = 3) -> list[str]:
	"""Who to ask when nobody said: the items' default suppliers, then whoever they were bought from last."""
	found = []
	for s in frappe.get_all("Item Default", {"parent": ["in", item_codes or ["-"]], "company": company,
			"default_supplier": ["is", "set"]}, pluck="default_supplier"):
		if s not in found:
			found.append(s)
	for (s,) in frappe.db.sql("""SELECT po.supplier FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po
		ON po.name = poi.parent WHERE po.docstatus = 1 AND po.company = %s AND poi.item_code IN %s
		GROUP BY po.supplier ORDER BY MAX(po.transaction_date) DESC LIMIT 10""", (company, tuple(item_codes or ["-"]))):
		if s not in found:
			found.append(s)
	return [s for s in found if not _cannot_ask(s)][:limit]


def _lines_block(lines: list[dict]) -> str:
	return "\n".join(f"{i}. {ln['item_name']} ({ln['item_code']}): {_qty(ln['qty'])} {ln['uom']}, needed by "
		f"{frappe.utils.formatdate(ln['schedule_date'])}" for i, ln in enumerate(lines, 1))


def rfq_body(supplier: str, intro: str, closing: str, lines: list[dict]) -> str:
	"""The RFQ email: the items, quantities and dates written by code, the model's words around them."""
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	return f"Dear {name},\n\n{intro}\n\n{_lines_block(lines)}\n\n{RFQ_ASK}\n\n{closing}"


def _rfq_preview(lines: list[dict], suppliers: list[dict], skipped: list[str]) -> str:
	rows = "".join(f"<tr><td>{frappe.utils.escape_html(ln['item_name'])} ({frappe.utils.escape_html(ln['item_code'])})</td>"
		f"<td>{_qty(ln['qty'])} {frappe.utils.escape_html(ln['uom'])}</td><td>{ln['schedule_date']}</td></tr>" for ln in lines)
	who = "".join(f"<li>{frappe.utils.escape_html(s['supplier'])}: {frappe.utils.escape_html(s.get('email_id') or _('no email, a person sends it'))}"
		"</li>" for s in suppliers)
	out = (f"<p><b>{_('Request for Quotation (draft, not submitted)')}</b></p><table class='table table-bordered'>"
		f"<tr><th>{_('Item')}</th><th>{_('Qty')}</th><th>{_('Needed by')}</th></tr>{rows}</table><ul>{who}</ul>")
	if skipped:
		out += "<p>" + _("Not asked: {0}").format(frappe.utils.escape_html("; ".join(skipped))) + "</p>"
	return out


@frappe.whitelist(methods=["POST"])
def request_rfq(agent: str, items=None, suppliers=None, material_request: str | None = None) -> str:
	"""A person asks the agent for an RFQ: items [{item_code, qty, uom?, schedule_date?}] or a Material
	Request, and the suppliers to ask (else the items' usual suppliers). Returns the task."""
	doc = frappe.get_doc("Excom AI Agent", agent)
	if not proposals.may_decide(doc, frappe.session.user):
		frappe.throw(_("Only this agent's owner, backup, reviewers or an Excom Admin give it work."), frappe.PermissionError)
	items, suppliers = frappe.parse_json(items) if isinstance(items, str) else items, frappe.parse_json(suppliers) \
		if isinstance(suppliers, str) else suppliers
	if not items and not material_request:
		frappe.throw(_("Say what to ask for: items and quantities, or a Material Request."))
	key = f"draft_rfq:{material_request or frappe.generate_hash(length=10)}"
	return tasks.add(doc.name, "draft_rfq", subject=("Material Request", material_request) if material_request else None,
		dedupe_key=key, params={"material_request": material_request, "items": items or [], "suppliers": suppliers or []})


@registry.task_kind("draft_rfq", priority=registry.PRIORITY["write"], effort="write")
def draft_rfq(task, agent) -> str:
	"""A draft RFQ (create_rfq) and one email per supplier (send_rfq, Review)."""
	params = frappe.parse_json(task.params or "{}") or {}
	mr = params.get("material_request") or (task.subject_name if task.subject_doctype == "Material Request" else None)
	company = (frappe.db.get_value("Material Request", mr, "company") if mr else None) or company_of(agent)
	lines = rfq_lines(mr, params.get("items"))
	if not lines:
		return _("nothing left to ask a price for")
	wanted = params.get("suppliers") or suggest_suppliers([ln["item_code"] for ln in lines], company)
	rows, skipped = [], []
	for s in list(dict.fromkeys(wanted))[:MAX_SUPPLIERS]:
		why = _cannot_ask(s)
		if why:
			skipped.append(f"{s}: {why}")
			continue
		rows.append({"supplier": s, "email_id": supplier_email(s)})
	if not rows:
		proposals.propose(task, agent, "create_todo", subject=("Material Request", mr) if mr else None, risk="Low",
			payload={"text": _("Choose suppliers to ask for prices{0}.").format(f" ({mr})" if mr else "")
				+ (" " + "; ".join(skipped) if skipped else "")})
		return _("no supplier to ask; a person chooses")
	schedule = max(getdate(nowdate()), min(getdate(ln["schedule_date"]) for ln in lines))
	rfq_prop = proposals.propose(task, agent, "create_rfq", subject=("Material Request", mr) if mr else None, risk="Low",
		payload={"company": company, "material_request": mr, "schedule_date": str(schedule), "lines": lines, "suppliers": rows},
		preview_html=_rfq_preview(lines, rows, skipped))
	if not rfq_prop:
		return _("RFQ drafts are switched off for this agent")
	rfq = _made(rfq_prop)
	account = next(iter(rfq_accounts(agent)), None)
	made, by_hand, words = [], [], {}
	for row in rows:
		if not row["email_id"]:
			by_hand.append(row["supplier"])
			continue
		intro, closing, notes = _wording(task, agent, "rfq", row["supplier"], words)
		body = rfq_body(row["supplier"], intro, closing, lines)
		subject = _("Request for quotation") + (f" {rfq}" if rfq else "")
		results = _check(agent, row["email_id"], subject, body, account, len(lines), notes)
		name = proposals.propose(task, agent, "send_rfq", subject=("Supplier", row["supplier"]), channel_account=account,
			kind="Reply", to_email=row["email_id"], risk="Medium" if results["ok"] else "High", checks=results,
			checks_ok=results["ok"], preview_html=_html(body),
			payload={"subject": "", "body": body, "supplier": row["supplier"], "rfq_proposal": rfq_prop, "rfq": rfq,
				"follow_up": 0})
		made.append(name)
	out = _("RFQ {0}; {1} emails proposed").format(rfq or rfq_prop, len([m for m in made if m]))
	if by_hand:
		out += "; " + _("no email for {0}: a person sends it").format(", ".join(by_hand))
	return out


def _made(proposal_name: str | None) -> str | None:
	"""The record an executed proposal made (the RFQ), or None while it waits."""
	if not proposal_name:
		return None
	row = frappe.db.get_value("Excom AI Proposal", proposal_name, ["status", "executed_name"], as_dict=True)
	return row.executed_name if row and row.status == "Done" else None


def rfq_of(payload: dict) -> str | None:
	return payload.get("rfq") or _made(payload.get("rfq_proposal"))


@registry.executor("create_rfq")
def create_rfq(proposal, agent):
	"""The approved RFQ becomes a draft Request for Quotation. It is never submitted here (a person
	does that), and its suppliers are marked not to be emailed by ERPNext: the agent writes to them."""
	p = _payload(proposal)
	lines = p.get("lines") or []
	if not lines or not p.get("suppliers"):
		frappe.throw(_("An RFQ needs items and suppliers."))
	today = getdate(nowdate())
	rfq = frappe.get_doc({
		"doctype": "Request for Quotation", "company": p.get("company") or company_of(agent), "transaction_date": nowdate(),
		"schedule_date": str(max(today, getdate(p.get("schedule_date") or today))),
		"subject": _("Request for Quotation"),
		"message_for_supplier": _("Please send your unit price for each item, any price breaks by quantity, the lead time, "
			"the validity of your offer and your payment terms."),
		"suppliers": [{"supplier": s["supplier"], "email_id": s.get("email_id"), "send_email": 0} for s in p["suppliers"]],
		"items": [{"item_code": ln["item_code"], "item_name": ln.get("item_name"), "description": ln.get("description")
			or ln.get("item_name"), "qty": flt(ln["qty"]), "uom": ln["uom"], "stock_uom": ln.get("stock_uom") or ln["uom"],
			"conversion_factor": flt(ln.get("conversion_factor")) or 1,
			"schedule_date": str(max(today, getdate(ln.get("schedule_date") or today))), "warehouse": ln.get("warehouse"),
			"material_request": ln.get("material_request"), "material_request_item": ln.get("material_request_item")}
			for ln in lines],
	})
	rfq.flags.ignore_permissions = True
	rfq.insert(ignore_permissions=True)
	return ("Request for Quotation", rfq.name)


# ── sending (RFQs, reminders, chasers) ───────────────────────────────────────


def _send(proposal, agent, supplier: str, subject: str, body: str, in_reply_to: str = "") -> tuple[str, str]:
	"""Send one approved email to a supplier now, or Defer to their office hours or tomorrow's room."""
	from excom.excom.channels.email.outbound import send_email_reply
	from excom.excom.services.thread_service import upsert_thread
	from excom.excom_ai.writer import compose

	zone = supplier_zone(supplier, agent)
	now = now_datetime()
	if not sequencer.in_window(now, zone):
		raise registry.Defer(sequencer.next_window(now, zone), _("outside the supplier's office hours ({0})").format(zone))
	account = proposal.channel_account or next(iter(rfq_accounts(agent)), None)
	if not account:
		raise registry.Cancel(_("the agent has no mailbox to write from"))
	if room(agent, account) <= 0:
		raise registry.Defer(sequencer.next_window(add_days(now, 1).replace(hour=0, minute=0), zone),
			_("{0} has sent its share for today").format(account))
	to = (proposal.to_email or "").strip().lower()
	if not to:
		raise registry.Cancel(_("no address for {0}").format(supplier))
	identity = supplier_identity(supplier, to)
	thread = proposal.thread or upsert_thread(identity, "email", account)
	html = compose(agent, body, None, identity, "Reply")
	message = send_email_reply(thread, to=to, subject=subject, body_html=html, in_reply_to_gmail_id=in_reply_to or "")
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime(), "thread": thread},
		update_modified=False)
	# the supplier's answer comes back to this agent (engine.inbound reads the thread's agent)
	frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle"}, update_modified=False)
	return message, thread


def _answered_since(thread: str | None, since) -> bool:
	return bool(thread and since and frappe.db.exists("Excom Message", {"thread": thread, "direction": "Inbound",
		"creation": [">", get_datetime(since)]}))


def _quoted(rfq: str | None, supplier: str) -> bool:
	"""A Supplier Quotation from this supplier for this RFQ exists (any state but cancelled)."""
	if not rfq:
		return False
	return bool(frappe.db.sql("""SELECT 1 FROM `tabSupplier Quotation Item` i JOIN `tabSupplier Quotation` sq
		ON sq.name = i.parent WHERE i.request_for_quotation = %s AND sq.supplier = %s AND sq.docstatus < 2 LIMIT 1""",
		(rfq, supplier)))


@registry.executor("send_rfq")
def send_rfq(proposal, agent):
	"""Send an approved RFQ email (or its one reminder) to the supplier, as the agent's own user."""
	p = _payload(proposal)
	supplier = p.get("supplier") or proposal.subject_name
	rfq = rfq_of(p)
	if not rfq:
		made = frappe.db.get_value("Excom AI Proposal", p.get("rfq_proposal"), "status") if p.get("rfq_proposal") else None
		if not made or made in proposals.RELEASED:
			raise registry.Cancel(_("the RFQ draft was not made ({0})").format(made or _("none")))
		raise registry.Defer(now_datetime() + timedelta(hours=1),
			_("waiting for the RFQ draft to be approved"))
	if frappe.db.get_value("Request for Quotation", rfq, "docstatus") == 2:
		raise registry.Cancel(_("{0} was cancelled").format(rfq))
	if _cannot_ask(supplier):
		raise registry.Cancel(_("{0} cannot be asked: {1}").format(supplier, _cannot_ask(supplier)))
	in_reply_to, subject = "", p.get("subject") or _("Request for quotation {0}").format(rfq)
	if cint(p.get("follow_up")):
		parent = frappe.db.get_value("Excom AI Proposal", p.get("parent"), ["thread", "sent_on", "message"], as_dict=True)
		if not parent or not parent.sent_on:
			raise registry.Cancel(_("the first email was never sent"))
		if _answered_since(parent.thread, parent.sent_on) or _quoted(rfq, supplier):
			raise registry.Cancel(_("the supplier has answered"))
		in_reply_to = frappe.db.get_value("Excom Message", parent.message, "provider_message_id") or ""
	message, _thread = _send(proposal, agent, supplier, subject, p.get("body") or "", in_reply_to)
	if not cint(p.get("follow_up")) and frappe.get_meta("Request for Quotation Supplier").has_field("email_sent"):
		frappe.db.set_value("Request for Quotation Supplier", {"parent": rfq, "supplier": supplier}, "email_sent", 1,
			update_modified=False)
	return ("Excom Message", message)


@registry.task_kind("rfq_follow_up", priority=registry.PRIORITY["write"], effort="write")
def rfq_follow_up(task, agent) -> str:
	"""One reminder to a supplier who has not answered an RFQ."""
	first = frappe.get_doc("Excom AI Proposal", task.subject_name)
	p = _payload(first)
	supplier, rfq = p.get("supplier") or first.subject_name, rfq_of(p)
	if not rfq or _answered_since(first.thread, first.sent_on) or _quoted(rfq, supplier):
		return _("answered; no reminder")
	doc = frappe.get_doc("Request for Quotation", rfq)
	if doc.docstatus == 2:
		return _("{0} was cancelled").format(rfq)
	lines = [{"item_code": it.item_code, "item_name": it.item_name, "qty": it.qty, "uom": it.uom, "schedule_date": str(it.schedule_date)}
		for it in doc.items]
	intro, closing, notes = _wording(task, agent, "follow_up", supplier)
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	body = f"Dear {name},\n\n{intro}\n\n{_lines_block(lines)}\n\n{RFQ_ASK}\n\n{closing}"
	subject = "Re: " + (p.get("subject") or _("Request for quotation {0}").format(rfq))
	results = _check(agent, first.to_email, subject, body, first.channel_account, len(lines), notes)
	made = proposals.propose(task, agent, "send_rfq", subject=("Excom AI Proposal", first.name), thread=first.thread,
		channel_account=first.channel_account, kind="Reply", to_email=first.to_email, checks=results, checks_ok=results["ok"],
		risk="Low" if results["ok"] else "High", preview_html=_html(body),
		payload={"subject": subject, "body": body, "supplier": supplier, "rfq": rfq, "follow_up": 1, "parent": first.name})
	return _("reminder for {0} to {1}: {2}").format(rfq, supplier, made)


# ── replies: supplier quotations ─────────────────────────────────────────────


def read_email(message: str) -> frappe._dict:
	"""One inbound message: its body and its attachments' list, from the mailbox (read, not stored)."""
	m = frappe.db.get_value("Excom Message", message, ["name", "channel", "content_text", "provider_message_id", "account"],
		as_dict=True)
	out = frappe._dict(message=message, body=(m.content_text if m else "") or "", attachments=[], account=None, raw=None)
	if not m or m.channel != "email" or not m.provider_message_id:
		return out
	try:
		from excom.excom.channels.email import providers as P

		account = frappe.get_doc("Excom Channel Account", m.account)
		raw = P.raw_id(account, m.provider_message_id)
		full = P.get(account).fetch_full(account, raw) or {}
		out.update(body=full.get("body_text") or frappe.utils.strip_html_tags(full.get("body_html") or "") or out.body,
			attachments=list(full.get("attachments") or []), account=account, raw=raw)
	except Exception:
		frappe.log_error(title="Excom AI: could not read a supplier's email", message=frappe.get_traceback())
	return out


def _xlsx_text(data: bytes, max_rows: int = 300) -> str:
	from openpyxl import load_workbook

	wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
	out = []
	for ws in wb.worksheets:
		out.append(f"[{ws.title}]")
		for row in ws.iter_rows(values_only=True):
			cells = [str(c).strip() for c in row if c not in (None, "")]
			if cells:
				out.append(" | ".join(cells))
			if len(out) >= max_rows:
				return "\n".join(out)
	return "\n".join(out)


def attachment_texts(task, agent, read, rfq: str) -> list[tuple[str, str]]:
	"""(filename, text) for each readable attachment: spreadsheets and CSV by code, PDFs and images by
	ai_interface vision. A PDF or image is kept on the RFQ as a private file, for the person who checks."""
	from excom.excom.channels.email import providers as P

	out = []
	if not read.attachments or not read.account:
		return out
	for att in read.attachments[:MAX_ATTACHMENTS]:
		name = att.get("filename") or "attachment"
		ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
		mime = (att.get("mimeType") or "").lower()
		if cint(att.get("size")) > MAX_ATTACHMENT_BYTES:
			out.append((name, _("(too large to read)")))
			continue
		kind = ("xlsx" if ext in ("xlsx", "xlsm") else "csv" if ext in ("csv", "txt") or mime.startswith("text/")
			else "vision" if ext in ("pdf", "png", "jpg", "jpeg") or mime == "application/pdf" or mime.startswith("image/")
			else None)
		if not kind:
			continue
		try:
			data = P.get(read.account).fetch_attachment(read.account, read.raw, att.get("attachmentId"))
			if kind == "xlsx":
				text = _xlsx_text(data)
			elif kind == "csv":
				text = data.decode("utf-8", "replace")
			else:
				from ai_interface.services import vision

				f = frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1, "content": data,
					"attached_to_doctype": "Request for Quotation", "attached_to_name": rfq}).insert(ignore_permissions=True)
				text = vision.extract(f.file_url, VISION_PROMPT, sync=True, calling_app="excom", reference_doctype="Excom AI Task",
					reference_name=task.name, action="read_quotation", user=agent.service_user,
					budget_scope=f"Excom AI Agent:{agent.name}",
					budget_caps={"daily": flt(agent.daily_ai_budget), "monthly": flt(agent.monthly_ai_budget), "action": "Block"})
			out.append((name, (text or "")[:12000]))
		except Exception as e:
			if runner._budget_refusal(e):
				raise
			frappe.log_error(title=f"Excom AI: could not read the attachment {name}", message=frappe.get_traceback())
			out.append((name, _("(could not be read)")))
	return out


def _context(thread: str, agent, text: str) -> frappe._dict | None:
	"""What the supplier is answering: the RFQ or the purchase order last written about on this
	conversation, or the one their reply names."""
	sent = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "thread": thread, "status": "Sent",
		"action": ["in", ("send_rfq", "po_chase")]}, ["name", "action", "subject_name", "payload"], order_by="sent_on desc",
		limit=20)
	rows = []
	for s in sent:
		p = frappe.parse_json(s.payload or "{}") or {}
		ref = rfq_of(p) if s.action == "send_rfq" else s.subject_name
		if ref:
			rows.append(frappe._dict(kind="rfq" if s.action == "send_rfq" else "po", ref=ref, proposal=s.name,
				supplier=p.get("supplier") or (frappe.db.get_value("Purchase Order", ref, "supplier") if s.action == "po_chase"
					else None)))
	low = (text or "").lower()
	return next((r for r in rows if r.ref.lower() in low), None) or (rows[0] if rows else None)


def same_uom(theirs: str, ours: str, stock_uom: str | None = None) -> bool:
	t = (theirs or "").strip().lower().rstrip(".")
	if not t:
		return True
	return any(UNITS.get(t, t) == UNITS.get((u or "").strip().lower(), (u or "").strip().lower()) for u in (ours, stock_uom) if u)


def map_quote(rfq, data: dict) -> frappe._dict:
	"""The supplier's prices on the RFQ's lines. The rate for our quantity is the break that applies
	to it; anything in doubt is listed as unclear and left out (a person decides, nothing is guessed)."""
	rows = {it.name: it for it in rfq.items}
	by_text = {}
	for it in rfq.items:
		for key in (it.item_code, it.item_name):
			by_text.setdefault((key or "").strip().lower(), it.name)
	offers, unclear = {}, []
	for ln in data.get("lines") or []:
		lid = ln.get("line_id") if ln.get("line_id") in rows else by_text.get((ln.get("supplier_text") or "").strip().lower())
		row = rows.get(lid)
		why = None
		if ln.get("unclear"):
			why = ln.get("why") or _("the reader was not sure")
		elif not row:
			why = _("not one of the items asked for")
		elif flt(ln.get("rate")) <= 0:
			why = _("no price stated")
		elif not same_uom(ln.get("uom"), row.uom, row.stock_uom):
			why = _("priced per {0}, we asked per {1}").format(ln.get("uom"), row.uom)
		if why:
			unclear.append({"supplier_text": ln.get("supplier_text") or "", "item_code": row.item_code if row else "",
				"rate": flt(ln.get("rate")), "why": why})
			continue
		offers.setdefault(lid, []).append(ln)
	lines = []
	for lid, offs in offers.items():
		row = rows[lid]
		breaks = sorted({(flt(o.get("min_qty")), flt(o.get("rate"))) for o in offs})
		fits = [b for b in breaks if b[0] <= flt(row.qty)]
		if not fits:
			unclear.append({"supplier_text": offs[0].get("supplier_text") or row.item_name, "item_code": row.item_code,
				"rate": breaks[0][1], "why": _("quoted only from {0} {1}; we asked for {2}").format(_qty(breaks[0][0]), row.uom,
					_qty(row.qty))})
			continue
		top = max(b[0] for b in fits)
		rates = {b[1] for b in fits if b[0] == top}
		if len(rates) > 1:
			unclear.append({"supplier_text": offs[0].get("supplier_text") or row.item_name, "item_code": row.item_code,
				"rate": min(rates), "why": _("two prices for the same quantity: {0}").format(", ".join(map(str, sorted(rates))))})
			continue
		lead = next((cint(o.get("lead_time_days")) for o in offs if cint(o.get("lead_time_days"))), 0) or cint(data.get("lead_time_days"))
		lines.append({"rfq_item": lid, "item_code": row.item_code, "item_name": row.item_name, "qty": flt(row.qty),
			"uom": row.uom, "conversion_factor": flt(row.conversion_factor) or 1, "rate": rates.pop(), "lead_time_days": lead,
			"breaks": [{"min_qty": b[0], "rate": b[1]} for b in breaks]})
	answered = {ln["rfq_item"] for ln in lines} | {u["item_code"] for u in unclear}
	not_quoted = [it.item_code for it in rfq.items if it.name not in answered and it.item_code not in answered]
	return frappe._dict(lines=lines, unclear=unclear, not_quoted=not_quoted)


def exchange_rate(currency: str, company_currency: str) -> float | None:
	if not currency or currency == company_currency:
		return 1.0
	rate = frappe.db.get_value("Currency Exchange", {"from_currency": currency, "to_currency": company_currency},
		"exchange_rate", order_by="date desc")
	return flt(rate) or None


def _valid_till(value: str) -> str | None:
	try:
		day = getdate(value) if value else None
	except Exception:
		return None
	return str(day) if day and day >= getdate(nowdate()) else None


@registry.responder("purchase")
def respond(task, agent) -> str:
	"""A supplier wrote on a conversation the agent started: a quotation becomes a Supplier
	Quotation proposal; an answer about a late order becomes a note on the order; anything else
	goes to a person."""
	from excom.excom_ai.responder import clean, rule_intent

	params = frappe.parse_json(task.params or "{}") or {}
	thread = frappe.get_doc("Excom Thread", task.thread)
	inbound = frappe.get_all("Excom Message", {"thread": thread.name, "direction": "Inbound"}, ["name", "creation"],
		order_by="creation desc", limit=5)
	last_out = frappe.db.get_value("Excom Message", {"thread": thread.name, "direction": "Outbound"}, "creation",
		order_by="creation desc")
	new = [m.name for m in inbound if not last_out or m.creation > last_out] or ([params["message"]] if params.get("message") else [])
	reads = [read_email(m) for m in reversed(new)]
	text = clean("\n\n".join(r.body for r in reads))
	if not text and not any(r.attachments for r in reads):
		return _("nothing to read")
	hit = rule_intent(text)
	if hit == "unsubscribe":
		from excom.excom_ai.mail import opt_out

		if thread.omni_identity:
			opt_out(thread.omni_identity, source="ai_reply_opt_out")
		frappe.db.set_value("Excom Thread", thread.name, "ai_mode", "Hold", update_modified=False)
		return _("asked not to be written to; nothing sent")
	if hit == "escalate":
		from excom.excom_ai.engine.inbound import hand_to_person

		hand_to_person(thread.name, agent, text[:500], params.get("message"), reason="escalate")
		return _("escalate: handed to a person")
	ctx = _context(thread.name, agent, text)
	if not ctx:
		to_person(thread.name, agent, _("a supplier wrote about something the agent did not ask"), text)
		return _("not about an RFQ or an order: left to a person")
	if ctx.kind == "po":
		return _po_reply(task, agent, thread, text, ctx)
	return _quotation(task, agent, thread, text, reads, ctx)


def to_person(thread: str, agent, why: str, text: str = "") -> None:
	"""The conversation waits for a person, who is told why."""
	frappe.db.set_value("Excom Thread", thread, "ai_mode", "Needs person", update_modified=False)
	frappe.get_doc("Excom Thread", thread).add_comment("Comment", _("{0} (AI) handed this conversation to a person: {1}.").format(
		agent.agent_name, why))
	notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user],
		_("A supplier needs you on a conversation {0} (AI) had").format(agent.agent_name),
		message=frappe.utils.escape_html((text or why)[:500]), doctype="Excom Thread", name=thread)


def _quotation(task, agent, thread, text: str, reads: list, ctx) -> str:
	rfq = frappe.get_doc("Request for Quotation", ctx.ref)
	supplier = ctx.supplier or frappe.db.get_value("Excom AI Proposal", ctx.proposal, "subject_name")
	files = [t for r in reads for t in attachment_texts(task, agent, r, rfq.name)]
	asked = "\n".join(f"{it.name} | {it.item_code} | {it.item_name} | {_qty(it.qty)} | {it.uom}" for it in rfq.items)
	prompt = (_("Today is {0}. Our request for quotation {1} to {2} asked for (line_id | item | name | qty | uom):").format(
		nowdate(), rfq.name, frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier) + f"\n{asked}\n\n"
		+ untrusted(text[:8000], _("the supplier's email"))
		+ "".join("\n\n" + untrusted(body, _("attachment {0}").format(name)) for name, body in files))
	r = runner.ai(task, agent, pack_instructions=QUOTE_RULES, output_schema=QUOTE_SCHEMA, prompt=prompt)
	data = dict(r.data)
	quote = map_quote(rfq, data)
	if not quote.lines:
		why = _("the supplier replied without prices") if not data.get("is_quotation") and not quote.unclear else \
			_("the supplier's prices could not be read with certainty: {0}").format(
				"; ".join(f"{u['supplier_text'] or u['item_code']}: {u['why']}" for u in quote.unclear[:5]))
		to_person(thread.name, agent, why, text)
		return why
	company_currency = frappe.db.get_value("Company", rfq.company, "default_currency")
	currency = (data.get("currency") or "").strip().upper() or frappe.db.get_value("Supplier", supplier, "default_currency") \
		or company_currency
	known = bool(frappe.db.exists("Currency", currency))
	rate = exchange_rate(currency, company_currency) if known else None
	results = {
		"lines": {"ok": not quote.unclear, "why": _("{0} lines read").format(len(quote.lines)) if not quote.unclear else
			_("{0} lines unclear, left for a person").format(len(quote.unclear))},
		"currency": {"ok": known, "why": currency if known else _("unknown currency {0}").format(currency)},
		"exchange": {"ok": bool(rate), "why": _("1 {0} = {1} {2}").format(currency, rate, company_currency) if rate else
			_("no exchange rate from {0} to {1}").format(currency, company_currency)},
	}
	results["ok"] = all(v["ok"] for v in results.values())
	compared = [dict(ln, **{"compare": compare(ln["item_code"], flt(ln["rate"]) * (rate or 1) / (flt(ln["conversion_factor"]) or 1),
		rfq.company)}) for ln in quote.lines]
	payload = {"rfq": rfq.name, "supplier": supplier, "currency": currency, "conversion_rate": rate,
		"valid_till": _valid_till(data.get("valid_till")), "payment_terms": (data.get("payment_terms") or "")[:500],
		"notes": (data.get("notes") or "")[:1000], "lines": quote.lines, "unclear": quote.unclear, "not_quoted": quote.not_quoted,
		"message": reads[-1].message if reads else None}
	name = proposals.propose(task, agent, "create_supplier_quotation", subject=("Request for Quotation", rfq.name),
		thread=thread.name, payload=payload, checks=results, checks_ok=results["ok"], risk="Medium" if results["ok"] else "High",
		preview_html=quote_preview(payload, compared, company_currency))
	return _("quotation from {0} for {1}: {2} lines, {3} unclear ({4})").format(supplier, rfq.name, len(quote.lines),
		len(quote.unclear), name)


def quote_preview(p: dict, compared: list[dict], company_currency: str) -> str:
	esc = frappe.utils.escape_html
	head = (f"<p><b>{_('Supplier Quotation (draft)')}</b>: {esc(p['supplier'])}, {_('for')} {esc(p['rfq'])}, "
		f"{esc(p['currency'])}{', ' + _('valid till') + ' ' + p['valid_till'] if p.get('valid_till') else ''}</p>")
	if p.get("payment_terms") or p.get("notes"):
		head += f"<p>{esc(' · '.join(filter(None, (p.get('payment_terms'), p.get('notes')))))}</p>"
	rows = ""
	for ln in compared:
		c = ln["compare"]
		breaks = ", ".join(f"{_qty(b['min_qty'])}+: {b['rate']}" for b in ln.get("breaks") or [] if b["min_qty"]) or ""
		rows += (f"<tr><td>{esc(ln['item_name'])} ({esc(ln['item_code'])})</td><td>{_qty(ln['qty'])} {esc(ln['uom'])}</td>"
			f"<td>{fmt_money(ln['rate'], currency=p['currency'])}</td><td>{esc(breaks)}</td>"
			f"<td>{cint(ln.get('lead_time_days')) or ''}</td><td><b>{esc(c['verdict'])}</b> {esc(c['note'])}</td></tr>")
	out = head + (f"<table class='table table-bordered'><tr><th>{_('Item')}</th><th>{_('Qty')}</th><th>{_('Rate')}</th>"
		f"<th>{_('Breaks')}</th><th>{_('Lead days')}</th><th>{_('Against history')}</th></tr>{rows}</table>")
	if p.get("unclear"):
		out += f"<p><b>{_('Unclear, for a person (left out of the draft)')}</b></p><ul>" + "".join(
			f"<li>{esc(u['supplier_text'] or u['item_code'])}: {esc(u['why'])}</li>" for u in p["unclear"]) + "</ul>"
	if p.get("not_quoted"):
		out += f"<p>{_('Not quoted')}: {esc(', '.join(p['not_quoted']))}</p>"
	return out


@registry.executor("create_supplier_quotation")
def create_supplier_quotation(proposal, agent):
	"""The approved quotation becomes a draft Supplier Quotation, linked to the RFQ and its lines.
	Quantities, units and warehouses come from the RFQ; only the rates and lead times are the
	supplier's. It is never submitted here."""
	p = _payload(proposal)
	rfq = frappe.get_doc("Request for Quotation", p["rfq"])
	if rfq.docstatus == 2:
		frappe.throw(_("{0} was cancelled.").format(rfq.name))
	rows = {it.name: it for it in rfq.items}
	lines = [ln for ln in p.get("lines") or [] if ln.get("rfq_item") in rows and flt(ln.get("rate")) > 0]
	if not lines:
		frappe.throw(_("No clear line to put on a Supplier Quotation."))
	company_currency = frappe.db.get_value("Company", rfq.company, "default_currency")
	currency = p.get("currency") or company_currency
	rate = flt(p.get("conversion_rate")) or exchange_rate(currency, company_currency)
	if not rate:
		frappe.throw(_("No exchange rate from {0} to {1}: add a Currency Exchange first.").format(currency, company_currency))
	terms = [x for x in (p.get("payment_terms"), p.get("notes")) if x]
	breaks = [ln["item_code"] + ": " + ", ".join(f"{_qty(b['min_qty'])}+ at {b['rate']}" for b in ln["breaks"])
		for ln in lines if len(ln.get("breaks") or []) > 1]
	if breaks:
		terms.append(_("Quantity breaks: {0}").format("; ".join(breaks)))
	sq = frappe.get_doc({
		"doctype": "Supplier Quotation", "supplier": p["supplier"], "company": rfq.company, "transaction_date": nowdate(),
		"currency": currency, "conversion_rate": rate, "valid_till": _valid_till(p.get("valid_till")), "ignore_pricing_rule": 1,
		"terms": "<br>".join(frappe.utils.escape_html(t) for t in terms) or None,
		"items": [{"item_code": rows[ln["rfq_item"]].item_code, "item_name": rows[ln["rfq_item"]].item_name,
			"description": rows[ln["rfq_item"]].description, "qty": flt(rows[ln["rfq_item"]].qty), "uom": rows[ln["rfq_item"]].uom,
			"stock_uom": rows[ln["rfq_item"]].stock_uom, "conversion_factor": flt(rows[ln["rfq_item"]].conversion_factor) or 1,
			"warehouse": rows[ln["rfq_item"]].warehouse, "material_request": rows[ln["rfq_item"]].material_request,
			"material_request_item": rows[ln["rfq_item"]].material_request_item, "rate": flt(ln["rate"]),
			"request_for_quotation": rfq.name, "request_for_quotation_item": ln["rfq_item"],
			"lead_time_days": cint(ln.get("lead_time_days")) or None,
			"expected_delivery_date": add_days(nowdate(), cint(ln["lead_time_days"])) if cint(ln.get("lead_time_days")) else None}
			for ln in lines],
	})
	sq.run_method("set_missing_values")
	for it, ln in zip(sq.items, lines, strict=True):
		it.rate = flt(ln["rate"])  # the supplier's price, whatever a price list would say
		it.discount_percentage = 0
	sq.flags.ignore_permissions = True
	try:
		sq.insert(ignore_permissions=True)
	except frappe.PermissionError:
		# ERPNext checks the supplier's payable account currency as the user saving it
		frappe.throw(_("The Excom AI Purchase role needs Select on Account to save a Supplier Quotation (ERPNext reads "
			"the supplier's payable account currency)."), frappe.PermissionError)
	return ("Supplier Quotation", sq.name)


# ── comparison with price history ────────────────────────────────────────────


def buying_price_list() -> str | None:
	pl = frappe.db.get_single_value("Buying Settings", "buying_price_list") or "Standard Buying"
	return pl if frappe.db.exists("Price List", pl) else None


def history(item_code: str, company: str) -> dict:
	"""The item's last purchase prices, per stock unit in the company's currency."""
	out = {}
	for key, parent, date in (("last_po", "Purchase Order", "transaction_date"), ("last_pi", "Purchase Invoice", "posting_date")):
		row = frappe.db.sql(f"""SELECT d.base_rate / IFNULL(NULLIF(d.conversion_factor, 0), 1) AS rate, p.{date} AS date,
			p.supplier, p.name FROM `tab{parent} Item` d JOIN `tab{parent}` p ON p.name = d.parent
			WHERE p.docstatus = 1 AND p.company = %s AND d.item_code = %s ORDER BY p.{date} DESC, p.creation DESC LIMIT 1""",
			(company, item_code), as_dict=True)
		if row:
			out[key] = {"rate": flt(row[0].rate), "date": str(row[0].date), "supplier": row[0].supplier, "name": row[0].name}
	pl = buying_price_list()
	currency = frappe.db.get_value("Company", company, "default_currency")
	if pl and frappe.db.get_value("Price List", pl, "currency") == currency:
		price = frappe.db.get_value("Item Price", {"price_list": pl, "item_code": item_code}, ["price_list_rate", "uom"],
			as_dict=True, order_by="valid_from desc")
		stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
		if price and (not price.uom or price.uom == stock_uom):
			out["price_list"] = {"rate": flt(price.price_list_rate), "name": pl}
	rule = cost_rule(item_code)
	if rule:
		out["rule"] = rule
	return out


def cost_rule(item_code: str) -> dict | None:
	"""material_price_control's expected rate and allowed variance for the item, when it is installed."""
	if "material_price_control" not in frappe.get_installed_apps():
		return None
	try:
		from material_price_control.material_price_control.guard import get_expected_rate, get_settings

		expected = get_expected_rate(item_code)
		if not expected:
			return None
		settings = get_settings()
		allowed = flt(expected.get("allowed_variance_pct")) or flt(settings.default_variance_pct if settings else 0) or 10
		return {"rate": flt(expected["expected_rate"]), "allowed_pct": allowed, "min_rate": expected.get("min_rate"),
			"max_rate": expected.get("max_rate"), "name": expected.get("rule_name")}
	except Exception:
		frappe.log_error(title="Excom AI: material_price_control rule lookup failed", message=frappe.get_traceback())
		return None


def compare(item_code: str, rate: float, company: str) -> dict:
	"""A quoted rate (per stock unit, company currency) against history: better, worse, about the
	same, or no history. The reference is the last PO, else the last invoice, else the price list."""
	h = history(item_code, company)
	ref_key = next((k for k in ("last_po", "last_pi", "price_list") if h.get(k) and flt(h[k]["rate"])), None)
	notes = []
	verdict, delta = _("no history"), None
	if ref_key:
		ref = flt(h[ref_key]["rate"])
		delta = (flt(rate) - ref) / ref * 100
		verdict = _("better") if delta < -SAME_BAND_PCT else _("worse") if delta > SAME_BAND_PCT else _("about the same")
		label = {"last_po": _("last PO"), "last_pi": _("last invoice"), "price_list": _("price list")}[ref_key]
		notes.append(_("{0:+.1f}% against the {1} ({2})").format(delta, label, flt(ref, 2)))
	for k, label in (("last_po", _("last PO")), ("last_pi", _("last invoice")), ("price_list", _("price list"))):
		if h.get(k) and k != ref_key:
			notes.append(_("{0} {1}").format(label, flt(h[k]["rate"], 2)))
	rule = h.get("rule")
	if rule:
		off = abs(flt(rate) - rule["rate"]) / rule["rate"] * 100 if rule["rate"] else 0
		outside = off > rule["allowed_pct"] or (rule.get("min_rate") and rate < flt(rule["min_rate"])) or \
			(rule.get("max_rate") and rate > flt(rule["max_rate"]))
		notes.append(_("outside the cost rule ({0} ± {1}%)").format(flt(rule["rate"], 2), flt(rule["allowed_pct"], 1)) if outside
			else _("within the cost rule"))
	return {"verdict": verdict, "delta_pct": None if delta is None else round(delta, 1), "note": "; ".join(notes),
		"history": h}


@registry.task_kind("compare_quotation", priority=registry.PRIORITY["check"], effort="classify")
def compare_quotation(task, agent) -> str:
	"""A Supplier Quotation draft someone entered: each rate against history, as a note on it (no model)."""
	sq = frappe.get_doc("Supplier Quotation", task.subject_name)
	if sq.docstatus != 0:
		return _("{0} is no longer a draft").format(sq.name)
	lines = []
	for it in sq.items:
		c = compare(it.item_code, flt(it.base_rate) / (flt(it.conversion_factor) or 1), sq.company)
		lines.append(f"{it.item_code} at {flt(it.rate, 2)} {sq.currency}: {c['verdict']}" + (f" ({c['note']})" if c["note"] else ""))
	name = proposals.propose(task, agent, "note", subject=("Supplier Quotation", sq.name), risk="Low",
		payload={"text": _("Against price history: {0}").format("; ".join(lines))})
	return _("{0} lines compared ({1})").format(len(lines), name)


# ── PO chasers ───────────────────────────────────────────────────────────────

DONE_PO = ("Closed", "Completed", "Delivered", "Cancelled", "On Hold")


def received(po: str) -> bool:
	row = frappe.db.get_value("Purchase Order", po, ["docstatus", "per_received", "status"], as_dict=True)
	return not row or row.docstatus != 1 or flt(row.per_received) >= 100 or row.status in DONE_PO


def pending_lines(po) -> list[dict]:
	return [{"item_code": it.item_code, "item_name": it.item_name, "uom": it.uom, "pending": flt(it.qty) - flt(it.received_qty),
		"schedule_date": str(it.schedule_date or po.schedule_date)} for it in po.items if flt(it.qty) - flt(it.received_qty) > 0]


def chase_body(po, intro: str, closing: str, lines: list[dict]) -> str:
	name = frappe.db.get_value("Supplier", po.supplier, "supplier_name") or po.supplier
	items = "\n".join(f"{i}. {ln['item_name']} ({ln['item_code']}): {_qty(ln['pending'])} {ln['uom']} still to come"
		for i, ln in enumerate(lines, 1))
	return (f"Dear {name},\n\n{intro}\n\nPurchase order {po.name}, due on {frappe.utils.formatdate(po.schedule_date)}:\n"
		f"{items}\n\nCould you tell us when these will be dispatched?\n\n{closing}")


@registry.task_kind("chase_po", priority=registry.PRIORITY["write"], effort="write")
def chase_po(task, agent) -> str:
	"""A polite question about a purchase order that is due, to the supplier (po_chase)."""
	po = frappe.get_doc("Purchase Order", task.subject_name)
	if received(po.name):
		return _("{0} is received or closed: no chaser").format(po.name)
	lines = pending_lines(po)
	if not lines:
		return _("nothing outstanding on {0}").format(po.name)
	to = (po.get("contact_email") or "").strip().lower() or supplier_email(po.supplier, po.get("contact_person"))
	if not to:
		proposals.propose(task, agent, "create_todo", subject=("Purchase Order", po.name), risk="Low",
			payload={"text": _("{0} is due and {1} has no email address: please chase by phone.").format(po.name, po.supplier)})
		return _("no email for {0}: a person chases").format(po.supplier)
	intro, closing, notes = _wording(task, agent, "chase", po.supplier)
	body = chase_body(po, intro, closing, lines)
	subject = _("Purchase order {0}: delivery").format(po.name)
	account = next(iter(rfq_accounts(agent)), None)
	results = _check(agent, to, subject, body, account, len(lines), notes)
	name = proposals.propose(task, agent, "po_chase", subject=("Purchase Order", po.name), channel_account=account,
		kind="Reply", to_email=to, checks=results, checks_ok=results["ok"], risk="Low" if results["ok"] else "High",
		preview_html=_html(body), payload={"subject": subject, "body": body, "supplier": po.supplier, "purchase_order": po.name})
	return _("chaser for {0}: {1}").format(po.name, name)


@registry.executor("po_chase")
def po_chase(proposal, agent):
	"""Send an approved chaser in the supplier's office hours; never once the goods are in."""
	p = _payload(proposal)
	po = p.get("purchase_order") or proposal.subject_name
	if received(po):
		raise registry.Cancel(_("{0} is received or closed").format(po))
	supplier = p.get("supplier") or frappe.db.get_value("Purchase Order", po, "supplier")
	message, _thread = _send(proposal, agent, supplier, p.get("subject") or _("Purchase order {0}: delivery").format(po),
		p.get("body") or "")
	return ("Excom Message", message)


def stop_received_chases(agent) -> int:
	"""Chasers not yet sent for orders that have since been received are cancelled."""
	n = 0
	for p in frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "po_chase",
			"status": ["in", ("Pending", "Approved", "Edited", "Shadow", "Held")]}, ["name", "subject_name"]):
		if received(p.subject_name):
			frappe.db.set_value("Excom AI Proposal", p.name, {"status": "Cancelled", "hold_reason": _("received or closed")},
				update_modified=False)
			n += 1
	return n


def _po_reply(task, agent, thread, text: str, ctx) -> str:
	"""A supplier answered a chaser: a note on the order with what they said, and whoever is in charge is told."""
	r = runner.ai(task, agent, output_schema=PO_REPLY_SCHEMA,
		pack_instructions=_("Summarise in one sentence what the supplier says about the delivery of our purchase order. "
			"dispatch_date: the date they give for dispatch or delivery as YYYY-MM-DD (today is {0}), else \"\".").format(nowdate()),
		prompt=untrusted(text[:6000], _("the supplier's reply")))
	summary, date = (r.data.get("summary") or "").strip()[:500], (r.data.get("dispatch_date") or "").strip()
	note = _("{0} replied about delivery: {1}").format(ctx.supplier or _("The supplier"), summary) + (
		" " + _("Date given: {0}.").format(date) if date else "")
	name = proposals.propose(task, agent, "note", subject=("Purchase Order", ctx.ref), thread=thread.name, risk="Low",
		payload={"text": note})
	notify.alert([agent.acting_owner or agent.owner_user], _("{0} (AI): the supplier answered about {1}").format(
		agent.agent_name, ctx.ref), message=frappe.utils.escape_html(note), doctype="Purchase Order", name=ctx.ref)
	return _("delivery answer on {0} noted ({1})").format(ctx.ref, name)


# ── supplier discovery ───────────────────────────────────────────────────────


def spend_by_group(company: str, months: int = 12, limit: int = DISCOVERY_GROUPS) -> list[frappe._dict]:
	"""Item groups by purchase spend (submitted Purchase Invoices), biggest first."""
	return frappe.db.sql("""SELECT pii.item_group, SUM(pii.base_net_amount) AS spend, COUNT(DISTINCT pi.supplier) AS suppliers
		FROM `tabPurchase Invoice Item` pii JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		WHERE pi.docstatus = 1 AND pi.company = %s AND pi.posting_date >= %s AND IFNULL(pii.item_group, '') != ''
		GROUP BY pii.item_group ORDER BY spend DESC LIMIT %s""", (company, add_months(nowdate(), -months), limit), as_dict=True)


def supplier_domains() -> dict[str, str]:
	"""Website and email domains of the suppliers already on record -> supplier."""
	from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain
	from excom.excom_ai.research import FREE_MAIL

	out = {}
	for s in frappe.get_all("Supplier", {"website": ["is", "set"]}, ["name", "website"]):
		d = normalise_domain(s.website)
		if d:
			out.setdefault(d, s.name)
	for r in frappe.db.sql("""SELECT dl.link_name, ce.email_id FROM `tabContact Email` ce JOIN `tabDynamic Link` dl
		ON dl.parent = ce.parent AND dl.parenttype = 'Contact' AND dl.link_doctype = 'Supplier'
		WHERE IFNULL(ce.email_id, '') LIKE '%%@%%'""", as_dict=True):
		d = r.email_id.split("@", 1)[1].lower()
		if d not in FREE_MAIL:
			out.setdefault(d, r.link_name)
	return out


def _candidate(c: dict, agent, task, source: str, group: str, known: dict) -> tuple[str | None, bool]:
	"""One Excom AI Company row per domain (shared with research); an existing supplier is excluded."""
	from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain

	domain = normalise_domain(c.get("domain") or c.get("website"))
	if not domain:
		return None, False
	existing = frappe.db.get_value("Excom AI Company", {"domain": domain})
	if existing:
		if not frappe.db.get_value("Excom AI Company", existing, "agent"):
			frappe.db.set_value("Excom AI Company", existing, "agent", agent.name, update_modified=False)
		return existing, False
	country = c.get("country") if c.get("country") and frappe.db.exists("Country", c.get("country")) else None
	doc = frappe.get_doc({"doctype": "Excom AI Company", "company_name": (c.get("name") or domain)[:140], "domain": domain,
		"website": c.get("website"), "country": country, "city": c.get("city"), "phone": c.get("phone"), "industry": group,
		"location_text": ", ".join(filter(None, (c.get("city"), c.get("country")))), "source": SOURCE_LABEL.get(source, "Import"),
		"source_url": c.get("source_url"), "found_by_task": task.name, "agent": agent.name, "status": "New",
		"research_notes": _("Supplier candidate for {0}").format(group)})
	if domain in known:
		doc.update({"status": "Excluded", "exclusion_reason": "Other",
			"research_notes": _("Already a supplier: {0}").format(known[domain])})
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		frappe.clear_last_message()
		return frappe.db.get_value("Excom AI Company", {"domain": domain}), False
	return doc.name, True


@registry.task_kind("discover_suppliers", priority=registry.PRIORITY["discover"], effort="classify")
def discover_suppliers(task, agent) -> str:
	"""Supplier candidates for the biggest spend groups, researched one by one (profile_supplier)."""
	from excom.excom_ai.tools import discovery as discovery_tools

	params = frappe.parse_json(task.params or "{}") or {}
	company = company_of(agent)
	groups = params.get("groups") or [g.item_group for g in spend_by_group(company)]
	source = next((s for s in DISCOVERY_SOURCES if tools.available(s)), None)
	if not groups:
		return _("no purchase spend in the last 12 months")
	if not source:
		return _("no discovery tool is switched on")
	country = frappe.db.get_value("Company", company, "country")
	known = supplier_domains()
	new = seen = 0
	for group in groups[:DISCOVERY_GROUPS]:
		query = {"keywords": [group], "category": group, "countries": [country] if country else [], "languages": ["en"],
			"buyer_types": ["manufacturer", "supplier"], "limit": DISCOVERY_PER_GROUP, "source": source}
		try:
			found = tools.use(source, "search", query=query, cost=discovery_tools.estimate(source, query))
		except tools.ToolRefused as e:
			return _("{0}: {1}").format(source, e)
		for c in (found or [])[:DISCOVERY_PER_GROUP]:
			name, created = _candidate(c, agent, task, source, group, known)
			if not name:
				continue
			seen += 1
			if created and frappe.db.get_value("Excom AI Company", name, "status") == "New":
				new += 1
				tasks.add(agent.name, "profile_supplier", subject=("Excom AI Company", name))
	return _("{0} candidates from {1} for {2}; {3} new").format(seen, source, ", ".join(groups[:DISCOVERY_GROUPS]), new)


ROLE_INBOXES = ("sales", "info", "enquiry", "enquiries", "inquiry", "contact", "export", "marketing", "office", "hello")


def _pick_address(found: dict, published: list[str], domain: str) -> str | None:
	"""A published address on the supplier's own domain, a sales or info inbox first. Never a guess."""
	addresses = [(a.get("email") or "").lower() for a in found.get("addresses") or [] if a.get("source") == "Published"]
	addresses += [(e or "").lower() for e in published]
	own = [a for a in dict.fromkeys(addresses) if a.endswith("@" + domain)]
	return next((a for a in own if a.split("@")[0] in ROLE_INBOXES), None) or (own[0] if own else None)


def _supplier_group_for(item_group: str | None) -> str | None:
	if item_group and frappe.db.exists("Supplier Group", item_group):
		return item_group
	return frappe.db.get_single_value("Buying Settings", "supplier_group") or None


@registry.task_kind("profile_supplier", priority=registry.PRIORITY["research"], effort="classify")
def profile_supplier(task, agent) -> str:
	"""Read a candidate's website and published inbox, then propose it as a supplier for a person."""
	company = frappe.get_doc("Excom AI Company", task.subject_name)
	if company.status not in ("New", "Profiled"):
		return _("{0} is {1}").format(company.company_name, company.status)
	try:
		facts = tools.use("web_reader", "profile", url=company.website) if company.website else {}
		found = tools.use("email_finder", "find", domain=company.domain, published=facts.get("emails") or [], people=[],
			known=[]) if company.domain else {}
	except Exception as e:
		company.db_set({"status": "Profiled", "profile_checked_on": now_datetime(),
			"research_notes": _("The website could not be read: {0}").format(str(e)[:200])}, update_modified=False)
		return _("{0}: website not readable").format(company.company_name)
	email = _pick_address(found or {}, facts.get("emails") or [], company.domain or "")
	known = supplier_domains().get(company.domain or "")
	company.db_set({
		"website_facts": frappe.as_json({k: facts.get(k) for k in ("title", "description", "emails", "phones", "language")}),
		"description": (facts.get("description") or company.description or "")[:500] or None,
		"status": "Excluded" if known else "Contacts found" if email else "No contacts", "profile_checked_on": now_datetime(),
		"contacts_checked_on": now_datetime(), "exclusion_reason": "Other" if known else None,
	}, update_modified=False)
	if known:
		return _("{0} is already the supplier {1}").format(company.company_name, known)
	payload = {"supplier_name": company.company_name, "supplier_group": _supplier_group_for(company.industry),
		"country": company.country, "website": company.website, "email": email, "research": company.name,
		"item_group": company.industry}
	esc = frappe.utils.escape_html
	preview = (f"<p><b>{esc(company.company_name)}</b> ({esc(company.domain or '')}), "
		f"{_('a candidate supplier for')} {esc(company.industry or '')}</p><p>{esc((facts.get('description') or '')[:300])}</p>"
		f"<p>{_('Email')}: {esc(email or _('none published'))}</p>")
	# A new supplier is always a person's decision, whatever the create_record policy allows.
	results = {"approval": {"ok": False, "why": _("a new supplier is always approved by a person")}, "ok": False}
	name = proposals.propose(task, agent, "create_supplier", subject=("Excom AI Company", company.name), risk="Medium",
		payload=payload, preview_html=preview, checks=results, checks_ok=False)
	return _("{0}: proposed as a supplier ({1})").format(company.company_name, name)


@registry.executor("create_supplier")
def create_supplier(proposal, agent):
	"""A person approved a researched candidate: the Supplier, and its contact when an address was found."""
	if proposal.decided_by == "Autopilot":
		raise registry.Cancel(_("a new supplier is a person's decision, never automatic"))
	p = _payload(proposal)
	existing = frappe.db.get_value("Supplier", {"supplier_name": p["supplier_name"]})
	if existing:
		return ("Supplier", existing)
	s = frappe.get_doc({"doctype": "Supplier", "supplier_name": p["supplier_name"], "supplier_type": "Company",
		"supplier_group": p.get("supplier_group"), "website": p.get("website"),
		"country": p.get("country") if p.get("country") and frappe.db.exists("Country", p.get("country")) else None})
	s.insert(ignore_permissions=True)
	if p.get("email"):
		contact = frappe.get_doc({"doctype": "Contact", "first_name": p["supplier_name"][:100], "is_primary_contact": 1,
			"email_ids": [{"email_id": p["email"], "is_primary": 1}],
			"links": [{"link_doctype": "Supplier", "link_name": s.name}]}).insert(ignore_permissions=True)
		frappe.db.set_value("Supplier", s.name, {"supplier_primary_contact": contact.name, "email_id": p["email"]},
			update_modified=False)
	if p.get("research") and frappe.db.exists("Excom AI Company", p["research"]):
		frappe.db.set_value("Excom AI Company", p["research"], {"status": "Promoted",
			"research_notes": _("Made supplier {0}").format(s.name)}, update_modified=False)
	return ("Supplier", s.name)


# ── the Purchase planner ─────────────────────────────────────────────────────


def _rfq_requests(agent, company: str) -> list[dict]:
	"""Submitted purchase Material Requests with nothing ordered or asked yet, once each."""
	specs = []
	for (mr,) in frappe.db.sql("""SELECT mr.name FROM `tabMaterial Request` mr WHERE mr.docstatus = 1 AND mr.company = %s
		AND mr.material_request_type = 'Purchase' AND mr.status IN ('Pending', 'Partially Ordered')
		AND mr.transaction_date >= %s AND NOT EXISTS (SELECT 1 FROM `tabRequest for Quotation Item` ri
			WHERE ri.material_request = mr.name AND ri.docstatus < 2)
		ORDER BY mr.transaction_date DESC LIMIT %s""", (company, add_days(nowdate(), -30), PER_TICK)):
		if not frappe.db.exists("Excom AI Task", {"agent": agent.name, "kind": "draft_rfq", "subject_name": mr}):
			specs.append({"kind": "draft_rfq", "subject": ("Material Request", mr), "params": {"material_request": mr}})
	return specs


def _follow_ups(agent) -> list[dict]:
	specs = []
	for p in frappe.get_all("Excom AI Proposal", filters=[["agent", "=", agent.name], ["action", "=", "send_rfq"],
			["status", "=", "Sent"], ["subject_doctype", "=", "Supplier"],
			["sent_on", ">", add_days(now_datetime(), -FOLLOW_UP_UNTIL_DAYS)], ["sent_on", "<", add_days(now_datetime(), -FOLLOW_UP_DAYS)]],
			fields=["name", "thread", "sent_on", "payload"], limit=50):
		if frappe.db.exists("Excom AI Task", {"agent": agent.name, "kind": "rfq_follow_up", "subject_name": p.name}):
			continue
		payload = frappe.parse_json(p.payload or "{}") or {}
		if _answered_since(p.thread, p.sent_on) or _quoted(rfq_of(payload), payload.get("supplier")):
			continue
		specs.append({"kind": "rfq_follow_up", "subject": ("Excom AI Proposal", p.name)})
		if len(specs) >= PER_TICK:
			break
	return specs


def _chases(agent, company: str) -> list[dict]:
	specs = []
	today = getdate(nowdate())
	for po in frappe.get_all("Purchase Order", filters=[["docstatus", "=", 1], ["company", "=", company],
			["per_received", "<", 100], ["status", "not in", DONE_PO],
			["schedule_date", "<=", add_days(today, CHASE_AHEAD_DAYS)], ["schedule_date", ">=", add_days(today, -CHASE_BACK_DAYS)]],
			pluck="name", order_by="schedule_date asc", limit=50):
		last = frappe.db.get_value("Excom AI Proposal", {"agent": agent.name, "subject_doctype": "Purchase Order", "subject_name": po,
			"action": ["in", ("po_chase", "create_todo")]}, "creation", order_by="creation desc")
		if last and get_datetime(last) > add_days(now_datetime(), -CHASE_EVERY_DAYS):
			continue
		specs.append({"kind": "chase_po", "subject": ("Purchase Order", po)})
		if len(specs) >= PER_TICK:
			break
	return specs


def _comparisons(agent, company: str) -> list[dict]:
	specs = []
	for sq in frappe.get_all("Supplier Quotation", {"docstatus": 0, "company": company, "owner": ["!=", agent.service_user or ""],
			"creation": [">=", add_days(now_datetime(), -7)]}, pluck="name", limit=PER_TICK):
		if not frappe.db.exists("Excom AI Task", {"agent": agent.name, "kind": "compare_quotation", "subject_name": sq}):
			specs.append({"kind": "compare_quotation", "subject": ("Supplier Quotation", sq)})
	return specs


@registry.planner("purchase")
def plan(agent) -> list[dict]:
	"""Each tick for a working Purchase agent: RFQs for new Material Requests, reminders, chasers for
	orders that are due, comparisons for new quotation drafts, a monthly supplier search, and sending
	what was approved."""
	if agent.status not in ("Live", "Shadow"):
		return []
	company = company_of(agent)
	stop_received_chases(agent)
	specs = _rfq_requests(agent, company) + _follow_ups(agent) + _chases(agent, company) + _comparisons(agent, company)
	if any(tools.available(s) for s in DISCOVERY_SOURCES):
		specs.append({"kind": "discover_suppliers", "dedupe_key": f"discover_suppliers:{agent.name}:{nowdate()[:7]}"})
	if agent.status == "Live" and frappe.db.exists("Excom AI Proposal", {"agent": agent.name,
			"status": ["in", ("Approved", "Edited")], "action": ["in", proposals.SENDS]}):
		specs.append({"kind": "send_due", "dedupe_key": f"send_due:{agent.name}"})
	return specs


# ── readiness (§7.3) ─────────────────────────────────────────────────────────

RULES = [
	("purchase.supplier_groups", "Supplier groups", "Warning", "",
		"Put every active supplier in a supplier group (raw material, packing, job work…), so RFQs and discovery know who "
		"sells what.", "/app/supplier", "excom.excom_ai.packs.purchase.supplier_groups"),
	("purchase.supplier_contacts", "Supplier contacts", "Blocker", "",
		"Give the suppliers you buy from a contact with an email address: RFQs and chasers go there.", "/app/contact",
		"excom.excom_ai.packs.purchase.supplier_contacts"),
	("purchase.price_history", "Buying price history", "Warning", "",
		"Keep buying prices in the buying price list (Standard Buying), so quotations can be compared.", "/app/item-price",
		"excom.excom_ai.packs.purchase.price_history"),
	("purchase.rfq_mailbox", "RFQ mailbox", "Blocker", "",
		"Connect a mailbox to the agent for RFQs and chasers (purpose Outreach, or Outreach and replies).",
		"/excom/admin/accounts", "excom.excom_ai.packs.purchase.rfq_mailbox"),
	("purchase.people", "People", "Blocker", "", "Set an active owner and backup, and the team.", "",
		"excom.excom_ai.builder.readiness.people"),
]
SHARE_NEEDED = 0.8


def _active_suppliers() -> list[str]:
	"""Suppliers bought from in the last 12 months, else every enabled one."""
	recent = [r[0] for r in frappe.db.sql("""SELECT DISTINCT supplier FROM `tabPurchase Order` WHERE docstatus = 1
		AND transaction_date >= %s""", add_months(nowdate(), -12))]
	return recent or frappe.get_all("Supplier", {"disabled": 0}, pluck="name")


@readiness.check("purchase.supplier_groups")
def supplier_groups(agent, facts):
	roots = {r[0] for r in frappe.db.sql("""SELECT name FROM `tabSupplier Group` WHERE IFNULL(parent_supplier_group, '') = ''""")}
	roots.add("All Supplier Groups")
	rows = frappe.get_all("Supplier", {"disabled": 0}, ["name", "supplier_group"])
	if not rows:
		return False, _("No suppliers on record.")
	without = [r.name for r in rows if not r.supplier_group or r.supplier_group in roots]
	return len(without) <= len(rows) * (1 - SHARE_NEEDED), _("{0} of {1} suppliers have no supplier group.").format(
		len(without), len(rows))


@readiness.check("purchase.supplier_contacts")
def supplier_contacts(agent, facts):
	active = _active_suppliers()
	if not active:
		return False, _("No suppliers on record.")
	with_email = [s for s in active if supplier_email(s)]
	return len(with_email) >= len(active) * SHARE_NEEDED, _("{0} of the {1} suppliers you buy from have an email address.").format(
		len(with_email), len(active))


@readiness.check("purchase.price_history")
def price_history(agent, facts):
	pl = buying_price_list()
	prices = frappe.db.count("Item Price", {"price_list": pl}) if pl else 0
	orders = frappe.db.count("Purchase Order", {"docstatus": 1, "transaction_date": [">=", add_months(nowdate(), -12)]})
	return bool(prices or orders), _("{0} holds {1} prices; {2} purchase orders in 12 months.").format(
		pl or _("No buying price list"), prices, orders)


@readiness.check("purchase.rfq_mailbox")
def rfq_mailbox(agent, facts):
	boxes = [r for r in agent.channels or [] if r.purpose != "Replies"
		and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "email"]
	if not boxes:
		return False, _("No mailbox on the agent for RFQs.")
	for r in boxes:
		status = frappe.db.get_value("Excom Channel Account", r.channel_account, "status")
		if status == "Active" and not r.paused:
			return True, _("{0} sends RFQs and chasers.").format(r.channel_account)
	return False, _("{0}: not active, or paused.").format(", ".join(r.channel_account for r in boxes))


def seed() -> None:
	"""The Purchase readiness rules, once (setup._builder calls each pack's seed)."""
	readiness.seed("Purchase", RULES)


# ── knowledge recipes for the Purchase builder ───────────────────────────────


@recipe("purchase_spend", "Spend by item group (12 months)", packs=("purchase",), privacy="aggregates", order=10)
def recipe_spend(ctx):
	need("Purchase Invoice")
	groups = spend_by_group(ctx.company, limit=10)
	total = sum(flt(g.spend) for g in groups)
	facts = [fact(_("Purchases in 12 months: {0}, mostly {1}").format(money(total, ctx.currency), ", ".join(
		f"{g.item_group} ({money(g.spend, ctx.currency)})" for g in groups[:3]) or _("nothing")),
		_("Purchase Invoice Item by item group, submitted, last 12 months"), "Item Group", [g.item_group for g in groups],
		level="info" if groups else "warning")]
	if ctx.groups:
		mine = [g for g in groups if g.item_group in ctx.groups]
		facts.append(fact(_("In scope: {0}").format(", ".join(f"{g.item_group} ({money(g.spend, ctx.currency)})" for g in mine)
			or _("no spend in the groups in scope")), _("Purchase Invoice Item"), "Item Group", [g.item_group for g in mine]))
	return facts, {"groups": [dict(g) for g in groups], "total": total}


@recipe("purchase_suppliers", "Top suppliers", packs=("purchase",), privacy="names", order=20)
def recipe_suppliers(ctx):
	need("Purchase Invoice", "Supplier")
	rows = frappe.db.sql("""SELECT pi.supplier, SUM(pi.base_net_total) AS spend, COUNT(*) AS invoices FROM `tabPurchase Invoice` pi
		WHERE pi.docstatus = 1 AND pi.company = %s AND pi.posting_date >= %s GROUP BY pi.supplier ORDER BY spend DESC LIMIT 10""",
		(ctx.company, add_months(nowdate(), -12)), as_dict=True)
	with_email = [r.supplier for r in rows if supplier_email(r.supplier)]
	names = ", ".join(f"{frappe.db.get_value('Supplier', r.supplier, 'supplier_name') or r.supplier} ({money(r.spend, ctx.currency)})"
		for r in rows[:5])
	facts = [fact(_("Top suppliers: {0}").format(names or _("none in 12 months")), _("Purchase Invoice, last 12 months"),
		"Supplier", [r.supplier for r in rows]),
		fact(_("{0} of the top {1} have an email address").format(len(with_email), len(rows)), _("Supplier, Contact"),
			"Supplier", with_email, level="info" if len(with_email) == len(rows) else "warning")]
	return facts, {"suppliers": [dict(r) for r in rows], "with_email": with_email}


@recipe("purchase_late_orders", "Open purchase orders past their date", packs=("purchase",), privacy="names", order=30)
def recipe_late_orders(ctx):
	need("Purchase Order")
	rows = frappe.get_all("Purchase Order", filters=[["docstatus", "=", 1], ["company", "=", ctx.company],
		["per_received", "<", 100], ["status", "not in", DONE_PO], ["schedule_date", "<", nowdate()]],
		fields=["name", "supplier", "schedule_date", "base_grand_total", "per_received"], order_by="schedule_date asc", limit=500)
	value = sum(flt(r.base_grand_total) * (100 - flt(r.per_received)) / 100 for r in rows)
	facts = [fact(_("{0} purchase orders are past their schedule date with goods still to come, worth about {1}").format(
		len(rows), money(value, ctx.currency)), _("Purchase Order: submitted, per_received < 100, schedule_date passed"),
		"Purchase Order", [r.name for r in rows], level="warning" if rows else "info")]
	return facts, {"late": [r.name for r in rows], "count": len(rows), "value": value}


@recipe("purchase_prices", "Buying price lists", packs=("purchase",), privacy="aggregates", order=40)
def recipe_prices(ctx):
	need("Price List", "Item Price")
	lists = frappe.get_all("Price List", {"buying": 1, "enabled": 1}, ["name", "currency"])
	rows = [{"price_list": pl.name, "currency": pl.currency, "prices": frappe.db.count("Item Price", {"price_list": pl.name})}
		for pl in lists]
	facts = [fact(_("{0} ({1}): {2} prices").format(r["price_list"], r["currency"], r["prices"]), _("Price List, Item Price"),
		"Price List", [r["price_list"]]) for r in rows] or [fact(_("No buying price list"), _("Price List (buying)"),
			level="warning")]
	return facts, {"lists": rows, "default": buying_price_list()}

