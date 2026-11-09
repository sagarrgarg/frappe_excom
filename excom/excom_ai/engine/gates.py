"""May this agent do this kind of work right now? (HLD-007 §10.1, §12)

Every task passes here twice: when it is dispatched and when it starts, because a stop, a hold or
a spent budget can land in between.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, nowdate

WORKING = ("Shadow", "Live")


def engine_on() -> bool:
	return bool(frappe.db.get_single_value("Excom AI Settings", "enabled"))


def can_work(agent, spec) -> tuple[bool, str]:
	"""(True, "") or (False, why). `spec` is a registry.TaskKind."""
	if not engine_on():
		return False, "AI agents are switched off"
	if not frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "enabled"):
		return False, f"Agent type {agent.agent_type} is switched off"
	if agent.status not in WORKING:
		if agent.status == "On Hold" and spec.on_hold:
			pass
		else:
			return False, f"Agent is {agent.status}"
	if spec.outreach and agent.status == "On Hold":
		return False, "No outreach while on hold"
	if not agent.acting_owner:
		return False, "Nobody is in charge of this agent"
	if spec.outreach and is_quiet_date():
		return False, "Quiet date: no outreach today"
	over, why = over_budget(agent)
	if over:
		return False, why
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
	return flt(frappe.db.sql(
		f"SELECT COALESCE(SUM(base_cost), 0) FROM `tabAI Call Log` WHERE {cond} AND DATE(creation) = %(today)s",
		{**params, "today": nowdate()},
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
