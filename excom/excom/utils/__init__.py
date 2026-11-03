"""Run on each event."""
import frappe
from frappe.utils import now_datetime, time_diff_in_seconds

from frappe.core.doctype.server_script.server_script_utils import EVENT_MAP
from excom.excom.utils.graph import graph_version


def run_server_script_for_doc_event(doc, event):
    """Run on each event."""
    if event not in EVENT_MAP:
        return

    if frappe.flags.in_install:
        return

    if frappe.flags.in_migrate:
        return
    
    if frappe.flags.in_uninstall:
        return

    notification = get_notifications_map().get(
        doc.doctype, {}
    ).get(EVENT_MAP[event], None)

    if notification:
        for notification_name in notification:
            frappe.get_doc(
                _notification_doctype(),
                notification_name,
            ).send_template_message(doc)


def _notification_doctype() -> str:
    """Return the active notification doctype name (handles pre/post rename)."""
    if frappe.db.table_exists("Excom Notification"):
        return "Excom Notification"
    return "WhatsApp Notification"


def _notification_log_doctype() -> str:
    """Return the active notification log doctype name."""
    if frappe.db.table_exists("Excom Notification Log"):
        return "Excom Notification Log"
    return "WhatsApp Notification Log"


def get_notifications_map():
    """Get mapping."""
    dt = _notification_doctype()
    if frappe.flags.in_patch and not frappe.db.table_exists(dt):
        return {}

    notification_map = {}
    enabled_notifications = frappe.get_all(
        dt,
        fields=("name", "reference_doctype", "doctype_event", "notification_type"),
        filters={"disabled": 0},
    )
    for notification in enabled_notifications:
        if notification.notification_type == "DocType Event":
            notification_map.setdefault(
                notification.reference_doctype, {}
            ).setdefault(
                notification.doctype_event, []
            ).append(notification.name)

    frappe.cache().set_value("excom_notification_map", notification_map)

    return notification_map


def trigger_whatsapp_notifications_all():
    """Run all."""
    trigger_whatsapp_notifications("All")


def trigger_whatsapp_notifications_hourly():
    """Run hourly."""
    trigger_whatsapp_notifications("Hourly")


def trigger_whatsapp_notifications_daily():
    """Run daily."""
    trigger_whatsapp_notifications("Daily")


def trigger_whatsapp_notifications_weekly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Weekly")


def trigger_whatsapp_notifications_monthly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Monthly")


def trigger_whatsapp_notifications_yearly():
    """Trigger notification."""
    trigger_whatsapp_notifications("Yearly")


def trigger_whatsapp_notifications_hourly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Hourly Long")


def trigger_whatsapp_notifications_daily_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Daily Long")


def trigger_whatsapp_notifications_weekly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Weekly Long")


def trigger_whatsapp_notifications_monthly_long():
    """Trigger notification."""
    trigger_whatsapp_notifications("Monthly Long")


def trigger_whatsapp_notifications(event):
    """Run cron."""
    dt = _notification_doctype()
    notify_list = frappe.get_list(
        dt,
        filters={
            "event_frequency": event,
            "disabled": 0,
        },
    )

    for n in notify_list:
        frappe.get_doc(dt, n.name).send_scheduled_message()


def process_pending_whatsapp_notification_logs(batch_size: int = 100):
    """Process delayed notifications queued in log table."""
    if frappe.flags.in_import or frappe.flags.in_patch:
        return

    log_dt = _notification_log_doctype()
    now_dt = now_datetime()
    pending_logs = frappe.get_all(
        log_dt,
        filters={"status": "Pending"},
        fields=["name", "pending_since", "scheduled_for"],
        limit_page_length=batch_size,
        order_by="scheduled_for asc",
    )

    for log in pending_logs:
        updates = {}
        if log.pending_since:
            updates["pending_for_seconds"] = max(0, int(time_diff_in_seconds(now_dt, log.pending_since)))
        if updates:
            frappe.db.set_value(log_dt, log.name, updates, update_modified=False)

    due_logs = [log for log in pending_logs if not log.scheduled_for or log.scheduled_for <= now_dt]
    for due in due_logs:
        _process_pending_notification_log(due.name)


def _process_pending_notification_log(log_name: str):
    """Process one pending notification log entry safely."""
    log_dt = _notification_log_doctype()
    notif_dt = _notification_doctype()

    log = frappe.get_doc(log_dt, log_name)
    if log.status != "Pending":
        return

    if not log.notification or not frappe.db.exists(notif_dt, log.notification):
        frappe.db.set_value(log_dt, log_name, {
            "status": "Cancelled",
            "reason": "Notification config missing",
            "processed_on": now_datetime(),
        }, update_modified=True)
        return

    if not log.reference_doctype or not log.reference_name:
        frappe.db.set_value(log_dt, log_name, {
            "status": "Skipped",
            "reason": "Reference document not available",
            "processed_on": now_datetime(),
        }, update_modified=True)
        return

    if not frappe.db.exists(log.reference_doctype, log.reference_name):
        frappe.db.set_value(log_dt, log_name, {
            "status": "Cancelled",
            "reason": "Reference document deleted",
            "processed_on": now_datetime(),
        }, update_modified=True)
        return

    reference_doc = frappe.get_doc(log.reference_doctype, log.reference_name)
    if hasattr(reference_doc, "docstatus") and reference_doc.docstatus == 2:
        frappe.db.set_value(log_dt, log_name, {
            "status": "Cancelled",
            "reason": "Reference document cancelled",
            "processed_on": now_datetime(),
        }, update_modified=True)
        return

    notification = frappe.get_doc(notif_dt, log.notification)
    try:
        notification.send_template_message(
            reference_doc,
            phone_no=log.to_number,
            ignore_condition=True,
            notification_log_name=log.name,
            force_send=True,
        )
        frappe.db.set_value(log_dt, log_name, {
            "status": "Sent",
            "processed_on": now_datetime(),
        }, update_modified=True)
    except Exception as e:
        frappe.db.set_value(log_dt, log_name, {
            "status": "Failed",
            "reason": str(e),
            "processed_on": now_datetime(),
        }, update_modified=True)
        frappe.log_error(frappe.get_traceback(), "Excom delayed notification failed")


def get_channel_account(phone_id=None, channel='whatsapp', account_type='incoming'):
    """
    Get Excom Channel Account by phone_id or default for channel.

    Args:
        phone_id: WhatsApp phone number ID to look up by wa_phone_id field.
        channel: Channel type filter (e.g. 'whatsapp').
        account_type: 'incoming' or 'outgoing' to pick the default account.

    Returns:
        Excom Channel Account doc or None.
    """
    if phone_id:
        account_name = frappe.db.get_value(
            'Excom Channel Account', {'wa_phone_id': phone_id}, 'name'
        )
        if account_name:
            return frappe.get_doc("Excom Channel Account", account_name)

    account_field_type = 'is_default_incoming' if account_type == 'incoming' else 'is_default_outgoing'
    default_account_name = frappe.db.get_value(
        'Excom Channel Account',
        {account_field_type: 1, 'channel': channel},
        'name',
    )
    if default_account_name:
        return frappe.get_doc("Excom Channel Account", default_account_name)

    return None




def get_wa_credentials(account_doc) -> dict:
    """Extract WhatsApp API credentials from an Excom Channel Account doc.

    Returns:
        dict with keys: token, url, version, phone_id, business_id, app_id, headers
    """
    token = account_doc.get_password("wa_token")
    if not token:
        frappe.throw(f"No access token configured for account {account_doc.name}")

    url = account_doc.wa_url or "https://graph.facebook.com"
    version = graph_version(account_doc.wa_version)
    phone_id = account_doc.wa_phone_id
    business_id = account_doc.wa_business_id or ""
    app_id = account_doc.wa_app_id or ""

    return {
        "token": token,
        "url": url,
        "version": version,
        "phone_id": phone_id,
        "business_id": business_id,
        "app_id": app_id,
        "headers": {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
    }


def format_number(number: str) -> str:
    """Strip leading '+' from phone numbers."""
    if number.startswith("+"):
        number = number[1:]
    return number
