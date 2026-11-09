"""Acting as an agent's own user for a moment, without harming the session of the person whose
request it is (HLD-007 §14.2).

frappe.set_user resets the session's sid, data (the CSRF token among it) and the request's
form_dict. Inside a web request (a person approves a proposal, which then runs as the agent) that
would be written back at the end of the request. `as_user` puts every one of them back.
"""

from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe import _


@contextmanager
def as_user(user: str):
	session = frappe.local.session
	saved = (session.user, session.sid, session.data, frappe.local.form_dict, frappe.local.cache)
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(saved[0])
		session.sid, session.data = saved[1], saved[2]
		frappe.local.form_dict, frappe.local.cache = saved[3], saved[4]


def service_user_of(agent) -> str:
	"""The agent's own user, refusing to fall back to anyone else when it is missing or disabled."""
	user = agent.service_user
	if not user or not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} has no active user of its own, so it cannot act.").format(agent.agent_name))
	return user
