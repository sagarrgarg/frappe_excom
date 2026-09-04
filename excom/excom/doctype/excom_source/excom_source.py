import frappe
from frappe import _
from frappe.model.document import Document

# How each source type talks to Excom. Website: the site posts to our webhook (push). TradeIndia: we poll.
# IndiaMART / Meta: we poll (authoritative) and accept their push as an accelerator. Manual/Exhibition: neither.
MODE_BY_TYPE = {"Website": "Push", "TradeIndia": "Pull", "IndiaMART": "Both", "Meta Lead Ads": "Both", "Exhibition": "", "Manual": "", "Channel": ""}


class ExcomSource(Document):
	"""The one Source list. Every row mirrors itself into ERPNext's attribution master (Lead Source on v15,
	UTM Source on v16) so Lead.source / utm_source always has a matching value — nobody maintains two lists."""

	def on_update(self):
		from excom.excom.services.crm_compat import seed_attribution_rows
		seed_attribution_rows([self.source_name])

	def validate(self):
		self.mode = MODE_BY_TYPE.get(self.source_type, self.mode or "")
		if self.source_type == "TradeIndia" and self.enabled and not self.api_url:
			frappe.msgprint(_("TradeIndia needs the 'My Inquiry API' URL before it can poll."), indicator="orange")
		if self.source_type == "Website" and self.enabled and not self.get_password("push_token", raise_exception=False):
			frappe.msgprint(_("Generate a push token (embed panel) — the website cannot post without it."), indicator="orange")
