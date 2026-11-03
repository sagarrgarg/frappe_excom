"""HLD-005 Phase 3 — Ask Best Price: signed request form, Rate Request, offer commands from Raven,
discount guardrail and approval, offer to the dealer (window / template), Accept → draft Sales
Order at the offered rate, decline / talk, take-over, expiry and reminders. Raven is stubbed.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_negotiation'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.services import whatsapp_service as ws
from excom.excom.services.commerce import offers, raven_bridge, requests
from excom.excom.utils import webhook

PHONE_ID = "QAPHONEP3"
WABA = "QAWABAP3"
PL = "QA P3 Dealer"
ITEM = "QA-P3-GUN"
PHONE = "919900300001"
SALES = SALES
SAID = []


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _leaf(doctype):
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")


def _cleanup(account):
	ids = frappe.db.sql("SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199003%%'", pluck=True)
	if ids:
		for child in ("Omni Identity Alias", "Omni Identity Channel"):
			frappe.db.delete(child, {"parent": ["in", ids]})
		frappe.db.delete("Omni Identity Link", {"parent": ["in", ids], "linked_doctype": ["!=", "Customer"]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", ids]})
	for rr in frappe.get_all("Excom Rate Request", filters={"item_code": ITEM}, pluck="name"):
		frappe.db.delete("Excom Rate Offer", {"parent": rr})
		frappe.db.delete("Excom Rate Request", rr)
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	for so in frappe.get_all("Sales Order", filters={"customer": "QA P3 Customer", "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Sales Order", so, force=True, ignore_permissions=True)


class TestWhatsAppNegotiation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		if not frappe.db.exists("Price List", PL):
			frappe.get_doc({"doctype": "Price List", "price_list_name": PL, "currency": "INR", "selling": 1, "enabled": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("Item", ITEM):
			frappe.get_doc({"doctype": "Item", "item_code": ITEM, "item_name": "QA Big Water Gun", "item_group": _leaf("Item Group"),
				"stock_uom": frappe.db.get_value("UOM", "Nos") or frappe.db.get_value("UOM", {}, "name"), "is_stock_item": 0, "is_sales_item": 1}).insert(ignore_permissions=True)
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.get_doc({"doctype": "Item Price", "item_code": ITEM, "price_list": PL, "price_list_rate": 30,
			"uom": frappe.db.get_value("Item", ITEM, "stock_uom")}).insert(ignore_permissions=True)
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Negotiation", "channel": "whatsapp", "status": "Active",
			"wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_token": "qa-token-0000000000", "catalog_price_list": PL,
			"company": frappe.defaults.get_global_default("company")}).insert(ignore_permissions=True)
		if not frappe.db.exists("Customer", "QA P3 Customer"):
			frappe.get_doc({"doctype": "Customer", "customer_name": "QA P3 Customer", "customer_group": _leaf("Customer Group"),
				"territory": _leaf("Territory"), "default_price_list": PL}).insert(ignore_permissions=True)
		if not frappe.db.exists("Role", "Excom Agent"):
			frappe.get_doc({"doctype": "Role", "role_name": "Excom Agent", "desk_access": 1}).insert(ignore_permissions=True)
		if not frappe.db.exists("User", SALES):  # Link fields on the request are validated on save
			u = frappe.get_doc({"doctype": "User", "email": SALES, "first_name": "QA P3 Sales", "enabled": 1, "user_type": "System User", "send_welcome_email": 0})
			u.flags.no_welcome_mail = True
			u.insert(ignore_permissions=True)
		u = frappe.get_doc("User", SALES)
		u.set("roles", [])
		u.add_roles("Excom Agent")  # sales, but not an approver
		cls._settings = frappe.get_single("Excom Commerce Settings").as_dict(no_default_fields=True)
		s = frappe.get_single("Excom Commerce Settings")
		s.update({"max_discount_pct_without_approval": 5, "approver_role": "Sales Manager", "offer_validity_hours": 24, "reply_reminder_min": 30, "offer_template": None,
			"raven_channel": "qa-p3-channel"})
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup(cls.acc.name)
		s = frappe.get_single("Excom Commerce Settings")
		for k, v in cls._settings.items():
			if k not in ("name", "doctype") and s.meta.has_field(k):
				s.set(k, v)
		s.flags.ignore_mandatory = True
		s.save(ignore_permissions=True)
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.calls = []
		self._orig = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (self.calls.append((url, kw.get("json"))) or FakeResp(200, {"messages": [{"id": f"wamid.P3OUT{len(self.calls)}"}]}))
		SAID.clear()
		self._say, self._post = raven_bridge.say, raven_bridge.post_request
		raven_bridge.say = lambda request, html: SAID.append((request, html)) or "rv"
		raven_bridge.post_request = lambda request: SAID.append((request, "posted"))
		frappe.clear_document_cache("Excom Commerce Settings", "Excom Commerce Settings")
		self.identity = self._dealer()

	def tearDown(self):
		ws.http_requests.post = self._orig
		raven_bridge.say, raven_bridge.post_request = self._say, self._post
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _dealer(self):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		linked = frappe.db.get_value("Omni Identity Link", {"linked_doctype": "Customer", "linked_name": "QA P3 Customer"}, "parent")
		if linked:
			frappe.db.set_value("Omni Identity", linked, {"primary_phone": PHONE, "primary_whatsapp": PHONE, "normalized_phone": PHONE,
				"whatsapp_opt_out": 0, "do_not_contact": 0, "wa_marketing_opt_out": 0})
			identity = linked
		else:
			identity = resolve_identity_internal(phone=PHONE, channel="whatsapp", channel_user_id=PHONE, display_name="QA Dealer")
			doc = frappe.get_doc("Omni Identity", identity)
			doc.add_link("Customer", "QA P3 Customer", "Unknown")
			doc.save(ignore_permissions=True)
		frappe.db.commit()
		return identity

	def _thread(self, open_window=True):
		from excom.excom.services.thread_service import upsert_thread

		thread = upsert_thread(self.identity, "whatsapp", self.acc.name)
		frappe.db.set_value("Excom Thread", thread, "last_inbound_at", now_datetime() if open_window else add_to_date(now_datetime(), days=-2))
		return thread

	def _answer(self, token, wamid="wamid.P3F1", **fields):
		answer = {"flow_token": token, "item": ITEM, "qty": "500", **fields}
		webhook._process_webhook_payload(json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
			"metadata": {"phone_number_id": PHONE_ID}, "contacts": [{"wa_id": PHONE, "profile": {"name": "QA Dealer"}}],
			"messages": [{"from": PHONE, "id": wamid, "timestamp": "1700000800", "type": "interactive",
				"interactive": {"type": "nfm_reply", "nfm_reply": {"name": "flow", "response_json": json.dumps(answer)}}}]}}]}]}))
		return frappe.db.get_value("Excom Rate Request", {"flow_token": token}, "name")

	def _request(self, **extra):
		self._thread()
		return self._answer(requests.make_token(self.identity), **extra)

	def _tap(self, payload, wamid):
		webhook._process_webhook_payload(json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
			"metadata": {"phone_number_id": PHONE_ID}, "contacts": [{"wa_id": PHONE, "profile": {"name": "QA Dealer"}}],
			"messages": [{"from": PHONE, "id": wamid, "timestamp": "1700000900", "type": "interactive",
				"interactive": {"type": "button_reply", "button_reply": {"id": payload, "title": "x"}}}]}}]}]}))

	# ── parsing and tokens ───────────────────────────────────────────────────
	def test_parse_offer(self):
		self.assertEqual(offers.parse_offer("offer 4150 for 500"), ([(4150, 500)], None))
		self.assertEqual(offers.parse_offer("Offer 4100 for 1000, 4150 for 500 valid 2d"), ([(4150, 500), (4100, 1000)], 48))
		self.assertEqual(offers.parse_offer("offer 27@200 valid 6h"), ([(27, 200)], 6))
		self.assertIsNone(offers.parse_offer("offer something"))
		self.assertIsNone(offers.parse_offer("hello"))
		self.assertEqual(offers.parse_offer("offer 4,150 for 500, 4,100 for 1,000"), ([(4150, 500), (4100, 1000)], None))  # not ₹150
		self.assertEqual(offers.parse_offer("offer 10 for 500 confirm"), ([(10, 500)], None))

	def test_token_is_signed(self):
		token = requests.make_token(self.identity, "RU-00001")
		self.assertEqual(requests.read_token(token), {"identity": self.identity, "rate_update": "RU-00001"})
		forged = token.replace(self.identity, "OTHER-IDENTITY")
		self.assertIsNone(requests.read_token(forged))

	# ── the request ──────────────────────────────────────────────────────────
	def test_flow_answer_creates_request_and_acks(self):
		name = self._request(target_rate="27", note="urgent")
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual((req.status, req.customer, req.qty, req.list_rate, req.target_rate, req.price_list), ("Open", "QA P3 Customer", 500, 30, 27, PL))
		self.assertIn((name, "posted"), SAID)
		self.assertTrue(any(b and b.get("type") == "text" and name in b["text"]["body"] for _u, b in self.calls))

	def test_token_of_another_contact_and_replays_are_ignored(self):
		self._thread()
		other = requests.make_token("SOMEONE-ELSE")
		self.assertIsNone(self._answer(other))
		token = requests.make_token(self.identity)
		first = self._answer(token, wamid="wamid.P3F2")
		self.assertTrue(first)
		self.assertEqual(frappe.db.count("Excom Rate Request", {"flow_token": token}), 1)

	# ── offers ───────────────────────────────────────────────────────────────
	def test_deep_discount_needs_approval_then_goes_out(self):
		name = self._request()
		offers.on_command(name, "offer 27 for 500", SALES)  # 10% below list, not an approver
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Pending Approval")
		self.calls.clear()
		offers.on_command(name, "approve", SALES)  # still not an approver
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Pending Approval")
		offers.on_command(name, "approve", "Administrator")
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual((req.status, req.approved_by, req.claimed_by), ("Offered", "Administrator", SALES))
		sent = [b for _u, b in self.calls if b and b.get("type") == "interactive"]
		self.assertEqual([x["reply"]["id"] for x in sent[0]["interactive"]["action"]["buttons"]], [f"RR:{name}:accept:1", f"RR:{name}:decline:1", f"RR:{name}:talk:1"])

	def test_small_discount_goes_straight_out(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", SALES)
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Offered")

	def test_closed_window_without_template_is_reported_in_raven(self):
		name = self._request()
		frappe.db.set_value("Excom Thread", frappe.db.get_value("Excom Rate Request", name, "excom_thread"), "last_inbound_at", add_to_date(now_datetime(), days=-2))
		offers.on_command(name, "offer 29 for 500", SALES)
		self.assertNotEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Offered")
		self.assertTrue(any("Could not send" in html for _r, html in SAID))

	def test_accept_creates_sales_order_at_the_offer_rate(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", SALES)
		self._tap(f"RR:{name}:accept:1", "wamid.P3ACC")
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual(req.status, "Accepted")
		so = frappe.get_doc("Sales Order", req.sales_order)
		self.assertEqual((so.docstatus, so.items[0].item_code, so.items[0].qty, so.items[0].rate), (0, ITEM, 500, 29))
		self._tap(f"RR:{name}:accept:1", "wamid.P3ACC2")
		self.assertEqual(frappe.db.count("Sales Order", {"customer": "QA P3 Customer", "docstatus": 0}), 1)  # once only

	def test_expired_offer_and_foreign_taps(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", SALES)
		frappe.db.set_value("Excom Rate Request", name, "valid_till", add_to_date(now_datetime(), hours=-1))
		frappe.db.set_value("Excom Rate Request", name, "omni_identity", "SOMEONE-ELSE")
		self._tap(f"RR:{name}:accept:1", "wamid.P3X1")  # not this contact's offer
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Offered")
		frappe.db.set_value("Excom Rate Request", name, "omni_identity", self.identity)
		self._tap(f"RR:{name}:accept:1", "wamid.P3X2")
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Expired")

	def test_best_slab(self):
		req = frappe._dict(qty=700, offers=[frappe._dict(rate=29, qty=500), frappe._dict(rate=28, qty=1000)])
		self.assertEqual(offers.best_slab(req), (29, 700))
		req.qty = 1200
		self.assertEqual(offers.best_slab(req), (28, 1200))
		req.qty = 100
		self.assertEqual(offers.best_slab(req), (29, 500))

	def test_chat_takes_the_thread_over(self):
		name = self._request()
		offers.on_command(name, "chat", "Administrator")
		req = frappe.db.get_value("Excom Rate Request", name, ["status", "claimed_by", "excom_thread"], as_dict=True)
		self.assertEqual((req.status, req.claimed_by), ("Handled in Chat", "Administrator"))
		self.assertEqual(frappe.db.get_value("Excom Thread", req.excom_thread, "assigned_to"), "Administrator")

	def test_scheduler_expires_and_reminds(self):
		a = self._request()
		offers.on_command(a, "offer 29 for 500", "Administrator")
		frappe.db.set_value("Excom Rate Request", a, "valid_till", add_to_date(now_datetime(), hours=-1))
		b = self._answer(requests.make_token(self.identity), wamid="wamid.P3F9")
		frappe.db.set_value("Excom Rate Request", b, "creation", add_to_date(now_datetime(), minutes=-45))
		offers.run_scheduler()
		self.assertEqual(frappe.db.get_value("Excom Rate Request", a, "status"), "Expired")
		self.assertEqual(frappe.db.get_value("Excom Rate Request", b, "reminder_sent"), 1)

	# ── guardrail edge cases (review H2–H4) ──────────────────────────────────
	def _limit(self, value):
		frappe.db.set_single_value("Excom Commerce Settings", "max_discount_pct_without_approval", value)
		frappe.clear_document_cache("Excom Commerce Settings", "Excom Commerce Settings")

	def test_no_list_rate_or_zero_limit_always_needs_approval(self):
		name = self._request()
		frappe.db.set_value("Excom Rate Request", name, "list_rate", 0)
		offers.on_command(name, "offer 29 for 500", SALES)
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Pending Approval")
		other = self._answer(requests.make_token(self.identity), wamid="wamid.P3F7")
		self._limit(0)
		try:
			offers.on_command(other, "offer 29.9 for 500", SALES)
			self.assertEqual(frappe.db.get_value("Excom Rate Request", other, "status"), "Pending Approval")
		finally:
			self._limit(5)

	def test_very_deep_offer_needs_confirm(self):
		name = self._request()
		offers.on_command(name, "offer 10 for 500", SALES)  # 67 % below list: a typo until confirmed
		self.assertFalse(frappe.db.get_value("Excom Rate Request", name, "pending_offer"))
		self.assertTrue(any("confirm" in html for _r, html in SAID))
		offers.on_command(name, "offer 10 for 500 confirm", SALES)
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Pending Approval")

	def test_old_accept_button_cannot_accept_a_newer_offer(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", "Administrator")
		offers.on_command(name, "offer 29.5 for 500", "Administrator")
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "offer_version"), 2)
		self._tap(f"RR:{name}:accept:1", "wamid.P3V1")
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Offered")
		self._tap(f"RR:{name}:accept:2", "wamid.P3V2")
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual(req.status, "Accepted")
		self.assertEqual(frappe.db.get_value("Sales Order Item", {"parent": req.sales_order}, "rate"), 29.5)

	def test_failed_send_keeps_the_live_offer(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", SALES)
		ws.http_requests.post = lambda url, **kw: FakeResp(400, {"error": {"code": 100, "message": "bad"}})
		offers.on_command(name, "offer 28.8 for 500", SALES)
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual((req.status, req.offer_version, [o.rate for o in req.offers]), ("Offered", 1, [29]))
		self.assertFalse(req.pending_offer)

	def test_reject_keeps_the_live_offer_and_pending_accept_still_counts(self):
		name = self._request()
		offers.on_command(name, "offer 29 for 500", SALES)  # live, version 1
		offers.on_command(name, "offer 27 for 500", SALES)  # deeper: waits for approval
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Pending Approval")
		offers.on_command(name, "reject", "Administrator")
		req = frappe.get_doc("Excom Rate Request", name)
		self.assertEqual((req.status, [o.rate for o in req.offers]), ("Offered", [29]))
		offers.on_command(name, "offer 27 for 500", SALES)
		self._tap(f"RR:{name}:accept:1", "wamid.P3R1")  # the dealer accepts what they were shown
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Accepted")

	def test_only_sales_can_offer(self):
		name = self._request()
		outsider = "qa-p3-outsider@example.com"
		if not frappe.db.exists("User", outsider):
			frappe.get_doc({"doctype": "User", "email": outsider, "first_name": "QA P3 Outsider", "enabled": 1, "send_welcome_email": 0}).insert(ignore_permissions=True)
		offers.on_command(name, "offer 29 for 500", outsider)
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, ["status", "claimed_by"]), ("Open", None))

	def test_chat_needs_access_to_the_conversation(self):
		name = self._request()
		thread = frappe.db.get_value("Excom Rate Request", name, "excom_thread")
		frappe.db.set_value("Excom Thread", thread, {"assigned_to": "Administrator", "assigned_team": None})
		offers.on_command(name, "chat", SALES)
		self.assertEqual(frappe.db.get_value("Excom Rate Request", name, "status"), "Open")
		self.assertEqual(frappe.db.get_value("Excom Thread", thread, "assigned_to"), "Administrator")

	# ── Raven: only commands in excom's own thread count (review H1) ─────────
	def test_raven_commands_only_from_the_request_thread(self):
		name = self._request()
		frappe.db.set_value("Excom Rate Request", name, "raven_thread", "RVTHREAD1")
		roots = {"RVTHREAD1": frappe._dict(link_doctype="Excom Rate Request", link_document=name, is_bot_message=1, bot="bot-user", channel_id="qa-p3-channel"),
			"FORGED": frappe._dict(link_doctype="Excom Rate Request", link_document=name, is_bot_message=0, bot=None, channel_id="qa-p3-channel"),
			"ELSEWHERE": frappe._dict(link_doctype="Excom Rate Request", link_document=name, is_bot_message=1, bot="bot-user", channel_id="other")}
		patched = (raven_bridge._thread_root, raven_bridge._bot_user, raven_bridge.enabled, frappe.enqueue)
		jobs = []
		raven_bridge._thread_root = roots.get
		raven_bridge._bot_user = lambda: "bot-user"
		raven_bridge.enabled = lambda: True
		frappe.enqueue = lambda method, **kw: jobs.append((kw.get("request"), kw.get("text"), kw.get("user")))
		try:
			msg = lambda channel, text="offer 29 for 500", **kw: frappe._dict(channel_id=channel, content=text, owner=SALES, message_type="Text", **kw)
			raven_bridge.on_raven_message(msg("FORGED"))
			raven_bridge.on_raven_message(msg("ELSEWHERE"))
			raven_bridge.on_raven_message(msg("RVTHREAD1", is_bot_message=1))
			self.assertEqual(jobs, [])
			raven_bridge.on_raven_message(msg("RVTHREAD1"))
			self.assertEqual(jobs, [(name, "offer 29 for 500", SALES)])
			frappe.db.set_value("Excom Rate Request", name, "raven_thread", "SOMETHING-ELSE")
			raven_bridge.on_raven_message(msg("RVTHREAD1"))
			self.assertEqual(len(jobs), 1)
		finally:
			raven_bridge._thread_root, raven_bridge._bot_user, raven_bridge.enabled, frappe.enqueue = patched
