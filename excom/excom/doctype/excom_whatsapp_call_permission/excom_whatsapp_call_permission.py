"""One row per (contact, WhatsApp number): may this business call this person?

Named after both, so two webhooks racing to create it collide on the primary key instead of
making two rows.
"""

from frappe.model.document import Document


def permission_name(channel_account: str, omni_identity: str) -> str:
	return f"{channel_account}::{omni_identity}"[:140]


class ExcomWhatsAppCallPermission(Document):
	def autoname(self):
		self.name = permission_name(self.channel_account, self.omni_identity)
