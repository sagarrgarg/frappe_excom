"""Mail folders and two-way sync: fill in where the emails already here sit, then, in the
background, line each mailbox up with Excom once (channels/email/cutover.py)."""

import frappe


def execute():
	for dt in ("excom_message", "excom_channel_account", "excom_email_draft"):
		frappe.reload_doc("excom", "doctype", dt)
	from excom.excom.channels.email import cutover

	cutover.fill_folders()
	cutover.enqueue_align()
