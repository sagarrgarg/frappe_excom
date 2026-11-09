"""Voice (HLD-007 §11.4, Phase 3): an agent takes the inbound calls nobody answers.

The audio runs on a small service on the VM (apps/excom/voice_service): the telephony provider
streams the call to it, it turns speech into text and back, and asks these endpoints what to say.
The model is called here, through the engine, with the agent's brief and playbooks.

When it answers:
- the line is one of the agent's channels with the purpose "Calls";
- the agent is Live and may work (gates), and its voice_call policy is Auto (default Off; a
  Custom agent's ceiling is Review, so it never takes calls);
- the voice service is set up (Excom AI Settings);
- nobody could be rung, or the people rung did not answer (no answer, busy, failed).

On a call it answers from the playbook, never agrees prices or terms (a colleague calls back), and
when asked whether it is a person it does not answer that: a colleague calls back (the owner's
policy, §12.6). After the call the transcript goes on the Excom Call, a summary task fills its
summary, sentiment and next action, and the owner gets a callback ToDo when one is needed.
"""

from __future__ import annotations

import hmac
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from excom.excom_ai.engine import proposals, registry, runner, tasks

TOKEN_TTL = 3600
UNANSWERED = ("No Answer", "Busy", "Failed", "Missed")
LANGUAGES = {"english": "en", "spanish": "es", "portuguese": "pt", "hindi": "hi", "french": "fr", "german": "de",
	"italian": "it", "dutch": "nl", "japanese": "ja"}


# ── which agent, if any ──────────────────────────────────────────────────────


def settings():
	return frappe.get_cached_doc("Excom AI Settings")


def agent_for_line(account: str):
	"""The agent that takes this line's unanswered calls, or None."""
	from excom.excom_ai.engine.gates import agent_can_work

	if not settings().enabled or not settings().voice_service_url:
		return None
	for parent in frappe.get_all("Excom AI Channel", {"parenttype": "Excom AI Agent", "channel_account": account,
			"purpose": "Calls", "paused": 0}, pluck="parent", order_by="creation"):
		agent = frappe.get_doc("Excom AI Agent", parent)
		if agent.status != "Live" or not agent_can_work(agent)[0]:
			continue
		if proposals.mode_for(agent, "voice_call", policy_action="voice_call") == "Auto":
			return agent
	return None


def _key(token: str) -> str:
	return f"excom_ai_voice:{token}"


def _call_key(call_id: str) -> str:
	return f"excom_ai_voice_call:{call_id}"


def offer(account: str, call_id: str, caller: str, identity: str | None = None) -> tuple[str, str] | None:
	"""At the answer URL: when an agent could take this call, a stream address and a one-call token
	(kept in the cache, never in the database: routing writes nothing). None otherwise."""
	if not call_id:
		return None
	agent = agent_for_line(account)
	if not agent:
		return None
	token = frappe.generate_hash(length=32)
	state = {"agent": agent.name, "account": account, "call_id": call_id, "caller": caller, "identity": identity,
		"history": [], "turns": 0, "started": None, "task": None}
	frappe.cache.set_value(_key(token), state, expires_in_sec=TOKEN_TTL)
	frappe.cache.set_value(_call_key(call_id), token, expires_in_sec=TOKEN_TTL)
	return settings().voice_service_url, token


def offered(call_id: str) -> str | None:
	return frappe.cache.get_value(_call_key(call_id)) if call_id else None


def after_dial(provider, event) -> str | None:
	"""At the dial action: when the people rung did not answer and an agent was offered this call,
	the call-control document that connects it to the agent. None otherwise."""
	token = offered(event.provider_call_id)
	if not token or event.status not in UNANSWERED:
		return None
	state = frappe.cache.get_value(_key(token))
	if not state or state.get("started"):
		return None
	return provider.render_ai(settings().voice_service_url, token)


# ── the voice service's endpoints ────────────────────────────────────────────


def _verify_voice_key():
	key = settings().get_password("voice_service_key", raise_exception=False) or ""
	sent = frappe.get_request_header("X-Excom-Voice-Key") or ""
	if not key or not hmac.compare_digest(key, sent):
		frappe.throw(_("Not allowed."), frappe.PermissionError)


def _state(token: str) -> dict:
	state = frappe.cache.get_value(_key(token or ""))
	if not state:
		frappe.throw(_("This call has ended."), frappe.DoesNotExistError)
	return state


def _save(token: str, state: dict) -> None:
	frappe.cache.set_value(_key(token), state, expires_in_sec=TOKEN_TTL)


def language_of(agent) -> str:
	for m in agent.markets or []:
		if m.language:
			code = m.language.strip().lower()
			return LANGUAGES.get(code, code[:2] if len(code) <= 3 else "en")
	return "en"


def _company(agent) -> str:
	return agent.company or frappe.defaults.get_global_default("company") or ""


def greeting(agent) -> str:
	first = (agent.agent_name or "").split()[0] if agent.agent_name else ""
	company = _company(agent)
	return _("Hello, this is {0} from {1}. How can I help you?").format(first, company) if company else \
		_("Hello, this is {0}. How can I help you?").format(first)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def start(token: str) -> dict:
	"""The call is connected to the voice service: who speaks, in which language, and the greeting."""
	_verify_voice_key()
	state = _state(token)
	if state.get("started") or state.get("declined"):
		frappe.throw(_("This call has already started."), frappe.PermissionError)  # a token works once
	agent = frappe.get_doc("Excom AI Agent", state["agent"])
	from excom.excom_ai.engine.gates import agent_can_work

	if not agent_can_work(agent)[0]:
		state["declined"] = True
		_save(token, state)
		return {"ok": False, "say": _("Sorry, nobody can take your call right now. A colleague will call you back.")}
	task = tasks.add(agent.name, "voice_call", dedupe_key=f"call:{state['call_id']}",
		params={"call_id": state["call_id"], "caller": state["caller"]})
	frappe.db.set_value("Excom AI Task", task, {"status": "Running", "started_on": now_datetime()})
	say = greeting(agent)
	state.update(started=str(now_datetime()), task=task, history=[{"speaker": "agent", "text": say}])
	_save(token, state)
	frappe.db.commit()
	s = settings()
	return {"ok": True, "call": state["call_id"], "agent_name": agent.agent_name, "language": language_of(agent),
		"voice": agent.get("voice") or "", "greeting": say, "max_seconds": cint(s.voice_max_seconds) or 600,
		"turn_limit": cint(s.voice_turn_limit) or 30}


VOICE_INSTRUCTIONS = """You are on a phone call, answering a caller for your company. Rules for calls:
- Speak in short, natural sentences: one or two at a time. No lists, no links, no formatting.
- Answer in the caller's language.
- Answer only from your brief and playbooks. For prices, discounts, terms, delivery dates, samples, orders,
  complaints, or anything you do not know, say a colleague will call them back, and set needs_person.
- If the caller wants a callback, confirm their name and the best time, and set needs_person.
- When the caller is done, say goodbye and set end_call."""

TURN_SCHEMA = {"type": "object", "properties": {
	"say": {"type": "string"},
	"intent": {"type": "string", "enum": ["question", "interested", "callback", "price", "wrong_person", "not_interested",
		"complaint", "other"]},
	"needs_person": {"type": "boolean"},
	"end_call": {"type": "boolean"}},
	"required": ["say", "intent", "needs_person", "end_call"], "additionalProperties": False}


def _handover_line(agent) -> str:
	return _("A colleague will call you back shortly. Thank you for calling, goodbye.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def turn(token: str, text: str) -> dict:
	"""The caller said something: what the agent says back, and whether the call ends."""
	from excom.excom_ai import responder
	from excom.excom_ai.engine.identity import as_user
	from excom.excom_ai.engine.inbound import asks_if_ai

	_verify_voice_key()
	state = _state(token)
	agent = frappe.get_doc("Excom AI Agent", state["agent"])
	text = (text or "").strip()[:2000]
	state["history"].append({"speaker": "caller", "text": text})
	state["turns"] = cint(state.get("turns")) + 1
	limit = cint(settings().voice_turn_limit) or 30
	reply = None
	if asks_if_ai(text):
		state["needs_person"], state["reason"] = True, "asks_if_ai"
		reply = {"say": _handover_line(agent), "end_call": True}
	elif (rule := responder.rule_intent(text)) is not None:
		state["needs_person"], state["reason"] = rule == "escalate", rule
		reply = {"say": _("Understood. Thank you for calling, goodbye.") if rule == "unsubscribe" else _handover_line(agent),
			"end_call": True}
	elif state["turns"] > limit:
		state["needs_person"], state["reason"] = True, "turn_limit"
		reply = {"say": _handover_line(agent), "end_call": True}
	else:
		try:
			task = frappe.get_doc("Excom AI Task", state["task"])
			messages = [{"role": "user" if h["speaker"] == "caller" else "assistant",
				"content": h["text"] if h["speaker"] == "agent" else f"<untrusted source=\"the caller\">{h['text']}</untrusted>"}
				for h in state["history"]]
			if messages and messages[0]["role"] == "assistant":
				messages.insert(0, {"role": "user", "content": "(the call connects)"})
			with as_user(agent.service_user):
				r = runner.ai(task, agent, messages=messages, output_schema=TURN_SCHEMA, pack_instructions=VOICE_INSTRUCTIONS,
					effort="low", max_tokens=400)
			data = r.data or {}
			if data.get("needs_person"):
				state["needs_person"] = True
				state.setdefault("reason", data.get("intent"))
			reply = {"say": data.get("say") or _handover_line(agent), "end_call": bool(data.get("end_call"))}
		except Exception:
			frappe.log_error(title="Excom AI: a call turn failed", message=frappe.get_traceback())
			state["needs_person"], state["reason"] = True, "error"
			reply = {"say": _("Sorry, I'm having trouble. A colleague will call you back."), "end_call": True}
	state["history"].append({"speaker": "agent", "text": reply["say"]})
	_save(token, state)
	frappe.db.commit()
	return reply


@frappe.whitelist(allow_guest=True, methods=["POST"])
def end(token: str, transcript=None, duration_seconds: int = 0, reason: str = "") -> dict:
	"""The call is over: the transcript goes on the Excom Call, and a summary task is queued. Safe to
	repeat (the voice service retries): a call already ended is acknowledged again."""
	_verify_voice_key()
	state = frappe.cache.get_value(_key(token or ""))
	if not state:
		return {"ok": True, "already": True}
	agent = frappe.get_doc("Excom AI Agent", state["agent"])
	lines = frappe.parse_json(transcript) if isinstance(transcript, str) else (transcript or [])
	if not lines:
		lines = state.get("history") or []
	lines = [{"speaker": "caller" if ln.get("speaker") == "caller" else "agent", "text": str(ln.get("text") or "")[:2000],
		"at_ms": cint(ln.get("at_ms"))} for ln in lines[:400]]
	text = "\n".join(f"{_('Caller') if ln['speaker'] == 'caller' else agent.agent_name}: {ln['text']}" for ln in lines)
	call = frappe.db.get_value("Excom Call", {"provider_call_id": state["call_id"]}, "name")
	if call:
		frappe.db.set_value("Excom Call", call, {
			"transcript": text, "transcript_json": json.dumps(lines, ensure_ascii=False), "transcript_status": "Ready",
			"transcript_engine": "Excom AI voice", "summary_status": "Pending", "answered_by": agent.service_user,
			"status": "Completed", "talk_time": cint(duration_seconds), "outcome": _("Answered by {0} (AI)").format(agent.agent_name),
		})
	if state.get("task"):
		frappe.db.set_value("Excom AI Task", state["task"], {"status": "Done", "ended_on": now_datetime(),
			"outcome": _("call ended ({0}), {1} turns").format(reason or "caller_hangup", cint(state.get("turns")))})
	tasks.add(agent.name, "call_summary", subject=("Excom Call", call) if call else None, dedupe_key=f"summary:{state['call_id']}",
		params={"call_id": state["call_id"], "needs_person": bool(state.get("needs_person")), "reason": state.get("reason") or "",
			"caller": state.get("caller") or "", "transcript": text[:20000]})
	frappe.cache.delete_value(_key(token))
	frappe.cache.delete_value(_call_key(state["call_id"]))
	frappe.db.commit()
	return {"ok": True}


# ── task kinds ───────────────────────────────────────────────────────────────


@registry.task_kind("voice_call", priority=registry.PRIORITY["inbound"], effort="write")
def voice_call(task, agent) -> str:
	"""A live call is driven by the voice service through turn(); a queued one has nothing to do."""
	return _("the call is handled live")


SUMMARY_SCHEMA = {"type": "object", "properties": {
	"summary": {"type": "string"},
	"sentiment": {"type": "string", "enum": ["Positive", "Neutral", "Negative"]},
	"next_action": {"type": "string"},
	"callback_needed": {"type": "boolean"}},
	"required": ["summary", "sentiment", "next_action", "callback_needed"], "additionalProperties": False}


@registry.task_kind("call_summary", priority=registry.PRIORITY["check"], effort="classify")
def call_summary(task, agent) -> str:
	"""Summarise the call onto the Excom Call; a callback ToDo for the owner when one is needed."""
	from excom.excom_ai.engine.prompts import untrusted

	params = frappe.parse_json(task.params or "{}") or {}
	transcript = params.get("transcript") or ""
	call = task.subject_name if task.subject_doctype == "Excom Call" else None
	if not transcript:
		return _("no transcript")
	r = runner.ai(task, agent, output_schema=SUMMARY_SCHEMA, prompt=untrusted(transcript, _("the call transcript")),
		pack_instructions=_("Summarise this phone call for a colleague in two or three sentences: who called, what they "
			"wanted, what was promised. next_action: what a person should do next, or nothing."))
	data = r.data or {}
	if call:
		frappe.db.set_value("Excom Call", call, {"summary": data.get("summary"), "sentiment": data.get("sentiment"),
			"next_action": data.get("next_action"), "summary_status": "Ready"})
	if params.get("needs_person") or data.get("callback_needed"):
		caller = params.get("caller") or _("the caller")
		proposals.propose(task, agent, "create_todo", subject=("Excom Call", call) if call else ("Excom AI Agent", agent.name),
			risk="Low", payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "High",
				"text": _("Call back {0}: {1}").format(caller, data.get("next_action") or data.get("summary") or "")})
	return _("summarised: {0}").format((data.get("summary") or "")[:150])
