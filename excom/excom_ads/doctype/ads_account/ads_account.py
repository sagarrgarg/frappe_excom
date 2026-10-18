import frappe
from frappe import _
from frappe.model.document import Document

from excom.excom_ads.naming import account_name


class AdsAccount(Document):
	def autoname(self):
		self.name = account_name(self.provider, self.external_id)

	def validate(self):
		self.external_id = (self.external_id or "").strip()
		if self.provider == "Meta" and not self.external_id.startswith("act_"):
			self.external_id = f"act_{self.external_id}"
		if self.provider == "Google":
			self.external_id = self.external_id.replace("-", "")
		if self.brand:
			brand_ws = frappe.db.get_value("Ads Brand", self.brand, "workspace")
			if brand_ws and brand_ws != self.workspace:
				frappe.throw(_("Brand {0} belongs to workspace {1}, not {2}").format(self.brand, brand_ws, self.workspace))
