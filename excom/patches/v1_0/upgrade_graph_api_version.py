"""Stored Graph API versions older than v24.0 become v26.0.

Graph v21.0 stops working on 2027-01-21, and v24.0 changed the WhatsApp webhooks excom reads
(status `pricing` instead of `conversation`). Calls already use v26.0 for any older stored
value (excom.excom.utils.graph.graph_version); this makes the saved setting say the same.
A version someone set to v24.0 or later is left alone.
"""

import frappe

from excom.excom.utils.graph import DEFAULT_GRAPH_VERSION, is_outdated

_FIELDS = (
	("Excom Channel Account", "wa_version"),
	("Excom Channel Account", "meta_api_version"),
	("Excom Meta Connection", "api_version"),
)


def execute():
	for doctype, fieldname in _FIELDS:
		if not frappe.db.exists("DocType", doctype) or not frappe.get_meta(doctype).has_field(fieldname):
			continue
		for row in frappe.get_all(doctype, fields=["name", fieldname]):
			value = row.get(fieldname)
			# only rows that use this field: a WhatsApp account has no Meta DM version and vice versa
			if value and is_outdated(value):
				frappe.db.set_value(doctype, row.name, fieldname, DEFAULT_GRAPH_VERSION, update_modified=False)
