"""Provider adapter contract.

Each ad platform is one AdsProvider subclass. It declares, per operation, whether the platform
supports it, why not if it does not, which scopes it needs and what it touches. Callers check the
declaration before calling, and the UI greys out anything unsupported with the stated reason.
"""

from dataclasses import dataclass, field

# Effects an operation can have. Anything that mutates the platform or spends money only ever runs
# through an approved Action Request (release 2); release 1 declares reads only.
READS = "reads"
MUTATES_PLATFORM = "mutates_platform"
SPENDS_MONEY = "spends_money"
SENDS_MESSAGE = "sends_message"


@dataclass(frozen=True)
class Capability:
	supported: bool
	effects: tuple = (READS,)
	scopes: tuple = ()
	reason: str = ""


# Error classes. Retryable and Rate Limited are tried again by the next scheduled run; the others
# need a person (new token, more permissions, fixed configuration) and are shown on the account.
RETRYABLE = "Retryable"
RATE_LIMITED = "Rate Limited"
PERMISSION = "Permission"
TOKEN = "Token"
INVALID = "Invalid"
UNKNOWN = "Unknown"


class AdsProviderError(Exception):
	def __init__(self, message: str, error_class: str = UNKNOWN, retry_after: int | None = None, code=None):
		super().__init__(message)
		self.error_class = error_class
		self.retry_after = retry_after
		self.code = code

	@property
	def needs_person(self) -> bool:
		return self.error_class in (PERMISSION, TOKEN, INVALID)


@dataclass
class CallStats:
	api_calls: int = 0
	usage: dict = field(default_factory=dict)


class AdsProvider:
	provider = ""
	CAPABILITIES: dict[str, Capability] = {}

	def __init__(self, account):
		self.account = account
		self.stats = CallStats()

	@classmethod
	def capability(cls, operation: str) -> Capability:
		return cls.CAPABILITIES.get(operation) or Capability(False, reason="Not declared by this provider")

	def require(self, operation: str) -> None:
		cap = self.capability(operation)
		if not cap.supported:
			raise AdsProviderError(f"{self.provider}: {operation} is not supported. {cap.reason}".strip(), INVALID)

	# Read operations every provider implements. Each returns plain, normalised dicts.
	def read_account(self) -> dict:
		raise NotImplementedError

	def list_campaigns(self) -> list[dict]:
		raise NotImplementedError

	def list_ad_groups(self) -> list[dict]:
		raise NotImplementedError

	def list_ads(self) -> list[dict]:
		raise NotImplementedError

	def daily_metrics(self, level: str, since, until) -> list[dict]:
		raise NotImplementedError


# provider name -> "module:Class". Imported lazily so a provider's dependencies load only when used.
PROVIDERS = {
	"Meta": "excom.excom_ads.providers.meta:MetaAdsProvider",
	"Google": "excom.excom_ads.providers.google_ads:GoogleAdsProvider",
}


def provider_class(provider: str) -> type:
	path = PROVIDERS.get(provider)
	if not path:
		raise AdsProviderError(f"Provider {provider} is not available yet", INVALID)
	module, cls = path.split(":")
	import importlib

	return getattr(importlib.import_module(module), cls)


def get_provider(account) -> AdsProvider:
	return provider_class(account.provider)(account)
