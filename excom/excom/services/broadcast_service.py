"""
Broadcast execution service.

Handles sending broadcasts to subscriber lists via WhatsApp or Email,
including per-subscriber Jinja rendering and unsubscribe footer injection.
"""

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.utils import now_datetime, get_url


def execute_broadcast(broadcast_name: str):
    """
    Background job: send a broadcast to all active subscribers.

    Iterates each subscribed member, renders content per-subscriber,
    dispatches via the appropriate channel, and tracks results.
    """
    broadcast = frappe.get_doc("Excom Broadcast", broadcast_name)

    if broadcast.docstatus != 1 or broadcast.status not in ("Queued", "Sending"):
        return

    broadcast.db_set("status", "Sending", update_modified=False)

    subscribers = frappe.get_all(
        "Excom Subscriber",
        filters={
            "subscriber_list": broadcast.subscriber_list,
            "status": "Subscribed",
        },
        pluck="omni_identity",
    )

    sent, failed = 0, 0

    from excom.excom.services import whatsapp_errors as wae

    # A paused broadcast that is queued again continues where it stopped: nobody gets it twice.
    already_sent = set(frappe.get_all(
        "Excom Broadcast Log",
        filters={"broadcast": broadcast.name, "status": "Sent"},
        pluck="omni_identity",
    ))
    sent = len(already_sent)

    for oi_name in subscribers:
        if oi_name in already_sent:
            continue
        try:
            if broadcast.channel == "Email":
                success = _send_email_to_subscriber(broadcast, oi_name)
            elif broadcast.channel == "WhatsApp":
                success = _send_with_retry(broadcast, oi_name)
            else:
                success = False

            if success:
                sent += 1
            else:
                failed += 1
        except wae.ExcomConsentError as e:
            # opted out since joining the list: not a failure, nothing was sent
            _log_delivery(broadcast.name, oi_name, "Skipped", error=e.message)
        except wae.WhatsAppAPIError as e:
            failed += 1
            _log_delivery(broadcast.name, oi_name, "Failed", error=e.message)
            if e.error_class == wae.OPTED_OUT:
                from excom.excom.services.consent import set_marketing_opt_out
                set_marketing_opt_out(oi_name, source="meta_131050", reference_doctype="Excom Broadcast", reference_name=broadcast.name)
            if e.error_class in wae.CAMPAIGN_STOPPERS:
                # template paused / portfolio pacing drop / account restricted: every remaining send
                # would fail the same way and can hurt the account further — stop and say why.
                broadcast.db_set({"status": "Paused", "sent_count": sent, "failed_count": failed}, update_modified=True)
                broadcast.add_comment("Comment", _("Paused by Meta error {0}: {1}").format(e.code, e.message))
                frappe.db.commit()
                return
        except Exception as e:
            failed += 1
            _log_delivery(broadcast.name, oi_name, "Failed", error=str(e))
            frappe.log_error(
                title=f"Broadcast send failed: {broadcast_name} -> {oi_name}",
            )

        if (sent + failed) % 10 == 0:
            broadcast.db_set(
                {"sent_count": sent, "failed_count": failed},
                update_modified=False,
            )
            frappe.db.commit()

    final_status = "Completed"
    if failed > 0 and sent == 0:
        final_status = "Failed"
    elif failed > 0:
        final_status = "Partially Failed"

    broadcast.db_set(
        {
            "status": final_status,
            "sent_count": sent,
            "failed_count": failed,
        },
        update_modified=True,
    )
    frappe.db.commit()


def render_email_body(body_template: str, omni_identity_name: str) -> str:
    """
    Render an email body template with full subscriber context.

    Available Jinja variables:
        subscriber: Omni Identity doc
        customer:   first linked Customer (or None)
        lead:       first linked Lead (or None)
        supplier:   first linked Supplier (or None)
        contact:    first linked Contact (or None)
        frappe:     frappe module (for frappe.utils)

    Args:
        body_template: Jinja HTML template string
        omni_identity_name: Omni Identity name

    Returns:
        Rendered HTML string
    """
    context = _build_subscriber_context(omni_identity_name)
    return frappe.render_template(body_template, context)


def generate_unsubscribe_token(
    subscriber_list: str, omni_identity: str
) -> str:
    """
    Generate an HMAC-SHA256 token for stateless unsubscribe verification.

    The token encodes subscriber_list + omni_identity signed with the
    site's encryption key. No DB storage needed.
    """
    secret = frappe.utils.password.get_encryption_key()
    message = f"{subscriber_list}:{omni_identity}"
    return hmac.new(
        secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def verify_unsubscribe_token(
    subscriber_list: str, omni_identity: str, token: str
) -> bool:
    """Verify an unsubscribe token."""
    expected = generate_unsubscribe_token(subscriber_list, omni_identity)
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------


def _send_email_to_subscriber(broadcast, omni_identity_name: str) -> bool:
    """Send a single email to a subscriber with Jinja rendering + unsubscribe footer."""
    identity = frappe.get_doc("Omni Identity", omni_identity_name)
    email = identity.primary_email
    if not email:
        _log_delivery(
            broadcast.name, omni_identity_name, "Skipped",
            recipient="", error="No email address",
        )
        return False
    if identity.do_not_contact or identity.email_opt_out:
        # e.g. UNSUBSCRIBE / STOP ALL on WhatsApp: no campaign email either
        _log_delivery(
            broadcast.name, omni_identity_name, "Skipped",
            recipient=email, error="Opted out of email",
        )
        return False

    rendered_body = render_email_body(broadcast.email_body, omni_identity_name)

    footer = _build_unsubscribe_footer(
        broadcast.subscriber_list,
        omni_identity_name,
        broadcast.sender_company,
    )
    full_body = rendered_body + footer

    account_name = broadcast.sender_email_account
    if account_name:
        from excom.excom.services import gmail_service

        gmail_service.send_email(
            account_name=account_name,
            to=email,
            subject=broadcast.email_subject,
            body_html=full_body,
        )
    else:
        frappe.sendmail(
            recipients=[email],
            subject=broadcast.email_subject,
            message=full_body,
            now=True,
        )

    _create_broadcast_message(
        broadcast=broadcast,
        omni_identity_name=omni_identity_name,
        channel="email",
        account_name=account_name or "",
        content_text=broadcast.email_subject,
        message_type="Email",
    )

    _log_delivery(broadcast.name, omni_identity_name, "Sent", recipient=email)
    return True


def _build_unsubscribe_footer(
    subscriber_list: str, omni_identity: str, company_name: str = ""
) -> str:
    """Build CAN-SPAM compliant unsubscribe footer HTML."""
    token = generate_unsubscribe_token(subscriber_list, omni_identity)
    site_url = get_url()
    unsub_url = (
        f"{site_url}/api/method/excom.excom.api.unsubscribe.unsubscribe"
        f"?list={subscriber_list}&oi={omni_identity}&token={token}"
    )

    address = ""
    if company_name:
        company = frappe.db.get_value(
            "Company",
            company_name,
            ["company_name", "address_html"],
            as_dict=True,
        )
        if company and company.address_html:
            address = company.address_html
        elif company:
            address = company.company_name

    return f"""
    <div style="margin-top:30px; padding-top:15px; border-top:1px solid #eee;
                font-size:12px; color:#888; text-align:center;">
        <p>You received this email because you are subscribed to {subscriber_list}.</p>
        <p><a href="{unsub_url}" style="color:#888;">Unsubscribe</a></p>
        {f'<p>{address}</p>' if address else ''}
    </div>
    """


# ---------------------------------------------------------------------------
# WhatsApp sending
# ---------------------------------------------------------------------------


def _send_with_retry(broadcast, omni_identity_name: str) -> bool:
    """One WhatsApp send, retried once after the wait Meta asks for on rate / transient errors."""
    from excom.excom.services import whatsapp_errors as wae

    try:
        return _send_whatsapp_to_subscriber(broadcast, omni_identity_name)
    except wae.WhatsAppAPIError as e:
        # Only rate limits are retried: after a Meta-side error the message may already have been
        # accepted, and a second marketing message to the same person is worse than one missing.
        waits = {wae.RETRY_GLOBAL: min(int(e.retry_after or 60), 60), wae.RETRY_RECIPIENT: 6}
        if e.error_class not in waits:
            raise
        _sleep(waits[e.error_class])
        return _send_whatsapp_to_subscriber(broadcast, omni_identity_name)


def _sleep(seconds: int) -> None:
    if not frappe.flags.in_test:
        import time
        time.sleep(seconds)


def _send_whatsapp_to_subscriber(broadcast, omni_identity_name: str) -> bool:
    """Send a WhatsApp template message to a subscriber with variables and header media."""
    identity = frappe.get_doc("Omni Identity", omni_identity_name)
    # Meta recommends marketing to phone numbers; a username-only contact is reached by user id.
    phone = identity.primary_whatsapp or identity.primary_phone or identity.get("wa_bsuid")
    if not phone:
        _log_delivery(
            broadcast.name, omni_identity_name, "Skipped",
            recipient="", error="No phone number",
        )
        return False

    wa_account_name = broadcast.get("wa_channel_account")
    if wa_account_name:
        account = frappe.get_doc("Excom Channel Account", wa_account_name)
    else:
        from excom.excom.utils import get_channel_account
        account = get_channel_account(channel="whatsapp")

    if not account:
        _log_delivery(
            broadcast.name, omni_identity_name, "Failed",
            recipient=phone, error="No WhatsApp account configured",
        )
        return False

    template = frappe.get_doc("WhatsApp Templates", broadcast.wa_template)

    header_media = broadcast.get("wa_header_media") or ""
    button_urls = json.loads(broadcast.get("wa_button_urls") or "[]")
    language_code = broadcast.get("wa_language_code") or template.language_code or "en_US"

    variables = _resolve_whatsapp_body_variables(broadcast, omni_identity_name)

    from excom.excom.api.chat import _build_template_components

    components = _build_template_components(template, variables, header_media, button_urls)

    from excom.excom.services.whatsapp_service import send_template_message

    result = send_template_message(
        account=account,
        to=phone,
        template_name=template.actual_name or template.name,
        language_code=language_code,
        components=components,
        identity=omni_identity_name,
        gate_category="MARKETING",  # a list campaign is marketing whatever the template category
    )

    from excom.excom.api.chat import _build_template_preview
    preview = _build_template_preview(template, variables)

    _create_broadcast_message(
        broadcast=broadcast,
        omni_identity_name=omni_identity_name,
        channel="whatsapp",
        account_name=account.name,
        content_text=preview,
        message_type="Template",
        provider_message_id=result.get("provider_message_id", ""),
        delivery_status=result.get("status", "Sent"),
        media_file=header_media,
        template_name=broadcast.wa_template,
    )

    _log_delivery(broadcast.name, omni_identity_name, "Sent", recipient=phone)
    return True


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_whatsapp_body_variables(broadcast, omni_identity_name: str) -> list[str]:
    """Build ordered body variable values for WhatsApp template send.

    Prefer ``wa_variable_slots`` (per-variable literal vs subscriber field). If
    absent, use legacy ``wa_variable_mode`` + template_variables / mapping.
    """
    slots_raw = (broadcast.get("wa_variable_slots") or "").strip()
    if slots_raw:
        try:
            slots = json.loads(slots_raw)
        except json.JSONDecodeError:
            slots = []
        if isinstance(slots, list) and len(slots) > 0:
            context = _build_subscriber_context(omni_identity_name)
            return _resolve_variable_slots(slots, context)

    variable_mode = broadcast.get("wa_variable_mode") or "same_for_all"
    if variable_mode == "per_subscriber":
        mapping = json.loads(broadcast.get("wa_variable_mapping") or "[]")
        context = _build_subscriber_context(omni_identity_name)
        return _resolve_variable_mapping(mapping, context)

    return json.loads(broadcast.get("wa_template_variables") or "[]")


def _resolve_variable_slots(slots: list, context: dict) -> list[str]:
    """Resolve per-slot definitions to a list of strings for Meta body parameters."""
    values: list[str] = []
    for slot in slots:
        if not isinstance(slot, dict):
            values.append("")
            continue
        kind = slot.get("kind")
        if kind == "literal":
            values.append(str(slot.get("value", "") or ""))
        elif kind == "subscriber_field":
            path = (slot.get("field") or "").strip()
            if path and "." in path:
                resolved = _resolve_variable_mapping([path], context)
                values.append(resolved[0] if resolved else "")
            else:
                values.append("")
        else:
            values.append(str(slot.get("value", "") or ""))
    return values


def _resolve_variable_mapping(mapping: list, context: dict) -> list:
    """Resolve per-subscriber variable mapping to actual values.

    Args:
        mapping: List of dotted field paths like ["subscriber.display_name", "customer.customer_name"]
        context: Subscriber context dict from _build_subscriber_context

    Returns:
        List of resolved string values
    """
    values = []
    for field_path in mapping:
        if not field_path or "." not in field_path:
            values.append("")
            continue
        obj_key, attr = field_path.split(".", 1)
        obj = context.get(obj_key)
        if obj and hasattr(obj, attr):
            values.append(str(getattr(obj, attr) or ""))
        elif obj and isinstance(obj, dict):
            values.append(str(obj.get(attr, "")))
        else:
            values.append("")
    return values


def _build_subscriber_context(omni_identity_name: str) -> dict:
    """
    Build full Jinja context for a subscriber.

    Loads the Omni Identity and all linked ERP entities
    (Customer, Lead, Supplier, Contact).
    """
    identity = frappe.get_doc("Omni Identity", omni_identity_name)

    context = {
        "subscriber": identity,
        "customer": None,
        "lead": None,
        "supplier": None,
        "contact": None,
        "frappe": frappe._dict({"utils": frappe.utils}),
    }

    for link in identity.get("linked_entities", []):
        dt = link.linked_doctype
        key_map = {
            "Customer": "customer",
            "Lead": "lead",
            "Supplier": "supplier",
            "Contact": "contact",
        }
        key = key_map.get(dt)
        if key and context[key] is None:
            try:
                context[key] = frappe.get_cached_doc(dt, link.linked_name)
            except frappe.DoesNotExistError:
                pass

    return context


def _create_broadcast_message(
    broadcast,
    omni_identity_name: str,
    channel: str,
    account_name: str,
    content_text: str,
    message_type: str,
    provider_message_id: str = "",
    delivery_status: str = "Sent",
    media_file: str = "",
    template_name: str = "",
) -> None:
    """Create an Excom Thread (if needed) and Excom Message for a broadcast send.

    This ensures broadcast messages appear in the subscriber's inbox thread,
    so conversation history is complete when they reply.
    """
    from excom.excom.services.thread_service import upsert_thread

    try:
        thread_name = upsert_thread(omni_identity_name, channel, account_name)
        now = now_datetime()

        frappe.get_doc({
            "doctype": "Excom Message",
            "thread": thread_name,
            "omni_identity": omni_identity_name,
            "channel": channel,
            "account_doctype": "Excom Channel Account",
            "account": account_name,
            "direction": "Outbound",
            "message_type": message_type,
            "provider_message_id": provider_message_id,
            "provider_timestamp": now,
            "content_text": content_text[:500] if content_text else "",
            "media_file": media_file or None,
            "delivery_status": delivery_status,
            "template": template_name or None,
            "reference_doctype": "Excom Broadcast",
            "reference_name": broadcast.name,
        }).insert(ignore_permissions=True)

        preview = content_text[:100] if content_text else "Broadcast message"
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
        frappe.log_error(
            title=f"Broadcast message record failed: {broadcast.name} -> {omni_identity_name}",
        )


def _log_delivery(
    broadcast_name: str,
    omni_identity: str,
    status: str,
    recipient: str = "",
    error: str = "",
):
    """Create a Broadcast Log entry for per-recipient tracking."""
    frappe.get_doc({
        "doctype": "Excom Broadcast Log",
        "broadcast": broadcast_name,
        "omni_identity": omni_identity,
        "status": status,
        "recipient_address": recipient,
        "error_message": error[:500] if error else "",
        "sent_at": now_datetime() if status == "Sent" else None,
    }).insert(ignore_permissions=True)
