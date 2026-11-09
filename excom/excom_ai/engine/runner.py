"""Running one task (HLD-007 §10.2): as the agent's own user, after the gates, with retries.

- The task was claimed at dispatch with a conditional update (Queued -> Running), so it starts
  once; a task not in Running is left alone.
- Gates again at start: a stop, hold or spent budget since dispatch puts it back or holds it.
- The kind's handler runs as the agent's service user; its outcome line is stored.
- A failure retries with growing gaps (2, 4, 8 minutes…) up to the set attempts, then fails.
  Failures in a row put the agent on hold.
- ai() is how every handler calls a model: through ai_interface, charged to the agent's budget
  scope, attributed to the task, with the cached system blocks of prompts.py.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime

from excom.excom_ai.engine import gates, ownership, prompts, registry

EFFORT_FIELD = {"classify": "effort_classify", "write": "effort_write", "reason": "effort_reason"}


def run_task(task_name: str):
	task = frappe.get_doc("Excom AI Task", task_name)
	if task.status != "Running":
		return
	agent = frappe.get_doc("Excom AI Agent", task.agent)
	spec = registry.kind(task.kind)
	ok, why = gates.can_work(agent, spec)
	if not ok:
		task.db_set({"status": "Held" if agent.status == "On Hold" else "Queued", "outcome": why}, update_modified=False)
		frappe.db.commit()
		return

	settings = frappe.get_cached_doc("Excom AI Settings")
	original = frappe.session.user
	frappe.set_user(agent.service_user or "Administrator")
	try:
		outcome = spec.handler(task, agent)
		task.reload()
		task.db_set({"status": "Done", "outcome": (outcome or "")[:500], "ended_on": now_datetime(), "error": None},
			update_modified=False)
		if cint(agent.consecutive_failures):
			agent.db_set("consecutive_failures", 0, update_modified=False)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		attempts = cint(task.attempts) + 1
		max_attempts = cint(settings.max_task_attempts) or 3
		values = {"attempts": attempts, "error": f"{type(e).__name__}: {e!s}"[:500], "ended_on": now_datetime()}
		if attempts < max_attempts:
			values.update({"status": "Queued", "not_before": add_to_date(now_datetime(), minutes=2 ** attempts)})
		else:
			values["status"] = "Failed"
		frappe.db.set_value("Excom AI Task", task.name, values, update_modified=False)
		failures = cint(frappe.db.get_value("Excom AI Agent", agent.name, "consecutive_failures")) + 1
		frappe.db.set_value("Excom AI Agent", agent.name, "consecutive_failures", failures, update_modified=False)
		frappe.log_error(title=f"Excom AI task {task.kind} failed ({agent.name})", message=frappe.get_traceback())
		limit = cint(settings.hold_after_failures) or 5
		if failures >= limit:
			frappe.set_user(original)
			ownership.hold(frappe.get_doc("Excom AI Agent", agent.name), f"{failures} tasks failed in a row")
		frappe.db.commit()
	finally:
		frappe.set_user(original)


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
