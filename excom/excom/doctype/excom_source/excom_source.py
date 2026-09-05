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

	# Types where nothing arrives from outside, so there is nobody to acknowledge.
	NO_ACK_TYPES = ("Exhibition", "Manual")

	def validate(self):
		self.mode = MODE_BY_TYPE.get(self.source_type, self.mode or "")
		self._clear_ack_when_it_cannot_apply()
		if self.source_type == "TradeIndia" and self.enabled and not self.api_url:
			frappe.msgprint(_("TradeIndia needs the 'My Inquiry API' URL before it can poll."), indicator="orange")
		if self.source_type == "Website" and self.enabled and not self.get_password("push_token", raise_exception=False):
			frappe.msgprint(_("Generate a push token (embed panel) — the website cannot post without it."), indicator="orange")
		if self.auto_ack_template and not self.channel_account:
			frappe.msgprint(
				_("Auto-acknowledgement needs a channel account to send from, or nothing will be sent."),
				indicator="orange",
			)

	def _clear_ack_when_it_cannot_apply(self):
		"""A value the form has stopped showing is worse than no value.

		The template field hides for Exhibition and Manual, but switching a source to one of those
		types used to leave the old template stored and invisible. Now that a stored template is
		what actually triggers the send, an invisible one would send messages nobody configured.
		"""
		if self.source_type in self.NO_ACK_TYPES and self.auto_ack_template:
			self.auto_ack_template = None
			self.auto_ack_repeat = 0
			frappe.msgprint(
				_("Cleared the auto-acknowledgement template: a {0} source has nobody to acknowledge.").format(self.source_type),
				indicator="orange",
			)
