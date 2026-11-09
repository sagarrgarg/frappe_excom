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


def dispatch(limit: int | None = None, agents: list[str] | None = None) -> list[str]:
	"""Start due tasks in turns between agents. Returns the task names started.

	Each agent with due work is checked once: an agent that cannot work now (stopped, over budget,
	its type switched off) is skipped as a whole, so its queue never crowds out the others (the
	failure of Harvey's single ladder). From each agent that can work, its best tasks by priority
	then age, taken in turns. `agents` limits the run to some agents (tests, a manual kick).
	"""
	limit = limit or cint(frappe.db.get_single_value("Excom AI Settings", "tasks_per_tick")) or 20
	registry.load()
	filters = {"status": "Queued", "not_before": ["<=", now_datetime()]}
	if agents:
		filters["agent"] = ["in", agents]
	due_agents = frappe.get_all("Excom AI Task", filters, ["agent"], group_by="agent", pluck="agent")

	queues: dict[str, list] = {}
	docs = {}
	for name in due_agents:
		agent = frappe.get_doc("Excom AI Agent", name)
		ok, why = gates.agent_can_work(agent, allow_hold=True)
		if not ok:
			_park(name, agent, why)
			continue
		docs[name] = agent
		queues[name] = frappe.get_all("Excom AI Task", {**filters, "agent": name}, ["name", "kind"],
			order_by="priority asc, creation asc", limit=limit)

	order = []
	while queues and len(order) < limit:
		for name in list(queues):
			if queues[name]:
				order.append((name, queues[name].pop(0)))
			if not queues[name]:
				del queues[name]
			if len(order) >= limit:
				break

	started = []
	for agent_name, t in order:
		try:
			spec = registry.TASK_KINDS.get(t.kind)
			if not spec:
				frappe.db.set_value("Excom AI Task", t.name, {"status": "Failed", "error": f"Unknown task kind {t.kind}"},
					update_modified=False)
				continue
			ok, why = gates.can_work(docs[agent_name], spec)
			if not ok:
				from excom.excom_ai.engine.runner import requeue_or_hold

				requeue_or_hold(t.name, docs[agent_name], why)
				continue
			if _claim(t.name):
				started.append(t.name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom AI: dispatch of {t.name} failed", message=frappe.get_traceback())
	frappe.db.commit()
	for name in started:
		frappe.enqueue(
			"excom.excom_ai.engine.runner.run_task", queue="long", timeout=900, task_name=name,
			job_id=_job_id(name), deduplicate=True,
		)
	return started


def _park(agent_name: str, agent, why: str) -> None:
	"""An agent that cannot work: its due tasks wait (or are held when it is stopped)."""
	from excom.excom_ai.engine.runner import requeue_or_hold

	for t in frappe.get_all("Excom AI Task", {"agent": agent_name, "status": "Queued", "not_before": ["<=", now_datetime()]},
			pluck="name"):
		requeue_or_hold(t, agent, why)
	frappe.db.commit()


def _claim(task: str) -> bool:
	"""Queued -> Running, once. Fresh transaction; a concurrent claim (or MariaDB's snapshot check,
	error 1020) means another tick has it."""
	frappe.db.commit()
	try:
		frappe.db.sql("UPDATE `tabExcom AI Task` SET status = 'Running', started_on = NULL WHERE name = %s AND status = 'Queued'", task)
		claimed = bool(frappe.db.sql("SELECT ROW_COUNT()")[0][0])
	except frappe.QueryDeadlockError:
		frappe.db.rollback()
		return False
	frappe.db.commit()
	return claimed


def _job_id(task: str) -> str:
	return f"excom_ai_task::{task}"


def recover_stale():
	"""Running tasks that will never finish: the worker died after starting it (started more than
	STALE_MINUTES ago), or the job is gone from the queue before it started. Time spent waiting in
	a busy queue is not counted."""
	from frappe.utils.background_jobs import is_job_enqueued

	cutoff = add_to_date(now_datetime(), minutes=-STALE_MINUTES)
	max_attempts = cint(frappe.db.get_single_value("Excom AI Settings", "max_task_attempts")) or 3
	for t in frappe.get_all("Excom AI Task", {"status": "Running"}, ["name", "attempts", "started_on"]):
		if t.started_on and t.started_on > cutoff:
			continue
		if not t.started_on and is_job_enqueued(_job_id(t.name)):
			continue  # still waiting in the queue
		if t.started_on and is_job_enqueued(_job_id(t.name)):
			continue  # still running
		attempts = cint(t.attempts) + 1
		frappe.db.set_value("Excom AI Task", t.name, {
			"status": "Queued" if attempts < max_attempts else "Failed",
			"attempts": attempts,
			"error": "Stopped without finishing (the worker was restarted or timed out)",
		}, update_modified=False)
	frappe.db.commit()
