"""
Core service layer for Excom Thread and Message operations.

All inbound/outbound message flows route through here to guarantee
consistent thread state, idempotency, and identity resolution.
"""

import json
import re

import frappe
from frappe import _

from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
from excom.excom.utils import get_channel_account


def upsert_thread(
    omni_identity: str,
    channel: str,
    account: str,
    assigned_team: str | None = None,
    assigned_to: str | None = None,
) -> str:
    """
    Find or create an Excom Thread for the given identity + channel + account.
    Returns the thread name.

    Handles concurrent inserts gracefully — if a duplicate key error occurs
    (race between check and insert), returns the existing thread.

    ``assigned_team`` / ``assigned_to`` are stamped only when the thread is
    newly created, so a caller that knows who should own the conversation (a
    transactional send from another app, routed to a chosen Excom Team) lands
    it directly in that team's inbox instead of the unassigned shared inbox. An
    EXISTING thread is never reassigned here — reassignment is a deliberate act
    (an agent claim or a transfer), not a side effect of the next message.
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
            "assigned_team": assigned_team or None,
            "assigned_to": assigned_to or None,
            "unread_count": 0,
            "last_message_at": frappe.utils.now_datetime(),
        })
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
    channel_user_id: str = "",
    wa_user_id: str = "",
    wa_username: str = "",
    wa_waba: str = "",
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

    identity_name = resolve_identity_internal(
        phone=phone,
        channel=channel,
        # PSID / IGSID for Meta DMs; phone for WhatsApp, or the business-scoped user id when the
        # person shares a username instead of a number
        channel_user_id=channel_user_id or phone or wa_user_id,
        display_name=display_name,
        wa_user_id=wa_user_id,
        wa_username=wa_username,
        wa_waba=wa_waba,
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

    was_closed = frappe.db.get_value("Excom Thread", thread_name, ["status", "closure_outcome"], as_dict=True)
    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_name,
        "omni_identity": identity_name,
        "channel": channel,
        "account_doctype": "Excom Channel Account",
        "account": account,
        "direction": "Inbound",
        "message_type": message_type,
        "provider_message_id": provider_message_id or None,
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
    if was_closed and was_closed.status == "Closed":
        _reopened_by_customer(thread_name, identity_name, was_closed.closure_outcome)

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
    if thread.assigned_to and frappe.db.get_value("User", thread.assigned_to, "enabled"):
        return False  # an assignee whose user is disabled counts as unassigned

    from excom.excom.services.crm_visibility import team_for_user

    update: dict = {"assigned_to": user}
    team = team_for_user(user)
    if team:
        update["assigned_team"] = team

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

    elif thread.channel in ("instagram", "messenger"):
        from excom.excom.channels.meta_dm import service as meta_dm
        account = frappe.get_doc(account_doctype, account_name)
        to_id = meta_dm.recipient_id(identity, thread.channel)
        if media_file:
            result = meta_dm.send_media(account, to_id, message_type, media_file, content_text, thread=thread)
        else:
            result = meta_dm.send_text(account, to_id, content_text, thread=thread)
        provider_message_id = result.get("provider_message_id", "")
    elif thread.channel == "whatsapp":
        # A username-only contact has no number but can be reached by business-scoped user id.
        to_number = identity.primary_whatsapp or identity.primary_phone or identity.get("wa_bsuid")
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
        # providers must not be able to write an arbitrary value into the Select field
        delivery_status = str(result.get("status") or "Sent").title()
        if delivery_status not in ("Queued", "Scheduled", "Sent", "Delivered", "Read", "Failed"):
            delivery_status = "Sent"

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
        "provider_message_id": provider_message_id or None,
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


# A status only moves forward: Meta does not guarantee webhook order, so a late "delivered"
# must not overwrite "read". "failed" can follow "sent" (Meta drops held or capped messages
# after accepting them) but never a delivered or read message.
_STATUS_RANK = {"Queued": 0, "Scheduled": 0, "Sent": 1, "Delivered": 2, "Read": 3}
_STATUS_MAP = {"sent": "Sent", "delivered": "Delivered", "read": "Read", "played": "Read", "failed": "Failed"}


def record_outbound_message(
    thread,
    content_text: str,
    message_type: str,
    content_json: dict = None,
    provider_message_id: str = "",
    delivery_status: str = "Sent",
    reply_to: str = "",
    reference: tuple = None,
    template: str = "",
) -> str:
    """Store a message excom already sent (interactive, Flow…) on its thread and bump the thread.

    `thread` is an Excom Thread doc or name. The send itself is the caller's job; this keeps the
    inbox, counters and realtime events the same as for a typed reply (it does not claim the
    thread for anyone: automated sends are not an agent's reply).
    """
    if isinstance(thread, str):
        thread = frappe.get_doc("Excom Thread", thread)
    now = frappe.utils.now_datetime()
    preview = _make_preview(content_text)
    doc = {
        "doctype": "Excom Message",
        "thread": thread.name,
        "omni_identity": thread.omni_identity,
        "channel": thread.channel,
        "account_doctype": thread.account_doctype,
        "account": thread.account,
        "direction": "Outbound",
        "message_type": message_type,
        "provider_message_id": provider_message_id or None,
        "provider_timestamp": now,
        "content_text": content_text,
        "content_json": json.dumps(content_json) if content_json else "{}",
        "delivery_status": delivery_status,
        "created_by_user": frappe.session.user,
        "reply_to": reply_to or None,
        "template": template or None,
    }
    if reference:
        doc["reference_doctype"], doc["reference_name"] = reference
    msg = frappe.get_doc(doc).insert(ignore_permissions=True)
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
        {"now": now, "preview": preview, "thread": thread.name},
    )
    frappe.publish_realtime("excom:message_sent", {
        "thread": thread.name, "message": msg.name, "omni_identity": thread.omni_identity,
        "direction": "Outbound", "preview": preview, "delivery_status": delivery_status,
    }, after_commit=True)
    frappe.publish_realtime("excom:thread_updated", {"thread": thread.name, "event": "new_outbound"}, after_commit=True)
    return msg.name


def update_delivery_status(
    provider_message_id: str,
    status: str,
    conversation_id: str = "",
    failure_reason: str = "",
    failure_code=None,
    pricing_category: str = "",
    pricing_type: str = "",
) -> str:
    """Update delivery status on an Excom Message by provider_message_id.

    Returns the Excom Message name ('' when the message is not ours).
    """
    msg = frappe.db.get_value(
        "Excom Message",
        {"provider_message_id": provider_message_id},
        ["name", "thread", "delivery_status"],
        as_dict=True,
    )
    if not msg:
        return ""

    mapped = _STATUS_MAP.get(status, status)
    current = msg.delivery_status or ""
    updates: dict = {}
    if mapped == "Failed":
        if current not in ("Delivered", "Read"):
            updates["delivery_status"] = "Failed"
            if failure_reason:
                updates["failure_reason"] = failure_reason[:1000]
            if failure_code not in (None, ""):
                try:
                    updates["failure_code"] = int(failure_code)
                except (TypeError, ValueError):
                    pass
    elif mapped in _STATUS_RANK and (current == "Failed" or _STATUS_RANK[mapped] > _STATUS_RANK.get(current, -1)):
        # a real delivery after a "failed" (a retried send reusing the id) wins too
        updates["delivery_status"] = mapped
    if pricing_category:
        updates["wa_pricing_category"] = pricing_category
    if pricing_type:
        updates["wa_pricing_type"] = pricing_type
    if not updates:
        # Nothing to change here, but a broadcast still counts a late "delivered" event.
        _attribute_broadcast_status(provider_message_id, status)
        return msg.name

    frappe.db.set_value("Excom Message", msg.name, updates, update_modified=True)
    mapped = updates.get("delivery_status", current)

    frappe.publish_realtime(
        "excom:message_status_updated",
        {
            "message": msg.name,
            "thread": msg.thread,
            "status": mapped,
            "provider_message_id": provider_message_id or None,
        },
        after_commit=True,
    )

    _attribute_broadcast_status(provider_message_id, status)

    frappe.db.commit()
    return msg.name


def _attribute_broadcast_status(provider_message_id: str, status: str) -> None:
    try:
        from excom.excom.services.broadcast_metrics import attribute_delivery_status
        attribute_delivery_status(provider_message_id, status)
    except Exception:
        frappe.log_error(title="Excom: broadcast metric attribution failed")




def _make_preview(text: str) -> str:
    """Truncate text for thread preview."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    return clean[:120]



def _reopened_by_customer(thread_name: str, identity_name: str, outcome: str | None) -> None:
    """Closing a chat is not a wall: the next customer message reopens it. Clear the closure so the row
    stops saying 'Closed · …', and leave a trace on the record. The CRM record's status is NOT changed —
    a Lead marked Do Not Contact stays that way until an agent reopens it deliberately."""
    frappe.db.set_value("Excom Thread", thread_name, {"closure_outcome": None, "closure_reason": None, "closed_by": None, "closed_at": None}, update_modified=False)
    try:
        from excom.excom.services import crm_gateway as gw
        text = "<b>Reopened by a new customer message</b>" + (f" (was closed as {frappe.utils.escape_html(outcome)})" if outcome else "")
        frappe.get_doc("Excom Thread", thread_name).add_comment("Comment", text)
        recs = gw.find_open_records_for_identity(identity_name) if identity_name else []
        if recs:
            gw.add_timeline_comment(recs[0], text)
    except Exception:
        frappe.log_error(title="Excom: reopen trace failed", message=frappe.get_traceback())
