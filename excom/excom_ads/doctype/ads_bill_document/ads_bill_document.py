"""Ads Bill Document: one platform bill (API invoice or a manually uploaded PDF) on its way to a
Purchase Invoice. See excom_ads/bills.py for the pipeline.

Whoever types or changes the figures becomes `prepared_by` and can no longer approve the bill.
Figures freeze once its Purchase Invoice is submitted.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from excom.excom_ads.bills import FIGURE_FIELDS, SUBMITTED


PIPELINE_ONLY = ("platform_update", "purchase_invoice", "return_against", "original_document", "approved_by", "approved_at", "auto_submitted", "platform_invoice_id", "raw_payload", "bill_source", "differences", "pi_drafted_at", "gstr2b_status", "gstr2b_detail", "last_error", "fetched_at", "pdf_url", "amount_due", "payment_status", "billing_setup")


class AdsBillDocument(Document):
	def before_insert(self):
		if not self.flags.from_pipeline:
			# A person creates manual bills only; links to ERPNext documents are the pipeline's.
			self.source_type = "Manual"
			self.extraction_method = "Typed"
			self.status = "Fetched"
			for f in PIPELINE_ONLY:
				self.set(f, None)

	def validate(self):
		pipeline = bool(self.flags.from_pipeline)
		before = None if self.is_new() else self.get_doc_before_save()
		if before and not pipeline:
			# Read-only fields cannot be changed through the form or the REST API.
			for f in PIPELINE_ONLY + ("source_type", "status", "supplier", "prepared_by", "extraction_method"):
				self.set(f, before.get(f))
		if not self.company:
			frappe.throw(_("Company is mandatory"))
		for f in ("supplier_gstin", "company_gstin_on_bill"):
			if self.get(f):
				from excom.excom_ads.bill_suppliers import check_gstin

				self.set(f, check_gstin(self.get(f), self.meta.get_label(f)))
		self.bill_no = (self.bill_no or "").strip()
		if flt(self.net_amount) < 0 or flt(self.tax_amount) < 0:
			frappe.throw(_("Enter amounts as positive numbers; choose Credit Memo as the document type for a credit."))
		if not flt(self.total_amount):
			self.total_amount = flt(self.net_amount) + flt(self.tax_amount)
		for ln in self.lines:
			ln.currency = self.currency
		self.title = f"{self.provider} {self.bill_no}"[:140]
		if self.account_map:
			m = frappe.db.get_value("Ads Bill Account Map", self.account_map, ["company", "provider"], as_dict=True)
			if not m or m.company != self.company or m.provider != self.provider:
				frappe.throw(_("Account map {0} is for another company or platform").format(self.account_map))
		from excom.excom_ads.permissions import assert_in_scope

		assert_in_scope(self)
		if not pipeline and (self.is_new() or self._figures_changed(before)):
			if before and before.status == SUBMITTED:
				frappe.throw(_("This bill's Purchase Invoice is submitted; its figures cannot change."))
			self.prepared_by = frappe.session.user

	def _figures_changed(self, before) -> bool:
		if not before:
			return True
		for f in FIGURE_FIELDS:
			a, b = self.get(f), before.get(f)
			if isinstance(a, (int, float)) or isinstance(b, (int, float)):
				if abs(flt(a) - flt(b)) >= 0.005:
					return True
			elif str(a or "") != str(b or ""):
				return True
		lines = lambda d: json.dumps([[r.description, r.platform_account_id, r.campaign_id, r.brand, r.cost_center, flt(r.net_amount, 2), flt(r.tax_amount, 2)] for r in d.get("lines")], default=str)
		return lines(self) != lines(before)

	def on_trash(self):
		if self.purchase_invoice and frappe.db.get_value("Purchase Invoice", self.purchase_invoice, "docstatus") == 1:
			frappe.throw(_("Cancel Purchase Invoice {0} before deleting this bill.").format(self.purchase_invoice))
