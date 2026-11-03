"""WhatsApp platform deadlines (HLD-005 Phase 0): Graph v26, Marketing Messages API routing, error
classes, consent gate + ledger, business-scoped user ids, batched/monotonic statuses with pricing,
account/template/preference webhooks, paused broadcasts, pricing-based analytics.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_platform'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import whatsapp_errors as wae
from excom.excom.services import whatsapp_service as ws
from excom.excom.utils import webhook
from excom.excom.utils.graph import graph_version, is_outdated

PHONE_ID = "QAPHONE26"
WABA = "QAWABA26"


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b = status, body
		self.content = b"x"
		self.text = json.dumps(body)
		self.ok = status < 400

	def json(self):
		return self._b


def _ok(wamid="wamid.QA", **extra):
	return FakeResp(200, {"messages": [{"id": wamid, **extra}], "contacts": [{"input": "x", "wa_id": "x"}]})


def _payload(field, value, waba=WABA):
	return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": waba, "changes": [{"field": field, "value": value}]}]})


_TEST_BSUIDS = ("IN.USER222", "IN.NEW444", "IN.OLD333", "IN.PREF111")


def _cleanup(account: str) -> None:
	"""Production code commits (webhooks, identity creation), so test rows are removed by key."""
	identities = frappe.db.sql(
		"""SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199000%%' OR normalized_phone LIKE '099000%%'
		OR wa_bsuid IN %(ids)s OR primary_whatsapp LIKE '9199000%%'""",
		{"ids": _TEST_BSUIDS}, pluck=True,
	)
	if identities:
		for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
			frappe.db.delete(child, {"parent": ["in", identities]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", identities]})
		frappe.db.delete("Excom Subscriber", {"omni_identity": ["in", identities]})
		frappe.db.delete("Omni Identity", {"name": ["in", identities]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	frappe.db.delete("Excom WhatsApp Event", {"waba_id": WABA})
	frappe.db.delete("Excom WhatsApp Event", {"channel_account": account})
	for bc in frappe.get_all("Excom Broadcast", filters={"broadcast_name": "QA P0"}, pluck="name"):
		frappe.db.delete("Excom Broadcast Log", {"broadcast": bc})
		frappe.db.delete("Comment", {"reference_doctype": "Excom Broadcast", "reference_name": bc})
		frappe.db.delete("Excom Broadcast", bc)
	frappe.db.delete("Notification Log", {"subject": ["like", "WhatsApp: account_alerts%"], "email_content": "qa"})
	frappe.db.delete("Excom Subscriber List", {"list_name": "QA P0 List"})


class TestWhatsAppPlatform(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		cls.acc = frappe.get_doc({
			"doctype": "Excom Channel Account", "account_name": "QA WA Platform", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_version": "v21.0",
			"wa_token": "qa-token-0000000000",
		}).insert(ignore_permissions=True)
		lang = frappe.db.get_value("Language", {}, "name") or "en"
		cls.templates = {}
		for key, category in (("mkt", "MARKETING"), ("utl", "UTILITY")):
			doc = frappe.get_doc({
				"doctype": "WhatsApp Templates", "template_name": f"qa p0 {key}", "actual_name": f"qa_p0_{key}",
				"template": "Hello {{1}}", "language": lang, "language_code": "en", "category": category,
				"id": f"77{len(cls.templates)}", "status": "APPROVED",
			})
			doc.name = f"qa-p0-{key}"
			doc.db_insert()
			cls.templates[key] = doc.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in cls.templates.values():
			frappe.db.delete("WhatsApp Templates", name)
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.calls = []
		self.replies = []
		self._orig_post = ws.http_requests.post

		def fake_post(url, **kw):
			self.calls.append((url, kw.get("json")))
			return self.replies.pop(0) if self.replies else _ok()

		ws.http_requests.post = fake_post
		self.acc.reload()
		frappe.db.set_value("WhatsApp Templates", self.templates["mkt"], "category", "MARKETING")
		frappe.db.set_value("WhatsApp Templates", self.templates["utl"], "category", "UTILITY")
		frappe.db.commit()

	def tearDown(self):
		ws.http_requests.post = self._orig_post
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _identity(self, phone, **flags):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		name = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone, display_name="QA " + phone)
		if flags:
			frappe.db.set_value("Omni Identity", name, flags)
		return name

	def _outbound(self, identity, wamid, status="Sent"):
		from excom.excom.services.thread_service import upsert_thread

		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		return frappe.get_doc({
			"doctype": "Excom Message", "thread": thread, "omni_identity": identity, "channel": "whatsapp",
			"account_doctype": "Excom Channel Account", "account": self.acc.name, "direction": "Outbound",
			"message_type": "Template", "provider_message_id": wamid, "delivery_status": status, "content_text": "x",
		}).insert(ignore_permissions=True).name

	# ── Graph version ────────────────────────────────────────────────────────
	def test_graph_version_floor(self):
		self.assertEqual(graph_version("v21.0"), "v26.0")
		self.assertEqual(graph_version(""), "v26.0")
		self.assertEqual(graph_version("v25.0"), "v25.0")
		self.assertEqual(graph_version("24"), "v24.0")
		self.assertTrue(is_outdated("v23.0"))
		self.assertFalse(is_outdated("v26.0"))

	def test_calls_use_v26_even_with_stored_v21(self):
		ws.send_text_message(self.acc, "+919900000101", "hi")
		self.assertIn("/v26.0/QAPHONE26/messages", self.calls[0][0])

	# ── errors ───────────────────────────────────────────────────────────────
	def test_error_classes(self):
		self.assertEqual(wae.classify(131050), wae.OPTED_OUT)
		self.assertEqual(wae.classify("132015"), wae.PAUSE_CAMPAIGN)
		self.assertEqual(wae.classify(368), wae.STOP_ACCOUNT)
		self.assertEqual(wae.classify(131049), wae.CAPPED)
		self.assertEqual(wae.classify(424242), wae.PERMANENT)

	def test_rate_limit_error_keeps_old_type_and_retry_after(self):
		from excom.excom.utils.errors import ExcomRateLimitError

		self.replies = [FakeResp(400, {"error": {"code": 130429, "message": "too fast", "retry_after": 7}})]
		with self.assertRaises(ExcomRateLimitError) as ctx:
			ws.send_text_message(self.acc, "+919900000102", "hi")
		self.assertIsInstance(ctx.exception, wae.WhatsAppAPIError)
		self.assertEqual(ctx.exception.retry_after, 7)
		self.assertEqual(ctx.exception.error_class, wae.RETRY_GLOBAL)

	# ── Marketing Messages API routing ───────────────────────────────────────
	def test_marketing_goes_to_marketing_endpoint_when_enabled(self):
		self.acc.wa_marketing_api_enabled = 1
		self.acc.wa_marketing_product_policy = "STRICT"
		r = ws.send_template_message(self.acc, "+919900000103", "qa_p0_mkt", "en")
		url, body = self.calls[0]
		self.assertTrue(url.endswith("/QAPHONE26/marketing_messages"))
		self.assertEqual(body["product_policy"], "STRICT")
		self.assertNotIn("message_activity_sharing", body)  # Meta's account default unless chosen
		self.assertEqual(r["category"], "MARKETING")
		self.acc.wa_marketing_activity_sharing = "Off"
		ws.send_template_message(self.acc, "+919900000103", "qa_p0_mkt", "en")
		self.assertIs(self.calls[1][1]["message_activity_sharing"], False)

	def test_marketing_stays_on_messages_when_not_enabled_and_utility_always(self):
		self.acc.wa_marketing_api_enabled = 0
		ws.send_template_message(self.acc, "+919900000104", "qa_p0_mkt", "en")
		self.acc.wa_marketing_api_enabled = 1
		ws.send_template_message(self.acc, "+919900000104", "qa_p0_utl", "en")
		self.assertTrue(self.calls[0][0].endswith("/messages"))
		self.assertTrue(self.calls[1][0].endswith("/messages"))
		self.assertNotIn("product_policy", self.calls[1][1])

	def test_131063_retries_once_on_marketing_endpoint(self):
		self.acc.wa_marketing_api_enabled = 1
		self.replies = [FakeResp(400, {"error": {"code": 131063, "message": "marketing template on cloud api"}}), _ok("wamid.MM")]
		r = ws.send_template_message(self.acc, "+919900000105", "qa_p0_utl", "en")
		self.assertTrue(self.calls[0][0].endswith("/messages"))
		self.assertTrue(self.calls[1][0].endswith("/marketing_messages"))
		self.assertEqual(r["provider_message_id"], "wamid.MM")
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", self.templates["utl"], "category"), "MARKETING")

	def test_held_message_status_is_recorded(self):
		identity = self._identity("919900000106")
		self.replies = [_ok("wamid.HELD", message_status="held_for_quality_assessment")]
		ws.send_template_message(self.acc, "919900000106", "qa_p0_utl", "en")
		name = self._outbound(identity, "wamid.HELD")
		ws.set_message_status("wamid.HELD", "held_for_quality_assessment")  # the after-commit job
		self.assertEqual(frappe.db.get_value("Excom Message", name, "wa_message_status"), "held_for_quality_assessment")

	# ── consent gate ─────────────────────────────────────────────────────────
	def test_marketing_opt_out_blocks_only_marketing(self):
		self._identity("919900000107", wa_marketing_opt_out=1)
		with self.assertRaises(wae.ExcomConsentError):
			ws.send_template_message(self.acc, "+919900000107", "qa_p0_mkt", "en")
		ws.send_template_message(self.acc, "+919900000107", "qa_p0_utl", "en")
		self.assertEqual(len(self.calls), 1)

	def test_whatsapp_stop_blocks_every_template(self):
		self._identity("919900000108", whatsapp_opt_out=1)
		for tpl in ("qa_p0_mkt", "qa_p0_utl"):
			with self.assertRaises(wae.ExcomConsentError):
				ws.send_template_message(self.acc, "919900000108", tpl, "en")
		self.assertEqual(self.calls, [])

	def test_bsuid_is_sent_as_recipient(self):
		ws.send_text_message(self.acc, "IN.13491208655302741918", "hi")
		body = self.calls[0][1]
		self.assertEqual(body["recipient"], "IN.13491208655302741918")
		self.assertNotIn("to", body)

	# ── statuses ─────────────────────────────────────────────────────────────
	def test_status_batch_monotonic_with_pricing(self):
		identity = self._identity("919900000109")
		a, b = self._outbound(identity, "wamid.A"), self._outbound(identity, "wamid.B")
		webhook._process_webhook_payload(_payload("messages", {"metadata": {"phone_number_id": PHONE_ID}, "statuses": [
			{"id": "wamid.A", "status": "read", "pricing": {"category": "marketing_lite", "type": "regular", "pricing_model": "PMP"}},
			{"id": "wamid.B", "status": "delivered", "pricing": {"category": "utility", "type": "free_customer_service"}},
		]}))
		self.assertEqual(frappe.db.get_value("Excom Message", a, ["delivery_status", "wa_pricing_category", "wa_pricing_type"]), ("Read", "marketing_lite", "regular"))
		self.assertEqual(frappe.db.get_value("Excom Message", b, "delivery_status"), "Delivered")
		webhook._process_webhook_payload(_payload("messages", {"metadata": {"phone_number_id": PHONE_ID}, "statuses": [{"id": "wamid.A", "status": "delivered"}]}))
		self.assertEqual(frappe.db.get_value("Excom Message", a, "delivery_status"), "Read")

	def test_131050_failure_marks_marketing_opt_out_and_ledger(self):
		identity = self._identity("919900000110")
		msg = self._outbound(identity, "wamid.OPT")
		webhook._process_webhook_payload(_payload("messages", {"metadata": {"phone_number_id": PHONE_ID}, "statuses": [
			{"id": "wamid.OPT", "status": "failed", "errors": [{"code": 131050, "title": "User stopped marketing", "error_data": {"details": "x"}}]},
		]}))
		self.assertEqual(frappe.db.get_value("Excom Message", msg, ["delivery_status", "failure_code"]), ("Failed", 131050))
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_marketing_opt_out"), 1)
		self.assertTrue(frappe.db.exists("Excom Consent", {"omni_identity": identity, "action": "Withdrawn", "source": ["like", "meta_131050%"]}))

	# ── user_preferences ─────────────────────────────────────────────────────
	def test_user_preferences_stop_then_resume(self):
		identity = self._identity("919900000111")
		for value in ("stop", "resume"):
			webhook._process_webhook_payload(_payload("user_preferences", {
				"messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE_ID},
				"user_preferences": [{"wa_id": "919900000111", "user_id": "IN.PREF111", "category": "marketing_messages", "value": value, "detail": "x", "timestamp": 1}],
			}))
			self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_marketing_opt_out"), 1 if value == "stop" else 0)
		actions = frappe.get_all("Excom Consent", filters={"omni_identity": identity, "source": f"meta_user_preferences@{WABA}"}, pluck="action", order_by="creation asc")
		self.assertEqual(actions, ["Withdrawn", "Granted"])
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_bsuid"), "IN.PREF111")

	# ── business-scoped user ids ─────────────────────────────────────────────
	def test_username_only_sender_then_number_joins_same_identity(self):
		value = {"metadata": {"phone_number_id": PHONE_ID},
			"contacts": [{"user_id": "IN.USER222", "username": "qa_dealer", "profile": {"name": "QA Dealer"}}],
			"messages": [{"from_user_id": "IN.USER222", "id": "wamid.IN1", "timestamp": "1700000000", "type": "text", "text": {"body": "rates?"}}]}
		webhook._process_webhook_payload(_payload("messages", value))
		identity = frappe.db.get_value("Omni Identity", {"wa_bsuid": "IN.USER222"}, ["name", "wa_username", "primary_phone"], as_dict=True)
		self.assertTrue(identity)
		self.assertEqual(identity.wa_username, "qa_dealer")
		self.assertFalse(identity.primary_phone)
		value["contacts"][0]["wa_id"] = "919900000222"
		value["messages"] = [{"from": "919900000222", "from_user_id": "IN.USER222", "id": "wamid.IN2", "timestamp": "1700000100", "type": "text", "text": {"body": "hi"}}]
		webhook._process_webhook_payload(_payload("messages", value))
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.IN2"}, "omni_identity"), identity.name)
		self.assertEqual(frappe.db.get_value("Omni Identity", identity.name, "normalized_phone"), "919900000222")

	def test_user_changed_number_relinks(self):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		identity = resolve_identity_internal(phone="919900000333", channel="whatsapp", channel_user_id="919900000333", wa_user_id="IN.OLD333")
		webhook._process_webhook_payload(_payload("messages", {"metadata": {"phone_number_id": PHONE_ID},
			"messages": [{"from": "919900000333", "from_user_id": "IN.OLD333", "id": "wamid.SYS", "timestamp": "1700000200", "type": "system",
				"system": {"type": "user_changed_number", "body": "changed", "wa_id": "919900000444", "user_id": "IN.NEW444", "previous_user_id": "IN.OLD333"}}]}))
		doc = frappe.get_doc("Omni Identity", identity)
		self.assertEqual(doc.normalized_phone, "919900000444")
		self.assertEqual(doc.wa_bsuid, "IN.NEW444")
		self.assertIn("IN.OLD333", {a.alias_value_normalized for a in doc.aliases})
		# the old number is history only: if it is reissued, its new owner does not land here
		self.assertTrue(any(a.alias_type == "Other" and "919900000333" in a.alias_value_raw for a in doc.aliases))
		self.assertFalse(any(a.alias_type in ("Phone", "WhatsApp") and a.alias_value_normalized == "919900000333" for a in doc.aliases))
		self.assertFalse(frappe.db.exists("Excom Message", {"provider_message_id": "wamid.SYS"}))

	# ── account / template webhooks ──────────────────────────────────────────
	def test_template_category_update_changes_routing(self):
		webhook._process_webhook_payload(_payload("template_category_update", {
			"message_template_id": "771", "message_template_name": "qa_p0_utl", "message_template_language": "en",
			"previous_category": "UTILITY", "new_category": "MARKETING"}))
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", self.templates["utl"], "category"), "MARKETING")
		self.assertEqual(ws.template_category("qa_p0_utl", "en"), "MARKETING")
		self.assertTrue(frappe.db.exists("Excom WhatsApp Event", {"field": "template_category_update"}))

	def test_quality_and_capability_events_update_the_account(self):
		webhook._process_webhook_payload(_payload("phone_number_quality_update", {"display_phone_number": "x", "event": "DOWNGRADE", "current_limit": "TIER_2K", "phone_number_id": PHONE_ID}))
		self.assertEqual(frappe.db.get_value("Excom Channel Account", self.acc.name, "wa_messaging_limit"), "TIER_2K")
		self.assertEqual(frappe.db.get_value("Excom WhatsApp Event", {"field": "phone_number_quality_update"}, "severity"), "Warning")
		webhook._process_webhook_payload(_payload("account_update", {"event": "MM_LITE_TERMS_SIGNED"}))
		self.assertEqual(frappe.db.get_value("Excom Channel Account", self.acc.name, "wa_marketing_terms_signed"), 1)

	# ── consent ledger ───────────────────────────────────────────────────────
	def test_consent_ledger_is_append_only(self):
		from excom.excom.services.consent import latest_consent, record_consent

		identity = self._identity("919900000555")
		name = record_consent(identity, "Granted", "whatsapp", "marketing", "manual", notice_version="v1")
		self.assertEqual(latest_consent(identity), "Granted")
		doc = frappe.get_doc("Excom Consent", name)
		doc.notes = "edited"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	# ── broadcasts ───────────────────────────────────────────────────────────
	def test_broadcast_pauses_on_template_pause_and_skips_opt_outs(self):
		from excom.excom.services import broadcast_service as bs

		lst = frappe.get_doc({"doctype": "Excom Subscriber List", "list_name": "QA P0 List"}).insert(ignore_permissions=True)
		people = [self._identity(f"9199000006{i:02d}") for i in range(3)]
		for p in people:
			frappe.get_doc({"doctype": "Excom Subscriber", "subscriber_list": lst.name, "omni_identity": p, "status": "Subscribed"}).insert(ignore_permissions=True)
		bc = frappe.get_doc({"doctype": "Excom Broadcast", "broadcast_name": "QA P0", "subscriber_list": lst.name, "channel": "WhatsApp"})
		bc.name = "QA-P0-BROADCAST"
		bc.db_insert()
		frappe.db.set_value("Excom Broadcast", bc.name, {"docstatus": 1, "status": "Queued"})
		outcomes = [wae.ExcomConsentError("opted out"), True, wae.WhatsAppAPIError("paused", code=132015)]
		orig = bs._send_whatsapp_to_subscriber

		def fake(broadcast, oi):
			out = outcomes.pop(0)
			if isinstance(out, Exception):
				raise out
			bs._log_delivery(broadcast.name, oi, "Sent")
			return out

		bs._send_whatsapp_to_subscriber = fake
		try:
			bs.execute_broadcast(bc.name)
		finally:
			bs._send_whatsapp_to_subscriber = orig
		self.assertEqual(frappe.db.get_value("Excom Broadcast", bc.name, ["status", "sent_count", "failed_count"]), ("Paused", 1, 1))
		self.assertTrue(frappe.db.exists("Excom Broadcast Log", {"broadcast": bc.name, "status": "Skipped"}))
		self.assertTrue(frappe.db.exists("Excom Consent", {"purpose": f"list:{lst.name}", "action": "Granted"}))

	# ── analytics ────────────────────────────────────────────────────────────
	def test_overview_no_longer_asks_for_conversation_analytics(self):
		from excom.excom.services import whatsapp_analytics as wa

		asked = []
		orig = wa._call_analytics_api

		def fake(waba_id, token, base_url, version, fields):
			asked.append(fields)
			if fields.startswith("pricing_analytics"):
				return {"pricing_analytics": {"data": [{"data_points": [{"volume": 5, "cost": 4.3, "pricing_category": "MARKETING"}]}]}}
			return {"analytics": {"data_points": []}}

		wa._call_analytics_api = fake
		try:
			out = wa.get_account_overview(self.acc.name, days=7)
		finally:
			wa._call_analytics_api = orig
		self.assertFalse(any(f.startswith("conversation_analytics") for f in asked))
		self.assertEqual(out["conversations"]["total"], 5)
		self.assertEqual(out["conversations"]["by_category"], {"MARKETING": 5})

	# ── review fixes ─────────────────────────────────────────────────────────
	def test_recycled_number_gets_a_new_identity(self):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		old = resolve_identity_internal(phone="919900000777", channel="whatsapp", channel_user_id="919900000777", wa_user_id="IN.OLD333", wa_waba=WABA)
		new = resolve_identity_internal(phone="919900000777", channel="whatsapp", channel_user_id="919900000777", wa_user_id="IN.USER222", wa_waba=WABA)
		self.assertNotEqual(old, new)
		self.assertEqual(frappe.db.get_value("Omni Identity", new, ["needs_review", "potential_duplicate_of"]), (1, old))
		self.assertEqual(frappe.db.get_value("Omni Identity", old, ["wa_bsuid", "primary_phone", "needs_review"]), ("IN.OLD333", "", 1))
		# another business portfolio's id for the same number is the same person
		again = resolve_identity_internal(phone="919900000777", channel="whatsapp", channel_user_id="919900000777", wa_user_id="IN.PREF111", wa_waba="OTHERWABA")
		self.assertIn(again, (old, new))

	def test_gate_checks_duplicates_and_merge_keeps_opt_outs(self):
		a = self._identity("919900000888")
		# the same number written the national way: a duplicate record the variant lookup still finds
		b = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA dup", "primary_phone": "09900000888", "status": "Active", "is_master": 1, "wa_marketing_opt_out": 1}).insert(ignore_permissions=True).name
		with self.assertRaises(wae.ExcomConsentError):
			ws.send_template_message(self.acc, "919900000888", "qa_p0_mkt", "en", identity=a)
		frappe.get_doc("Omni Identity", b).merge_into(frappe.get_doc("Omni Identity", a))
		self.assertEqual(frappe.db.get_value("Omni Identity", a, "wa_marketing_opt_out"), 1)
		self.assertTrue(frappe.db.exists("Excom Consent", {"omni_identity": a, "action": "Withdrawn", "source": f"merge:{b}"}))

	def test_unknown_category_is_gated_as_marketing(self):
		self._identity("919900000999", wa_marketing_opt_out=1)
		with self.assertRaises(wae.ExcomConsentError):
			ws.send_template_message(self.acc, "919900000999", "qa_not_in_excom", "en")

	def _inbound(self, phone, wamid, text=None, button=None):
		msg = {"from": phone, "id": wamid, "timestamp": "1700000300"}
		if button:
			msg.update({"type": "button", "button": button})
		else:
			msg.update({"type": "text", "text": {"body": text}})
		return _payload("messages", {"metadata": {"phone_number_id": PHONE_ID}, "contacts": [{"wa_id": phone, "profile": {"name": "QA"}}], "messages": [msg]})

	def test_stop_promotions_button_stops_marketing_only(self):
		identity = self._identity("919900000901")
		webhook._process_webhook_payload(self._inbound("919900000901", "wamid.BTN", button={"text": "Stop promotions", "payload": "Stop promotions"}))
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, ["wa_marketing_opt_out", "whatsapp_opt_out"]), (1, 0))

	def test_start_only_undoes_a_whatsapp_stop(self):
		from excom.excom.doctype.omni_identity.omni_identity import set_opt_out

		staff = self._identity("919900000902")
		set_opt_out(staff, channel="whatsapp", source="manual")
		webhook._process_webhook_payload(self._inbound("919900000902", "wamid.ST1", text="START"))
		self.assertEqual(frappe.db.get_value("Omni Identity", staff, "whatsapp_opt_out"), 1)
		own = self._identity("919900000903")
		webhook._process_webhook_payload(self._inbound("919900000903", "wamid.ST2", text="STOP"))
		self.assertEqual(frappe.db.get_value("Omni Identity", own, "whatsapp_opt_out"), 1)
		webhook._process_webhook_payload(self._inbound("919900000903", "wamid.ST3", text="START"))
		self.assertEqual(frappe.db.get_value("Omni Identity", own, "whatsapp_opt_out"), 0)

	def test_unsigned_payload_cannot_change_consent(self):
		identity = self._identity("919900000904")
		webhook._process_webhook_payload(_payload("user_preferences", {"metadata": {"phone_number_id": PHONE_ID},
			"user_preferences": [{"wa_id": "919900000904", "category": "marketing_messages", "value": "stop"}]}), signed=False)
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "wa_marketing_opt_out"), 0)

	def test_category_advance_notice_does_not_change_routing(self):
		webhook._process_webhook_payload(_payload("template_category_update", {
			"message_template_id": "771", "message_template_name": "qa_p0_utl", "new_category": "UTILITY",
			"correct_category": "MARKETING", "category_update_timestamp": 1}))
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", self.templates["utl"], "category"), "UTILITY")
		self.assertEqual(frappe.db.get_value("Excom WhatsApp Event", {"field": "template_category_update"}, "severity"), "Warning")

	def test_account_alert_reads_alert_info(self):
		webhook._process_webhook_payload(_payload("account_alerts", {"entity_type": "WABA", "entity_id": WABA,
			"alert_info": {"alert_severity": "CRITICAL", "alert_status": "ACTIVE", "alert_type": "OBA_APPROVED", "alert_description": "qa"}}))
		self.assertEqual(frappe.db.get_value("Excom WhatsApp Event", {"field": "account_alerts"}, ["event", "severity"]), ("OBA_APPROVED", "Critical"))

	def test_135000_does_not_pause_a_campaign(self):
		self.assertEqual(wae.classify(135000), wae.PERMANENT)
		self.assertNotIn(wae.classify(135000), wae.CAMPAIGN_STOPPERS)
