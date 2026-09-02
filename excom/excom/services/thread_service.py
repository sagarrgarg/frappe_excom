"""
Core service layer for Excom Thread and Message operations.

All inbound/outbound message flows route through here to guarantee
consistent thread state, idempotency, and identity resolution.
"""

import json
import re

import frappe
from frappe import _

from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
from excom.excom.utils import get_channel_account


def upsert_thread(omni_identity: str, channel: str, account: str) -> str:
    """
    Find or create an Excom Thread for the given identity + channel + account.
    Returns the thread name.

    Handles concurrent inserts gracefully — if a duplicate key error occurs
    (race between check and insert), returns the existing thread.
    """
    thread_key = f"{channel}:{account}:{omni_identity}"

    existing = frappe.db.get_value("Excom Thread", {"thread_key": thread_key}, "name")
    if existing:
        return existing

    oi = frappe.db.get_value(
        "Omni Identity", omni_identity,
        ["display_name", "primary_phone"], as_dict=True,
    )

    try:
        doc = frappe.get_doc({
            "doctype": "Excom Thread",
            "omni_identity": omni_identity,
            "channel": channel,
            "account_doctype": "Excom Channel Account",
            "account": account,
            "thread_key": thread_key,
            "status": "Open",
            "display_name": oi.display_name if oi else "Unknown",
            "primary_phone": oi.primary_phone if oi else "",
            "unread_count": 0,
            "last_message_at": frappe.utils.now_datetime(),
        })
        frappe.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        existing = frappe.db.get_value("Excom Thread", {"thread_key": thread_key}, "name")
        if existing:
            return existing
        raise


def ingest_inbound_message(
    phone: str,
    channel: str,
    account: str,
    provider_message_id: str,
    content_text: str,
    message_type: str = "Text",
    display_name: str = "",
    content_json: dict = None,
    media_file: str = "",
    reply_to_provider_id: str = "",
    provider_timestamp: str = "",
) -> str:
    """
    Full inbound message ingestion pipeline.

    1. Resolve identity
    2. Upsert thread
    3. Check idempotency
    4. Insert Excom Message
    5. Update thread counters

    Returns the Excom Message name, or empty string if duplicate.
    """
    if provider_message_id and frappe.db.exists(
        "Excom Message", {"provider_message_id": provider_message_id}
    ):
        return ""

    identity_name = resolve_identity(
        phone=phone,
        channel=channel,
        channel_user_id=phone,
        display_name=display_name,
    )

    thread_name = upsert_thread(identity_name, channel, account)

    reply_to = ""
    if reply_to_provider_id:
        reply_to = frappe.db.get_value(
            "Excom Message",
            {"provider_message_id": reply_to_provider_id},
            "name",
        ) or ""

    now = frappe.utils.now_datetime()
    preview = _make_preview(content_text)

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_name,
        "omni_identity": identity_name,
        "channel": channel,
        "account_doctype": "Excom Channel Account",
        "account": account,
        "direction": "Inbound",
        "message_type": message_type,
        "provider_message_id": provider_message_id,
        "provider_timestamp": provider_timestamp or now,
        "content_text": content_text,
        "content_json": json.dumps(content_json) if content_json else "{}",
        "media_file": media_file,
        "delivery_status": "Delivered",
        "reply_to": reply_to,
    })
    msg.insert(ignore_permissions=True)

    frappe.db.sql(
        """
        UPDATE `tabExcom Thread`
        SET last_message_at = %(now)s,
            last_inbound_at = %(now)s,
            unread_count = unread_count + 1,
            last_message_preview = %(preview)s,
            last_message_direction = 'Inbound',
            status = CASE WHEN status = 'Closed' THEN 'Open' ELSE status END,
            modified = %(now)s
        WHERE name = %(thread)s
        """,
        {"now": now, "preview": preview, "thread": thread_name},
    )

    display_name = frappe.db.get_value("Excom Thread", thread_name, "display_name") or ""

    frappe.publish_realtime(
        "excom:message_received",
        {
            "thread": thread_name,
            "message": msg.name,
            "omni_identity": identity_name,
            "direction": "Inbound",
            "preview": preview,
            "display_name": display_name,
        },
        after_commit=True,
    )
    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_name, "event": "new_inbound"},
        after_commit=True,
    )

    _schedule_push_notification(thread_name, msg.name)

    try:
        from excom.excom.services.broadcast_metrics import attribute_inbound_reply
        attribute_inbound_reply(msg.name, reply_to_provider_id, message_type, content_text)
    except Exception:
        frappe.log_error(title="Excom: broadcast reply attribution failed")

    frappe.db.commit()

    return msg.name


def _schedule_push_notification(thread_name: str, message_name: str) -> None:
    """Schedule push notification delivery after the HTTP response completes."""
    try:
        from excom.excom.notification import send_push_for_inbound_message

        if frappe.request and hasattr(frappe.request, "after_response"):
            frappe.request.after_response.add(
                lambda: send_push_for_inbound_message(thread_name, message_name)
            )
        else:
            send_push_for_inbound_message(thread_name, message_name)
    except Exception:
        frappe.log_error(title="Excom: failed to schedule push notification")


def _auto_claim_thread(thread, user: str) -> bool:
    """Auto-assign thread to user on first reply if nobody is assigned.

    Returns True if the thread was claimed.
    """
    if thread.assigned_to:
        return False

    from excom.excom.doctype.excom_team.excom_team import get_user_teams
    teams = get_user_teams(user)
    update: dict = {"assigned_to": user}
    if teams:
        update["assigned_team"] = teams[0]

    frappe.db.set_value("Excom Thread", thread.name, update, update_modified=False)
    return True


def send_outbound_message(
    thread_name: str,
    content_text: str,
    message_type: str = "Text",
    media_file: str = "",
    template: str = "",
    reply_to: str = "",
    sticker_name: str = "",
) -> str:
    """
    Send an outbound message through the channel provider.

    1. Load thread and account
    2. Call provider API
    3. Insert Excom Message
    4. Update thread counters

    Returns the Excom Message name.
    """
    thread = frappe.get_doc("Excom Thread", thread_name)
    identity = frappe.get_doc("Omni Identity", thread.omni_identity)

    account_doctype = thread.account_doctype
    account_name = thread.account

    if thread.channel == "email":
        frappe.throw(
            _("Use the email API (excom.excom.api.email.send_email) for email threads. "
              "The generic send_message endpoint is for chat channels only.")
        )

    provider_message_id = ""
    delivery_status = "Queued"

    if thread.channel == "webchat":
        import secrets
        provider_message_id = f"wc-agent-{secrets.token_hex(8)}"
        delivery_status = "Delivered"

    elif thread.channel == "whatsapp":
        to_number = identity.primary_whatsapp or identity.primary_phone
        if not to_number:
            frappe.throw(_("No phone number on identity to send to"))

        account = frappe.get_doc(account_doctype, account_name)

        from excom.excom.services.whatsapp_service import (
            send_text_message,
            send_media_message,
            send_sticker_message,
        )

        if message_type == "Sticker" and sticker_name:
            result = send_sticker_message(account, to_number, sticker_name)
        elif message_type in ("Image", "Video", "Audio", "Document") and media_file:
            result = send_media_message(account, to_number, message_type, media_file, content_text)
        else:
            result = send_text_message(account, to_number, content_text)
        provider_message_id = result.get("provider_message_id", "")
        delivery_status = result.get("status", "Sent")

    now = frappe.utils.now_datetime()
    preview = _make_preview(content_text)

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_name,
        "omni_identity": thread.omni_identity,
        "channel": thread.channel,
        "account_doctype": account_doctype,
        "account": account_name,
        "direction": "Outbound",
        "message_type": message_type,
        "provider_message_id": provider_message_id,
        "provider_timestamp": now,
        "content_text": content_text,
        "media_file": media_file,
        "delivery_status": delivery_status,
        "created_by_user": frappe.session.user,
        "template": template or None,
        "reply_to": reply_to or None,
    })
    msg.insert(ignore_permissions=True)

    auto_claimed = _auto_claim_thread(thread, frappe.session.user)

    frappe.db.sql(
        """
        UPDATE `tabExcom Thread`
        SET last_message_at = %(now)s,
            last_outbound_at = %(now)s,
            last_message_preview = %(preview)s,
            last_message_direction = 'Outbound',
            modified = %(now)s
        WHERE name = %(thread)s
        """,
        {"now": now, "preview": preview, "thread": thread_name},
    )

    rt_data: dict = {
        "thread": thread_name,
        "message": msg.name,
        "omni_identity": thread.omni_identity,
        "direction": "Outbound",
        "preview": preview,
        "delivery_status": delivery_status,
    }
    if auto_claimed:
        rt_data["auto_claimed_by"] = frappe.session.user

    frappe.publish_realtime("excom:message_sent", rt_data, after_commit=True)
    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_name, "event": "new_outbound"},
        after_commit=True,
    )

    return msg.name


def update_delivery_status(
    provider_message_id: str,
    status: str,
    conversation_id: str = "",
    failure_reason: str = "",
) -> None:
    """Update delivery status on an Excom Message by provider_message_id."""
    msg = frappe.db.get_value(
        "Excom Message",
        {"provider_message_id": provider_message_id},
        ["name", "thread"],
        as_dict=True,
    )
    if not msg:
        return

    status_map = {
        "sent": "Sent",
        "delivered": "Delivered",
        "read": "Read",
        "failed": "Failed",
    }
    mapped = status_map.get(status, status)

    updates: dict = {"delivery_status": mapped}
    if mapped == "Failed" and failure_reason:
        updates["failure_reason"] = failure_reason

    frappe.db.set_value("Excom Message", msg.name, updates, update_modified=True)

    frappe.publish_realtime(
        "excom:message_status_updated",
        {
            "message": msg.name,
            "thread": msg.thread,
            "status": mapped,
            "provider_message_id": provider_message_id,
        },
        after_commit=True,
    )

    try:
        from excom.excom.services.broadcast_metrics import attribute_delivery_status
        attribute_delivery_status(provider_message_id, status)
    except Exception:
        frappe.log_error(title="Excom: broadcast metric attribution failed")

    frappe.db.commit()




def _make_preview(text: str) -> str:
    """Truncate text for thread preview."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    return clean[:120]
