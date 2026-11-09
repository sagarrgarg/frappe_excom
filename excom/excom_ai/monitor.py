"""Monitoring, alerts, digest, coaching, promotion and handover (HLD-007 §13, §12.3), Phase 1H.

- **Live view** per agent: today's work, channel results, spend against budget, health and the
  mode of each action.
- **Alerts**, to the owner and backup (once a day per kind): an interested buyer waiting past the
  4-working-hour target, the budget at 80%, the readiness check failing again. (Holds, demotions,
  bounce spikes and unhealthy mailboxes alert where they happen.)
- **Morning digest** at the settings' digest time: yesterday's numbers and today's to-do.
- **Weekly coach**: what worked by market and step, and the reasons people gave when they rejected
  or edited drafts, turned by the model into playbook suggestions the owner accepts or rejects.
- **Promotion suggestions**: an action whose last 20 decided drafts were all approved unchanged,
  and whose sampled Auto actions were all fine, can move up one mode (never past its ceiling).
- **Handover**: an owner leaving hands the agent, and its open conversations and leads, to someone.
- **Evaluation gate**: changing an agent's model, or the effort settings, runs the Sales eval set
  first; a change that scores below the set's threshold is refused.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, get_first_day, get_time, now_datetime, nowdate

from excom.excom_ai.constants import MODES, ceiling
from excom.excom_ai.engine import notify, proposals, registry, runner

REPLY_TARGET_HOURS = 4
BUDGET_ALERT_SHARE = 0.8
PROMOTE_AFTER = 20


def _spend(agent_name: str, since) -> float:
	if not frappe.db.table_exists("AI Call Log"):
		return 0.0
	return flt(frappe.db.sql("SELECT SUM(base_cost) FROM `tabAI Call Log` WHERE budget_scope = %s AND creation >= %s",
		(f"Excom AI Agent:{agent_name}", since))[0][0])


def working_hours_waited(since, until=None) -> float:
	"""Hours between two times, Monday to Friday only (a buyer waiting over the weekend is not late)."""
	since, until = get_datetime(since), get_datetime(until or now_datetime())
	hours, t = 0.0, since
	while t < until:
		step = min(until, t + frappe.utils.datetime.timedelta(hours=1))
		if t.weekday() < 5:
			hours += (step - t).total_seconds() / 3600
		t = step
	return hours


# ── the live view ────────────────────────────────────────────────────────────


def live(agent_name: str) -> dict:
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	today = get_datetime(nowdate())
	tasks_today = dict(frappe.db.sql("""SELECT status, COUNT(*) FROM `tabExcom AI Task` WHERE agent = %s AND modified >= %s
		GROUP BY status""", (agent.name, today)))
	decided = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "decided_on": [">=", today], "decided_by": ["!=", "Autopilot"]},
		["status", "reject_reason"])
	sent = frappe.db.count("Excom AI Proposal", {"agent": agent.name, "status": "Sent", "sent_on": [">=", today]})
	accounts = [r.channel_account for r in agent.channels or []]
	bounces = frappe.db.count("Excom Mail Bounce", {"account": ["in", accounts or ["-"]], "received_on": [">=", today]}) \
		if frappe.db.table_exists("Excom Mail Bounce") else 0
	replies = dict(frappe.db.sql("""SELECT IFNULL(ai_intent, 'unread'), COUNT(*) FROM `tabExcom Thread` WHERE ai_agent = %s
		AND last_inbound_at >= %s GROUP BY ai_intent""", (agent.name, today)))
	waiting = []
	for t in frappe.get_all("Excom Thread", {"ai_agent": agent.name, "ai_mode": "Needs person", "status": ["!=", "Closed"]},
			["name", "display_name", "ai_intent", "last_inbound_at"]):
		hours = working_hours_waited(t.last_inbound_at) if t.last_inbound_at else 0
		waiting.append({**t, "hours": round(hours, 1), "late": hours > REPLY_TARGET_HOURS})
	month = get_datetime(get_first_day(nowdate()))
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	return {
		"today": {"tasks": tasks_today, "waiting_for_you": frappe.db.count("Excom AI Proposal", {"agent": agent.name, "status": "Pending"}),
			"approved": sum(1 for d in decided if d.status in ("Approved", "Edited", "Sent", "Done")),
			"rejected": [d.reject_reason for d in decided if d.status == "Rejected"]},
		"channels": {"sent": sent, "bounces": bounces, "replies_by_intent": replies, "waiting": waiting},
		"spend": {"today": _spend(agent.name, today), "month": _spend(agent.name, month), "daily_budget": flt(agent.daily_ai_budget),
			"monthly_budget": flt(agent.monthly_ai_budget),
			"tools_month": flt(frappe.db.sql("SELECT SUM(used_this_month) FROM `tabExcom AI Tool` WHERE enabled = 1")[0][0])},
		"health": {"status": agent.status, "readiness": agent.readiness_state, "failures_in_a_row": cint(agent.consecutive_failures),
			"channels": [{"account": r.channel_account, "paused": r.paused, "pause_reason": r.pause_reason,
				"channel": frappe.db.get_value("Excom Channel Account", r.channel_account, "channel"),
				"quality_rating": frappe.db.get_value("Excom Channel Account", r.channel_account, "wa_quality_rating")
					if frappe.get_meta("Excom Channel Account").has_field("wa_quality_rating") else None,
				"health": json.loads(r.health) if r.get("health") else None} for r in agent.channels or []],
			"modes": {p.action: {"mode": p.mode, "ceiling": ceiling(p.action, pack, p.condition)} for p in agent.policies or []}},
		"promotions": promotion_suggestions(agent),
	}


# ── alerts ───────────────────────────────────────────────────────────────────


def _once_a_day(agent: str, kind: str, key: str = "") -> bool:
	"""True the first time today for this agent, kind and key (so alerts are not repeated each tick)."""
	cache_key = f"excom_ai_alert:{agent}:{kind}:{key}:{nowdate()}"
	if frappe.cache.get_value(cache_key):
		return False
	frappe.cache.set_value(cache_key, 1, expires_in_sec=26 * 3600)
	return True


def alerts(agent) -> list[str]:
	"""Checked each tick for a working agent; returns the alerts sent."""
	sent = []
	people = [agent.acting_owner or agent.owner_user, agent.backup_user]
	for t in frappe.get_all("Excom Thread", {"ai_agent": agent.name, "ai_mode": "Needs person", "status": ["!=", "Closed"]},
			["name", "display_name", "last_inbound_at", "last_outbound_at"]):
		answered = t.last_outbound_at and t.last_inbound_at and get_datetime(t.last_outbound_at) > get_datetime(t.last_inbound_at)
		if answered or not t.last_inbound_at:
			continue
		if working_hours_waited(t.last_inbound_at) > REPLY_TARGET_HOURS and _once_a_day(agent.name, "late", t.name):
			notify.alert([*people, agent.escalation_user], _("{0} has waited over {1} working hours for an answer ({2} AI)").format(
				t.display_name or t.name, REPLY_TARGET_HOURS, agent.agent_name), doctype="Excom Thread", name=t.name)
			sent.append(f"late:{t.name}")
	for period, since, cap in (("daily", get_datetime(nowdate()), flt(agent.daily_ai_budget)),
			("monthly", get_datetime(get_first_day(nowdate())), flt(agent.monthly_ai_budget))):
		if cap > 0 and _spend(agent.name, since) >= BUDGET_ALERT_SHARE * cap and _once_a_day(agent.name, f"budget-{period}"):
			notify.alert(people, _("{0} (AI) has used {1}% of its {2} AI budget").format(agent.agent_name,
				round(100 * _spend(agent.name, since) / cap), period), doctype="Excom AI Agent", name=agent.name)
			sent.append(f"budget:{period}")
	return sent


def readiness_changed(agent_name: str, before: str | None, after: str) -> None:
	"""The nightly refresh found blockers again on an agent that was ready: tell its people."""
	if before in ("Ready", "Warnings") and after == "Blocked":
		agent = frappe.get_doc("Excom AI Agent", agent_name)
		notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user, *notify.admins()],
			_("{0} (AI): a readiness rule is failing again").format(agent.agent_name), doctype="Excom AI Agent", name=agent.name)


# ── morning digest ───────────────────────────────────────────────────────────


def digest_due() -> bool:
	t = frappe.db.get_single_value("Excom AI Settings", "digest_time")
	if not t:
		return False
	now = now_datetime()
	return get_time(t).hour == now.hour


def digest_for(owner: str) -> str:
	yesterday = get_datetime(add_days(nowdate(), -1))
	today = get_datetime(nowdate())
	rows = []
	for a in frappe.get_all("Excom AI Agent", {"status": ["in", ("Shadow", "Live", "On Hold")],
			"owner_user": owner}, ["name", "agent_name", "status"]):
		sent = frappe.db.count("Excom AI Proposal", {"agent": a.name, "status": "Sent", "sent_on": ["between", [yesterday, today]]})
		replies = frappe.db.count("Excom Thread", {"ai_agent": a.name, "last_inbound_at": ["between", [yesterday, today]]})
		waiting = frappe.db.count("Excom AI Proposal", {"agent": a.name, "status": "Pending"})
		buyers = frappe.db.count("Excom Thread", {"ai_agent": a.name, "ai_mode": "Needs person", "status": ["!=", "Closed"]})
		rows.append(f"<li><b>{frappe.utils.escape_html(a.agent_name)}</b> ({a.status}): "
			+ _("yesterday {0} sent and {1} replies; today {2} drafts to review and {3} buyers waiting for you").format(
				sent, replies, waiting, buyers) + "</li>")
	return f"<ul>{''.join(rows)}</ul>" if rows else ""


def morning_digest():
	"""Hourly; sends at the settings' digest time. Nothing while the master switch is off."""
	if not frappe.db.get_single_value("Excom AI Settings", "enabled") or not digest_due():
		return
	owners = {a.owner_user for a in frappe.get_all("Excom AI Agent", {"status": ["in", ("Shadow", "Live", "On Hold")]}, ["owner_user"])}
	for owner in owners:
		body = digest_for(owner)
		if not body or not _once_a_day(owner, "digest"):
			continue
		notify.alert([owner], _("Your AI agents this morning"), message=body)
		frappe.sendmail(recipients=[owner], subject=_("Your AI agents this morning"), message=body, delayed=True)


# ── weekly coach ─────────────────────────────────────────────────────────────

COACH_SCHEMA = {
	"type": "object",
	"properties": {"suggestions": {"type": "array", "items": {"type": "object", "properties": {
		"kind": {"type": "string", "enum": ["Allowed claims", "Banned claims", "Objections", "FAQ", "Process rules", "Tone examples"]},
		"text": {"type": "string"}, "why": {"type": "string"}}, "required": ["kind", "text", "why"], "additionalProperties": False}}},
	"required": ["suggestions"],
	"additionalProperties": False,
}


def week_stats(agent_name: str) -> dict:
	since = add_days(now_datetime(), -7)
	rejected = frappe.get_all("Excom AI Proposal", {"agent": agent_name, "status": "Rejected", "decided_on": [">=", since]},
		["reject_reason", "reject_note", "action"])
	edited = frappe.get_all("Excom AI Proposal", {"agent": agent_name, "edit_diff": ["is", "set"], "decided_on": [">=", since]},
		["edit_diff"], limit=10)
	by_step = frappe.db.sql("""SELECT p.step, COUNT(*) sent, SUM(t.last_inbound_at > p.sent_on) answered
		FROM `tabExcom AI Proposal` p LEFT JOIN `tabExcom Thread` t ON t.name = p.thread
		WHERE p.agent = %s AND p.status = 'Sent' AND p.sent_on >= %s GROUP BY p.step""", (agent_name, since), as_dict=True)
	return {"rejected": rejected, "edits": [json.loads(e.edit_diff) for e in edited], "by_step": by_step}


@registry.task_kind("coach", priority=registry.PRIORITY["refresh"], effort="reason")
def coach(task, agent) -> str:
	"""What worked this week and what people corrected, as playbook suggestions for the owner."""
	from excom.excom_ai import learning

	tests = learning.propose_winners(task, agent)
	stats = week_stats(agent.name)
	stats["subject_tests"] = learning.subject_results(agent.name)
	if not (stats["rejected"] or stats["edits"] or stats["by_step"]):
		return _("nothing to learn from this week") + (_("; {0} subject tests decided").format(tests) if tests else "")
	r = runner.ai(task, agent, output_schema=COACH_SCHEMA,
		pack_instructions=_("You coach an AI sales colleague. From this week's numbers and the corrections people made, "
			"suggest at most 5 short additions to its playbook. Each is one plain sentence a person can accept or reject. "
			"Only suggest what the evidence supports."),
		prompt=frappe.as_json(stats, indent=None)[:8000])
	made = 0
	for s in (r.data.get("suggestions") or [])[:5]:
		if proposals.propose(task, agent, "update_playbook", policy_action="playbook_change", subject=("Excom AI Agent", agent.name),
				risk="Low", payload={"kind": s["kind"], "text": s["text"], "why": s["why"]},
				preview_html=f"<b>{frappe.utils.escape_html(s['kind'])}</b>: {frappe.utils.escape_html(s['text'])}"):
			made += 1
	return _("{0} playbook suggestions").format(made) + (_("; {0} subject tests decided").format(tests) if tests else "")


@registry.executor("update_playbook")
def update_playbook(proposal, agent):
	"""An accepted suggestion is added to the agent's first playbook (which then goes back to review
	before it reaches prompts, as every playbook edit does)."""
	p = frappe.parse_json(proposal.payload or "{}")
	link = next((r.playbook for r in agent.playbooks or []), None)
	if not link:
		raise registry.Cancel(_("the agent has no playbook"))
	pb = frappe.get_doc("Excom AI Playbook", link)
	pb.append("sections", {"kind": p.get("kind") or "Process rules", "title": _("Coach suggestion"), "content": p.get("text"),
		"source": "Coach", "source_ref": proposal.name})
	pb.change_note = p.get("why")
	pb.flags.ignore_permissions = True
	pb.save(ignore_permissions=True)
	return ("Excom AI Playbook", pb.name)


def weekly_coach():
	"""Mondays: a coach task for every working agent."""
	if not frappe.db.get_single_value("Excom AI Settings", "enabled"):
		return
	from excom.excom_ai.engine import tasks

	for a in frappe.get_all("Excom AI Agent", {"status": ["in", ("Shadow", "Live")]}, pluck="name"):
		tasks.add(a, "coach", dedupe_key=f"coach:{nowdate()}")
	frappe.db.commit()


# ── promotion ────────────────────────────────────────────────────────────────


def promotion_suggestions(agent) -> list[dict]:
	"""Actions whose last 20 decided drafts were approved unchanged (and whose sampled Auto actions
	were all fine) may move up one mode, never past the ceiling."""
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	out = []
	for row in agent.policies or []:
		top = ceiling(row.action, pack, row.condition)
		if MODES.index(row.mode) >= MODES.index(top) or row.mode == "Off":
			continue
		recent = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "policy_action": row.action,
			"decided_by": ["not in", ("", "Autopilot")], "status": ["not in", ("Pending", "Shadow", "Held")]},
			["status", "edit_diff"], order_by="decided_on desc", limit=PROMOTE_AFTER)
		bad_samples = frappe.db.count("Excom AI Proposal", {"agent": agent.name, "policy_action": row.action, "sampled": 1,
			"reject_note": ["is", "set"]})
		if len(recent) >= PROMOTE_AFTER and all(r.status != "Rejected" and not r.edit_diff for r in recent) and not bad_samples:
			out.append({"action": row.action, "from": row.mode, "to": MODES[MODES.index(row.mode) + 1],
				"why": _("the last {0} were approved unchanged").format(PROMOTE_AFTER)})
	return out


# ── handover ─────────────────────────────────────────────────────────────────


def handover(agent_name: str, new_owner: str, new_backup: str | None = None, by: str | None = None) -> dict:
	"""The owner is leaving: the agent, its open conversations and its leads go to someone else."""
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	old = agent.owner_user
	agent.owner_user = new_owner
	if new_backup is not None:
		agent.backup_user = new_backup or None
	agent.save(ignore_permissions=True)  # the record checks the new people
	threads = frappe.get_all("Excom Thread", {"ai_agent": agent.name, "assigned_to": old, "status": ["!=", "Closed"]}, pluck="name")
	for t in threads:
		frappe.db.set_value("Excom Thread", t, "assigned_to", new_owner, update_modified=False)
	leads = frappe.get_all("Lead", {"ai_agent": agent.name, "lead_owner": old}, pluck="name") \
		if frappe.get_meta("Lead").has_field("ai_agent") else []
	for lead in leads:
		frappe.db.set_value("Lead", lead, "lead_owner", new_owner, update_modified=False)
	from excom.excom_ai.engine import ownership

	ownership.evaluate(frappe.get_doc("Excom AI Agent", agent.name))
	agent.add_comment("Comment", _("Handed over from {0} to {1} by {2}: {3} conversations and {4} leads moved.").format(
		old, new_owner, by or frappe.session.user, len(threads), len(leads)))
	return {"threads": len(threads), "leads": len(leads)}


# ── evaluation gate ──────────────────────────────────────────────────────────


def gate_change(model: str | None = None, effort: str | None = None) -> None:
	"""Before a model or effort change goes live: the Sales reply-intents set must still pass with it.
	Refused (ValidationError) when it scores below its threshold. Skipped when no provider is set up."""
	from excom.excom_ai.builder.brief import ai_ready
	from excom.excom_ai.evals import SET

	if not ai_ready() or not frappe.db.exists("AI Eval Set", SET):
		return
	from ai_interface.services import evals

	with _trial(model, effort):
		ok = evals.gate(SET, notes=_("before changing to model {0}, effort {1}").format(model or "-", effort or "-"))
	if not ok:
		score = frappe.db.get_value("AI Eval Set", SET, "last_score")
		frappe.throw(_("The evaluation set scores {0}% with this change, below its threshold: the change is not saved.").format(
			round(flt(score))))


class _trial:
	"""Runs the eval with the candidate model and effort (as the responder would use them)."""

	def __init__(self, model, effort):
		self.model, self.effort = model, effort

	def __enter__(self):
		frappe.flags.excom_ai_trial = {"model": self.model, "effort": self.effort}
		return self

	def __exit__(self, *a):
		frappe.flags.excom_ai_trial = None
		return False


def hourly():
	"""Hourly jobs: the morning digest, and the weekly coach on Monday mornings."""
	morning_digest()
	if now_datetime().weekday() == 0 and digest_due():
		weekly_coach()

