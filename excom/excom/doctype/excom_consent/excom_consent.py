import frappe
from frappe import _
from frappe.model.document import Document


class ExcomConsent(Document):
	"""One consent event. Never edited or deleted: a change of mind is a new row.

	Rows are written through excom.excom.services.consent.record_consent so the Omni Identity
	opt-out flags and this ledger always move together.
	"""

	def before_insert(self):
		# set by the server, never by the client: a ledger row cannot be backdated or attributed
		self.recorded_on = frappe.utils.now_datetime()
		self.recorded_by = frappe.session.user

	def validate(self):
		if not self.is_new():
			frappe.throw(_("Consent records cannot be changed. Record a new consent or withdrawal instead."))

	def on_trash(self):
		if not frappe.flags.in_test and not frappe.flags.excom_allow_consent_delete:
			frappe.throw(_("Consent records are kept as proof and cannot be deleted."))
