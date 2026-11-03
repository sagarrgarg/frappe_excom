"""WhatsApp Business Calling, behind the voice provider seam.

There is no vendor SDK and no answer URL. Signalling is Graph API `POST /{phone id}/calls` actions
plus `calls` webhooks; the media is WebRTC from the agent's browser straight to Meta's relay. So
this adapter implements the parts of `VoiceProvider` that mean something here (hang up, normalise
a webhook, read back what we know) and a `SoftphoneProvider` whose "SDK" is the browser's own
RTCPeerConnection: the descriptor carries the ICE servers and nothing else.

Meta supplies no STUN/TURN, is ICE-lite, takes no trickle ICE and never renegotiates. The browser
therefore sends one complete SDP (gathering finished) and the ICE servers come from Excom Settings.

The orchestration (who rings, who answers, permissions) is in `channels/voice/whatsapp_calling.py`.
"""

import json
import math

import frappe
from frappe import _

from excom.excom.channels.voice.providers.base import (
	CAP_HANGUP,
	CAP_PARALLEL_RING,
	CAP_WEBRTC,
	CallDecision,
	CallDetails,
	CallEvent,
	Destination,
	EndpointRef,
	ProviderCallRef,
	SoftphoneProvider,
	VoiceProvider,
)

NAME = "WhatsApp"
SDK = "whatsapp-webrtc"
# A public STUN server is enough when the agent's network allows UDP out. TURN is for strict NAT.
DEFAULT_ICE_SERVERS = [{"urls": ["stun:stun.l.google.com:19302"]}]
ICE_SCHEMES = ("stun:", "turn:", "turns:")
MAX_SDP = 20000
MAX_OPAQUE = 512

INBOUND = "USER_INITIATED"
OUTBOUND = "BUSINESS_INITIATED"

# Calling error codes (calling/troubleshooting), in words an agent can act on.
ERROR_TEXT = {
	138000: "Calling is not enabled on this WhatsApp number.",
	138001: "This person cannot be called on WhatsApp.",
	138002: "Too many calls are in progress on this number. Try again shortly.",
	138003: "A call with this person is already in progress.",
	138004: "WhatsApp could not connect the call. Try again.",
	138005: "Too many call attempts. Wait a little before calling again.",
	138006: "This person has not given permission to be called. Send a call permission request first.",
	138007: "The call timed out while connecting (the browser's answer arrived too late).",
	138009: "The limit for call permission requests to this person has been reached (1 a day, 2 a week).",
	138012: "The daily limit of business-initiated calls to this person has been reached.",
	138013: "Business-initiated calling is not available for this number's country.",
	138014: "Calling is temporarily disabled on this number because of low call quality.",
	138015: "Calling needs a messaging limit of at least 2,000 unique users a day.",
	138017: "This person already allows calls permanently.",
	138018: "Calling prerequisites are not met: subscribe the app to the 'calls' webhook field.",
	138019: "The call could not be set up.",
	138020: "The call's media relay failed.",
	138021: "The person's WhatsApp ended the call: it received no audio from Excom (check the agent's microphone and network).",
	138022: "The person's WhatsApp ended the call: it could not send its audio (their network).",
	138023: "The call was accepted but no audio flowed.",
	131055: "This number uses SIP for calls, so calls cannot be placed from the browser.",
	131026: "This person's WhatsApp is too old for calls or call permission requests.",
}


# ── payloads (pure) ───────────────────────────────────────────────────────────


def _check_sdp(sdp: str) -> str:
	sdp = str(sdp or "")
	if not sdp.strip():
		frappe.throw(_("The browser did not produce a session description."))
	if len(sdp) > MAX_SDP or "m=audio" not in sdp:
		frappe.throw(_("The browser's session description is not a single audio stream."))
	# Meta wants RFC 8866 line endings.
	return "\r\n".join(sdp.replace("\r\n", "\n").split("\n"))


def action_payload(call_id: str, action: str, sdp: str = "", opaque: str = "") -> dict:
	"""One `/calls` action: pre_accept | accept | reject | terminate."""
	if action not in ("pre_accept", "accept", "reject", "terminate"):
		frappe.throw(_("Unknown call action {0}").format(action))
	payload = {"messaging_product": "whatsapp", "call_id": call_id, "action": action}
	if action in ("pre_accept", "accept"):
		payload["session"] = {"sdp_type": "answer", "sdp": _check_sdp(sdp)}
	if action == "accept" and opaque:
		payload["biz_opaque_callback_data"] = str(opaque)[:MAX_OPAQUE]
	return payload


def connect_payload(to: str, sdp: str, opaque: str = "") -> dict:
	"""A business-initiated call. `to` is a phone number or a business-scoped user id."""
	from excom.excom.services import whatsapp_service as ws

	payload = {"messaging_product": "whatsapp", **ws._recipient(to), "action": "connect",
		"session": {"sdp_type": "offer", "sdp": _check_sdp(sdp)}}
	if opaque:
		payload["biz_opaque_callback_data"] = str(opaque)[:MAX_OPAQUE]
	return payload


def call_direction(item: dict) -> str:
	return "Outbound" if str(item.get("direction") or "").upper() == OUTBOUND else "Inbound"


def terminate_status(item: dict) -> str:
	"""COMPLETED | FAILED. The doc shows a string and a one-item list, in either case."""
	raw = item.get("status")
	if isinstance(raw, (list, tuple)):
		raw = raw[0] if raw else ""
	return str(raw or "").strip().upper()


def call_status_after_terminate(item: dict, answered: bool) -> str:
	"""Excom's status for a terminated call. A call that talked (duration > 0) and then failed on
	media is Completed; the error code still goes into hangup_cause."""
	if terminate_status(item) == "FAILED" and cint_(item.get("duration")) <= 0:
		return "Failed"
	if cint_(item.get("duration")) > 0 or answered:
		return "Completed"
	return "Missed" if call_direction(item) == "Inbound" else "No Answer"


def cint_(value) -> int:
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def error_of(errors) -> tuple[int, str]:
	"""(code, sentence) from a webhook's `errors` list."""
	for err in errors or []:
		if not isinstance(err, dict):
			continue
		code = cint_(err.get("code"))
		detail = (err.get("error_data") or {}).get("details") or err.get("message") or err.get("title") or ""
		return code, ERROR_TEXT.get(code) or str(detail)[:200]
	return 0, ""


def call_cost(duration: int, rate_per_minute: float) -> float:
	"""Billed in 6-second pulses, part pulses rounded up."""
	if duration <= 0 or not rate_per_minute:
		return 0.0
	return round(math.ceil(duration / 6) * 6 / 60 * float(rate_per_minute), 4)


def scrub_event(item: dict) -> dict:
	"""A webhook item fit for `provider_events`: no numbers, user ids, names or SDP (it holds IPs)."""
	from excom.excom.utils.webhook import _scrub

	clean = _scrub(item or {})
	if isinstance(clean, dict):
		for key in ("to", "to_user_id", "to_parent_user_id", "from_parent_user_id", "recipient_parent_user_id"):
			if key in clean:
				clean[key] = "…"
		session = clean.get("session")
		if isinstance(session, dict) and session.get("sdp"):
			clean["session"] = {"sdp_type": session.get("sdp_type"), "sdp": f"<{len(str(session['sdp']))} bytes>"}
		clean.pop("connection", None)
	return clean


# ── ICE servers ───────────────────────────────────────────────────────────────


def ice_servers() -> list[dict]:
	"""STUN/TURN servers for the browser, from Excom Settings. Never logged.

	`whatsapp_ice_servers` is a JSON list of {"urls": ..., "username": ...}; the TURN credential is
	kept encrypted in `whatsapp_turn_credential` and added to every entry with a username. It is one
	static credential shared by every agent (time-limited TURN credentials are not built).
	"""
	log = getattr(frappe.local, "message_log", None)
	seen = len(log) if isinstance(log, list) else 0
	try:
		settings = frappe.get_cached_doc("Excom Settings")
		raw = settings.get("whatsapp_ice_servers") or ""
		credential = settings.get_password("whatsapp_turn_credential", raise_exception=False) or ""
	except Exception:
		return DEFAULT_ICE_SERVERS
	finally:
		# Only what reading the password may have queued; the request's own messages stay.
		log = getattr(frappe.local, "message_log", None)
		if isinstance(log, list) and len(log) > seen:
			del log[seen:]
	servers = parse_ice_servers(raw, credential)
	return servers or DEFAULT_ICE_SERVERS


def inline_credentials(raw) -> list[str]:
	"""Credentials pasted into the ICE servers JSON (they belong in the Password field)."""
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return []
	data = [data] if isinstance(data, dict) else data
	return [str(e["credential"]) for e in data if isinstance(e, dict) and e.get("credential")] if isinstance(data, list) else []


def move_inline_credential(settings) -> None:
	"""Excom Settings validate: a credential pasted into the ICE servers JSON moves into the
	encrypted TURN Credential field, or the save is refused if that cannot be done cleanly."""
	raw = settings.get("whatsapp_ice_servers") or ""
	found = inline_credentials(raw)
	if not found:
		return
	stored = settings.get("whatsapp_turn_credential") or ""
	if stored and set(stored) == {"*"}:
		# unchanged on this save: the field shows a mask, the value is in the encrypted store
		stored = settings.get_password("whatsapp_turn_credential", raise_exception=False) or ""
	if len(set(found)) > 1 or (stored and stored != found[0]):
		frappe.throw(_("Put the TURN password in the TURN Credential field, not in the ICE servers list. "
			"Excom uses one credential for every TURN server."))
	data = json.loads(raw) if isinstance(raw, str) else raw
	data = [data] if isinstance(data, dict) else data
	for entry in data:
		if isinstance(entry, dict):
			entry.pop("credential", None)
	settings.whatsapp_ice_servers = json.dumps(data, indent=1)
	settings.whatsapp_turn_credential = found[0]


def parse_ice_servers(raw, credential: str = "") -> list[dict]:
	"""Keep only well-formed stun:/turn:/turns: entries. Bad JSON gives an empty list.

	A credential inside the JSON is ignored: only the encrypted TURN Credential field is used."""
	if not raw:
		return []
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return []
	if isinstance(data, dict):
		data = [data]
	out = []
	for entry in data if isinstance(data, list) else []:
		if not isinstance(entry, dict):
			continue
		urls = entry.get("urls") or entry.get("url")
		urls = [urls] if isinstance(urls, str) else urls
		urls = [str(u).strip() for u in urls or [] if str(u).strip().lower().startswith(ICE_SCHEMES)]
		if not urls:
			continue
		server = {"urls": urls}
		if entry.get("username"):
			server["username"] = str(entry["username"])
			if credential:
				server["credential"] = str(credential)
		out.append(server)
	return out


# ── Graph ─────────────────────────────────────────────────────────────────────


def graph(account, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
	"""One call to `/{phone id}/{path}`. Raises WhatsAppAPIError with Meta's code."""
	from excom.excom.services import whatsapp_service as ws
	from excom.excom.services.whatsapp_errors import raise_for_error
	from excom.excom.utils.errors import ExcomProviderError

	token, base_url, version, phone_id = ws._resolve_credentials(account)
	url = f"{base_url}/{version}/{phone_id}/{path}"
	headers = {"Authorization": f"Bearer {token}"}
	try:
		if method == "GET":
			resp = ws.http_requests.get(url, params=params or {}, headers=headers, timeout=(5, 20))
		else:
			headers["Content-Type"] = "application/json"
			resp = ws.http_requests.post(url, json=payload or {}, headers=headers, timeout=(5, 20))
	except ws.http_requests.exceptions.RequestException as exc:
		raise ExcomProviderError(f"WhatsApp calling API unreachable: {exc}", provider="whatsapp") from exc
	try:
		data = resp.json()
	except ValueError:
		data = {}
	if not isinstance(data, dict):
		data = {}
	status = getattr(resp, "status_code", 200)
	if status >= 400 or data.get("error"):
		raise_for_error(data.get("error") or {"message": (getattr(resp, "text", "") or "")[:300]}, status,
			account_name=getattr(account, "name", ""))
	return data


# ── the adapter ───────────────────────────────────────────────────────────────


class WhatsAppCallingAdapter(VoiceProvider, SoftphoneProvider):
	name = NAME

	def capabilities(self) -> set[str]:
		# Parallel ring is ours, not Meta's: every ready agent's browser is shown the offer.
		return {CAP_WEBRTC, CAP_HANGUP, CAP_PARALLEL_RING}

	def render_decision(self, decision: CallDecision) -> str:
		frappe.throw(_("WhatsApp calls have no answer URL; they are answered from the browser."))

	def initiate_call(self, to: Destination, caller_id: str = "", opts: dict | None = None) -> ProviderCallRef:
		opts = opts or {}
		data = graph(self.account, "POST", "calls", connect_payload(to.ref, opts.get("sdp") or "", opts.get("opaque") or ""))
		calls = data.get("calls") or []
		call_id = (calls[0] or {}).get("id") if calls else ""
		if not call_id:
			frappe.throw(_("WhatsApp did not return a call id."))
		return ProviderCallRef(provider_call_id=call_id, status="ringing", to_number=to.ref, raw={"calls": [{"id": call_id}]})

	def pre_accept(self, call_id: str, sdp: str) -> dict:
		return graph(self.account, "POST", "calls", action_payload(call_id, "pre_accept", sdp))

	def accept(self, call_id: str, sdp: str, opaque: str = "") -> dict:
		return graph(self.account, "POST", "calls", action_payload(call_id, "accept", sdp, opaque))

	def reject(self, call_id: str) -> dict:
		return graph(self.account, "POST", "calls", action_payload(call_id, "reject"))

	def hangup(self, provider_call_id: str) -> None:
		graph(self.account, "POST", "calls", action_payload(provider_call_id, "terminate"))

	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		"""Meta has no call detail record to read back: the terminate webhook is the record.

		A closed call is "found" with what we stored, so the reconcile sweep can retire it; an open
		one is "not yet", so it is never closed as missed while it is still ringing.
		"""
		from excom.excom.doctype.excom_call.excom_call import CLOSED_STATUSES

		row = frappe.db.get_value("Excom Call", {"provider_call_id": provider_call_id},
			["status", "duration", "cost", "hangup_cause"], as_dict=True)
		if not row or row.status not in CLOSED_STATUSES:
			return CallDetails(provider_call_id=provider_call_id, found=False)
		return CallDetails(provider_call_id=provider_call_id, status=row.status, duration=cint_(row.duration),
			bill_duration=cint_(row.duration), cost=float(row.cost or 0), hangup_cause=row.hangup_cause or "", found=True)

	def fetch_recording_stream(self, call_doc):
		frappe.throw(_("WhatsApp calls are not recorded by Excom."))

	def normalize_event(self, kind_hint: str, payload: dict) -> CallEvent:
		"""A `calls[]` or `statuses[]` item → CallEvent. `payload["errors"]` may carry the sibling errors."""
		item = payload or {}
		direction = call_direction(item)
		event_name = str(item.get("event") or "").lower()
		status_word = str(item.get("status") or "").upper() if item.get("type") == "call" else ""
		code, reason = error_of(item.get("errors"))
		if event_name == "terminate":
			kind, status = "ended", call_status_after_terminate(item, answered=False)
		elif status_word == "ACCEPTED":
			kind, status = "answered", "In Progress"
		elif status_word == "REJECTED":
			kind, status, reason = "ended", "Busy", reason or "They declined the call."
		elif event_name == "connect" or status_word == "RINGING":
			kind, status = "ringing", "Ringing"
		else:
			kind, status = "unknown", ""
		business = item.get("to") if direction == "Inbound" else item.get("from")
		customer = item.get("from") if direction == "Inbound" else (item.get("to") or item.get("recipient_id"))
		return CallEvent(
			kind=kind,
			provider_call_id=str(item.get("id") or ""),
			from_number=str(customer or "") if direction == "Inbound" else str(business or ""),
			to_number=str(business or "") if direction == "Inbound" else str(customer or ""),
			business_number=str(business or ""),
			direction=direction.lower(),
			status=status,
			duration=cint_(item.get("duration")),
			bill_duration=cint_(item.get("duration")),
			hangup_cause=f"{code}" if code else "",
			failure_reason=reason,
			raw=scrub_event(item),
		)

	def verify_webhook(self, url: str, method: str, headers: dict, form: dict) -> bool:
		# Meta signs the whole webhook; utils.webhook.post() has already checked it.
		return bool(frappe.flags.get("excom_webhook_signed"))

	def originates_from_softphone(self, payload: dict) -> bool:
		return False

	# ── softphone ─────────────────────────────────────────────────────────

	def provision_endpoint(self, user: str, alias: str) -> EndpointRef:
		frappe.throw(_("WhatsApp calls need no softphone account: an agent on the line takes them in the browser."))

	def deprovision_endpoint(self, endpoint_id: str) -> None:
		return None

	def mint_access_token(self, endpoint_username: str, ttl_seconds: int) -> str:
		# The browser's Frappe session is the credential; nothing vendor-side to mint.
		return ""

	def sdk_descriptor(self) -> dict:
		return {"sdk": SDK, "options": {"iceServers": ice_servers(), "account": self.account.name}}

	def browser_context(self, context: dict[str, str]) -> dict[str, str]:
		return {k: str(v) for k, v in (context or {}).items() if v}


def adapter_for(account) -> WhatsAppCallingAdapter:
	if isinstance(account, str):
		account = frappe.get_cached_doc("Excom Channel Account", account)
	return WhatsAppCallingAdapter(account)
