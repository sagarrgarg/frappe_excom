"""Ads endpoints for the desk form and the Excom app.

Every endpoint checks scope (the record must be visible to the caller) and, for anything that
changes state, the matching right on Ads Access Profile.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from excom.excom_ads import permissions, sync

JOB_TYPES = ("Structure", "Metrics", "Settle", "Backfill")
SNOOZE_HOURS = (1, 4, 24, 72, 168)


def _visible(doctype: str, name: str):
	doc = frappe.get_doc(doctype, name)
	if not permissions.doc_allowed(doc):
		raise frappe.PermissionError(_("Not permitted"))
	doc.check_permission("read")
	return doc


@frappe.whitelist(methods=["POST"])
def sync_now(account: str, job_type: str = "Structure") -> dict:
	if job_type not in JOB_TYPES:
		frappe.throw(_("Unknown job type {0}").format(job_type))
	permissions.require("manage_connections")
	_visible("Ads Account", account)
	job = sync.enqueue(account, job_type, triggered_by=frappe.session.user)
	return {"queued": bool(job), "already_running": job is None}


@frappe.whitelist()
def capabilities(account: str) -> dict:
	"""What this account's provider can do, with the reason for anything it cannot."""
	from excom.excom_ads.providers.base import provider_class

	acc = _visible("Ads Account", account)
	return {op: {"supported": c.supported, "effects": list(c.effects), "scopes": list(c.scopes), "reason": c.reason} for op, c in provider_class(acc.provider).CAPABILITIES.items()}


# ─── alerts ───────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def acknowledge_alert(name: str) -> dict:
	_visible("Ads Alert", name)
	frappe.db.set_value("Ads Alert", name, {"status": "Acknowledged", "acknowledged_by": frappe.session.user, "acknowledged_at": now_datetime()})
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def snooze_alert(name: str, hours: int = 24) -> dict:
	hours = int(hours)
	if hours not in SNOOZE_HOURS:
		frappe.throw(_("Snooze for one of {0} hours").format(", ".join(map(str, SNOOZE_HOURS))))
	alert = _visible("Ads Alert", name)
	if alert.severity == "Critical":
		frappe.throw(_("Critical alerts cannot be snoozed; acknowledge or resolve them."))
	frappe.db.set_value("Ads Alert", name, {"status": "Snoozed", "snoozed_until": add_to_date(now_datetime(), hours=hours)})
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def resolve_alert(name: str, resolution: str = "") -> dict:
	_visible("Ads Alert", name)
	frappe.db.set_value("Ads Alert", name, {"status": "Resolved", "resolved_by": frappe.session.user, "resolved_at": now_datetime(), "resolution": (resolution or "Resolved by hand")[:1000]})
	return {"ok": True}
