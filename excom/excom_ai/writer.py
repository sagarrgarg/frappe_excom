"""The Writer (HLD-007 §10.2, Appendix A), Phase 1D.

`write_sequence` writes one buyer's whole sequence at once, in the buyer's language (Harvey wrote
emails 2 and 3 once per industry batch, which sent English follow-ups to Spanish buyers):

1. Load the enrollment, the person and company research, the agent's brief and approved playbooks,
   the knowledge pack's product and price facts, and the owner's active feedback.
2. Ask the model for every step, with the sequence's purpose and word limit per step. Research
   text from the buyer's website goes in fenced, as data.
3. Code checks (checks.py) on each email; one rewrite with the reasons when any fail.
4. The AI checker reads each email against the playbook and the facts: Pass, Fixed (with the
   fixed text, checked again in code) or Fail.
5. Each email becomes a proposal for its step, with the checks and the checker's verdict on it.
   An email that fails a check, or that the checker failed, always waits for a person.
"""

from __future__ import annotations

import frappe
from frappe import _

from excom.excom_ai import checks, mail
from excom.excom_ai.constants import OUTREACH_PURPOSES
from excom.excom_ai.engine import proposals, registry, runner
from excom.excom_ai.engine.prompts import untrusted

DEFAULT_STEPS = [
	(1, 0, "First email: why we are relevant to this buyer, one product with its price that fits them, one question.", 90, 1),
	(2, 3, "A second angle: another product or a fact from the playbook that matters to this buyer, one question.", 80, 1),
	(3, 7, "A short last note: one line and one easy question.", 45, 0),
]

SEQUENCE_SCHEMA = {
	"type": "object",
	"properties": {
		"language": {"type": "string"},
		"emails": {"type": "array", "items": {"type": "object", "properties": {
			"step": {"type": "integer"}, "subject": {"type": "string"}, "body": {"type": "string"}},
			"required": ["step", "subject", "body"], "additionalProperties": False}},
	},
	"required": ["language", "emails"],
	"additionalProperties": False,
}
CHECKER_SCHEMA = {
	"type": "object",
	"properties": {
		"verdict": {"type": "string", "enum": ["Pass", "Fixed", "Fail"]},
		"notes": {"type": "string"},
		"subject": {"type": "string"},
		"body": {"type": "string"},
	},
	"required": ["verdict", "notes", "subject", "body"],
	"additionalProperties": False,
}
CHECKER_RULES = """You check an email an AI sales colleague wrote, before it goes to a buyer.
Fail it if it states a fact, price, certification, stock or date that is not in the playbook or the facts given,
claims anything the playbook bans, is in the wrong language, sounds like a marketer or an AI, asks more than one
question, or says or implies the writer is a person. If a small edit fixes it, return verdict Fixed with the
corrected subject and body (same language, same length or shorter). If it is fine, Pass with the text unchanged."""


def default_sequence(agent) -> str:
	"""The agent's Sales sequence (three steps), made once."""
	existing = frappe.db.get_value("Excom AI Sequence", {"agent": agent.name, "enabled": 1})
	if existing:
		return existing
	language = next((m.language for m in agent.markets or [] if m.language), None)
	doc = frappe.get_doc({"doctype": "Excom AI Sequence", "title": _("{0}: first contact").format(agent.agent_name),
		"agent": agent.name, "language": language if language and frappe.db.exists("Language", language) else None,
		"first_email_style": "Direct offer", "enabled": 1,
		"steps": [{"step": s, "wait_days": w, "channel": "Email", "purpose": p, "word_limit": wl, "photo_cards": pc}
			for s, w, p, wl, pc in DEFAULT_STEPS]})
	doc.insert(ignore_permissions=True)
	return doc.name


def _facts(agent) -> str:
	if not agent.knowledge:
		return ""
	import json

	facts = json.loads(frappe.db.get_value("Excom AI Knowledge", agent.knowledge, "facts") or "{}")
	lines = []
	for key in ("products", "prices", "terms", "logistics"):
		for f in (facts.get(key) or {}).get("facts") or []:
			lines.append(f"- {f['text']}")
	return "\n".join(lines)


def _feedback(agent, enrollment) -> str:
	rows = frappe.get_all("Excom AI Feedback", {"agent": agent.name, "active": 1}, ["scope", "reference_name", "comment"])
	keep = [r.comment for r in rows if r.scope in ("Agent", "Sequence") or r.reference_name in
		(enrollment.lead, enrollment.company, enrollment.sequence)]
	return "\n".join(f"- {c}" for c in keep)


def compose(agent, body: str, step_row=None, identity: str | None = None, kind: str = "Sequence") -> str:
	"""The email as the buyer sees it: the text, product photo cards where the step asks, and on
	cold mail the postal address and the personal unsubscribe link."""
	html = "".join(f"<p>{frappe.utils.escape_html(p)}</p>" for p in (body or "").split("\n\n") if p.strip())
	html = html.replace("\n", "<br>")
	if step_row is not None and step_row.photo_cards:
		items = []
		for p in agent.products or []:
			if p.item:
				items.append(p.item)
			elif p.item_group:
				items += frappe.get_all("Item", {"item_group": p.item_group, "disabled": 0, "image": ["is", "set"]}, pluck="name",
					limit=3)
		html += mail.photo_cards(items)
	if kind == "Sequence" and identity:
		html += mail.footer(identity, checks.postal_address(agent), mail.data_source(identity))
	return html


def _outreach_account(agent) -> str | None:
	for r in agent.channels or []:
		if r.purpose in OUTREACH_PURPOSES and not r.paused and frappe.db.get_value("Excom Channel Account", r.channel_account, "channel") == "email":
			return r.channel_account
	return None


def subject_styles(step_row) -> list[str]:
	return [ln.strip() for ln in (step_row.get("subject_test") or "").splitlines() if ln.strip()]


def subject_variant(step_row, enrollment: str) -> str | None:
	"""The subject-line style this buyer gets for a step under test: the same one every time for the
	same enrollment, and an even split across buyers (Phase 4)."""
	import hashlib

	styles = subject_styles(step_row)
	if not styles:
		return None
	if len(styles) == 1:
		return styles[0]
	return styles[int(hashlib.sha1(f"{enrollment}:{step_row.step}".encode()).hexdigest(), 16) % len(styles)]


@registry.task_kind("write_sequence", priority=registry.PRIORITY["write"], effort="write")
def write_sequence(task, agent) -> str:
	enrollment = frappe.get_doc("Excom AI Enrollment", task.subject_name)
	sequence = frappe.get_doc("Excom AI Sequence", enrollment.sequence)
	person = frappe.get_doc("Excom AI Person", enrollment.person) if enrollment.person else None
	company = frappe.get_doc("Excom AI Company", enrollment.company) if enrollment.company else None
	to_email = (person.email if person else None) or frappe.db.get_value("Lead", enrollment.lead, "email_id")
	identity = frappe.db.get_value("Omni Identity", {"primary_email": to_email}) if to_email else None
	language = enrollment.language or (person.language if person else None) or sequence.language or (
		company.language if company else None) or "en"
	steps = sorted(sequence.steps, key=lambda s: s.step)
	account = _outreach_account(agent)

	buyer = "\n".join(filter(None, [
		f"Company: {company.company_name}" if company else None,
		f"Country: {company.country}" if company and company.country else None,
		f"Buyer type: {company.buyer_type}" if company and company.buyer_type else None,
		f"Contact: {person.full_name}, {person.title or ''}" if person and not person.is_inbox else "Contact: the company's general inbox",
		f"Research notes: {company.research_notes}" if company and company.research_notes else None,
	]))
	# names, titles and notes came from websites and data providers (HLD-009): data, never instructions
	buyer = untrusted(buyer, "research records", ("Excom AI Enrollment", enrollment.name))
	site = ""
	if company and company.website_facts:
		site = untrusted(frappe.as_json(frappe.parse_json(company.website_facts))[:4000], company.website or company.domain)
	variants = {s.step: subject_variant(s, enrollment.name) for s in steps}
	plan = "\n".join(f"Step {s.step}: {s.purpose} At most {s.word_limit or 90} words."
		+ (f" Subject line style: {variants[s.step]}." if variants[s.step] else "") for s in steps)
	prompt = (f"Write the whole sequence for this buyer, every email in language '{language}'.\n\n{plan}\n\n"
		f"What we sell (from the ERP):\n{_facts(agent)}\n\nOwner feedback to follow:\n{_feedback(agent, enrollment) or '- none'}\n\n"
		f"The buyer:\n{buyer}\n\n{site}")
	result = runner.ai(task, agent, prompt=prompt, pack_instructions=checks.WRITER_RULES, output_schema=SEQUENCE_SCHEMA)
	emails = {e["step"]: e for e in result.data.get("emails") or []}
	missing = [s.step for s in steps if s.step not in emails]
	if missing:
		frappe.throw(_("The writer left out step {0}.").format(", ".join(map(str, missing))))

	made = []
	for s in steps:
		e = emails[s.step]
		kind = "Sequence"
		results = _check(agent, to_email, e, s, person, enrollment, account, language, identity, kind)
		if not results["ok"]:
			e = _rewrite(task, agent, e, s, language, checks.failures(results))
			results = _check(agent, to_email, e, s, person, enrollment, account, language, identity, kind)
		verdict, notes, e = _ai_check(task, agent, e, language)
		if verdict == "Fixed":
			results = _check(agent, to_email, e, s, person, enrollment, account, language, identity, kind)
		ok = results["ok"] and verdict != "Fail"
		name = proposals.propose(
			task, agent, "send_email", policy_action="first_email" if s.step == 1 else "follow_up_email",
			payload={"subject": e["subject"], "body": e["body"], "language": language,
				**({"subject_variant": variants[s.step]} if variants[s.step] else {})},
			preview_html=compose(agent, e["body"], s, identity, kind),
			subject=("Lead", enrollment.lead) if enrollment.lead else (("Excom AI Person", person.name) if person else None),
			thread=enrollment.thread, channel_account=account, risk="Low" if ok else "High", checks=results,
			enrollment=enrollment.name, step=s.step, kind=kind, to_email=to_email, checks_ok=ok,
		)
		if name:
			frappe.db.set_value("Excom AI Proposal", name, {"checker_verdict": verdict, "checker_notes": notes[:1000]},
				update_modified=False)
			made.append(name)
	return _("{0} emails written in {1} for {2}").format(len(made), language, to_email)


def _check(agent, to_email, e, step_row, person, enrollment, account, language, identity, kind) -> dict:
	return checks.check(agent, to_email, e["subject"], e["body"], kind=kind, step=step_row.step, word_limit=step_row.word_limit,
		person=person, lead=enrollment.lead, account=account, language=language,
		composed_html=compose(agent, e["body"], step_row, identity, kind))


def _rewrite(task, agent, e, step_row, language, problems: list[str]) -> dict:
	schema = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
		"required": ["subject", "body"], "additionalProperties": False}
	r = runner.ai(task, agent, pack_instructions=checks.WRITER_RULES, output_schema=schema,
		prompt=_("Rewrite this email (language {0}, at most {1} words) to fix: {2}\n\nSubject: {3}\n\n{4}").format(
			language, step_row.word_limit or 90, "; ".join(problems), e["subject"], e["body"]))
	return {"step": e["step"], "subject": r.data["subject"], "body": r.data["body"]}


def _ai_check(task, agent, e, language) -> tuple[str, str, dict]:
	r = runner.ai(task, agent, pack_instructions=CHECKER_RULES, output_schema=CHECKER_SCHEMA, effort="low",
		prompt=_("Language expected: {0}\n\nSubject: {1}\n\n{2}").format(language, e["subject"], e["body"]))
	d = r.data
	if d["verdict"] == "Fixed" and d.get("body"):
		e = {"step": e["step"], "subject": d.get("subject") or e["subject"], "body": d["body"]}
	return d["verdict"], d.get("notes") or "", e
