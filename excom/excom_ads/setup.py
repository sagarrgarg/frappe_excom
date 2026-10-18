"""Excom Ads seed data. Runs after install and after every migrate; idempotent."""

import frappe

from excom.excom_ads.permissions import MARKETING_ROLE


def after_install():
	seed()


def after_migrate():
	seed()


def seed():
	# Marketing staff read ads data inside the scope their User Permissions give them. It is not an
	# inbox role: Excom Agent and Excom Admin keep their meaning.
	if not frappe.db.exists("Role", MARKETING_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": MARKETING_ROLE, "desk_access": 1}).insert(ignore_permissions=True)
