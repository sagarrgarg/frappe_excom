"""
Per-user rate limiting for authenticated Excom endpoints.

frappe.rate_limiter.rate_limit(key="user") reads ``frappe.form_dict["user"]`` — a request
parameter, not the session user — so it silently degrades to an IP-keyed limit. Behind a
shared office NAT every agent then competes for the same bucket (QA saw 364 × 429 on
get_messages from one machine). This decorator keys on ``frappe.session.user``.
"""

from functools import wraps

import frappe
from frappe import _


def user_rate_limit(limit: int = 60, seconds: int = 60):
	"""Allow ``limit`` calls per ``seconds`` per session user per endpoint."""

	def decorator(fn):
		@wraps(fn)
		def wrapper(*args, **kwargs):
			if not getattr(frappe, "request", None):
				return fn(*args, **kwargs)
			user = frappe.session.user or "Guest"
			cmd = frappe.form_dict.get("cmd") or f"{fn.__module__}.{fn.__name__}"
			cache_key = frappe.cache.make_key(f"rl:user:{cmd}:{user}")

			# Count first, then make sure the count expires.
			#
			# Reading the counter and only then seeding it with an expiry loses a race it will
			# eventually meet: the key expires in the gap between the read and the increment, and
			# `incrby` recreates it with no TTL at all. From that moment the counter only grows,
			# so once it passes the limit the endpoint is refused for that user *for ever* — which
			# is how a 600-per-minute allowance became a permanent 429 on the thread list, with
			# the client's retries walking it further up.
			#
			# Incrementing first cannot lose that race: a missing key comes back as 1, and the
			# expiry is then set on a key that certainly exists. The TTL check repairs any key
			# already stuck without one.
			value = frappe.cache.incrby(cache_key, 1)
			try:
				if value == 1 or frappe.cache.ttl(cache_key) < 0:
					frappe.cache.expire(cache_key, seconds)
			except Exception:
				# A cache that cannot expire a key must not take the endpoint down with it.
				pass

			if value > limit:
				frappe.throw(
					_("You hit the rate limit because of too many requests. Please try after sometime."),
					frappe.RateLimitExceededError,
				)
			return fn(*args, **kwargs)

		return wrapper

	return decorator
