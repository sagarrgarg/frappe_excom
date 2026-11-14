"""Someone the People Finder must never find or add again (HLD-009): hashes only, never the identifiers."""

import hashlib

import frappe
from frappe.model.document import Document


class ExcomAIPeopleSuppression(Document):
	pass


def key(value: str | None) -> str | None:
	value = (value or "").strip().lower()
	return hashlib.sha256(value.encode()).hexdigest() if value else None


def suppressed(*values) -> bool:
	"""True when any of these identifiers (email, LinkedIn URL, provider ids) was forgotten."""
	keys = [k for k in (key(v) for v in values) if k]
	return bool(keys) and bool(frappe.db.exists("Excom AI People Suppression", {"key_hash": ["in", keys]}))


def add(kind: str, value: str | None, reason: str = "") -> None:
	k = key(value)
	if k and not frappe.db.exists("Excom AI People Suppression", k):
		frappe.get_doc({"doctype": "Excom AI People Suppression", "key_hash": k, "kind": kind, "reason": reason,
			"forgotten_by": frappe.session.user}).insert(ignore_permissions=True)
