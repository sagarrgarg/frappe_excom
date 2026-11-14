"""Excom AI: master switch and global rules for AI agents (HLD-007 §5.1).

Off by default. While off, no scheduled work, no AI call and no queue entry happens for any agent.
Prompt retention is passed on to ai_interface as its policy for the calling app "excom".
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain


class ExcomAISettings(Document):
	def validate(self):
		seen = set()
		for row in self.dnc_domains:
			row.domain = normalise_domain(row.domain)
			if row.domain in seen:
				frappe.throw(_("{0} is listed twice.").format(row.domain))
			seen.add(row.domain)
		if cint(self.prompt_retention_days) < 0:
			frappe.throw(_("Retention cannot be negative."))
		from excom.excom_ai.voice import validate_settings

		validate_settings(self)  # the voice service's key length and the longest call
		if self.has_value_changed("effort_classify") and self.get_doc_before_save():
			from excom.excom_ai.monitor import gate_change

			gate_change(effort=self.effort_classify)  # replies are classified at this effort

	def on_update(self):
		_sync_ai_interface_policy(cint(self.prompt_retention_days))


def is_enabled() -> bool:
	return bool(frappe.db.get_single_value("Excom AI Settings", "enabled"))


def _sync_ai_interface_policy(days: int):
	"""Keep excom's prompt retention in ai_interface's per-app policies, without touching the rest.

	0 means "follow AI Settings' own retention" there, as here.
	"""
	from frappe.utils import cint

	days = cint(days)
	if "ai_interface" not in frappe.get_installed_apps() or not frappe.db.exists("DocType", "AI App Policy"):
		return
	existing = frappe.db.get_value("AI App Policy", {"parent": "AI Settings", "calling_app": "excom"}, "name")
	if existing:
		if cint(frappe.db.get_value("AI App Policy", existing, "payload_retention_days")) != days:
			frappe.db.set_value("AI App Policy", existing, "payload_retention_days", days)
			frappe.clear_document_cache("AI Settings", "AI Settings")
		return
	idx = (frappe.db.sql("SELECT MAX(idx) FROM `tabAI App Policy` WHERE parent = 'AI Settings'")[0][0] or 0) + 1
	frappe.get_doc({"doctype": "AI App Policy", "parent": "AI Settings", "parenttype": "AI Settings",
		"parentfield": "app_policies", "idx": idx, "calling_app": "excom",
		"payload_retention_days": days}).db_insert()
	frappe.clear_document_cache("AI Settings", "AI Settings")
