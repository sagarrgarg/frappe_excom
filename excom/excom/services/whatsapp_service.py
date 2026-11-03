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


from excom.excom.services.whatsapp_errors import (
    CATEGORY_MISMATCH,
    RECIPIENT_TYPE,
    ExcomConsentError,
    WhatsAppAPIError,
    raise_for_error,
)
from excom.excom.utils.graph import graph_version

# Business-scoped user id: "IN.<up to 128 alphanumerics>" (a parent id adds ".ENT"). Phones are digits.
_BSUID_RE = re.compile(r"^[A-Z]{2}(?:\.[A-Za-z0-9]+){1,2}$")


def is_bsuid(value: str) -> bool:
    return bool(value) and bool(_BSUID_RE.match(value.strip()))


def _recipient(to: str) -> dict:
    """{"to": digits} for a phone number, {"recipient": id} for a business-scoped user id.

    Meta still accepts `to` for phone numbers and recommends it for marketing; `recipient` is
    the only way to reach someone who shared a username but no number.
    """
    to = (to or "").strip()
    if is_bsuid(to):
        return {"recipient": to}
    digits = _clean_phone(to)
    if not digits:
        raise ExcomProviderError("No WhatsApp number or user id to send to", provider="whatsapp")
    return {"to": digits}


def send_text_message(account, to: str, content: str) -> dict:
    """
    Send a plain text WhatsApp message.

    Args:
        account: Excom Channel Account doc
        to: Recipient phone number (E.164) or business-scoped user id
        content: Message body text

    Returns:
        dict with keys: provider_message_id, status
    """
    payload = {
        "messaging_product": "whatsapp",
        **_recipient(to),
        "type": "text",
        "text": {"body": content},
    }
    return _call_api(account, payload)


def template_category(template_name: str, language_code: str = "") -> str:
    """MARKETING / UTILITY / AUTHENTICATION for a template, as excom last heard it from Meta.

    Meta moves templates between categories (template_category_update), so the stored
    category — not the one a template was created with — decides the endpoint and the
    consent rules.
    """
    if not template_name:
        return ""
    filters = {"actual_name": template_name}
    if language_code:
        filters["language_code"] = language_code
    category = frappe.db.get_value("WhatsApp Templates", filters, "category")
    if category is None and language_code:
        category = frappe.db.get_value("WhatsApp Templates", {"actual_name": template_name}, "category")
    if category is None:
        category = frappe.db.get_value("WhatsApp Templates", template_name, "category")
    return (category or "").upper()


def send_template_message(account, to: str, template_name: str, language_code: str,
                          components: list = None, category: str = "", identity: str = "",
                          gate_category: str = "") -> dict:
    """
    Send a template WhatsApp message.

    Marketing templates go to /{phone_id}/marketing_messages when the account has the
    Marketing Messages API enabled (Meta retires marketing on /messages: new accounts from
    2027-04-13, everyone from 2027-10-13). Everything else goes to /messages.

    Every template is refused before it reaches Meta when the recipient opted out (see
    assert_template_allowed).

    Args:
        account: Account doc
        to: Recipient phone (E.164) or business-scoped user id
        template_name: The actual_name registered with Meta
        language_code: e.g. "en_US"
        components: Template component parameters list
        category: override the stored template category (normally looked up)
        identity: the Omni Identity being messaged, when the caller knows it (checked for opt-outs
            together with every identity that holds this number or user id)
        gate_category: category used only for the consent check — a broadcast passes MARKETING
            whatever the template says, since a list campaign is marketing by nature

    Returns:
        dict with keys: provider_message_id, status, message_status, endpoint, category
    """
    category = (category or template_category(template_name, language_code)).upper()
    # an unknown category is checked as marketing: when in doubt, honour the stricter opt-out
    assert_template_allowed(to, (gate_category or category or "MARKETING").upper(), identity=identity)

    template_block: dict = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_block["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        **_recipient(to),
        "type": "template",
        "template": template_block,
    }

    use_marketing_api = category == "MARKETING" and _marketing_api_enabled(account)
    try:
        if use_marketing_api:
            result = _call_api(account, _marketing_payload(account, payload), endpoint="marketing_messages")
        else:
            result = _call_api(account, payload)
    except WhatsAppAPIError as e:
        if e.error_class == CATEGORY_MISMATCH and not use_marketing_api and _marketing_api_enabled(account):
            # 131063: marketing templates are disabled on the Cloud API for this account
            # (disable_marketing_messages_on_cloud_api, or the 2027 sunset) — the template is
            # marketing, so it is re-checked as marketing and sent once on /marketing_messages.
            assert_template_allowed(to, "MARKETING", identity=identity)
            _remember_category(template_name, language_code, "MARKETING")
            result = _call_api(account, _marketing_payload(account, payload), endpoint="marketing_messages")
            category = "MARKETING"
        elif e.error_class == RECIPIENT_TYPE and is_bsuid(to) and _phone_of(identity):
            # 131062: this template type cannot go to a user id (e.g. one-tap / copy-code auth) — use the number
            payload.pop("recipient", None)
            payload["to"] = _clean_phone(_phone_of(identity))
            result = _call_api(account, _marketing_payload(account, payload) if use_marketing_api else payload,
                               endpoint="marketing_messages" if use_marketing_api else "messages")
        else:
            raise
    result["category"] = category
    return result


def _phone_of(identity: str) -> str:
    if not identity:
        return ""
    row = frappe.db.get_value("Omni Identity", identity, ["primary_whatsapp", "primary_phone"], as_dict=True) or {}
    return row.get("primary_whatsapp") or row.get("primary_phone") or ""


def _marketing_api_enabled(account) -> bool:
    return bool(getattr(account, "wa_marketing_api_enabled", 0))


def _marketing_payload(account, payload: dict) -> dict:
    """The /marketing_messages body: the template payload plus the account's delivery choices."""
    out = dict(payload)
    out["product_policy"] = getattr(account, "wa_marketing_product_policy", None) or "CLOUD_API_FALLBACK"
    # Meta's account-level default applies unless the account explicitly chose (a per-message
    # value overrides it, so it is only sent when set).
    sharing = (getattr(account, "wa_marketing_activity_sharing", None) or "").strip()
    if sharing in ("On", "Off"):
        out["message_activity_sharing"] = sharing == "On"
    return out


def _remember_category(template_name: str, language_code: str, category: str) -> None:
    name = frappe.db.get_value("WhatsApp Templates", {"actual_name": template_name, "language_code": language_code}, "name") or frappe.db.get_value("WhatsApp Templates", {"actual_name": template_name}, "name")
    if name:
        frappe.db.set_value("WhatsApp Templates", name, "category", category, update_modified=False)


def identities_for_recipient(to: str) -> list[str]:
    """Every non-merged Omni Identity that holds this number or user id — primary fields and aliases.

    Duplicates exist (the same person imported twice, a number moved by a relink), and an opt-out
    may sit on any of them, so the consent gate checks them all.
    """
    to = (to or "").strip()
    if not to:
        return []
    names: list[str] = []
    if is_bsuid(to):
        names += frappe.get_all("Omni Identity", filters={"wa_bsuid": to, "status": ["!=", "Merged"]}, pluck="name")
        names += [r[0] for r in frappe.db.sql(
            """SELECT oia.parent FROM `tabOmni Identity Alias` oia JOIN `tabOmni Identity` oi ON oi.name = oia.parent
            WHERE oia.alias_type = 'WhatsApp User ID' AND oia.alias_value_normalized = %(v)s AND oi.status != 'Merged'""",
            {"v": to},
        )]
        return list(dict.fromkeys(names))
    from excom.excom.utils.phone import phone_variants

    digits = _clean_phone(to)
    variants = tuple(phone_variants(digits)) if digits else ()
    if not variants:
        return []
    names += [r[0] for r in frappe.db.sql(
        """SELECT name FROM `tabOmni Identity` WHERE status != 'Merged'
        AND (normalized_phone IN %(v)s OR primary_whatsapp IN %(v)s)""",
        {"v": variants},
    )]
    names += [r[0] for r in frappe.db.sql(
        """SELECT oia.parent FROM `tabOmni Identity Alias` oia JOIN `tabOmni Identity` oi ON oi.name = oia.parent
        WHERE oia.alias_type IN ('Phone', 'WhatsApp') AND oia.alias_value_normalized IN %(v)s AND oi.status != 'Merged'""",
        {"v": variants},
    )]
    return list(dict.fromkeys(names))


def find_identity_for_recipient(to: str) -> str:
    """The first identity holding this number or user id ('' if none). Kept for callers."""
    found = identities_for_recipient(to)
    return found[0] if found else ""


def assert_template_allowed(to: str, category: str, identity: str = "") -> None:
    """Refuse a template to someone who opted out — the single gate every template send passes.

    Checked on the identity the caller is messaging *and* every identity holding the number or
    user id, so an opt-out on a duplicate record still counts.
    - do_not_contact or a WhatsApp STOP: no template of any category.
    - Meta "Stop promotions" (user_preferences webhook / error 131050 / the template opt-out
      button): no MARKETING template; utility and authentication still go.
    """
    names = list(dict.fromkeys(([identity] if identity else []) + identities_for_recipient(to)))
    if not names:
        return
    rows = frappe.get_all(
        "Omni Identity",
        filters={"name": ["in", names]},
        fields=["name", "do_not_contact", "whatsapp_opt_out", "wa_marketing_opt_out"],
    )
    for row in rows:
        if row.do_not_contact or row.whatsapp_opt_out:
            raise ExcomConsentError(_("{0} opted out of WhatsApp messages").format(row.name), identity=row.name)
    if (category or "").upper() == "MARKETING":
        for row in rows:
            if row.wa_marketing_opt_out:
                raise ExcomConsentError(_("{0} stopped marketing messages on WhatsApp").format(row.name), identity=row.name)


# ─── media upload (Meta error 131053: Meta cannot fetch private / non-public links) ─────────────

def _is_local_file(file_url: str) -> bool:
	return bool(file_url) and (file_url.startswith("/files/") or file_url.startswith("/private/files/"))


def _safe_site_file_path(file_url: str) -> str:
	"""The real path of /files/… or /private/files/… inside this site's folder; refuses `..` and
	anything that resolves (symlinks included) outside it."""
	import os

	url = (file_url or "").split("?", 1)[0]
	if ".." in url.replace("\\", "/").split("/") or not _is_local_file(url):
		frappe.throw(_("Not a file of this site: {0}").format(url[:120]), frappe.PermissionError)
	rel = url.lstrip("/")
	if rel.startswith("files/"):
		rel = "public/" + rel  # /files/x is served from <site>/public/files/x
	site = os.path.realpath(frappe.get_site_path())
	path = os.path.realpath(os.path.join(site, rel))
	if os.path.commonpath([site, path]) != site:
		frappe.throw(_("Not a file of this site: {0}").format(url[:120]), frappe.PermissionError)
	return path


def check_file_access(file_url: str, user: str | None = None) -> None:
	"""Refuse a private file the user may not read (an agent must not send someone else's ID scan).
	Public files and external links pass. Background sends check the owner when they are queued."""
	url = (file_url or "").strip()
	if not url.startswith("/private/"):
		if ".." in url.replace("\\", "/").split("/"):
			frappe.throw(_("Not a file of this site: {0}").format(url[:120]), frappe.PermissionError)
		return
	_safe_site_file_path(url)
	user = user or frappe.session.user
	name = frappe.db.get_value("File", {"file_url": url.split("?", 1)[0]}, "name")
	if not name:
		if user != "Administrator":
			frappe.throw(_("Attachment not found: {0}").format(url[:120]), frappe.PermissionError)
		return
	if not frappe.has_permission("File", "read", doc=frappe.get_doc("File", name), user=user):
		frappe.throw(_("You do not have access to the attachment {0}.").format(url.rsplit("/", 1)[-1][:120]), frappe.PermissionError)


def _local_file_bytes(file_url: str) -> tuple[bytes, str, str]:
	"""(content, filename, mimetype) for a Frappe file URL, private or public."""
	import mimetypes
	import os
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if name:
		f = frappe.get_doc("File", name)
		# no encodings: the raw bytes (get_content() would decode a PNG as windows-1250 text)
		content = f.get_content(encodings=())
		filename = f.file_name or os.path.basename(file_url)
	else:
		path = _safe_site_file_path(file_url)
		with open(path, "rb") as fh:
			content = fh.read()
		filename = os.path.basename(path)
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
    media_key = media_type.lower()

    # Local files are uploaded to Meta first (a /private/files link or a site behind login gives Meta a 403 →
    # error 131053); only external http(s) URLs are sent as links.
    media_obj = media_reference(account, file_url)
    if caption and media_key != "audio":
        media_obj["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        **_recipient(to),
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
        **_recipient(to),
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
    version = graph_version(getattr(account, "wa_version", None))
    phone_id = getattr(account, "wa_phone_id", None)
    if not phone_id:
        raise ExcomProviderError(
            f"No Phone Number ID for account {account.name}",
            provider="whatsapp",
        )

    return token, base_url, version, phone_id


def _call_api(account, payload: dict, endpoint: str = "messages") -> dict:
    """
    Make the actual HTTP POST to Meta's WhatsApp Cloud API.

    Args:
        endpoint: "messages" (Cloud API) or "marketing_messages" (Marketing Messages API)

    Returns:
        dict with provider_message_id, status, message_status (Meta's "accepted" /
        "held_for_quality_assessment" / "paused"), wa_user_id (the recipient's
        business-scoped id when Meta returns it) and endpoint.

    Raises:
        WhatsAppAPIError / WhatsAppRateLimitError carrying Meta's code and its class.
    """
    token, base_url, version, phone_id = _resolve_credentials(account)
    url = f"{base_url}/{version}/{phone_id}/{endpoint}"
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
        sent = data["messages"][0]
        message_id = sent.get("id", "")
        message_status = sent.get("message_status") or "accepted"
        if message_id and message_status != "accepted":
            _mark_message_status_after_commit(message_id, message_status)
        contact = (data.get("contacts") or [{}])[0]
        return {
            "provider_message_id": message_id,
            "status": "Sent",
            "message_status": message_status,
            "wa_user_id": contact.get("user_id") or "",
            "endpoint": endpoint,
        }

    raise_for_error(data.get("error") or {}, response.status_code, account_name=getattr(account, "name", ""))


def _mark_message_status_after_commit(message_id: str, message_status: str) -> None:
    """Record Meta's hold ("held_for_quality_assessment") on the Excom Message once the caller has saved it.

    Callers insert the Excom Message after this returns, so the write is queued after the commit
    instead of changing every caller.
    """
    frappe.enqueue(
        "excom.excom.services.whatsapp_service.set_message_status",
        queue="short",
        enqueue_after_commit=True,
        now=frappe.flags.in_test,
        message_id=message_id,
        message_status=message_status,
    )


def set_message_status(message_id: str, message_status: str) -> None:
    name = frappe.db.get_value("Excom Message", {"provider_message_id": message_id}, "name")
    if name:
        frappe.db.set_value("Excom Message", name, "wa_message_status", message_status, update_modified=False)


def _clean_phone(number: str) -> str:
    """Strip non-digit characters for the API payload."""
    return re.sub(r"[^\d]", "", number)


def fetch_phone_profile(account) -> dict:
	"""GET /{phone_id}?fields=display_phone_number,verified_name → stored on the account so the UI can show a real number."""
	creds = _resolve_credentials(account)
	token, base_url, version, phone_id = (creds["token"], creds["base_url"], creds["version"], creds["phone_id"]) if isinstance(creds, dict) else creds
	headers = {"Authorization": f"Bearer {token}"}
	resp = http_requests.get(f"{base_url}/{version}/{phone_id}", params={"fields": "display_phone_number,verified_name,quality_rating,throughput"}, headers=headers, timeout=20)
	data = resp.json() if resp.content else {}
	if resp.status_code != 200:
		raise ExcomProviderError(f"WhatsApp phone profile ({resp.status_code}): {(data.get('error') or {}).get('message') or resp.text[:200]}")
	updates = {
		"wa_display_phone": data.get("display_phone_number") or "",
		"wa_verified_name": data.get("verified_name") or "",
		"wa_quality_rating": data.get("quality_rating") or "",
		"wa_throughput": ((data.get("throughput") or {}).get("level") or "") if isinstance(data.get("throughput"), dict) else str(data.get("throughput") or ""),
		"wa_health_checked_on": now_datetime(),
	}
	# Optional extras: a missing permission or field must not fail the profile sync.
	limit = _get_json(f"{base_url}/{version}/{phone_id}", {"fields": "whatsapp_business_manager_messaging_limit"}, headers)
	if limit.get("whatsapp_business_manager_messaging_limit") is not None:
		updates["wa_messaging_limit"] = _limit_label(limit["whatsapp_business_manager_messaging_limit"])
	if getattr(account, "wa_business_id", None):
		waba = _get_json(f"{base_url}/{version}/{account.wa_business_id}", {"fields": "currency"}, headers)
		if waba.get("currency"):
			updates["wa_billing_currency"] = waba["currency"]
	frappe.db.set_value("Excom Channel Account", account.name, updates, update_modified=False)
	return data


def _get_json(url: str, params: dict, headers: dict) -> dict:
	try:
		r = http_requests.get(url, params=params, headers=headers, timeout=20)
		return r.json() if r.status_code == 200 and r.content else {}
	except Exception:
		return {}


def _limit_label(value) -> str:
	"""Meta returns the portfolio limit as a tier string (e.g. "TIER_2K") or an object; store it readable."""
	if isinstance(value, dict):
		return str(value.get("tier") or value.get("limit") or value.get("max_daily_conversations_per_business") or value)[:140]
	return str(value)[:140]


def sync_phone_profiles() -> None:
	"""Scheduler daily: keep display numbers fresh for every active WhatsApp account."""
	for name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "status": "Active"}, pluck="name"):
		try:
			fetch_phone_profile(frappe.get_doc("Excom Channel Account", name))
		except Exception:
			frappe.log_error(title=f"Excom: phone profile sync failed {name}", message=frappe.get_traceback())
	frappe.db.commit()
