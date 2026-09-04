"""Mode is now derived from the source type; fix rows saved before that rule existed."""

import frappe


def execute():
	from excom.excom.doctype.excom_source.excom_source import MODE_BY_TYPE
	for name, stype in frappe.get_all("Excom Source", fields=["name", "source_type"], as_list=True):
		frappe.db.set_value("Excom Source", name, "mode", MODE_BY_TYPE.get(stype, ""), update_modified=False)
	frappe.db.commit()
