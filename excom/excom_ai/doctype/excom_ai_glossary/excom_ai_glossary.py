"""The company's own words: 'water gun' means Item Group Pichkari."""

import frappe
from frappe.model.document import Document


class ExcomAIGlossary(Document):
	def validate(self):
		self.term = (self.term or "").strip().lower()
		if not self.confirmed_by:
			self.confirmed_by = frappe.session.user
