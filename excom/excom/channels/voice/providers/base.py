from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class ProviderCallRef:
	provider_call_id: str
	status: str
	from_number: str
	to_number: str
	caller_id: str
	raw_response: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CallDetails:
	provider_call_id: str
	status: str
	duration: int = 0
	talk_time: int = 0
	cost: float = 0.0
	recording_url: Optional[str] = None
	start_time: Optional[str] = None
	end_time: Optional[str] = None
	raw_response: Dict[str, Any] = field(default_factory=dict)

@dataclass
class CallEvent:
	provider_call_id: str
	event_type: str  # ringing, connected, completed, failed, busy, no-answer
	from_number: str
	to_number: str
	business_number: str
	duration: int = 0
	recording_url: Optional[str] = None
	digits: Optional[str] = None
	raw_payload: Dict[str, Any] = field(default_factory=dict)

class VoiceProvider(ABC):
	def __init__(self, account_doc):
		self.account = account_doc

	@abstractmethod
	def initiate_call(self, from_number: str, to_number: str, caller_id: str, opts: Optional[Dict[str, Any]] = None) -> ProviderCallRef:
		"""Initiate a 2-leg outbound PSTN call (rings from_number, then bridges to to_number)."""
		pass

	@abstractmethod
	def hangup(self, provider_call_id: str) -> None:
		"""Terminate an active call."""
		pass

	@abstractmethod
	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		"""Query the provider Call Details API for reconciliation of duration and pricing."""
		pass

	@abstractmethod
	def fetch_recording_stream(self, recording_url: str):
		"""Stream recording bytes authenticated against provider Basic Auth."""
		pass

	@abstractmethod
	def normalize_event(self, raw_payload: Dict[str, Any]) -> CallEvent:
		"""Map vendor-specific webhook payload into unified CallEvent."""
		pass

	@abstractmethod
	def capabilities(self) -> set:
		"""Return provider capability flags (e.g. {'click_to_call', 'parallel_ringing', 'dual_channel'})."""
		pass