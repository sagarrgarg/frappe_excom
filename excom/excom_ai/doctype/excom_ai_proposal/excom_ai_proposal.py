"""An action an agent wants to take, waiting for a person or done under its policy (HLD-007 §5.2)."""

import frappe
from frappe import _
from frappe.model.document import Document

DECIDED = ("Approved", "Edited", "Rejected")
# A proposal in one of these no longer holds its sequence step: the step may be proposed again.
RELEASED = ("Rejected", "Cancelled", "Failed", "Expired")


class ExcomAIProposal(Document):
	def validate(self):
		if self.enrollment and self.step is not None:
			# One message per sequence step, as Harvey's unique (campaign, prospect, step).
			other = frappe.db.get_value(
				"Excom AI Proposal",
				{"enrollment": self.enrollment, "step": self.step, "name": ["!=", self.name or ""],
				 "status": ["not in", RELEASED]},
			)
			if other:
				frappe.throw(_("Step {0} of this enrollment already has a proposal ({1}).").format(self.step, other))
		# The database holds the same rule with a unique key, so two workers can never both send a step.
		held = self.enrollment and self.step is not None and self.status not in RELEASED
		self.step_key = f"{self.enrollment}:{self.step}" if held else None
		if self.status == "Rejected" and not self.reject_reason:
			frappe.throw(_("Say why it is rejected: the reason teaches the agent."))
		if self.has_value_changed("status") and self.status in DECIDED and not self.decided_by:
			self.decided_by = frappe.session.user
			self.decided_on = frappe.utils.now_datetime()
