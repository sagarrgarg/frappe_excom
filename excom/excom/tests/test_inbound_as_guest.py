"""Inbound messages arrive with nobody signed in, and must still be stored.

Meta's webhooks (WhatsApp, Instagram, Messenger) reach excom as Guest and are processed in a job
that runs as Guest too. When resolve_identity became the session-gated HTTP endpoint, the inbound
path kept calling it, so every new message failed with "You do not have access to Excom … Signed
in as Guest" and was lost. Server-side callers use resolve_identity_internal; this pins that.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.utils.webhook import _process_webhook_payload

ACCOUNT = "QA Guest Inbound WA"
PHONE_ID = "qa-guest-inbound-phone-id"
SENDER = "919900000777"
MESSAGE_ID = "wamid.qa-guest-inbound-1"


def _cleanup():
	frappe.set_user("Administrator")
	for name in frappe.get_all("Excom Message", {"provider_message_id": MESSAGE_ID}, pluck="name"):
		frappe.delete_doc("Excom Message", name, force=True, ignore_permissions=True)
	for oi in frappe.get_all("Omni Identity", {"normalized_phone": SENDER}, pluck="name"):
		for thread in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", thread, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Channel Account", ACCOUNT):
		frappe.delete_doc("Excom Channel Account", ACCOUNT, force=True, ignore_permissions=True)
	frappe.db.commit()


def _payload() -> str:
	return json.dumps(
		{
			"object": "whatsapp_business_account",
			"entry": [
				{
					"id": "qa-waba",
					"changes": [
						{
							"field": "messages",
							"value": {
								"messaging_product": "whatsapp",
								"metadata": {"phone_number_id": PHONE_ID},
								"contacts": [{"profile": {"name": "QA Guest Sender"}, "wa_id": SENDER}],
								"messages": [
									{
										"from": SENDER,
										"id": MESSAGE_ID,
										"timestamp": "1761200000",
										"type": "text",
										"text": {"body": "Hi"},
									}
								],
							},
						}
					],
				}
			],
		}
	)


class TestInboundAsGuest(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		frappe.get_doc(
			{
				"doctype": "Excom Channel Account",
				"account_name": ACCOUNT,
				"channel": "whatsapp",
				"wa_phone_id": PHONE_ID,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def test_whatsapp_message_is_stored_when_processed_as_guest(self):
		errors_before = frappe.db.count("Error Log", {"method": ["like", "Excom webhook handler failed%"]})

		frappe.set_user("Guest")
		try:
			_process_webhook_payload(_payload())
		finally:
			frappe.set_user("Administrator")

		message = frappe.db.get_value(
			"Excom Message",
			{"provider_message_id": MESSAGE_ID},
			["name", "thread", "content_text"],
			as_dict=True,
		)
		self.assertTrue(message, "the inbound message was not stored")
		self.assertEqual(message.content_text, "Hi")
		thread = frappe.db.get_value(
			"Excom Thread", message.thread, ["account", "omni_identity"], as_dict=True
		)
		self.assertTrue(thread, "no thread was created for the sender")
		self.assertEqual(thread.account, ACCOUNT)
		self.assertEqual(
			frappe.db.get_value("Omni Identity", thread.omni_identity, "normalized_phone"), SENDER
		)
		self.assertEqual(
			frappe.db.count("Error Log", {"method": ["like", "Excom webhook handler failed%"]}),
			errors_before,
			"the webhook handler logged a failure",
		)


class TestLostMessageFilter(FrappeTestCase):
	"""The recovery replays only customer messages that were never stored."""

	def test_keeps_only_unstored_messages(self):
		from excom.excom.services.inbound_replay import lost_messages

		payload = json.loads(_payload())
		value = payload["entry"][0]["changes"][0]["value"]
		value["messages"].append(
			{"from": SENDER, "id": "wamid.stored", "type": "text", "text": {"body": "old"}}
		)
		value["statuses"] = [{"id": "wamid.out", "status": "read"}]
		payload["entry"][0]["changes"].append({"field": "leadgen", "value": {"leadgen_id": "1"}})

		lost = lost_messages(payload, {"wamid.stored"})
		changes = lost["entry"][0]["changes"]
		self.assertEqual(len(changes), 1)
		self.assertEqual([m["id"] for m in changes[0]["value"]["messages"]], [MESSAGE_ID])
		self.assertEqual(changes[0]["value"]["statuses"], [])
		self.assertIsNone(lost_messages(payload, {MESSAGE_ID, "wamid.stored"}))

	def test_meta_dm_skips_echoes_and_stored(self):
		from excom.excom.services.inbound_replay import lost_messages

		payload = {
			"object": "instagram",
			"entry": [
				{
					"id": "ig-1",
					"messaging": [
						{"sender": {"id": "u1"}, "message": {"mid": "m.new", "text": "Hi"}},
						{"sender": {"id": "u1"}, "message": {"mid": "m.old", "text": "seen"}},
						{"sender": {"id": "page"}, "message": {"mid": "m.echo", "is_echo": True}},
						{"sender": {"id": "u1"}, "read": {"mid": "m.x"}},
					],
				}
			],
		}
		lost = lost_messages(payload, {"m.old"})
		self.assertEqual([e["message"]["mid"] for e in lost["entry"][0]["messaging"]], ["m.new"])
