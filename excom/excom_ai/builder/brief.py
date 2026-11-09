"""The draft agent: brief, markets, products, channels, targets and a playbook skeleton
(HLD-007 §6.2 step 4, §6.5).

The draft is built from the type, the keywords and the knowledge pack, so every line can name its
source ("from 6 export invoices, last 12 months"). A line with no ERP source says "AI suggestion,
please check". When ai_interface has a provider, the model rewrites the wording from the same
facts (as the person building the agent, under the budget scope "Excom AI Builder"); without
one, the deterministic draft stands, so the builder works on any site.

Tone (§6.5) comes from the desk's own last outbound messages: traits, never sentences, and no
names. With fewer than 30 messages it is the type's default.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import add_months, nowdate

from excom.excom_ai.builder import geo

SUGGEST = "AI suggestion, please check"
TYPE_DEFAULT = "type default"
PERSONAS = {"es": ["Lucía", "Mateo", "Sofía"], "pt": ["Ana", "Rafael"], "en": ["Harish", "Maya", "Arjun"],
	"fr": ["Camille"], "de": ["Lena"], "ar": ["Omar"], "hi": ["Aarav"]}
SALES_TONE = "Warm, direct, manufacturer to buyer. Short paragraphs, one question at a time, no hype."
TONE_BY_LANG = {"es": "Formal Spanish (usted).", "pt": "Formal Portuguese (o senhor / a senhora).",
	"de": "Formal German (Sie).", "fr": "Formal French (vous)."}
SALES_DO = ["Answer only from the playbook and the ERP facts you are given",
	"Quote only from the agent's price list, in its currency",
	"Hand every price negotiation, sample request and order to the owner",
	"Write in the buyer's language"]
SALES_DONT = ["Never invent certifications, prices, stock or delivery dates",
	"Never agree to a discount, payment term or delivery date",
	"Never say or imply you are a person, and never deny being an AI",
	"Never write cold to an existing customer or someone on do-not-contact"]
PLAYBOOK_KINDS = ["Facts", "Prices and terms", "Allowed claims", "Certifications", "Banned claims", "Objections", "FAQ",
	"Process rules", "Compliance", "Tone examples"]


def _data(sections: dict, key: str) -> dict:
	return (sections.get(key) or {}).get("data") or {}


def draft(ctx, sections: list[dict], persona: str = "") -> dict:
	"""The deterministic draft: {fields…, markets, products, channels, kpi_targets, sources}."""
	by = {s["section"]: s for s in sections}
	s = ctx.scope
	export = "export" in s.concepts
	company = ctx.company
	langs = s.languages or sorted({lang for c in s.countries for lang in geo.languages_of(c)}) or ["en"]
	products = ", ".join(s.get("product_words") or s.item_groups or s.brands or s.item_codes) or _("products")
	markets_txt = _market_words(s)
	sources = {}

	# Only a list that prices items in scope is a price basis (GGIL's Color Blaze prices Gulal, not water guns).
	lists = [p for p in _data(by, "prices").get("lists") or [] if p.get("currency") != ctx.currency and p.get("covered")]
	best = max(lists, key=lambda p: p.get("covered") or 0) if lists else None
	price_txt = _("the export price list {0}").format(best["price_list"]) if best else _("the price list (none yet)")
	if not best:
		sources["price"] = _("No price list in the market's currency covers these items: a readiness blocker")

	in_markets = _data(by, "customers").get("in_markets") or []
	audience_types = [c for c in s.concepts if c in ("distributor", "retailer", "b2b", "oem", "marketplace")]
	who = {"distributor": "importers and distributors", "retailer": "retailers", "b2b": "wholesalers",
		"oem": "private-label buyers", "marketplace": "marketplace sellers"}
	who_txt = ", ".join(who[a] for a in audience_types) or _("importers, distributors and wholesalers")

	fields = {}
	fields["agent_name"] = persona or (PERSONAS.get(langs[0]) or PERSONAS["en"])[0]
	sources["agent_name"] = SUGGEST
	fields["title"] = f"{'Export ' if export else ''}Sales, {company}"
	sources["title"] = _("type and company")
	fields["what_it_is"] = _(
		"An {0}sales agent for {1}'s {2}{3}. It finds {4}, writes to them in {5}, answers their questions from the approved "
		"playbook and {6}, and hands every price negotiation, sample request and order to its owner.").format(
		"export " if export else "", company, products, f" in {markets_txt}" if markets_txt else "", who_txt,
		", ".join(geo.LANGUAGES.get(code, {}).get("name", code) for code in langs), price_txt)
	sources["what_it_is"] = _("type, keywords and the knowledge pack")
	fields["mission"] = _("Reply to every enquiry within 2 working hours; 5 qualified buyers a month{0}.").format(
		f" in {markets_txt}" if markets_txt else "")
	sources["mission"] = SUGGEST
	if in_markets:
		fields["audience"] = _("Buyers like our current customers in these markets: {0}.").format(", ".join(in_markets[:5]))
		sources["audience"] = _("{0} customers in the target markets").format(len(in_markets))
	else:
		fields["audience"] = _("No customers in these markets yet, so this is not a lookalike: {0} of {1}, found in "
			"customs data and by local-language searches, sized as distributors and wholesalers.").format(who_txt, products)
		sources["audience"] = _("market data; ") + SUGGEST
	tone = tone_for(ctx, langs)
	fields["tone"] = tone["tone"]
	sources["tone"] = tone["source"]
	fields["tone_examples"] = tone.get("examples") or ""
	do = list(SALES_DO)
	if best:
		do[1] = _("Quote only from the price list {0} ({1})").format(best["price_list"], best["currency"])
		sources["rules_do"] = _("type defaults; Price List {0}").format(best["price_list"])
	else:
		sources["rules_do"] = TYPE_DEFAULT
	fields["rules_do"] = "\n".join(f"- {x}" for x in do)
	fields["rules_dont"] = "\n".join(f"- {x}" for x in SALES_DONT)
	sources["rules_dont"] = TYPE_DEFAULT

	markets = [{"country": c, "language": next(iter(geo.languages_of(c)), None) if not s.languages else
		next((code for code in s.languages if c in geo.LANGUAGES.get(code, {}).get("countries", [])), s.languages[0]),
		"timezone": geo.main_timezone(c), "priority": 1} for c in s.countries if frappe.db.exists("Country", c)]
	for m in markets:
		if m["language"] and not frappe.db.exists("Language", m["language"]):
			m["language"] = None
	products_rows = [{"item_group": g, "price_list": best["price_list"] if best else None} for g in s.item_groups]
	products_rows += [{"item": i, "price_list": best["price_list"] if best else None} for i in s.item_codes]
	channels = [{"channel_account": a, "purpose": "Outreach and replies"} for a in _channels_for(ctx)]
	kpis = [{"metric": _("Reply to enquiries within (hours)"), "value": 2, "period": "Day"},
		{"metric": _("Qualified buyers"), "value": 5, "period": "Month"}]
	return {"fields": fields, "markets": markets, "products": products_rows, "channels": channels, "kpi_targets": kpis,
		"sources": sources, "left_out": getattr(s, "left_out", []) or []}


def _market_words(s) -> str:
	regions = [k for k, v in geo.REGIONS.items() if v and set(s.countries) and set(s.countries) <= set(v)]
	if regions and s.languages:
		names = ", ".join(geo.LANGUAGES[code]["name"] for code in s.languages if code in geo.LANGUAGES)
		return _("{0}-speaking {1}").format(names, regions[0].title())
	if regions:
		return regions[0].title()
	return ", ".join(s.countries[:4]) + ("…" if len(s.countries) > 4 else "")


def _channels_for(ctx) -> list[str]:
	"""Email and WhatsApp accounts the desk may use; an export desk prefers accounts named Export."""
	taken = {r[0] for r in frappe.db.sql("""SELECT c.channel_account FROM `tabExcom AI Channel` c
		JOIN `tabExcom AI Agent` a ON a.name = c.parent WHERE a.status IN ('Shadow', 'Live', 'On Hold')
		AND IFNULL(c.purpose, '') != 'Replies'""")}  # another agent's outreach account is not offered
	rows = [r for r in frappe.get_all("Excom Channel Account", {"name": ["in", ctx.accounts or ["-"]],
		"channel": ["in", ("email", "whatsapp")]}, ["name", "account_name"]) if r.name not in taken]
	if "export" in ctx.scope.concepts:
		named = [r.name for r in rows if "export" in (r.account_name or r.name).lower()]
		if named:
			return named
	return [r.name for r in rows][:4]


# ── tone from the desk's own words (§6.5) ────────────────────────────────────

GREETINGS = ("dear", "hi", "hello", "good morning", "hola", "estimado", "buenos", "namaste", "greetings")


def tone_for(ctx, langs: list[str]) -> dict:
	members = frappe.get_all("Excom Team Member", {"parent": ctx.team, "parenttype": "Excom Team"}, pluck="user") if ctx.team else []
	base = SALES_TONE + (" " + TONE_BY_LANG[langs[0]] if langs and langs[0] in TONE_BY_LANG else "")
	if not members or not ctx.accounts:
		return {"tone": base, "source": TYPE_DEFAULT}
	msgs = frappe.get_all("Excom Message", {"direction": "Outbound", "created_by_user": ["in", members],
		"account": ["in", ctx.accounts], "creation": [">=", add_months(nowdate(), -12)], "message_type": ["in", ("Text", "Email")]},
		["content_text"], order_by="creation desc", limit=50)
	texts = [m.content_text for m in msgs if (m.content_text or "").strip()]
	if len(texts) < 30:
		return {"tone": base, "source": _("{0}: the desk has only {1} recent messages, too few to learn from").format(
			TYPE_DEFAULT, len(texts))}
	words = sum(len(t.split()) for t in texts) / len(texts)
	greet = sum(1 for t in texts if t.strip().lower().startswith(GREETINGS)) / len(texts)
	emoji = sum(1 for t in texts if re.search(r"[\U0001F300-\U0001FAFF]", t)) / len(texts)
	traits = [
		_("short messages (about {0} words)").format(round(words)) if words < 40 else _("fuller messages (about {0} words)").format(round(words)),
		_("usually opens with a greeting") if greet > 0.5 else _("gets straight to the point"),
		_("uses emoji now and then") if emoji > 0.15 else _("no emoji"),
	]
	return {"tone": base + " " + _("Like the desk: {0}.").format(", ".join(traits)),
		"source": _("{0} outbound messages by the desk, last 12 months (traits only)").format(len(texts))}


# ── the AI's wording, when a provider is set up ──────────────────────────────

BRIEF_SCHEMA = {
	"type": "object",
	"properties": {k: {"type": "string"} for k in ("title", "what_it_is", "mission", "audience", "tone", "tone_examples")},
	"required": ["title", "what_it_is", "mission", "audience", "tone", "tone_examples"],
	"additionalProperties": False,
}


def ai_ready() -> bool:
	if "ai_interface" not in frappe.get_installed_apps():
		return False
	return bool(frappe.db.get_single_value("AI Settings", "default_provider"))


def ai_polish(d: dict, knowledge_md: str, keywords: str) -> dict:
	"""Let the model rewrite the draft's wording from the same facts. Sources stay: a field the
	model changed keeps its source and gains 'AI wording'. Any failure keeps the draft."""
	if not ai_ready():
		return d
	from ai_interface.services.chat import run

	from excom.excom_ai.engine.prompts import GLOBAL_RULES

	try:
		result = run(
			system=[{"text": GLOBAL_RULES + "\n\nYou draft the brief of a new AI sales colleague for a manager to review. "
				"Use only the facts given. Keep every number and name exactly as given. Write plainly; no marketing words.",
				"cache": True}],
			prompt=f"Keywords: {keywords}\n\nWhat the ERP shows:\n{knowledge_md[:12000]}\n\nThe draft to improve:\n"
				+ frappe.as_json({k: d["fields"].get(k) for k in BRIEF_SCHEMA["properties"]}),
			output_schema=BRIEF_SCHEMA, calling_app="excom", action="draft_brief", effort="medium",
			budget_scope="Excom AI Builder",
		)
		for k, v in (result.data or {}).items():
			if v and v != d["fields"].get(k):
				d["fields"][k] = v
				d["sources"][k] = f"{d['sources'].get(k, SUGGEST)}; AI wording"
	except Exception:
		frappe.log_error(title="Excom AI builder: brief wording failed", message=frappe.get_traceback())
	return d


def playbook_skeleton(agent_name: str, agent_type: str, language: str | None, sections: list[dict]) -> str:
	"""A Draft playbook: facts and terms filled from the ERP, the rest left for people to write."""
	by = {s["section"]: s for s in sections}
	filled = {
		"Facts": "\n".join(f"- {f['text']}" for f in (by.get("products") or {}).get("facts", [])),
		"Prices and terms": "\n".join(f"- {f['text']}" for k in ("prices", "terms") for f in (by.get(k) or {}).get("facts", [])),
	}
	doc = frappe.get_doc({"doctype": "Excom AI Playbook", "title": _("{0} playbook").format(agent_name),
		"agent_type": agent_type, "language": language if language and frappe.db.exists("Language", language) else None,
		"status": "Draft", "sections": [{"kind": k, "title": "", "content": filled.get(k, ""),
			"source": "ERP" if filled.get(k) else "Manual"} for k in PLAYBOOK_KINDS]})
	doc.insert(ignore_permissions=True)
	return doc.name
