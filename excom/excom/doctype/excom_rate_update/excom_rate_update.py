import frappe
from frappe import _
from frappe.model.document import Document


class ExcomRateUpdate(Document):
	def validate(self):
		if not self.currency and self.price_list:
			self.currency = frappe.db.get_value("Price List", self.price_list, "currency")

	@frappe.whitelist()
	def publish_now(self):
		"""Publish this Rate Update now (catalogue sync, then the WhatsApp broadcast)."""
		from excom.excom.api.chat import _check_manager_access
		from excom.excom.services.commerce import rates

		_check_manager_access()
		if self.status not in ("Draft", "Failed"):
			frappe.throw(_("Only a Draft or Failed Rate Update can be published (this one is {0}).").format(self.status))
		if not self.items:
			frappe.throw(_("Nothing to publish: no rate changes."))
		return rates.queue_publish(self.name, manual=True)

	@frappe.whitelist()
	def cancel_update(self):
		"""Drop this draft without announcing it. Published rates stay as they were, so the next
		change to these items is measured against the last rate customers were actually told."""
		from excom.excom.api.chat import _check_manager_access

		_check_manager_access()
		if self.status != "Draft":
			frappe.throw(_("Only a Draft Rate Update can be cancelled."))
		self.db_set("status", "Cancelled")
