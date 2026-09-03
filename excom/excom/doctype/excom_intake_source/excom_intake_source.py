import frappe
from frappe import _
from frappe.model.document import Document

# How each source type talks to Excom. Website: the site posts to our webhook (push). TradeIndia: we poll.
# IndiaMART / Meta: we poll (authoritative) and accept their push as an accelerator. Manual/Exhibition: neither.
MODE_BY_TYPE = {"Website": "Push", "TradeIndia": "Pull", "IndiaMART": "Both", "Meta Lead Ads": "Both", "Exhibition": "", "Manual": ""}


class ExcomIntakeSource(Document):
	def validate(self):
		self.mode = MODE_BY_TYPE.get(self.source_type, self.mode or "")
		if self.source_type == "TradeIndia" and self.enabled and not self.api_url:
			frappe.msgprint(_("TradeIndia needs the 'My Inquiry API' URL before it can poll."), indicator="orange")
		if self.source_type == "Website" and self.enabled and not self.get_password("push_token", raise_exception=False):
			frappe.msgprint(_("Generate a push token (embed panel) — the website cannot post without it."), indicator="orange")
