"""
Email inbound polling adapter.

Fetches new email metadata from Gmail API and creates lightweight Excom Message
stubs linked to Omni Identity and Excom Thread. Only metadata is stored --
the email body lives in Gmail and is fetched on-demand.
"""

import json
import re
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, add_days

from excom.excom.services import gmail_service
from excom.excom.services.thread_service import upsert_thread
from excom.excom.doctype.omni_identity.omni_identity import resolve_identity


def poll_all_email_accounts():
    """
    Scheduler entry point: polls all enabled email accounts for new messages.
    Called from hooks.py scheduler_events every minute.
    Respects each account's poll_interval_minutes setting via cache-based throttle.
    """
    accounts = frappe.get_all(
        "Excom Channel Account",
        filters={
            "channel": "email",
            "status": "Active",
            "email_authorized": 1,
        },
        fields=["name", "email_poll_interval_minutes"],
    )

    now = now_datetime()

    for account in accounts:
        interval = max(int(account.email_poll_interval_minutes or 2), 1)

        cache_key = f"excom_email_last_poll:{account.name}"
        last_poll = frappe.cache.get_value(cache_key)
        if last_poll:
            minutes_since = (now - get_datetime(last_poll)).total_seconds() / 60
            if minutes_since < interval:
                continue

        frappe.cache.set_value(cache_key, str(now), expires_in_sec=interval * 120)

        try:
            frappe.enqueue(
                "excom.excom.channels.email.inbound.poll_single_account",
                account_name=account.name,
                queue="short",
                is_async=True,
                deduplicate=True,
                job_id=f"email_poll:{account.name}",
            )
        except Exception:
            frappe.log_error(
                title=f"Email poll enqueue failed: {account.name}",
            )


def poll_single_account(account_name: str):
    """
    Poll a single email account for new messages.
    Uses Gmail History API for incremental sync when possible,
    falls back to messages.list for initial sync.
    """
    try:
        account = frappe.get_doc("Excom Channel Account", account_name)
        last_history_id = account.email_last_history_id

        if last_history_id:
            _incremental_sync(account_name, last_history_id, account)
        else:
            _initial_sync(account_name, account)

    except Exception:
        frappe.log_error(
            title=f"Email poll failed: {account_name}",
        )


def _initial_sync(account_name: str, account):
    """
    First-time sync: fetch recent messages from Gmail using messages.list.
    Uses sync_from_date if set, otherwise defaults to last 7 days.
    """
    sync_from = account.email_sync_from_date
    if sync_from:
        query = f"after:{sync_from.strftime('%Y/%m/%d')}"
    else:
        seven_days_ago = add_days(now_datetime(), -7)
        query = f"after:{seven_days_ago.strftime('%Y/%m/%d')}"

    labels = _get_label_ids(account)
    result = gmail_service.list_messages(
        account_name,
        query=query,
        label_ids=labels,
        max_results=100,
    )

    messages = result.get("messages", [])
    new_history_id = ""

    for msg_ref in messages:
        gmail_msg_id = msg_ref["id"]

        if frappe.db.exists(
            "Excom Message", {"provider_message_id": gmail_msg_id}
        ):
            continue

        try:
            meta = gmail_service.get_message_metadata(account_name, gmail_msg_id)
            _ingest_email_metadata(account_name, account, meta)

            if not new_history_id:
                profile = gmail_service.get_profile(account_name)
                new_history_id = str(profile.get("historyId", ""))
        except Exception:
            frappe.log_error(
                title=f"Email metadata fetch failed: {gmail_msg_id}",
            )

    if not new_history_id:
        try:
            profile = gmail_service.get_profile(account_name)
            new_history_id = str(profile.get("historyId", ""))
        except Exception:
            pass

    if new_history_id:
        frappe.db.set_value(
            "Excom Channel Account", account_name,
            "email_last_history_id", new_history_id,
        )
        frappe.db.commit()


def _incremental_sync(account_name: str, last_history_id: str, account):
    """
    Incremental sync using Gmail History API.
    Fetches only messages added since last_history_id.
    """
    label = _get_label_ids(account)
    label_id = label[0] if label else "INBOX"

    result = gmail_service.get_history(
        account_name, last_history_id, label_id=label_id
    )

    if result.get("expired"):
        frappe.db.set_value(
            "Excom Channel Account", account_name,
            "email_last_history_id", "",
        )
        frappe.db.commit()
        _initial_sync(account_name, account)
        return

    history_entries = result.get("history", [])
    new_history_id = result.get("historyId", last_history_id)

    for entry in history_entries:
        for added in entry.get("messagesAdded", []):
            gmail_msg_id = added.get("message", {}).get("id")
            if not gmail_msg_id:
                continue

            if frappe.db.exists(
                "Excom Message", {"provider_message_id": gmail_msg_id}
            ):
                continue

            try:
                meta = gmail_service.get_message_metadata(
                    account_name, gmail_msg_id
                )
                _ingest_email_metadata(account_name, account, meta)
            except Exception:
                frappe.log_error(
                    title=f"Email metadata fetch failed: {gmail_msg_id}",
                )

    frappe.db.set_value(
        "Excom Channel Account", account_name,
        "email_last_history_id", new_history_id,
    )
    frappe.db.commit()


def _ingest_email_metadata(account_name: str, account, meta: dict):
    """
    Create an Excom Message stub from Gmail metadata.
    Resolves Omni Identity from sender email and upserts Excom Thread
    using Gmail thread ID.
    """
    headers = meta.get("headers", {})
    from_raw = headers.get("From", "")
    to_raw = headers.get("To", "")
    subject = headers.get("Subject", "(No Subject)")
    date_str = headers.get("Date", "")
    message_id_header = headers.get("Message-ID", "")
    gmail_msg_id = meta["id"]
    gmail_thread_id = meta.get("threadId", "")
    snippet = meta.get("snippet", "")
    label_ids = meta.get("labelIds", [])

    from_email, from_name = _parse_email_address(from_raw)
    account_email = account.email_address or ""

    is_sent = "SENT" in label_ids and from_email.lower() == account_email.lower()
    direction = "Outbound" if is_sent else "Inbound"

    if direction == "Inbound":
        contact_email = from_email
        contact_name = from_name or from_email
    else:
        to_email, to_name = _parse_email_address(to_raw)
        contact_email = to_email
        contact_name = to_name or to_email

    identity_name = resolve_identity(
        email=contact_email,
        channel="email",
        channel_user_id=contact_email,
        display_name=contact_name,
    )

    # One thread per person per channel per account (same as WhatsApp).
    # gmail_thread_id is stored per-message in content_json for reply threading.
    thread_name = upsert_thread(identity_name, "email", account_name)

    internal_date = meta.get("internalDate", "")
    if internal_date:
        try:
            # Gmail internalDate is UTC milliseconds since epoch → site time zone (not the OS clock).
            from excom.excom.utils.site_time import epoch_to_site_time
            provider_ts = epoch_to_site_time(int(internal_date) / 1000)
        except (ValueError, OSError):
            provider_ts = now_datetime()
    else:
        provider_ts = now_datetime()

    content_json = {
        "gmail_message_id": gmail_msg_id,
        "gmail_thread_id": gmail_thread_id,
        "subject": subject,
        "from_email": from_email,
        "from_name": from_name,
        "to": to_raw,
        "cc": headers.get("Cc", ""),
        "date": date_str,
        "message_id_header": message_id_header,
        "in_reply_to": headers.get("In-Reply-To", ""),
        "label_ids": label_ids,
        "snippet": snippet,
    }

    preview = f"{subject}: {snippet}"[:120]

    msg = frappe.get_doc({
        "doctype": "Excom Message",
        "thread": thread_name,
        "omni_identity": identity_name,
        "channel": "email",
        "account_doctype": "Excom Channel Account",
        "account": account_name,
        "direction": direction,
        "message_type": "Email",
        "provider_message_id": gmail_msg_id,
        "provider_timestamp": provider_ts,
        "content_text": snippet[:150],
        "content_json": json.dumps(content_json),
        "delivery_status": "Delivered" if direction == "Inbound" else "Sent",
    })
    msg.insert(ignore_permissions=True)

    update_ts = provider_ts  # use the actual email date, not the sync time
    now = now_datetime()
    if direction == "Inbound":
        frappe.db.sql(
            """
            UPDATE `tabExcom Thread`
            SET last_message_at = %(ts)s,
                last_inbound_at = %(ts)s,
                unread_count = unread_count + 1,
                last_message_preview = %(preview)s,
                last_message_direction = 'Inbound',
                status = CASE WHEN status = 'Closed' THEN 'Open' ELSE status END,
                modified = %(now)s
            WHERE name = %(thread)s
            """,
            {"ts": update_ts, "now": now, "preview": preview, "thread": thread_name},
        )
    else:
        frappe.db.sql(
            """
            UPDATE `tabExcom Thread`
            SET last_message_at = %(ts)s,
                last_outbound_at = %(ts)s,
                last_message_preview = %(preview)s,
                last_message_direction = 'Outbound',
                modified = %(now)s
            WHERE name = %(thread)s
            """,
            {"ts": update_ts, "now": now, "preview": preview, "thread": thread_name},
        )

    frappe.publish_realtime(
        "excom:message_received",
        {
            "thread": thread_name,
            "message": msg.name,
            "omni_identity": identity_name,
            "direction": direction,
            "preview": preview,
        },
        after_commit=True,
    )
    frappe.publish_realtime(
        "excom:thread_updated",
        {"thread": thread_name, "event": "new_email"},
        after_commit=True,
    )

    frappe.db.commit()


def _parse_email_address(raw: str) -> tuple:
    """
    Parse 'Display Name <email@example.com>' into (email, display_name).
    Falls back to treating the whole string as email if no angle brackets.
    """
    if not raw:
        return "", ""

    match = re.match(r'^"?([^"<]*)"?\s*<([^>]+)>', raw.strip())
    if match:
        return match.group(2).strip(), match.group(1).strip()

    email_match = re.search(r'[\w.+-]+@[\w.-]+\.\w+', raw)
    if email_match:
        return email_match.group(0), ""

    return raw.strip(), ""


def _get_label_ids(account) -> list:
    """Parse the label filter from account config."""
    label_str = account.email_label_filter or "INBOX"
    return [l.strip() for l in label_str.split(",") if l.strip()]
