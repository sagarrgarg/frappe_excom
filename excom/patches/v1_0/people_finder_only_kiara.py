"""Kiara (AG-0138, GGIL's export Sales agent) emails only named people found by the People Finder.
The owner's decision, 2026-11-15. Other agents keep the box as it is (off unless someone ticks it)."""

import frappe


def execute():
	if not frappe.db.has_column("Excom AI Agent", "people_finder_only"):
		return
	if frappe.db.exists("Excom AI Agent", "AG-0138"):
		frappe.db.set_value("Excom AI Agent", "AG-0138", "people_finder_only", 1, update_modified=False)
