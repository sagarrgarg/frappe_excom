"""Transactional outbound — one call for another app to message a person.

excom owns delivery. Another bench app (e.g. webshop, for abandoned-checkout
reminders) calls :func:`send_transactional` instead of talking to Meta's Cloud
API or ``frappe.sendmail`` itself. This resolves the person to an Omni
Identity, honours their consent, picks the channel, sends through the same
WhatsApp / email services the rest of excom uses, and records an Excom Message
so the send appears in the inbox thread beside the person's other
conversations.

It differs from the other send paths on purpose:

- Broadcast (`broadcast_service`) is list + campaign shaped; this is one person,
  one message, no subscriber list, no Broadcast doc.
- The per-thread APIs (`thread_service.send_outbound_message`,
  `api/chat.send_template_to_thread`) need a pre-existing thread and a
  logged-in agent session; this is a trusted server-to-server call with
  neither — it upserts the thread itself.

Never raises: a failure is reported in the return dict and logged, so a caller
in a scheduler loop is never broken by one bad send.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime


def send_transactional(
    *,
    purpose: str,
    phone: str = "",
    email: str = "",
    channel: str = "auto",
    whatsapp_template: str = "",
    whatsapp_variables: list[str] | None = None,
    whatsapp_button_urls: list[str] | None = None,
    whatsapp_header_media: str = "",
    email_subject: str = "",
    email_html: str = "",
    display_name: str = "",
    reference: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Send one transactional message to a person, excom-owned end to end.

    Args:
        purpose: short tag for logs / thread preview, e.g. "abandoned_checkout".
        phone / email: how to reach them; at least one is required.
        channel: "auto" (WhatsApp if reachable, else email), "whatsapp", "email".
        whatsapp_template: an excom "WhatsApp Templates" record name (or its
            template_name). Required to send on WhatsApp.
        whatsapp_variables: ordered body {{n}} values.
        whatsapp_button_urls: ordered dynamic URL-button suffixes (e.g. a token).
        email_subject / email_html: the email the CALLER composed — excom only
            delivers and logs it, so the caller keeps ownership of its template.
        reference: (doctype, name) stamped on the Excom Message for traceability.

    Returns a dict: ok, channel, message_id, identity, skipped (a reason when
    nothing was sent).
    """
    result: dict[str, Any] = {
        "ok": False,
        "channel": None,
        "message_id": "",
        "identity": "",
        "skipped": "",
    }
    phone = (phone or "").strip()
    email = (email or "").strip()
    if not phone and not email:
        result["skipped"] = "no_contact"
        return result

    try:
        from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

        identity = resolve_identity_internal(
            phone=phone,
            email=email,
            channel="whatsapp" if phone else "",
            channel_user_id=_digits(phone) if phone else "",
            display_name=display_name,
        )
    except Exception:
        frappe.log_error(title=f"excom.send_transactional: identity resolve failed ({purpose})")
        result["skipped"] = "identity_error"
        return result
    result["identity"] = identity

    consent = (
        frappe.db.get_value(
            "Omni Identity",
            identity,
            ["do_not_contact", "whatsapp_opt_out", "email_opt_out"],
            as_dict=True,
        )
        or frappe._dict()
    )
    if consent.get("do_not_contact"):
        result["skipped"] = "do_not_contact"
        return result

    want_whatsapp = (
        channel in ("auto", "whatsapp")
        and bool(phone and whatsapp_template)
        and not consent.get("whatsapp_opt_out")
    )
    want_email = (
        channel in ("auto", "email")
        and bool(email and email_html)
        and not consent.get("email_opt_out")
    )

    # "auto" prefers WhatsApp and falls back to email; an explicit channel does not.
    if want_whatsapp:
        sent = _send_whatsapp(
            identity=identity,
            template_name=whatsapp_template,
            variables=whatsapp_variables or [],
            button_urls=whatsapp_button_urls or [],
            header_media=whatsapp_header_media,
            purpose=purpose,
            reference=reference,
        )
        if sent.get("ok") or channel == "whatsapp":
            result.update(sent)
            return result

    if want_email:
        sent = _send_email(
            identity=identity,
            email=email,
            subject=email_subject,
            body_html=email_html,
            purpose=purpose,
            reference=reference,
        )
        result.update(sent)
        return result

    result["skipped"] = result["skipped"] or "no_reachable_channel"
    return result


# ---------------------------------------------------------------------------
# Channel senders
# ---------------------------------------------------------------------------


def _send_whatsapp(
    *, identity, template_name, variables, button_urls, header_media, purpose, reference
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "channel": "whatsapp", "message_id": "", "skipped": ""}
    try:
        from excom.excom.utils import get_channel_account

        account = get_channel_account(channel="whatsapp", account_type="outgoing")
        if not account:
            out["skipped"] = "no_whatsapp_account"
            return out

        tpl_name = (
            template_name
            if frappe.db.exists("WhatsApp Templates", template_name)
            else frappe.db.get_value("WhatsApp Templates", {"template_name": template_name}, "name")
        )
        if not tpl_name:
            out["skipped"] = "template_not_found"
            return out
        template = frappe.get_doc("WhatsApp Templates", tpl_name)

        idt = frappe.get_doc("Omni Identity", identity)
        to = idt.primary_whatsapp or idt.primary_phone
        if not to:
            out["skipped"] = "no_whatsapp_number"
            return out

        from excom.excom.api.chat import _build_template_components, _build_template_preview
        from excom.excom.services.whatsapp_service import send_template_message

        components = _build_template_components(template, variables, header_media, button_urls)
        res = send_template_message(
            account=account,
            to=to,
            template_name=template.actual_name or template.name,
            language_code=template.language_code or "en_US",
            components=components,
        )
        out["ok"] = True
        out["message_id"] = res.get("provider_message_id", "")
        _record_message(
            identity=identity,
            channel="whatsapp",
            account_name=account.name,
            content_text=_build_template_preview(template, variables),
            message_type="Template",
            provider_message_id=out["message_id"],
            delivery_status=res.get("status", "Sent"),
            template_name=tpl_name,
            media_file=header_media,
            purpose=purpose,
            reference=reference,
        )
    except Exception:
        frappe.log_error(title=f"excom.send_transactional: WhatsApp send failed ({purpose})")
        out["skipped"] = "whatsapp_error"
    return out


def _send_email(*, identity, email, subject, body_html, purpose, reference) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "channel": "email", "message_id": "", "skipped": ""}
    try:
        from excom.excom.utils import get_channel_account

        account = get_channel_account(channel="email", account_type="outgoing")
        if account:
            from excom.excom.services import gmail_service

            gmail_service.send_email(
                account_name=account.name, to=email, subject=subject, body_html=body_html
            )
            account_name = account.name
        else:
            # Same fallback the broadcast executor uses when no sender account is set.
            frappe.sendmail(recipients=[email], subject=subject, message=body_html, now=True)
            account_name = ""
        out["ok"] = True
        _record_message(
            identity=identity,
            channel="email",
            account_name=account_name,
            content_text=subject,
            message_type="Email",
            purpose=purpose,
            reference=reference,
        )
    except Exception:
        frappe.log_error(title=f"excom.send_transactional: email send failed ({purpose})")
        out["skipped"] = "email_error"
    return out


def _record_message(
    *,
    identity: str,
    channel: str,
    account_name: str,
    content_text: str,
    message_type: str,
    provider_message_id: str = "",
    delivery_status: str = "Sent",
    template_name: str = "",
    media_file: str = "",
    purpose: str = "",
    reference: tuple[str, str] | None = None,
) -> None:
    """Record the send as an Excom Message on the person's thread.

    Mirrors ``broadcast_service._create_broadcast_message`` so a transactional
    send shows up in the inbox exactly like a broadcast or a reply does. Never
    raises — the message went out; failing to log it must not undo that.
    """
    try:
        from excom.excom.services.thread_service import upsert_thread

        thread_name = upsert_thread(identity, channel, account_name)
        now = now_datetime()

        doc: dict[str, Any] = {
            "doctype": "Excom Message",
            "thread": thread_name,
            "omni_identity": identity,
            "channel": channel,
            "account_doctype": "Excom Channel Account",
            "account": account_name or None,
            "direction": "Outbound",
            "message_type": message_type,
            "provider_message_id": provider_message_id,
            "provider_timestamp": now,
            "content_text": (content_text or "")[:500],
            "media_file": media_file or None,
            "delivery_status": delivery_status,
            "template": template_name or None,
        }
        if reference:
            doc["reference_doctype"] = reference[0]
            doc["reference_name"] = reference[1]
        frappe.get_doc(doc).insert(ignore_permissions=True)

        preview = (content_text or purpose or "Message")[:100]
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
    except Exception:
        frappe.log_error(title=f"excom.send_transactional: message record failed ({purpose})")


def _digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())
