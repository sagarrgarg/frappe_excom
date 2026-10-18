from frappe.model.document import Document

from excom.excom_ads.naming import mirror_name


class AdsCampaign(Document):
	# A mirror of the platform record. Local edits are never the platform state until read back.
	def autoname(self):
		self.name = mirror_name(self.account, self.external_id)
