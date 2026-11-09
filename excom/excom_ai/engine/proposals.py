"""Proposals: what an agent wants to do, under its policy (HLD-007 §12.1).

    Off     the action is not done (no proposal)
    Shadow  prepared and recorded, nothing leaves (always, while the agent itself is in Shadow)
    Review  waits for the owner, backup, a listed reviewer or an admin
    Auto    done at once, within the action's daily cap; a share is drawn for review afterwards

Ceilings (constants.CEILINGS) are applied again here, so no record edit can raise an action past
them. Execution runs the action's executor once, as the agent's own user, and only while the
agent is Live and the engine is on.
"""

from __future__ import annotations

import random

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from excom.excom_ai.constants import capped
from excom.excom_ai.engine import gates, notify, ownership, registry

ACTION_POLICY = {
	# proposal action -> policy action
	"send_email": "first_email",
	"send_whatsapp": "whatsapp_template",
	"reply": "reply",
	"create_lead": "create_record",
	"update_lead": "create_record",
	"create_opportunity": "create_opportunity",
	"create_quotation": "quotation",
	"payment_reminder": "payment_reminder",
	"create_todo": "todo_note",
	"note": "todo_note",
}
MANAGERS = {"Excom Admin", "System Manager"}


def mode_for(agent, action: str, policy_action: str | None = None) -> str:
	key = policy_action or ACTION_POLICY.get(action, action)
	row = next((p for p in agent.policies or [] if p.action == key), None)
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	return capped(row.mode if row else "Review", key, pack, row.condition if row else None)


def propose(task, agent, action: str, *, policy_action: str | None = None, payload: dict | None = None,
		preview_html: str | None = None, subject: tuple | None = None, thread: str | None = None,
		channel_account: str | None = None, risk: str = "Medium", checks: dict | None = None,
		enrollment: str | None = None, step: int | None = None, kind: str | None = None,
		to_email: str | None = None, send_at=None, email_draft: str | None = None) -> str | None:
	"""Record an action under the agent's policy. Returns the proposal name (None when Off)."""
	key = policy_action or ACTION_POLICY.get(action, action)
	mode = mode_for(agent, action, key)
	if mode == "Off":
		return None
	if agent.status == "Shadow" or mode == "Shadow":
		status = "Shadow"
	elif mode == "Auto" and not _over_daily_cap(agent, key):
		status = "Approved"
	else:
		status = "Pending"
	doc = frappe.get_doc({
		"doctype": "Excom AI Proposal", "agent": agent.name, "task": task.name if task else None,
		"action": action, "status": status, "risk": risk,
		"subject_doctype": subject[0] if subject else None, "subject_name": subject[1] if subject else None,
		"thread": thread, "channel_account": channel_account, "payload": payload, "preview_html": preview_html,
		"checks": checks, "enrollment": enrollment, "step": step, "kind": kind, "to_email": to_email,
		"send_at": send_at, "email_draft": email_draft,
	})
	if status == "Approved":
		doc.decided_by = "Autopilot"
		doc.decided_on = now_datetime()
		row = next((p for p in agent.policies or [] if p.action == key), None)
		doc.sampled = 1 if random.random() * 100 < flt(row.sample_percent if row else 10) else 0
	try:
		doc.insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# Another worker proposed this sequence step a moment ago: theirs stands.
		return None
	if status == "Approved":
		execute(doc.name)
	elif status == "Pending":
		notify.alert(reviewers(agent), _("{0} (AI) needs your review: {1}").format(agent.agent_name, action),
			doctype="Excom AI Proposal", name=doc.name)
	return doc.name


def _over_daily_cap(agent, policy_action: str) -> bool:
	row = next((p for p in agent.policies or [] if p.action == policy_action), None)
	cap = cint(row.daily_cap) if row else 0
	if cap <= 0:
		return False
	actions = [a for a, p in ACTION_POLICY.items() if p == policy_action] or [policy_action]
	done = frappe.db.count("Excom AI Proposal", {"agent": agent.name, "action": ["in", actions],
		"decided_by": "Autopilot", "creation": [">=", nowdate()]})
	return done >= cap


def reviewers(agent) -> list[str]:
	extra = [u.strip() for u in (agent.reviewers or "").split(",") if u.strip()]
	return [u for u in (agent.acting_owner or agent.owner_user, agent.backup_user, *extra) if u]


def may_decide(agent, user: str) -> bool:
	return user in reviewers(agent) or user == agent.owner_user or bool(set(frappe.get_roles(user)) & MANAGERS)


def decide(name: str, decision: str, *, reason: str | None = None, note: str | None = None,
		payload: dict | None = None) -> str:
	"""A person approves (optionally with edits) or rejects a pending proposal."""
	doc = frappe.get_doc("Excom AI Proposal", name)
	agent = frappe.get_doc("Excom AI Agent", doc.agent)
	user = frappe.session.user
	if not may_decide(agent, user):
		frappe.throw(_("Only this agent's owner, backup, reviewers or an Excom Admin decide its proposals."), frappe.PermissionError)
	if doc.status != "Pending":
		frappe.throw(_("This proposal is {0}, not waiting for a decision.").format(doc.status))
	if decision == "reject":
		if not reason:
			frappe.throw(_("Say why it is rejected: the reason teaches the agent."))
		doc.update({"status": "Rejected", "reject_reason": reason, "reject_note": note})
	elif decision == "approve":
		if payload is not None and payload != frappe.parse_json(doc.payload or "{}"):
			doc.edit_diff = frappe.as_json({"before": frappe.parse_json(doc.payload or "{}"), "after": payload})
			doc.payload = payload
			doc.status = "Edited"
		else:
			doc.status = "Approved"
	else:
		frappe.throw(_("Unknown decision {0}").format(decision))
	doc.decided_by = user
	doc.decided_on = now_datetime()
	doc.save(ignore_permissions=True)
	ownership.seen(agent.name, user)
	if doc.status in ("Approved", "Edited"):
		execute(doc.name)
	return frappe.db.get_value("Excom AI Proposal", name, "status")


def execute(name: str):
	"""Run an approved proposal's executor once, as the agent's user."""
	frappe.db.sql(
		"""UPDATE `tabExcom AI Proposal` SET status = 'Executing'
		WHERE name = %s AND status IN ('Approved', 'Edited')""", name)
	if not frappe.db.sql("SELECT ROW_COUNT()")[0][0]:
		return
	doc = frappe.get_doc("Excom AI Proposal", name)
	agent = frappe.get_doc("Excom AI Agent", doc.agent)
	if not gates.engine_on() or agent.status != "Live":
		doc.db_set({"status": "Held", "hold_reason": _("Agent is {0}").format(agent.status) if gates.engine_on()
			else _("AI agents are switched off")}, update_modified=False)
		return
	registry.load()
	fn = registry.EXECUTORS.get(doc.action)
	if not fn:
		doc.db_set({"status": "Failed", "error": _("Nothing can carry out '{0}' yet.").format(doc.action)}, update_modified=False)
		return
	original = frappe.session.user
	frappe.set_user(agent.service_user or "Administrator")
	try:
		made = fn(doc, agent)
		values = {"status": "Sent" if doc.action in ("send_email", "send_whatsapp", "reply", "payment_reminder") else "Done",
			"error": None}
		if made:
			values.update({"executed_doctype": made[0], "executed_name": made[1]})
		doc.db_set(values, update_modified=False)
	except Exception as e:
		doc.db_set({"status": "Failed", "error": f"{type(e).__name__}: {e!s}"[:500]}, update_modified=False)
		frappe.log_error(title=f"Excom AI: {doc.action} failed ({agent.name})", message=frappe.get_traceback())
	finally:
		frappe.set_user(original)


def review_sample(name: str, ok: bool, note: str | None = None):
	"""A person checks an Auto action drawn for review. A bad one moves that action back to Review."""
	doc = frappe.get_doc("Excom AI Proposal", name)
	agent = frappe.get_doc("Excom AI Agent", doc.agent)
	user = frappe.session.user
	if not may_decide(agent, user):
		frappe.throw(_("Only this agent's owner, backup, reviewers or an Excom Admin review its actions."), frappe.PermissionError)
	if not doc.sampled:
		frappe.throw(_("This action was not drawn for review."))
	doc.db_set({"sample_reviewed_by": user, "reject_note": note if not ok else doc.reject_note}, update_modified=False)
	ownership.seen(agent.name, user)
	if not ok:
		key = ACTION_POLICY.get(doc.action, doc.action)
		for row in agent.policies:
			if row.action == key and row.mode == "Auto":
				frappe.db.set_value("Excom AI Policy", row.name, "mode", "Review", update_modified=False)
				agent.add_comment("Comment", _("{0} moved back to Review: a sampled action was marked wrong by {1}. {2}").format(
					key, user, note or ""))
				notify.alert([agent.owner_user, agent.backup_user],
					_("{0} (AI): {1} is back under Review").format(agent.agent_name, key),
					doctype="Excom AI Agent", name=agent.name)
