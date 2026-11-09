"""An action an agent wants to take, waiting for a person or done under its policy (HLD-007 §5.2)."""

import frappe
from frappe import _
from frappe.model.document import Document

DECIDED = ("Approved", "Edited", "Rejected")


class ExcomAIProposal(Document):
	def validate(self):
		if self.enrollment and self.step is not None:
			# One message per sequence step, as Harvey's unique (campaign, prospect, step).
			other = frappe.db.get_value(
				"Excom AI Proposal",
				{"enrollment": self.enrollment, "step": self.step, "name": ["!=", self.name or ""],
				 "status": ["not in", ("Rejected", "Cancelled", "Failed")]},
			)
			if other:
				frappe.throw(_("Step {0} of this enrollment already has a proposal ({1}).").format(self.step, other))
		if self.status == "Rejected" and not self.reject_reason:
			frappe.throw(_("Say why it is rejected: the reason teaches the agent."))
		if self.has_value_changed("status") and self.status in DECIDED and not self.decided_by:
			self.decided_by = frappe.session.user
			self.decided_on = frappe.utils.now_datetime()
