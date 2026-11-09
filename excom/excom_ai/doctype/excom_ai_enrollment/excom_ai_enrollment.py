"""One lead in one agent's sequence. One active enrollment per lead and agent."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

OPEN = ("Active", "Paused")


class ExcomAIEnrollment(Document):
	def validate(self):
		if self.lead and self.status in OPEN:
			other = frappe.db.get_value("Excom AI Enrollment", {"lead": self.lead, "agent": self.agent,
				"status": ["in", OPEN], "name": ["!=", self.name or ""]})
			if other:
				frappe.throw(_("Lead {0} is already in a sequence of this agent ({1}).").format(self.lead, other))
		if self.status not in OPEN and not self.stop_reason and self.status != "Completed":
			self.stop_reason = self.status
		self.started_on = self.started_on or now_datetime()
