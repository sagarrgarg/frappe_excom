import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from excom.excom_ads.settings import DEFAULTS, POSITIVE


class AdsSettings(Document):
	def onload(self):
		# Frappe loads a Single's defaults only when nothing is saved; once any field is saved the rest
		# come back empty (checks as 0) and the form would show 0 / off. Show what get_settings() uses.
		saved = set(frappe.db.sql_list("select field from `tabSingles` where doctype = 'Ads Settings'"))
		for k, default in DEFAULTS.items():
			if self.meta.has_field(k) and k not in saved:
				self.set(k, default)

	def validate(self):
		for k, default in DEFAULTS.items():
			if self.meta.has_field(k) and self.get(k) in (None, ""):
				self.set(k, default)
		bad = [self.meta.get_label(f) for f in POSITIVE if cint(self.get(f)) < 1]
		if bad:
			frappe.throw(_("These must be at least 1: {0}").format(", ".join(bad)), title=_("Invalid Ads Settings"))
