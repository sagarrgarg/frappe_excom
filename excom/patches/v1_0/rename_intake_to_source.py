"""Excom Intake Source / Log / Field Map → Excom Source / Source Log / Source Field Map (one word: Source).
rename_doc on DocType moves the table and rewrites every Link/Table option that pointed at the old name."""

import frappe

RENAMES = [("Excom Intake Field Map", "Excom Source Field Map"), ("Excom Intake Log", "Excom Source Log"), ("Excom Intake Source", "Excom Source")]


def execute():
	for old, new in RENAMES:
		if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
			frappe.rename_doc("DocType", old, new, force=True)
			frappe.db.commit()
	frappe.clear_cache()
