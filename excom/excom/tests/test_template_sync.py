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
		self.assertTrue(calls[0].startswith("https://graph.facebook.com/v26.0/WABA1/message_templates?fields=")); self.assertIn("limit=200", calls[0])

	def test_fetch_is_whitelisted_for_desk_button(self):
		self.assertTrue(getattr(wt.fetch, "whitelisted", False) or wt.fetch in frappe.whitelisted or "excom.excom.doctype.whatsapp_templates.whatsapp_templates.fetch" in [f"{getattr(x, '__module__', '')}.{getattr(x, '__name__', '')}" for x in frappe.whitelisted])
