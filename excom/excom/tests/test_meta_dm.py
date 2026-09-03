"""Instagram / Messenger via Graph API: ingest transform, idempotency, identity ids, window rule, outbound payload. No network."""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.channels.meta_dm import service as dm
from excom.excom.tests.test_core_flows import _cleanup

CONV = {
	"id": "t_qa_conv_1", "updated_time": "2026-09-03T10:00:00+0000",
	"participants": {"data": [{"id": "999001", "username": "qa_ravi"}, {"id": "222", "username": "ourbrand"}]},
	"messages": {"data": [
		{"id": "m_qa_2", "created_time": "2026-09-03T10:00:00+0000", "from": {"id": "222", "username": "ourbrand"}, "message": "Thanks!"},
		{"id": "m_qa_1", "created_time": "2026-09-03T09:59:00+0000", "from": {"id": "999001", "username": "qa_ravi"}, "message": "QA Kinds: price of cones?", "attachments": {"data": [{"image_data": {"url": "https://example.com/qa.jpg"}}]}},
	]},
}


class TestMetaDm(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass(); _cleanup(); cls._clean_own()
		frappe.set_user("Administrator")
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA Instagram", "channel": "instagram", "status": "Active", "meta_page_id": "111", "meta_ig_user_id": "222", "meta_api_version": "v21.0"}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean_own(); _cleanup(); super().tearDownClass()

	@classmethod
	def _clean_own(cls):
		accs = frappe.get_all("Excom Channel Account", {"account_name": ["like", "QA %"]}, pluck="name")
		for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, pluck="name"):
			frappe.db.delete("Excom Message", {"thread": t}); frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for oi in frappe.get_all("Omni Identity Channel", {"channel_user_id": "999001"}, pluck="parent"):
			frappe.db.delete("Excom Message", {"omni_identity": oi})
			frappe.db.delete("Omni Identity Link", {"parent": oi}); frappe.db.delete("Omni Identity Channel", {"parent": oi}); frappe.db.delete("Omni Identity Alias", {"parent": oi}); frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for a in accs:
			frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		accs = [self.acc.name]
		for t in frappe.get_all("Excom Thread", {"account": ["in", accs]}, pluck="name"):
			frappe.db.delete("Excom Message", {"thread": t}); frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for oi in frappe.get_all("Omni Identity Channel", {"channel_user_id": "999001"}, pluck="parent"):
			frappe.db.delete("Excom Message", {"omni_identity": oi})
			frappe.db.delete("Omni Identity Link", {"parent": oi}); frappe.db.delete("Omni Identity Channel", {"parent": oi}); frappe.db.delete("Omni Identity Alias", {"parent": oi}); frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_ingest_conversation_is_idempotent_and_skips_own(self):
		n = dm.ingest_conversation(self.acc, CONV)
		self.assertEqual(n, 1)  # only the customer's message; ours is skipped
		self.assertEqual(dm.ingest_conversation(self.acc, CONV), 0)
		msg = frappe.get_doc("Excom Message", {"provider_message_id": "m_qa_1"})
		self.assertEqual(msg.channel, "instagram"); self.assertEqual(msg.message_type, "Image"); self.assertEqual(msg.media_file, "https://example.com/qa.jpg")
		thread = frappe.get_doc("Excom Thread", msg.thread)
		self.assertEqual(thread.channel, "instagram"); self.assertEqual(thread.account, self.acc.name); self.assertEqual(thread.unread_count, 1)
		oi = frappe.get_doc("Omni Identity", thread.omni_identity)
		self.assertEqual(dm.recipient_id(oi, "instagram"), "999001")
		self.assertEqual(oi.display_name, "qa_ravi")

	def test_window_and_outbound_payload(self):
		dm.ingest_conversation(self.acc, CONV)
		thread = frappe.get_doc("Excom Thread", {"account": self.acc.name})
		frappe.db.set_value("Excom Thread", thread.name, "last_inbound_at", now_datetime()); thread.reload()
		self.assertTrue(dm.window_status(thread)["window_open"])
		sent = {}
		orig = dm._post
		dm._post = lambda url, params, payload: (sent.update({"url": url, "payload": payload}) or {"message_id": "m_out_1"})
		try:
			acc = frappe.get_doc("Excom Channel Account", self.acc.name)
			acc.meta_page_token = "qa-token-000000000"; acc.save(ignore_permissions=True)
			r = dm.send_text(acc, "999001", "Hello from QA", thread=thread)
			self.assertEqual(r["provider_message_id"], "m_out_1")
			self.assertTrue(sent["url"].endswith("/111/messages")); self.assertEqual(sent["payload"]["recipient"]["id"], "999001"); self.assertEqual(sent["payload"]["messaging_type"], "RESPONSE")
			frappe.db.set_value("Excom Thread", thread.name, "last_inbound_at", add_to_date(now_datetime(), hours=-30)); thread.reload()
			self.assertFalse(dm.window_status(thread)["window_open"])
			with self.assertRaises(frappe.ValidationError):
				dm.send_text(acc, "999001", "too late", thread=thread)
			frappe.db.set_value("Excom Channel Account", acc.name, "meta_human_agent_tag", 1)
			dm.send_text(acc, "999001", "human agent follow-up", thread=thread)
			self.assertEqual(sent["payload"]["tag"], "HUMAN_AGENT")
		finally:
			dm._post = orig

	def test_webhook_messaging_accelerator(self):
		entry = {"id": "111", "time": 1, "messaging": [{"sender": {"id": "999001"}, "recipient": {"id": "111"}, "timestamp": 1, "message": {"mid": "m_qa_wh_1", "text": "via webhook"}}, {"sender": {"id": "222"}, "recipient": {"id": "999001"}, "message": {"mid": "m_echo", "text": "x", "is_echo": True}}]}
		self.assertEqual(dm.handle_messaging(entry, "instagram"), 1)
		self.assertTrue(frappe.db.exists("Excom Message", {"provider_message_id": "m_qa_wh_1"}))
		self.assertFalse(frappe.db.exists("Excom Message", {"provider_message_id": "m_echo"}))
