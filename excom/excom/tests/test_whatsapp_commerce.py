"""HLD-005 Phase 2 — rate updates on WhatsApp: scan, draft, summary, auto-publish rules, publish
(catalogue sync → rate card → broadcast with payloads), catalogue payloads, View & Order /
Full Catalogue replies, cart order → draft Sales Order re-priced by ERPNext.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_commerce'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, now_datetime, nowdate

from excom.excom.services import whatsapp_service as ws
from excom.excom.services.commerce import catalog, handlers, rates
from excom.excom.utils import webhook

PHONE_ID = "QAPHONEP2"
WABA = "QAWABAP2"
PL = "QA P2 Dealer"
ITEMS = [f"QA-P2-{i:02d}" for i in range(12)]


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _leaf(doctype):
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")


def _cleanup(account):
	ids = frappe.db.sql("SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199002%%'", pluck=True)
	if ids:
		for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
			frappe.db.delete(child, {"parent": ["in", ids]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", ids]})
		frappe.db.delete("Excom Subscriber", {"omni_identity": ["in", ids]})
		frappe.db.delete("Omni Identity", {"name": ["in", ids]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	for ru in frappe.get_all("Excom Rate Update", filters={"price_list": PL}, pluck="name"):
		frappe.db.delete("Excom Rate Update Item", {"parent": ru})
		frappe.db.delete("File", {"attached_to_doctype": "Excom Rate Update", "attached_to_name": ru})
		frappe.db.delete("Excom Rate Update", ru)
	frappe.db.delete("Excom Published Rate", {"price_list": PL})
	frappe.db.delete("Excom Catalog Sync Log", {"channel_account": account})
	for bc in frappe.get_all("Excom Broadcast", filters={"reference_doctype": "Excom Rate Update", "wa_channel_account": account}, pluck="name"):
		frappe.db.delete("Excom Broadcast Log", {"broadcast": bc})
		frappe.db.delete("Excom Broadcast", bc)
	for so in frappe.get_all("Sales Order", filters={"customer": "QA P2 Customer", "docstatus": 0}, pluck="name"):
		frappe.delete_doc("Sales Order", so, force=True, ignore_permissions=True)
	frappe.db.delete("Excom Subscriber List", {"list_name": "QA P2 Dealers"})


class TestWhatsAppCommerce(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		if not frappe.db.exists("Price List", PL):
			frappe.get_doc({"doctype": "Price List", "price_list_name": PL, "currency": "INR", "selling": 1, "enabled": 1}).insert(ignore_permissions=True)
		uom = frappe.db.get_value("UOM", "Nos") or frappe.db.get_value("UOM", {}, "name")
		for code in ITEMS:
			if not frappe.db.exists("Item", code):
				frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": f"QA Water Gun {code[-2:]}", "item_group": _leaf("Item Group"),
					"stock_uom": uom, "is_stock_item": 0, "is_sales_item": 1}).insert(ignore_permissions=True)
		cls.uom = frappe.db.get_value("Item", ITEMS[0], "stock_uom")
		for code in ITEMS:
			frappe.db.set_value("Item", code, "image", f"/files/{code.lower()}.png")  # public image path for the catalogue
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Commerce", "channel": "whatsapp", "status": "Active",
			"wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_token": "qa-token-0000000000", "wa_catalog_id": "QACAT2",
			"catalog_price_list": PL, "company": frappe.defaults.get_global_default("company")}).insert(ignore_permissions=True)
		lang = frappe.db.get_value("Language", {}, "name") or "en"
		tpl = frappe.get_doc({"doctype": "WhatsApp Templates", "template_name": "qa p2 rates", "actual_name": "qa_p2_rates",
			"template": "Rates updated {{date}}: {{summary}}, {{more}}. Tap a button below.", "language": lang, "language_code": "en", "category": "MARKETING", "status": "APPROVED", "whatsapp_account": cls.acc.name})
		for label in ("View & Order", "Full Catalogue", "Ask Best Price"):
			tpl.append("buttons", {"button_type": "Quick Reply", "button_label": label})
		tpl.name = "qa-p2-rates"
		tpl.db_insert()
		for row in tpl.buttons:
			row.parent, row.parenttype, row.parentfield = tpl.name, "WhatsApp Templates", "buttons"
			row.db_insert()
		# the site's own commerce settings are restored after the run (they drive real scans and syncs)
		cls._settings = frappe.get_single("Excom Commerce Settings").as_dict(no_default_fields=True)
		if not frappe.db.exists("Customer", "QA P2 Customer"):
			frappe.get_doc({"doctype": "Customer", "customer_name": "QA P2 Customer", "customer_group": _leaf("Customer Group"),
				"territory": _leaf("Territory"), "default_price_list": PL}).insert(ignore_permissions=True)
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
		frappe.db.delete("WhatsApp Button", {"parent": "qa-p2-rates"})
		frappe.db.delete("WhatsApp Templates", "qa-p2-rates")
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.db.delete("Item Price", {"price_list": PL})
		s = frappe.get_single("Excom Commerce Settings")
		s.update({"enabled": 1, "auto_publish_delay_min": 30, "min_hours_between_broadcasts": 4, "summary_items": 3,
			"rate_update_template": "qa-p2-rates", "wave_size": 0, "product_url_template": "https://qa.example.com/p/{item_code}",
			"brand": "QA Brand", "last_scan_at": None})
		s.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_document_cache("Excom Commerce Settings", "Excom Commerce Settings")
		self.calls = []
		self._orig = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (self.calls.append((url, kw.get("json"))) or FakeResp(200, {"messages": [{"id": f"wamid.P2OUT{len(self.calls)}"}]}))

	def tearDown(self):
		ws.http_requests.post = self._orig
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _price(self, code, rate, **extra):
		name = None if extra.get("valid_from") else frappe.db.get_value("Item Price", {"item_code": code, "price_list": PL, "customer": extra.get("customer"), "valid_from": ["is", "not set"]}, "name")
		if name:
			frappe.db.set_value("Item Price", name, "price_list_rate", rate)  # like ERPNext's own auto-update: no hooks
			return name
		return frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": PL, "price_list_rate": rate, "uom": self.uom, **extra}).insert(ignore_permissions=True).name

	def _draft(self):
		name = frappe.db.get_value("Excom Rate Update", {"price_list": PL, "status": "Draft"}, "name")
		return frappe.get_doc("Excom Rate Update", name) if name else None

	def _baseline(self, n=3, rate=30):
		for code in ITEMS[:n]:
			self._price(code, rate)
		rates.scan()

	# ── scan ─────────────────────────────────────────────────────────────────
	def test_first_scan_is_a_silent_baseline(self):
		self._baseline()
		self.assertEqual(frappe.db.count("Excom Published Rate", {"price_list": PL}), 3)
		self.assertIsNone(self._draft())

	def test_change_collects_and_change_back_drops(self):
		self._baseline()
		self._price(ITEMS[0], 28)
		self._price(ITEMS[1], 33)
		rates.scan()
		d = self._draft()
		rows = {r.item_code: (r.old_rate, r.new_rate, r.change) for r in d.items}
		self.assertEqual(rows, {ITEMS[0]: (30, 28, -2), ITEMS[1]: (30, 33, 3)})
		self._price(ITEMS[1], 30)
		rates.scan()
		self.assertEqual([r.item_code for r in self._draft().items], [ITEMS[0]])

	def test_customer_specific_and_future_prices_are_ignored(self):
		self._baseline(1)
		self._price(ITEMS[1], 50, customer="QA P2 Customer")
		self._price(ITEMS[2], 50, valid_from=add_days(nowdate(), 3))
		rates.scan()
		self.assertIsNone(self._draft())

	def test_summary_is_one_line_with_more_count(self):
		self._baseline(4)
		for code, rate in zip(ITEMS[:4], (31, 25, 30.5, 40)):
			self._price(code, rate)
		rates.scan()
		frappe.db.set_single_value("Excom Commerce Settings", "summary_items", 2)
		frappe.clear_document_cache("Excom Commerce Settings", "Excom Commerce Settings")
		text, more = rates.summary(self._draft())
		self.assertNotIn("\n", text)
		self.assertTrue(text.startswith("QA Water Gun 03"))  # biggest change first (+10)
		self.assertEqual(more, 2)

	def test_auto_publish_waits_for_quiet_and_gap(self):
		self._baseline()
		self._price(ITEMS[0], 29)
		rates.scan()
		d = self._draft()
		orig = rates.queue_publish
		rates.queue_publish = lambda name, manual=False: {"queued": name}  # never queue a real draft of the site
		try:
			self.assertNotIn(d.name, rates.auto_publish())  # just changed
			frappe.db.set_value("Excom Rate Update", d.name, "last_change_on", add_to_date(now_datetime(), minutes=-31))
			recent = frappe.get_doc({"doctype": "Excom Rate Update", "price_list": PL, "status": "Published", "published_on": add_to_date(now_datetime(), hours=-1)}).insert(ignore_permissions=True)
			self.assertNotIn(d.name, rates.auto_publish())  # last broadcast 1 h ago < 4 h
			frappe.db.set_value("Excom Rate Update", recent.name, "published_on", add_to_date(now_datetime(), hours=-5))
			self.assertIn(d.name, rates.auto_publish())
		finally:
			rates.queue_publish = orig

	# ── publish ──────────────────────────────────────────────────────────────
	def test_publish_syncs_catalogue_then_broadcasts_with_payloads(self):
		self._baseline()
		self._price(ITEMS[0], 27)
		rates.scan()
		d = self._draft()
		lst = frappe.get_doc({"doctype": "Excom Subscriber List", "list_name": "QA P2 Dealers", "channel_account": self.acc.name}).insert(ignore_permissions=True)
		frappe.db.set_value("Excom Rate Update", d.name, "status", "Syncing")  # as queue_publish leaves it
		synced = []
		orig_sync = catalog.sync_items
		catalog.sync_items = lambda acc, items, pl, rate_update="": (synced.append(items) or {"_status": "Finished", ITEMS[0]: "ok"})
		try:
			rates.publish(d.name)
		finally:
			catalog.sync_items = orig_sync
		d.reload()
		self.assertEqual(d.status, "Published", d.error)
		self.assertEqual(synced, [[(ITEMS[0], 27)]])
		self.assertTrue(d.rate_card_image)
		bc = frappe.get_doc("Excom Broadcast", {"reference_name": d.name})
		self.assertEqual(bc.subscriber_list, lst.name)
		self.assertEqual(json.loads(bc.wa_button_payloads), [f"RU:{d.name}:view", f"RU:{d.name}:catalog", f"RU:{d.name}:ask"])
		self.assertEqual(set(json.loads(bc.wa_template_variables)), {"date", "summary", "more"})
		self.assertEqual(frappe.db.get_value("Excom Published Rate", {"item_code": ITEMS[0], "price_list": PL}, "rate"), 27)

	def test_publish_stops_when_the_catalogue_fails(self):
		self._baseline()
		self._price(ITEMS[0], 26)
		rates.scan()
		d = self._draft()
		frappe.get_doc({"doctype": "Excom Subscriber List", "list_name": "QA P2 Dealers", "channel_account": self.acc.name}).insert(ignore_permissions=True)
		frappe.db.set_value("Excom Rate Update", d.name, "status", "Syncing")
		orig_sync = catalog.sync_items
		catalog.sync_items = lambda *a, **k: {"_status": "Failed"}
		try:
			rates.publish(d.name)
		finally:
			catalog.sync_items = orig_sync
		self.assertEqual(frappe.db.get_value("Excom Rate Update", d.name, "status"), "Failed")
		self.assertEqual(frappe.db.get_value("Excom Published Rate", {"item_code": ITEMS[0], "price_list": PL}, "rate"), 30)

	# ── catalogue ────────────────────────────────────────────────────────────
	def test_catalogue_item_payload(self):
		data = catalog.item_payload(ITEMS[0], 30, "INR")
		self.assertEqual((data["id"], data["price"], data["availability"], data["condition"]), (ITEMS[0], "30.00 INR", "in stock", "new"))
		self.assertEqual(data["link"], f"https://qa.example.com/p/{ITEMS[0]}")
		self.assertEqual((data["brand"], data["wa_compliance_category"], data["origin_country"]), ("QA Brand", "DEFAULT", "IN"))
		self.assertIn("?v=", data["image_link"])  # a new URL whenever the image changes
		frappe.db.set_value("Item", ITEMS[0], "image", "/private/files/secret.png")
		with self.assertRaises(catalog.NotListable):
			catalog.item_payload(ITEMS[0], 30, "INR")  # Meta cannot fetch a private image

	def test_items_batch_call_and_log(self):
		import requests

		sent = []
		orig_post, orig_get = requests.post, requests.get
		requests.post = lambda url, **kw: (sent.append((url, {**kw["data"], "requests": json.loads(kw["data"]["requests"])})) or FakeResp(200, {"handles": ["H1"], "validation_status": [{"retailer_id": ITEMS[1], "errors": [{"message": "bad image"}]}]}))
		requests.get = lambda url, **kw: FakeResp(200, {"data": [{"status": "Finished", "errors": [], "ids_of_invalid_requests": [ITEMS[2]]}]})
		try:
			result = catalog.sync_items(self.acc, [(ITEMS[0], 30), (ITEMS[1], 31), (ITEMS[2], 32)], PL)
		finally:
			requests.post, requests.get = orig_post, orig_get
		url, body = sent[0]
		self.assertTrue(url.endswith("/QACAT2/items_batch"))
		self.assertEqual((body["item_type"], body["allow_upsert"], body["requests"][0]["method"]), ("PRODUCT_ITEM", "true", "UPDATE"))
		self.assertEqual(result[ITEMS[0]], "ok")
		self.assertTrue(result[ITEMS[1]].startswith("error"))  # validation_status
		self.assertTrue(result[ITEMS[2]].startswith("error"))  # ids_of_invalid_requests (the full list)
		self.assertEqual(frappe.db.get_value("Excom Catalog Sync Log", {"channel_account": self.acc.name}, ["status", "items_failed"]), ("Partly Failed", 2))

	# ── replies to taps ──────────────────────────────────────────────────────
	def _published(self, n):
		doc = frappe.get_doc({"doctype": "Excom Rate Update", "price_list": PL, "status": "Published", "published_on": now_datetime(),
			"items": [{"item_code": c, "old_rate": 30, "new_rate": 30 + i, "change": i} for i, c in enumerate(ITEMS[:n], start=1)]}).insert(ignore_permissions=True)
		return doc.name

	def test_view_and_order_shape_by_count(self):
		self.assertEqual(handlers.view_and_order(self._published(1), self.acc, "x")["type"], "product")
		self.assertEqual(handlers.view_and_order(self._published(3), self.acc, "x")["type"], "carousel")
		many = handlers.view_and_order(self._published(12), self.acc, "x")
		self.assertEqual(many["type"], "product_list")
		self.assertEqual(sum(len(s["product_items"]) for s in many["action"]["sections"]), 12)

	def test_view_tap_replies_with_carousel_in_the_thread(self):
		name = self._published(3)
		webhook._process_webhook_payload(json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
			"metadata": {"phone_number_id": PHONE_ID}, "contacts": [{"wa_id": "919900200001", "profile": {"name": "QA"}}],
			"messages": [{"from": "919900200001", "id": "wamid.P2TAP", "timestamp": "1700000600", "type": "button", "button": {"text": "View & Order", "payload": f"RU:{name}:view"}}]}}]}]}))
		interactive = [b for _u, b in self.calls if b and b.get("type") == "interactive"]
		self.assertEqual(interactive[0]["interactive"]["type"], "carousel")
		self.assertEqual(interactive[0]["context"], {"message_id": "wamid.P2TAP"})

	# ── orders ───────────────────────────────────────────────────────────────
	def _order(self, phone, wamid, items):
		return json.dumps({"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
			"metadata": {"phone_number_id": PHONE_ID}, "contacts": [{"wa_id": phone, "profile": {"name": "QA"}}],
			"messages": [{"from": phone, "id": wamid, "timestamp": "1700000700", "type": "order",
				"order": {"catalog_id": "QACAT2", "product_items": items}}]}}]}]})

	def test_cart_order_becomes_a_repriced_draft_sales_order(self):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		self._price(ITEMS[0], 30)
		# excom gives every Customer an identity when it is created; put the dealer's number on it
		identity = frappe.db.get_value("Omni Identity Link", {"linked_doctype": "Customer", "linked_name": "QA P2 Customer"}, "parent")
		if identity:
			frappe.db.set_value("Omni Identity", identity, {"primary_phone": "919900200002", "primary_whatsapp": "919900200002", "normalized_phone": "919900200002"})
		else:
			identity = resolve_identity_internal(phone="919900200002", channel="whatsapp", channel_user_id="919900200002", display_name="QA")
			doc = frappe.get_doc("Omni Identity", identity)
			doc.add_link("Customer", "QA P2 Customer", "Unknown")
			doc.save(ignore_permissions=True)
		frappe.db.commit()
		webhook._process_webhook_payload(self._order("919900200002", "wamid.P2ORD", [
			{"product_retailer_id": ITEMS[0], "quantity": 100, "item_price": 1, "currency": "INR"},
			{"product_retailer_id": "NOT-AN-ITEM", "quantity": 5, "item_price": 1, "currency": "INR"}]))
		so_name = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P2ORD"}, "reference_name")
		self.assertTrue(so_name)
		so = frappe.get_doc("Sales Order", so_name)
		self.assertEqual((so.docstatus, so.customer, len(so.items)), (0, "QA P2 Customer", 1))
		self.assertEqual(so.items[0].rate, 30)  # ERPNext's price, not the cart's 1.00
		texts = [b["text"]["body"] for _u, b in self.calls if b and b.get("type") == "text"]
		self.assertTrue(any(so_name in t and "NOT-AN-ITEM" in t for t in texts))

	def test_order_from_unknown_contact_creates_no_sales_order(self):
		webhook._process_webhook_payload(self._order("919900200003", "wamid.P2UNK", [{"product_retailer_id": ITEMS[0], "quantity": 1, "item_price": 30, "currency": "INR"}]))
		self.assertFalse(frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P2UNK"}, "reference_name"))
		thread = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.P2UNK"}, "thread")
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Excom Thread", "reference_name": thread, "content": ["like", "%no linked Customer%"]}))

	# ── broadcast budget ─────────────────────────────────────────────────────
	def test_messaging_budget_from_tier(self):
		from excom.excom.services.broadcast_service import _messaging_budget

		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_messaging_limit", "TIER_250")
		bc = frappe._dict(channel="WhatsApp", wa_channel_account=self.acc.name)
		self.assertEqual(_messaging_budget(bc), 250)
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_messaging_limit", "TIER_UNLIMITED")
		self.assertIsNone(_messaging_budget(bc))

	# ── review fixes ─────────────────────────────────────────────────────────
	def test_scheduled_price_is_announced_on_the_day(self):
		self._baseline(1)
		name = self._price(ITEMS[0], 35, valid_from=nowdate())
		frappe.db.sql("UPDATE `tabItem Price` SET modified = %s WHERE name = %s", (add_days(now_datetime(), -2), name))  # saved days ago
		rates.scan(since=add_days(now_datetime(), -1))
		d = self._draft()
		self.assertEqual([(r.item_code, r.new_rate) for r in d.items], [(ITEMS[0], 35)])

	def test_latest_valid_from_wins(self):
		self._baseline(1)
		self._price(ITEMS[0], 31, valid_from=add_days(nowdate(), -10))
		got = rates.current_prices(PL, {ITEMS[0]})
		self.assertEqual(got[(ITEMS[0], self.uom)][0], 31)

	def test_change_back_during_publish_is_caught(self):
		self._baseline(1)
		ru = frappe.get_doc({"doctype": "Excom Rate Update", "price_list": PL, "status": "Published",
			"items": [{"item_code": ITEMS[0], "uom": self.uom, "old_rate": 30, "new_rate": 28, "change": -2}]}).insert(ignore_permissions=True)
		rates._set_published(ITEMS[0], PL, self.uom, 28, now_datetime(), ru.name)  # told 28, but the price is back at 30
		rates._rebase_open_draft(ru)
		d = self._draft()
		self.assertEqual([(r.old_rate, r.new_rate) for r in d.items], [(28, 30)])

	def test_queue_publish_runs_once(self):
		self._baseline(1)
		self._price(ITEMS[0], 29)
		rates.scan()
		d = self._draft()
		frappe.db.set_value("Excom Rate Update", d.name, "status", "Syncing")
		self.assertEqual(rates.queue_publish(d.name)["queued"], "")  # already publishing: not queued again
