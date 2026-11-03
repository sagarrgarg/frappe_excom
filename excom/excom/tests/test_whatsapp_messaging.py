"""HLD-005 Phase 1 — messaging basics: interactive builders and their limits, sending into a
thread (24 h window), named templates / quick-reply payloads / Flow buttons, structured inbound
parsing (orders, taps, Flow answers, addresses, locations) and the inbound dispatcher.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_messaging'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import whatsapp_interactive as wi
from excom.excom.services import whatsapp_service as ws
from excom.excom.utils import webhook

PHONE_ID = "QAPHONEP1"
WABA = "QAWABAP1"
EVENTS: list = []


def record_event(event):  # an excom_inbound_handlers entry used by the tests
	EVENTS.append(event)


def broken_handler(event):
	raise RuntimeError("boom")


def committing_broken_handler(event):
	frappe.db.commit()  # loses the dispatcher's savepoint
	raise RuntimeError("boom after commit")


def unsigned_ok_handler(event):
	EVENTS.append({"unsigned_ok": True, **event})


unsigned_ok_handler.accepts_unsigned = True


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _payload(value):
	return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {"metadata": {"phone_number_id": PHONE_ID}, **value}}]}]})


def _cleanup(account):
	ids = frappe.db.sql("SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199001%%'", pluck=True)
	if ids:
		for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
			frappe.db.delete(child, {"parent": ["in", ids]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", ids]})
		frappe.db.delete("Omni Identity", {"name": ["in", ids]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	frappe.db.delete("Error Log", {"method": ["like", "Excom: inbound handler failed: excom.excom.tests.%"]})
	frappe.db.delete("WhatsApp Flow", "QA-P1-FLOW")
	frappe.cache.delete_value("excom:flow_session:qa-tok-p1")


class TestWhatsAppMessaging(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Messaging", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_token": "qa-token-0000000000"}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup(cls.acc.name)
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.calls = []
		self._orig = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (self.calls.append((url, kw.get("json"))) or FakeResp(200, {"messages": [{"id": f"wamid.OUT{len(self.calls)}"}]}))
		EVENTS.clear()

	def tearDown(self):
		ws.http_requests.post = self._orig
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	def _thread(self, phone, inbound=True):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
		from excom.excom.services.thread_service import upsert_thread

		identity = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone, display_name="QA")
		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		if inbound:
			frappe.db.set_value("Excom Thread", thread, "last_inbound_at", frappe.utils.now_datetime())
		return identity, thread

	# ── builders and limits ──────────────────────────────────────────────────
	def test_reply_buttons_limits(self):
		ok = wi.reply_buttons("Rates updated", [{"id": "RU:1:view", "title": "View & Order"}, {"id": "RU:1:cat", "title": "Full Catalogue"}])
		self.assertEqual(ok["type"], "button")
		self.assertEqual(ok["action"]["buttons"][0]["reply"], {"id": "RU:1:view", "title": "View & Order"})
		for bad in ([{"id": str(i), "title": f"B{i}"} for i in range(4)], [{"id": "a", "title": "x" * 21}], [{"id": "a", "title": "Same"}, {"id": "b", "title": "Same"}]):
			with self.assertRaises(wi.InteractiveLimitError):
				wi.reply_buttons("body", bad)

	def test_list_rows_capped_at_ten_in_total(self):
		rows = [{"id": f"r{i}", "title": f"Row {i}"} for i in range(6)]
		wi.list_message("Pick", "Choose", [{"title": "A", "rows": rows[:5]}, {"title": "B", "rows": rows[5:]}])
		with self.assertRaises(wi.InteractiveLimitError):
			wi.list_message("Pick", "Choose", [{"title": "A", "rows": rows}, {"title": "B", "rows": rows}])

	def test_commerce_and_flow_builders(self):
		pl = wi.product_list("CAT1", "New rates", "Today's prices", [{"title": "Water guns", "retailer_ids": ["FM097", "FM099"]}])
		self.assertEqual(pl["action"]["sections"][0]["product_items"], [{"product_retailer_id": "FM097"}, {"product_retailer_id": "FM099"}])
		with self.assertRaises(wi.InteractiveLimitError):
			wi.product_carousel("CAT1", "x", ["only-one"])
		car = wi.product_carousel("CAT1", "Changed today", ["FM097", "FM099", "FM100"])
		self.assertEqual([c["card_index"] for c in car["action"]["cards"]], [0, 1, 2])
		fl = wi.flow("FLOW1", "Ask best price", "Tell us what you need", "RR:tok", screen="REQUEST", data={"items": [{"id": "FM097", "title": "Pulse"}]}, draft=True)
		params = fl["action"]["parameters"]
		self.assertEqual(params["flow_action_payload"], {"screen": "REQUEST", "data": {"items": [{"id": "FM097", "title": "Pulse"}]}})
		self.assertEqual((params["flow_message_version"], params["mode"]), ("3", "draft"))
		with self.assertRaises(wi.InteractiveLimitError):
			wi.cta_url("x", "Open", "ftp://nope")

	# ── sending ──────────────────────────────────────────────────────────────
	def test_send_shapes_payload_with_context(self):
		wi.send(self.acc, "+919900100001", wi.catalog_message("See all products", "FM097"), reply_to="wamid.IN")
		url, body = self.calls[0]
		self.assertTrue(url.endswith(f"/{PHONE_ID}/messages"))
		self.assertEqual((body["type"], body["to"], body["context"]), ("interactive", "919900100001", {"message_id": "wamid.IN"}))
		self.assertEqual(body["interactive"]["action"]["parameters"], {"thumbnail_product_retailer_id": "FM097"})

	def test_address_form_only_for_india(self):
		with self.assertRaises(wi.InteractiveLimitError):
			wi.send(self.acc, "+14155550100", wi.address_request("Where should we deliver?"))
		wi.send(self.acc, "+919900100002", wi.address_request("Where should we deliver?"))
		self.assertEqual(self.calls[0][1]["interactive"]["action"]["parameters"], {"country": "IN"})

	def test_send_to_thread_records_and_respects_window(self):
		identity, thread = self._thread("919900100003", inbound=False)
		with self.assertRaises(wi.InteractiveLimitError):
			wi.send_to_thread(thread, wi.reply_buttons("x", [{"id": "a", "title": "A"}]))
		frappe.db.set_value("Excom Thread", thread, "last_inbound_at", frappe.utils.now_datetime())
		name = wi.send_to_thread(thread, wi.reply_buttons("Accept the offer?", [{"id": "RR:1:accept", "title": "Accept"}, {"id": "RR:1:decline", "title": "Decline"}]))
		msg = frappe.db.get_value("Excom Message", name, ["message_type", "content_text", "direction", "provider_message_id"], as_dict=True)
		self.assertEqual((msg.message_type, msg.direction), ("Interactive", "Outbound"))
		self.assertIn("[Accept]", msg.content_text)
		self.assertTrue(msg.provider_message_id.startswith("wamid.OUT"))

	def test_stop_blocks_interactive_too(self):
		identity, thread = self._thread("919900100004")
		frappe.db.set_value("Omni Identity", identity, "whatsapp_opt_out", 1)
		from excom.excom.services.whatsapp_errors import ExcomConsentError

		with self.assertRaises(ExcomConsentError):
			wi.send_to_thread(thread, wi.catalog_message("x"))

	def test_typing_indicator_marks_read(self):
		wi.mark_read(self.acc, "wamid.IN9", typing=True)
		self.assertEqual(self.calls[0][1], {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.IN9", "typing_indicator": {"type": "text"}})

	# ── templates ────────────────────────────────────────────────────────────
	def _template(self, body, buttons=(), header=None):
		doc = frappe.new_doc("WhatsApp Templates")
		doc.update({"template_name": "qa p1", "template": body, "language_code": "en", "category": "MARKETING"})
		if header:
			doc.update({"header_type": "TEXT", "header": header})
		for b in buttons:
			doc.append("buttons", b)
		return doc

	def test_named_parameters_and_payloads_at_send(self):
		from excom.excom.api.chat import _build_template_components

		doc = self._template("Rates updated {{date}}: {{summary}}", buttons=[
			{"button_type": "Quick Reply", "button_label": "View & Order"},
			{"button_type": "Quick Reply", "button_label": "Full Catalogue"},
			{"button_type": "Quick Reply", "button_label": "Ask Best Price"},
		])
		comps = _build_template_components(doc, {"date": "3 Nov", "summary": "Pulse ₹30"}, button_payloads=["RU:9:view", "RU:9:cat", "RU:9:ask"])
		body = next(c for c in comps if c["type"] == "body")
		self.assertEqual(body["parameters"], [{"type": "text", "text": "3 Nov", "parameter_name": "date"}, {"type": "text", "text": "Pulse ₹30", "parameter_name": "summary"}])
		qr = [c for c in comps if c.get("sub_type") == "quick_reply"]
		self.assertEqual([(c["index"], c["parameters"][0]["payload"]) for c in qr], [("0", "RU:9:view"), ("1", "RU:9:cat"), ("2", "RU:9:ask")])

	def test_positional_templates_unchanged(self):
		from excom.excom.api.chat import _build_template_components

		comps = _build_template_components(self._template("Hello {{1}}"), ["Asha"])
		self.assertEqual(comps, [{"type": "body", "parameters": [{"type": "text", "text": "Asha"}]}])

	def test_template_creation_uses_named_format_and_flow_buttons(self):
		flow = frappe.get_doc({"doctype": "WhatsApp Flow", "flow_name": "QA P1 Flow", "flow_id": "FLOW-QA-1", "whatsapp_account": self.acc.name, "status": "Published"})
		flow.name = "QA-P1-FLOW"
		flow.db_insert()
		doc = self._template("Rates {{date}}", buttons=[{"button_type": "Flow", "button_label": "Ask Best Price", "flow": "QA-P1-FLOW", "navigate_screen": "REQUEST"}])
		doc.body_variable_samples = json.dumps(["3 Nov"])
		self.assertTrue(doc.uses_named_parameters())
		comps = doc.meta_components()
		self.assertEqual(comps[0]["example"], {"body_text_named_params": [{"param_name": "date", "example": "3 Nov"}]})
		self.assertEqual(comps[-1]["buttons"][0], {"type": "FLOW", "text": "Ask Best Price", "flow_id": "FLOW-QA-1", "flow_action": "navigate", "navigate_screen": "REQUEST"})
		doc.buttons[0].navigate_screen = ""
		with self.assertRaises(frappe.ValidationError):
			doc.meta_components()  # Meta requires the first screen for navigate

	def test_split_quick_replies_are_rejected(self):
		doc = self._template("Hi", buttons=[
			{"button_type": "Quick Reply", "button_label": "A"},
			{"button_type": "Visit Website", "button_label": "Site", "website_url": "https://example.com", "url_type": "Static"},
			{"button_type": "Quick Reply", "button_label": "B"},
		])
		with self.assertRaises(frappe.ValidationError):
			doc.validate_button_count()

	# ── inbound parsing + dispatch ───────────────────────────────────────────
	def _hooks(self, *paths):
		from excom.excom.services import inbound_dispatch

		orig = inbound_dispatch.frappe.get_hooks
		inbound_dispatch.frappe.get_hooks = lambda name, *a, **k: list(paths) if name == "excom_inbound_handlers" else orig(name, *a, **k)
		self.addCleanup(setattr, inbound_dispatch.frappe, "get_hooks", orig)

	def _in(self, wamid, phone, **msg):
		return _payload({"contacts": [{"wa_id": phone, "profile": {"name": "QA Dealer"}}],
			"messages": [{"from": phone, "id": wamid, "timestamp": "1700000500", **msg}]})

	def test_cart_order_is_parsed_and_dispatched(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.broken_handler", "excom.excom.tests.test_whatsapp_messaging.record_event")
		order = {"catalog_id": "CAT1", "text": "urgent", "product_items": [
			{"product_retailer_id": "FM097", "quantity": 500, "item_price": 30, "currency": "INR"},
			{"product_retailer_id": "FM099", "quantity": 200, "item_price": 33, "currency": "INR"}]}
		webhook._process_webhook_payload(self._in("wamid.ORD", "919900100010", type="order", order=order))
		msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.ORD"}, ["message_type", "content_text"], as_dict=True)
		self.assertEqual(msg.message_type, "Order")
		self.assertIn("2 item(s)", msg.content_text)
		self.assertIn("21,600.00", msg.content_text)
		self.assertEqual(len(EVENTS), 1)  # the broken handler did not stop the next one
		self.assertEqual((EVENTS[0]["kind"], EVENTS[0]["payload"]["catalog_id"], EVENTS[0]["waba"]), ("order", "CAT1", WABA))

	def test_button_list_and_template_payloads_are_ids_not_titles(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.record_event")
		webhook._process_webhook_payload(self._in("wamid.B1", "919900100011", type="interactive", interactive={"type": "button_reply", "button_reply": {"id": "RR:7:accept", "title": "Accept"}}))
		webhook._process_webhook_payload(self._in("wamid.B2", "919900100011", type="interactive", interactive={"type": "list_reply", "list_reply": {"id": "ITEM:FM097", "title": "Pulse", "description": "₹30"}}))
		webhook._process_webhook_payload(self._in("wamid.B3", "919900100011", type="button", button={"text": "View & Order", "payload": "RU:9:view"}))
		self.assertEqual([(e["kind"], e["payload"]) for e in EVENTS], [("button", "RR:7:accept"), ("list", "ITEM:FM097"), ("button", "RU:9:view")])
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.B2"}, "content_text"), "Pulse")

	def test_flow_answer_and_address_and_location(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.record_event")
		answer = {"flow_token": "RR:tok1", "item": "FM097", "qty": "500"}
		webhook._process_webhook_payload(self._in("wamid.F1", "919900100012", type="interactive", interactive={"type": "nfm_reply", "nfm_reply": {"name": "flow", "body": "Sent", "response_json": json.dumps(answer)}}))
		addr = {"values": {"name": "QA", "address": "Plot 7, Sector 24", "city": "Faridabad", "state": "Haryana", "in_pin_code": "121005"}}
		webhook._process_webhook_payload(self._in("wamid.F2", "919900100012", type="interactive", interactive={"type": "nfm_reply", "nfm_reply": {"name": "address_message", "response_json": json.dumps(addr)}}))
		webhook._process_webhook_payload(self._in("wamid.F3", "919900100012", type="location", location={"latitude": 28.4, "longitude": 77.3, "name": "Shop"}))
		self.assertEqual([e["kind"] for e in EVENTS], ["flow", "address", "location"])
		self.assertEqual(EVENTS[0]["payload"]["flow_token"], "RR:tok1")
		self.assertNotIn("flow_token", frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.F1"}, "content_text"))
		self.assertIn("121005", frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.F2"}, "content_text"))
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.F3"}, "content_text"), "📍 Shop (28.4, 77.3)")

	def test_flow_endpoint_session_cache(self):
		from excom.excom.api import flow_endpoint as fe

		fe.save_flow_data("qa-tok-p1", "S1", {"item": "FM097"})
		fe.save_flow_data("qa-tok-p1", "S2", {"qty": 5})
		self.assertEqual(fe.get_flow_session("qa-tok-p1"), {"item": "FM097", "qty": 5, "_last_screen": "S2"})
		self.assertFalse(frappe.db.exists("Error Log", {"method": "WhatsApp Flow Data"}))

	# ── review fixes ─────────────────────────────────────────────────────────
	def test_meta_button_limits(self):
		two_phones = self._template("Hi", buttons=[
			{"button_type": "Call Phone", "button_label": "Call", "phone_number": "+911234567890"},
			{"button_type": "Call Phone", "button_label": "Call 2", "phone_number": "+911234567891"}])
		with self.assertRaises(frappe.ValidationError):
			two_phones.validate_button_count()
		mixed = self._template("Hi", buttons=[
			{"button_type": "Quick Reply", "button_label": f"Q{i}"} for i in range(4)] + [
			{"button_type": "Visit Website", "button_label": "Site", "website_url": "https://example.com", "url_type": "Static"},
			{"button_type": "Visit Website", "button_label": "Site 2", "website_url": "https://example.org", "url_type": "Static"},
			{"button_type": "Call Phone", "button_label": "Call", "phone_number": "+911234567890"}])
		mixed.validate_button_count()  # 4 quick replies + 2 links + 1 call is valid on Meta

	def test_dict_variables_preview_and_missing_names(self):
		from excom.excom.api.chat import _build_template_components, _build_template_preview

		doc = self._template("Rates {{date}}: {{summary}}")
		self.assertEqual(_build_template_preview(doc, {"date": "3 Nov", "summary": "Pulse ₹30"}), "Rates 3 Nov: Pulse ₹30")
		with self.assertRaises(frappe.ValidationError):
			_build_template_components(doc, {"date": "3 Nov"})

	def test_single_list_section_gets_a_title(self):
		lst = wi.list_message("Pick", "Choose item", [{"rows": [{"id": "a", "title": "A"}]}])
		self.assertEqual(lst["action"]["sections"][0]["title"], "Choose item")

	def test_product_tap_and_second_address_shape(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.record_event")
		webhook._process_webhook_payload(self._in("wamid.P1", "919900100013", type="interactive", interactive={"type": "product",
			"product": {"retailer_id": "FM097", "title": "Pulse", "currency_code": "INR", "price_amount": 30000}}))
		webhook._process_webhook_payload(self._in("wamid.P2", "919900100013", type="interactive", interactive={"type": "interactive", "action": "address_message",
			"nfm_reply": {"response_json": json.dumps({"values": {"name": "QA", "tower_number": "T2", "city": "Faridabad", "in_pin_code": "121005"}})}}))
		self.assertEqual([e["kind"] for e in EVENTS], ["product", "address"])
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P1"}, "content_text"), "🛍 Pulse · INR 30.00")
		self.assertIn("T2", frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P2"}, "content_text"))

	def test_unsigned_events_reach_only_opted_in_handlers(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.record_event", "excom.excom.tests.test_whatsapp_messaging.unsigned_ok_handler")
		webhook._process_webhook_payload(self._in("wamid.U1", "919900100014", type="interactive", interactive={"type": "button_reply", "button_reply": {"id": "RR:9:accept", "title": "Accept"}}), signed=False)
		self.assertEqual(len(EVENTS), 1)
		self.assertTrue(EVENTS[0]["unsigned_ok"])

	def test_handler_that_commits_then_fails_does_not_break_dispatch(self):
		self._hooks("excom.excom.tests.test_whatsapp_messaging.committing_broken_handler", "excom.excom.tests.test_whatsapp_messaging.record_event")
		webhook._process_webhook_payload(self._in("wamid.C1", "919900100015", type="text", text={"body": "hello"}))
		self.assertEqual([e["kind"] for e in EVENTS], ["text"])
		self.assertTrue(frappe.db.exists("Excom Message", {"provider_message_id": "wamid.C1"}))
