"""Whose a conversation is (HLD-007 §11): one rule for every pack that writes on a customer's or a
supplier's existing thread, so Accounts and Purchase never disagree.

A conversation is not the agent's to take when:
- a person has it (ai_mode Needs person or Hold);
- another *working* agent handles it (Shadow, Live, On Hold) — a paused or retired agent's old claim
  does not count, or one conversation once touched would block reminders for ever;
- a colleague outside the agent's own people (owner, acting owner, backup, the agent's service user)
  is assigned to it and it is still open.
"""

from __future__ import annotations

import frappe
from frappe import _

WORKING = ("Shadow", "Live", "On Hold")


def our_people(agent) -> set:
	return {u for u in (agent.owner_user, agent.acting_owner, agent.backup_user, agent.service_user) if u}


def holder(thread: str | None, agent) -> str | None:
	"""Why this conversation is not the agent's, else None."""
	t = frappe.db.get_value("Excom Thread", thread, ["ai_agent", "ai_mode", "assigned_to", "status"],
		as_dict=True) if thread else None
	if not t:
		return None
	if t.ai_mode in ("Needs person", "Hold"):
		return _("a person has the conversation ({0})").format(t.ai_mode)
	if t.ai_agent and t.ai_agent != agent.name and frappe.db.get_value("Excom AI Agent", t.ai_agent, "status") in WORKING:
		return _("the conversation is handled by {0} (AI)").format(
			frappe.db.get_value("Excom AI Agent", t.ai_agent, "agent_name") or t.ai_agent)
	if t.ai_agent != agent.name and t.assigned_to and t.assigned_to not in our_people(agent) and t.status != "Closed" \
			and frappe.db.get_value("User", t.assigned_to, "enabled"):
		return _("{0} is handling the conversation").format(t.assigned_to)
	return None


def claim(thread: str | None, agent, created: bool = False) -> bool:
	"""After a send: answers on this conversation come back to the agent, but only when it may take it
	(a thread it just made, one it has, or one nobody else holds). The mode a person set is kept; on a
	conversation one of the agent's own people is assigned to, the agent only drafts for them (Assist).
	Returns whether the agent has the conversation now."""
	if not thread:
		return False
	if created:
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle"}, update_modified=False)
		return True
	if holder(thread, agent):
		return False
	t = frappe.db.get_value("Excom Thread", thread, ["ai_mode", "assigned_to", "ai_agent"], as_dict=True) or frappe._dict()
	mode = t.ai_mode or ("Assist" if t.assigned_to and t.ai_agent != agent.name else "Handle")
	frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": mode}, update_modified=False)
	return True
