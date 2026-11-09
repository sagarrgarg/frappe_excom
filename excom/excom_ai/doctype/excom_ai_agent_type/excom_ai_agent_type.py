"""An agent type: what a pack of abilities can do, and its editable defaults."""

import frappe
from frappe import _
from frappe.model.document import Document

from excom.excom_ai.constants import CEILINGS, MODES


class ExcomAIAgentType(Document):
	def validate(self):
		for row in self.default_policies:
			ceiling = CEILINGS.get(row.action, "Review")
			if MODES.index(row.mode) > MODES.index(ceiling):
				frappe.throw(_("Row {0}: {1} can be set to {2} at most.").format(row.idx, row.action, ceiling))
