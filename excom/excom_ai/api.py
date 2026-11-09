"""Endpoints for people: stop and resume an agent, acknowledge it, decide and sample proposals.

Every endpoint checks who is asking: the agent's owner, backup, listed reviewers, or an Excom Admin.
"""

from __future__ import annotations

import frappe
from frappe import _

from excom.excom_ai.engine import ownership, proposals, tasks

MANAGERS = {"Excom Admin", "System Manager"}


def _agent_for(name: str, need_owner: bool = True):
	agent = frappe.get_doc("Excom AI Agent", name)
	frappe.has_permission("Excom AI Agent", "read", agent, throw=True)
	user = frappe.session.user
	allowed = {agent.owner_user, agent.backup_user} if need_owner else set(proposals.reviewers(agent))
	if user not in allowed and not set(frappe.get_roles(user)) & MANAGERS:
		frappe.throw(_("Only this agent's owner, backup or an Excom Admin can do that."), frappe.PermissionError)
	return agent


@frappe.whitelist(methods=["POST"])
def stop_agent(name: str, reason: str = ""):
	"""The Stop button: on hold at once; queued work skipped, approved actions held, drafts kept."""
	agent = _agent_for(name)
	ownership.hold(agent, reason or _("Stopped by {0}").format(frappe.session.user), by=frappe.session.user)
	frappe.db.commit()
	return {"status": "On Hold"}


@frappe.whitelist(methods=["POST"])
def resume_agent(name: str):
	agent = _agent_for(name)
	ownership.resume(agent, frappe.session.user)
	frappe.db.commit()
	return {"status": frappe.db.get_value("Excom AI Agent", name, "status")}


@frappe.whitelist(methods=["POST"])
def acknowledge(name: str):
	"""The owner or backup confirms they are watching (the dead-man switch)."""
	agent = _agent_for(name)
	counted = ownership.seen(agent.name, frappe.session.user)
	frappe.db.commit()
	# Only the owner and the backup restart the watch: a manager acknowledging does not.
	return {"ok": counted}


@frappe.whitelist(methods=["POST"])
def decide_proposal(name: str, decision: str, reason: str = "", note: str = "", payload=None):
	doc = frappe.get_doc("Excom AI Proposal", name)
	frappe.has_permission("Excom AI Proposal", "read", doc, throw=True)
	status = proposals.decide(name, decision, reason=reason or None, note=note or None,
		payload=frappe.parse_json(payload) if payload else None)
	frappe.db.commit()
	return {"status": status}


@frappe.whitelist(methods=["POST"])
def review_sample(name: str, ok: int = 1, note: str = ""):
	doc = frappe.get_doc("Excom AI Proposal", name)
	frappe.has_permission("Excom AI Proposal", "read", doc, throw=True)
	proposals.review_sample(name, bool(int(ok)), note or None)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def run_self_test(name: str):
	"""Queue the echo task for an agent: the end-to-end check of the engine."""
	agent = _agent_for(name)
	task = tasks.add(agent.name, "echo", dedupe_key=f"echo:{agent.name}")
	frappe.db.commit()
	return {"task": task}


@frappe.whitelist(methods=["POST"])
def promote_person(name: str, person: str):
	"""Research becomes CRM for one person: a Lead (and the company a Prospect), exclusions checked again."""
	from excom.excom_ai import research

	agent = _agent_for(name)
	if frappe.db.get_value("Excom AI Company", frappe.db.get_value("Excom AI Person", person, "company"), "agent") != agent.name:
		frappe.throw(_("This person was not found by this agent."), frappe.PermissionError)
	out = research.promote(person, agent)
	frappe.db.commit()
	return out


@frappe.whitelist()
def research_summary(name: str):
	from excom.excom_ai import research

	agent = _agent_for(name, need_owner=False)
	return research.summary(agent.name)


@frappe.whitelist(methods=["POST"])
def import_volza(name: str, file_url: str):
	"""Queue a Volza customs-data export (an uploaded file) as a discovery run for this agent."""
	agent = _agent_for(name)
	if not frappe.db.exists("File", {"file_url": file_url}):
		frappe.throw(_("Upload the Volza export first."))
	task = tasks.add(agent.name, "discover", dedupe_key=f"volza:{file_url}", params={"source": "volza", "file_url": file_url})
	frappe.db.commit()
	return {"task": task}


@frappe.whitelist()
def dry_run(name: str, days: int = 21):
	"""When each step of each active enrollment would go, under today's rules, without sending."""
	from excom.excom_ai import sequencer

	agent = _agent_for(name, need_owner=False)
	return sequencer.dry_run(agent.name, days=int(days or 21))


@frappe.whitelist()
def research_people(name: str, status: str = "Ready", limit: int = 200):
	"""The people this agent's research found (research records are not readable to owners directly)."""
	agent = _agent_for(name, need_owner=False)
	companies = frappe.get_all("Excom AI Company", {"agent": agent.name}, pluck="name")
	if not companies:
		return []
	filters = {"company": ["in", companies]}
	if status:
		filters["status"] = status
	rows = frappe.get_all("Excom AI Person", filters, ["name", "email", "full_name", "title", "email_status", "status", "company",
		"consent_verdict", "is_inbox", "lead"], order_by="modified desc", limit=int(limit or 200))
	names = dict(frappe.get_all("Excom AI Company", {"name": ["in", list({r.company for r in rows}) or ["-"]]},
		["name", "company_name"], as_list=True))
	for r in rows:
		r.company_name = names.get(r.company)
	return rows
