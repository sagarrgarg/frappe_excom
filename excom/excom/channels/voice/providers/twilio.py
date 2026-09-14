"""Twilio adapter — the second vendor, and the one that can reach the rest of the world.

Plivo keeps India, because an India-region Plivo account may only call India and a US-region one
may never call India, on any plan. Twilio's per-country permissions are self-service, so every
other destination comes here.

Three things are genuinely simpler than the Plivo side, and the code is shorter because of them:

1. **There is no endpoint to provision.** A client identity is any alphanumeric string carried in
   the access token. Nothing is created at Twilio, so `provision_endpoint` only decides on a name.
   The auth-id suffix that made Plivo's inbound calls fail silently has no equivalent here.
2. **Tokens are signed locally** with an API key and secret, so signing in costs no round trip.
3. **The browser SDK is not a singleton**, so a Twilio line can be registered alongside the Plivo
   one rather than replacing it.

The two rules from the Plivo adapter still hold: credentials go in an ``auth=`` tuple and never
into a URL, and call-control XML is built by the vendor's own builder rather than by string
formatting, because a caller id is attacker-controlled input.
"""

from typing import Any, Iterator
from urllib.parse import quote

import frappe
from frappe import _

from excom.excom.channels.voice.providers.base import (
	CAP_CLICK_TO_CALL,
	CAP_DUAL_CHANNEL_RECORDING,
	CAP_HANGUP,
	CAP_MIXED_PARALLEL,
	CAP_PARALLEL_RING,
	CAP_SIGNED_WEBHOOKS,
	CAP_TRANSFER,
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
from excom.excom.channels.voice.providers.plivo import public_site_url, webhook_url

API_BASE = "https://api.twilio.com/2010-04-01"
HTTP_TIMEOUT = 15

# Twilio hands the browser its own parameters on `device.connect({params})`, which arrive at the
# answer URL as ordinary form fields. No SIP headers, so no prefix — but the names still need to be
# ours and not collide with Twilio's own `To`, `From`, `CallSid`.
PARAM_PREFIX = "Excom"

# DialCallStatus (the action URL) -> Excom Call.status
DIAL_STATUS = {
	"completed": "Completed",
	"answered": "Completed",
	"busy": "Busy",
	"no-answer": "No Answer",
	"failed": "Failed",
	"canceled": "Canceled",
}

# CallStatus (the status callback) -> Excom Call.status
CALL_STATUS = {
	"completed": "Completed",
	"busy": "Busy",
	"no-answer": "No Answer",
	"failed": "Failed",
	"canceled": "Canceled",
	"in-progress": "In Progress",
	"ringing": "Ringing",
}

# Twilio error code -> what the agent should do about it. Same job as the Plivo table: a call that
# a carrier refused must not read like a call nobody answered.
FAILURE_REASONS = {
	"13224": _(
		"Twilio will not call this country from your account. Enable it under "
		"Voice → Geo permissions, then try again."
	),
	"13225": _("Calls to this number are blocked by a dialling permission on the account."),
	"21215": _(
		"This destination is not enabled on the account. Check Voice → Geo permissions."
	),
	"21216": _("Calls to this number are blocked."),
	"13214": _("Twilio has no route to this number. Check the country code, then the number."),
	"21211": _("That number is not in a form the network accepts."),
	"21210": _("The caller id is not a number this account owns or has verified."),
	"31005": _("The connection to the softphone dropped."),
}


def _int(value) -> int:
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return 0


def _float(value) -> float:
	try:
		return abs(float(value))
	except (TypeError, ValueError):
		return 0.0


def client_identity(user: str) -> str:
	"""A Twilio client identity for this agent.

	"Voice tokens may only contain alpha-numeric and underscore characters", so an email address
	cannot be used as-is. Derived rather than stored so the same agent always resolves to the same
	identity, on any line, without a lookup.
	"""
	safe = "".join(ch if ch.isalnum() else "_" for ch in (user or "").lower())
	return f"excom_{safe.strip('_')}"[:120]


class TwilioAdapter(VoiceProvider, SoftphoneProvider):
	name = "Twilio"

	def __init__(self, account_doc):
		super().__init__(account_doc)
		self.account_sid = (account_doc.get("voice_auth_id") or "").strip()
		base = (account_doc.get("voice_api_base") or "").strip() or API_BASE
		if not base.startswith("http"):
			base = f"https://{base}"
		self.api_base = base.rstrip("/")
		# The TwiML Application, which is what a browser call is placed against.
		self.app_sid = (account_doc.get("voice_app_id") or "").strip()
		self.api_key_sid = (account_doc.get("voice_api_key_sid") or "").strip()

	# ── credentials ───────────────────────────────────────────────────────────

	@property
	def auth_token(self) -> str:
		"""Read lazily so merely constructing an adapter does not decrypt a secret."""
		try:
			return self.account.get_password("voice_auth_token", raise_exception=False) or ""
		except Exception:
			return ""

	@property
	def api_key_secret(self) -> str:
		try:
			return self.account.get_password("voice_api_key_secret", raise_exception=False) or ""
		except Exception:
			return ""

	def _auth(self) -> tuple[str, str]:
		return (self.account_sid, self.auth_token)

	def _url(self, path: str) -> str:
		return f"{self.api_base}/Accounts/{quote(self.account_sid, safe='')}/{path}"

	def _request(self, method: str, path: str, **kwargs):
		import requests

		if not self.account_sid or not self.auth_token:
			frappe.throw(_("This voice line has no Twilio credentials configured."))
		kwargs.setdefault("timeout", HTTP_TIMEOUT)
		return requests.request(method, self._url(path), auth=self._auth(), **kwargs)

	# ── capabilities ──────────────────────────────────────────────────────────

	def capabilities(self) -> set[str]:
		caps = {
			CAP_CLICK_TO_CALL,
			CAP_PARALLEL_RING,
			CAP_MIXED_PARALLEL,
			CAP_SIGNED_WEBHOOKS,
			CAP_DUAL_CHANNEL_RECORDING,
			CAP_TRANSFER,
			CAP_HANGUP,
		}
		# Browser calling needs somewhere for the call to land and a key to sign the token with.
		if self.account.get("voice_allow_browser_calls") and self.app_sid and self.api_key_sid:
			caps.add(CAP_WEBRTC)
		return caps

	# ── call control ──────────────────────────────────────────────────────────

	def render_decision(self, decision: CallDecision) -> str:
		"""One decision in, one TwiML document out.

		Built with Twilio's own `VoiceResponse` rather than by formatting strings — the same reason
		the Plivo side uses ElementTree. A caller id reaches this method unsanitised.
		"""
		from twilio.twiml.voice_response import Dial, VoiceResponse

		response = VoiceResponse()

		if decision.is_empty:
			response.say(
				decision.no_answer_message
				or _("Sorry, nobody is available to take your call right now. Please try again later.")
			)
			response.hangup()
			return str(response)

		if decision.consent_prompt:
			response.play(decision.consent_prompt)

		attrs: dict[str, Any] = {
			"timeout": decision.ring_seconds,
			"time_limit": decision.max_conversation_seconds,
			"action": webhook_url("dial_action", self.account.name, tuned=False),
			"method": "POST",
			# The caller hears ringing until somebody actually answers, rather than being connected
			# to silence while we are still hunting for an agent.
			"answer_on_bridge": True,
		}
		if decision.caller_id:
			attrs["caller_id"] = decision.caller_id
		if decision.record:
			# Dual channel keeps each speaker on their own track, which is what makes attribution
			# in a transcript a fact rather than a guess.
			attrs["record"] = (
				"record-from-answer-dual"
				if (decision.record_channels or "stereo") == "stereo"
				else "record-from-answer"
			)
			attrs["recording_status_callback"] = webhook_url("recording_ready", self.account.name, tuned=False)
			attrs["recording_status_callback_method"] = "POST"

		dial = Dial(**attrs)
		for dest in decision.destinations:
			if dest.is_browser:
				# `<Client>` takes the identity from the access token. No SIP URI, and none of the
				# suffix trouble the Plivo endpoint address caused.
				dial.client(identity_from_ref(dest.ref))
			else:
				dial.number(
					dest.ref,
					status_callback=webhook_url("dial_event", self.account.name, tuned=False),
					status_callback_method="POST",
					status_callback_event="answered completed",
				)
		response.append(dial)
		return str(response)

	def initiate_call(
		self, to: Destination, caller_id: str, opts: dict[str, Any] | None = None
	) -> ProviderCallRef:
		"""Place a call from Twilio's side. This is the Phone transport: the agent's own handset is
		dialled first, and the answer URL bridges them to the customer when they pick up."""
		opts = opts or {}
		from_number = opts.get("from_number") or ""
		if not from_number:
			frappe.throw(_("No number to ring the agent on."))

		payload = {
			"From": caller_id or from_number,
			"To": from_number,
			"Url": opts.get("answer_url") or webhook_url("route", self.account.name, tuned=False),
			"Method": "POST",
			"StatusCallback": webhook_url("hangup", self.account.name, tuned=False),
			"StatusCallbackMethod": "POST",
			"StatusCallbackEvent": ["completed"],
			"Timeout": str(opts.get("ring_seconds") or 30),
			"TimeLimit": str(opts.get("max_conversation_seconds") or 3600),
		}
		fallback = webhook_url("fallback", self.account.name, tuned=False)
		if fallback:
			payload["FallbackUrl"] = fallback

		# Twilio has no SIP headers on an API-placed call, so who this call is for rides in the
		# answer URL's query string instead — the answer URL is ours, and it is signed.
		extras = {"to": to.ref}
		extras.update({k: str(v) for k, v in (opts.get("sip_headers") or {}).items()})
		payload["Url"] = _with_params(payload["Url"], extras)

		resp = self._request("POST", "Calls.json", data=payload)
		if resp.status_code not in (200, 201, 202):
			frappe.throw(_explain_refusal(resp))
		data = resp.json() if resp.content else {}
		return ProviderCallRef(
			provider_call_id=data.get("sid") or "",
			status=CALL_STATUS.get((data.get("status") or "").lower(), "Ringing"),
			from_number=from_number,
			to_number=to.ref,
			raw=data,
		)

	def hangup(self, provider_call_id: str) -> None:
		if not provider_call_id:
			return
		try:
			self._request(
				"POST",
				f"Calls/{quote(provider_call_id, safe='')}.json",
				data={"Status": "completed"},
			)
		except Exception as exc:
			frappe.log_error(
				title="Excom Voice: Twilio hangup failed", message=f"{provider_call_id}: {exc}"
			)

	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		if not provider_call_id:
			return CallDetails(provider_call_id="", found=False)
		try:
			resp = self._request("GET", f"Calls/{quote(provider_call_id, safe='')}.json")
		except Exception as exc:
			frappe.log_error(
				title="Excom Voice: Twilio CDR fetch failed",
				message=f"{provider_call_id}: {exc}",
			)
			return CallDetails(provider_call_id=provider_call_id, found=False)

		if resp.status_code == 404:
			return CallDetails(provider_call_id=provider_call_id, found=False)
		if not resp.ok:
			return CallDetails(provider_call_id=provider_call_id, found=False)

		data = resp.json() if resp.content else {}
		return CallDetails(
			provider_call_id=provider_call_id,
			status=CALL_STATUS.get((data.get("status") or "").lower(), ""),
			duration=_int(data.get("duration")),
			bill_duration=_int(data.get("duration")),
			# Twilio reports price as a negative number, being money leaving the account.
			cost=_float(data.get("price")),
			hangup_cause=str(data.get("status") or ""),
			start_time=data.get("start_time"),
			end_time=data.get("end_time"),
			found=True,
			raw=data,
		)

	def fetch_recording_stream(self, call_doc) -> tuple[Iterator[bytes], str]:
		"""Stream the recording through Excom. The URL Twilio gives is public until the account is
		configured otherwise, so it never reaches the browser."""
		import requests

		url = (call_doc.get("recording_url") or "").strip()
		if not url:
			frappe.throw(_("This call has no recording."), frappe.DoesNotExistError)
		if not url.startswith("http"):
			url = f"{self.api_base}{url}"
		if not url.endswith(".mp3"):
			url = f"{url}.mp3"

		resp = requests.get(url, auth=self._auth(), stream=True, timeout=HTTP_TIMEOUT)
		if not resp.ok:
			frappe.throw(_("The recording could not be fetched from Twilio."))
		return resp.iter_content(chunk_size=64 * 1024), resp.headers.get(
			"Content-Type", "audio/mpeg"
		)

	def action_response(self) -> str:
		"""An empty document: the dial is over and there is nothing further to do.

		Twilio executes whatever comes back from the dial action URL as call control, so this
		cannot be the JSON every other webhook returns — that is error 12300, invalid content
		type, and the caller is thrown to the fallback URL in the middle of a call. An empty
		response document ends the call cleanly.
		"""
		from twilio.twiml.voice_response import VoiceResponse

		return str(VoiceResponse())

	# ── webhooks ──────────────────────────────────────────────────────────────

	def normalize_event(self, kind_hint: str, payload: dict[str, Any]) -> CallEvent:
		"""Map a Twilio webhook onto a CallEvent.

		Twilio names the parent call `CallSid` everywhere, so unlike Plivo there is no hunting for
		which of several uuid fields identifies the leg we hold a record for.
		"""
		params = {k: v for k, v in (payload or {}).items() if k.startswith(PARAM_PREFIX)}
		extras = {k[len(PARAM_PREFIX) :].lower(): v for k, v in params.items()}

		event = CallEvent(
			kind="unknown",
			provider_call_id=payload.get("CallSid") or payload.get("ParentCallSid") or "",
			from_number=payload.get("From") or payload.get("Caller") or "",
			to_number=payload.get("To") or payload.get("Called") or "",
			direction=(payload.get("Direction") or "").lower(),
			digits=payload.get("Digits") or "",
			sip_headers=extras,
			raw=payload,
		)

		if kind_hint == "ringing":
			event.kind = "ringing"
			return event

		if kind_hint == "dial_event":
			status = (payload.get("CallStatus") or payload.get("DialCallStatus") or "").lower()
			if status in ("in-progress", "answered"):
				event.kind = "answered"
				event.answered_destination = payload.get("Called") or payload.get("To") or ""
			elif status in ("completed", "busy", "no-answer", "failed", "canceled"):
				event.kind = "ended"
				event.status = CALL_STATUS.get(status, "")
				event.duration = _int(payload.get("CallDuration") or payload.get("DialCallDuration"))
				event.hangup_cause = status
				event.failure_reason = _reason(payload)
			return event

		if kind_hint == "dial_action":
			event.kind = "ended"
			status = (payload.get("DialCallStatus") or "").lower()
			event.status = DIAL_STATUS.get(status, "Completed" if status else "")
			event.duration = _int(payload.get("DialCallDuration"))
			event.answered_destination = payload.get("DialCallTo") or ""
			event.hangup_cause = status
			event.failure_reason = _reason(payload)
			if payload.get("RecordingUrl"):
				event.recording_url = payload.get("RecordingUrl") or ""
				event.recording_id = payload.get("RecordingSid") or ""
			return event

		if kind_hint == "hangup":
			event.kind = "ended"
			status = (payload.get("CallStatus") or "").lower()
			event.status = CALL_STATUS.get(status, "Completed" if status else "")
			event.duration = _int(payload.get("CallDuration"))
			event.bill_duration = _int(payload.get("CallDuration"))
			event.cost = _float(payload.get("CallPrice") or payload.get("Price"))
			event.hangup_cause = status
			event.failure_reason = _reason(payload)
			return event

		if kind_hint == "recording":
			event.kind = "recording_ready"
			event.recording_id = payload.get("RecordingSid") or ""
			event.recording_url = payload.get("RecordingUrl") or ""
			event.recording_duration_ms = _int(payload.get("RecordingDuration")) * 1000
			return event

		return event

	def verify_webhook(self, url: str, method: str, headers: dict, form: dict) -> bool:
		"""True when this request really came from Twilio.

		Delegated to Twilio's own validator rather than reimplemented. The Plivo side learned that
		the hard way: the published description of its signature omits a detail, and a hand-rolled
		check that agrees with the misreading passes its own tests while rejecting every real
		webhook.
		"""
		signature = _header(headers, "X-Twilio-Signature")
		if not signature:
			return False
		token = self.auth_token
		if not token:
			return False
		try:
			from twilio.request_validator import RequestValidator

			return bool(RequestValidator(token).validate(url, dict(form or {}), signature))
		except Exception as exc:
			frappe.log_error(
				title="Excom Voice: Twilio signature check errored", message=str(exc)
			)
			return False

	# ── the softphone ─────────────────────────────────────────────────────────

	def provision_endpoint(self, user: str, alias: str) -> EndpointRef:
		"""There is nothing to create.

		A Twilio client identity is whatever string the access token carries, so provisioning is a
		naming decision rather than a remote resource. Returned in the same shape as Plivo's so the
		`Excom Voice Endpoint` record and everything reading it stay identical — the password is
		empty because no password exists to leak.
		"""
		identity = client_identity(user)
		return EndpointRef(
			endpoint_id=identity,
			username=identity,
			password="",
			sip_uri=f"client:{identity}",
			alias=alias,
			raw={"identity": identity},
		)

	def find_endpoint(self, alias: str) -> EndpointRef | None:
		# Nothing is stored at Twilio, so there is nothing to find. Callers treat None as "create
		# it", and creating it costs nothing.
		return None

	def deprovision_endpoint(self, endpoint_id: str) -> None:
		# Likewise nothing to delete. Removing the Excom Voice Endpoint row is what takes the agent
		# off the line, because the token is minted from that row.
		return None

	def mint_access_token(self, endpoint_username: str, ttl_seconds: int) -> str:
		"""A short-lived credential for this agent's browser.

		Signed here with the API key, not fetched from Twilio — one less round trip on the path
		between an agent opening Excom and being reachable.
		"""
		if not self.api_key_sid or not self.api_key_secret:
			frappe.throw(
				_(
					"This Twilio line has no API key. Add the API Key SID and Secret on the "
					"channel account — the account auth token cannot sign a browser token."
				)
			)
		if not self.app_sid:
			frappe.throw(_("This Twilio line has no TwiML application yet."))

		from twilio.jwt.access_token import AccessToken
		from twilio.jwt.access_token.grants import VoiceGrant

		token = AccessToken(
			self.account_sid,
			self.api_key_sid,
			self.api_key_secret,
			identity=endpoint_username,
			ttl=max(300, min(int(ttl_seconds or 3600), 86400)),
		)
		token.add_grant(
			VoiceGrant(outgoing_application_sid=self.app_sid, incoming_allow=True)
		)
		return token.to_jwt()

	def sdk_descriptor(self) -> dict[str, Any]:
		"""What the browser needs to boot the SDK. Contains no secret."""
		region = (self.account.get("voice_client_region") or "").strip()
		options: dict[str, Any] = {
			"logLevel": "error",
			# Opus first: it survives a bad line far better than PCMU, which matters most on the
			# international calls this line exists for.
			"codecPreferences": ["opus", "pcmu"],
		}
		if region:
			options["edge"] = _edge_for(region)
		return {"sdk": "@twilio/voice-sdk", "options": options}


# ── helpers ───────────────────────────────────────────────────────────────────


def identity_from_ref(ref: str) -> str:
	"""`client:excom_somil` -> `excom_somil`. Stored with the scheme so a glance at the row says
	which kind of destination it is."""
	value = str(ref or "")
	return value[len("client:") :] if value.startswith("client:") else value


def _with_params(url: str, params: dict) -> str:
	from urllib.parse import urlencode

	extras = {f"{PARAM_PREFIX}{k}": v for k, v in (params or {}).items() if v}
	if not extras:
		return url
	joiner = "&" if "?" in url else "?"
	return f"{url}{joiner}{urlencode(extras)}"


def _header(headers, name: str) -> str:
	"""Read a header without caring how it was capitalised — HTTP/2 lowercases them, and a plain
	dict copy loses the case-insensitivity Werkzeug's own header object has."""
	if headers is None:
		return ""
	direct = headers.get(name)
	if direct:
		return direct
	wanted = name.lower()
	try:
		for key, value in headers.items():
			if key.lower() == wanted:
				return value
			if key.upper() == "HTTP_" + name.upper().replace("-", "_"):
				return value
	except Exception:
		return ""
	return ""


def _reason(payload: dict) -> str:
	"""Twilio reports why a call failed as a numeric code, which means nothing to an agent."""
	code = str(payload.get("ErrorCode") or payload.get("DialCallErrorCode") or "").strip()
	return FAILURE_REASONS.get(code, "")


def _explain_refusal(resp) -> str:
	"""Turn a rejected Calls API response into a sentence worth reading."""
	try:
		body = resp.json() or {}
	except ValueError:
		body = {}
	code = str(body.get("code") or "")
	message = str(body.get("message") or "").strip()

	if code in FAILURE_REASONS:
		return FAILURE_REASONS[code]
	if resp.status_code == 401:
		return _("Twilio rejected the credentials on this voice line.")
	if resp.status_code == 402:
		return _("The Twilio account is out of credit.")
	if message:
		return _("Twilio refused the call: {0}").format(message)
	return _("Twilio refused the call ({0}).").format(resp.status_code)


def _edge_for(region: str) -> str:
	"""Our region names are the Plivo ones. Map them onto the nearest Twilio edge so a line does not
	route its media across the world."""
	return {
		"asia": "singapore",
		"south_asia": "singapore",
		"usa_east": "ashburn",
		"usa_west": "roaming",
		"europe": "dublin",
		"australia": "sydney",
		"south_america": "sao-paulo",
	}.get(region, "roaming")
