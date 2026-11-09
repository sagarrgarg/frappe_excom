"""One observed signal, with its evidence. Only Confirmed signals may be recorded."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class ExcomAIObservation(Document):
	def validate(self):
		if not (self.company or self.person):
			frappe.throw(_("An observation is about a company or a person."))
		if frappe.db.get_value("Excom AI Signal Code", self.signal, "status") != "Confirmed":
			frappe.throw(_("Signal {0} is not confirmed: a person decides what agents may collect.").format(self.signal))
		if self.confidence is not None and not 0 <= self.confidence <= 1:
			frappe.throw(_("Confidence is between 0 and 1."))
		self.observed_on = self.observed_on or now_datetime()
