"""The knowledge pack: what the ERP shows for an agent's keywords (HLD-007 §6.3, §6.6).

`context()` turns keywords (resolved rows) into the scope the recipes read; `build()` runs the
pack's recipes and returns the sections; `save()` stores them as a new Excom AI Knowledge version
with a Markdown summary (what may reach prompts, by each section's privacy) and what changed since
the last version, which is what the nightly drift check reports.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from excom.excom_ai.builder import recipes, resolve


def default_company() -> str:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company") \
		or frappe.db.get_value("Company", {}, "name")


def team_accounts(team: str | None) -> list[str]:
	"""Numbers and mailboxes a team may work on (an account with no team rows is open to everyone)."""
	accounts = frappe.get_all("Excom Channel Account", {"status": "Active"}, ["name", "channel"])
	restricted = {}
	for r in frappe.get_all("Excom Account Team", {"parenttype": "Excom Channel Account"}, ["parent", "team"]):
		restricted.setdefault(r.parent, set()).add(r.team)
	out = []
	for a in accounts:
		if a.channel not in ("email", "whatsapp", "instagram", "messenger"):
			continue
		teams = restricted.get(a.name)
		if not teams or (team and team in teams):
			out.append(a.name)
	return out


def context(rows: list[dict], pack: str = "sales", company: str | None = None, team: str | None = None,
		excluded: dict | None = None, accounts: list[str] | None = None) -> frappe._dict:
	scope = resolve.scope_from(rows)
	company = company or default_company()
	groups = []
	for g in scope.item_groups:
		groups += [x for x in resolve.item_group_tree(g) if x not in groups]
	if team and team not in scope.teams:
		scope.teams.append(team)
	return frappe._dict(scope=scope, pack=pack, company=company,
		currency=frappe.db.get_value("Company", company, "default_currency") or "INR", groups=groups,
		excluded=excluded or {}, accounts=accounts if accounts is not None else (scope.accounts or team_accounts(team)),
		item_codes=[], team=team)


def build(ctx) -> list[dict]:
	sections = []
	for key in recipes.for_pack(ctx.pack):
		if not _recipe_enabled(key):
			continue
		section = recipes.run(key, ctx)
		if key == "products":
			ctx.item_codes = section["data"].get("items") or []
		sections.append(section)
	for name in ctx.scope.get("recipes") or []:
		sections.append(_custom(name))
	return sections


def _custom(name: str) -> dict:
	"""A recipe a person accepted for a keyword (§6.4): its rows, as counts and names."""
	from excom.excom_ai.doctype.excom_ai_recipe.excom_ai_recipe import run_spec

	doc = frappe.get_doc("Excom AI Recipe", name)
	out = {"section": f"recipe:{name}", "title": doc.label, "privacy": "aggregates", "facts": [], "data": {}}
	try:
		rows = run_spec(doc.spec(), limit=200)
	except Exception as e:
		out["facts"] = [recipes.fact(_("Could not run: {0}").format(e), doc.label, level="warning")]
		return out
	names = [str(r.get("name") or next(iter(r.values()), "")) for r in rows]
	out["facts"] = [recipes.fact(_("{0} records").format(len(rows)), _("recipe {0}").format(doc.recipe_key),
		doc.spec().get("doctype"), names)]
	out["data"] = {"rows": [dict(r) for r in rows[:50]], "count": len(rows)}
	return out


def _recipe_enabled(key: str) -> bool:
	enabled = frappe.db.get_value("Excom AI Recipe", {"recipe_key": f"sales.{key}"}, "enabled")
	return enabled is None or bool(enabled)


def summary(sections: list[dict]) -> str:
	"""Markdown of the facts that may reach a prompt: names only where the section allows them."""
	lines = []
	for s in sections:
		if s.get("privacy") == "none" or s.get("skipped"):
			continue
		lines.append(f"## {s['title']}")
		for f in s["facts"]:
			lines.append(f"- {f['text']} _(source: {f['source']})_")
	return "\n".join(lines)


def save(agent, sections: list[dict], recipes_used: list[str] | None = None) -> str:
	prev = frappe.get_all("Excom AI Knowledge", {"agent": agent.name}, ["name", "version", "facts"],
		order_by="version desc", limit=1)
	version = cint(prev[0].version) + 1 if prev else 1
	text = summary(sections)
	facts = {s["section"]: {"title": s["title"], "facts": s["facts"], "data": s["data"], "privacy": s["privacy"]}
		for s in sections}
	changes = diff(json.loads(prev[0].facts or "{}") if prev else {}, facts) if prev else []
	doc = frappe.get_doc({
		"doctype": "Excom AI Knowledge", "agent": agent.name, "version": version, "generated_on": now_datetime(),
		"approx_tokens": len(text) // 4, "recipes_used": json.dumps(recipes_used or list(facts)),
		"changes_since_last": "\n".join(changes)[:5000], "summary": text, "facts": json.dumps(facts, default=str),
	}).insert(ignore_permissions=True)
	frappe.db.set_value("Excom AI Agent", agent.name, "knowledge", doc.name, update_modified=False)
	return doc.name


# The changes that matter to an agent (§6.6), read from each section's data.
WATCH = [
	("prices", "lists", _("price lists")),
	("customers", "customers", _("customers in scope")),
	("products", "ready", _("items ready to offer (photo, weight, HS code)")),
	("products", "count", _("items in scope")),
	("logistics", "forwarders", _("forwarders and customs agents")),
	("terms", "payment_terms", _("payment terms templates")),
]


def diff(old: dict, new: dict) -> list[str]:
	out = []
	for section, key, label in WATCH:
		a = ((old.get(section) or {}).get("data") or {}).get(key)
		b = ((new.get(section) or {}).get("data") or {}).get(key)
		if a == b or (a is None and b is None):
			continue
		if isinstance(a, list) or isinstance(b, list):
			added = [x for x in (b or []) if x not in (a or [])]
			removed = [x for x in (a or []) if x not in (b or [])]
			if added:
				out.append(_("{0}: {1} new ({2})").format(label, len(added), ", ".join(map(_short, added[:5]))))
			if removed:
				out.append(_("{0}: {1} gone ({2})").format(label, len(removed), ", ".join(map(_short, removed[:5]))))
		else:
			out.append(_("{0}: {1} → {2}").format(label, a, b))
	return out


def _short(x) -> str:
	if isinstance(x, dict):
		return str(x.get("price_list") or x.get("name") or x)
	return str(x)
