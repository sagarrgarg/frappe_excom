"""A person or published inbox at a researched company (HLD-007 C.2)."""

import frappe
from frappe import _
from frappe.model.document import Document


class ExcomAIPerson(Document):
	def validate(self):
		# Found on LinkedIn through Apollo or Prospeo, a person may have no address yet (HLD-009); an
		# empty one is stored as NULL so the unique index never sees two blanks.
		self.email = (self.email or "").strip().lower() or None
		if self.linkedin_url:
			from excom.excom_ai.tools.people import linkedin

			# one form everywhere (Hunter's raw link and the providers' cleaned one), so a person is never doubled
			self.linkedin_url = linkedin(self.linkedin_url) or (None if self.email else self.linkedin_url)
		if not self.email and not self.linkedin_url:
			frappe.throw(_("A person needs an email or a LinkedIn URL."))
		self.full_name = " ".join(p for p in (self.first_name, self.last_name) if p) or (
			_("General inbox") if self.is_inbox else (self.email or "")
		)
		if self.is_inbox:
			self.seniority = "Inbox"
		if self.email_status == "Invalid" and self.status in ("New", "Ready"):
			self.status = "Invalid"
