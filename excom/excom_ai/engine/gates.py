"""May this agent do this kind of work right now? (HLD-007 §10.1, §12)

Every task passes here twice: when it is dispatched and when it starts, because a stop, a hold or
a spent budget can land in between.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, flt, get_datetime, getdate, nowdate

WORKING = ("Shadow", "Live")


def engine_on() -> bool:
	return bool(frappe.db.get_single_value("Excom AI Settings", "enabled"))


def agent_can_work(agent, allow_hold: bool = False) -> tuple[bool, str]:
	"""Checks that hold for every task of the agent alike. allow_hold: an On Hold agent passes
	(for the kinds that still run on hold)."""
	if not engine_on():
		return False, "AI agents are switched off"
	if not frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "enabled"):
		return False, f"Agent type {agent.agent_type} is switched off"
	if agent.status not in WORKING and not (allow_hold and agent.status == "On Hold"):
		return False, f"Agent is {agent.status}"
	if not agent.acting_owner:
		return False, "Nobody is in charge of this agent"
	if not agent.service_user or not frappe.db.get_value("User", agent.service_user, "enabled"):
		return False, "The agent's own user is missing or disabled"
	over, why = over_budget(agent)
	return (False, why) if over else (True, "")


def can_work(agent, spec) -> tuple[bool, str]:
	"""(True, "") or (False, why). `spec` is a registry.TaskKind."""
	ok, why = agent_can_work(agent, allow_hold=spec.on_hold)
	if not ok:
		return False, why
	if spec.outreach and agent.status == "On Hold":
		return False, "No outreach while on hold"
	if spec.outreach and is_quiet_date():
		return False, "Quiet date: no outreach today"
	return True, ""


def is_quiet_date(day=None) -> bool:
	day = getdate(day or nowdate())
	settings = frappe.get_cached_doc("Excom AI Settings")
	return any(getdate(r.date) == day for r in settings.quiet_dates or [])


def spent_today(scope: str | None = None) -> float:
	"""AI spend today in base currency: one agent's scope, or every excom call."""
	if not frappe.db.table_exists("AI Call Log"):
		return 0.0
	cond, params = ("budget_scope = %(scope)s", {"scope": scope}) if scope else ("calling_app = 'excom'", {})
	start = get_datetime(nowdate())
	return flt(frappe.db.sql(
		f"SELECT COALESCE(SUM(base_cost), 0) FROM `tabAI Call Log` WHERE {cond} AND creation >= %(start)s AND creation < %(end)s",
		{**params, "start": start, "end": add_days(start, 1)},
	)[0][0])


def over_budget(agent) -> tuple[bool, str]:
	"""Checked before starting work, so an agent over its cap stops queueing calls ai_interface
	would refuse anyway. ai_interface enforces the same caps on every call."""
	cap = flt(agent.daily_ai_budget)
	if cap > 0 and spent_today(f"Excom AI Agent:{agent.name}") >= cap:
		return True, "Daily AI budget of this agent is spent"
	total = flt(frappe.db.get_single_value("Excom AI Settings", "daily_budget_total"))
	if total > 0 and spent_today() >= total:
		return True, "Daily AI budget of all agents is spent"
	return False, ""
