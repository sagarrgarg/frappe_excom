import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, now_datetime

from excom.excom.services.thread_service import send_outbound_message
from excom.excom.utils.errors import ExcomProviderError, ExcomRateLimitError

EXCOM_ROLES = {"System Manager", "Excom Manager", "Excom User"}

MAX_MESSAGE_LENGTH = {
    "whatsapp": 4096,
    "email": 100_000,
    "webchat": 10_000,
}
DEFAULT_MAX_LENGTH = 10_000


def _sanitize_message(text: str, thread_id: str = "") -> str:
    """Strip dangerous HTML and enforce max length per channel."""
    if not text:
        return text

    text = frappe.utils.sanitize_html(text)

    channel = ""
    if thread_id:
        channel = frappe.db.get_value("Excom Thread", thread_id, "channel") or ""

    max_len = MAX_MESSAGE_LENGTH.get(channel, DEFAULT_MAX_LENGTH)
    if len(text) > max_len:
        frappe.throw(
            _("Message exceeds maximum length of {0} characters for {1}").format(
                max_len, channel or "this channel"
            )
        )

    return text


def _check_excom_access() -> None:
    """Block users without any Excom role from accessing chat APIs."""
    if not EXCOM_ROLES.intersection(frappe.get_roles(frappe.session.user)):
        frappe.throw(_("You do not have access to Excom"), frappe.PermissionError)


MANAGER_ROLES = {"System Manager", "Excom Manager"}


def _check_manager_access() -> None:
    """Block users without Excom Manager or System Manager role."""
    if not MANAGER_ROLES.intersection(frappe.get_roles(frappe.session.user)):
        frappe.throw(_("You need Excom Manager role to perform this action"), frappe.PermissionError)


@frappe.whitelist()
@rate_limit(key="user", limit=60, seconds=60)
def get_threads(
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    team: str = "",
    broadcast: str = "",
    broadcast_status: str = "",
    channel: str = "",
    account: str = "",
    date_from: str = "",
    date_to: str = "",
):
    """
    Inbox query: returns threads ordered by last_message_at.
    Enriches each thread with contact data from Omni Identity and User.

    Channel/account filtering:
    - channel="whatsapp": only WhatsApp threads
    - account="ACC-00001": only threads on this specific Excom Channel Account

    Date range filtering:
    - date_from="2026-03-01": threads with last_message_at >= this date
    - date_to="2026-03-29": threads with last_message_at <= end of this date

    Team filtering:
    - team="" (default): threads in user's teams + General (unassigned) + directly assigned
    - team="__general__": only unassigned threads (assigned_team IS NULL)
    - team="<team_name>": only threads assigned to that team

    Broadcast filtering:
    - broadcast="EXBC-00001": only threads whose omni_identity received this broadcast
    - broadcast_status="Sent": further filter by delivery status in the broadcast log
    """
    _check_excom_access()
    limit = int(limit)
    offset = int(offset)

    conditions = "t.status NOT IN ('Closed', 'Spam')"
    params: dict = {"limit": limit, "offset": offset, "current_user": frappe.session.user}

    if search:
        conditions += " AND (t.display_name LIKE %(search)s OR t.primary_phone LIKE %(search)s)"
        params["search"] = f"%{search}%"

    if channel:
        conditions += " AND t.channel = %(channel_filter)s"
        params["channel_filter"] = channel

    if account:
        conditions += " AND t.account = %(account_filter)s"
        params["account_filter"] = account

    if date_from:
        conditions += " AND t.last_message_at >= %(date_from)s"
        params["date_from"] = date_from

    if date_to:
        conditions += " AND t.last_message_at <= %(date_to)s"
        params["date_to"] = f"{date_to} 23:59:59"

    if broadcast:
        bl_filter = ""
        if broadcast_status:
            bl_filter = " AND bl.status = %(broadcast_status)s"
            params["broadcast_status"] = broadcast_status
        conditions += (
            " AND t.omni_identity IN ("
            "   SELECT bl.omni_identity FROM `tabExcom Broadcast Log` bl"
            f"   WHERE bl.broadcast = %(broadcast)s{bl_filter}"
            " )"
        )
        params["broadcast"] = broadcast

    user_roles = set(frappe.get_roles(frappe.session.user))
    is_manager = bool(user_roles & {"System Manager", "Excom Manager"})

    if team == "__general__":
        if not is_manager:
            from excom.excom.doctype.excom_team.excom_team import get_user_teams
            if "General" not in get_user_teams():
                frappe.throw(_("You do not have access to the General inbox"), frappe.PermissionError)
        conditions += " AND t.assigned_team IS NULL"
    elif team:
        conditions += " AND t.assigned_team = %(team_filter)s"
        params["team_filter"] = team
    elif is_manager:
        pass
    else:
        from excom.excom.doctype.excom_team.excom_team import get_user_teams
        user_teams = get_user_teams()
        if user_teams:
            conditions += (
                " AND (t.assigned_team IN %(user_teams)s"
                " OR t.assigned_to = %(current_user)s)"
            )
            params["user_teams"] = user_teams
        else:
            conditions += " AND t.assigned_to = %(current_user)s"

    broadcast_badge_join = ""
    broadcast_badge_col = ""
    if broadcast:
        broadcast_badge_join = (
            " LEFT JOIN `tabExcom Broadcast Log` bl_badge"
            " ON bl_badge.omni_identity = t.omni_identity AND bl_badge.broadcast = %(broadcast)s"
        )
        broadcast_badge_col = ", bl_badge.status AS broadcast_delivery_status"

    threads = frappe.db.sql(
        f"""
        SELECT t.name, t.display_name, t.primary_phone, t.last_message_at,
               t.last_message_preview, t.last_message_direction, t.unread_count,
               t.status, t.assigned_to, t.assigned_team, t.omni_identity,
               t.channel, t.account,
               oi.primary_email,
               u.full_name AS assigned_to_name, u.user_image AS assigned_to_avatar,
               et.team_name AS assigned_team_name
               {broadcast_badge_col}
        FROM `tabExcom Thread` t
        LEFT JOIN `tabOmni Identity` oi ON oi.name = t.omni_identity
        LEFT JOIN `tabUser` u ON u.name = t.assigned_to
        LEFT JOIN `tabExcom Team` et ON et.name = t.assigned_team
        {broadcast_badge_join}
        WHERE {conditions}
        ORDER BY t.last_message_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
        as_dict=True,
    )

    if threads:
        _enrich_company(threads)
        _enrich_tags(threads)

    return threads


@frappe.whitelist()
def get_recent_broadcasts_for_filter(limit: int = 20) -> list:
    """Return recent submitted broadcasts for the inbox filter dropdown."""
    _check_excom_access()
    return frappe.db.sql(
        """
        SELECT b.name, b.broadcast_name, b.channel, b.status,
               b.total_recipients, b.sent_count, b.failed_count,
               b.creation
        FROM `tabExcom Broadcast` b
        WHERE b.docstatus = 1
        ORDER BY b.creation DESC
        LIMIT %(limit)s
        """,
        {"limit": int(limit)},
        as_dict=True,
    )


def _enrich_company(threads: list):
    """
    Batch-fetch company from linked Contact/Lead for each unique omni_identity.
    Avoids N+1 by fetching all at once.
    """
    identity_names = list({t.omni_identity for t in threads if t.get("omni_identity")})
    if not identity_names:
        return

    links = frappe.db.sql(
        """
        SELECT parent, linked_doctype, linked_name
        FROM `tabOmni Identity Link`
        WHERE parent IN %(ids)s AND linked_doctype IN ('Contact', 'Lead', 'Customer')
        ORDER BY parent, creation ASC
        """,
        {"ids": identity_names},
        as_dict=True,
    )

    identity_company = {}
    for link in links:
        if link.parent in identity_company:
            continue
        company = None
        if link.linked_doctype == "Customer":
            company = frappe.db.get_value("Customer", link.linked_name, "customer_name")
        elif link.linked_doctype == "Lead":
            company = frappe.db.get_value("Lead", link.linked_name, "company_name")
        elif link.linked_doctype == "Contact":
            company = frappe.db.get_value("Contact", link.linked_name, "company_name")
        if company:
            identity_company[link.parent] = company

    for t in threads:
        t["company"] = identity_company.get(t.get("omni_identity"), "")


def _enrich_tags(threads: list):
    """Batch-fetch tags for all threads in one query."""
    thread_names = [t.name for t in threads]
    if not thread_names:
        return

    tag_rows = frappe.db.sql(
        """
        SELECT tt.parent, tt.tag, t.color, t.tag_name
        FROM `tabExcom Thread Tag` tt
        JOIN `tabExcom Tag` t ON t.name = tt.tag
        WHERE tt.parent IN %(names)s
        ORDER BY tt.added_on ASC
        """,
        {"names": thread_names},
        as_dict=True,
    )

    thread_tags: dict = {}
    for row in tag_rows:
        thread_tags.setdefault(row.parent, []).append({
            "tag": row.tag,
            "tag_name": row.tag_name,
            "color": row.color,
        })

    for t in threads:
        t["tags"] = thread_tags.get(t.name, [])


@frappe.whitelist()
@rate_limit(key="user", limit=120, seconds=60)
def get_messages(thread_id: str, limit: int = 50, before: str = ""):
    """Load messages for a thread, ordered chronologically."""
    _check_excom_access()
    limit = int(limit)
    params = {"thread": thread_id, "limit": limit}

    conditions = "m.thread = %(thread)s"
    if before:
        conditions += " AND m.creation < %(before)s"
        params["before"] = before

    messages = frappe.db.sql(
        f"""
        SELECT m.name, m.direction, m.message_type, m.content_text,
               m.media_file, m.delivery_status, m.creation,
               m.provider_timestamp,
               m.provider_message_id, m.reply_to,
               m.created_by_user, m.is_internal,
               m.is_pinned, m.pinned_by, m.reactions,
               m.failure_reason,
               CASE WHEN m.message_type = 'Email' THEN m.content_json ELSE NULL END AS content_json,
               u.full_name AS sender_name,
               rt.content_text AS reply_to_content,
               rt.direction AS reply_to_direction,
               ru.full_name AS reply_to_sender
        FROM `tabExcom Message` m
        LEFT JOIN `tabUser` u ON u.name = m.created_by_user
        LEFT JOIN `tabExcom Message` rt ON rt.name = m.reply_to
        LEFT JOIN `tabUser` ru ON ru.name = rt.created_by_user
        WHERE {conditions}
        ORDER BY COALESCE(m.provider_timestamp, m.creation) ASC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )

    for msg in messages:
        if msg.reactions and isinstance(msg.reactions, str):
            try:
                msg.reactions = json.loads(msg.reactions)
            except (json.JSONDecodeError, TypeError):
                msg.reactions = {}

    # Auto-assign the thread to whoever opens it first if it is unassigned
    auto_claimed_by = None
    thread_row = frappe.db.get_value(
        "Excom Thread", thread_id, ["assigned_to", "status"], as_dict=True
    )
    if thread_row and not thread_row.assigned_to and thread_row.status not in ("Closed", "Spam"):
        from excom.excom.services.thread_service import _auto_claim_thread
        thread_doc = frappe.get_doc("Excom Thread", thread_id)
        claimed = _auto_claim_thread(thread_doc, frappe.session.user)
        if claimed:
            auto_claimed_by = frappe.session.user
            frappe.db.commit()
            frappe.publish_realtime(
                "excom:thread_updated",
                {"thread_id": thread_id, "assigned_to": frappe.session.user},
                user=frappe.session.user,
            )

    return {"messages": messages, "auto_claimed_by": auto_claimed_by}


@frappe.whitelist()
@rate_limit(key="user", limit=30, seconds=60)
def send_message(thread_id: str, message: str = "", message_type: str = "Text",
                 media_url: str = "", reply_to: str = "", sticker_name: str = ""):
    """
    Send a message on an existing thread.

    Args:
        thread_id: Excom Thread name
        message: Text content (required for Text, optional caption for media)
        message_type: Text | Image | Video | Audio | Document | Sticker
        media_url: Frappe file URL for media messages
        reply_to: Excom Message name being replied to
        sticker_name: Excom Sticker name (for Sticker type)
    """
    _check_excom_access()
    message = _sanitize_message(message, thread_id)

    if message_type == "Sticker" and sticker_name:
        sticker = frappe.get_doc("Excom Sticker", sticker_name)
        if not sticker.media_id and not sticker.sticker_file:
            frappe.throw(_("This sticker has no media. Please re-upload it."))
        media_url = media_url or sticker.sticker_file or ""

    try:
        msg_name = send_outbound_message(
            thread_name=thread_id,
            content_text=message,
            message_type=message_type,
            media_file=media_url,
            sticker_name=sticker_name,
            reply_to=reply_to,
        )
        frappe.db.commit()
        return {"success": True, "message_name": msg_name}
    except Exception as e:
        frappe.log_error(
            title="Excom send_message failed",
            message=f"thread_id={thread_id}\n{str(e)}",
        )
        raise


@frappe.whitelist()
def get_stickers(pack: str = ""):
    """
    Fetch available stickers, optionally filtered by pack.

    Args:
        pack: Filter by sticker pack name

    Returns:
        list of sticker dicts with name, sticker_name, pack, sticker_file,
        media_id, is_animated
    """
    _check_excom_access()
    filters = {"enabled": 1}
    if pack:
        filters["pack"] = pack

    stickers = frappe.get_all(
        "Excom Sticker",
        filters=filters,
        fields=["name", "sticker_name", "pack", "sticker_file", "media_id", "is_animated"],
        order_by="pack asc, sticker_name asc",
    )
    packs = frappe.get_all(
        "Excom Sticker",
        filters={"enabled": 1},
        fields=["pack"],
        group_by="pack",
        order_by="pack asc",
    )
    return {
        "stickers": stickers,
        "packs": [p["pack"] for p in packs],
    }


@frappe.whitelist()
def mark_read(thread_id: str):
    """Reset unread count for a thread."""
    _check_excom_access()
    frappe.db.set_value("Excom Thread", thread_id, "unread_count", 0)
    return {"success": True}


@frappe.whitelist()
def mark_unread(thread_id: str):
    """Mark a thread as unread by setting unread_count to 1 if currently zero."""
    _check_excom_access()
    current = frappe.db.get_value("Excom Thread", thread_id, "unread_count") or 0
    if current == 0:
        frappe.db.set_value("Excom Thread", thread_id, "unread_count", 1)
    return {"success": True}


@frappe.whitelist()
def get_linked_entities(omni_identity: str):
    """
    Fetch all linked ERP entities from the Omni Identity's linked_entities child table.
    Returns list of {linked_doctype, linked_name, role, title} for display in the sidebar.
    """
    _check_excom_access()
    if not omni_identity or not frappe.db.exists("Omni Identity", omni_identity):
        return []

    links = frappe.get_all(
        "Omni Identity Link",
        filters={"parent": omni_identity},
        fields=["linked_doctype", "linked_name", "role"],
        order_by="creation desc",
    )

    result = []
    for link in links:
        title = link.linked_name
        try:
            doc = frappe.get_cached_doc(link.linked_doctype, link.linked_name)
            title = (
                getattr(doc, "customer_name", None)
                or getattr(doc, "lead_name", None)
                or getattr(doc, "company_name", None)
                or getattr(doc, "supplier_name", None)
                or (
                    (getattr(doc, "first_name", None) or "")
                    + " "
                    + (getattr(doc, "last_name", None) or "")
                ).strip()
                or getattr(doc, "email_id", None)
            )
        except Exception:
            pass
        result.append(
            {
                "linked_doctype": link.linked_doctype,
                "linked_name": link.linked_name,
                "role": link.role or "Unknown",
                "title": str(title).strip() if title else link.linked_name,
            }
        )
    return result


@frappe.whitelist()
def get_conversation_stats(omni_identity: str):
    """
    Returns real conversation statistics across ALL threads for an Omni Identity:
    - total_messages: total Excom Message count
    - inbound_count / outbound_count: breakdown by direction
    - erp_users_replied: whether any ERP user has sent an outbound message
    - avg_response_time_seconds: average seconds between an inbound message
      and the next outbound reply (calculated per-thread, then averaged)
    - channels: list of distinct channels used
    """
    _check_excom_access()
    empty = {
        "total_messages": 0,
        "inbound_count": 0,
        "outbound_count": 0,
        "erp_users_replied": False,
        "avg_response_time_seconds": None,
        "channels": [],
    }

    if not omni_identity:
        return empty

    thread_names = frappe.get_all(
        "Excom Thread",
        filters={"omni_identity": omni_identity},
        pluck="name",
    )

    if not thread_names:
        return empty

    channels = list(set(
        frappe.get_all(
            "Excom Thread",
            filters={"name": ["in", thread_names]},
            pluck="channel",
        )
    ))

    messages = frappe.db.sql(
        """
        SELECT direction, creation, thread
        FROM `tabExcom Message`
        WHERE thread IN %(threads)s
        ORDER BY thread, creation ASC
        """,
        {"threads": thread_names},
        as_dict=True,
    )

    total = len(messages)
    inbound = sum(1 for m in messages if m.direction == "Inbound")
    outbound = sum(1 for m in messages if m.direction == "Outbound")
    erp_replied = outbound > 0

    response_times = []
    last_inbound_at = None
    current_thread = None
    for m in messages:
        if m.thread != current_thread:
            current_thread = m.thread
            last_inbound_at = None
        if m.direction == "Inbound":
            last_inbound_at = m.creation
        elif m.direction == "Outbound" and last_inbound_at is not None:
            delta = (m.creation - last_inbound_at).total_seconds()
            response_times.append(delta)
            last_inbound_at = None

    avg_response = None
    if response_times:
        avg_response = flt(sum(response_times) / len(response_times), 0)

    return {
        "total_messages": total,
        "inbound_count": inbound,
        "outbound_count": outbound,
        "erp_users_replied": erp_replied,
        "avg_response_time_seconds": avg_response,
        "channels": channels,
    }


@frappe.whitelist()
def get_ai_suggestions(thread_id: str, force_refresh: bool = False):
    """
    Returns AI suggestions for a thread.
    Phase 2 stub: computes basic insights from message history.
    Full LLM integration deferred to Phase 5.
    """
    _check_excom_access()
    if not thread_id or not frappe.db.exists("Excom Thread", thread_id):
        return _empty_ai_response()

    thread = frappe.db.get_value(
        "Excom Thread", thread_id,
        ["omni_identity", "last_message_at", "display_name"],
        as_dict=True,
    )

    messages = frappe.db.sql(
        """
        SELECT direction, creation, content_text
        FROM `tabExcom Message`
        WHERE thread = %(thread)s
        ORDER BY creation DESC
        LIMIT 50
        """,
        {"thread": thread_id},
        as_dict=True,
    )

    total = len(messages)
    inbound = [m for m in messages if m.direction == "Inbound"]
    outbound = [m for m in messages if m.direction == "Outbound"]

    suggested_replies = []
    if outbound:
        seen = set()
        for m in outbound[:10]:
            text = (m.content_text or "").strip()
            if text and len(text) > 10 and text not in seen:
                seen.add(text)
                suggested_replies.append({"text": text, "confidence": 0.7})
            if len(suggested_replies) >= 3:
                break

    summary_text = f"{total} messages exchanged"
    if thread.last_message_at:
        summary_text += f". Last activity: {thread.last_message_at}"

    response_times = []
    sorted_msgs = sorted(messages, key=lambda m: m.creation)
    last_inbound_at = None
    for m in sorted_msgs:
        if m.direction == "Inbound":
            last_inbound_at = m.creation
        elif m.direction == "Outbound" and last_inbound_at:
            response_times.append((m.creation - last_inbound_at).total_seconds())
            last_inbound_at = None

    avg_rt = sum(response_times) / len(response_times) if response_times else None
    best_time = "Unknown"
    if inbound:
        hours = [m.creation.hour for m in inbound]
        from collections import Counter
        most_common_hour = Counter(hours).most_common(1)[0][0]
        best_time = f"{most_common_hour}:00 - {most_common_hour + 1}:00"

    next_actions = _get_next_actions(thread.omni_identity)

    return {
        "suggested_replies": suggested_replies,
        "summary": {
            "text": summary_text,
            "updated_at": str(now_datetime()),
            "sentiment": "neutral",
        },
        "next_actions": next_actions,
        "insights": {
            "response_pattern": f"~{int(avg_rt / 60)}m avg reply time" if avg_rt else "Not enough data",
            "engagement_rate": round(len(outbound) / max(len(inbound), 1), 2) if inbound else 0,
            "best_contact_time": best_time,
        },
    }


def _empty_ai_response():
    return {
        "suggested_replies": [],
        "summary": {"text": "No data available", "updated_at": "", "sentiment": "neutral"},
        "next_actions": [],
        "insights": {"response_pattern": "—", "engagement_rate": 0, "best_contact_time": "—"},
    }


def _get_next_actions(omni_identity: str):
    """Derive next actions from linked ERP entity statuses."""
    actions = []
    if not omni_identity:
        return actions

    links = frappe.get_all(
        "Omni Identity Link",
        filters={"parent": omni_identity},
        fields=["linked_doctype", "linked_name"],
        limit=5,
    )

    for link in links:
        dt, dn = link.linked_doctype, link.linked_name
        if dt == "Lead":
            status = frappe.db.get_value("Lead", dn, "status")
            if status in ("Lead", "Open"):
                actions.append({"action": f"Follow up on Lead {dn}", "priority": "high", "due": ""})
        elif dt == "Customer":
            actions.append({"action": f"Review Customer {dn} account", "priority": "low", "due": ""})

    return actions[:3]


@frappe.whitelist()
def assign_thread(thread_id: str, user: str = ""):
    """
    Assign a thread to the current user or a specified user.
    Used by the "Take Over" button.
    """
    _check_excom_access()
    if not user:
        user = frappe.session.user

    frappe.db.set_value("Excom Thread", thread_id, "assigned_to", user)
    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_id, "event": "assigned"},
        after_commit=True,
    )
    return {"success": True, "assigned_to": user}


@frappe.whitelist()
def transfer_thread(thread_id: str, target_team: str, target_user: str = "", note: str = "") -> dict:
    """Transfer a thread to another team and optionally a specific member.

    When target_user is provided the thread is assigned directly to that
    user within the target team.  Otherwise, assigned_to is cleared so
    any member of the target team can pick it up.
    """
    _check_excom_access()
    thread = frappe.db.get_value(
        "Excom Thread", thread_id, ["assigned_team", "display_name"], as_dict=True
    )
    if not thread:
        frappe.throw(_("Thread not found"))

    from_team = thread.assigned_team or ""

    frappe.db.set_value(
        "Excom Thread",
        thread_id,
        {"assigned_team": target_team, "assigned_to": target_user or ""},
    )

    frappe.get_doc({
        "doctype": "Excom Thread Transfer Log",
        "thread": thread_id,
        "from_team": from_team,
        "to_team": target_team,
        "transferred_by": frappe.session.user,
        "note": note[:500] if note else "",
        "transferred_at": now_datetime(),
    }).insert(ignore_permissions=True)

    frappe.publish_realtime(
        "excom:thread_transferred",
        {
            "thread": thread_id,
            "from_team": from_team,
            "to_team": target_team,
            "target_user": target_user,
            "transferred_by": frappe.session.user,
        },
        after_commit=True,
    )

    frappe.db.commit()
    return {"success": True, "from_team": from_team, "to_team": target_team, "assigned_to": target_user}


@frappe.whitelist()
def claim_thread(thread_id: str, team: str = "") -> dict:
    """
    Claim a thread from the General inbox.

    Sets assigned_team to the provided team (or user's first team)
    and assigned_to to the current user.
    """
    _check_excom_access()
    if not team:
        from excom.excom.doctype.excom_team.excom_team import get_user_teams
        user_teams = get_user_teams()
        if not user_teams:
            frappe.throw(_("You are not a member of any team"))
        team = user_teams[0]
    else:
        from excom.excom.doctype.excom_team.excom_team import get_user_teams
        user_teams = get_user_teams()
        if team not in user_teams:
            frappe.throw(_("You are not a member of team {0}").format(team))

    frappe.db.set_value(
        "Excom Thread",
        thread_id,
        {"assigned_team": team, "assigned_to": frappe.session.user},
    )

    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_id, "event": "claimed", "team": team},
        after_commit=True,
    )

    return {"success": True, "team": team, "assigned_to": frappe.session.user}


@frappe.whitelist()
def get_transfer_history(thread_id: str) -> list:
    """Return the transfer log for a thread."""
    _check_excom_access()
    return frappe.db.sql(
        """
        SELECT tl.from_team, tl.to_team, tl.transferred_by, tl.note,
               tl.transferred_at,
               ft.team_name AS from_team_name,
               tt.team_name AS to_team_name,
               u.full_name AS transferred_by_name
        FROM `tabExcom Thread Transfer Log` tl
        LEFT JOIN `tabExcom Team` ft ON ft.name = tl.from_team
        LEFT JOIN `tabExcom Team` tt ON tt.name = tl.to_team
        LEFT JOIN `tabUser` u ON u.name = tl.transferred_by
        WHERE tl.thread = %(thread)s
        ORDER BY tl.transferred_at DESC
        """,
        {"thread": thread_id},
        as_dict=True,
    )


@frappe.whitelist()
def get_response_metrics(omni_identity: str):
    """
    Calculates average response time for an Omni Identity.
    Time between last inbound and next outbound per thread.
    """
    _check_excom_access()
    if not omni_identity:
        return {"avg_response_time_seconds": None}

    thread_names = frappe.get_all(
        "Excom Thread",
        filters={"omni_identity": omni_identity},
        pluck="name",
    )
    if not thread_names:
        return {"avg_response_time_seconds": None}

    messages = frappe.db.sql(
        """
        SELECT direction, creation, thread
        FROM `tabExcom Message`
        WHERE thread IN %(threads)s
        ORDER BY thread, creation ASC
        """,
        {"threads": thread_names},
        as_dict=True,
    )

    response_times = []
    last_inbound_at = None
    current_thread = None
    for m in messages:
        if m.thread != current_thread:
            current_thread = m.thread
            last_inbound_at = None
        if m.direction == "Inbound":
            last_inbound_at = m.creation
        elif m.direction == "Outbound" and last_inbound_at:
            response_times.append((m.creation - last_inbound_at).total_seconds())
            last_inbound_at = None

    avg = flt(sum(response_times) / len(response_times), 0) if response_times else None
    return {"avg_response_time_seconds": avg}


@frappe.whitelist()
def get_canned_responses(search: str = "", channel: str = ""):
    """
    Returns canned responses filtered by search text and channel.
    Includes global responses and current user's personal ones.
    """
    _check_excom_access()
    filters = [
        ["is_global", "=", 1],
    ]

    user_filters = [
        ["is_global", "=", 0],
        ["owner", "=", frappe.session.user],
    ]

    fields = ["name", "title", "shortcode", "content", "category", "channel"]

    global_responses = frappe.get_all(
        "Excom Canned Response",
        filters=filters,
        fields=fields,
        order_by="title asc",
        limit=50,
    )

    personal_responses = frappe.get_all(
        "Excom Canned Response",
        filters=user_filters,
        fields=fields,
        order_by="title asc",
        limit=50,
    )

    all_responses = global_responses + personal_responses

    if channel and channel != "All":
        all_responses = [
            r for r in all_responses
            if r.channel in ("All", channel)
        ]

    if search:
        q = search.lower()
        all_responses = [
            r for r in all_responses
            if q in (r.title or "").lower()
            or q in (r.shortcode or "").lower()
            or q in (r.content or "").lower()
        ]

    return all_responses


@frappe.whitelist()
@rate_limit(key="user", limit=30, seconds=60)
def send_internal_note(thread_id: str, content: str):
    """
    Creates an internal note on a thread visible only to agents.
    Does NOT send anything to the customer via any channel.

    Args:
        thread_id: Excom Thread name
        content: Note text
    """
    _check_excom_access()
    if not content or not content.strip():
        frappe.throw(_("Note content cannot be empty"))

    content = _sanitize_message(content, thread_id)

    thread = frappe.get_doc("Excom Thread", thread_id)

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_id,
        "omni_identity": thread.omni_identity,
        "direction": "Outbound",
        "message_type": "Text",
        "channel": thread.channel,
        "account_doctype": thread.account_doctype,
        "account": thread.account,
        "delivery_status": "Read",
        "content_text": content.strip(),
        "is_internal": 1,
        "created_by_user": frappe.session.user,
    })
    msg.insert(ignore_permissions=True)

    frappe.publish_realtime(
        "excom:message_received",
        {
            "thread": thread_id,
            "message": msg.name,
            "omni_identity": thread.omni_identity,
            "direction": "Outbound",
            "preview": content.strip()[:100],
            "is_internal": True,
        },
        after_commit=True,
    )

    frappe.db.commit()

    return {"success": True, "message_name": msg.name}


@frappe.whitelist()
def pin_message(message_name: str):
    """Pin a message. Sets is_pinned=1 and pinned_by to current user."""
    _check_excom_access()
    if not frappe.db.exists("Excom Message", message_name):
        frappe.throw(_("Message not found"))
    frappe.db.set_value("Excom Message", message_name, {
        "is_pinned": 1,
        "pinned_by": frappe.session.user,
    })
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    frappe.publish_realtime("excom:message_pinned", {
        "message": message_name, "thread": thread, "pinned": True,
    }, after_commit=True)
    return {"success": True}


@frappe.whitelist()
def unpin_message(message_name: str):
    """Unpin a message."""
    _check_excom_access()
    if not frappe.db.exists("Excom Message", message_name):
        frappe.throw(_("Message not found"))
    frappe.db.set_value("Excom Message", message_name, {
        "is_pinned": 0,
        "pinned_by": "",
    })
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    frappe.publish_realtime("excom:message_pinned", {
        "message": message_name, "thread": thread, "pinned": False,
    }, after_commit=True)
    return {"success": True}


@frappe.whitelist()
def get_pinned_messages(thread_id: str):
    """Return all pinned messages for a thread."""
    _check_excom_access()
    return frappe.db.sql(
        """
        SELECT m.name, m.content_text, m.direction, m.creation,
               m.message_type, m.created_by_user,
               u.full_name AS sender_name
        FROM `tabExcom Message` m
        LEFT JOIN `tabUser` u ON u.name = m.created_by_user
        WHERE m.thread = %(thread)s AND m.is_pinned = 1
        ORDER BY m.creation DESC
        """,
        {"thread": thread_id},
        as_dict=True,
    )


@frappe.whitelist()
def toggle_reaction(message_name: str, emoji: str):
    """
    Toggle the current user's reaction on a message.
    Reactions stored as JSON: {"emoji": ["user1", "user2"]}.
    """
    _check_excom_access()
    if not frappe.db.exists("Excom Message", message_name):
        frappe.throw(_("Message not found"))

    raw = frappe.db.get_value("Excom Message", message_name, "reactions") or "{}"
    try:
        reactions = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        reactions = {}

    user = frappe.session.user
    users_list = reactions.get(emoji, [])

    if user in users_list:
        users_list.remove(user)
        if not users_list:
            reactions.pop(emoji, None)
    else:
        users_list.append(user)
        reactions[emoji] = users_list

    frappe.db.set_value("Excom Message", message_name, "reactions", json.dumps(reactions))

    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    frappe.publish_realtime("excom:message_reaction", {
        "message": message_name, "thread": thread, "reactions": reactions,
    }, after_commit=True)

    return {"success": True, "reactions": reactions}


@frappe.whitelist()
def get_tags():
    """Return all available Excom Tag records."""
    _check_excom_access()
    return frappe.get_all(
        "Excom Tag",
        fields=["name", "tag_name", "color", "description"],
        order_by="tag_name asc",
        limit=100,
    )


@frappe.whitelist()
def add_thread_tag(thread_id: str, tag_name: str):
    """Add a tag to a thread. Creates the Excom Tag if it doesn't exist."""
    _check_excom_access()
    if not frappe.db.exists("Excom Thread", thread_id):
        frappe.throw(_("Thread not found"))

    if not frappe.db.exists("Excom Tag", tag_name):
        frappe.get_doc({
            "doctype": "Excom Tag",
            "tag_name": tag_name,
        }).insert(ignore_permissions=True)

    existing = frappe.db.exists(
        "Excom Thread Tag", {"parent": thread_id, "tag": tag_name}
    )
    if existing:
        return {"success": True, "already_exists": True}

    thread = frappe.get_doc("Excom Thread", thread_id)
    thread.append("tags", {
        "tag": tag_name,
        "added_by": frappe.session.user,
        "added_on": now_datetime(),
    })
    thread.save(ignore_permissions=True)
    return {"success": True}


@frappe.whitelist()
def remove_thread_tag(thread_id: str, tag_name: str):
    """Remove a tag from a thread."""
    _check_excom_access()
    if not frappe.db.exists("Excom Thread", thread_id):
        frappe.throw(_("Thread not found"))

    thread = frappe.get_doc("Excom Thread", thread_id)
    thread.tags = [t for t in thread.tags if t.tag != tag_name]
    thread.save(ignore_permissions=True)
    return {"success": True}


@frappe.whitelist()
def get_thread_tags(thread_id: str):
    """Return tags for a specific thread."""
    _check_excom_access()
    return frappe.db.sql(
        """
        SELECT tt.tag, t.color, t.tag_name
        FROM `tabExcom Thread Tag` tt
        JOIN `tabExcom Tag` t ON t.name = tt.tag
        WHERE tt.parent = %(thread_id)s
        ORDER BY tt.added_on ASC
        """,
        {"thread_id": thread_id},
        as_dict=True,
    )


@frappe.whitelist()
def get_related_documents(omni_identity: str):
    """
    Returns all transaction documents linked to an Omni Identity.

    Customer-side: Quotation, Sales Order, Delivery Note, Sales Invoice.
    Supplier-side: Request for Quotation, Purchase Order, Purchase Receipt, Purchase Invoice.
    """
    _check_excom_access()
    result = {
        "quotations": [],
        "sales_orders": [],
        "delivery_notes": [],
        "sales_invoices": [],
        "rfqs": [],
        "purchase_orders": [],
        "purchase_receipts": [],
        "purchase_invoices": [],
    }

    if not omni_identity:
        return result

    links = frappe.get_all(
        "Omni Identity Link",
        filters={"parent": omni_identity},
        fields=["linked_doctype", "linked_name"],
    )

    customer_names = [
        l.linked_name for l in links if l.linked_doctype == "Customer"
    ]
    supplier_names = [
        l.linked_name for l in links if l.linked_doctype == "Supplier"
    ]

    common_fields = ["name", "grand_total", "status", "currency"]
    date_field_map = {
        "Quotation": "transaction_date",
        "Sales Order": "transaction_date",
        "Delivery Note": "posting_date",
        "Sales Invoice": "posting_date",
        "Request for Quotation": "transaction_date",
        "Purchase Order": "transaction_date",
        "Purchase Receipt": "posting_date",
        "Purchase Invoice": "posting_date",
    }

    if customer_names:
        customer_doctypes = {
            "Quotation": ("quotations", "party_name", "customer_name"),
            "Sales Order": ("sales_orders", "customer", "customer_name"),
            "Delivery Note": ("delivery_notes", "customer", "customer_name"),
            "Sales Invoice": ("sales_invoices", "customer", "customer_name"),
        }
        for dt, (key, filter_field, name_field) in customer_doctypes.items():
            date_field = date_field_map[dt]
            fields = common_fields + [date_field, name_field]
            if dt == "Sales Invoice":
                fields.append("outstanding_amount")
            try:
                filters = {filter_field: ["in", customer_names], "docstatus": ["!=", 2]}
                if dt == "Quotation":
                    filters["quotation_to"] = "Customer"
                result[key] = frappe.get_all(
                    dt, filters=filters, fields=fields,
                    order_by=f"{date_field} desc", limit=15,
                )
            except Exception:
                pass

    if supplier_names:
        supplier_doctypes = {
            "Request for Quotation": ("rfqs", None, None),
            "Purchase Order": ("purchase_orders", "supplier", "supplier_name"),
            "Purchase Receipt": ("purchase_receipts", "supplier", "supplier_name"),
            "Purchase Invoice": ("purchase_invoices", "supplier", "supplier_name"),
        }
        for dt, (key, filter_field, name_field) in supplier_doctypes.items():
            date_field = date_field_map[dt]
            if dt == "Request for Quotation":
                try:
                    rfq_names = frappe.get_all(
                        "Request for Quotation Supplier",
                        filters={"supplier": ["in", supplier_names]},
                        pluck="parent",
                    )
                    if rfq_names:
                        result[key] = frappe.get_all(
                            dt,
                            filters={"name": ["in", list(set(rfq_names))], "docstatus": ["!=", 2]},
                            fields=common_fields + [date_field],
                            order_by=f"{date_field} desc",
                            limit=15,
                        )
                except Exception:
                    pass
            else:
                fields = common_fields + [date_field, name_field]
                if dt == "Purchase Invoice":
                    fields.append("outstanding_amount")
                try:
                    result[key] = frappe.get_all(
                        dt,
                        filters={filter_field: ["in", supplier_names], "docstatus": ["!=", 2]},
                        fields=fields,
                        order_by=f"{date_field} desc",
                        limit=15,
                    )
                except Exception:
                    pass

    return result


# ---------------------------------------------------------------------------
# Navbar config
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_navbar_config() -> dict:
    """Return navbar button config for the Frappe desk."""
    _check_excom_access()
    try:
        settings = frappe.get_single("Excom Settings")
        return {
            "show_navbar_button": bool(getattr(settings, "show_navbar_button", 1)),
            "gradient_from": getattr(settings, "logo_gradient_from", "#3b82f6") or "#3b82f6",
            "gradient_to": getattr(settings, "logo_gradient_to", "#8b5cf6") or "#8b5cf6",
        }
    except Exception:
        return {"show_navbar_button": True, "gradient_from": "#3b82f6", "gradient_to": "#8b5cf6"}


# ---------------------------------------------------------------------------
# Initiate outbound conversation
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_channel_accounts(channel: str = "") -> list:
    """Return active channel accounts visible to the current user's teams.

    Visibility rules:
    - If an account has no entries in allowed_teams → visible to everyone
    - If it has entries → visible only to users whose teams (or ancestor teams)
      overlap with the allowed list
    - System Manager / Excom Manager see all accounts
    """
    _check_excom_access()
    filters = {"status": "Active"}
    if channel:
        filters["channel"] = channel

    accounts = frappe.get_all(
        "Excom Channel Account",
        filters=filters,
        fields=[
            "name", "account_name", "channel",
            "is_default_incoming", "is_default_outgoing",
            "wa_phone_id", "email_address",
        ],
        order_by="is_default_outgoing desc, account_name asc",
    )

    user_roles = set(frappe.get_roles(frappe.session.user))
    if user_roles & {"System Manager", "Excom Manager"}:
        return accounts

    from excom.excom.doctype.excom_team.excom_team import get_user_teams_with_ancestors
    user_teams = set(get_user_teams_with_ancestors())

    visible = []
    for acc in accounts:
        allowed = frappe.get_all(
            "Excom Account Team",
            filters={"parenttype": "Excom Channel Account", "parent": acc.name},
            pluck="team",
        )
        if not allowed or user_teams & set(allowed):
            visible.append(acc)

    return visible


@frappe.whitelist()
def initiate_outbound(
    channel: str,
    omni_identity: str = "",
    display_name: str = "",
    phone: str = "",
    email: str = "",
    account: str = "",
) -> dict:
    """
    Start a new outbound conversation with an existing or new contact.

    Either provide an existing omni_identity, or provide display_name + phone/email
    to resolve/create one. Optionally specify an account; otherwise the default
    outgoing account for the channel is used.
    """
    _check_excom_access()

    if not channel:
        frappe.throw(_("Channel is required"))

    if channel not in ("whatsapp", "email", "voice"):
        frappe.throw(_("Channel must be 'whatsapp', 'email', or 'voice'"))

    if not omni_identity and not phone and not email:
        frappe.throw(_("Provide an existing identity or at least a phone/email"))

    if channel in ("whatsapp", "voice") and not phone and not omni_identity:
        frappe.throw(_("Phone number is required for {0} conversations").format(channel))

    if channel == "email" and not email and not omni_identity:
        frappe.throw(_("Email is required for email conversations"))

    from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
    from excom.excom.services.thread_service import upsert_thread
    from excom.excom.utils import get_channel_account

    if omni_identity:
        if not frappe.db.exists("Omni Identity", omni_identity):
            frappe.throw(_("Identity not found"))
        identity_name = omni_identity
    else:
        identity_name = resolve_identity(
            phone=phone,
            email=email,
            channel=channel,
            channel_user_id=phone or email,
            display_name=display_name or phone or email,
        )

    if account:
        account_doc = frappe.get_doc("Excom Channel Account", account)
    else:
        account_doc = get_channel_account(channel=channel, account_type="outgoing")

    if not account_doc:
        frappe.throw(_("No outgoing {0} account configured. Create one in Excom Channel Account.").format(channel))

    thread_name = upsert_thread(identity_name, channel, account_doc.name)

    user_roles = set(frappe.get_roles(frappe.session.user))
    is_new = not frappe.db.get_value("Excom Thread", thread_name, "assigned_to")
    if is_new:
        update_fields: dict = {"assigned_to": frappe.session.user}
        if not (user_roles & {"System Manager", "Excom Manager"}):
            from excom.excom.doctype.excom_team.excom_team import get_user_teams
            teams = get_user_teams()
            if teams:
                update_fields["assigned_team"] = teams[0]
        frappe.db.set_value("Excom Thread", thread_name, update_fields, update_modified=False)

    frappe.db.commit()

    return {"thread_id": thread_name, "omni_identity": identity_name}


# ---------------------------------------------------------------------------
# WhatsApp template messaging
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_thread_channel_account(thread_id: str) -> dict:
    """Return channel account linked to a thread (for template filtering)."""
    _check_excom_access()
    row = frappe.db.get_value(
        "Excom Thread",
        thread_id,
        ["channel", "account", "account_doctype"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("Thread not found"))
    return {
        "channel": row.get("channel") or "",
        "account": row.get("account") or "",
        "account_doctype": row.get("account_doctype") or "",
    }


@frappe.whitelist()
def get_whatsapp_templates(search: str = "", whatsapp_account: str = "") -> list:
    """Return approved WhatsApp templates with language variants and buttons.

    When ``whatsapp_account`` is set, only templates linked to that
    ``Excom Channel Account`` (primary or child table) are returned.
    """
    from excom.excom.whatsapp_template_utils import (
        body_variable_slot_count,
        get_body_variable_samples,
        get_header_variable_samples,
        header_variable_slot_count,
    )

    filters = {"status": "APPROVED"}
    if search:
        filters["template_name"] = ["like", f"%{search}%"]

    templates = frappe.get_all(
        "WhatsApp Templates",
        filters=filters,
        fields=[
            "name",
            "template_name",
            "actual_name",
            "template",
            "language_code",
            "category",
            "header_type",
            "sample_values",
            "body_variable_samples",
            "header_variable_samples",
            "field_names",
            "header",
            "footer",
            "whatsapp_account",
        ],
        order_by="template_name asc",
        limit=100,
    )

    if whatsapp_account:
        linked_parents = set(
            frappe.get_all(
                "WhatsApp Template Linked Account",
                filters={"channel_account": whatsapp_account},
                pluck="parent",
            )
        )
        primary_parents = set(
            frappe.get_all(
                "WhatsApp Templates",
                filters={"whatsapp_account": whatsapp_account, "status": "APPROVED"},
                pluck="name",
            )
        )
        allowed = linked_parents | primary_parents
        templates = [t for t in templates if t.name in allowed]

    all_names = [t.name for t in templates]
    buttons_by_parent: dict = {}
    if all_names:
        buttons = frappe.get_all(
            "WhatsApp Button",
            filters={"parent": ["in", all_names], "parenttype": "WhatsApp Templates"},
            fields=["parent", "button_type", "button_label", "website_url", "url_type", "example_url", "phone_number"],
            order_by="idx asc",
        )
        for btn in buttons:
            buttons_by_parent.setdefault(btn.parent, []).append(btn)

    lang_groups: dict = {}
    for t in templates:
        variables = get_body_variable_samples(t)
        header_samples = get_header_variable_samples(t)
        t["variable_count"] = body_variable_slot_count(t)
        t["header_variable_count"] = header_variable_slot_count(t)
        t["sample_variables"] = variables
        t["header_sample_variables"] = header_samples
        t["buttons"] = buttons_by_parent.get(t.name, [])
        t["has_dynamic_url"] = any(
            b.get("url_type") == "Dynamic" for b in t["buttons"]
            if b.get("button_type") == "Visit Website"
        )

        key = t.actual_name or t.template_name
        if key not in lang_groups:
            lang_groups[key] = []
        lang_groups[key].append(t.language_code)

    for t in templates:
        key = t.actual_name or t.template_name
        t["available_languages"] = lang_groups.get(key, [t.language_code])

    return templates


@frappe.whitelist()
def get_subscriber_variable_fields() -> list:
    """Return Omni Identity fields available for per-subscriber variable mapping."""
    return [
        {"value": "subscriber.display_name", "label": "Display Name"},
        {"value": "subscriber.primary_phone", "label": "Phone"},
        {"value": "subscriber.primary_email", "label": "Email"},
        {"value": "subscriber.primary_whatsapp", "label": "WhatsApp Number"},
        {"value": "customer.customer_name", "label": "Customer Name"},
        {"value": "customer.customer_group", "label": "Customer Group"},
        {"value": "lead.lead_name", "label": "Lead Name"},
        {"value": "lead.company_name", "label": "Lead Company"},
        {"value": "supplier.supplier_name", "label": "Supplier Name"},
        {"value": "contact.first_name", "label": "Contact First Name"},
        {"value": "contact.last_name", "label": "Contact Last Name"},
    ]


@frappe.whitelist()
def send_template_to_thread(
    thread_id: str,
    template_name: str,
    variables: str = "[]",
    header_media_url: str = "",
    button_urls: str = "[]",
    header_location: str = "",
) -> dict:
    """
    Send an approved WhatsApp template message on an existing thread.

    Args:
        thread_id: Excom Thread name
        template_name: WhatsApp Templates document name
        variables: JSON array -- header ``{{n}}`` values first (if any), then body values,
            in order of appearance within each section.
        header_media_url: Frappe file URL for IMAGE/VIDEO/DOCUMENT header attachments
        button_urls: JSON array of suffix strings for each dynamic URL button, in order
        header_location: JSON object ``{"latitude","longitude","name","address"}`` for LOCATION headers
    """
    _check_excom_access()

    thread = frappe.db.get_value(
        "Excom Thread", thread_id,
        ["channel", "omni_identity", "account_doctype", "account"],
        as_dict=True,
    )
    if not thread:
        frappe.throw(_("Thread not found"))

    if thread.channel != "whatsapp":
        frappe.throw(_("Templates are only supported for WhatsApp"))

    if not thread.get("account"):
        frappe.throw(
            _("This thread has no WhatsApp channel account. Open a conversation on a linked WhatsApp number, then try again."),
            title=_("Cannot send template"),
        )

    account_doctype = thread.get("account_doctype") or "Excom Channel Account"

    template_doc = frappe.get_doc("WhatsApp Templates", template_name)

    from excom.excom.whatsapp_template_utils import template_is_linked_to_account

    if not template_is_linked_to_account(template_name, thread.account):
        frappe.throw(
            _("This template is not linked to the WhatsApp number used in this conversation ({0}).").format(
                thread.account or _("unknown")
            )
        )

    ht_upper = (template_doc.header_type or "").upper()
    if ht_upper in ("IMAGE", "VIDEO", "DOCUMENT") and not header_media_url:
        frappe.throw(
            _("This template requires a {0} attachment in the header").format(
                ht_upper.lower()
            )
        )

    location_data: dict | None = None
    if ht_upper == "LOCATION":
        try:
            location_data = json.loads(header_location) if isinstance(header_location, str) and header_location.strip() else None
        except (json.JSONDecodeError, TypeError):
            location_data = None
        if not location_data or not location_data.get("latitude") or not location_data.get("longitude"):
            frappe.throw(_("This template requires a location (latitude, longitude, name, address) in the header"))

    identity = frappe.get_doc("Omni Identity", thread.omni_identity)
    to_number = identity.primary_whatsapp or identity.primary_phone
    if not to_number:
        frappe.throw(_("No phone number on identity"))

    try:
        account = frappe.get_doc(account_doctype, thread.account)
    except frappe.DoesNotExistError:
        frappe.throw(
            _("Channel account {0} is missing or was deleted.").format(thread.account),
            title=_("Cannot send template"),
        )

    try:
        var_list = json.loads(variables) if isinstance(variables, str) else variables
    except (json.JSONDecodeError, TypeError):
        frappe.throw(_("Invalid template variables. Expected a JSON array of strings."))

    if not isinstance(var_list, list):
        frappe.throw(_("Template variables must be a JSON array"))

    try:
        btn_list = json.loads(button_urls) if isinstance(button_urls, str) else button_urls
    except (json.JSONDecodeError, TypeError):
        btn_list = []
    if not isinstance(btn_list, list):
        btn_list = []

    components = _build_template_components(
        template_doc, var_list, header_media_url, btn_list, location_data,
    )

    preview = _build_template_preview(template_doc, var_list, location_data)
    now = now_datetime()

    from excom.excom.services.whatsapp_service import send_template_message as wa_send_template

    provider_message_id = ""
    delivery_status = "Sent"
    failure_reason = ""

    try:
        result = wa_send_template(
            account=account,
            to=to_number,
            template_name=template_doc.actual_name or template_doc.template_name,
            language_code=template_doc.language_code or "en_US",
            components=components,
        )
        provider_message_id = result.get("provider_message_id", "")
        delivery_status = result.get("status", "Sent")
    except ExcomRateLimitError as e:
        delivery_status = "Failed"
        failure_reason = f"Rate limit: {e.message}"
    except ExcomProviderError as e:
        delivery_status = "Failed"
        failure_reason = e.message
        frappe.log_error(
            title="send_template_to_thread provider error",
            message=f"{e.message}\nthread={thread_id}\ntemplate={template_name}\naccount={thread.account}",
        )

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_id,
        "omni_identity": thread.omni_identity,
        "channel": "whatsapp",
        "account_doctype": thread.account_doctype,
        "account": thread.account,
        "direction": "Outbound",
        "message_type": "Template",
        "provider_message_id": provider_message_id,
        "provider_timestamp": now,
        "content_text": preview,
        "media_file": header_media_url or None,
        "delivery_status": delivery_status,
        "failure_reason": failure_reason or None,
        "created_by_user": frappe.session.user,
        "template": template_name,
    })
    msg.insert(ignore_permissions=True)

    if delivery_status == "Failed":
        frappe.db.commit()
        frappe.throw(
            _(failure_reason or "WhatsApp send failed"),
            title=_("WhatsApp template"),
        )

    from excom.excom.services.thread_service import _auto_claim_thread
    thread_doc = frappe.db.get_value(
        "Excom Thread", thread_id, ["name", "assigned_to"], as_dict=True,
    )
    if thread_doc:
        _auto_claim_thread(thread_doc, frappe.session.user)

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
        {"now": now, "preview": preview[:120], "thread": thread_id},
    )

    frappe.publish_realtime(
        "excom:message_sent",
        {"thread": thread_id, "message": msg.name, "direction": "Outbound", "preview": preview[:120]},
        after_commit=True,
    )

    frappe.db.commit()
    return {"success": True, "message_name": msg.name}


@frappe.whitelist()
def check_24h_window(thread_id: str) -> dict:
    """
    Check if the WhatsApp 24h messaging window is open for a thread.

    Returns:
        {"window_open": bool, "last_inbound_at": str or None, "hours_remaining": float or 0}
    """
    last_inbound = frappe.db.get_value("Excom Thread", thread_id, "last_inbound_at")
    if not last_inbound:
        return {"window_open": False, "last_inbound_at": None, "hours_remaining": 0}

    from frappe.utils import time_diff_in_seconds
    diff = time_diff_in_seconds(now_datetime(), last_inbound)
    window_seconds = 24 * 60 * 60
    remaining = max(0, window_seconds - diff)

    return {
        "window_open": diff < window_seconds,
        "last_inbound_at": str(last_inbound),
        "hours_remaining": round(remaining / 3600, 1),
    }


def _build_template_components(
    template_doc, body_variables: list, header_media_url: str = "",
    button_urls: list = None, location_data: dict = None,
) -> list:
    """Build Meta Cloud API ``components`` for template sends.

    *values* order: header variable values first, then body variable values,
    in order of first appearance in the template text. Works with both
    positional (``{{1}}``) and named (``{{sale_date}}``) placeholder syntax.

    Meta requires:
    - One ``text.text`` parameter per placeholder, in positional order.
    - Dynamic URL buttons must each include a ``button`` component with a text parameter.
    - Typical component order: header, body, then buttons.
    - VIDEO headers use ``video.link``; LOCATION headers use ``location`` object.
    """
    from excom.excom.whatsapp_template_utils import (
        body_variable_slot_count,
        header_variable_slot_count,
        is_text_header,
    )

    values = list(body_variables or [])
    button_urls = list(button_urls or [])

    h_n = header_variable_slot_count(template_doc)
    b_n = body_variable_slot_count(template_doc)
    need = h_n + b_n

    if len(values) < need:
        frappe.throw(
            _("This template needs {0} variable value(s) ({1} in the header, {2} in the body). You sent {3}.").format(
                need, h_n, b_n, len(values),
            ),
            title=_("WhatsApp template"),
        )

    header_vals = values[:h_n]
    body_vals = values[h_n : h_n + b_n]

    components: list = []
    ht = (getattr(template_doc, "header_type", None) or "").upper()

    if is_text_header(ht) and h_n:
        components.append({
            "type": "header",
            "parameters": [{"type": "text", "text": str(v)} for v in header_vals],
        })
    elif ht == "IMAGE" and header_media_url:
        components.append({
            "type": "header",
            "parameters": [{
                "type": "image",
                "image": {"link": _to_absolute_url(header_media_url)},
            }],
        })
    elif ht == "VIDEO" and header_media_url:
        components.append({
            "type": "header",
            "parameters": [{
                "type": "video",
                "video": {"link": _to_absolute_url(header_media_url)},
            }],
        })
    elif ht == "DOCUMENT" and header_media_url:
        filename = header_media_url.rsplit("/", 1)[-1] if "/" in header_media_url else "document"
        components.append({
            "type": "header",
            "parameters": [{
                "type": "document",
                "document": {
                    "link": _to_absolute_url(header_media_url),
                    "filename": filename,
                },
            }],
        })
    elif ht == "LOCATION" and location_data:
        components.append({
            "type": "header",
            "parameters": [{
                "type": "location",
                "location": {
                    "latitude": str(location_data.get("latitude", "")),
                    "longitude": str(location_data.get("longitude", "")),
                    "name": str(location_data.get("name", "")),
                    "address": str(location_data.get("address", "")),
                },
            }],
        })

    if b_n:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(v)} for v in body_vals],
        })

    url_idx = 0
    for i, btn in enumerate(template_doc.get("buttons") or []):
        if btn.button_type != "Visit Website" or btn.url_type != "Dynamic":
            continue
        if url_idx >= len(button_urls) or str(button_urls[url_idx] or "").strip() == "":
            frappe.throw(
                _('Template "{0}" has a dynamic URL button ({1}). Add the URL suffix parameter.').format(
                    template_doc.template_name or template_doc.name,
                    getattr(btn, "button_label", None) or _("button"),
                ),
                title=_("WhatsApp template"),
            )
        components.append({
            "type": "button",
            "sub_type": "url",
            "index": str(i),
            "parameters": [{"type": "text", "text": str(button_urls[url_idx]).strip()}],
        })
        url_idx += 1

    return components


def _get_site_url() -> str:
    """Derive public site URL from the current request or config.

    Priority: request origin > host_name in site_config > frappe.utils.get_url()
    """
    if frappe.request:
        scheme = frappe.request.headers.get("X-Forwarded-Proto", frappe.request.scheme)
        host = frappe.request.headers.get("X-Forwarded-Host") or frappe.request.host
        if host:
            return f"{scheme}://{host}".rstrip("/")

    return (frappe.conf.get("host_name") or frappe.utils.get_url()).rstrip("/")


def _to_absolute_url(file_url: str) -> str:
    """Convert a Frappe file URL to a publicly accessible absolute URL.

    Private files are moved to public so Meta can download them.
    """
    if file_url.startswith("http"):
        return file_url

    site_url = _get_site_url()

    if "/private/" in file_url:
        file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
        if file_name:
            file_doc = frappe.get_doc("File", file_name)
            file_doc.is_private = 0
            file_doc.save(ignore_permissions=True)
            frappe.db.commit()
            return f"{site_url}{file_doc.file_url}"

    return f"{site_url}{file_url}"


def _replace_placeholders(text: str, values: list) -> str:
    """Replace ``{{...}}`` placeholders in *text* with *values* in first-appearance order.

    Works with both positional (``{{1}}``) and named (``{{sale_date}}``) params.
    """
    from excom.excom.whatsapp_template_utils import ordered_placeholder_names

    names = ordered_placeholder_names(text)
    for i, name in enumerate(names):
        if i < len(values):
            text = text.replace("{{" + name + "}}", str(values[i]))
    return text


def _build_template_preview(template_doc, body_variables: list, location_data: dict = None) -> str:
    """Build a human-readable preview of the template with variables filled in.

    *body_variables* follows the inbox convention: header values first, then body values.
    """
    from excom.excom.whatsapp_template_utils import header_variable_slot_count, is_text_header

    h_n = header_variable_slot_count(template_doc)
    header_vals = body_variables[:h_n]
    body_vals = body_variables[h_n:]

    parts: list[str] = []

    ht = (getattr(template_doc, "header_type", None) or "").upper()
    if is_text_header(ht):
        hdr = _replace_placeholders(template_doc.header or "", header_vals)
        if hdr.strip():
            parts.append(hdr.strip())
    elif ht == "LOCATION" and location_data:
        loc_name = location_data.get("name", "")
        loc_addr = location_data.get("address", "")
        loc_preview = " - ".join(p for p in (loc_name, loc_addr) if p)
        if loc_preview:
            parts.append(f"[Location: {loc_preview}]")

    parts.append(_replace_placeholders(template_doc.template or "", body_vals))

    return "\n".join(parts)[:500]


@frappe.whitelist()
def retry_message(message_name: str = "") -> dict:
    """
    Retry a failed outbound message.

    Re-sends via the original provider and updates the same Excom Message record
    instead of creating a duplicate.
    """
    _check_excom_access()

    if not message_name:
        frappe.throw(_("message_name is required"))

    from excom.excom.services.delivery_watchdog import retry_failed_message
    return retry_failed_message(message_name)


@frappe.whitelist()
def mark_spam(thread_id: str = "", omni_identity: str = "") -> dict:
    """
    Mark a thread (and its sender identity) as spam.

    Sets thread status to Spam and flags the Omni Identity so future
    messages from this sender are auto-filtered.
    """
    _check_excom_access()

    if not thread_id:
        frappe.throw(_("thread_id is required"))

    thread = frappe.get_doc("Excom Thread", thread_id)

    frappe.db.set_value("Excom Thread", thread_id, {
        "status": "Spam",
        "modified": frappe.utils.now_datetime(),
    })

    identity = omni_identity or thread.omni_identity
    if identity:
        frappe.db.set_value("Omni Identity", identity, "is_spam", 1)

    frappe.db.commit()
    return {"success": True, "status": "Spam"}


@frappe.whitelist()
def archive_thread(thread_id: str = "") -> dict:
    """Archive a thread — hides it from active inbox without deleting."""
    _check_excom_access()

    if not thread_id:
        frappe.throw(_("thread_id is required"))

    frappe.db.set_value("Excom Thread", thread_id, {
        "status": "Closed",
        "modified": frappe.utils.now_datetime(),
    })

    frappe.db.commit()
    return {"success": True, "status": "Closed"}


@frappe.whitelist()
def delete_thread(thread_id: str = "") -> dict:
    """
    Delete a thread and all its messages. Restricted to System Manager.
    """
    _check_excom_access()

    user_roles = set(frappe.get_roles(frappe.session.user))
    if "System Manager" not in user_roles:
        frappe.throw(
            _("Only System Managers can delete threads"),
            frappe.PermissionError,
        )

    if not thread_id:
        frappe.throw(_("thread_id is required"))

    frappe.db.delete("Excom Message", {"thread": thread_id})
    frappe.delete_doc("Excom Thread", thread_id, force=True)

    frappe.db.commit()
    return {"success": True}
