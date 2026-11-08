"""
Email inbound polling adapter.

Every minute, each connected mailbox is brought up to date through its own server (Gmail or a
JMAP server such as Stalwart; see channels/email/providers). Only metadata is stored -- the body
stays in the mailbox and is fetched on demand. Where each email sits, and how a conversation
follows it, is channels/email/mailbox.py.
"""

import frappe
from frappe.utils import now_datetime, get_datetime


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
    Bring one mailbox up to date through its server (Gmail or JMAP): new mail in every folder,
    and whatever was moved, read, flagged or deleted there since the last run.
    """
    from excom.excom.channels.email import providers
    from excom.excom.channels.email.mailbox import Sink

    try:
        account = frappe.get_doc("Excom Channel Account", account_name)
        providers.get(account).sync(account, Sink(account))
    except Exception:
        frappe.log_error(
            title=f"Email poll failed: {account_name}",
        )


def _ingest_email_metadata(account_name: str, account, meta: dict):
    """One email in Gmail's shape (labels, internalDate, headers) through the common ingest.
    Kept for callers and tests that speak Gmail; providers hand mailbox.ingest the common shape."""
    from excom.excom.channels.email.mailbox import ingest
    from excom.excom.channels.email.providers.gmail import to_meta

    return ingest(account, meta if "folder" in meta else to_meta(meta))


def _parse_email_address(raw: str) -> tuple:
    from excom.excom.channels.email.mailbox import parse_address

    return parse_address(raw)
