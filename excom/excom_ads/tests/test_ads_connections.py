"""Connections health page: problems are named with a fix, scope applies, rights are required, and
Meta's token check records validity, scopes and expiry. No network.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_connections'}"
"""

import time

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import meta_connect as mc
from excom.excom_ads import connections, permissions

P = "QA Conn"
COMPANY_B = "_Test Ads Company B"
U_MKT = "qa_conn_mkt@example.com"
U_ADMIN_B = "qa_conn_admin_b@example.com"
USERS = [U_MKT, U_ADMIN_B]


def _items(health, section):
	sec = next((s for s in health["sections"] if s["key"] == section), None)
	return {i["key"]: i for i in (sec or {}).get("items", [])}


class TestAdsConnections(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		from excom.excom_ads.tests.test_ads_permissions import _ensure_company_b

		_ensure_company_b()
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		conn = ins({"doctype": "Excom Meta Connection", "connection_name": f"{P} BM", "business_id": "777", "company": cls.company, "app_id": "a1", "app_secret": "s1", "system_user_token": "TOK", "token_valid": 1, "token_scopes": "pages_messaging, pages_manage_metadata"})
		conn.append("assets", {"asset_type": "Ad Account", "asset_id": "act_777", "asset_name": f"{P} Ad Acc", "enabled": 1, "extra": "{}"})
		conn.append("assets", {"asset_type": "Instagram", "asset_id": "ig777", "asset_name": f"{P} IG", "enabled": 0, "extra": "{}"})
		conn.save(ignore_permissions=True)
		ins({"doctype": "Excom Meta Connection", "connection_name": f"{P} BM B", "business_id": "778", "company": COMPANY_B, "app_id": "a2", "system_user_token": "TOKB", "token_valid": 1})
		for u, role in ((U_MKT, "Excom Marketing"), (U_ADMIN_B, "Excom Admin")):
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "roles": [{"role": role}]})
		ins({"doctype": "User Permission", "user": U_MKT, "allow": "Company", "for_value": cls.company, "apply_to_all_doctypes": 1})
		ins({"doctype": "User Permission", "user": U_ADMIN_B, "allow": "Company", "for_value": COMPANY_B, "apply_to_all_doctypes": 1})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		for n in frappe.get_all("Excom Meta Connection", {"connection_name": ["like", f"{P}%"]}, pluck="name"):
			frappe.delete_doc("Excom Meta Connection", n, force=True, ignore_permissions=True)
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		# Each test starts from the same connection state (the token check rewrites it).
		frappe.db.set_value("Excom Meta Connection", f"{P} BM", {"token_valid": 1, "token_scopes": "pages_messaging, pages_manage_metadata", "token_expires_at": None, "data_access_expires_at": None, "token_checked_at": None})

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def test_missing_scope_for_a_used_feature_is_an_error_with_a_fix(self):
		h = connections.health()
		meta = _items(h, "meta")
		ads = meta[f"meta:{P} BM:scope:Ads reporting and billing"]
		self.assertEqual(ads["status"], "error", "an enabled ad account without ads_read is broken")
		self.assertIn("ads_read", ads["detail"])
		self.assertTrue(ads["hint"])
		ig = meta[f"meta:{P} BM:scope:Instagram DMs"]
		self.assertEqual(ig["status"], "off", "a feature nobody enabled is only a note")
		self.assertEqual(meta[f"meta:{P} BM:token"]["status"], "ok")
		self.assertEqual(meta[f"meta:{P} BM:token"]["action"]["method"], "excom.excom_ads.connections.test_meta")
		self.assertTrue(h["counts"]["error"] >= 1)
		self.assertIn("chain", {s["key"] for s in h["sections"]})

	def test_token_expiry_is_stored_and_flagged(self):
		soon = int(time.time()) + 5 * 86400
		orig = mc._get
		mc._get = lambda url, params: {"data": {"is_valid": True, "scopes": ["ads_read", "business_management"], "expires_at": soon, "data_access_expires_at": 0}}
		try:
			r = connections.test_meta(f"{P} BM")
		finally:
			mc._get = orig
		self.assertTrue(r["ok"])
		row = frappe.db.get_value("Excom Meta Connection", f"{P} BM", ["token_expires_at", "data_access_expires_at", "token_checked_at", "token_scopes"], as_dict=True)
		self.assertIsNotNone(row.token_expires_at)
		self.assertIsNone(row.data_access_expires_at, "0 means it never expires")
		self.assertIsNotNone(row.token_checked_at)
		token = _items(connections.health(), "meta")[f"meta:{P} BM:token"]
		self.assertEqual(token["status"], "warn")
		self.assertIn("expires in", token["detail"])

	def test_scope_and_rights(self):
		frappe.set_user(U_MKT)
		permissions.clear_cache()
		self.assertRaises(frappe.PermissionError, connections.health)
		self.assertRaises(frappe.PermissionError, connections.test_meta, f"{P} BM")
		frappe.set_user(U_ADMIN_B)
		permissions.clear_cache()
		h = connections.health()
		keys = set(_items(h, "meta"))
		self.assertFalse(any(k.startswith(f"meta:{P} BM:") for k in keys), "company B's admin never sees company A's connection")
		self.assertTrue(any(k.startswith(f"meta:{P} BM B") for k in keys))
		self.assertRaises(frappe.PermissionError, connections.test_meta, f"{P} BM")

	def test_daily_credential_check_alerts_and_resolves(self):
		name = f"{P} BM"
		orig = mc._get
		try:
			mc._get = lambda url, params: {"data": {"is_valid": False, "scopes": []}}
			connections.check_credentials()
			self.assertEqual(frappe.db.get_value("Ads Alert", {"dedupe_key": f"meta_token:{name}", "status": "Open"}, "severity"), "Critical")
			soon = int(time.time()) + 5 * 86400
			mc._get = lambda url, params: {"data": {"is_valid": True, "scopes": ["ads_read"], "expires_at": soon}}
			connections.check_credentials()
			self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"meta_token:{name}", "status": ["!=", "Resolved"]}), "a valid token resolves the invalid-token alert")
			self.assertEqual(frappe.db.get_value("Ads Alert", {"dedupe_key": f"meta_token_expiry:{name}", "status": "Open"}, "severity"), "High", "5 days left is High")
			mc._get = lambda url, params: {"data": {"is_valid": True, "scopes": ["ads_read"], "expires_at": 0}}
			connections.check_credentials()
			self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"meta_token_expiry:{name}", "status": ["!=", "Resolved"]}))
		finally:
			mc._get = orig
			frappe.db.delete("Ads Alert", {"dedupe_key": ["like", f"meta_token%{P}%"]})
			frappe.db.commit()
