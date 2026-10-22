"""One GA4 property read through a Google Ads Connection (scope analytics.readonly). Named
GA4-{property id}. Figures land in Ads Web Snapshot (source "Analytics"): never Verified, never
platform ad figures."""

import re

import frappe
from frappe import _
from frappe.model.document import Document


def property_doc_name(property_id: str) -> str:
	return f"GA4-{property_id}"


def normalise_property_id(value) -> str:
	"""'properties/123', '123' → '123'. Anything else is refused (it goes into a URL path)."""
	v = str(value or "").strip()
	if v.startswith("properties/"):
		v = v.split("/", 1)[1]
	if not re.fullmatch(r"\d{4,15}", v):
		frappe.throw(_("'{0}' is not a GA4 property ID (digits only)").format(str(value)[:30]))
	return v


class AdsAnalyticsProperty(Document):
	def autoname(self):
		self.property_id = normalise_property_id(self.property_id)
		self.name = property_doc_name(self.property_id)

	def validate(self):
		from excom.excom_ads import permissions

		self.property_id = normalise_property_id(self.property_id)
		company = frappe.db.get_value("Ads Workspace", self.workspace, "company") if self.workspace else None
		if company:
			self.company = company
		if self.brand:
			brand_ws = frappe.db.get_value("Ads Brand", self.brand, "workspace")
			if brand_ws and brand_ws != self.workspace:
				frappe.throw(_("Brand {0} belongs to workspace {1}, not {2}").format(self.brand, brand_ws, self.workspace))
		if self.connection:
			conn = frappe.db.get_value("Ads Connection", self.connection, ["provider", "company"], as_dict=True)
			if conn and conn.provider != "Google":
				frappe.throw(_("Ads Connection {0} is not a Google connection").format(self.connection))
			if conn and self.company and conn.company != self.company:
				frappe.throw(_("Ads Connection {0} belongs to company {1}, not {2}").format(self.connection, conn.company, self.company))
		permissions.assert_in_scope(self)

	def on_trash(self):
		frappe.db.delete("Ads Web Snapshot", {"property": self.name})
