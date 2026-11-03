"""Admin → WhatsApp number: profile, blocked users, QR codes, ice breakers and commands,
registration and PIN, webhook overrides, commerce settings (HLD-005 Phase 5).

Everything here is an administrator action, except blocking from a conversation, which follows
thread access like mark_spam. The Meta calls live in services/whatsapp_account.py.
"""

import frappe
from frappe import _

from excom.excom.api.chat import _check_admin_access, _check_thread_access
from excom.excom.services import whatsapp_account as wa
from excom.excom.utils.errors import ExcomProviderError


def _account(account: str):
	if not account or not frappe.db.exists("Excom Channel Account", account):
		frappe.throw(_("Unknown WhatsApp number {0}").format(account or ""))
	doc = frappe.get_doc("Excom Channel Account", account)
	if (doc.channel or "").lower() != "whatsapp":
		frappe.throw(_("{0} is not a WhatsApp number.").format(account))
	return doc


def _run(fn, *args, **kwargs):
	"""Call the service; Meta's refusal and our own limit checks come back as one readable message."""
	try:
		return fn(*args, **kwargs)
	except wa.AccountSettingError as e:
		frappe.throw(str(e), title=_("Not sent to WhatsApp"))
	except ExcomProviderError as e:
		# e.message is Meta's text and code: never the token, the PIN or a verification code
		frappe.log_error(title=f"Excom: WhatsApp number {fn.__name__} failed ({getattr(e, 'code', '') or ''})", message=e.message or str(e))
		frappe.throw(e.message or str(e), title=_("WhatsApp refused"))


# ─── the numbers ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_numbers() -> list:
	_check_admin_access()
	return frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp"},
		fields=["name", "account_name", "status", "wa_display_phone", "wa_verified_name", "wa_phone_id", "wa_business_id", "wa_app_id", "wa_catalog_id"],
		order_by="account_name asc")


@frappe.whitelist()
def get_overview(account: str) -> dict:
	"""What excom keeps locally for the number (no Meta call)."""
	_check_admin_access()
	doc = _account(account)
	return {
		"account": doc.name,
		"has_app_id": bool(doc.wa_app_id),
		"has_waba": bool(doc.wa_business_id),
		"welcome_message": doc.get("wa_welcome_message") or "",
		"data_localization_region": doc.get("wa_data_localization_region") or "",
		"regions": list(wa.LOCALIZATION_REGIONS),
		"verticals": [v for v in wa.VERTICALS if v],
		"catalog_id": doc.wa_catalog_id or "",
		"qr_count": wa.qr_count(doc.name),
		"qr_cap": wa.QR_CAP,
		"default_webhook_url": wa.default_webhook_url(),
	}


# ─── business profile ────────────────────────────────────────────────────────

@frappe.whitelist()
def get_profile(account: str) -> dict:
	_check_admin_access()
	return _run(wa.get_business_profile, _account(account))


@frappe.whitelist(methods=["POST"])
def update_profile(account: str, values) -> dict:
	"""values: the profile fields to change (leave a field out to keep it)."""
	_check_admin_access()
	values = frappe.parse_json(values) or {}
	return _run(wa.update_business_profile, _account(account), values)


@frappe.whitelist(methods=["POST"])
def set_profile_photo(account: str, file_url: str) -> dict:
	_check_admin_access()
	return _run(wa.set_profile_photo, _account(account), file_url)


# ─── blocked users ───────────────────────────────────────────────────────────

@frappe.whitelist()
def list_blocked(account: str, after: str = "") -> dict:
	_check_admin_access()
	return _run(wa.list_blocked_users, _account(account), 50, after)


@frappe.whitelist(methods=["POST"])
def block(account: str, user: str) -> dict:
	"""user: a WhatsApp number or business-scoped user id who wrote to this number in the last 24 h."""
	_check_admin_access()
	return _run(wa.block_person, _account(account), value=user)


@frappe.whitelist(methods=["POST"])
def unblock(account: str, user: str) -> dict:
	_check_admin_access()
	return _run(wa.unblock_person, _account(account), value=user)


def _thread_target(thread_id: str):
	thread = frappe.db.get_value("Excom Thread", thread_id, ["channel", "account", "omni_identity"], as_dict=True)
	if not thread or thread.channel != "whatsapp" or not thread.account or not thread.omni_identity:
		frappe.throw(_("Only a WhatsApp conversation can be blocked on WhatsApp."))
	return _account(thread.account), thread.omni_identity


@frappe.whitelist()
def thread_block_status(thread_id: str) -> dict:
	_check_thread_access(thread_id)
	thread = frappe.db.get_value("Excom Thread", thread_id, ["channel", "omni_identity"], as_dict=True) or {}
	if thread.get("channel") != "whatsapp" or not thread.get("omni_identity"):
		return {"whatsapp": False, "blocked": False}
	row = frappe.db.get_value("Omni Identity", thread.omni_identity, ["wa_blocked", "wa_blocked_on"], as_dict=True) or {}
	return {"whatsapp": True, "blocked": bool(row.get("wa_blocked")), "blocked_on": str(row.get("wa_blocked_on") or "")}


@frappe.whitelist(methods=["POST"])
def block_thread_contact(thread_id: str) -> dict:
	"""Block the person in this conversation on WhatsApp (they must have written in the last 24 h)."""
	_check_thread_access(thread_id)
	account, identity = _thread_target(thread_id)
	return _run(wa.block_person, account, identity=identity)


@frappe.whitelist(methods=["POST"])
def unblock_thread_contact(thread_id: str) -> dict:
	_check_thread_access(thread_id)
	account, identity = _thread_target(thread_id)
	return _run(wa.unblock_person, account, identity=identity)


# ─── QR codes and short links ────────────────────────────────────────────────

_QR_FIELDS = ["name", "code", "prefilled_message", "deep_link_url", "image_format", "qr_image", "qr_image_url", "last_synced_on", "modified"]


@frappe.whitelist()
def list_qr_codes(account: str) -> dict:
	_check_admin_access()
	doc = _account(account)
	rows = frappe.get_all("Excom WhatsApp QR Code", filters={"channel_account": doc.name}, fields=_QR_FIELDS, order_by="modified desc", limit_page_length=wa.QR_CAP)
	return {"data": rows, "count": len(rows), "cap": wa.QR_CAP}


@frappe.whitelist(methods=["POST"])
def create_qr_code(account: str, prefilled_message: str, image_format: str = "PNG") -> dict:
	_check_admin_access()
	doc = _account(account)
	qr = _run(lambda: frappe.get_doc({"doctype": "Excom WhatsApp QR Code", "channel_account": doc.name,
		"prefilled_message": prefilled_message, "image_format": image_format}).insert())
	return qr.as_dict()


def _qr(name: str):
	if not frappe.db.exists("Excom WhatsApp QR Code", name):
		frappe.throw(_("Unknown QR code {0}").format(name))
	return frappe.get_doc("Excom WhatsApp QR Code", name)


@frappe.whitelist(methods=["POST"])
def update_qr_code(name: str, prefilled_message: str) -> dict:
	_check_admin_access()
	qr = _qr(name)
	qr.prefilled_message = prefilled_message

	def save():
		qr.save()
		return qr

	return _run(save).as_dict()


@frappe.whitelist(methods=["POST"])
def delete_qr_code(name: str) -> dict:
	_check_admin_access()
	_qr(name)
	_run(frappe.delete_doc, "Excom WhatsApp QR Code", name)
	return {"success": True}


@frappe.whitelist(methods=["POST"])
def refresh_qr_image(name: str) -> dict:
	"""Fetch a fresh image link from Meta and keep a copy on the record."""
	_check_admin_access()
	qr = _qr(name)
	qr.check_permission("write")
	row = _run(wa.get_qr_code, _account(qr.channel_account), qr.code, qr.image_format or "PNG")
	if not row:
		frappe.throw(_("Meta no longer has this code. Sync from Meta to remove it."))
	qr.db_set("qr_image_url", row.get("qr_image_url") or "", update_modified=False)
	return {"qr_image": wa.attach_qr_image(qr), "qr_image_url": qr.qr_image_url}


@frappe.whitelist(methods=["POST"])
def sync_qr_codes(account: str) -> dict:
	_check_admin_access()
	return _run(wa.sync_qr_codes, _account(account))


# ─── ice breakers, commands, welcome message ─────────────────────────────────

@frappe.whitelist()
def get_conversational(account: str) -> dict:
	_check_admin_access()
	doc = _account(account)
	return {**_run(wa.get_conversational_automation, doc), "welcome_message": doc.get("wa_welcome_message") or ""}


@frappe.whitelist(methods=["POST"])
def set_conversational(account: str, prompts=None, commands=None, welcome_message: str = None) -> dict:
	"""Replace the ice breakers and commands. welcome_message: "", "On" or "Off" (sent only when On / Off)."""
	_check_admin_access()
	doc = _account(account)
	if welcome_message is not None:
		if welcome_message not in ("", "On", "Off"):
			frappe.throw(_("Welcome message must be On, Off or empty."))
		doc.db_set("wa_welcome_message", welcome_message)
	return _run(wa.set_conversational_automation, doc, frappe.parse_json(prompts) or [], frappe.parse_json(commands) or [])


# ─── two-step PIN and registration ───────────────────────────────────────────

@frappe.whitelist()
def get_registration(account: str) -> dict:
	_check_admin_access()
	doc = _account(account)
	return {**_run(wa.get_number_status, doc), "data_localization_region": doc.get("wa_data_localization_region") or "",
		"regions": list(wa.LOCALIZATION_REGIONS)}


@frappe.whitelist(methods=["POST"])
def set_pin(account: str, pin: str) -> dict:
	_check_admin_access()
	return _run(wa.set_two_step_pin, _account(account), pin)


@frappe.whitelist(methods=["POST"])
def register(account: str, pin: str = "", region: str = "") -> dict:
	_check_admin_access()
	doc = _account(account)
	if region and region.upper() in wa.LOCALIZATION_REGIONS:
		doc.db_set("wa_data_localization_region", region.upper())
	result = _run(wa.register_number, doc, pin, region)
	return {**(result or {}), "budget": wa.registration_budget(doc.name)}


@frappe.whitelist(methods=["POST"])
def deregister(account: str) -> dict:
	_check_admin_access()
	doc = _account(account)
	result = _run(wa.deregister_number, doc)
	return {**(result or {}), "budget": wa.registration_budget(doc.name)}


@frappe.whitelist(methods=["POST"])
def request_code(account: str, code_method: str = "SMS", language: str = "en_US") -> dict:
	_check_admin_access()
	return _run(wa.request_verification_code, _account(account), code_method, language)


@frappe.whitelist(methods=["POST"])
def verify_code(account: str, code: str) -> dict:
	_check_admin_access()
	return _run(wa.verify_code, _account(account), code)


# ─── webhook overrides ───────────────────────────────────────────────────────

@frappe.whitelist()
def get_webhook(account: str) -> dict:
	_check_admin_access()
	return _run(wa.get_webhook_configuration, _account(account))


@frappe.whitelist(methods=["POST"])
def set_webhook(account: str, level: str = "number", url: str = "", verify_token: str = "") -> dict:
	"""level: "number" (this phone number) or "waba" (every number of the WhatsApp account). url defaults to this site."""
	_check_admin_access()
	doc = _account(account)
	fn = wa.set_waba_webhook if level == "waba" else wa.set_number_webhook
	return _run(fn, doc, url, verify_token)


@frappe.whitelist(methods=["POST"])
def clear_webhook(account: str, level: str = "number") -> dict:
	_check_admin_access()
	doc = _account(account)
	return _run(wa.clear_waba_webhook if level == "waba" else wa.clear_number_webhook, doc)


# ─── commerce ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_commerce(account: str) -> dict:
	_check_admin_access()
	doc = _account(account)
	return {**_run(wa.get_commerce_settings, doc), "catalog_id": doc.wa_catalog_id or ""}


@frappe.whitelist(methods=["POST"])
def set_commerce(account: str, is_cart_enabled=None, is_catalog_visible=None) -> dict:
	_check_admin_access()
	return _run(wa.set_commerce_settings, _account(account), is_cart_enabled, is_catalog_visible)


@frappe.whitelist()
def list_catalogs(account: str) -> list:
	_check_admin_access()
	return _run(wa.linked_catalogs, _account(account))


@frappe.whitelist(methods=["POST"])
def link_catalog(account: str, catalog_id: str) -> dict:
	"""Meta has no API to unlink a catalogue: that is done in WhatsApp Manager or Commerce Manager."""
	_check_admin_access()
	return _run(wa.link_catalog, _account(account), catalog_id)
