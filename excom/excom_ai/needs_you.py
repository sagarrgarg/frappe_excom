"""Needs you (HLD-007 §15.4), the Shadow comparison, and taking over a conversation (§15.5).

Needs you is one queue across every agent the person may decide for (the agent's owner, backup,
whoever is in charge, its listed reviewers; Excom Admins see all), grouped by urgency:

1. buyers waiting for a person (a conversation the agent handed over), with how long they waited;
2. proposals to approve, each with the preview exactly as the buyer will see it, the code checks
   and the AI checker's verdict; approve one, reject with a reason, or approve a whole agent's
   step at once (only proposals whose checks all passed);
3. held items and readiness blockers.

The Shadow comparison puts what the agent would have sent next to what a person actually sent on
the same conversation, so the owner can judge before letting it write for real.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from excom.excom_ai.engine import proposals
from excom.excom_ai.permissions import is_manager, visible_agents

REPLY_TARGET_HOURS = 4


def _deciding_for(user: str | None = None) -> list[str]:
	"""Agents this person decides for."""
	user = user or frappe.session.user
	if is_manager(user):
		return frappe.get_all("Excom AI Agent", {"status": ["!=", "Retired"]}, pluck="name")
	out = []
	for name in visible_agents(user) or []:
		agent = frappe.get_doc("Excom AI Agent", name)
		if proposals.may_decide(agent, user):
			out.append(name)
	return out


def _excom_user():
	if not set(frappe.get_roles()) & {"Excom Agent", "Excom Admin", "System Manager"}:
		frappe.throw(_("Needs you is for Excom users."), frappe.PermissionError)


@frappe.whitelist()
def queue() -> dict:
	_excom_user()
	agents = _deciding_for()
	if not agents:
		return {"waiting": [], "proposals": [], "held": [], "blockers": [], "agents": {}}
	names = dict(frappe.get_all("Excom AI Agent", {"name": ["in", agents]}, ["name", "agent_name"], as_list=True))
	waiting = frappe.get_all("Excom Thread", {"ai_agent": ["in", agents], "ai_mode": "Needs person", "status": ["!=", "Closed"]},
		["name", "display_name", "channel", "ai_agent", "ai_intent", "last_inbound_at", "last_message_preview", "assigned_to",
			"omni_identity"],
		order_by="last_inbound_at asc", limit=100)
	from excom.excom_ai.monitor import working_hours_waited

	for w in waiting:
		# weekday hours, as the live view and the alerts count them
		w.waited_hours = round(working_hours_waited(w.last_inbound_at), 1) if w.last_inbound_at else None
		w.late = bool(w.waited_hours and w.waited_hours > REPLY_TARGET_HOURS)
	fields = ["name", "agent", "action", "policy_action", "status", "risk", "step", "kind", "to_email", "thread", "subject_doctype", "subject_name",
		"preview_html", "payload", "checks", "checker_verdict", "checker_notes", "creation", "hold_reason", "enrollment", "send_at"]
	pending = frappe.get_all("Excom AI Proposal", {"agent": ["in", agents], "status": "Pending"}, fields,
		order_by="risk desc, creation asc", limit=300)
	held = frappe.get_all("Excom AI Proposal", {"agent": ["in", agents], "status": "Held"}, fields, order_by="creation asc", limit=100)
	for p in pending + held:
		p.payload = json.loads(p.payload or "{}") if isinstance(p.payload, str) else p.payload
		p.checks = json.loads(p.checks or "{}") if isinstance(p.checks, str) else p.checks
		p.checks_ok = bool((p.checks or {}).get("ok", True)) and p.checker_verdict != "Fail"
	blockers = frappe.get_all("Excom AI Readiness Gap", {"agent": ["in", agents], "status": "Open", "severity": "Blocker"},
		["name", "agent", "rule", "detail"], limit=100)
	for b in blockers:
		b.title = frappe.db.get_value("Excom AI Readiness Rule", b.rule, "title")
	return {"waiting": waiting, "proposals": pending, "held": held, "blockers": blockers, "agents": names,
		"reply_target_hours": REPLY_TARGET_HOURS}


@frappe.whitelist(methods=["POST"])
def decide_many(names, decision: str, reason: str = "", note: str = "") -> dict:
	"""Approve or reject several proposals; each is checked on its own (who may decide, its state)."""
	names = frappe.parse_json(names) if isinstance(names, str) else names
	done, refused = [], []
	for name in names or []:
		doc = frappe.get_doc("Excom AI Proposal", name)
		frappe.has_permission("Excom AI Proposal", "read", doc, throw=True)
		if doc.action == "approve_template":
			refused.append({"name": name, "why": _("A template is approved on its own, after reading it.")})
			continue
		try:
			proposals.decide(name, decision, reason=reason or None, note=note or None)
			done.append(name)
		except frappe.PermissionError:
			raise
		except frappe.ValidationError as e:
			frappe.clear_last_message()
			refused.append({"name": name, "why": str(e)})
	frappe.db.commit()
	return {"done": done, "refused": refused}


@frappe.whitelist(methods=["POST"])
def approve_step(agent: str, step: int | None = None, action: str = "send_email") -> dict:
	"""Bulk approval for one agent (and one sequence step): only proposals whose checks all passed
	and the AI checker did not fail; the rest stay for one-by-one reading."""
	doc = frappe.get_doc("Excom AI Agent", agent)
	frappe.has_permission("Excom AI Agent", "read", doc, throw=True)
	if not proposals.may_decide(doc, frappe.session.user):
		frappe.throw(_("Only this agent's owner, backup, reviewers or an Excom Admin approve its proposals."), frappe.PermissionError)
	filters = {"agent": agent, "status": "Pending", "action": action}
	if step is not None and str(step) != "":
		filters["step"] = cint(step)
	names, skipped = [], 0
	for p in frappe.get_all("Excom AI Proposal", filters, ["name", "checks", "checker_verdict"]):
		ok = bool((json.loads(p.checks or "{}") or {}).get("ok", True)) and p.checker_verdict != "Fail"
		if ok:
			names.append(p.name)
		else:
			skipped += 1
	out = decide_many(names, "approve")
	out["skipped_failing_checks"] = skipped
	return out


# ── taking over a conversation ───────────────────────────────────────────────


def _thread_for_change(thread: str):
	from excom.excom.doctype.excom_thread.excom_thread import can_access

	doc = frappe.get_doc("Excom Thread", thread)
	if not can_access(doc, frappe.session.user):
		frappe.throw(_("You cannot open this conversation."), frappe.PermissionError)
	return doc


@frappe.whitelist(methods=["POST"])
def take_over(thread: str) -> dict:
	"""A person takes the conversation: the agent stays out (Hold) and it is theirs."""
	doc = _thread_for_change(thread)
	frappe.db.set_value("Excom Thread", thread, {"ai_mode": "Hold", "assigned_to": frappe.session.user}, update_modified=False)
	for p in frappe.get_all("Excom AI Proposal", {"thread": thread, "status": ["in", ("Pending", "Held")]}, pluck="name"):
		frappe.db.set_value("Excom AI Proposal", p, {"status": "Cancelled", "step_key": None,
			"hold_reason": _("{0} took over the conversation").format(frappe.session.user)}, update_modified=False)
	doc.add_comment("Comment", _("{0} took over this conversation from the AI agent.").format(frappe.session.user))
	frappe.db.commit()
	return {"ai_mode": "Hold"}


@frappe.whitelist(methods=["POST"])
def hand_back(thread: str) -> dict:
	"""The person hands the conversation back to its agent."""
	doc = _thread_for_change(thread)
	if not doc.ai_agent:
		frappe.throw(_("No AI agent handles this conversation."))
	frappe.db.set_value("Excom Thread", thread, "ai_mode", "Handle", update_modified=False)
	doc.add_comment("Comment", _("{0} handed this conversation back to the AI agent.").format(frappe.session.user))
	frappe.db.commit()
	return {"ai_mode": "Handle"}


@frappe.whitelist()
def thread_proposals(thread: str) -> list:
	"""An agent's open proposals on one conversation, for the inbox (inline at its foot)."""
	_thread_for_change(thread)
	rows = frappe.get_all("Excom AI Proposal", {"thread": thread, "status": ["in", ("Pending", "Held", "Shadow")]},
		["name", "agent", "action", "policy_action", "status", "preview_html", "payload", "checks", "checker_verdict", "checker_notes", "to_email",
			"creation"], order_by="creation desc", limit=10)
	out = []
	for r in rows:
		if frappe.has_permission("Excom AI Proposal", "read", frappe.get_doc("Excom AI Proposal", r.name)):
			r.payload = json.loads(r.payload or "{}") if isinstance(r.payload, str) else r.payload
			r.checks = json.loads(r.checks or "{}") if isinstance(r.checks, str) else r.checks
			out.append(r)
	return out


# ── the Shadow comparison ────────────────────────────────────────────────────


@frappe.whitelist()
def shadow_comparison(agent: str, limit: int = 50) -> list:
	"""What the agent would have sent, next to what a person sent on the same conversation after it."""
	doc = frappe.get_doc("Excom AI Agent", agent)
	frappe.has_permission("Excom AI Agent", "read", doc, throw=True)
	rows = frappe.get_all("Excom AI Proposal", {"agent": agent, "status": "Shadow", "action": ["in", ("send_email", "reply",
		"send_whatsapp")]}, ["name", "action", "thread", "to_email", "payload", "preview_html", "creation", "shadow_verdict",
		"shadow_note", "checker_verdict"], order_by="creation desc", limit=cint(limit) or 50)
	for r in rows:
		r.payload = json.loads(r.payload or "{}") if isinstance(r.payload, str) else r.payload
		r.omni_identity = frappe.db.get_value("Excom Thread", r.thread, "omni_identity") if r.thread else None
		human = None
		if r.thread:
			human = frappe.db.get_value("Excom Message", {"thread": r.thread, "direction": "Outbound",
				"creation": [">=", r.creation], "created_by_user": ["not like", "%@ai-agents.invalid"]},
				["name", "content_text", "created_by_user", "creation"], as_dict=True, order_by="creation asc")
		r.person_sent = human
	return rows


@frappe.whitelist(methods=["POST"])
def rate_shadow(proposal: str, verdict: str, note: str = "") -> dict:
	"""The owner's judgement of a Shadow draft against what a person sent."""
	doc = frappe.get_doc("Excom AI Proposal", proposal)
	agent = frappe.get_doc("Excom AI Agent", doc.agent)
	if not proposals.may_decide(agent, frappe.session.user):
		frappe.throw(_("Only this agent's owner, backup, reviewers or an Excom Admin judge its drafts."), frappe.PermissionError)
	if verdict not in ("Agent better", "About the same", "Person better"):
		frappe.throw(_("Choose: agent better, about the same, or person better."))
	doc.db_set({"shadow_verdict": verdict, "shadow_note": note or None}, update_modified=False)
	from excom.excom_ai.engine.ownership import seen

	seen(agent.name, frappe.session.user)
	frappe.db.commit()
	return {"ok": True}


def shadow_score(agent: str) -> dict:
	"""How the agent's drafts compare so far: the evidence for letting it go Live."""
	rows = frappe.get_all("Excom AI Proposal", {"agent": agent, "shadow_verdict": ["is", "set"]}, pluck="shadow_verdict")
	return {v: rows.count(v) for v in ("Agent better", "About the same", "Person better")} | {"rated": len(rows)}


@frappe.whitelist()
def count() -> dict:
	"""For a badge: what waits for this person, cheaply."""
	_excom_user()
	agents = _deciding_for()
	if not agents:
		return {"total": 0, "waiting": 0, "proposals": 0}
	waiting = frappe.db.count("Excom Thread", {"ai_agent": ["in", agents], "ai_mode": "Needs person", "status": ["!=", "Closed"]})
	pending = frappe.db.count("Excom AI Proposal", {"agent": ["in", agents], "status": "Pending"})
	return {"total": waiting + pending, "waiting": waiting, "proposals": pending}


@frappe.whitelist()
def shadow_scores(agent: str) -> dict:
	doc = frappe.get_doc("Excom AI Agent", agent)
	frappe.has_permission("Excom AI Agent", "read", doc, throw=True)
	return shadow_score(agent)
