"""A person or published inbox at a researched company (HLD-007 C.2)."""

import frappe
from frappe import _
from frappe.model.document import Document


class ExcomAIPerson(Document):
	def validate(self):
		if self.email:
			self.email = self.email.strip().lower()
		self.full_name = " ".join(p for p in (self.first_name, self.last_name) if p) or (
			_("General inbox") if self.is_inbox else (self.email or "")
		)
		if self.is_inbox:
			self.seniority = "Inbox"
		if self.email_status == "Invalid" and self.status in ("New", "Ready"):
			self.status = "Invalid"
