"""What is known about a mail domain: provider, address pattern, catch-all."""

from frappe.model.document import Document

from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain


class ExcomAIEmailDomain(Document):
	def before_naming(self):
		self.domain = normalise_domain(self.domain)

	def validate(self):
		self.domain = normalise_domain(self.domain)
