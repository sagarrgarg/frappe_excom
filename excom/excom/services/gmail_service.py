"""
Gmail API service layer for Excom email integration.

Uses Frappe's Connected App OAuth2 infrastructure for authentication.
Gmail is the storage backend -- bodies are NEVER stored in the Frappe database.
Only lightweight metadata pointers (Message-ID, Thread-ID, From, Subject,
Date, snippet) are persisted locally.
"""

import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.utils.password import set_encrypted_password

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Refresh a little before the real expiry so an in-flight request never races
# the token going stale.
TOKEN_EXPIRY_SKEW_SECONDS = 120
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

DOCTYPE = "Excom Channel Account"


def get_access_token(account_name: str) -> str:
    """
    Return a valid OAuth2 access token for the given Excom Channel Account.

    Tokens are stored per-account (encrypted) on the Excom Channel Account
    itself -- NOT in Frappe's shared Connected App Token Cache, which is keyed
    only by (connected_app, user) and therefore collapses multiple Gmail
    mailboxes authorized by the same operator onto one record. Reading from the
    account's own fields keeps every mailbox isolated.

    Refreshes transparently when the stored access token is missing or expired.
    """
    account = frappe.get_doc(DOCTYPE, account_name)

    if account.channel != "email":
        frappe.throw(_("Account {0} is not an email account").format(account_name))

    access_token = account.get_password("email_access_token", raise_exception=False)
    expiry = account.email_token_expiry

    if access_token and expiry and not _is_expired(expiry):
        return access_token

    # Missing or (nearly) expired -> refresh using the account's own refresh token.
    return _refresh_account_token(account)


def _is_expired(expiry) -> bool:
    """True if we are within the skew window of (or past) the token expiry."""
    threshold = add_to_date(now_datetime(), seconds=TOKEN_EXPIRY_SKEW_SECONDS)
    return get_datetime(expiry) <= threshold


def _store_account_tokens(
    account_name: str,
    access_token: str,
    refresh_token: str | None,
    expires_in,
    token_type: str = "Bearer",
) -> None:
    """Persist tokens on the account. refresh_token is only written when present
    (Google omits it on refresh; it is only re-issued on a prompt=consent flow)."""
    set_encrypted_password(DOCTYPE, account_name, access_token or "", "email_access_token")
    if refresh_token:
        set_encrypted_password(DOCTYPE, account_name, refresh_token, "email_refresh_token")

    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = DEFAULT_TOKEN_LIFETIME_SECONDS

    frappe.db.set_value(
        DOCTYPE,
        account_name,
        {
            "email_token_expiry": add_to_date(now_datetime(), seconds=seconds),
            "email_token_type": token_type or "Bearer",
            "email_authorized": 1,
        },
        update_modified=False,
    )
    frappe.db.commit()


def _deauthorize(account_name: str, reason: str) -> None:
    """Wipe stored tokens and mark the account unauthorized (refresh token dead)."""
    set_encrypted_password(DOCTYPE, account_name, "", "email_access_token")
    set_encrypted_password(DOCTYPE, account_name, "", "email_refresh_token")
    frappe.db.set_value(
        DOCTYPE,
        account_name,
        {"email_token_expiry": None, "email_authorized": 0},
        update_modified=False,
    )
    frappe.db.commit()
    frappe.log_error(title=f"Excom email deauthorized: {account_name}", message=reason)


def _refresh_account_token(account) -> str:
    """
    Exchange the account's stored refresh token for a fresh access token and
    persist it. Raises (and deauthorizes) if the refresh token is revoked.
    """
    account_name = account.name
    if not account.email_connected_app:
        frappe.throw(_("Email account {0} is missing its Connected App").format(account_name))

    refresh_token = account.get_password("email_refresh_token", raise_exception=False)
    if not refresh_token:
        frappe.throw(
            _("No refresh token for {0}. Please click Authorize Gmail to re-authorize.").format(
                account_name
            )
        )

    connected_app = frappe.get_doc("Connected App", account.email_connected_app)
    resp = requests.post(
        connected_app.token_uri,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": connected_app.client_id,
            "client_secret": connected_app.get_password("client_secret"),
        },
        timeout=15,
    )

    if resp.status_code != 200:
        error = ""
        try:
            error = (resp.json() or {}).get("error", "")
        except ValueError:
            error = resp.text[:200]
        # invalid_grant => refresh token revoked/expired: the mailbox must re-consent.
        if error == "invalid_grant":
            _deauthorize(account_name, f"Refresh failed (invalid_grant): {resp.text[:300]}")
            frappe.throw(
                _("Gmail access for {0} was revoked. Please click Authorize Gmail to re-authorize.").format(
                    account_name
                )
            )
        resp.raise_for_status()

    data = resp.json()
    new_access_token = data.get("access_token")
    if not new_access_token:
        frappe.throw(_("Token refresh failed for {0}: no access_token in response").format(account_name))

    # Google may rotate the refresh token; persist it if so.
    _store_account_tokens(
        account_name,
        new_access_token,
        data.get("refresh_token"),
        data.get("expires_in", DEFAULT_TOKEN_LIFETIME_SECONDS),
        data.get("token_type", "Bearer"),
    )
    return new_access_token


def _force_token_refresh(account_name: str) -> None:
    """Backward-compatible wrapper used by the 401 retry path."""
    _refresh_account_token(frappe.get_doc(DOCTYPE, account_name))


def _profile_with_token(access_token: str) -> dict:
    """Fetch the Gmail profile using a raw bearer token (no account lookup).
    Used to verify which mailbox a freshly issued token actually belongs to."""
    resp = requests.get(
        f"{GMAIL_API_BASE}/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def capture_tokens_from_connected_app(account_name: str) -> dict:
    """
    Copy the tokens Frappe just wrote to the shared Connected App Token Cache
    (during the OAuth callback) into this account's own encrypted fields.

    Verifies the token's real mailbox via the Gmail profile and refuses to store
    it against a mismatched account -- so a wrong-mailbox token can never be
    persisted to the wrong account, even if two authorizations interleave.

    Returns {"authorized": bool, "email": str|None, "error": str|None}.
    """
    account = frappe.get_doc(DOCTYPE, account_name)
    if account.channel != "email":
        return {"authorized": False, "email": None, "error": "Not an email account"}

    if not account.email_connected_app or not account.email_connected_user:
        return {"authorized": False, "email": None, "error": "Missing Connected App or Connected User"}

    connected_app = frappe.get_doc("Connected App", account.email_connected_app)
    token_cache = connected_app.get_token_cache(account.email_connected_user)

    access_token = token_cache.get_password("access_token") if token_cache else None
    if not access_token:
        # Nothing fresh in the shared cache -- report current state only.
        already = bool(account.get_password("email_refresh_token", raise_exception=False))
        return {"authorized": already, "email": account.email_address, "error": None}

    refresh_token = token_cache.get_password("refresh_token") if token_cache else None

    # Verify which mailbox this token belongs to before persisting anything.
    try:
        profile = _profile_with_token(access_token)
    except Exception as e:
        return {"authorized": False, "email": None, "error": f"Could not verify Gmail profile: {e}"}

    mailbox = (profile or {}).get("emailAddress") or ""
    if account.email_address and mailbox and account.email_address.strip().lower() != mailbox.strip().lower():
        return {
            "authorized": False,
            "email": mailbox,
            "error": _("Authorized the wrong mailbox: expected {0}, got {1}. Please re-authorize with the correct Google account.").format(
                account.email_address, mailbox
            ),
        }

    _store_account_tokens(
        account_name,
        access_token,
        refresh_token,
        getattr(token_cache, "expires_in", None) or DEFAULT_TOKEN_LIFETIME_SECONDS,
        "Bearer",
    )

    if not account.email_address and mailbox:
        frappe.db.set_value(DOCTYPE, account_name, "email_address", mailbox, update_modified=False)
        frappe.db.commit()

    return {"authorized": True, "email": mailbox or account.email_address, "error": None}


def _headers(account_name: str) -> dict:
    """Build authorization headers for Gmail API requests."""
    token = get_access_token(account_name)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def get_profile(account_name: str) -> dict:
    """Fetch the authenticated user's Gmail profile to verify connection."""
    resp = requests.get(
        f"{GMAIL_API_BASE}/profile",
        headers=_headers(account_name),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def list_messages(
    account_name: str,
    query: str = "",
    label_ids: list = None,
    max_results: int = 50,
    page_token: str = "",
) -> dict:
    """
    List message IDs from Gmail matching a query or label filter.

    Returns:
        {"messages": [{"id": "...", "threadId": "..."}],
         "nextPageToken": "...", "resultSizeEstimate": N}
    """
    params = {"maxResults": max_results}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids
    if page_token:
        params["pageToken"] = page_token

    resp = requests.get(
        f"{GMAIL_API_BASE}/messages",
        headers=_headers(account_name),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_message_metadata(account_name: str, message_id: str) -> dict:
    """
    Fetch metadata-only for a single Gmail message.
    Returns headers (From, To, Cc, Subject, Date), snippet, labelIds, threadId.
    Does NOT return the body.
    """
    resp = requests.get(
        f"{GMAIL_API_BASE}/messages/{message_id}",
        headers=_headers(account_name),
        params={
            "format": "metadata",
            "metadataHeaders": [
                "From", "To", "Cc", "Bcc", "Subject",
                "Date", "Message-ID", "In-Reply-To",
                # Bulk / automated mail markers, for the Other lane (services/inbox_lane.py).
                "List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted",
            ],
        },
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()

    headers = {}
    for h in raw.get("payload", {}).get("headers", []):
        headers[h["name"]] = h["value"]

    return {
        "id": raw["id"],
        "threadId": raw.get("threadId", ""),
        "labelIds": raw.get("labelIds", []),
        "snippet": raw.get("snippet", "")[:150],
        "internalDate": raw.get("internalDate", ""),
        "headers": headers,
        "sizeEstimate": raw.get("sizeEstimate", 0),
    }


def get_message_full(account_name: str, message_id: str) -> dict:
    """
    Fetch the FULL message from Gmail API -- body + attachments metadata.
    Called ON-DEMAND when an agent opens a specific email. Result is returned
    to the frontend but NEVER persisted to the Frappe database.
    If the message has been deleted from Gmail, returns a deleted marker.
    On 401, attempts a token refresh and retries once.
    """
    def _fetch(hdrs: dict) -> requests.Response:
        return requests.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers=hdrs,
            params={"format": "full"},
            timeout=30,
        )

    try:
        resp = _fetch(_headers(account_name))

        # Expired token — try to force a refresh via Connected App
        if resp.status_code == 401:
            try:
                _force_token_refresh(account_name)
                resp = _fetch(_headers(account_name))
            except Exception:
                pass  # If refresh also fails, fall through to raise_for_status

        if resp.status_code == 404:
            return {
                "id": message_id, "deleted": True,
                "body_html": "", "body_text": "",
                "attachments": [], "headers": {},
            }

        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if "404" in str(e):
            return {
                "id": message_id, "deleted": True,
                "body_html": "", "body_text": "",
                "attachments": [], "headers": {},
            }
        raise

    raw = resp.json()

    headers = {}
    for h in raw.get("payload", {}).get("headers", []):
        headers[h["name"]] = h["value"]

    body_html, body_text = _extract_body(raw.get("payload", {}))
    attachments = _extract_attachments(raw.get("payload", {}))

    return {
        "id": raw["id"],
        "threadId": raw.get("threadId", ""),
        "headers": headers,
        "body_html": body_html,
        "body_text": body_text,
        "attachments": attachments,
        "deleted": False,
    }


def get_attachment(account_name: str, message_id: str, attachment_id: str) -> bytes:
    """Download an attachment from Gmail API. Returns raw bytes."""
    resp = requests.get(
        f"{GMAIL_API_BASE}/messages/{message_id}/attachments/{attachment_id}",
        headers=_headers(account_name),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json().get("data", "")
    return base64.urlsafe_b64decode(data)


def send_email(
    account_name: str,
    to: str,
    subject: str,
    body_html: str,
    cc: str = "",
    bcc: str = "",
    in_reply_to: str = "",
    references: str = "",
    thread_id: str = "",
) -> dict:
    """
    Send an email via Gmail API.

    Args:
        thread_id: Gmail thread ID — required for replies so Gmail
                   places the sent message in the same thread.

    Returns:
        {"id": "...", "threadId": "...", "labelIds": [...]}
    """
    account = frappe.get_doc("Excom Channel Account", account_name)
    from_email = account.email_address

    msg = MIMEMultipart("alternative")
    msg["From"] = from_email
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    text_part = MIMEText(
        frappe.utils.strip_html_tags(body_html), "plain", "utf-8"
    )
    html_part = MIMEText(body_html, "html", "utf-8")
    msg.attach(text_part)
    msg.attach(html_part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id

    resp = requests.post(
        f"{GMAIL_API_BASE}/messages/send",
        headers=_headers(account_name),
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_history(
    account_name: str,
    start_history_id: str,
    label_id: str = "INBOX",
) -> dict:
    """
    Fetch Gmail history since a given history ID.
    Used for efficient incremental sync.
    """
    params = {
        "startHistoryId": start_history_id,
        "historyTypes": "messageAdded",
    }
    if label_id:
        params["labelId"] = label_id

    resp = requests.get(
        f"{GMAIL_API_BASE}/history",
        headers=_headers(account_name),
        params=params,
        timeout=30,
    )

    if resp.status_code == 404:
        return {
            "history": [],
            "historyId": start_history_id,
            "expired": True,
        }

    resp.raise_for_status()
    return resp.json()


def search_messages(
    account_name: str, query: str, max_results: int = 20
) -> list:
    """
    Delegate search to Gmail API using Gmail's native query syntax.
    Returns a list of message metadata dicts.
    """
    result = list_messages(
        account_name, query=query, max_results=max_results
    )
    messages = result.get("messages", [])

    metadata_list = []
    for msg_ref in messages[:max_results]:
        try:
            meta = get_message_metadata(account_name, msg_ref["id"])
            metadata_list.append(meta)
        except Exception:
            continue

    return metadata_list


def _extract_body(payload: dict) -> tuple:
    """
    Recursively extract HTML and plain text body from a Gmail message payload.

    Returns:
        (body_html, body_text)
    """
    body_html = ""
    body_text = ""

    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/html" and body_data:
        body_html = base64.urlsafe_b64decode(body_data).decode(
            "utf-8", errors="replace"
        )
    elif mime_type == "text/plain" and body_data:
        body_text = base64.urlsafe_b64decode(body_data).decode(
            "utf-8", errors="replace"
        )

    for part in payload.get("parts", []):
        part_html, part_text = _extract_body(part)
        if part_html and not body_html:
            body_html = part_html
        if part_text and not body_text:
            body_text = part_text

    return body_html, body_text


def _extract_attachments(payload: dict) -> list:
    """
    Extract attachment metadata from a Gmail message payload.

    Returns:
        list of {filename, mimeType, size, attachmentId}
    """
    attachments = []

    body = payload.get("body", {})
    if body.get("attachmentId"):
        attachments.append({
            "filename": payload.get("filename", ""),
            "mimeType": payload.get("mimeType", ""),
            "size": body.get("size", 0),
            "attachmentId": body["attachmentId"],
        })

    for part in payload.get("parts", []):
        attachments.extend(_extract_attachments(part))

    return [a for a in attachments if a.get("filename")]
