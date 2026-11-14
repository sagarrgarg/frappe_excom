# Parts of this file are ported from Harvey (harvey/gate.py), MIT License,
# Copyright (c) 2026 Ethan Rogers. See https://opensource.org/licenses/MIT.
"""Code checks before anything leaves (HLD-007 §12.5).

Prompts are a floor, not a guarantee: every email an agent writes passes these checks in code,
and the result of each is stored on the proposal so a reviewer sees why it passed or failed. An
email that fails any check is never sent on Autopilot; it waits for a person.

    recipient      a real address, the subject's own, not opted out, not on do-not-contact, not an
                   existing customer or someone else's lead (cold mail), deliverable
    channel        one of the agent's mailboxes, not paused
    prices         every price in the email is in one of the agent's price lists (not for kind
                   "Statement": a reminder or statement quotes what a customer owes, written by
                   code from the ledger, not prices; the Accounts pack checks those amounts itself)
    phrases        no spam triggers or AI tells, nothing the playbook bans, and the agent never
                   says it is a person or denies being an AI
    language       the email is in the language of the buyer's market
    shape          subject up to 90 characters, the step's word limit, at most one link and only
                   to our own or the buyer's domain, no merge tags left, plain text
    footer         cold mail carries the postal address and the unsubscribe link
    signature      no sign-off when the mail server adds the signature
    leak           no run of 12 words copied from the agent's own instructions
"""

from __future__ import annotations

import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import flt

BANNED_PHRASES = (
	"act now", "buy now", "click here", "click below", "limited time", "limited offer", "exclusive deal", "special offer",
	"risk-free", "no obligation", "100% free", "make money", "double your", "guaranteed results", "winner",
	"congratulations", "i hope this finds you well", "hope this email finds you well", "i hope this email finds you",
	"picture this", "what if i told you", "in today's fast-paced world", "game-changer", "game changer",
	"cutting-edge solution", "revolutionize your", "unlock your", "supercharge your", "as an ai", "i'm an ai", "i am an ai",
	"as a language model",
)
# Saying or implying it is a person (HLD-007 §12.6: never human, never denied).
HUMAN_CLAIMS = re.compile(
	r"\b(i am|i'm|im) (a )?(real |actual )?(human|person)\b|\bnot (a )?(bot|robot|machine|an ai)\b|\bi'?m not an? (ai|bot)\b"
	r"|\bsoy (una )?persona (real|humana)\b|\bno soy (un )?(bot|robot|una ia)\b|\bsou (uma )?pessoa real\b|\bnão sou (um )?robô\b",
	re.IGNORECASE,
)
SIGN_OFFS = ("regards", "best", "best regards", "kind regards", "thanks", "thank you", "cheers", "saludos", "atentamente",
	"cordialmente", "un saludo", "gracias", "abraços", "atenciosamente", "cordiali saluti", "mit freundlichen grüßen", "sincerely")
MAX_SUBJECT_LEN, MAX_BODY_WORDS, MAX_LINKS = 90, 220, 1
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MERGE_TAG_RE = re.compile(r"\{\{\s*\w+\s*\}\}|\{\s*\w+\s*\}(?!\})|\[(?:first ?name|company|name)\]", re.IGNORECASE)
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.IGNORECASE)
HTML_RE = re.compile(r"<\s*(html|body|div|table|img|a|p|br|span|b|i|strong)\b", re.IGNORECASE)
PRICE_RE = re.compile(r"(?:(US\$|USD|\$|€|EUR|£|GBP|₹|INR|MXN|R\$)\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?))"
	r"|(\d+(?:[.,]\d{1,2})?)\s?(USD|EUR|GBP|INR|MXN|dólares|dolares)", re.IGNORECASE)
LEAK_RUN_WORDS = 12
WORD_RE = re.compile(r"[a-z0-9áéíóúñüç']+")
STOPWORDS = {
	"es": {"de", "la", "que", "el", "en", "los", "para", "con", "una", "por", "su", "usted", "sus", "del", "las", "nuestro", "le"},
	"en": {"the", "and", "to", "of", "for", "your", "you", "with", "our", "we", "is", "in", "on", "this", "are"},
	"pt": {"de", "que", "não", "para", "com", "uma", "os", "seu", "sua", "você", "nosso", "das", "dos", "em"},
	"fr": {"le", "la", "les", "des", "et", "pour", "vous", "votre", "nous", "une", "est", "avec", "dans"},
	"de": {"der", "die", "und", "für", "sie", "ihr", "wir", "mit", "ist", "das", "ein", "eine", "unsere"},
}


def check(agent, to_email: str, subject: str, body: str, *, kind: str = "Sequence", step=None, word_limit: int | None = None,
		person=None, lead: str | None = None, account: str | None = None, language: str | None = None,
		composed_html: str | None = None) -> dict:
	"""Every check, each {ok, why}. `ok` overall is all of them."""
	text = f"{subject or ''}\n{body or ''}"
	results = {
		"recipient": _recipient(agent, to_email, kind, person, lead),
		"channel": _channel(agent, account),
		"prices": (True, _("amounts owed, written by code from the ledger")) if kind == "Statement" else _prices(agent, text),
		"phrases": _phrases(agent, text),
		"language": _language(body, language),
		"shape": _shape(subject, body, word_limit, to_email),
		"footer": _footer(kind, composed_html, agent),
		"signature": _signature(agent, account, body),
		"leak": _leak(agent, text),
	}
	results = {k: {"ok": bool(v[0]), "why": v[1]} for k, v in results.items()}
	results["ok"] = all(v["ok"] for k, v in results.items() if k != "ok")
	return results


def failures(results: dict) -> list[str]:
	return [f"{k}: {v['why']}" for k, v in results.items() if k != "ok" and not v["ok"]]


# ── each check: (ok, why) ────────────────────────────────────────────────────


def _recipient(agent, to_email, kind, person, lead) -> tuple[bool, str]:
	from excom.excom_ai.research import exclusion

	to_email = (to_email or "").strip().lower()
	if not EMAIL_RE.match(to_email):
		return False, _("'{0}' is not an email address").format(to_email)
	if person and (person.email or "").lower() != to_email:
		return False, _("the address is not the buyer's own ({0})").format(person.email)
	identity = frappe.db.get_value("Omni Identity", {"primary_email": to_email},
		["name", "do_not_contact", "email_opt_out"], as_dict=True)
	if identity and (identity.do_not_contact or identity.email_opt_out):
		return False, _("{0} opted out").format(to_email)
	if kind == "Sequence":
		reason, _customer = exclusion(to_email.split("@", 1)[1], [to_email], agent.name)
		if reason:
			return False, reason
		owner = frappe.db.get_value("Lead", lead, ["ai_agent", "lead_owner"], as_dict=True) if lead else None
		if owner and owner.ai_agent and owner.ai_agent != agent.name:
			return False, _("the lead belongs to another agent")
		status = (person.email_status if person else None) or (frappe.db.get_value("Lead", lead, "ai_email_status") if lead else None)
		if status not in ("Verified", "Risky"):
			return False, _("the address is {0}, not verified").format(status or _("unchecked"))
	return True, to_email


def _channel(agent, account) -> tuple[bool, str]:
	if not account:
		return True, _("no mailbox yet (chosen when it is sent)")
	row = next((r for r in agent.channels or [] if r.channel_account == account), None)
	if not row:
		return False, _("{0} is not one of the agent's mailboxes").format(account)
	if row.paused:
		return False, _("{0} is paused: {1}").format(account, row.pause_reason or "")
	return True, account


def _price_lists(agent) -> list[str]:
	return list({p.price_list for p in agent.products or [] if p.price_list})


def _prices(agent, text: str) -> tuple[bool, str]:
	found = []
	for m in PRICE_RE.finditer(text):
		raw = m.group(2) or m.group(3)
		value = flt(raw.replace(",", "") if raw.count(",") and "." in raw else raw.replace(",", "."))
		if value:
			found.append(value)
	if not found:
		return True, _("no prices")
	lists = _price_lists(agent)
	if not lists:
		return False, _("prices {0} but the agent has no price list").format(", ".join(map(str, found)))
	known = {round(flt(r), 2) for r in frappe.get_all("Item Price", {"price_list": ["in", lists]}, pluck="price_list_rate")}
	unknown = [v for v in found if round(v, 2) not in known]
	if unknown:
		return False, _("{0} is not in the price lists {1}").format(", ".join(map(str, unknown)), ", ".join(lists))
	return True, _("prices from {0}").format(", ".join(lists))


def _banned_by_playbook(agent) -> list[str]:
	approved = [r.playbook for r in agent.playbooks or [] if frappe.db.get_value("Excom AI Playbook", r.playbook, "status") == "Approved"]
	out = []
	for s in frappe.get_all("Excom AI Playbook Section", {"parent": ["in", approved or ["-"]], "kind": "Banned claims"},
			pluck="content"):
		for line in frappe.utils.strip_html_tags(s or "").splitlines():
			line = re.sub(r"^[\s\-*•\d.)]+", "", line).strip().strip('"').rstrip(".,;:!").lower()
			# "Never claim EN71" -> "en71"
			line = re.sub(r"^(never (claim|say|mention|use)|don't (claim|say)|no)\s+", "", line)
			if 3 <= len(line) <= 60:
				out.append(line)
	return out


def _phrases(agent, text: str) -> tuple[bool, str]:
	low = _normalize(text).lower()
	hits = [p for p in BANNED_PHRASES if p in low]
	hits += [p for p in _banned_by_playbook(agent) if re.search(rf"\b{re.escape(p)}\b", low)]
	if HUMAN_CLAIMS.search(low):
		hits.append(_("says or implies it is a person"))
	return (not hits), (_("banned: {0}").format(", ".join(hits[:4])) if hits else _("clean"))


def detect_language(text: str) -> str | None:
	words = WORD_RE.findall((text or "").lower())
	if len(words) < 6:
		return None
	scores = {code: sum(1 for w in words if w in sw) for code, sw in STOPWORDS.items()}
	best = max(scores, key=scores.get)
	return best if scores[best] >= 2 else None


def _language(body: str, language: str | None) -> tuple[bool, str]:
	if not language:
		return True, _("no language set")
	found = detect_language(body)
	if found and found != language[:2]:
		return False, _("written in {0}, the buyer's language is {1}").format(found, language)
	return True, found or _("too short to tell")


def _shape(subject: str, body: str, word_limit: int | None, to_email: str) -> tuple[bool, str]:
	problems = []
	subject, body = (subject or "").strip(), (body or "").strip()
	if not subject:
		problems.append(_("no subject"))
	if not body:
		problems.append(_("no body"))
	if len(subject) > MAX_SUBJECT_LEN:
		problems.append(_("subject {0} characters").format(len(subject)))
	words = len(body.split())
	limit = word_limit or MAX_BODY_WORDS
	if words > limit:
		problems.append(_("{0} words, the limit is {1}").format(words, limit))
	if MERGE_TAG_RE.search(subject + " " + body):
		problems.append(_("a merge tag left in"))
	links = URL_RE.findall(body)
	if len(links) > MAX_LINKS:
		problems.append(_("{0} links").format(len(links)))
	if HTML_RE.search(body):
		problems.append(_("HTML in the text"))
	return (not problems), ("; ".join(problems) or _("{0} words").format(words))


def _footer(kind: str, composed_html: str | None, agent) -> tuple[bool, str]:
	if kind != "Sequence" or composed_html is None:
		return True, _("not cold mail")
	address = postal_address(agent)
	missing = []
	if not address:
		missing.append(_("no postal address in Excom AI Settings for {0}").format(agent.company))
	elif frappe.utils.escape_html(address.splitlines()[0]) not in composed_html:
		missing.append(_("postal address"))
	if "excom.excom_ai.mail.unsubscribe" not in composed_html and agent.status != "Shadow":
		missing.append(_("unsubscribe link"))  # a Shadow draft has no person record yet: the link is added when sent
	return (not missing), (_("missing: {0}").format(", ".join(missing)) if missing else _("address and unsubscribe present"))


def postal_address(agent) -> str:
	for r in frappe.get_single("Excom AI Settings").postal_addresses or []:
		if r.company == agent.company:
			return r.address or ""
	return ""


def _signature(agent, account, body: str) -> tuple[bool, str]:
	row = next((r for r in agent.channels or [] if r.channel_account == account), None) if account else None
	mode = row.signature_mode if row else "Server adds it"
	if mode != "Server adds it":
		return True, _("the agent signs")
	tail = [ln.strip().lower().rstrip(",.!") for ln in (body or "").strip().splitlines() if ln.strip()][-3:]
	name = (agent.agent_name or "").lower()
	if any(ln in SIGN_OFFS or (name and ln == name) for ln in tail):
		return False, _("ends with a sign-off: the mail server adds the signature")
	return True, _("no sign-off")


def _leak(agent, text: str) -> tuple[bool, str]:
	from excom.excom_ai.engine import prompts

	sources = [prompts.GLOBAL_RULES, WRITER_RULES]
	words = WORD_RE.findall(text.lower())
	runs = set()
	for src in sources:
		w = WORD_RE.findall(src.lower())
		runs |= {tuple(w[i:i + LEAK_RUN_WORDS]) for i in range(len(w) - LEAK_RUN_WORDS + 1)}
	for i in range(len(words) - LEAK_RUN_WORDS + 1):
		if tuple(words[i:i + LEAK_RUN_WORDS]) in runs:
			return False, _("quotes the agent's own instructions")
	return True, _("no quoted instructions")


def _normalize(text: str) -> str:
	text = unicodedata.normalize("NFKC", text or "")
	return re.sub("[​-‏⁠﻿]", "", text)


# The writer's rules (ported from Harvey's prompts/writer.md, made general: company facts live in
# the playbook, not here). Kept beside the checks because the leak check reads them too.
WRITER_RULES = """Rules for every email you write:
1. Plain text only: no bullet points, bold, italics, links in the first email, images or attachments.
2. Never use em dashes. Use periods or commas.
3. Never use: picture this, imagine if, what if I told you, in today's fast-paced world, game-changer, unlock, supercharge, elevate, seamless, leverage, delve.
4. Never start an email with I or We. Start with them.
5. No exclamation marks, no ALL CAPS, no more than one question mark.
6. One point and exactly one question per email.
7. Respect each step's word limit. Count the words.
8. Subject lines: lowercase, 2 to 5 words, no buyer name, never a fake Re: or Fwd:.
9. Write like an established supplier: calm, specific, never needy. No just checking in, following up, any update, no urgency tricks.
10. Every fact, price, certification and claim must come from the playbook or the ERP facts given. Never invent details, prices, stock, dates or certifications.
11. Write the whole sequence in the buyer's language, the same language in every email.
12. Use the short name people call the company, not its legal form.
13. Never add a signature, sign-off or name block: the mail server adds it. End on the question.
14. Customs or import data may tell you a company imports the category; never name their suppliers, shipments, values or dates, or where you learned it.
15. Never say or imply that you are a person, and never deny being an AI.
16. Spam words to avoid: free, guarantee, risk-free, act now, limited time, discount, % off, click here, buy now, 100%."""
