"""Instagram / Messenger via Graph API: ingest transform, idempotency, identity ids, window rule, outbound payload. No network."""

from unittest.mock import patch

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

	# ─── long Instagram ids (CharacterLengthExceededError at 140) ─────────────

	LONG_ID = "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQxN" + "x" * 157  # 200 characters

	def test_long_instagram_id_via_webhook_is_saved_and_deduped(self):
		self.assertEqual(len(self.LONG_ID), 200)
		entry = {"id": "111", "time": 1, "messaging": [{"sender": {"id": "999001"}, "recipient": {"id": "111"}, "timestamp": 1, "message": {"mid": self.LONG_ID, "text": "long id via webhook"}}]}
		self.assertEqual(dm.handle_messaging(entry, "instagram"), 1)
		self.assertEqual(dm.handle_messaging(entry, "instagram"), 0)  # same id delivered again
		ids = frappe.get_all("Excom Message", {"provider_message_id": self.LONG_ID}, pluck="provider_message_id")
		self.assertEqual(ids, [self.LONG_ID])  # stored whole, once

	def test_long_instagram_id_via_poll_is_saved_and_deduped(self):
		conv = {"id": "t_qa_conv_long", "messages": {"data": [{"id": self.LONG_ID, "created_time": "2026-09-03T10:00:00+0000", "from": {"id": "999001", "username": "qa_ravi"}, "message": "long id via poll"}]}}
		self.assertEqual(dm.ingest_conversation(self.acc, conv), 1)
		self.assertEqual(dm.ingest_conversation(self.acc, conv), 0)
		ids = frappe.get_all("Excom Message", {"provider_message_id": self.LONG_ID}, pluck="provider_message_id")
		self.assertEqual(ids, [self.LONG_ID])

	# ─── polling: page size, retry, paging, error log ──────────────────────────

	def _token_account(self):
		acc = frappe.get_doc("Excom Channel Account", self.acc.name)
		acc.meta_page_token = "qa-token-000000000"
		acc.meta_last_polled_at = None
		acc.save(ignore_permissions=True)
		return acc

	@staticmethod
	def _now_graph(minutes_ago=0):
		from frappe.utils import add_to_date
		return add_to_date(now_datetime(), minutes=-minutes_ago).strftime("%Y-%m-%dT%H:%M:%S+0000")

	def _conv(self, conv_id, msg_id, text):
		return {"id": conv_id, "updated_time": self._now_graph(), "messages": {"data": [{"id": msg_id, "created_time": self._now_graph(), "from": {"id": "999001", "username": "qa_ravi"}, "message": text}]}}

	def test_poll_retries_smaller_when_graph_says_too_much_data(self):
		self._token_account()
		calls = []
		too_much = '{"error":{"code":1,"message":"Please reduce the amount of data you\'re asking for, then retry your request"}}'

		def fake_get(url, params):
			calls.append((url, dict(params)))
			if url.endswith("/conversations"):
				if params["limit"] == dm.CONVERSATIONS_PER_PAGE:
					raise dm.GraphError(500, too_much)
				return {"data": [self._conv("t_qa_conv_r", "m_qa_retry", "after retry")]}
			return {}  # attachments lookup: none

		with patch.object(dm, "_get", side_effect=fake_get):
			result = dm.poll_account(self.acc.name)
		conv_calls = [p for u, p in calls if u.endswith("/conversations")]
		self.assertEqual([p["limit"] for p in conv_calls], [10, 5])  # same page, half the size
		self.assertIn("messages.limit(5)", conv_calls[1]["fields"])
		self.assertNotIn("attachments", conv_calls[1]["fields"])
		self.assertEqual(result["ingested"], 1)
		self.assertTrue(frappe.db.exists("Excom Message", {"provider_message_id": "m_qa_retry"}))
		self.assertTrue(frappe.db.get_value("Excom Channel Account", self.acc.name, "meta_last_polled_at"))

	def test_poll_fails_when_the_smaller_retry_fails_too(self):
		self._token_account()
		too_much = '{"error":{"code":1,"message":"Please reduce the amount of data you\'re asking for"}}'
		with patch.object(dm, "_get", side_effect=dm.GraphError(500, too_much)) as get:
			with self.assertRaises(dm.GraphError):
				dm.poll_account(self.acc.name)
		self.assertEqual(get.call_count, 2)  # one retry only
		self.assertFalse(frappe.db.get_value("Excom Channel Account", self.acc.name, "meta_last_polled_at"))

	def test_poll_follows_cursors_without_skipping(self):
		self._token_account()

		def fake_get(url, params):
			if url.endswith("/conversations"):
				if params.get("after") == "C1":
					return {"data": [self._conv("t_qa_conv_p2", "m_qa_page2", "page two")]}
				return {"data": [self._conv("t_qa_conv_p1", "m_qa_page1", "page one")], "paging": {"cursors": {"after": "C1"}, "next": "https://graph.facebook.com/next"}}
			if url.endswith("/t_qa_conv_p1/messages"):
				return {"data": [{"id": "m_qa_page1_older", "created_time": self._now_graph(5), "from": {"id": "999001"}, "message": "older message"}]}
			return {}

		def first_page_has_more(url, params):
			data = fake_get(url, params)
			for conv in data.get("data") or []:
				if conv["id"] == "t_qa_conv_p1":
					conv["messages"]["paging"] = {"cursors": {"after": "M1"}, "next": "https://graph.facebook.com/next"}
			return data

		with patch.object(dm, "_get", side_effect=first_page_has_more):
			result = dm.poll_account(self.acc.name)
		self.assertEqual(result["pages"], 2)
		for mid in ("m_qa_page1", "m_qa_page1_older", "m_qa_page2"):
			self.assertTrue(frappe.db.exists("Excom Message", {"provider_message_id": mid}), mid)

	def test_attachments_fetched_only_for_new_customer_messages(self):
		acc = self._token_account()
		dm.ingest_conversation(acc, self._conv("t_qa_conv_a", "m_qa_old", "already here"))
		conv = self._conv("t_qa_conv_a", "m_qa_new", "")
		conv["messages"]["data"] += [
			{"id": "m_qa_old", "created_time": self._now_graph(1), "from": {"id": "999001"}, "message": "already here"},
			{"id": "m_qa_ours", "created_time": self._now_graph(2), "from": {"id": "222"}, "message": "our reply"},
		]
		fetched = []

		def fake_get(url, params):
			fetched.append(url.rsplit("/", 1)[-1])
			return {"attachments": {"data": [{"image_data": {"url": "https://example.com/new.jpg"}}]}}

		with patch.object(dm, "_get", side_effect=fake_get):
			dm._fill_messages(dm._creds(acc), conv, {"messages": 10}, now_datetime().replace(year=2000), dm._own_ids(acc))
		self.assertEqual(fetched, ["m_qa_new"])
		dm.ingest_conversation(acc, conv)
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": "m_qa_new"}, "media_file"), "https://example.com/new.jpg")

	def test_poll_failure_logged_once_an_hour(self):
		self._token_account()
		frappe.cache.delete_value(f"excom:meta_dm_poll_failed:{self.acc.name}")
		title = f"Excom Meta DM poll failed: {self.acc.name}"
		before = frappe.db.count("Error Log", {"method": title})
		try:
			with patch.object(dm, "poll_account", side_effect=dm.GraphError(500, "{}")):
				dm.poll_all()
				dm.poll_all()  # the next minute, still failing
			self.assertEqual(frappe.db.count("Error Log", {"method": title}) - before, 1)
		finally:
			frappe.cache.delete_value(f"excom:meta_dm_poll_failed:{self.acc.name}")
			frappe.db.delete("Error Log", {"method": title})

	def test_provider_id_column_and_indexes_survive_schema_sync(self):
		from excom.excom.doctype.excom_message.excom_message import on_doctype_update

		on_doctype_update()  # what migrate runs after syncing the doctype
		col = frappe.db.sql("""SELECT character_maximum_length FROM information_schema.columns
			WHERE table_schema = DATABASE() AND table_name = 'tabExcom Message' AND column_name = 'provider_message_id'""")
		self.assertGreaterEqual(col[0][0], 500)
		self.assertTrue(frappe.db.has_index("tabExcom Message", "unique_provider_message_id"))
		self.assertTrue(frappe.db.has_index("tabExcom Message", "idx_excom_msg_provider_id"))
