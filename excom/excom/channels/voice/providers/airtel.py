from .base import VoiceProvider, ProviderCallRef, CallDetails, CallEvent

class AirtelIQAdapter(VoiceProvider):
	"""
	Airtel IQ Telephony Adapter (Modular Call Flow / Session-based).
	Stub ready for 1-file provider switch.
	"""
	def capabilities(self) -> set:
		return {"click_to_call", "session_flow", "reconcile"}

	def initiate_call(self, from_number: str, to_number: str, caller_id: str, opts: dict = None) -> ProviderCallRef:
		raise NotImplementedError("Airtel IQ adapter will be configured when switching providers.")

	def hangup(self, provider_call_id: str) -> None:
		raise NotImplementedError("Airtel IQ adapter hangup not implemented.")

	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		raise NotImplementedError("Airtel IQ fetch_call_details not implemented.")

	def fetch_recording_stream(self, recording_url: str):
		raise NotImplementedError("Airtel IQ fetch_recording_stream not implemented.")

	def normalize_event(self, raw_payload: dict) -> CallEvent:
		raise NotImplementedError("Airtel IQ normalize_event not implemented.")