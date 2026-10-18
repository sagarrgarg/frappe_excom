import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, date_diff, flt, get_first_day, getdate, now_datetime

MAX_PERIOD_MONTHS = 24
TARGETS = ("target_leads", "target_purchases", "target_revenue", "target_cpl", "target_roas")


class AdsBudgetPlan(Document):
	"""Monthly spend plan with targets. Submit = approve, maker-checker (see before_submit).

	Only submitted plans count in budgets.plan_vs_actual and the Overspend alert. A change after
	approval is Cancel → Amend, so the approved history stays as it was.
	"""

	def validate(self):
		from excom.excom_ads import permissions

		if not self.flags.ignore_permissions:
			permissions.require("manage_budgets")
		ws_company = frappe.db.get_value("Ads Workspace", self.workspace, "company")
		if ws_company != self.company:
			frappe.throw(_("Workspace {0} belongs to company {1}, not {2}").format(self.workspace, ws_company, self.company))
		if self.brand and frappe.db.get_value("Ads Brand", self.brand, "workspace") != self.workspace:
			frappe.throw(_("Brand {0} is not in workspace {1}").format(self.brand, self.workspace))
		permissions.assert_in_scope(self)
		a, b = getdate(self.period_from), getdate(self.period_to)
		if b < a:
			frappe.throw(_("Period To is before Period From"))
		if b > add_months(a, MAX_PERIOD_MONTHS):
			frappe.throw(_("A plan covers at most {0} months").format(MAX_PERIOD_MONTHS))
		# Actuals are compared in company currency, so the plan is in it too.
		self.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		if self.docstatus == 0:
			# A draft (new, or amended from an approved plan) carries no approval.
			self.status = "Draft"
			self.approved_by = self.approved_at = self.approval_note = None
			self.approval_override = 0
		self._validate_lines(get_first_day(a), b)
		self.total_planned = sum(flt(r.planned_spend) for r in self.lines)

	def _validate_lines(self, first_month, last_day):
		if not self.lines:
			frappe.throw(_("Add at least one plan line"))
		seen = {}
		brand_ws = {}
		for r in self.lines:
			r.month = get_first_day(r.month)
			r.currency = self.currency
			if not (first_month <= getdate(r.month) <= last_day):
				frappe.throw(_("Row {0}: month {1} is outside the plan period").format(r.idx, str(r.month)[:7]))
			if flt(r.planned_spend) < 0 or any(flt(r.get(t)) < 0 for t in TARGETS):
				frappe.throw(_("Row {0}: planned spend and targets cannot be negative").format(r.idx))
			if r.brand:
				if self.brand and r.brand != self.brand:
					frappe.throw(_("Row {0}: this plan is for brand {1}").format(r.idx, self.brand))
				if r.brand not in brand_ws:
					brand_ws[r.brand] = frappe.db.get_value("Ads Brand", r.brand, "workspace")
				if brand_ws[r.brand] != self.workspace:
					frappe.throw(_("Row {0}: brand {1} is not in workspace {2}").format(r.idx, r.brand, self.workspace))
			if r.account:
				acc = frappe.db.get_value("Ads Account", r.account, ["provider", "workspace"], as_dict=True)
				if not acc or acc.workspace != self.workspace:
					frappe.throw(_("Row {0}: ad account {1} is not in workspace {2}").format(r.idx, r.account, self.workspace))
				if acc.provider != r.provider:
					frappe.throw(_("Row {0}: ad account {1} is a {2} account, not {3}").format(r.idx, r.account, acc.provider, r.provider))
			key = (str(r.month), r.provider, r.brand or "", r.account or "")
			if key in seen:
				frappe.throw(_("Row {0} repeats row {1} (same month, network, brand and account)").format(r.idx, seen[key]))
			seen[key] = r.idx
			_fill_targets(r)

	def before_submit(self):
		from excom.excom_ads import permissions

		user = frappe.session.user
		permissions.require("manage_budgets")
		# Maker-checker: whoever created or last edited the draft cannot approve it.
		saved = frappe.db.get_value(self.doctype, self.name, ["owner", "modified_by"], as_dict=True) if not self.is_new() else None
		makers = {self.owner or user} | ({saved.owner, saved.modified_by} if saved else {user})
		self.approval_override = 0
		self.approval_note = None
		if user in makers:
			if not permissions.is_super(user):
				frappe.throw(_("You created or last edited this plan, so another user with the Manage Budgets right must approve it."), frappe.PermissionError)
			self.approval_override = 1
			self.approval_note = _("Approved by {0}, who also created or last edited it (System Manager override).").format(user)
		self._check_overlap()
		self.approved_by = user
		self.approved_at = now_datetime()
		self.status = "Approved"

	def _check_overlap(self):
		"""Two approved plans for the same scope and months would count the same budget twice."""
		cond = {"docstatus": 1, "company": self.company, "workspace": self.workspace, "name": ["!=", self.name], "period_from": ["<=", self.period_to], "period_to": [">=", self.period_from]}
		for other in frappe.get_all("Ads Budget Plan", filters=cond, fields=["name", "brand", "plan_name"]):
			if not other.brand or not self.brand or other.brand == self.brand:
				frappe.throw(_("Approved plan {0} ({1}) already covers this workspace{2} in an overlapping period. Cancel or amend it instead.").format(other.name, other.plan_name, f" / {other.brand}" if other.brand else ""))

	def before_cancel(self):
		from excom.excom_ads import permissions

		permissions.require("manage_budgets")

	def on_cancel(self):
		self.db_set("status", "Cancelled")
		from excom.excom_ads.alerts import resolve_alert
		from excom.excom_ads.budgets import overspend_key

		for r in self.lines:
			resolve_alert(overspend_key(self.name, r.name), "Plan cancelled")


def _fill_targets(r) -> None:
	"""Keep spend, leads and CPL (and spend, revenue and ROAS) consistent when only some are given."""
	spend = flt(r.planned_spend)
	if spend:
		if flt(r.target_leads) and not flt(r.target_cpl):
			r.target_cpl = spend / flt(r.target_leads)
		elif flt(r.target_cpl) and not flt(r.target_leads):
			r.target_leads = spend / flt(r.target_cpl)
		if flt(r.target_revenue) and not flt(r.target_roas):
			r.target_roas = flt(r.target_revenue) / spend
		elif flt(r.target_roas) and not flt(r.target_revenue):
			r.target_revenue = flt(r.target_roas) * spend


def days_in_month(d) -> int:
	first = get_first_day(d)
	return date_diff(add_months(first, 1), first)
