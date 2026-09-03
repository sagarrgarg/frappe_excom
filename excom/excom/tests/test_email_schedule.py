"""Scheduled email: parked as an Excom Message, sent by the scheduler at the right time, cancellable. Gmail mocked."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.api import email as api
from excom.excom.tests.test_core_flows import _cleanup, _mk_identity


class TestEmailSchedule(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator"); _cleanup()
		self.acc = frappe.get_all("Excom Channel Account", filters={"channel": "email"}, pluck="name", limit=1)
		self.sent = []
		self._orig = api.send_email_reply
		api.send_email_reply = lambda **kw: (self.sent.append(kw) or "MSG-OK")

	def tearDown(self):
		api.send_email_reply = self._orig; _cleanup()

	def _thread(self):
		oi = _mk_identity("QA Mail Person", "+919900000801")
		frappe.db.set_value("Omni Identity", oi.name, "primary_email", "qa.mail@example.com")
		acc = self.acc[0] if self.acc else frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA Mail Acc", "channel": "email", "status": "Active", "email_address": "qa@example.com"}).insert(ignore_permissions=True).name
		return frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi.name, "channel": "email", "account_doctype": "Excom Channel Account", "account": acc, "thread_key": "qa-mail-1", "status": "Open", "last_message_at": now_datetime()}).insert(ignore_permissions=True)

	def test_schedule_then_scheduler_sends(self):
		t = self._thread()
		later = add_to_date(now_datetime(), minutes=30)
		r = api.send_email(t.name, "qa.mail@example.com", "Hello", "<p>Hi</p>", cc="a@example.com", send_at=str(later))
		self.assertTrue(r["scheduled"]); self.assertEqual(self.sent, [])
		m = frappe.get_doc("Excom Message", r["message_name"])
		self.assertEqual((m.delivery_status, m.message_type), ("Scheduled", "Email")); self.assertEqual(json.loads(m.content_json)["cc"], "a@example.com")
		api.send_scheduled_emails()  # not due yet
		self.assertEqual(self.sent, [])
		frappe.db.set_value("Excom Message", m.name, "scheduled_at", add_to_date(now_datetime(), minutes=-1))
		api.send_scheduled_emails()
		self.assertEqual(len(self.sent), 1); self.assertEqual(self.sent[0]["to"], "qa.mail@example.com"); self.assertEqual(self.sent[0]["subject"], "Hello")
		self.assertFalse(frappe.db.exists("Excom Message", m.name))

	def test_cancel_scheduled(self):
		t = self._thread()
		r = api.send_email(t.name, "qa.mail@example.com", "Later", "<p>x</p>", send_at=str(add_to_date(now_datetime(), hours=2)))
		api.cancel_scheduled_email(r["message_name"])
		self.assertFalse(frappe.db.exists("Excom Message", r["message_name"]))

	def test_suggest_recipients(self):
		self._thread()
		out = api.suggest_recipients("qa.mail")
		self.assertTrue(any(x["email"] == "qa.mail@example.com" and x["kind"] == "contact" for x in out))
