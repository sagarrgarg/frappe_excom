"""Media goes to Meta by upload + id, never as a login-protected link (Meta error 131053)."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import whatsapp_service as ws


class FakeResp:
	def __init__(self, status, body): self.status_code, self._b = status, body; self.content = b"x"; self.text = json.dumps(body); self.ok = status < 400
	def json(self): return self._b


class TestWhatsAppMedia(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Media", "channel": "whatsapp", "status": "Active", "wa_phone_id": "123", "wa_version": "v21.0", "wa_token": "qa-token-0000000000"}).insert(ignore_permissions=True)
		self.file = frappe.get_doc({"doctype": "File", "file_name": "qa-media.png", "is_private": 1, "content": b"\\x89PNG\\r\\n\\x1a\\nQA"}).insert(ignore_permissions=True)
		self.calls = []
		self._orig = ws.http_requests.post
		def fake_post(url, **kw):
			self.calls.append((url, kw))
			if url.endswith("/media"): return FakeResp(200, {"id": "MEDIA123"})
			return FakeResp(200, {"messages": [{"id": "wamid.QA"}]})
		ws.http_requests.post = fake_post

	def tearDown(self):
		ws.http_requests.post = self._orig
		frappe.delete_doc("File", self.file.name, force=True, ignore_permissions=True)
		frappe.delete_doc("Excom Channel Account", self.acc.name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_private_file_is_uploaded_and_sent_by_id(self):
		self.assertTrue(self.file.file_url.startswith("/private/files/"))
		r = ws.send_media_message(self.acc, "+919900000001", "image", self.file.file_url, caption="hi")
		self.assertEqual(r.get("provider_message_id"), "wamid.QA")
		upload, send = self.calls
		self.assertTrue(upload[0].endswith("/123/media")); self.assertEqual(upload[1]["data"]["messaging_product"], "whatsapp"); self.assertIn("file", upload[1]["files"])
		payload = send[1]["json"]
		self.assertEqual(payload["image"], {"id": "MEDIA123", "caption": "hi"})

	def test_external_link_is_passed_through(self):
		ws.send_media_message(self.acc, "+919900000001", "document", "https://cdn.example.com/a.pdf")
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(self.calls[0][1]["json"]["document"], {"link": "https://cdn.example.com/a.pdf"})

	def test_template_header_uses_media_id(self):
		from excom.excom.api.chat import _header_media
		self.assertEqual(_header_media(self.acc, self.file.file_url), {"id": "MEDIA123"})
