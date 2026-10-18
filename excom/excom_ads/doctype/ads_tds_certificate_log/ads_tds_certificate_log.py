"""Ads TDS Certificate Log: one row per platform supplier, company and quarter. Marking it Sent (or
Credit Received) clears the TDS Certificate Due alert."""

import frappe
from frappe import _
from frappe.model.document import Document


class AdsTDSCertificateLog(Document):
	def validate(self):
		from excom.excom_ads.bills import QUARTERS

		if self.credit_received:
			self.status = "Credit Received"
		elif self.sent_date and self.status == "Pending":
			self.status = "Sent"
		if self.status != "Pending" and not self.sent_date:
			frappe.throw(_("Enter the date the certificate was sent"))
		if self.status != "Pending" and not self.certificate_reference:
			frappe.throw(_("Enter the Form 131 reference of the certificate"))
		if not self.due_date and self.fiscal_year and self.quarter in QUARTERS:
			start = frappe.db.get_value("Fiscal Year", self.fiscal_year, "year_start_date")
			if start:
				# Q1 / Q2 deadlines fall in the fiscal year's first calendar year, Q3 / Q4 in the next.
				_first, month, day, _yoff = QUARTERS[self.quarter]
				year = start.year + (1 if self.quarter.startswith(("Q3", "Q4")) else 0)
				self.due_date = f"{year:04d}-{month:02d}-{day:02d}"

		from excom.excom_ads.permissions import assert_in_scope

		assert_in_scope(self)

	def on_update(self):
		if self.status != "Pending":
			from excom.excom_ads import alerts

			alerts.resolve_alert(f"tds-cert:{self.name}", f"Certificate {self.status.lower()}")
