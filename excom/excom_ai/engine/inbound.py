"""A buyer wrote: what the agents do at once (HLD-007 §11, §12.6). Phase 0.4.

`on_inbound` is an `excom_inbound_handlers` hook, so it runs for every new message on every
channel, inside ingestion. It must be quick and never fail ingestion, so it only decides and queues:

1. Master switch off, or the conversation belongs to no agent: nothing (one setting read).
2. Which agent: the thread's `ai_agent`, or the agent whose open enrolment is on this thread
   (the thread then remembers it).
3. A reply stops that agent's sequence for the lead: the enrolment becomes Replied.
4. "Are you a bot / an AI / a real person?" (and its translations) is a fixed rule, checked before
   any model sees the message. The conversation moves to Needs person and whoever is in charge is
   alerted with the question; what to answer is a person's call. The agent itself never answers it.
5. Otherwise an `inbound` task is queued (priority 0, runs on hold too, one open task per thread,
   which reads every message that came in since).

What the agent then does with the message (classify, draft, answer) is the pack's responder,
registered with @registry.responder; Sales gets its own in Phase 1F. Until then the task records
the message and leaves it to people.
"""

from __future__ import annotations

import re

import frappe
from frappe import _

from excom.excom_ai.engine import notify, registry, tasks

WORKING = ("Shadow", "Live", "On Hold")

# "Am I talking to a bot?" in the languages the desks sell in. Matched on the lowered text with
# accents kept; a match is enough to hand the conversation to a person.
IDENTITY_QUESTION = re.compile(
	r"(\b(are|r)\s*(you|u)\s+(a\s+|an\s+)?(bot|robot|ai|a\.i\.|machine|chat\s*bot|computer|real|human|person|automated)\b"
	r"|\b(is\s+this|am\s+i\s+(talking|speaking|chatting)\s+(to|with))\s+(a\s+|an\s+)?(bot|robot|ai|machine|real\s+person|human|automated)"
	r"|\b(real|actual|live)\s+(person|human|agent)\b\s*\?"
	r"|\bchat\s*gpt\b|\bartificial intelligence\b"
	r"|eres\s+(un\s+)?(bot|robot|humano|una\s+persona|real|ia)\b|es\s+un\s+bot|hablo\s+con\s+(un\s+)?(bot|robot|humano|una\s+persona)"
	r"|voc[eê]\s+[eé]\s+(um\s+)?(rob[oô]|bot|humano|uma\s+pessoa)|estou\s+falando\s+com\s+(um\s+)?(rob[oô]|bot)"
	r"|es[- ]tu\s+(un\s+)?(bot|robot|humain)|êtes[- ]vous\s+(un\s+)?(bot|robot|humain)"
	r"|bist\s+du\s+(ein\s+)?(bot|roboter|mensch)|sind\s+sie\s+(ein\s+)?(bot|roboter|mensch)"
	r"|क्या\s+(आप|तुम)\s+(बॉट|रोबोट|इंसान)|(bot|robot)\s+ho\s*\?|insaan\s+ho)",
	re.IGNORECASE,
)


def asks_if_ai(text: str | None) -> bool:
	return bool(text and IDENTITY_QUESTION.search(text))


# Events that are not a buyer writing: nothing to stop or answer.
IGNORED_KINDS = ("reaction", "welcome", "call_permission")


def on_inbound(event: dict) -> None:
	if event.get("kind") in IGNORED_KINDS:
		return
	if not frappe.db.get_single_value("Excom AI Settings", "enabled"):
		return
	thread = event.get("thread")
	if not thread:
		return
	row = frappe.db.get_value("Excom Thread", thread, ["ai_agent", "ai_mode", "omni_identity"], as_dict=True)
	if not row:
		return
	agent_name = row.ai_agent or _agent_from_enrollment(thread)
	if not agent_name:
		return
	agent = frappe.db.get_value("Excom AI Agent", agent_name,
		["name", "agent_name", "status", "owner_user", "backup_user", "acting_owner", "escalation_user"], as_dict=True)
	if not agent or agent.status not in WORKING:
		return
	if not row.ai_agent:
		frappe.db.set_value("Excom Thread", thread, "ai_agent", agent.name, update_modified=False)

	automatic = bool((event.get("payload") or {}).get("auto")) if isinstance(event.get("payload"), dict) else False
	if not automatic:
		# An out-of-office or a delivery report is not a reply: the sequence goes on (bounces: 1B).
		_stop_sequences(agent.name, thread, event)

	if asks_if_ai(event.get("text")) or asks_if_ai(event.get("subject")):
		hand_to_person(thread, agent, event.get("text") or event.get("subject"), event.get("message"))
		return
	if row.ai_mode in ("Needs person", "Hold"):
		return  # a person has it; the agent stays out until they hand it back
	tasks.add(agent.name, "inbound", thread=thread, dedupe_key=f"inbound:{thread}",
		params={"message": event.get("message"), "channel": event.get("channel"),
			"sender_verified": bool(event.get("sender_verified")), "automatic": automatic})


def _agent_from_enrollment(thread: str) -> str | None:
	return frappe.db.get_value("Excom AI Enrollment", {"thread": thread, "status": ["in", ("Active", "Paused")]}, "agent")


def _stop_sequences(agent: str, thread: str, event: dict) -> None:
	"""Someone answered: no more sequence steps to them from this agent."""
	for name in frappe.get_all("Excom AI Enrollment", {"agent": agent, "thread": thread, "status": ["in", ("Active", "Paused")]},
			pluck="name"):
		doc = frappe.get_doc("Excom AI Enrollment", name)
		doc.status = "Replied"
		doc.stop_reason = _("Replied on {0}").format(event.get("channel") or "a channel")
		doc.save(ignore_permissions=True)
		for p in frappe.get_all("Excom AI Proposal", {"enrollment": name, "status": ["in", ("Pending", "Approved", "Edited", "Shadow")]},
				pluck="name"):
			frappe.db.set_value("Excom AI Proposal", p, {"status": "Cancelled", "step_key": None,
				"hold_reason": _("The buyer replied")}, update_modified=False)


def hand_to_person(thread: str, agent, question: str | None, message: str | None = None) -> None:
	"""The fixed escalation: the conversation waits for a person, who is told why."""
	frappe.db.set_value("Excom Thread", thread, {"ai_mode": "Needs person", "ai_intent": "asks_if_ai"}, update_modified=False)
	frappe.get_doc("Excom Thread", thread).add_comment(
		"Comment", _("{0} (AI) handed this conversation to a person: the buyer asked whether they are talking to a person or an AI.").format(
			agent.agent_name))
	notify.alert(
		[agent.acting_owner or agent.owner_user, agent.backup_user, agent.escalation_user],
		_("A buyer asked {0} (AI) if it is a person: please answer").format(agent.agent_name),
		message=frappe.utils.escape_html((question or "")[:500]),
		doctype="Excom Thread", name=thread,
	)


@registry.task_kind("inbound", priority=registry.PRIORITY["inbound"], on_hold=True, effort="classify")
def handle_inbound(task, agent) -> str:
	"""Run the pack's responder on what came in. Without one, the message stays with people."""
	pack = frappe.db.get_value("Excom AI Agent Type", agent.agent_type, "pack")
	responder = registry.RESPONDERS.get(pack)
	if not responder:
		return _("Received on {0}; no responder for {1} agents yet, left for people").format(
			(frappe.parse_json(task.params or "{}") or {}).get("channel") or "?", pack)
	return responder(task, agent) or ""
