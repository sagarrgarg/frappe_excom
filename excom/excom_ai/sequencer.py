# Parts of this file follow Harvey (harvey/agents/sender.py, harvey/send_window.py), MIT License,
# Copyright (c) 2026 Ethan Rogers. See https://opensource.org/licenses/MIT.
"""Sequencer and sender (HLD-007 §11.1, Appendix A), Phase 1E.

Enrolling: each tick a working agent takes ready people (verified address, consent not blocked)
from its best-fitting researched companies, one person per company, as many as its mailboxes have
room for today, and enrols them in its sequence; the writer then writes their emails. In Shadow
nothing is promoted into the CRM; a Lead is made when a Live agent is about to send.

Sending: approving a cold email does not send it at once. The sender (`send_due`, every tick)
sends what is due, at most 8 a run, 4 to 15 seconds apart, as the agent's own user:

- in the buyer's office hours (08:00 to 17:00, Monday to Friday, in their time zone), else at the
  next window plus a little spread so a batch does not all go on one minute;
- a follow-up only after the step before it went out, and its wait (business days, at least one
  day) has passed; if the step before will never go, the follow-up is cancelled;
- within the mailbox's warm-up and daily cap;
- never to an enrollment that stopped (a reply, an opt-out, a bounce) or a company someone there
  answered for (the company stop);
- an email whose send failed partway is marked Failed and never retried: it may have gone out.
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, get_system_timezone, getdate, now_datetime

from excom.excom_ai import mail
from excom.excom_ai.builder import geo
from excom.excom_ai.constants import OUTREACH_PURPOSES
from excom.excom_ai.engine import proposals, registry

WINDOW = (time(8, 0), time(17, 0))
START_SPREAD_MINUTES = 30
MIN_FOLLOW_UP_GAP = timedelta(days=1)
FIT_TO_ENROL = 50


# ── windows ──────────────────────────────────────────────────────────────────


def _system() -> ZoneInfo:
	return ZoneInfo(get_system_timezone() or "UTC")


def to_zone(dt, zone: str) -> datetime:
	return get_datetime(dt).replace(tzinfo=_system()).astimezone(ZoneInfo(zone))


def from_zone(dt: datetime) -> datetime:
	"""A zone-aware time back to the site's naive system time, as Frappe stores it."""
	return dt.astimezone(_system()).replace(tzinfo=None)


def in_window(dt, zone: str) -> bool:
	local = to_zone(dt, zone)
	return local.weekday() < 5 and WINDOW[0] <= local.time() < WINDOW[1]


def next_window(dt, zone: str, spread: bool = True) -> datetime:
	"""The earliest time at or after dt inside the buyer's window (system time)."""
	if in_window(dt, zone):
		return get_datetime(dt)
	local = to_zone(dt, zone)
	day = local.date()
	if local.time() >= WINDOW[1]:
		day += timedelta(days=1)
	while day.weekday() >= 5:
		day += timedelta(days=1)
	start = datetime.combine(day, WINDOW[0], tzinfo=ZoneInfo(zone))
	if spread:
		start += timedelta(minutes=random.randint(0, START_SPREAD_MINUTES))
	return from_zone(start)


def add_business_days(dt, days: int, zone: str) -> datetime:
	local = to_zone(dt, zone)
	n = 0
	while n < days:
		local += timedelta(days=1)
		if local.weekday() < 5:
			n += 1
	return from_zone(local)


def recipient_zone(enrollment, agent) -> str:
	"""The person's own, else their company's country's main zone, else the agent's first market's."""
	if enrollment.person:
		tz = frappe.db.get_value("Excom AI Person", enrollment.person, "timezone")
		if tz:
			return tz
	country = frappe.db.get_value("Excom AI Company", enrollment.company, "country") if enrollment.company else None
	if not country and enrollment.lead:
		country = frappe.db.get_value("Lead", enrollment.lead, "country")
	if country and geo.main_timezone(country):
		return geo.main_timezone(country)
	return next((m.timezone for m in agent.markets or [] if m.timezone), None) or get_system_timezone() or "UTC"


# ── enrolling ────────────────────────────────────────────────────────────────


def outreach_accounts(agent) -> list[str]:
	return [r.channel_account for r in agent.channels or [] if r.purpose in OUTREACH_PURPOSES and not r.paused
		and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "email"]


def room_today(agent) -> int:
	return sum(mail.room(agent, a) for a in outreach_accounts(agent))


def enrol_ready(agent, limit: int | None = None) -> list[str]:
	"""Enrol ready people, one per company, best fit first, within today's sending room."""
	from excom.excom_ai.writer import default_sequence

	limit = room_today(agent) if limit is None else limit
	if limit <= 0 and agent.status == "Live":
		return []
	limit = limit if agent.status == "Live" else max(limit, 5)  # Shadow drafts a few a day to compare
	today = getdate()
	started = frappe.db.count("Excom AI Enrollment", {"agent": agent.name, "creation": [">=", get_datetime(today)]})
	limit = max(0, limit - started)
	rows = frappe.db.sql("""SELECT p.name, p.company, p.language, p.lead FROM `tabExcom AI Person` p
		JOIN `tabExcom AI Company` c ON c.name = p.company
		WHERE c.agent = %(agent)s AND c.status NOT IN ('Excluded', 'Promoted') AND IFNULL(c.company_stop, 0) = 0
			AND IFNULL(c.fit_score, 0) >= %(fit)s AND p.status = 'Ready' AND p.email_status IN ('Verified', 'Risky')
			AND IFNULL(p.consent_verdict, 'Unknown') != 'Blocked'
			AND NOT EXISTS (SELECT 1 FROM `tabExcom AI Enrollment` e WHERE e.company = p.company)
		ORDER BY c.fit_score DESC, p.is_inbox ASC, p.creation ASC""", {"agent": agent.name, "fit": FIT_TO_ENROL}, as_dict=True)
	seq = default_sequence(agent)
	made, seen = [], set()
	from excom.excom_ai.engine import tasks

	for r in rows:
		if len(made) >= limit or r.company in seen:
			continue
		seen.add(r.company)
		lead = r.lead
		if agent.status == "Live" and not lead:
			from excom.excom_ai.research import promote

			try:
				lead = promote(r.name, agent)["lead"]
			except frappe.ValidationError:
				frappe.clear_last_message()
				continue
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": agent.name, "sequence": seq, "person": r.name,
			"company": r.company, "lead": lead, "language": r.language, "status": "Active", "current_step": 0,
			"prospect": frappe.db.get_value("Excom AI Company", r.company, "prospect")}).insert(ignore_permissions=True)
		tasks.add(agent.name, "write_sequence", subject=("Excom AI Enrollment", enr.name))
		made.append(enr.name)
	return made


# ── sending ──────────────────────────────────────────────────────────────────


def _previous(proposal) -> frappe._dict | None:
	return frappe.db.get_value("Excom AI Proposal", {"enrollment": proposal.enrollment, "step": cint(proposal.step) - 1,
		"status": ["not in", ("Rejected", "Expired")]}, ["name", "status", "sent_on"], as_dict=True, order_by="creation desc")


def _wait_days(proposal) -> int:
	seq = frappe.db.get_value("Excom AI Enrollment", proposal.enrollment, "sequence")
	return cint(frappe.db.get_value("Excom AI Sequence Step", {"parent": seq, "step": proposal.step}, "wait_days"))


@registry.executor("send_email")
def send_email(proposal, agent):
	"""Send one approved sequence email now, or say when it can go (Defer) or that it never will (Cancel)."""
	from excom.excom.channels.email.outbound import send_email_reply
	from excom.excom.services.thread_service import upsert_thread
	from excom.excom_ai.writer import compose

	enr = frappe.get_doc("Excom AI Enrollment", proposal.enrollment) if proposal.enrollment else None
	if enr and enr.status not in ("Active",):
		raise registry.Cancel(_("the sequence stopped: {0}").format(enr.stop_reason or enr.status))
	company = frappe.get_doc("Excom AI Company", enr.company) if enr and enr.company else None
	if company and company.company_stop:
		raise registry.Cancel(_("someone at {0} replied: no more cold email there").format(company.company_name))
	zone = recipient_zone(enr, agent) if enr else (get_system_timezone() or "UTC")
	now = now_datetime()
	if enr and cint(proposal.step) > 1:
		prev = _previous(proposal)
		if prev and prev.status in ("Pending", "Approved", "Edited", "Executing", "Sending", "Held", "Shadow"):
			raise registry.Defer(add_days(now, 0) + timedelta(hours=1), _("waiting for step {0} to go").format(cint(proposal.step) - 1))
		if prev and prev.status != "Sent":
			raise registry.Cancel(_("step {0} was not sent ({1})").format(cint(proposal.step) - 1, prev.status))
		if prev and prev.sent_on:
			ready = max(get_datetime(prev.sent_on) + MIN_FOLLOW_UP_GAP, add_business_days(prev.sent_on, _wait_days(proposal), zone))
			if now < ready:
				raise registry.Defer(next_window(ready, zone), _("the follow-up waits {0} business days").format(_wait_days(proposal)))
	if not in_window(now, zone):
		raise registry.Defer(next_window(now, zone), _("outside the buyer's office hours ({0})").format(zone))
	account = proposal.channel_account or next(iter(outreach_accounts(agent)), None)
	if not account:
		raise registry.Cancel(_("the agent has no mailbox for outreach"))
	if mail.room(agent, account) <= 0:
		raise registry.Defer(next_window(add_days(now, 1).replace(hour=0, minute=0), zone),
			_("{0} has sent its share for today").format(account))
	if enr and not enr.lead and enr.person:
		from excom.excom_ai.research import promote

		enr.db_set("lead", promote(enr.person, agent)["lead"], update_modified=False)
	to = proposal.to_email or (frappe.db.get_value("Lead", enr.lead, "email_id") if enr else None)
	identity = frappe.db.get_value("Omni Identity", {"primary_email": to})
	if not identity:
		raise registry.Cancel(_("no person record for {0}").format(to))
	thread = (enr.thread if enr else None) or proposal.thread or upsert_thread(identity, "email", account)
	payload = frappe.parse_json(proposal.payload or "{}")
	step_row = frappe.get_doc("Excom AI Sequence Step", {"parent": enr.sequence, "step": proposal.step}) if enr else None
	html = compose(agent, payload.get("body") or "", step_row, identity, proposal.kind or "Sequence")
	account_doc = frappe.get_doc("Excom Channel Account", account)
	message = send_email_reply(thread, to=to, subject=payload.get("subject") or "", body_html=html,
		extra_headers=mail.cold_headers(identity, account_doc) if (proposal.kind or "Sequence") == "Sequence" else None)
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime(), "thread": thread},
		update_modified=False)
	if enr:
		enr.db_set({"thread": thread, "current_step": cint(proposal.step), "last_sent_on": now_datetime()}, update_modified=False)
	frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle",
		"ai_stage": "Initial outreach"}, update_modified=False)
	return ("Excom Message", message)


@registry.task_kind("send_due", priority=registry.PRIORITY["send"], effort="classify")
def send_due(task, agent) -> str:
	"""Send what is due now, at most 8, follow-ups first, 4 to 15 seconds apart."""
	due = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "status": ["in", ("Approved", "Edited")],
		"action": ["in", proposals.SENDS]}, ["name", "step", "send_at"], order_by="step desc, send_at asc, creation asc", limit=50)
	now = now_datetime()
	due = [p for p in due if not p.send_at or get_datetime(p.send_at) <= now][: mail.RUN_MAX]
	results = mail.send_paced([lambda n=p.name: proposals.execute(n) for p in due])
	sent = frappe.db.count("Excom AI Proposal", {"name": ["in", [p.name for p in due] or ["-"]], "status": "Sent"})
	return _("{0} due, {1} sent").format(len(due), sent) + ("" if all(r is None for r in results) else _(" (some failed)"))


@registry.planner("sales")
def plan(agent) -> list[dict]:
	"""Each tick: enrol ready people (Live: within today's room; Shadow: a few to compare), and send
	what is due."""
	specs = []
	if agent.status in ("Live", "Shadow"):
		enrol_ready(agent)
	if agent.status == "Live" and frappe.db.exists("Excom AI Proposal", {"agent": agent.name, "status": ["in", ("Approved", "Edited")],
			"action": ["in", proposals.SENDS]}):
		specs.append({"kind": "send_due", "dedupe_key": f"send_due:{agent.name}"})
	return specs


def dry_run(agent_name: str, days: int = 21) -> list[dict]:
	"""When each step of each active enrollment would go, with today's rules (windows, waits,
	warm-up and caps), without sending anything: to compare with Harvey's schedule."""
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	accounts = outreach_accounts(agent)
	rows = {r.channel_account: r for r in agent.channels or []}
	used: dict[tuple, int] = {}
	plan_ = []
	start = now_datetime()
	for enr in frappe.get_all("Excom AI Enrollment", {"agent": agent_name, "status": "Active"}, ["name"], order_by="creation asc"):
		e = frappe.get_doc("Excom AI Enrollment", enr.name)
		zone = recipient_zone(e, agent)
		steps = sorted(frappe.get_doc("Excom AI Sequence", e.sequence).steps, key=lambda s: s.step)
		at = start
		for s in steps:
			if s.step > 1:
				at = max(at + MIN_FOLLOW_UP_GAP, add_business_days(at, cint(s.wait_days), zone))
			at = next_window(at, zone, spread=False)
			account = accounts[0] if accounts else None
			while account and (at - start).days <= days:
				day = getdate(at)
				if used.get((account, day), 0) < mail.allowance(rows[account], day):
					used[(account, day)] = used.get((account, day), 0) + 1
					break
				at = next_window(datetime.combine(day + timedelta(days=1), time(0, 0)), zone, spread=False)
			plan_.append({"enrollment": e.name, "lead": e.lead, "person": e.person, "step": s.step, "zone": zone,
				"send_at": str(at) if (at - start).days <= days else None, "account": account})
	return plan_
