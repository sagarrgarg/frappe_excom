"""Excom Voice Endpoint — one SIP identity per agent per voice line.

An agent can work two lines belonging to two provider accounts, so this is one-to-many and needs
rows rather than fields on User. It is provisioned by `channels/voice/provisioning.py` and is not
meant to be hand-edited; the fields the provider owns are read-only in the form.

The password is stored so the server can re-mint tokens and, if a provider ever loses browser-SDK
support, fall back to direct SIP registration. It never leaves the server: the softphone logs in
with a short-lived token from `mint_access_token()`.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ExcomVoiceEndpoint(Document):
	def validate(self):
		self.enforce_one_per_line()
		if self.channel_account and not self.provider:
			self.provider = frappe.db.get_value(
				"Excom Channel Account", self.channel_account, "voice_provider"
			)

	def enforce_one_per_line(self):
		"""Two endpoints for the same agent on the same line means two registrations racing, and
		half the calls landing on a socket nobody is watching."""
		if not (self.user and self.channel_account):
			return
		clash = frappe.db.exists(
			"Excom Voice Endpoint",
			{
				"user": self.user,
				"channel_account": self.channel_account,
				"status": ["!=", "Deprovisioned"],
				"name": ["!=", self.name or ""],
			},
		)
		if clash:
			frappe.throw(
				_("{0} already has a voice endpoint on this line.").format(self.user),
				frappe.DuplicateEntryError,
			)


def for_user(user: str, channel_account: str) -> str | None:
	"""The active endpoint name for this agent on this line, or None."""
	return frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": channel_account, "status": "Active"},
		"name",
	)


def on_doctype_update():
	frappe.db.add_index("Excom Voice Endpoint", ["user", "channel_account"])
