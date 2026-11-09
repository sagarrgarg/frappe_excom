"""Proposals: what an agent wants to do, under its policy (HLD-007 §12.1).

    Off     the action is not done (no proposal)
    Shadow  prepared and recorded, nothing leaves (always, while the agent itself is in Shadow)
    Review  waits for the owner, backup, a listed reviewer or an admin
    Auto    done at once, within the policy's daily cap; a share is drawn for review afterwards

Ceilings (constants.CEILINGS) are applied again here, so no record edit can raise an action past
them. A reply goes out on Auto only when the intent it answers is one the policy's condition
names. While the agent is On Hold nothing is Auto: it waits for a person.

Every proposal is committed as soon as it is made, and proposing the same thing again (a retried
task, a step already proposed) returns the one that exists, so a retry never acts twice.

Execution runs the action's executor once (claimed by a conditional update), as the agent's own
user, only while the engine and the agent's type are switched on, someone is in charge, and the
agent is Live (On Hold: internal actions a person approved only, never anything that leaves).
"""

from __future__ import annotations

import random

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, now_datetime, nowdate

from excom.excom_ai.constants import capped, intents
from excom.excom_ai.engine import gates, identity, notify, ownership, registry

ACTION_POLICY = {
	# proposal action -> default policy action (send_email is follow_up_email for later steps)
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
	"update_playbook": "playbook_change",
	"keep_subject": "playbook_change",
	"approve_template": "template",
	# Accounts
	"send_statement": "statement",
	"record_promise": "create_record",
	"suggest_match": "todo_note",
	# Purchase
	"send_rfq": "rfq",
	"po_chase": "po_chase",
	"create_rfq": "create_record",
	"create_supplier_quotation": "create_record",
	"create_supplier": "create_record",
}
SENDS = ("send_email", "send_whatsapp", "reply", "payment_reminder", "send_statement", "send_rfq", "po_chase")
SEND_POLICIES = ("first_email", "follow_up_email", "reply", "whatsapp_template", "payment_reminder", "prices_and_terms",
	"statement", "rfq", "po_chase")
# Actions that change only our own records: a person may let them run while the agent is on hold.
INTERNAL = ("note", "create_todo", "create_lead", "update_lead")
# Actions a person always decides, whatever the policy they share says (Review at most).
NEVER_AUTO = ("create_supplier",)
RELEASED = ("Rejected", "Cancelled", "Failed", "Expired")
MANAGERS = {"Excom Admin", "System Manager"}


def policy_row(agent, key: str):
	return next((p for p in agent.policies or [] if p.action == key), None)


def mode_for(agent, action: str, policy_action: str | None = None, intent: str | None = None) -> str:
	key = policy_action or ACTION_POLICY.get(action, action)
	row = policy_row(agent, key)
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	mode = capped(row.mode if row else "Review", key, pack, row.condition if row else None)
	if mode == "Auto" and action in NEVER_AUTO:
		mode = "Review"
	if mode == "Auto" and key == "reply" and (not intent or intent.strip().lower() not in intents(row.condition if row else "")):
		mode = "Review"  # Auto covers only the intents the condition names
	return mode


def propose(task, agent, action: str, *, policy_action: str | None = None, payload: dict | None = None,
		preview_html: str | None = None, subject: tuple | None = None, thread: str | None = None,
		channel_account: str | None = None, risk: str = "Medium", checks: dict | None = None,
		enrollment: str | None = None, step: int | None = None, kind: str | None = None,
		to_email: str | None = None, send_at=None, email_draft: str | None = None,
		intent: str | None = None, checks_ok: bool = True) -> str | None:
	"""Record an action under the agent's policy. Returns the proposal name (None when Off).

	It commits: what the task's handler wrote before it is committed with it. Handlers are
	written so that running again does no harm (look before creating), since a task that fails
	later is retried.
	"""
	key = policy_action or ACTION_POLICY.get(action, action)
	if action in SENDS and key not in SEND_POLICIES:
		key = ACTION_POLICY.get(action, action)  # a send is always judged by a send's policy
	existing = _existing(task, action, subject, thread, enrollment, step, payload)
	if existing:
		return existing
	mode = mode_for(agent, action, key, intent)
	if mode == "Off":
		return None
	if mode == "Auto" and not checks_ok:
		mode = "Review"  # a failed check always waits for a person
	if mode == "Auto" and payload and payload.get("template"):
		from excom.excom_ai import templates

		if templates.first_use_pending(payload["template"]):
			mode = "Review"  # the first send of a new template version: a person reads it once (HLD-008)
	if agent.status == "Shadow" or mode == "Shadow":
		status = "Shadow"
	elif mode == "Auto" and agent.status == "Live" and not _over_daily_cap(agent, key):
		status = "Approved"
	else:
		status = "Pending"
	doc = frappe.get_doc({
		"doctype": "Excom AI Proposal", "agent": agent.name, "task": task.name if task else None,
		"action": action, "policy_action": key, "intent": intent, "status": status, "risk": risk,
		"subject_doctype": subject[0] if subject else None, "subject_name": subject[1] if subject else None,
		"thread": thread, "channel_account": channel_account, "payload": payload, "preview_html": preview_html,
		"checks": checks, "enrollment": enrollment, "step": step, "kind": kind, "to_email": to_email,
		"send_at": send_at, "email_draft": email_draft,
	})
	if status == "Approved":
		doc.decided_by = "Autopilot"
		doc.decided_on = now_datetime()
		row = policy_row(agent, key)
		doc.sampled = 1 if random.random() * 100 < flt(row.sample_percent if row else 10) else 0
	frappe.db.savepoint("excom_ai_propose")
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another worker proposed this sequence step a moment ago: theirs stands.
		frappe.db.rollback(save_point="excom_ai_propose")
		frappe.clear_last_message()
		return _existing(None, action, None, None, enrollment, step)
	frappe.db.commit()  # made is made: a later failure in the task must not undo or repeat it
	if status == "Approved" and _over_cap_now(agent, key, doc.name):
		# Workers running side by side all counted under the cap: the late ones wait for a person.
		doc.db_set({"status": "Pending", "decided_by": None, "decided_on": None, "sampled": 0}, update_modified=False)
		frappe.db.commit()
		status = "Pending"
	if status == "Approved" and action not in SENDS:
		execute(doc.name)  # a send waits for the paced sender (sequencer.send_due), in the buyer's hours
	elif status == "Pending":
		notify.alert(reviewers(agent), _("{0} (AI) needs your review: {1}").format(agent.agent_name, action),
			doctype="Excom AI Proposal", name=doc.name)
		frappe.db.commit()
	return doc.name


def _existing(task, action, subject, thread, enrollment, step, payload=None) -> str | None:
	"""The live proposal for the same step, or the same task's same action on the same subject
	with the same payload (a task may propose two different notes on one record)."""
	if enrollment and step is not None:
		return frappe.db.get_value("Excom AI Proposal", {"enrollment": enrollment, "step": step,
			"status": ["not in", RELEASED]})
	if task:
		filters = {"task": task.name, "action": action, "status": ["not in", RELEASED]}
		if subject:
			filters.update({"subject_doctype": subject[0], "subject_name": subject[1]})
		if thread:
			filters["thread"] = thread
		want = frappe.parse_json(frappe.as_json(payload)) if payload is not None else None
		for row in frappe.get_all("Excom AI Proposal", filters, ["name", "payload"], order_by="creation asc"):
			if want is None or frappe.parse_json(row.payload or "null") == want:
				return row.name
		return None
	return None


def _over_daily_cap(agent, policy_action: str) -> bool:
	row = policy_row(agent, policy_action)
	cap = cint(row.daily_cap) if row else 0
	if cap <= 0:
		return False
	done = frappe.db.count("Excom AI Proposal", {"agent": agent.name, "policy_action": policy_action,
		"decided_by": "Autopilot", "creation": [">=", get_datetime(nowdate())]})
	return done >= cap


def _over_cap_now(agent, policy_action: str, name: str) -> bool:
	"""After committing: is this Autopilot approval past today's cap, counting in creation order?
	A fresh read sees every worker's committed approvals, so all of them agree on who is late."""
	row = policy_row(agent, policy_action)
	cap = cint(row.daily_cap) if row else 0
	if cap <= 0:
		return False
	names = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "policy_action": policy_action,
		"decided_by": "Autopilot", "creation": [">=", get_datetime(nowdate())]}, pluck="name", order_by="creation asc, name asc")
	return name in names and names.index(name) >= cap


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
	if doc.status not in ("Pending", "Held"):
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
			if doc.action in SENDS and payload.get("body") is not None:
				doc.preview_html = _preview_after_edit(doc, agent, payload)
		else:
			doc.status = "Approved"
	else:
		frappe.throw(_("Unknown decision {0}").format(decision))
	doc.decided_by = user
	doc.decided_on = now_datetime()
	doc.save(ignore_permissions=True)
	ownership.seen(agent.name, user)
	frappe.db.commit()
	if doc.status in ("Approved", "Edited") and doc.action not in SENDS:
		execute(doc.name)  # sends go out paced, in the buyer's office hours (sequencer.send_due)
	return frappe.db.get_value("Excom AI Proposal", name, "status")


def _preview_after_edit(doc, agent, payload: dict) -> str:
	"""What the buyer will now see (the sender composes from the payload the same way)."""
	from excom.excom_ai.writer import compose

	registry.load()
	if doc.action in registry.PREVIEWERS:
		return registry.PREVIEWERS[doc.action](doc, agent, payload)

	step_row = None
	if doc.enrollment:
		seq = frappe.db.get_value("Excom AI Enrollment", doc.enrollment, "sequence")
		name = frappe.db.get_value("Excom AI Sequence Step", {"parent": seq, "step": doc.step})
		step_row = frappe.get_doc("Excom AI Sequence Step", name) if name else None
	identity = frappe.db.get_value("Omni Identity", {"primary_email": doc.to_email}) if doc.to_email else None
	return compose(agent, payload.get("body") or "", step_row, identity, doc.kind or "Sequence")


def _blocked(doc, agent) -> tuple[str, str] | None:
	"""(status, why) when the proposal may not run now; None when it may."""
	if not gates.engine_on():
		return "Held", _("AI agents are switched off")
	if not frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "enabled"):
		return "Held", _("Agent type {0} is switched off").format(agent.agent_type)
	if not agent.acting_owner:
		return "Held", _("Nobody is in charge of this agent")
	if not agent.service_user or not frappe.db.get_value("User", agent.service_user, "enabled"):
		return "Held", _("The agent's own user is missing or disabled")
	if agent.status == "On Hold":
		if doc.action not in INTERNAL or doc.decided_by == "Autopilot":
			return "Held", _("Agent is on hold")
	elif agent.status != "Live":
		return "Held", _("Agent is {0}").format(agent.status)
	if mode_for(agent, doc.action, doc.policy_action, doc.intent) == "Off":
		return "Cancelled", _("The policy for {0} is now Off").format(doc.policy_action or doc.action)
	return None


def _claim(name: str) -> bool:
	"""Approved/Edited -> Executing, once. A concurrent claim (or MariaDB's snapshot check, error
	1020, on a row another transaction just changed) means someone else has it."""
	frappe.db.commit()
	try:
		frappe.db.sql("""UPDATE `tabExcom AI Proposal` SET status = 'Executing'
			WHERE name = %s AND status IN ('Approved', 'Edited')""", name)
		claimed = bool(frappe.db.sql("SELECT ROW_COUNT()")[0][0])
	except frappe.QueryDeadlockError:
		frappe.db.rollback()
		return False
	frappe.db.commit()
	return claimed


def execute(name: str):
	"""Run an approved proposal's executor once, as the agent's user."""
	if not _claim(name):
		return
	doc = frappe.get_doc("Excom AI Proposal", name)
	agent = frappe.get_doc("Excom AI Agent", doc.agent)
	blocked = _blocked(doc, agent)
	if blocked:
		values = {"status": blocked[0], "hold_reason": blocked[1]}
		if blocked[0] in RELEASED:
			values["step_key"] = None
		doc.db_set(values, update_modified=False)
		frappe.db.commit()
		return
	registry.load()
	fn = registry.EXECUTORS.get(doc.action)
	if not fn:
		doc.db_set({"status": "Failed", "step_key": None,
			"error": _("Nothing can carry out '{0}' yet.").format(doc.action)}, update_modified=False)
		frappe.db.commit()
		return
	frappe.db.savepoint("excom_ai_execute")
	try:
		with identity.as_user(agent.service_user):
			made = fn(doc, agent)
		values = {"status": "Sent" if doc.action in SENDS else "Done", "error": None, "hold_reason": None}
		if made:
			values.update({"executed_doctype": made[0], "executed_name": made[1]})
		doc.db_set(values, update_modified=False)
	except registry.Defer as d:
		# not now: back to approved, due again at the time the executor named
		frappe.db.rollback(save_point="excom_ai_execute")
		doc.db_set({"status": "Edited" if doc.edit_diff else "Approved", "send_at": d.until, "hold_reason": d.why},
			update_modified=False)
	except registry.Cancel as c:
		frappe.db.rollback(save_point="excom_ai_execute")
		doc.db_set({"status": "Cancelled", "step_key": None, "hold_reason": str(c)}, update_modified=False)
	except Exception as e:
		# Failed is final: a send that broke partway may have gone out, so it is never retried.
		frappe.db.rollback(save_point="excom_ai_execute")  # nothing half-done stays behind
		doc.db_set({"status": "Failed", "step_key": None, "error": f"{type(e).__name__}: {e!s}"[:500]}, update_modified=False)
		frappe.log_error(title=f"Excom AI: {doc.action} failed ({agent.name})", message=frappe.get_traceback())
	frappe.db.commit()


def review_sample(name: str, ok: bool, note: str | None = None):
	"""A person checks an Auto action drawn for review. A bad one moves its policy back to Review."""
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
		key = doc.policy_action or ACTION_POLICY.get(doc.action, doc.action)
		for row in agent.policies:
			if row.action == key and row.mode == "Auto":
				frappe.db.set_value("Excom AI Policy", row.name, "mode", "Review", update_modified=False)
				agent.add_comment("Comment", _("{0} moved back to Review: a sampled action was marked wrong by {1}. {2}").format(
					key, user, frappe.utils.escape_html(note or "")))
				notify.alert([agent.owner_user, agent.backup_user],
					_("{0} (AI): {1} is back under Review").format(agent.agent_name, key),
					doctype="Excom AI Agent", name=agent.name)
