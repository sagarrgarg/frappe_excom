"""Knowledge a person writes or approves. Only Approved playbooks reach prompts; editing an
approved one sends it back to review with a new version number."""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

APPROVERS = {"Excom Admin", "System Manager"}


class ExcomAIPlaybook(Document):
	def validate(self):
		before = self.get_doc_before_save()
		changed = before is not None and self._sections(before) != self._sections(self)
		if changed:
			self.version = cint(self.version) + 1
			if before.status == "Approved" and self.status == "Approved":
				self.status = "In Review"
				self.approved_by = self.approved_on = None
		if self.status == "Approved" and (before is None or before.status != "Approved"):
			if not set(frappe.get_roles()) & APPROVERS:
				frappe.throw(_("Only an Excom Admin approves a playbook."), frappe.PermissionError)
			self.approved_by = frappe.session.user
			self.approved_on = now_datetime()

	@staticmethod
	def _sections(doc):
		return json.dumps([(s.kind, s.title, s.content) for s in doc.sections], default=str)
