"""Every AI agent acts as its own Frappe user (HLD-007 §14.2).

So what it does is recorded under its own name (`Excom Message.created_by_user`, comments, version
history), and limited by Frappe's permissions like any person's:

- no password and no welcome mail, so nobody can sign in as it;
- the Excom Agent role, so Excom's own rules (threads, visibility) apply to it as to a person;
- one pack role (Excom AI Sales, …) for what its type needs, nothing more;
- a Member of the agent's team, and of no other, so it sees what that desk sees and nothing more.

The address is on the reserved `.invalid` domain, so no mail can ever be sent to it.
"""

from __future__ import annotations

import re

import frappe

from excom.excom_ai.constants import PACK_ROLES

DOMAIN = "ai-agents.invalid"


def address_for(agent) -> str:
	slug = re.sub(r"[^a-z0-9]+", ".", (agent.agent_name or "agent").lower()).strip(".") or "agent"
	return f"{slug}.{agent.name.lower()}@{DOMAIN}"


def ensure(agent) -> str:
	"""Create or update the agent's user; returns its name."""
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack") or "custom"
	email = agent.service_user or address_for(agent)
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.new_doc("User")
		user.email = email
		user.send_welcome_email = 0
		user.user_type = "System User"
		user.flags.no_welcome_mail = True
	user.first_name = agent.agent_name
	user.last_name = "(AI)"
	if agent.status == "Retired":
		user.enabled = 0
	elif user.is_new():
		user.enabled = 1
	# An existing user keeps its enabled flag: an admin who switched it off by hand stays obeyed.
	wanted = {"Excom Agent", PACK_ROLES.get(pack, PACK_ROLES["custom"])}
	stale = [r for r in user.roles if r.role in set(PACK_ROLES.values()) - wanted]
	for r in stale:
		user.remove(r)
	have = {r.role for r in user.roles}
	for role in wanted - have:
		user.append("roles", {"role": role})
	user.flags.ignore_permissions = True
	user.flags.ignore_password_policy = True
	user.save(ignore_permissions=True) if not user.is_new() else user.insert(ignore_permissions=True)
	_join_team(user.name, agent.team)
	return user.name


def disable(agent) -> None:
	if agent.service_user and frappe.db.exists("User", agent.service_user):
		frappe.db.set_value("User", agent.service_user, "enabled", 0)


def is_service_user(user: str | None) -> bool:
	return bool(user and user.endswith("@" + DOMAIN))


def claim_for(user: str, thread: str | None = None) -> str:
	"""Whom a send by `user` claims a chat and lead for: the user, or for an agent's own user the
	person in charge of that agent (HLD-007 §11.6). The thread then names the agent."""
	if not is_service_user(user):
		return user
	agent = frappe.db.get_value("Excom AI Agent", {"service_user": user}, ["name", "acting_owner", "owner_user"], as_dict=True)
	if not agent:
		return user
	if thread:
		frappe.db.set_value("Excom Thread", thread, "ai_agent", agent.name, update_modified=False)
	return agent.acting_owner or agent.owner_user


def humans(users):
	"""The people among these users: agents' own users never ring, get notified or fill a report."""
	return [u for u in users if not is_service_user(u)]


def _join_team(user: str, team: str | None) -> None:
	# Leave every other team first: an agent moved from Export to Domestic stops seeing Export.
	for other in frappe.get_all(
		"Excom Team Member", {"parenttype": "Excom Team", "user": user, "parent": ["!=", team or ""]},
		pluck="parent",
	):
		doc = frappe.get_doc("Excom Team", other)
		doc.members = [m for m in doc.members if m.user != user]
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
	if not team or not frappe.db.exists("Excom Team", team):
		return
	if frappe.db.exists("Excom Team Member", {"parent": team, "parenttype": "Excom Team", "user": user}):
		return
	doc = frappe.get_doc("Excom Team", team)
	doc.append("members", {"user": user, "role": "Member"})
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
