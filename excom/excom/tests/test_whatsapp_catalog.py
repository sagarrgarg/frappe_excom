"""WhatsApp catalogue sync: ERPNext Items + today's price → Meta items_batch, logs, price choice,
and the broadcast messaging budget from the number's tier.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_catalog'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from excom.excom.services import whatsapp_catalog as catalog

PHONE_ID = "QAPHONECAT"
WABA = "QAWABACAT"
PL = "QA Catalogue"
ITEMS = [f"QA-CAT-{i:02d}" for i in range(3)]


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _leaf(doctype):
	return frappe.db.get_value(doctype, {"is_group": 0}, "name")


class TestWhatsAppCatalog(FrappeTestCase):
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
				frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": f"QA Product {code[-2:]}", "item_group": _leaf("Item Group"),
					"stock_uom": uom, "is_stock_item": 0, "is_sales_item": 1}).insert(ignore_permissions=True)
			frappe.db.set_value("Item", code, "image", f"/files/{code.lower()}.png")
		cls.uom = frappe.db.get_value("Item", ITEMS[0], "stock_uom")
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Catalogue", "channel": "whatsapp", "status": "Active",
			"wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_token": "qa-token-0000000000", "wa_catalog_id": "QACAT",
			"catalog_price_list": PL, "catalog_product_url": "https://qa.example.com/p/{item_code}", "catalog_brand": "QA Brand",
			"company": frappe.defaults.get_global_default("company")}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.db.delete("Excom Catalog Sync Log", {"channel_account": cls.acc.name})
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.db.rollback()
		frappe.db.delete("Item Price", {"price_list": PL})
		frappe.db.delete("Excom Catalog Sync Log", {"channel_account": self.acc.name})
		frappe.db.commit()

	def _price(self, code, rate, **extra):
		return frappe.get_doc({"doctype": "Item Price", "item_code": code, "price_list": PL, "price_list_rate": rate, "uom": self.uom, **extra}).insert(ignore_permissions=True).name

	def test_item_payload(self):
		data = catalog.item_payload(ITEMS[0], 30, "INR", self.acc)
		self.assertEqual((data["id"], data["price"], data["availability"], data["condition"]), (ITEMS[0], "30.00 INR", "in stock", "new"))
		self.assertEqual(data["link"], f"https://qa.example.com/p/{ITEMS[0]}")
		self.assertEqual((data["brand"], data["wa_compliance_category"], data["origin_country"]), ("QA Brand", "DEFAULT", "IN"))
		self.assertIn("?v=", data["image_link"])  # a new URL whenever the image changes
		frappe.db.set_value("Item", ITEMS[0], "image", "/private/files/secret.png")
		with self.assertRaises(catalog.NotListable):
			catalog.item_payload(ITEMS[0], 30, "INR", self.acc)  # Meta cannot fetch a private image
		with self.assertRaises(catalog.NotListable):
			catalog.item_payload(ITEMS[1], 30, "INR", frappe._dict())  # no product page URL

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
		self.assertTrue(url.endswith("/QACAT/items_batch"))
		self.assertEqual((body["item_type"], body["allow_upsert"], body["requests"][0]["method"]), ("PRODUCT_ITEM", "true", "UPDATE"))
		self.assertEqual(result[ITEMS[0]], "ok")
		self.assertTrue(result[ITEMS[1]].startswith("error"))  # validation_status
		self.assertTrue(result[ITEMS[2]].startswith("error"))  # ids_of_invalid_requests (the full list)
		self.assertEqual(frappe.db.get_value("Excom Catalog Sync Log", {"channel_account": self.acc.name}, ["status", "items_failed"]), ("Partly Failed", 2))

	def test_current_prices_like_erpnext(self):
		self._price(ITEMS[0], 30)
		self._price(ITEMS[0], 28, valid_from=nowdate())  # the latest valid_from wins
		self._price(ITEMS[1], 30, valid_from=add_days(nowdate(), 3))  # not valid yet
		customer = frappe.db.get_value("Customer", {}, "name")
		if customer:
			self._price(ITEMS[2], 10, customer=customer)  # one customer's price is not the catalogue price
		prices = catalog.current_prices(PL, set(ITEMS))
		self.assertEqual(prices[(ITEMS[0], self.uom)][0], 28)
		self.assertNotIn((ITEMS[1], self.uom), prices)
		self.assertNotIn((ITEMS[2], self.uom), prices)

	def test_messaging_budget_from_tier(self):
		from excom.excom.services.broadcast_service import _messaging_budget

		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_messaging_limit", "TIER_250")
		bc = frappe._dict(channel="WhatsApp", wa_channel_account=self.acc.name)
		self.assertEqual(_messaging_budget(bc), 250)
		frappe.db.set_value("Excom Channel Account", self.acc.name, "wa_messaging_limit", "TIER_UNLIMITED")
		self.assertIsNone(_messaging_budget(bc))
