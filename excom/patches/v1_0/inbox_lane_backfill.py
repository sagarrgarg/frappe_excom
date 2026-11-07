"""Sort the email conversations already in the inbox into the main inbox and Other."""

import frappe


def execute():
	frappe.reload_doc("excom", "doctype", "excom_thread")
	from excom.excom.services.inbox_lane import backfill

	backfill()
