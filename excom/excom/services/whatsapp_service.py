"""
WhatsApp Message Service — single entry point for all WhatsApp API operations.

All outbound and inbound WhatsApp flows should route through this service
so that rate limiting, retries, circuit-breaking, and audit logging can be
added as middleware without touching callers.
"""

import json
import re

import frappe
import requests as http_requests
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.password import get_decrypted_password

from excom.excom.utils.errors import ExcomProviderError, ExcomRateLimitError


def send_text_message(account, to: str, content: str) -> dict:
    """
    Send a plain text WhatsApp message.

    Args:
        account: Excom Channel Account doc
        to: Recipient phone number (E.164)
        content: Message body text

    Returns:
        dict with keys: provider_message_id, status
    """
    to = _clean_phone(to)
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": content},
    }
    return _call_api(account, payload)


def send_template_message(account, to: str, template_name: str, language_code: str,
                          components: list = None) -> dict:
    """
    Send a template WhatsApp message.

    Args:
        account: Account doc
        to: Recipient phone (E.164)
        template_name: The actual_name registered with Meta
        language_code: e.g. "en_US"
        components: Template component parameters list

    Returns:
        dict with keys: provider_message_id, status
    """
    to = _clean_phone(to)
    template_block: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_block["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template_block,
    }
    return _call_api(account, payload)


# ─── media upload (Meta error 131053: Meta cannot fetch private / non-public links) ─────────────

def _is_local_file(file_url: str) -> bool:
	return bool(file_url) and (file_url.startswith("/files/") or file_url.startswith("/private/files/"))


def _local_file_bytes(file_url: str) -> tuple[bytes, str, str]:
	"""(content, filename, mimetype) for a Frappe file URL, private or public."""
	import mimetypes
	import os
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if name:
		f = frappe.get_doc("File", name)
		content = f.get_content()
		filename = f.file_name or os.path.basename(file_url)
	else:
		path = frappe.get_site_path(file_url.lstrip("/"))
		with open(path, "rb") as fh:
			content = fh.read()
		filename = os.path.basename(file_url)
	mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
	return content, filename, mimetype


def upload_media(account, file_url: str) -> str:
	"""Upload a local file to /{phone_id}/media and return Meta's media id (valid ~30 days)."""
	creds = _resolve_credentials(account)
	if isinstance(creds, dict):
		token, base_url, version, phone_id = creds["token"], creds["base_url"], creds["version"], creds["phone_id"]
	else:
		token, base_url, version, phone_id = creds
	content, filename, mimetype = _local_file_bytes(file_url)
	url = f"{base_url}/{version}/{phone_id}/media"
	try:
		resp = http_requests.post(
			url,
			headers={"Authorization": f"Bearer {token}"},
			data={"messaging_product": "whatsapp", "type": mimetype},
			files={"file": (filename, content, mimetype)},
			timeout=120,
		)
	except http_requests.exceptions.RequestException as e:
		raise ExcomProviderError(f"WhatsApp media upload failed: {e}")
	data = resp.json() if resp.content else {}
	if resp.status_code != 200 or not data.get("id"):
		err = (data.get("error") or {}).get("message") or resp.text[:300]
		raise ExcomProviderError(f"WhatsApp media upload rejected ({resp.status_code}): {err}")
	return data["id"]


def media_reference(account, file_url: str) -> dict:
	"""{"id": …} for local files (uploaded first), {"link": …} for external URLs Meta can fetch itself."""
	if _is_local_file(file_url):
		return {"id": upload_media(account, file_url)}
	if file_url.startswith("http"):
		return {"link": file_url}
	from excom.excom.api.chat import _get_site_url
	return {"id": upload_media(account, file_url)} if file_url.startswith("/") else {"link": _get_site_url() + "/" + file_url}


def send_media_message(account, to: str, media_type: str, file_url: str,
                       caption: str = "") -> dict:
    """
    Send a media WhatsApp message (image, video, audio, document).

    Args:
        account: Account doc
        to: Recipient phone (E.164)
        media_type: One of "image", "video", "audio", "document"
        file_url: Public URL or Frappe file path
        caption: Optional caption (not supported for audio)

    Returns:
        dict with keys: provider_message_id, status
    """
    to = _clean_phone(to)
    media_key = media_type.lower()

    # Local files are uploaded to Meta first (a /private/files link or a site behind login gives Meta a 403 →
    # error 131053); only external http(s) URLs are sent as links.
    media_obj = media_reference(account, file_url)
    if caption and media_key != "audio":
        media_obj["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": media_key,
        media_key: media_obj,
    }
    return _call_api(account, payload)


def send_sticker_message(account, to: str, sticker_name: str) -> dict:
    """
    Send a sticker WhatsApp message.

    Prefers the pre-uploaded media_id; falls back to hosting the file URL
    as a link (not recommended by Meta but functional).

    Args:
        account: Excom Channel Account doc
        to: Recipient phone number (E.164)
        sticker_name: Excom Sticker doctype name

    Returns:
        dict with keys: provider_message_id, status
    """
    import frappe as _frappe

    sticker = _frappe.get_doc("Excom Sticker", sticker_name)
    to = _clean_phone(to)

    sticker_obj: dict = {}
    if sticker.media_id:
        sticker_obj["id"] = sticker.media_id
    elif sticker.sticker_file:
        sticker_obj.update(media_reference(account, sticker.sticker_file))  # uploaded, never a login-protected link
    else:
        raise ExcomProviderError(
            f"Sticker {sticker_name} has no media_id or file URL",
            provider="whatsapp",
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "sticker",
        "sticker": sticker_obj,
    }
    return _call_api(account, payload)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_credentials(account):
    """Extract token, base_url, version, phone_id from Excom Channel Account."""
    token = None
    try:
        token = account.get_password("wa_token")
    except Exception:
        pass
    if not token:
        token = get_decrypted_password(
            account.doctype, account.name, "wa_token", raise_exception=False
        )
    if not token:
        raise ExcomProviderError(
            f"No access token for account {account.name}",
            provider="whatsapp",
        )

    base_url = getattr(account, "wa_url", None) or "https://graph.facebook.com"
    version = getattr(account, "wa_version", None) or "v21.0"
    phone_id = getattr(account, "wa_phone_id", None)
    if not phone_id:
        raise ExcomProviderError(
            f"No Phone Number ID for account {account.name}",
            provider="whatsapp",
        )

    return token, base_url, version, phone_id


def _call_api(account, payload: dict) -> dict:
    """
    Make the actual HTTP POST to Meta's WhatsApp Cloud API.

    Returns:
        dict with provider_message_id and status
    """
    token, base_url, version, phone_id = _resolve_credentials(account)
    url = f"{base_url}/{version}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = http_requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=(10, 60),
        )
    except http_requests.exceptions.Timeout as e:
        raise ExcomProviderError(
            f"WhatsApp API request timed out after 60s: {e}",
            provider="whatsapp",
        ) from e
    except http_requests.exceptions.RequestException as e:
        raise ExcomProviderError(
            f"WhatsApp API connection error: {e}",
            provider="whatsapp",
        ) from e

    try:
        data = response.json()
    except ValueError:
        data = {
            "error": {
                "message": (response.text or "")[:500] or f"Non-JSON response (HTTP {response.status_code})",
                "code": response.status_code,
            }
        }

    if response.ok and data.get("messages"):
        return {
            "provider_message_id": data["messages"][0].get("id", ""),
            "status": "Sent",
        }

    error = data.get("error", {})
    error_msg = error.get("message", "Unknown error")
    error_code = error.get("code")

    if error_code == 130429 or response.status_code == 429:
        raise ExcomRateLimitError(
            f"Rate limit exceeded: {error_msg}",
            provider="whatsapp",
            retry_after=int(error.get("retry_after", 60)),
        )

    if "expired" in error_msg.lower() or error_code in (190, 102):
        raise ExcomProviderError(
            f"WhatsApp API auth error: {error_msg}. Update the access token for {account.name}.",
            provider="whatsapp",
        )

    raise ExcomProviderError(
        f"WhatsApp API error ({error_code}): {error_msg}",
        provider="whatsapp",
    )


def _clean_phone(number: str) -> str:
    """Strip non-digit characters for the API payload."""
    return re.sub(r"[^\d]", "", number)
