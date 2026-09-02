import requests
import frappe
from urllib.parse import urljoin
from .base import VoiceProvider, ProviderCallRef, CallDetails, CallEvent

class ExotelAdapter(VoiceProvider):
	"""
	Exotel Cloud Telephony Adapter implementing the VoiceProvider ABC.
	Uses HTTP Basic Auth (API Key : API Secret) in request headers — never in URL strings.
	"""
	def __init__(self, account_doc):
		super().__init__(account_doc)
		self.sid = account_doc.voice_account_sid or ""
		self.api_key = account_doc.voice_api_key or ""
		self.api_secret = account_doc.get_password("voice_api_secret") or ""
		self.api_base = account_doc.voice_api_base or "api.in.exotel.com"
		if not self.api_base.startswith("http"):
			self.base_url = f"https://{self.api_base}/v1/Accounts/{self.sid}/"
		else:
			self.base_url = f"{self.api_base.rstrip('/')}/v1/Accounts/{self.sid}/"

	def _get_auth(self):
		return (self.api_key, self.api_secret)

	def capabilities(self) -> set:
		return {"click_to_call", "parallel_ringing", "dual_channel_recording", "reconcile"}

	def initiate_call(self, from_number: str, to_number: str, caller_id: str, opts: dict = None) -> ProviderCallRef:
		"""
		POST /v1/Accounts/<sid>/Calls/connect.json
		From: Agent Mobile
		To: Customer Mobile
		CallerId: ExoPhone DID
		"""
		url = urljoin(self.base_url, "Calls/connect.json")
		opts = opts or {}
		
		# Build status callback URL if available
		site_url = frappe.utils.get_url()
		token = self.account.voice_webhook_token or ""
		callback_url = f"{site_url}/api/method/excom.excom.api.voice.status_webhook?token={token}&account={self.account.name}"

		payload = {
			"From": from_number,
			"To": to_number,
			"CallerId": caller_id or self.account.voice_phone_number,
			"StatusCallback": callback_url,
			"Record": "true" if self.account.voice_record_calls in ["All", "Outbound only"] else "false",
		}
		if self.account.voice_recording_channels == "Dual":
			payload["RecordingChannels"] = "dual"

		resp = requests.post(url, data=payload, auth=self._get_auth(), timeout=10)
		if not resp.ok:
			frappe.throw(f"Exotel Call Initiation Failed [{resp.status_code}]: {resp.text}")

		data = resp.json().get("Call", {})
		call_sid = data.get("Sid") or data.get("sid") or ""
		status = data.get("Status") or "in-progress"

		return ProviderCallRef(
			provider_call_id=call_sid,
			status=status,
			from_number=from_number,
			to_number=to_number,
			caller_id=caller_id,
			raw_response=data
		)

	def hangup(self, provider_call_id: str) -> None:
		url = urljoin(self.base_url, f"Calls/{provider_call_id}.json")
		try:
			requests.post(url, data={"Status": "completed"}, auth=self._get_auth(), timeout=5)
		except Exception as e:
			frappe.log_error(f"Error hanging up call {provider_call_id}: {e}", "Excom Calls")

	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		"""
		GET /v1/Accounts/<sid>/Calls/<CallSid>.json
		Fetches reconciled duration, talk_time, cost, and recording URL.
		"""
		url = urljoin(self.base_url, f"Calls/{provider_call_id}.json")
		resp = requests.get(url, auth=self._get_auth(), timeout=10)
		if not resp.ok:
			frappe.log_error(f"Failed to fetch call details for {provider_call_id}: {resp.status_code} {resp.text}", "Excom Calls Reconcile")
			return CallDetails(provider_call_id=provider_call_id, status="unknown")

		data = resp.json().get("Call", {})
		duration = int(data.get("Duration") or 0)
		price = float(data.get("Price") or 0.0)
		recording_url = data.get("RecordingUrl")
		status = data.get("Status") or "completed"

		return CallDetails(
			provider_call_id=provider_call_id,
			status=status,
			duration=duration,
			talk_time=duration,
			cost=price,
			recording_url=recording_url,
			start_time=data.get("StartTime"),
			end_time=data.get("EndTime"),
			raw_response=data
		)

	def fetch_recording_stream(self, recording_url: str):
		"""Stream audio bytes authenticated via Basic Auth."""
		return requests.get(recording_url, auth=self._get_auth(), stream=True, timeout=15)

	def normalize_event(self, raw_payload: dict) -> CallEvent:
		call_sid = raw_payload.get("CallSid") or raw_payload.get("Sid") or ""
		from_no = raw_payload.get("CallFrom") or raw_payload.get("From") or ""
		to_no = raw_payload.get("CallTo") or raw_payload.get("To") or ""
		business_no = raw_payload.get("DialWhomNumber") or to_no
		status = raw_payload.get("DialCallStatus") or raw_payload.get("Status") or "completed"
		duration = int(raw_payload.get("DialCallDuration") or raw_payload.get("Duration") or 0)
		recording = raw_payload.get("RecordingUrl")
		digits = raw_payload.get("digits") or ""
		if digits.startswith('"') and digits.endswith('"'):
			digits = digits[1:-1]

		return CallEvent(
			provider_call_id=call_sid,
			event_type=status.lower(),
			from_number=from_no,
			to_number=to_no,
			business_number=business_no,
			duration=duration,
			recording_url=recording,
			digits=digits,
			raw_payload=raw_payload
		)