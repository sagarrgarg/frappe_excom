"""The tick (HLD-007 §10.1). Scheduled every 2 minutes.

While the master switch is off it reads one setting and returns: no queries over agents, no jobs.

When on:
1. Tasks stuck in Running (a worker died) go back to the queue, or fail after their attempts.
2. For every agent in Shadow, Live or On Hold: who is in charge (ownership.evaluate), then its
   pack's planners add tasks (not while on hold).
3. Dispatch: due tasks start, at most `tasks_per_tick`, taking turns between agents so one busy
   agent never starves another (Harvey's single global ladder did). Within an agent, priority then
   age. Each task is claimed with a conditional update, so it starts exactly once.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from excom.excom_ai.engine import gates, ownership, registry

ACTIVE = ("Shadow", "Live", "On Hold")
STALE_MINUTES = 30


def tick():
	if not gates.engine_on():
		return
	registry.load()
	recover_stale()
	for name in frappe.get_all("Excom AI Agent", {"status": ["in", ACTIVE]}, pluck="name"):
		agent = frappe.get_doc("Excom AI Agent", name)
		try:
			ownership.evaluate(agent)
			if agent.status in ("Shadow", "Live"):
				plan(agent)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom AI: planning failed for {name}", message=frappe.get_traceback())
	dispatch()


def plan(agent):
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	from excom.excom_ai.engine import tasks

	for planner in registry.PLANNERS.get(pack, []):
		for spec in planner(agent) or []:
			tasks.add(agent.name, **spec)


def dispatch(limit: int | None = None) -> list[str]:
	"""Start due tasks in turns between agents. Returns the task names started."""
	limit = limit or cint(frappe.db.get_single_value("Excom AI Settings", "tasks_per_tick")) or 20
	due = frappe.get_all(
		"Excom AI Task",
		{"status": "Queued", "not_before": ["<=", now_datetime()]},
		["name", "agent", "kind"],
		order_by="priority asc, creation asc",
		limit=limit * 10,
	)
	by_agent: dict[str, list] = {}
	for t in due:
		by_agent.setdefault(t.agent, []).append(t)
	order = []
	while by_agent and len(order) < limit:
		for agent in list(by_agent):
			order.append(by_agent[agent].pop(0))
			if not by_agent[agent]:
				del by_agent[agent]
			if len(order) >= limit:
				break

	started, agents = [], {}
	for t in order:
		agent = agents.get(t.agent) or agents.setdefault(t.agent, frappe.get_doc("Excom AI Agent", t.agent))
		ok, why = gates.can_work(agent, registry.kind(t.kind))
		if not ok:
			status = "Held" if agent.status in ("On Hold", "Retired") else "Queued"
			frappe.db.set_value("Excom AI Task", t.name, {"status": status, "outcome": why}, update_modified=False)
			continue
		frappe.db.sql(
			"""UPDATE `tabExcom AI Task` SET status = 'Running', started_on = %s
			WHERE name = %s AND status = 'Queued'""",
			(now_datetime(), t.name),
		)
		if not frappe.db.sql("SELECT ROW_COUNT()")[0][0]:
			continue  # another tick took it
		started.append(t.name)
	frappe.db.commit()
	for name in started:
		frappe.enqueue(
			"excom.excom_ai.engine.runner.run_task", queue="long", timeout=900, task_name=name,
			job_id=f"excom_ai_task::{name}", deduplicate=True,
		)
	return started


def recover_stale():
	cutoff = add_to_date(now_datetime(), minutes=-STALE_MINUTES)
	max_attempts = cint(frappe.db.get_single_value("Excom AI Settings", "max_task_attempts")) or 3
	for t in frappe.get_all("Excom AI Task", {"status": "Running", "started_on": ["<", cutoff]}, ["name", "attempts"]):
		attempts = cint(t.attempts) + 1
		frappe.db.set_value("Excom AI Task", t.name, {
			"status": "Queued" if attempts < max_attempts else "Failed",
			"attempts": attempts,
			"error": "Stopped without finishing (the worker was restarted or timed out)",
		}, update_modified=False)
	frappe.db.commit()
