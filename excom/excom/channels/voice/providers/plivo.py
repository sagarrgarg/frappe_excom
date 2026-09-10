"""Plivo adapter — the only module in the channel that knows what Plivo is.

Chosen because it is the only vendor with a browser SDK on public npm, webhooks signed with a real
HMAC, and the ability to ring a WebRTC endpoint and a phone number in the same parallel dial.

Two things here are load-bearing and easy to get wrong:

1. Credentials go in an ``auth=`` tuple, never built into the URL. Frappe CRM's telephony code
   builds ``https://id:token@host/...`` and leaks the token into every traceback and access log.
2. The call-control XML is built with ElementTree, not string formatting. A caller id is attacker-
   controlled input; concatenating it into markup is how you get an injected ``<Dial>``.
"""

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
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

DEFAULT_API_BASE = "https://api.plivo.com"
SIP_DOMAIN = "phone.plivo.com"
HTTP_TIMEOUT = 15

# Plivo forwards custom SIP headers as form params under this prefix. We ride the thread id in on
# an outbound browser leg this way, because that leg is created by the browser and we have no call
# uuid to correlate on until the answer URL fires.
SIP_HEADER_PREFIX = "X-PH-"

# DialStatus (action URL) -> Excom Call.status
DIAL_STATUS = {
	"completed": "Completed",
	"busy": "Busy",
	"no-answer": "No Answer",
	"timeout": "No Answer",
	"cancel": "Canceled",
	"canceled": "Canceled",
	"cancelled": "Canceled",
	"failed": "Failed",
}

# CallStatus (hangup URL) -> Excom Call.status
CALL_STATUS = {
	"completed": "Completed",
	"busy": "Busy",
	"no-answer": "No Answer",
	"failed": "Failed",
	"cancel": "Canceled",
	"timeout": "No Answer",
}


def _b(value: bool) -> str:
	"""Plivo wants lowercase string booleans in XML attributes."""
	return "true" if value else "false"


class PlivoAdapter(VoiceProvider, SoftphoneProvider):
	name = "Plivo"

	def __init__(self, account_doc):
		super().__init__(account_doc)
		self.auth_id = (account_doc.get("voice_auth_id") or "").strip()
		base = (account_doc.get("voice_api_base") or "").strip() or DEFAULT_API_BASE
		if not base.startswith("http"):
			base = f"https://{base}"
		self.api_base = base.rstrip("/")
		self.app_id = (account_doc.get("voice_app_id") or "").strip()

	# ── credentials ───────────────────────────────────────────────────────────

	@property
	def auth_token(self) -> str:
		"""Read lazily so merely constructing an adapter does not decrypt a secret."""
		try:
			return self.account.get_password("voice_auth_token", raise_exception=False) or ""
		except Exception:
			return ""

	def _auth(self) -> tuple[str, str]:
		return (self.auth_id, self.auth_token)

	def _url(self, path: str) -> str:
		return f"{self.api_base}/v1/Account/{quote(self.auth_id, safe='')}/{path}"

	def _request(self, method: str, path: str, **kwargs):
		import requests

		if not self.auth_id or not self.auth_token:
			frappe.throw(_("This voice line has no Plivo credentials configured."))
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
		# Browser calling is a per-line switch as well as a provider capability: a line may be
		# deliberately PSTN-only while the regulatory position is being settled.
		if self.account.get("voice_allow_browser_calls") and self.app_id:
			caps.add(CAP_WEBRTC)
		return caps

	# ── call control ──────────────────────────────────────────────────────────

	def render_decision(self, decision: CallDecision) -> str:
		"""One decision in, one PlivoXML document out.

		This is the only vendor-shaped part of routing. Everything that reaches it has already been
		decided; nothing here queries the database.
		"""
		response = ET.Element("Response")

		if decision.is_empty:
			message = decision.no_answer_message or _(
				"Sorry, nobody is available to take your call right now. Please try again later."
			)
			speak = ET.SubElement(response, "Speak")
			speak.text = message
			ET.SubElement(response, "Hangup")
			return self._serialise(response)

		if decision.consent_prompt:
			play = ET.SubElement(response, "Play")
			play.text = decision.consent_prompt

		if decision.record:
			ET.SubElement(
				response,
				"Record",
				{
					"recordSession": "true",
					"startOnDialAnswer": "true",
					"redirect": "false",
					"fileFormat": "mp3",
					"recordChannelType": decision.record_channels or "stereo",
					"callbackUrl": webhook_url("recording_ready", self.account.name),
					"callbackMethod": "POST",
				},
			)

		dial_attrs = {
			"timeout": str(decision.ring_seconds),
			"timeLimit": str(decision.max_conversation_seconds),
			"action": webhook_url("dial_action", self.account.name),
			"method": "POST",
			"callbackUrl": webhook_url("dial_event", self.account.name),
			"callbackMethod": "POST",
			"redirect": "false",
			"hangupOnStar": "false",
		}
		if decision.caller_id:
			dial_attrs["callerId"] = decision.caller_id
		dial = ET.SubElement(response, "Dial", dial_attrs)

		for dest in decision.destinations:
			if dest.is_browser:
				node = ET.SubElement(dial, "User")
			else:
				node = ET.SubElement(dial, "Number")
			node.text = dest.ref

		return self._serialise(response)

	@staticmethod
	def _serialise(root: ET.Element) -> str:
		return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
			root, encoding="unicode"
		)

	def initiate_call(
		self, to: Destination, caller_id: str, opts: dict[str, Any] | None = None
	) -> ProviderCallRef:
		"""Place a call from Plivo's side. This is the Phone transport: the agent's own mobile is
		the ``from``, so their handset rings first and Plivo bridges to the customer when they
		answer."""
		opts = opts or {}
		from_number = opts.get("from_number") or ""
		if not from_number:
			frappe.throw(_("No number to ring the agent on."))

		payload = {
			"from": caller_id or from_number,
			"to": from_number,
			"answer_url": opts.get("answer_url") or webhook_url("route", self.account.name),
			"answer_method": "POST",
			"hangup_url": webhook_url("hangup", self.account.name),
			"hangup_method": "POST",
			"ring_timeout": str(opts.get("ring_seconds") or 30),
			"time_limit": str(opts.get("max_conversation_seconds") or 3600),
		}
		fallback = webhook_url("fallback", self.account.name)
		if fallback:
			payload["fallback_url"] = fallback
		# The customer number and thread ride along as SIP headers so the answer URL, which fires
		# after the agent picks up, knows who to dial and which conversation this belongs to.
		headers = {f"{SIP_HEADER_PREFIX}to": to.ref}
		for key, value in (opts.get("sip_headers") or {}).items():
			headers[f"{SIP_HEADER_PREFIX}{key}"] = str(value)
		payload["sip_headers"] = ",".join(f"{k}={v}" for k, v in headers.items())

		resp = self._request("POST", "Call/", json=payload)
		if resp.status_code not in (200, 201, 202):
			frappe.throw(
				_("Plivo refused the call ({0}): {1}").format(resp.status_code, resp.text[:400])
			)
		data = resp.json() if resp.content else {}
		return ProviderCallRef(
			provider_call_id=data.get("request_uuid") or "",
			status="Ringing",
			from_number=from_number,
			to_number=to.ref,
			raw=data,
		)

	def hangup(self, provider_call_id: str) -> None:
		if not provider_call_id:
			return
		try:
			self._request("DELETE", f"Call/{quote(provider_call_id, safe='')}/")
		except Exception as exc:
			frappe.log_error(
				f"Plivo hangup failed for {provider_call_id}: {exc}", "Excom Voice"
			)

	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		if not provider_call_id:
			return CallDetails(provider_call_id="", found=False)
		try:
			resp = self._request("GET", f"Call/{quote(provider_call_id, safe='')}/")
		except Exception as exc:
			frappe.log_error(
				f"Plivo CDR fetch failed for {provider_call_id}: {exc}", "Excom Voice Reconcile"
			)
			return CallDetails(provider_call_id=provider_call_id, found=False)

		if resp.status_code == 404:
			# Not yet in the CDR store. The reconcile sweep will try again.
			return CallDetails(provider_call_id=provider_call_id, found=False)
		if not resp.ok:
			return CallDetails(provider_call_id=provider_call_id, found=False)

		data = resp.json() if resp.content else {}
		return CallDetails(
			provider_call_id=provider_call_id,
			status=data.get("call_state") or "",
			duration=_int(data.get("call_duration")),
			bill_duration=_int(data.get("bill_duration")),
			cost=_float(data.get("total_amount")),
			hangup_cause=data.get("hangup_cause_name") or "",
			start_time=data.get("initiation_time"),
			end_time=data.get("end_time"),
			found=True,
			raw=data,
		)

	def fetch_recording_stream(self, call_doc) -> tuple[Iterator[bytes], str]:
		"""Stream the recording through us, so the provider URL and its credentials never reach a
		browser. Plivo recordings are public URLs until Basic Auth is switched on in Voice
		Settings — we send the auth either way, which is harmless when it is not required."""
		import requests

		url = call_doc.recording_url
		if not url:
			frappe.throw(_("This call has no recording."))
		resp = requests.get(url, auth=self._auth(), stream=True, timeout=HTTP_TIMEOUT)
		if not resp.ok:
			frappe.throw(
				_("The recording could not be fetched from the provider ({0}).").format(
					resp.status_code
				)
			)
		content_type = resp.headers.get("Content-Type") or "audio/mpeg"
		return resp.iter_content(chunk_size=64 * 1024), content_type

	# ── webhooks ──────────────────────────────────────────────────────────────

	def normalize_event(self, kind_hint: str, payload: dict[str, Any]) -> CallEvent:
		"""Map a Plivo webhook onto a CallEvent.

		Plivo gives each lifecycle moment its own URL, so we are told what kind of event this is
		rather than having to guess from the payload — which is what made the Exotel handler
		fragile enough to need patching twice.
		"""
		call_uuid = (
			payload.get("CallUUID")
			or payload.get("DialALegUUID")
			or payload.get("RequestUUID")
			or ""
		)
		sip_headers = {
			key[len(SIP_HEADER_PREFIX) :].lower(): value
			for key, value in payload.items()
			if key.startswith(SIP_HEADER_PREFIX)
		}

		event = CallEvent(
			kind="unknown",
			provider_call_id=call_uuid,
			from_number=payload.get("From") or payload.get("CallerName") or "",
			to_number=payload.get("To") or "",
			direction=(payload.get("Direction") or "").lower(),
			digits=payload.get("Digits") or payload.get("DialDigitsMatch") or "",
			sip_headers=sip_headers,
			raw=payload,
		)

		if kind_hint == "ringing":
			event.kind = "ringing"
			return event

		if kind_hint == "dial_event":
			action = (payload.get("DialAction") or "").lower()
			if action in ("answer", "connected"):
				event.kind = "answered"
				event.answered_destination = payload.get("DialBLegTo") or ""
			elif action == "hangup":
				event.kind = "ended"
				event.duration = _int(payload.get("DialBLegDuration"))
				event.hangup_cause = payload.get("DialBLegHangupCauseName") or ""
			return event

		if kind_hint == "dial_action":
			event.kind = "ended"
			raw_status = (payload.get("DialStatus") or "").lower()
			event.status = DIAL_STATUS.get(raw_status, "Completed" if raw_status else "")
			event.duration = _int(payload.get("DialBLegDuration") or payload.get("Duration"))
			event.answered_destination = payload.get("DialBLegTo") or ""
			event.hangup_cause = payload.get("DialHangupCause") or ""
			return event

		if kind_hint == "hangup":
			event.kind = "ended"
			raw_status = (payload.get("CallStatus") or "").lower()
			event.status = CALL_STATUS.get(raw_status, "Completed" if raw_status else "")
			event.duration = _int(payload.get("Duration"))
			event.bill_duration = _int(payload.get("BillDuration"))
			event.cost = _float(payload.get("TotalCost"))
			event.hangup_cause = payload.get("HangupCauseName") or ""
			event.hangup_source = payload.get("HangupSource") or ""
			return event

		if kind_hint == "recording":
			event.kind = "recording_ready"
			event.recording_id = payload.get("RecordingID") or ""
			event.recording_url = payload.get("RecordUrl") or ""
			ms = _int(payload.get("RecordingDurationMs"))
			if ms <= 0:
				ms = _int(payload.get("RecordingDuration")) * 1000
			# Session recordings report -1 until the final callback; treat that as "not yet".
			event.recording_duration_ms = max(ms, 0)
			return event

		return event

	def verify_webhook(self, url: str, method: str, headers: dict, form: dict) -> bool:
		"""Plivo signature V3: HMAC-SHA256 over the full URL, the POST params sorted by name, and a
		per-request nonce, keyed with the auth token.

		The SDK's own validator is authoritative, so it is used when importable. The local
		implementation below exists so a missing SDK degrades to "verify it ourselves" rather than
		"accept anything".
		"""
		signature = headers.get("X-Plivo-Signature-V3") or headers.get(
			"HTTP_X_PLIVO_SIGNATURE_V3"
		)
		nonce = headers.get("X-Plivo-Signature-V3-Nonce") or headers.get(
			"HTTP_X_PLIVO_SIGNATURE_V3_NONCE"
		)
		token = self.auth_token
		if not (signature and nonce and token):
			return False

		try:
			from plivo.utils import validate_v3_signature

			return bool(
				validate_v3_signature(method.upper(), url, nonce, token, signature, form or {})
			)
		except ImportError:
			pass
		except Exception as exc:
			frappe.log_error(f"Plivo signature check errored: {exc}", "Excom Voice")
			return False

		return self._verify_v3_locally(url, method, nonce, token, signature, form)

	@staticmethod
	def _verify_v3_locally(
		url: str, method: str, nonce: str, token: str, signature: str, form: dict
	) -> bool:
		base = url
		if method.upper() == "POST" and form:
			base += "".join(f"{k}{form[k]}" for k in sorted(form))
		digest = hmac.new(
			token.encode("utf-8"), (base + nonce).encode("utf-8"), hashlib.sha256
		).digest()
		expected = base64.b64encode(digest).decode("utf-8")
		# Plivo may send several comma-separated signatures; any one matching is enough.
		return any(
			hmac.compare_digest(expected, candidate.strip())
			for candidate in signature.split(",")
			if candidate.strip()
		)

	# ── softphone ─────────────────────────────────────────────────────────────

	def provision_endpoint(self, user: str, alias: str) -> EndpointRef:
		"""Create a SIP identity for one agent.

		Plivo appends a 12-digit suffix of its own to whatever username we ask for, so the returned
		username is the one that matters — we never reconstruct it locally.
		"""
		username = _endpoint_username(user)
		password = frappe.generate_hash(length=24)
		payload = {"username": username, "password": password, "alias": alias}
		if self.app_id:
			payload["app_id"] = self.app_id

		resp = self._request("POST", "Endpoint/", json=payload)
		if resp.status_code not in (200, 201, 202):
			frappe.throw(
				_("Plivo refused to create a voice endpoint ({0}): {1}").format(
					resp.status_code, resp.text[:400]
				)
			)
		data = resp.json() if resp.content else {}
		returned_username = data.get("username") or username
		return EndpointRef(
			endpoint_id=data.get("endpoint_id") or "",
			username=returned_username,
			password=password,
			sip_uri=f"sip:{returned_username}@{SIP_DOMAIN}",
			alias=alias,
			raw=data,
		)

	def deprovision_endpoint(self, endpoint_id: str) -> None:
		if not endpoint_id:
			return
		try:
			self._request("DELETE", f"Endpoint/{quote(endpoint_id, safe='')}/")
		except Exception as exc:
			frappe.log_error(
				f"Plivo endpoint delete failed for {endpoint_id}: {exc}", "Excom Voice"
			)

	def mint_access_token(self, endpoint_username: str, ttl_seconds: int) -> str:
		"""Ask Plivo for a short-lived login token for this endpoint.

		Plivo signs it, so we never hold or guess a signing scheme. This is what the browser gets;
		the SIP password stays on the server.
		"""
		import time

		now = int(time.time())
		ttl = max(180, min(int(ttl_seconds), 24 * 3600))  # Plivo allows 3 minutes to 24 hours
		payload = {"sub": endpoint_username, "nbf": now - 30, "exp": now + ttl}
		resp = self._request("POST", "JWT/Token/", json=payload)
		if not resp.ok:
			frappe.throw(
				_("Plivo refused to issue a softphone token ({0}): {1}").format(
					resp.status_code, resp.text[:400]
				)
			)
		data = resp.json() if resp.content else {}
		token = data.get("token") or data.get("jwt") or ""
		if not token:
			frappe.throw(_("Plivo returned no softphone token."))
		return token

	def sdk_descriptor(self) -> dict[str, Any]:
		"""What the browser needs to boot the SDK. Contains no secret."""
		return {
			"sdk": "plivo-browser-sdk",
			"options": {
				"debug": "ERROR",
				"permOnClick": True,
				"enableTracking": True,
				"closeProtection": True,
				"clientRegion": self.account.get("voice_client_region") or "asia",
				"enableNoiseReduction": True,
			},
		}


# ── helpers ───────────────────────────────────────────────────────────────────


def _int(value) -> int:
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return 0


def _float(value) -> float:
	try:
		return float(value)
	except (TypeError, ValueError):
		return 0.0


def _endpoint_username(user: str) -> str:
	"""Plivo wants 1-25 alphanumeric characters starting with a letter, and appends its own
	12-digit suffix. Keep it recognisable in the Plivo console without leaking a full email."""
	import re

	local = (user or "").split("@")[0]
	cleaned = re.sub(r"[^a-zA-Z0-9]", "", local).lower()[:11] or "agent"
	if not cleaned[0].isalpha():
		cleaned = f"a{cleaned}"
	return f"excom{cleaned}"


def webhook_url(kind: str, account: str) -> str:
	"""The public URL Plivo should call back on.

	The account rides in the query string so a site with several lines can tell them apart before
	it has parsed anything. It is not a credential — authenticity comes from the signature.
	"""
	site = frappe.utils.get_url().rstrip("/")
	return (
		f"{site}/api/method/excom.excom.api.voice.{kind}"
		f"?account={quote(account or '', safe='')}"
	)


def build_sip_headers(values: dict[str, Any]) -> str:
	"""Serialise custom SIP headers for the browser SDK's extraHeaders and the Calls API."""
	return ",".join(
		f"{SIP_HEADER_PREFIX}{key}={value}" for key, value in values.items() if value
	)
