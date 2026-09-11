"""Provider registry.

One place that maps `Excom Channel Account.voice_provider` onto an adapter. Everything else asks
`for_account()` and never names a vendor — the same discipline `crm_gateway.py` applies to native
CRM doctypes, for the same reason.

Only Plivo is implemented. Exotel and Airtel IQ are declared so the Select field has somewhere to
point and a misconfigured line fails with a sentence rather than a KeyError.
"""

import frappe
from frappe import _

from excom.excom.channels.voice.providers.base import (  # noqa: F401  (re-exported)
	CallDecision,
	CallDetails,
	CallEvent,
	Destination,
	EndpointRef,
	ProviderCallRef,
	SoftphoneProvider,
	VoiceProvider,
)

PLIVO = "Plivo"
EXOTEL = "Exotel"
AIRTEL = "Airtel IQ"

IMPLEMENTED = (PLIVO,)


def for_account(account_doc) -> VoiceProvider:
	"""The adapter for this line. `account_doc` may be a name or a document."""
	if isinstance(account_doc, str):
		# Cached: this runs on the answer URL, which is on the critical path of a live call.
		account_doc = frappe.get_cached_doc("Excom Channel Account", account_doc)

	provider = (account_doc.get("voice_provider") or "").strip()
	if provider == PLIVO:
		from excom.excom.channels.voice.providers.plivo import PlivoAdapter

		return PlivoAdapter(account_doc)

	if not provider:
		frappe.throw(
			_("The voice line {0} has no provider set.").format(
				account_doc.get("account_name") or account_doc.name
			)
		)

	frappe.throw(
		_("{0} is not implemented yet. This line cannot place or receive calls.").format(provider)
	)


def is_softphone(provider: VoiceProvider) -> bool:
	return isinstance(provider, SoftphoneProvider)
