"""Who can actually take a call right now.

Three independent facts, all required before an agent's browser goes into a ring set:

    registered  — the SIP socket is up, proved by a heartbeat from the tab
    available   — the agent said they are taking calls
    free        — not already on a call

None of this is a doctype. It is high-write ephemeral state: a heartbeat per agent per minute
would be a database write per agent per minute, forever, to store something that is worthless
thirty seconds later. It lives in the cache with a TTL, which also means a closed laptop leaves
the ring set on its own without anybody changing a setting.
"""

import frappe

# A heartbeat every 60s with a 150s TTL tolerates one missed beat before dropping the agent. Longer
# and a closed laptop keeps getting calls; shorter and one slow request drops a working agent.
REGISTRATION_TTL = 150
HEARTBEAT_SECONDS = 60

# Availability is a deliberate choice, so it outlives a page reload — but not a forgotten Friday.
AVAILABILITY_TTL = 12 * 3600

# A busy flag only has to outlive the call it belongs to; the hangup clears it.
BUSY_TTL = 4 * 3600

_REG = "excom:voice:reg"
_AVAIL = "excom:voice:avail"
_BUSY = "excom:voice:busy"


def _key(prefix: str, *parts: str) -> str:
	return frappe.cache.make_key(":".join([prefix, *[p or "" for p in parts]]))


# ── registration ──────────────────────────────────────────────────────────────


def mark_registered(user: str, account: str, ip: str = "") -> None:
	"""Called on softphone login and on every heartbeat."""
	frappe.cache.setex(_key(_REG, user, account), REGISTRATION_TTL, ip or "1")


def mark_unregistered(user: str, account: str) -> None:
	"""Called on an explicit logout or a socket close. Not required for correctness — the TTL
	catches everything — but it makes a deliberate sign-off instant."""
	frappe.cache.delete_value(_key(_REG, user, account))


def is_registered(user: str, account: str) -> bool:
	return bool(frappe.cache.get_value(_key(_REG, user, account)))


# ── availability ──────────────────────────────────────────────────────────────


def set_available(user: str, available: bool) -> None:
	key = _key(_AVAIL, user)
	if available:
		frappe.cache.setex(key, AVAILABILITY_TTL, "1")
	else:
		frappe.cache.delete_value(key)


def is_available(user: str) -> bool:
	return bool(frappe.cache.get_value(_key(_AVAIL, user)))


# ── busy ──────────────────────────────────────────────────────────────────────


def mark_busy(user: str, call: str) -> None:
	if user:
		frappe.cache.setex(_key(_BUSY, user), BUSY_TTL, call)


def clear_busy(user: str) -> None:
	if user:
		frappe.cache.delete_value(_key(_BUSY, user))


def is_busy(user: str) -> bool:
	return bool(frappe.cache.get_value(_key(_BUSY, user)))


# ── the question routing actually asks ────────────────────────────────────────


def can_ring_browser(user: str, account: str) -> bool:
	"""True when this agent's browser is worth putting in a ring set."""
	return is_registered(user, account) and is_available(user) and not is_busy(user)


def can_ring_phone(user: str) -> bool:
	"""The phone transport does not need a registration — only that the agent has not signed off
	and is not already talking to somebody."""
	return is_available(user) and not is_busy(user)


def snapshot(user: str, account: str = "") -> dict:
	"""What the softphone shows the agent about their own state."""
	return {
		"user": user,
		"available": is_available(user),
		"registered": is_registered(user, account) if account else False,
		"busy": is_busy(user),
		"heartbeat_seconds": HEARTBEAT_SECONDS,
	}
