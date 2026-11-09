"""Running one task (HLD-007 §10.2): as the agent's own user, after the gates, with retries.

- The task was claimed at dispatch with a conditional update (Queued -> Running), so it starts
  once; a task not in Running is left alone. `started_on` is set here, when it really starts.
- Gates again at start. The agent stopped or on hold: the task is Held (resume brings it back).
  Anything passing (a spent budget, a quiet date, the type switched off): back to the queue for
  later, without using an attempt.
- The kind's handler runs as the agent's own user, never anyone else's; its outcome is stored.
- A failure retries with growing gaps (2, 4, 8 minutes…) up to the set attempts, then fails.
  Failures in a row put the agent on hold. A budget refusal from ai_interface is not a failure:
  the task waits for the next hour.
- ai() is how every handler calls a model: through ai_interface, charged to the agent's budget
  scope, attributed to the task, with the cached system blocks of prompts.py.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime

from excom.excom_ai.engine import gates, identity, ownership, prompts, registry

EFFORT_FIELD = {"classify": "effort_classify", "write": "effort_write", "reason": "effort_reason"}
STOPPED = ("On Hold", "Retired", "Draft", "Preparing")
RETRY_GATED_MINUTES = 30


def requeue_or_hold(task_name: str, agent, why: str) -> str:
	"""Where a task goes when a gate says not now: held only when the reason is the agent's own
	status (stopped, on hold); anything that passes by itself (a budget, a switch, a quiet date)
	puts it back in the queue for later."""
	if agent.status == "Retired":
		status, values = "Skipped", {}
	elif agent.status in STOPPED and why.startswith("Agent is"):
		status, values = "Held", {}
	else:
		status, values = "Queued", {"not_before": add_to_date(now_datetime(), minutes=RETRY_GATED_MINUTES)}
	frappe.db.set_value("Excom AI Task", task_name, {"status": status, "outcome": why, **values}, update_modified=False)
	return status


def run_task(task_name: str):
	task = frappe.get_doc("Excom AI Task", task_name)
	if task.status != "Running":
		return
	agent = frappe.get_doc("Excom AI Agent", task.agent)
	registry.load()
	spec = registry.TASK_KINDS.get(task.kind)
	if not spec:
		task.db_set({"status": "Failed", "error": f"Unknown task kind {task.kind}", "ended_on": now_datetime()},
			update_modified=False)
		frappe.db.commit()
		return
	ok, why = gates.can_work(agent, spec)
	if not ok:
		requeue_or_hold(task.name, agent, why)
		frappe.db.commit()
		return
	task.db_set("started_on", now_datetime(), update_modified=False)
	frappe.db.commit()

	settings = frappe.get_cached_doc("Excom AI Settings")
	try:
		with identity.as_user(identity.service_user_of(agent)):
			outcome = spec.handler(task, agent)
		task.db_set({"status": "Done", "outcome": (outcome or "")[:500], "ended_on": now_datetime(), "error": None},
			update_modified=False)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		if _budget_refusal(e):
			frappe.db.set_value("Excom AI Task", task.name, {"status": "Queued", "outcome": str(e)[:500],
				"not_before": add_to_date(now_datetime(), hours=1)}, update_modified=False)
			frappe.db.commit()
			return
		attempts = cint(task.attempts) + 1
		max_attempts = cint(settings.max_task_attempts) or 3
		values = {"attempts": attempts, "error": f"{type(e).__name__}: {e!s}"[:500], "ended_on": now_datetime()}
		if attempts < max_attempts:
			values.update({"status": "Queued", "not_before": add_to_date(now_datetime(), minutes=2 ** attempts)})
		else:
			values["status"] = "Failed"
		frappe.db.set_value("Excom AI Task", task.name, values, update_modified=False)
		frappe.db.commit()
		frappe.log_error(title=f"Excom AI task {task.kind} failed ({agent.name})", message=frappe.get_traceback())
		failures = _bump_failures(agent.name, +1)
		if failures >= (cint(settings.hold_after_failures) or 5):
			ownership.hold(frappe.get_doc("Excom AI Agent", agent.name), f"{failures} tasks failed in a row")
		frappe.db.commit()
		return
	if cint(agent.consecutive_failures):
		_bump_failures(agent.name, 0)


def _bump_failures(agent: str, step: int) -> int:
	"""Add one to the failures in a row (step 1) or reset them (0), in a transaction of its own."""
	try:
		if step:
			frappe.db.sql("UPDATE `tabExcom AI Agent` SET consecutive_failures = IFNULL(consecutive_failures, 0) + 1 WHERE name = %s", agent)
		else:
			frappe.db.sql("UPDATE `tabExcom AI Agent` SET consecutive_failures = 0 WHERE name = %s", agent)
		frappe.db.commit()
	except frappe.QueryDeadlockError:
		frappe.db.rollback()
	return cint(frappe.db.get_value("Excom AI Agent", agent, "consecutive_failures"))


def _budget_refusal(e: Exception) -> bool:
	try:
		from ai_interface.services.budget import BudgetExceeded
	except ImportError:
		return False
	return isinstance(e, BudgetExceeded)


def ai(
	task,
	agent,
	*,
	prompt: str | None = None,
	messages: list | None = None,
	pack_instructions: str = "",
	output_schema: dict | None = None,
	tools: list | None = None,
	allowed_tiers: tuple = ("read",),
	effort: str | None = None,
	max_tokens: int | None = None,
):
	"""Call a model for a task. Returns ai_interface's result (text, data, usage, log)."""
	from ai_interface.services.chat import run

	settings = frappe.get_cached_doc("Excom AI Settings")
	spec = registry.kind(task.kind)
	blocks = prompts.system_blocks(agent, pack_instructions)
	result = run(
		system=blocks,
		prompt=prompt,
		messages=messages,
		output_schema=output_schema,
		tools=tools,
		allowed_tiers=allowed_tiers,
		effort=effort or settings.get(EFFORT_FIELD.get(spec.effort, "effort_write")),
		cache_ttl="1h",
		model=agent.model or None,
		max_tokens=max_tokens,
		calling_app="excom",
		reference_doctype="Excom AI Task",
		reference_name=task.name,
		action=task.kind,
		budget_scope=f"Excom AI Agent:{agent.name}",
		budget_caps={"daily": flt(agent.daily_ai_budget), "monthly": flt(agent.monthly_ai_budget), "action": "Block"},
		user=agent.service_user or frappe.session.user,
	)
	frappe.db.set_value("Excom AI Task", task.name, {
		"ai_log": result.log,
		"model": result.model,
		"effort": effort or settings.get(EFFORT_FIELD.get(spec.effort, "effort_write")),
		"ai_cost": flt(frappe.db.get_value("Excom AI Task", task.name, "ai_cost")) + flt(result.usage.base_cost),
		"prompt_versions": prompts.dumps(prompts.versions(blocks)),
	}, update_modified=False)
	return result
