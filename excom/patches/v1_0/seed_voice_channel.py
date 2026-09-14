import frappe


def execute():
	"""Bring the voice channel online on an existing site.

	Three things, all idempotent, so re-running a migrate is free:
	  - the `voice` Excom Channel row, without which no voice account can be created
	  - the `Call` option on Excom Message.message_type, so calls can appear in a thread
	  - the new doctypes and the voice fields on Excom Channel Account
	"""
	frappe.reload_doc("excom", "doctype", "excom_call")
	frappe.reload_doc("excom", "doctype", "excom_voice_endpoint")
	frappe.reload_doc("excom", "doctype", "excom_message")
	frappe.reload_doc("excom", "doctype", "excom_channel_account")
	frappe.reload_doc("excom", "doctype", "excom_settings")

	if not frappe.db.exists("Excom Channel", "voice"):
		frappe.get_doc(
			{
				"doctype": "Excom Channel",
				"__newname": "voice",
				"channel_label": "Calls",
				"allows_multiple_accounts": 1,
				"is_enabled": 1,
				"description": (
					"Voice calls, answered in the browser over WebRTC with the agent's own phone "
					"as the fallback."
				),
			}
		).insert(ignore_permissions=True)
