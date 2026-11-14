"""Compliance pack (HLD-007 §8.4, Phase 2D): internal work only, never a message outside the company.

    due-date calendar    a ToDo for the obligation's named owner N days before each due date, once
                         per period (GST returns, TDS, PF and ESI, ROC filings, licence renewals)
    document expiry      certificates and licences: a ToDo for the document's owner 60, 30 and 7
                         days before expiry, and an alert once it has expired
    transaction checks   pending e-invoices and e-way bills; customers and suppliers whose GSTIN is
                         cancelled, inactive, suspended or invalid (flagged before the next invoice);
                         export invoices under LUT with no LUT reference
    regulatory watch     once a week, a search for GST, CBIC and DGFT notices on the agent's
                         keywords, summarised by the model for a person to verify

Where the calendar and its owners live
--------------------------------------
In the agent's approved playbook, in sections of kind **Compliance**, one obligation per line:

    Name | rule | owner | days before

    GSTR-1                    | monthly 11                          | gst.desk@example.com | 5
    GSTR-3B                   | monthly 20                          | gst.desk@example.com | 5
    TDS payment               | yearly 01-07, 02-07, 03-07, 04-30, 05-07, 06-07, 07-07, 08-07, 09-07, 10-07, 11-07, 12-07 | accounts@example.com | 3
    TDS return                | yearly 07-31, 10-31, 01-31, 05-31   | accounts@example.com | 10
    Advance tax, 1st (15%)    | yearly 06-15                        | accounts@example.com | 7
    PF                        | monthly 15                          | hr@example.com       | 3
    ESI                       | monthly 15                          | hr@example.com       | 3
    ROC AOC-4                 | yearly 10-30                        | cs@example.com       | 15
    ROC MGT-7                 | yearly 11-29                        | cs@example.com       | 15
    Factory licence renewal   | on 2027-03-31                       | admin@example.com    | 30
    FSSAI licence             | expires 2027-02-15                  | qa@example.com
    e-Invoice and e-Way bill  | checks                              | gst.desk@example.com
    Counterparty GSTIN        | checks                              | accounts@example.com
    LUT                       | checks                              | gst.desk@example.com

Rules: `monthly D` (day D of every month, the month's last day when shorter), `quarterly D` (day D of
January, April, July and October: the month after each quarter), `yearly MM-DD[, MM-DD …]`,
`on YYYY-MM-DD` (once), `expires YYYY-MM-DD` (a document outside the register: the 60/30/7-day
ladder), and `checks` (no date: who gets the transaction checks of that name). The owner is a user
(its ID or email); days before defaults to 7. Lines without `|` are prose and are left alone, so the
same section can still hold market rules (§12.6). TDS is a yearly list because March's deposit is due
on 30 April and every other month's on the 7th of the next.

Why there: the playbook is already the agent's approved, versioned instructions, a person approves it
before it counts, and its Compliance section kind exists. No doctype had to be added (no migrate);
a "Compliance Obligation" doctype would be the cleaner home later (one row per obligation, a Link to
User, validation at entry). `template()` gives the standard Indian calendar to paste and fill in.

The create_todo executor only hands work to the agent's own people; this pack registers the owners
named here (and the document register's owner of the subject document) as allowed assignees through
`builtin.assignees`, read from records, never from a proposal's payload.

Once per period: every ToDo carries a key in its payload (obligation and due date, document, expiry
date and threshold, invoice and status, party and GSTIN status, week), and a key already proposed is
never proposed again, even after the ToDo is closed or the proposal rejected.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from excom.excom_ai.builder import readiness
from excom.excom_ai.builder.recipes import fact, need, recipe
from excom.excom_ai.engine import builtin, notify, proposals, registry, runner
from excom.excom_ai.engine.prompts import untrusted

DEFAULT_LEAD_DAYS = 7
EXPIRY_THRESHOLDS = (7, 30, 60)  # smallest first: the threshold a document is inside
EXPIRED_LOOKBACK_DAYS = 30  # an expiry field (not the register) older than this is history, not news
EXPIRED_REGISTER_LOOKBACK_DAYS = 365  # a register entry expired longer ago than this is a stale record, not news
RECENT_DAYS = 60  # invoices the transaction checks look at
MAX_PER_RUN = 25  # ToDos one check raises in one run, so a first run on old data is not a flood
DOCUMENT_HORIZON_DAYS = 90  # what the builder shows as expiring soon
PENDING = ("Pending", "Auto-Retry", "Failed")  # e-invoice and e-way bill states a person must act on
BAD_GSTIN = ("Cancelled", "Inactive", "Suspended")
REGISTER = "Document Tracker List"  # docpulse's document register
SEARCH_TOOLS = ("serper", "tinyfish", "scavio", "dataforseo")
WATCH_TOPICS = ("GST notification", "CBIC circular", "DGFT trade notice")
OFFICIAL = re.compile(r"(^|\.)(gov\.in|nic\.in)$")
LUT_FIELD = re.compile(r"(^|_)lut(_|$)")
EXPIRY_FIELD = re.compile(r"expir|valid_upto|valid_till|valid_until|validity_date")
DATED = ("monthly", "quarterly", "yearly", "on")

# The Indian statutory calendar, owners to be named (§8.4).
# TDS deducted in March is deposited by 30 April; every other month's by the 7th of the next.
TDS_PAYMENT = "yearly " + ", ".join("04-30" if m == 4 else f"{m:02d}-07" for m in range(1, 13))
STANDARD = [
	("GSTR-1", "monthly 11", 5),
	("GSTR-3B", "monthly 20", 5),
	("GSTR-9 annual return", "yearly 12-31", 15),
	("TDS payment", TDS_PAYMENT, 3),
	("TDS return", "yearly 07-31, 10-31, 01-31, 05-31", 10),
	("Advance tax, 1st instalment (15%)", "yearly 06-15", 7),
	("Advance tax, 2nd instalment (45%)", "yearly 09-15", 7),
	("Advance tax, 3rd instalment (75%)", "yearly 12-15", 7),
	("Advance tax, 4th instalment (100%)", "yearly 03-15", 7),
	("PF", "monthly 15", 3),
	("ESI", "monthly 15", 3),
	("ROC AOC-4", "yearly 10-30", 15),
	("ROC MGT-7", "yearly 11-29", 15),
	("ROC MSME-1 (half-yearly)", "yearly 04-30, 10-31", 10),
]


def _today() -> date:
	"""Today, in one place, so tests can move the calendar."""
	return getdate(nowdate())


def template() -> str:
	"""The standard calendar as Compliance-section lines, owners left for a person to name."""
	return "\n".join(f"{name} | {rule} |  | {lead}" for name, rule, lead in STANDARD)


# ── the calendar: reading the playbook ───────────────────────────────────────


def parse_rule(text: str) -> dict | None:
	"""A rule as {kind, …}; None when it is not one this pack knows (the line is then reported)."""
	words = (text or "").strip().lower().split(None, 1)
	if not words:
		return None
	kind, rest = words[0], (words[1].strip() if len(words) > 1 else "")
	try:
		if kind in ("monthly", "quarterly") and rest.isdigit() and 1 <= int(rest) <= 31:
			return {"kind": kind, "day": int(rest)}
		if kind == "yearly" and rest:
			dates = []
			for part in rest.split(","):
				m, d = (int(x) for x in part.strip().split("-"))
				date(2024, m, d)  # a leap year, so 02-29 is accepted
				dates.append((m, d))
			return {"kind": kind, "dates": dates}
		if kind in ("on", "expires") and rest:
			return {"kind": kind, "date": getdate(rest)}
		if kind == "checks" and not rest:
			return {"kind": kind}
	except (ValueError, TypeError, frappe.ValidationError):
		return None
	return None


def _lines(content: str) -> list[str]:
	"""Text Editor HTML to plain lines (each paragraph, break or list item is a line)."""
	text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", content or "")
	return [html.unescape(line).strip() for line in frappe.utils.strip_html_tags(text).splitlines()]


def _user(value: str) -> str | None:
	value = (value or "").strip()
	if not value:
		return None
	if frappe.db.exists("User", value):
		return value
	return frappe.db.get_value("User", {"email": value}, "name")


def approved_compliance_sections(agent) -> list[str]:
	"""The content of the Compliance sections a person approved, playbook by playbook: an Approved
	playbook's own sections, and for one whose newer version waits in review, the last approved
	version kept on the record (as prompts do), so the calendar does not vanish during a review."""
	out = []
	for link in agent.get("playbooks") or []:
		pb = frappe.db.get_value("Excom AI Playbook", link.playbook, ["name", "status", "approved_sections"], as_dict=True)
		if not pb:
			continue
		if pb.status == "Approved":
			out += frappe.get_all("Excom AI Playbook Section", {"parent": pb.name, "parenttype": "Excom AI Playbook",
				"kind": "Compliance"}, pluck="content", order_by="idx")
		elif pb.status == "In Review" and pb.approved_sections:
			try:
				kept = json.loads(pb.approved_sections) or []
			except ValueError:
				kept = []
			out += [s.get("content") for s in kept if isinstance(s, dict) and s.get("kind") == "Compliance"]
	return out


def calendar_of(agent) -> tuple[list[frappe._dict], list[str]]:
	"""(obligations, lines that look like obligations but could not be read), from the Compliance
	sections of the agent's approved playbooks: only what a person approved sets anyone's work."""
	out, bad, seen = [], [], set()
	for content in approved_compliance_sections(agent):
		for line in _lines(content):
			line = line.lstrip("-*• ").strip()
			if "|" not in line or line.startswith("#"):
				continue
			cells = [c.strip() for c in line.split("|")]
			name, rule = cells[0], parse_rule(cells[1] if len(cells) > 1 else "")
			if not name or not rule or name.lower() == "name":
				if name.lower() != "name":
					bad.append(line)
				continue
			if name.lower() in seen:
				continue
			seen.add(name.lower())
			given = cells[2] if len(cells) > 2 else ""
			lead = cint(cells[3]) if len(cells) > 3 and cells[3].isdigit() else DEFAULT_LEAD_DAYS
			out.append(frappe._dict(name=name, rule=rule, rule_text=cells[1], owner_given=given, owner=_user(given),
				lead=lead))
	return out, bad


def obligations(agent) -> list[frappe._dict]:
	return calendar_of(agent)[0]


def next_due(rule: dict, today: date) -> date | None:
	"""The first due date on or after today; None for a one-off date already past."""
	kind = rule["kind"]
	if kind in ("monthly", "quarterly"):
		months = range(1, 13) if kind == "monthly" else (1, 4, 7, 10)
		for i in range(13):
			y, m = today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1
			if m not in months:
				continue
			due = date(y, m, min(rule["day"], calendar.monthrange(y, m)[1]))
			if due >= today:
				return due
		return None
	if kind == "yearly":
		found = [date(y, m, min(d, calendar.monthrange(y, m)[1]))
			for y in (today.year, today.year + 1) for m, d in rule["dates"]]
		return min((d for d in found if d >= today), default=None)
	if kind in ("on", "expires"):
		return rule["date"] if rule["date"] >= today else None
	return None


def in_charge(agent) -> str:
	return agent.acting_owner or agent.owner_user


def _active(user: str | None) -> str | None:
	return user if user and frappe.db.get_value("User", user, "enabled") else None


def check_owner(agent, *words: str) -> str | None:
	"""The owner named on a `checks` line whose name mentions one of these words."""
	for ob in obligations(agent):
		if ob.rule["kind"] == "checks" and any(w in ob.name.lower() for w in words) and _active(ob.owner):
			return ob.owner
	return None


@builtin.assignees
def named_owners(proposal, agent) -> set[str]:
	"""Who a Compliance agent's ToDo may also go to: the owners its approved calendar names, and the
	register's owner of the document the ToDo is about."""
	if frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack") != "compliance":
		return set()
	people = {ob.owner for ob in obligations(agent) if ob.owner}
	if proposal.subject_doctype == REGISTER and frappe.db.table_exists(REGISTER):
		people.add(frappe.db.get_value(REGISTER, proposal.subject_name, "owner_person"))
	return {p for p in people if p}


# ── raising ToDos, once per key ──────────────────────────────────────────────


def _key(kind: str, *parts) -> str:
	"""A readable, collision-safe key: what it is about, plus a hash of the exact parts."""
	raw = "|".join(str(p) for p in parts)
	slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:60]
	return f"cmp:{kind}:{slug}:{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def _already(agent, key: str) -> bool:
	"""Proposed before (in any state but a failure): once per period, even when it was rejected."""
	return bool(frappe.db.sql("""SELECT name FROM `tabExcom AI Proposal` WHERE agent = %s AND action = 'create_todo'
		AND status NOT IN ('Failed', 'Cancelled', 'Expired') AND payload LIKE %s LIMIT 1""",
		(agent.name, f'%"{key}"%')))


def _todo(task, agent, key: str, owner: str | None, text: str, subject: tuple, *, due=None,
		priority: str = "Medium") -> bool:
	"""Propose one ToDo (Auto by the todo_note policy) unless this key was raised before."""
	if _already(agent, key):
		return False
	payload = {"key": key, "allocated_to": _active(owner) or in_charge(agent), "text": text, "priority": priority,
		"date": str(due) if due else None}
	return bool(proposals.propose(task, agent, "create_todo", subject=subject, payload=payload, risk="Low",
		preview_html=frappe.utils.escape_html(text)))


def _home(agent) -> tuple[str, str]:
	"""What a calendar ToDo is about: the company whose obligation it is (or the agent itself)."""
	return ("Company", agent.company) if agent.company else ("Excom AI Agent", agent.name)


# ── the planner ──────────────────────────────────────────────────────────────


def _ran(agent, key: str) -> bool:
	return bool(frappe.db.exists("Excom AI Task", {"agent": agent.name, "dedupe_key": key}))


def search_tool() -> str | None:
	from excom.excom_ai import tools

	return next((k for k in SEARCH_TOOLS if tools.available(k)), None)


@registry.planner("compliance")
def plan(agent) -> list[dict]:
	"""Each check once a day, the regulatory watch once a week (only with a search tool on)."""
	if agent.status not in ("Live", "Shadow"):
		return []
	today = _today()
	specs = []
	for kind in ("compliance_calendar", "compliance_documents", "compliance_transactions"):
		key = f"{kind}:{today}"
		if not _ran(agent, key):
			specs.append({"kind": kind, "dedupe_key": key})
	year, week, _day = today.isocalendar()
	key = f"compliance_watch:{year}-W{week:02d}"
	if search_tool() and not _ran(agent, key):
		specs.append({"kind": "compliance_watch", "dedupe_key": key})
	return specs


# ── 1. the due-date calendar ─────────────────────────────────────────────────


@registry.task_kind("compliance_calendar", priority=registry.PRIORITY["check"], effort="classify")
def compliance_calendar(task, agent) -> str:
	"""A ToDo for each obligation due within its lead days, for its named owner, once per due date."""
	today, made, unowned = _today(), 0, []
	for ob in obligations(agent):
		if ob.rule["kind"] not in DATED:
			continue
		due = next_due(ob.rule, today)
		if not due or (due - today).days > ob.lead:
			continue
		owner = _active(ob.owner)
		if not owner:
			unowned.append(ob.name)
		days = (due - today).days
		text = _("{0} is due on {1} ({2}).").format(ob.name, frappe.format(due, "Date"),
			_("today") if not days else _("in {0} days").format(days))
		if agent.company:
			text += " " + _("Company: {0}.").format(agent.company)
		if not owner:
			text += " " + _("No active owner is named for it in the Compliance playbook: please name one.")
		if _todo(task, agent, _key("due", ob.name, due), owner, text, _home(agent), due=due,
				priority="High" if days <= 2 else "Medium"):
			made += 1
	out = _("{0} due-date reminders raised").format(made)
	return out + (_("; no owner for {0}").format(", ".join(unowned)) if unowned else "")


# ── 2. document expiry ───────────────────────────────────────────────────────


def register_installed() -> bool:
	return "docpulse" in frappe.get_installed_apps() and bool(frappe.db.table_exists(REGISTER))


def register_documents(company: str | None, within_days: int, expired_within: int | None = None) -> list[frappe._dict]:
	"""Current, submitted, expiry-based documents in docpulse expiring within the window, and the
	expired ones still current (the register is curated, so an old expiry still needs a person).
	expired_within: only those expired at most this many days ago (None: all of them)."""
	if not register_installed():
		return []
	today = _today()
	window = ["<=", add_days(today, within_days)] if expired_within is None else \
		["between", [add_days(today, -expired_within), add_days(today, within_days)]]
	filters = {"docstatus": 1, "lifecycle_state": "Current", "is_expiry_based": 1, "expiry_date": window,
		"status": ["not in", ("Renewed", "Revoked", "Cancelled")]}
	if company:
		filters["company"] = company
	fields = ["name", "document_name", "expiry_date", "owner_person", "document_category", "authority",
		"document_reference_no"]
	return [frappe._dict(ref=(REGISTER, r.name), title=r.document_name, expiry=getdate(r.expiry_date),
		owner=r.owner_person,
		about=", ".join(filter(None, (r.document_category, r.authority, r.document_reference_no))))
		for r in frappe.get_all(REGISTER, filters, fields, order_by="expiry_date asc")]


def expiry_fields() -> list[tuple[str, str]]:
	"""Date fields that read as an expiry on the records that hold certificates and licences when
	there is no register (a custom `lut_expiry` on Company, a `bis_valid_upto` on Item …)."""
	out = []
	for dt in ("Company", "Item", "Supplier", "Customer"):
		out += [(dt, df.fieldname) for df in frappe.get_meta(dt).fields
			if df.fieldtype == "Date" and EXPIRY_FIELD.search(df.fieldname)]
	return out


def documents(agent, within_days: int = EXPIRY_THRESHOLDS[-1]) -> list[frappe._dict]:
	"""Everything with an expiry the agent watches, earliest expiry first: the register (expired up
	to a year ago), `expires` lines in the calendar, and expiry-like date fields (expired up to 30
	days ago). (Files have no expiry of their own; Item's has_expiry_date is batch shelf life, not a
	certificate, so neither is read.)"""
	today = _today()
	docs = register_documents(agent.company, within_days, EXPIRED_REGISTER_LOOKBACK_DAYS)
	for ob in obligations(agent):
		if ob.rule["kind"] == "expires" and (ob.rule["date"] - today).days <= within_days:
			docs.append(frappe._dict(ref=_home(agent), title=ob.name, expiry=ob.rule["date"], owner=ob.owner, about=""))
	for dt, field in expiry_fields():
		window = [add_days(today, -EXPIRED_LOOKBACK_DAYS), add_days(today, within_days)]
		filters = {field: ["between", window]}
		if dt == "Company" and agent.company:
			filters["name"] = agent.company
		label = frappe.get_meta(dt).get_label(field)
		for r in frappe.get_all(dt, filters, ["name", field], order_by=f"`{field}` asc"):
			docs.append(frappe._dict(ref=(dt, r.name), title=f"{label} ({dt} {r.name})", expiry=getdate(r[field]),
				owner=None, about=""))
	return sorted(docs, key=lambda d: d.expiry)


def stage(expiry: date, today: date) -> int | str | None:
	"""Which rung of the ladder a document is on: 'expired', 7, 30, 60, or None (not yet)."""
	days = (expiry - today).days
	if days < 0:
		return "expired"
	return next((t for t in EXPIRY_THRESHOLDS if days <= t), None)


@registry.task_kind("compliance_documents", priority=registry.PRIORITY["check"], effort="classify")
def compliance_documents(task, agent) -> str:
	"""A ToDo for the document's owner at 60, 30 and 7 days before expiry; an alert once expired.
	At most MAX_PER_RUN a run, earliest expiry first; what was raised before is skipped before
	counting, and what is left for the next run is said in the outcome."""
	today, made, expired, later = _today(), 0, [], 0
	for d in documents(agent):
		rung = stage(d.expiry, today)
		if rung is None:
			continue
		key = _key("doc", d.ref[0], d.ref[1], d.title, d.expiry, rung)
		if _already(agent, key):
			continue
		if made >= MAX_PER_RUN:
			later += 1
			continue
		owner = _active(d.owner) or in_charge(agent)
		about = f" ({d.about})" if d.about else ""
		if rung == "expired":
			text = _("{0}{1} expired on {2}. Renew it or record the renewal.").format(d.title, about,
				frappe.format(d.expiry, "Date"))
		else:
			text = _("{0}{1} expires on {2}, in {3} days. Start the renewal.").format(d.title, about,
				frappe.format(d.expiry, "Date"), (d.expiry - today).days)
		priority = {60: "Low", 30: "Medium"}.get(rung, "High")
		if not _todo(task, agent, key, owner, text, d.ref, due=max(d.expiry, today), priority=priority):
			continue
		made += 1
		if rung == "expired":
			expired.append(d.title)
			notify.alert([owner, in_charge(agent), agent.backup_user],
				_("{0} (AI): {1} has expired").format(agent.agent_name, d.title),
				message=frappe.utils.escape_html(text), doctype=d.ref[0], name=d.ref[1])
	out = _("{0} expiry reminders raised").format(made)
	out += _("; expired: {0}").format(", ".join(expired)) if expired else ""
	return out + (_("; {0} more on the next run").format(later) if later else "")


# ── 3. transaction checks ────────────────────────────────────────────────────


def _has(doctype: str, *fields: str) -> bool:
	meta = frappe.get_meta(doctype)
	return all(meta.has_field(f) for f in fields)


def pending_invoices(company: str | None = None, since=None, limit: int | None = None) -> list[frappe._dict]:
	"""Submitted Sales Invoices whose e-invoice or e-way bill is pending, retrying or failed
	(india_compliance's einvoice_status and e_waybill_status); [] without india_compliance."""
	fields = [f for f in ("einvoice_status", "e_waybill_status") if _has("Sales Invoice", f)]
	if not fields:
		return []
	conds = " OR ".join(f"si.{f} IN %(pending)s" for f in fields)
	where = [f"({conds})", "si.docstatus = 1"]
	params = {"pending": PENDING, "company": company, "since": since}
	if company:
		where.append("si.company = %(company)s")
	if since:
		where.append("si.posting_date >= %(since)s")
	cols = ", ".join(f"si.{f}" for f in fields)
	return frappe.db.sql(f"""SELECT si.name, si.customer, si.customer_name, si.posting_date, si.grand_total,
		si.currency, {cols} FROM `tabSales Invoice` si WHERE {" AND ".join(where)} ORDER BY si.posting_date DESC
		{f"LIMIT {cint(limit)}" if limit else ""}""", params, as_dict=True)


def gstin_checksum_ok(gstin: str) -> bool:
	"""GSTIN shape and check digit (the same arithmetic as india_compliance's validate_gstin_check_digit)."""
	gstin = (gstin or "").strip().upper()
	if not re.fullmatch(r"[0-9]{2}[0-9A-Z]{13}", gstin):
		return False
	chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
	total, factor = 0, 1
	for ch in gstin[:-1]:
		v = factor * chars.find(ch)
		total += v // 36 + v % 36
		factor = 2 if factor == 1 else 1
	return gstin[-1] == chars[(36 - total % 36) % 36]


def counterparties() -> list[frappe._dict]:
	"""Enabled customers and suppliers with a GSTIN, and that GSTIN's status as india_compliance
	last saw it (None: never checked)."""
	rows = []
	for dt, label in (("Customer", "customer_name"), ("Supplier", "supplier_name")):
		if not _has(dt, "gstin"):
			continue
		extra = ["account_manager"] if _has(dt, "account_manager") else []
		rows += [frappe._dict(r, doctype=dt, title=r.get(label) or r.name)
			for r in frappe.get_all(dt, {"disabled": 0, "gstin": ["is", "set"]}, ["name", label, "gstin", *extra])]
	known = {}
	if rows and frappe.db.table_exists("GSTIN"):
		gstins = list({(r.gstin or "").strip().upper() for r in rows})
		known = {g.name: g for g in frappe.get_all("GSTIN", {"name": ["in", gstins]},
			["name", "status", "cancelled_date", "is_blocked"])}
	for r in rows:
		g = known.get((r.gstin or "").strip().upper())
		r.status = g.status if g and g.status else None
		r.cancelled_date = g.cancelled_date if g else None
		r.problem = r.status if r.status in BAD_GSTIN else (_("invalid") if not gstin_checksum_ok(r.gstin) else None)
	return rows


def lut_field() -> tuple[str, str] | None:
	"""Where a LUT reference is kept: a field named lut on Sales Invoice, else on Company. GGIL has
	none (§7.2), and then the LUT check says so instead of guessing."""
	for dt in ("Sales Invoice", "Company"):
		for df in frappe.get_meta(dt).fields:
			if LUT_FIELD.search(df.fieldname) and df.fieldtype in ("Data", "Link", "Small Text", "Dynamic Link"):
				return dt, df.fieldname
	return None


def export_invoices_under_lut(company: str | None, since) -> list[frappe._dict]:
	"""Submitted export (Overseas or SEZ) invoices without IGST paid: these need a LUT."""
	if not _has("Sales Invoice", "gst_category", "is_export_with_gst"):
		return []
	filters = {"docstatus": 1, "gst_category": ["in", ("Overseas", "SEZ")], "is_export_with_gst": 0,
		"posting_date": [">=", since]}
	if company:
		filters["company"] = company
	return frappe.get_all("Sales Invoice", filters, ["name", "customer_name", "posting_date", "company"],
		order_by="posting_date desc")


def _fy(day: date) -> str:
	start = day.year if day.month >= 4 else day.year - 1
	return f"{start}-{str(start + 1)[2:]}"


@registry.task_kind("compliance_transactions", priority=registry.PRIORITY["check"], effort="classify")
def compliance_transactions(task, agent) -> str:
	"""Pending e-invoices and e-way bills, bad counterparty GSTINs, and exports without a LUT."""
	return "; ".join((_check_invoices(task, agent), _check_gstins(task, agent), _check_lut(task, agent)))


def _check_invoices(task, agent) -> str:
	if not _has("Sales Invoice", "einvoice_status") and not _has("Sales Invoice", "e_waybill_status"):
		return _("e-invoices: india_compliance is not installed")
	owner = check_owner(agent, "invoice", "way", "irn")
	made = 0
	for si in pending_invoices(agent.company, add_days(_today(), -RECENT_DAYS), limit=MAX_PER_RUN * 2):
		states = [(_("e-invoice"), si.get("einvoice_status")), (_("e-way bill"), si.get("e_waybill_status"))]
		problems = [f"{label} {state}" for label, state in states if state in PENDING]
		text = _("Sales Invoice {0} ({1}, {2}, {3}): {4}. Generate or fix it on the GST portal.").format(si.name,
			si.customer_name or si.customer, frappe.format(si.posting_date, "Date"),
			frappe.utils.fmt_money(si.grand_total, currency=si.currency), "; ".join(problems))
		key = _key("einv", si.name, si.get("einvoice_status"), si.get("e_waybill_status"))
		if _todo(task, agent, key, owner, text, ("Sales Invoice", si.name), priority="High"):
			made += 1
			if made >= MAX_PER_RUN:
				break
	return _("{0} pending e-invoices or e-way bills raised").format(made)


def _check_gstins(task, agent) -> str:
	"""Flag before the next invoice: a ToDo for the account owner (or the GSTIN checks' owner), High
	when a draft invoice for that party is already waiting."""
	made = 0
	fallback = check_owner(agent, "gstin")
	for p in counterparties():
		if not p.problem:
			continue
		draft_dt = "Sales Invoice" if p.doctype == "Customer" else "Purchase Invoice"
		party_field = "customer" if p.doctype == "Customer" else "supplier"
		drafts = frappe.db.count(draft_dt, {party_field: p.name, "docstatus": 0})
		since = _(" since {0}").format(frappe.format(p.cancelled_date, "Date")) if p.cancelled_date else ""
		text = _("Before the next invoice with {0} {1}: their GSTIN {2} is {3}{4}.").format(_(p.doctype), p.title,
			p.gstin, p.problem, since)
		if p.problem == _("invalid"):
			text += " " + _("Correct it on the {0}.").format(_(p.doctype))
		else:
			text += " " + _("Input tax credit and e-invoices depend on it: confirm the party's registration first.")
		if drafts:
			text += " " + _("{0} draft {1} waiting.").format(drafts, _(draft_dt))
		owner = _active(p.get("account_manager")) or fallback
		if _todo(task, agent, _key("gstin", p.doctype, p.name, p.gstin, p.problem), owner, text, (p.doctype, p.name),
				priority="High" if drafts else "Medium"):
			made += 1
			if made >= MAX_PER_RUN:
				break
	return _("{0} counterparties with a bad GSTIN raised").format(made)


def _check_lut(task, agent) -> str:
	since = add_days(_today(), -RECENT_DAYS)
	exports = export_invoices_under_lut(agent.company, since)
	if not exports:
		return _("no export invoices under LUT")
	where = lut_field()
	if not where:
		return _("{0} export invoices under LUT, but no LUT field to check").format(len(exports))
	owner, made = check_owner(agent, "lut"), 0
	dt, field = where
	if dt == "Sales Invoice":
		for si in frappe.get_all("Sales Invoice", {"name": ["in", [e.name for e in exports]], field: ["is", "not set"]},
				["name", "customer_name", "posting_date"], limit=MAX_PER_RUN):
			text = _("Export invoice {0} ({1}, {2}) is under LUT but has no LUT reference.").format(si.name,
				si.customer_name, frappe.format(si.posting_date, "Date"))
			if _todo(task, agent, _key("lut", si.name), owner, text, ("Sales Invoice", si.name), priority="High"):
				made += 1
		return _("{0} export invoices without a LUT reference raised").format(made)
	for company in sorted({e.company for e in exports}):
		if frappe.db.get_value("Company", company, field):
			continue
		n = sum(1 for e in exports if e.company == company)
		text = _("{0} has {1} export invoices under LUT since {2} but no LUT recorded on the company.").format(
			company, n, frappe.format(since, "Date"))
		if _todo(task, agent, _key("lut", company, _fy(_today())), owner, text, ("Company", company), priority="High"):
			made += 1
	return _("{0} companies without a LUT raised").format(made)


# ── 4. the regulatory watch ──────────────────────────────────────────────────

WATCH_SCHEMA = {
	"type": "object",
	"properties": {
		"summary": {"type": "string"},
		"items": {"type": "array", "items": {"type": "object", "properties": {
			"title": {"type": "string"}, "source": {"type": "string"}, "what_changed": {"type": "string"},
			"who_should_check": {"type": "string"}},
			"required": ["title", "source", "what_changed", "who_should_check"], "additionalProperties": False}},
	},
	"required": ["summary", "items"],
	"additionalProperties": False,
}


def watch_topics(agent) -> list[str]:
	"""GST, CBIC and DGFT notices, and the same on the agent's own keywords."""
	own = [k.strip() for k in (agent.keywords or "").split(",") if k.strip()]
	return list(dict.fromkeys([*WATCH_TOPICS, *(f"{k} notification" for k in own[:3])]))


def _domain(url: str) -> str:
	from excom.excom_ai.tools.web import normalize_domain

	return normalize_domain(url or "") or ""


@registry.task_kind("compliance_watch", priority=registry.PRIORITY["research"], effort="write")
def compliance_watch(task, agent) -> str:
	"""Search for this week's notices, read the official ones, and propose a short note for a person to
	verify. Skipped when no search tool is switched on; nothing found is not an error."""
	from excom.excom_ai import tools

	today = _today()
	year, week, _day = today.isocalendar()
	key = _key("watch", f"{year}-W{week:02d}")
	if _already(agent, key):
		return _("this week's watch is done")
	tool = search_tool()
	if not tool:
		return _("skipped: no search tool is switched on")
	query = {"keywords": watch_topics(agent), "countries": ["India"], "languages": ["en"],
		"buyer_types": [today.strftime("%B %Y")], "limit": 10}
	try:
		from excom.excom_ai.tools.discovery import estimate

		results = tools.use(tool, "search", cost=estimate(tool, query), query=query) or []
	except tools.ToolRefused as e:
		return _("skipped: {0}").format(e)
	hits = [r for r in results if r.get("website") or r.get("source_url")]
	hits.sort(key=lambda r: not OFFICIAL.search(_domain(r.get("source_url") or r.get("website"))))
	if not hits:
		return _("no notices found")
	parts = []
	can_read = tools.available("web_reader")
	for i, r in enumerate(hits[:8]):
		url = r.get("source_url") or r.get("website")
		entry = f"[{i + 1}] {r.get('name') or ''} {url}"
		if can_read and i < 3 and OFFICIAL.search(_domain(url)):
			try:
				page = tools.use("web_reader", "profile", url=url) or {}
				read = (page.get("title"), page.get("description"), (page.get("text") or "")[:1500])
				entry += "\n" + "\n".join(filter(None, read))
			except Exception:
				pass  # a page that cannot be read is listed by its title alone
		parts.append(entry)
	r = runner.ai(task, agent, output_schema=WATCH_SCHEMA,
		pack_instructions=_("You watch Indian GST, CBIC (customs) and DGFT (foreign trade) notices for the company. "
			"From the search results only, list the notices that look new and relevant to the company's business. "
			"For each: its title, the source URL as given, what changed in one sentence, and who should check it "
			"(GST, customs, foreign trade, payroll). Do not guess dates, numbers or rates the results do not state. "
			"summary is two sentences at most. If nothing is relevant, return no items and say so."),
		prompt=_("Topics: {0}. Week of {1}.").format(", ".join(query["keywords"]), frappe.format(today, "Date"))
			+ "\n\n" + untrusted("\n\n".join(parts)[:12000], _("search results")))
	items = (r.data or {}).get("items") or []
	lines = [r.data.get("summary") or ""]
	lines += [f"- {i['title']}: {i['what_changed']} ({i['who_should_check']}) {i['source']}" for i in items[:10]]
	text = _("Regulatory watch, week {0}. To verify on the official site before acting:").format(
		f"{year}-W{week:02d}") + "\n" + "\n".join(x for x in lines if x)
	_todo(task, agent, key, in_charge(agent), text[:3000], ("Excom AI Agent", agent.name), priority="Low")
	return _("{0} notices summarised from {1} results ({2})").format(len(items), len(hits), tool)


# ── knowledge recipes for the Compliance builder (§6.3) ──────────────────────


@recipe("obligations", "Obligations and next due dates", packs=("compliance",), privacy="names", order=110)
def obligations_recipe(ctx):
	"""The standard calendar with its next due dates; owners are named in the playbook later."""
	today = _today()
	rows = [{"name": n, "rule": rule, "lead_days": lead, "next_due": str(next_due(parse_rule(rule), today))}
		for n, rule, lead in STANDARD]
	soonest = sorted(rows, key=lambda r: r["next_due"])[:4]
	facts = [fact(_("{0} statutory obligations; next: {1}").format(len(rows), ", ".join(
		f"{r['name']} {frappe.format(getdate(r['next_due']), 'Date')}" for r in soonest)),
		_("the standard Indian calendar; owners are named in the playbook's Compliance section"))]
	if frappe.has_permission("Employee", "read") and frappe.db.table_exists("Employee"):
		filters = {"status": "Active", **({"company": ctx.company} if ctx.company else {})}
		n = frappe.db.count("Employee", filters)
		facts.append(fact(_("{0} active employees: PF and ESI {1}").format(n, _("apply") if n else _("may not apply")),
			_("Employee"), "Employee", level="info" if n else "warning"))
	return facts, {"obligations": rows, "template": template()}


def register_status() -> dict:
	"""Is there a document register, and how much does it hold?"""
	if "docpulse" not in frappe.get_installed_apps():
		return {"installed": False, "documents": 0}
	if not frappe.db.table_exists(REGISTER):
		return {"installed": False, "documents": 0, "note": _("docpulse is listed but its tables are missing")}
	return {"installed": True, "documents": frappe.db.count(REGISTER, {"docstatus": 1, "lifecycle_state": "Current",
		"is_expiry_based": 1})}


@recipe("expiring_documents", "Documents expiring in 90 days", packs=("compliance",), privacy="names", order=120)
def expiring_documents(ctx):
	status = register_status()
	if not status["installed"]:
		return [fact(_("No document register (docpulse) on this site: certificate and licence expiry dates are not "
			"on record"), _("installed apps"), level="warning")], {"register": False}
	need(REGISTER)
	docs = register_documents(ctx.company, DOCUMENT_HORIZON_DAYS)
	today = _today()
	expired = [d for d in docs if d.expiry < today]
	soon = [d for d in docs if d.expiry >= today]
	named = ", ".join(f"{d.title} ({frappe.format(d.expiry, 'Date')})" for d in soon[:5])
	text = _("{0} certificates and licences on record; {1} expire within {2} days{3}").format(status["documents"],
		len(soon), DOCUMENT_HORIZON_DAYS, f": {named}" if named else "")
	facts = [fact(text, _("{0}: current, expiry-based").format(REGISTER), REGISTER, [d.ref[1] for d in soon],
		level="warning" if soon else "info")]
	if expired:
		facts.append(fact(_("{0} already expired: {1}").format(len(expired), ", ".join(
			f"{d.title} ({frappe.format(d.expiry, 'Date')})" for d in expired[:5])), REGISTER, REGISTER,
			[d.ref[1] for d in expired], level="blocker"))
	return facts, {"register": True, "documents": status["documents"],
		"expiring": [{"name": d.ref[1], "title": d.title, "expiry": str(d.expiry), "owner": d.owner} for d in soon],
		"expired": [{"name": d.ref[1], "title": d.title, "expiry": str(d.expiry), "owner": d.owner} for d in expired]}


@recipe("einvoice_pending", "Pending e-invoices and e-way bills", packs=("compliance",), privacy="names", order=130)
def einvoice_pending(ctx):
	need("Sales Invoice")
	if not _has("Sales Invoice", "einvoice_status") and not _has("Sales Invoice", "e_waybill_status"):
		return [fact(_("No e-invoice or e-way bill status on invoices (india_compliance is not installed)"),
			_("Sales Invoice"), level="warning")], {}
	rows = pending_invoices(ctx.company)
	einv = [r.name for r in rows if r.get("einvoice_status") in PENDING]
	ewb = [r.name for r in rows if r.get("e_waybill_status") in PENDING]
	facts = [fact(_("{0} e-invoices and {1} e-way bills pending, retrying or failed").format(len(einv), len(ewb)),
		_("Sales Invoice: einvoice_status, e_waybill_status"), "Sales Invoice", einv + ewb,
		level="warning" if rows else "info")]
	lut = lut_field()
	facts.append(fact(_("LUT reference kept on {0}.{1}").format(*lut) if lut else _("No field for the LUT reference"),
		_("Sales Invoice, Company fields"), level="info" if lut else "warning"))
	return facts, {"einvoice": einv, "eway_bill": ewb, "lut_field": ".".join(lut) if lut else None}


@recipe("gstin_status", "Counterparty GSTIN status", packs=("compliance",), privacy="names", order=140)
def gstin_status(ctx):
	need("Customer", "Supplier")
	if not frappe.db.table_exists("GSTIN"):
		return [fact(_("No GSTIN status register (india_compliance is not installed)"), _("installed apps"),
			level="warning")], {}
	rows = counterparties()
	bad = [r for r in rows if r.problem]
	unknown = [r for r in rows if not r.status]
	named = ", ".join(f"{r.title} ({r.problem})" for r in bad[:5])
	text = _("{0} customers and suppliers with a GSTIN; {1} cancelled, inactive, suspended or invalid{2}")
	facts = [fact(text.format(len(rows), len(bad), f": {named}" if named else ""),
		_("Customer, Supplier: gstin; GSTIN: status"), "Customer", [r.name for r in bad],
		level="warning" if bad else "info"),
		fact(_("{0} of them never checked").format(len(unknown)), _("GSTIN: status"),
			level="warning" if unknown else "info")]
	return facts, {"counterparties": len(rows), "bad": [{"doctype": r.doctype, "name": r.name, "gstin": r.gstin,
		"problem": r.problem} for r in bad], "unknown": len(unknown)}


# ── readiness (§7.3) ─────────────────────────────────────────────────────────

RULES = [
	("compliance.owners", "Owners for every obligation", "Blocker", "",
		"In the agent's approved playbook, write one line per obligation in a Compliance section: "
		"Name | rule | owner | days before (for example: GSTR-1 | monthly 11 | gst.desk@example.com | 5).",
		"/app/excom-ai-playbook", "excom.excom_ai.packs.compliance.owners_check"),
	("compliance.register", "Document register", "Warning", "",
		"Install docpulse and record every certificate and licence with its expiry date and owner.",
		"/app/document-tracker-list", "excom.excom_ai.packs.compliance.register_check"),
	("compliance.gstin", "Counterparty GSTIN status", "Warning", "",
		"Refresh the GSTIN status of customers and suppliers (india_compliance: GSTIN, update status).",
		"/app/gstin", "excom.excom_ai.packs.compliance.gstin_check"),
	("compliance.people", "People", "Blocker", "", "Set an active owner and backup, and the team.", "",
		"excom.excom_ai.builder.readiness.people"),
]


@readiness.check("compliance.owners")
def owners_check(agent, facts):
	obs, bad = calendar_of(agent)
	if not obs:
		return False, _("No obligations: the approved playbook has no Compliance lines "
			"(Name | rule | owner | days before).")
	missing = [ob.name for ob in obs if not _active(ob.owner)]
	problems = []
	if missing:
		problems.append(_("no active owner for {0}").format(", ".join(missing)))
	if bad:
		problems.append(_("unreadable lines: {0}").format("; ".join(bad[:3])))
	dated = sum(1 for ob in obs if ob.rule["kind"] in DATED)
	done = _("{0} obligations, {1} dated, each with an active owner.").format(len(obs), dated)
	return not problems, "; ".join(problems) or done


@readiness.check("compliance.register")
def register_check(agent, facts):
	status = register_status()
	if not status["installed"]:
		return False, status.get("note") or _("No document register: certificate and licence expiry dates are not "
			"on this site.")
	if not status["documents"]:
		return False, _("docpulse is installed but holds no current expiry-based documents.")
	return True, _("{0} certificates and licences on record.").format(status["documents"])


@readiness.check("compliance.gstin")
def gstin_check(agent, facts):
	if not frappe.db.table_exists("GSTIN"):
		return False, _("No GSTIN status register (india_compliance).")
	rows = counterparties()
	unknown = [r for r in rows if not r.status]
	bad = [r for r in rows if r.problem]
	detail = _("{0} counterparties with a GSTIN: {1} never checked, {2} not Active or invalid.").format(len(rows),
		len(unknown), len(bad))
	return not unknown, detail


def seed() -> None:
	"""The Compliance type's readiness rules, created once (setup._builder calls every pack's seed)."""
	readiness.seed("Compliance", RULES)
