from frappe.model.document import Document

from excom.excom_ads.naming import billing_snapshot_name


class AdsBillingSnapshot(Document):
	"""Written only by the Structure sync (billing.record_reading); read-only on the Desk."""

	def autoname(self):
		self.name = billing_snapshot_name(self.account, self.snapshot_date)
