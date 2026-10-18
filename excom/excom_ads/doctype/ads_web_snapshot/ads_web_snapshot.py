from frappe.model.document import Document

from excom.excom_ads.naming import web_snapshot_name


class AdsWebSnapshot(Document):
	def autoname(self):
		self.name = web_snapshot_name(self.property, self.report, self.date, self.session_source, self.session_medium, self.session_campaign_name, self.session_campaign_id, self.landing_page)
