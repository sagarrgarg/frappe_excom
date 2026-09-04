"""Template sync follows Meta's paging and normalises url/version. Meta mocked."""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.doctype.whatsapp_templates import whatsapp_templates as wt


class TestTemplateSync(FrappeTestCase):
	def test_pagination_and_normalisation(self):
		calls = []
		def fake(method, url, headers=None, **kw):
			calls.append(url)
			if "after=p2" in url:
				return {"data": [{"name": "t2", "status": "APPROVED", "language": "en", "category": "MARKETING", "id": "2", "components": []}]}
			return {"data": [{"name": "t1", "status": "APPROVED", "language": "en", "category": "UTILITY", "id": "1", "components": []}], "paging": {"next": url + "&after=p2"}}
		orig = wt.make_request; wt.make_request = fake
		try:
			r = wt._fetch_all_templates({"url": "https://graph.facebook.com/", "version": "26.0", "headers": {}}, "WABA1")
		finally:
			wt.make_request = orig
		self.assertEqual([t["name"] for t in r["data"]], ["t1", "t2"]); self.assertEqual(r["pages"], 2)
		self.assertTrue(calls[0].startswith("https://graph.facebook.com/v26.0/WABA1/message_templates?fields=")); self.assertIn("limit=100", calls[0])

	def test_retries_bare_request_when_fielded_call_is_rejected(self):
		"""A WABA that 400s on the explicit field list still syncs: we fall back to the plain edge."""
		calls = []
		def fake(method, url, headers=None, **kw):
			calls.append(url)
			if "fields=" in url:
				raise Exception("400 Client Error: Bad Request for url: " + url)
			return {"data": [{"name": "t1", "status": "APPROVED", "language": "en", "category": "UTILITY", "id": "1", "components": []}]}
		orig = wt.make_request; wt.make_request = fake
		try:
			r = wt._fetch_all_templates({"url": "https://graph.facebook.com", "version": "v26.0", "headers": {}}, "WABA1")
		finally:
			wt.make_request = orig
		self.assertEqual(len(r["data"]), 1); self.assertEqual(len(calls), 2); self.assertNotIn("fields=", calls[1])

	def test_fetch_is_whitelisted_for_desk_button(self):
		self.assertTrue(getattr(wt.fetch, "whitelisted", False) or wt.fetch in frappe.whitelisted or "excom.excom.doctype.whatsapp_templates.whatsapp_templates.fetch" in [f"{getattr(x, '__module__', '')}.{getattr(x, '__name__', '')}" for x in frappe.whitelisted])

	def test_business_portfolio_id_is_healed_to_the_real_waba(self):
		"""A Business Portfolio id in Business Account ID: Meta says 'nonexisting field (message_templates)';
		we find the WABA under it, correct the account and retry."""
		frappe.set_user("Administrator")
		for stale in frappe.get_all("Excom Channel Account", {"account_name": "QA WA Heal"}, pluck="name"):
			frappe.delete_doc("Excom Channel Account", stale, force=True, ignore_permissions=True)
		frappe.db.delete("WhatsApp Templates", {"actual_name": "healed"}); frappe.db.commit()
		acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Heal", "channel": "whatsapp", "status": "Active", "wa_phone_id": "P1", "wa_business_id": "BIZ1", "wa_version": "v26.0", "wa_token": "qa-token-000"}).insert(ignore_permissions=True)
		frappe.db.commit()
		seen = []
		class Err(Exception):
			pass
		def fake(method, url, headers=None, **kw):
			seen.append(url)
			if "/BIZ1/message_templates" in url:
				raise Err("(#100) Tried accessing nonexisting field (message_templates) on node type (Business)")
			if "/BIZ1/owned_whatsapp_business_accounts" in url:
				return {"data": [{"id": "WABA9", "name": "GGIL"}]}
			if "client_whatsapp_business_accounts" in url:
				return {"data": []}
			if "/WABA9/message_templates" in url:
				return {"data": [{"name": "healed", "status": "APPROVED", "language": "en", "category": "UTILITY", "id": "9", "components": []}]}
			return {"data": []}
		orig = wt.make_request; wt.make_request = fake
		try:
			wt.fetch()
		finally:
			wt.make_request = orig
		self.assertEqual(frappe.db.get_value("Excom Channel Account", acc.name, "wa_business_id"), "WABA9")
		self.assertTrue(frappe.db.exists("WhatsApp Templates", {"actual_name": "healed"}))
		frappe.db.delete("WhatsApp Templates", {"actual_name": "healed"})
		frappe.delete_doc("Excom Channel Account", acc.name, force=True, ignore_permissions=True); frappe.db.commit()
