"""Excom Ads scope and rights: no user sees another company's, workspace's, brand's or account's data
(acceptance test 9), sensitive rights need a profile, alerts dedupe and reach only in-scope people.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_permissions'}"
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom_ads import alerts, permissions

COMPANY_B = "_Test Ads Company B"
U_NONE = "qa_ads_none@example.com"
U_COMPANY = "qa_ads_company@example.com"
U_WS = "qa_ads_ws@example.com"
U_BRAND = "qa_ads_brand@example.com"
U_ACCOUNT = "qa_ads_account@example.com"
U_ADMIN = "qa_ads_admin@example.com"
U_ADMIN_SCOPED = "qa_ads_admin_scoped@example.com"
U_FIN = "qa_ads_fin@example.com"
USERS = [U_NONE, U_COMPANY, U_WS, U_BRAND, U_ACCOUNT, U_ADMIN, U_ADMIN_SCOPED, U_FIN]
P = "QA Perm"


def _ensure_company_b():
	if frappe.db.exists("Company", COMPANY_B):
		return
	frappe.get_doc({"doctype": "Company", "company_name": COMPANY_B, "abbr": "_TACB", "default_currency": "INR", "country": "India", "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard"}).insert(ignore_permissions=True)


class TestAdsPermissions(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company_a = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		_ensure_company_b()
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS A", "company": cls.company_a})
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS A2", "company": cls.company_a})
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS B", "company": COMPANY_B})
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand A", "workspace": f"{P} WS A"})
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand A-other", "workspace": f"{P} WS A"})
		cls.acc_a = ins({"doctype": "Ads Account", "account_name": f"{P} Acc A", "provider": "Google", "external_id": "111-000-0001", "workspace": f"{P} WS A", "brand": f"{P} Brand A"}).name
		cls.acc_a2 = ins({"doctype": "Ads Account", "account_name": f"{P} Acc A2", "provider": "Google", "external_id": "111-000-0002", "workspace": f"{P} WS A2"}).name
		cls.acc_b = ins({"doctype": "Ads Account", "account_name": f"{P} Acc B", "provider": "Google", "external_id": "111-000-0003", "workspace": f"{P} WS B"}).name
		# Metric rows: A with brand, A without brand (unallocated), A2, B.
		for acc, brand, ext in ((cls.acc_a, f"{P} Brand A", "e1"), (cls.acc_a, None, "e2"), (cls.acc_a, f"{P} Brand A-other", "e5"), (cls.acc_a2, None, "e3"), (cls.acc_b, None, "e4")):
			a = frappe.get_doc("Ads Account", acc)
			ins({"doctype": "Ads Metric Snapshot", "account": acc, "workspace": a.workspace, "company": a.company, "brand": brand, "provider": "Google", "level": "Campaign", "entity_external_id": ext, "date": "2026-10-01", "spend": 100, "currency": "INR"})
		for u in USERS:
			roles = ["Excom Admin"] if u in (U_ADMIN, U_ADMIN_SCOPED) else ["Excom Marketing"]
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": r} for r in roles]})
		grants = [(U_COMPANY, "Company", cls.company_a), (U_WS, "Ads Workspace", f"{P} WS A"), (U_BRAND, "Ads Brand", f"{P} Brand A"), (U_ACCOUNT, "Ads Account", cls.acc_a2), (U_ADMIN_SCOPED, "Company", COMPANY_B), (U_FIN, "Company", cls.company_a)]
		for u, dt, name in grants:
			ins({"doctype": "User Permission", "user": u, "allow": dt, "for_value": name, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_FIN, "view_finance": 1, "export_data": 1})
		ins({"doctype": "Ads Access Profile", "user": U_COMPANY, "view_finance": 0})
		frappe.db.commit()
		frappe.clear_cache()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name")
		for dt in ("Ads Metric Snapshot", "Ads Alert", "Ads Sync Job"):
			frappe.db.delete(dt, {"account": ["in", accs or ["-"]]})
		frappe.db.delete("Ads Alert", {"dedupe_key": ["like", "qa-perm:%"]})
		frappe.db.delete("Ads Notification Policy", {"policy_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Notification Recipient", {"parent": ["like", f"{P}%"]})
		frappe.db.delete("Ads Account", {"name": ["in", accs or ["-"]]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", USERS]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		frappe.db.delete("Notification Log", {"for_user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")

	def _as(self, user):
		frappe.set_user(user)
		permissions.clear_cache()

	def _accounts(self, user):
		self._as(user)
		return set(frappe.get_list("Ads Account", filters={"account_name": ["like", f"{P}%"]}, pluck="name"))

	def _snapshots(self, user):
		self._as(user)
		return set(frappe.get_list("Ads Metric Snapshot", filters={"account": ["in", [self.acc_a, self.acc_a2, self.acc_b]]}, pluck="entity_external_id"))

	# ─── scope ────────────────────────────────────────────────────────────────

	def test_marketing_user_without_grants_sees_nothing(self):
		self.assertEqual(self._accounts(U_NONE), set())
		self.assertEqual(self._snapshots(U_NONE), set())
		self.assertRaises(frappe.PermissionError, permissions.require_access)
		self._as(U_NONE)
		self.assertFalse(frappe.has_permission("Ads Account", "read", doc=frappe.get_doc("Ads Account", self.acc_a)))

	def test_company_grant_hides_other_company(self):
		self.assertEqual(self._accounts(U_COMPANY), {self.acc_a, self.acc_a2})
		self.assertEqual(self._snapshots(U_COMPANY), {"e1", "e2", "e3", "e5"})
		self._as(U_COMPANY)
		self.assertFalse(frappe.has_permission("Ads Account", "read", doc=frappe.get_doc("Ads Account", self.acc_b)))
		self.assertTrue(frappe.has_permission("Ads Account", "read", doc=frappe.get_doc("Ads Account", self.acc_a)))

	def test_workspace_grant(self):
		self.assertEqual(self._accounts(U_WS), {self.acc_a})
		self._as(U_WS)
		self.assertEqual(set(frappe.get_list("Ads Workspace", filters={"workspace_name": ["like", f"{P}%"]}, pluck="name")), {f"{P} WS A"})

	def test_brand_grant_is_strict_about_unallocated_rows(self):
		self.assertEqual(self._accounts(U_BRAND), {self.acc_a})
		# e2 has no brand of its own and inherits its account's (Brand A); e5 is allocated to another
		# brand and e3 is unallocated (its account has no brand): neither reaches a Brand A user.
		self.assertEqual(self._snapshots(U_BRAND), {"e1", "e2"}, "brand-scoped users never see unallocated or other-brand rows")

	def test_account_grant(self):
		self.assertEqual(self._accounts(U_ACCOUNT), {self.acc_a2})
		self.assertEqual(self._snapshots(U_ACCOUNT), {"e3"})

	def test_admin_unscoped_sees_all_and_scoped_admin_is_narrowed(self):
		self.assertEqual(self._accounts(U_ADMIN), {self.acc_a, self.acc_a2, self.acc_b})
		self.assertEqual(self._accounts(U_ADMIN_SCOPED), {self.acc_b})

	def test_scope_filters_match_query_conditions(self):
		# API code that reads with frappe.get_all must apply the same scope.
		for u in (U_COMPANY, U_WS, U_BRAND, U_ACCOUNT, U_NONE):
			listed = self._snapshots(u)
			self._as(u)
			f = permissions.scope_filters("Ads Metric Snapshot")
			frappe.set_user("Administrator")
			got = set(frappe.get_all("Ads Metric Snapshot", filters={**f, "account": ["in", [self.acc_a, self.acc_a2, self.acc_b]]} if "account" not in f else {**f}, pluck="entity_external_id"))
			got &= {"e1", "e2", "e3", "e4", "e5"}
			self.assertEqual(got, listed, u)

	def test_access_profile_is_private(self):
		self._as(U_COMPANY)
		self.assertEqual(set(frappe.get_list("Ads Access Profile", pluck="user")), {U_COMPANY})
		self._as(U_ADMIN)
		self.assertTrue({U_COMPANY, U_FIN} <= set(frappe.get_list("Ads Access Profile", pluck="user")))

	# ─── rights ───────────────────────────────────────────────────────────────

	def test_capabilities_need_a_profile_or_admin_role(self):
		self.assertTrue(permissions.has_capability("view_finance", U_FIN))
		self.assertTrue(permissions.has_capability("export_data", U_FIN))
		self.assertFalse(permissions.has_capability("manage_budgets", U_FIN))
		self.assertFalse(permissions.has_capability("view_finance", U_COMPANY), "a profile with the flag off grants nothing")
		self.assertFalse(permissions.has_capability("view_finance", U_NONE))
		self.assertTrue(permissions.has_capability("manage_connections", U_ADMIN))
		self.assertFalse(permissions.has_capability("enable_automation", U_ADMIN), "automation always needs an explicit profile")
		self._as(U_COMPANY)
		self.assertRaises(frappe.PermissionError, permissions.require, "view_finance")

	def test_sync_now_needs_manage_connections_and_scope(self):
		from excom.excom_ads import api

		self._as(U_COMPANY)
		self.assertRaises(frappe.PermissionError, api.sync_now, self.acc_a)
		self._as(U_ADMIN_SCOPED)
		self.assertRaises(frappe.PermissionError, api.sync_now, self.acc_a)

	def test_finance_fields_are_level_one(self):
		meta = frappe.get_meta("Ads Account")
		for f in ("reported_amount_spent", "reported_spend_cap", "reported_amount_due", "billing_status"):
			self.assertEqual(meta.get_field(f).permlevel, 1, f)
		self._as(U_COMPANY)
		doc = frappe.get_doc("Ads Account", self.acc_a)
		self.assertNotIn(1, doc.get_permlevel_access("read"), "a marketing user cannot read billing amounts on the desk")

	# ─── alerts ───────────────────────────────────────────────────────────────

	def test_alert_dedupes_escalates_and_resolves(self):
		acc = frappe.get_doc("Ads Account", self.acc_a)
		n1 = alerts.raise_alert("Sync Failed", "Medium", "QA failing", "first", ref=acc, dedupe_key="qa-perm:sync")
		n2 = alerts.raise_alert("Sync Failed", "Medium", "QA failing", "again", ref=acc, dedupe_key="qa-perm:sync")
		self.assertEqual(n1, n2)
		a = frappe.get_doc("Ads Alert", n1)
		self.assertEqual((a.occurrences, a.company, a.workspace, a.account, a.status), (2, acc.company, acc.workspace, acc.name, "Open"))
		alerts.raise_alert("Sync Failed", "Info", "QA failing", "lower", ref=acc, dedupe_key="qa-perm:sync")
		self.assertEqual(frappe.db.get_value("Ads Alert", n1, "severity"), "Medium", "an open alert is never downgraded")
		self.assertEqual(alerts.resolve_alert("qa-perm:sync"), 1)
		n3 = alerts.raise_alert("Sync Failed", "Medium", "QA failing", "after resolve", ref=acc, dedupe_key="qa-perm:sync")
		self.assertNotEqual(n3, n1, "a problem that comes back after resolution is a new alert")

	def test_qa_fix_long_dedupe_keys_are_stored_and_found_again(self):
		acc = frappe.get_doc("Ads Account", self.acc_a)
		key200 = "qa-perm:report_delivery:" + "x" * 176
		self.assertEqual(len(key200), 200)
		n = alerts.raise_alert("Sync Failed", "Medium", "QA Fix long key", "x", ref=acc, dedupe_key=key200)
		self.assertTrue(n, "a 200-character key is stored (dedupe_key holds 255)")
		self.assertEqual(frappe.db.get_value("Ads Alert", n, "dedupe_key"), key200)
		self.assertEqual(alerts.raise_alert("Sync Failed", "Medium", "QA Fix long key", "again", ref=acc, dedupe_key=key200), n)
		key400 = "qa-perm:bill-fetch:" + "y" * 381
		m = alerts.raise_alert("Sync Failed", "Medium", "QA Fix longer key", "x", ref=acc, dedupe_key=key400)
		self.assertTrue(m, "a key longer than the field is hashed, not dropped")
		stored = frappe.db.get_value("Ads Alert", m, "dedupe_key")
		self.assertLessEqual(len(stored), 255)
		self.assertTrue(stored.startswith("qa-perm:bill-fetch:"))
		self.assertEqual(stored, alerts.alert_key(key400), "deterministic")
		self.assertNotEqual(alerts.alert_key(key400), alerts.alert_key(key400[:-1] + "z"))
		self.assertEqual(alerts.raise_alert("Sync Failed", "Medium", "QA Fix longer key", "again", ref=acc, dedupe_key=key400), m, "the same long key finds the same alert")
		self.assertEqual(frappe.db.get_value("Ads Alert", m, "occurrences"), 2)
		self.assertEqual(alerts.resolve_alert(key400), 1)
		self.assertEqual(alerts.resolve_alert(key200), 1)

	def test_alert_delivery_reaches_only_in_scope_recipients(self):
		pol = frappe.get_doc({"doctype": "Ads Notification Policy", "policy_name": f"{P} desk", "min_severity": "Medium", "via_desk": 1, "recipients": [{"recipient_type": "Role", "role": "Excom Marketing"}]}).insert(ignore_permissions=True)
		acc_b = frappe.get_doc("Ads Account", self.acc_b)
		name = alerts.raise_alert("Sync Failed", "High", "QA B down", "x", ref=acc_b, dedupe_key="qa-perm:b")
		frappe.db.set_value("Ads Alert", name, "notified_severity", None)
		alerts.deliver(name)
		got = set(frappe.get_all("Notification Log", filters={"document_type": "Ads Alert", "document_name": name}, pluck="for_user"))
		self.assertFalse(got & {U_COMPANY, U_WS, U_BRAND, U_ACCOUNT, U_NONE}, "no company-A user hears about company B")
		acc_a = frappe.get_doc("Ads Account", self.acc_a)
		name_a = alerts.raise_alert("Sync Failed", "High", "QA A down", "x", ref=acc_a, dedupe_key="qa-perm:a")
		alerts.deliver(name_a)
		got_a = set(frappe.get_all("Notification Log", filters={"document_type": "Ads Alert", "document_name": name_a}, pluck="for_user"))
		self.assertTrue({U_COMPANY, U_WS, U_FIN} <= got_a, got_a)
		self.assertNotIn(U_NONE, got_a)
		self.assertNotIn(U_ACCOUNT, got_a)
		pol.delete(ignore_permissions=True)

	def test_alert_actions_respect_scope(self):
		from excom.excom_ads import api

		acc_b = frappe.get_doc("Ads Account", self.acc_b)
		name = alerts.raise_alert("Sync Failed", "Medium", "QA B", "x", ref=acc_b, dedupe_key="qa-perm:b2")
		self._as(U_COMPANY)
		self.assertRaises(frappe.PermissionError, api.acknowledge_alert, name)
		frappe.set_user("Administrator")
		crit = alerts.raise_alert("Token Problem", "Critical", "QA token", "x", ref=frappe.get_doc("Ads Account", self.acc_a), dedupe_key="qa-perm:crit")
		self._as(U_COMPANY)
		self.assertRaises(frappe.ValidationError, api.snooze_alert, crit, 24)
		api.acknowledge_alert(crit)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Ads Alert", crit, "status"), "Acknowledged")


class TestEveryAdsDoctypeIsScoped(FrappeTestCase):
	def test_hooks_cover_every_ads_doctype(self):
		hooks = frappe.get_hooks()
		pqc, hp = hooks.get("permission_query_conditions") or {}, hooks.get("has_permission") or {}
		missing = []
		for dt in frappe.get_all("DocType", filters={"module": "Excom Ads", "istable": 0, "issingle": 0}, pluck="name"):
			if "excom.excom_ads.permissions.get_permission_query_conditions" not in (pqc.get(dt) or []):
				missing.append(f"{dt}: permission_query_conditions")
			if "excom.excom_ads.permissions.has_permission" not in (hp.get(dt) or []):
				missing.append(f"{dt}: has_permission")
		self.assertEqual(missing, [], "register every Ads DocType in hooks.py (see excom_ads/README.md)")

	def test_every_data_doctype_carries_a_scope_field(self):
		exempt = {"Ads Access Profile"}
		bad = []
		for dt in frappe.get_all("DocType", filters={"module": "Excom Ads", "istable": 0, "issingle": 0}, pluck="name"):
			if dt in exempt or dt in permissions.SELF_FIELD:
				continue
			meta = frappe.get_meta(dt)
			if not any(meta.has_field(f) for f in permissions.DIMENSIONS):
				bad.append(dt)
		self.assertEqual(bad, [], "every Ads DocType needs company, workspace, brand or account")
