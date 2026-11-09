"""An agent type: what a pack of abilities can do, and its editable defaults."""

from frappe.model.document import Document

from excom.excom_ai.constants import check_policy_rows


class ExcomAIAgentType(Document):
	def validate(self):
		check_policy_rows(self.default_policies, self.pack)
