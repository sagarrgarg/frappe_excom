import frappe
from frappe.model.document import Document

from excom.excom_ads.naming import mirror_name


class AdsCampaign(Document):
	# A mirror of the platform record. Local edits are never the platform state until read back.
	def autoname(self):
		self.name = mirror_name(self.account, self.external_id)

	def on_update(self):
		# Brand is allocated per campaign; its results follow at once instead of at the next sync.
		if self.has_value_changed("brand"):
			brand = self.brand or frappe.db.get_value("Ads Account", self.account, "brand")
			frappe.db.sql(
				"update `tabAds Metric Snapshot` set brand = %(brand)s where campaign = %(name)s and level in ('Campaign', 'Ad Group', 'Ad')",
				{"brand": brand, "name": self.name},
			)
