"""Meta Graph API version used by every WhatsApp / Messenger / Instagram call.

Meta retires a Graph version about two years after release: v21.0 stops working on
2027-01-21. v24.0 also changed WhatsApp behaviour excom now depends on: status webhooks
carry `pricing` instead of `conversation`, and messaging limits are read from
`whatsapp_business_manager_messaging_limit`. So any stored version below MIN_GRAPH_VERSION
is treated as the current default, not just the empty one.

Webhook payloads are a separate setting: they follow the version of the app's webhook
subscription (App Dashboard → Webhooks), not the version used for API calls. diagnose_whatsapp
reports subscribed fields older than v24.0.
"""

import re

DEFAULT_GRAPH_VERSION = "v26.0"
MIN_GRAPH_VERSION = (24, 0)

_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?$")


def parse_version(value) -> tuple[int, int] | None:
	"""'v26.0' / '26.0' / 'v26' → (26, 0); anything else → None."""
	m = _VERSION_RE.match((value or "").strip())
	if not m:
		return None
	return int(m.group(1)), int(m.group(2) or 0)


def graph_version(stored=None) -> str:
	"""The version to call: the stored one when it is still current, otherwise the default."""
	parsed = parse_version(stored)
	if not parsed or parsed < MIN_GRAPH_VERSION:
		return DEFAULT_GRAPH_VERSION
	return f"v{parsed[0]}.{parsed[1]}"


def is_outdated(stored) -> bool:
	"""True when the stored version is missing, malformed or older than MIN_GRAPH_VERSION."""
	parsed = parse_version(stored)
	return not parsed or parsed < MIN_GRAPH_VERSION
