"""Endpoints for WhatsApp Business Calling (HLD-005 Phase 6).

Agent endpoints check Excom access first, and thread access wherever a conversation is named. The
SDP an agent's browser posts is relayed to Meta as is (after a shape check); nothing here logs it.
Webhooks arrive through utils.webhook (`calls` field), which verifies Meta's signature.
"""

import json

import frappe
from frappe import _

from excom.excom.api.chat import _check_admin_access, _check_excom_access, _check_thread_access
from excom.excom.channels.voice import presence
from excom.excom.channels.voice import whatsapp_call_settings as settings
from excom.excom.channels.voice import whatsapp_calling as calling
from excom.excom.utils.ratelimit import user_rate_limit


# ── the agent's browser ───────────────────────────────────────────────────────


@frappe.whitelist()
def lines():
	"""WhatsApp numbers this agent takes calls on, with the ICE servers the browser needs."""
	_check_excom_access()
	return {"lines": calling.lines_for(frappe.session.user), "heartbeat_seconds": presence.HEARTBEAT_SECONDS}


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=60)
def answer(call: str, sdp: str):
	"""The browser's SDP answer to Meta's offer. First agent wins; the others are told."""
	_check_excom_access()
	return calling.answer(call, sdp, frappe.session.user)


@frappe.whitelist()
def ringing():
	"""WhatsApp calls ringing for this agent now, with the offer: for a browser that just reloaded."""
	_check_excom_access()
	return calling.ringing_for(frappe.session.user)


@frappe.whitelist(methods=["POST"])
def decline(call: str, timeout: int = 0):
	"""This agent passes (or let the pop run out). When the whole ring set has, the call is rejected."""
	_check_excom_access()
	return calling.decline(call, frappe.session.user, timeout=bool(frappe.utils.cint(timeout)))


@frappe.whitelist(methods=["POST"])
def hangup(call: str = "", provider_call_id: str = ""):
	"""End a WhatsApp call: `reject` while ringing, `terminate` once connected. Named by either id,
	since an outbound call has only Meta's id in the browser at first. Only the agent on the call
	or a manager may (see whatsapp_calling.may_hang_up). Also sent as a beacon when the page closes,
	with the CSRF token in the form."""
	_check_excom_access()
	name = call or (frappe.db.get_value("Excom Call", {"provider_call_id": provider_call_id}, "name") if provider_call_id else "")
	if not name or not frappe.db.exists("Excom Call", name):
		frappe.throw(_("That call does not exist."), frappe.DoesNotExistError)
	return calling.hangup(name, frappe.session.user)


# ── a conversation ────────────────────────────────────────────────────────────


@frappe.whitelist()
@user_rate_limit(limit=30, seconds=60)
def permission(thread: str, refresh: int = 0):
	"""May this business call this person now; can a permission request be sent; the daily cap.
	Meta is asked at most once a minute per number and contact, however often this is called."""
	_check_thread_access(thread)
	return calling.thread_permission(thread, refresh=bool(frappe.utils.cint(refresh)), throttle=True)


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=10, seconds=60)
def request_permission(thread: str, body: str = ""):
	_check_thread_access(thread)
	return calling.request_permission(thread, body or "")


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=10, seconds=60)
def start_call(thread: str, sdp: str):
	"""Call the person from the browser; `sdp` is the browser's complete offer."""
	_check_thread_access(thread)
	return calling.start_call(thread, sdp, frappe.session.user)


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=10, seconds=60)
def send_call_button(thread: str, body: str, display_text: str = "", ttl_minutes: int = 0, payload: str = ""):
	"""Send a "Call on WhatsApp" button: the person calls us, which is free for both sides."""
	from excom.excom.services import whatsapp_interactive as wi

	_check_thread_access(thread)
	interactive = wi.voice_call(body, display_text=display_text, ttl_minutes=frappe.utils.cint(ttl_minutes), payload=payload)
	return {"message": wi.send_to_thread(thread, interactive)}


# ── administration ────────────────────────────────────────────────────────────


@frappe.whitelist()
def numbers():
	"""WhatsApp numbers and their stored calling settings, for the admin screen."""
	_check_admin_access()
	out = []
	for name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp"}, pluck="name", order_by="creation asc"):
		account = frappe.get_doc("Excom Channel Account", name)
		out.append({"account": name, "account_name": account.account_name or name, "status": account.status,
			"business_number": account.get("wa_display_phone") or "", "messaging_limit": account.get("wa_messaging_limit") or "",
			"settings": settings.stored_values(account), "warnings": settings.warnings_for(account)})
	return out


@frappe.whitelist()
def get_settings(account: str):
	"""Read the calling settings back from Meta (and keep a copy)."""
	_check_admin_access()
	return settings.fetch(account)


@frappe.whitelist(methods=["POST"])
def save_settings(account: str, values: str | dict = ""):
	"""Write the full calling object. Holidays not sent are kept."""
	_check_admin_access()
	if isinstance(values, str):
		try:
			values = json.loads(values or "{}")
		except ValueError:
			frappe.throw(_("The settings could not be read."))
	if not isinstance(values, dict):
		frappe.throw(_("The settings could not be read."))
	return settings.save(account, values)

