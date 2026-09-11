"""Whitelisted endpoints for the voice channel.

Two families, and they are guarded completely differently:

  * **Provider webhooks** (`allow_guest=True`) prove who they are with an HMAC signature. There is
    no role to check — Plivo is not a user. `_verified_account()` is the gate and every one of them
    goes through it.
  * **Agent endpoints** start with `_check_excom_access()` on the first line, without exception. The
    reference implementation left `initiate_call`, `get_recording`, `get_active_call` and
    `get_call_history` open to any authenticated Frappe user, which is a company phone line and a
    complete call history handed to anybody with a login.

The route endpoint is the only synchronous one. It writes nothing and enqueues everything.
"""

import json

import frappe
from frappe import _
from werkzeug.wrappers import Response

from excom.excom.api.chat import (
	_check_admin_access,
	_check_excom_access,
	_check_thread_access,
)
from excom.excom.channels.voice import handler, outbound, presence, providers, recording, routing
from excom.excom.channels.voice.provisioning import ensure_endpoint, mint_token, note_registration, sync_line
from excom.excom.services.access import deny
from excom.excom.utils.ratelimit import user_rate_limit

XML_TYPE = "text/xml; charset=utf-8"


# ── provider webhooks ─────────────────────────────────────────────────────────


def _verified_account() -> str:
	"""Authenticate a provider webhook and return the account it belongs to.

	Authenticity comes from the signature, not from the `account` query parameter — that only says
	which line to check the signature against.
	"""
	params = dict(frappe.local.form_dict or {})
	account = params.pop("account", "") or ""
	params.pop("cmd", None)

	if not account or not frappe.db.exists(
		"Excom Channel Account", {"name": account, "channel": "voice"}
	):
		# Never say which part was wrong. A probe should learn nothing.
		raise frappe.PermissionError("Unrecognised voice webhook")

	account_doc = frappe.get_cached_doc("Excom Channel Account", account)
	provider = providers.for_account(account_doc)

	request = frappe.local.request
	method = request.method

	# Signed against the raw body, not frappe.local.form_dict.
	#
	# Frappe rewrites some values on the way in — `SIP-H-To` arrives as
	# `<sip:919250333699@phone.plivo.com>` and comes out altered, because it looks like markup. The
	# signature covers what Plivo sent, so anything that has passed through Frappe's parsing is the
	# wrong thing to check. `request.form` is werkzeug's own parse of the body and is untouched.
	form = {k: request.form[k] for k in request.form} if method == "POST" else {}
	# Not dict(...): werkzeug's header object is case-insensitive and a plain dict is not. HTTP/2
	# sends header names lowercase, so flattening it here made every signed request look unsigned.
	headers = request.headers

	# The signature covers the URL the provider sent. Behind a reverse proxy the request we see is
	# the internal one, so the forwarded headers are checked first — but a site with no proxy, or
	# one that rewrites the path, still has to work, so `request.url` remains a candidate.
	from excom.excom.channels.voice.providers.plivo import _header, received_url

	candidates = []
	for url in (received_url(request), request.url):
		if url and url not in candidates:
			candidates.append(url)

	if not any(provider.verify_webhook(url, method, headers, form) for url in candidates):
		# Title first, and short: Error Log caps it at 140 characters and *throws* past that, so a
		# long title turns a clean refusal into a 417 with a stack trace — which is what the caller
		# then has to debug instead of the actual signature problem.
		frappe.log_error(
			title="Excom Voice: webhook signature rejected",
			message=(
				f"account: {account}\n"
				f"from ip: {frappe.local.request_ip or 'unknown'}\n"
				f"method : {method}\n"
				# Whether the headers were even found is the first thing to check: a missing
				# signature and a wrong one look identical from the outside.
				f"signature header present: {bool(_header(headers, 'X-Plivo-Signature-V3'))}\n"
				f"nonce header present    : {bool(_header(headers, 'X-Plivo-Signature-V3-Nonce'))}\n"
				f"urls tried:\n  " + "\n  ".join(candidates) + "\n"
				f"signed param names: {sorted(form)}"
			),
		)
		raise frappe.PermissionError("Unrecognised voice webhook")

	return account


def _payload() -> dict:
	params = dict(frappe.local.form_dict or {})
	params.pop("cmd", None)
	params.pop("account", None)
	return params


def _xml(body: str) -> Response:
	return Response(body, content_type=XML_TYPE, status=200)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def route():
	"""The answer URL. Synchronous, on the critical path, and it writes nothing.

	The caller is listening to silence for as long as this takes. Everything here is cached reads;
	the screen pop is published before the response goes back, and the record is enqueued after.
	"""
	account = _verified_account()
	params = _payload()

	provider = providers.for_account(account)
	call_uuid = params.get("CallUUID") or params.get("RequestUUID") or ""
	direction = (params.get("Direction") or "inbound").lower()

	try:
		if direction == "outbound" or str(params.get("From") or "").startswith("sip:"):
			return _xml(_route_outbound(provider, account, params, call_uuid))
		return _xml(_route_inbound(provider, account, params, call_uuid))
	except Exception:
		# A live call is on the line. Fall back to ringing the whole team rather than dropping it,
		# and let the log carry the reason.
		frappe.log_error(frappe.get_traceback(), "Excom Voice: routing failed")
		return _xml(provider.render_decision(routing.fallback_decision(account)))


def _route_inbound(provider, account: str, params: dict, call_uuid: str) -> str:
	caller = params.get("From") or ""
	business = params.get("To") or ""
	digits = params.get("Digits") or ""

	decision, context = routing.build_decision(account, caller)
	context["display_name"] = _known_name(context.get("identity"), caller)

	# The pop fires before anything is written, so it is instant even when the queue is backed up.
	handler.announce_ringing(decision, context, call_uuid)

	frappe.enqueue(
		"excom.excom.channels.voice.handler.persist_call",
		queue="short",
		provider_call_id=call_uuid,
		account=account,
		direction="Inbound",
		caller_number=caller,
		business_number=business,
		decision_users=decision.ring_set,
		sticky_agent=context.get("sticky_agent"),
		identity=context.get("identity"),
		ivr_selection=digits,
		raw=params,
	)
	return provider.render_decision(decision)


def _route_outbound(provider, account: str, params: dict, call_uuid: str) -> str:
	"""A leg the browser created. The customer number rides in on a SIP header, because this leg
	has no other way of telling us who it is for."""
	from excom.excom.channels.voice.providers.base import CallDecision, Destination

	event = provider.normalize_event("ringing", params)
	target = event.sip_headers.get("to") or params.get("To") or ""
	account_doc = frappe.get_cached_doc("Excom Channel Account", account)
	policy = account_doc.get("voice_record_policy") or "All"

	# The dialling policy is checked in `dial()` before the browser is told what to call — but this
	# is the leg that actually bills, and the browser chooses what it sends. Anyone who can open a
	# console can call the SDK directly with a number `dial()` never approved, and the only thing
	# standing between that and an international bill is this check. So it runs again here, on the
	# server, against the number Plivo is really about to ring.
	refusal = _refuse_outbound(event.sip_headers.get("user", ""), target, account_doc)
	if refusal:
		return provider.render_decision(refusal)

	decision = CallDecision(
		destinations=[Destination(kind="pstn", ref=target, user=event.sip_headers.get("user", ""))],
		ring_set=[event.sip_headers.get("user", "")],
		ring_seconds=frappe.utils.cint(account_doc.get("voice_team_ring_seconds")) or 30,
		record=policy in ("All", "Outbound only"),
		record_channels="stereo"
		if (account_doc.get("voice_recording_channels") or "Dual") == "Dual"
		else "mono",
		caller_id=account_doc.get("voice_number") or "",
		max_conversation_seconds=frappe.utils.cint(account_doc.get("voice_max_call_seconds")) or 3600,
	)

	frappe.enqueue(
		"excom.excom.channels.voice.handler.persist_call",
		queue="short",
		provider_call_id=call_uuid,
		account=account,
		direction="Outbound",
		caller_number=target,
		business_number=account_doc.get("voice_number") or "",
		decision_users=[u for u in [event.sip_headers.get("user")] if u],
		agent=event.sip_headers.get("user") or None,
		transport="Browser",
		raw=params,
	)
	return provider.render_decision(decision)


def _refuse_outbound(agent: str, target: str, account_doc):
	"""A refusal to speak down the line, or None if the call may go ahead.

	Refusing has to be said out loud rather than raised: a throw here is caught by `route()`, which
	falls back to ringing the whole team — so a blocked number would end up connected to whoever
	answered. An agent who hears the reason also stops retrying.
	"""
	from excom.excom.channels.voice.providers.base import CallDecision

	try:
		number = outbound.to_e164(target, account_doc)
		outbound.check_dialling_allowed(agent or frappe.session.user, number, account_doc)
		return None
	except Exception as exc:
		message = getattr(exc, "message", "") or str(exc) or _("That call is not allowed.")
		return CallDecision(destinations=[], no_answer_message=frappe.utils.strip_html(message))


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def fallback():
	"""Where the provider goes when `route` did not answer in time. Cache-only, no identity work."""
	account = _verified_account()
	provider = providers.for_account(account)
	return _xml(provider.render_decision(routing.fallback_decision(account)))


def _handle_event(kind: str, account: str):
	provider = providers.for_account(account)
	event = provider.normalize_event(kind, _payload())
	return handler.apply_event(event, account)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def ringing():
	"""ring_url. The provider started dialling somebody."""
	return _handle_event("ringing", _verified_account())


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def dial_event():
	"""Dial callbackUrl. Fires on answer, so this is where we learn who picked up."""
	return _handle_event("dial_event", _verified_account())


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def dial_action():
	"""Dial action URL. The dial finished, with a status saying how."""
	return _handle_event("dial_action", _verified_account())


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def hangup_event():
	"""hangup_url. Carries billed duration and cost."""
	return _handle_event("hangup", _verified_account())


# Plivo is configured with `.../voice.hangup`, which reads better in the console than
# `hangup_event`. The Python name differs because `hangup` is also the agent action below.
hangup = hangup_event


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def recording_ready():
	"""Record callbackUrl. Session recordings report a duration of -1 until the final call."""
	return _handle_event("recording", _verified_account())



# ── the softphone ─────────────────────────────────────────────────────────────


@frappe.whitelist()
@user_rate_limit(limit=30, seconds=60)
def softphone_token(account: str = ""):
	"""A short-lived login credential for the caller's own softphone.

	Takes no user parameter, deliberately. Minting for an arbitrary user would hand anybody a phone
	line billed to the company.
	"""
	_check_excom_access()
	account = account or outbound.default_voice_account()
	if not account:
		frappe.throw(_("No voice line is configured."), frappe.DoesNotExistError)
	return mint_token(frappe.session.user, account)


@frappe.whitelist()
def heartbeat(account: str, registered: int = 1):
	"""Called on softphone login and once a minute after. Cheap by design."""
	_check_excom_access()
	user = frappe.session.user
	if frappe.utils.cint(registered):
		note_registration(user, account, frappe.local.request_ip or "")
	else:
		presence.mark_unregistered(user, account)
	return presence.snapshot(user, account)


@frappe.whitelist()
def set_availability(available: int = 1, account: str = ""):
	"""The "take calls / do not" toggle."""
	_check_excom_access()
	user = frappe.session.user
	presence.set_available(user, bool(frappe.utils.cint(available)))
	return presence.snapshot(user, account)


@frappe.whitelist()
def my_presence(account: str = ""):
	_check_excom_access()
	return presence.snapshot(frappe.session.user, account)


@frappe.whitelist()
@user_rate_limit(limit=60, seconds=60)
def identify_caller(number: str):
	"""Who is calling, for the screen pop.

	The pop can arrive by two routes: the server's `excom:call_ringing`, which carries the identity
	already, and the softphone SDK's own incoming-call event, which carries only a number. The
	second route is the one that still works when Frappe's realtime is down, so it needs a way to
	put a name on the card.

	Read-only and rate-limited. It answers for a number the agent could already have reached by
	searching, and says nothing at all about one nobody has spoken to.
	"""
	_check_excom_access()
	from excom.excom.channels.voice.routing import resolve_identity_readonly

	identity = resolve_identity_readonly(number or "")
	if not identity:
		return {"found": False}

	row = frappe.db.get_value(
		"Omni Identity", identity, ["name", "display_name", "primary_phone"], as_dict=True
	)
	thread = frappe.db.get_value(
		"Excom Thread",
		{"omni_identity": identity},
		"name",
		order_by="last_message_at desc",
	)
	return {
		"found": True,
		"omni_identity": row.name,
		"display_name": row.display_name or row.primary_phone,
		"thread": thread,
	}


@frappe.whitelist()
def softphone_config():
	"""What the browser needs before it can show a dialler at all."""
	_check_excom_access()
	user = frappe.session.user
	account = outbound.default_voice_account(user)
	if not account:
		return {"enabled": False, "reason": "no_line"}

	account_doc = frappe.get_cached_doc("Excom Channel Account", account)
	provider = providers.for_account(account_doc)
	endpoint = frappe.db.exists(
		"Excom Voice Endpoint", {"user": user, "channel_account": account, "status": "Active"}
	)
	return {
		"enabled": True,
		"account": account,
		"account_name": account_doc.account_name,
		"business_number": account_doc.get("voice_number"),
		"has_endpoint": bool(endpoint),
		"browser_calls": bool(account_doc.get("voice_allow_browser_calls")),
		"phone_calls": bool(account_doc.get("voice_allow_phone_calls")),
		"capabilities": sorted(provider.capabilities()),
		"presence": presence.snapshot(user, account),
		"heartbeat_seconds": presence.HEARTBEAT_SECONDS,
	}


# ── calling ───────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=60)
def dial(to_number: str, account: str = "", thread: str = "", transport: str = ""):
	"""Start an outbound call. Returns `{mode: browser|phone}` and what to do with it."""
	_check_excom_access()
	if thread:
		_check_thread_access(thread)
	return outbound.dial(
		to_number=to_number,
		account=account,
		thread=thread,
		transport=transport,
		user=frappe.session.user,
	)


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=40, seconds=60)
def browser_call_started(provider_call_id: str, to_number: str, account: str, thread: str = ""):
	"""The SDK gives the browser a call uuid the moment it dials. Registering it here means the
	record exists before the answer URL fires, so the timeline never shows a gap."""
	_check_excom_access()
	if thread:
		_check_thread_access(thread)
	outbound.register_browser_call(
		provider_call_id=provider_call_id,
		to_number=to_number,
		account=account,
		thread=thread,
		user=frappe.session.user,
	)
	return {"status": "ok"}


@frappe.whitelist(methods=["POST"])
def end_call(call: str):
	"""Hang up from the server. The browser drops its own leg locally as well."""
	_check_excom_access()
	_assert_call_access(call)
	return outbound.hangup(call)


@frappe.whitelist(methods=["POST"])
def report_quality(call: str, metrics: str = ""):
	"""Store the SDK's media metrics on the call.

	When an agent says "the line was terrible", this is the only record that can answer.
	"""
	_check_excom_access()
	_assert_call_access(call)
	try:
		parsed = json.loads(metrics or "{}")
	except ValueError:
		frappe.throw(_("Call quality data could not be read."))
	if not isinstance(parsed, dict):
		frappe.throw(_("Call quality data could not be read."))

	score = parsed.get("mos") or parsed.get("score")
	updates = {"quality_json": json.dumps(parsed)[:8000]}
	if isinstance(score, (int, float)):
		updates["quality_score"] = float(score)
	frappe.db.set_value("Excom Call", call, updates, update_modified=False)
	return {"status": "ok"}


# ── reading ───────────────────────────────────────────────────────────────────

CALL_FIELDS = [
	"name",
	"provider_call_id",
	"direction",
	"transport",
	"status",
	"display_name",
	"customer_number",
	"business_number",
	"agent",
	"answered_by",
	"team",
	"thread",
	"omni_identity",
	"duration",
	"talk_time",
	"recording_status",
	"summary",
	"transcript_status",
	"creation",
]


@frappe.whitelist()
def active_call():
	"""The call this agent is on, or being rung for."""
	_check_excom_access()
	rows = frappe.get_all(
		"Excom Call",
		filters={"status": ["in", ["Ringing", "In Progress"]]},
		or_filters=[
			["agent", "=", frappe.session.user],
			["answered_by", "=", frappe.session.user],
		],
		fields=CALL_FIELDS,
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def call_history(thread: str = "", omni_identity: str = "", limit: int = 20):
	"""Calls on one conversation or one contact.

	Scoped by the same rule as the conversation itself — the reference implementation took a thread
	id and returned its calls to anyone who asked.
	"""
	_check_excom_access()
	limit = min(frappe.utils.cint(limit) or 20, 100)

	if thread:
		_check_thread_access(thread)
		filters = {"thread": thread}
	elif omni_identity:
		from excom.excom.api.chat import _check_identity_access

		_check_identity_access(omni_identity)
		filters = {"omni_identity": omni_identity}
	else:
		frappe.throw(_("Give a conversation or a contact."))

	return frappe.get_all(
		"Excom Call", filters=filters, fields=CALL_FIELDS, order_by="creation desc", limit=limit
	)


@frappe.whitelist()
def call_detail(call: str):
	_check_excom_access()
	_assert_call_access(call)
	return frappe.db.get_value(
		"Excom Call", call, CALL_FIELDS + ["cost", "hangup_cause", "notes", "next_action"], as_dict=True
	)


# The statuses that mean a customer tried to reach us and did not get through. Busy and Failed
# belong here as much as Missed does: from the customer's side they are the same experience.
UNANSWERED = ["Missed", "No Answer", "Busy", "Failed"]


@frappe.whitelist()
def list_calls(view: str = "missed", limit: int = 100):
	"""The Calls page.

	`get_list`, not `get_all`, so the permission query runs: an agent sees the calls belonging to
	conversations they can open, and nothing else.
	"""
	_check_excom_access()
	limit = min(frappe.utils.cint(limit) or 100, 200)

	filters = {}
	if view == "missed":
		filters = {"status": ["in", UNANSWERED], "direction": "Inbound"}
	elif view in ("inbound", "outbound"):
		filters = {"direction": view.capitalize()}
	elif view == "mine":
		filters = {"agent": frappe.session.user}
	elif view == "recorded":
		filters = {"recording_status": "Ready"}

	return frappe.get_list(
		"Excom Call",
		filters=filters,
		fields=CALL_FIELDS,
		order_by="creation desc",
		limit=limit,
	)


@frappe.whitelist()
def missed_calls(limit: int = 50):
	"""The missed-call queue. Kept as its own name because that is what it is called everywhere
	else — the badge, the notification and the worklist."""
	_check_excom_access()
	return list_calls(view="missed", limit=limit)


@frappe.whitelist(methods=["POST"])
def save_notes(call: str, notes: str = ""):
	"""Notes taken during or just after the call."""
	_check_excom_access()
	_assert_call_access(call)
	frappe.db.set_value(
		"Excom Call", call, "notes", frappe.utils.sanitize_html(notes or "")[:20000]
	)
	return {"status": "ok"}


@frappe.whitelist()
@user_rate_limit(limit=60, seconds=60)
def get_recording(call: str, download: int = 0):
	"""Stream a recording. Never hands the provider URL to the browser."""
	_check_excom_access()
	return recording.stream(call, download=bool(frappe.utils.cint(download)))


# ── administration ────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def provision_line(account: str):
	"""Give every agent who works this line a softphone, and retire the ones who no longer do."""
	_check_admin_access()
	return sync_line(account)


@frappe.whitelist(methods=["POST"])
def provision_agent(user: str, account: str):
	_check_admin_access()
	return {"endpoint": ensure_endpoint(user, account)}


@frappe.whitelist()
def line_status(account: str):
	"""Credential check and endpoint roster for the admin screen."""
	_check_admin_access()
	account_doc = frappe.get_doc("Excom Channel Account", account)

	# A line whose provider has no adapter yet is a thing an administrator needs to SEE, not an
	# error that blanks the page. The site had an old Exotel line sitting first in the list, and
	# opening Admin → Calls raised through to a 417 and showed nothing at all.
	try:
		provider = providers.for_account(account_doc)
		capabilities = sorted(provider.capabilities())
		provider_error = ""
	except Exception as exc:
		provider = None
		capabilities = []
		provider_error = str(exc)

	endpoints = frappe.get_all(
		"Excom Voice Endpoint",
		filters={"channel_account": account},
		fields=["name", "user", "status", "sip_uri", "last_registered_at"],
		order_by="user asc",
	)
	for row in endpoints:
		row["registered_now"] = presence.is_registered(row["user"], account)
		row["available"] = presence.is_available(row["user"])

	from excom.excom.channels.voice.providers.plivo import webhook_url

	return {
		"account": account,
		"provider": account_doc.get("voice_provider"),
		"capabilities": capabilities,
		"provider_error": provider_error,
		"credentials_present": bool(account_doc.get("voice_auth_id")),
		"agents_on_line": len(routing.line_agents(account)),
		"endpoints": endpoints,
		"webhooks": {
			# Untuned: the admin screen shows these for a human to read and paste. The timeout
			# fragment is machine configuration and only clutters the copy button.
			kind: webhook_url(kind, account, tuned=False)
			for kind in ("route", "fallback", "hangup", "ringing")
		},
	}


@frappe.whitelist(methods=["POST"])
def test_credentials(account: str):
	"""Prove the credentials work before a real call does it for us."""
	_check_admin_access()
	provider = providers.for_account(account)
	details = provider.fetch_call_details("connectivity-check")
	# A 404 for a made-up call id is a success: it means we authenticated and were answered.
	return {"reachable": True, "found": details.found}


# ── shared ────────────────────────────────────────────────────────────────────


def _assert_call_access(call: str) -> None:
	from excom.excom.doctype.excom_call.excom_call import can_access

	if not frappe.db.exists("Excom Call", call):
		frappe.throw(_("That call does not exist."), frappe.DoesNotExistError)
	if not can_access(call):
		deny(
			_("You cannot open this call."),
			detail=_("It belongs to a conversation on another desk."),
		)


def _known_name(identity: str | None, fallback_number: str) -> str:
	if not identity:
		return fallback_number
	return frappe.db.get_value("Omni Identity", identity, "display_name") or fallback_number
