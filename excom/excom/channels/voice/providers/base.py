"""The voice provider contract.

Two interfaces, because browser calling is a capability and not a given. Every provider implements
`VoiceProvider`; only a provider with a real WebRTC SDK implements `SoftphoneProvider`.

Everything above this layer speaks `CallDecision` and `CallEvent` and never names a vendor. The
only provider-shaped thing in the whole channel is `render_decision()`, which turns one decision
into whatever call-control document that vendor wants back on the answer URL.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

# Capability flags. The frontend greys out what the account's provider cannot do rather than
# throwing when somebody presses it.
CAP_WEBRTC = "webrtc"
CAP_CLICK_TO_CALL = "click_to_call"
CAP_PARALLEL_RING = "parallel_ring"
CAP_MIXED_PARALLEL = "mixed_parallel"  # a SIP endpoint and a phone number in the same dial
CAP_SIGNED_WEBHOOKS = "signed_webhooks"
CAP_DUAL_CHANNEL_RECORDING = "dual_channel_recording"
CAP_TRANSFER = "transfer"
CAP_HANGUP = "hangup"


@dataclass(frozen=True)
class Destination:
	"""One thing that can ring.

	`kind` is "sip" for a browser endpoint and "pstn" for a phone number. `user` is the Excom user
	behind it, which is what lets us say "picked up by Priya" without matching phone numbers back
	to people afterwards — we already know who we rang.
	"""

	kind: str
	ref: str
	user: str = ""
	label: str = ""

	@property
	def is_browser(self) -> bool:
		return self.kind == "sip"


@dataclass
class CallDecision:
	"""What Excom decided should happen, before any vendor is involved.

	Produced by routing.py, identical whatever the provider. `ring_set` is the list of users to pop
	a screen for — deliberately users, not destinations, because one agent may be rung on two.
	"""

	destinations: list[Destination] = field(default_factory=list)
	ring_set: list[str] = field(default_factory=list)
	parallel: bool = True
	ring_seconds: int = 25
	record: bool = False
	record_channels: str = "stereo"
	consent_prompt: str = ""
	caller_id: str = ""
	max_conversation_seconds: int = 3600
	# Set when the decision is empty and the caller needs to hear something before we log a miss.
	no_answer_message: str = ""

	@property
	def is_empty(self) -> bool:
		return not self.destinations


@dataclass
class ProviderCallRef:
	"""What came back from asking a provider to place a call."""

	provider_call_id: str
	status: str = ""
	from_number: str = ""
	to_number: str = ""
	raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallDetails:
	"""A call detail record, read back for reconciliation."""

	provider_call_id: str
	status: str = ""
	duration: int = 0
	bill_duration: int = 0
	cost: float = 0.0
	hangup_cause: str = ""
	start_time: str | None = None
	end_time: str | None = None
	found: bool = False
	raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallEvent:
	"""A vendor webhook, normalised.

	`kind` is one of: ringing, answered, ended, recording_ready. Anything a vendor sends that does
	not map onto one of those is `unknown` and gets logged rather than acted on.
	"""

	kind: str
	provider_call_id: str
	from_number: str = ""
	to_number: str = ""
	business_number: str = ""
	direction: str = ""
	status: str = ""
	duration: int = 0
	bill_duration: int = 0
	cost: float = 0.0
	hangup_cause: str = ""
	hangup_source: str = ""
	#: Why the call failed, in a sentence an agent can act on. Only the adapter can write this:
	#: `hangup_cause` is the vendor's own vocabulary ("Destination Country Barred"), and turning
	#: that into advice needs to know what the vendor means by it.
	failure_reason: str = ""
	answered_destination: str = ""
	digits: str = ""
	recording_id: str = ""
	recording_url: str = ""
	recording_duration_ms: int = 0
	sip_headers: dict[str, str] = field(default_factory=dict)
	raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EndpointRef:
	"""A provisioned SIP identity for one agent."""

	endpoint_id: str
	username: str
	password: str
	sip_uri: str
	alias: str = ""
	raw: dict[str, Any] = field(default_factory=dict)


class VoiceProvider(ABC):
	"""PSTN call control. Every provider implements this."""

	name: str = ""

	def __init__(self, account_doc):
		self.account = account_doc

	@abstractmethod
	def capabilities(self) -> set[str]:
		"""What this provider can actually do. The UI reads this, so it must be honest."""

	@abstractmethod
	def render_decision(self, decision: CallDecision) -> str:
		"""Turn a decision into the call-control document this vendor expects on the answer URL."""

	@abstractmethod
	def initiate_call(
		self, to: Destination, caller_id: str, opts: dict[str, Any] | None = None
	) -> ProviderCallRef:
		"""Place a call from the provider's side. Used by the Phone transport."""

	@abstractmethod
	def hangup(self, provider_call_id: str) -> None:
		"""End a live call."""

	@abstractmethod
	def fetch_call_details(self, provider_call_id: str) -> CallDetails:
		"""Read the call detail record back, for reconciliation."""

	@abstractmethod
	def fetch_recording_stream(self, call_doc) -> tuple[Iterator[bytes], str]:
		"""Return (byte iterator, content type). Credentials stay on this side of the wire."""

	@abstractmethod
	def normalize_event(self, kind_hint: str, payload: dict[str, Any]) -> CallEvent:
		"""Map a vendor webhook onto a CallEvent."""

	@abstractmethod
	def verify_webhook(self, url: str, method: str, headers: dict, form: dict) -> bool:
		"""True when this request really came from the provider."""

	def supports(self, capability: str) -> bool:
		return capability in self.capabilities()


class SoftphoneProvider(ABC):
	"""Browser calling. Implemented only where a real WebRTC SDK exists."""

	@abstractmethod
	def provision_endpoint(self, user: str, alias: str) -> EndpointRef:
		"""Create a SIP identity. Must be safe to call twice for the same agent."""

	def find_endpoint(self, alias: str) -> EndpointRef | None:
		"""An endpoint this alias already owns at the provider, if there is one.

		Creating an endpoint is a call to somebody else's system; the row recording it is a local
		transaction. Those two can come apart — a rollback after a successful create leaves an
		orphan that a naive retry would duplicate. Providers that can look one up override this;
		the default is "no idea", which is only ever a missed optimisation.
		"""
		return None

	@abstractmethod
	def deprovision_endpoint(self, endpoint_id: str) -> None:
		"""Remove it. Called when an agent leaves the line."""

	@abstractmethod
	def mint_access_token(self, endpoint_username: str, ttl_seconds: int) -> str:
		"""A short-lived credential the browser may hold. Never the SIP password."""

	@abstractmethod
	def sdk_descriptor(self) -> dict[str, Any]:
		"""Everything the browser needs to boot the SDK, minus secrets."""
