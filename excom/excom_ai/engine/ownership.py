"""Who is in charge of an agent, and what happens when nobody is (HLD-007 §12.3).

Rule: an agent's authority is the lower of its policy and its owner's status.

- The owner is in charge while available. Unavailable: user disabled, employee marked Left, or on
  approved leave today. Then the backup is in charge (and the owner, backup and escalation user are
  told once).
- Neither available: the agent goes On Hold.
- Dead-man switch: neither has approved, reviewed or acknowledged anything for the set number of
  working days: On Hold.
- On Hold: outreach and Auto actions stop; tasks marked as safe on hold (bounces, opt-outs,
  out-of-office) still run. Approved actions not yet done are held. Buyers who write get the
  holding line and go to the escalation user (Phase 1F).
- Resume: the owner, backup or an admin, once someone is available again.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate

from excom.excom_ai.engine import notify, tasks

WORKING = ("Shadow", "Live")


def available(user: str | None, team: str | None = None) -> tuple[bool, str]:
	if not user:
		return False, "not set"
	if not frappe.db.get_value("User", user, "enabled"):
		return False, "user disabled"
	if team and not set(frappe.get_roles(user)) & {"Excom Admin", "System Manager"}:
		from excom.excom.doctype.excom_team.excom_team import get_user_teams_with_ancestors

		if team not in get_user_teams_with_ancestors(user):
			return False, "has moved to another team"
	if frappe.db.table_exists("Employee") and frappe.get_meta("Employee").has_field("user_id"):
		rows = frappe.get_all("Employee", {"user_id": user}, ["name", "status"])
		emp = next((r for r in rows if r.status == "Active"), None)
		if rows and not emp:
			# Left, Inactive or Suspended (a rehire has an Active row besides the old Left one)
			return False, "has left" if any(r.status == "Left" for r in rows) else "not an active employee"
		if emp and frappe.db.table_exists("Leave Application") and frappe.db.exists("Leave Application", {
			"employee": emp.name, "status": "Approved", "docstatus": 1,
			"from_date": ["<=", nowdate()], "to_date": [">=", nowdate()],
		}):
			return False, "on leave today"
	return True, ""


def evaluate(agent) -> str | None:
	"""Set who is in charge, and hold the agent if nobody is. Returns the acting owner."""
	owner_ok, owner_why = available(agent.owner_user, agent.team)
	backup_ok, backup_why = available(agent.backup_user, agent.team)
	acting = agent.owner_user if owner_ok else agent.backup_user if backup_ok else None

	if acting != agent.acting_owner:
		agent.db_set("acting_owner", acting, update_modified=False)
		if acting and acting == agent.backup_user and agent.backup_user:
			notify.alert(
				[agent.backup_user, agent.owner_user, agent.escalation_user],
				_("{0} (AI): {1} is in charge while the owner {2}").format(agent.agent_name, agent.backup_user, owner_why),
				doctype="Excom AI Agent", name=agent.name,
			)

	if agent.status in WORKING:
		if not acting:
			hold(agent, _("Owner ({0}) and backup ({1}) are both unavailable").format(owner_why, backup_why or "not set"))
		else:
			_dead_man(agent)
	return acting


def _dead_man(agent):
	days = cint(frappe.db.get_single_value("Excom AI Settings", "owner_quiet_days") or 2)
	if days <= 0:
		return
	if not agent.owner_last_seen:
		# The clock starts the first time the engine looks at the agent.
		agent.db_set("owner_last_seen", now_datetime(), update_modified=False)
		return
	if working_days_between(get_datetime(agent.owner_last_seen), now_datetime()) >= days:
		hold(agent, _("Neither owner nor backup has approved, reviewed or acknowledged anything for {0} working days").format(days))


def working_days_between(start, end) -> int:
	"""Whole Monday-to-Friday days between start and end: neither the start day nor today counts,
	so someone seen on Monday morning is held on Thursday at the earliest (with 2 days set)."""
	n, day = 0, add_days(getdate(start), 1)
	end = getdate(end)
	while day < end:
		if day.weekday() < 5:
			n += 1
		day = add_days(day, 1)
	return n


def seen(agent_name: str, user: str) -> bool:
	"""The owner or backup acted (approved, reviewed, acknowledged): restart the dead-man clock.
	Returns whether it counted (only the owner and the backup restart it)."""
	row = frappe.db.get_value("Excom AI Agent", agent_name, ["owner_user", "backup_user"], as_dict=True)
	if row and user in (row.owner_user, row.backup_user):
		frappe.db.set_value("Excom AI Agent", agent_name, "owner_last_seen", now_datetime(), update_modified=False)
		return True
	return False


def hold(agent, reason: str, by: str | None = None):
	"""Put an agent on hold: no outreach, no Auto actions; approved actions not yet done are held."""
	if agent.status == "On Hold":
		return
	if agent.status not in WORKING:
		frappe.throw(_("{0} is {1}: only an agent that is working can be put on hold.").format(agent.agent_name, agent.status))
	reason = frappe.utils.escape_html(reason or "")
	agent.db_set({"status": "On Hold", "hold_reason": reason, "resume_status": agent.status}, update_modified=False)
	skipped = tasks.cancel_open(agent.name, _("Agent on hold: {0}").format(reason))
	for name in frappe.get_all("Excom AI Proposal", {"agent": agent.name, "status": ["in", ("Approved", "Edited")]}, pluck="name"):
		frappe.db.set_value("Excom AI Proposal", name, {"status": "Held", "hold_reason": reason}, update_modified=False)
	agent.add_comment("Comment", _("On hold{0}: {1}. {2} queued tasks skipped.").format(
		f" ({by})" if by else "", reason, skipped))
	notify.alert(
		[agent.owner_user, agent.backup_user, agent.escalation_user, *notify.admins()],
		_("{0} (AI) is on hold: {1}").format(agent.agent_name, reason),
		doctype="Excom AI Agent", name=agent.name,
	)


def release_held(agent_name: str) -> None:
	"""The agent works again (Shadow or Live, however it got there): held tasks go back to the
	queue, and held actions go back to a person, since what was approved before is decided again."""
	for name in frappe.get_all("Excom AI Proposal", {"agent": agent_name, "status": "Held"}, pluck="name"):
		frappe.db.set_value("Excom AI Proposal", name, {"status": "Pending", "hold_reason": None}, update_modified=False)
	for name in frappe.get_all("Excom AI Task", {"agent": agent_name, "status": "Held"}, pluck="name"):
		frappe.db.set_value("Excom AI Task", name, {"status": "Queued", "not_before": now_datetime()}, update_modified=False)


def resume(agent, user: str):
	if agent.status != "On Hold":
		frappe.throw(_("{0} is not on hold.").format(agent.agent_name))
	acting = evaluate(agent)
	if not acting:
		frappe.throw(_("Nobody can be in charge: the owner and backup are both unavailable. Change them first."))
	doc = frappe.get_doc("Excom AI Agent", agent.name)
	target = doc.resume_status if doc.resume_status in WORKING else "Shadow"
	if target == "Live" and (not doc.brief_approved_on or doc.readiness_state not in ("Ready", "Warnings")):
		target = "Shadow"  # the brief or readiness changed while it was held: it earns Live again
	doc.update({"status": target, "hold_reason": None, "resume_status": None, "consecutive_failures": 0,
		"owner_last_seen": now_datetime()})
	doc.save(ignore_permissions=True)  # the record's own checks run; on_update releases held work
	doc.add_comment("Comment", _("Resumed as {0} by {1}.").format(target, user))
