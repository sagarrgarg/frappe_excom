"""HLD-005 Phase 4 — template types and Meta's template library: create payloads per type, Meta's
rules checked before posting, the Meta → excom sync round-trip, send-time components per type,
early refusals (before Meta's 131008 / 132000) and the library search / create.

	bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_whatsapp_template_types'}"
"""

import json
import time

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.doctype.whatsapp_templates import whatsapp_templates as wt
from excom.excom.services import whatsapp_service as ws
from excom.excom.services import whatsapp_template_library as lib
from excom.excom.services import whatsapp_template_types as wtt

PHONE_ID = "QAPHONEP4"
WABA = "QAWABAP4"
CATALOG = "QACAT4"
FUTURE_MS = lambda: int(time.time() * 1000) + 2 * 86400 * 1000  # noqa: E731


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _cleanup(account):
	names = frappe.get_all("WhatsApp Templates", filters={"actual_name": ["like", "qa_p4_%"]}, pluck="name")
	for child in ("WhatsApp Button", "WhatsApp Carousel Card", "WhatsApp Template Linked Account"):
		if names:
			frappe.db.delete(child, {"parent": ["in", names]})
	if names:
		frappe.db.delete("WhatsApp Templates", {"name": ["in", names]})
	ids = frappe.db.sql("SELECT name FROM `tabOmni Identity` WHERE normalized_phone LIKE '9199004%%'", pluck=True)
	if ids:
		for child in ("Omni Identity Alias", "Omni Identity Channel", "Omni Identity Link"):
			frappe.db.delete(child, {"parent": ["in", ids]})
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", ids]})
		frappe.db.delete("Omni Identity", {"name": ["in", ids]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})


# Meta's GET /message_templates shapes for the new types (from Meta's create examples, upper-cased as GET returns them)
META_LTO = {"name": "qa_p4_lto", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4LTO", "components": [
	{"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["https://scontent.example/lto.jpg"]}},
	{"type": "LIMITED_TIME_OFFER", "limited_time_offer": {"text": "Expiring offer!", "has_expiration": True}},
	{"type": "BODY", "text": "Good news, {{1}}! Use code {{2}} to get 25% off!", "example": {"body_text": [["Pablo", "CARIBE25"]]}},
	{"type": "BUTTONS", "buttons": [
		{"type": "COPY_CODE", "example": ["CARIBE25"]},
		{"type": "URL", "text": "Book now!", "url": "https://awesomedestinations.com/offers?code={{1}}", "example": ["https://awesomedestinations.com/offers?ref=n3mtql"]}]},
]}
META_MEDIA_CAROUSEL = {"name": "qa_p4_media_carousel", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4MC", "components": [
	{"type": "BODY", "text": "Rare succulents for sale! {{1}}, use code {{2}}.", "example": {"body_text": [["Pablo", "30OFF"]]}},
	{"type": "CAROUSEL", "cards": [
		{"components": [
			{"type": "HEADER", "format": "IMAGE", "example": {"header_handle": [f"https://scontent.example/card{i}.jpg"]}},
			{"type": "BODY", "text": "{{1}} off this one", "example": {"body_text": [["20%"]]}},
			{"type": "BUTTONS", "buttons": [
				{"type": "QUICK_REPLY", "text": "Send me more like this!"},
				{"type": "URL", "text": "Shop", "url": "https://www.luckyshrub.com/rare-succulents/{{1}}", "example": ["BLUE_ELF"]}]}]}
		for i in range(3)]},
]}
META_PRODUCT_CAROUSEL = {"name": "qa_p4_product_carousel", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4PC", "components": [
	{"type": "BODY", "text": "New this week, {{1}}!", "example": {"body_text": [["Pablo"]]}},
	{"type": "CAROUSEL", "cards": [{"components": [{"type": "HEADER", "format": "PRODUCT"}, {"type": "BUTTONS", "buttons": [{"type": "SPM", "text": "View"}]}]} for _ in range(2)]},
]}
META_CATALOG = {"name": "qa_p4_catalog", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4CAT", "components": [
	{"type": "BODY", "text": "Get Rs {{1}} off on orders above {{2}}Rs!", "example": {"body_text": [["100", "400"]]}},
	{"type": "FOOTER", "text": "Best grocery deals on WhatsApp!"},
	{"type": "BUTTONS", "buttons": [{"type": "CATALOG", "text": "View catalog"}]},
]}
META_MPM = {"name": "qa_p4_mpm", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4MPM", "components": [
	{"type": "HEADER", "format": "TEXT", "text": "Forget something, {{1}}?", "example": {"header_text": ["Pablo"]}},
	{"type": "BODY", "text": "Use code {{1}} to get 10% off!", "example": {"body_text": [["10OFF"]]}},
	{"type": "BUTTONS", "buttons": [{"type": "MPM", "text": "View items"}]},
]}
META_SPM = {"name": "qa_p4_spm", "status": "APPROVED", "language": "en_US", "category": "MARKETING", "id": "P4SPM", "components": [
	{"type": "HEADER", "format": "PRODUCT"},
	{"type": "BODY", "text": "Use code {{code}} to get {{percent}} off!", "example": {"body_text_named_params": [{"param_name": "code", "example": "15OFF"}, {"param_name": "percent", "example": "15%"}]}},
	{"type": "FOOTER", "text": "Offer ends September 22"},
	{"type": "BUTTONS", "buttons": [{"type": "SPM", "text": "View"}]},
]}
META_VOICE = {"name": "qa_p4_voice", "status": "APPROVED", "language": "en_US", "category": "UTILITY", "id": "P4VC", "components": [
	{"type": "BODY", "text": "You can call us on WhatsApp now for faster service!"},
	{"type": "BUTTONS", "buttons": [{"type": "VOICE_CALL", "text": "Call Now", "ttl_minutes": 1440}, {"type": "URL", "text": "Contact Support", "url": "https://www.luckyshrub.com/support"}]},
]}
META_CPR = {"name": "qa_p4_cpr", "status": "APPROVED", "language": "en", "category": "UTILITY", "id": "P4CPR", "components": [
	{"type": "BODY", "text": "We would like to call you about order {{1}}.", "example": {"body_text": [["ON-12345"]]}},
	{"type": "CALL_PERMISSION_REQUEST"},
]}


class TestWhatsAppTemplateTypes(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Channel Account", {"wa_phone_id": PHONE_ID})
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": "QA WA Template Types", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": PHONE_ID, "wa_business_id": WABA, "wa_catalog_id": CATALOG,
			"wa_token": "qa-token-0000000000"}).insert(ignore_permissions=True)
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
		self._orig_post = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (self.calls.append((url, kw.get("json"))) or FakeResp(200, {"messages": [{"id": f"wamid.P4OUT{len(self.calls)}"}]}))

	def tearDown(self):
		ws.http_requests.post = self._orig_post
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _template(self, body="Hi {{1}}", buttons=(), cards=(), **fields):
		doc = frappe.new_doc("WhatsApp Templates")
		doc.update({"template_name": "qa p4", "template": body, "language_code": "en_US", "category": "MARKETING",
			"whatsapp_account": self.acc.name, **fields})
		for b in buttons:
			doc.append("buttons", b)
		for c in cards:
			doc.append("cards", c)
		return doc

	def _check(self, doc):
		doc.validate_button_count()
		wtt.validate_template(doc)

	def _stored(self, meta):
		wt._store_templates({"data": [json.loads(json.dumps(meta))]}, [self.acc.name])
		return frappe.get_doc("WhatsApp Templates", {"actual_name": meta["name"]})

	def _media_cards(self, n=2, body=True):
		return [{"card_body": "{{1}} off this one" if body else "", "body_variable_samples": '["20%"]', "button_1_text": "More like this",
			"button_2_text": "Shop", "button_2_value": "https://shop.example/{{1}}", "button_2_example": "BLUE_ELF",
			"header_handle": f"4::handle{i}"} for i in range(n)]

	def _media_carousel(self, n=2, **kw):
		return self._template("Rare succulents {{1}}", template_type="Media Carousel", card_header_format="IMAGE",
			card_button_1_type="Quick Reply", card_button_2_type="Visit Website", cards=self._media_cards(n, **kw))

	# ── create payloads ──────────────────────────────────────────────────────
	def test_copy_code_create_payload_and_code_limits(self):
		doc = self._template("Use {{1}} for {{2}} off", buttons=[
			{"button_type": "Quick Reply", "button_label": "Unsubscribe"},
			{"button_type": "Copy Code", "button_label": "Copy offer code", "example_code": "WINTER25"}])
		self._check(doc)
		self.assertEqual(doc.meta_components()[-1]["buttons"], [{"type": "QUICK_REPLY", "text": "Unsubscribe"}, {"type": "COPY_CODE", "example": "WINTER25"}])
		doc.buttons[1].example_code = "C" * 21  # coupon page: 20
		with self.assertRaises(frappe.ValidationError):
			self._check(doc)
		doc.buttons[1].example_code = "C" * 20
		self._check(doc)
		doc.append("buttons", {"button_type": "Copy Code", "button_label": "x", "example_code": "Y"})
		with self.assertRaises(frappe.ValidationError):
			self._check(doc)  # one copy-code button per template

	def test_limited_time_offer_create_payload_and_rules(self):
		doc = self._template("Good news, {{1}}! Use code {{2}}", template_type="Limited-Time Offer", lto_text="Expiring offer!", lto_has_expiration=1,
			body_variable_samples=json.dumps(["Pablo", "CARIBE25"]), buttons=[
				{"button_type": "Copy Code", "button_label": "Copy offer code", "example_code": "CARIBE25"},
				{"button_type": "Visit Website", "button_label": "Book now!", "website_url": "https://a.example/offers?code={{1}}", "url_type": "Dynamic", "example_url": "https://a.example/offers?ref=n3mtql"}])
		self._check(doc)
		comps = doc.meta_components()
		self.assertIn({"type": "LIMITED_TIME_OFFER", "limited_time_offer": {"text": "Expiring offer!", "has_expiration": True}}, comps)
		self.assertEqual(comps[-1]["buttons"][0], {"type": "COPY_CODE", "example": "CARIBE25"})
		self.assertFalse(any(c["type"] == "FOOTER" for c in comps))
		for field, bad in (("footer", "Terms apply"), ("lto_text", "x" * 17), ("category", "UTILITY"), ("header_type", "DOCUMENT")):
			before = doc.get(field)
			doc.set(field, bad)
			with self.assertRaises(frappe.ValidationError, msg=field):
				self._check(doc)
			doc.set(field, before)
		doc.buttons[0].example_code = "C" * 16  # LTO page: 15
		with self.assertRaises(frappe.ValidationError):
			self._check(doc)

	def test_media_carousel_create_payload_and_rules(self):
		doc = self._media_carousel()
		doc.body_variable_samples = json.dumps(["Pablo"])
		self._check(doc)
		comps = doc.meta_components()
		self.assertEqual([c["type"] for c in comps], ["BODY", "CAROUSEL"])
		card = comps[1]["cards"][0]["components"]
		self.assertEqual(card[0], {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["4::handle0"]}})
		self.assertEqual(card[1], {"type": "BODY", "text": "{{1}} off this one", "example": {"body_text": [["20%"]]}})
		self.assertEqual(card[2]["buttons"], [{"type": "QUICK_REPLY", "text": "More like this"},
			{"type": "URL", "text": "Shop", "url": "https://shop.example/{{1}}", "example": ["BLUE_ELF"]}])
		bad = [
			self._media_carousel(n=1),  # 2–10 cards
			self._media_carousel(n=11),
		]
		mixed = self._media_carousel()
		mixed.cards[1].card_body = ""  # body on every card or none
		bad.append(mixed)
		header = self._media_carousel()
		header.header_type = "TEXT"
		header.header = "No headers on a carousel"
		bad.append(header)
		spm = self._media_carousel()
		spm.card_button_1_type = "SPM"
		bad.append(spm)
		long_body = self._media_carousel()
		long_body.cards[0].card_body = "x" * 161
		bad.append(long_body)
		for d in bad:
			with self.assertRaises(frappe.ValidationError):
				self._check(d)

	def test_card_samples_use_the_resumable_upload_once(self):
		doc = self._media_carousel()
		for c in doc.cards:
			c.header_handle = None
			c.header_sample = f"/files/qa-card-{c.idx or id(c)}.jpg"
		uploads = []
		doc._start_upload = lambda url: (uploads.append(url) or f"upload:{len(uploads)}")
		doc._upload_bytes = lambda session, url: f"4::{session}"
		doc._upload_card_samples()
		self.assertEqual(len(uploads), 2)
		self.assertTrue(all(c.header_handle.startswith("4::upload:") for c in doc.cards))
		doc._upload_card_samples()  # unchanged samples are not uploaded again
		self.assertEqual(len(uploads), 2)

	def test_product_carousel_catalog_mpm_spm_voice_and_permission_payloads(self):
		pc = self._template("New this week, {{1}}!", template_type="Product Carousel", card_button_1_type="SPM",
			cards=[{"button_1_text": "View"}, {"button_1_text": "View"}])
		self._check(pc)
		self.assertEqual(pc.meta_components()[-1]["cards"][0], {"components": [{"type": "HEADER", "format": "PRODUCT"}, {"type": "BUTTONS", "buttons": [{"type": "SPM", "text": "View"}]}]})
		pc.append("cards", {"button_1_text": "View"})
		with self.assertRaises(frappe.ValidationError):
			self._check(pc)  # created with exactly 2

		cat = self._template("Shop now", template_type="Catalog", footer="Deals", buttons=[{"button_type": "Catalog", "button_label": "View catalog"}])
		self._check(cat)
		self.assertEqual(cat.meta_components()[-1], {"type": "BUTTONS", "buttons": [{"type": "CATALOG", "text": "View catalog"}]})

		mpm = self._template("Use {{1}}", template_type="Multi-Product", header_type="TEXT", header="Forget something?",
			buttons=[{"button_type": "MPM", "button_label": "View items"}])
		self._check(mpm)
		self.assertEqual(mpm.meta_components()[-1]["buttons"], [{"type": "MPM", "text": "View items"}])
		mpm.header_type = ""
		with self.assertRaises(frappe.ValidationError):
			self._check(mpm)  # text header required

		spm = self._template("Use code {{code}}", template_type="Single-Product", buttons=[{"button_type": "SPM", "button_label": "View"}])
		self._check(spm)
		self.assertEqual(spm.header_type, "PRODUCT")
		self.assertIn({"type": "HEADER", "format": "PRODUCT"}, spm.meta_components())

		vc = self._template("Call us", category="UTILITY", call_permission_request=1, buttons=[
			{"button_type": "Voice Call", "button_label": "Call Now", "ttl_minutes": 1440},
			{"button_type": "Visit Website", "button_label": "Support", "website_url": "https://x.example/support", "url_type": "Static"}])
		self._check(vc)
		comps = vc.meta_components()
		self.assertEqual(comps[-2]["buttons"][0], {"type": "VOICE_CALL", "text": "Call Now", "ttl_minutes": 1440})
		self.assertEqual(comps[-1], {"type": "call_permission_request"})
		vc.buttons[0].ttl_minutes = 60  # template range 1440–43200
		with self.assertRaises(frappe.ValidationError):
			self._check(vc)

	def test_commerce_buttons_only_on_their_type(self):
		for kind in ("Catalog", "MPM", "SPM"):
			with self.assertRaises(frappe.ValidationError, msg=kind):
				self._check(self._template("Hi", buttons=[{"button_type": kind, "button_label": "View"}], template_type="Standard"))
		with self.assertRaises(frappe.ValidationError):
			self._check(self._template("Hi", template_type="Catalog", buttons=[
				{"button_type": "Catalog", "button_label": "View"}, {"button_type": "Quick Reply", "button_label": "No"}]))

	def test_plain_templates_unchanged(self):
		doc = self._template("Hello {{1}}", buttons=[{"button_type": "Quick Reply", "button_label": "Yes"}])
		self._check(doc)
		self.assertEqual(doc.template_type, "Standard")
		self.assertEqual(doc.meta_components(), [{"type": "BODY", "text": "Hello {{1}}"}, {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "Yes"}]}])

	# ── sync round-trip ──────────────────────────────────────────────────────
	def test_sync_round_trip_keeps_every_type(self):
		lto = self._stored(META_LTO)
		self.assertEqual((lto.template_type, lto.lto_text, lto.lto_has_expiration, lto.header_type), ("Limited-Time Offer", "Expiring offer!", 1, "IMAGE"))
		self.assertEqual((lto.buttons[0].button_type, lto.buttons[0].example_code), ("Copy Code", "CARIBE25"))
		self.assertIn({"type": "LIMITED_TIME_OFFER", "limited_time_offer": {"text": "Expiring offer!", "has_expiration": True}}, lto.meta_components())

		mc = self._stored(META_MEDIA_CAROUSEL)
		self.assertEqual((mc.template_type, mc.card_header_format, mc.card_button_1_type, mc.card_button_2_type), ("Media Carousel", "IMAGE", "Quick Reply", "Visit Website"))
		self.assertEqual(len(mc.cards), 3)
		self.assertEqual((mc.cards[0].button_2_value, mc.cards[0].button_2_example, mc.cards[2].header_handle),
			("https://www.luckyshrub.com/rare-succulents/{{1}}", "BLUE_ELF", "https://scontent.example/card2.jpg"))
		self.assertEqual(mc.meta_components()[-1]["cards"][1]["components"][2], META_MEDIA_CAROUSEL["components"][1]["cards"][1]["components"][2])

		pc = self._stored(META_PRODUCT_CAROUSEL)
		self.assertEqual((pc.template_type, pc.card_button_1_type, len(pc.cards)), ("Product Carousel", "SPM", 2))
		cat, mpm, spm = self._stored(META_CATALOG), self._stored(META_MPM), self._stored(META_SPM)
		self.assertEqual([cat.template_type, mpm.template_type, spm.template_type], ["Catalog", "Multi-Product", "Single-Product"])
		self.assertEqual((spm.header_type, json.loads(spm.body_variable_samples)), ("PRODUCT", ["15OFF", "15%"]))
		vc, cpr = self._stored(META_VOICE), self._stored(META_CPR)
		self.assertEqual((vc.buttons[0].button_type, vc.buttons[0].ttl_minutes), ("Voice Call", 1440))
		self.assertEqual((cpr.call_permission_request, cpr.template_type), (1, "Standard"))

		# re-sync as a plain template: nothing stale survives
		plain = dict(META_LTO, components=[{"type": "BODY", "text": "Now plain"}])
		lto = self._stored(plain)
		self.assertEqual((lto.template_type, lto.lto_text or "", lto.lto_has_expiration), ("Standard", "", 0))
		mc = self._stored(dict(META_MEDIA_CAROUSEL, components=[{"type": "BODY", "text": "Now plain"}]))
		self.assertEqual(len(mc.cards), 0)

	def test_saved_unchanged_carousel_does_not_post_to_meta(self):
		mc = self._stored(META_MEDIA_CAROUSEL)
		mc.load_doc_before_save()  # what save() does before validate
		self.assertFalse(mc._has_content_changed())
		mc.cards[0].button_1_text = "Changed"
		self.assertTrue(mc._has_content_changed())

	# ── send components ──────────────────────────────────────────────────────
	def test_send_components_per_type(self):
		from excom.excom.api import chat

		future = FUTURE_MS()
		lto = self._stored(META_LTO)
		orig = chat._header_media
		chat._header_media = lambda account, url: {"id": "MEDIA-" + url.rsplit("/", 1)[-1]}
		self.addCleanup(setattr, chat, "_header_media", orig)
		comps = chat._build_template_components(lto, ["Pablo", "CARIBE25"], "/files/h.jpg", ["n3mtql"], account=self.acc,
			extras={"coupon_code": "CARIBE25", "offer_expiration_ms": future})
		self.assertEqual([c["type"] for c in comps], ["header", "body", "limited_time_offer", "button", "button"])
		self.assertIn({"type": "limited_time_offer", "parameters": [{"type": "limited_time_offer", "limited_time_offer": {"expiration_time_ms": future}}]}, comps)
		self.assertIn({"type": "button", "sub_type": "copy_code", "index": "0", "parameters": [{"type": "coupon_code", "coupon_code": "CARIBE25"}]}, comps)
		self.assertIn({"type": "button", "sub_type": "url", "index": "1", "parameters": [{"type": "text", "text": "n3mtql"}]}, comps)

		mc = self._stored(META_MEDIA_CAROUSEL)
		cards = [{"header_media": f"/files/c{i}.jpg", "body": [f"{i}0%"], "buttons": [f"more-{i}", f"slug-{i}"]} for i in range(3)]
		cache = {}
		comps = chat._build_template_components(mc, ["Pablo", "30OFF"], account=self.acc, extras={"cards": cards}, media_cache=cache)
		car = comps[-1]
		self.assertEqual(car["type"], "carousel")
		self.assertEqual(car["cards"][1], {"card_index": 1, "components": [
			{"type": "header", "parameters": [{"type": "image", "image": {"id": "MEDIA-c1.jpg"}}]},
			{"type": "body", "parameters": [{"type": "text", "text": "10%"}]},
			{"type": "button", "sub_type": "quick_reply", "index": 0, "parameters": [{"type": "payload", "payload": "more-1"}]},
			{"type": "button", "sub_type": "url", "index": 1, "parameters": [{"type": "text", "text": "slug-1"}]}]})
		self.assertEqual(set(cache), {"/files/c0.jpg", "/files/c1.jpg", "/files/c2.jpg"})

		pc = self._stored(META_PRODUCT_CAROUSEL)
		comps = chat._build_template_components(pc, ["Pablo"], account=self.acc, extras={"cards": [{"product_retailer_id": f"SKU{i}"} for i in range(4)]})
		self.assertEqual(len(comps[-1]["cards"]), 4)  # created with 2, sent with up to 10
		self.assertEqual(comps[-1]["cards"][3]["components"][0]["parameters"][0], {"type": "product", "product": {"product_retailer_id": "SKU3", "catalog_id": CATALOG}})

		cat = self._stored(META_CATALOG)
		comps = chat._build_template_components(cat, ["100", "400"], account=self.acc, extras={"thumbnail_product_retailer_id": "2lc20305pt"})
		self.assertEqual(comps[-1], {"type": "button", "sub_type": "CATALOG", "index": "0", "parameters": [{"type": "action", "action": {"thumbnail_product_retailer_id": "2lc20305pt"}}]})

		mpm = self._stored(META_MPM)
		comps = chat._build_template_components(mpm, ["Pablo", "10OFF"], account=self.acc, extras={"sections": [
			{"title": "Popular Bundles", "product_retailer_ids": ["2lc20305pt", "nseiw1x3ch"]}, {"title": "Premium", "product_retailer_ids": ["n6k6x0y7oe"]}]})
		self.assertEqual(comps[-1]["parameters"][0]["action"]["sections"][1], {"title": "Premium", "product_items": [{"product_retailer_id": "n6k6x0y7oe"}]})
		self.assertEqual(comps[-1]["parameters"][0]["action"]["thumbnail_product_retailer_id"], "2lc20305pt")

		spm = self._stored(META_SPM)
		comps = chat._build_template_components(spm, {"code": "25OFF", "percent": "25%"}, account=self.acc, extras={"product_retailer_id": "nqryix03ez"})
		self.assertEqual(comps[0], {"type": "header", "parameters": [{"type": "product", "product": {"product_retailer_id": "nqryix03ez", "catalog_id": CATALOG}}]})
		self.assertEqual(comps[1]["parameters"][0], {"type": "text", "text": "25OFF", "parameter_name": "code"})

		vc = self._stored(META_VOICE)
		self.assertEqual(chat._build_template_components(vc, [], account=self.acc), [])  # the call button needs nothing by default
		comps = chat._build_template_components(vc, [], account=self.acc, extras={"voice_call": {"ttl_minutes": 100, "payload": "lead-42"}})
		self.assertEqual(comps[0]["sub_type"], "voice_call")
		self.assertEqual(comps[0]["parameters"], [{"type": "ttl_minutes", "ttl_minutes": 100}, {"type": "payload", "payload": "lead-42"}])

	def test_send_refuses_missing_values_before_meta(self):
		from excom.excom.api import chat

		lto, mc, mpm, spm = self._stored(META_LTO), self._stored(META_MEDIA_CAROUSEL), self._stored(META_MPM), self._stored(META_SPM)
		orig = chat._header_media
		chat._header_media = lambda account, url: {"id": "MEDIA"}
		self.addCleanup(setattr, chat, "_header_media", orig)
		cases = [
			(lto, ["P", "C"], {"offer_expiration_ms": FUTURE_MS()}, ["x"]),  # no coupon code
			(lto, ["P", "C"], {"coupon_code": "C" * 16, "offer_expiration_ms": FUTURE_MS()}, ["x"]),  # LTO code ≤ 15
			(lto, ["P", "C"], {"coupon_code": "C"}, ["x"]),  # countdown without an end
			(lto, ["P", "C"], {"coupon_code": "C", "offer_expiration_ms": 1698562800000}, ["x"]),  # past
			(mc, ["P", "C"], {"cards": [{"header_media": "/files/a.jpg", "body": ["1"], "buttons": ["", "s"]}]}, []),  # 3 cards approved
			(mc, ["P", "C"], {"cards": [{"header_media": "/files/a.jpg", "buttons": ["", "s"]}] * 3}, []),  # card body value
			(mc, ["P", "C"], {"cards": [{"body": ["1"], "buttons": ["", "s"]}] * 3}, []),  # card media
			(mpm, ["P", "C"], {}, []),  # sections
			(mpm, ["P", "C"], {"sections": [{"title": "A", "product_retailer_ids": [str(i) for i in range(31)]}]}, []),
			(mpm, ["P", "C"], {"sections": [{"title": f"S{i}", "product_retailer_ids": ["x"]} for i in range(11)]}, []),
			(spm, {"code": "a", "percent": "b"}, {}, []),  # product
		]
		for doc, variables, extras, urls in cases:
			with self.assertRaises(frappe.ValidationError, msg=json.dumps(extras)[:80]):
				chat._build_template_components(doc, variables, "/files/h.jpg", urls, account=self.acc, extras=extras)
		no_catalog = frappe._dict(name="x", wa_catalog_id="")
		with self.assertRaises(frappe.ValidationError):
			chat._build_template_components(spm, {"code": "a", "percent": "b"}, account=no_catalog, extras={"product_retailer_id": "p"})

	def test_send_to_thread_with_extras_and_preview(self):
		from excom.excom.api import chat
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
		from excom.excom.services.thread_service import upsert_thread

		self._stored(dict(META_CATALOG, name="qa_p4_coupon", id="P4CPN", components=[
			{"type": "BODY", "text": "Use code {{1}}", "example": {"body_text": [["WINTER25"]]}},
			{"type": "BUTTONS", "buttons": [{"type": "COPY_CODE", "example": "WINTER25"}]}]))
		name = frappe.db.get_value("WhatsApp Templates", {"actual_name": "qa_p4_coupon"}, "name")
		identity = resolve_identity_internal(phone="919900400001", channel="whatsapp", channel_user_id="919900400001", display_name="QA")
		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		with self.assertRaises(frappe.ValidationError):
			chat.send_template_to_thread(thread, name, variables=json.dumps(["WINTER25"]))  # no coupon code
		with self.assertRaises(frappe.ValidationError):
			chat.send_template_to_thread(thread, name, variables=json.dumps(["WINTER25"]), extras="[1]")  # not an object
		res = chat.send_template_to_thread(thread, name, variables=json.dumps(["WINTER25"]), extras=json.dumps({"coupon_code": "WINTER25"}))
		sent = self.calls[-1][1]["template"]["components"]
		self.assertIn({"type": "button", "sub_type": "copy_code", "index": "0", "parameters": [{"type": "coupon_code", "coupon_code": "WINTER25"}]}, sent)
		self.assertIn("WINTER25", frappe.db.get_value("Excom Message", res["message_name"], "content_text"))

	def test_previews(self):
		from excom.excom.api.chat import _build_template_preview

		mc = self._stored(META_MEDIA_CAROUSEL)
		text = _build_template_preview(mc, ["Pablo", "30OFF"], extras={"cards": [{"body": ["10%"]}, {"body": ["20%"]}, {"body": ["30%"]}]})
		self.assertIn("Carousel: 3 card(s)", text)
		self.assertIn("2. 20% off this one", text)
		mpm = self._stored(META_MPM)
		self.assertIn("2 product(s) in 1 section(s)", _build_template_preview(mpm, ["P", "C"], extras={"sections": [{"title": "A", "product_retailer_ids": ["a", "b"]}]}))
		self.assertIn("Asks permission to call", _build_template_preview(self._stored(META_CPR), ["ON-1"]))

	def test_broadcast_checks_extras_before_sending(self):
		self._stored(META_LTO)
		template = frappe.db.get_value("WhatsApp Templates", {"actual_name": "qa_p4_lto"}, "name")
		bc = frappe.new_doc("Excom Broadcast")
		bc.update({"channel": "WhatsApp", "wa_template": template, "wa_channel_account": self.acc.name})
		bc.wa_template_extras = json.dumps({"offer_expiration_ms": FUTURE_MS()})
		with self.assertRaises(frappe.ValidationError):
			bc._validate_template_extras()  # coupon code missing
		bc.wa_template_extras = json.dumps({"coupon_code": "CARIBE25", "offer_expiration_ms": FUTURE_MS()})
		bc._validate_template_extras()
		bc.scheduled_at = frappe.utils.add_days(frappe.utils.now_datetime(), 5)
		with self.assertRaises(frappe.ValidationError):
			bc._validate_template_extras()  # the offer ends before the broadcast starts

	# ── template library ─────────────────────────────────────────────────────
	def _lib_http(self, get_body=None):
		seen = {"get": [], "post": []}
		library = {"data": [{"name": "delivery_update_1", "language": "en_US", "category": "UTILITY", "topic": "ORDER_MANAGEMENT",
			"usecase": "DELIVERY_UPDATE", "industry": ["E_COMMERCE"], "body": "Hi {{1}}, your order {{2}} is on its way.", "body_params": ["Jim", "123"],
			"buttons": [{"type": "URL", "text": "Track", "url": "https://www.example.com/"}], "id": "714701"}],
			"paging": {"cursors": {"after": "CUR2"}, "next": "https://graph/next"}}
		created = {"name": "qa_p4_delivery", "language": "en_US", "category": "UTILITY", "status": "APPROVED", "id": "P4LIB",
			"library_template_name": "delivery_update_1", "components": [
				{"type": "BODY", "text": "Hi {{1}}, your order {{2}} is on its way.", "example": {"body_text": [["Jim", "123"]]}},
				{"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Track", "url": "https://www.example.com/{{1}}", "example": ["https://www.example.com/order_update"]}]}]}

		def fake_get(url, params=None, headers=None, timeout=None):
			seen["get"].append((url, params))
			return FakeResp(200, library if url.endswith("/message_template_library") else (get_body if get_body is not None else created))

		def fake_post(url, json=None, headers=None, timeout=None):
			seen["post"].append((url, json))
			return FakeResp(200, {"id": "P4LIB", "status": "APPROVED", "category": "UTILITY"})

		orig = (lib.http_requests.get, lib.http_requests.post)
		lib.http_requests.get, lib.http_requests.post = fake_get, fake_post
		self.addCleanup(lambda: (setattr(lib.http_requests, "get", orig[0]), setattr(lib.http_requests, "post", orig[1])))
		return seen

	def test_library_search(self):
		seen = self._lib_http()
		res = lib.search(account_name=self.acc.name, search="delivery", topic="ORDER_MANAGEMENT", language="en_US")
		url, params = seen["get"][0]
		self.assertTrue(url.endswith("/v26.0/message_template_library"))
		self.assertEqual((params["search"], params["topic"], params["language"]), ("delivery", "ORDER_MANAGEMENT", "en_US"))
		self.assertNotIn("usecase", params)
		self.assertEqual(res["after"], "CUR2")
		self.assertEqual(res["data"][0]["button_inputs"], [{"index": 0, "type": "URL", "text": "Track", "default": "https://www.example.com/"}])
		with self.assertRaises(frappe.ValidationError):
			lib.search(account_name=self.acc.name, topic="NOT_A_TOPIC")

	def test_library_create_stores_without_posting_twice(self):
		seen = self._lib_http()
		meta_posts = []
		orig = wt.make_post_request
		wt.make_post_request = lambda *a, **k: meta_posts.append(a) or {"id": "dup", "status": "PENDING"}
		self.addCleanup(setattr, wt, "make_post_request", orig)
		res = lib.create("delivery_update_1", "QA P4 Delivery", "en_US", account_name=self.acc.name,
			button_inputs=[{"type": "URL", "url": {"base_url": "https://www.example.com/{{1}}", "url_suffix_example": "https://www.example.com/order_update"}}])
		url, payload = seen["post"][0]
		self.assertTrue(url.endswith(f"/{WABA}/message_templates"))
		self.assertEqual(payload, {"name": "qa_p4_delivery", "language": "en_US", "category": "UTILITY", "library_template_name": "delivery_update_1",
			"library_template_button_inputs": [{"type": "URL", "url": {"base_url": "https://www.example.com/{{1}}", "url_suffix_example": "https://www.example.com/order_update"}}]})
		self.assertEqual(len(seen["post"]), 1)
		self.assertEqual(meta_posts, [])  # stored with hooks off: after_insert never posted it again
		doc = frappe.get_doc("WhatsApp Templates", res["name"])
		self.assertEqual((doc.status, doc.category, doc.library_template_name, doc.id), ("APPROVED", "UTILITY", "delivery_update_1", "P4LIB"))
		self.assertEqual(doc.buttons[0].website_url, "https://www.example.com/{{1}}")
		self.assertTrue(frappe.db.exists("WhatsApp Template Linked Account", {"parent": doc.name, "channel_account": self.acc.name}))
		with self.assertRaises(frappe.ValidationError):
			lib.create("delivery_update_1", "qa_p4_delivery", "en_US", account_name=self.acc.name)  # name taken

	def test_library_create_refusals(self):
		self._lib_http()
		for kwargs in (
			{"category": "MARKETING"},
			{"name": "Bad-Name!"},
			{"button_inputs": [{"type": "URL", "url": {"base_url": "example.com"}}]},
			{"button_inputs": [{"type": "URL", "url": {"base_url": "https://e.com/{{1}}"}}]},  # needs a suffix example
			{"button_inputs": [{"type": "PHONE_NUMBER", "phone_number": "call me"}]},
			{"button_inputs": [{"type": "NOPE"}]},
		):
			args = {"library_template_name": "delivery_update_1", "name": "qa_p4_refused", "language": "en_US", "account_name": self.acc.name, **kwargs}
			with self.assertRaises(frappe.ValidationError, msg=str(kwargs)):
				lib.create(**args)

	def test_library_endpoints_need_an_admin(self):
		from excom.excom.api import chat

		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				chat.search_template_library(search="x")
			with self.assertRaises(frappe.PermissionError):
				chat.create_template_from_library("delivery_update_1", "qa_p4_x", "en_US")
		finally:
			frappe.set_user("Administrator")
