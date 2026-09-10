"""Placing a call.

Two transports, one entry point. `dial()` decides which and returns either a dial string for the
browser SDK to call, or confirmation that the agent's own phone is about to ring. The caller — the
React softphone — does not branch on provider, only on the `mode` we hand back.

Every policy check that costs money happens here, server-side, before the provider is touched. A
check in the UI is not a control.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, today

from excom.excom.channels.voice import presence, providers
from excom.excom.channels.voice.providers.base import Destination
from excom.excom.utils.phone import normalize_phone, validate_phone_number

# Spend counters are per agent per day and reset by expiry, not by a job.
_MINUTES = "excom:voice:intl_minutes"
SECONDS_IN_DAY = 86400


def dial(
	to_number: str,
	account: str = "",
	thread: str = "",
	transport: str = "",
	user: str = "",
) -> dict:
	"""Start an outbound call. Returns what the browser should do next."""
	user = user or frappe.session.user
	number = validate_phone_number(to_number, _("Number to call"))
	account = account or default_voice_account()
	if not account:
		frappe.throw(_("No voice line is configured. An administrator can set one up in Admin."))

	account_doc = frappe.get_cached_doc("Excom Channel Account", account)
	if account_doc.status != "Active":
		frappe.throw(_("The voice line {0} is inactive.").format(account_doc.account_name))

	check_dialling_allowed(user, number, account_doc)

	provider = providers.for_account(account_doc)
	transport = transport or preferred_transport(user, account, account_doc)

	if transport == "Browser":
		return _dial_from_browser(user, number, account_doc, thread, provider)
	return _dial_via_phone(user, number, account_doc, thread, provider)


def _dial_from_browser(user, number, account_doc, thread, provider) -> dict:
	"""The browser places the leg itself, so there is no provider call to make here.

	The thread and identity ride out as SIP headers because that outgoing leg is created by the
	SDK — we have no call uuid to correlate on until Plivo hits the answer URL, and by then the
	number alone is not enough to pick the right conversation.
	"""
	from excom.excom.channels.voice.providers.plivo import SIP_HEADER_PREFIX

	if not account_doc.get("voice_allow_browser_calls"):
		frappe.throw(_("Browser calling is switched off for this line."))

	endpoint = frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": account_doc.name, "status": "Active"},
		"name",
	)
	if not endpoint:
		frappe.throw(
			_("You do not have a softphone on this line yet. An administrator can set one up.")
		)
	if not presence.is_registered(user, account_doc.name):
		frappe.throw(_("Your softphone is not connected. Reload Excom and allow the microphone."))

	# The browser SDK takes headers as an object, not the comma-joined string the REST API wants.
	headers = {
		f"{SIP_HEADER_PREFIX}{key}": value
		for key, value in (
			("to", number),
			("thread", thread or ""),
			("user", user),
			("account", account_doc.name),
		)
		if value
	}
	return {
		"mode": "browser",
		"dial_string": number.lstrip("+"),
		"extra_headers": headers,
		"account": account_doc.name,
		"thread": thread,
		"to_number": number,
	}


def _dial_via_phone(user, number, account_doc, thread, provider) -> dict:
	"""Ring the agent's own handset, then bridge. The fallback that always works."""
	if not account_doc.get("voice_allow_phone_calls"):
		frappe.throw(_("Calling from your phone is switched off for this line."))

	mobile = normalize_phone(frappe.db.get_value("User", user, "mobile_no") or "")
	if not mobile:
		frappe.throw(
			_("Add a mobile number to your user profile before calling from your phone.")
		)

	caller_id = normalize_phone(account_doc.get("voice_number") or "")
	ref = provider.initiate_call(
		Destination(kind="pstn", ref=number, user=""),
		caller_id=caller_id,
		opts={
			"from_number": mobile,
			"ring_seconds": cint(account_doc.get("voice_sticky_ring_seconds")) or 30,
			"max_conversation_seconds": cint(account_doc.get("voice_max_call_seconds")) or 3600,
			"sip_headers": {"to": number, "thread": thread or "", "user": user},
		},
	)

	frappe.enqueue(
		"excom.excom.channels.voice.handler.persist_call",
		queue="short",
		provider_call_id=ref.provider_call_id,
		account=account_doc.name,
		direction="Outbound",
		caller_number=number,
		business_number=caller_id,
		decision_users=[user],
		agent=user,
		transport="Phone",
		raw=ref.raw,
	)
	return {
		"mode": "phone",
		"provider_call_id": ref.provider_call_id,
		"account": account_doc.name,
		"thread": thread,
		"to_number": number,
		"message": _("Your phone will ring. Answer it to be connected."),
	}


def register_browser_call(
	provider_call_id: str, to_number: str, account: str, thread: str = "", user: str = ""
) -> str | None:
	"""Record a call the browser placed.

	The SDK gives us a call uuid the moment it dials, so the record can exist before the answer URL
	fires. Idempotent, so it does not matter whether this or the webhook wins the race.
	"""
	user = user or frappe.session.user
	caller_id = normalize_phone(
		frappe.db.get_value("Excom Channel Account", account, "voice_number") or ""
	)
	return frappe.enqueue(
		"excom.excom.channels.voice.handler.persist_call",
		queue="short",
		provider_call_id=provider_call_id,
		account=account,
		direction="Outbound",
		caller_number=normalize_phone(to_number),
		business_number=caller_id,
		decision_users=[user],
		agent=user,
		transport="Browser",
	)


def hangup(call: str) -> dict:
	"""End a live call from the server side. The browser hangs its own leg up locally."""
	doc = frappe.get_doc("Excom Call", call)
	if not doc.provider_call_id or not doc.channel_account:
		return {"status": "ignored"}
	provider = providers.for_account(doc.channel_account)
	provider.hangup(doc.provider_call_id)
	return {"status": "ok", "call": call}


# ── policy ────────────────────────────────────────────────────────────────────


def check_dialling_allowed(user: str, number: str, account_doc) -> None:
	"""Three server-side layers, evaluated in this order.

	The blocklist is checked before any allowance, so an explicitly blocked country cannot be
	re-enabled by a broader permission further down.
	"""
	settings = _settings()
	digits = number.lstrip("+")

	for code in _blocked_codes(settings):
		if digits.startswith(code):
			_log_denial(user, number, f"country +{code} is blocked")
			frappe.throw(_("Calls to +{0} numbers are blocked.").format(code))

	if _is_international(number, account_doc):
		if not account_doc.get("voice_allow_international") and not settings.get(
			"default_allow_international"
		):
			_log_denial(user, number, "international dialling not enabled")
			frappe.throw(_("International calling is not enabled on this line."))

		cap = cint(settings.get("daily_international_minutes_cap"))
		if cap and int(_minutes_used(user)) >= cap:
			_log_denial(user, number, f"daily international cap of {cap} minutes reached")
			frappe.throw(
				_("You have reached your daily international calling limit of {0} minutes.").format(
					cap
				)
			)


def _settings() -> dict:
	try:
		doc = frappe.get_cached_doc("Excom Settings")
		return {
			"default_allow_international": doc.get("default_allow_international"),
			"blocked_country_codes": doc.get("blocked_country_codes"),
			"daily_international_minutes_cap": doc.get("daily_international_minutes_cap"),
		}
	except Exception:
		return {}


def _blocked_codes(settings: dict) -> list[str]:
	raw = settings.get("blocked_country_codes") or ""
	return [
		code.strip().lstrip("+")
		for code in raw.replace("\n", ",").split(",")
		if code.strip().lstrip("+").isdigit()
	]


def _is_international(number: str, account_doc) -> bool:
	"""Compared against the line's own country code, not a hardcoded +91."""
	home = normalize_phone(account_doc.get("voice_number") or "").lstrip("+")
	target = number.lstrip("+")
	if not home:
		return False
	# Compare on the country code length that actually applies to the line's own number.
	for length in (3, 2, 1):
		if len(home) > length and target.startswith(home[:length]):
			return False
	return True


def _minutes_used(user: str) -> int:
	value = frappe.cache.get_value(_key_minutes(user))
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def record_international_minutes(user: str, seconds: int) -> None:
	"""Called from reconcile once a real duration is known."""
	if not user or seconds <= 0:
		return
	key = _key_minutes(user)
	if frappe.cache.get_value(key) is None:
		frappe.cache.setex(key, SECONDS_IN_DAY, 0)
	frappe.cache.incrby(key, max(1, seconds // 60))


def _key_minutes(user: str) -> str:
	return frappe.cache.make_key(f"{_MINUTES}:{today()}:{user}")


def _log_denial(user: str, number: str, reason: str) -> None:
	"""Every refused attempt is logged with who, what and why — this is the trail that shows a
	compromised account trying to run up a bill."""
	frappe.log_error(
		message=json.dumps({"user": user, "number": number, "reason": reason}),
		title="Excom Voice: dialling denied",
	)


# ── helpers ───────────────────────────────────────────────────────────────────


def default_voice_account(user: str = "") -> str | None:
	"""The line this agent should dial out on: the one they have a softphone on, else the default
	outgoing line, else the only active one."""
	user = user or frappe.session.user
	mine = frappe.get_all(
		"Excom Voice Endpoint",
		filters={"user": user, "status": "Active"},
		pluck="channel_account",
		limit=1,
	)
	if mine:
		return mine[0]

	preferred = frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "voice", "status": "Active", "is_default_outgoing": 1},
		pluck="name",
		limit=1,
	)
	if preferred:
		return preferred[0]

	any_line = frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "voice", "status": "Active"},
		pluck="name",
		limit=1,
	)
	return any_line[0] if any_line else None


def preferred_transport(user: str, account: str, account_doc=None) -> str:
	"""Browser when the agent's softphone is actually connected, phone otherwise.

	This is the fallback in one line: if the tab is closed, the mic was denied or the network eats
	WebSockets, the agent has not registered, and the call goes to their handset instead of
	failing.
	"""
	account_doc = account_doc or frappe.get_cached_doc("Excom Channel Account", account)
	if account_doc.get("voice_allow_browser_calls") and presence.is_registered(user, account):
		return "Browser"
	if account_doc.get("voice_allow_phone_calls"):
		return "Phone"
	return "Browser"
