"""Excom Call — one row per call, on the same thread as that contact's WhatsApp and email.

A call mutates four to six times over its life (ringing, answered, ended, reconciled, recorded,
transcribed), and duration/cost/outcome have no home in the message schema. So it is its own
doctype, and `Excom Message` carries a `Call` stub pointing back here for the timeline.

Visibility follows the thread. An agent who may not open the conversation may not read the call
that belongs to it — see `has_permission` below and `excom_thread.can_access()`.
"""

import json

import frappe
from frappe.model.document import Document

from excom.excom.utils.phone import normalize_phone

# Terminal states. Once a call is in one of these, late webhooks may still add duration, cost or a
# recording, but they must not move it back to Ringing.
CLOSED_STATUSES = {"Completed", "Missed", "No Answer", "Busy", "Failed", "Canceled"}


class ExcomCall(Document):
	def validate(self):
		if not self.status:
			self.status = "Ringing"
		for field in ("customer_number", "business_number"):
			value = self.get(field)
			if value:
				self.set(field, normalize_phone(value))
		if self.ring_set and isinstance(self.ring_set, str):
			# Stored as JSON text; reject anything that is not a list so the fan-out cannot explode.
			try:
				parsed = json.loads(self.ring_set)
			except ValueError:
				self.ring_set = "[]"
			else:
				if not isinstance(parsed, list):
					self.ring_set = "[]"
		if self.duration and not self.talk_time:
			self.talk_time = self.duration

	def before_insert(self):
		if not self.display_name and self.omni_identity:
			self.display_name = (
				frappe.db.get_value("Omni Identity", self.omni_identity, "display_name")
				or self.customer_number
				or ""
			)

	def ring_set_users(self) -> list[str]:
		"""The users Excom decided should ring. Empty list rather than a throw on bad JSON —
		a malformed decision record must not stop a call being written."""
		if not self.ring_set:
			return []
		try:
			value = json.loads(self.ring_set)
		except ValueError:
			return []
		return [u for u in value if isinstance(u, str)] if isinstance(value, list) else []


def can_access(doc, user: str | None = None) -> bool:
	"""One rule, and it is the thread's rule.

	A call that has no thread yet (the webhook arrived before the identity resolved) falls back to
	the agent it was routed to, so the person whose phone is ringing can always see it.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	from excom.excom.doctype.excom_thread.excom_thread import (
		EXCOM_ROLES,
		MANAGER_ROLES,
		can_access as thread_can_access,
	)

	roles = set(frappe.get_roles(user))
	if roles & MANAGER_ROLES:
		return True
	if not (roles & EXCOM_ROLES):
		return False

	if isinstance(doc, str):
		doc = frappe.db.get_value(
			"Excom Call", doc, ["thread", "agent", "answered_by", "ring_set"], as_dict=True
		)
		if not doc:
			return False

	if doc.get("thread"):
		return thread_can_access(doc.get("thread"), user)

	if user in (doc.get("agent"), doc.get("answered_by")):
		return True

	ring_set = doc.get("ring_set")
	if ring_set:
		try:
			return user in (json.loads(ring_set) or [])
		except ValueError:
			return False
	return False


def has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Hook target. Registered in hooks.py so the Desk list and the API agree."""
	return can_access(doc, user)


def get_permission_query_conditions(user: str | None = None) -> str:
	"""List-view filter. Mirrors can_access() so a Desk report never shows a row the detail view
	would refuse to open."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	from excom.excom.doctype.excom_thread.excom_thread import (
		MANAGER_ROLES,
		get_permission_query_conditions as thread_conditions,
	)

	if set(frappe.get_roles(user)) & MANAGER_ROLES:
		return ""

	thread_cond = thread_conditions(user)
	escaped = frappe.db.escape(user)
	own = f"`tabExcom Call`.`agent` = {escaped} OR `tabExcom Call`.`answered_by` = {escaped}"
	if not thread_cond or thread_cond == "1=0":
		return f"({own})"
	return (
		f"({own} OR `tabExcom Call`.`thread` IN "
		f"(SELECT `name` FROM `tabExcom Thread` WHERE {thread_cond}))"
	)


def on_doctype_update():
	# The call list, the per-thread history and the reconcile sweep each filter on one of these.
	frappe.db.add_index("Excom Call", ["thread", "creation"])
	frappe.db.add_index("Excom Call", ["omni_identity", "creation"])
	frappe.db.add_index("Excom Call", ["agent", "status"])
	frappe.db.add_index("Excom Call", ["status", "reconciled"])
