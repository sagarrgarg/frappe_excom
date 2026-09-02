import frappe
from frappe.model.document import Document

class ExcomCall(Document):
	def validate(self):
		if not self.status:
			self.status = "Ringing"
			
		# Normalize phone numbers
		if self.from_number:
			self.from_number = self.from_number.strip()
		if self.to_number:
			self.to_number = self.to_number.strip()