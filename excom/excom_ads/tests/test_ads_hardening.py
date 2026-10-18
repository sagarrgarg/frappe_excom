"""Security review regressions: scope fails closed, ads endpoints need a marketing/admin role, scoped
admins manage only their own access profile, and one company's Meta connection cannot take over
another company's ad account.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_hardening'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom_ads import permissions

P = "QA Harden"
COMPANY_B = "_Test Ads Company B"
U_ACC_ONLY = "qa_harden_acc_only@example.com"  # Excom Marketing, restricted to one ad account
U_COMPANY = "qa_harden_company@example.com"  # Excom Marketing, restricted to company A
U_BRAND = "qa_harden_brand@example.com"  # Excom Marketing, restricted to one brand
U_FIN_ONLY = "qa_harden_fin_only@example.com"  # Accounts User only, with a Company permission
U_ADMIN_A = "qa_harden_admin_a@example.com"  # Excom Admin scoped to company A
USERS = [U_ACC_ONLY, U_COMPANY, U_BRAND, U_FIN_ONLY, U_ADMIN_A]


class TestAdsHardening(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		from excom.excom_ads.tests.test_ads_permissions import _ensure_company_b

		_ensure_company_b()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS A", "company": cls.company})
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS B", "company": COMPANY_B})
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand A", "workspace": f"{P} WS A"})
		cls.acc_a = ins({"doctype": "Ads Account", "account_name": f"{P} Acc A", "provider": "Google", "external_id": "333-000-0001", "workspace": f"{P} WS A", "brand": f"{P} Brand A"}).name
		cls.acc_b = ins({"doctype": "Ads Account", "account_name": f"{P} Acc B", "provider": "Google", "external_id": "333-000-0002", "workspace": f"{P} WS B"}).name
		cls.job_b = ins({"doctype": "Ads Sync Job", "account": cls.acc_b, "job_type": "Structure", "status": "Failed", "error": "company B's error text"}).name
		cls.job_a = ins({"doctype": "Ads Sync Job", "account": cls.acc_a, "job_type": "Structure", "status": "Success"}).name
		ins({"doctype": "Ads Notification Policy", "policy_name": f"{P} Policy A", "company": cls.company, "recipients": [{"recipient_type": "Role", "role": "Excom Admin"}]})
		roles = {U_ACC_ONLY: ["Excom Marketing"], U_COMPANY: ["Excom Marketing"], U_BRAND: ["Excom Marketing"], U_FIN_ONLY: ["Accounts User"], U_ADMIN_A: ["Excom Admin"]}
		for u, rs in roles.items():
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": r} for r in rs]})
		for u, dt, v in ((U_ACC_ONLY, "Ads Account", cls.acc_a), (U_COMPANY, "Company", cls.company), (U_BRAND, "Ads Brand", f"{P} Brand A"), (U_FIN_ONLY, "Company", cls.company), (U_ADMIN_A, "Company", cls.company)):
			ins({"doctype": "User Permission", "user": u, "allow": dt, "for_value": v, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_COMPANY, "view_finance": 1})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Ads Sync Job", {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Notification Recipient", {"parent": ["like", f"{P}%"]})
		frappe.db.delete("Ads Notification Policy", {"policy_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", USERS]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for n in frappe.get_all("Excom Meta Connection", {"connection_name": ["like", f"{P}%"]}, pluck="name"):
			frappe.delete_doc("Excom Meta Connection", n, force=True, ignore_permissions=True)
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _as(self, u):
		frappe.set_user(u)
		permissions.clear_cache()

	def _names(self, doctype, **filters):
		return set(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=0))

	def test_scope_fails_closed_where_a_doctype_lacks_the_column(self):
		# Sync jobs carry only the account: company and brand restrictions go through it.
		self._as(U_COMPANY)
		self.assertNotIn(self.job_b, self._names("Ads Sync Job"), "company A never reads company B's sync errors")
		self.assertIn(self.job_a, self._names("Ads Sync Job"))
		self._as(U_BRAND)
		self.assertEqual(self._names("Ads Sync Job", name=["in", [self.job_a, self.job_b]]), {self.job_a})
		self.assertEqual(self._names("Ads Workspace", workspace_name=["like", f"{P}%"]), {f"{P} WS A"}, "a brand user sees only the workspace of that brand")
		# Notification policies have only a company: an account restriction cannot be checked → none.
		self._as(U_ACC_ONLY)
		self.assertEqual(self._names("Ads Notification Policy", policy_name=["like", f"{P}%"]), set())
		self.assertEqual(self._names("Ads Workspace", workspace_name=["like", f"{P}%"]), {f"{P} WS A"})
		self.assertFalse(permissions.doc_allowed(frappe.get_doc("Ads Sync Job", self.job_b)))
		self.assertEqual(permissions.scope_filters("Ads Sync Job"), {"account": ["in", [self.acc_a]]})

	def test_ads_endpoints_need_a_marketing_or_admin_role(self):
		from excom.excom_ads import budgets, dashboard, leads_dashboard

		self._as(U_FIN_ONLY)
		self.assertRaises(frappe.PermissionError, permissions.require_access)
		self.assertRaises(frappe.PermissionError, dashboard.overview, "2026-10-01", "2026-10-02")
		self.assertRaises(frappe.PermissionError, leads_dashboard.summary, "2026-10-01", "2026-10-02")
		self.assertRaises(frappe.PermissionError, budgets.get_plan_vs_actual, "2026-10-01", "2026-10-02")

	def test_scoped_admin_manages_only_their_own_access_profile(self):
		self._as(U_ADMIN_A)
		self.assertNotIn(U_COMPANY, set(frappe.get_list("Ads Access Profile", pluck="user", limit_page_length=0)))
		self.assertFalse(permissions.doc_allowed(frappe.get_doc("Ads Access Profile", U_COMPANY)))
		frappe.set_user("Administrator")
		permissions.clear_cache()
		self.assertIn(U_COMPANY, set(frappe.get_list("Ads Access Profile", pluck="user", limit_page_length=0)))

	def test_meta_enable_never_takes_over_another_companys_account(self):
		from excom.excom_ads.accounts import upsert_meta_ad_account

		acc = frappe.get_doc({"doctype": "Ads Account", "account_name": f"{P} Meta B", "provider": "Meta", "external_id": "act_3330003", "workspace": f"{P} WS B"})
		acc.flags.ignore_mandatory = True
		acc.insert(ignore_permissions=True)
		conn = frappe.get_doc({"doctype": "Excom Meta Connection", "connection_name": f"{P} BM A", "company": self.company, "system_user_token": "x" * 20})
		conn.insert(ignore_permissions=True)
		row = frappe._dict(asset_id="act_3330003", asset_name="takeover", extra=json.dumps({}))
		self.assertRaises(frappe.ValidationError, upsert_meta_ad_account, conn, row)
		self.assertIsNone(frappe.db.get_value("Ads Account", acc.name, "meta_connection"))

	def test_settings_form_shows_defaults_and_refuses_zero_counts(self):
		# Once any Ads Settings field is saved, Frappe loads the rest as empty: the form must still show
		# the defaults get_settings() uses, a save must not turn them into 0, and a stored 0 reads as default.
		from excom.excom_ads.settings import get_settings

		prior = frappe.db.sql("select field, value from `tabSingles` where doctype = 'Ads Settings'")
		try:
			frappe.db.sql("delete from `tabSingles` where doctype = 'Ads Settings'")
			frappe.db.sql("insert into `tabSingles` (doctype, field, value) values ('Ads Settings', 'bills_auto_submit', '0'), ('Ads Settings', 'gstr2b_missing_after_days', '0')")
			frappe.clear_document_cache("Ads Settings", "Ads Settings")
			self.assertEqual(get_settings().gstr2b_missing_after_days, 45, "a stored 0 for a count reads as the default")
			doc = frappe.get_doc("Ads Settings")
			doc.run_method("onload")
			self.assertEqual((doc.max_pages_per_call, doc.settle_days, doc.enabled), (200, 28, 1))
			doc.max_pages_per_call = 0
			self.assertRaises(frappe.ValidationError, doc.save, ignore_permissions=True)
		finally:
			frappe.db.rollback()
			frappe.db.sql("delete from `tabSingles` where doctype = 'Ads Settings'")
			for f, v in prior:
				frappe.db.sql("insert into `tabSingles` (doctype, field, value) values ('Ads Settings', %s, %s)", (f, v))
			frappe.db.commit()
			frappe.clear_document_cache("Ads Settings", "Ads Settings")
