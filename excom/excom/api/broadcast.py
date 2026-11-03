"""
API endpoints for the Broadcast page in the React SPA.

Provides create/submit/list/detail operations for Excom Broadcast,
plus delivery log access and email preview.
"""

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from excom.excom.api.chat import _check_manager_access as _check_broadcast_access


@frappe.whitelist()
def get_broadcasts(
    search: str = "",
    status: str = "",
    channel: str = "",
    subscriber_list: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List broadcasts with optional filters.

    Args:
        search: Filter by broadcast name
        status: Filter by status (Draft, Queued, Sending, Completed, etc.)
        channel: Filter by channel (WhatsApp, Email)
        subscriber_list: Filter by subscriber list name
        limit: Max results
        offset: Pagination offset

    Returns:
        {"broadcasts": [...], "total": int}
    """
    _check_broadcast_access()
    conditions = "b.docstatus < 2"
    params: dict = {"limit": int(limit), "offset": int(offset)}

    if search:
        conditions += " AND b.broadcast_name LIKE %(search)s"
        params["search"] = f"%{search}%"
    if status:
        conditions += " AND b.status = %(status)s"
        params["status"] = status
    if channel:
        conditions += " AND b.channel = %(channel)s"
        params["channel"] = channel
    if subscriber_list:
        conditions += " AND b.subscriber_list = %(subscriber_list)s"
        params["subscriber_list"] = subscriber_list

    broadcasts = frappe.db.sql(
        f"""
        SELECT b.name, b.broadcast_name, b.subscriber_list,
               sl.list_name AS subscriber_list_name,
               b.channel, b.status, b.docstatus,
               b.wa_template, b.email_subject,
               b.scheduled_at,
               b.total_recipients, b.sent_count, b.failed_count,
               b.creation, b.modified
        FROM `tabExcom Broadcast` b
        LEFT JOIN `tabExcom Subscriber List` sl ON sl.name = b.subscriber_list
        WHERE {conditions}
        ORDER BY b.modified DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
        as_dict=True,
    )

    total = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tabExcom Broadcast` b WHERE {conditions}",
        params,
    )[0][0]

    return {"broadcasts": broadcasts, "total": total}


@frappe.whitelist()
def create_broadcast(
    broadcast_name: str,
    subscriber_list: str,
    channel: str,
    wa_channel_account: str = "",
    wa_template: str = "",
    wa_language_code: str = "",
    wa_variable_mode: str = "same_for_all",
    wa_variable_slots: str = "",
    wa_template_variables: str = "[]",
    wa_variable_mapping: str = "[]",
    wa_header_media: str = "",
    wa_button_urls: str = "[]",
    wa_template_extras: str = "",
    email_subject: str = "",
    email_body: str = "",
    sender_email_account: str = "",
    sender_company: str = "",
    scheduled_at: str = "",
) -> dict:
    """
    Create a new broadcast in Draft state.

    Returns:
        {"name": "EXBC-00001", "success": True}
    """
    _check_broadcast_access()

    slots_final = (wa_variable_slots or "").strip() if wa_template else ""
    if wa_template and not slots_final:
        slots_final = "[]"
    mode_final = wa_variable_mode if wa_template else "same_for_all"
    if wa_template and slots_final:
        try:
            parsed_slots = json.loads(slots_final)
        except json.JSONDecodeError:
            frappe.throw(_("wa_variable_slots must be valid JSON"))
        if isinstance(parsed_slots, list) and len(parsed_slots) > 0:
            mode_final = "mixed"

    sched = (scheduled_at or "").strip() or None
    if sched and get_datetime(sched) <= now_datetime():
        frappe.throw(_("Scheduled At must be in the future"))

    doc = frappe.get_doc({
        "doctype": "Excom Broadcast",
        "broadcast_name": broadcast_name,
        "subscriber_list": subscriber_list,
        "channel": channel,
        "wa_channel_account": wa_channel_account or None,
        "wa_template": wa_template or None,
        "wa_language_code": wa_language_code if wa_template else "",
        "wa_variable_mode": mode_final,
        "wa_variable_slots": slots_final if wa_template else "",
        "wa_template_variables": wa_template_variables if wa_template else "[]",
        "wa_variable_mapping": wa_variable_mapping if wa_template else "[]",
        "wa_header_media": wa_header_media if wa_template else "",
        "wa_button_urls": wa_button_urls if wa_template else "[]",
        "wa_template_extras": (wa_template_extras or None) if wa_template else None,
        "email_subject": email_subject,
        "email_body": email_body,
        "sender_email_account": sender_email_account or None,
        "sender_company": sender_company or None,
        "scheduled_at": sched,
    })
    doc.insert()
    frappe.db.commit()

    return {"success": True, "name": doc.name}


@frappe.whitelist()
def submit_broadcast(broadcast_name: str) -> dict:
    """
    Submit a draft broadcast to begin sending.

    Returns:
        {"success": True, "status": "Queued"}
    """
    _check_broadcast_access()

    doc = frappe.get_doc("Excom Broadcast", broadcast_name)
    if doc.docstatus != 0:
        frappe.throw(_("Only Draft broadcasts can be submitted"))

    doc.submit()
    frappe.db.commit()

    return {"success": True, "status": doc.status}


@frappe.whitelist()
def get_broadcast_detail(broadcast_name: str) -> dict:
    """
    Fetch a single broadcast with stats and recent logs.

    Returns:
        Full broadcast dict plus recent_logs list
    """
    _check_broadcast_access()
    doc = frappe.get_doc("Excom Broadcast", broadcast_name)

    recent_logs = frappe.get_all(
        "Excom Broadcast Log",
        filters={"broadcast": broadcast_name},
        fields=[
            "omni_identity", "status", "recipient_address",
            "error_message", "sent_at", "creation",
        ],
        order_by="creation desc",
        limit=50,
    )

    identity_names = [l.omni_identity for l in recent_logs if l.omni_identity]
    identity_map = {}
    if identity_names:
        identities = frappe.get_all(
            "Omni Identity",
            filters={"name": ["in", identity_names]},
            fields=["name", "display_name"],
        )
        identity_map = {i.name: i.display_name for i in identities}

    for log in recent_logs:
        log["display_name"] = identity_map.get(log.omni_identity, "")

    return {
        "name": doc.name,
        "broadcast_name": doc.broadcast_name,
        "subscriber_list": doc.subscriber_list,
        "channel": doc.channel,
        "status": doc.status,
        "docstatus": doc.docstatus,
        "wa_channel_account": doc.get("wa_channel_account") or "",
        "wa_template": doc.wa_template,
        "wa_template_variables": doc.get("wa_template_variables") or "[]",
        "wa_header_media": doc.get("wa_header_media") or "",
        "email_subject": doc.email_subject,
        "email_body": doc.email_body,
        "sender_email_account": doc.sender_email_account,
        "sender_company": doc.sender_company,
        "scheduled_at": str(doc.scheduled_at) if doc.get("scheduled_at") else "",
        "total_recipients": doc.total_recipients,
        "sent_count": doc.sent_count,
        "failed_count": doc.failed_count,
        "creation": str(doc.creation),
        "modified": str(doc.modified),
        "recent_logs": recent_logs,
    }


@frappe.whitelist()
def get_broadcast_logs(
    broadcast_name: str,
    status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """
    Paginated delivery logs for a broadcast.

    Returns:
        {"logs": [...], "total": int}
    """
    _check_broadcast_access()
    filters: dict = {"broadcast": broadcast_name}
    if status:
        filters["status"] = status

    logs = frappe.db.sql(
        """
        SELECT bl.omni_identity, bl.status, bl.recipient_address,
               bl.error_message, bl.sent_at, bl.creation,
               oi.display_name
        FROM `tabExcom Broadcast Log` bl
        LEFT JOIN `tabOmni Identity` oi ON oi.name = bl.omni_identity
        WHERE bl.broadcast = %(broadcast)s
        {status_filter}
        ORDER BY bl.creation DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """.format(
            status_filter="AND bl.status = %(status)s" if status else ""
        ),
        {"broadcast": broadcast_name, "status": status, "limit": int(limit), "offset": int(offset)},
        as_dict=True,
    )

    total = frappe.db.count("Excom Broadcast Log", filters)

    return {"logs": logs, "total": total}


@frappe.whitelist()
def preview_broadcast_email(broadcast_name: str) -> dict:
    """
    Render the email body with the first subscriber's context for preview.

    Returns:
        {"subject": "...", "body": "<html>...", "subscriber": "..."}
    """
    _check_broadcast_access()
    doc = frappe.get_doc("Excom Broadcast", broadcast_name)
    return doc.preview_email()


@frappe.whitelist()
def get_subscriber_lists_for_broadcast() -> list:
    """
    Quick helper: return subscriber lists with active counts for the broadcast composer.

    Returns:
        List of {name, list_name, active_subscribers}
    """
    _check_broadcast_access()
    return frappe.get_all(
        "Excom Subscriber List",
        fields=["name", "list_name", "active_subscribers"],
        order_by="list_name asc",
        limit=200,
    )


@frappe.whitelist()
def get_channel_accounts_for_broadcast(channel: str = "") -> list:
    """
    Return Excom Channel Accounts available for sending.

    Args:
        channel: Optional filter (e.g. 'email', 'whatsapp')

    Returns:
        List of {name, channel, account_name}
    """
    _check_broadcast_access()
    filters: dict = {}
    if channel:
        filters["channel"] = channel

    return frappe.get_all(
        "Excom Channel Account",
        filters=filters,
        fields=["name", "channel", "account_name"],
        order_by="account_name asc",
        limit=50,
    )
