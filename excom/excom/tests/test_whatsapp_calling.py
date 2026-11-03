"""WhatsApp Business Calling (HLD-005 Phase 6): calling settings, incoming calls rung to agents'
browsers, first answer wins (pre_accept + accept), terminate, call permission requests and replies,
business-initiated calls, the voice_call / call_permission_request builders, BSUID-only callers.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_calling'}"
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.channels.voice import whatsapp_call_settings as settings
from excom.excom.channels.voice import whatsapp_calling as calling
from excom.excom.channels.voice.providers import whatsapp as wa
from excom.excom.services import whatsapp_interactive as wi
from excom.excom.services import whatsapp_service as ws
from excom.excom.utils import webhook

PHONE_ID = "QACALL26"
WABA = "QACALLWABA26"
BUSINESS = "919800000000"
AGENT_A = "qa-wa-call-a@example.com"
AGENT_B = "qa-wa-call-b@example.com"
CALLER = "919800000101"
CALLER_2 = "919800000102"
CALLER_3 = "919800000103"
BSUID = "IN.QACALL1001"
BSUID_ONLY = "IN.QACALL2002"
BSUID_3 = "IN.QACALL3003"
OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\na=candidate:1 1 udp 2122260223 157.240.0.1 3480 typ host\r\n"
ANSWER = "v=0\r\no=- 3 4 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=setup:active\r\na=candidate:1 1 udp 2122260223 192.0.2.10 54400 typ host\r\n"

PERM_NONE = {"messaging_product": "whatsapp", "permission": {"status": "no_permission"}, "actions": [
	{"action_name": "send_call_permission_request", "can_perform_action": True,
	 "limits": [{"time_period": "PT24H", "max_allowed": 1, "current_usage": 0}, {"time_period": "P7D", "max_allowed": 2, "current_usage": 0}]},
	{"action_name": "start_call", "can_perform_action": False, "limits": [{"time_period": "PT24H", "max_allowed": 100, "current_usage": 0}]}]}
PERM_TEMP = {"messaging_product": "whatsapp", "permission": {"status": "temporary", "expiration_time": 1893456000}, "actions": [
	{"action_name": "send_call_permission_request", "can_perform_action": True, "limits": []},
	{"action_name": "start_call", "can_perform_action": True, "limits": [{"time_period": "PT24H", "max_allowed": 100, "current_usage": 3}]}]}


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b = status, body
		self.content = b"x"
		self.text = json.dumps(body)
		self.ok = status < 400

	def json(self):
		return self._b


def _calls(value: dict) -> str:
	return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "calls", "value": {
		"messaging_product": "whatsapp", "metadata": {"display_phone_number": BUSINESS, "phone_number_id": PHONE_ID}, **value}}]}]})


def _messages(value: dict) -> str:
	return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
		"messaging_product": "whatsapp", "metadata": {"display_phone_number": BUSINESS, "phone_number_id": PHONE_ID}, **value}}]}]})


def _connect(call_id: str, phone: str | None = CALLER, user_id: str = BSUID, name: str = "QA Caller") -> str:
	contact = {"profile": {"name": name}, "user_id": user_id}
	item = {"id": call_id, "to": BUSINESS, "from_user_id": user_id, "event": "connect", "timestamp": "1767168000",
		"direction": "USER_INITIATED", "cta_payload": "order-42", "session": {"sdp_type": "offer", "sdp": OFFER}}
	if phone:
		contact["wa_id"] = phone
		item["from"] = phone
	return _calls({"contacts": [contact], "calls": [item]})


def _terminate(call_id: str, direction: str = "USER_INITIATED", duration: int = 0, status="COMPLETED", errors=None) -> str:
	item = {"id": call_id, "to": BUSINESS, "from": CALLER, "from_user_id": BSUID, "event": "terminate",
		"direction": direction, "timestamp": "1767168100", "status": status}
	if duration:
		item.update({"start_time": "1767168000", "end_time": str(1767168000 + duration), "duration": duration})
	value = {"calls": [item]}
	if errors:
		value["errors"] = errors
	return _calls(value)


_CALL_IDS = ("wacid.QAIN1", "wacid.QAIN2", "wacid.QAIN3", "wacid.QAIN4", "wacid.QABS1", "wacid.QAOUT1", "wacid.QAOUT2")


class TestWhatsAppCalling(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		cls.acc = frappe.get_doc({
			"doctype": "Excom Channel Account", "account_name": "QA WA Calling", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_display_phone": BUSINESS,
			"wa_token": "qa-token-0000000000", "wa_calling_status": "ENABLED",
		}).insert(ignore_permissions=True)
		for email in (AGENT_A, AGENT_B):
			if not frappe.db.exists("User", email):
				frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
					"send_welcome_email": 0}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		for email in (AGENT_A, AGENT_B):
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.posts, self.gets, self.replies, self.get_replies = [], [], [], []
		self._orig = (ws.http_requests.post, ws.http_requests.get)
		self.ready = {AGENT_A, AGENT_B}

		def fake_post(url, **kw):
			body = kw.get("json") or {}
			self.posts.append((url, body))
			if self.replies:
				return self.replies.pop(0)
			if url.endswith("/messages"):
				return FakeResp(200, {"messages": [{"id": f"wamid.QACALL{len(self.posts)}"}], "contacts": [{"wa_id": "x"}]})
			if url.endswith("/calls") and body.get("action") == "connect":
				return FakeResp(200, {"messaging_product": "whatsapp", "calls": [{"id": "wacid.QAOUT1"}]})
			return FakeResp(200, {"messaging_product": "whatsapp", "success": True})

		def fake_get(url, **kw):
			self.gets.append((url, kw.get("params")))
			return self.get_replies.pop(0) if self.get_replies else FakeResp(200, PERM_TEMP)

		ws.http_requests.post, ws.http_requests.get = fake_post, fake_get
		self.realtime = patch("frappe.publish_realtime").start()
		patch.object(calling.routing, "line_agents", return_value=[{"user": AGENT_A}, {"user": AGENT_B}]).start()
		patch.object(calling.presence, "can_ring_browser", side_effect=lambda user, account: user in self.ready).start()
		self.addCleanup(patch.stopall)
		self.acc.reload()

	def tearDown(self):
		ws.http_requests.post, ws.http_requests.get = self._orig
		frappe.db.rollback()
		for call_id in _CALL_IDS:
			calling.release(call_id)
		for user in (AGENT_A, AGENT_B, "Administrator"):
			calling.presence.clear_busy(user)
		identities = frappe.db.sql(
			"""SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '%%9198000001%%'
			OR primary_whatsapp LIKE '9198000001%%' OR wa_bsuid LIKE 'IN.QACALL%%'""", pluck=True)
		frappe.db.delete("Excom WhatsApp Call Permission", {"channel_account": self.acc.name})
		frappe.db.delete("Excom Call", {"channel_account": self.acc.name})
		frappe.db.delete("Excom Message", {"account": self.acc.name})
		frappe.db.delete("Excom Thread", {"account": self.acc.name})
		if identities:
			for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
				frappe.db.delete(child, {"parent": ["in", identities]})
			frappe.db.delete("Excom Consent", {"omni_identity": ["in", identities]})
			frappe.db.delete("Omni Identity", {"name": ["in", identities]})
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _published(self, event: str) -> list[tuple[str, dict]]:
		return [(c.kwargs.get("user"), c.args[1]) for c in self.realtime.call_args_list if c.args and c.args[0] == event]

	def _call(self, call_id: str):
		name = frappe.db.get_value("Excom Call", {"provider_call_id": call_id}, "name")
		return frappe.get_doc("Excom Call", name) if name else None

	def _thread(self, phone: str = CALLER_2, bsuid: str = "", window: bool = True) -> str:
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
		from excom.excom.services.thread_service import upsert_thread

		identity = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone or bsuid,
			display_name="QA Callee", wa_user_id=bsuid, wa_waba=WABA)
		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		frappe.db.set_value("Excom Thread", thread, "last_inbound_at",
			now_datetime() if window else add_to_date(now_datetime(), hours=-30))
		frappe.db.commit()
		return thread

	def _calls_posted(self, action: str) -> list[dict]:
		return [body for url, body in self.posts if url.endswith(f"/{PHONE_ID}/calls") and body.get("action") == action]

	# ── settings ─────────────────────────────────────────────────────────────
	def test_settings_payload_keeps_holidays(self):
		stored = json.dumps([{"date": "2099-01-26", "start_time": "0000", "end_time": "2359"}])
		week = [{"day_of_week": "monday", "open_time": "09:30", "close_time": "1300"},
			{"day_of_week": "MONDAY", "open_time": "1400", "close_time": "1830"}]
		body = settings.build_payload({"status": "ENABLED", "call_icon_visibility": "DEFAULT", "callback_permission_status": "ENABLED",
			"call_hours_status": "ENABLED", "timezone_id": "Asia/Kolkata", "weekly_operating_hours": week}, stored_holidays=stored)
		hours = body["calling"]["call_hours"]
		self.assertEqual(hours["holiday_schedule"], [{"date": "2099-01-26", "start_time": "0000", "end_time": "2359"}])
		self.assertEqual(hours["weekly_operating_hours"][0], {"day_of_week": "MONDAY", "open_time": "0930", "close_time": "1300"})
		self.assertEqual(body["calling"]["status"], "ENABLED")
		# holidays given explicitly replace the stored ones; past ones are dropped
		body = settings.build_payload({"call_hours_status": "ENABLED", "timezone_id": "Asia/Kolkata", "weekly_operating_hours": week,
			"holiday_schedule": [{"date": "2099-08-15"}, {"date": "2001-01-01"}]}, stored_holidays=stored)
		self.assertEqual([h["date"] for h in body["calling"]["call_hours"]["holiday_schedule"]], ["2099-08-15"])
		# disabled with no week: call_hours left out rather than sent incomplete
		body = settings.build_payload({"status": "DISABLED", "call_hours_status": "DISABLED"})
		self.assertNotIn("call_hours", body["calling"])

	def test_settings_validation(self):
		day = lambda o, c, d="TUESDAY": {"day_of_week": d, "open_time": o, "close_time": c}  # noqa: E731
		base = {"call_hours_status": "ENABLED", "timezone_id": "Asia/Kolkata"}
		for bad in ([day("0900", "1000"), day("1100", "1200"), day("1300", "1400")],  # 3 ranges a day
				[day("0900", "1200"), day("1100", "1300")],  # overlap
				[day("1200", "1100")],  # open after close
				[day("2500", "2600")],  # not a time
				[day("0900", "1000", "FUNDAY")]):
			with self.assertRaises(frappe.ValidationError):
				settings.build_payload({**base, "weekly_operating_hours": bad})
		with self.assertRaises(frappe.ValidationError):
			settings.build_payload({"call_hours_status": "ENABLED", "weekly_operating_hours": [day("0900", "1000")]})  # no time zone
		with self.assertRaises(frappe.ValidationError):
			settings.build_payload({**base, "weekly_operating_hours": []})  # empty week
		with self.assertRaises(frappe.ValidationError):
			settings.build_payload({**base, "weekly_operating_hours": [day("0900", "1000")],
				"holiday_schedule": [{"date": f"2099-01-{i:02d}"} for i in range(1, 22)]})  # 21 holidays
		with self.assertRaises(frappe.ValidationError):
			settings.build_payload({**base, "weekly_operating_hours": [day("0900", "1000")], "holiday_schedule": [{"date": "26/01/2099"}]})
		with self.assertRaises(frappe.ValidationError):
			settings.build_payload({"call_icon_countries": "India"})
		self.assertEqual(settings.build_payload({"call_icon_countries": "in, ae"})["calling"]["call_icons"],
			{"restrict_to_user_countries": ["IN", "AE"]})

	def test_save_posts_the_whole_calling_object_and_keeps_holidays(self):
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_call_holidays",
			json.dumps([{"date": "2099-10-20", "start_time": "0000", "end_time": "2359"}]))
		out = settings.save(self.acc.name, {"status": "ENABLED", "callback_permission_status": "ENABLED", "call_hours_status": "ENABLED",
			"timezone_id": "Asia/Kolkata", "weekly_operating_hours": [{"day_of_week": "FRIDAY", "open_time": "1000", "close_time": "1800"}],
			"rate_per_minute": 0.3885})
		url, body = self.posts[-1]
		self.assertTrue(url.endswith(f"/{PHONE_ID}/settings"))
		self.assertEqual(body["calling"]["call_hours"]["holiday_schedule"][0]["date"], "2099-10-20")
		self.assertEqual(out["settings"]["timezone_id"], "Asia/Kolkata")
		self.assertAlmostEqual(frappe.db.get_value("Excom Channel Account", self.acc.name, "wa_call_rate_per_minute"), 0.3885)

	def test_read_back_stores_sip_and_restrictions_and_warns(self):
		self.get_replies.append(FakeResp(200, {"calling": {"status": "ENABLED", "call_icon_visibility": "DEFAULT",
			"callback_permission_status": "DISABLED", "call_hours": {"status": "ENABLED", "timezone_id": "Asia/Kolkata",
			"weekly_operating_hours": [{"day_of_week": "MONDAY", "open_time": "0900", "close_time": "1800"}],
			"holiday_schedule": []}, "sip": {"status": "ENABLED", "servers": [{"hostname": "sip.example.com"}]},
			"restrictions": {"restrictions_list": [{"type": "RESTRICTED_BUSINESS_INITIATED_CALLING", "reason": "low quality", "expiration": 1754072386}]}}}))
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_messaging_limit", "TIER_250")
		out = settings.fetch(self.acc.name)
		self.assertTrue(self.gets[-1][0].endswith(f"/{PHONE_ID}/settings"))
		self.assertEqual(out["settings"]["sip_status"], "ENABLED")
		self.assertIn("RESTRICTED_BUSINESS_INITIATED_CALLING", out["settings"]["restrictions"])
		text = " ".join(w["text"] for w in out["warnings"])
		self.assertIn("SIP", text)
		self.assertIn("2,000", text)
		self.assertTrue(settings.low_tier("TIER_250"))
		self.assertFalse(settings.low_tier("TIER_2K"))
		self.assertIsNone(settings.low_tier(""))

	# ── incoming ─────────────────────────────────────────────────────────────
	def test_incoming_connect_creates_call_and_rings_ready_agents(self):
		self.ready = {AGENT_A, AGENT_B}
		webhook._process_webhook_payload(_connect("wacid.QAIN1"))
		call = self._call("wacid.QAIN1")
		self.assertEqual((call.provider, call.direction, call.status, call.transport), ("WhatsApp", "Inbound", "Ringing", "Browser"))
		self.assertEqual(call.ring_set_users(), [AGENT_A, AGENT_B])
		self.assertEqual(call.ivr_selection, "order-42")
		self.assertTrue(call.thread and call.omni_identity)
		self.assertEqual(frappe.db.get_value("Excom Thread", call.thread, "channel"), "whatsapp")
		self.assertTrue(frappe.db.get_value("Excom Thread", call.thread, "last_inbound_at"))  # the call opened the window
		rung = self._published("excom:call_ringing")
		self.assertEqual(sorted(u for u, _ in rung), [AGENT_A, AGENT_B])
		self.assertEqual(rung[0][1]["sdp"], OFFER)
		self.assertEqual(rung[0][1]["channel"], "whatsapp")
		self.assertEqual(rung[0][1]["call"], call.name)
		# the event log holds neither the number nor the SDP
		self.assertNotIn(CALLER, call.provider_events)
		self.assertNotIn("v=0", call.provider_events)
		self.assertTrue(frappe.db.exists("Excom Message", {"thread": call.thread, "message_type": "Call"}))
		# Meta retries webhooks: a second delivery rings nobody again
		self.realtime.reset_mock()
		webhook._process_webhook_payload(_connect("wacid.QAIN1"))
		self.assertEqual(frappe.db.count("Excom Call", {"provider_call_id": "wacid.QAIN1"}), 1)
		self.assertEqual(self._published("excom:call_ringing"), [])

	def test_nobody_ready_rejects_at_once(self):
		self.ready = set()
		webhook._process_webhook_payload(_connect("wacid.QAIN2"))
		call = self._call("wacid.QAIN2")
		self.assertEqual(call.status, "Missed")
		self.assertEqual(len(self._calls_posted("reject")), 1)
		self.assertEqual(self._published("excom:call_ringing"), [])

	def test_answer_sends_pre_accept_then_accept_and_first_answer_wins(self):
		webhook._process_webhook_payload(_connect("wacid.QAIN1"))
		call = self._call("wacid.QAIN1")
		out = calling.answer(call.name, ANSWER, AGENT_A)
		pre, acc = self._calls_posted("pre_accept"), self._calls_posted("accept")
		self.assertEqual(len(pre), 1)
		self.assertEqual(len(acc), 1)
		self.assertEqual(pre[0]["call_id"], "wacid.QAIN1")
		self.assertEqual(pre[0]["session"], {"sdp_type": "answer", "sdp": ANSWER})
		self.assertEqual(acc[0]["session"], pre[0]["session"])  # the same SDP twice, as Meta requires
		self.assertEqual(acc[0]["biz_opaque_callback_data"], call.name)
		self.assertEqual(out["call"], call.name)
		call.reload()
		self.assertEqual((call.status, call.answered_by, call.agent), ("In Progress", AGENT_A, AGENT_A))
		cancelled = [u for u, p in self._published(calling.EV_SIGNAL) if p.get("type") == "cancel"]
		self.assertEqual(cancelled, [AGENT_B])
		self.assertTrue(any(p.get("answered_by") == AGENT_A for _, p in self._published("excom:call_answered")))
		with self.assertRaises(frappe.ValidationError):
			calling.answer(call.name, ANSWER, AGENT_B)
		self.assertEqual(len(self._calls_posted("accept")), 1)

	def test_concurrent_answer_loses_the_claim(self):
		webhook._process_webhook_payload(_connect("wacid.QAIN3"))
		call = self._call("wacid.QAIN3")
		self.assertTrue(calling.claim("wacid.QAIN3", AGENT_A))  # A's request is in flight
		with self.assertRaises(frappe.ValidationError):
			calling.answer(call.name, ANSWER, AGENT_B)
		self.assertEqual(self._calls_posted("pre_accept"), [])
		self.assertTrue(calling.claim("wacid.QAIN3", AGENT_A))  # the same agent may retry

	def test_failed_accept_releases_the_claim(self):
		webhook._process_webhook_payload(_connect("wacid.QAIN4"))
		call = self._call("wacid.QAIN4")
		self.replies.append(FakeResp(400, {"error": {"code": 138007, "message": "Connect timeout"}}))
		with self.assertRaises(Exception):
			calling.answer(call.name, ANSWER, AGENT_A)
		self.assertEqual(frappe.db.get_value("Excom Call", call.name, "status"), "Ringing")
		self.assertTrue(calling.claim("wacid.QAIN4", AGENT_B))

	def test_terminate_records_duration_and_status(self):
		webhook._process_webhook_payload(_connect("wacid.QAIN1"))
		call = self._call("wacid.QAIN1")
		calling.answer(call.name, ANSWER, AGENT_A)
		webhook._process_webhook_payload(_terminate("wacid.QAIN1", duration=120))
		call.reload()
		self.assertEqual((call.status, call.duration, call.reconciled), ("Completed", 120, 1))
		self.assertNotIn(CALLER, call.provider_events)
		ended = [u for u, p in self._published(calling.EV_SIGNAL) if p.get("type") == "terminate"]
		self.assertIn(AGENT_A, ended)
		self.assertFalse(calling.presence.is_busy(AGENT_A))
		# an unanswered call is a missed call; a failure keeps its code
		webhook._process_webhook_payload(_connect("wacid.QAIN2"))
		webhook._process_webhook_payload(_terminate("wacid.QAIN2", status=["Failed"],
			errors=[{"code": 138021, "message": "Media receive timeout"}]))
		missed = self._call("wacid.QAIN2")
		self.assertEqual((missed.status, missed.hangup_cause), ("Failed", "138021"))
		webhook._process_webhook_payload(_connect("wacid.QAIN3"))
		webhook._process_webhook_payload(_terminate("wacid.QAIN3"))
		self.assertEqual(self._call("wacid.QAIN3").status, "Missed")
		# no call detail record at Meta: the reconcile sweep sees closed calls as found, open ones as not yet
		adapter = wa.adapter_for(self.acc.name)
		self.assertTrue(adapter.fetch_call_details("wacid.QAIN1").found)
		webhook._process_webhook_payload(_connect("wacid.QAIN4"))
		self.assertFalse(adapter.fetch_call_details("wacid.QAIN4").found)

	def test_bsuid_only_caller(self):
		webhook._process_webhook_payload(_connect("wacid.QABS1", phone=None, user_id=BSUID_ONLY, name="QA Username"))
		call = self._call("wacid.QABS1")
		self.assertFalse(call.customer_number)
		self.assertEqual(frappe.db.get_value("Omni Identity", call.omni_identity, "wa_bsuid"), BSUID_ONLY)
		self.assertTrue(call.thread)
		payload = self._published("excom:call_ringing")[0][1]
		self.assertEqual(payload["display_name"], "QA Username")
		# calling them back goes by user id, for the permission check and the call itself
		frappe.db.set_value("Excom Thread", call.thread, "last_inbound_at", now_datetime())
		calling.start_call(call.thread, OFFER, "Administrator")
		self.assertEqual(self.gets[-1][1], {"recipient": BSUID_ONLY})
		connect = self._calls_posted("connect")[-1]
		self.assertEqual(connect.get("recipient"), BSUID_ONLY)
		self.assertNotIn("to", connect)

	# ── permission ───────────────────────────────────────────────────────────
	def test_permission_request_limits(self):
		now = now_datetime()
		window = calling.request_window([str(add_to_date(now, hours=-2))], now)
		self.assertFalse(window["allowed"])
		self.assertEqual(str(window["next_at"])[:16], str(add_to_date(now, hours=22))[:16])
		window = calling.request_window([str(add_to_date(now, days=-6)), str(add_to_date(now, days=-2))], now)
		self.assertFalse(window["allowed"])  # 2 in 7 days
		self.assertTrue(calling.request_window([str(add_to_date(now, days=-8)), str(add_to_date(now, days=-2))], now)["allowed"])

		thread = self._thread()
		self.get_replies.extend([FakeResp(200, PERM_NONE), FakeResp(200, PERM_NONE), FakeResp(200, PERM_NONE)])
		out = calling.request_permission(thread, "May we call you about your order?")
		url, body = self.posts[-1]
		self.assertTrue(url.endswith(f"/{PHONE_ID}/messages"))
		self.assertEqual(body["interactive"], {"type": "call_permission_request", "action": {"name": "call_permission_request"},
			"body": {"text": "May we call you about your order?"}})
		self.assertEqual(frappe.db.get_value("Excom Message", out["message"], "content_text"), "May we call you about your order? [Allow calls]")
		self.assertEqual(out["permission"]["requests_today"], 1)
		self.assertFalse(out["permission"]["can_request"])
		sent = len(self.posts)
		with self.assertRaises(frappe.ValidationError):
			calling.request_permission(thread, "Again?")  # 1 per 24 h
		self.assertEqual(len(self.posts), sent)

	def test_permission_request_needs_the_window(self):
		thread = self._thread(window=False)
		self.get_replies.append(FakeResp(200, PERM_NONE))
		with self.assertRaises(frappe.ValidationError):
			calling.request_permission(thread)
		self.assertEqual([u for u, _ in self.posts if u.endswith("/messages")], [])

	def test_call_permission_reply_parsing(self):
		message = {"from": CALLER_3, "from_user_id": BSUID_3, "id": "wamid.QACPR1", "timestamp": "1767168000", "type": "interactive",
			"interactive": {"type": "call_permission_reply", "call_permission_reply": {
				"response": "accept", "is_permanent": False, "expiration_timestamp": "1893456000", "response_source": "user_action"}}}
		parsed = webhook._parse_inbound(message, "interactive", self.acc)
		self.assertEqual(parsed[0], "📞 Allowed calls for 7 days")
		self.assertEqual((parsed[3], parsed[4]), ("Interactive", "call_permission"))
		self.assertEqual(parsed[5]["response"], "accept")

		contacts = [{"profile": {"name": "QA Reply"}, "wa_id": CALLER_3, "user_id": BSUID_3}]
		webhook._process_webhook_payload(_messages({"contacts": contacts, "messages": [message]}))
		msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.QACPR1"}, ["omni_identity", "content_text"], as_dict=True)
		self.assertEqual(msg.content_text, "📞 Allowed calls for 7 days")
		# the inbound hook (excom_inbound_handlers) cached the grant; call it directly too, it is idempotent
		calling.on_inbound({"kind": "call_permission", "identity": msg.omni_identity, "account": self.acc.name,
			"payload": message["interactive"]["call_permission_reply"], "signed": True})
		row = frappe.get_doc("Excom WhatsApp Call Permission", {"omni_identity": msg.omni_identity})
		self.assertEqual((row.status, row.response_source), ("temporary", "user_action"))
		self.assertTrue(row.expires_on)

		revoked = dict(message, id="wamid.QACPR2", interactive={"type": "call_permission_reply", "call_permission_reply": {
			"response": "reject", "response_source": "automatic"}})
		self.assertEqual(webhook._parse_inbound(revoked, "interactive", self.acc)[0], "📞 Call permission withdrawn (unanswered calls)")
		calling.on_inbound({"kind": "call_permission", "identity": msg.omni_identity, "account": self.acc.name,
			"payload": revoked["interactive"]["call_permission_reply"], "signed": True})
		row.reload()
		self.assertEqual((row.status, row.response_source), ("no_permission", "automatic"))
		permanent = calling.apply_permission_reply(self.acc.name, msg.omni_identity, {"response": "accept", "is_permanent": True})
		self.assertEqual(permanent["status"], "permanent")

	def test_permission_response_and_summary(self):
		parsed = calling.parse_permission_response(PERM_TEMP)
		self.assertEqual((parsed["status"], parsed["can_call"], parsed["can_request"]), ("temporary", 1, 1))
		self.assertTrue(parsed["expires_on"])
		self.assertEqual(json.loads(parsed["call_limits"])[0]["current_usage"], 3)
		row = frappe._dict(status="temporary", expires_on=add_to_date(now_datetime(), days=-1), can_call=1, can_request=1,
			requests_sent="[]", call_limits="[]", consecutive_unanswered=2, call_limit_resets_on=None, checked_on=None,
			last_response="", response_source="")
		summary = calling.summarise(row, window_open=True)
		self.assertEqual(summary["status"], "no_permission")  # lapsed: Meta sends no webhook for that
		self.assertFalse(summary["can_call"])
		self.assertTrue(summary["can_request"])
		self.assertTrue(summary["unanswered_warning"])
		row.update(status="permanent", expires_on=None)
		summary = calling.summarise(row, window_open=True)
		self.assertTrue(summary["can_call"])
		self.assertFalse(summary["can_request"])  # 138017 otherwise
		self.assertFalse(calling.summarise(frappe._dict(row, status="no_permission"), window_open=False)["can_request"])

	# ── outgoing ─────────────────────────────────────────────────────────────
	def test_outbound_connect_and_progress(self):
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_call_rate_per_minute", 0.3885)
		thread = self._thread()
		out = calling.start_call(thread, OFFER, "Administrator")
		self.assertTrue(self.gets[-1][0].endswith(f"/{PHONE_ID}/call_permissions"))
		self.assertEqual(self.gets[-1][1], {"user_wa_id": CALLER_2})
		connect = self._calls_posted("connect")[-1]
		self.assertEqual(connect["to"], CALLER_2)
		self.assertEqual(connect["session"], {"sdp_type": "offer", "sdp": OFFER})
		self.assertEqual(connect["biz_opaque_callback_data"], out["call"])
		call = frappe.get_doc("Excom Call", out["call"])
		self.assertEqual((call.provider_call_id, call.direction, call.status, call.agent), ("wacid.QAOUT1", "Outbound", "Ringing", "Administrator"))

		# Meta's SDP answer goes to the browser that made the offer
		answer_item = {"id": "wacid.QAOUT1", "to": CALLER_2, "from": BUSINESS, "event": "connect", "direction": "BUSINESS_INITIATED",
			"biz_opaque_callback_data": out["call"], "session": {"sdp_type": "answer", "sdp": ANSWER}}
		webhook._process_webhook_payload(_calls({"calls": [answer_item]}))
		answers = [(u, p) for u, p in self._published(calling.EV_SIGNAL) if p.get("type") == "answer"]
		self.assertEqual(answers[0][0], "Administrator")
		self.assertEqual(answers[0][1]["sdp"], ANSWER)

		webhook._process_webhook_payload(_calls({"statuses": [{"id": "wacid.QAOUT1", "type": "call", "status": "RINGING",
			"recipient_id": CALLER_2, "biz_opaque_callback_data": out["call"]}]}))
		webhook._process_webhook_payload(_calls({"statuses": [{"id": "wacid.QAOUT1", "type": "call", "status": "ACCEPTED",
			"recipient_id": CALLER_2}]}))
		call.reload()
		self.assertEqual((call.status, call.answered_by), ("In Progress", "Administrator"))
		webhook._process_webhook_payload(_terminate("wacid.QAOUT1", direction="BUSINESS_INITIATED", duration=30))
		call.reload()
		self.assertEqual((call.status, call.duration), ("Completed", 30))
		self.assertAlmostEqual(call.cost, 0.19, places=2)  # 5 pulses of 6 s at ₹0.3885 a minute

	def test_outbound_rejected_counts_as_unanswered(self):
		thread = self._thread()
		self.replies.append(FakeResp(200, {"messaging_product": "whatsapp", "calls": [{"id": "wacid.QAOUT2"}]}))
		out = calling.start_call(thread, OFFER, "Administrator")
		webhook._process_webhook_payload(_calls({"statuses": [{"id": "wacid.QAOUT2", "type": "call", "status": "REJECTED"}]}))
		webhook._process_webhook_payload(_terminate("wacid.QAOUT2", direction="BUSINESS_INITIATED"))
		self.assertEqual(frappe.db.get_value("Excom Call", out["call"], "status"), "Busy")
		identity = frappe.db.get_value("Excom Thread", thread, "omni_identity")
		self.assertEqual(frappe.db.get_value("Excom WhatsApp Call Permission", {"omni_identity": identity}, "consecutive_unanswered"), 1)

	def test_outbound_without_permission_is_refused_before_meta(self):
		thread = self._thread()
		self.get_replies.append(FakeResp(200, PERM_NONE))
		with self.assertRaises(frappe.ValidationError):
			calling.start_call(thread, OFFER, "Administrator")
		self.assertEqual(self._calls_posted("connect"), [])
		self.assertFalse(frappe.db.exists("Excom Call", {"thread": thread, "direction": "Outbound"}))

	def test_meta_refusal_is_remembered(self):
		thread = self._thread()
		self.replies.append(FakeResp(400, {"error": {"code": 138006, "message": "No approved call permission"}}))
		with self.assertRaises(frappe.ValidationError):
			calling.start_call(thread, OFFER, "Administrator")
		identity = frappe.db.get_value("Excom Thread", thread, "omni_identity")
		row = frappe.get_doc("Excom WhatsApp Call Permission", {"omni_identity": identity})
		self.assertEqual(row.status, "no_permission")
		self.assertTrue(row.last_error.startswith("138006"))
		self.assertEqual(frappe.db.get_value("Excom Call", {"thread": thread}, "status"), "Failed")

	# ── builders and pure helpers ────────────────────────────────────────────
	def test_interactive_builders_and_limits(self):
		button = wi.voice_call("Call us on WhatsApp", display_text="Call Now", ttl_minutes=100, payload="order-42")
		self.assertEqual(button, {"type": "voice_call", "body": {"text": "Call us on WhatsApp"}, "action": {"name": "voice_call",
			"parameters": {"display_text": "Call Now", "ttl_minutes": 100, "payload": "order-42"}}})
		self.assertEqual(wi.voice_call("Hi")["action"], {"name": "voice_call"})  # Meta's defaults
		self.assertEqual(wi.preview_text(button), "Call us on WhatsApp [📞 Call Now]")
		for bad in (dict(display_text="x" * 21), dict(ttl_minutes=43201), dict(ttl_minutes=-5), dict(payload="p" * 513)):
			with self.assertRaises(wi.InteractiveLimitError):
				wi.voice_call("Call us", **bad)
		with self.assertRaises(wi.InteractiveLimitError):
			wi.voice_call("")
		self.assertEqual(wi.voice_call("Call", ttl_minutes=43200)["action"]["parameters"]["ttl_minutes"], 43200)
		self.assertEqual(wi.call_permission_request(), {"type": "call_permission_request", "action": {"name": "call_permission_request"}})
		self.assertNotIn("header", wi.call_permission_request("May we call?"))
		with self.assertRaises(wi.InteractiveLimitError):
			wi.call_permission_request("x" * 1025)

	def test_payloads_and_event_helpers(self):
		self.assertEqual(wa.action_payload("wacid.X", "terminate"), {"messaging_product": "whatsapp", "call_id": "wacid.X", "action": "terminate"})
		self.assertEqual(wa.action_payload("wacid.X", "pre_accept", "v=0\nm=audio 9 x\n")["session"]["sdp"], "v=0\r\nm=audio 9 x\r\n")
		with self.assertRaises(frappe.ValidationError):
			wa.action_payload("wacid.X", "accept", "")
		with self.assertRaises(frappe.ValidationError):
			wa.action_payload("wacid.X", "accept", "v=0\r\nm=video 9 x\r\n")
		self.assertEqual(wa.terminate_status({"status": ["Failed"]}), "FAILED")
		self.assertEqual(wa.terminate_status({"status": "completed"}), "COMPLETED")
		self.assertEqual(wa.call_status_after_terminate({"status": "COMPLETED", "direction": "BUSINESS_INITIATED"}, False), "No Answer")
		self.assertEqual(wa.call_cost(61, 1.0), 1.1)  # 11 pulses of 6 s
		self.assertEqual(wa.call_cost(0, 1.0), 0.0)
		scrubbed = wa.scrub_event({"id": "wacid.X", "from": CALLER, "to": BUSINESS, "from_user_id": BSUID, "session": {"sdp_type": "offer", "sdp": OFFER}})
		self.assertNotIn(CALLER, json.dumps(scrubbed))
		self.assertNotIn(BSUID, json.dumps(scrubbed))
		self.assertNotIn("v=0", json.dumps(scrubbed))
		servers = wa.parse_ice_servers(json.dumps([{"urls": "stun:stun.example.com:3478"}, {"urls": ["turn:t.example.com"], "username": "u"},
			{"urls": "http://nope"}]), credential="secret")
		self.assertEqual(servers, [{"urls": ["stun:stun.example.com:3478"]}, {"urls": ["turn:t.example.com"], "username": "u", "credential": "secret"}])
		self.assertEqual(wa.parse_ice_servers("not json"), [])
		descriptor = wa.adapter_for(self.acc.name).sdk_descriptor()
		self.assertEqual(descriptor["sdk"], "whatsapp-webrtc")
		self.assertTrue(descriptor["options"]["iceServers"])

	def test_provider_registry_routes_whatsapp_accounts(self):
		from excom.excom.channels.voice import providers

		adapter = providers.for_account(self.acc.name)
		self.assertIsInstance(adapter, wa.WhatsAppCallingAdapter)
		self.assertTrue(providers.is_softphone(adapter))
