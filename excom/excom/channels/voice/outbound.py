"""Placing a call.

Two transports, one entry point. `dial()` decides which and returns either a dial string for the
browser SDK to call, or confirmation that the agent's own phone is about to ring. The caller — the
React softphone — does not branch on provider, only on the `mode` we hand back.

Every policy check that costs money happens here, server-side, before the provider is touched. A
check in the UI is not a control.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, today

from excom.excom.channels.voice import presence, providers
from excom.excom.channels.voice.providers.base import Destination
from excom.excom.utils.phone import normalize_phone, validate_phone_number

# Spend counters are per agent per day and reset by expiry, not by a job.
_MINUTES = "excom:voice:intl_minutes"
SECONDS_IN_DAY = 86400

# Country codes, longest first so +971 is read as the UAE and not as India with a stray 1.
#
# The order is load-bearing and the list is the gate's accuracy: a destination whose code is not
# here cannot be proved domestic, so `_is_international` treats it as international. That is the
# safe direction to be wrong in — it asks for permission rather than quietly billing for it — but
# every code missing here is a call an agent gets refused for no visible reason, so the list is
# worth keeping broad.
KNOWN_COUNTRY_CODES = (
	# 3-digit. Each must precede any 2-digit code it starts with.
	"971", "972", "973", "974", "975", "976", "977", "970",
	"960", "961", "962", "963", "964", "965", "966", "967", "968",
	"992", "993", "994", "995", "996", "998",
	"880", "886", "852", "853", "855", "856",
	"350", "351", "352", "353", "354", "355", "356", "357", "358", "359",
	"370", "371", "372", "373", "374", "375", "376", "377", "378", "380", "381",
	"382", "383", "385", "386", "387", "389",
	"420", "421", "423",
	"212", "213", "216", "218", "220", "221", "223", "225", "226", "228", "229",
	"230", "231", "232", "233", "234", "235", "237", "238", "240", "241", "243",
	"244", "248", "249", "250", "251", "252", "253", "254", "255", "256", "257",
	"258", "260", "261", "263", "264", "265", "266", "267", "268", "269",
	"501", "502", "503", "504", "505", "506", "507", "509",
	# 2-digit.
	"20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44",
	"45", "46", "47", "48", "49",
	"51", "52", "53", "54", "55", "56", "57", "58",
	"60", "61", "62", "63", "64", "65", "66",
	"81", "82", "84", "86",
	"90", "91", "92", "93", "94", "95", "98",
	# 1-digit. +1 is North America, +7 is Russia and Kazakhstan.
	"1", "7",
)

# The national significant number length for the codes we care about most. India is 10.
NSN_LENGTH = {"91": 10, "1": 10, "44": 10, "971": 9, "65": 8}


def line_country_code(account_doc) -> str:
	"""The country this line dials from, read off its own number."""
	own = normalize_phone(account_doc.get("voice_number") or "").lstrip("+")
	for code in KNOWN_COUNTRY_CODES:
		if own.startswith(code):
			return code
	return ""


def to_e164(raw: str, account_doc) -> str:
	"""Turn whatever is in the contact record into something a provider will dial.

	Real address books are not in E.164. An Indian mobile is written 9217025599, or 09217025599
	with the trunk prefix, or 0092170 25599 after a bad import - and a provider takes none of them.
	The line's own number says which country "no country code" means.
	"""
	if not raw or not str(raw).strip():
		frappe.throw(_("There is no number to call."))

	text = str(raw).strip()
	cc = line_country_code(account_doc)

	if text.startswith("+"):
		digits = re.sub(r"\D", "", text)
	else:
		digits = re.sub(r"\D", "", text)
		if digits.startswith("00"):
			# 00 is the international prefix in most of the world, including India.
			digits = digits[2:]
		elif cc:
			nsn = NSN_LENGTH.get(cc, 10)
			if digits.startswith("0") and len(digits) == nsn + 1:
				digits = cc + digits[1:]  # trunk prefix, e.g. 09217025599
			elif len(digits) == nsn:
				digits = cc + digits  # bare national number
			elif not digits.startswith(cc) and len(digits) < nsn:
				frappe.throw(
					_("{0} is too short to be a phone number.").format(raw),
					frappe.ValidationError,
				)

	if not digits:
		frappe.throw(_("{0} is not a phone number.").format(raw), frappe.ValidationError)

	return validate_phone_number(f"+{digits}", _("Number to call"))


def dial(
	to_number: str,
	account: str = "",
	thread: str = "",
	transport: str = "",
	user: str = "",
) -> dict:
	"""Start an outbound call. Returns what the browser should do next."""
	user = user or frappe.session.user

	# Two steps that look circular and are not. Normalising a bare number needs a line, because
	# "9250333699" only means India if the agent's own line is Indian; choosing a line needs the
	# number, because the destination country decides. So the agent's usual line resolves what they
	# typed, and the resulting country then picks the line that will actually carry the call.
	home = account or default_voice_account(user)
	if not home:
		frappe.throw(_("No voice line is configured. An administrator can set one up in Admin."))

	# Contact lists hold national numbers - 09217025599, 9217025599, 092170 25599 - and a provider
	# only accepts E.164. Validating before converting refused to dial most of the address book.
	number = to_e164(to_number, frappe.get_cached_doc("Excom Channel Account", home))

	account = account or line_for_destination(user, number) or home
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

	# Each of these names the line. With one line "this line" was unambiguous; with two, an agent
	# told their softphone is not connected has to know which desk to go and look at — and the line
	# is chosen by the destination, so it is rarely the one they were last looking at.
	line = account_doc.account_name or account_doc.name

	if not account_doc.get("voice_allow_browser_calls"):
		frappe.throw(_("Browser calling is switched off on {0}.").format(line))

	endpoint = frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": account_doc.name, "status": "Active"},
		"name",
	)
	if not endpoint:
		frappe.throw(
			_("You do not have a softphone on {0} yet. An administrator can set one up.").format(
				line
			)
		)
	# Not "are you on this line" but "is your softphone working at all".
	#
	# The line comes from the destination, and the browser can only be signed in to one line at a
	# time — so an agent dialling an Indian number while signed in to the American desk is the
	# normal case, not an error. Refusing here made it unreachable: the client switches line on the
	# plan we return, so throwing before we return it meant the switch could never happen and the
	# call was rejected for a condition the client was about to fix.
	#
	# The guard that actually matters is in the browser: `softphone.call` refuses if the switch did
	# not land, so a call can never go out over the wrong provider account.
	if not presence.registered_line(user, agent_lines(user)):
		frappe.throw(
			_("Your softphone is not connected. Reload Excom and allow the microphone.")
		)

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
		# Say which of the two ways in is missing. "Add a mobile number" sent an administrator
		# looking for a setting, when the real answer was that this account has no softphone on
		# this line and was never going to place a browser call either.
		has_endpoint = frappe.db.exists(
			"Excom Voice Endpoint",
			{"user": user, "channel_account": account_doc.name, "status": "Active"},
		)
		if has_endpoint:
			frappe.throw(
				_(
					"Your softphone is not connected, and there is no mobile number on your profile "
					"to fall back to. Reload Excom and allow the microphone, or add a mobile number."
				)
			)
		frappe.throw(
			_(
				"{0} cannot place calls on this line: no softphone and no mobile number. "
				"An administrator can create a softphone in Admin → Calls, or add a mobile number "
				"to this user."
			).format(user)
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


def country_code_of(number: str) -> str:
	"""The dialling country code at the front of an E.164 number, or "" if we do not know it."""
	digits = normalize_phone(number or "").lstrip("+")
	for code in KNOWN_COUNTRY_CODES:
		if digits.startswith(code):
			return code
	return ""


def _is_international(number: str, account_doc) -> bool:
	"""Whether this number is outside the country the line itself lives in.

	Both sides are resolved to a real country code before being compared. The obvious shortcut —
	checking whether the target starts with the first one, two or three digits of our own number —
	is wrong in a way that costs money: from an Indian line (+91…) a single-digit comparison makes
	every number beginning with 9 look domestic, so Pakistan (+92), Sri Lanka (+94) and the UAE
	(+971) all slipped past the international gate and the daily minutes cap along with it. Those
	are not exotic destinations for an Indian desk; they are the ones it dials most.
	"""
	home = line_country_code(account_doc)
	if not home:
		# A line whose own number we cannot place has no "home" to be international from. Gating
		# every call on it would take an unconfigured line off the air completely.
		return False

	target = country_code_of(number)
	if not target:
		# Unrecognised: we cannot prove it is domestic, and of the two ways to be wrong, asking for
		# permission is the recoverable one.
		return True
	return target != home


def _minutes_used(user: str) -> int:
	"""Raw `get`, to match the raw `setex`/`incrby` below.

	`get_value` would prefix the already-made key a second time and try to unpickle a plain integer,
	so it always reads zero — and a spend cap that always reads zero is not a cap at all. Same
	pairing the repo's own `utils/ratelimit.py` uses.
	"""
	value = frappe.cache.get(_key_minutes(user))
	if value is None:
		return 0
	try:
		return int(value.decode() if isinstance(value, bytes) else value)
	except (TypeError, ValueError, AttributeError):
		return 0


def record_international_minutes(user: str, seconds: int) -> None:
	"""Called from reconcile once a real duration is known."""
	if not user or seconds <= 0:
		return
	key = _key_minutes(user)
	if frappe.cache.get(key) is None:
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


def agent_lines(user: str = "") -> list[str]:
	"""Every voice line this agent is allowed to dial out on, the preferred one first.

	Which agents work a line is already a property of the line: `Excom Channel Account` carries a
	table of teams, and an empty table means everybody. That is the same rule the inbound ring set
	obeys, so obeying it here is what makes a line a permission rather than a suggestion — put the
	international desk on the American line and only they can spend on it, in either direction.

	Ordered, not merely collected. With one line the order never mattered; with two, "whichever row
	the database happened to return" decides which country a call is billed in.
	"""
	from excom.excom.channels.voice.routing import line_agents

	user = user or frappe.session.user
	mine = set(
		frappe.get_all(
			"Excom Voice Endpoint",
			filters={"user": user, "status": "Active"},
			pluck="channel_account",
		)
	)
	lines = frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "voice", "status": "Active"},
		fields=["name", "is_default_outgoing"],
		order_by="is_default_outgoing desc, creation asc",
	)

	allowed = []
	for row in lines:
		try:
			if any(agent.get("user") == user for agent in line_agents(row.name)):
				allowed.append(row.name)
		except Exception:
			# A line whose team list cannot be read must not take the agent's other lines down
			# with it — they may be mid-call on one of them.
			continue

	# A line the agent has a softphone on comes before one they do not, and the default-outgoing
	# line comes before the rest. Both orderings are stable, so the same call always picks the same
	# line.
	return [n for n in allowed if n in mine] + [n for n in allowed if n not in mine]


def default_voice_account(user: str = "") -> str | None:
	"""The line this agent dials out on when nothing says otherwise."""
	lines = agent_lines(user)
	return lines[0] if lines else None


def line_for_destination(user: str, number: str, candidates: list[str] | None = None) -> str | None:
	"""Which line should carry a call to this number.

	A provider account is tied to a country: ours can dial India and not America, the American one
	can dial America and not India, and each has its own caller id. So the destination decides the
	line, in this order:

	  1. a line that lives in the destination's own country — a local call, right caller id, and
	     it can never be refused as a barred destination;
	  2. failing that, a line permitted to dial abroad;
	  3. failing that, the agent's usual line, which will refuse with a reason rather than
	     silently picking one that cannot place the call.

	Without this, two lines means the database decides, and it decides differently each time: an
	Indian number goes out on the American line at international rates showing a +1 caller id, or
	an American number goes out on the Indian line and the carrier bars it.
	"""
	# `is None` rather than a falsy test: an empty list means "this agent has no line", and `or`
	# would quietly turn that into "go and find one", which is the opposite answer.
	if candidates is None:
		candidates = agent_lines(user)
	if not candidates:
		return None

	# An unrecognised country code matches no line, so it falls through to the international one —
	# the same call `_is_international` makes. A number we cannot place is far more likely to be
	# abroad than at home, and the domestic line would only have it barred by the carrier.
	target = country_code_of(number)

	abroad = None
	for name in candidates:
		doc = frappe.get_cached_doc("Excom Channel Account", name)
		if target and line_country_code(doc) == target:
			return name
		if abroad is None and doc.get("voice_allow_international"):
			abroad = name

	return abroad or candidates[0]


def preferred_transport(user: str, account: str, account_doc=None) -> str:
	"""Browser when the agent's softphone is actually connected, phone otherwise.

	This is the fallback in one line: if the tab is closed, the mic was denied or the network eats
	WebSockets, the agent has not registered, and the call goes to their handset instead of
	failing.
	"""
	account_doc = account_doc or frappe.get_cached_doc("Excom Channel Account", account)

	# A softphone signed in to a *different* line still counts. It is one client that can move, and
	# the destination is what chose this line — so asking "are you registered here" would send an
	# Indian call to the agent's handset merely because their browser happened to be sitting on the
	# American desk. What disqualifies the browser is a softphone that is not running anywhere.
	softphone_alive = bool(presence.registered_line(user, agent_lines(user)))
	has_endpoint = bool(
		frappe.db.exists(
			"Excom Voice Endpoint", {"user": user, "channel_account": account, "status": "Active"}
		)
	)
	if account_doc.get("voice_allow_browser_calls") and softphone_alive and has_endpoint:
		return "Browser"
	if account_doc.get("voice_allow_phone_calls"):
		return "Phone"
	return "Browser"
