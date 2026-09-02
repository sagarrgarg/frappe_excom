import frappe

def execute():
	if frappe.db.exists("Excom Channel", "voice"):
		return

	was_in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = True
	try:
		doc = frappe.get_doc({
			"doctype": "Excom Channel",
			"__newname": "voice",
			"channel_label": "Calls",
			"allows_multiple_accounts": 1,
			"is_enabled": 1,
			"description": "Voice Telephony channel (Calls) with dynamic routing and recording.",
		})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
	finally:
		frappe.flags.in_migrate = was_in_migrate