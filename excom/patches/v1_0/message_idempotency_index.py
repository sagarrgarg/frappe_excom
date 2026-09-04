"""Inbound idempotency was a check-then-insert with no database constraint: two concurrent webhook
retries could both create the same message. Remove any duplicate that already exists, then let
on_doctype_update add the unique index."""

import frappe


def execute():
	dupes = frappe.db.sql(
		"""SELECT provider_message_id, COUNT(*) c FROM `tabExcom Message`
		   WHERE provider_message_id IS NOT NULL AND provider_message_id != ''
		   GROUP BY provider_message_id HAVING c > 1""",
		as_dict=True,
	)
	removed = 0
	for d in dupes:
		keep = frappe.db.get_value("Excom Message", {"provider_message_id": d.provider_message_id}, "name", order_by="creation asc")
		for name in frappe.get_all("Excom Message", filters={"provider_message_id": d.provider_message_id, "name": ["!=", keep]}, pluck="name"):
			frappe.db.delete("Excom Message", {"name": name})
			removed += 1
	# empty strings would collide under a unique index; NULL is the right "no provider id"
	frappe.db.sql("UPDATE `tabExcom Message` SET provider_message_id = NULL WHERE provider_message_id = ''")
	frappe.db.commit()
	# the old non-unique index has to go before the unique one can be added
	if frappe.db.sql("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='tabExcom Message' AND index_name='provider_message_id_index'"):
		frappe.db.sql("ALTER TABLE `tabExcom Message` DROP INDEX `provider_message_id_index`")
	frappe.reload_doc("excom", "doctype", "excom_message")
	frappe.db.commit()
	print(f"duplicate provider messages removed: {removed}")
