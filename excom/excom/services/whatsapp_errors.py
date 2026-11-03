"""What to do about each WhatsApp Cloud API error code.

Meta asks integrators to act on `code` (and `error_data.details`), not on titles, which are
being deprecated. Every sender in excom raises WhatsAppAPIError carrying the code and one
of the classes below, so a broadcast, a notification and a single reply all react the same
way: back off, skip this person, stop the campaign, or stop the account.

Source: developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes (2026-11-03).
"""

from excom.excom.utils.errors import ExcomProviderError, ExcomRateLimitError, ExcomValidationError

RETRY = "retry"  # transient on Meta's side: try again shortly
RETRY_GLOBAL = "retry_global"  # throughput / API rate limit: back off every send on this account
RETRY_RECIPIENT = "retry_recipient"  # too many messages to this one person: back off them only
CAPPED = "capped"  # per-user marketing cap: never retry within 24 h
OPTED_OUT = "opted_out"  # the user stopped marketing messages: never retry
UNDELIVERABLE = "undeliverable"  # not on WhatsApp / old app / terms not accepted
WINDOW_CLOSED = "window_closed"  # 24 h customer service window closed: needs a template
RECIPIENT_TYPE = "recipient_type"  # this message type cannot go to a username/BSUID: use the phone
CATEGORY_MISMATCH = "category_mismatch"  # marketing template on the wrong endpoint
PAUSE_CAMPAIGN = "pause_campaign"  # template paused / portfolio pacing drop: stop this campaign
STOP_ACCOUNT = "stop_account"  # account restricted, locked, token or payment problem: stop all sends
PERMANENT = "permanent"  # bad request for this message: do not retry as is

_CODES = {
	RETRY: {1, 2, 131000, 131016, 131057},
	RETRY_GLOBAL: {4, 80007, 130429},
	RETRY_RECIPIENT: {131056},
	CAPPED: {131049},
	OPTED_OUT: {131050},
	UNDELIVERABLE: {131026},
	WINDOW_CLOSED: {131047},
	RECIPIENT_TYPE: {131062},
	CATEGORY_MISMATCH: {131063},
	PAUSE_CAMPAIGN: {132015, 132016},  # template paused for quality / pacing drop, template disabled
	STOP_ACCOUNT: {10, 102, 190, 200, 368, 131005, 131031, 131037, 131042, 131045, 131048, 133010},
	# 135000 is "unknown error with request parameters": a bad request, PERMANENT by default
}
_BY_CODE = {code: cls for cls, codes in _CODES.items() for code in codes}

# Classes that end every remaining send of a broadcast / bulk run.
CAMPAIGN_STOPPERS = {PAUSE_CAMPAIGN, STOP_ACCOUNT}


def classify(code) -> str:
	try:
		return _BY_CODE.get(int(code), PERMANENT)
	except (TypeError, ValueError):
		return PERMANENT


class WhatsAppAPIError(ExcomProviderError):
	"""A Cloud API call Meta refused, with the code and what to do about it."""

	title = "Excom WhatsApp API Error"

	def __init__(self, message: str = "", code=None, details: str = "", http_status: int = 0, retry_after: int = 0, error_class: str = ""):
		error_class = error_class or classify(code)
		# retry_after goes through super() so ExcomRateLimitError (in the MRO of the subclass) keeps it.
		super().__init__(message, provider="whatsapp", code=code, error_class=error_class, retry_after=retry_after)
		self.code = code
		self.details = details or ""
		self.http_status = http_status
		self.retry_after = retry_after
		self.error_class = error_class


class WhatsAppRateLimitError(WhatsAppAPIError, ExcomRateLimitError):
	"""Throughput / pair-rate / API-call limits. Still an ExcomRateLimitError for older callers."""

	http_status_code = 429


def raise_for_error(error: dict, http_status: int, account_name: str = ""):
	"""Raise the right error for a Graph `error` object."""
	code = error.get("code") or http_status
	details = (error.get("error_data") or {}).get("details") or error.get("error_user_msg") or ""
	message = error.get("message") or "Unknown error"
	cls = classify(code)
	if cls == PERMANENT and http_status >= 500:
		cls = RETRY  # Meta-side outage or a non-JSON reply: worth one more try later
	if cls == STOP_ACCOUNT and code in (102, 190, 10, 200):
		message = f"WhatsApp API auth error: {message}. Update the access token for {account_name}."
	else:
		message = f"WhatsApp API error ({code}): {message}" + (f" — {details}" if details and details not in message else "")
	kind = WhatsAppRateLimitError if (cls in (RETRY_GLOBAL, RETRY_RECIPIENT) or http_status == 429) else WhatsAppAPIError
	try:
		retry_after = int(error.get("retry_after") or 60)
	except (TypeError, ValueError):
		retry_after = 60
	raise kind(message, code=code, details=details, http_status=http_status, retry_after=retry_after, error_class=cls)


class ExcomConsentError(ExcomValidationError):
	"""A send refused before it reached Meta because the person opted out."""

	title = "Excom Consent Block"
