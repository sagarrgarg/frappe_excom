"""Endpoints for the builder and the Agents screens (HLD-007 §6.2, §15).

Who may do what:
- build an agent: an Excom Admin, a System Manager, or the manager of an Excom team;
- see an agent: Excom AI permissions (its people and its team, excom_ai/permissions.py);
- change an agent: its owner, its backup, its team's manager, or an Excom Admin. The agent record
  itself is read-only for people; these endpoints check first and then save through its controller,
  so every rule of the record still holds.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_first_day, nowdate

from excom.excom_ai.builder import readiness, resolve, service
from excom.excom_ai.constants import CEILINGS, MODES, ceiling

MANAGERS = {"Excom Admin", "System Manager"}
BRIEF = ("agent_name", "title", "what_it_is", "mission", "audience", "tone", "tone_examples", "rules_do", "rules_dont")


def _roles() -> set:
	return set(frappe.get_roles())


def _team_manager(team: str | None = None) -> bool:
	filters = {"parenttype": "Excom Team", "user": frappe.session.user, "role": "Manager"}
	if team:
		filters["parent"] = team
	return bool(frappe.db.exists("Excom Team Member", filters))


def _can_build():
	if not (_roles() & MANAGERS or _team_manager()):
		frappe.throw(_("Only an Excom Admin or a team's manager builds agents."), frappe.PermissionError)


def _readable(name: str):
	doc = frappe.get_doc("Excom AI Agent", name)
	frappe.has_permission("Excom AI Agent", "read", doc, throw=True)
	return doc


def _changeable(name: str):
	doc = _readable(name)
	user = frappe.session.user
	if user in (doc.owner_user, doc.backup_user) or _roles() & MANAGERS or _team_manager(doc.team):
		return doc
	frappe.throw(_("Only the agent's owner, backup, its team's manager or an Excom Admin can change it."), frappe.PermissionError)


def _json(v, default=None):
	if v is None or v == "":
		return default
	return frappe.parse_json(v) if isinstance(v, str) else v


# ── building ─────────────────────────────────────────────────────────────────


@frappe.whitelist()
def types() -> list:
	_can_build()
	return frappe.get_all("Excom AI Agent Type", {"enabled": 1}, ["name", "pack", "description", "colour", "icon"],
		order_by="creation asc")


@frappe.whitelist()
def resolve_keywords(agent_type: str, keywords: str) -> list:
	_can_build()
	return resolve.resolve(keywords, service.pack_of(agent_type))


@frappe.whitelist(methods=["POST"])
def confirm_glossary(term: str, link_doctype: str, link_name: str, language: str | None = None) -> str:
	"""A person confirms what a word means here; every later agent reuses it."""
	_can_build()
	if link_doctype not in {d for d, _f in resolve.VOCABULARY} | {"Item"}:
		frappe.throw(_("A glossary entry names an item group, item, brand, group, territory or price list."))
	if not frappe.db.exists(link_doctype, link_name):
		frappe.throw(_("{0} {1} does not exist.").format(link_doctype, link_name))
	term = resolve.singular(term)
	existing = frappe.db.get_value("Excom AI Glossary", {"term": term, "link_doctype": link_doctype, "link_name": link_name})
	doc = frappe.get_doc("Excom AI Glossary", existing) if existing else frappe.new_doc("Excom AI Glossary")
	doc.update({"term": term, "link_doctype": link_doctype, "link_name": link_name, "language": language,
		"confirmed_by": frappe.session.user})
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def preview(agent_type: str, keywords: str, rows=None, excluded=None, team: str | None = None, company: str | None = None):
	_can_build()
	return service.preview(agent_type, keywords, _json(rows), _json(excluded, {}), team, company)


@frappe.whitelist(methods=["POST"])
def create_agent(agent_type: str, keywords: str, rows=None, excluded=None, persona: str = "", backup: str | None = None,
		team: str | None = None, company: str | None = None, owner: str | None = None) -> dict:
	_can_build()
	if owner and owner != frappe.session.user and not _roles() & MANAGERS and not _team_manager(team):
		frappe.throw(_("Only an Excom Admin or the team's manager names someone else as the owner."), frappe.PermissionError)
	name = service.create(agent_type, keywords, _json(rows), _json(excluded, {}), persona, owner, backup or None,
		team or None, company or None)
	frappe.db.commit()
	return {"name": name}


# ── the Agents home and page ─────────────────────────────────────────────────


def _can_see_agents():
	if not _roles() & {"Excom Agent", "Excom Admin", "System Manager"}:
		frappe.throw(_("Agents are for Excom users."), frappe.PermissionError)


@frappe.whitelist()
def agents() -> list:
	"""One card per agent the person may see, with today's numbers."""
	_can_see_agents()
	rows = frappe.get_list("Excom AI Agent", fields=["name", "agent_name", "title", "agent_type", "status", "owner_user",
		"acting_owner", "readiness_state", "consecutive_failures", "avatar", "team"], order_by="modified desc", limit_page_length=200)
	today = get_datetime(nowdate())
	colours = dict(frappe.get_all("Excom AI Agent Type", fields=["name", "colour"], as_list=True))
	for r in rows:
		r.colour = colours.get(r.agent_type)
		r.owner_name = frappe.db.get_value("User", r.owner_user, "full_name")
		r.waiting = frappe.db.count("Excom AI Proposal", {"agent": r.name, "status": "Pending"})
		r.sent_today = frappe.db.count("Excom AI Proposal", {"agent": r.name, "status": ["in", ("Sent", "Done")],
			"modified": [">=", today]})
		r.replies_today = frappe.db.count("Excom AI Task", {"agent": r.name, "kind": "inbound", "creation": [">=", today]})
		r.health = ("red" if r.status == "On Hold" or cint(r.consecutive_failures) or r.readiness_state == "Blocked"
			else "amber" if r.readiness_state in ("Warnings", "Not checked") else "green")
	return rows


@frappe.whitelist()
def agent(name: str) -> dict:
	"""Everything the agent page shows, tab by tab."""
	doc = _readable(name)
	user = frappe.session.user
	k = frappe.db.get_value("Excom AI Knowledge", doc.knowledge, ["name", "version", "generated_on", "summary",
		"changes_since_last", "facts", "approx_tokens"], as_dict=True) if doc.knowledge else None
	knowledge = None
	if k:
		facts = json.loads(k.facts or "{}")
		knowledge = {"name": k.name, "version": k.version, "generated_on": k.generated_on, "changes": k.changes_since_last,
			"approx_tokens": k.approx_tokens,
			"sections": [{"section": key, "title": s.get("title"), "facts": s.get("facts"), "privacy": s.get("privacy")}
				for key, s in facts.items()]}
	gaps = frappe.get_all("Excom AI Readiness Gap", {"agent": name}, ["name", "rule", "severity", "status", "detail", "todo",
		"waived_by", "waive_reason", "checked_on"])
	rank = {"Blocker": 0, "Warning": 1, "Info": 2}
	gaps.sort(key=lambda g: (g.status != "Open", rank.get(g.severity, 3)))
	rules = {r.name: r for r in frappe.get_all("Excom AI Readiness Rule", fields=["name", "title", "fix_hint", "fix_route"])}
	for g in gaps:
		r = rules.get(g.rule) or {}
		g.update({"title": r.get("title"), "fix_hint": r.get("fix_hint"), "fix_route": r.get("fix_route")})
	pack = service.pack_of(doc.agent_type)
	month = get_datetime(get_first_day(nowdate()))
	usage = {"today": 0, "month": 0, "calls_month": 0}
	if frappe.db.table_exists("AI Call Log"):
		scope = f"Excom AI Agent:{name}"
		for key, since in (("today", get_datetime(nowdate())), ("month", month)):
			usage[key] = flt(frappe.db.sql("SELECT SUM(base_cost) FROM `tabAI Call Log` WHERE budget_scope = %s AND creation >= %s",
				(scope, since))[0][0])
		usage["calls_month"] = frappe.db.count("AI Call Log", {"budget_scope": scope, "creation": [">=", month]})
	return {
		"agent": {f: doc.get(f) for f in ("name", "agent_name", "title", "agent_type", "company", "status", "hold_reason",
			"service_user", "avatar", "owner_user", "backup_user", "team", "escalation_user", "reviewers", "acting_owner",
			"owner_last_seen", "keywords", "brief_version", "brief_approved_by", "brief_approved_on", "readiness_state",
			"readiness_checked_on", "daily_ai_budget", "monthly_ai_budget", "office_hours", "model", *BRIEF)},
		"pack": pack,
		"people": {u: frappe.db.get_value("User", u, "full_name") or u for u in
			{doc.owner_user, doc.backup_user, doc.acting_owner, doc.escalation_user, doc.brief_approved_by} if u},
		"brief_sources": json.loads(doc.brief_sources or "{}") if doc.brief_sources else {},
		"keyword_rows": [r.as_dict() for r in doc.keyword_rows],
		"markets": [r.as_dict() for r in doc.markets], "products": [r.as_dict() for r in doc.products],
		"channels": [r.as_dict() | {"channel": frappe.db.get_value("Excom Channel Account", r.channel_account, "channel")}
			for r in doc.channels],
		"kpi_targets": [r.as_dict() for r in doc.kpi_targets],
		"policies": [r.as_dict() | {"ceiling": ceiling(r.action, pack, r.condition), "hard_ceiling": CEILINGS.get(r.action, "Review")}
			for r in doc.policies],
		"playbooks": [frappe.db.get_value("Excom AI Playbook", r.playbook, ["name", "title", "status", "version"], as_dict=True)
			for r in doc.playbooks],
		"knowledge": knowledge,
		"gaps": gaps,
		"needs_you": frappe.get_all("Excom AI Proposal", {"agent": name, "status": ["in", ("Pending", "Held")]},
			["name", "action", "status", "risk", "subject_doctype", "subject_name", "thread", "preview_html", "creation",
				"hold_reason"], order_by="creation desc", limit=50),
		"activity": frappe.get_all("Excom AI Task", {"agent": name}, ["name", "kind", "status", "outcome", "error",
			"creation", "ended_on", "ai_cost"], order_by="creation desc", limit=50),
		"usage": usage,
		"can_change": user in (doc.owner_user, doc.backup_user) or bool(_roles() & MANAGERS) or _team_manager(doc.team),
		"modes": MODES,
	}


# ── changing an agent ────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def save_brief(name: str, fields) -> dict:
	"""Edit the brief. A change clears its approval (and a Live agent goes back to Shadow)."""
	doc = _changeable(name)
	fields = _json(fields, {})
	for k, v in fields.items():
		if k in BRIEF:
			doc.set(k, v)
			sources = json.loads(doc.brief_sources or "{}") if doc.brief_sources else {}
			sources[k] = _("edited by {0}").format(frappe.session.user)
			doc.brief_sources = json.dumps(sources)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"status": doc.status, "brief_approved_on": doc.brief_approved_on}


@frappe.whitelist(methods=["POST"])
def set_people(name: str, owner_user: str | None = None, backup_user: str | None = None, team: str | None = None,
		escalation_user: str | None = None, reviewers: str | None = None) -> dict:
	doc = _changeable(name)
	if ((owner_user and owner_user != doc.owner_user) or (team and team != doc.team)) and not (_roles() & MANAGERS or _team_manager(doc.team)):
		frappe.throw(_("Only the team's manager or an Excom Admin changes an agent's owner or team."), frappe.PermissionError)
	for k, v in (("owner_user", owner_user), ("backup_user", backup_user), ("team", team), ("escalation_user", escalation_user),
			("reviewers", reviewers)):
		if v is not None:
			doc.set(k, v or None)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def set_limits(name: str, daily_ai_budget=None, monthly_ai_budget=None, office_hours: str | None = None,
		channels=None) -> dict:
	doc = _changeable(name)
	if daily_ai_budget is not None:
		doc.daily_ai_budget = flt(daily_ai_budget)
	if monthly_ai_budget is not None:
		doc.monthly_ai_budget = flt(monthly_ai_budget)
	if office_hours:
		doc.office_hours = office_hours
	if channels is not None:
		doc.set("channels", [])
		for r in _json(channels, []):
			doc.append("channels", {k: r.get(k) for k in ("channel_account", "purpose", "daily_cap", "warmup_start",
				"signature_mode", "paused")})
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def set_policy(name: str, action: str, mode: str, sample_percent=None, daily_cap=None, condition: str | None = None) -> dict:
	"""Autonomy: one action's mode, within its ceiling (the record refuses anything above it)."""
	doc = _changeable(name)
	row = next((p for p in doc.policies if p.action == action), None)
	if not row:
		row = doc.append("policies", {"action": action})
	row.mode = mode
	if sample_percent is not None:
		row.sample_percent = flt(sample_percent)
	if daily_cap is not None:
		row.daily_cap = cint(daily_cap)
	if condition is not None:
		row.condition = condition
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True}


TRANSITIONS = {"Shadow": ("Draft", "Preparing", "Live"), "Live": ("Shadow",), "Retired": ("Draft", "Preparing", "Shadow", "Live", "On Hold")}


@frappe.whitelist(methods=["POST"])
def set_status(name: str, status: str) -> dict:
	"""Start Shadow, go Live, back to Shadow, or retire. Stop and resume are excom_ai.api."""
	doc = _changeable(name)
	if doc.status not in TRANSITIONS.get(status, ()):
		frappe.throw(_("{0} cannot go from {1} to {2}.").format(doc.agent_name, doc.status, status))
	if status == "Live":
		result = readiness.run(doc.name)
		doc.reload()
		if result["state"] == "Blocked":
			frappe.throw(_("{0} readiness blockers are open: fix or waive them first.").format(result["blockers"]))
	doc.status = status
	doc.save(ignore_permissions=True)
	if status == "Shadow":
		from excom.excom_ai.engine import ownership

		ownership.evaluate(frappe.get_doc("Excom AI Agent", doc.name))
	frappe.db.commit()
	return {"status": frappe.db.get_value("Excom AI Agent", name, "status")}


@frappe.whitelist(methods=["POST"])
def refresh(name: str) -> dict:
	_changeable(name)
	out = service.refresh(name)
	frappe.db.commit()
	return out


@frappe.whitelist(methods=["POST"])
def run_readiness(name: str) -> dict:
	_changeable(name)
	out = readiness.run(name)
	frappe.db.commit()
	return out


@frappe.whitelist()
def redraft(name: str) -> dict:
	_changeable(name)
	return service.redraft(name)


@frappe.whitelist(methods=["POST"])
def gap_todo(gap: str, user: str) -> dict:
	"""Turn a readiness gap into a ToDo for a named person."""
	agent_name = frappe.db.get_value("Excom AI Readiness Gap", gap, "agent")
	_changeable(agent_name)
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} is not an active user.").format(user))
	todo = readiness.gap_to_todo(gap, user)
	frappe.db.commit()
	return {"todo": todo}


@frappe.whitelist(methods=["POST"])
def waive_gap(gap: str, reason: str) -> dict:
	"""Waive a gap with a reason; a blocker needs an Excom Admin (the gap record enforces it)."""
	doc = frappe.get_doc("Excom AI Readiness Gap", gap)
	_changeable(doc.agent)
	doc.status, doc.waive_reason = "Waived", reason
	doc.save(ignore_permissions=True)
	readiness.run(doc.agent)
	frappe.db.commit()
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def suggest_recipe(keyword: str, agent_type: str = "Sales") -> dict:
	"""§6.4: the AI proposes a safe query for a keyword the ERP doesn't know; nothing is saved."""
	_can_build()
	return service.suggest_recipe(keyword, agent_type)


@frappe.whitelist(methods=["POST"])
def accept_recipe(keyword: str, meaning: str, spec, agent_type: str = "Sales") -> dict:
	_can_build()
	return {"name": service.accept_recipe(keyword, meaning, _json(spec, {}), agent_type)}
