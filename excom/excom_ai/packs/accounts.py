"""The Accounts pack (HLD-007 §8.2, Phase 2B): collections, promises, payment matching, the MSME
payables watch and statements on request. Nothing here posts, reconciles, submits or cancels.

Collections ladder, per customer (not per invoice)
    The ladder state is an Excom AI Enrollment in the agent's own "Collections ladder" Excom AI
    Sequence (made on first use; a person may edit its waits). The enrollment has no customer
    field, so it points at an Excom AI Company that this agent keeps for the customer
    (`customer` set, status Promoted): enrollment -> company -> customer. The enrollment holds:

        status        Active (on the ladder) · Paused (a promise in date, or waiting for a person
                      to match a payment the customer says they made) · Replied (the customer
                      wrote: the engine's inbound handler sets it; the responder below decides) ·
                      Stopped (a dispute: a person resumes it by setting it Active) ·
                      Completed (nothing outstanding) · Opted out
        current_step  the last step proposed; next_due when the ladder looks at it again;
        last_sent_on  when the last reminder went out; thread the conversation it went on;
        stop_reason   why it is paused or stopped, in words

    Steps (waits are the sequence's wait_days; halved, at least 3, when the oldest invoice is
    60+ days overdue, so older debts move faster):
        1  statement and a polite reminder, once the oldest invoice is 7 days past due
        2  firmer: asks on which date they will pay (10 days after step 1 went out)
        3  asks for a short call (10 days after step 2)
        4  handed to the owner: a ToDo, and Needs person on the conversation (7 days after 3)
    A step goes only after the one before it went out (or, in Shadow, was recorded); a rejected
    step is tried again after 7 days. A broken promise moves to the next step at once.

    It stops on payment (nothing outstanding: the planner closes the enrollment and the sender
    cancels anything approved), on a dispute (the reply says so, or the invoice is marked in a
    dispute field where the site has one), and pauses while a promise is in date. A promise still
    waiting for a person to approve it pauses the ladder a week at most; a promise is judged once,
    only on its own ladder and only on the invoices that were overdue when it was made.

    One Accounts agent chases a customer: a customer on another working Accounts agent's open
    ladder (same company) is left to it. A reminder never takes over a conversation a person has
    (Needs person, Hold, a colleague's) or another agent handles: it is not written (nor sent, if
    that changed after approval), the ladder waits a week and the owner gets a ToDo. A reply the
    agent could not read (its responder failed, or a person has the conversation) does not leave
    the ladder waiting in Replied: after a couple of hours it is Stopped and the owner told.
    Approved reminders wait out quiet dates (engine.gates.quiet_until) and go in the customer's
    office hours, in the time zone of their billing address's country.

    Messages go to the billing contact only (the customer's Contact marked is_billing_contact,
    else the primary contact's email), checked again when it is sent. With no such contact a
    person gets a ToDo to add one. Email, or an approved WhatsApp utility template when the
    contact has only a mobile number: the template steward's (HLD-008, `accounts.payment_reminder`,
    variables filled by code; its first send waits for a person), else a hand-made one found by name.

    Amounts and invoice numbers are written by code from ERPNext; the opening and closing words come
    from the steward's approved email template, else the model writes them in the customer's
    language. A code check refuses any digit in those words and any threat, waiver or discount; the
    sender compares the invoices with the ledger again and cancels if they changed (the next tick
    writes a fresh one).

Amounts are always in the currency the customer owes in (the party account's), and totals are
per currency, never added across currencies.

Statements: when the customer does not answer by email, the emailed statement carries a WhatsApp
notice (`accounts.statement_ready`) on the steward's approved template, sent after the email.

Promises: the `accounts` responder reads a customer's reply (rules first: opt-out, escalation),
then the model: promise (with a date), paid, dispute, statement, question, other.

Payment matching: unreconciled Bank Transactions against open invoices, by amount, reference and
party, as `suggest_match` proposals whose executor only makes a ToDo and a comment.

MSME watch: weekly, an internal list for finance of Micro and Small suppliers' unpaid Purchase
Invoices near or past the 45-day limit of section 43B(h) (15 days where the record says there is no
written agreement). MSME status comes from sigzen_msme's MSME Registration Detail rows, else flat
Supplier fields Frappe knows, else columns left in the table that Frappe no longer knows (read,
and flagged by the readiness rule). It goes to the owner and the team's accounts people as a
notification (with its email) and a ToDo. Nothing leaves the company.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	date_diff,
	flt,
	fmt_money,
	formatdate,
	get_datetime,
	get_system_timezone,
	getdate,
	now_datetime,
	nowdate,
)

from excom.excom_ai import checks, responder, sequencer
from excom.excom_ai import templates as steward
from excom.excom_ai.builder import geo
from excom.excom_ai.builder.readiness import check, people
from excom.excom_ai.builder.readiness import seed as seed_rules
from excom.excom_ai.builder.recipes import fact, label, money, need, recipe
from excom.excom_ai.engine import notify, proposals, registry, runner, tasks
from excom.excom_ai.engine.prompts import untrusted

LADDER_TITLE = "Collections ladder"
LADDER = (  # step, wait_days (step 1: days past due), what it is
	(1, 7, "Statement and a polite reminder"),
	(2, 10, "Firmer: asks on which date they will pay"),
	(3, 10, "Asks for a short call to agree a date"),
	(4, 7, "Handed to the owner"),
)
HAND_OVER_STEP = 4
OLD_DEBT_DAYS = 60  # waits are halved from here
PER_TICK = 5  # customers a tick may queue
LINES_MAX = 15  # invoice lines written into the text (the total covers all)
WA_LIST_CHARS = 180  # invoice numbers in one WhatsApp variable (the steward cuts values at 200)
REMINDER_NEED, STATEMENT_NEED = "accounts.payment_reminder", "accounts.statement_ready"  # templates.NEEDS
GRACE_DAYS = 1  # a promise is broken this many days after its date
RETRY_DAYS = 7  # a rejected or failed step is tried again after this
PAUSE_DAYS = {"paid": 5, "question": 7, "statement": 3, "no_contact": 7}
OPEN_ENROLLMENT = ("Active", "Paused", "Replied", "Stopped", "Opted out")
RECORDED = ("Done", "Approved", "Edited", "Shadow")  # a promise on record (Pending: not yet approved)
PROMISE_REVIEW_DAYS = 7  # how long a promise waiting for a person may pause the ladder
STUCK_REPLY_HOURS = 2  # a ladder still in Replied this long after the reply: nobody is reading it
WAITING = ("Pending", "Approved", "Edited", "Executing", "Sending", "Held")
DONE = ("Sent", "Shadow", "Done")
MSME_LIMIT_DAYS, MSME_NO_AGREEMENT_DAYS, MSME_NEAR_DAYS = 45, 15, 10
MSME_HOUR = 8  # the weekly list goes out from this hour (system time) on the first day of the week
FINANCE_ROLES = {"Accounts Manager", "Accounts User"}
MATCH_LIMIT = 50
DISPUTE_FIELDS = ("is_disputed", "disputed", "dispute", "in_dispute", "custom_is_disputed", "custom_disputed",
	"custom_dispute", "custom_in_dispute")
MSME_TYPE_FIELDS = ("msme_type", "custom_msme_type", "msme_category", "custom_msme_category", "enterprise_type",
	"msme_enterprise_type")
MSME_REG_FIELDS = ("msme_registration", "msme_registration_no", "custom_msme_registration_no", "udyam_registration_no",
	"custom_udyam_registration_no", "msme_no")
MSME_AGREEMENT_FIELDS = ("custom_contract_done", "msme_contract_done", "msme_agreement")

# Words a reminder never carries (HLD-007 §8.2: never waive, discount or threaten), in the languages
# the desks write in.
NEVER_SAY = re.compile(
	r"\b(legal action|lawyer|attorney|court|sue|penalt\w*|interest charge\w*|late fee\w*|waive\w*|write[- ]?off"
	r"|discount\w*|blacklist\w*|credit (bureau|rating|report|score)|stop (supply|supplies|deliver\w*)|suspend\w*"
	r"|consequence\w*|final notice|last chance|or else|abogado|demanda|tribunal|descuento|multa|intereses"
	r"|advogado|desconto|avocat|tribunal|remise)\b", re.IGNORECASE)
LABELS = {
	"en": {"dated": "dated", "due": "due", "total": "Total overdue",
		"more": "and {0} more invoices, included in the total",
		"open": "Open invoices", "outstanding": "Total outstanding", "paid": "Payments received in the last 90 days"},
	"es": {"dated": "del", "due": "vence", "total": "Total vencido", "more": "y {0} facturas más, incluidas en el total",
		"open": "Facturas abiertas", "outstanding": "Total pendiente", "paid": "Pagos recibidos en los últimos 90 días"},
	"pt": {"dated": "de", "due": "vence", "total": "Total vencido", "more": "e mais {0} faturas, incluídas no total",
		"open": "Faturas em aberto", "outstanding": "Total em aberto", "paid": "Pagamentos recebidos nos últimos 90 dias"},
	"fr": {"dated": "du", "due": "échéance", "total": "Total échu",
		"more": "et {0} autres factures, comprises dans le total",
		"open": "Factures ouvertes", "outstanding": "Total dû", "paid": "Paiements reçus ces 90 derniers jours"},
}

STEP_ASKS = {
	1: "A polite first reminder; the statement of open invoices follows below. Assume it may be an oversight; ask them "
		"to arrange payment, or to tell us if anything on the list looks wrong.",
	2: "A firmer second reminder: say we wrote earlier about these invoices and ask on which date they will pay. "
		"Still courteous.",
	3: "Ask for a short call this week with the person who handles payments, to agree on a date. Courteous.",
}
REMINDER_RULES = """You write the words around a payment reminder from a company's accounts team to a customer.
Code puts the invoice numbers, dates and amounts from the ledger between your opening and your closing, so:
- Never write any number, amount, date, invoice number or currency yourself.
- Write in the customer's language. Courteous and plain: this is a valued customer.
- Never threaten. Never mention penalties, interest, legal steps, stopping supplies or credit ratings.
- Never offer or hint at a discount, a waiver, a write-off, an instalment plan or new payment terms.
- No sign-off, signature or name at the end: the mail server adds the signature.
subject: 2 to 6 words, no numbers. opening: one or two sentences leading into the list. closing: one or two
sentences with the step's ask."""
REMINDER_SCHEMA = {
	"type": "object",
	"properties": {"subject": {"type": "string"}, "opening": {"type": "string"}, "closing": {"type": "string"}},
	"required": ["subject", "opening", "closing"],
	"additionalProperties": False,
}
STATEMENT_RULES = """You write a short covering note for a customer's statement of account, from the company's
accounts team. Code adds the statement itself (invoices, dates, amounts) below your note, so never write a
number, amount, date or invoice number. In the customer's language, courteous, two sentences at most, no
sign-off (the mail server signs). Never threaten, never offer a discount, waiver or new terms."""
STATEMENT_SCHEMA = {
	"type": "object",
	"properties": {"subject": {"type": "string"}, "note": {"type": "string"}},
	"required": ["subject", "note"],
	"additionalProperties": False,
}
INTENTS = ("promise", "paid", "dispute", "statement", "question", "other")
CLASSIFY_RULES = """Classify a customer's reply to a payment reminder into exactly one intent:
- promise: says they will pay on or by a date (promise_date as YYYY-MM-DD; promise_amount if they name one, else 0)
- paid: says they have already paid (reference: any payment reference, UTR or cheque number they give, else "")
- dispute: disagrees with an invoice or the amount: wrong price, goods damaged, short or not delivered, already returned
- statement: asks for a statement of account, a ledger or copies of the invoices
- question: asks something else that a person must answer
- other: anything else (thanks, an acknowledgement without a date)
When in doubt between dispute and anything else, choose dispute. Use today's date to turn "the 15th" or
"next Friday" into a date. summary: one sentence, in English."""
CLASSIFY_SCHEMA = {
	"type": "object",
	"properties": {
		"intent": {"type": "string", "enum": list(INTENTS)},
		"promise_date": {"type": "string"},
		"promise_amount": {"type": "number"},
		"reference": {"type": "string"},
		"language": {"type": "string"},
		"summary": {"type": "string"},
	},
	"required": ["intent", "promise_date", "promise_amount", "reference", "language", "summary"],
	"additionalProperties": False,
}


# ── ledger facts (code, never the model) ─────────────────────────────────────


def company_of(agent) -> str | None:
	return agent.company or frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


def _has(doctype: str, field: str) -> bool:
	return frappe.get_meta(doctype).has_field(field)


def open_invoices(customer: str, company: str | None, overdue_only: bool = False) -> list[frappe._dict]:
	"""Submitted Sales Invoices with something left to pay, oldest due first. Every amount is in the
	currency the customer owes in, the party account's (`currency`): ERPNext keeps the outstanding
	amount in it, and `amount` is the invoice total in it too (the invoice's own total when the
	invoice is in that currency, else its company-currency total, which is then the account's)."""
	filters = {"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]}
	if company:
		filters["company"] = company
	if overdue_only:
		filters["due_date"] = ["<", nowdate()]
	fields = ["name", "posting_date", "due_date", "grand_total", "base_grand_total", "outstanding_amount", "currency"]
	if _has("Sales Invoice", "party_account_currency"):
		fields.append("party_account_currency")
	rows = frappe.get_all("Sales Invoice", filters, fields, order_by="due_date asc, posting_date asc, name asc")
	today = getdate(nowdate())
	for r in rows:
		r.invoice_currency = r.currency
		r.currency = r.get("party_account_currency") or r.currency
		r.amount = flt(r.grand_total) if r.currency == r.invoice_currency else flt(r.base_grand_total)
		r.days_overdue = max(0, (today - getdate(r.due_date)).days) if r.due_date else 0
	return rows


def snapshot(invoices: list) -> dict:
	"""What a message was written from: invoice -> amount outstanding."""
	return {r["name"]: round(flt(r["outstanding_amount"]), 2) for r in invoices}


def totals(invoices: list) -> dict:
	"""Outstanding per currency: amounts in different currencies are never added together."""
	out: dict[str, float] = {}
	for r in invoices:
		out[r["currency"]] = out.get(r["currency"], 0) + flt(r["outstanding_amount"])
	return out


def credits(customer: str, company: str | None) -> dict:
	"""Money the customer has with us that is not set against an invoice yet (advances, credit
	notes). A reminder while these exist would overstate what is owed."""
	out: dict[str, float] = {}
	pe = {"party_type": "Customer", "party": customer, "docstatus": 1, "unallocated_amount": [">", 0],
		"payment_type": "Receive"}
	if company:
		pe["company"] = company
	for r in frappe.get_all("Payment Entry", pe, ["unallocated_amount", "paid_from_account_currency"]):
		cur = r.paid_from_account_currency or "?"
		out[cur] = out.get(cur, 0) + flt(r.unallocated_amount)
	si = {"customer": customer, "docstatus": 1, "outstanding_amount": ["<", 0]}
	if company:
		si["company"] = company
	fields = ["outstanding_amount", "currency"] + (["party_account_currency"] if _has("Sales Invoice",
		"party_account_currency") else [])
	for r in frappe.get_all("Sales Invoice", si, fields):
		cur = r.get("party_account_currency") or r.currency  # a credit note's outstanding is in the account's currency
		out[cur] = out.get(cur, 0) - flt(r.outstanding_amount)
	return out


def recent_payments(customer: str, company: str | None, days: int = 90) -> list[frappe._dict]:
	filters = {"party_type": "Customer", "party": customer, "docstatus": 1, "payment_type": "Receive",
		"posting_date": [">=", add_days(nowdate(), -days)]}
	if company:
		filters["company"] = company
	return frappe.get_all("Payment Entry", filters, ["name", "posting_date", "paid_amount", "paid_from_account_currency",
		"reference_no"], order_by="posting_date desc", limit=20)


def labels(language: str | None) -> dict:
	return LABELS.get((language or "en")[:2], LABELS["en"])


def invoice_lines(invoices: list, language: str | None, total_label: str = "total") -> list[str]:
	"""The lines of the message that carry numbers, written from the ledger."""
	lb = labels(language)
	lines = [f"{r['name']} ({lb['dated']} {formatdate(r['posting_date'])}, {lb['due']} {formatdate(r['due_date'])}): "
		f"{fmt_money(r['outstanding_amount'], currency=r['currency'])}" for r in invoices[:LINES_MAX]]
	if len(invoices) > LINES_MAX:
		lines.append(lb["more"].format(len(invoices) - LINES_MAX))
	for cur, amount in totals(invoices).items():
		lines.append(f"{lb[total_label]}: {fmt_money(amount, currency=cur)}")
	return lines


def statement_table(invoices: list, payments: list | None = None, language: str | None = None) -> str:
	"""The statement as an HTML table, built from the ledger."""
	e = frappe.utils.escape_html
	lb = labels(language)
	rows = "".join(
		f"<tr><td>{e(r['name'])}</td><td>{e(formatdate(r['posting_date']))}</td><td>{e(formatdate(r['due_date']))}</td>"
		f"<td style='text-align:right'>{e(fmt_money(r.get('amount', r['grand_total']), currency=r['currency']))}</td>"
		f"<td style='text-align:right'>{e(fmt_money(r['outstanding_amount'], currency=r['currency']))}</td>"
		f"<td style='text-align:right'>{cint(r.get('days_overdue'))}</td></tr>" for r in invoices)
	foot = "".join(f"<tr><th colspan='4' style='text-align:right'>{e(lb['outstanding'])}</th>"
		f"<th style='text-align:right'>{e(fmt_money(v, currency=c))}</th><th></th></tr>" for c, v in totals(invoices).items())
	html = (f"<p><b>{e(lb['open'])}</b></p><table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>"
		"<tr><th>Invoice</th><th>Date</th><th>Due</th><th>Amount</th><th>Outstanding</th><th>Days overdue</th></tr>"
		f"{rows}{foot}</table>")
	if payments:
		html += f"<p><b>{e(lb['paid'])}</b></p><ul>" + "".join(
			f"<li>{e(formatdate(p.posting_date))}: {e(fmt_money(p.paid_amount, currency=p.paid_from_account_currency))}"
			f"{' (' + e(p.reference_no) + ')' if p.reference_no else ''}</li>" for p in payments) + "</ul>"
	return html


def dispute_fields() -> list[tuple[str, str]]:
	"""Check fields where people mark a dispute, on invoices or customers, if the site has any."""
	return [(dt, f) for dt in ("Sales Invoice", "Customer") for f in DISPUTE_FIELDS
		if (frappe.get_meta(dt).get_field(f) or frappe._dict()).fieldtype == "Check"]


def disputed(customer: str, invoices: list) -> str | None:
	for dt, f in dispute_fields():
		if dt == "Customer" and frappe.db.get_value("Customer", customer, f):
			return _("the customer is marked as in dispute ({0})").format(f)
		if dt == "Sales Invoice":
			hit = frappe.db.get_value("Sales Invoice", {"name": ["in", [r["name"] for r in invoices] or ["-"]], f: 1})
			if hit:
				return _("{0} is marked as in dispute").format(hit)
	return None


# ── who to write to ──────────────────────────────────────────────────────────


def billing_contact(customer: str) -> frappe._dict | None:
	"""The customer's billing contact (marked is_billing_contact, with an email or a mobile), else
	the primary contact's email. Nobody else is ever written to."""
	rows = frappe.db.sql("""SELECT c.name, c.email_id, c.mobile_no, c.first_name, c.last_name, c.is_billing_contact,
			c.is_primary_contact
		FROM `tabContact` c JOIN `tabDynamic Link` dl ON dl.parent = c.name AND dl.parenttype = 'Contact'
			AND dl.link_doctype = 'Customer'
		WHERE dl.link_name = %s ORDER BY c.is_billing_contact DESC, c.is_primary_contact DESC, c.creation ASC""",
		customer, as_dict=True)
	pick = next((r for r in rows if r.is_billing_contact and (r.email_id or r.mobile_no)), None)
	if not pick:
		primary = frappe.db.get_value("Customer", customer, "customer_primary_contact")
		pick = next((r for r in rows if (r.name == primary or r.is_primary_contact) and r.email_id), None)
	if not pick:
		return None
	return frappe._dict(contact=pick.name, email=(pick.email_id or "").strip().lower() or None, mobile=pick.mobile_no,
		name=" ".join(filter(None, (pick.first_name, pick.last_name))) or label("Customer", customer),
		first_name=pick.first_name, billing=bool(pick.is_billing_contact))


def email_account(agent) -> str | None:
	rows = [r for r in agent.channels or [] if not r.paused
		and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "email"]
	rows.sort(key=lambda r: r.purpose == "Replies")
	return rows[0].channel_account if rows else None


def whatsapp_numbers(agent) -> list[str]:
	return [r.channel_account for r in agent.channels or [] if not r.paused
		and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "whatsapp"]


def whatsapp_template(agent) -> frappe._dict | None:
	"""A hand-made approved utility template for payment reminders on one of the agent's WhatsApp
	numbers, with at most three body variables (name, amount, invoices). The steward's own templates
	are left out: they are used only through `steward.find`, with their own variable map."""
	if not frappe.db.table_exists("WhatsApp Templates"):
		return None
	from excom.excom.whatsapp_template_utils import template_is_linked_to_account

	numbers = whatsapp_numbers(agent)
	if not numbers:
		return None
	for t in frappe.get_all("WhatsApp Templates", {"status": "APPROVED"}, ["name", "template_name", "category",
		"template", "xai_need"]):
		if t.xai_need or (t.category or "").upper() != "UTILITY" or not any(w in (t.template_name or t.name).lower()
				for w in ("payment", "reminder", "dues", "outstanding", "statement")):
			continue
		n = len(set(re.findall(r"\{\{\s*(\d+)\s*\}\}", t.template or "")))
		if n > 3:
			continue
		for account in numbers:
			if template_is_linked_to_account(t.name, account):
				return frappe._dict(account=account, template=t.name, variables=n, text=t.template or "")
	return None


def invoice_list(invoices: list, limit: int = WA_LIST_CHARS) -> str:
	"""Invoice numbers for one WhatsApp variable: whole numbers only, then "(+n)" for the rest (the
	total covers all of them)."""
	names = [r["name"] for r in invoices]
	shown = []
	for name in names:
		if shown and len(", ".join([*shown, name])) > limit:
			break
		shown.append(name)
	more = len(names) - len(shown)
	return ", ".join(shown) + (f" (+{more})" if more else "")


def reminder_values(contact, invoices: list) -> dict:
	"""The `accounts.payment_reminder` variables, from the contact and the ledger."""
	return {"contact_name": contact.first_name or contact.name,
		"total_due": "; ".join(fmt_money(v, currency=c) for c, v in totals(invoices).items()),
		"invoices": invoice_list(invoices)}


def steward_whatsapp(agent, key: str, language: str, values: dict) -> frappe._dict | None:
	"""The steward's approved template for this need on one of the agent's WhatsApp numbers, filled.
	None when there is none (`find` records the need for each number's WABA). With `error` set when
	one exists but these values do not fit it (missing, or the message too long)."""
	for account in whatsapp_numbers(agent):
		found = steward.find(agent, key, "WhatsApp", language, account)
		if not found:
			continue
		try:
			variables = steward.whatsapp_values(found, values)
		except ValueError as e:
			return frappe._dict(account=account, template=found["template"], need=found["need"], error=str(e))
		return frappe._dict(account=account, template=found["template"], need=found["need"], variables=variables,
			text=steward.fill(found["text"], variables), first_use=not found["first_use_done"])
	return None


def whatsapp_reminder(agent, contact, overdue: list, language: str) -> frappe._dict | None:
	"""What a WhatsApp reminder goes on: the steward's template, else a hand-made one (as before).
	None when neither can carry it; a steward template whose values do not fit is not swapped for
	a hand-made one (the reminder falls back to email, or a person adds a contact)."""
	from excom.excom_ai.send_rules import number_opted_in

	found = steward_whatsapp(agent, REMINDER_NEED, language, reminder_values(contact, overdue))  # records the need
	if not number_opted_in(contact.mobile):
		return None  # WhatsApp's policy: no opt-in, no business-started message (email, or a ToDo, instead)
	if found:
		return None if found.error else found
	wa = whatsapp_template(agent)
	if not wa:
		return None
	total = "; ".join(fmt_money(v, currency=c) for c, v in totals(overdue).items())
	variables = [contact.name, total, ", ".join(r.name for r in overdue[:5])][: wa.variables]
	return frappe._dict(account=wa.account, template=wa.template, need=None, variables=variables,
		text=steward.fill(wa.text, variables), first_use=False)


def is_steward_template(template: str | None) -> bool:
	return bool(template and frappe.db.get_value("WhatsApp Templates", template, "xai_need"))


def customer_zone(customer: str, agent) -> str:
	"""The customer's time zone: their billing (or primary) address's country, else the agent's
	first market's, else the site's."""
	row = frappe.db.sql("""SELECT a.country FROM `tabAddress` a JOIN `tabDynamic Link` dl ON dl.parent = a.name
		AND dl.parenttype = 'Address' AND dl.link_doctype = 'Customer' WHERE dl.link_name = %s
		ORDER BY (a.address_type = 'Billing') DESC, a.is_primary_address DESC LIMIT 1""", customer)
	tz = geo.main_timezone(row[0][0]) if row and row[0][0] else ""
	return tz or next((m.timezone for m in agent.markets or [] if m.timezone), None) or get_system_timezone() or "UTC"


def find_identity(email: str | None = None, phone: str | None = None) -> str | None:
	if email:
		name = frappe.db.get_value("Omni Identity", {"primary_email": email}) or frappe.db.get_value(
			"Omni Identity", {"normalized_email": email, "status": ["!=", "Merged"]})
		if name:
			return name
	if phone:
		from excom.excom.services.identity_sync import _find_existing_identity

		return _find_existing_identity(phone=phone) or None
	return None


def identity_for(email: str | None = None, phone: str | None = None, display: str = "") -> str:
	"""The person's Omni Identity, made if Excom has none yet (it is the conversation's anchor)."""
	name = find_identity(email, phone)
	if name:
		return name
	doc = {"doctype": "Omni Identity", "display_name": display or email or phone}
	if email:
		doc["primary_email"] = email
	if phone:
		doc.update(primary_phone=phone, primary_whatsapp=phone)
	return frappe.get_doc(doc).insert(ignore_permissions=True).name


def thread_for(channel: str, account: str | None, email: str | None = None, phone: str | None = None) -> str | None:
	"""The conversation a message to this person on this number or mailbox would go on, if there is
	one already (nothing is made here)."""
	identity = find_identity(email, phone) if account else None
	return frappe.db.get_value("Excom Thread", {"thread_key": f"{channel}:{account}:{identity}"}) if identity else None


def held_by_other(thread: str | None, agent) -> str | None:
	"""Why this conversation is not the agent's to write on, else None (engine.threads: the one rule
	Accounts and Purchase share). A reminder never takes such a conversation over."""
	from excom.excom_ai.engine import threads

	return threads.holder(thread, agent)


# ── ladder state ─────────────────────────────────────────────────────────────


def ladder(agent) -> str:
	"""The agent's collections sequence, made once with the default steps; a person may edit the waits."""
	name = frappe.db.get_value("Excom AI Sequence", {"agent": agent.name, "title": LADDER_TITLE})
	if name:
		return name
	return frappe.get_doc({"doctype": "Excom AI Sequence", "title": LADDER_TITLE, "agent": agent.name, "enabled": 1,
		"steps": [{"step": s, "wait_days": w, "channel": "Email", "purpose": p,
			"stop_on": "payment, dispute, a promise in date"}
			for s, w, p in LADDER]}).insert(ignore_permissions=True).name


def waits(sequence: str | None) -> dict[int, int]:
	rows = frappe.get_all("Excom AI Sequence Step", {"parent": sequence}, ["step", "wait_days"]) if sequence else []
	out = {s: w for s, w, _p in LADDER}
	out.update({cint(r.step): cint(r.wait_days) for r in rows})
	return out


def wait_for(sequence: str | None, step: int, days_overdue: int) -> int:
	days = waits(sequence).get(step, RETRY_DAYS)
	if step > 1 and days_overdue >= OLD_DEBT_DAYS:
		days = max(3, days // 2)
	return days


def enrollment_for(agent, customer: str, create: bool = True):
	"""The customer's ladder with this agent: the latest one still open, else a new one (a customer
	who paid and fell behind again starts from step 1)."""
	company = frappe.db.get_value("Excom AI Company", {"agent": agent.name, "customer": customer})
	name = frappe.db.get_value("Excom AI Enrollment", {"agent": agent.name, "company": company, "status": ["in",
		OPEN_ENROLLMENT]},
		order_by="creation desc") if company else None
	if name:
		return frappe.get_doc("Excom AI Enrollment", name)
	if not create:
		return None
	if not company:
		company = frappe.get_doc({"doctype": "Excom AI Company", "company_name": label("Customer", customer)[:140],
			"agent": agent.name,
			"customer": customer, "status": "Promoted", "source": "Manual"}).insert(ignore_permissions=True).name
	language = frappe.db.get_value("Customer", customer, "language")
	return frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": agent.name, "sequence": ladder(agent),
		"company": company,
		"status": "Active", "current_step": 0,
		"language": language if language and frappe.db.exists("Language",
			language) else None}).insert(ignore_permissions=True)


def customer_of(enrollment) -> str | None:
	return frappe.db.get_value("Excom AI Company", enrollment.company,
		"customer") if enrollment and enrollment.company else None


def _set(enrollment, **values):
	frappe.db.set_value("Excom AI Enrollment", enrollment.name, values, update_modified=True)
	enrollment.update(values)


def cancel_open(enrollment, why: str) -> None:
	for p in frappe.get_all("Excom AI Proposal", {"enrollment": enrollment.name, "status": ["in", ("Pending",
		"Approved", "Edited")]},
			pluck="name"):
		frappe.db.set_value("Excom AI Proposal", p, {"status": "Cancelled", "step_key": None, "hold_reason": why},
			update_modified=False)


def close(enrollment, status: str, why: str) -> None:
	_set(enrollment, status=status, stop_reason=why, next_due=None)
	cancel_open(enrollment, why)


def close_paid(agent, company: str | None) -> int:
	"""Ladders whose customer owes nothing any more are Completed, and what was waiting is cancelled."""
	rows = frappe.db.sql("""SELECT e.name FROM `tabExcom AI Enrollment` e JOIN `tabExcom AI Company` c ON c.name = e.company
		WHERE e.agent = %(agent)s AND e.status IN ('Active', 'Paused', 'Replied') AND IFNULL(c.customer, '') != ''
			AND NOT EXISTS (SELECT 1 FROM `tabSales Invoice` si WHERE si.customer = c.customer AND si.docstatus = 1
				AND si.outstanding_amount > 0 AND (%(co)s IS NULL OR si.company = %(co)s))
		LIMIT 50""", {"agent": agent.name, "co": company}, pluck=True)
	for name in rows:
		close(frappe.get_doc("Excom AI Enrollment", name), "Completed", _("Paid: nothing outstanding"))
	return len(rows)


def last_step(enrollment) -> frappe._dict | None:
	return frappe.db.get_value("Excom AI Proposal", {"enrollment": enrollment.name, "step": [">", 0]},
		["name", "step", "status", "sent_on", "creation", "decided_on", "modified"], as_dict=True, order_by="creation desc")


def last_promise(customer: str, agent, enrollment=None) -> frappe._dict | None:
	"""The latest promise on record (recorded, or approved; in Shadow, what would have been) made on
	this ladder: after it started, so a promise from an earlier ladder for the same customer (paid
	off since, then behind again) is never judged. A promise still waiting for a person to approve
	it does not hold the ladder (see PROMISE_REVIEW_DAYS)."""
	filters = {"agent": agent.name, "action": "record_promise", "subject_doctype": "Customer", "subject_name": customer,
		"status": ["in", RECORDED]}
	if enrollment:
		filters["creation"] = [">=", get_datetime(enrollment.started_on or enrollment.creation)]
	for row in frappe.get_all("Excom AI Proposal", filters, ["name", "payload", "status", "creation"],
			order_by="creation desc", limit=5):
		p = frappe._dict(frappe.parse_json(row.payload or "{}"))
		if enrollment and p.enrollment and p.enrollment != enrollment.name:
			continue  # made on another ladder
		p.proposal, p.status, p.made_on = row.name, row.status, row.creation
		return p
	return None


def promise_kept(promise, customer: str, company: str | None) -> bool:
	"""Was the promise kept? Judged only on the invoices that were overdue when it was made (in its
	currency): an invoice that fell due afterwards was never part of it."""
	names = list(promise.invoices or {})
	filters = {"customer": customer, "docstatus": 1}
	if company:
		filters["company"] = company
	if names:
		filters["name"] = ["in", names]
	else:  # a promise recorded before the invoices were kept with it: those due when it was made
		filters["due_date"] = ["<", getdate(promise.made_on)]
	fields = ["outstanding_amount", "currency"] + (["party_account_currency"] if _has("Sales Invoice",
		"party_account_currency") else [])
	owed = sum(flt(r.outstanding_amount) for r in frappe.get_all("Sales Invoice", filters, fields)
		if not promise.currency or (r.get("party_account_currency") or r.currency) == promise.currency)
	return owed <= flt(promise.overdue_then) - flt(promise.amount) + 0.5


def mark_promise(promise, outcome: str) -> None:
	"""A promise is judged once: kept or broken is written on its proposal."""
	payload = frappe.parse_json(frappe.db.get_value("Excom AI Proposal", promise.proposal, "payload") or "{}") or {}
	payload["judged"] = outcome
	frappe.db.set_value("Excom AI Proposal", promise.proposal, "payload", json.dumps(payload), update_modified=False)


def next_step(enrollment, oldest_overdue: int, broken: bool = False, not_before=None) -> tuple[int | None, object, str]:
	"""(step to propose now, or None; when to look again; why)."""
	now = now_datetime()
	last = last_step(enrollment)
	if not last:
		start = waits(enrollment.sequence).get(1, 7)
		if oldest_overdue < start:
			return None, add_days(now,
				start - oldest_overdue), _("overdue {0} days; the ladder starts at {1}").format(oldest_overdue, start)
		return 1, now, ""
	if last.status in WAITING:
		return None, add_days(now, 1), _("step {0} is waiting ({1})").format(last.step, last.status)
	if last.status in DONE:
		step = cint(last.step) + 1
		due = add_days(get_datetime(last.sent_on or last.creation), wait_for(enrollment.sequence, step, oldest_overdue))
	elif last.status in ("Rejected", "Failed"):
		step = cint(last.step)
		due = add_days(get_datetime(last.decided_on or last.modified), RETRY_DAYS)
	else:  # Cancelled, Expired: the step did not happen and may be written again
		step, due = cint(last.step), now
	if step > HAND_OVER_STEP:
		return None, None, _("with the owner since step {0}").format(HAND_OVER_STEP)
	if broken:
		due = now
	if not_before and get_datetime(not_before) > get_datetime(due):
		due = not_before
	if get_datetime(due) > now:
		return None, due, _("step {0} is due on {1}").format(step, formatdate(getdate(due)))
	return step, now, ""


# ── the ladder ───────────────────────────────────────────────────────────────


def other_ladders(agent, company: str | None, customer: str | None = None) -> dict:
	"""Customers on another working Accounts agent's open ladder for the same company: customer ->
	when the earliest of those ladders began. One agent chases a customer, never two."""
	rows = frappe.db.sql("""SELECT c.customer, a.company, MIN(e.creation) AS began FROM `tabExcom AI Enrollment` e
		JOIN `tabExcom AI Company` c ON c.name = e.company
		JOIN `tabExcom AI Agent` a ON a.name = e.agent
		JOIN `tabExcom AI Agent Type` t ON t.name = a.agent_type
		WHERE e.agent != %(agent)s AND t.pack = 'accounts' AND e.status IN %(open)s
			AND a.status IN ('Shadow', 'Live', 'On Hold') AND IFNULL(c.customer, '') != ''
			AND (%(customer)s IS NULL OR c.customer = %(customer)s)
		GROUP BY c.customer, a.company""", {"agent": agent.name, "open": OPEN_ENROLLMENT, "customer": customer},
		as_dict=True)
	out = {}
	for r in rows:
		if company and company_of(frappe._dict(company=r.company)) != company:
			continue
		out[r.customer] = min(out.get(r.customer) or r.began, r.began)
	return out


def chased_by_another(customer: str, own_began, others: dict) -> bool:
	"""Another agent's ladder has this customer: it keeps them unless this agent's own began first."""
	began = others.get(customer)
	return bool(began) and (not own_began or get_datetime(began) <= get_datetime(own_began))


def due_customers(agent, company: str | None, limit: int = 1000) -> list[str]:
	"""Customers with overdue invoices whose ladder needs a look now: broken or due promises first,
	then the most overdue. Customers already handled today (next_due later), disputed, opted out,
	waiting on a reply, or with the owner are left alone."""
	rows = frappe.db.sql("""SELECT si.customer, MIN(si.due_date) AS oldest FROM `tabSales Invoice` si
		LEFT JOIN `tabCustomer` cu ON cu.name = si.customer
		WHERE si.docstatus = 1 AND si.outstanding_amount > 0 AND si.due_date < %(today)s
			AND (%(co)s IS NULL OR si.company = %(co)s) AND IFNULL(cu.is_internal_customer, 0) = 0
		GROUP BY si.customer ORDER BY oldest ASC, si.customer ASC LIMIT %(limit)s""",
		{"today": nowdate(), "co": company, "limit": limit}, as_dict=True)
	state = {}
	for e in frappe.db.sql("""SELECT c.customer, e.status, e.next_due, e.current_step, e.creation
			FROM `tabExcom AI Enrollment` e JOIN `tabExcom AI Company` c ON c.name = e.company
			WHERE e.agent = %s AND e.status IN %s ORDER BY e.creation ASC""", (agent.name, OPEN_ENROLLMENT), as_dict=True):
		state[e.customer] = e  # the newest wins
	others = other_ladders(agent, company)
	start = waits(frappe.db.get_value("Excom AI Sequence", {"agent": agent.name, "title": LADDER_TITLE})).get(1, 7)
	now, today = now_datetime(), getdate(nowdate())
	promised, due = [], []
	for r in rows:
		s = state.get(r.customer)
		if chased_by_another(r.customer, s.creation if s else None, others):
			continue
		if not s:
			if (today - getdate(r.oldest)).days >= start:
				due.append(r.customer)
			continue
		if s.status in ("Stopped", "Opted out", "Replied"):
			continue
		if s.next_due and get_datetime(s.next_due) > now:
			continue
		if s.status == "Active" and cint(s.current_step) >= HAND_OVER_STEP:
			continue
		(promised if s.status == "Paused" else due).append(r.customer)
	return promised + due


def unread_replies(agent) -> int:
	"""Ladders left in Replied with nobody reading the reply: the responder failed (or found nothing it
	could act on), or no task was queued because a person has the conversation. Without this the
	ladder would wait there silently for ever. Each is Stopped, with a ToDo and an alert for the
	owner: a person reads the reply and sets the ladder Active to resume it."""
	cutoff = add_to_date(now_datetime(), hours=-STUCK_REPLY_HOURS)
	stuck = frappe.get_all("Excom AI Enrollment", {"agent": agent.name, "status": "Replied", "modified": ["<", cutoff]},
		["name", "thread", "company", "modified"], limit=20)
	done = 0
	for e in stuck:
		# the task queued for this reply (one from an earlier reply does not count)
		task = frappe.db.get_value("Excom AI Task", {"agent": agent.name, "kind": "inbound", "thread": e.thread,
			"creation": [">=", add_to_date(e.modified, minutes=-5)]}, ["status", "error", "outcome"], as_dict=True,
			order_by="creation desc") if e.thread else None
		if task and task.status in ("Queued", "Running", "Held"):
			continue  # still on its way (a retry, a budget wait, the agent on hold)
		if task and task.status == "Failed":
			why = _("the agent could not read the reply ({0})").format((task.error or "")[:200])
		elif task:
			why = _("the agent read the reply but could not act on it ({0})").format((task.outcome or "")[:200])
		else:
			why = _("a person has the conversation, so the agent did not read the reply")
		enr = frappe.get_doc("Excom AI Enrollment", e.name)
		_set(enr, status="Stopped", next_due=None, stop_reason=_("Replied; {0}").format(why))
		customer = customer_of(enr)
		text = _("{0} replied to a collections reminder, but {1}. Read the reply and answer it; set the ladder ({2}) "
			"back to Active to resume the reminders.").format(label("Customer", customer) if customer else _("A customer"),
			why, enr.name)
		proposals.propose(None, agent, "create_todo", subject=("Customer", customer) if customer else
			("Excom AI Enrollment", enr.name), risk="Low", payload={"allocated_to": agent.acting_owner or agent.owner_user,
				"priority": "High", "date": nowdate(), "text": text})
		notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user],
			_("Collections: a reply needs you ({0} AI)").format(agent.agent_name), message=frappe.utils.escape_html(text),
			doctype="Excom Thread" if e.thread else "Excom AI Enrollment", name=e.thread or enr.name)
		done += 1
	return done


@registry.planner("accounts")
def plan(agent) -> list[dict]:
	"""Each tick: close ladders that were paid, hand unread replies to a person, queue a few
	customers' next step, send what was approved; once a week the MSME list; once a day
	payment-match suggestions."""
	if agent.status not in ("Live", "Shadow"):
		return []
	company = company_of(agent)
	close_paid(agent, company)
	unread_replies(agent)
	specs = [{"kind": "collections_step", "subject": ("Customer", c)} for c in due_customers(agent, company)[:PER_TICK]]
	if agent.status == "Live" and frappe.db.exists("Excom AI Proposal", {"agent": agent.name, "status": ["in",
		("Approved", "Edited")],
			"action": ["in", proposals.SENDS]}):
		specs.append({"kind": "send_due", "dedupe_key": f"send_due:{agent.name}"})
	now = now_datetime()
	year, week, _day = getdate(now).isocalendar()
	key = f"msme_watch:{year}-W{week:02d}"
	if now.hour >= MSME_HOUR and not frappe.db.exists("Excom AI Task", {"agent": agent.name, "dedupe_key": key}):
		specs.append({"kind": "msme_watch", "dedupe_key": key})
	key = f"match_payments:{nowdate()}"
	if (frappe.db.exists("DocType", "Bank Transaction") and not frappe.db.exists("Excom AI Task", {"agent": agent.name,
		"dedupe_key": key})
			and frappe.db.exists("Bank Transaction", {"status": ["in", ("Pending", "Unreconciled")], "deposit": [">", 0]})):
		specs.append({"kind": "match_payments", "dedupe_key": key})
	return specs


@registry.task_kind("collections_step", priority=registry.PRIORITY["write"], outreach=True, effort="write")
def collections_step(task, agent) -> str:
	"""One customer's next step on the ladder, if one is due."""
	customer = task.subject_name
	company = company_of(agent)
	own = enrollment_for(agent, customer, create=False)
	if chased_by_another(customer, own.creation if own else None, other_ladders(agent, company, customer)):
		if own:
			_set(own, next_due=add_days(now_datetime(), RETRY_DAYS))
		return _("{0}: another Accounts agent's ladder has this customer; left to it").format(customer)
	enr = own or enrollment_for(agent, customer)
	if enr.status in ("Stopped", "Opted out"):
		return _("{0}: {1}; left to a person").format(customer, enr.stop_reason or enr.status)
	if enr.status == "Replied":
		return _("{0}: they wrote back; the reply is read first").format(customer)
	if not open_invoices(customer, company):
		close(enr, "Completed", _("Paid: nothing outstanding"))
		return _("{0}: paid").format(customer)
	overdue = open_invoices(customer, company, overdue_only=True)
	if not overdue:
		_set(enr, next_due=add_days(now_datetime(), 1))
		return _("{0}: nothing overdue").format(customer)
	why = disputed(customer, overdue)
	if why:
		close(enr, "Stopped", _("Dispute: {0}").format(why))
		return _("{0}: {1}; the ladder stopped").format(customer, why)
	now = now_datetime()
	if enr.status == "Paused" and enr.next_due and get_datetime(enr.next_due) > now:
		return _("{0}: paused until {1} ({2})").format(customer, enr.next_due, enr.stop_reason or "")
	broken, not_before = False, None
	if enr.status == "Paused":
		promise = last_promise(customer, agent, enr)
		if promise and promise.get("date") and not promise.get("judged"):
			if getdate(promise.date) >= getdate(nowdate()):
				not_before = add_days(get_datetime(promise.date), 1)  # still in date
			elif promise_kept(promise, customer, company):
				mark_promise(promise, "kept")
			else:
				broken = True
				mark_promise(promise, "broken")
				frappe.get_doc("Customer", customer).add_comment("Comment",
					_("{0} (AI): the promise to pay {1} by {2} was not kept; "
					"the collections ladder moves to its next step.").format(agent.agent_name,
					frappe.utils.escape_html(fmt_money(promise.get("amount"), currency=promise.get("currency"))),
						formatdate(promise.date)))
		_set(enr, status="Active", stop_reason=None)
	step, due, why = next_step(enr, max(r.days_overdue for r in overdue), broken, not_before)
	if step is None:
		_set(enr, next_due=due)
		return _("{0}: {1}").format(customer, why)
	if step >= HAND_OVER_STEP:
		return hand_to_owner(task, agent, enr, customer, overdue)
	return write_reminder(task, agent, enr, customer, overdue, step, broken)


def _no_contact(task, agent, enr, customer: str) -> str:
	"""Nobody to write to: a person adds the billing contact (one open ToDo at a time), and the
	ladder looks again in a week."""
	if enr:
		_set(enr, status="Paused", next_due=add_days(now_datetime(), PAUSE_DAYS["no_contact"]),
			stop_reason=_("No billing contact with an email or mobile"))
	if not frappe.db.exists("ToDo", {"reference_type": "Customer", "reference_name": customer, "status": "Open",
			"description": ["like", "%billing contact%"]}):
		proposals.propose(task, agent, "create_todo", subject=("Customer", customer), risk="Low",
			payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "Medium",
				"text": _("Add a billing contact (Contact marked 'Is Billing Contact', with an email) for {0}: it has overdue "
					"invoices and the collections ladder has nobody to write to.").format(label("Customer", customer))})
	return _("{0}: no billing contact; a person adds one").format(customer)


def _conversation_held(task, agent, enr, customer: str, step: int, why: str) -> str:
	"""The conversation the reminder would go on is a person's or another agent's. The agent does not
	take it over and does not write there: the ladder waits a week and the owner is told (one open
	ToDo at a time), to send it themselves or hand the conversation back."""
	again = add_days(now_datetime(), RETRY_DAYS)
	_set(enr, status="Paused", next_due=again, stop_reason=_("Reminder not written: {0}").format(why))
	if not frappe.db.exists("ToDo", {"reference_type": "Customer", "reference_name": customer, "status": "Open",
			"description": ["like", "%collections reminder%was not written%"]}):
		proposals.propose(task, agent, "create_todo", subject=("Customer", customer), risk="Low",
			payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "Medium",
				"text": _("{0} has overdue invoices, but the collections reminder (step {1}) was not written: {2}. Remind "
					"them from that conversation yourself, or hand it back to the agent; the ladder looks again on {3}.").format(
					label("Customer", customer), step, why, formatdate(getdate(again)))})
	return _("{0}: step {1} not written: {2}").format(customer, step, why)


def whatsapp_recipient(mobile: str | None) -> tuple[bool, str]:
	"""The WhatsApp recipient check, for a reviewer to see: a real mobile number, nobody holding it on
	do-not-contact or opted out of WhatsApp, and an opt-in (the send rules check it again at sending)."""
	from excom.excom.services.whatsapp_service import identities_for_recipient
	from excom.excom_ai.send_rules import whatsapp_opt_in

	number = (mobile or "").strip()
	if not 8 <= len(re.sub(r"\D", "", number)) <= 15:
		return False, _("'{0}' is not a mobile number").format(number)
	names = identities_for_recipient(number)
	for row in frappe.get_all("Omni Identity", {"name": ["in", names or ["-"]]},
			["name", "do_not_contact", "whatsapp_opt_out"]):
		if row.do_not_contact:
			return False, _("{0} is on do-not-contact").format(number)
		if row.whatsapp_opt_out:
			return False, _("{0} opted out of WhatsApp").format(number)
	if not whatsapp_opt_in(names):
		return False, _("{0} has no WhatsApp opt-in").format(number)
	return True, _("WhatsApp {0} (billing contact, opted in)").format(number)


def _model_words_check(texts: list[str], customer: str) -> tuple[dict, dict]:
	"""The model's words carry no numbers (amounts come only from ERPNext) and never threaten,
	waive or discount."""
	words = " ".join(texts)
	for name in {customer, label("Customer", customer)}:
		words = words.replace(name or "", " ")
	digits = re.findall(r"\d[\d.,]*", words)
	bad = sorted({m.group(0).lower() for m in NEVER_SAY.finditer(words)})
	return ({"ok": not digits, "why": _("numbers in the wording: {0}").format(", ".join(digits[:3])) if digits
			else _("every amount and invoice is from the ledger")},
		{"ok": not bad,
			"why": _("never said in collections: {0}").format(", ".join(bad)) if bad else _("no threat, waiver or discount")})


def _finish_checks(results: dict, texts: list[str], customer: str, company: str | None) -> dict:
	results["amounts"], results["collections"] = _model_words_check(texts, customer)
	held = {c: v for c, v in credits(customer, company).items() if v > 0.5}
	results["credits"] = {"ok": not held, "why": _("unallocated payments or credit notes to set off first: {0}").format(
		", ".join(fmt_money(v, currency=c) for c, v in held.items())) if held else _("no unallocated credits")}
	results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	return results


def write_reminder(task, agent, enr, customer: str, overdue: list, step: int, broken: bool = False) -> str:
	company = company_of(agent)
	contact = billing_contact(customer)
	account = email_account(agent)
	language = enr.language or frappe.db.get_value("Customer", customer, "language") or "en"
	wa = whatsapp_reminder(agent, contact, overdue, language) \
		if contact and contact.mobile and not (contact.email and account) else None
	if not contact or not ((contact.email and account) or wa):
		return _no_contact(task, agent, enr, customer)
	thread = thread_for("whatsapp", wa.account, phone=contact.mobile) if wa else thread_for("email", account,
		email=contact.email)
	held = held_by_other(thread, agent)
	if held:
		return _conversation_held(task, agent, enr, customer, step, held)
	lines = invoice_lines(overdue, language)
	base = {"step": step, "customer": customer, "invoices": snapshot(overdue), "lines": lines, "language": language,
		"scope": "overdue", "with_table": step == 1, "broken_promise": broken}
	if wa:
		why = _("approved template {0} from the template steward{1}").format(wa.template,
			_("; its first send waits for a person") if wa.first_use else "") if wa.need \
			else _("approved utility template {0}").format(wa.template)
		ok, who = whatsapp_recipient(contact.mobile)
		results = {"recipient": {"ok": ok, "why": who}, "channel": {"ok": True, "why": wa.account},
			"template": {"ok": True, "why": why}}
		results = _finish_checks(results, [], customer, company)
		name = proposals.propose(task, agent, "payment_reminder", subject=("Customer", customer), enrollment=enr.name,
			step=step,
			kind="Sequence", channel_account=wa.account, risk="Medium" if results["ok"] else "High", checks=results,
			checks_ok=results["ok"], payload={**base, "channel": "whatsapp", "template": wa.template,
				"variables": wa.variables, "mobile": contact.mobile, "contact": contact.contact, "body": wa.text,
				**({"template_need": wa.need} if wa.need else {})},
			preview_html=f"<p>{frappe.utils.escape_html(wa.text)}</p>")
	else:
		found = steward.find(agent, REMINDER_NEED, "Email", language, None)
		if found:
			# A person approved these words once: no model call. The checks read the template's own
			# words; the values filled into them come from the contact and the ledger.
			subject, opening, closing = steward.email_parts(found, reminder_values(contact, overdue))
			own_words = list(steward.email_parts(found, {}, strict=False))
		else:
			r = runner.ai(task, agent, pack_instructions=REMINDER_RULES, output_schema=REMINDER_SCHEMA,
				prompt=_("Customer: {0}. Their language: {1}. Step {2} of the collections ladder. {3}{4}").format(
					label("Customer", customer), language, step, STEP_ASKS[step],
					_(" They promised to pay by a date that has passed; mention it calmly.") if broken else ""))
			subject = (r.data.get("subject") or "").strip()
			opening, closing = (r.data.get("opening") or "").strip(), (r.data.get("closing") or "").strip()
			own_words = [subject, opening, closing]
		body = "\n\n".join(filter(None, [opening, "\n".join(lines), closing]))
		results = checks.check(agent, contact.email, subject, body, kind="Statement", account=account, language=language,
			word_limit=120 + 15 * len(lines))
		results = _finish_checks(results, own_words, customer, company)
		preview = paragraphs(body) + (statement_table(open_invoices(customer, company),
			language=language) if step == 1 else "")
		name = proposals.propose(task, agent, "payment_reminder", subject=("Customer", customer), enrollment=enr.name,
			step=step,
			kind="Sequence", to_email=contact.email, channel_account=account, risk="Medium" if results["ok"] else "High",
			checks=results, checks_ok=results["ok"], preview_html=preview,
			payload={**base, "channel": "email", "subject": subject, "opening": opening, "closing": closing, "body": body,
				"contact": contact.contact, **({"template_need": found["need"]} if found else {})})
	_set(enr, current_step=step, next_due=add_days(now_datetime(), 1))
	return _("{0}: step {1} proposed to the billing contact ({2})").format(customer, step, name or _("policy off"))


def hand_to_owner(task, agent, enr, customer: str, overdue: list) -> str:
	"""Step 4: the owner takes it. A ToDo with what is owed and what was sent, and the conversation
	(if there is one) waits for a person."""
	owed = "; ".join(fmt_money(v, currency=c) for c, v in totals(overdue).items())
	sent = frappe.get_all("Excom AI Proposal", {"enrollment": enr.name, "status": ["in", DONE]}, ["step", "sent_on",
		"creation"],
		order_by="step asc")
	history = ", ".join(_("step {0} on {1}").format(s.step, formatdate(getdate(s.sent_on or s.creation))) for s in sent)
	name = proposals.propose(task, agent, "create_todo", subject=("Customer", customer), enrollment=enr.name,
		step=HAND_OVER_STEP,
		risk="Medium", payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "High", "date": nowdate(),
			"text": _("Collections: {0} still owes {1} on {2} overdue invoices (oldest {3} days). Reminders sent: {4}. "
				"Please call them; the agent will not write again.").format(label("Customer", customer), owed, len(overdue),
				max(r.days_overdue for r in overdue), history or _("none"))})
	if enr.thread and agent.status == "Live":
		frappe.db.set_value("Excom Thread", enr.thread, {"ai_mode": "Needs person", "ai_intent": "collections"},
			update_modified=False)
		frappe.get_doc("Excom Thread", enr.thread).add_comment("Comment", _("{0} (AI) handed this conversation to a person: "
			"the collections ladder reached its last step.").format(agent.agent_name))
		notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user],
			_("Collections: {0} is yours now ({1} AI)").format(label("Customer", customer), agent.agent_name),
			doctype="Excom Thread", name=enr.thread)
	_set(enr, current_step=HAND_OVER_STEP, next_due=None, stop_reason=_("With the owner"))
	return _("{0}: handed to the owner ({1})").format(customer, name or _("policy off"))


def paragraphs(body: str) -> str:
	return "".join(f"<p>{frappe.utils.escape_html(p).replace(chr(10), '<br>')}</p>" for p in (body or "").split("\n\n") if p.strip())


@registry.previewer("payment_reminder", "send_statement")
def preview(proposal, agent, payload: dict) -> str:
	"""After a person edits a reminder or a statement: the message as _send will compose it (no
	cold-mail footer; the table from the ledger as it is now)."""
	p = frappe._dict(payload)
	if p.channel == "whatsapp":
		return f"<p>{frappe.utils.escape_html(p.body or '')}</p>"
	html = paragraphs(p.body or "")
	if p.with_table:
		company = company_of(agent)
		html += statement_table(open_invoices(proposal.subject_name, company), recent_payments(proposal.subject_name, company),
			p.language)
	return html + notice_html(p.notice)


def notice_html(notice: dict | None) -> str:
	n = frappe._dict(notice or {})
	if not n.template:
		return ""
	e = frappe.utils.escape_html
	return f"<p><i>{e(_('And on WhatsApp to {0}:').format(n.mobile))}</i><br>{e(n.text or '')}</p>"


# ── sending ──────────────────────────────────────────────────────────────────


def _send(proposal, agent):
	"""Send an approved reminder or statement to the billing contact, in their office hours, if the
	ledger still says what the message says."""
	p = frappe._dict(frappe.parse_json(proposal.payload or "{}"))
	customer = proposal.subject_name
	company = company_of(agent)
	fresh = open_invoices(customer, company, overdue_only=p.scope == "overdue")
	if not fresh:
		raise registry.Cancel(_("{0} owes nothing now").format(customer))
	if snapshot(fresh) != {k: round(flt(v), 2) for k, v in (p.invoices or {}).items()}:
		raise registry.Cancel(_("the balance changed since this was written; a fresh one follows"))
	contact = billing_contact(customer)
	if not contact or (p.channel == "email" and contact.email != (proposal.to_email or "").lower()) or (
			p.channel == "whatsapp" and contact.mobile != p.mobile):
		raise registry.Cancel(_("the billing contact changed since this was written"))
	zone = customer_zone(customer, agent)
	now = now_datetime()
	if not sequencer.in_window(now, zone):
		raise registry.Defer(sequencer.next_window(now, zone), _("outside the customer's office hours ({0})").format(zone))
	from excom.excom.services.thread_service import upsert_thread

	if p.channel == "whatsapp":
		identity = identity_for(phone=contact.mobile, display=contact.name)
		thread = upsert_thread(identity, "whatsapp", proposal.channel_account)
	else:
		account = proposal.channel_account or email_account(agent)
		row = next((r for r in agent.channels or [] if r.channel_account == account), None)
		if row and cint(row.daily_cap) and frappe.db.count("Excom AI Proposal", {"agent": agent.name,
			"channel_account": account,
				"status": "Sent", "sent_on": [">=", get_datetime(nowdate())]}) >= cint(row.daily_cap):
			raise registry.Defer(sequencer.next_window(add_days(get_datetime(nowdate()), 1), zone),
				_("today's cap on {0}").format(account))
		identity = identity_for(email=contact.email, display=contact.name)
		thread = upsert_thread(identity, "email", account)
	# A person (Needs person, Hold, a colleague's conversation) or another agent may have taken the
	# conversation since this was written: it is never taken over. The ladder's next look sees the
	# same and tells the owner (see _conversation_held).
	held = held_by_other(thread, agent)
	if held:
		raise registry.Cancel(_("not sent: {0}").format(held))
	if p.channel == "whatsapp":
		from excom.excom.api.chat import send_template_to_thread

		if is_steward_template(p.template):  # marks the template's first use once a person approved a send
			sent = steward.send_whatsapp(proposal, agent, thread, p.template, p.variables or [])
		else:
			sent = send_template_to_thread(thread, p.template, variables=json.dumps(p.variables or []))
		message = (sent or {}).get("message_name")
	else:
		from excom.excom.channels.email.outbound import send_email_reply

		html = paragraphs(p.body)
		if p.with_table:
			html += statement_table(open_invoices(customer, company), recent_payments(customer, company), p.language)
		message = send_email_reply(thread, to=contact.email, subject=p.subject or "", body_html=html)
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime(),
		"thread": thread},
		update_modified=False)
	from excom.excom_ai.engine import threads

	threads.claim(thread, agent)  # a conversation nobody else holds becomes the agent's; its mode is kept
	if proposal.enrollment:
		frappe.db.set_value("Excom AI Enrollment", proposal.enrollment, {"thread": thread, "last_sent_on": now_datetime(),
			"current_step": cint(proposal.step)}, update_modified=False)
	return ("Excom Message", message)


@registry.executor("payment_reminder")
def payment_reminder(proposal, agent):
	enr = frappe.db.get_value("Excom AI Enrollment", proposal.enrollment, ["status", "stop_reason"], as_dict=True) \
		if proposal.enrollment else None
	if enr and enr.status != "Active":
		raise registry.Cancel(_("the ladder is {0}: {1}").format(enr.status, enr.stop_reason or ""))
	return _send(proposal, agent)


@registry.executor("send_statement")
def send_statement(proposal, agent):
	out = _send(proposal, agent)
	_send_notice(proposal, agent)
	return out


def _send_notice(proposal, agent) -> None:
	"""The WhatsApp notice that goes with an emailed statement (payload "notice"). The statement is
	out already, so a refusal (no opt-in, the template withdrawn) is noted and never fails the send."""
	n = frappe._dict(frappe.parse_json(proposal.payload or "{}").get("notice") or {})
	if not n.template:
		return
	contact = billing_contact(proposal.subject_name)
	if not contact or contact.mobile != n.mobile:
		why = _("the billing contact's mobile changed")
	else:
		from excom.excom.services.thread_service import upsert_thread

		try:
			thread = upsert_thread(identity_for(phone=contact.mobile, display=contact.name), "whatsapp", n.account)
			steward.send_whatsapp(proposal, agent, thread, n.template, n.variables or [])
			return
		except Exception as e:
			frappe.clear_last_message()
			why = str(e)
	frappe.get_doc("Excom AI Proposal", proposal.name).add_comment("Comment",
		_("The WhatsApp notice was not sent: {0}").format(why)[:500])


# ── statements on request ────────────────────────────────────────────────────


@registry.task_kind("prepare_statement", priority=registry.PRIORITY["write"], effort="write")
def prepare_statement(task, agent) -> str:
	"""The customer asked for their statement: open invoices and recent payments from the ledger,
	with a short note, for a person to approve (statements never go on their own)."""
	customer = task.subject_name
	company = company_of(agent)
	invoices = open_invoices(customer, company)
	if not invoices:
		return _("{0}: nothing outstanding; a person answers").format(customer)
	contact = billing_contact(customer)
	account = email_account(agent)
	if not contact or not contact.email or not account:
		return _no_contact(task, agent, None, customer)
	language = frappe.db.get_value("Customer", customer, "language") or "en"
	payments = recent_payments(customer, company)
	lb = labels(language)
	lines = [lb["open"] + ":", *invoice_lines(invoices, language, "outstanding")]
	if payments:
		lines += ["", lb["paid"] + ":"]
		lines += [f"{formatdate(x.posting_date)}: {fmt_money(x.paid_amount, currency=x.paid_from_account_currency)}"
			for x in payments[:LINES_MAX]]
	r = runner.ai(task, agent, pack_instructions=STATEMENT_RULES, output_schema=STATEMENT_SCHEMA,
		prompt=_("Customer: {0}. Their language: {1}. They asked for their statement of account.").format(
			label("Customer", customer), language))
	subject, note = (r.data.get("subject") or "").strip(), (r.data.get("note") or "").strip()
	body = "\n\n".join(filter(None, [note, "\n".join(lines)]))
	results = checks.check(agent, contact.email, subject, body, kind="Statement", account=account, language=language,
		word_limit=120 + 15 * len(lines))
	results = _finish_checks(results, [subject, note], customer, company)
	results.pop("credits", None)  # a statement shows credits as they are; it asks for nothing
	notice = statement_notice(agent, customer, contact, language, task.thread)
	if notice:
		results["notice"] = {"ok": True, "why": _("WhatsApp notice to {0} on template {1}").format(notice.mobile,
			notice.template)}
	results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	name = proposals.propose(task, agent, "send_statement", subject=("Customer", customer), thread=task.thread,
		kind="Reply",
		to_email=contact.email, channel_account=account, risk="Medium" if results["ok"] else "High", checks=results,
		checks_ok=results["ok"],
		preview_html=paragraphs(note) + statement_table(invoices, payments, language) + notice_html(notice),
		payload={"customer": customer, "subject": subject, "note": note, "body": note, "lines": lines,
			"invoices": snapshot(invoices), "scope": "all", "with_table": True, "language": language, "channel": "email",
			"contact": contact.contact, **({"notice": dict(notice)} if notice else {})})
	return _("{0}: statement prepared for review ({1})").format(customer, name or _("policy off"))


def email_unanswered(agent, customer: str, thread: str | None) -> bool:
	"""Email is not where this customer answers: they asked on WhatsApp, or (asked some other way)
	the ladder's last email got no reply."""
	channel = frappe.db.get_value("Excom Thread", thread, "channel") if thread else None
	if channel in ("email", "whatsapp"):
		return channel == "whatsapp"
	enr = enrollment_for(agent, customer, create=False)
	if not enr or not enr.thread or not enr.last_sent_on or \
			frappe.db.get_value("Excom Thread", enr.thread, "channel") != "email":
		return False
	return not frappe.db.exists("Excom Message", {"thread": enr.thread, "direction": "Inbound",
		"creation": [">", enr.last_sent_on]})


def statement_notice(agent, customer: str, contact, language: str, thread: str | None) -> frappe._dict | None:
	"""A short WhatsApp notice that the statement is on its way by email, sent with it, for a billing
	contact who does not answer by email. Only on the steward's approved template; without one,
	`find` records the need and the statement goes by email alone."""
	if not contact.mobile or not whatsapp_numbers(agent) or not email_unanswered(agent, customer, thread):
		return None
	found = steward_whatsapp(agent, STATEMENT_NEED, language,
		{"contact_name": contact.first_name or contact.name, "as_of": formatdate(nowdate())})
	if not found or found.error:
		return None
	return frappe._dict(account=found.account, template=found.template, need=found.need, variables=found.variables,
		text=found.text, mobile=contact.mobile)


# ── replies: promises, payments, disputes ────────────────────────────────────


def customer_of_thread(thread, agent) -> str | None:
	company = frappe.db.get_value("Excom AI Enrollment", {"agent": agent.name, "thread": thread.name}, "company",
		order_by="modified desc")
	customer = frappe.db.get_value("Excom AI Company", company, "customer") if company else None
	customer = customer or frappe.db.get_value("Excom AI Proposal", {"agent": agent.name, "thread": thread.name,
		"subject_doctype": "Customer"}, "subject_name", order_by="creation desc")
	if customer or not thread.omni_identity:
		return customer
	customer = frappe.db.get_value("Omni Identity Link", {"parent": thread.omni_identity,
		"linked_doctype": "Customer"}, "linked_name")
	email = frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email")
	if customer or not email:
		return customer
	row = frappe.db.sql("""SELECT dl.link_name FROM `tabContact Email` ce JOIN `tabDynamic Link` dl ON dl.parent = ce.parent
		AND dl.parenttype = 'Contact' AND dl.link_doctype = 'Customer' WHERE ce.email_id = %s LIMIT 1""", email)
	return row[0][0] if row else None


def _new_text(thread, params: dict) -> str:
	messages = frappe.get_all("Excom Message", {"thread": thread.name, "direction": "Inbound"}, ["name", "creation"],
		order_by="creation desc", limit=5)
	last_out = frappe.db.get_value("Excom Message", {"thread": thread.name, "direction": "Outbound"}, "creation",
		order_by="creation desc")
	new = [m.name for m in messages if not last_out or m.creation > last_out] or ([params["message"]] if params.get("message") else [])
	return responder.clean("\n\n".join(responder.full_text(m) for m in reversed(new)))


def classify(task, agent, text: str, overdue: list) -> dict:
	known = ", ".join(r.name for r in overdue[:20]) or _("none")
	r = runner.ai(task, agent, pack_instructions=CLASSIFY_RULES, output_schema=CLASSIFY_SCHEMA,
		prompt=_("Today is {0}. Their overdue invoices: {1}.").format(nowdate(), known) + "\n\n"
			+ untrusted(text[:6000], _("the customer's reply")))
	d = dict(r.data)
	if d.get("intent") not in INTENTS:
		d["intent"] = "question"  # unknown: a person reads it
	return d


def promise_date(value: str | None):
	"""A promised date the ladder can wait for: today to 120 days ahead, else None (a person reads it)."""
	try:
		d = getdate(value) if value else None
	except Exception:
		return None
	if not d or d < getdate(nowdate()) or d > getdate(add_days(nowdate(), 120)):
		return None
	return d


@registry.responder("accounts")
def respond(task, agent) -> str:
	params = frappe.parse_json(task.params or "{}") or {}
	thread = frappe.get_doc("Excom Thread", task.thread)
	text = _new_text(thread, params)
	if not text:
		return _("nothing to read")
	from excom.excom_ai.engine.inbound import hand_to_person

	customer = customer_of_thread(thread, agent)
	enr = enrollment_for(agent, customer, create=False) if customer else None
	hit = responder.rule_intent(text)
	if hit == "unsubscribe":
		from excom.excom_ai.mail import opt_out

		if thread.omni_identity:
			opt_out(thread.omni_identity, source="ai_reply_opt_out")
		if enr:
			close(enr, "Opted out", _("Asked not to be written to"))
		frappe.db.set_value("Excom Thread", thread.name, "ai_mode", "Hold", update_modified=False)
		if customer:
			proposals.propose(task, agent, "create_todo", subject=("Customer", customer), risk="Low",
				payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "High",
					"text": _("{0} asked not to receive reminders by email. Collections need a person.").format(label("Customer",
						customer))})
		return _("opted out; the ladder stopped")
	if hit == "escalate" or not customer:
		if enr:
			close(enr, "Stopped", _("Escalated: the customer's reply needs a person"))
		hand_to_person(thread.name, agent, text[:500], params.get("message"), reason="escalate")
		return _("handed to a person") if customer else _("no customer for this conversation: handed to a person")
	company = company_of(agent)
	overdue = open_invoices(customer, company, overdue_only=True)
	d = classify(task, agent, text, overdue)
	intent = d["intent"]
	frappe.db.set_value("Excom Thread", thread.name, "ai_intent", intent, update_modified=False)
	when = promise_date(d.get("promise_date")) if intent == "promise" else None
	if intent == "promise" and not when:
		intent = "question"  # no usable date: a person agrees one
	if intent == "promise":
		owed = totals(overdue)
		currency = next(iter(owed), None) or frappe.db.get_value("Company", company, "default_currency")
		total = flt(owed.get(currency))
		amount = flt(d.get("promise_amount"))
		amount = amount if 0 < amount <= total else total
		if enr:
			# Until a person approves the promise (Auto records it at once, and the executor then pauses
			# the ladder to the promised date), the pause is short: a reply alone cannot stall the ladder.
			until = min(get_datetime(add_days(get_datetime(when), GRACE_DAYS)),
				get_datetime(add_days(now_datetime(), PROMISE_REVIEW_DAYS)))
			_set(enr, status="Paused", next_due=until,
				stop_reason=_("Promised {0} by {1}; the promise waits for a person to approve it").format(
					fmt_money(amount, currency=currency), formatdate(when)))
		name = proposals.propose(task, agent, "record_promise", subject=("Customer", customer), thread=thread.name,
			risk="Low",
			payload={"customer": customer, "date": str(when), "amount": amount, "currency": currency, "overdue_then": total,
				"invoices": snapshot([r for r in overdue if r.currency == currency]),
				"enrollment": enr.name if enr else None, "summary": d.get("summary") or ""},
			preview_html=frappe.utils.escape_html(_("Promised {0} by {1}").format(fmt_money(amount, currency=currency),
				formatdate(when))))
		return _("promise to pay by {0} recorded ({1})").format(when, name or _("policy off"))
	if intent == "paid":
		if enr:
			_set(enr, status="Paused", next_due=add_days(now_datetime(), PAUSE_DAYS["paid"]),
				stop_reason=_("Says it is paid: waiting for a person to match it"))
		found = candidates_for_customer(customer, company)
		text_ = _("{0} says the overdue invoices are paid{1}. Check the bank and reconcile. {2}").format(
			label("Customer", customer), _(" (reference {0})").format(d["reference"]) if d.get("reference") else "",
			_("Possible bank entries: {0}.").format(", ".join(found)) if found else _("No matching bank entry found yet."))
		name = proposals.propose(task, agent, "suggest_match", subject=("Customer", customer), thread=thread.name, risk="Low",
			payload={"customer": customer, "reference": d.get("reference") or "", "bank_transactions": found, "text": text_,
				"invoices": [r.name for r in overdue]})
		return _("says paid: a match suggested to a person ({0})").format(name or _("policy off"))
	if intent == "dispute":
		if enr:
			close(enr, "Stopped", _("Dispute: {0}").format(d.get("summary") or text[:200]))
		frappe.get_doc("Customer", customer).add_comment("Comment", _("{0} (AI): the customer disputes: {1}").format(
			agent.agent_name, frappe.utils.escape_html(d.get("summary") or text[:300])))
		hand_to_person(thread.name, agent, text[:500], params.get("message"), reason="escalate")
		return _("dispute: the ladder stopped and a person has it")
	if intent == "statement":
		if enr:
			_set(enr, status="Active", next_due=add_days(now_datetime(), PAUSE_DAYS["statement"]), stop_reason=None)
		t = tasks.add(agent.name, "prepare_statement", subject=("Customer", customer), thread=thread.name)
		return _("statement asked for: {0}").format(t)
	if enr:
		_set(enr, status="Paused", next_due=add_days(now_datetime(), PAUSE_DAYS["question"]),
			stop_reason=_("A question for a person"))
	hand_to_person(thread.name, agent, text[:500], params.get("message"), reason="escalate")
	return _("{0}: handed to a person").format(intent)


@registry.executor("record_promise")
def record_promise(proposal, agent):
	"""The promise on record: a comment on the customer, a ToDo for the owner on the promised date,
	and the ladder paused until the day after it."""
	p = frappe._dict(frappe.parse_json(proposal.payload or "{}"))
	customer = proposal.subject_name
	when = getdate(p.date)
	what = _("{0} promised to pay {1} by {2}").format(label("Customer", customer), fmt_money(p.amount,
		currency=p.currency),
		formatdate(when))
	frappe.get_doc("Customer", customer).add_comment("Comment", frappe.utils.escape_html(what + ". " + (p.summary or ""))
		+ f"<br><span class='text-muted'>{frappe.utils.escape_html(agent.agent_name)} (AI)</span>")
	todo = frappe.get_doc({"doctype": "ToDo", "allocated_to": agent.acting_owner or agent.owner_user,
		"reference_type": "Customer",
		"reference_name": customer, "date": when, "priority": "Medium", "assigned_by": agent.service_user,
		"description": _("{0}. Check the payment arrived; the reminders resume the day after if it has not.").format(what)
		}).insert(ignore_permissions=True)
	if p.enrollment and frappe.db.get_value("Excom AI Enrollment", p.enrollment, "status") in ("Active", "Paused", "Replied"):
		frappe.db.set_value("Excom AI Enrollment", p.enrollment, {"status": "Paused",
			"next_due": add_days(get_datetime(when), GRACE_DAYS),
			"stop_reason": what})
	return ("ToDo", todo.name)


# ── payment matching (suggestions only) ──────────────────────────────────────


def _score(bt, inv, names: dict) -> tuple[int, list[str]]:
	text = f"{bt.description or ''} {bt.reference_number or ''} {bt.bank_party_name or ''}".lower()
	score, why = 0, []
	if abs(flt(bt.unallocated_amount) - flt(inv.outstanding_amount)) <= 1:
		score, why = score + 2, [*why, _("same amount")]
	if inv.name.lower() in text:
		score, why = score + 3, [*why, _("the invoice number is in the reference")]
	if bt.party_type == "Customer" and bt.party == inv.customer:
		score, why = score + 2, [*why, _("the bank names the customer")]
	elif len(names.get(inv.customer) or "") >= 4 and names[inv.customer].lower() in text:
		score, why = score + 1, [*why, _("the customer's name is in the narration")]
	return score, why


def unreconciled(company: str | None, limit: int = MATCH_LIMIT) -> list:
	if not frappe.db.exists("DocType", "Bank Transaction"):
		return []
	filters = {"status": ["in", ("Pending", "Unreconciled")], "deposit": [">", 0], "unallocated_amount": [">", 0],
		"docstatus": ["<", 2]}
	if company:
		filters["company"] = company
	return frappe.get_all("Bank Transaction", filters, ["name", "date", "deposit", "unallocated_amount", "currency",
		"description",
		"reference_number", "bank_party_name", "party_type", "party"], order_by="date desc", limit=limit)


def suggestions(company: str | None, transactions: list | None = None, customer: str | None = None) -> list[dict]:
	"""For each unreconciled deposit, the best open invoice by amount, reference and party (score 3+).
	customer: only that customer's open invoices (a reply saying it is paid needs no others)."""
	transactions = unreconciled(company) if transactions is None else transactions
	if not transactions:
		return []
	filters = {"docstatus": 1, "outstanding_amount": [">", 0]}
	if company:
		filters["company"] = company
	if customer:
		filters["customer"] = customer
	invoices = frappe.get_all("Sales Invoice", filters, ["name", "customer", "outstanding_amount", "currency"], limit=5000)
	names = {r.name: r.customer_name for r in frappe.get_all("Customer", {"name": ["in",
		list({i.customer for i in invoices}) or ["-"]]},
		["name", "customer_name"])}
	out = []
	for bt in transactions:
		best = None
		for inv in invoices:
			score, why = _score(bt, inv, names)
			if score >= 3 and (not best or score > best["score"]):
				best = {"bank_transaction": bt.name, "invoice": inv.name, "customer": inv.customer, "score": score, "why": why,
					"amount": flt(bt.unallocated_amount), "currency": bt.currency or inv.currency, "date": str(bt.date)}
		if best:
			out.append(best)
	return out


def candidates_for_customer(customer: str, company: str | None) -> list[str]:
	return sorted({s["bank_transaction"] for s in suggestions(company, customer=customer) if s["customer"] == customer})


@registry.task_kind("match_payments", priority=registry.PRIORITY["research"], effort="classify")
def match_payments(task, agent) -> str:
	"""Suggest matches for a person; one suggestion per bank entry, ever (a rejected one is not
	suggested again)."""
	made = 0
	for s in suggestions(company_of(agent)):
		if frappe.db.exists("Excom AI Proposal", {"action": "suggest_match", "subject_doctype": "Bank Transaction",
				"subject_name": s["bank_transaction"]}):
			continue
		text = _("Bank entry {0} ({1}, {2}) looks like payment of {3} from {4}: {5}. Reconcile it in Bank Reconciliation if "
			"right.").format(s["bank_transaction"], s["date"], fmt_money(s["amount"], currency=s["currency"]), s["invoice"],
			label("Customer", s["customer"]), ", ".join(s["why"]))
		if proposals.propose(task, agent, "suggest_match", subject=("Bank Transaction", s["bank_transaction"]), risk="Low",
				payload={**s, "text": text}, preview_html=frappe.utils.escape_html(text)):
			made += 1
	return _("{0} matches suggested").format(made)


@registry.executor("suggest_match")
def suggest_match(proposal, agent):
	"""A ToDo for the owner and a comment on the record. It never reconciles, allocates or posts."""
	p = frappe._dict(frappe.parse_json(proposal.payload or "{}"))
	text = p.text or _("Check this payment against the open invoices.")
	if proposal.subject_doctype and proposal.subject_name:
		frappe.get_doc(proposal.subject_doctype, proposal.subject_name).add_comment("Comment", frappe.utils.escape_html(text)
			+ f"<br><span class='text-muted'>{frappe.utils.escape_html(agent.agent_name)} (AI), a suggestion only</span>")
	todo = frappe.get_doc({"doctype": "ToDo", "allocated_to": agent.acting_owner or agent.owner_user, "description": text,
		"reference_type": proposal.subject_doctype, "reference_name": proposal.subject_name, "priority": "Medium",
		"assigned_by": agent.service_user}).insert(ignore_permissions=True)
	return ("ToDo", todo.name)


# ── MSME payables watch ──────────────────────────────────────────────────────


def _orphan_msme_columns() -> list[str]:
	meta = frappe.get_meta("Supplier")
	return [c for c in frappe.db.get_table_columns("Supplier") if ("msme" in c.lower() or "udyam" in c.lower())
		and not meta.has_field(c)]


def msme_source() -> frappe._dict:
	"""Where the site keeps supplier MSME status: sigzen_msme's table, flat Supplier fields Frappe
	knows, or only columns Frappe no longer knows (orphaned)."""
	meta = frappe.get_meta("Supplier")
	if frappe.db.exists("DocType", "MSME Registration Detail") and meta.has_field("custom_msme_details"):
		parent = (frappe.db.get_single_value("MSME Settings", "msme_detail_source") if frappe.db.exists("DocType",
			"MSME Settings")
			else None) or "Supplier"
		return frappe._dict(kind="table", parent=parent, fields=["custom_msme_details (MSME Registration Detail)"])
	flat = [f for f in MSME_TYPE_FIELDS if meta.has_field(f)]
	if flat:
		return frappe._dict(kind="fields",
			fields=flat + [f for f in MSME_REG_FIELDS + MSME_AGREEMENT_FIELDS if meta.has_field(f)])
	orphan = _orphan_msme_columns()
	if orphan:
		return frappe._dict(kind="orphaned", fields=orphan)
	return frappe._dict(kind=None, fields=[])


def _kind_of(value) -> str | None:
	v = str(value or "").lower()
	return "Micro" if "micro" in v else "Small" if "small" in v else None


def _agreement(value) -> bool | None:
	v = str(value or "").strip().lower()
	return True if v in ("yes", "1", "true") else False if v in ("no", "0", "false") else None


def msme_suppliers() -> dict[str, frappe._dict]:
	"""Micro and Small suppliers: supplier -> {type, agreement, source}."""
	src, out = msme_source(), {}
	today = getdate(nowdate())
	if src.kind == "table":
		for r in frappe.get_all("MSME Registration Detail", {"parenttype": src.parent, "msme_type": ["in", ("Micro",
			"Small")]},
				["parent", "msme_type", "msme_registered", "msme_contract_done", "effective_from", "effective_to"]):
			if r.msme_registered == "No" or (r.effective_to and getdate(r.effective_to) < today) or (
					r.effective_from and getdate(r.effective_from) > today):
				continue
			suppliers = [r.parent] if src.parent == "Supplier" else frappe.get_all("Dynamic Link", {"parenttype": "Address",
				"parent": r.parent, "link_doctype": "Supplier"}, pluck="link_name")
			for s in suppliers:
				out[s] = frappe._dict(type=r.msme_type, agreement=_agreement(r.msme_contract_done),
					source="MSME Registration Detail")
		return out
	if src.kind in ("fields", "orphaned"):
		type_col = next((f for f in src.fields if f in MSME_TYPE_FIELDS or "type" in f.lower() or "category" in f.lower()), None)
		if not type_col:
			return out
		agree_col = next((f for f in src.fields if f in MSME_AGREEMENT_FIELDS or "contract" in f.lower()), None)
		cols = ", ".join(f"`{c}`" for c in filter(None, (type_col, agree_col)))
		for r in frappe.db.sql(f"SELECT name, {cols} FROM `tabSupplier` WHERE IFNULL(`{type_col}`, '') != ''", as_dict=True):
			kind = _kind_of(r.get(type_col))
			if kind:
				out[r.name] = frappe._dict(type=kind, agreement=_agreement(r.get(agree_col)) if agree_col else None,
					source=type_col + (_(" (a column Frappe no longer knows)") if src.kind == "orphaned" else ""))
	return out


def limit_days(agreement: bool | None) -> int:
	"""45 days with a written agreement (or when the record does not say), 15 without; MSME
	Settings' own day counts win where set."""
	yes = no = 0
	if frappe.db.exists("DocType", "MSME Settings"):
		yes = cint(frappe.db.get_single_value("MSME Settings", "yes"))
		no = cint(frappe.db.get_single_value("MSME Settings", "no"))
	if agreement is False:
		return no or MSME_NO_AGREEMENT_DAYS
	return yes or MSME_LIMIT_DAYS


def msme_watch_list(company: str | None) -> list[frappe._dict]:
	"""Unpaid Purchase Invoices of Micro and Small suppliers within MSME_NEAR_DAYS of their limit or
	past it, the most urgent first. Age counts from the bill date (else the posting date)."""
	suppliers = msme_suppliers()
	if not suppliers:
		return []
	filters = {"docstatus": 1, "outstanding_amount": [">", 0], "supplier": ["in", list(suppliers)]}
	if company:
		filters["company"] = company
	today = getdate(nowdate())
	out = []
	fields = ["name", "supplier", "supplier_name", "posting_date", "bill_date", "bill_no", "outstanding_amount", "currency"]
	if _has("Purchase Invoice", "party_account_currency"):
		fields.append("party_account_currency")
	for r in frappe.get_all("Purchase Invoice", filters, fields):
		r.currency = r.get("party_account_currency") or r.currency  # the outstanding amount is in the account's currency
		s = suppliers[r.supplier]
		start = getdate(r.bill_date or r.posting_date)
		age, limit = (today - start).days, limit_days(s.agreement)
		if age >= limit - MSME_NEAR_DAYS:
			out.append(frappe._dict(r, type=s.type, source=s.source, age=age, limit=limit, days_left=limit - age,
				pay_by=add_days(start, limit)))
	return sorted(out, key=lambda x: (x.days_left, x.supplier))


def finance_people(agent) -> list[str]:
	"""The owner, the backup, and the team's members who hold an accounts role."""
	from excom.excom_ai.service_user import is_service_user

	users = [agent.acting_owner or agent.owner_user, agent.backup_user]
	if agent.team:
		for u in frappe.get_all("Excom Team Member", {"parent": agent.team}, pluck="user"):
			if u and not is_service_user(u) and set(frappe.get_roles(u)) & FINANCE_ROLES:
				users.append(u)
	return list(dict.fromkeys(u for u in users if u))


def msme_table(rows: list) -> str:
	e = frappe.utils.escape_html
	body = "".join(
		f"<tr><td>{e(r.supplier_name or r.supplier)}</td><td>{e(r.type)}</td><td>{e(r.name)}{' / ' + e(r.bill_no) if r.bill_no else ''}</td>"
		f"<td>{e(formatdate(r.bill_date or r.posting_date))}</td><td style='text-align:right'>{r.age}</td>"
		f"<td>{e(formatdate(r.pay_by))}</td><td style='text-align:right'>{e(fmt_money(r.outstanding_amount, currency=r.currency))}</td></tr>"
		for r in rows)
	return ("<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'><tr><th>Supplier</th><th>MSME</th>"
		"<th>Invoice</th><th>Bill date</th><th>Days</th><th>Pay by</th><th>Outstanding</th></tr>" + body + "</table>")


@registry.task_kind("msme_watch", priority=registry.PRIORITY["research"], effort="classify")
def msme_watch(task, agent) -> str:
	"""The weekly list for finance: in the app (with its email) and as a ToDo. Internal only."""
	rows = msme_watch_list(company_of(agent))
	if not rows:
		return _("no Micro or Small supplier invoice near its limit")
	past = [r for r in rows if r.days_left < 0]
	subject = _("MSME payables: {0} invoices from {1} suppliers, {2} past the limit ({3})").format(len(rows),
		len({r.supplier for r in rows}), len(past), "; ".join(money(v, c) for c, v in totals(rows).items()))
	intro = _("Micro and Small suppliers' unpaid invoices near or past the 43B(h) limit (45 days with a written agreement, "
		"15 without). Paid late, they are not deductible this year.")
	notify.alert(finance_people(agent), subject, message=f"<p>{frappe.utils.escape_html(intro)}</p>" + msme_table(rows),
		doctype="Excom AI Agent", name=agent.name)
	top = "; ".join(f"{r.supplier_name or r.supplier} {r.name} ({r.age} days, {fmt_money(r.outstanding_amount, currency=r.currency)})"
		for r in rows[:10])
	proposals.propose(task, agent, "create_todo", subject=("Excom AI Agent", agent.name), risk="Low",
		payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "High" if past else "Medium",
			"date": nowdate(),
			"text": f"{subject}. {top}{' …' if len(rows) > 10 else ''}"})
	return subject


# ── readiness (HLD-007 §7.3) ─────────────────────────────────────────────────

RULES = [
	("accounts.billing_contacts", "Billing contacts", "Blocker", "",
		"Give every customer with dues a Contact marked 'Is Billing Contact' with an email (or a WhatsApp number).",
		"/app/contact", "excom.excom_ai.packs.accounts.billing_contacts_rule"),
	("accounts.payment_terms", "Payment terms", "Warning", "",
		"Set payment terms on customers so a due date means something; without them the due date is the invoice date and "
		"'over 90 days' overstates lateness.", "/app/payment-terms-template/new",
			"excom.excom_ai.packs.accounts.payment_terms_rule"),
	("accounts.reminder_channel", "Reminder channel", "Blocker", "",
		"Connect an email mailbox to the agent, or approve a WhatsApp utility template for payment reminders.",
		"/excom/admin/accounts", "excom.excom_ai.packs.accounts.reminder_channel_rule"),
	("accounts.dispute", "A way to mark a dispute", "Warning", "",
		"Add a 'Disputed' check on Sales Invoice (or Customer) so people can stop reminders on an invoice in dispute.",
		"/app/customize-form?doc_type=Sales%20Invoice", "excom.excom_ai.packs.accounts.dispute_rule"),
	("accounts.bank_feed", "Bank feed reconciled", "Warning", "",
		"Import the bank statement at least weekly and reconcile deposits older than 30 days, so payments are seen.",
		"/app/bank-reconciliation-tool", "excom.excom_ai.packs.accounts.bank_feed_rule"),
	("accounts.msme_fields", "MSME status", "Warning", "",
		"Record each supplier's MSME type (Micro, Small, Medium) and written-agreement status in the MSME Registration "
		"Details on the Supplier.", "/app/supplier", "excom.excom_ai.packs.accounts.msme_fields_rule"),
	("accounts.people", "People", "Blocker", "", "Set an active owner and backup, and the team.", "",
		"excom.excom_ai.packs.accounts.people_rule"),
]


def customers_with_dues(company: str | None) -> list[str]:
	return frappe.db.sql("""SELECT DISTINCT customer FROM `tabSales Invoice` WHERE docstatus = 1 AND outstanding_amount > 0
		AND due_date < %(today)s AND (%(co)s IS NULL OR company = %(co)s)""", {"today": nowdate(), "co": company}, pluck=True)


def customers_with_billing_contact(customers: list[str]) -> set[str]:
	if not customers:
		return set()
	return set(frappe.db.sql("""SELECT DISTINCT dl.link_name FROM `tabDynamic Link` dl JOIN `tabContact` c ON c.name = dl.parent
		JOIN `tabCustomer` cu ON cu.name = dl.link_name
		WHERE dl.parenttype = 'Contact' AND dl.link_doctype = 'Customer' AND dl.link_name IN %(c)s
			AND ((c.is_billing_contact = 1 AND (IFNULL(c.email_id, '') != '' OR IFNULL(c.mobile_no, '') != ''))
				OR ((c.is_primary_contact = 1 OR c.name = cu.customer_primary_contact) AND IFNULL(c.email_id, '') != ''))""",
		{"c": tuple(customers)}, pluck=True))


@check("accounts.billing_contacts")
def billing_contacts_rule(agent, facts):
	customers = customers_with_dues(company_of(agent))
	if not customers:
		return True, _("No customer has overdue invoices.")
	missing = sorted(set(customers) - customers_with_billing_contact(customers))
	return not missing, _("{0} of {1} customers with dues have a billing contact{2}.").format(len(customers) - len(missing),
		len(customers), _("; missing: {0}").format(", ".join(label("Customer", c) for c in missing[:5])) if missing else "")


@check("accounts.payment_terms")
def payment_terms_rule(agent, facts):
	templates = frappe.db.count("Payment Terms Template")
	row = frappe.db.sql("""SELECT COUNT(*), SUM(due_date > posting_date) FROM `tabSales Invoice` WHERE docstatus = 1
		AND outstanding_amount > 0 AND (%(co)s IS NULL OR company = %(co)s)""", {"co": company_of(agent)})[0]
	n, with_terms = cint(row[0]), cint(row[1])
	ok = templates > 0 and (not n or with_terms * 2 >= n)
	return ok, _("{0} payment terms templates; {1} of {2} open invoices fall due after their invoice date.").format(templates,
		with_terms, n)


@check("accounts.reminder_channel")
def reminder_channel_rule(agent, facts):
	account, wa = email_account(agent), whatsapp_template(agent)
	wabas = [w for w in (frappe.db.get_value("Excom Channel Account", n, "wa_business_id") for n in whatsapp_numbers(agent)) if w]
	met = frappe.db.get_value("Excom AI Template Need", {"need_key": REMINDER_NEED, "channel": "WhatsApp", "status": "Met",
		"wa_business_id": ["in", wabas]}, "template") if wabas else None
	if account or wa or met:
		return True, ", ".join(filter(None, [_("mailbox {0}").format(account) if account else None,
			_("WhatsApp template {0}").format(wa.template) if wa else None,
			_("WhatsApp template {0} (template steward)").format(met) if met else None]))
	return False, _("No mailbox on the agent and no approved WhatsApp utility template for reminders.")


@check("accounts.dispute")
def dispute_rule(agent, facts):
	found = dispute_fields()
	if found:
		return True, _("Disputes are marked in {0}.").format(", ".join(f"{dt}.{f}" for dt, f in found))
	return False, _("No dispute field on Sales Invoice or Customer: a dispute is only seen when a customer's reply says so.")


@check("accounts.bank_feed")
def bank_feed_rule(agent, facts):
	if not frappe.db.exists("DocType", "Bank Transaction"):
		return False, _("Bank Transaction is not on this site.")
	company = company_of(agent)
	where = {"company": company} if company else {}
	latest = frappe.db.get_value("Bank Transaction", {**where, "docstatus": ["<", 2]}, "date", order_by="date desc")
	if not latest:
		return False, _("No bank transactions imported.")
	old = frappe.db.count("Bank Transaction", {**where, "status": ["in", ("Pending", "Unreconciled")],
		"docstatus": ["<", 2],
		"date": ["<", add_days(nowdate(), -30)]})
	stale = date_diff(nowdate(), latest) > 30
	return not old and not stale, _("Last bank entry {0}; {1} unreconciled for more than 30 days.").format(formatdate(latest), old)


@check("accounts.msme_fields")
def msme_fields_rule(agent, facts):
	src = msme_source()
	if src.kind == "orphaned":
		return False, _("MSME status sits in columns Frappe cannot see ({0}): move it into the MSME Registration Details.").format(
			", ".join(src.fields))
	if not src.kind:
		return False, _("No MSME field on Supplier.")
	marked = len(msme_suppliers())
	return marked > 0, _("{0}: {1} Micro or Small suppliers marked.").format(", ".join(src.fields), marked)


@check("accounts.people")
def people_rule(agent, facts):
	return people(agent, facts)


def seed() -> None:
	"""The Accounts readiness rules (setup calls this on every migrate; never overwrites edits)."""
	seed_rules("Accounts", RULES)


# ── knowledge recipes for the builder ────────────────────────────────────────


@recipe("receivables_aged", "Receivables by due date", packs=("accounts",), privacy="aggregates", order=10)
def receivables_aged(ctx):
	need("Sales Invoice")
	rows = frappe.db.sql("""SELECT CASE WHEN due_date >= %(today)s THEN 'not due'
			WHEN DATEDIFF(%(today)s, due_date) <= 30 THEN '1-30' WHEN DATEDIFF(%(today)s, due_date) <= 60 THEN '31-60'
			WHEN DATEDIFF(%(today)s, due_date) <= 90 THEN '61-90' ELSE '90+' END AS bucket,
			COUNT(*) AS n, COUNT(DISTINCT customer) AS customers, SUM(base_grand_total * outstanding_amount / NULLIF(grand_total, 0)) AS amount
		FROM `tabSales Invoice` WHERE docstatus = 1 AND outstanding_amount > 0 AND company = %(co)s GROUP BY bucket""",
		{"today": nowdate(), "co": ctx.company}, as_dict=True)
	same = frappe.db.sql("""SELECT COUNT(*), SUM(due_date <= posting_date) FROM `tabSales Invoice` WHERE docstatus = 1
		AND outstanding_amount > 0 AND company = %s""", ctx.company)[0]
	buckets = {r.bucket: {"invoices": cint(r.n), "customers": cint(r.customers), "amount": flt(r.amount)} for r in rows}
	total = sum(b["amount"] for b in buckets.values())
	facts = [fact(_("{0} outstanding on {1} invoices").format(money(total, ctx.currency),
		sum(b["invoices"] for b in buckets.values())),
		_("Sales Invoice, submitted, outstanding"), "Sales Invoice")]
	for key in ("not due", "1-30", "31-60", "61-90", "90+"):
		if key in buckets:
			b = buckets[key]
			facts.append(fact(_("{0}: {1} from {2} customers ({3} invoices)").format(key, money(b["amount"], ctx.currency),
				b["customers"],
				b["invoices"]), _("Sales Invoice by days past due date"), "Sales Invoice",
				level="warning" if key == "90+" and b["amount"] else "info"))
	n, due_same = cint(same[0]), cint(same[1])
	if n and due_same * 2 > n:
		facts.append(fact(_("{0} of {1} open invoices fall due on their invoice date: no payment terms, so lateness is overstated").format(
			due_same, n), _("Sales Invoice: due_date against posting_date"), level="warning"))
	return facts, {"buckets": buckets, "total": total, "due_on_invoice_date": due_same, "open_invoices": n}


@recipe("overdue_customers", "Top overdue customers", packs=("accounts",), privacy="names", order=20)
def overdue_customers(ctx):
	need("Sales Invoice", "Customer", "Contact")
	rows = frappe.db.sql("""SELECT customer, SUM(base_grand_total * outstanding_amount / NULLIF(grand_total, 0)) AS amount,
			MIN(due_date) AS oldest, COUNT(*) AS n FROM `tabSales Invoice` WHERE docstatus = 1 AND outstanding_amount > 0
			AND due_date < %(today)s AND company = %(co)s GROUP BY customer ORDER BY amount DESC LIMIT 10""",
		{"today": nowdate(), "co": ctx.company}, as_dict=True)
	all_due = customers_with_dues(ctx.company)
	with_contact = customers_with_billing_contact(all_due)
	facts = [fact(_("Top overdue: {0}").format("; ".join(f"{label('Customer', r.customer)} {money(r.amount, ctx.currency)} "
		f"({date_diff(nowdate(), r.oldest)} days)" for r in rows[:5])) if rows else _("No overdue customers"),
		_("Sales Invoice past due, by customer"), "Customer", [r.customer for r in rows])]
	if all_due:
		facts.append(fact(_("{0} of {1} customers with dues have a billing contact").format(len(with_contact), len(all_due)),
			_("Contact: is_billing_contact, or the primary contact's email"), "Customer", sorted(set(all_due) - with_contact),
			level="warning" if len(with_contact) < len(all_due) else "info"))
	return facts, {"top": [{"customer": r.customer, "amount": flt(r.amount), "oldest": str(r.oldest),
		"invoices": cint(r.n)} for r in rows],
		"customers_with_dues": len(all_due), "with_billing_contact": len(with_contact)}


@recipe("msme_payables", "MSME payables", packs=("accounts",), privacy="aggregates", order=30)
def msme_payables(ctx):
	need("Purchase Invoice", "Supplier")
	src = msme_source()
	rows = msme_watch_list(ctx.company)
	past = [r for r in rows if r.days_left < 0]
	facts = [fact(_("MSME status from {0}").format(", ".join(src.fields)) if src.kind else _("No MSME status on suppliers"),
		_("Supplier fields"), level="info" if src.kind in ("table", "fields") else "warning"),
		fact(_("{0} past the 43B(h) limit from {1} suppliers ({2}); {3} more within {4} days").format(
			"; ".join(money(v, c) for c, v in totals(past).items()) or money(0, ctx.currency),
			len({r.supplier for r in past}), len(past),
			len(rows) - len(past), MSME_NEAR_DAYS), _("Purchase Invoice, unpaid, Micro and Small suppliers"), "Purchase Invoice",
			[r.name for r in rows], level="warning" if past else "info")]
	return facts, {"source": src.kind, "fields": src.fields, "past": len(past), "near": len(rows) - len(past),
		"past_amount": sum(flt(r.outstanding_amount) for r in past if r.currency == ctx.currency)}


@recipe("unreconciled_bank", "Unreconciled bank transactions", packs=("accounts",), privacy="aggregates", order=40)
def unreconciled_bank(ctx):
	if not frappe.db.exists("DocType", "Bank Transaction"):
		return [fact(_("No bank feed on this site"), _("Bank Transaction"), level="warning")], {"unreconciled": 0}
	need("Bank Transaction")
	where = {"company": ctx.company, "docstatus": ["<", 2]}
	n = frappe.db.count("Bank Transaction", {**where, "status": ["in", ("Pending", "Unreconciled")]})
	oldest = frappe.db.get_value("Bank Transaction", {**where, "status": ["in", ("Pending", "Unreconciled")]}, "date",
		order_by="date asc")
	latest = frappe.db.get_value("Bank Transaction", where, "date", order_by="date desc")
	facts = [fact(_("{0} unreconciled bank transactions{1}; last entry {2}").format(n,
		_(", the oldest from {0}").format(formatdate(oldest)) if oldest else "",
			formatdate(latest) if latest else _("never")),
		_("Bank Transaction: status"), "Bank Transaction", level="warning" if n or not latest else "info")]
	return facts, {"unreconciled": n, "oldest": str(oldest) if oldest else None, "latest": str(latest) if latest else None}

