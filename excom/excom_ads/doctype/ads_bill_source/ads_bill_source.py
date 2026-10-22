import frappe
from frappe import _
from frappe.model.document import Document


class AdsBillSource(Document):
	def validate(self):
		if self.provider == "Meta":
			if not self.meta_connection:
				frappe.throw(_("Choose the Excom Meta Connection of the Business on monthly invoicing"))
			company = frappe.db.get_value("Excom Meta Connection", self.meta_connection, "company")
			if company and company != self.company:
				frappe.throw(_("Excom Meta Connection {0} belongs to {1}").format(self.meta_connection, company))
		if self.provider == "Google":
			from excom.excom_ads import google_auth
			from excom.excom_ads.providers.base import AdsProviderError

			if not self.google_connection:
				frappe.throw(_("Choose the Google Ads Connection"))
			company = frappe.db.get_value("Ads Connection", self.google_connection, "company")
			if company and company != self.company:
				frappe.throw(_("Ads Connection {0} belongs to {1}").format(self.google_connection, company))
			try:
				self.customer_id = google_auth.normalise_customer_id(self.customer_id)
			except AdsProviderError as e:
				frappe.throw(str(e))
			if self.billing_setup_id and not str(self.billing_setup_id).strip().isdigit():
				frappe.throw(_("Billing Setup ID is a number"))
		if self.workspace and frappe.db.get_value("Ads Workspace", self.workspace, "company") != self.company:
			frappe.throw(_("Workspace {0} belongs to another company").format(self.workspace))
		from excom.excom_ads.permissions import assert_in_scope

		assert_in_scope(self)
