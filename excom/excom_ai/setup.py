"""Excom AI seeds. After install and after every migrate; idempotent; never overwrites edits.

Creates the pack roles, the five agent types with their default policies, the trade signals
(Proposed: a person confirms what agents may collect), the tools (disabled, no keys), and the
fields on native records. The master switch is not touched: it starts off.
"""

import frappe

from excom.excom_ai import constants, schema


def after_install():
	seed()


def after_migrate():
	seed()


def seed():
	if not frappe.db.table_exists("Excom AI Agent Type"):
		return
	_roles()
	_types()
	_signals()
	_tools()
	_cap_custom()
	_pack_permissions()
	_builder()
	# The unique open-task key (a generated column Frappe does not manage): checked every migrate,
	# since the schema sync drops indexes it did not create.
	from excom.excom_ai.doctype.excom_ai_task.excom_ai_task import ensure_open_key

	ensure_open_key()
	from excom.excom_ai import evals

	evals.seed()
	schema.apply()
	_sync_retention()
	_snapshot_playbooks()
	_template_defaults()


TEMPLATE_DEFAULTS = {"templates_enabled": 1, "whatsapp_auto_submit": 1, "template_first_use_review": 1,
	"template_submissions_per_day": 10, "template_max_attempts": 2,
	# HLD-009, the people finder
	"people_per_company": 2, "people_on_demand": 1, "people_daily_per_user": 25,
	"people_titles": "purchase, purchasing, procurement, buyer, buying, import, sourcing, category manager, compras, "
		"importaciones, owner, founder, managing director, general manager, gerente general",
	"people_exclude_titles": "marketing, human resources, hr, recruit, talent, software, engineer, developer, "
		"it manager, product owner, legal, intern, student, assistant"}


def _template_defaults():
	"""HLD-008's settings get their defaults once: Frappe never writes a JSON default into a Single
	that already exists, so they would read 0 (steward off). A value a person saved is kept."""
	if not frappe.get_meta("Excom AI Settings").has_field("templates_enabled"):
		return
	saved = set(frappe.db.sql_list("""SELECT field FROM `tabSingles` WHERE doctype = 'Excom AI Settings'
		AND field IN %s""", (tuple(TEMPLATE_DEFAULTS),)))
	missing = {k: v for k, v in TEMPLATE_DEFAULTS.items() if k not in saved}
	if missing:
		frappe.db.set_single_value("Excom AI Settings", missing)


def _snapshot_playbooks():
	"""Approved playbooks from before the snapshot existed get one, so a later edit keeps them in use."""
	import json

	if not frappe.db.has_column("Excom AI Playbook", "approved_sections"):
		return
	for pb in frappe.get_all("Excom AI Playbook", {"status": "Approved", "approved_sections": ["is", "not set"]},
			["name", "version"]):
		rows = frappe.get_all("Excom AI Playbook Section", {"parent": pb.name}, ["kind", "title", "content"], order_by="idx")
		frappe.db.set_value("Excom AI Playbook", pb.name, {"approved_version": pb.version,
			"approved_sections": json.dumps(rows, ensure_ascii=False, default=str)}, update_modified=False)


def _roles():
	for role in constants.PACK_ROLES.values():
		if not frappe.db.exists("Role", role):
			# Held only by agents' own users: no desk of its own.
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0}).insert(ignore_permissions=True)


def _types():
	for name, pack, description, colour in constants.TYPES:
		if frappe.db.exists("Excom AI Agent Type", name):
			# a policy a later version added is appended; rows a person set are never changed
			doc = frappe.get_doc("Excom AI Agent Type", name)
			have = {r.action for r in doc.default_policies}
			missing = [(a, m) for a, m in constants.DEFAULT_POLICIES[pack] if a not in have]
			for action, mode in missing:
				doc.append("default_policies", {"action": action, "mode": mode})
			if missing:
				doc.save(ignore_permissions=True)
			continue
		doc = frappe.get_doc({"doctype": "Excom AI Agent Type", "type_name": name, "pack": pack,
			"description": description, "colour": colour})
		for action, mode in constants.DEFAULT_POLICIES[pack]:
			row = {"action": action, "mode": mode}
			if action == "reply" and pack == "sales":
				row["condition"] = constants.SAFE_REPLY_INTENTS
			doc.append("default_policies", row)
		doc.insert(ignore_permissions=True)


def _pack_permissions():
	"""Give each pack role read (and draft-write) rights on the records its work needs, as Custom
	DocPerms for that role only. Never submit, cancel or delete."""
	from frappe.permissions import add_permission, update_permission_property

	for pack, rights in constants.PACK_PERMISSIONS.items():
		role = constants.PACK_ROLES[pack]
		if not frappe.db.exists("Role", role):
			continue
		for ptype, doctypes in rights.items():
			for doctype in doctypes:
				if not frappe.db.exists("DocType", doctype):
					continue
				if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
					add_permission(doctype, role, 0)
				name = frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0})
				wanted = {"read": {"read": 1}, "select": {"select": 1}}.get(ptype, {"read": 1, "write": 1, "create": 1})
				for prop, value in wanted.items():
					if not frappe.db.get_value("Custom DocPerm", name, prop):
						update_permission_property(doctype, role, 0, prop, value)


def _cap_custom():
	"""Custom agents work in Review at most: lower any row an earlier seed or edit left at Auto."""
	for t in frappe.get_all("Excom AI Agent Type", {"pack": "custom"}, pluck="name"):
		frappe.db.sql(
			"""UPDATE `tabExcom AI Policy` SET mode = 'Review'
			WHERE parenttype = 'Excom AI Agent Type' AND parent = %s AND mode = 'Auto'""", t)
		for agent in frappe.get_all("Excom AI Agent", {"agent_type": t}, pluck="name"):
			frappe.db.sql(
				"""UPDATE `tabExcom AI Policy` SET mode = 'Review'
				WHERE parenttype = 'Excom AI Agent' AND parent = %s AND mode = 'Auto'""", agent)


def _builder():
	"""The Sales readiness rules and the built-in recipes, as records people can see and switch off."""
	from excom.excom_ai.builder import readiness, recipes

	readiness.seed("Sales")
	from excom.excom_ai.engine import registry

	registry.load()  # pack modules register their recipes and readiness checks
	for module in registry.MODULES:
		try:
			m = frappe.get_module(module)
		except ModuleNotFoundError:
			continue
		if hasattr(m, "seed"):
			m.seed()  # a pack's own readiness rules and records
	for key, r in recipes.RECIPES.items():
		rk = f"{r['packs'][0]}.{key}"
		handler = f"{r['fn'].__module__}.{r['fn'].__name__}"
		existing = frappe.db.get_value("Excom AI Recipe", {"recipe_key": rk}, ["name", "handler"], as_dict=True)
		if existing:
			if existing.handler != handler:  # recorded under the builder's module before packs had their own
				frappe.db.set_value("Excom AI Recipe", existing.name, "handler", handler, update_modified=False)
			continue
		frappe.get_doc({"doctype": "Excom AI Recipe", "recipe_key": rk, "label": r["title"], "kind": "Built-in",
			"agent_types": ", ".join(r["packs"]), "section": r["title"], "handler": handler,
			"privacy": {"names": "Names allowed", "none": "Aggregates only"}.get(r["privacy"], "Aggregates only"),
			"enabled": 1}).insert(ignore_permissions=True)


def _sync_retention():
	from excom.excom_ai.doctype.excom_ai_settings.excom_ai_settings import _sync_ai_interface_policy

	_sync_ai_interface_policy(frappe.db.get_single_value("Excom AI Settings", "prompt_retention_days") or 0)


def _signals():
	for code, label, category, value_type, collector in constants.SIGNALS:
		if frappe.db.exists("Excom AI Signal Code", code):
			continue
		frappe.get_doc({"doctype": "Excom AI Signal Code", "code": code, "label": label, "category": category,
			"value_type": value_type, "collector": collector, "status": "Proposed",
			"cost_note": "paid" if code in constants.PAID_SIGNALS else "free"}).insert(ignore_permissions=True)


def _tools():
	for key, label, tier, note in constants.TOOLS:
		if frappe.db.exists("Excom AI Tool", key):
			continue
		frappe.get_doc({"doctype": "Excom AI Tool", "tool_key": key, "label": label, "tier": tier,
			"enabled": 0, "notes": note}).insert(ignore_permissions=True)
