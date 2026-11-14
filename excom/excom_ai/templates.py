"""The Template Steward (HLD-008): a background service that makes templates for what agents send
again and again.

- A **need** is recorded when an agent prepares a message and finds no approved template
  (`find`, at writing time). The same need from several agents is one record.
- Every hour `run` reads Meta's verdicts and hands Open needs to `template_draft` on a Live agent
  that needs them. WhatsApp templates go to Meta on their own (Meta's review is the approval);
  email templates go to a person in Needs you. Meta's library is tried before anything is written.
- Agents send from approved templates only: `find`, `whatsapp_values` and `send_rules` (approved,
  on the agent's number, the person opted in). The first send of a new template waits for a person.

The steward never sends anything itself.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, now_datetime, nowdate

from excom.excom_ai.engine import proposals, registry, runner

# ── what the packs may need (v1) ─────────────────────────────────────────────

NEEDS: dict[str, dict] = {}


def declare(key: str, *, channels: tuple, category: str, purpose: str, variables: list[dict], library: tuple = (),
		content: str = "") -> None:
	"""A pack's need. variables: [{"n", "field", "means", "fallback"}] in order; code fills `field`.
	content: for email, what code inserts at {{content}} (the invoice lines, the items)."""
	NEEDS[key] = {"key": key, "channels": channels, "category": category, "purpose": purpose, "variables": variables,
		"library": library, "content": content}


def _v(n, field, means, fallback=""):
	return {"n": n, "field": field, "means": means, "fallback": fallback}


declare("accounts.payment_reminder", channels=("WhatsApp", "Email"), category="UTILITY",
	purpose="a reminder that invoices are overdue, naming the total and the invoice numbers",
	variables=[_v(1, "contact_name", "the contact's first name", "there"), _v(2, "total_due", "the total overdue, with currency"),
		_v(3, "invoices", "the overdue invoice numbers")],
	library=("PAYMENT_OVERDUE", "PAYMENT_DUE_REMINDER"), content="the list of overdue invoices with amounts")
declare("accounts.statement_ready", channels=("WhatsApp",), category="UTILITY",
	purpose="a notice that the customer's statement of account is ready and will be sent by email",
	variables=[_v(1, "contact_name", "the contact's first name", "there"), _v(2, "as_of", "the statement date")],
	library=("STATEMENT_AVAILABLE",))
declare("purchase.po_chase", channels=("WhatsApp", "Email"), category="UTILITY",
	purpose="asking a supplier for the delivery date of a purchase order that is due or late",
	variables=[_v(1, "supplier_name", "the supplier's name", "there"), _v(2, "po", "the purchase order number"),
		_v(3, "due_date", "the date it was due")], content="the outstanding items and quantities")
declare("purchase.rfq_reminder", channels=("WhatsApp", "Email"), category="UTILITY",
	purpose="a reminder that a request for quotation is waiting for the supplier's quote",
	variables=[_v(1, "supplier_name", "the supplier's name", "there"), _v(2, "rfq", "the RFQ number"),
		_v(3, "reply_by", "the date we need the quote by")], content="the items asked for")
declare("sales.order_update", channels=("WhatsApp",), category="UTILITY",
	purpose="an update on the buyer's order: its number and its new status",
	variables=[_v(1, "buyer_name", "the buyer's first name", "there"), _v(2, "order", "the order number"),
		_v(3, "status", "the order's new status")],
	library=("ORDER_CONFIRMATION", "SHIPMENT_CONFIRMATION", "DELIVERY_UPDATE"))
declare("sales.quote_follow_up", channels=("WhatsApp",), category="UTILITY",
	purpose="an informational notice of when a quotation the buyer received stops being valid; nothing persuasive",
	variables=[_v(1, "quotation", "the quotation number"), _v(2, "valid_until", "the date it stops being valid")])


# ── languages (Meta's codes) ─────────────────────────────────────────────────

LANGUAGE_NAMES = {"english": "en", "spanish": "es", "portuguese": "pt_BR", "hindi": "hi", "french": "fr", "german": "de",
	"italian": "it", "dutch": "nl", "arabic": "ar", "chinese": "zh_CN", "japanese": "ja", "russian": "ru", "turkish": "tr",
	"indonesian": "id", "bengali": "bn", "gujarati": "gu", "marathi": "mr", "tamil": "ta", "telugu": "te", "urdu": "ur"}
# Frappe or ISO codes Meta spells differently (Meta has no bare "pt" or "zh").
META_CODES = {"pt": "pt_BR", "zh": "zh_CN", "zh_tw": "zh_TW", "zh_hk": "zh_HK", "pt_pt": "pt_PT", "pt_br": "pt_BR",
	"en_us": "en_US", "en_gb": "en_GB", "es_mx": "es_MX", "es_es": "es_ES", "es_ar": "es_AR"}
META_LANGUAGES = {"af", "sq", "ar", "az", "bn", "bg", "ca", "zh_CN", "zh_HK", "zh_TW", "hr", "cs", "da", "nl", "en", "en_GB",
	"en_US", "et", "fil", "fi", "fr", "ka", "de", "el", "gu", "ha", "he", "hi", "hu", "id", "ga", "it", "ja", "kn", "kk", "rw_RW",
	"ko", "ky_KG", "lo", "lv", "lt", "mk", "ms", "ml", "mr", "nb", "fa", "pl", "pt_BR", "pt_PT", "pa", "ro", "ru", "sr", "sk",
	"sl", "es", "es_AR", "es_ES", "es_MX", "sw", "sv", "ta", "te", "th", "tr", "uk", "ur", "uz", "vi", "zu"}


def meta_language(value: str | None) -> str:
	"""A language as Meta spells it ('es-MX' -> 'es_MX', 'pt' -> 'pt_BR', 'Spanish' -> 'es'); 'en' when unknown."""
	raw = (value or "").strip()
	if not raw:
		return "en"
	low = raw.lower().replace("-", "_")
	code = LANGUAGE_NAMES.get(low) or META_CODES.get(low) or (low[:2] + raw.replace("-", "_")[2:] if len(low) > 2 else low)
	if code not in META_LANGUAGES:
		code = META_CODES.get(code[:2], code[:2])
	return code if code in META_LANGUAGES else "en"


def language_fallbacks(language: str, company: str | None = None) -> list[str]:
	"""The exact language, then its base ('es_MX' -> 'es'), then the company's."""
	out = [meta_language(language)]
	base = out[0].split("_")[0]
	if base in META_LANGUAGES and base not in out:
		out.append(base)
	company_lang = meta_language(frappe.db.get_value("Company", company, "default_language") if company and
		frappe.get_meta("Company").has_field("default_language") else None) if company else None
	if company_lang and company_lang not in out:
		out.append(company_lang)  # then nothing: a buyer is never written to in a language they were not given
	return out


# ── recording a need ─────────────────────────────────────────────────────────


def _settings():
	return frappe.get_cached_doc("Excom AI Settings")


def enabled() -> bool:
	s = _settings()
	return bool(s.enabled and s.templates_enabled)


def _waba(account: str | None) -> str:
	if not account:
		return ""
	return (frappe.db.get_value("Excom Channel Account", account, "wa_business_id") or "").strip()


def unique_key(key: str, channel: str, language: str, agent: str, account: str | None) -> str | None:
	if channel == "WhatsApp":
		waba = _waba(account)
		return f"{key}|WhatsApp|{waba}|{language}" if waba else None  # templates belong to the WABA
	return f"{key}|Email|{agent}|{language}"  # email words are the agent's own voice


def need(agent, key: str, channel: str, language: str, account: str | None = None) -> str | None:
	"""Record (or join) a need. None when it cannot be one (undeclared, no WABA). Never raises."""
	spec = NEEDS.get(key)
	if not spec or channel not in spec["channels"]:
		return None
	language = meta_language(language)
	agent_name = agent if isinstance(agent, str) else agent.name
	uk = unique_key(key, channel, language, agent_name, account)
	if not uk:
		return None
	existing = frappe.db.get_value("Excom AI Template Need", {"unique_key": uk}, ["name", "agents"], as_dict=True)
	if existing:
		agents = [a.strip() for a in (existing.agents or "").split(",") if a.strip()]
		if agent_name not in agents:
			frappe.db.set_value("Excom AI Template Need", existing.name, "agents", ", ".join([*agents, agent_name]),
				update_modified=False)
		return existing.name
	doc = frappe.get_doc({"doctype": "Excom AI Template Need", "need_key": key, "channel": channel,
		"category": spec["category"] if channel == "WhatsApp" else "", "language": language, "agent": agent_name,
		"agents": agent_name, "channel_account": account, "wa_business_id": _waba(account) if channel == "WhatsApp" else "",
		"unique_key": uk, "status": "Open"})
	frappe.db.savepoint("excom_ai_need")
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback(save_point="excom_ai_need")  # another worker recorded it a moment ago
		return frappe.db.get_value("Excom AI Template Need", {"unique_key": uk})
	return doc.name


# ── finding and filling ──────────────────────────────────────────────────────


def _usable_whatsapp(need_row, account: str) -> frappe._dict | None:
	from excom.excom.whatsapp_template_utils import template_is_linked_to_account

	if not need_row.template:
		return None
	t = frappe.db.get_value("WhatsApp Templates", need_row.template, ["name", "status", "category", "template",
		"xai_variable_map", "xai_first_use_done"], as_dict=True)
	if not t or (t.status or "").upper() != "APPROVED" or (t.category or "").upper() != (need_row.category or "").upper():
		return None  # paused, rejected, or moved to another category by Meta: never used
	if not template_is_linked_to_account(t.name, account):
		return None
	return t


def find(agent, key: str, channel: str, language: str, account: str | None = None, record: bool = True) -> dict | None:
	"""An approved template for this need, or None (the need is then recorded, for the steward)."""
	spec = NEEDS.get(key)
	if not spec or channel not in spec["channels"]:
		return None
	agent_doc = agent if not isinstance(agent, str) else frappe.get_doc("Excom AI Agent", agent)
	for lang in language_fallbacks(language, agent_doc.company):
		uk = unique_key(key, channel, lang, agent_doc.name, account)
		row = frappe.db.get_value("Excom AI Template Need", {"unique_key": uk, "status": "Met"},
			["name", "template", "category", "subject", "body", "variable_map", "language"], as_dict=True) if uk else None
		if not row:
			continue
		variables = json.loads(row.variable_map or "null") or spec["variables"]
		if channel == "WhatsApp":
			t = _usable_whatsapp(row, account)
			if t:
				return {"channel": "WhatsApp", "need": row.name, "template": t.name, "text": t.template or "",
					"variables": json.loads(t.xai_variable_map or "null") or variables, "language": lang,
					"first_use_done": bool(t.xai_first_use_done)}
		elif row.body:
			return {"channel": "Email", "need": row.name, "subject": row.subject or "", "body": row.body,
				"variables": variables, "language": lang}
	if record and enabled():
		need(agent_doc, key, channel, language, account)
	return None


def clean_value(value, limit: int = 200) -> str:
	"""One line, no tabs, at most 3 spaces in a row (Meta refuses more), cut to `limit`."""
	text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
	return text[:limit].strip()


def whatsapp_values(found: dict, values: dict) -> list[str]:
	"""The template's variables in order, cleaned, with fallbacks. ValueError when a value is missing
	and has no fallback, or the filled text would pass Meta's 1024 characters."""
	out = []
	for v in used_variables(found, found.get("text") or ""):
		value = clean_value(values.get(v["field"])) or clean_value(v.get("fallback"))
		if not value:
			raise ValueError(_("No value for {0}").format(v.get("means") or v["field"]))
		out.append(value)
	if len(fill(found.get("text") or "", out)) > 1024:
		raise ValueError(_("The filled message is longer than WhatsApp allows"))
	return out


def used_variables(found: dict, text: str) -> list[dict]:
	"""The template's variables that its words use, in order (Meta expects exactly those)."""
	used = {int(n) for n in VAR.findall(text or "")}
	return [v for v in sorted(found["variables"], key=lambda x: cint(x["n"])) if cint(v["n"]) in used]


def fill(text: str, values: list[str]) -> str:
	return re.sub(r"\{\{\s*(\d+)\s*\}\}", lambda m: values[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(values)
		else m.group(0), text or "")


def email_parts(found: dict, values: dict, strict: bool = True) -> tuple[str, str, str]:
	"""(subject, opening, closing) of an approved email template, filled; code puts its own lines
	between the opening and the closing (where the template says {{content}}). ValueError when a value
	is missing and has no fallback (strict=False leaves it empty: for checking the template's own words)."""
	vals = [""] * max([cint(v["n"]) for v in found["variables"]] or [0])
	for v in used_variables(found, (found.get("subject") or "") + (found.get("body") or "")):
		value = clean_value(values.get(v["field"])) or clean_value(v.get("fallback"))
		if not value and strict:
			raise ValueError(_("No value for {0}").format(v.get("means") or v["field"]))
		vals[cint(v["n"]) - 1] = value
	body = fill(found["body"], vals)
	opening, _sep, closing = body.partition("{{content}}")
	return fill(found["subject"], vals), opening.strip(), closing.strip()


# ── sending (for the packs' executors) ───────────────────────────────────────


def send_whatsapp(proposal, agent, thread: str, template: str, variables: list[str]) -> dict:
	"""Send an approved template on the thread, through Excom's own send (and its agent send rules)."""
	from excom.excom.api.chat import send_template_to_thread

	sent = send_template_to_thread(thread, template, variables=json.dumps(variables))
	if proposal and proposal.decided_by and proposal.decided_by != "Autopilot":
		frappe.db.set_value("WhatsApp Templates", template, "xai_first_use_done", 1, update_modified=False)
	return sent or {}


def first_use_pending(template: str | None) -> bool:
	"""The first send of a new template version waits for a person (proposals.propose asks)."""
	if not template or not _settings().template_first_use_review or not frappe.db.exists("WhatsApp Templates", template):
		return False
	if not frappe.db.get_value("WhatsApp Templates", template, "xai_need"):
		return False  # a template people made themselves
	return not frappe.db.get_value("WhatsApp Templates", template, "xai_first_use_done")


# ── the code checks ──────────────────────────────────────────────────────────

# whole words and phrases only: "feel free to reply" and "XYZ Pvt Limited" are not promotions
PROMO = re.compile(r"\b(offers?|discount\w*|sale|deals?|(?<!feel )free(?! to\b)|limited[- ](time|offer|period|stock|edition)|"
	r"hurry|buy now|best price|special|exclusive|promo\w*)\b|\d\s*% off\b", re.IGNORECASE)
RISKY = re.compile(r"(\$|₹|€|£|\bUSD\b|\bINR\b|\bEUR\b|https?://|www\.|\blegal action\b|\blawyer\b|\bcourt\b|\bpenalt\w*|"
	r"\bguarantee\w*\b|\bwe promise\b|\bI am (a )?(human|person|bot|AI)\b|\bas an AI\b|\bartificial intelligence\b)",
	re.IGNORECASE)
VAR = re.compile(r"\{\{\s*(\d+)\s*\}\}")


def check_body(spec: dict, channel: str, body: str, samples: list, subject: str = "") -> list[str]:
	"""What is wrong with a written template, in words a person can act on. [] when it may go."""
	problems = []
	body = body or ""
	declared = [cint(v["n"]) for v in spec["variables"]]
	used = [int(n) for n in VAR.findall(body)]
	text = body.replace("{{content}}", "") if channel == "Email" else body
	if channel == "WhatsApp" and "{{content}}" in body:
		problems.append(_("WhatsApp templates take no {{content}} block"))
	if channel == "Email" and body.count("{{content}}") != 1:
		problems.append(_("An email template has {{content}} exactly once, where code puts its lines"))
	if any(n not in declared for n in used):
		problems.append(_("Only the declared variables {0} may be used").format(declared))
	order = list(dict.fromkeys(used))
	if order != sorted(order) or (order and order != list(range(1, len(order) + 1))):
		problems.append(_("Variables must be numbered in order from {{1}}"))
	if channel == "WhatsApp":
		stripped = text.strip()
		if re.match(r"^\{\{\s*\d+\s*\}\}", stripped) or re.search(r"\{\{\s*\d+\s*\}\}[\s.!?]*$", stripped):
			problems.append(_("A variable may not start or end the message"))
		if re.search(r"\}\}\s*\{\{", text):
			problems.append(_("Two variables may not stand side by side"))
		words = len(VAR.sub(" ", text).split())
		if used and words < 3 * len(used):
			problems.append(_("Too many variables for so few words"))
		if len(body) > 1024:
			problems.append(_("Longer than 1024 characters"))
		if len(samples or []) != len(set(used)) or any(not str(s or "").strip() for s in samples or []):
			problems.append(_("One made-up sample is needed for each variable"))
		elif real := _real_values(samples):
			problems.append(_("Samples must be made up; these are real records: {0}").format(", ".join(real)))
	for part in (subject, text):
		if RISKY.search(VAR.sub(" ", part or "")):
			problems.append(_("No prices, money signs, links, threats, guarantees or claims about being a person or an AI"))
			break
	if spec.get("category") == "UTILITY" and PROMO.search(VAR.sub(" ", text)):
		problems.append(_("A utility template states a fact about a transaction; no promotional words"))
	if not VAR.sub("", text).strip():
		problems.append(_("The template is empty"))
	return problems


def _real_values(samples) -> list[str]:
	found = []
	for s in samples or []:
		s = str(s or "").strip()
		# a lone first name ("Maria") is common on any real site; numbers and full names are not
		if len(s) < 3 or not (any(c.isdigit() for c in s) or " " in s):
			continue
		for dt, field in (("Customer", "customer_name"), ("Supplier", "supplier_name"), ("Contact", "first_name"),
				("Sales Invoice", "name"), ("Purchase Order", "name"), ("Sales Order", "name"), ("Quotation", "name"),
				("Request for Quotation", "name"), ("Lead", "lead_name")):
			if frappe.db.table_exists(dt) and frappe.db.exists(dt, {field: s}):
				found.append(s)
				break
	return found


def template_name(key: str, language: str, waba: str, version: int = 1) -> str:
	"""Meta's rule: lowercase letters, digits and _ only. Language and WABA are in the name, since
	template sync matches rows by name alone; a version after a withdrawal, since Meta keeps a deleted
	name for weeks."""
	raw = f"xai_{key}_{language}_{(waba or '')[-6:]}" + (f"_v{cint(version)}" if cint(version) > 1 else "")
	raw = raw.lower()
	return re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", raw)).strip("_")[:512]


# ── the hourly run ───────────────────────────────────────────────────────────

FINAL = ("DISABLED", "PENDING_DELETION", "DELETED")
POLL_AFTER_HOURS = 24
PENDING_GIVE_UP_DAYS = 14
SLOT_ALERT = 200  # 80% of Meta's 250 for an unverified business
REOPEN_LIMIT = 10  # tries of one template before Try again needs a withdrawal (Meta caps edits per template)


def run():
	"""Hourly (monitor.hourly): Meta's verdicts, then the Open needs."""
	if not enabled():
		return
	try:
		statuses()
		dispatch()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Excom AI: template steward run failed", message=frappe.get_traceback())
	frappe.db.commit()


def statuses() -> None:
	for n in frappe.get_all("Excom AI Template Need", {"status": "Submitted", "channel": "WhatsApp"},
			["name", "template", "category", "attempts", "submitted_on", "agent", "agents", "need_key"]):
		if not n.template or not frappe.db.exists("WhatsApp Templates", n.template):
			if n.submitted_on and get_datetime(n.submitted_on) > add_to_date(now_datetime(), minutes=-30):
				continue  # being posted right now: the template is linked when Meta answers
			_set(n.name, status="Failed", last_error=_("The template is gone from Excom"))
			continue
		t = frappe.db.get_value("WhatsApp Templates", n.template, ["status", "category", "rejection_reason", "id",
			"rejected_at"], as_dict=True)
		state = (t.status or "").upper()
		if state == "REJECTED" and t.rejected_at and n.submitted_on and get_datetime(t.rejected_at) < get_datetime(n.submitted_on):
			state = "PENDING"  # the verdict on the words before this rework, not on what Meta has now
		if state in ("", "PENDING", "IN_APPEAL") and n.submitted_on and \
				get_datetime(n.submitted_on) < add_to_date(now_datetime(), hours=-POLL_AFTER_HOURS):
			t = _poll(n.template) or t
			state = (t.status or "").upper()
		if state == "APPROVED":
			if (t.category or "").upper() == (n.category or "").upper():
				_set(n.name, status="Met", met_on=now_datetime(), last_error=None)
				_tell(n, _("A WhatsApp template is approved and agents will use it: {0}").format(n.need_key))
			else:
				_set(n.name, status="Failed", last_error=_("Meta approved it as {0}, not {1}; it is not used.").format(
					t.category, n.category))
				_tell(n, _("Meta moved a template to {0}; agents will not use it: {1}").format(t.category, n.need_key))
		elif state == "REJECTED":
			if cint(n.attempts) < cint(_settings().template_max_attempts or 2):
				_set(n.name, status="Open", ready=0, last_error=_("Rejected by Meta: {0}").format(t.rejection_reason or "-"))
			else:
				_set(n.name, status="Failed", last_error=_("Rejected by Meta {0} times: {1}").format(n.attempts,
					t.rejection_reason or "-"))
				_tell(n, _("Meta rejected a template again; a person is needed: {0}").format(n.need_key))
		elif state in FINAL:
			_set(n.name, status="Failed", last_error=_("Meta: {0}").format(state))
		elif n.submitted_on and get_datetime(n.submitted_on) < add_days(now_datetime(), -PENDING_GIVE_UP_DAYS):
			_set(n.name, status="Failed", last_error=_("No verdict from Meta in {0} days ({1})").format(PENDING_GIVE_UP_DAYS,
				state or "-"))
	# A need waiting for a person whose proposal is gone (rejected, cancelled, expired, or never made
	# because the template policy is Off) fails, so a person can try again from the Templates screen.
	for n in frappe.get_all("Excom AI Template Need", {"status": "In review"}, ["name", "proposal"]):
		p = frappe.db.get_value("Excom AI Proposal", n.proposal, ["status", "hold_reason"], as_dict=True) if n.proposal else None
		if not p or p.status in proposals.RELEASED:
			why = ": ".join(filter(None, [p.status, p.hold_reason])) if p else _("no review")
			_set(n.name, status="Failed", last_error=_("The review ended without approval ({0})").format(why)[:1000])
	# A template in use that Meta later disabled, deleted or moved to another category: the owners hear
	# once (find() already refuses it); a person tries again from the Templates screen.
	for n in frappe.get_all("Excom AI Template Need", {"status": "Met", "channel": "WhatsApp"},
			["name", "template", "category", "agent", "agents", "need_key"]):
		t = frappe.db.get_value("WhatsApp Templates", n.template, ["status", "category"], as_dict=True) if n.template else None
		state = ((t or {}).get("status") or "").upper()
		moved = t and (t.category or "").upper() != (n.category or "").upper()
		if not t or state in FINAL or moved:
			why = _("moved to {0}").format(t.category) if moved else (state or _("gone"))
			_set(n.name, status="Failed", last_error=_("No longer usable: {0}").format(why))
			_tell(n, _("A WhatsApp template agents used is no longer usable ({0}): {1}").format(why, n.need_key))


def _poll(template: str) -> frappe._dict | None:
	"""Meta's own record, for a template whose status webhook never came."""
	try:
		doc = frappe.get_doc("WhatsApp Templates", template)
		creds = doc._get_creds()
		import requests

		resp = requests.get(f"{creds['url']}/{creds['version']}/{doc.id}", headers=creds["headers"], timeout=30,
			params={"fields": "status,category,rejected_reason,quality_score"})
		data = resp.json() if resp.content else {}
		if resp.status_code >= 400 or not isinstance(data, dict) or data.get("error"):
			return None
		values = {"status": data.get("status") or doc.status, "category": data.get("category") or doc.category}
		if data.get("rejected_reason") and data.get("rejected_reason") != "NONE":
			values["rejection_reason"] = data["rejected_reason"]
		state = (values["status"] or "").upper()
		if state != (doc.status or "").upper() and state in ("APPROVED", "REJECTED"):
			# as the status webhook stamps it: the "3 rejections in 7 days" stop counts polled ones too
			values["approved_at" if state == "APPROVED" else "rejected_at"] = now_datetime()
		frappe.db.set_value("WhatsApp Templates", template, values, update_modified=False)
		return frappe._dict(values)
	except Exception:
		frappe.log_error(title="Excom AI: template status poll failed", message=frappe.get_traceback())
		return None


def _set(name: str, **values) -> None:
	frappe.db.set_value("Excom AI Template Need", name, values, update_modified=True)


def _owners(agent_names: list[str]) -> list[str]:
	people = []
	for a in agent_names:
		row = frappe.db.get_value("Excom AI Agent", a, ["owner_user", "acting_owner", "backup_user"], as_dict=True)
		if row:
			people += [row.acting_owner or row.owner_user, row.backup_user]
	return list(dict.fromkeys(p for p in people if p))


def _agents_of(n) -> list[str]:
	return [a.strip() for a in (n.agents or n.agent or "").split(",") if a.strip()] or [n.agent]


def _tell(n, subject: str) -> None:
	from excom.excom_ai.engine import notify

	notify.alert(_owners(_agents_of(n)), subject, message=_("Open it to see the words, Meta's verdict, or to withdraw it."),
		doctype="Excom AI Template Need", name=n.name)


def submitted_today() -> int:
	return frappe.db.count("Excom AI Template Need", {"channel": "WhatsApp", "submitted_on": [">=", nowdate()]})


def waba_stop(waba: str) -> str | None:
	"""Why auto-submission is paused for this WhatsApp business account, or None. A RED (LOW) or
	flagged number stops it; YELLOW (MEDIUM) only warns the owners (`waba_warning`)."""
	accounts = frappe.get_all("Excom Channel Account", {"wa_business_id": waba}, ["name", "wa_quality_rating"])
	names = [a.name for a in accounts]
	for a in accounts:
		if (a.wa_quality_rating or "").upper() in ("RED", "LOW", "FLAGGED"):
			return _("number {0} has quality {1}").format(a.name, a.wa_quality_rating)
	if not names:
		return None
	ours = frappe.get_all("WhatsApp Templates", {"whatsapp_account": ["in", names], "xai_need": ["is", "set"]},
		["status", "rejected_at", "quality_score"])
	since = add_days(now_datetime(), -7)
	if sum(1 for t in ours if (t.status or "").upper() == "REJECTED" and t.rejected_at and
			get_datetime(t.rejected_at) >= since) >= 3:
		return _("3 rejections in 7 days")
	if any((t.quality_score or "").upper() in ("RED", "LOW") for t in ours):
		return _("a template's quality is low")
	if frappe.db.count("WhatsApp Templates", {"whatsapp_account": ["in", names]}) >= SLOT_ALERT:
		return _("{0} templates or more: near Meta's limit").format(SLOT_ALERT)
	return None


def waba_warning(waba: str) -> str | None:
	"""A number on this business account at YELLOW (MEDIUM) quality: submissions go on, the owners hear."""
	for a in frappe.get_all("Excom Channel Account", {"wa_business_id": waba}, ["name", "wa_quality_rating"]):
		if (a.wa_quality_rating or "").upper() in ("YELLOW", "MEDIUM"):
			return _("number {0} has quality {1}").format(a.name, a.wa_quality_rating)
	return None


def _claim(name: str) -> bool:
	frappe.db.commit()
	try:
		frappe.db.sql("""UPDATE `tabExcom AI Template Need` SET status = 'Drafting', modified = %s
			WHERE name = %s AND status = 'Open'""", (now_datetime(), name))
		claimed = bool(frappe.db.sql("SELECT ROW_COUNT()")[0][0])
	except frappe.QueryDeadlockError:
		frappe.db.rollback()
		return False
	frappe.db.commit()
	return claimed


def _working_agent(n):
	from excom.excom_ai.engine.gates import agent_can_work

	for a in _agents_of(n):
		if not frappe.db.exists("Excom AI Agent", a):
			continue
		agent = frappe.get_doc("Excom AI Agent", a)
		if agent.status == "Live" and agent_can_work(agent)[0]:
			return agent  # Shadow, On Hold and Retired agents never cause anything to go to Meta
	return None


def dispatch(limit: int = 20) -> list[str]:
	"""Claim Open needs and queue their drafting on a Live agent that needs them, oldest first, up to
	`limit`. Needs that cannot go now (a paused business account, no Live agent, a retry not yet due, the
	daily cap) are passed over in the query or skipped page by page, so they never hold up newer ones."""
	from excom.excom_ai.engine import tasks

	# a claim left behind by a run that died is opened again after two hours, unless its task is still
	# open (held, or waiting for a budget): then that task finishes it, and no second one is made
	for stale in frappe.get_all("Excom AI Template Need", {"status": "Drafting",
			"modified": ["<", add_to_date(now_datetime(), hours=-2)]}, pluck="name"):
		if not frappe.db.exists("Excom AI Task", {"kind": "template_draft", "subject_name": stale,
				"status": ["in", ("Queued", "Running", "Held", "Batched")]}):
			_set(stale, status="Open")
	value = _settings().template_submissions_per_day
	cap = 10 if value is None else cint(value)  # 0 stops submissions
	room = max(cap - submitted_today(), 0)
	paused = _paused_wabas()
	queued, skipped, working = [], ["-"], {}
	while len(queued) < limit:
		rows = frappe.db.sql("""SELECT name, channel, wa_business_id, agent, agents, need_key, attempts, total_attempts
			FROM `tabExcom AI Template Need`
			WHERE status = 'Open' AND (retry_after IS NULL OR retry_after <= %(now)s) AND name NOT IN %(skipped)s
				AND (channel = 'Email' OR (%(room)s AND IFNULL(wa_business_id, '') NOT IN %(paused)s))
			ORDER BY creation ASC LIMIT %(page)s""", {"now": now_datetime(), "skipped": tuple(skipped),
			"room": int(room > 0), "paused": tuple(paused) or ("-",), "page": limit}, as_dict=True)
		if not rows:
			break
		for n in rows:
			if len(queued) >= limit:
				break
			if n.channel == "WhatsApp" and room <= 0:
				skipped.append(n.name)  # the daily cap: no more WhatsApp today
				continue
			key = n.agents or n.agent
			if key not in working:
				working[key] = _working_agent(n)
			agent = working[key]
			if not agent or not _claim(n.name):
				skipped.append(n.name)
				continue
			tasks.add(agent.name, "template_draft", subject=("Excom AI Template Need", n.name),
				dedupe_key=f"tpl:{n.name}:{cint(n.total_attempts)}")
			queued.append(n.name)
			if n.channel == "WhatsApp":
				room -= 1
	frappe.db.commit()
	return queued


def _paused_wabas() -> list[str]:
	"""Business accounts with Open WhatsApp needs whose auto-submission is stopped; each of those needs'
	owners hear once. A YELLOW number only warns, once a day."""
	paused = []
	for waba in frappe.get_all("Excom AI Template Need", {"status": "Open", "channel": "WhatsApp"}, pluck="wa_business_id",
			distinct=True):
		if not waba:
			continue
		why = waba_stop(waba)
		if why:
			paused.append(waba)
			for n in frappe.get_all("Excom AI Template Need", {"status": "Open", "channel": "WhatsApp", "wa_business_id": waba,
					"notified": 0}, ["name", "agent", "agents"]):
				_tell(n, _("Templates for WhatsApp are paused: {0}").format(why))
				_set(n.name, notified=1)
		elif (warn := waba_warning(waba)) and not frappe.cache.get_value(f"excom_ai_tpl_warn:{waba}:{nowdate()}"):
			frappe.cache.set_value(f"excom_ai_tpl_warn:{waba}:{nowdate()}", 1, expires_in_sec=86400)
			n = frappe.get_all("Excom AI Template Need", {"status": "Open", "channel": "WhatsApp", "wa_business_id": waba},
				["name", "agent", "agents"], order_by="creation asc", limit=1)[0]
			_tell(n, _("A WhatsApp number's quality is falling ({0}): templates still go to Meta, but watch it").format(warn))
	return paused


# ── drafting ─────────────────────────────────────────────────────────────────

DRAFT_SCHEMA = {"type": "object", "properties": {
	"subject": {"type": "string"}, "body": {"type": "string"}, "samples": {"type": "array", "items": {"type": "string"}},
	"back_translation": {"type": "string"}}, "required": ["subject", "body", "samples", "back_translation"],
	"additionalProperties": False}
CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "problems": {"type": "array",
	"items": {"type": "string"}}, "back_translation": {"type": "string"}}, "required": ["ok", "problems", "back_translation"],
	"additionalProperties": False}
MAP_SCHEMA = {"type": "object", "properties": {"usable": {"type": "boolean"}, "map": {"type": "array", "items": {
	"type": "object", "properties": {"n": {"type": "integer"}, "field": {"type": "string"}}, "required": ["n", "field"],
	"additionalProperties": False}}}, "required": ["usable", "map"], "additionalProperties": False}


def _spec_text(spec: dict, channel: str) -> str:
	vars_ = "\n".join(f"{{{{{v['n']}}}}} = {v['means']}" for v in spec["variables"])
	extra = f"\nWhere code inserts {spec['content']}, write {{{{content}}}} on its own line." if channel == "Email" else ""
	return _("Purpose: {0}\nCategory: {1}\nVariables (only these, in this order):\n{2}{3}").format(
		spec["purpose"], spec["category"], vars_, extra)


@registry.task_kind("template_draft", priority=registry.PRIORITY["write"], effort="write")
def template_draft(task, agent) -> str:
	"""Draft the need's template (library first for WhatsApp), check it, then submit it to Meta
	(WhatsApp, auto-submission on) or ask a person (email, or auto-submission off). Meta errors are
	written on the need and never raised: no retry loop posts twice."""
	n = frappe.get_doc("Excom AI Template Need", task.subject_name)
	if n.status != "Drafting":
		return _("need is {0}").format(n.status)
	if not enabled():
		_set(n.name, status="Open")
		return _("the template steward is switched off")
	if agent.status != "Live":
		_set(n.name, status="Open")
		return _("the agent is not Live")
	spec = NEEDS.get(n.need_key)
	if not spec:
		_set(n.name, status="Failed", last_error=_("Not a declared need"))
		return _("undeclared")
	if n.ready and n.body and n.channel == "WhatsApp":
		# checked (or approved) words that waited for a stop to lift or for Meta to answer: no new draft
		if _settings().whatsapp_auto_submit or _approved(n):
			return submit(n.name)
		_set(n.name, ready=0)  # auto-submission went off meanwhile: a person reads them first
		return _ask_person(task, agent, n.name)
	_set(n.name, attempts=cint(n.attempts) + 1, total_attempts=cint(n.total_attempts) + 1)
	if n.channel == "WhatsApp" and spec.get("library") and not n.template:
		done = _try_library(task, agent, n, spec)
		if done:
			return done
		n.reload()  # a name clash moved it to a new version
	draft, problems = _write(task, agent, n, spec)
	if problems:
		_set(n.name, status="Failed", last_error="; ".join(problems)[:1000], body=draft.get("body"),
			back_translation=draft.get("back_translation"))
		return _("checks failed: {0}").format("; ".join(problems)[:200])
	_set(n.name, subject=(draft.get("subject") or "")[:250] if n.channel == "Email" else None, body=draft["body"],
		samples=json.dumps(draft.get("samples") or []), variable_map=json.dumps(spec["variables"]),
		back_translation=draft.get("back_translation"), source="Written", last_error=None)
	if n.channel == "WhatsApp" and _settings().whatsapp_auto_submit:
		return submit(n.name)
	return _ask_person(task, agent, n.name)


def _write(task, agent, n, spec) -> tuple[dict, list[str]]:
	"""Up to two drafts: each checked by code, then by a model in the template's language."""
	from excom.excom_ai.engine.prompts import untrusted

	feedback, draft, problems = n.last_error or "", {}, []
	for _attempt in range(2):
		prompt = _("Write a reusable {0} message template in the language with Meta code '{1}'.\n{2}\n"
			"Give one made-up sample per variable (never a real name or number) and an English back-translation."
			).format(n.channel, n.language, _spec_text(spec, n.channel))
		if n.channel == "WhatsApp":
			prompt += _("\nWhatsApp: at most 600 characters, plain text, no links, no money signs, a variable never first or "
				"last, never two variables side by side. Leave subject empty.")
		if feedback:
			prompt += "\n\n" + _("Fix these problems from the last try:") + "\n" + untrusted(feedback[:1500], _("checks"))
		r = runner.ai(task, agent, prompt=prompt, output_schema=DRAFT_SCHEMA, pack_instructions=_(
			"You write message templates for this company's agents: short, polite, factual, in the agent's tone."))
		draft = r.data or {}
		problems = check_body(spec, n.channel, draft.get("body") or "", draft.get("samples") or [], draft.get("subject") or "")
		if not problems:
			c = runner.ai(task, agent, output_schema=CHECK_SCHEMA,
				prompt=_("Check this {0} template, written in '{1}':\n{2}\n\n{3}").format(n.channel, n.language,
					_spec_text(spec, n.channel), untrusted(json.dumps({"subject": draft.get("subject"), "body": draft.get("body")},
						ensure_ascii=False), _("the template"))),
				pack_instructions=_("You check message templates before they are used with customers or suppliers. Fail it "
					"if, in its language, it is impolite, unnatural, promotional (for UTILITY), names prices, discounts, "
					"delivery promises, threats or legal consequences, or claims to be a person or an AI. Give an English "
					"back-translation."))
			check = c.data or {}
			if check.get("ok"):
				draft["back_translation"] = check.get("back_translation") or draft.get("back_translation")
				return draft, []
			problems = [str(p) for p in check.get("problems") or ["the language check failed"]]
		feedback = "; ".join(problems)
	return draft, problems


def _try_library(task, agent, n, spec) -> str | None:
	"""A ready-written UTILITY template from Meta's library, approved at once, when one fits."""
	from excom.excom.services import whatsapp_template_library as lib

	fields = {v["field"]: v for v in spec["variables"]}
	for usecase in spec["library"]:
		try:
			entries = lib.search(account_name=n.channel_account, usecase=usecase, language=n.language, limit=5).get("data") or []
		except Exception as e:
			_set(n.name, last_error=_("Library search: {0}").format(str(e)[:300]))
			return None
		for e in entries[:3]:
			if e.get("button_inputs") or (e.get("language") and e.get("language") != n.language):
				continue  # buttons needing our links, or another language
			params = sorted({int(x) for x in VAR.findall(e.get("body") or "")})
			if not params:
				continue
			r = runner.ai(task, agent, output_schema=MAP_SCHEMA, prompt=_(
				"A ready-made template: {0}\nOur fields: {1}\nMap every {{{{n}}}} to one of our fields. usable=false when "
				"any {{{{n}}}} cannot be filled from them.").format(e.get("body"), json.dumps(
					{k: v["means"] for k, v in fields.items()})))
			mapping = {cint(m["n"]): m["field"] for m in (r.data or {}).get("map") or [] if m.get("field") in fields}
			if not (r.data or {}).get("usable") or sorted(mapping) != params:
				continue
			variables = [dict(fields[mapping[p]], n=p) for p in params]
			# the need's own versioned name: after a withdrawal Meta still holds the old one
			name = template_name(n.need_key, n.language, n.wa_business_id, n.version)
			previous = n.submitted_on
			if not _claim_submit(n.name):
				return _("another run is submitting it")
			frappe.flags.integration_request = None
			try:
				out = lib.create(e["name"], name, n.language, account_name=n.channel_account)
			except Exception as err:
				frappe.db.rollback()
				frappe.clear_last_message()
				if _transient(err):
					return _meta_error(n.name, err, previous)
				# the name is taken (here or on Meta): the written template gets the next version
				clash = _name_clash(err)
				if not _set_if(n.name, "Submitted", status="Drafting", submitted_on=previous,
						version=cint(n.version or 1) + (1 if clash else 0),
						last_error=_("Meta refused the library template: {0}").format(str(err)[:500])):
					return _("withdrawn meanwhile")
				frappe.db.commit()
				return None  # written instead
			_submitted(n, out["name"], _("A WhatsApp template from Meta's library is in use (or waiting for Meta): {0}").format(
				n.need_key), source="Library", library_template_name=e["name"], body=e.get("body"),
				variable_map=json.dumps(variables))
			return _("library template {0} created ({1})").format(e["name"], out.get("status"))
	return None


def _approved(n) -> bool:
	"""A person approved these words (the review is done or under way)."""
	return bool(n.proposal) and frappe.db.get_value("Excom AI Proposal", n.proposal, "status") in (
		"Approved", "Edited", "Executing", "Done")


def _transient(err) -> bool:
	"""A timeout, an outage on Meta's side (5xx) or a rate limit (429): worth trying again later. A
	rejection or a validation error (any other 4xx) is final."""
	import requests

	from excom.excom.services import whatsapp_errors as wae

	e, seen = err, set()
	while e is not None and id(e) not in seen:
		seen.add(id(e))
		if isinstance(e, requests.Timeout | requests.ConnectionError):
			return True
		if isinstance(e, wae.WhatsAppAPIError) and (e.error_class in (wae.RETRY, wae.RETRY_GLOBAL) or
				cint(e.http_status) == 429 or cint(e.http_status) >= 500):
			return True
		status = getattr(getattr(e, "response", None), "status_code", None)
		if isinstance(status, int) and (status == 429 or status >= 500):
			return True
		e = e.__cause__ or e.__context__
	resp = getattr(frappe.flags, "integration_request", None)  # the last Meta call (frappe's make_request)
	status = getattr(resp, "status_code", None)
	if not isinstance(status, int):
		return False
	if status == 429 or status >= 500:
		return True
	try:
		error = (resp.json() or {}).get("error") or {}
	except Exception:
		return False
	return bool(error.get("is_transient")) or wae.classify(error.get("code")) in (wae.RETRY, wae.RETRY_GLOBAL)


def _name_clash(err) -> bool:
	text = str(err).lower()
	return any(w in text for w in ("already exists", "being deleted", "name is already", "2388023", "2388024"))


def _meta_error(name: str, err, previous_submitted_on=None) -> str:
	"""After a claimed post to Meta failed: a passing error leaves the checked words to go again in an
	hour (no new draft, no new approval); anything else fails the need for a person."""
	if _transient(err):
		_set_if(name, "Submitted", status="Open", ready=1, submitted_on=previous_submitted_on,
			retry_after=add_to_date(now_datetime(), hours=1),
			last_error=_("Meta did not answer ({0}); it is tried again in an hour.").format(str(err)[:500]))
		frappe.db.commit()
		return _("Meta did not answer: tried again later")
	_set_if(name, "Submitted", status="Failed", ready=0, submitted_on=previous_submitted_on,
		last_error=_("Meta refused it: {0}").format(str(err)[:800]))
	frappe.db.commit()
	return _("Meta refused it")


def _set_if(name: str, while_status: str, **values) -> bool:
	"""Values on the need only while it is still `while_status` (a person may have withdrawn it meanwhile)."""
	values["modified"] = now_datetime()
	cols = ", ".join(f"`{k}` = %({k})s" for k in values)
	frappe.db.sql(f"UPDATE `tabExcom AI Template Need` SET {cols} WHERE name = %(_name)s AND status = %(_while)s",
		dict(values, _name=name, _while=while_status))
	return bool(frappe.db.sql("SELECT ROW_COUNT()")[0][0])


def _submitted(n, template: str, notice: str, **values) -> bool:
	"""The template is on Meta: linked to its claimed need and the owners told (with Withdraw). A need a
	person withdrew while it was being posted keeps nothing: this template is taken back too."""
	frappe.db.set_value("WhatsApp Templates", template, {"xai_need": n.name,
		"xai_variable_map": values.get("variable_map") or n.variable_map}, update_modified=False)
	if not _set_if(n.name, "Submitted", template=template, last_error=None, notified=0, ready=0, retry_after=None, **values):
		frappe.db.commit()
		try:
			if frappe.db.exists("WhatsApp Templates", template):
				frappe.delete_doc("WhatsApp Templates", template, ignore_permissions=True)  # on_trash deletes it on Meta
		except Exception:
			frappe.log_error(title="Excom AI: template withdrawn while posting", message=frappe.get_traceback())
		frappe.db.commit()
		return False
	frappe.db.commit()  # the template is on Meta now: keep the link whatever happens next
	_tell(n, notice)
	return True


def ensure_language(code: str) -> str:
	"""The Frappe Language whose name gives this Meta code (WhatsApp Templates derives the code from it)."""
	name = code.replace("_", "-")
	if not frappe.db.exists("Language", name):
		frappe.get_doc({"doctype": "Language", "language_code": name, "language_name": name}).insert(ignore_permissions=True)
	return name


def _adopt(n, name: str) -> str | None:
	"""A template of this name already on Meta (an earlier try whose local row was lost): store it here."""
	from excom.excom.doctype.whatsapp_templates.whatsapp_templates import _store_templates
	from excom.excom.services import whatsapp_template_library as lib

	try:
		base, headers, waba = lib._api(frappe.get_doc("Excom Channel Account", n.channel_account))
		import requests

		resp = requests.get(f"{base}/{waba}/message_templates", headers=headers, timeout=30,
			params={"name": name, "fields": lib.TEMPLATE_FIELDS})
		rows = [t for t in ((resp.json() or {}).get("data") or []) if t.get("name") == name and t.get("language") == n.language
			and (t.get("status") or "").upper() not in FINAL]
	except Exception:
		return None
	if not rows:
		return None
	accounts = frappe.get_all("Excom Channel Account", {"wa_business_id": n.wa_business_id}, pluck="name") or [n.channel_account]
	_store_templates({"data": rows[:1]}, accounts, [])
	return frappe.db.get_value("WhatsApp Templates", {"actual_name": name})


def _claim_submit(name: str, rework: str | None = None) -> bool:
	"""Drafting -> Submitted, once: two runs on one need never both post to Meta. For a rework the
	template's old verdict is cleared in the same commit, so the hourly run never reads a rejection of
	the old words as a verdict on this submission."""
	frappe.db.commit()
	now = now_datetime()
	try:
		frappe.db.sql("""UPDATE `tabExcom AI Template Need` SET status = 'Submitted', submitted_on = %s, modified = %s
			WHERE name = %s AND status = 'Drafting'""", (now, now, name))
		claimed = bool(frappe.db.sql("SELECT ROW_COUNT()")[0][0])
		if claimed and rework:
			frappe.db.set_value("WhatsApp Templates", rework, {"status": "PENDING", "rejection_reason": None},
				update_modified=False)
	except frappe.QueryDeadlockError:
		frappe.db.rollback()
		return False
	frappe.db.commit()
	return claimed


def submit(need_name: str) -> str:
	"""Post the need's checked template to Meta (insert, or edit after a rejection). Never raises."""
	n = frappe.get_doc("Excom AI Template Need", need_name)
	spec = NEEDS[n.need_key]
	samples = json.loads(n.samples or "[]")
	problems = check_body(spec, "WhatsApp", n.body or "", samples)
	if problems:
		_set(n.name, status="Failed", ready=0, last_error="; ".join(problems)[:1000])
		return _("checks failed")
	stop = waba_stop(n.wa_business_id)
	if stop:
		# the checked (or approved) words are kept: they go when the stop lifts, with no new draft or approval
		_set(n.name, status="Open", ready=1, last_error=_("Paused: {0}").format(stop))
		return _("paused: {0}").format(stop)
	rework = n.template if n.template and frappe.db.exists("WhatsApp Templates", n.template) else None
	before = frappe.db.get_value("WhatsApp Templates", rework, ["status", "rejection_reason"], as_dict=True) if rework else None
	if not _claim_submit(n.name, rework):
		return _("another run is submitting it")
	name = template_name(n.need_key, n.language, n.wa_business_id, n.version)
	frappe.flags.integration_request = None
	frappe.db.savepoint("excom_ai_template")
	try:
		if rework:
			doc = frappe.get_doc("WhatsApp Templates", rework)  # a rejected one: edited, same id, no new slot
			doc.template = n.body
			doc.body_variable_samples = json.dumps(samples)
			doc.xai_back_translation = n.back_translation
			doc.xai_first_use_done = 0
			doc.save(ignore_permissions=True)
			frappe.db.set_value("WhatsApp Templates", doc.name, "submitted_at", now_datetime(), update_modified=False)
		else:
			existing = frappe.db.get_value("WhatsApp Templates", {"actual_name": name}) or _adopt(n, name)
			if existing:
				doc = frappe.get_doc("WhatsApp Templates", existing)
			else:
				doc = frappe.get_doc({"doctype": "WhatsApp Templates", "template_name": name, "template": n.body,
					"language": ensure_language(n.language), "category": spec["category"], "whatsapp_account": n.channel_account,
					"header_type": "", "body_variable_samples": json.dumps(samples), "xai_need": n.name,
					"xai_variable_map": n.variable_map, "xai_back_translation": n.back_translation})
				doc.insert(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback(save_point="excom_ai_template")
		frappe.clear_last_message()
		if before:  # the edit never reached Meta: its last verdict stands again
			frappe.db.set_value("WhatsApp Templates", rework, dict(before), update_modified=False)
		return _meta_error(n.name, e, n.submitted_on)
	if not _submitted(n, doc.name, _("A WhatsApp template went to Meta for review: {0}").format(n.need_key)):
		return _("withdrawn while it was being submitted")
	return _("submitted {0}").format(doc.name)


def _ask_person(task, agent, need_name: str) -> str:
	n = frappe.get_doc("Excom AI Template Need", need_name)
	preview = (f"<b>{frappe.utils.escape_html(n.subject or '')}</b><br>" if n.subject else "") + \
		frappe.utils.escape_html(n.body or "").replace("\n", "<br>") + \
		(f"<br><br><i>{frappe.utils.escape_html(n.back_translation or '')}</i>" if n.back_translation else "")
	name = proposals.propose(task, agent, "approve_template", subject=("Excom AI Template Need", n.name), risk="Medium",
		payload={"need": n.name, "channel": n.channel, "subject": n.subject or "", "body": n.body or "",
			"back_translation": n.back_translation or ""}, preview_html=preview)
	if not name:
		_set(n.name, status="Failed", last_error=_("The agent's template policy is Off: nobody can approve it"))
		return _("template policy Off")
	_set(n.name, status="In review", proposal=name)
	return _("template waiting for review ({0})").format(name)


@registry.executor("approve_template")
def approve_template(proposal, agent):
	"""A person approved the template (as written or edited): checked again, then email templates are
	usable at once and WhatsApp ones go to Meta."""
	p = frappe.parse_json(proposal.payload or "{}")
	# the proposal's own need, never one named in an edited payload
	if proposal.subject_doctype != "Excom AI Template Need" or not proposal.subject_name:
		raise registry.Cancel(_("not a template proposal"))
	n = frappe.get_doc("Excom AI Template Need", proposal.subject_name)
	if n.status != "In review" or n.proposal != proposal.name:
		raise registry.Cancel(_("the need is {0} now; this review is no longer current").format(n.status))
	if proposal.get("decided_by") and proposal.decided_by != "Autopilot":
		from excom.excom_ai.permissions import need_change_refusal

		# a template several owners' agents share is approved by an Excom Admin, as it is withdrawn
		why = need_change_refusal(n, proposal.decided_by)
		if why:
			raise registry.Cancel(why)
	if not enabled():
		raise registry.Defer(add_to_date(now_datetime(), hours=1), _("the template steward is switched off"))
	spec = NEEDS.get(n.need_key)
	if not spec:
		raise registry.Cancel(_("not a declared need"))
	body, subject = p.get("body") or "", p.get("subject") or ""
	problems = check_body(spec, n.channel, body, json.loads(n.samples or "[]"), subject)
	if problems:
		raise registry.Cancel("; ".join(problems))
	_set(n.name, body=body, subject=subject)
	if n.channel == "Email":
		_set(n.name, status="Met", met_on=now_datetime(), last_error=None)
		return ("Excom AI Template Need", n.name)
	_set(n.name, status="Drafting")
	submit(n.name)
	return ("Excom AI Template Need", n.name)


def withdraw(need_name: str) -> None:
	"""Take a template back: deleted on Meta (the row's on_trash), the need Withdrawn."""
	n = frappe.get_doc("Excom AI Template Need", need_name)
	template = n.template
	# unlinked first, or the row cannot be deleted; the next try gets a new name (Meta keeps a deleted
	# one for weeks); an open review is closed, so approving it later does nothing
	_set(n.name, status="Withdrawn", template=None, version=cint(n.version or 1) + (1 if template else 0), ready=0,
		retry_after=None, total_attempts=0 if template else cint(n.total_attempts))
	if n.proposal and frappe.db.get_value("Excom AI Proposal", n.proposal, "status") in ("Pending", "Held", "Shadow"):
		frappe.db.set_value("Excom AI Proposal", n.proposal, "status", "Cancelled")
	if template and frappe.db.exists("WhatsApp Templates", template):
		if frappe.db.exists("Excom Message", {"template": template}):
			# messages were sent with it: the row stays for their history, marked deleted, and only
			# Meta's copy goes (the row's own on_trash call, without deleting the row)
			frappe.get_doc("WhatsApp Templates", template).on_trash()
			frappe.db.set_value("WhatsApp Templates", template, {"status": "DELETED", "xai_need": None},
				update_modified=False)
		else:
			frappe.delete_doc("WhatsApp Templates", template, ignore_permissions=True)  # on_trash deletes it on Meta
	frappe.get_doc("Excom AI Template Need", n.name).add_comment("Comment", _("Withdrawn by {0}").format(frappe.session.user))
