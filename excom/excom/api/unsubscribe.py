"""
Public unsubscribe endpoint for Excom email broadcasts.

Handles tokenized unsubscribe links embedded in broadcast emails.
CAN-SPAM compliant: one-click, immediate effect, no login required.
"""

import frappe
from frappe.rate_limiter import rate_limit
from frappe import _
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60)
def unsubscribe():
    """
    Process an unsubscribe request from an email footer link.

    Query params:
        list: Excom Subscriber List name
        oi: Omni Identity name
        token: HMAC-SHA256 verification token

    Returns an HTML confirmation page.
    """
    subscriber_list = frappe.form_dict.get("list", "")
    omni_identity = frappe.form_dict.get("oi", "")
    token = frappe.form_dict.get("token", "")

    if not subscriber_list or not omni_identity or not token:
        frappe.respond_as_web_page(
            _("Invalid Request"),
            _("The unsubscribe link is invalid or incomplete."),
            http_status_code=400,
        )
        return

    from excom.excom.services.broadcast_service import verify_unsubscribe_token

    if not verify_unsubscribe_token(subscriber_list, omni_identity, token):
        frappe.respond_as_web_page(
            _("Invalid Token"),
            _("This unsubscribe link has expired or is invalid."),
            http_status_code=403,
        )
        return

    subscriber_name = frappe.db.get_value(
        "Excom Subscriber",
        {"subscriber_list": subscriber_list, "omni_identity": omni_identity},
        "name",
    )

    if not subscriber_name:
        frappe.respond_as_web_page(
            _("Already Unsubscribed"),
            _("You are not currently subscribed to this list."),
        )
        return

    frappe.db.set_value(
        "Excom Subscriber",
        subscriber_name,
        {
            "status": "Unsubscribed",
            "unsubscribed_on": now_datetime(),
        },
    )
    frappe.db.commit()

    display_name = frappe.db.get_value(
        "Omni Identity", omni_identity, "display_name"
    ) or ""

    frappe.respond_as_web_page(
        _("Unsubscribed"),
        _(
            "You have been successfully unsubscribed from <b>{0}</b>. "
            "You will no longer receive emails from this list."
        ).format(subscriber_list),
        http_status_code=200,
    )
