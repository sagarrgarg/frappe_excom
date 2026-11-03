"""WhatsApp number and account settings (HLD-005 Phase 5), one function per Meta operation.

Each function takes an `Excom Channel Account` (channel whatsapp) and calls the Cloud API with the
account's token and Graph version. Meta's length limits are checked here first, so a bad value is
refused with a clear message instead of a Meta error code. A refusal from Meta raises
WhatsAppAPIError (services/whatsapp_errors.py) carrying the code.

	Business profile        GET/POST /{phone}/whatsapp_business_profile, photo via /{app}/uploads
	Block users             POST/DELETE/GET /{phone}/block_users (only people who wrote in the last 24 h)
	QR codes / short links  /{phone}/message_qrdls (≤ 2,000 per number, message ≤ 140)
	Conversational          /{phone}/conversational_automation (ice breakers, commands)
	Two-step PIN, register  /{phone}, /{phone}/register, deregister, request_code, verify_code
	Webhook overrides       /{phone} webhook_configuration, /{waba}/subscribed_apps
	Commerce                /{phone}/whatsapp_commerce_settings, /{waba}/product_catalogs

Never log the access token, the PIN or a verification code.
"""

import json
import re
import unicodedata

import frappe
import requests as http_requests
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime, time_diff_in_seconds

from excom.excom.services import whatsapp_service as ws
from excom.excom.services.whatsapp_errors import WhatsAppAPIError, classify, raise_for_error
from excom.excom.utils.errors import ExcomProviderError


class AccountSettingError(frappe.ValidationError):
	"""A value Meta would refuse (too long, too many, wrong format), caught before calling Meta."""


class MetaUnreachable(ExcomProviderError):
	"""No answer from Meta (timeout, connection error): the call may or may not have reached it."""


def _fail(message: str):
	raise AccountSettingError(message)


# ─── HTTP ────────────────────────────────────────────────────────────────────

def _creds(account):
	token, base_url, version, phone_id = ws._resolve_credentials(account)
	return token, (base_url or "https://graph.facebook.com").rstrip("/"), version, phone_id


_GRAPH_ID = re.compile(r"^[0-9]+$")


def _graph_id(value, label: str, account) -> str:
	"""A Meta object id that goes into a URL path: digits only, so it cannot point at another node."""
	value = str(value or "").strip()
	if not _GRAPH_ID.match(value):
		_fail(_("The {0} of {1} must be digits only (it is {2}).").format(label, account.name, value or "''"))
	return value


def _phone(account) -> str:
	return _graph_id(_creds(account)[3], _("Phone Number ID"), account)


def _waba(account) -> str:
	waba = (account.get("wa_business_id") or "").strip()
	if not waba:
		_fail(_("Set the WhatsApp Business Account ID on {0} first.").format(account.name))
	return _graph_id(waba, _("WhatsApp Business Account ID"), account)


def _graph(account, method: str, node: str, *, params=None, body=None, data=None, headers=None, keep: str = "", timeout=(10, 60)) -> dict:
	"""One Graph call → its JSON body. Meta's refusal raises WhatsAppAPIError.

	keep: a key that, when present, means the body is an answer even if it also carries `error`
	(block_users reports a partial failure that way).
	"""
	token, base_url, version, _phone_id = _creds(account)
	url = f"{base_url}/{version}/{node}"
	h = {"Authorization": f"Bearer {token}"}
	h.update(headers or {})
	kw = {"headers": h, "timeout": timeout}
	if params:
		kw["params"] = params
	if body is not None:
		kw["json"] = body
	if data is not None:
		kw["data"] = data
	try:
		resp = getattr(http_requests, method.lower())(url, **kw)
	except http_requests.exceptions.RequestException as e:
		# the URL never carries a secret here (PIN and codes go in the body)
		raise MetaUnreachable(_("WhatsApp API connection error: {0}").format(e), provider="whatsapp") from e
	try:
		out = resp.json() if resp.content else {}
	except ValueError:
		out = {"error": {"message": (resp.text or "")[:300] or f"HTTP {resp.status_code}", "code": resp.status_code}}
	if not isinstance(out, dict):
		out = {"data": out}
	if keep and keep in out:
		return out
	if resp.status_code >= 400 or out.get("error"):
		raise_for_error(out.get("error") or {}, resp.status_code, account_name=account.name)
	return out


def _as_bool(value):
	"""None for "not given"; otherwise a real bool from 1/0, true/false, "1"/"0"."""
	if value is None or value == "":
		return None
	if isinstance(value, bool):
		return value
	return str(value).strip().lower() in ("1", "true", "yes", "on")


# Misc symbols and dingbats, arrows and stars (⭐), the emoji planes, keycap, variation selector, tags
_EMOJI_RANGES = ((0x2600, 0x27BF), (0x2B00, 0x2BFF), (0x1F000, 0x1FAFF), (0x20E3, 0x20E3), (0xFE0F, 0xFE0F), (0xE0020, 0xE007F))


def _has_emoji(text: str) -> bool:
	"""Any emoji or pictograph: Unicode "other symbol" (So) or one of the emoji ranges."""
	for ch in text or "":
		cp = ord(ch)
		if unicodedata.category(ch) == "So" or any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES):
			return True
	return False


# ─── B.1 Business profile ────────────────────────────────────────────────────

PROFILE_FIELDS = ("about", "address", "description", "email", "websites", "vertical")
VERTICALS = (
	"", "OTHER", "AUTO", "BEAUTY", "APPAREL", "EDU", "ENTERTAIN", "EVENT_PLAN", "FINANCE", "GROCERY", "GOVT",
	"HOTEL", "HEALTH", "NONPROFIT", "PROF_SERVICES", "RETAIL", "TRAVEL", "RESTAURANT", "ALCOHOL",
	"ONLINE_GAMBLING", "PHYSICAL_GAMBLING", "OTC_DRUGS",
)
PROFILE_LIMITS = {"about": 139, "address": 256, "description": 512, "email": 128}
MAX_WEBSITES, WEBSITE_LIMIT = 2, 256
PHOTO_TYPES = ("image/jpeg", "image/png")
PHOTO_MAX_BYTES = 5 * 1024 * 1024  # UNVERIFIED: Meta's page gives no size rule; 5 MB as a guard
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_profile(values: dict) -> dict:
	"""The fields to send, checked against Meta's limits. Fields left out (None) are not changed."""
	out = {}
	for key in PROFILE_FIELDS:
		if values.get(key) is None:
			continue
		value = values[key]
		if key == "websites":
			if isinstance(value, str):
				value = [w for w in re.split(r"[\s,]+", value) if w]
			sites = [str(w).strip() for w in value if str(w).strip()]
			if len(sites) > MAX_WEBSITES:
				_fail(_("WhatsApp allows at most {0} websites.").format(MAX_WEBSITES))
			for w in sites:
				if not re.match(r"^https?://", w, re.I):
					_fail(_("Website {0} must start with http:// or https://.").format(w))
				if len(w) > WEBSITE_LIMIT:
					_fail(_("Each website may be at most {0} characters.").format(WEBSITE_LIMIT))
			out["websites"] = sites
			continue
		value = str(value).strip()
		if key == "vertical":
			value = value.upper()
			if value not in VERTICALS:
				_fail(_("{0} is not a WhatsApp business category.").format(value))
		elif key == "about":
			if not value:
				_fail(_("About must have at least 1 character."))
		if key in PROFILE_LIMITS and len(value) > PROFILE_LIMITS[key]:
			_fail(_("{0} may be at most {1} characters (it has {2}).").format(key.title(), PROFILE_LIMITS[key], len(value)))
		if key == "email" and value and not _EMAIL.match(value):
			_fail(_("{0} is not a valid email address.").format(value))
		out[key] = value
	return out


def get_business_profile(account) -> dict:
	data = _graph(account, "GET", f"{_phone(account)}/whatsapp_business_profile",
		params={"fields": "about,address,description,email,profile_picture_url,websites,vertical"})
	rows = data.get("data") or [{}]
	profile = dict(rows[0])
	profile.pop("messaging_product", None)
	return profile


def update_business_profile(account, values: dict) -> dict:
	fields = validate_profile(values)
	if not fields:
		_fail(_("Nothing to change."))
	return _graph(account, "POST", f"{_phone(account)}/whatsapp_business_profile", body={"messaging_product": "whatsapp", **fields})


def _assert_photo_readable(account, file_url: str) -> None:
	"""The photo must be a File the user may read, or one attached to this channel account."""
	files = frappe.get_all("File", filters={"file_url": file_url}, fields=["name", "attached_to_doctype", "attached_to_name"]) if file_url else []
	if not files:
		_fail(_("Upload the photo first."))
	for f in files:
		if f.attached_to_doctype == account.doctype and f.attached_to_name == account.name:
			return
		if frappe.has_permission("File", "read", doc=frappe.get_doc("File", f.name)):
			return
	_fail(_("You cannot use that file as the profile photo."))


def upload_profile_photo(account, file_url: str) -> str:
	"""Resumable Upload API: start a session on the app, send the bytes, return Meta's handle."""
	app_id = (account.get("wa_app_id") or "").strip()
	if not app_id:
		_fail(_("Set the App ID on {0}: Meta's upload API runs on the app.").format(account.name))
	app_id = _graph_id(app_id, _("App ID"), account)
	_assert_photo_readable(account, file_url)
	content, filename, mimetype = ws._local_file_bytes(file_url)
	if not isinstance(content, bytes):  # never send text-decoded image bytes
		raise ExcomProviderError(_("The profile photo could not be read as an image file"), provider="whatsapp")
	mimetype = "image/jpeg" if mimetype == "image/jpg" else mimetype
	if mimetype not in PHOTO_TYPES:
		_fail(_("The profile photo must be a JPEG or PNG image."))
	if len(content) > PHOTO_MAX_BYTES:
		_fail(_("The profile photo may be at most 5 MB."))
	session = _graph(account, "POST", f"{app_id}/uploads",
		params={"file_name": filename, "file_length": len(content), "file_type": mimetype}).get("id") or ""
	# Meta's session id (may hold base64 "/" and "?sig="): only refuse what would break the URL
	if not session.startswith("upload:") or re.search(r"[\s#]", session):
		raise ExcomProviderError(_("Meta did not start an upload session"), provider="whatsapp")
	token = _creds(account)[0]
	handle = _graph(account, "POST", session, data=content, timeout=(10, 120),
		headers={"Authorization": f"OAuth {token}", "file_offset": "0"}).get("h") or ""
	if not handle:
		raise ExcomProviderError(_("Meta did not return a handle for the uploaded photo"), provider="whatsapp")
	return handle


def set_profile_photo(account, file_url: str) -> dict:
	handle = upload_profile_photo(account, file_url)
	return _graph(account, "POST", f"{_phone(account)}/whatsapp_business_profile",
		body={"messaging_product": "whatsapp", "profile_picture_handle": handle})


# ─── B.2 Block users ─────────────────────────────────────────────────────────

BLOCK_WINDOW_SECONDS = 24 * 3600
BLOCK_BATCH = 1000


def block_entry(value: str) -> dict:
	"""{"user": "+<digits>"} for a number, {"user_id": BSUID} for a business-scoped user id."""
	value = (value or "").strip()
	if ws.is_bsuid(value):
		if value.split(".")[1:2] == ["ENT"]:
			_fail(_("Meta does not accept a parent user id ({0}) here.").format(value))
		return {"user_id": value}
	digits = ws._clean_phone(value)
	if not digits:
		_fail(_("{0} is not a WhatsApp number or user id.").format(value or "''"))
	return {"user": "+" + digits}


def identity_block_value(identity: str, account) -> str:
	"""What to send Meta for this person: their user id on this WhatsApp account, else their number."""
	row = frappe.db.get_value("Omni Identity", identity, ["wa_bsuid", "wa_bsuid_waba", "primary_whatsapp", "primary_phone"], as_dict=True)
	if not row:
		_fail(_("Unknown contact {0}.").format(identity))
	waba = account.get("wa_business_id") or ""
	# user ids are per business portfolio: only use one that belongs to this account
	if row.wa_bsuid and (not row.wa_bsuid_waba or not waba or row.wa_bsuid_waba == waba):
		return row.wa_bsuid
	phone = row.primary_whatsapp or row.primary_phone
	if not phone:
		_fail(_("{0} has no WhatsApp number or user id.").format(identity))
	return phone


def last_inbound_at(identities: list, account_name: str):
	"""When any of these people last wrote to this number (None if never)."""
	if not identities:
		return None
	rows = frappe.db.sql(
		"""SELECT MAX(last_inbound_at) FROM `tabExcom Thread`
		WHERE omni_identity IN %(ids)s AND account = %(account)s AND channel = 'whatsapp'""",
		{"ids": tuple(identities), "account": account_name},
	)
	return rows[0][0] if rows and rows[0][0] else None


def assert_can_block(identities: list, account, who: str = "") -> None:
	"""Meta only blocks someone who messaged this number in the last 24 hours."""
	last = last_inbound_at(identities, account.name)
	who = who or _("This person")
	if not last:
		_fail(_("{0} has never written to this WhatsApp number. Meta only lets you block someone who messaged you in the last 24 hours.").format(who))
	if time_diff_in_seconds(now_datetime(), last) >= BLOCK_WINDOW_SECONDS:
		_fail(_("{0} last wrote on {1}. Meta only lets you block someone who messaged you in the last 24 hours.").format(
			who, frappe.utils.format_datetime(last)))


def _block_call(account, method: str, values: list) -> dict:
	if not values:
		_fail(_("Nobody to block or unblock."))
	if len(values) > BLOCK_BATCH:
		_fail(_("At most {0} people per request.").format(BLOCK_BATCH))
	entries = [block_entry(v) for v in values]
	data = _graph(account, method, f"{_phone(account)}/block_users",
		body={"messaging_product": "whatsapp", "block_users": entries}, keep="block_users")
	lists = data.get("block_users") or {}
	done = lists.get("added_users") or lists.get("removed_users") or []
	failed = lists.get("failed_users") or []
	for f in failed:
		err = (f.get("errors") or [{}])[0]
		frappe.log_error(
			title=f"Excom: WhatsApp {'block' if method == 'POST' else 'unblock'} refused ({err.get('code')})",
			message=json.dumps({"account": account.name, "code": err.get("code"), "class": classify(err.get("code")),
				"message": err.get("message"), "details": (err.get("error_data") or {}).get("details")}),
		)
	return {"done": done, "failed": failed}


def block_users(account, values: list) -> dict:
	"""POST block_users. Returns {"done": added_users, "failed": failed_users}."""
	return _block_call(account, "POST", values)


def unblock_users(account, values: list) -> dict:
	"""DELETE block_users (same body as block). Returns {"done": removed_users, "failed": ...}."""
	return _block_call(account, "DELETE", values)


def _raise_failed(failed: list, account) -> None:
	err = (failed[0].get("errors") or [{}])[0]
	code = err.get("code")
	if code == 131047:
		_fail(_("Meta refused: this person has not messaged you in the last 24 hours."))
	raise WhatsAppAPIError(
		_("WhatsApp refused ({0}): {1}").format(code, err.get("message") or (err.get("error_data") or {}).get("details") or ""),
		code=code, details=(err.get("error_data") or {}).get("details") or "")


def blocked_numbers(identity: str) -> dict:
	"""{Excom Channel Account: blocked on} — a block is per WhatsApp number, not per person."""
	row = frappe.db.get_value("Omni Identity", identity, ["wa_blocked_numbers", "wa_blocked", "wa_blocked_on", "wa_blocked_account"], as_dict=True)
	if not row:
		return {}
	try:
		numbers = json.loads(row.wa_blocked_numbers or "{}")
	except (TypeError, ValueError):
		numbers = {}
	if not isinstance(numbers, dict):
		numbers = {}
	# recorded before the per-number map: the one number in wa_blocked_account
	if not numbers and row.wa_blocked and row.wa_blocked_account:
		numbers = {row.wa_blocked_account: str(row.wa_blocked_on or "")}
	return numbers


def _set_blocked(identities: list, account_name: str, blocked: bool) -> None:
	"""Record a block / unblock on this number only; a block on another number stays."""
	for identity in identities:
		numbers = blocked_numbers(identity)
		if blocked:
			numbers[account_name] = str(now_datetime())
		elif account_name in numbers:
			numbers.pop(account_name)
		else:
			continue
		# wa_blocked / _on / _account summarise the map: any block, and the latest one
		latest = max(numbers.items(), key=lambda kv: kv[1] or "") if numbers else None
		frappe.db.set_value("Omni Identity", identity, {
			"wa_blocked_numbers": json.dumps(numbers) if numbers else "",
			"wa_blocked": 1 if numbers else 0,
			"wa_blocked_on": (latest[1] or None) if latest else None,
			"wa_blocked_account": latest[0] if latest else None,
		}, update_modified=False)


def block_person(account, value: str = "", identity: str = "") -> dict:
	"""Block one person (an identity, or a number / user id), after the 24-hour check. Records it on the identity."""
	identities = [identity] if identity else ws.identities_for_recipient(value)
	if identity and not value:
		value = identity_block_value(identity, account)
	if not value:
		_fail(_("Nobody to block."))
	assert_can_block(identities, account, who=value)
	result = block_users(account, [value])
	if result["failed"] and not result["done"]:
		_raise_failed(result["failed"], account)
	_set_blocked(identities, account.name, True)
	return {"blocked": value, "identities": identities}


def unblock_person(account, value: str = "", identity: str = "") -> dict:
	identities = [identity] if identity else ws.identities_for_recipient(value)
	if identity and not value:
		value = identity_block_value(identity, account)
	if not value:
		_fail(_("Nobody to unblock."))
	result = unblock_users(account, [value])
	if result["failed"] and not result["done"]:
		_raise_failed(result["failed"], account)
	_set_blocked(identities, account.name, False)
	return {"unblocked": value, "identities": identities}


def list_blocked_users(account, limit: int = 50, after: str = "") -> dict:
	"""One page of Meta's block list, each row matched to a contact when we know them."""
	params = {"limit": max(1, min(int(limit or 50), 100))}
	if after:
		params["after"] = after
	data = _graph(account, "GET", f"{_phone(account)}/block_users", params=params)
	rows = []
	for r in data.get("data") or []:
		key = r.get("user_id") or r.get("wa_id") or ""
		found = ws.identities_for_recipient(key) if key else []
		if not found and r.get("wa_id"):
			found = ws.identities_for_recipient(r["wa_id"])
		rows.append({"wa_id": r.get("wa_id") or "", "user_id": r.get("user_id") or "", "identity": found[0] if found else "",
			"name": frappe.db.get_value("Omni Identity", found[0], "display_name") if found else ""})
	paging = data.get("paging") or {}
	return {"data": rows, "after": (paging.get("cursors") or {}).get("after") if paging.get("next") else ""}


# ─── B.3 QR codes and short links ────────────────────────────────────────────

QR_CAP = 2000
QR_MESSAGE_LIMIT = 140
QR_FORMATS = ("PNG", "SVG")
_QR_CODE = re.compile(r"^[A-Za-z0-9]{1,32}$")


def validate_prefilled_message(message: str) -> str:
	message = (message or "").strip()
	if not message:
		_fail(_("The prefilled message cannot be empty."))
	if len(message) > QR_MESSAGE_LIMIT:
		_fail(_("The prefilled message may be at most {0} characters (it has {1}).").format(QR_MESSAGE_LIMIT, len(message)))
	return message


def _qr_format(fmt: str) -> str:
	fmt = (fmt or "PNG").upper()
	if fmt not in QR_FORMATS:
		_fail(_("QR image format must be PNG or SVG."))
	return fmt


def _qr_code(code: str) -> str:
	code = (code or "").strip()
	if not _QR_CODE.match(code):
		_fail(_("{0} is not a WhatsApp QR code.").format(code or "''"))
	return code


def create_qr_code(account, prefilled_message: str, image_format: str = "PNG") -> dict:
	"""{code, prefilled_message, deep_link_url, qr_image_url}."""
	message, fmt = validate_prefilled_message(prefilled_message), _qr_format(image_format)
	return _graph(account, "POST", f"{_phone(account)}/message_qrdls", body={"prefilled_message": message, "generate_qr_image": fmt})


def update_qr_code(account, code: str, prefilled_message: str) -> dict:
	return _graph(account, "POST", f"{_phone(account)}/message_qrdls",
		body={"code": _qr_code(code), "prefilled_message": validate_prefilled_message(prefilled_message)})


def delete_qr_code(account, code: str) -> dict:
	return _graph(account, "DELETE", f"{_phone(account)}/message_qrdls/{_qr_code(code)}")


def get_qr_code(account, code: str, image_format: str = "PNG") -> dict:
	"""One code with a fresh image link ({} when Meta no longer has it)."""
	data = _graph(account, "GET", f"{_phone(account)}/message_qrdls/{_qr_code(code)}",
		params={"fields": f"code,prefilled_message,deep_link_url,qr_image_url.format({_qr_format(image_format)})"})
	rows = data.get("data") or []
	return rows[0] if rows else {}


def list_qr_codes(account) -> list:
	"""Every code on the number (Meta pages 25 at a time)."""
	out, after = [], ""
	for _page in range(QR_CAP // 25 + 2):
		params = {"fields": "code,prefilled_message,deep_link_url", "limit": 25}
		if after:
			params["after"] = after
		data = _graph(account, "GET", f"{_phone(account)}/message_qrdls", params=params)
		out += data.get("data") or []
		paging = data.get("paging") or {}
		after = (paging.get("cursors") or {}).get("after") or ""
		if not paging.get("next") or not after:
			break
	return out


def qr_count(account_name: str) -> int:
	return frappe.db.count("Excom WhatsApp QR Code", {"channel_account": account_name})


def assert_qr_room(account_name: str) -> None:
	if qr_count(account_name) >= QR_CAP:
		_fail(_("This number already has {0} QR codes, Meta's maximum. Delete one first.").format(QR_CAP))


def sync_qr_codes(account) -> dict:
	"""Mirror Meta's list into Excom WhatsApp QR Code: add new codes, update messages, drop deleted ones."""
	remote = {r["code"]: r for r in list_qr_codes(account) if r.get("code")}
	local = {r.code: r for r in frappe.get_all("Excom WhatsApp QR Code", filters={"channel_account": account.name},
		fields=["name", "code", "prefilled_message", "deep_link_url"]) if r.code}
	created = updated = removed = 0
	now = now_datetime()
	for code, r in remote.items():
		row = local.get(code)
		if not row:
			doc = frappe.get_doc({"doctype": "Excom WhatsApp QR Code", "channel_account": account.name, "code": code,
				"prefilled_message": r.get("prefilled_message") or "", "deep_link_url": r.get("deep_link_url") or "", "last_synced_on": now})
			doc.flags.from_meta = True
			doc.insert(ignore_permissions=True)
			created += 1
		elif row.prefilled_message != (r.get("prefilled_message") or "") or row.deep_link_url != (r.get("deep_link_url") or ""):
			frappe.db.set_value("Excom WhatsApp QR Code", row.name, {"prefilled_message": r.get("prefilled_message") or "",
				"deep_link_url": r.get("deep_link_url") or "", "last_synced_on": now})
			updated += 1
		else:
			frappe.db.set_value("Excom WhatsApp QR Code", row.name, "last_synced_on", now, update_modified=False)
	for code, row in local.items():
		if code not in remote:
			doc = frappe.get_doc("Excom WhatsApp QR Code", row.name)
			doc.flags.from_meta = True
			doc.delete(ignore_permissions=True)
			removed += 1
	return {"created": created, "updated": updated, "removed": removed, "total": len(remote)}


def delete_qr_code_on_rollback(account, code: str) -> None:
	"""Meta made the code before our record is saved: if the insert or the request fails, delete it on Meta too."""

	def undo():
		try:
			delete_qr_code(account, code)
		except Exception:
			frappe.log_error(title=f"Excom: orphan WhatsApp QR code {code}", message=frappe.get_traceback())

	frappe.db.after_rollback.add(undo)


def attach_qr_image(doc) -> str:
	"""Keep a copy of Meta's QR image on the record (Meta's image links are CDN links that can expire).
	The previous copy is deleted once the new image has downloaded."""
	if not doc.qr_image_url:
		return ""
	try:
		resp = http_requests.get(doc.qr_image_url, timeout=(10, 30))
		if resp.status_code != 200 or not resp.content:
			return ""
		for old in frappe.get_all("File", filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name,
				"attached_to_field": "qr_image"}, pluck="name"):
			frappe.delete_doc("File", old, ignore_permissions=True)
			doc.db_set("qr_image", "", update_modified=False)
		ext = "svg" if (doc.image_format or "").upper() == "SVG" or b"<svg" in resp.content[:200] else "png"
		f = frappe.get_doc({"doctype": "File", "file_name": f"whatsapp-qr-{doc.code}.{ext}", "content": resp.content, "is_private": 1,
			"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "attached_to_field": "qr_image"})
		f.insert(ignore_permissions=True)
		doc.db_set("qr_image", f.file_url, update_modified=False)
		return f.file_url
	except Exception:
		frappe.log_error(title=f"Excom: QR image download failed {doc.code}", message=frappe.get_traceback())
		return ""


# ─── B.4 Conversational components ───────────────────────────────────────────

MAX_ICE_BREAKERS, ICE_BREAKER_LIMIT = 4, 80
MAX_COMMANDS, COMMAND_NAME_LIMIT, COMMAND_DESCRIPTION_LIMIT = 30, 32, 256
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9_-]+$")  # UNVERIFIED: Meta names no character set; no spaces or "/"
_COMMAND_TEXT = re.compile(r"^/([A-Za-z0-9_-]{1,32})(?:\s+(.*))?$", re.S)


def validate_conversational(prompts, commands) -> tuple[list, list]:
	"""(prompts, commands) cleaned and checked: ≤ 4 ice breakers of ≤ 80 characters, ≤ 30 commands, no emoji."""
	prompts = [str(p).strip() for p in (prompts or []) if str(p or "").strip()]
	if len(prompts) > MAX_ICE_BREAKERS:
		_fail(_("WhatsApp allows at most {0} ice breakers.").format(MAX_ICE_BREAKERS))
	for p in prompts:
		if len(p) > ICE_BREAKER_LIMIT:
			_fail(_("Ice breaker \"{0}\" is longer than {1} characters.").format(p[:30], ICE_BREAKER_LIMIT))
		if _has_emoji(p):
			_fail(_("Ice breakers cannot contain emoji."))
	if len({p.lower() for p in prompts}) != len(prompts):
		_fail(_("Ice breakers must be different from each other."))
	out, seen = [], set()
	for c in commands or []:
		name = str(c.get("command_name") or "").strip().lstrip("/")
		description = str(c.get("command_description") or "").strip()
		if not name and not description:
			continue
		if not name:
			_fail(_("Every command needs a name."))
		if len(name) > COMMAND_NAME_LIMIT:
			_fail(_("Command /{0} is longer than {1} characters.").format(name, COMMAND_NAME_LIMIT))
		if not _COMMAND_NAME.match(name):
			_fail(_("Command /{0}: use letters, digits, - or _ only, without spaces.").format(name))
		if not description:
			_fail(_("Command /{0} needs a description.").format(name))
		if len(description) > COMMAND_DESCRIPTION_LIMIT:
			_fail(_("The description of /{0} is longer than {1} characters.").format(name, COMMAND_DESCRIPTION_LIMIT))
		if _has_emoji(name) or _has_emoji(description):
			_fail(_("Commands cannot contain emoji."))
		if name.lower() in seen:
			_fail(_("Command /{0} is listed twice.").format(name))
		seen.add(name.lower())
		out.append({"command_name": name, "command_description": description})
	if len(out) > MAX_COMMANDS:
		_fail(_("WhatsApp allows at most {0} commands.").format(MAX_COMMANDS))
	return prompts, out


def get_conversational_automation(account) -> dict:
	"""{"prompts": [...], "commands": [...], "enable_welcome_message"?: bool} as Meta has them now."""
	data = _graph(account, "GET", _phone(account), params={"fields": "conversational_automation"})
	conv = data.get("conversational_automation") or {}
	if remember_commands(account, conv.get("commands") or []):
		frappe.local.flags.commit = True  # a GET request: keep what Meta has now
	return {"prompts": conv.get("prompts") or [], "commands": conv.get("commands") or [],
		**({"enable_welcome_message": conv["enable_welcome_message"]} if "enable_welcome_message" in conv else {})}


def set_conversational_automation(account, prompts, commands) -> dict:
	"""Replace the ice breakers and commands. enable_welcome_message is sent only when the account sets
	it (On / Off); it is gone from Meta's current docs, so a refusal is retried without it."""
	prompts, commands = validate_conversational(prompts, commands)
	body = {"prompts": prompts, "commands": commands}
	welcome = (account.get("wa_welcome_message") or "").strip()
	if welcome in ("On", "Off"):
		body["enable_welcome_message"] = welcome == "On"
	node = f"{_phone(account)}/conversational_automation"
	warning = ""
	try:
		_graph(account, "POST", node, body=body)
	except WhatsAppAPIError as e:
		# only an invalid-parameter refusal can be the welcome setting; a token or rate error stands
		if "enable_welcome_message" not in body or str(e.code) != "100":
			raise
		_graph(account, "POST", node, body={"prompts": prompts, "commands": commands})
		warning = _("Saved, but Meta refused the welcome-message setting; it was left out.")
	remember_commands(account, commands)
	return {"success": True, "warning": warning}


def command_names(account) -> set:
	"""The number's commands as excom last saved or read them (lower case, without "/")."""
	try:
		names = json.loads(account.get("wa_command_names") or "[]")
	except (TypeError, ValueError):
		names = []
	return {str(n).lower() for n in names if n} if isinstance(names, list) else set()


def remember_commands(account, commands: list) -> bool:
	"""Keep the command names on the channel account, for inbound dispatch. True when they changed."""
	names = sorted({str(c.get("command_name") or "").strip().lstrip("/").lower() for c in commands or [] if isinstance(c, dict)} - {""})
	if set(names) == command_names(account):
		return False
	value = json.dumps(names) if names else ""
	frappe.db.set_value("Excom Channel Account", account.name, "wa_command_names", value, update_modified=False)
	account.wa_command_names = value
	return True


def parse_command(text: str) -> dict | None:
	"""{"command": name, "args": rest} for an inbound "/name rest" text, else None."""
	m = _COMMAND_TEXT.match((text or "").strip())
	if not m:
		return None
	return {"command": m.group(1).lower(), "args": (m.group(2) or "").strip()}


def inbound_command(text: str, account) -> tuple[str, dict | None]:
	"""(dispatch kind, payload) for an inbound text.

	"command" only when the name is one of this number's commands (command_names); a text that
	merely starts with "/name" stays "text", with the parse in the payload as a hint for handlers.
	Commands changed in WhatsApp Manager count once Admin → WhatsApp number has read them.
	"""
	parsed = parse_command(text)
	if not parsed:
		return "text", None
	return ("command" if parsed["command"] in command_names(account) else "text"), parsed


# ─── B.5 Two-step PIN, register, deregister, verification codes ──────────────

REGISTRATION_LIMIT = 10
REGISTRATION_WINDOW_HOURS = 72
REGISTRATION_LIMIT_CODE = 133016
LOCALIZATION_REGIONS = ("AU", "ID", "IN", "JP", "SG", "KR", "DE", "CH", "GB", "BR", "BH", "ZA", "AE", "CA")
CODE_METHODS = ("SMS", "VOICE")
_PIN = re.compile(r"^\d{6}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:_[A-Z]{2})?$")


def validate_pin(pin) -> str:
	pin = str(pin or "").strip()
	if not _PIN.match(pin):
		_fail(_("The PIN must be exactly 6 digits."))
	return pin


def _store_secret(account_name: str, fieldname: str, value: str) -> None:
	from frappe.utils.password import set_encrypted_password

	set_encrypted_password("Excom Channel Account", account_name, value, fieldname)
	frappe.db.set_value("Excom Channel Account", account_name, fieldname, "*" * len(value), update_modified=False)


def _stored_secret(account_name: str, fieldname: str) -> str:
	from frappe.utils.password import get_decrypted_password

	return get_decrypted_password("Excom Channel Account", account_name, fieldname, raise_exception=False) or ""


def set_two_step_pin(account, pin) -> dict:
	"""POST /{phone} {"pin": …}, then keep the PIN (encrypted) for the next register."""
	pin = validate_pin(pin)
	_graph(account, "POST", _phone(account), body={"pin": pin})
	_store_secret(account.name, "wa_two_step_pin", pin)
	return {"success": True}


def registration_attempts(account_name: str) -> list:
	"""Register / deregister calls in the last 72 hours, oldest first."""
	raw = frappe.db.get_value("Excom Channel Account", account_name, "wa_registration_log") or "[]"
	try:
		rows = json.loads(raw)
	except (TypeError, ValueError):
		rows = []
	since = add_to_date(now_datetime(), hours=-REGISTRATION_WINDOW_HOURS)
	return [r for r in rows if isinstance(r, dict) and r.get("at") and get_datetime(r["at"]) > since]


def registration_budget(account_name: str) -> dict:
	attempts = registration_attempts(account_name)
	left = max(0, REGISTRATION_LIMIT - len(attempts))
	free_at = str(add_to_date(get_datetime(attempts[0]["at"]), hours=REGISTRATION_WINDOW_HOURS)) if attempts and not left else ""
	return {"used": len(attempts), "left": left, "limit": REGISTRATION_LIMIT, "free_at": free_at, "attempts": attempts}


def _assert_registration_budget(account_name: str) -> None:
	budget = registration_budget(account_name)
	if not budget["left"]:
		_fail(_("This number already made {0} register / deregister requests in 72 hours. One more would make Meta block it for 72 hours (error 133016). Try again after {1}.").format(
			REGISTRATION_LIMIT, budget["free_at"]))


def _record_registration(account_name: str, action: str, ok: bool, code=None) -> None:
	rows = registration_attempts(account_name)
	rows.append({"at": str(now_datetime()), "action": action, "ok": bool(ok), **({"code": code} if code else {})})
	frappe.db.set_value("Excom Channel Account", account_name, "wa_registration_log", json.dumps(rows[-20:]), update_modified=False)
	# Meta counts the attempt whatever happens next, so the record must survive a rolled-back request
	frappe.db.commit()


def _registration_call(account, action: str, node: str, body) -> dict:
	_assert_registration_budget(account.name)
	try:
		out = _graph(account, "POST", node, body=body)
	except WhatsAppAPIError as e:
		_record_registration(account.name, action, False, e.code)
		if str(e.code) == str(REGISTRATION_LIMIT_CODE):
			raise WhatsAppAPIError(_("Meta blocked register / deregister on this number for 72 hours (error 133016)."),
				code=e.code, details=e.details, http_status=e.http_status) from e
		raise
	except MetaUnreachable:
		# no answer, but Meta may still have counted the request: it uses up one of the 10 too
		_record_registration(account.name, action, False, "no answer")
		raise
	_record_registration(account.name, action, True)
	return out


def register_number(account, pin: str = "", region: str | None = None) -> dict:
	"""POST /{phone}/register. Uses the stored PIN when none is given (Meta still wants one, even while
	it phases out the PIN check from 2026-09-23).

	region: None uses the stored region; "" registers without one. The region is stored only once
	Meta has accepted the registration.
	"""
	pin = pin or _stored_secret(account.name, "wa_two_step_pin")
	if not pin:
		_fail(_("Set the two-step verification PIN first: Meta needs it to register the number."))
	pin = validate_pin(pin)
	stored = (account.get("wa_data_localization_region") or "").strip().upper()
	region = stored if region is None else str(region).strip().upper()
	if region and region not in LOCALIZATION_REGIONS:
		_fail(_("{0} is not a WhatsApp data localisation region.").format(region))
	body = {"messaging_product": "whatsapp", "pin": pin}
	if region:
		body["data_localization_region"] = region
	out = _registration_call(account, "register", f"{_phone(account)}/register", body)
	if region != stored:
		frappe.db.set_value("Excom Channel Account", account.name, "wa_data_localization_region", region, update_modified=False)
	if not _stored_secret(account.name, "wa_two_step_pin"):
		_store_secret(account.name, "wa_two_step_pin", pin)
	return out


def deregister_number(account) -> dict:
	return _registration_call(account, "deregister", f"{_phone(account)}/deregister", None)


def request_verification_code(account, code_method: str = "SMS", language: str = "en_US") -> dict:
	code_method = (code_method or "SMS").upper()
	if code_method not in CODE_METHODS:
		_fail(_("Send the code by SMS or VOICE."))
	language = (language or "").strip()
	if not _LANGUAGE.match(language):
		_fail(_("{0} is not a language code such as en_US.").format(language or "''"))
	try:
		return _graph(account, "POST", f"{_phone(account)}/request_code", params={"code_method": code_method, "language": language})
	except WhatsAppAPIError as e:
		if str(e.code) == "136024":
			raise WhatsAppAPIError(_("This number is already verified; no code is needed."), code=e.code, details=e.details) from e
		raise


def verify_code(account, code) -> dict:
	code = str(code or "").strip()
	if not re.match(r"^\d{6}$", code):
		_fail(_("The verification code is 6 digits."))
	# in the form body, not the query string, so the code never sits in a URL
	return _graph(account, "POST", f"{_phone(account)}/verify_code", data={"code": code})


def get_number_status(account) -> dict:
	"""Meta's registration-related fields for the number (see local_registration for excom's own)."""
	data = _graph(account, "GET", _phone(account), params={"fields": "status,code_verification_status,platform_type,name_status,verified_name,display_phone_number"})
	data.pop("id", None)
	return data


def local_registration(account) -> dict:
	"""What excom keeps itself: the register / deregister budget, whether a PIN is stored, the region."""
	return {"budget": registration_budget(account.name), "has_pin": bool(_stored_secret(account.name, "wa_two_step_pin")),
		"data_localization_region": account.get("wa_data_localization_region") or "", "regions": list(LOCALIZATION_REGIONS)}


# ─── B.6 Webhook overrides ───────────────────────────────────────────────────

CALLBACK_LIMIT = 200


def default_webhook_url() -> str:
	return frappe.utils.get_url("/api/method/excom.excom.utils.webhook.webhook")


def webhook_verify_token(account) -> str:
	"""The account's verify token (utils.webhook.get accepts it); one is made the first time."""
	token = _stored_secret(account.name, "wa_webhook_verify_token")
	if not token:
		token = frappe.generate_hash(length=32)
		_store_secret(account.name, "wa_webhook_verify_token", token)
		# Meta checks the URL with this token from another request before answering us: commit now,
		# or that request cannot see the token and the override is refused
		frappe.db.commit()
	return token


def _callback(url: str, verify_token: str, account) -> tuple[str, str]:
	"""(url, verify_token) to send: this site's endpoint with our token by default; another endpoint needs its own token."""
	url = (url or "").strip() or default_webhook_url()
	if not url.lower().startswith("https://"):
		_fail(_("The webhook URL must start with https://."))
	if len(url) > CALLBACK_LIMIT:
		_fail(_("The webhook URL may be at most {0} characters.").format(CALLBACK_LIMIT))
	if url == default_webhook_url():
		return url, webhook_verify_token(account)
	verify_token = (verify_token or "").strip()
	if not verify_token:
		_fail(_("Give the verify token that {0} expects; Meta checks it before saving.").format(url))
	return url, verify_token


def get_webhook_configuration(account) -> dict:
	"""Which URL each level points at: the number's override, the WhatsApp account's, the app's."""
	cfg = _graph(account, "GET", _phone(account), params={"fields": "webhook_configuration"}).get("webhook_configuration") or {}
	apps, waba_error = [], ""
	if account.get("wa_business_id"):
		try:
			apps = _graph(account, "GET", f"{_waba(account)}/subscribed_apps").get("data") or []
		except ExcomProviderError as e:
			waba_error = str(e)
	return {
		"phone_number": cfg.get("phone_number") or "",
		"whatsapp_business_account": cfg.get("whatsapp_business_account") or "",
		"application": cfg.get("application") or "",
		"subscribed_apps": [{"id": (a.get("whatsapp_business_api_data") or {}).get("id") or "", "name": (a.get("whatsapp_business_api_data") or {}).get("name") or "",
			"override_callback_uri": a.get("override_callback_uri") or ""} for a in apps],
		"waba_error": waba_error,
		"default_url": default_webhook_url(),
	}


def set_number_webhook(account, url: str = "", verify_token: str = "") -> dict:
	url, token = _callback(url, verify_token, account)
	_graph(account, "POST", _phone(account), body={"webhook_configuration": {"override_callback_uri": url, "verify_token": token}})
	frappe.db.set_value("Excom Channel Account", account.name, "wa_number_webhook_url", url, update_modified=False)
	return {"success": True, "url": url}


def clear_number_webhook(account) -> dict:
	_graph(account, "POST", _phone(account), body={"webhook_configuration": {"override_callback_uri": ""}})
	frappe.db.set_value("Excom Channel Account", account.name, "wa_number_webhook_url", "", update_modified=False)
	return {"success": True}


def set_waba_webhook(account, url: str = "", verify_token: str = "") -> dict:
	waba = _waba(account)
	url, token = _callback(url, verify_token, account)
	_graph(account, "POST", f"{waba}/subscribed_apps", body={"override_callback_uri": url, "verify_token": token})
	frappe.db.set_value("Excom Channel Account", account.name, "wa_waba_webhook_url", url, update_modified=False)
	return {"success": True, "url": url}


def clear_waba_webhook(account) -> dict:
	"""POST subscribed_apps with no body (DELETE would unsubscribe the app altogether)."""
	waba = _waba(account)
	_graph(account, "POST", f"{waba}/subscribed_apps")
	frappe.db.set_value("Excom Channel Account", account.name, "wa_waba_webhook_url", "", update_modified=False)
	return {"success": True}


# ─── B.7 Commerce settings and catalogue linking ─────────────────────────────

def get_commerce_settings(account) -> dict:
	rows = _graph(account, "GET", f"{_phone(account)}/whatsapp_commerce_settings").get("data") or [{}]
	row = rows[0]
	return {"is_cart_enabled": bool(row.get("is_cart_enabled", True)), "is_catalog_visible": bool(row.get("is_catalog_visible", False))}


def set_commerce_settings(account, is_cart_enabled=None, is_catalog_visible=None) -> dict:
	"""Query parameters, not a JSON body (Meta's shape for this endpoint)."""
	params = {}
	for key, value in (("is_cart_enabled", is_cart_enabled), ("is_catalog_visible", is_catalog_visible)):
		value = _as_bool(value)
		if value is not None:
			params[key] = "true" if value else "false"
	if not params:
		_fail(_("Nothing to change."))
	return _graph(account, "POST", f"{_phone(account)}/whatsapp_commerce_settings", params=params)


def link_catalog(account, catalog_id: str) -> dict:
	"""Connect a Commerce Manager catalogue to the WhatsApp account (shared by all its numbers).
	Unlinking is not possible through the API: use WhatsApp Manager or Commerce Manager."""
	catalog_id = str(catalog_id or "").strip()
	if not _GRAPH_ID.match(catalog_id):
		_fail(_("A catalogue ID is a number, from Commerce Manager."))
	waba = _waba(account)
	out = _graph(account, "POST", f"{waba}/product_catalogs", data={"catalog_id": catalog_id})
	if not account.get("wa_catalog_id"):
		frappe.db.set_value("Excom Channel Account", account.name, "wa_catalog_id", catalog_id)
	return out


def linked_catalogs(account) -> list:
	waba = _waba(account)
	return [{"id": c.get("id") or "", "name": c.get("name") or ""} for c in
		_graph(account, "GET", f"{waba}/product_catalogs", params={"fields": "id,name"}).get("data") or []]
