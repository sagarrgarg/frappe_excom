import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class AdsBillingRecord(Document):
	"""A top-up / refund / adjustment on a prepaid ad account, entered by finance.

	Feeds the ledger estimate (billing.ledger_estimate), which is always labelled as an estimate.
	"""

	def validate(self):
		from excom.excom_ads import permissions

		if not self.flags.ignore_permissions:
			permissions.require("view_finance")
		acc = frappe.db.get_value("Ads Account", self.account, ["currency", "company", "workspace", "provider", "brand"], as_dict=True)
		if not acc:
			frappe.throw(_("Ad account {0} does not exist").format(self.account))
		if not acc.currency:
			frappe.throw(_("Ad account {0} has no currency yet. Run its structure sync first.").format(self.account))
		# The ledger subtracts platform spend in the account currency: an amount in anything else
		# would be a different unit.
		self.currency = acc.currency
		self.company, self.workspace, self.provider = acc.company, acc.workspace, acc.provider
		if not self.brand:
			self.brand = acc.brand
		permissions.assert_in_scope(self)
		amount = flt(self.amount, 9)
		if self.record_type in ("Top-up", "Refund") and amount <= 0:
			frappe.throw(_("{0} amount must be more than zero").format(_(self.record_type)))
		if not amount:
			frappe.throw(_("Amount cannot be zero"))
		self.reference = (self.reference or "").strip() or None
		if self.reference:
			dup = frappe.db.get_value("Ads Billing Record", {"account": self.account, "reference": self.reference, "name": ["!=", self.name or ""]}, "name")
			if dup:
				frappe.throw(_("Reference {0} is already recorded on {1}").format(self.reference, dup))
		if self.payment_entry:
			pe = frappe.db.get_value("Payment Entry", self.payment_entry, ["docstatus", "company"], as_dict=True)
			if not pe or pe.docstatus != 1:
				frappe.throw(_("Payment Entry {0} is not submitted").format(self.payment_entry))
			if acc.company and pe.company != acc.company:
				frappe.throw(_("Payment Entry {0} belongs to company {1}, not {2}").format(self.payment_entry, pe.company, acc.company))

	def on_trash(self):
		from excom.excom_ads import permissions

		if not self.flags.ignore_permissions:
			permissions.require("view_finance")

	def on_update(self):
		self._recheck()

	def after_delete(self):
		self._recheck()

	def _recheck(self):
		# The ledger estimate moved: re-check its Low Headroom alert now rather than at the next sync.
		try:
			from excom.excom_ads.billing import on_record_change

			on_record_change(self)
		except Exception:
			frappe.log_error(title=f"Excom Ads: ledger alert check failed for {self.account}", message=frappe.get_traceback())
