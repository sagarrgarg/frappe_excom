"""Ads Bill Account Map: which company, GSTIN, supplier, cost centre and TDS category a platform
account's bills belong to. Saving it compares the GSTIN the platform holds with the company GSTIN
and raises / clears the GSTIN Mismatch alert."""

import frappe
from frappe import _
from frappe.model.document import Document


class AdsBillAccountMap(Document):
	def validate(self):
		from excom.excom_ads.bill_suppliers import check_gstin
		from excom.excom_ads.bills import _norm_key

		self.platform_key = (self.platform_key or "").strip()
		key = _norm_key(self.provider, self.platform_key)
		if not key:
			frappe.throw(_("Platform ID is mandatory"))
		for r in frappe.get_all("Ads Bill Account Map", filters={"provider": self.provider, "key_type": self.key_type, "name": ["!=", self.name or ""]}, fields=["name", "platform_key"]):
			if _norm_key(self.provider, r.platform_key) == key:
				frappe.throw(_("{0} {1} is already mapped in {2}").format(self.key_type, self.platform_key, r.name))
		if self.platform_gstin:
			self.platform_gstin = check_gstin(self.platform_gstin, _("GSTIN held by the platform"))
		if self.billing_address:
			addr = frappe.db.get_value("Address", self.billing_address, ["is_your_company_address"] + (["gstin"] if frappe.get_meta("Address").has_field("gstin") else []), as_dict=True)
			if not addr or not addr.is_your_company_address:
				frappe.throw(_("Billing address {0} must be one of your company's addresses").format(self.billing_address))
			linked = frappe.db.exists("Dynamic Link", {"parenttype": "Address", "parent": self.billing_address, "link_doctype": "Company", "link_name": self.company})
			if not linked:
				frappe.throw(_("Billing address {0} is not an address of {1}").format(self.billing_address, self.company))
			self.company_gstin = addr.get("gstin") or ""
		if self.account and frappe.db.get_value("Ads Account", self.account, "company") not in (None, self.company):
			frappe.throw(_("Ads Account {0} belongs to another company").format(self.account))
		if self.tax_withholding_category and self.tds_not_applicable:
			frappe.throw(_("Choose either a Tax Withholding Category or No TDS, not both"))
		if not self.platform_gstin:
			self.gstin_status = "Not Entered"
		else:
			self.gstin_status = "Matches" if self.platform_gstin == (self.company_gstin or "") else "Mismatch"
		from excom.excom_ads.permissions import assert_in_scope

		assert_in_scope(self)

	def on_update(self):
		from excom.excom_ads.bills import gstin_check

		gstin_check(self)
