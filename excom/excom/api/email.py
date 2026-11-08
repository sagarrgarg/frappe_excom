"""
Email API endpoints for Excom.

Provides on-demand email body fetching from Gmail (never stored locally),
search delegation to Gmail API, email sending, and connection management.
"""

import json

import frappe
from frappe import _

from excom.excom.api.chat import _check_excom_access, _check_thread_access
from excom.excom.channels.email.outbound import send_email_reply


@frappe.whitelist()
def get_email_body(message_name: str):
    """
    Fetch the full email body from Gmail API on-demand.
    The body is returned to the frontend but NEVER written to the database.

    If the email has been deleted from Gmail, returns the stored metadata
    with a 'deleted' flag so the UI can show a graceful notice.

    Args:
        message_name: Excom Message name

    Returns:
        {
            "body_html": "...",
            "body_text": "...",
            "subject": "...",
            "from_email": "...",
            "from_name": "...",
            "to": "...",
            "cc": "...",
            "date": "...",
            "attachments": [...],
            "deleted": false
        }
    """
    _check_excom_access()
    # The body belongs to a conversation, so the conversation decides who may read it. Checking
    # only "is this an Excom user" let any agent read another desk's mail.
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    if not thread:
        frappe.throw(_("No such message"), frappe.PermissionError)
    _check_thread_access(thread)
    msg = frappe.get_doc("Excom Message", message_name)

    if msg.message_type != "Email":
        frappe.throw(_("Message {0} is not an email").format(message_name))

    gmail_msg_id = msg.provider_message_id
    if not gmail_msg_id:
        frappe.throw(_("Message {0} has no mailbox message ID").format(message_name))

    account_name = msg.account

    try:
        content_json = json.loads(msg.content_json or "{}")
    except (json.JSONDecodeError, TypeError):
        content_json = {}

    try:
        full = _server_for(msg).fetch_full(frappe.get_doc("Excom Channel Account", account_name), _raw(msg))
    except Exception as e:
        err_str = str(e)
        frappe.log_error(
            title=f"Gmail body fetch failed: {gmail_msg_id}",
            message=err_str,
        )
        # Distinguish auth errors from missing messages
        is_auth_error = any(
            code in err_str for code in ("401", "403", "invalid_grant", "unauthorized", "Unauthorized", "No valid OAuth2")
        )
        error_msg = (
            "The mailbox refused the login. Please reconnect the email account."
            if is_auth_error
            else "Could not fetch it from the mailbox. The email may have been deleted or moved."
        )
        return {
            "body_html": "",
            "body_text": "",
            "subject": content_json.get("subject", ""),
            "from_email": content_json.get("from_email", ""),
            "from_name": content_json.get("from_name", ""),
            "to": content_json.get("to", ""),
            "cc": content_json.get("cc", ""),
            "date": content_json.get("date", ""),
            "attachments": [],
            "deleted": True,
            "error": error_msg,
            "auth_error": is_auth_error,
        }

    if full.get("deleted"):
        return {
            "body_html": "",
            "body_text": "",
            "subject": content_json.get("subject", ""),
            "from_email": content_json.get("from_email", ""),
            "from_name": content_json.get("from_name", ""),
            "to": content_json.get("to", ""),
            "cc": content_json.get("cc", ""),
            "date": content_json.get("date", ""),
            "attachments": [],
            "deleted": True,
            "error": "This email is no longer in the mailbox.",
        }

    return {
        "body_html": full.get("body_html", ""),
        "body_text": full.get("body_text", ""),
        "subject": full.get("headers", {}).get("Subject", content_json.get("subject", "")),
        "from_email": content_json.get("from_email", ""),
        "from_name": content_json.get("from_name", ""),
        "to": full.get("headers", {}).get("To", content_json.get("to", "")),
        "cc": full.get("headers", {}).get("Cc", content_json.get("cc", "")),
        "date": full.get("headers", {}).get("Date", content_json.get("date", "")),
        "attachments": full.get("attachments", []),
        "deleted": False,
    }


@frappe.whitelist()
def get_email_attachment(
    message_name: str,
    attachment_id: str,
    filename: str = "",
) -> None:
    """
    Download an email attachment directly from Gmail API.

    Streams the binary content to the user's browser without saving to the
    Frappe server filesystem. Gmail is the single source of truth.

    Args:
        message_name: Excom Message name
        attachment_id: Gmail attachment ID
        filename: Original filename for the Content-Disposition header
    """
    _check_excom_access()
    # An attachment is part of a conversation, and so is who may download it.
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    if not thread:
        frappe.throw(_("No such message"), frappe.PermissionError)
    _check_thread_access(thread)
    msg = frappe.get_doc("Excom Message", message_name)
    if msg.message_type != "Email":
        frappe.throw(_("Message {0} is not an email").format(message_name))

    file_bytes = _server_for(msg).fetch_attachment(frappe.get_doc("Excom Channel Account", msg.account), _raw(msg), attachment_id)

    safe_name = (filename or f"attachment_{attachment_id[:8]}").replace('"', "")
    frappe.local.response.filename = safe_name
    frappe.local.response.filecontent = file_bytes
    frappe.local.response.type = "download"


@frappe.whitelist()
def search_emails(account_name: str, query: str, max_results: int = 20):
    """
    Delegate email search to Gmail API using Gmail's native query syntax.
    No local full-text search -- everything goes through Gmail.

    Examples of Gmail query syntax:
        - "from:john@example.com"
        - "subject:invoice"
        - "has:attachment after:2026/01/01"
        - "is:unread"

    Args:
        account_name: Excom Channel Account name
        query: Gmail search query string
        max_results: Maximum number of results

    Returns:
        List of email metadata dicts
    """
    _check_excom_access()
    from excom.excom.api.chat import get_channel_accounts
    if account_name not in {a["name"] for a in get_channel_accounts()}:
        frappe.throw(_("You do not have access to that mailbox"), frappe.PermissionError)
    max_results = min(int(max_results), 50)
    from excom.excom.channels.email import providers
    account = frappe.get_doc("Excom Channel Account", account_name)
    return providers.get(account).search(account, query, max_results)


@frappe.whitelist()
def send_email(
    thread_id: str,
    to: str,
    subject: str,
    body_html: str,
    cc: str = "",
    bcc: str = "",
    in_reply_to_gmail_id: str = "",
    send_at: str = "",
    draft_name: str = "",
):
    """
    Send an email on an existing email conversation, through the mailbox's server.
    draft_name: the draft being sent; it leaves Drafts in the mailbox too.

    Args:
        thread_id: Excom Thread name
        to: Recipient email address
        subject: Email subject
        body_html: HTML body content
        cc: CC recipients
        bcc: BCC recipients
        in_reply_to_gmail_id: Gmail message ID being replied to

    Returns:
        {"success": True, "message_name": "..."}
    """
    from excom.excom.api.chat import _check_thread_access, _check_send_access, _claim_on_talk
    _check_thread_access(thread_id)  # before anything else: sending from a thread you cannot see is not allowed
    _check_send_access(thread_id)
    _claim_on_talk(thread_id)
    if send_at:
        from frappe.utils import get_datetime, now_datetime
        when = get_datetime(send_at)
        if when > now_datetime():
            out = schedule_email(thread_id, to, subject, body_html, cc, bcc, in_reply_to_gmail_id, when)
            if draft_name and frappe.db.get_value("Excom Email Draft", draft_name, "thread") == thread_id:
                from excom.excom.channels.email import drafts
                drafts.discard(draft_name)  # it waits in Scheduled now, not in Drafts
            return out
    _check_excom_access()
    try:
        msg_name = send_email_reply(
            thread_name=thread_id,
            to=to,
            subject=subject,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            in_reply_to_gmail_id=in_reply_to_gmail_id,
            draft_name=draft_name if draft_name and frappe.db.get_value("Excom Email Draft", draft_name, "thread") == thread_id else "",
        )
        frappe.db.commit()
        return {"success": True, "message_name": msg_name}
    except Exception as e:
        frappe.log_error(
            title="Excom send_email failed",
            message=f"thread_id={thread_id}\n{str(e)}",
        )
        raise


@frappe.whitelist()
def check_gmail_connection(account_name: str):
    """
    Verify Gmail OAuth2 connection is active.

    Returns:
        {"success": True, "email": "user@gmail.com", "historyId": "..."}
    """
    _check_excom_access()
    from excom.excom.channels.email import providers
    account = frappe.get_doc("Excom Channel Account", account_name)
    r = providers.get(account).check(account)
    return {"success": bool(r.get("ok")), "email": r.get("email", ""), "messagesTotal": r.get("total", 0), "error": r.get("error", "")}


@frappe.whitelist()
def manual_sync(account_name: str):
    """
    Trigger a manual email sync for a specific account.
    Useful for testing or when the scheduler hasn't run yet.
    """
    _check_excom_access()
    from excom.excom.channels.email.inbound import poll_single_account

    try:
        poll_single_account(account_name)
        return {"success": True}
    except Exception as e:
        frappe.log_error(title=f"Manual email sync failed: {account_name}")
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def get_my_signature() -> dict:
    """
    Return the current user's active email signature.

    Returns:
        {signature_html: str, position: str, exists: bool}
    """
    _check_excom_access()
    user = frappe.session.user
    name = frappe.db.get_value("Excom Email Signature", {"user": user, "is_active": 1}, "name")
    if not name:
        return {"exists": False, "signature_html": "", "position": "Below Reply"}
    doc = frappe.get_doc("Excom Email Signature", name)
    return {
        "exists": True,
        "signature_html": doc.signature_html or "",
        "position": doc.position or "Below Reply",
    }


@frappe.whitelist()
def save_my_signature(signature_html: str, position: str = "Below Reply") -> dict:
    """
    Upsert the current user's email signature.

    Args:
        signature_html: HTML string for the signature body
        position: "Below Reply" or "Below All"

    Returns:
        {success: bool}
    """
    _check_excom_access()
    user = frappe.session.user
    existing = frappe.db.get_value("Excom Email Signature", {"user": user}, "name")
    if existing:
        doc = frappe.get_doc("Excom Email Signature", existing)
        doc.signature_html = signature_html
        doc.position = position
        doc.is_active = 1
        doc.save(ignore_permissions=False)
    else:
        doc = frappe.get_doc({
            "doctype": "Excom Email Signature",
            "user": user,
            "signature_html": signature_html,
            "position": position,
            "is_active": 1,
        })
        doc.insert(ignore_permissions=False)
    frappe.db.commit()
    return {"success": True}



# ─── scheduled send ───────────────────────────────────────────────────────────

def schedule_email(thread_id, to, subject, body_html, cc, bcc, in_reply_to_gmail_id, when) -> dict:
    """Park the email as an Excom Message with delivery_status Scheduled; the scheduler sends it at `when`."""
    import json
    thread = frappe.get_doc("Excom Thread", thread_id)
    msg = frappe.get_doc({
        "doctype": "Excom Message", "thread": thread_id, "omni_identity": thread.omni_identity, "direction": "Outbound",
        "message_type": "Email", "channel": thread.channel, "account_doctype": thread.account_doctype, "account": thread.account,
        "delivery_status": "Scheduled", "scheduled_at": when, "content_text": frappe.utils.strip_html(body_html)[:2000],
        "content_json": json.dumps({"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body_html": body_html, "in_reply_to_gmail_id": in_reply_to_gmail_id}),
        "created_by_user": frappe.session.user,
    })
    msg.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.publish_realtime("excom:thread_updated", {"thread": thread_id, "event": "scheduled"}, after_commit=True)
    return {"success": True, "scheduled": True, "message_name": msg.name, "send_at": str(when)}


@frappe.whitelist()
def cancel_scheduled_email(message_name: str) -> dict:
    from excom.excom.api.chat import _check_thread_access
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    _check_thread_access(thread)
    if frappe.db.get_value("Excom Message", message_name, "delivery_status") != "Scheduled":
        frappe.throw(_("Only scheduled emails can be cancelled"))
    frappe.delete_doc("Excom Message", message_name, force=True, ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}


def send_scheduled_emails() -> None:
    """Scheduler (every minute): send every Scheduled email whose time has come, as the user who scheduled it."""
    import json
    from frappe.utils import now_datetime
    due = frappe.get_all("Excom Message", filters={"delivery_status": "Scheduled", "scheduled_at": ["<=", now_datetime()]}, fields=["name", "thread", "content_json", "created_by_user"])
    for m in due:
        try:
            p = json.loads(m.content_json or "{}")
            frappe.set_user(m.created_by_user or "Administrator")
            send_email_reply(thread_name=m.thread, to=p.get("to", ""), subject=p.get("subject", ""), body_html=p.get("body_html", ""), cc=p.get("cc", ""), bcc=p.get("bcc", ""), in_reply_to_gmail_id=p.get("in_reply_to_gmail_id", ""))
            frappe.delete_doc("Excom Message", m.name, force=True, ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.db.set_value("Excom Message", m.name, {"delivery_status": "Failed", "failure_reason": frappe.get_traceback()[-500:]})
            frappe.db.commit()
            frappe.log_error(title=f"Excom scheduled email failed: {m.name}", message=frappe.get_traceback())
        finally:
            frappe.set_user("Administrator")


@frappe.whitelist()
def suggest_recipients(q: str = "", limit: int = 12) -> list:
    """Addresses to tag in To / Cc / Bcc: this site's contacts and colleagues."""
    from excom.excom.api.chat import _check_excom_access
    _check_excom_access()
    q = (q or "").strip()
    like = f"%{q}%"
    out, seen = [], set()
    def add(email, name, kind):
        e = (email or "").strip().lower()
        if e and "@" in e and e not in seen:
            seen.add(e); out.append({"email": e, "name": name or e, "kind": kind})
    oi_filters = [["primary_email", "!=", ""]]
    oi_or = [["primary_email", "like", like], ["display_name", "like", like]] if q else None
    for r in frappe.get_all("Omni Identity", filters=oi_filters, or_filters=oi_or, fields=["display_name", "primary_email"], limit=limit):
        add(r.primary_email, r.display_name, "contact")
    for r in frappe.get_all("User", filters=[["enabled", "=", 1], ["user_type", "=", "System User"], ["name", "!=", "Administrator"]] + ([["full_name", "like", like]] if q else []), fields=["name", "full_name"], limit=limit):
        add(r.name, r.full_name, "colleague")
    return out[:limit]


# ─── which server holds an email ──────────────────────────────────────────────

def _server_for(msg):
    """The provider an email was synced from. Emails from before a mailbox moved to Stalwart keep
    their Gmail ids and are read from Gmail while it still lets us."""
    from excom.excom.channels.email import providers
    if providers.provider_of_stored(msg.provider_message_id) == providers.JMAP:
        from excom.excom.channels.email.providers import jmap
        return jmap
    from excom.excom.channels.email.providers import gmail
    return gmail


def _raw(msg) -> str:
    from excom.excom.channels.email import providers
    return providers.raw_id(frappe._dict(name=msg.account), msg.provider_message_id)


# ─── drafts (kept in step with the mailbox's Drafts folder) ──────────────────

@frappe.whitelist()
def get_email_draft(thread_id: str):
    """The conversation's draft, if any, with its text (fetched from the mailbox if written there)."""
    from excom.excom.api.chat import _check_thread_access
    from excom.excom.channels.email import drafts
    _check_thread_access(thread_id)
    return drafts.for_thread(thread_id)


@frappe.whitelist(methods=["POST"])
def save_email_draft(thread_id: str, to: str = "", cc: str = "", bcc: str = "", subject: str = "",
                     body_html: str = "", in_reply_to_message: str = "", draft_name: str = ""):
    """Save while typing: here and in the mailbox's Drafts. Same access as replying."""
    from excom.excom.api.chat import _check_thread_access, _check_send_access
    from excom.excom.channels.email import drafts
    _check_thread_access(thread_id)
    _check_send_access(thread_id)
    out = drafts.save(thread_id, to, cc, bcc, subject, body_html, in_reply_to_message, draft_name)
    frappe.db.commit()
    return out


@frappe.whitelist(methods=["POST"])
def discard_email_draft(draft_name: str):
    from excom.excom.api.chat import _check_thread_access
    from excom.excom.channels.email import drafts
    thread = frappe.db.get_value("Excom Email Draft", draft_name, "thread")
    if not thread:
        return {"ok": True}
    _check_thread_access(thread)
    drafts.discard(draft_name)
    frappe.db.commit()
    return {"ok": True}


# ─── JMAP (Stalwart): connect, and live updates ──────────────────────────────

@frappe.whitelist(methods=["POST"])
def connect_mail_server(account_name: str):
    """Sign in to the JMAP server, find the folders and the sending identity, and ask for live
    updates. Admins only: it reads the stored password."""
    from excom.excom.api.chat import _check_admin_access
    from excom.excom.channels.email.providers import jmap
    _check_admin_access()
    account = frappe.get_doc("Excom Channel Account", account_name)
    if account.channel != "email" or (account.email_provider or "Gmail") != "JMAP":
        frappe.throw(_("{0} is not a JMAP mailbox").format(account_name))
    r = jmap.check(account)
    push = ""
    if r.get("ok") and account.get("jmap_push"):
        try:
            jmap.ensure_push(account, push_url(account))
            push = "requested"
        except Exception as e:
            push = f"not available: {str(e)[:200]}"
            frappe.log_error(title=f"Excom JMAP push setup failed: {account_name}", message=frappe.get_traceback())
    frappe.db.commit()
    return {**r, "push": push}


def push_url(account) -> str:
    from excom.excom.channels.email.providers import jmap
    token = jmap.push_token(account)
    return frappe.utils.get_url(f"/api/method/excom.excom.api.email.jmap_push?account={frappe.utils.quote(account.name)}&token={token}")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def jmap_push(account: str = "", token: str = ""):
    """The mail server calls this when something changed (RFC 8620 push). The token in the URL is
    the account's own secret; anything else is ignored. The actual work is a normal sync."""
    from frappe.utils.password import get_decrypted_password
    if not account or not token or not frappe.db.exists("Excom Channel Account", account):
        frappe.local.response.http_status_code = 404
        return
    secret = get_decrypted_password("Excom Channel Account", account, "jmap_push_token", raise_exception=False)
    import hmac
    if not secret or not hmac.compare_digest(str(token), str(secret)):
        frappe.local.response.http_status_code = 403
        return
    payload = frappe.request.get_json(silent=True) or {}
    from excom.excom.channels.email.providers import jmap
    frappe.set_user("Administrator")
    acc = frappe.get_doc("Excom Channel Account", account)
    if jmap.handle_push(acc, payload):
        frappe.enqueue(
            "excom.excom.channels.email.inbound.poll_single_account",
            account_name=account, queue="short", deduplicate=True, job_id=f"email_poll:{account}",
        )
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist(methods=["POST"])
def set_email_flag(message_name: str, flagged: int = 1):
    """Star an email (Gmail) / flag it (Outlook, Stalwart): here and in the mailbox."""
    from excom.excom.api.chat import _check_thread_access
    from excom.excom.channels.email import mailbox
    thread = frappe.db.get_value("Excom Message", message_name, "thread")
    if not thread:
        frappe.throw(_("No such message"), frappe.PermissionError)
    _check_thread_access(thread)
    mailbox.set_message_flag(message_name, bool(int(flagged)))
    frappe.db.commit()
    return {"flagged": bool(int(flagged))}
