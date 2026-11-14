"""The Purchase pack (HLD-007 §8.3, Phase 2C): RFQs, supplier quotations, comparison, PO chasers and
supplier discovery, every message to a supplier under Review.

    draft_rfq           a Material Request (or items and quantities) and chosen suppliers: a draft
                        Request for Quotation (create_rfq) and one email per supplier (send_rfq). The
                        items, quantities and dates in the email are written by code; the model writes
                        only the polite words around them, and they may hold no number at all.
    rfq_follow_up       one reminder when a supplier has not answered for FOLLOW_UP_DAYS; for a supplier
                        with no email (asked by a person) a WhatsApp reminder from an approved template.
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
                        supplier's office hours; it stops when the goods are received. A supplier with
                        no email but a WhatsApp number gets it as an approved WhatsApp template.
    templates (HLD-008) WhatsApp chasers and reminders only from an approved template (templates.find;
                        a missing one is recorded as a need and a person is given a ToDo). An approved
                        email template gives the chaser's and the reminder's words instead of the model.
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

from excom.excom_ai import checks, sequencer, templates, tools
from excom.excom_ai.builder import geo, readiness
from excom.excom_ai.builder.recipes import fact, money, need, recipe
from excom.excom_ai.engine import notify, proposals, registry, runner, tasks
from excom.excom_ai.engine.prompts import untrusted

FOLLOW_UP_DAYS = 4  # a supplier who has not answered an RFQ for this long gets one reminder
FOLLOW_UP_UNTIL_DAYS = 30  # older RFQs are left to people
CHASE_AHEAD_DAYS = 2  # a PO is chased from this many days before its schedule date
CHASE_EVERY_DAYS = 7  # and again a week later while goods are still to come
CHASE_BACK_DAYS = 120  # orders late for longer than this are a person's problem, not a reminder's
REPLY_WITHIN_DAYS = 3  # a WhatsApp RFQ reminder asks for the quote by this many days from today
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
- prices_include: "tax" when the prices include GST, VAT or other taxes, "freight" when they include delivery or
  freight; [] when the prices are net or it is not said.
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
		"prices_include": {"type": "array", "items": {"type": "string", "enum": ["tax", "freight"]}},
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
	"required": ["is_quotation", "currency", "valid_till", "payment_terms", "lead_time_days", "notes", "prices_include", "lines"],
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


def supplier_identity(supplier: str, email: str | None = None, phone: str | None = None) -> str:
	"""The supplier's person record for this address or WhatsApp number (made, linked to the
	supplier, when missing)."""
	from excom.excom.doctype.omni_identity.omni_identity import normalize_phone

	if email:
		name = frappe.db.get_value("Omni Identity", {"primary_email": email, "status": ["!=", "Merged"]})
	else:
		from excom.excom.services.identity_sync import _find_existing_identity

		name = _find_existing_identity(phone=phone)
	if name:
		return name
	doc = {"doctype": "Omni Identity", "display_name": frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier,
		"status": "Active", "is_master": 1,
		"linked_entities": [{"linked_doctype": "Supplier", "linked_name": supplier, "role": "Primary Contact"}]}
	if email:
		doc["primary_email"] = email
	else:  # validation is skipped (the supplier may be linked elsewhere already), so the number is normalised here
		doc.update(primary_phone=phone, primary_whatsapp=normalize_phone(phone), normalized_phone=normalize_phone(phone))
	doc = frappe.get_doc(doc)
	doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc.name


def supplier_language(supplier: str | None) -> str:
	return (frappe.db.get_value("Supplier", supplier, "language") if supplier else None) or "en"


def _contact_mobile(contact: str | None) -> str | None:
	if not contact:
		return None
	fields = [f for f in ("whatsapp_no", "whatsapp", "mobile_no") if frappe.get_meta("Contact").has_field(f)]
	row = frappe.db.get_value("Contact", contact, fields, as_dict=True) or {}
	number = next((row.get(f) for f in fields if (row.get(f) or "").strip()), None) or frappe.db.get_value(
		"Contact Phone", {"parent": contact, "parenttype": "Contact", "is_primary_mobile_no": 1}, "phone")
	return number.strip() if number and number.strip() else None


def supplier_mobile(supplier: str, contact: str | None = None) -> str | None:
	"""The supplier's WhatsApp number: the contact named, else the supplier's primary contact, else
	any contact of theirs with a mobile or WhatsApp number."""
	number = _contact_mobile(contact) or _contact_mobile(frappe.db.get_value("Supplier", supplier, "supplier_primary_contact"))
	if number:
		return number
	for (name,) in frappe.db.sql("""SELECT c.name FROM `tabContact` c JOIN `tabDynamic Link` dl ON dl.parent = c.name
			AND dl.parenttype = 'Contact' AND dl.link_doctype = 'Supplier' WHERE dl.link_name = %s
			ORDER BY c.is_primary_contact DESC, c.creation ASC LIMIT 10""", supplier):
		number = _contact_mobile(name)
		if number:
			return number
	return None


def whatsapp_numbers(agent) -> list[str]:
	"""The agent's own WhatsApp numbers (not paused)."""
	return [r.channel_account for r in agent.channels or [] if r.channel_account and not r.paused
		and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "whatsapp"]


def whatsapp_route(agent, key: str, supplier: str, contact: str | None = None) -> frappe._dict | None:
	"""For a supplier with no email: their number, the agent's number and the approved template for the
	need `key` (found None when there is none; templates.find has then recorded the need). None when
	the supplier or the agent has no WhatsApp number."""
	mobile, numbers = supplier_mobile(supplier, contact), whatsapp_numbers(agent)
	if not mobile or not numbers:
		return None
	language = supplier_language(supplier)
	for account in numbers:
		found = templates.find(agent, key, "WhatsApp", language, account, record=False)
		if found:
			return frappe._dict(mobile=mobile, account=account, found=found, language=language, contact=contact)
	templates.find(agent, key, "WhatsApp", language, numbers[0])  # none: recorded for the steward
	return frappe._dict(mobile=mobile, account=numbers[0], found=None, language=language, contact=contact)


def whatsapp_opted_in(mobile: str) -> bool:
	"""The number's person opted in to WhatsApp (send_rules' own test): a template to anyone else
	would be refused at sending, so a person is asked instead."""
	from excom.excom_ai.send_rules import number_opted_in

	return number_opted_in(mobile)


def whatsapp_values(route, values: dict) -> tuple[list[str] | None, str | None]:
	"""(variables, None) when the template may go to this number, else (None, why not)."""
	if not route or not route.found:
		return None, _("no approved WhatsApp template yet")
	try:
		variables = templates.whatsapp_values(route.found, values)
	except ValueError as e:
		return None, str(e)
	if not whatsapp_opted_in(route.mobile):
		return None, _("{0} has not opted in to WhatsApp messages").format(route.mobile)
	return variables, None


def propose_whatsapp(task, agent, action: str, subject: tuple, route, variables: list[str], extra: dict) -> str | None:
	"""A WhatsApp template send under the pack's own action. The first send of a new template waits
	for a person (proposals.propose), who sees its English back-translation and Meta's category."""
	found = route.found
	text = templates.fill(found["text"], variables)
	results = {"recipient": {"ok": True, "why": _("WhatsApp {0} (no email on record)").format(route.mobile)},
		"channel": {"ok": True, "why": route.account},
		"template": {"ok": True, "why": _("approved utility template {0}").format(found["template"])},
		"opt_in": {"ok": True, "why": _("the supplier opted in to WhatsApp")}}
	results["ok"] = True
	preview = f"<p>{frappe.utils.escape_html(text)}</p>"
	if not found.get("first_use_done"):
		t = frappe.db.get_value("WhatsApp Templates", found["template"], ["category", "xai_back_translation"], as_dict=True) or {}
		preview += "<p><i>" + frappe.utils.escape_html(_("First use of this template ({0}). In English: {1}").format(
			t.get("category") or "-", t.get("xai_back_translation") or "-")) + "</i></p>"
	payload = {**extra, "channel": "whatsapp", "template": found["template"], "variables": variables, "mobile": route.mobile,
		"contact": route.contact, "need": found["need"], "language": found["language"], "body": text}
	return proposals.propose(task, agent, action, subject=subject, channel_account=route.account, kind="Reply",
		checks=results, checks_ok=True, risk="Low" if found.get("first_use_done") else "Medium", preview_html=preview,
		payload=payload)


def _held(s) -> bool:
	"""The supplier is on hold for everything (not only invoices or payments) and the hold has not
	reached its release date."""
	if not s.on_hold or (s.hold_type or "") not in ("All", ""):
		return False
	return not s.release_date or getdate(s.release_date) > getdate(nowdate())


def _cannot_ask(supplier: str) -> str | None:
	s = frappe.db.get_value("Supplier", supplier, ["disabled", "prevent_rfqs", "on_hold", "hold_type", "release_date"],
		as_dict=True)
	if not s:
		return _("no such supplier")
	if s.disabled:
		return _("disabled")
	if s.prevent_rfqs:
		return _("RFQs blocked by the scorecard")
	if _held(s):
		return _("on hold")
	return None


def _cannot_chase(supplier: str) -> str | None:
	"""Why a supplier may not be chased about an order: disabled, or on hold until a later date."""
	s = frappe.db.get_value("Supplier", supplier, ["disabled", "on_hold", "hold_type", "release_date"], as_dict=True)
	if not s:
		return _("no such supplier")
	if s.disabled:
		return _("disabled")
	if _held(s):
		return _("on hold")
	return None


def _off(agent, action: str) -> bool:
	"""The agent's policy for this action is Off: nothing would be proposed, so the model is not asked."""
	return proposals.mode_for(agent, action) == "Off"


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


def _check(agent, to: str, subject: str, body: str, account: str | None, lines: int, notes: list[str],
		words: str | None = None) -> dict:
	"""The code checks (§12.5) for a message to a supplier, plus whether the model's words held
	(`words` says where they came from when it was not the model)."""
	results = checks.check(agent, to, subject, body, kind="Supplier", account=account, word_limit=150 + 25 * lines)
	results["wording"] = {"ok": not notes, "why": "; ".join(notes) or words or _("the model's words only")}
	results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	return results


def email_words(agent, key: str, supplier: str, values: dict) -> frappe._dict | None:
	"""The subject, opening and closing of the approved email template for the need `key`, filled;
	None when there is none (templates.find has then recorded the need, and the model writes)."""
	found = templates.find(agent, key, "Email", supplier_language(supplier), None)
	if not found:
		return None
	subject, opening, closing = templates.email_parts(found, values)
	return frappe._dict(subject=subject, opening=opening, closing=closing, need=found["need"])


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
	emails_off = _off(agent, "send_rfq")
	for row in rows:
		if not row["email_id"]:
			by_hand.append(row["supplier"])
			continue
		if emails_off:
			continue
		intro, closing, notes = _wording(task, agent, "rfq", row["supplier"], words)
		made.append(_propose_rfq_email(task, agent, row["supplier"], row["email_id"], account, rfq_prop, rfq, lines,
			intro, closing, notes))
	out = _("RFQ {0}; {1} emails proposed").format(rfq or rfq_prop, len([m for m in made if m]))
	if by_hand:
		out += "; " + _("no email for {0}: a person sends it").format(", ".join(by_hand))
	return out


def lines_key(lines) -> list:
	"""What an RFQ email says about each line (item, quantity, unit, date), to tell whether the email
	still matches the RFQ."""
	return sorted([ln.get("item_code"), flt(ln.get("qty")), ln.get("uom") or "", str(getdate(ln.get("schedule_date")))
		if ln.get("schedule_date") else ""] for ln in lines)


def _propose_rfq_email(task, agent, supplier: str, to: str, account: str | None, rfq_prop: str | None, rfq: str | None,
		lines: list[dict], intro: str, closing: str, notes: list[str], why_review: str | None = None) -> str | None:
	"""One supplier's RFQ email (send_rfq). The model's words are kept in the payload, so the email can
	be written again from them if a person changes the RFQ before it goes. `why_review` keeps it for a
	person whatever the policy says."""
	body = rfq_body(supplier, intro, closing, lines)
	subject = _("Request for quotation") + (f" {rfq}" if rfq else "")
	results = _check(agent, to, subject, body, account, len(lines), notes)
	if why_review:
		results["rfq_changed"] = {"ok": False, "why": why_review}
		results["ok"] = False
	return proposals.propose(task, agent, "send_rfq", subject=("Supplier", supplier), channel_account=account,
		kind="Reply", to_email=to, risk="Medium" if results["ok"] else "High", checks=results,
		checks_ok=results["ok"], preview_html=_html(body),
		payload={"subject": "", "body": body, "supplier": supplier, "rfq_proposal": rfq_prop, "rfq": rfq,
			"follow_up": 0, "intro": intro, "closing": closing, "lines_key": lines_key(lines)})


def _rfq_doc_lines(rfq) -> list[dict]:
	return [{"item_code": it.item_code, "item_name": it.item_name, "qty": flt(it.qty), "uom": it.uom,
		"schedule_date": str(it.schedule_date)} for it in rfq.items]


WAITING = ("Pending", "Held", "Approved", "Edited", "Shadow")


def follow_rfq_edit(rfq_proposal, agent, rfq) -> dict:
	"""The RFQ proposal was approved, perhaps after a person changed its suppliers or lines: the
	supplier emails drafted with it must follow the RFQ that was really made.

	- a supplier no longer on the RFQ: its email is cancelled;
	- an email whose items, quantities, units or dates no longer match: it is written again from the
	  same approved words and the RFQ's own lines, and goes back to a person for review (even if it
	  had been approved: what was approved is not what would go);
	- a supplier added with an email address: an email is drafted for them, for review.
	The sender checks again before sending (send_rfq), in case the RFQ is changed later still."""
	out = {"cancelled": [], "rewritten": [], "added": []}
	on_rfq = {s.supplier: (s.email_id or "").strip().lower() for s in rfq.suppliers}
	lines = _rfq_doc_lines(rfq)
	key = lines_key(lines)
	seen, words = set(), None
	for row in frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "send_rfq", "status": ["in", WAITING],
			"payload": ["like", f'%"{rfq_proposal.name}"%']}, ["name", "status", "payload", "to_email", "channel_account"]):
		p = frappe.parse_json(row.payload or "{}") or {}
		if p.get("rfq_proposal") != rfq_proposal.name or cint(p.get("follow_up")):
			continue
		supplier = p.get("supplier")
		seen.add(supplier)
		if supplier not in on_rfq:
			frappe.db.set_value("Excom AI Proposal", row.name, {"status": "Cancelled", "step_key": None,
				"hold_reason": _("{0} was taken off the RFQ before it was made").format(supplier)}, update_modified=False)
			out["cancelled"].append(row.name)
			continue
		words = words or (p.get("intro"), p.get("closing"))
		if p.get("lines_key") == key:
			frappe.db.set_value("Excom AI Proposal", row.name, "payload", frappe.as_json({**p, "rfq": rfq.name}),
				update_modified=False)
			continue
		intro, closing = p.get("intro") or FALLBACK["rfq"][0], p.get("closing") or FALLBACK["rfq"][1]
		body = rfq_body(supplier, intro, closing, lines)
		results = _check(agent, row.to_email, _("Request for quotation") + f" {rfq.name}", body, row.channel_account,
			len(lines), [])
		results["rfq_changed"] = {"ok": False, "why": _("the RFQ was changed after this email was drafted: written again "
			"from the RFQ's lines")}
		results["ok"] = False
		values = {"payload": frappe.as_json({**p, "rfq": rfq.name, "body": body, "lines_key": key}), "preview_html": _html(body),
			"checks": frappe.as_json(results), "risk": "High"}
		if row.status in ("Approved", "Edited", "Held"):
			values.update({"status": "Pending", "decided_by": None, "decided_on": None, "send_at": None,
				"hold_reason": _("the RFQ was changed after this email was approved: please read it again")})
		frappe.db.set_value("Excom AI Proposal", row.name, values, update_modified=False)
		out["rewritten"].append(row.name)
	# only a person's edit adds suppliers (when the RFQ is made at once, its emails are drafted next)
	added = [s for s, email in on_rfq.items() if s not in seen and email] if rfq_proposal.edit_diff else []
	if added and not _off(agent, "send_rfq"):
		intro, closing = (words[0], words[1]) if words and words[0] and words[1] else FALLBACK["rfq"]
		account = rfq_proposal.channel_account or next(iter(rfq_accounts(agent)), None)
		for s in added:
			if _cannot_ask(s) or frappe.db.exists("Excom AI Proposal", {"agent": agent.name, "action": "send_rfq",
					"subject_name": s, "payload": ["like", f'%"{rfq_proposal.name}"%']}):
				continue
			name = _propose_rfq_email(None, agent, s, on_rfq[s], account, rfq_proposal.name, rfq.name, lines, intro, closing, [],
				why_review=_("{0} was added to the RFQ by a person: a new email").format(s))
			if name:
				out["added"].append(name)
	if out["rewritten"] or out["added"]:
		notify.alert(proposals.reviewers(agent), _("{0} (AI): the RFQ {1} was changed, its emails need your review").format(
			agent.agent_name, rfq.name), doctype="Request for Quotation", name=rfq.name)
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
	try:
		follow_rfq_edit(proposal, agent, rfq)
	except Exception:
		# never undo the RFQ for this: the sender checks each email against the RFQ again anyway
		frappe.log_error(title="Excom AI: RFQ emails could not follow the edited RFQ", message=frappe.get_traceback())
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
	why = opted_out(to)
	if why:  # asked since the email was written: checked again now
		raise registry.Cancel(why)
	identity = supplier_identity(supplier, to)
	thread, created = proposal.thread, False
	if not thread:
		created = not frappe.db.exists("Excom Thread", {"thread_key": f"email:{account}:{identity}"})
		thread = upsert_thread(identity, "email", account)
	html = compose(agent, body, None, identity, "Reply")
	message = send_email_reply(thread, to=to, subject=subject, body_html=html, in_reply_to_gmail_id=in_reply_to or "")
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime(), "thread": thread},
		update_modified=False)
	take_thread(thread, agent, created)
	return message, thread


def opted_out(email: str) -> str | None:
	"""Why this address may not be written to now (opted out or do-not-contact), else None."""
	row = frappe.db.get_value("Omni Identity", {"primary_email": email, "status": ["!=", "Merged"]},
		["do_not_contact", "email_opt_out"], as_dict=True)
	if row and (row.do_not_contact or row.email_opt_out):
		return _("{0} opted out").format(email)
	return None


def take_thread(thread: str, agent, created: bool) -> None:
	"""The supplier's answer should come back to this agent (engine.inbound reads the thread's agent),
	but only on a conversation the agent may take: one it just made, one it already has, or one nobody
	holds. A conversation waiting for a person (Needs person, Hold), assigned to a person, or held by
	another working agent is left as it is: the message went out, and whoever has the conversation
	keeps it."""
	from excom.excom_ai.engine import threads

	threads.claim(thread, agent, created)  # the rule Accounts uses too (engine.threads)


def _send_whatsapp(proposal, agent, supplier: str, p: dict) -> tuple[str, str]:
	"""Send an approved WhatsApp template to a supplier now (send_rules: approved, on the agent's
	number, opted in), or Defer to their office hours."""
	from excom.excom.services.thread_service import upsert_thread
	from excom.excom_ai import send_rules

	zone = supplier_zone(supplier, agent)
	now = now_datetime()
	if not sequencer.in_window(now, zone):
		raise registry.Defer(sequencer.next_window(now, zone), _("outside the supplier's office hours ({0})").format(zone))
	mobile = p.get("mobile")
	if not mobile or supplier_mobile(supplier, p.get("contact")) != mobile:
		raise registry.Cancel(_("the supplier's WhatsApp number changed since this was written"))
	account = proposal.channel_account
	if not account or account not in whatsapp_numbers(agent):
		raise registry.Cancel(_("{0} is no longer one of the agent's WhatsApp numbers").format(account or "-"))
	if not p.get("template"):
		raise registry.Cancel(_("no template"))
	identity = supplier_identity(supplier, phone=mobile)
	thread, created = proposal.thread, False
	if not thread:
		created = not frappe.db.exists("Excom Thread", {"thread_key": f"whatsapp:{account}:{identity}"})
		thread = upsert_thread(identity, "whatsapp", account)
	try:
		sent = templates.send_whatsapp(proposal, agent, thread, p["template"], p.get("variables") or [])
	except send_rules.AgentSendRefused as e:
		raise registry.Cancel(str(e)) from e  # refused before anything left: nothing was sent
	message = sent.get("message_name")
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime(), "thread": thread},
		update_modified=False)
	take_thread(thread, agent, created)
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
	if p.get("channel") == "whatsapp":  # the reminder to a supplier with no email
		if _quoted(rfq, supplier):
			raise registry.Cancel(_("the supplier has answered"))
		message, _thread = _send_whatsapp(proposal, agent, supplier, p)
		return ("Excom Message", message)
	in_reply_to, subject = "", p.get("subject") or _("Request for quotation {0}").format(rfq)
	if not cint(p.get("follow_up")):
		# the email must still match the RFQ that was made (a person may have changed it since)
		doc = frappe.get_doc("Request for Quotation", rfq)
		if supplier not in {s.supplier for s in doc.suppliers}:
			raise registry.Cancel(_("{0} is no longer on {1}").format(supplier, rfq))
		if p.get("lines_key") is not None and p["lines_key"] != lines_key(_rfq_doc_lines(doc)):
			raise registry.Cancel(_("{0} was changed after this email was written: its items no longer match").format(rfq))
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
	"""One reminder to a supplier who has not answered an RFQ: by email on the RFQ's conversation, or
	(subject: the RFQ, params.supplier) on WhatsApp to a supplier with no email."""
	if _off(agent, "send_rfq"):
		return _("RFQ emails are switched off for this agent")
	if task.subject_doctype == "Request for Quotation":
		return _rfq_whatsapp_reminder(task, agent)
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
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	# a reply keeps its conversation's subject, whatever the template's subject says
	subject = "Re: " + (p.get("subject") or _("Request for quotation {0}").format(rfq))
	words = email_words(agent, "purchase.rfq_reminder", supplier, {"supplier_name": name, "rfq": rfq,
		"reply_by": frappe.utils.formatdate(add_days(nowdate(), REPLY_WITHIN_DAYS))})
	if words:
		body = "\n\n".join(filter(None, (words.opening, _lines_block(lines), RFQ_ASK, words.closing)))
		notes, source = [], _("an approved email template ({0})").format(words.need)
	else:
		intro, closing, notes = _wording(task, agent, "follow_up", supplier)
		body, source = f"Dear {name},\n\n{intro}\n\n{_lines_block(lines)}\n\n{RFQ_ASK}\n\n{closing}", None
	results = _check(agent, first.to_email, subject, body, first.channel_account, len(lines), notes, source)
	made = proposals.propose(task, agent, "send_rfq", subject=("Excom AI Proposal", first.name), thread=first.thread,
		channel_account=first.channel_account, kind="Reply", to_email=first.to_email, checks=results, checks_ok=results["ok"],
		risk="Low" if results["ok"] else "High", preview_html=_html(body),
		payload={"subject": subject, "body": body, "supplier": supplier, "rfq": rfq, "follow_up": 1, "parent": first.name,
			**({"email_template": words.need} if words else {})})
	return _("reminder for {0} to {1}: {2}").format(rfq, supplier, made)


def contacted(rfq: str, supplier: str) -> bool:
	"""The RFQ really reached this supplier: the agent sent it (a Sent send_rfq), ERPNext emailed it
	(email_sent on the RFQ's supplier row), or someone wrote to the supplier on one of their
	conversations (email or WhatsApp) since the RFQ was made."""
	for row in frappe.get_all("Excom AI Proposal", {"action": "send_rfq", "status": "Sent", "subject_name": supplier,
			"payload": ["like", f'%"{rfq}"%']}, ["payload"]):
		if rfq_of(frappe.parse_json(row.payload or "{}") or {}) == rfq:
			return True
	if frappe.get_meta("Request for Quotation Supplier").has_field("email_sent") and frappe.db.get_value(
			"Request for Quotation Supplier", {"parent": rfq, "supplier": supplier}, "email_sent"):
		return True
	since = frappe.db.get_value("Request for Quotation", rfq, "creation")
	return bool(since and frappe.db.sql("""SELECT 1 FROM `tabExcom Message` m JOIN `tabExcom Thread` t ON t.name = m.thread
		JOIN `tabOmni Identity Link` l ON l.parent = t.omni_identity AND l.parenttype = 'Omni Identity'
		WHERE l.linked_doctype = 'Supplier' AND l.linked_name = %s AND m.direction = 'Outbound' AND m.creation >= %s
		LIMIT 1""", (supplier, since)))


def _rfq_whatsapp_reminder(task, agent) -> str:
	"""A supplier with no email was asked by a person and has not quoted: a WhatsApp reminder from an
	approved template, else a ToDo to remind them by phone."""
	supplier = (frappe.parse_json(task.params or "{}") or {}).get("supplier")
	rfq = frappe.db.get_value("Request for Quotation", task.subject_name, ["name", "docstatus"], as_dict=True)
	if not rfq or not supplier:
		return _("no RFQ or supplier")
	if rfq.docstatus == 2:
		return _("{0} was cancelled").format(rfq.name)
	if rfq.docstatus != 1:
		return _("{0} was never submitted: no reminder for an RFQ that did not go out").format(rfq.name)
	if _quoted(rfq.name, supplier):
		return _("answered; no reminder")
	if not contacted(rfq.name, supplier):
		return _("{0} was never sent {1}: no reminder").format(supplier, rfq.name)
	if _cannot_ask(supplier):
		return _("{0} cannot be asked: {1}").format(supplier, _cannot_ask(supplier))
	if supplier_email(supplier):
		return _("{0} has an email address now: a person follows up").format(supplier)
	route = whatsapp_route(agent, "purchase.rfq_reminder", supplier)
	if not route:
		return _("{0} has no WhatsApp number: left to a person").format(supplier)
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	variables, why = whatsapp_values(route, {"supplier_name": name, "rfq": rfq.name,
		"reply_by": frappe.utils.formatdate(add_days(nowdate(), REPLY_WITHIN_DAYS))})
	if variables:
		made = propose_whatsapp(task, agent, "send_rfq", ("Request for Quotation", rfq.name), route, variables,
			{"supplier": supplier, "rfq": rfq.name, "follow_up": 1, "parent": None})
		return _("WhatsApp reminder for {0} to {1}: {2}").format(rfq.name, supplier, made)
	proposals.propose(task, agent, "create_todo", subject=("Request for Quotation", rfq.name), risk="Low",
		payload={"text": _("{0} has not quoted for {1} and has no email address: please remind them by phone ({2}).").format(
			supplier, rfq.name, why)})
	return _("no WhatsApp template for {0}: a person reminds {1}").format(rfq.name, supplier)


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
	named = next((r for r in rows if r.ref.lower() in low), None)
	if named or not rows:
		return named
	# The reply names nothing. With both an RFQ and a chased order still open on this conversation,
	# the latest one is only a guess: a person reads it.
	open_rfq = [r for r in rows if r.kind == "rfq" and (frappe.db.get_value("Request for Quotation", r.ref, "docstatus") or 0) < 2]
	open_po = [r for r in rows if r.kind == "po" and not received(r.ref)]
	if open_rfq and open_po:
		return frappe._dict(kind="unsure", ref=None, refs=[open_rfq[0].ref, open_po[0].ref], proposal=None, supplier=None)
	return rows[0]


def _unit(u: str | None) -> str:
	u = (u or "").strip().lower().rstrip(".")
	return UNITS.get(u, u)


def same_uom(theirs: str, ours: str, stock_uom: str | None = None) -> bool:
	t = _unit(theirs)
	if not t:
		return True
	return any(t == _unit(u) for u in (ours, stock_uom) if u)


def line_factor(row) -> float | None:
	"""How many stock units one of the RFQ line's units holds: the line's own conversion factor, else
	the item's (UOM Conversion Detail). None when the units differ and no factor is known."""
	if not row.uom or row.uom == row.stock_uom:
		return 1.0
	factor = flt(row.get("conversion_factor"))
	if factor and factor != 1:
		return factor
	factor = flt(frappe.db.get_value("UOM Conversion Detail", {"parent": row.item_code, "parenttype": "Item",
		"uom": row.uom}, "conversion_factor"))
	return factor or None


def to_line_unit(theirs: str | None, row) -> tuple[float | None, str | None]:
	"""(how many of the supplier's units make one of ours, why not) for a price per `theirs` on the RFQ
	line: 1 when it is our unit; the line's conversion factor when they priced per stock unit (a box of
	12 at 5 a piece is 60 a box); None, with the reason, when it cannot be told."""
	t = _unit(theirs)
	if not t or t == _unit(row.uom):
		return 1.0, None
	if row.stock_uom and t == _unit(row.stock_uom):
		factor = line_factor(row)
		if not factor:
			return None, _("priced per {0}, we asked per {1}, and no conversion between them is set").format(theirs, row.uom)
		return factor, None
	return None, _("priced per {0}, we asked per {1}").format(theirs, row.uom)


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
		why, factor = None, None
		if ln.get("unclear"):
			why = ln.get("why") or _("the reader was not sure")
		elif not row:
			why = _("not one of the items asked for")
		elif flt(ln.get("rate")) <= 0:
			why = _("no price stated")
		else:
			factor, why = to_line_unit(ln.get("uom"), row)
		if why:
			unclear.append({"supplier_text": ln.get("supplier_text") or "", "item_code": row.item_code if row else "",
				"rate": flt(ln.get("rate")), "why": why})
			continue
		# the price and the break's quantity in the RFQ line's unit (stock units: per piece x pieces a box)
		quoted = flt(ln.get("rate"))
		ln = dict(ln, rate=flt(quoted * factor, 6), min_qty=flt(flt(ln.get("min_qty")) / factor, 6))
		if factor != 1:
			ln["quoted_as"] = _("{0} per {1}").format(f"{quoted:g}", ln.get("uom"))
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
		quoted_as = "; ".join(dict.fromkeys(o["quoted_as"] for o in offs if o.get("quoted_as")))
		lines.append({"rfq_item": lid, "item_code": row.item_code, "item_name": row.item_name, "qty": flt(row.qty),
			"uom": row.uom, "conversion_factor": line_factor(row) or flt(row.conversion_factor) or 1, "rate": rates.pop(),
			"lead_time_days": lead, "breaks": [{"min_qty": b[0], "rate": b[1]} for b in breaks],
			**({"quoted_as": quoted_as} if quoted_as else {})})
	answered = {ln["rfq_item"] for ln in lines} | {u["item_code"] for u in unclear}
	not_quoted = [it.item_code for it in rfq.items if it.name not in answered and it.item_code not in answered]
	return frappe._dict(lines=lines, unclear=unclear, not_quoted=not_quoted)


def exchange_rate(currency: str, company_currency: str) -> float | None:
	"""Company-currency units for one unit of `currency`: the latest Currency Exchange record either
	way round (a record kept the other way is turned over), else ERPNext's own lookup (its settings,
	and its rate service when one is set up). None when nothing knows."""
	if not currency or currency == company_currency:
		return 1.0
	rate = flt(frappe.db.get_value("Currency Exchange", {"from_currency": currency, "to_currency": company_currency},
		"exchange_rate", order_by="date desc"))
	if rate:
		return rate
	inverse = flt(frappe.db.get_value("Currency Exchange", {"from_currency": company_currency, "to_currency": currency},
		"exchange_rate", order_by="date desc"))
	if inverse:
		return flt(1 / inverse, 9)
	try:
		from erpnext.setup.utils import get_exchange_rate

		rate = flt(get_exchange_rate(currency, company_currency, nowdate(), "for_buying"))
	except Exception:
		rate = 0
	frappe.clear_last_message()  # ERPNext's "could not find a rate" note is not for the person
	return rate or None


def _valid_till(value: str) -> str | None:
	"""A date the offer is valid till, or None; only still-valid dates go on a Supplier Quotation."""
	day = _offer_date(value)
	return str(day) if day and day >= getdate(nowdate()) else None


def _offer_date(value: str | None):
	try:
		return getdate(value) if value else None
	except Exception:
		return None


TAX_INCLUDED = re.compile(r"\b(incl(\.|usive|uding|udes|uded)?|with)\s+(of\s+)?(all\s+)?(gst|igst|cgst|sgst|vat|tax(es)?)\b"
	r"|\b(gst|igst|vat|tax(es)?)\s+(is\s+|are\s+)?incl(\.|usive|uded)?\b", re.I)
FREIGHT_INCLUDED = re.compile(r"\b(incl(\.|usive|uding|udes|uded)?|with)\s+(of\s+)?(freight|transport(ation)?|delivery|"
	r"shipping|cartage)\b|\b(freight|transport(ation)?|delivery|shipping|cartage)\s+(is\s+)?(incl(\.|usive|uded)?|free|paid)\b"
	r"|\bfree\s+(delivery|shipping)\b|\bdoor\s+delivery\b", re.I)


def inclusive(data: dict) -> list[str]:
	"""What the quoted prices include besides the goods ('tax', 'freight'), from the reader's answer
	and the supplier's own words on terms. Such prices are not net, so they are not compared as net."""
	said = {str(x).strip().lower() for x in data.get("prices_include") or []}
	words = " ".join(str(data.get(k) or "") for k in ("notes", "payment_terms"))
	out = []
	if "tax" in said or TAX_INCLUDED.search(words):
		out.append("tax")
	if "freight" in said or FREIGHT_INCLUDED.search(words):
		out.append("freight")
	return out


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
	if ctx.kind == "unsure":
		why = _("the supplier's reply names neither {0} nor {1}, and both are open on this conversation").format(*ctx.refs)
		to_person(thread.name, agent, why, text)
		return _("not sure which document the reply is about: left to a person")
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
	stated = (data.get("currency") or "").strip().upper()
	currency = stated or frappe.db.get_value("Supplier", supplier, "default_currency") or company_currency
	known = bool(frappe.db.exists("Currency", currency))
	rate = exchange_rate(currency, company_currency) if known else None
	results = {
		"lines": {"ok": not quote.unclear, "why": _("{0} lines read").format(len(quote.lines)) if not quote.unclear else
			_("{0} lines unclear, left for a person").format(len(quote.unclear))},
		"currency": {"ok": known and bool(stated), "why": (_("unknown currency {0}").format(currency) if not known else currency
			if stated else _("the supplier did not say the currency; {0} assumed, please check").format(currency))},
		"exchange": {"ok": bool(rate), "why": _("1 {0} = {1} {2}").format(currency, rate, company_currency) if rate else
			_("no exchange rate from {0} to {1}").format(currency, company_currency)},
	}
	offer_date = _offer_date(data.get("valid_till"))
	if offer_date and offer_date < getdate(nowdate()):
		results["validity"] = {"ok": False, "why": _("the offer was valid till {0}, already past").format(offer_date)}
	extra = inclusive(data)
	if extra:
		results["net"] = {"ok": False, "why": _("the prices include {0}: not comparable with net prices").format(
			_(" and ").join(extra))}
	results["ok"] = all(v["ok"] for v in results.values())
	compared = [dict(ln, **{"compare": compare(ln["item_code"], flt(ln["rate"]) * (rate or 1) / (flt(ln["conversion_factor"]) or 1),
		rfq.company, includes=extra)}) for ln in quote.lines]
	payload = {"rfq": rfq.name, "supplier": supplier, "currency": currency, "currency_stated": bool(stated),
		"conversion_rate": rate, "valid_till": str(offer_date) if offer_date else None,
		"payment_terms": (data.get("payment_terms") or "")[:500], "notes": (data.get("notes") or "")[:1000],
		"prices_include": extra, "lines": quote.lines, "unclear": quote.unclear, "not_quoted": quote.not_quoted,
		"message": reads[-1].message if reads else None}
	earlier = _earlier_quote(task, agent, rfq.name, supplier)
	if earlier and earlier.doctype == "Supplier Quotation":
		# a draft is already made from this supplier's quotation: a person compares, nothing is made twice
		text = _("{0} sent prices again for {1}: {2}. The draft {3} was made from an earlier email; please check it.").format(
			supplier, rfq.name, "; ".join(f"{ln['item_code']} {flt(ln['rate'], 2)} {currency}" for ln in quote.lines), earlier.name)
		name = proposals.propose(task, agent, "create_todo", subject=("Supplier Quotation", earlier.name), thread=thread.name,
			risk="Low", payload={"text": text})
		return _("a later quotation from {0} for {1}: {2} stands, a person checks ({3})").format(supplier, rfq.name,
			earlier.name, name)
	if earlier:  # an earlier reading still waits: this one replaces it, and a person reads it
		frappe.db.set_value("Excom AI Proposal", earlier.name, {"status": "Cancelled", "step_key": None,
			"hold_reason": _("replaced by a later email from the supplier")}, update_modified=False)
		results["update"] = {"ok": False, "why": _("replaces an earlier reading ({0}) of this supplier's quotation").format(
			earlier.name)}
		results["ok"] = False
	name = proposals.propose(task, agent, "create_supplier_quotation", subject=("Request for Quotation", rfq.name),
		thread=thread.name, payload=payload, checks=results, checks_ok=results["ok"], risk="Medium" if results["ok"] else "High",
		preview_html=quote_preview(payload, compared, company_currency))
	return _("quotation from {0} for {1}: {2} lines, {3} unclear ({4})").format(supplier, rfq.name, len(quote.lines),
		len(quote.unclear), name)


def _earlier_quote(task, agent, rfq: str, supplier: str) -> frappe._dict | None:
	"""What already stands for this supplier's quotation on this RFQ: a Supplier Quotation (draft or
	submitted), else a create_supplier_quotation proposal still waiting (one this same task made is
	its own, found again by proposals.propose)."""
	sq = frappe.db.sql("""SELECT sq.name FROM `tabSupplier Quotation Item` i JOIN `tabSupplier Quotation` sq
		ON sq.name = i.parent WHERE i.request_for_quotation = %s AND sq.supplier = %s AND sq.docstatus < 2
		ORDER BY sq.creation DESC LIMIT 1""", (rfq, supplier))
	if sq:
		return frappe._dict(doctype="Supplier Quotation", name=sq[0][0])
	for row in frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "create_supplier_quotation",
			"subject_doctype": "Request for Quotation", "subject_name": rfq, "status": ["in", WAITING],
			"task": ["!=", task.name]}, ["name", "payload"], order_by="creation desc"):
		if (frappe.parse_json(row.payload or "{}") or {}).get("supplier") == supplier:
			return frappe._dict(doctype="Excom AI Proposal", name=row.name)
	return None


def quote_preview(p: dict, compared: list[dict], company_currency: str) -> str:
	esc = frappe.utils.escape_html
	valid = ""
	if p.get("valid_till"):
		valid = ", " + _("valid till") + " " + p["valid_till"] + (" " + _("(already past)") if not _valid_till(p["valid_till"])
			else "")
	currency = esc(p["currency"]) + ("" if p.get("currency_stated", True) else " " + esc(_("(not stated by the supplier)")))
	head = (f"<p><b>{_('Supplier Quotation (draft)')}</b>: {esc(p['supplier'])}, {_('for')} {esc(p['rfq'])}, "
		f"{currency}{esc(valid)}</p>")
	if p.get("prices_include"):
		head += "<p><b>" + esc(_("The prices include {0}: they are not net, so the comparison with history is only a "
			"guide.").format(_(" and ").join(p["prices_include"]))) + "</b></p>"
	if p.get("payment_terms") or p.get("notes"):
		head += f"<p>{esc(' · '.join(filter(None, (p.get('payment_terms'), p.get('notes')))))}</p>"
	rows = ""
	for ln in compared:
		c = ln["compare"]
		breaks = ", ".join(f"{_qty(b['min_qty'])}+: {b['rate']}" for b in ln.get("breaks") or [] if b["min_qty"]) or ""
		quoted = f" ({esc(_('quoted {0}').format(ln['quoted_as']))})" if ln.get("quoted_as") else ""
		rows += (f"<tr><td>{esc(ln['item_name'])} ({esc(ln['item_code'])})</td><td>{_qty(ln['qty'])} {esc(ln['uom'])}</td>"
			f"<td>{fmt_money(ln['rate'], currency=p['currency'])}{quoted}</td><td>{esc(breaks)}</td>"
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
	made = frappe.db.sql("""SELECT sq.name FROM `tabSupplier Quotation Item` i JOIN `tabSupplier Quotation` sq ON sq.name = i.parent
		WHERE i.request_for_quotation = %s AND sq.supplier = %s AND sq.docstatus < 2 LIMIT 1""", (rfq.name, p["supplier"]))
	if made:
		raise registry.Cancel(_("{0} already has {1} from {2}: not made twice").format(rfq.name, made[0][0], p["supplier"]))
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
	if p.get("valid_till") and not _valid_till(p["valid_till"]):
		terms.append(_("The supplier's offer was valid till {0}, already past when it was read.").format(p["valid_till"]))
	if p.get("prices_include"):
		terms.append(_("The quoted prices include {0}.").format(_(" and ").join(p["prices_include"])))
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


def compare(item_code: str, rate: float, company: str, includes: list[str] | None = None) -> dict:
	"""A quoted rate (per stock unit, company currency) against history: better, worse, about the
	same, or no history. The reference is the last PO, else the last invoice, else the price list.
	A rate that `includes` tax or freight is not net like the history: it is said so, never called
	better or worse."""
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
	if includes and ref_key:
		verdict = _("not comparable")
		notes.insert(0, _("the price includes {0}, the history is net").format(_(" and ").join(includes)))
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
	"""Nothing more to come on the order: not submitted, closed, or every line in. ERPNext counts
	rejected goods as received; here they are still to come."""
	row = frappe.db.get_value("Purchase Order", po, ["docstatus", "per_received", "status"], as_dict=True)
	if not row or row.docstatus != 1 or row.status in DONE_PO:
		return True
	if flt(row.per_received) < 100:
		return False
	return not pending_lines(frappe.get_doc("Purchase Order", po))


def rejected_qty(po_items: list[str]) -> dict[str, float]:
	"""Rejected quantity per Purchase Order line, from submitted receipts (and invoices that update stock)."""
	if not po_items:
		return {}
	out = {}
	for name, qty in frappe.db.sql("""SELECT purchase_order_item, SUM(rejected_qty) FROM `tabPurchase Receipt Item`
			WHERE docstatus = 1 AND purchase_order_item IN %(names)s GROUP BY purchase_order_item
			UNION ALL SELECT i.po_detail, SUM(i.rejected_qty) FROM `tabPurchase Invoice Item` i JOIN `tabPurchase Invoice` pi
			ON pi.name = i.parent WHERE i.docstatus = 1 AND pi.update_stock = 1 AND i.po_detail IN %(names)s GROUP BY i.po_detail""",
			{"names": tuple(po_items)}):
		out[name] = out.get(name, 0) + flt(qty)
	return out


def pending_lines(po, due_by=None) -> list[dict]:
	"""The order's lines with goods still to come (rejected goods do not count as received); with
	`due_by`, only the lines due by that day."""
	rejected = rejected_qty([it.name for it in po.items])
	out = []
	for it in po.items:
		pending = flt(it.qty) - max(0, flt(it.received_qty) - rejected.get(it.name, 0))
		day = getdate(it.schedule_date or po.schedule_date)
		if pending <= 0 or (due_by and day > getdate(due_by)):
			continue
		out.append({"item_code": it.item_code, "item_name": it.item_name, "uom": it.uom, "pending": pending,
			"schedule_date": str(day)})
	return out


def due_on(lines: list[dict]) -> str:
	"""The date the chased lines were due: the earliest of them."""
	return frappe.utils.formatdate(min(getdate(ln["schedule_date"]) for ln in lines))


def chase_body(po, intro: str, closing: str, lines: list[dict]) -> str:
	name = frappe.db.get_value("Supplier", po.supplier, "supplier_name") or po.supplier
	items = "\n".join(f"{i}. {ln['item_name']} ({ln['item_code']}): {_qty(ln['pending'])} {ln['uom']} still to come"
		for i, ln in enumerate(lines, 1))
	return (f"Dear {name},\n\n{intro}\n\nPurchase order {po.name}, due on {due_on(lines)}:\n"
		f"{items}\n\nCould you tell us when these will be dispatched?\n\n{closing}")


@registry.task_kind("chase_po", priority=registry.PRIORITY["write"], effort="write")
def chase_po(task, agent) -> str:
	"""A polite question about a purchase order that is due, to the supplier (po_chase): only the
	lines that are due (or nearly) and not yet in."""
	if _off(agent, "po_chase"):
		return _("chasers are switched off for this agent")
	po = frappe.get_doc("Purchase Order", task.subject_name)
	if received(po.name):
		return _("{0} is received or closed: no chaser").format(po.name)
	why = _cannot_chase(po.supplier)
	if why:
		return _("{0} is not chased: {1}").format(po.supplier, why)
	lines = pending_lines(po, add_days(nowdate(), CHASE_AHEAD_DAYS))
	if not lines:
		return _("nothing due yet on {0}").format(po.name)
	to = (po.get("contact_email") or "").strip().lower() or supplier_email(po.supplier, po.get("contact_person"))
	values = {"supplier_name": frappe.db.get_value("Supplier", po.supplier, "supplier_name") or po.supplier, "po": po.name,
		"due_date": due_on(lines)}
	if not to:
		route = whatsapp_route(agent, "purchase.po_chase", po.supplier, po.get("contact_person"))
		variables, why = whatsapp_values(route, values) if route else (None, None)
		if variables:
			name = propose_whatsapp(task, agent, "po_chase", ("Purchase Order", po.name), route, variables,
				{"supplier": po.supplier, "purchase_order": po.name})
			return _("WhatsApp chaser for {0}: {1}").format(po.name, name)
		text = _("{0} is due and {1} has no email address: please chase by phone.").format(po.name, po.supplier)
		proposals.propose(task, agent, "create_todo", subject=("Purchase Order", po.name), risk="Low",
			payload={"text": text + (" " + _("(WhatsApp: {0})").format(why) if why else "")})
		return _("no email for {0}: a person chases").format(po.supplier)
	subject = _("Purchase order {0}: delivery").format(po.name)
	words = email_words(agent, "purchase.po_chase", po.supplier, values)
	if words:
		items = "\n".join(f"{i}. {ln['item_name']} ({ln['item_code']}): {_qty(ln['pending'])} {ln['uom']} still to come"
			for i, ln in enumerate(lines, 1))
		content = f"Purchase order {po.name}, due on {due_on(lines)}:\n{items}"
		body = "\n\n".join(filter(None, (words.opening, content, words.closing)))
		subject = words.subject or subject
		notes, source = [], _("an approved email template ({0})").format(words.need)
	else:
		intro, closing, notes = _wording(task, agent, "chase", po.supplier)
		body, source = chase_body(po, intro, closing, lines), None
	account = next(iter(rfq_accounts(agent)), None)
	results = _check(agent, to, subject, body, account, len(lines), notes, source)
	name = proposals.propose(task, agent, "po_chase", subject=("Purchase Order", po.name), channel_account=account,
		kind="Reply", to_email=to, checks=results, checks_ok=results["ok"], risk="Low" if results["ok"] else "High",
		preview_html=_html(body), payload={"subject": subject, "body": body, "supplier": po.supplier, "purchase_order": po.name,
			**({"email_template": words.need} if words else {})})
	return _("chaser for {0}: {1}").format(po.name, name)


@registry.executor("po_chase")
def po_chase(proposal, agent):
	"""Send an approved chaser in the supplier's office hours; never once the goods are in."""
	p = _payload(proposal)
	po = p.get("purchase_order") or proposal.subject_name
	if received(po):
		raise registry.Cancel(_("{0} is received or closed").format(po))
	supplier = p.get("supplier") or frappe.db.get_value("Purchase Order", po, "supplier")
	if _cannot_chase(supplier):
		raise registry.Cancel(_("{0} is not chased: {1}").format(supplier, _cannot_chase(supplier)))
	if p.get("channel") == "whatsapp":
		message, _thread = _send_whatsapp(proposal, agent, supplier, p)
		return ("Excom Message", message)
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
	return specs + _whatsapp_follow_ups(agent, PER_TICK - len(specs))


def _whatsapp_follow_ups(agent, limit: int) -> list[dict]:
	"""Suppliers on the agent's RFQs who had no email (a person asked them) and have not quoted:
	one WhatsApp reminder each, when they and the agent have a WhatsApp number. Only for an RFQ a
	person submitted, and only to a supplier it really reached (contacted): no reminder about a
	request that never went out."""
	if limit <= 0 or not whatsapp_numbers(agent):
		return []
	specs = []
	rfqs = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "create_rfq", "status": "Done",
		"executed_name": ["is", "set"]}, pluck="executed_name", limit=50, order_by="creation desc")
	for rfq in frappe.get_all("Request for Quotation", filters=[["name", "in", rfqs or ["-"]], ["docstatus", "=", 1],
			["creation", ">", add_days(now_datetime(), -FOLLOW_UP_UNTIL_DAYS)],
			["creation", "<", add_days(now_datetime(), -FOLLOW_UP_DAYS)]], pluck="name"):
		for s in frappe.get_all("Request for Quotation Supplier", {"parent": rfq, "parenttype": "Request for Quotation"},
				["supplier", "email_id"]):
			key = f"rfq_follow_up:{rfq}:{s.supplier}"
			if (s.email_id or "").strip() or frappe.db.exists("Excom AI Task", {"agent": agent.name, "dedupe_key": key}):
				continue
			if supplier_email(s.supplier) or _quoted(rfq, s.supplier) or _cannot_ask(s.supplier) or not supplier_mobile(s.supplier):
				continue
			if not contacted(rfq, s.supplier):
				continue
			specs.append({"kind": "rfq_follow_up", "subject": ("Request for Quotation", rfq), "dedupe_key": key,
				"params": {"supplier": s.supplier}})
			if len(specs) >= limit:
				return specs
	return specs


CHASE_WAITING = ("Pending", "Held", "Approved", "Edited", "Executing")


def _chases(agent, company: str) -> list[dict]:
	"""Orders with a line due (or nearly) and goods still to come, earliest due line first; at most
	once a week each, and never while an earlier chaser still waits for a person or the sender."""
	if _off(agent, "po_chase"):
		return []
	specs = []
	today = getdate(nowdate())
	rows = frappe.db.sql("""SELECT poi.parent AS po, po.supplier, MIN(poi.schedule_date) AS due
		FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE po.docstatus = 1 AND po.company = %(company)s AND po.status NOT IN %(done)s
			AND poi.schedule_date <= %(ahead)s AND poi.schedule_date >= %(back)s
			AND (poi.received_qty < poi.qty OR EXISTS (SELECT 1 FROM `tabPurchase Receipt Item` pri
				WHERE pri.purchase_order_item = poi.name AND pri.docstatus = 1 AND pri.rejected_qty > 0))
		GROUP BY poi.parent, po.supplier ORDER BY due ASC LIMIT 50""", {"company": company, "done": DONE_PO,
		"ahead": add_days(today, CHASE_AHEAD_DAYS), "back": add_days(today, -CHASE_BACK_DAYS)}, as_dict=True)
	for r in rows:
		po = r.po
		if _cannot_chase(r.supplier):
			continue
		mine = {"agent": agent.name, "subject_doctype": "Purchase Order", "subject_name": po,
			"action": ["in", ("po_chase", "create_todo")]}
		if frappe.db.exists("Excom AI Proposal", {**mine, "status": ["in", CHASE_WAITING]}):
			continue  # the last one still waits: no second one on top
		last = frappe.db.get_value("Excom AI Proposal", mine, "creation", order_by="creation desc")
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
	key = f"discover_suppliers:{agent.name}:{nowdate()[:7]}"
	# once a month: tasks.add dedupes only open tasks, so a finished (or failed) search this month
	# counts too, or every tick would pay for a new one
	if any(tools.available(s) for s in DISCOVERY_SOURCES) and not frappe.db.exists("Excom AI Task",
			{"agent": agent.name, "dedupe_key": key}):
		specs.append({"kind": "discover_suppliers", "dedupe_key": key})
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

