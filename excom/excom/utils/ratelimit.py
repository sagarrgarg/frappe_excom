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
			value = frappe.cache.get(cache_key) or 0
			if not value:
				frappe.cache.setex(cache_key, seconds, 0)
			value = frappe.cache.incrby(cache_key, 1)
			if value > limit:
				frappe.throw(
					_("You hit the rate limit because of too many requests. Please try after sometime."),
					frappe.RateLimitExceededError,
				)
			return fn(*args, **kwargs)

		return wrapper

	return decorator
