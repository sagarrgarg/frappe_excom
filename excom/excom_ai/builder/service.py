"""The builder, end to end (HLD-007 §6.2): keywords → what the ERP shows → a Draft agent with its
brief, scope, channels, playbook skeleton, knowledge pack and readiness. Plus the nightly refresh
that keeps a working agent aligned (§6.6).
"""

from __future__ import annotations

import json

import frappe
from frappe import _

from excom.excom_ai.builder import brief, geo, knowledge, readiness, resolve


def pack_of(agent_type: str) -> str:
	return frappe.db.get_value("Excom AI Agent Type", agent_type, "pack") or "custom"


def preview(agent_type: str, keywords: str, rows: list | None = None, excluded: dict | None = None,
		team: str | None = None, company: str | None = None) -> dict:
	"""What the builder shows on step 3: the resolved keywords and one panel per knowledge section."""
	pack = pack_of(agent_type)
	rows = rows or resolve.resolve(keywords, pack)
	ctx = knowledge.context(rows, pack, company, team, excluded)
	sections = knowledge.build(ctx)
	return {"rows": rows, "scope": dict(ctx.scope), "company": ctx.company, "currency": ctx.currency,
		"sections": [{k: v for k, v in s.items() if k != "data"} | {"data": _visible(s)} for s in sections],
		"left_out": getattr(ctx.scope, "left_out", []) or [],
		# what each left-out country speaks, so the screen can offer "add Portuguese"
		"left_out_languages": {c: geo.languages_of(c) for c in getattr(ctx.scope, "left_out", []) or []}}


def _visible(section: dict) -> dict:
	"""The section's data for the screen: lists capped, nothing private past the person's own read."""
	out = {}
	for k, v in (section.get("data") or {}).items():
		out[k] = v[:50] if isinstance(v, list) else v
	return out


def create(agent_type: str, keywords: str, rows: list | None = None, excluded: dict | None = None, persona: str = "",
		owner: str | None = None, backup: str | None = None, team: str | None = None, company: str | None = None,
		use_ai: bool = True) -> str:
	"""Save the Draft agent. Returns its name."""
	pack = pack_of(agent_type)
	rows = rows or resolve.resolve(keywords, pack)
	ctx = knowledge.context(rows, pack, company, team, excluded)
	sections = knowledge.build(ctx)
	d = brief.draft(ctx, sections, persona)
	if use_ai:
		d = brief.ai_polish(d, knowledge.summary(sections), keywords)
	f = d["fields"]
	agent = frappe.get_doc({
		"doctype": "Excom AI Agent", "agent_type": agent_type, "status": "Draft", "company": ctx.company,
		"owner_user": owner or frappe.session.user, "backup_user": backup, "team": team,
		"keywords": ", ".join(r["keyword"] for r in rows),
		"keyword_rows": [_keyword_row(r) for r in rows],
		"scope_exclusions": json.dumps(excluded or {}),
		**{k: f.get(k) for k in ("agent_name", "title", "what_it_is", "mission", "audience", "tone", "tone_examples",
			"rules_do", "rules_dont")},
		"brief_sources": json.dumps(d["sources"]),
		"markets": d["markets"], "products": d["products"], "channels": d["channels"], "kpi_targets": d["kpi_targets"],
	})
	agent.insert(ignore_permissions=True)
	language = next((m["language"] for m in d["markets"] if m.get("language")), None)
	pb = brief.playbook_skeleton(agent.agent_name, agent_type, language, sections)
	agent.reload()
	agent.append("playbooks", {"playbook": pb})
	agent.save(ignore_permissions=True)
	knowledge.save(agent, sections)
	readiness.run(agent.name)
	if pack == "sales":
		from excom.excom_ai.writer import default_sequence

		default_sequence(frappe.get_doc("Excom AI Agent", agent.name))
	return agent.name


def _keyword_row(r: dict) -> dict:
	return {"keyword": r["keyword"], "resolved_as": r.get("resolved_as") or "Unmatched",
		"link_doctype": r.get("link_doctype"), "link_name": r.get("link_name"), "matches": r.get("matches") or 0,
		"recipe": r.get("recipe"), "included": 1 if r.get("included", 1) else 0}


def rows_of(agent) -> list[dict]:
	"""The agent's saved keywords, resolved again (a glossary entry or a region may have changed),
	keeping what the manager left out."""
	out = []
	for row in agent.keyword_rows or []:
		if row.resolved_as == "Vocabulary" and row.link_doctype and row.link_name:
			# What the manager confirmed for this agent stands (a proposal they accepted, a record they picked).
			r = resolve.resolve_one(row.keyword, pack_of(agent.agent_type))
			r.update(resolved_as="Vocabulary", link_doctype=row.link_doctype, link_name=row.link_name, concept=None,
				countries=[row.link_name] if row.link_doctype == "Country" else [], language=None)
		else:
			r = resolve.resolve_one(row.keyword, pack_of(agent.agent_type))
		r["included"] = row.included
		out.append(r)
	return out


def refresh(agent_name: str, notify_changes: bool = False) -> dict:
	"""Rebuild the knowledge pack and rerun readiness. Returns what changed."""
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	excluded = json.loads(agent.scope_exclusions or "{}") if agent.scope_exclusions else {}
	ctx = knowledge.context(rows_of(agent), pack_of(agent.agent_type), agent.company, agent.team, excluded,
		accounts=[r.channel_account for r in agent.channels] or None)
	sections = knowledge.build(ctx)
	name = knowledge.save(agent, sections)
	before = agent.readiness_state
	result = readiness.run(agent.name)
	from excom.excom_ai import monitor

	monitor.readiness_changed(agent.name, before, result["state"])
	changes = frappe.db.get_value("Excom AI Knowledge", name, "changes_since_last") or ""
	if notify_changes and changes:
		from excom.excom_ai.engine import notify

		notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user],
			_("{0} (AI): what it knows changed").format(agent.agent_name), message=frappe.utils.escape_html(changes),
			doctype="Excom AI Agent", name=agent.name)
	return {"knowledge": name, "changes": changes, **result}


def redraft(agent_name: str, use_ai: bool = True) -> dict:
	"""A fresh draft of the brief from the current knowledge (not saved: the screen shows it)."""
	agent = frappe.get_doc("Excom AI Agent", agent_name)
	excluded = json.loads(agent.scope_exclusions or "{}") if agent.scope_exclusions else {}
	ctx = knowledge.context(rows_of(agent), pack_of(agent.agent_type), agent.company, agent.team, excluded)
	sections = knowledge.build(ctx)
	d = brief.draft(ctx, sections, agent.agent_name)
	return brief.ai_polish(d, knowledge.summary(sections), agent.keywords or "") if use_ai else d


def nightly():
	"""Every night, for each working agent: rebuild what it knows, as its owner, and recheck
	readiness; the owner hears about changes (§6.6). Nothing while the master switch is off."""
	if not frappe.db.get_single_value("Excom AI Settings", "enabled"):
		return
	from excom.excom_ai.engine.identity import as_user

	for a in frappe.get_all("Excom AI Agent", {"status": ["in", ("Shadow", "Live", "On Hold")]}, ["name", "owner_user"]):
		try:
			with as_user(a.owner_user):
				refresh(a.name, notify_changes=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom AI: nightly refresh failed for {a.name}", message=frappe.get_traceback())


# ── keywords the ERP doesn't know (§6.4) ─────────────────────────────────────

RECIPE_SCHEMA = {
	"type": "object",
	"properties": {
		"meaning": {"type": "string"},
		"doctype": {"type": "string"},
		"filters": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "array"]}}},
		"fields": {"type": "array", "items": {"type": "string"}},
		"group_by": {"type": "string"},
		"order_by": {"type": "string"},
	},
	"required": ["meaning", "doctype", "filters", "fields", "group_by", "order_by"],
	"additionalProperties": False,
}


def schema_summary(max_fields: int = 25) -> str:
	"""The allowed doctypes and their plain fields, for the model to pick from."""
	from excom.excom_ai.doctype.excom_ai_recipe.excom_ai_recipe import ALLOWED

	lines = []
	for dt in sorted(ALLOWED):
		if not frappe.db.table_exists(dt) or not frappe.has_permission(dt, "read"):
			continue
		fields = [f"{df.fieldname} ({df.fieldtype})" for df in frappe.get_meta(dt).fields
			if df.fieldtype in ("Data", "Link", "Select", "Check", "Int", "Float", "Currency", "Date") and not df.hidden][:max_fields]
		lines.append(f"{dt}: {', '.join(fields)}")
	return "\n".join(lines)


def suggest_recipe(keyword: str, agent_type: str = "Sales") -> dict:
	"""Ask the model for a safe query that gives the keyword a meaning. Returns the proposal with a
	sample of what it finds; nothing is saved until a person accepts it."""
	from excom.excom_ai.builder.brief import ai_ready
	from excom.excom_ai.doctype.excom_ai_recipe.excom_ai_recipe import check_spec, run_spec

	if not ai_ready():
		return {"ok": False, "message": _("No AI provider is set up, so unknown keywords cannot be proposed yet.")}
	from ai_interface.services.chat import run

	result = run(
		system=[{"text": "You turn a sales manager's keyword into one read-only ERPNext query over the allowed doctypes. "
			"Use only listed doctypes and fields. Filters are [field, operator, value]. For counts use fields like "
			"\"count(name) as n\" with a group_by. Never guess a value that is not a plain word from the keyword.",
			"cache": True}],
		prompt=f"Agent type: {agent_type}\nKeyword: {keyword}\n\nAllowed doctypes and fields:\n{schema_summary()}",
		output_schema=RECIPE_SCHEMA, calling_app="excom", action="suggest_recipe", effort="low",
		budget_scope="Excom AI Builder",
	)
	d = result.data or {}
	spec = {k: d.get(k) for k in ("doctype", "filters", "fields", "group_by", "order_by") if d.get(k)}
	spec["limit"] = 20
	try:
		check_spec(spec)
		sample = run_spec(spec, limit=20)
	except Exception as e:
		return {"ok": False, "message": _("The proposal was not safe or did not run: {0}").format(e), "spec": spec}
	return {"ok": True, "keyword": keyword, "meaning": d.get("meaning"), "spec": spec, "sample": sample[:10],
		"count": len(sample)}


def accept_recipe(keyword: str, meaning: str, spec: dict, agent_type: str = "Sales") -> str:
	"""A person accepts a proposed recipe: it is saved so the next agent reuses it."""
	key = "custom." + resolve.singular(keyword).replace(" ", "_")[:60]
	doc = frappe.get_doc("Excom AI Recipe", {"recipe_key": key}) if frappe.db.exists("Excom AI Recipe", {"recipe_key": key}) \
		else frappe.new_doc("Excom AI Recipe")
	doc.update({"recipe_key": key, "label": meaning[:140] or keyword, "kind": "Query spec", "keywords": keyword,
		"agent_types": pack_of(agent_type), "query_spec": json.dumps(spec), "privacy": "Aggregates only", "enabled": 1})
	doc.save(ignore_permissions=True)
	return doc.name
