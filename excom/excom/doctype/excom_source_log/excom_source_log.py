import frappe
from frappe import _
from frappe.model.document import Document


class ExcomSourceLog(Document):
	@frappe.whitelist()
	def replay(self):
		"""Re-run the intake pipeline on this row (S9: replay, not loss)."""
		from excom.excom.services.intake import process_log
		self.check_permission("write")
		process_log(self.name, force=True)
		return {"status": frappe.db.get_value("Excom Source Log", self.name, "status")}
