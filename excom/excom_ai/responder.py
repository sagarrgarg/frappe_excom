# Parts of this file are ported from Harvey (harvey/agents/handler.py, prompts/handler.md), MIT
# License, Copyright (c) 2026 Ethan Rogers. See https://opensource.org/licenses/MIT.
"""The Sales responder: a buyer wrote back (HLD-007 §11, Appendix A), Phase 1F.

Runs for an `inbound` task (engine/inbound.py queued it). Fixed rules first, then the model:

1. Read what the buyer wrote, without the quoted thread and the signature.
2. Rules before any model: an opt-out ("unsubscribe", "remove me", "darme de baja", a bare
   "STOP") is honoured at once; anger, legal or spam talk goes to a person.
3. The model classifies the rest: interested, question, objection, negotiate, not_interested,
   ooo, wrong_person (plus unsubscribe and escalate, in doubt).
4. Route:
   - unsubscribe: opt-out recorded, every sequence to them stopped, nothing sent.
   - escalate, negotiate, or any talk of price and terms: a person answers (Needs person).
   - not_interested: the conversation is closed for the agent; nothing sent.
   - ooo: the sequence goes on (the stop the reply caused is undone), no reply.
   - wrong_person: the company is not stopped; a short reply asks who handles buying.
   - interested, question, objection: a reply is drafted from the playbook and the facts,
     checked in code and by the AI checker, and proposed. Interested buyers also become an
     Opportunity through Excom's CRM flow (the lead is classified first) and the owner is told.
5. The conversation's stage moves on as Harvey's did.

WhatsApp replies go out through Excom's chat send, inside the 24-hour window only; email replies
thread on the buyer's message.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom_ai import checks
from excom.excom_ai.engine import notify, proposals, registry, runner
from excom.excom_ai.engine.prompts import untrusted

INTENTS = ("interested", "question", "objection", "negotiate", "not_interested", "ooo", "wrong_person", "unsubscribe", "escalate")
AUTO_REPLY_INTENTS = {"interested", "objection", "question", "wrong_person"}
NO_COMPANY_STOP_INTENTS = {"ooo", "wrong_person"}
OPT_OUT_PATTERNS = (
	"unsubscribe", "opt out", "opt-out", "remove me", "take me off", "stop emailing", "stop contacting", "stop sending",
	"do not contact", "do not email", "don't contact", "don't email", "never contact", "delete my info", "delete my data",
	"darme de baja", "dar de baja", "no me escriba", "no me contacte", "eliminar mis datos", "quitar de su lista",
	"remover de la lista", "descadastrar", "não me envie", "nao me envie", "remova meu", "désabonner", "desinscrire",
)
BARE_OPT_OUTS = {"stop", "unsubscribe", "remove", "remove me", "opt out", "no thanks", "no thank you", "baja", "alto"}
# "darme / denme / dame / dar de baja" (unsubscribe me), not "de baja calidad" (low quality)
BAJA_RE = re.compile(r"\b(?:dar|darme|denme|dame|darnos|dennos)\s+de\s+baja\b", re.I)
NO_THANKS_RE = re.compile(r"(?<!\bor )\bno,?[ \t]+thank(?:s|[ \t]+you)\b(?![ \t]+(?:needed|necessary|required))")
ESCALATION_PATTERNS = ("lawyer", "attorney", "legal action", "sue you", "report you", "reported as spam", "harass", "gdpr",
	"can-spam", "can spam", "data protection", "abogado", "demanda", "denuncia", "acoso", "spam report", "cease and desist")
PRICE_TALK = re.compile(r"\b(price|prices|pricing|cost|quote|quotation|discount|cheaper|expensive|moq|minimum order|payment terms|"
	r"precio|precios|cotizaci[oó]n|descuento|barato|caro|pedido m[ií]nimo|condiciones de pago|preço|desconto|prix|remise)\b", re.I)
QUOTE_START = re.compile(r"^(on .{5,80} wrote:|el .{5,80} escribi[oó]:|em .{5,80} escreveu:|le .{5,80} a écrit|-{2,}\s*original message|"
	r"from:\s.+|de:\s.+|sent from my|enviado desde mi)", re.I | re.M)
STAGES = {"initial_outreach": "Initial outreach", "engaged": "Engaged", "qualifying": "Qualifying", "presenting": "Presenting",
	"negotiating": "Negotiating", "closing": "Closing", "closed_lost": "Closed lost"}

CLASSIFY_SCHEMA = {
	"type": "object",
	"properties": {
		"intent": {"type": "string", "enum": list(INTENTS)},
		"language": {"type": "string"},
		"summary": {"type": "string"},
		"asks_price": {"type": "boolean"},
	},
	"required": ["intent", "language", "summary", "asks_price"],
	"additionalProperties": False,
}
CLASSIFY_RULES = """Classify a buyer's reply to a supplier's email into exactly one intent:
- interested: wants to learn more, open to samples, a call or an order, positive
- objection: has a concern but has not said no (timing, quality, fit, an existing supplier)
- negotiate: pushes on price: a discount, a lower price, better terms, too expensive, will buy elsewhere over price
- not_interested: a clear no, not now, all set
- unsubscribe: asks to stop being contacted or to be removed
- escalate: angry, hostile, legal threats, spam complaints, or asks whether they are talking to a person or a bot
- ooo: out of office or any automatic reply (ticket received, we will get back to you, holiday notice)
- wrong_person: not the right contact, suggests someone else
- question: asks for information before deciding
When in doubt between not_interested and unsubscribe, choose unsubscribe. When in doubt between anything and escalate,
choose escalate. asks_price is true when they ask about prices, quotes, discounts, minimum orders or payment terms."""
HANDLER_RULES = """You answer a buyer who replied to the agent's email. Rules:
1. Reply in the language they wrote in (Spanish: usted). Match their tone.
2. Short: they know who you are. Answer their question first, then at most one question back.
3. Never fabricate: no invented customers, numbers, stock, dates, discounts or certifications. If it is not in the
   playbook or the facts, say a colleague will confirm.
4. Never agree to a price, discount, payment term, delivery date or sample: those go to your colleague.
5. Never say or imply you are a person and never deny being an AI.
6. No em dashes, no exclamation marks, no bullet points, no great question, no signature: the mail server signs.
7. Interested: move towards samples or a call within one or two messages, without overselling.
8. Objection: acknowledge, give one fact from the playbook, ask one question.
9. Wrong person: thank them and ask who handles buying for the category.
Write only the body of the reply."""
REPLY_SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"], "additionalProperties": False}


def clean(text: str) -> str:
	"""What the buyer wrote: the quoted thread and the mobile signature cut away."""
	text = (text or "").replace("\r\n", "\n")
	m = QUOTE_START.search(text)
	if m and m.start() > 0:
		text = text[: m.start()]
	lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
	return "\n".join(lines).strip()


def rule_intent(text: str) -> str | None:
	"""The hard rules, before any model: an opt-out always wins, then escalation."""
	low = " ".join((text or "").lower().split())
	bare = re.sub(r"[^\w\s]", "", low).strip()
	if bare in BARE_OPT_OUTS or any(p in low for p in OPT_OUT_PATTERNS) or BAJA_RE.search(low):
		return "unsubscribe"
	if any(NO_THANKS_RE.search(line) for line in (text or "").lower().splitlines()) and len(low.split()) <= 6:
		return "unsubscribe"
	if any(p in low for p in ESCALATION_PATTERNS):
		return "escalate"
	return None


def full_text(message: str) -> str:
	"""The whole inbound message: for email, the body from the mailbox (read, never stored)."""
	m = frappe.db.get_value("Excom Message", message, ["channel", "content_text", "provider_message_id", "account"], as_dict=True)
	if not m:
		return ""
	if m.channel == "email" and m.provider_message_id:
		try:
			from excom.excom.channels.email import providers as P

			account = frappe.get_doc("Excom Channel Account", m.account)
			full = P.get(account).fetch_full(account, P.raw_id(account, m.provider_message_id)) or {}
			return full.get("body_text") or frappe.utils.strip_html_tags(full.get("body_html") or "") or m.content_text or ""
		except Exception:
			frappe.log_error(title="Excom AI: could not read an email body", message=frappe.get_traceback())
	return m.content_text or ""


def classify(task, agent, text: str) -> dict:
	"""Rules first, then the model."""
	hit = rule_intent(text)
	if hit:
		return {"intent": hit, "language": checks.detect_language(text) or "", "summary": "", "asks_price": False, "by": "rule"}
	r = runner.ai(task, agent, pack_instructions=CLASSIFY_RULES, output_schema=CLASSIFY_SCHEMA,
		prompt=_("Classify this reply.") + "\n\n" + untrusted(text[:6000], _("the buyer's reply")))
	d = dict(r.data)
	if d.get("intent") not in INTENTS:
		d["intent"] = "escalate"  # unknown: a person reads it
	if d["intent"] == "question" and (d.get("asks_price") or PRICE_TALK.search(text)):
		d["intent"] = "negotiate"  # price talk goes to a person
	d["by"] = "model"
	return d


def next_stage(intent: str, current: str | None, text: str) -> str:
	cur = {v: k for k, v in STAGES.items()}.get(current or "", "initial_outreach")
	low = (text or "").lower()
	if intent in ("not_interested", "unsubscribe"):
		return STAGES["closed_lost"]
	if intent == "negotiate":
		return STAGES["negotiating"]
	if intent == "interested":
		if any(w in low for w in ("let's meet", "schedule", "calendar", "book a call", "available", "llamada", "reunión")):
			return STAGES["closing"]
		order = ["initial_outreach", "engaged", "qualifying", "presenting", "negotiating", "closing"]
		return STAGES[order[min(order.index(cur) + 1, len(order) - 1)]] if cur in order else STAGES["engaged"]
	if intent in ("objection", "question") and cur in ("initial_outreach", "engaged"):
		return STAGES["qualifying"] if intent == "objection" else STAGES["engaged"]
	return STAGES.get(cur, "Initial outreach")


@registry.responder("sales")
def respond(task, agent) -> str:
	params = frappe.parse_json(task.params or "{}") or {}
	thread = frappe.get_doc("Excom Thread", task.thread)
	messages = frappe.get_all("Excom Message", {"thread": thread.name, "direction": "Inbound"}, ["name", "creation"],
		order_by="creation desc", limit=5)
	last_out = frappe.db.get_value("Excom Message", {"thread": thread.name, "direction": "Outbound"}, "creation",
		order_by="creation desc")
	new = [m.name for m in messages if not last_out or m.creation > last_out] or ([params["message"]] if params.get("message") else [])
	text = clean("\n\n".join(full_text(m) for m in reversed(new)))
	if not text:
		return _("nothing to read")
	result = classify(task, agent, text)
	intent = result["intent"]
	stage = next_stage(intent, thread.ai_stage, text)
	frappe.db.set_value("Excom Thread", thread.name, {"ai_intent": intent, "ai_stage": stage}, update_modified=False)

	if intent in NO_COMPANY_STOP_INTENTS or params.get("automatic"):
		_undo_stop(thread.name, intent)
	if intent == "unsubscribe":
		from excom.excom_ai.mail import opt_out

		if thread.omni_identity:
			opt_out(thread.omni_identity, source="ai_reply_opt_out")
		frappe.db.set_value("Excom Thread", thread.name, "ai_mode", "Hold", update_modified=False)
		return _("opted out; nothing sent")
	if intent in ("escalate", "negotiate"):
		from excom.excom_ai.engine.inbound import hand_to_person

		hand_to_person(thread.name, agent, text[:500], params.get("message"))
		frappe.db.set_value("Excom Thread", thread.name, "ai_intent", intent, update_modified=False)
		return _("{0}: handed to a person").format(intent)
	if intent == "not_interested":
		frappe.db.set_value("Excom Thread", thread.name, "ai_mode", "Hold", update_modified=False)
		return _("not interested; the conversation is closed for the agent")
	if intent == "ooo":
		return _("an automatic reply; the sequence goes on")
	if intent == "interested":
		_interested(agent, thread)
	name = _draft_reply(task, agent, thread, text, intent, result.get("language") or "")
	return _("{0}: reply proposed ({1})").format(intent, name or _("policy off"))


def _undo_stop(thread: str, intent: str) -> None:
	"""An out-of-office or a wrong-person reply is not a buyer's answer: the sequence goes on, and
	the company stop it caused is lifted (Harvey restored the queue the same way)."""
	companies = set()
	for enr in frappe.get_all("Excom AI Enrollment", {"thread": thread, "status": "Replied"}, ["name", "company"]):
		frappe.db.set_value("Excom AI Enrollment", enr.name, {"status": "Active", "stop_reason": None}, update_modified=False)
		if enr.company:
			companies.add(enr.company)
	for company in companies:
		frappe.db.set_value("Excom AI Company", company, {"company_stop": 0, "stop_reason": None}, update_modified=False)
		# colleagues' sequences that only stopped because of this reply carry on too
		for other in frappe.get_all("Excom AI Enrollment", {"company": company, "status": "Replied", "thread": ["!=", thread]},
				["name", "thread"]):
			answered = other.thread and frappe.db.exists("Excom Message", {"thread": other.thread, "direction": "Inbound"})
			if not answered:
				frappe.db.set_value("Excom AI Enrollment", other.name, {"status": "Active", "stop_reason": None}, update_modified=False)


def _interested(agent, thread) -> None:
	"""An interested buyer: an Opportunity through Excom's CRM flow (the lead classified first),
	made as whoever is in charge of the agent (it is theirs), and they are told at once."""
	from excom.excom_ai.engine.identity import as_user

	email = frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email") if thread.omni_identity else None
	lead = frappe.db.get_value("Lead", {"email_id": email}) if email else None
	if lead:
		try:
			from excom.excom.services import crm_flow, crm_gateway

			if not frappe.db.get_value("Lead", lead, "customer_type") and agent.customer_types:
				frappe.db.set_value("Lead", lead, "customer_type", agent.customer_types.split(",")[0].strip(), update_modified=False)
			if frappe.db.get_value("Lead", lead, "customer_type") and not frappe.db.exists("Opportunity", {"party_name": lead}):
				with as_user(agent.acting_owner or agent.owner_user):
					opp = crm_flow.convert(crm_gateway.lead_ref(lead), crm_gateway.OPPORTUNITY)
				if frappe.get_meta("Opportunity").has_field("ai_origin_agent"):
					frappe.db.set_value("Opportunity", opp.name, "ai_origin_agent", agent.name, update_modified=False)
		except Exception:
			frappe.log_error(title="Excom AI: could not make the opportunity", message=frappe.get_traceback())
	notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user],
		_("An interested buyer is waiting: {0} ({1} AI)").format(thread.display_name or thread.name, agent.agent_name),
		doctype="Excom Thread", name=thread.name)


def _draft_reply(task, agent, thread, text: str, intent: str, language: str) -> str | None:
	from excom.excom_ai.writer import _facts

	history = frappe.get_all("Excom Message", {"thread": thread.name}, ["direction", "content_text"], order_by="creation desc",
		limit=6)
	convo = "\n".join(f"{'Buyer' if m.direction == 'Inbound' else 'Us'}: {m.content_text or ''}" for m in reversed(history))
	r = runner.ai(task, agent, pack_instructions=HANDLER_RULES, output_schema=REPLY_SCHEMA,
		prompt=_("Intent: {0}. Language: {1}. Conversation stage: {2}.").format(intent, language or "theirs", thread.ai_stage or "")
			+ f"\n\nWhat we sell:\n{_facts(agent)}\n\nThe conversation so far (data):\n{untrusted(convo[:4000], 'the conversation')}"
			+ f"\n\n{untrusted(text[:4000], _('the reply to answer'))}")
	body = r.data["body"]
	subject = ""
	if thread.channel == "email":
		last = frappe.db.get_value("Excom Message", {"thread": thread.name, "direction": "Inbound"}, "content_json",
			order_by="creation desc")
		subj = (frappe.parse_json(last or "{}") or {}).get("subject") or ""
		subject = subj if subj.lower().startswith(("re:", "aw:", "rv:")) else f"Re: {subj}".strip()
	to = frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email") if thread.channel == "email" else None
	results = checks.check(agent, to or "buyer@example.com", subject or "re", body, kind="Reply", account=thread.account,
		language=language or None)
	if thread.channel != "email":
		results["recipient"] = {"ok": True, "why": thread.channel}
		results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	from excom.excom_ai.writer import _ai_check

	verdict, notes, fixed = _ai_check(task, agent, {"step": 0, "subject": subject or "re", "body": body}, language or "")
	body = fixed["body"]
	ok = results["ok"] and verdict != "Fail"
	name = proposals.propose(task, agent, "reply", intent=intent, thread=thread.name, channel_account=thread.account,
		payload={"subject": subject, "body": body, "language": language, "intent": intent},
		preview_html="".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in body.split("\n\n") if p.strip()),
		subject=("Excom Thread", thread.name), risk="Low" if ok else "High", checks=results, kind="Reply", to_email=to,
		checks_ok=ok)
	if name:
		frappe.db.set_value("Excom AI Proposal", name, {"checker_verdict": verdict, "checker_notes": notes[:1000]},
			update_modified=False)
	return name


@registry.executor("reply")
def send_reply(proposal, agent):
	"""Send an approved reply on its conversation, as the agent's own user (Excom's send rules
	apply: consent, its own accounts, the 24-hour window for chat channels)."""
	thread = frappe.get_doc("Excom Thread", proposal.thread)
	if thread.ai_mode in ("Hold", "Needs person"):
		raise registry.Cancel(_("a person has this conversation"))
	payload = frappe.parse_json(proposal.payload or "{}")
	body = payload.get("body") or ""
	if thread.channel == "email":
		from excom.excom.channels.email.outbound import send_email_reply

		last = frappe.db.get_value("Excom Message", {"thread": thread.name, "direction": "Inbound"}, "provider_message_id",
			order_by="creation desc")
		to = frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email")
		html = "".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in body.split("\n\n") if p.strip())
		message = send_email_reply(thread.name, to=to, subject=payload.get("subject") or "", body_html=html,
			in_reply_to_gmail_id=last or "")
	else:
		from excom.excom.services.thread_service import send_outbound_message

		message = send_outbound_message(thread.name, body)
	frappe.db.set_value("Excom AI Proposal", proposal.name, {"message": message, "sent_on": now_datetime()}, update_modified=False)
	return ("Excom Message", message)


# ── inbound qualification (§11.5) ────────────────────────────────────────────


def on_enquiry(event: dict) -> None:
	"""`excom_enquiry_handlers`: a new enquiry (a form, an ad lead) on a desk that has a working Sales
	agent becomes that agent's qualification task."""
	if not frappe.db.get_single_value("Excom AI Settings", "enabled") or not event.get("created"):
		return
	lead = event.get("lead")
	team = frappe.db.get_value("Lead", lead, "excom_team") if lead and frappe.get_meta("Lead").has_field("excom_team") else None
	thread = event.get("thread")
	account = frappe.db.get_value("Excom Thread", thread, "account") if thread else None
	agent = None
	for a in frappe.get_all("Excom AI Agent", {"status": ["in", ("Shadow", "Live")]}, ["name", "team", "agent_type"]):
		if frappe.db.get_value("Excom AI Agent Type", a.agent_type, "pack") != "sales":
			continue
		on_account = account and frappe.db.exists("Excom AI Channel", {"parent": a.name, "channel_account": account})
		if (team and a.team == team) or on_account:
			agent = a.name
			break
	if not agent:
		return
	if thread:
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent, "ai_mode": "Handle"}, update_modified=False)
	from excom.excom_ai.engine import tasks

	tasks.add(agent, "qualify_enquiry", subject=("Lead", lead), thread=thread)


QUALIFY_SCHEMA = {
	"type": "object",
	"properties": {"customer_type": {"type": "string"}, "summary": {"type": "string"}, "language": {"type": "string"},
		"wants": {"type": "string"}},
	"required": ["customer_type", "summary", "language", "wants"],
	"additionalProperties": False,
}


@registry.task_kind("qualify_enquiry", priority=registry.PRIORITY["inbound"], effort="classify")
def qualify_enquiry(task, agent) -> str:
	"""A new enquiry: what kind of buyer, what they want; the classification is proposed for the
	lead (so it can become an opportunity), and a first reply is drafted where there is a channel."""
	lead = frappe.get_doc("Lead", task.subject_name)
	notes = "\n".join(frappe.get_all("Comment", {"reference_doctype": "Lead", "reference_name": lead.name,
		"comment_type": "Comment"}, pluck="content", order_by="creation asc", limit=5))
	options = [o for o in (frappe.get_meta("Lead").get_field("customer_type").options or "").split("\n") if o] \
		if frappe.get_meta("Lead").has_field("customer_type") else []
	r = runner.ai(task, agent, output_schema=QUALIFY_SCHEMA,
		pack_instructions=_("Classify a new enquiry for the sales desk. customer_type is one of: {0}.").format(", ".join(options)),
		prompt=untrusted(frappe.utils.strip_html_tags(notes)[:4000] + f"\nCompany: {lead.company_name or ''}\nCountry: {lead.country or ''}",
			_("the enquiry")))
	d = r.data
	if d.get("customer_type") in options and not lead.get("customer_type"):
		proposals.propose(task, agent, "update_lead", subject=("Lead", lead.name), risk="Low",
			payload={"customer_type": d["customer_type"], "note": d.get("summary")})
	if task.thread:
		_draft_reply(task, agent, frappe.get_doc("Excom Thread", task.thread), d.get("summary") or notes, "question",
			d.get("language") or "")
	return _("{0}: {1}").format(lead.name, d.get("customer_type") or "?")


@registry.executor("update_lead")
def update_lead(proposal, agent):
	"""Fields the agent may set on a lead: its customer type (the classification) and a note."""
	payload = frappe.parse_json(proposal.payload or "{}")
	allowed = {k: v for k, v in payload.items() if k in ("customer_type",)}
	if allowed:
		frappe.db.set_value("Lead", proposal.subject_name, allowed)
	if payload.get("note"):
		frappe.get_doc("Lead", proposal.subject_name).add_comment("Comment", frappe.utils.escape_html(payload["note"]))
	return ("Lead", proposal.subject_name)


def eval_classify(inp: dict) -> dict:
	"""Eval target (ai_interface eval sets): the intent for one reply text, as the responder decides it."""
	text = clean(inp.get("text") or "")
	hit = rule_intent(text)
	if hit:
		return {"intent": hit}
	from ai_interface.services.chat import run

	r = run(system=[{"text": CLASSIFY_RULES, "cache": True}], prompt=untrusted(text[:6000], "the buyer's reply"),
		output_schema=CLASSIFY_SCHEMA, calling_app="excom", action="classify_reply", effort="low")
	d = dict(r.data)
	if d.get("intent") == "question" and (d.get("asks_price") or PRICE_TALK.search(text)):
		d["intent"] = "negotiate"
	return {"intent": d.get("intent")}
