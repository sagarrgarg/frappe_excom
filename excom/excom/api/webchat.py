"""
Guest-accessible API endpoints for the Web Chat widget.

All endpoints use session_token-based authentication instead of Frappe login,
allowing unauthenticated website visitors to chat with agents.
"""

import secrets

import frappe
from frappe.rate_limiter import rate_limit
from frappe import _
from frappe.utils import now_datetime


def _validate_session(session_token: str):
    """
    Validate a visitor session token. Returns the session doc as dict
    or throws if invalid/expired.
    """
    if not session_token:
        frappe.throw(_("Session token is required"), frappe.AuthenticationError)

    session = frappe.db.get_value(
        "Excom Visitor Session",
        {"session_token": session_token, "status": "Active"},
        ["name", "thread", "omni_identity", "channel_account", "visitor_name"],
        as_dict=True,
    )

    if not session:
        frappe.throw(_("Invalid or expired session"), frappe.AuthenticationError)

    return session


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def get_config(account_id: str = ""):
    """
    Return the web chat widget configuration for a channel account.
    Called by the widget on initialization to fetch branding/settings.
    """
    if not account_id or not frappe.db.exists("Excom Channel Account", account_id):
        frappe.throw(_("Invalid account"), frappe.DoesNotExistError)

    fields = [
        "account_name",
        "webchat_widget_title",
        "webchat_welcome_message",
        "webchat_offline_message",
        "webchat_color",
        "webchat_position",
        "webchat_prechat_fields",
    ]

    config = frappe.db.get_value(
        "Excom Channel Account", account_id, fields, as_dict=True
    )

    return {
        "title": config.webchat_widget_title or "Chat with us",
        "welcome_message": config.webchat_welcome_message or "",
        "offline_message": config.webchat_offline_message or "We're currently offline. Leave a message!",
        "color": config.webchat_color or "#3b82f6",
        "position": config.webchat_position or "bottom-right",
        "prechat_fields": config.webchat_prechat_fields or '["name","email","phone"]',
    }


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def create_session(
    account_id: str,
    visitor_name: str = "Visitor",
    visitor_email: str = "",
    visitor_phone: str = "",
    page_url: str = "",
    user_agent: str = "",
):
    """
    Create a new visitor chat session.

    Creates an Omni Identity for the visitor, an Excom Thread,
    and an Excom Visitor Session. Returns the session_token for
    subsequent API calls.
    """
    if not account_id or not frappe.db.exists("Excom Channel Account", account_id):
        frappe.throw(_("Invalid account"))

    account = frappe.get_doc("Excom Channel Account", account_id)
    if account.channel != "webchat":
        frappe.throw(_("Account is not a webchat channel"))

    identifier = visitor_email or visitor_phone or f"visitor-{secrets.token_hex(6)}"

    from excom.excom.doctype.omni_identity.omni_identity import resolve_identity

    identity_name = resolve_identity(
        phone=visitor_phone or "",
        channel="webchat",
        channel_user_id=identifier,
        display_name=visitor_name,
        email=visitor_email,
    )

    from excom.excom.services.thread_service import upsert_thread

    thread_name = upsert_thread(identity_name, "webchat", account_id)

    token = secrets.token_urlsafe(32)
    now = now_datetime()

    session = frappe.get_doc({
        "doctype": "Excom Visitor Session",
        "session_token": token,
        "status": "Active",
        "channel_account": account_id,
        "thread": thread_name,
        "omni_identity": identity_name,
        "visitor_name": visitor_name,
        "visitor_email": visitor_email,
        "visitor_phone": visitor_phone,
        "page_url": page_url,
        "user_agent": user_agent,
        "started_at": now,
    })
    session.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_name, "event": "new_webchat_session"},
        after_commit=True,
    )

    return {
        "session_token": token,
        "thread_id": thread_name,
        "welcome_message": frappe.db.get_value(
            "Excom Channel Account", account_id, "webchat_welcome_message"
        ) or "",
    }


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def send_visitor_message(
    session_token: str,
    content: str,
    message_type: str = "Text",
):
    """
    Send a message from the visitor (inbound from the agent's perspective).
    """
    session = _validate_session(session_token)

    if not content or not content.strip():
        frappe.throw(_("Message cannot be empty"))

    now = now_datetime()
    preview = (content.strip())[:120]

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": session.thread,
        "omni_identity": session.omni_identity,
        "channel": "webchat",
        "account_doctype": "Excom Channel Account",
        "account": session.channel_account,
        "direction": "Inbound",
        "message_type": message_type,
        "content_text": content.strip(),
        "delivery_status": "Delivered",
        "provider_message_id": f"wc-{secrets.token_hex(8)}",
        "provider_timestamp": now,
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
        {"now": now, "preview": preview, "thread": session.thread},
    )

    frappe.publish_realtime(
        "excom:message_received",
        {
            "thread": session.thread,
            "message": msg.name,
            "omni_identity": session.omni_identity,
            "direction": "Inbound",
            "preview": preview,
        },
        after_commit=True,
    )

    frappe.db.commit()

    return {"success": True, "message_id": msg.name}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def get_visitor_messages(session_token: str, after: str = ""):
    """
    Fetch conversation messages for a visitor session.
    Supports pagination via `after` (ISO datetime).
    """
    session = _validate_session(session_token)

    conditions = "m.thread = %(thread)s"
    params = {"thread": session.thread}

    if after:
        conditions += " AND m.creation > %(after)s"
        params["after"] = after

    messages = frappe.db.sql(
        f"""
        SELECT m.name AS id, m.direction, m.content_text AS content,
               m.message_type, m.creation AS timestamp,
               CASE WHEN m.direction = 'Outbound' THEN
                   COALESCE(u.full_name, 'Agent')
               ELSE %(visitor_name)s END AS sender_name
        FROM `tabExcom Message` m
        LEFT JOIN `tabUser` u ON u.name = m.created_by_user
        WHERE {conditions}
          AND m.is_internal = 0
        ORDER BY m.creation ASC
        LIMIT 100
        """,
        {**params, "visitor_name": session.visitor_name},
        as_dict=True,
    )

    return messages


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=120, seconds=60)
def end_session(session_token: str):
    """Mark a visitor session as ended."""
    session = _validate_session(session_token)

    frappe.db.set_value("Excom Visitor Session", session.name, {
        "status": "Ended",
        "ended_at": now_datetime(),
    })
    frappe.db.commit()

    return {"success": True}
