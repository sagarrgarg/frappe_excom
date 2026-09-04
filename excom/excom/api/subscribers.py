"""
API endpoints for the Subscriber Management page.

Provides list/detail operations for Excom Subscriber Lists and their members,
plus bulk-add and import capabilities.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.api.chat import _check_excom_access, _check_identity_access


@frappe.whitelist()
def get_subscriber_lists(search: str = "", limit: int = 50, offset: int = 0) -> list:
    """
    Fetch all subscriber lists with counts.

    Args:
        search: Filter by list name
        limit: Max results
        offset: Pagination offset

    Returns:
        List of subscriber list dicts with counts
    """
    _check_excom_access()
    filters = {}
    if search:
        filters["list_name"] = ["like", f"%{search}%"]

    return frappe.get_all(
        "Excom Subscriber List",
        filters=filters,
        fields=["name", "list_name", "description", "total_subscribers", "active_subscribers", "creation"],
        order_by="modified desc",
        limit=int(limit),
        start=int(offset),
    )


@frappe.whitelist()
def get_subscribers(
    subscriber_list: str,
    search: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Fetch members of a subscriber list with Omni Identity details.

    Returns:
        {"subscribers": [...], "total": int, "active": int, "unsubscribed": int}
    """
    _check_excom_access()
    conditions = "s.subscriber_list = %(list)s"
    params = {"list": subscriber_list, "limit": int(limit), "offset": int(offset)}

    if search:
        conditions += " AND (oi.display_name LIKE %(search)s OR oi.primary_email LIKE %(search)s OR oi.primary_phone LIKE %(search)s)"
        params["search"] = f"%{search}%"

    if status and status in ("Subscribed", "Unsubscribed"):
        conditions += " AND s.status = %(status)s"
        params["status"] = status

    subscribers = frappe.db.sql(
        f"""
        SELECT s.name, s.omni_identity, s.status, s.subscribed_on, s.unsubscribed_on,
               oi.display_name, oi.primary_email, oi.primary_phone, oi.primary_whatsapp
        FROM `tabExcom Subscriber` s
        LEFT JOIN `tabOmni Identity` oi ON oi.name = s.omni_identity
        WHERE {conditions}
        ORDER BY s.subscribed_on DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
        as_dict=True,
    )

    total = frappe.db.count("Excom Subscriber", {"subscriber_list": subscriber_list})
    active = frappe.db.count(
        "Excom Subscriber", {"subscriber_list": subscriber_list, "status": "Subscribed"}
    )

    return {
        "subscribers": subscribers,
        "total": total,
        "active": active,
        "unsubscribed": total - active,
    }


@frappe.whitelist()
def add_subscriber(subscriber_list: str, omni_identity: str) -> dict:
    """
    Add a single subscriber to a list.

    Auto-creates the Excom Subscriber record.
    """
    _check_identity_access(omni_identity)
    if frappe.db.exists(
        "Excom Subscriber",
        {"subscriber_list": subscriber_list, "omni_identity": omni_identity},
    ):
        return {"success": False, "message": "Already subscribed"}

    doc = frappe.get_doc({
        "doctype": "Excom Subscriber",
        "subscriber_list": subscriber_list,
        "omni_identity": omni_identity,
        "status": "Subscribed",
        "subscribed_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "name": doc.name}


@frappe.whitelist()
def add_subscriber_by_contact(subscriber_list: str, phone: str = "", email: str = "") -> dict:
    """
    Add a subscriber by phone or email — resolves or creates Omni Identity.

    Args:
        subscriber_list: List name
        phone: Phone number
        email: Email address

    Returns:
        {"success": True, "omni_identity": "...", "created_identity": bool}
    """
    _check_excom_access()
    if not phone and not email:
        frappe.throw(_("Phone or email is required"))

    from excom.excom.doctype.omni_identity.omni_identity import resolve_identity

    identity_name = resolve_identity(phone=phone, email=email)

    if frappe.db.exists(
        "Excom Subscriber",
        {"subscriber_list": subscriber_list, "omni_identity": identity_name},
    ):
        return {"success": True, "omni_identity": identity_name, "already_exists": True}

    doc = frappe.get_doc({
        "doctype": "Excom Subscriber",
        "subscriber_list": subscriber_list,
        "omni_identity": identity_name,
        "status": "Subscribed",
        "subscribed_on": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {"success": True, "omni_identity": identity_name, "name": doc.name}


@frappe.whitelist()
def remove_subscriber(subscriber_name: str) -> dict:
    """Remove a subscriber from a list (hard delete)."""
    _check_excom_access()
    frappe.delete_doc("Excom Subscriber", subscriber_name, ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def unsubscribe_subscriber(subscriber_name: str) -> dict:
    """Soft-unsubscribe: set status to Unsubscribed."""
    _check_excom_access()
    frappe.db.set_value(
        "Excom Subscriber",
        subscriber_name,
        {"status": "Unsubscribed", "unsubscribed_on": now_datetime()},
    )
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def resubscribe(subscriber_name: str) -> dict:
    """Re-subscribe a previously unsubscribed member."""
    _check_excom_access()
    frappe.db.set_value(
        "Excom Subscriber",
        subscriber_name,
        {"status": "Subscribed", "unsubscribed_on": None},
    )
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def import_from_doctype(
    subscriber_list: str,
    doctype: str,
    filters: str = "{}",
    limit: int = 0,
) -> dict:
    """
    Bulk-import subscribers from an ERPNext DocType.

    Delegates to the Excom Subscriber List's import method.

    Args:
        subscriber_list: List name
        doctype: Customer, Supplier, or Lead
        filters: JSON string of Frappe filters
        limit: Max records (0 = all)

    Returns:
        {"added": int, "skipped": int}
    """
    _check_excom_access()
    if doctype not in ("Customer", "Supplier", "Lead", "Contact"):
        frappe.throw(_("Subscribers can only be imported from Customer, Supplier, Lead or Contact"), frappe.PermissionError)
    parsed_filters = json.loads(filters) if isinstance(filters, str) else filters
    doc = frappe.get_doc("Excom Subscriber List", subscriber_list)
    result = doc.add_subscribers_from_doctype(
        doctype=doctype,
        filters=parsed_filters,
        limit=int(limit),
    )
    return result


@frappe.whitelist()
def create_subscriber_list(list_name: str, description: str = "") -> dict:
    """Create a new subscriber list."""
    _check_excom_access()
    doc = frappe.get_doc({
        "doctype": "Excom Subscriber List",
        "list_name": list_name,
        "description": description,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True, "name": doc.name}


@frappe.whitelist()
def search_identities(search: str = "", limit: int = 20) -> list:
    """
    Search Omni Identities by name, email, phone, or WhatsApp for the subscriber picker.

    Returns list of dicts with name, display_name, primary_email, primary_phone.
    """
    _check_excom_access()
    limit = min(int(limit), 50)
    params: dict = {"limit": limit}
    where = "status = 'Active'"

    if search:
        params["q"] = f"%{search}%"
        where += (
            " AND (display_name LIKE %(q)s"
            " OR primary_email LIKE %(q)s"
            " OR primary_phone LIKE %(q)s"
            " OR primary_whatsapp LIKE %(q)s"
            " OR name LIKE %(q)s)"
        )

    return frappe.db.sql(
        f"""
        SELECT name, display_name, primary_email, primary_phone, primary_whatsapp
        FROM `tabOmni Identity`
        WHERE {where}
        ORDER BY display_name ASC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )
