"""
Delivery Watchdog — monitors outbound messages for delivery confirmation.

Marks messages as Failed if no delivery webhook arrives within the timeout.
Also provides retry functionality for failed messages.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, time_diff_in_seconds
from frappe import _
from frappe.utils import now_datetime, add_to_date

DELIVERY_TIMEOUT_SECONDS = 600  # 10 minutes


RETRY_WINDOW_SECONDS = 6 * 60 * 60


def check_stale_messages() -> None:
    """
    Find outbound messages stuck in Sent/Queued beyond the timeout and mark Failed.

    Runs as a scheduled job every minute. Covers both regular and broadcast messages.
    """
    cutoff = add_to_date(now_datetime(), seconds=-DELIVERY_TIMEOUT_SECONDS)

    stale = frappe.db.sql(
        """
        SELECT name, thread, provider_message_id, delivery_status,
               reference_doctype, reference_name
        FROM `tabExcom Message`
        WHERE direction = 'Outbound'
          AND delivery_status IN ('Queued', 'Sent')
          AND provider_timestamp <= %(cutoff)s
          AND provider_timestamp >= %(floor)s
          AND channel = 'whatsapp'
        LIMIT 200
        """,
        {
            "cutoff": cutoff,
            "floor": add_to_date(now_datetime(), hours=-24),
        },
        as_dict=True,
    )

    if not stale:
        return

    for msg in stale:
        frappe.db.set_value(
            "Excom Message",
            msg.name,
            {
                "delivery_status": "Failed",
                "failure_reason": "No delivery confirmation received within 10 minutes",
            },
            update_modified=False,
        )

        frappe.publish_realtime(
            "excom:message_status_updated",
            {
                "message": msg.name,
                "thread": msg.thread,
                "status": "Failed",
                "provider_message_id": msg.provider_message_id or "",
            },
            after_commit=True,
        )

        if msg.reference_doctype == "Excom Broadcast" and msg.reference_name:
            try:
                frappe.db.sql(
                    "UPDATE `tabExcom Broadcast` SET `failed_count` = `failed_count` + 1 WHERE name = %(name)s",
                    {"name": msg.reference_name},
                )
            except Exception:
                pass

    frappe.db.commit()
    frappe.logger().info(f"Delivery watchdog: marked {len(stale)} message(s) as Failed")


def retry_failed_message(message_name: str) -> dict:
    """
    Retry sending a failed message using the same content/media/template.

    Resets the message status to Queued, re-sends via the provider,
    and updates with the new provider_message_id.

    Returns:
        dict with success status and new delivery info
    """
    msg = frappe.get_doc("Excom Message", message_name)

    if msg.delivery_status != "Failed":
        frappe.throw(_("Only failed messages can be retried"))

    # Product rule: a failed message may be resent for 6 hours; after that it stays "Unsent".
    sent_at = msg.provider_timestamp or msg.creation
    if sent_at and time_diff_in_seconds(now_datetime(), sent_at) > RETRY_WINDOW_SECONDS:
        frappe.throw(_("This message is older than 6 hours and can no longer be resent. Send a new message instead."))

    if msg.direction != "Outbound":
        frappe.throw(_("Only outbound messages can be retried"))

    if msg.channel != "whatsapp":
        frappe.throw(_("Retry is currently supported for WhatsApp messages only"))

    thread = frappe.get_doc("Excom Thread", msg.thread)
    identity = frappe.get_doc("Omni Identity", msg.omni_identity)
    account = frappe.get_doc(msg.account_doctype, msg.account)

    to_number = identity.primary_whatsapp or identity.primary_phone
    if not to_number:
        frappe.throw(_("No phone number on identity to send to"))

    frappe.db.set_value(
        "Excom Message", message_name,
        {"delivery_status": "Queued", "failure_reason": ""},
        update_modified=False,
    )

    frappe.publish_realtime(
        "excom:message_status_updated",
        {"message": message_name, "thread": msg.thread, "status": "Queued"},
        after_commit=True,
    )

    from excom.excom.services.whatsapp_service import (
        send_text_message,
        send_media_message,
        send_sticker_message,
        send_template_message,
    )
    from excom.excom.utils.errors import ExcomProviderError, ExcomRateLimitError

    result: dict = {}
    try:
        if msg.message_type == "Sticker":
            sticker_file = msg.media_file or ""
            sticker_name = _find_sticker_by_file(sticker_file)
            if sticker_name:
                result = send_sticker_message(account, to_number, sticker_name)
            else:
                result = send_media_message(account, to_number, "image", sticker_file, "")

        elif msg.message_type == "Template" and msg.template:
            template_doc = frappe.get_doc("WhatsApp Templates", msg.template)
            result = send_template_message(
                account,
                to_number,
                template_doc.actual_name or template_doc.template_name,
                template_doc.language_code or "en_US",
            )

        elif msg.message_type in ("Image", "Video", "Audio", "Document") and msg.media_file:
            result = send_media_message(
                account, to_number, msg.message_type, msg.media_file, msg.content_text or ""
            )

        else:
            result = send_text_message(account, to_number, msg.content_text or "")

    except (ExcomProviderError, ExcomRateLimitError) as e:
        frappe.db.set_value(
            "Excom Message", message_name,
            {"delivery_status": "Failed", "failure_reason": str(e)},
            update_modified=False,
        )
        frappe.publish_realtime(
            "excom:message_status_updated",
            {"message": message_name, "thread": msg.thread, "status": "Failed"},
            after_commit=True,
        )
        frappe.db.commit()
        frappe.throw(_("Retry failed: {0}").format(str(e)))

    except Exception as e:
        frappe.db.set_value(
            "Excom Message", message_name,
            {"delivery_status": "Failed", "failure_reason": str(e)},
            update_modified=False,
        )
        frappe.db.commit()
        frappe.log_error(title="Excom: retry failed")
        frappe.throw(_("Retry failed unexpectedly"))

    new_provider_id = result.get("provider_message_id", "")
    new_status = result.get("status", "Sent")

    frappe.db.set_value(
        "Excom Message", message_name,
        {
            "delivery_status": new_status,
            "provider_message_id": new_provider_id,
            "failure_reason": "",
            "provider_timestamp": now_datetime(),
        },
        update_modified=True,
    )

    frappe.publish_realtime(
        "excom:message_status_updated",
        {
            "message": message_name,
            "thread": msg.thread,
            "status": new_status,
            "provider_message_id": new_provider_id,
        },
        after_commit=True,
    )

    frappe.db.commit()

    return {
        "success": True,
        "message_name": message_name,
        "delivery_status": new_status,
        "provider_message_id": new_provider_id,
    }


def _find_sticker_by_file(file_url: str) -> str:
    """Try to find an Excom Sticker matching this file URL."""
    if not file_url:
        return ""
    return frappe.db.get_value(
        "Excom Sticker", {"sticker_file": file_url, "enabled": 1}, "name"
    ) or ""
