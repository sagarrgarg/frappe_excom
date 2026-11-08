"""An unsent email on a conversation. The Drafts folder of the mailbox is the other copy:
saving here saves there (channels/email/drafts.py), and a draft written in Gmail or a mail app
shows up here. Kept apart from Excom Message so a draft never counts as something we sent."""

from frappe.model.document import Document


class ExcomEmailDraft(Document):
	pass
