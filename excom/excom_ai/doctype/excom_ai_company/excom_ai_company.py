"""A company an agent found: research data, not CRM, until someone there is contacted (HLD-007 C.2)."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


def normalise_domain(value: str | None) -> str | None:
	"""example.com from 'https://www.Example.com/contact', so one company is one row."""
	if not value:
		return None
	v = value.strip().lower()
	v = re.sub(r"^[a-z]+://", "", v)
	v = v.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
	v = re.sub(r"^www\d?\.", "", v)
	return v.strip(".") or None


class ExcomAICompany(Document):
	def validate(self):
		self.domain = normalise_domain(self.domain or self.website)
		if self.website and not re.match(r"^https?://", self.website):
			self.website = "https://" + self.website.strip()
		if not self.website and self.domain:
			self.website = f"https://{self.domain}"
		if self.status == "Excluded" and not self.exclusion_reason:
			frappe.throw(_("Say why the company is excluded."))
		if self.fit_score is not None and not 0 <= (self.fit_score or 0) <= 100:
			frappe.throw(_("Fit score is between 0 and 100."))
