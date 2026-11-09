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
	schema.apply()


def _roles():
	for role in constants.PACK_ROLES.values():
		if not frappe.db.exists("Role", role):
			# Held only by agents' own users: no desk of its own.
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 0}).insert(ignore_permissions=True)


def _types():
	for name, pack, description, colour in constants.TYPES:
		if frappe.db.exists("Excom AI Agent Type", name):
			continue
		doc = frappe.get_doc({"doctype": "Excom AI Agent Type", "type_name": name, "pack": pack,
			"description": description, "colour": colour})
		for action, mode in constants.DEFAULT_POLICIES[pack]:
			row = {"action": action, "mode": mode}
			if action == "reply" and pack == "sales":
				row["condition"] = constants.SAFE_REPLY_INTENTS
			doc.append("default_policies", row)
		doc.insert(ignore_permissions=True)


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
