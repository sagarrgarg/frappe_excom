"""Meta Business connection: discovery from Graph payloads (mocked), enable/disable into channel accounts + intake sources. No network."""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import meta_connect as mc

PAGES = {"data": [{"id": "111", "name": "QA Brand Page", "category": "Retail", "access_token": "PAGE_TOKEN_111", "instagram_business_account": {"id": "222", "username": "qa_brand"}}]}
FORMS = {"data": [{"id": "333", "name": "QA Diwali Form", "status": "ACTIVE", "leads_count": 12}]}
WABAS = {"data": [{"id": "444", "name": "QA WABA"}]}
NUMBERS = {"data": [{"id": "555", "display_phone_number": "+91 99000 00000", "verified_name": "QA Brand", "quality_rating": "GREEN"}]}


def fake_get(url, params):
	if url.endswith("/me/accounts"): return PAGES
	if url.endswith("/owned_pages") or url.endswith("/client_pages"): return {"data": []}
	if url.endswith("/111/leadgen_forms"): return FORMS
	if url.endswith("/owned_whatsapp_business_accounts"): return WABAS
	if url.endswith("/client_whatsapp_business_accounts"): return {"data": []}
	if url.endswith("/444/phone_numbers"): return NUMBERS
	if "/debug_token" in url: return {"data": {"is_valid": True, "scopes": ["pages_messaging", "leads_retrieval"]}}
	raise AssertionError(f"unexpected GET {url}")


class TestMetaConnect(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass(); cls._clean()
		frappe.set_user("Administrator")
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		conn = frappe.get_doc({"doctype": "Excom Meta Connection", "connection_name": "QA Meta BM", "business_id": "999", "company": company, "app_id": "app1", "app_secret": "secret1", "system_user_token": "SYS_TOKEN", "webhook_verify_token": "qa-verify"})
		conn.insert(ignore_permissions=True); frappe.db.commit()
		cls.name = conn.name
		cls._orig = (mc._get, mc._post)
		mc._get = fake_get
		mc._post = lambda url, params, payload=None: {"success": True}

	@classmethod
	def tearDownClass(cls):
		mc._get, mc._post = cls._orig
		cls._clean(); super().tearDownClass()

	@classmethod
	def _clean(cls):
		for a in frappe.get_all("Excom Channel Account", {"account_name": ["like", "% · QA %"]}, pluck="name") + frappe.get_all("Excom Channel Account", {"account_name": ["like", "% · @qa_%"]}, pluck="name"):
			for t in frappe.get_all("Excom Thread", {"account": a}, pluck="name"):
				frappe.db.delete("Excom Message", {"thread": t}); frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
		for s in frappe.get_all("Excom Intake Source", {"source_name": ["like", "Meta · QA %"]}, pluck="name"):
			frappe.delete_doc("Excom Intake Source", s, force=True, ignore_permissions=True)
		if frappe.db.exists("Excom Meta Connection", "QA Meta BM"):
			frappe.delete_doc("Excom Meta Connection", "QA Meta BM", force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_discover_then_enable_each_asset(self):
		r = mc.discover(self.name)
		self.assertEqual(r["found"], {"Page": 1, "Instagram": 1, "Lead Form": 1, "WhatsApp Number": 1})
		conn = frappe.get_doc("Excom Meta Connection", self.name)
		self.assertEqual({a.asset_type for a in conn.assets}, {"Page", "Instagram", "Lead Form", "WhatsApp Number"})
		# webhook secrets + verify token are picked up
		self.assertIn("secret1", mc.app_secrets())
		# Page → Messenger account with the page token
		e = mc.enable_asset(self.name, "Page", "111")
		acc = frappe.get_doc("Excom Channel Account", e["linked_name"])
		self.assertEqual(acc.channel, "messenger"); self.assertEqual(acc.meta_page_id, "111"); self.assertEqual(acc.get_password("meta_page_token"), "PAGE_TOKEN_111"); self.assertEqual(acc.status, "Active")
		# Instagram → instagram account bound to the page
		e = mc.enable_asset(self.name, "Instagram", "222")
		ig = frappe.get_doc("Excom Channel Account", e["linked_name"])
		self.assertEqual((ig.channel, ig.meta_page_id, ig.meta_ig_user_id), ("instagram", "111", "222"))
		# Lead form → intake source pulling with the page token
		e = mc.enable_asset(self.name, "Lead Form", "333")
		src = frappe.get_doc("Excom Intake Source", e["linked_name"])
		self.assertEqual((src.source_type, src.form_id, src.page_id, src.enabled, src.mode), ("Meta Lead Ads", "333", "111", 1, "Both"))
		self.assertEqual(src.get_password("access_token"), "PAGE_TOKEN_111")
		# WhatsApp number → whatsapp account with system token + app secret + verify token
		e = mc.enable_asset(self.name, "WhatsApp Number", "555")
		wa = frappe.get_doc("Excom Channel Account", e["linked_name"])
		self.assertEqual((wa.channel, wa.wa_phone_id, wa.wa_business_id, wa.wa_webhook_verify_token), ("whatsapp", "555", "444", "qa-verify"))
		self.assertEqual(wa.get_password("wa_token"), "SYS_TOKEN")
		# re-discover keeps enabled state; enabling twice reuses the record
		mc.discover(self.name)
		conn = frappe.get_doc("Excom Meta Connection", self.name)
		self.assertTrue(all(a.enabled for a in conn.assets))
		e2 = mc.enable_asset(self.name, "Page", "111")
		self.assertEqual(e2["linked_name"], acc.name)
		# disable → account inactive, source disabled
		mc.enable_asset(self.name, "Page", "111", 0)
		self.assertEqual(frappe.db.get_value("Excom Channel Account", acc.name, "status"), "Inactive")
		mc.enable_asset(self.name, "Lead Form", "333", 0)
		self.assertEqual(frappe.db.get_value("Excom Intake Source", src.name, "enabled"), 0)
		# token debug
		d = mc.debug_token(self.name)
		self.assertTrue(d["is_valid"]); self.assertEqual(frappe.db.get_value("Excom Meta Connection", self.name, "token_valid"), 1)
