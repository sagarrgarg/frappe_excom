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

No call is lost: a call the agent could not take still gets its callback, and a call whose end
report never came (the voice service died) is closed by its voice_call task when the engine
reaps it, from what is known (caller, agent, the turns so far).
"""

from __future__ import annotations

import hmac
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, time_diff_in_seconds

from excom.excom_ai.engine import proposals, registry, runner, tasks

TOKEN_TTL = 3600
TRANSCRIPT_TTL = 86400  # a transcript waiting for its Excom Call row (the answer URL's write is queued)
UNANSWERED = ("No Answer", "Busy", "Failed", "Missed")
LANGUAGES = {"english": "en", "spanish": "es", "portuguese": "pt", "hindi": "hi", "french": "fr", "german": "de",
	"italian": "it", "dutch": "nl", "japanese": "ja"}
# The languages the voice service has a voice for (voice_service/excom_voice/speech/deepgram.py,
# DEFAULT_VOICES). In any other language, unless the agent has its own voice, the agent speaks
# English: an English voice reading Hindi or Portuguese text is not speech anyone can follow.
VOICE_LANGUAGES = ("en", "es", "fr", "de", "it", "nl", "ja")
# One default for both sides (the voice service's DEFAULT_MAX_SECONDS is the same): the longest call.
DEFAULT_MAX_SECONDS = 600
MAX_SECONDS_CEILING = 900  # the service's systemd unit waits out live calls at shutdown (TimeoutStopSec)
DEFAULT_TURN_LIMIT = 30
# Turns past max_seconds by more than this are refused here, whatever the service does.
MAX_SECONDS_GRACE = 30
MIN_KEY_LENGTH = 32
# The voice service ends these calls with a line promising that a colleague follows up
# (voice_service/excom_voice/phrases.py): end() makes that callback.
PROMISED_CALLBACK = ("error", "max_seconds", "turn_limit", "idle")


# ── which agent, if any ──────────────────────────────────────────────────────


def settings():
	return frappe.get_cached_doc("Excom AI Settings")


def stream_url() -> str:
	"""The service's media-stream address. It must end with /stream (the service answers 404 on
	any other path), so it is added when the setting leaves it out."""
	url = (settings().voice_service_url or "").strip().rstrip("/")
	if url and not url.endswith("/stream"):
		url += "/stream"
	return url


def _voice_key() -> str:
	return settings().get_password("voice_service_key", raise_exception=False) or ""


def voice_ready() -> bool:
	"""The voice service is set up: an address and a long enough key. A key saved before the minimum
	existed switches AI calls off: the admins hear about it once a day, rather than calls quietly
	going back to ringing people."""
	if not stream_url():
		return False
	key = _voice_key()
	if key and len(key) < MIN_KEY_LENGTH:
		flag = f"excom_ai_voice_short_key:{frappe.utils.nowdate()}"
		if not frappe.cache.get_value(flag):
			frappe.cache.set_value(flag, 1, expires_in_sec=86400)
			from excom.excom_ai.engine import notify

			notify.alert(notify.admins(), _("AI calls are off: the voice service key is too short"),
				_("The voice service key in Excom AI Settings must be at least {0} characters. Make a new one, save it "
					"there and in the voice service's settings, and AI calls start again.").format(MIN_KEY_LENGTH),
				"Excom AI Settings", "Excom AI Settings")
	return len(key) >= MIN_KEY_LENGTH


def validate_settings(doc) -> None:
	"""Excom AI Settings.validate: a usable key, and a call length the service can wait out."""
	key = doc.get("voice_service_key") or ""
	if key and not doc.is_dummy_password(key) and len(key) < MIN_KEY_LENGTH:
		frappe.throw(_("The voice service key must be at least {0} characters.").format(MIN_KEY_LENGTH))
	seconds = cint(doc.get("voice_max_seconds"))
	# checked when it changes: a value from before the limit never blocks saving other settings
	if seconds and doc.has_value_changed("voice_max_seconds") and not 60 <= seconds <= MAX_SECONDS_CEILING:
		frappe.throw(_("The longest AI call must be between 60 and {0} seconds.").format(MAX_SECONDS_CEILING))


def agent_for_line(account: str):
	"""The agent that takes this line's unanswered calls, or None."""
	from excom.excom_ai.engine.gates import agent_can_work

	if not settings().enabled or not voice_ready():
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


def _ended_key(token: str) -> str:
	return f"excom_ai_voice_ended:{token}"


def _transcript_key(call_id: str) -> str:
	return f"excom_ai_voice_transcript:{call_id}"


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
	return stream_url(), token


def offered(call_id: str) -> str | None:
	return frappe.cache.get_value(_call_key(call_id), use_local_cache=False) if call_id else None


def _live_state(token: str | None) -> dict | None:
	"""The call's state as it is now (never this request's local copy: end may have removed it)."""
	return frappe.cache.get_value(_key(token), use_local_cache=False) if token else None


def after_dial(provider, event) -> str | None:
	"""At the dial action: when the people rung did not answer and an agent was offered this call,
	the call-control document that connects it to the agent. None otherwise."""
	token = offered(event.provider_call_id)
	if not token or event.status not in UNANSWERED:
		return None
	state = _live_state(token)
	if not state or state.get("started"):
		return None
	state["handed"] = True
	_save(token, state)
	return provider.render_ai(stream_url(), token)


def mark_handed(call_id: str) -> None:
	"""Nobody to ring: the call goes straight to the agent (api.voice._offer_ai)."""
	token = offered(call_id)
	state = _live_state(token)
	if state:
		state["handed"] = True
		_save(token, state)


def never_answered(call_id: str) -> bool:
	"""The call was handed to the agent but the voice service never started it (down, or over its
	call cap): from the caller's side, a missed call (api.voice.hangup_event)."""
	state = _live_state(offered(call_id))
	return bool(state and state.get("handed") and not state.get("started") and not state.get("declined"))


# ── the voice service's endpoints ────────────────────────────────────────────


def _verify_voice_key():
	"""The voice service's shared key, compared as bytes in constant time (a header with non-ASCII
	characters is refused, not an error). A key shorter than MIN_KEY_LENGTH accepts nothing."""
	key = _voice_key().encode()
	sent = (frappe.get_request_header("X-Excom-Voice-Key") or "").encode("utf-8", "replace")
	if len(key) < MIN_KEY_LENGTH or not hmac.compare_digest(key, sent):
		frappe.throw(_("Not allowed."), frappe.PermissionError)


def _state(token: str) -> dict:
	state = frappe.cache.get_value(_key(token or ""))
	if not state:
		frappe.throw(_("This call has ended."), frappe.DoesNotExistError)
	return state


def _save(token: str, state: dict) -> None:
	frappe.cache.set_value(_key(token), state, expires_in_sec=TOKEN_TTL)


def _has_ended(token: str) -> bool:
	return bool(frappe.cache.exists(frappe.cache.make_key(_ended_key(token))))


def _claim_end(token: str) -> bool:
	"""One ending per call: the voice service's end (and its retries) and the engine's lost-call
	recovery may overlap. True for the one that gets to end it."""
	return bool(frappe.cache.set(frappe.cache.make_key(_ended_key(token)), 1, nx=True, ex=TOKEN_TTL))


def _release_end(token: str) -> None:
	frappe.cache.delete_value(_ended_key(token))


def _elapsed(state: dict) -> float:
	return time_diff_in_seconds(now_datetime(), get_datetime(state["started"])) if state.get("started") else 0


def language_of(agent) -> str:
	for m in agent.markets or []:
		if m.language:
			code = m.language.strip().lower()
			return LANGUAGES.get(code, code[:2] if len(code) <= 3 else "en")
	return "en"


def speaks(agent, language: str) -> str:
	"""The language the agent speaks on the phone: its own when there is a voice for it."""
	return language if language in VOICE_LANGUAGES or agent.get("voice") else "en"


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
		# The caller is told a colleague calls back, so end() leaves a callback even with nothing said.
		state.update(declined=True, needs_person=True, reason="declined")
		_save(token, state)
		return {"ok": False, "say": _("Sorry, nobody can take your call right now. A colleague will call you back.")}
	task = tasks.add(agent.name, "voice_call", dedupe_key=f"call:{state['call_id']}",
		params={"call_id": state["call_id"], "caller": state["caller"]})
	frappe.db.set_value("Excom AI Task", task, {"status": "Running", "started_on": now_datetime()})
	s = settings()
	say = greeting(agent)
	notice = (s.get("voice_recording_notice") or "").strip()
	if notice:  # the owner's choice (a legal one), said right after the greeting
		say = f"{say} {notice}"
	language = language_of(agent)
	max_seconds = min(cint(s.voice_max_seconds) or DEFAULT_MAX_SECONDS, MAX_SECONDS_CEILING)
	state.update(started=str(now_datetime()), task=task, history=[{"speaker": "agent", "text": say}],
		max_seconds=max_seconds, reply_language=speaks(agent, language))
	_save(token, state)
	frappe.db.commit()
	return {"ok": True, "call": state["call_id"], "agent_name": agent.agent_name, "language": language,
		"reply_language": state["reply_language"], "voice": agent.get("voice") or "", "greeting": say,
		"max_seconds": max_seconds, "turn_limit": cint(s.voice_turn_limit) or DEFAULT_TURN_LIMIT}


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


ENGLISH_ONLY = """- The phone voice speaks English only: always answer in English, even when the caller speaks another
  language."""

# Said, not typed: speech-to-text gives "am I talking to a computer", "is it an AI", "real person"
# with no question mark, and callers ask for a person outright. Any of these hands the call to a
# colleague, as engine.inbound.asks_if_ai does for messages (the agent never says what it is).
SPOKEN_PERSON_QUESTION = re.compile(
	r"\b(am\s+i|are\s+we)\s+(talking|speaking|chatting)\s+(to|with)\s+(a\s+|an\s+)?"
	r"(computer|machine|bot|robot|recording|ai|a\.\s?i\b|real|human|person|someone\s+real)\b"
	r"|\bis\s+(this|it|that)\s+(a\s+|an\s+)?((computer|bot|robot|ai|human|recording|automated)\b|a\.\s?i\b|real\s+person\b|"
	r"(machine|person)\s*[?.!]?\s*$)"
	r"|\b(are\s+you|you\s+are|you'?re|r\s+u)\s+(a\s+|an\s+)?((computer|machine|bot|robot|ai|human|automated|artificial|"
	r"recording)\b|a\.\s?i\b|real\s+(person|human)\b|(real|person)\s*[?.!]?\s*$)"
	r"|\b(real|actual|live)\s+(person|human|agent)\b"
	r"|\b(speak|talk)\s+(to|with)\s+(a\s+|an\s+|the\s+)?(human|person|someone|somebody|representative|operator|manager)\b"
	r"|\b(eres|es|está|esta|estoy\s+hablando\s+con)\s+(un\s+|una\s+)?(m[aá]quina|computadora|robot|bot|grabaci[oó]n|humano|"
	r"ia|inteligencia\s+artificial)\b"
	r"|\bpersona\s+real\b|\bhablar\s+con\s+(una\s+persona|alguien|un\s+humano)\b"
	r"|\bpessoa\s+real\b|\bfalar\s+com\s+(uma\s+pessoa|algu[eé]m|um\s+humano)\b"
	r"|क्या\s+(आप|ये|यह|मैं)\s.{0,24}(मशीन|कंप्यूटर|रोबोट|बॉट|इंसान)|असली\s+(इंसान|आदमी|व्यक्ति)"
	r"|\b(machine|computer|robot)\s+(ho|hai)\b|\binsaan\s+(ho|hai|se\s+baat)\b",
	re.IGNORECASE,
)


def asks_for_person(text: str | None) -> bool:
	"""Whether the caller asked what they are talking to, or asked for a person."""
	from excom.excom_ai.engine.inbound import asks_if_ai

	return bool(text) and (asks_if_ai(text) or bool(SPOKEN_PERSON_QUESTION.search(text)))


def _handover_line(agent) -> str:
	return _("A colleague will call you back shortly. Thank you for calling, goodbye.")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def turn(token: str, text: str) -> dict:
	"""The caller said something: what the agent says back, and whether the call ends."""
	from excom.excom_ai import responder
	from excom.excom_ai.engine.identity import as_user

	_verify_voice_key()
	state = _state(token)
	if not state.get("started") or _has_ended(token):
		frappe.throw(_("This call has not started."), frappe.PermissionError)
	agent = frappe.get_doc("Excom AI Agent", state["agent"])
	text = (text or "").strip()[:2000]
	state["history"].append({"speaker": "caller", "text": text})
	state["turns"] = cint(state.get("turns")) + 1
	limit = cint(settings().voice_turn_limit) or DEFAULT_TURN_LIMIT
	reply = None
	if _elapsed(state) > (cint(state.get("max_seconds")) or DEFAULT_MAX_SECONDS) + MAX_SECONDS_GRACE:
		# The service should have hung up by now; whatever it does, no more model calls on this call.
		state["needs_person"], state["reason"] = True, "max_seconds"
		reply = {"say": _handover_line(agent), "end_call": True}
	elif asks_for_person(text):
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
			instructions = VOICE_INSTRUCTIONS
			if state.get("reply_language") == "en" and language_of(agent) != "en":
				instructions = f"{instructions}\n{ENGLISH_ONLY}"
			with as_user(agent.service_user):
				r = runner.ai(task, agent, messages=messages, output_schema=TURN_SCHEMA, pack_instructions=instructions,
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
	# The model can take seconds and the call may have ended meanwhile: its state is gone then, and
	# saving would bring it back for an hour.
	if _live_state(token) and not _has_ended(token):
		_save(token, state)
	frappe.db.commit()
	return reply


@frappe.whitelist(allow_guest=True, methods=["POST"])
def end(token: str, transcript=None, duration_seconds: int = 0, reason: str = "") -> dict:
	"""The call is over: the transcript goes on the Excom Call, and a summary task is queued. Safe to
	repeat (the voice service retries) and to overlap: one ending wins, the others are acknowledged."""
	_verify_voice_key()
	if not token or not _live_state(token) or not _claim_end(token):
		return {"ok": True, "already": True}
	state = _live_state(token)
	if not state:  # ended between the two reads
		return {"ok": True, "already": True}
	if reason in PROMISED_CALLBACK and state.get("started"):
		# The service's own last line told the caller a colleague follows up: keep that promise.
		state["needs_person"] = True
		state.setdefault("reason", reason)
	lines = frappe.parse_json(transcript) if isinstance(transcript, str) else (transcript or [])
	try:
		_close(token, state, lines if isinstance(lines, list) else [], cint(duration_seconds), reason or "caller_hangup")
		frappe.db.commit()
	except Exception:
		_release_end(token)  # the service's retry, or the recovery, ends it
		raise
	_forget(token, state)
	return {"ok": True}


def _close(token: str, state: dict, lines: list | None, duration: int, reason: str) -> None:
	"""Write what is known about the call, close its voice_call task, and queue the summary (which
	leaves the callback ToDo). The caller has claimed the ending (_claim_end)."""
	agent = frappe.get_doc("Excom AI Agent", state["agent"])
	if not lines:
		lines = state.get("history") or []
	lines = [{"speaker": "caller" if ln.get("speaker") == "caller" else "agent", "text": str(ln.get("text") or "")[:2000],
		"at_ms": cint(ln.get("at_ms"))} for ln in lines[:400] if isinstance(ln, dict)]
	text = "\n".join(f"{_('Caller') if ln['speaker'] == 'caller' else agent.agent_name}: {ln['text']}" for ln in lines)
	call = frappe.db.get_value("Excom Call", {"provider_call_id": state["call_id"]}, "name")
	if call and state.get("started"):
		values = {"transcript": text, "transcript_json": json.dumps(lines, ensure_ascii=False), "transcript_status": "Ready",
			"transcript_engine": "Excom AI voice", "summary_status": "Pending", "answered_by": agent.service_user,
			"status": "Completed", "outcome": _("Answered by {0} (AI)").format(agent.agent_name)}
		if duration:
			values["talk_time"] = duration
		frappe.db.set_value("Excom Call", call, values)
	elif call:
		frappe.db.set_value("Excom Call", call, "outcome",
			_("{0} (AI) could not take the call; a colleague calls back").format(agent.agent_name))
	elif text and state.get("started"):
		# The answer URL's write of the Excom Call has not landed yet: the summary picks it up here.
		frappe.cache.set_value(_transcript_key(state["call_id"]), {"text": text, "lines": lines},
			expires_in_sec=TRANSCRIPT_TTL)
	if state.get("task"):
		frappe.db.set_value("Excom AI Task", state["task"], {"status": "Done", "ended_on": now_datetime(),
			"outcome": _("call ended ({0}), {1} turns").format(reason, cint(state.get("turns")))})
	tasks.add(agent.name, "call_summary", subject=("Excom Call", call) if call else None, dedupe_key=f"summary:{state['call_id']}",
		params={"call_id": state["call_id"], "needs_person": bool(state.get("needs_person")),
			"reason": state.get("reason") or reason, "caller": state.get("caller") or ""})


def _forget(token: str, state: dict) -> None:
	"""After the ending is committed: the call's state goes (its ended mark stays, for late turns)."""
	frappe.cache.delete_value(_key(token))
	frappe.cache.delete_value(_call_key(state["call_id"]))


# ── task kinds ───────────────────────────────────────────────────────────────


@registry.task_kind("voice_call", priority=registry.PRIORITY["inbound"], effort="write")
def voice_call(task, agent) -> str:
	"""A live call is driven by the voice service through turn(), and end() closes this task. So it
	only ever runs when the engine reaped it (orchestrator.recover_stale, long after any call could
	still be live): the end report never came. The call is closed from what is known, and the
	owner gets the callback."""
	params = frappe.parse_json(task.params or "{}") or {}
	call_id = params.get("call_id") or ""
	token = offered(call_id)
	state = _live_state(token)
	if state:
		if not _claim_end(token):
			return _("the call is being ended by the voice service")
		state.update(needs_person=True, reason=state.get("reason") or "no_end_report")
		try:
			_close(token, state, None, 0, "no_end_report")
			frappe.db.commit()
		except Exception:
			_release_end(token)
			raise
		_forget(token, state)
		return _("the call ended without a report; a colleague calls back")
	# The call's state expired with it: the caller and the agent are what is known.
	call = frappe.db.get_value("Excom Call", {"provider_call_id": call_id}, "name") if call_id else None
	tasks.add(agent.name, "call_summary", subject=("Excom Call", call) if call else None, dedupe_key=f"summary:{call_id}",
		params={"call_id": call_id, "needs_person": True, "reason": "no_end_report", "caller": params.get("caller") or ""})
	return _("the call ended without a report; a colleague calls back")


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
	call_id = params.get("call_id") or ""
	call = task.subject_name if task.subject_doctype == "Excom Call" else None
	if not call and call_id:
		call = frappe.db.get_value("Excom Call", {"provider_call_id": call_id}, "name")
	# Read from the call, never carried in the task's params: the transcript stays in one place.
	transcript = (frappe.db.get_value("Excom Call", call, "transcript") if call else "") or ""
	if not transcript and call_id:
		waiting = frappe.cache.get_value(_transcript_key(call_id), use_local_cache=False) or {}
		transcript = waiting.get("text") or ""
		if transcript and call:  # the call's row landed after the end: the transcript goes on it now
			frappe.db.set_value("Excom Call", call, {"transcript": transcript, "transcript_json": json.dumps(
				waiting.get("lines") or [], ensure_ascii=False), "transcript_status": "Ready",
				"transcript_engine": "Excom AI voice"})
	data = {}
	if transcript:
		r = runner.ai(task, agent, output_schema=SUMMARY_SCHEMA, prompt=untrusted(transcript[:20000], _("the call transcript")),
			pack_instructions=_("Summarise this phone call for a colleague in two or three sentences: who called, what they "
				"wanted, what was promised. next_action: what a person should do next, or nothing."))
		data = r.data or {}
		if call:
			frappe.db.set_value("Excom Call", call, {"summary": data.get("summary"), "sentiment": data.get("sentiment"),
				"next_action": data.get("next_action"), "summary_status": "Ready"})
	elif not params.get("needs_person"):
		return _("no transcript")
	if params.get("needs_person") or data.get("callback_needed"):
		caller = params.get("caller") or _("the caller")
		why = data.get("next_action") or data.get("summary") or _no_transcript_reason(params.get("reason"))
		proposals.propose(task, agent, "create_todo", subject=("Excom Call", call) if call else ("Excom AI Agent", agent.name),
			risk="Low", payload={"allocated_to": agent.acting_owner or agent.owner_user, "priority": "High",
				"text": _("Call back {0}: {1}").format(caller, why)})
	if call_id:
		frappe.cache.delete_value(_transcript_key(call_id))
	if not transcript:
		return _("a callback for a call with nothing said ({0})").format(params.get("reason") or "")
	return _("summarised: {0}").format((data.get("summary") or "")[:150])


def _no_transcript_reason(reason: str | None) -> str:
	if reason == "declined":
		return _("they called and the agent could not take the call")
	if reason == "no_end_report":
		return _("their call with the agent ended without a report")
	return _("they called and asked for a colleague")
