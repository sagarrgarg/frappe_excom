"""Ads endpoints for the desk form and the Excom app. Running the system is an admin action."""

import frappe
from frappe import _

from excom.excom.api.chat import _check_admin_access
from excom.excom_ads import sync

JOB_TYPES = ("Structure", "Metrics", "Settle", "Backfill")


@frappe.whitelist(methods=["POST"])
def sync_now(account: str, job_type: str = "Structure") -> dict:
	_check_admin_access()
	if job_type not in JOB_TYPES:
		frappe.throw(_("Unknown job type {0}").format(job_type))
	frappe.get_doc("Ads Account", account).check_permission("write")
	job = sync.enqueue(account, job_type, triggered_by=frappe.session.user)
	return {"queued": bool(job), "already_running": job is None}


@frappe.whitelist()
def capabilities(account: str) -> dict:
	"""What this account's provider can do, with the reason for anything it cannot."""
	from excom.excom_ads.providers.base import get_provider

	acc = frappe.get_doc("Ads Account", account)
	acc.check_permission("read")
	cls = type(get_provider(acc))
	return {op: {"supported": c.supported, "effects": list(c.effects), "scopes": list(c.scopes), "reason": c.reason} for op, c in cls.CAPABILITIES.items()}
