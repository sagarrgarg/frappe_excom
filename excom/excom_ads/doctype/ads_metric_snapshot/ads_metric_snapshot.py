from frappe.model.document import Document

from excom.excom_ads.naming import snapshot_name


class AdsMetricSnapshot(Document):
	def autoname(self):
		self.name = snapshot_name(self.account, self.level, self.entity_external_id, self.date, self.breakdown_key, self.breakdown_value, self.attribution_key)
