"""HLD-005 Phase 5 — WhatsApp number and account settings: business profile (with the resumable
photo upload), block users (24 h rule), QR codes mirrored into Excom WhatsApp QR Code, ice breakers
and commands (+ welcome-message fallback), inbound commands and request_welcome, two-step PIN and
registration (10 per 72 h), webhook overrides, commerce settings and catalogue linking.

Every Meta call is faked; the tests assert the exact URL, method, query and body.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_account'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.services import whatsapp_account as wa
from excom.excom.services.whatsapp_errors import WhatsAppAPIError
from excom.excom.utils import webhook

PHONE_ID = "QAPHONEP5"
WABA = "QAWABAP5"
APP_ID = "QAAPPP5"
TOKEN = "qa-token-p5-0000000000"
BASE = "https://graph.facebook.com/v26.0"
EVENTS: list = []
STARTED = {"at": None}


def record_event(event):  # an excom_inbound_handlers entry used by the tests
	EVENTS.append(event)


class FakeResp:
	def __init__(self, status, body, content=None):
		self.status_code, self._b, self.ok = status, body, status < 400
		self.text = json.dumps(body)
		self.content = content if content is not None else b"x"

	def json(self):
		return self._b


def _payload(value):
	return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": PHONE_ID}, **value}}]}]})


def _cleanup(account):
	ids = frappe.db.sql("SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199005%%' OR primary_whatsapp LIKE '9199005%%' OR wa_bsuid LIKE 'IN.QAP5%%'", pluck=True)
	if ids:
		for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
			frappe.db.delete(child, {"parent": ["in", ids]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", ids]})
		frappe.db.delete("Omni Identity", {"name": ["in", ids]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	frappe.db.delete("Excom WhatsApp Event", {"channel_account": account})
	for name in frappe.get_all("Excom WhatsApp QR Code", filters={"channel_account": account}, pluck="name"):
		frappe.db.delete("File", {"attached_to_doctype": "Excom WhatsApp QR Code", "attached_to_name": name})
		frappe.db.delete("Excom WhatsApp QR Code", name)
	frappe.db.delete("File", {"file_name": "qa-p5-photo.png"})
	if STARTED["at"]:
		for title in ("Excom: WhatsApp block refused%", "Excom: WhatsApp unblock refused%", "Excom: WhatsApp number%", "Excom: QR image download failed%"):
			frappe.db.delete("Error Log", {"method": ["like", title], "creation": [">=", STARTED["at"]]})
	frappe.db.set_value("Excom Channel Account", account, {"wa_registration_log": "", "wa_welcome_message": "", "wa_catalog_id": "",
		"wa_data_localization_region": "", "wa_number_webhook_url": "", "wa_waba_webhook_url": ""}, update_modified=False)
	frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s AND fieldname IN ('wa_two_step_pin', 'wa_webhook_verify_token')", account)


class TestWhatsAppAccount(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		STARTED["at"] = now_datetime()
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Number P5", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_app_id": APP_ID, "wa_token": TOKEN}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup(cls.acc.name)
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.calls = []
		self.replies = []  # FakeResp queue; empty → {"success": true}
		self._orig = {m: getattr(wa.http_requests, m) for m in ("get", "post", "delete")}
		for m in self._orig:
			setattr(wa.http_requests, m, self._fake(m.upper()))
		EVENTS.clear()

	def tearDown(self):
		for m, fn in self._orig.items():
			setattr(wa.http_requests, m, fn)
		frappe.set_user("Administrator")
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	def _fake(self, method):
		def call(url, **kw):
			self.calls.append({"method": method, "url": url, "params": kw.get("params"), "json": kw.get("json"), "data": kw.get("data"), "headers": kw.get("headers") or {}})
			return self.replies.pop(0) if self.replies else FakeResp(200, {"success": True})
		return call

	def _account(self):
		return frappe.get_doc("Excom Channel Account", self.acc.name)

	def _person(self, phone, hours_ago=1.0, bsuid=""):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
		from excom.excom.services.thread_service import upsert_thread

		identity = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone, display_name="QA P5")
		if bsuid:
			frappe.db.set_value("Omni Identity", identity, {"wa_bsuid": bsuid, "wa_bsuid_waba": WABA})
		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		frappe.db.set_value("Excom Thread", thread, "last_inbound_at", add_to_date(now_datetime(), hours=-hours_ago) if hours_ago is not None else None)
		return identity, thread

	# ── business profile ─────────────────────────────────────────────────────
	def test_profile_limits_are_checked_before_meta(self):
		for bad in ({"about": "x" * 140}, {"about": ""}, {"address": "x" * 257}, {"description": "x" * 513}, {"email": "not-an-email"},
				{"email": "a" * 120 + "@ex.com1"}, {"websites": ["https://a.in", "https://b.in", "https://c.in"]}, {"websites": ["www.a.in"]},
				{"websites": ["https://" + "a" * 250]}, {"vertical": "NOT_A_BIZ"}):
			with self.assertRaises(wa.AccountSettingError, msg=str(bad)):
				wa.update_business_profile(self._account(), bad)
		self.assertEqual(self.calls, [])
		self.assertEqual(wa.validate_profile({"about": " Hi ", "vertical": "retail", "websites": "https://a.in, https://b.in", "address": None}),
			{"about": "Hi", "vertical": "RETAIL", "websites": ["https://a.in", "https://b.in"]})

	def test_profile_read_and_update_shapes(self):
		self.replies = [FakeResp(200, {"data": [{"about": "Hi", "vertical": "RETAIL", "websites": ["https://a.in/"], "messaging_product": "whatsapp"}]})]
		profile = wa.get_business_profile(self._account())
		self.assertEqual(profile, {"about": "Hi", "vertical": "RETAIL", "websites": ["https://a.in/"]})
		self.assertEqual(self.calls[0]["method"], "GET")
		self.assertEqual(self.calls[0]["url"], f"{BASE}/{PHONE_ID}/whatsapp_business_profile")
		self.assertEqual(self.calls[0]["params"], {"fields": "about,address,description,email,profile_picture_url,websites,vertical"})
		self.assertEqual(self.calls[0]["headers"]["Authorization"], f"Bearer {TOKEN}")
		wa.update_business_profile(self._account(), {"about": "Succulents", "websites": ["https://a.in"]})
		self.assertEqual(self.calls[1]["url"], f"{BASE}/{PHONE_ID}/whatsapp_business_profile")
		self.assertEqual(self.calls[1]["json"], {"messaging_product": "whatsapp", "about": "Succulents", "websites": ["https://a.in"]})

	def test_profile_photo_uses_the_resumable_upload_api(self):
		png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
		f = frappe.get_doc({"doctype": "File", "file_name": "qa-p5-photo.png", "content": png, "is_private": 1}).insert(ignore_permissions=True)
		self.replies = [FakeResp(200, {"id": "upload:SESSION1"}), FakeResp(200, {"h": "4::HANDLE"}), FakeResp(200, {"success": True})]
		wa.set_profile_photo(self._account(), f.file_url)
		start, send, apply = self.calls
		self.assertEqual((start["method"], start["url"]), ("POST", f"{BASE}/{APP_ID}/uploads"))
		self.assertEqual(start["params"], {"file_name": "qa-p5-photo.png", "file_length": len(png), "file_type": "image/png"})
		self.assertEqual(send["url"], f"{BASE}/upload:SESSION1")
		self.assertEqual(send["headers"]["Authorization"], f"OAuth {TOKEN}")
		self.assertEqual(send["headers"]["file_offset"], "0")
		self.assertEqual(send["data"], png)
		self.assertEqual(apply["json"], {"messaging_product": "whatsapp", "profile_picture_handle": "4::HANDLE"})

	def test_profile_photo_needs_app_id_and_an_image(self):
		acc = self._account()
		acc.wa_app_id = ""
		with self.assertRaises(wa.AccountSettingError):
			wa.set_profile_photo(acc, "/private/files/whatever.png")
		with self.assertRaises(wa.AccountSettingError):
			wa.set_profile_photo(self._account(), "/private/files/not-uploaded.png")
		self.assertEqual(self.calls, [])

	# ── block users ──────────────────────────────────────────────────────────
	def test_block_entry_shapes(self):
		self.assertEqual(wa.block_entry("+91 99005 00001"), {"user": "+919900500001"})
		self.assertEqual(wa.block_entry("IN.QAP5ABC"), {"user_id": "IN.QAP5ABC"})
		with self.assertRaises(wa.AccountSettingError):
			wa.block_entry("US.ENT.QAP5PARENT")  # parent user ids are rejected by Meta
		with self.assertRaises(wa.AccountSettingError):
			wa.block_entry("hello")

	def test_block_only_after_a_message_in_the_last_24_hours(self):
		identity, _thread = self._person("919900500002", hours_ago=25)
		with self.assertRaises(wa.AccountSettingError) as cm:
			wa.block_person(self._account(), identity=identity)
		self.assertIn("24 hours", str(cm.exception))
		self.assertEqual(self.calls, [])

		_i2, never = self._person("919900500003", hours_ago=None)
		with self.assertRaises(wa.AccountSettingError):
			wa.block_person(self._account(), value="919900500003")

		frappe.db.set_value("Excom Thread", _thread, "last_inbound_at", add_to_date(now_datetime(), hours=-2))
		self.replies = [FakeResp(200, {"messaging_product": "whatsapp", "block_users": {"added_users": [{"input": "+919900500002", "wa_id": "919900500002"}]}})]
		wa.block_person(self._account(), identity=identity)
		call = self.calls[-1]
		self.assertEqual((call["method"], call["url"]), ("POST", f"{BASE}/{PHONE_ID}/block_users"))
		self.assertEqual(call["json"], {"messaging_product": "whatsapp", "block_users": [{"user": "+919900500002"}]})
		row = frappe.db.get_value("Omni Identity", identity, ["wa_blocked", "wa_blocked_on", "wa_blocked_account"], as_dict=True)
		self.assertEqual((row.wa_blocked, row.wa_blocked_account), (1, self.acc.name))
		self.assertTrue(row.wa_blocked_on)

	def test_block_uses_the_user_id_of_this_account(self):
		identity, _thread = self._person("919900500004", bsuid="IN.QAP5USER4")
		self.replies = [FakeResp(200, {"block_users": {"added_users": [{"input": "IN.QAP5USER4", "user_id": "IN.QAP5USER4"}]}})]
		wa.block_person(self._account(), identity=identity)
		self.assertEqual(self.calls[-1]["json"]["block_users"], [{"user_id": "IN.QAP5USER4"}])

	def test_partial_failure_is_logged_and_not_recorded(self):
		identity, _thread = self._person("919900500005")
		self.replies = [FakeResp(200, {"messaging_product": "whatsapp", "block_users": {"added_users": [], "failed_users": [{"input": "+919900500005", "wa_id": "919900500005",
			"errors": [{"message": "Re-engagement required", "code": 131047, "error_data": {"details": "User has not messaged in the last 24 hours"}}]}]},
			"error": {"message": "(#139100) Failed to block/unblock users", "code": 139100}})]
		with self.assertRaises(wa.AccountSettingError) as cm:
			wa.block_person(self._account(), identity=identity)
		self.assertIn("24 hours", str(cm.exception))
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_blocked"), 0)
		self.assertTrue(frappe.db.exists("Error Log", {"method": ["like", "Excom: WhatsApp block refused (131047)%"]}))

	def test_unblock_uses_delete_and_clears_the_flag(self):
		identity, _thread = self._person("919900500006", hours_ago=100)  # no 24 h rule for unblocking
		frappe.db.set_value("Omni Identity", identity, {"wa_blocked": 1, "wa_blocked_account": self.acc.name})
		self.replies = [FakeResp(200, {"block_users": {"removed_users": [{"input": "+919900500006", "wa_id": "919900500006"}]}})]
		wa.unblock_person(self._account(), identity=identity)
		self.assertEqual((self.calls[-1]["method"], self.calls[-1]["url"]), ("DELETE", f"{BASE}/{PHONE_ID}/block_users"))
		self.assertEqual(self.calls[-1]["json"], {"messaging_product": "whatsapp", "block_users": [{"user": "+919900500006"}]})
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_blocked"), 0)

	def test_block_from_a_conversation_and_the_block_list(self):
		from excom.excom.api import whatsapp_account as api

		identity, thread = self._person("919900500007")
		self.replies = [FakeResp(200, {"block_users": {"added_users": [{"input": "+919900500007", "wa_id": "919900500007"}]}})]
		api.block_thread_contact(thread)
		self.assertEqual(api.thread_block_status(thread)["blocked"], True)
		self.replies = [FakeResp(200, {"data": [{"messaging_product": "whatsapp", "wa_id": "919900500007", "user_id": "IN.QAP5X"}], "paging": {"cursors": {"after": "AFT"}, "next": "https://n"}})]
		page = wa.list_blocked_users(self._account(), limit=10)
		self.assertEqual(self.calls[-1]["params"], {"limit": 10})
		self.assertEqual((page["data"][0]["identity"], page["after"]), (identity, "AFT"))

	# ── QR codes ─────────────────────────────────────────────────────────────
	def _new_qr(self, message="Hello from the poster", fmt="PNG"):
		return frappe.get_doc({"doctype": "Excom WhatsApp QR Code", "channel_account": self.acc.name, "prefilled_message": message, "image_format": fmt}).insert()

	def test_qr_create_update_delete_push_to_meta(self):
		self.replies = [FakeResp(200, {"code": "QAP5CODE000001", "prefilled_message": "Hello from the poster",
			"deep_link_url": "https://wa.me/message/QAP5CODE000001", "qr_image_url": "https://scontent.example/qr.png"}),
			FakeResp(200, {}, content=b"\x89PNG-image")]  # the image download
		qr = self._new_qr()
		create = self.calls[0]
		self.assertEqual((create["method"], create["url"]), ("POST", f"{BASE}/{PHONE_ID}/message_qrdls"))
		self.assertEqual(create["json"], {"prefilled_message": "Hello from the poster", "generate_qr_image": "PNG"})
		self.assertEqual((qr.code, qr.deep_link_url), ("QAP5CODE000001", "https://wa.me/message/QAP5CODE000001"))
		self.assertEqual(self.calls[1]["url"], "https://scontent.example/qr.png")
		self.assertTrue(frappe.db.get_value("Excom WhatsApp QR Code", qr.name, "qr_image"))

		qr.reload()
		qr.prefilled_message = "Cyber Tuesday"
		qr.save()
		self.assertEqual(self.calls[-1]["json"], {"code": "QAP5CODE000001", "prefilled_message": "Cyber Tuesday"})

		frappe.delete_doc("Excom WhatsApp QR Code", qr.name)
		self.assertEqual((self.calls[-1]["method"], self.calls[-1]["url"]), ("DELETE", f"{BASE}/{PHONE_ID}/message_qrdls/QAP5CODE000001"))

	def test_qr_limits(self):
		with self.assertRaises(frappe.ValidationError):
			self._new_qr("x" * 141)
		with self.assertRaises(frappe.ValidationError):
			self._new_qr("hi", fmt="JPG")
		orig = wa.qr_count
		wa.qr_count = lambda account_name: wa.QR_CAP
		try:
			with self.assertRaises(wa.AccountSettingError):
				self._new_qr("one too many")
		finally:
			wa.qr_count = orig
		self.assertEqual(self.calls, [])

	def test_qr_sync_from_meta(self):
		for code, msg in (("QAP5OLD0000001", "gone on Meta"), ("QAP5KEEP000001", "old text")):
			d = frappe.get_doc({"doctype": "Excom WhatsApp QR Code", "channel_account": self.acc.name, "code": code, "prefilled_message": msg})
			d.flags.from_meta = True
			d.insert()
		self.replies = [
			FakeResp(200, {"data": [{"code": "QAP5NEW0000001", "prefilled_message": "new", "deep_link_url": "https://wa.me/message/QAP5NEW0000001"},
				{"code": "QAP5KEEP000001", "prefilled_message": "new text", "deep_link_url": "https://wa.me/message/QAP5KEEP000001"}],
				"paging": {"cursors": {"after": "P2"}, "next": "https://graph/next"}}),
			FakeResp(200, {"data": [{"code": "QAP5NEW0000002", "prefilled_message": "newer", "deep_link_url": "https://wa.me/message/QAP5NEW0000002"}], "paging": {"cursors": {"after": "P3"}}}),
		]
		out = wa.sync_qr_codes(self._account())
		self.assertEqual(out, {"created": 2, "updated": 1, "removed": 1, "total": 3})
		self.assertEqual([c["params"].get("after") for c in self.calls], [None, "P2"])
		self.assertEqual(self.calls[0]["params"]["fields"], "code,prefilled_message,deep_link_url")
		self.assertEqual({c["method"] for c in self.calls}, {"GET"})  # nothing pushed back, nothing deleted on Meta
		self.assertEqual(frappe.db.get_value("Excom WhatsApp QR Code", {"code": "QAP5KEEP000001"}, "prefilled_message"), "new text")
		self.assertFalse(frappe.db.exists("Excom WhatsApp QR Code", {"code": "QAP5OLD0000001"}))

	# ── conversational components ────────────────────────────────────────────
	def test_conversational_limits(self):
		cmd = lambda n, d="Does it": {"command_name": n, "command_description": d}
		for prompts, commands in ((["a", "b", "c", "d", "e"], []), (["x" * 81], []), (["Hi 😀"], []), (["Same", "same"], []),
				([], [cmd(f"c{i}") for i in range(31)]), ([], [cmd("x" * 33)]), ([], [cmd("two words")]), ([], [cmd("dup"), cmd("DUP")]),
				([], [cmd("ok", "x" * 257)]), ([], [cmd("ok", "")])):
			with self.assertRaises(wa.AccountSettingError, msg=str((prompts, commands))):
				wa.validate_conversational(prompts, commands)
		self.assertEqual(wa.validate_conversational(["Book a flight", " "], [cmd("/tickets", "Book flight tickets")]),
			(["Book a flight"], [{"command_name": "tickets", "command_description": "Book flight tickets"}]))

	def test_conversational_shape_and_welcome_fallback(self):
		wa.set_conversational_automation(self._account(), ["Book a flight"], [{"command_name": "tickets", "command_description": "Book flight tickets"}])
		self.assertEqual(self.calls[-1]["url"], f"{BASE}/{PHONE_ID}/conversational_automation")
		self.assertEqual(self.calls[-1]["json"], {"prompts": ["Book a flight"], "commands": [{"command_name": "tickets", "command_description": "Book flight tickets"}]})

		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_welcome_message", "On")
		self.replies = [FakeResp(400, {"error": {"message": "Invalid parameter", "code": 100}}), FakeResp(200, {"success": True})]
		out = wa.set_conversational_automation(self._account(), [], [])
		self.assertEqual(self.calls[-2]["json"], {"prompts": [], "commands": [], "enable_welcome_message": True})
		self.assertEqual(self.calls[-1]["json"], {"prompts": [], "commands": []})
		self.assertTrue(out["warning"])

		self.replies = [FakeResp(400, {"error": {"message": "Invalid parameter", "code": 100}})]
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_welcome_message", "")
		with self.assertRaises(WhatsAppAPIError):  # no welcome setting to drop: Meta's refusal stands
			wa.set_conversational_automation(self._account(), ["x"], [])

	def test_conversational_read_back(self):
		self.replies = [FakeResp(200, {"conversational_automation": {"prompts": ["Find hotels"], "commands": [{"command_name": "tickets", "command_description": "Book"}]}, "id": PHONE_ID})]
		out = wa.get_conversational_automation(self._account())
		self.assertEqual(self.calls[0]["url"], f"{BASE}/{PHONE_ID}")
		self.assertEqual(self.calls[0]["params"], {"fields": "conversational_automation"})
		self.assertEqual(out, {"prompts": ["Find hotels"], "commands": [{"command_name": "tickets", "command_description": "Book"}]})

	def _hooks(self, *paths):
		from excom.excom.services import inbound_dispatch

		orig = inbound_dispatch.frappe.get_hooks
		inbound_dispatch.frappe.get_hooks = lambda name, *a, **k: list(paths) if name == "excom_inbound_handlers" else orig(name, *a, **k)
		self.addCleanup(setattr, inbound_dispatch.frappe, "get_hooks", orig)

	def _in(self, wamid, phone, **msg):
		return _payload({"contacts": [{"wa_id": phone, "profile": {"name": "QA P5"}}], "messages": [{"from": phone, "id": wamid, "timestamp": "1700000500", **msg}]})

	def test_parse_command(self):
		self.assertEqual(wa.parse_command("/Tickets to Delhi\nfor 2"), {"command": "tickets", "args": "to Delhi\nfor 2"})
		self.assertEqual(wa.parse_command("/hotel"), {"command": "hotel", "args": ""})
		for text in ("hello /tickets", "/", "/ tickets", "/path/to/file", "http://x"):
			self.assertIsNone(wa.parse_command(text), text)

	def test_inbound_command_and_welcome_are_dispatched(self):
		self._hooks("excom.excom.tests.test_whatsapp_account.record_event")
		webhook._process_webhook_payload(self._in("wamid.P5CMD", "919900500010", type="text", text={"body": "/tickets to Delhi"}))
		msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P5CMD"}, ["message_type", "content_text"], as_dict=True)
		self.assertEqual((msg.message_type, msg.content_text), ("Text", "/tickets to Delhi"))
		self.assertEqual((EVENTS[0]["kind"], EVENTS[0]["payload"]), ("command", {"command": "tickets", "args": "to Delhi"}))

		webhook._process_webhook_payload(self._in("wamid.P5WEL", "919900500011", type="request_welcome"))
		msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P5WEL"}, ["thread", "content_text", "omni_identity"], as_dict=True)
		self.assertTrue(msg and msg.thread)
		self.assertEqual(EVENTS[1]["kind"], "welcome")
		self.assertEqual(EVENTS[1]["thread"], msg.thread)
		# a welcome request is not a message: the 24 h window stays closed
		self.assertIsNone(frappe.db.get_value("Excom Thread", msg.thread, "last_inbound_at"))
		self.assertTrue(frappe.db.exists("Excom WhatsApp Event", {"channel_account": self.acc.name, "event": "request_welcome"}))

	# ── two-step PIN and registration ────────────────────────────────────────
	def test_pin_is_six_digits_and_stored_encrypted(self):
		for bad in ("12345", "1234567", "12a456", ""):
			with self.assertRaises(wa.AccountSettingError):
				wa.set_two_step_pin(self._account(), bad)
		self.assertEqual(self.calls, [])
		wa.set_two_step_pin(self._account(), "246810")
		self.assertEqual((self.calls[0]["url"], self.calls[0]["json"]), (f"{BASE}/{PHONE_ID}", {"pin": "246810"}))
		self.assertEqual(wa._stored_secret(self.acc.name, "wa_two_step_pin"), "246810")
		self.assertNotIn("246810", frappe.db.get_value("Excom Channel Account", self.acc.name, "wa_two_step_pin") or "")

	def test_register_and_deregister_shapes(self):
		with self.assertRaises(wa.AccountSettingError):  # no PIN stored yet
			wa.register_number(self._account())
		wa._store_secret(self.acc.name, "wa_two_step_pin", "246810")
		wa.register_number(self._account(), region="in")
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["json"]), (f"{BASE}/{PHONE_ID}/register",
			{"messaging_product": "whatsapp", "pin": "246810", "data_localization_region": "IN"}))
		with self.assertRaises(wa.AccountSettingError):
			wa.register_number(self._account(), region="FR")
		wa.deregister_number(self._account())
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["json"], self.calls[-1]["data"]), (f"{BASE}/{PHONE_ID}/deregister", None, None))
		self.assertEqual([a["action"] for a in wa.registration_attempts(self.acc.name)], ["register", "deregister"])

	def test_ten_register_calls_per_72_hours(self):
		wa._store_secret(self.acc.name, "wa_two_step_pin", "246810")
		old = [{"at": str(add_to_date(now_datetime(), hours=-80)), "action": "register", "ok": True}]
		recent = [{"at": str(add_to_date(now_datetime(), hours=-h)), "action": "register", "ok": True} for h in range(10, 0, -1)]
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_registration_log", json.dumps(old + recent[1:]))
		self.assertEqual(wa.registration_budget(self.acc.name)["left"], 1)  # the 80 h old one no longer counts
		self.replies = [FakeResp(400, {"error": {"message": "Rate limit hit", "code": 133016}})]
		with self.assertRaises(WhatsAppAPIError) as cm:
			wa.register_number(self._account())
		self.assertIn("72 hours", str(cm.exception))
		self.assertEqual(wa.registration_attempts(self.acc.name)[-1].get("code"), 133016)
		n = len(self.calls)
		with self.assertRaises(wa.AccountSettingError):  # 10 used: refused before Meta is asked
			wa.deregister_number(self._account())
		self.assertEqual(len(self.calls), n)

	def test_pin_never_reaches_the_error_log(self):
		from excom.excom.api import whatsapp_account as api

		wa._store_secret(self.acc.name, "wa_two_step_pin", "246810")
		self.replies = [FakeResp(400, {"error": {"message": "Invalid PIN format. PIN must be exactly 6 digits", "code": 100}})]
		with self.assertRaises(frappe.ValidationError):
			api.register(self.acc.name)
		logs = frappe.get_all("Error Log", filters={"method": ["like", "Excom: WhatsApp number%"]}, fields=["error"], order_by="creation desc", limit=5)
		self.assertTrue(logs)
		self.assertFalse(any("246810" in (r.error or "") for r in logs))

	def test_request_and_verify_code(self):
		wa.request_verification_code(self._account(), "voice", "en_US")
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["params"]), (f"{BASE}/{PHONE_ID}/request_code", {"code_method": "VOICE", "language": "en_US"}))
		for method, lang in (("FAX", "en_US"), ("SMS", "english")):
			with self.assertRaises(wa.AccountSettingError):
				wa.request_verification_code(self._account(), method, lang)
		wa.verify_code(self._account(), "000000")
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["data"], self.calls[-1]["params"]), (f"{BASE}/{PHONE_ID}/verify_code", {"code": "000000"}, None))
		self.replies = [FakeResp(400, {"error": {"message": "already verified", "code": 136024}})]
		with self.assertRaises(WhatsAppAPIError) as cm:
			wa.request_verification_code(self._account())
		self.assertIn("already verified", str(cm.exception))

	# ── webhook overrides ────────────────────────────────────────────────────
	def test_number_webhook_defaults_to_this_site_and_its_token_verifies(self):
		# a dev site often runs on http://; Meta only takes https, so the site URL is pinned here
		orig = wa.default_webhook_url
		wa.default_webhook_url = lambda: "https://qa-p5.example.com/api/method/excom.excom.utils.webhook.webhook"
		self.addCleanup(setattr, wa, "default_webhook_url", orig)
		self.addCleanup(setattr, frappe.local, "form_dict", frappe._dict())
		wa.set_number_webhook(self._account())
		url = wa.default_webhook_url()
		token = wa._stored_secret(self.acc.name, "wa_webhook_verify_token")
		self.assertTrue(token)
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["json"]), (f"{BASE}/{PHONE_ID}", {"webhook_configuration": {"override_callback_uri": url, "verify_token": token}}))
		# Meta's GET check of the new URL with that token passes
		frappe.local.form_dict = frappe._dict({"hub.challenge": "CH123", "hub.verify_token": token})
		self.assertEqual(webhook.get().status_code, 200)
		frappe.local.form_dict = frappe._dict({"hub.challenge": "CH123", "hub.verify_token": "wrong"})
		self.assertEqual(webhook.get().status_code, 403)

		wa.clear_number_webhook(self._account())
		self.assertEqual(self.calls[-1]["json"], {"webhook_configuration": {"override_callback_uri": ""}})
		for url, tok in (("http://example.com/hook", "t"), ("https://example.com/" + "a" * 200, "t"), ("https://other.example.com/hook", "")):
			with self.assertRaises(wa.AccountSettingError):
				wa.set_number_webhook(self._account(), url, tok)

	def test_waba_webhook_set_and_clear(self):
		wa.set_waba_webhook(self._account(), "https://other.example.com/hook", "their-token")
		self.assertEqual((self.calls[-1]["method"], self.calls[-1]["url"]), ("POST", f"{BASE}/{WABA}/subscribed_apps"))
		self.assertEqual(self.calls[-1]["json"], {"override_callback_uri": "https://other.example.com/hook", "verify_token": "their-token"})
		wa.clear_waba_webhook(self._account())
		self.assertEqual((self.calls[-1]["method"], self.calls[-1]["url"], self.calls[-1]["json"], self.calls[-1]["data"]), ("POST", f"{BASE}/{WABA}/subscribed_apps", None, None))

	def test_webhook_read_back(self):
		self.replies = [FakeResp(200, {"webhook_configuration": {"phone_number": "https://p/hook", "application": "https://app/hook"}, "id": PHONE_ID}),
			FakeResp(200, {"data": [{"whatsapp_business_api_data": {"id": "670", "name": "QA App"}, "override_callback_uri": "https://w/hook"}]})]
		out = wa.get_webhook_configuration(self._account())
		self.assertEqual(self.calls[0]["params"], {"fields": "webhook_configuration"})
		self.assertEqual((out["phone_number"], out["application"], out["subscribed_apps"][0]["override_callback_uri"]), ("https://p/hook", "https://app/hook", "https://w/hook"))

	# ── commerce ─────────────────────────────────────────────────────────────
	def test_commerce_settings_use_query_parameters(self):
		wa.set_commerce_settings(self._account(), is_cart_enabled="0", is_catalog_visible=True)
		call = self.calls[-1]
		self.assertEqual((call["url"], call["params"], call["json"], call["data"]), (f"{BASE}/{PHONE_ID}/whatsapp_commerce_settings",
			{"is_cart_enabled": "false", "is_catalog_visible": "true"}, None, None))
		with self.assertRaises(wa.AccountSettingError):
			wa.set_commerce_settings(self._account())
		self.replies = [FakeResp(200, {"data": [{"is_cart_enabled": True, "is_catalog_visible": False, "id": "1"}]})]
		self.assertEqual(wa.get_commerce_settings(self._account()), {"is_cart_enabled": True, "is_catalog_visible": False})

	def test_link_and_list_catalogues(self):
		with self.assertRaises(wa.AccountSettingError):
			wa.link_catalog(self._account(), "abc")
		wa.link_catalog(self._account(), "1234567890")
		self.assertEqual((self.calls[-1]["url"], self.calls[-1]["data"]), (f"{BASE}/{WABA}/product_catalogs", {"catalog_id": "1234567890"}))
		self.assertEqual(frappe.db.get_value("Excom Channel Account", self.acc.name, "wa_catalog_id"), "1234567890")
		self.replies = [FakeResp(200, {"data": [{"id": "1234567890", "name": "QA Catalogue"}]})]
		self.assertEqual(wa.linked_catalogs(self._account()), [{"id": "1234567890", "name": "QA Catalogue"}])
		self.assertEqual(self.calls[-1]["params"], {"fields": "id,name"})

	# ── access ───────────────────────────────────────────────────────────────
	def test_settings_are_admin_only(self):
		from excom.excom.api import whatsapp_account as api

		frappe.set_user("Guest")
		for fn, kw in ((api.get_profile, {"account": self.acc.name}), (api.set_pin, {"account": self.acc.name, "pin": "123456"}),
				(api.set_webhook, {"account": self.acc.name}), (api.block, {"account": self.acc.name, "user": "919900500099"})):
			with self.assertRaises(frappe.PermissionError):
				fn(**kw)
		self.assertEqual(self.calls, [])
