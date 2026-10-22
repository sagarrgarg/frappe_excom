"""Broadcast history is kept two years (720 days) in Log Settings.

A site whose daily clean-up already registered Excom Broadcast Event / Log
at the first default (180 days) is moved to 720; a retention someone set by
hand to anything else is left alone."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Logs To Clear"):
		return
	for doctype in ("Excom Broadcast Event", "Excom Broadcast Log"):
		frappe.db.set_value(
			"Logs To Clear",
			{"parent": "Log Settings", "ref_doctype": doctype, "days": 180},
			"days",
			720,
			update_modified=False,
		)
