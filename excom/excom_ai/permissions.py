"""Who sees which agent's records (HLD-007 §14.2).

An agent, and its proposals, sequences and feedback, are visible to:
- its owner, backup, whoever is in charge now, its escalation user and its listed reviewers;
- members of its team (that includes the agent's own service user);
- Excom Admins and System Managers, who see every agent.

So the Domestic desk never reads the Export desk's buyer quotes, and one agent never reads another
team's work. A sequence with no agent is a shared template and is visible to every Excom Agent.
"""

from __future__ import annotations

import frappe

MANAGERS = {"Excom Admin", "System Manager"}
BY_AGENT = {
	"Excom AI Proposal": "agent",
	"Excom AI Sequence": "agent",
	"Excom AI Feedback": "agent",
	"Excom AI Template Need": "agent",
}


def is_manager(user: str) -> bool:
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & MANAGERS)


def visible_agents(user: str | None = None) -> list[str] | None:
	"""Agent names the user may see; None means all of them."""
	user = user or frappe.session.user
	if is_manager(user):
		return None
	from excom.excom.doctype.excom_team.excom_team import get_user_teams

	teams = set(get_user_teams(user))
	names = []
	for a in frappe.get_all(
		"Excom AI Agent",
		fields=["name", "team", "owner_user", "backup_user", "acting_owner", "escalation_user", "reviewers"],
	):
		reviewers = {u.strip() for u in (a.reviewers or "").split(",") if u.strip()}
		people = {a.owner_user, a.backup_user, a.acting_owner, a.escalation_user} | reviewers
		if user in people or (a.team and a.team in teams):
			names.append(a.name)
	return names


def _in(column: str, names: list[str]) -> str:
	if not names:
		return "1=0"
	return f"{column} IN ({', '.join(frappe.db.escape(n) for n in names)})"


def agent_query(user: str | None = None) -> str:
	names = visible_agents(user)
	return "" if names is None else _in("`tabExcom AI Agent`.`name`", names)


def agent_has_permission(doc, ptype=None, user=None) -> bool:
	names = visible_agents(user)
	return names is None or doc.name in names


def _by_agent_query(doctype: str, user: str | None) -> str:
	names = visible_agents(user)
	if names is None:
		return ""
	column = f"`tab{doctype}`.`{BY_AGENT[doctype]}`"
	condition = _in(column, names)
	if doctype == "Excom AI Sequence":
		condition = f"({condition} OR IFNULL({column}, '') = '')"
	return condition


def _by_agent_has_permission(doc, user) -> bool:
	names = visible_agents(user)
	agent = doc.get(BY_AGENT[doc.doctype])
	if names is None or (doc.doctype == "Excom AI Sequence" and not agent):
		return True
	return agent in names


def proposal_query(user=None):
	return _by_agent_query("Excom AI Proposal", user)


def sequence_query(user=None):
	return _by_agent_query("Excom AI Sequence", user)


def feedback_query(user=None):
	return _by_agent_query("Excom AI Feedback", user)


def by_agent_has_permission(doc, ptype=None, user=None) -> bool:
	return _by_agent_has_permission(doc, user or frappe.session.user)


def template_need_query(user=None):
	"""A WhatsApp need is shared by every agent on the business account (`agents`): each of their
	people sees it, not only the first agent's."""
	names = visible_agents(user)
	if names is None:
		return ""
	if not names:
		return "1=0"
	column = "`tabExcom AI Template Need`"
	likes = " OR ".join(f"CONCAT(', ', IFNULL({column}.`agents`, ''), ',') LIKE {frappe.db.escape('%, ' + n + ',%')}"
		for n in names)
	return f"({_in(column + '.`agent`', names)} OR {likes})"


def template_need_has_permission(doc, ptype=None, user=None) -> bool:
	names = visible_agents(user or frappe.session.user)
	if names is None:
		return True
	agents = {a.strip() for a in (doc.get("agents") or "").split(",") if a.strip()} | {doc.get("agent")}
	return bool(agents & set(names))


def template_need_agents(doc) -> list[str]:
	return [a for a in dict.fromkeys([doc.agent, *[a.strip() for a in (doc.agents or "").split(",") if a.strip()]]) if a]
