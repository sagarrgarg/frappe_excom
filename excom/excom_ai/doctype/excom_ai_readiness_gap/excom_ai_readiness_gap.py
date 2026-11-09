"""A readiness rule that failed for an agent. Only an Excom Admin waives a blocker, with a reason."""

import frappe
from frappe import _
from frappe.model.document import Document


class ExcomAIReadinessGap(Document):
	def validate(self):
		if not self.severity:
			self.severity = frappe.db.get_value("Excom AI Readiness Rule", self.rule, "severity")
		if self.status == "Waived" and self.has_value_changed("status"):
			if not (self.waive_reason or "").strip():
				frappe.throw(_("Say why this gap is waived."))
			if self.severity == "Blocker" and not set(frappe.get_roles()) & {"Excom Admin", "System Manager"}:
				frappe.throw(_("Only an Excom Admin waives a blocker."), frappe.PermissionError)
			self.waived_by = frappe.session.user
