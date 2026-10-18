import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, validate_email_address


class AdsReportTemplate(Document):
	"""A marketing report (reports.py) and, optionally, its schedule (report_schedule.py).

	The template's company / workspace / brand bound what any rendering can show; each user
	recipient sees that ∩ their own scope. External addresses need the export data right."""

	def validate(self):
		from excom.excom_ads import permissions, reports

		self._validate_scope()
		permissions.assert_in_scope(self)
		if self.time_zone and not reports.valid_tz(self.time_zone.strip()):
			frappe.throw(_("{0} is not a time zone name (use e.g. Asia/Kolkata)").format(self.time_zone))
		self.time_zone = (self.time_zone or "").strip()
		self.day_of_month = min(max(cint(self.day_of_month) or 1, 1), 28)
		self._validate_recipients(permissions)
		if cint(self.schedule_enabled):
			if not self.recipients:
				frappe.throw(_("Add at least one recipient to send this report on a schedule"))
			if not (cint(self.via_email) or cint(self.via_whatsapp)):
				frappe.throw(_("Pick email and/or WhatsApp to send this report on a schedule"))
		if not any(cint(self.get(f)) for f, _label in reports.SECTIONS):
			frappe.throw(_("Pick at least one section"))

	def _validate_scope(self):
		if self.workspace:
			ws_company = frappe.db.get_value("Ads Workspace", self.workspace, "company")
			if ws_company != self.company:
				frappe.throw(_("Workspace {0} belongs to company {1}, not {2}").format(self.workspace, ws_company, self.company))
		if self.brand:
			brand_ws = frappe.db.get_value("Ads Brand", self.brand, "workspace")
			if self.workspace and brand_ws != self.workspace:
				frappe.throw(_("Brand {0} is not in workspace {1}").format(self.brand, self.workspace))
			if frappe.db.get_value("Ads Workspace", brand_ws, "company") != self.company:
				frappe.throw(_("Brand {0} is not in company {1}").format(self.brand, self.company))
			self.workspace = self.workspace or brand_ws
		if self.account:
			acc = frappe.db.get_value("Ads Account", self.account, ["company", "workspace", "provider"], as_dict=True) or frappe._dict()
			if acc.company != self.company or (self.workspace and acc.workspace != self.workspace):
				frappe.throw(_("Ad account {0} is not in this report's company / workspace").format(self.account))
			if self.provider and acc.provider != self.provider:
				frappe.throw(_("Ad account {0} is a {1} account, not {2}").format(self.account, acc.provider, self.provider))

	def _validate_recipients(self, permissions):
		seen = set()
		external = False
		for r in self.recipients or []:
			if r.recipient_type == "User":
				if not r.user:
					frappe.throw(_("Row {0}: pick a user").format(r.idx))
				u = frappe.db.get_value("User", r.user, ["enabled", "user_type"], as_dict=True)
				if not u or not u.enabled or (u.user_type != "System User" and r.user != "Administrator"):
					frappe.throw(_("Row {0}: {1} is not an enabled system user").format(r.idx, r.user))
				r.email = None
				key = ("u", r.user)
			else:
				r.email = (r.email or "").strip().lower()
				if not r.email:
					frappe.throw(_("Row {0}: enter an email address").format(r.idx))
				validate_email_address(r.email, throw=True)
				r.user = None
				external = True
				key = ("e", r.email)
			if key in seen:
				frappe.throw(_("Row {0}: {1} is listed twice").format(r.idx, r.user or r.email))
			seen.add(key)
		if external and not self.flags.ignore_permissions:
			# Sending figures to someone without a login is exporting them.
			permissions.require("export_data")
