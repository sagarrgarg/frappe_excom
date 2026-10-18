import frappe
from frappe import _
from frappe.model.document import Document

from excom.excom_ads.naming import account_name


def normalise_external_id(provider: str, external_id: str) -> str:
	"""One spelling per platform account, used for both the record name and the stored ID."""
	value = (external_id or "").strip()
	if provider == "Meta" and value and not value.startswith("act_"):
		value = f"act_{value}"
	if provider == "Google":
		value = value.replace("-", "")
	return value


class AdsAccount(Document):
	def autoname(self):
		# Runs before validate(), so normalise here too or the name and the ID disagree.
		self.external_id = normalise_external_id(self.provider, self.external_id)
		self.name = account_name(self.provider, self.external_id)

	def validate(self):
		self.external_id = normalise_external_id(self.provider, self.external_id)
		if self.brand:
			brand_ws = frappe.db.get_value("Ads Brand", self.brand, "workspace")
			if brand_ws and brand_ws != self.workspace:
				frappe.throw(_("Brand {0} belongs to workspace {1}, not {2}").format(self.brand, brand_ws, self.workspace))
		if self.get("connection"):
			# A credential of one company never reads another company's account.
			conn = frappe.db.get_value("Ads Connection", self.connection, ["provider", "company"], as_dict=True)
			if conn and conn.provider != self.provider:
				frappe.throw(_("Ads Connection {0} is for {1}, not {2}").format(self.connection, conn.provider, self.provider))
			company = self.company or frappe.db.get_value("Ads Workspace", self.workspace, "company")
			if conn and company and conn.company != company:
				frappe.throw(_("Ads Connection {0} belongs to company {1}, not {2}").format(self.connection, conn.company, company))
