"""A CSV / XLSX of daily figures for an ad network without a connector (a Manual ad account).
Validate checks every row; Import replaces the file's dates for the account (see imports.py)."""

import frappe
from frappe import _
from frappe.model.document import Document


class AdsMetricImport(Document):
	def validate(self):
		from excom.excom_ads import permissions

		if not self.flags.ignore_permissions:
			permissions.require("manage_connections")
		provider = frappe.db.get_value("Ads Account", self.account, "provider") if self.account else None
		if provider and provider != "Manual":
			frappe.throw(_("{0} is a {1} account: its figures come from the connector. Imports are for Manual accounts.").format(self.account, provider))
		if self.has_value_changed("import_file") and not self.is_new() and self.status != "Draft":
			# A new file starts over: the old validation result does not describe it.
			self.status = "Draft"
		permissions.assert_in_scope(self)
