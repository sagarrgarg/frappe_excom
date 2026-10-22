from frappe.model.document import Document

from excom.excom_ads.naming import touch_name


class AdsAttributionTouch(Document):
	# Written only by excom_ads.attribution: one row per observation, named from its natural key.
	def autoname(self):
		self.name = touch_name(self.touch_source, self.observation_id)
