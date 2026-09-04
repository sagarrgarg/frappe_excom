"""
API endpoints for the Bulk Identity Sync tool.

Triggers background sync of ERPNext entities into Omni Identities
and exposes progress polling for the UI.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.api.chat import _check_excom_access, _check_manager_access


PROGRESS_KEY = "excom_identity_sync_progress"


@frappe.whitelist()
def run_identity_sync() -> dict:
    """
    Enqueue a full identity sync job.

    Returns:
        {"enqueued": True, "progress_key": "..."}
    """
    _check_manager_access()

    existing = frappe.cache.get_value(PROGRESS_KEY)
    if existing and not existing.get("done"):
        return {
            "enqueued": False,
            "message": "A sync is already in progress.",
            "progress": existing,
        }

    frappe.cache.set_value(
        PROGRESS_KEY,
        {"done": False, "current": 0, "total": 0, "phase": "starting", "percent": 0},
        expires_in_sec=3600,
    )

    frappe.enqueue(
        "excom.excom.services.identity_sync.sync_all_identities",
        progress_key=PROGRESS_KEY,
        queue="long",
        timeout=3600,
        is_async=True,
    )

    return {"enqueued": True, "progress_key": PROGRESS_KEY}


@frappe.whitelist()
def get_sync_status() -> dict:
    """
    Poll current sync progress.

    Returns:
        {"done": bool, "current": int, "total": int, "phase": str, "percent": int}
        or {"done": True, "stats": {...}} when finished.
    """
    _check_manager_access()
    progress = frappe.cache.get_value(PROGRESS_KEY)
    if not progress:
        return {"done": True, "message": "No sync in progress."}
    return progress


@frappe.whitelist()
def sync_single_entity(doctype: str, name: str) -> dict:
    """
    Sync a single ERPNext entity into an Omni Identity.

    Args:
        doctype: Customer, Supplier, or Lead
        name: Document name

    Returns:
        {"success": True, "identity": "OI-00001"} or error
    """
    _check_manager_access()

    from excom.excom.services.identity_sync import (
        sync_single_customer,
        sync_single_supplier,
        sync_single_lead,
    )

    sync_map = {
        "Customer": sync_single_customer,
        "Supplier": sync_single_supplier,
        "Lead": sync_single_lead,
    }

    fn = sync_map.get(doctype)
    if not fn:
        frappe.throw(_("Unsupported doctype: {0}. Use Customer, Supplier, or Lead.").format(doctype))

    identity_name = fn(name)
    if identity_name:
        frappe.db.commit()
        return {"success": True, "identity": identity_name, "created": True}

    existing = frappe.db.get_value(
        "Omni Identity Link",
        {"linked_doctype": doctype, "linked_name": name},
        "parent",
    )
    return {"success": True, "identity": existing or "", "created": False}
