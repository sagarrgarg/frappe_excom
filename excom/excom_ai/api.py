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
def research_people(name: str, status: str = "Ready", limit: int = 200, linkedin_only: int = 0):
	"""The people this agent's research found (research records are not readable to owners directly).
	`linkedin_only`: people Apollo or Prospeo found with a LinkedIn profile but no address (HLD-009),
	for someone to write to on LinkedIn by hand."""
	agent = _agent_for(name, need_owner=False)
	companies = frappe.get_all("Excom AI Company", {"agent": agent.name}, pluck="name")
	if not companies:
		return []
	filters = {"company": ["in", companies]}
	if frappe.utils.cint(linkedin_only):
		# never someone research ruled out: a customer, a do-not-contact domain, a colleague's lead
		open_companies = frappe.get_all("Excom AI Company", {"name": ["in", companies], "status": ["!=", "Excluded"],
			"company_stop": 0}, pluck="name")
		if not open_companies:
			return []
		filters.update({"company": ["in", open_companies], "email": ["is", "not set"], "linkedin_url": ["is", "set"],
			"status": ["not in", ("Excluded", "Invalid")]})
	elif status:
		filters["status"] = status
	rows = frappe.get_all("Excom AI Person", filters, ["name", "email", "full_name", "title", "email_status", "status", "company",
		"consent_verdict", "is_inbox", "lead", "linkedin_url", "email_source", "verified_by"], order_by="modified desc",
		limit=int(limit or 200))
	names = dict(frappe.get_all("Excom AI Company", {"name": ["in", list({r.company for r in rows}) or ["-"]]},
		["name", "company_name"], as_list=True))
	for r in rows:
		r.company_name = names.get(r.company)
	return rows


@frappe.whitelist()
def live(name: str):
	"""The live view (HLD-007 §13.1)."""
	from excom.excom_ai import monitor

	agent = _agent_for(name, need_owner=False)
	return monitor.live(agent.name)


@frappe.whitelist(methods=["POST"])
def handover(name: str, new_owner: str, new_backup: str | None = None):
	"""The owner is leaving: an Excom Admin or the team's manager hands the agent over."""
	from excom.excom_ai import monitor

	agent = frappe.get_doc("Excom AI Agent", name)
	frappe.has_permission("Excom AI Agent", "read", agent, throw=True)
	manager = bool(set(frappe.get_roles()) & MANAGERS) or frappe.db.exists("Excom Team Member", {"parenttype": "Excom Team",
		"parent": agent.team, "user": frappe.session.user, "role": "Manager"})
	if not manager:
		frappe.throw(_("Only an Excom Admin or the team's manager hands an agent over."), frappe.PermissionError)
	out = monitor.handover(name, new_owner, new_backup, by=frappe.session.user)
	frappe.db.commit()
	return out


@frappe.whitelist(methods=["POST"])
def apply_promotion(name: str, action: str):
	"""Move one action up a mode, as suggested (never past its ceiling: the record refuses that)."""
	from excom.excom_ai import monitor

	agent = _agent_for(name)
	suggestion = next((s for s in monitor.promotion_suggestions(agent) if s["action"] == action), None)
	if not suggestion:
		frappe.throw(_("{0} is not ready to move up.").format(action))
	row = next(p for p in agent.policies if p.action == action)
	row.mode = suggestion["to"]
	agent.save(ignore_permissions=True)
	frappe.db.commit()
	return suggestion


@frappe.whitelist(methods=["POST"])
def request_draft(name: str, instruction: str, subject_doctype: str | None = None, subject_name: str | None = None):
	"""Custom agents: ask for a document or message draft (it arrives as a note for review)."""
	from excom.excom_ai.builder.api import _team_manager
	from excom.excom_ai.packs import custom

	agent = frappe.get_doc("Excom AI Agent", name)
	frappe.has_permission("Excom AI Agent", "read", agent, throw=True)
	# whoever may change the agent (as its page shows the form): the draft only lands for review
	if not (proposals.may_decide(agent, frappe.session.user) or (agent.team and _team_manager(agent.team))):
		frappe.throw(_("Only this agent's owner, backup, team manager or an Excom Admin can ask it."), frappe.PermissionError)
	if "draft" not in custom.abilities(agent):
		frappe.throw(_("This agent does not draft."))
	if subject_name:
		# the person asking must be able to read that record, not just that kind of record
		frappe.has_permission(subject_doctype, "read", subject_name, throw=True)
	task = custom.request_draft(agent.name, instruction, subject_doctype, subject_name)
	frappe.db.commit()
	return {"task": task}


@frappe.whitelist(methods=["POST"])
def cost_check(candidates):
	"""Excom Admin: score cheaper model/effort pairs on the evaluation set (each costs one call per
	case). Nothing is switched; the recommendation is applied through the usual settings change."""
	from excom.excom_ai import learning

	frappe.only_for(list(MANAGERS))  # it spends money on model calls
	return learning.cost_check(frappe.parse_json(candidates) if isinstance(candidates, str) else candidates)


def _may_change_need(doc) -> None:
	"""A need one agent uses: its owner, backup or an Excom Admin. A need several agents share (one
	WhatsApp business account, several owners): an Excom Admin only, since all of them depend on it."""
	from excom.excom_ai.permissions import template_need_agents

	frappe.has_permission("Excom AI Template Need", "read", doc, throw=True)
	agents = [frappe.get_doc("Excom AI Agent", a) for a in template_need_agents(doc) if frappe.db.exists("Excom AI Agent", a)]
	owners = {a.owner_user for a in agents}
	if set(frappe.get_roles()) & MANAGERS:
		return
	if len(owners) > 1:
		frappe.throw(_("Several agents' owners use this template: an Excom Admin decides."), frappe.PermissionError)
	if not agents or not proposals.may_decide(agents[0], frappe.session.user):
		frappe.throw(_("Only the agent's owner, backup or an Excom Admin can do that."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def withdraw_template(need: str):
	"""Take back a template the steward made: deleted on Meta, and agents stop using it."""
	from excom.excom_ai import templates

	doc = frappe.get_doc("Excom AI Template Need", need)
	_may_change_need(doc)
	if doc.status in ("Withdrawn",):
		frappe.throw(_("Already withdrawn."))
	templates.withdraw(need)
	frappe.db.commit()
	return {"status": "Withdrawn"}


@frappe.whitelist(methods=["POST"])
def reopen_template_need(need: str):
	"""A failed need tried again (after a person fixed what stopped it)."""
	doc = frappe.get_doc("Excom AI Template Need", need)
	_may_change_need(doc)
	if doc.status not in ("Failed", "Withdrawn"):
		frappe.throw(_("Only a failed or withdrawn need is opened again."))
	frappe.db.set_value("Excom AI Template Need", need, {"status": "Open", "attempts": 0, "notified": 0})
	frappe.db.commit()
	return {"status": "Open"}


@frappe.whitelist()
def template_needs(agent: str | None = None, status: str | None = None) -> list:
	"""The Template Steward's needs a person may see (their agents' and their team's), newest first,
	with the template's words, Meta's status and category."""
	frappe.has_permission("Excom AI Template Need", "read", throw=True)
	filters = {"status": status} if status else {}
	# a need shared by several agents names the first one in `agent` and all of them in `agents`
	or_filters = {"agent": agent, "agents": ["like", f"%{agent}%"]} if agent else None
	rows = frappe.get_list("Excom AI Template Need", filters=filters, or_filters=or_filters, fields=["name", "need_key", "channel", "category",
		"language", "status", "agent", "agents", "channel_account", "source", "template", "subject", "body",
		"back_translation", "last_error", "attempts", "submitted_on", "met_on", "proposal", "modified"],
		order_by="modified desc", limit_page_length=200)
	for r in rows:
		r.meta_status = r.meta_category = None
		if r.template:
			t = frappe.db.get_value("WhatsApp Templates", r.template, ["status", "category", "quality_score",
				"rejection_reason"], as_dict=True) or {}
			r.meta_status, r.meta_category = t.get("status"), t.get("category")
			r.quality, r.rejection_reason = t.get("quality_score"), t.get("rejection_reason")
		try:
			_may_change_need(frappe.get_doc("Excom AI Template Need", r.name))
			r.can_change = True
		except frappe.PermissionError:
			frappe.clear_last_message()
			r.can_change = False
	return rows
