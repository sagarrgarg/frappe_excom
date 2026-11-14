"""Fixes where the packs meet (2026-11-14 review): one rule for whose a conversation is, proactive
Sales replies held on quiet dates, a failed send never proposed again, voice settings that never
block an unrelated save. Database values are scripted: these are the rules, not the packs."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom_ai.engine import gates, threads


def agent(name="AG-1"):
	return frappe._dict(name=name, owner_user="owner@x.example", acting_owner=None, backup_user="backup@x.example",
		service_user="svc@x.example")


def scripted(thread: dict, agent_status: dict | None = None, user_enabled=1):
	def get_value(doctype, name=None, fields=None, *args, **kwargs):
		if doctype == "Excom Thread":
			if isinstance(fields, (list, tuple)):
				return frappe._dict({f: thread.get(f) for f in fields})
			return thread.get(fields)
		if doctype == "Excom AI Agent":
			return (agent_status or {}).get(name) if fields == "status" else name
		if doctype == "User":
			return user_enabled
		return None

	return patch.object(frappe.db, "get_value", side_effect=get_value)


class TestWhoseConversation(FrappeTestCase):
	def test_a_person_or_a_working_agent_holds_it(self):
		with scripted({"ai_mode": "Needs person"}):
			self.assertIn("person", threads.holder("T", agent()))
		with scripted({"ai_agent": "AG-2", "ai_mode": "Handle"}, {"AG-2": "Live"}):
			self.assertIn("(AI)", threads.holder("T", agent()))

	def test_a_retired_agents_old_claim_does_not_block(self):
		for status in ("Retired", "Paused", "Draft"):
			with scripted({"ai_agent": "AG-2", "ai_mode": "Handle"}, {"AG-2": status}):
				self.assertIsNone(threads.holder("T", agent()), status)

	def test_the_agents_own_people_do_not_block_but_a_colleague_does(self):
		with scripted({"assigned_to": "owner@x.example", "status": "Open"}):
			self.assertIsNone(threads.holder("T", agent()))
		with scripted({"assigned_to": "someone@x.example", "status": "Open"}):
			self.assertIn("someone@x.example", threads.holder("T", agent()))
		with scripted({"assigned_to": "someone@x.example", "status": "Closed"}):
			self.assertIsNone(threads.holder("T", agent()))
		with scripted({"assigned_to": "someone@x.example", "status": "Open", "ai_agent": "AG-1"}):
			self.assertIsNone(threads.holder("T", agent()))  # the agent already has it

	def test_claim_keeps_the_mode_a_person_set(self):
		with scripted({"ai_mode": "Assist"}), patch.object(frappe.db, "set_value") as sv:
			self.assertTrue(threads.claim("T", agent()))
		self.assertEqual(sv.call_args.args[2], {"ai_agent": "AG-1", "ai_mode": "Assist"})
		with scripted({"assigned_to": "owner@x.example", "status": "Open"}), patch.object(frappe.db, "set_value") as sv:
			self.assertTrue(threads.claim("T", agent()))
		self.assertEqual(sv.call_args.args[2], {"ai_agent": "AG-1", "ai_mode": "Assist"})  # the owner has it: drafts only
		with scripted({"ai_mode": "Hold"}), patch.object(frappe.db, "set_value") as sv:
			self.assertFalse(threads.claim("T", agent()))
		sv.assert_not_called()


class TestQuietDates(FrappeTestCase):
	def test_proactive_sales_replies_are_outreach(self):
		for intent in ("quote_follow_up", "order_update"):
			p = frappe._dict(action="reply", kind="Reply", intent=None, payload=frappe.as_json({"intent": intent}))
			self.assertTrue(gates.is_outreach_send(p), intent)
		answer = frappe._dict(action="reply", kind="Reply", intent=None, payload=frappe.as_json({"body": "hi"}))
		self.assertFalse(gates.is_outreach_send(answer))
		self.assertTrue(gates.is_outreach_send(frappe._dict(action="payment_reminder", kind="Reply", payload="{}")))


class TestFollowedUp(FrappeTestCase):
	def test_a_failed_send_counts_as_done(self):
		from excom.excom_ai import deals

		with patch.object(frappe, "get_all", return_value=[frappe._dict(task=None)]) as get_all:
			self.assertTrue(deals._followed_up("AG-1", "Quotation", "Q-1"))
		self.assertEqual(get_all.call_args.args[1]["status"], ["not in", ("Cancelled", "Expired")])


class TestVoiceSettings(FrappeTestCase):
	def test_an_old_call_length_never_blocks_an_unrelated_save(self):
		from excom.excom_ai import voice

		doc = frappe._dict(voice_service_key="", voice_max_seconds=1800)
		doc.is_dummy_password = lambda v: False
		doc.has_value_changed = lambda f: False
		voice.validate_settings(doc)  # unchanged: no error
		doc.has_value_changed = lambda f: f == "voice_max_seconds"
		with self.assertRaises(frappe.ValidationError):
			voice.validate_settings(doc)

	def test_a_short_key_turns_calls_off_and_tells_the_admins(self):
		from excom.excom_ai import voice

		frappe.cache.delete_value(f"excom_ai_voice_short_key:{frappe.utils.nowdate()}")
		with patch.object(voice, "stream_url", return_value="wss://voice.example/stream"), \
				patch.object(voice, "_voice_key", return_value="short"), \
				patch("excom.excom_ai.engine.notify.alert") as alert, \
				patch("excom.excom_ai.engine.notify.admins", return_value=["admin@x.example"]):
			self.assertFalse(voice.voice_ready())
			self.assertFalse(voice.voice_ready())
		self.assertEqual(alert.call_count, 1)  # once a day, not on every call
		frappe.cache.delete_value(f"excom_ai_voice_short_key:{frappe.utils.nowdate()}")
