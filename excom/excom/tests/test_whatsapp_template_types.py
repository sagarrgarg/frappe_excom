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
AGENT = "qa-p4-agent@example.com"
# Real Meta handles and sample links are ~190+ characters: longer than a Data field (140)
LONG_HANDLE = "4::aW1hZ2UvanBlZw==:ARZ" + "x" * 180
LONG_LINK = "https://scontent.whatsapp.net/v/t61.29466-34/" + "a" * 160 + ".jpg?ccb=1-7&_nc_sid=8b1bef&oh=01_Q5Aa1"
LONG_URL = "https://shop.example/rare-succulents/" + "p" * 260 + "/{{1}}"


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
		frappe.db.delete("Excom Subscriber", {"omni_identity": ["in", ids]})
		frappe.db.delete("Omni Identity", {"name": ["in", ids]})
	frappe.db.delete("Excom Message", {"account": account})
	frappe.db.delete("Excom Thread", {"account": account})
	for bc in frappe.get_all("Excom Broadcast", filters={"broadcast_name": "QA P4"}, pluck="name"):
		frappe.db.delete("Excom Broadcast Log", {"broadcast": bc})
		frappe.db.delete("Comment", {"reference_doctype": "Excom Broadcast", "reference_name": bc})
		frappe.db.delete("Excom Broadcast", bc)
	frappe.db.delete("Excom Subscriber List", {"list_name": "QA P4 List"})
	for f in frappe.get_all("File", filters={"file_name": ["like", "qa-p4-%"]}, pluck="name"):
		frappe.delete_doc("File", f, force=True, ignore_permissions=True)


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
			{"type": "HEADER", "format": "IMAGE", "example": {"header_handle": [LONG_LINK + str(i)]}},
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
		if not frappe.db.exists("User", AGENT):
			u = frappe.get_doc({"doctype": "User", "email": AGENT, "first_name": "QA P4 Agent", "send_welcome_email": 0})
			u.flags.ignore_permissions = True
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		_cleanup(cls.acc.name)
		if frappe.db.exists("User", AGENT):
			frappe.delete_doc("User", AGENT, force=True, ignore_permissions=True)
		frappe.db.delete("Excom Channel Account", cls.acc.name)
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", cls.acc.name)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.calls = []
		self._orig_post = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (self.calls.append((url, kw.get("json"))) or FakeResp(200, {"messages": [{"id": f"wamid.P4OUT{len(self.calls)}"}]}))
		# template create / update on Meta (WhatsApp Templates controller)
		self.meta_posts = []
		self._orig_meta_post = wt.make_post_request
		wt.make_post_request = lambda url, headers=None, data=None: (self.meta_posts.append((url, json.loads(data) if isinstance(data, str) else data))
			or {"id": f"P4NEW{len(self.meta_posts)}", "status": "PENDING"})

	def tearDown(self):
		ws.http_requests.post = self._orig_post
		wt.make_post_request = self._orig_meta_post
		frappe.set_user("Administrator")
		frappe.db.rollback()
		_cleanup(self.acc.name)
		frappe.db.commit()

	# ── helpers ──────────────────────────────────────────────────────────────
	def _template(self, body="Hi {{1}}", buttons=(), cards=(), **fields):
		doc = frappe.new_doc("WhatsApp Templates")
		self._n = getattr(self, "_n", 0) + 1
		doc.update({"template_name": f"qa p4 t{self._n}", "template": body, "language": "en", "language_code": "en_US",
			"category": "MARKETING", "whatsapp_account": self.acc.name, **fields})
		for b in buttons:
			doc.append("buttons", b)
		for c in cards:
			doc.append("cards", c)
		return doc

	def _check(self, doc):
		"""The controller's validate, then Frappe's own mandatory / select / length checks, without a DB write."""
		self._fake_uploads(doc)
		doc.run_method("validate")
		doc._validate()

	def _fake_uploads(self, doc):
		"""Resumable upload stubbed with a real-length handle (no file on disk, no Meta)."""
		doc._start_upload = lambda url: "upload:session"
		doc._upload_bytes = lambda session, url: LONG_HANDLE

	def _insert(self, doc):
		"""The full insert: validate, Frappe's checks, the DB write, then the create post (stubbed) in after_insert."""
		self._fake_uploads(doc)
		doc.insert(ignore_permissions=True)
		return doc

	def _stored(self, meta):
		wt._store_templates({"data": [json.loads(json.dumps(meta))]}, [self.acc.name])
		return frappe.get_doc("WhatsApp Templates", {"actual_name": meta["name"]})

	def _media_cards(self, n=2, body=True):
		return [{"card_body": "{{1}} off this one" if body else "", "body_variable_samples": '["20%"]', "button_1_text": "More like this",
			"button_2_text": "Shop", "button_2_value": "https://shop.example/{{1}}", "button_2_example": "BLUE_ELF",
			"header_handle": f"{LONG_HANDLE}{i}"} for i in range(n)]

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
		self.assertEqual(card[0], {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": [LONG_HANDLE + "0"]}})
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
		doc._upload_bytes = lambda session, url: f"{LONG_HANDLE}{session}"
		doc._upload_card_samples()
		self.assertEqual(len(uploads), 2)
		self.assertTrue(all(c.header_handle.startswith(LONG_HANDLE + "upload:") for c in doc.cards))
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
		self.assertEqual((mc.cards[0].button_2_value, mc.cards[0].button_2_example, mc.cards[2].header_sample_url),
			("https://www.luckyshrub.com/rare-succulents/{{1}}", "BLUE_ELF", LONG_LINK + "2"))
		self.assertFalse(any(c.header_handle for c in mc.cards), "Meta's preview link must never be stored as an upload handle")
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
		# header and buttons Meta no longer has are gone too (no stale Copy Code row)
		self.assertEqual((lto.header_type or "", len(lto.buttons)), ("", 0))
		self.assertFalse(frappe.db.exists("WhatsApp Button", {"parent": lto.name}))
		mc = self._stored(dict(META_MEDIA_CAROUSEL, components=[{"type": "BODY", "text": "Now plain"}]))
		self.assertEqual(len(mc.cards), 0)
		cat = self._stored(dict(META_CATALOG, components=[{"type": "BODY", "text": "No footer now"}]))
		self.assertEqual((cat.footer or "", cat.template_type), ("", "Standard"))

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
		after_insert_ran = []
		orig = wt.WhatsAppTemplates.after_insert
		wt.WhatsAppTemplates.after_insert = lambda doc: after_insert_ran.append(doc.name)
		self.addCleanup(setattr, wt.WhatsAppTemplates, "after_insert", orig)
		res = lib.create("delivery_update_1", "QA P4 Delivery", "en_US", account_name=self.acc.name,
			button_inputs=[{"type": "URL", "url": {"base_url": "https://www.example.com/{{1}}", "url_suffix_example": "https://www.example.com/order_update"}}])
		url, payload = seen["post"][0]
		self.assertTrue(url.endswith(f"/{WABA}/message_templates"))
		self.assertEqual(payload, {"name": "qa_p4_delivery", "language": "en_US", "category": "UTILITY", "library_template_name": "delivery_update_1",
			"library_template_button_inputs": [{"type": "URL", "url": {"base_url": "https://www.example.com/{{1}}", "url_suffix_example": "https://www.example.com/order_update"}}]})
		self.assertEqual(len(seen["post"]), 1)
		self.assertEqual(after_insert_ran, [])  # stored with hooks off: after_insert (the create post) never ran
		self.assertEqual(self.meta_posts, [])
		self.assertNotIn("note", res)
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

		frappe.set_user(AGENT)  # a signed-in agent, not Guest: the admin check itself must refuse
		try:
			with self.assertRaises(frappe.PermissionError):
				chat.search_template_library(search="x")
			with self.assertRaises(frappe.PermissionError):
				chat.create_template_from_library("delivery_update_1", "qa_p4_x", "en_US")
		finally:
			frappe.set_user("Administrator")

	# ── review fixes ─────────────────────────────────────────────────────────
	def _broadcast(self, template_name, extras, n=2, **fields):
		"""A queued, submitted broadcast to n new people (rows written directly, as test_whatsapp_platform does)."""
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

		lst = frappe.get_doc({"doctype": "Excom Subscriber List", "list_name": "QA P4 List"}).insert(ignore_permissions=True)
		for i in range(n):
			phone = f"9199004100{i:02d}"
			oi = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone, display_name="QA " + phone)
			frappe.get_doc({"doctype": "Excom Subscriber", "subscriber_list": lst.name, "omni_identity": oi, "status": "Subscribed"}).insert(ignore_permissions=True)
		bc = frappe.get_doc({"doctype": "Excom Broadcast", "broadcast_name": "QA P4", "subscriber_list": lst.name, "channel": "WhatsApp",
			"wa_template": template_name, "wa_channel_account": self.acc.name, "wa_template_extras": json.dumps(extras), **fields})
		bc.name = "QA-P4-BROADCAST-" + frappe.generate_hash(length=6)
		bc.db_insert()
		frappe.db.set_value("Excom Broadcast", bc.name, {"docstatus": 1, "status": "Queued"})
		return bc.name

	def _fake_header_media(self):
		from excom.excom.api import chat

		uploads = []
		orig = chat._header_media
		chat._header_media = lambda account, url: (uploads.append(url) or {"id": "MEDIA-" + url.rsplit("/", 1)[-1]})
		self.addCleanup(setattr, chat, "_header_media", orig)
		return uploads

	def test_real_length_handles_and_links_go_through_insert(self):
		"""C1: Meta handles (~190+) and long URLs fit; the create is posted only after Frappe's own checks."""
		self.assertGreater(len(LONG_HANDLE), 190)
		cards = self._media_cards(2)
		for i, c in enumerate(cards):
			c.pop("header_handle")
			c.update({"header_sample": f"/files/qa-p4-card{i}.jpg", "button_2_value": LONG_URL, "button_2_example": "BLUE_ELF-" + "e" * 200})
		doc = self._template("Rare succulents {{1}}", template_type="Media Carousel", card_header_format="IMAGE",
			card_button_1_type="Quick Reply", card_button_2_type="Visit Website", cards=cards, body_variable_samples=json.dumps(["Pablo"]))
		self._insert(doc)
		self.assertEqual(len(self.meta_posts), 1)
		posted = self.meta_posts[0][1]["components"][-1]["cards"][0]["components"]
		self.assertEqual(posted[0]["example"]["header_handle"], [LONG_HANDLE])
		self.assertEqual(posted[2]["buttons"][1]["url"], LONG_URL)
		stored = frappe.get_doc("WhatsApp Templates", doc.name)
		self.assertEqual([c.header_handle for c in stored.cards], [LONG_HANDLE, LONG_HANDLE])
		self.assertEqual((stored.cards[0].button_2_value, stored.id), (LONG_URL, "P4NEW1"))

	def test_existing_template_posts_only_content_changes_after_local_checks(self):
		doc = self._insert(self._template("Hello {{1}}", category="UTILITY", body_variable_samples=json.dumps(["Pablo"]),
			buttons=[{"button_type": "Quick Reply", "button_label": "Yes"}]))
		self.assertEqual(len(self.meta_posts), 1)  # the create
		doc = frappe.get_doc("WhatsApp Templates", doc.name)
		doc.save(ignore_permissions=True)  # Standard template with the new fields, nothing changed
		self.assertEqual(len(self.meta_posts), 1)
		doc.template = "Hello again {{1}}"
		doc.lto_text = "x" * 200  # over Frappe's 140 for a Data field: refused after validate
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		self.assertEqual(len(self.meta_posts), 1, "a local failure must never leave Meta changed")
		doc = frappe.get_doc("WhatsApp Templates", doc.name)
		doc.template = "Hello again {{1}}"
		doc.save(ignore_permissions=True)
		self.assertEqual(len(self.meta_posts), 2)
		url, payload = self.meta_posts[1]
		self.assertTrue(url.endswith("/P4NEW1"))
		self.assertEqual(payload["components"][0]["text"], "Hello again {{1}}")

	def test_synced_cards_keep_local_samples_and_need_an_upload_to_change(self):
		"""M1: a synced card has only Meta's preview link; a content edit needs the sample again."""
		mc = self._stored(META_MEDIA_CAROUSEL)
		mc.flags.ignore_mandatory = True  # synced rows have no Language link (only language_code)
		mc.save(ignore_permissions=True)
		self.assertEqual(self.meta_posts, [])  # unchanged: never posted
		mc.template = "Rare succulents, {{1}}! Code {{2}}."
		self._fake_uploads(mc)
		with self.assertRaises(wtt.TemplateTypeError) as err:
			mc.save(ignore_permissions=True)
		self.assertIn("attach the sample image again", str(err.exception))
		self.assertEqual(self.meta_posts, [])
		mc = frappe.get_doc("WhatsApp Templates", mc.name)
		mc.flags.ignore_mandatory = True
		for i, c in enumerate(mc.cards):
			c.header_sample = f"/files/qa-p4-card{i}.jpg"
		mc.template = "Rare succulents, {{1}}! Code {{2}}."
		self._fake_uploads(mc)
		mc.save(ignore_permissions=True)
		self.assertEqual(len(self.meta_posts), 1)
		self.assertEqual(self.meta_posts[0][1]["components"][-1]["cards"][0]["components"][0]["example"], {"header_handle": [LONG_HANDLE]})
		# a re-sync with the same card content keeps the local samples and handles
		mc = self._stored(META_MEDIA_CAROUSEL)
		self.assertEqual([(c.header_sample, c.header_handle) for c in mc.cards], [(f"/files/qa-p4-card{i}.jpg", LONG_HANDLE) for i in range(3)])
		self.assertTrue(all(c.header_sample_url for c in mc.cards))
		# Meta changed card 2: its local sample no longer matches and is dropped
		changed = json.loads(json.dumps(META_MEDIA_CAROUSEL))
		changed["components"][1]["cards"][1]["components"][1]["text"] = "{{1}} off, today only"
		mc = self._stored(changed)
		self.assertEqual([bool(c.header_sample) for c in mc.cards], [True, False, True])

	def test_named_card_bodies(self):
		from excom.excom.api import chat

		self._fake_header_media()
		cards = self._media_cards(2)
		for c in cards:
			c.update({"card_body": "{{discount}} off this one", "body_variable_samples": '["20%"]'})
		doc = self._template("Rare succulents for {{name}}", template_type="Media Carousel", card_header_format="IMAGE",
			card_button_1_type="Quick Reply", card_button_2_type="Visit Website", cards=cards, body_variable_samples=json.dumps(["Pablo"]))
		self._check(doc)
		self.assertTrue(doc.uses_named_parameters())
		self.assertEqual(doc.meta_components()[-1]["cards"][0]["components"][1]["example"],
			{"body_text_named_params": [{"param_name": "discount", "example": "20%"}]})
		self._insert(doc)
		self.assertEqual(self.meta_posts[-1][1]["parameter_format"], "NAMED")
		comps = chat._build_template_components(doc, {"name": "Pablo"}, account=self.acc, extras={"cards": [
			{"header_media": "/files/a.jpg", "body": {"discount": "15%"}, "buttons": ["", "s"]}] * 2})
		self.assertEqual(comps[0]["parameters"], [{"type": "text", "text": "Pablo", "parameter_name": "name"}])
		self.assertEqual(comps[-1]["cards"][0]["components"][1], {"type": "body", "parameters": [{"type": "text", "text": "15%", "parameter_name": "discount"}]})
		# named and numbered variables mixed across body and cards are refused
		mixed = self._template("Hi {{1}}", template_type="Media Carousel", card_header_format="IMAGE",
			card_button_1_type="Quick Reply", card_button_2_type="Visit Website", cards=cards, body_variable_samples=json.dumps(["Pablo"]))
		with self.assertRaises(wtt.TemplateTypeError):
			self._check(mixed)
		# a card body variable needs a real sample (no made-up "sample1")
		nosample = self._media_carousel()
		nosample.body_variable_samples = json.dumps(["Pablo"])
		for c in nosample.cards:
			c.body_variable_samples = None
		with self.assertRaises(wtt.TemplateTypeError):
			self._check(nosample)

	def test_limited_time_offer_without_copy_code(self):
		from excom.excom.api import chat

		doc = self._template("Good news, {{1}}!", template_type="Limited-Time Offer", lto_text="Expiring offer!", lto_has_expiration=1,
			body_variable_samples=json.dumps(["Pablo"]), buttons=[{"button_type": "Visit Website", "button_label": "Book now!",
				"website_url": "https://a.example/offers?code={{1}}", "url_type": "Dynamic", "example_url": "https://a.example/offers?ref=n3mtql"}])
		self._check(doc)
		future = FUTURE_MS()
		comps = chat._build_template_components(doc, ["Pablo"], button_urls=["n3mtql"], account=self.acc, extras={"offer_expiration_ms": future})
		self.assertEqual([c["type"] for c in comps], ["body", "limited_time_offer", "button"])
		self.assertEqual(comps[-1], {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": "n3mtql"}]})

	def test_copy_code_after_other_buttons_keeps_its_index(self):
		from excom.excom.api import chat

		doc = self._template("Use {{1}}", body_variable_samples=json.dumps(["W"]), buttons=[
			{"button_type": "Quick Reply", "button_label": "Stop"},
			{"button_type": "Visit Website", "button_label": "Shop", "website_url": "https://x.example/{{1}}", "url_type": "Dynamic", "example_url": "https://x.example/a"},
			{"button_type": "Copy Code", "example_code": "WINTER25"}])
		self._check(doc)
		self.assertEqual(doc.buttons[2].button_label, wtt.COPY_CODE_DEFAULT_LABEL)
		comps = chat._build_template_components(doc, ["W"], button_urls=["a"], account=self.acc, extras={"coupon_code": "WINTER25"})
		self.assertIn({"type": "button", "sub_type": "copy_code", "index": "2", "parameters": [{"type": "coupon_code", "coupon_code": "WINTER25"}]}, comps)
		self.assertIn({"type": "button", "sub_type": "url", "index": "1", "parameters": [{"type": "text", "text": "a"}]}, comps)

	def test_product_carousel_ignores_hidden_button_2_and_voice_label_defaults(self):
		pc = self._template("New this week, {{1}}!", template_type="Product Carousel", card_button_1_type="SPM", card_button_2_type="Quick Reply",
			body_variable_samples=json.dumps(["Pablo"]), cards=[{"button_1_text": "View"}, {"button_1_text": "View"}])
		self._check(pc)
		self.assertIsNone(pc.card_button_2_type)
		self.assertEqual(pc.meta_components()[-1]["cards"][0]["components"][1]["buttons"], [{"type": "SPM", "text": "View"}])
		vc = self._template("Call us", category="UTILITY", buttons=[{"button_type": "Voice Call"}])
		self._check(vc)
		self.assertEqual(vc.buttons[0].button_label, "Call Now")
		with self.assertRaises(frappe.ValidationError):
			self._check(self._template("Hi", buttons=[{"button_type": "Quick Reply"}]))  # other buttons still need their text

	def test_malformed_extras_are_clear_errors(self):
		from excom.excom.api import chat

		self._fake_header_media()
		mpm, mc, vc = self._stored(META_MPM), self._stored(META_MEDIA_CAROUSEL), self._stored(META_VOICE)
		lto = self._stored(META_LTO)
		cases = [
			(mpm, ["P", "C"], {"sections": "SKU1"}),
			(mpm, ["P", "C"], {"sections": ["SKU1"]}),
			(mpm, ["P", "C"], {"sections": [{"title": "A", "product_retailer_ids": "SKU1"}]}),
			(mc, ["P", "C"], {"cards": ["a", "b", "c"]}),
			(mc, ["P", "C"], {"cards": [{"header_media": "/files/a.jpg", "body": "20%", "buttons": ["", "s"]}] * 3}),
			(mc, ["P", "C"], {"cards": [{"header_media": "/files/a.jpg", "body": ["1"], "buttons": "s"}] * 3}),
			(vc, [], {"voice_call": "call me"}),
			(vc, [], {"voice_call": {"ttl_minutes": "abc"}}),
			(lto, ["P", "C"], {"coupon_code": "C", "offer_expires_at": "not a date"}),
		]
		for doc, variables, extras in cases:
			with self.assertRaises(wtt.TemplateTypeError, msg=json.dumps(extras)[:80]):
				chat._build_template_components(doc, variables, "/files/h.jpg", ["x"], account=self.acc, extras=extras)
		with self.assertRaises(wtt.TemplateTypeError):
			wtt.send_components(vc, ["not", "an", "object"])

	def test_previews_fill_named_card_values_in_placeholder_order(self):
		doc = self._template("Hi {{name}}", template_type="Media Carousel", card_header_format="IMAGE", card_button_1_type="Quick Reply",
			cards=[{"card_body": "{{size}} for {{price}}"}, {"card_body": "{{size}} for {{price}}"}])
		lines = wtt.preview_lines(doc, {"cards": [{"body": {"price": "Rs 99", "size": "Large"}}] * 2})
		self.assertIn("  1. Large for Rs 99", lines)

	def test_template_list_returns_cards(self):
		from excom.excom.api import chat

		self._stored(META_MEDIA_CAROUSEL)
		rows = chat.get_whatsapp_templates(search="qa_p4_media", whatsapp_account=self.acc.name)
		row = next(r for r in rows if r.actual_name == "qa_p4_media_carousel")
		self.assertEqual((row.template_type, len(row.cards)), ("Media Carousel", 3))
		self.assertEqual((row.cards[0].card_body, row.cards[0].button_2_value), ("{{1}} off this one", "https://www.luckyshrub.com/rare-succulents/{{1}}"))

	def test_broadcast_uploads_card_media_once_per_run(self):
		from excom.excom.services import broadcast_service as bs

		uploads = self._fake_header_media()
		mc = self._stored(META_MEDIA_CAROUSEL)
		cards = [{"header_media": f"/files/c{i}.jpg", "body": [f"{i}0%"], "buttons": ["", f"slug-{i}"]} for i in range(3)]
		name = self._broadcast(mc.name, {"cards": cards}, n=3, wa_template_variables=json.dumps(["Pablo", "30OFF"]))
		bs.execute_broadcast(name)
		self.assertEqual(frappe.db.get_value("Excom Broadcast", name, ["status", "sent_count"]), ("Completed", 3))
		self.assertEqual(sorted(uploads), ["/files/c0.jpg", "/files/c1.jpg", "/files/c2.jpg"])  # 3 people, each card uploaded once
		sent = [c[1] for c in self.calls if (c[1] or {}).get("template")]
		self.assertEqual(len(sent), 3)
		self.assertEqual(sent[2]["template"]["components"][-1]["cards"][1]["components"][0]["parameters"][0]["image"], {"id": "MEDIA-c1.jpg"})

	def test_offer_that_ends_mid_run_pauses_the_broadcast(self):
		"""M4: one clear pause, not one failure per remaining recipient."""
		from excom.excom.services import broadcast_service as bs

		self._fake_header_media()
		lto = self._stored(META_LTO)
		name = self._broadcast(lto.name, {"coupon_code": "CARIBE25", "offer_expiration_ms": FUTURE_MS()}, n=3,
			wa_template_variables=json.dumps(["Pablo", "CARIBE25"]), wa_header_media="/files/h.jpg", wa_button_urls=json.dumps(["n3mtql"]))
		orig = bs._send_whatsapp_to_subscriber

		def send_then_expire(broadcast, oi):
			ok = orig(broadcast, oi)
			# the offer ends while the broadcast waits (wave pause / messaging limit)
			broadcast.wa_template_extras = json.dumps({"coupon_code": "CARIBE25", "offer_expiration_ms": int(time.time() * 1000) - 1000})
			return ok

		bs._send_whatsapp_to_subscriber = send_then_expire
		try:
			bs.execute_broadcast(name)
		finally:
			bs._send_whatsapp_to_subscriber = orig
		self.assertEqual(frappe.db.get_value("Excom Broadcast", name, ["status", "sent_count", "failed_count"]), ("Paused", 1, 0))
		self.assertFalse(frappe.db.exists("Excom Broadcast Log", {"broadcast": name, "status": "Failed"}))
		comment = frappe.db.get_value("Comment", {"reference_doctype": "Excom Broadcast", "reference_name": name}, "content") or ""
		self.assertIn("past", comment)

	def test_private_media_needs_file_permission_and_no_traversal(self):
		"""M5: an agent cannot send a private file they cannot open, and `..` never leaves the site."""
		from excom.excom.api import chat
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
		from excom.excom.services.thread_service import upsert_thread

		f = frappe.get_doc({"doctype": "File", "file_name": "qa-p4-id-scan.png", "is_private": 1, "content": b"qa"}).insert(ignore_permissions=True)
		chat.check_template_media_access(f.file_url, {}, user="Administrator")
		chat.check_template_media_access("/files/qa-public.jpg", {}, user=AGENT)  # public files pass
		for args in ((f.file_url, {}), ("", {"cards": [{"header_media": "/files/ok.jpg"}, {"header_media": f.file_url}]})):
			with self.assertRaises(frappe.PermissionError):
				chat.check_template_media_access(*args, user=AGENT)
		for bad in ("/private/files/../../site_config.json", "/files/../../site_config.json", "/private/files/../../../common_site_config.json"):
			with self.assertRaises(frappe.PermissionError, msg=bad):
				chat.check_template_media_access(bad, {}, user="Administrator")
			with self.assertRaises(frappe.PermissionError, msg=bad):
				ws._local_file_bytes(bad)
		# a broadcast checks its owner when it is saved (its job runs later as the owner or Administrator)
		bc = frappe.new_doc("Excom Broadcast")
		bc.update({"channel": "WhatsApp", "wa_header_media": f.file_url})
		bc.owner = AGENT
		with self.assertRaises(frappe.PermissionError):
			bc._validate_media_access()
		# the inbox send refuses before anything reaches Meta
		lto = self._stored(META_LTO)
		identity = resolve_identity_internal(phone="919900400002", channel="whatsapp", channel_user_id="919900400002", display_name="QA")
		thread = upsert_thread(identity, "whatsapp", self.acc.name)
		with self.assertRaises(frappe.PermissionError):
			chat.send_template_to_thread(thread, lto.name, variables=json.dumps(["P", "C"]), header_media_url="/private/files/../../site_config.json",
				button_urls=json.dumps(["x"]), extras=json.dumps({"coupon_code": "C", "offer_expiration_ms": FUTURE_MS()}))
		self.assertEqual(self.calls, [])

	def test_paths_without_extras_refuse_clearly(self):
		"""L3: notifications, transactional sends and resends cannot give a coupon or cards."""
		from excom.excom.services import outbound

		coupon = self._stored(dict(META_CATALOG, name="qa_p4_coupon2", id="P4CPN2", components=[
			{"type": "BODY", "text": "Use the code"}, {"type": "BUTTONS", "buttons": [{"type": "COPY_CODE", "example": "W"}]}]))
		with self.assertRaises(wtt.TemplateTypeError):
			wtt.refuse_without_extras(coupon, "a notification")
		wtt.refuse_without_extras(self._stored(META_VOICE), "a notification")  # the call button needs nothing
		out = outbound._send_whatsapp(identity="none", template_name=coupon.name, variables=[], button_urls=[], header_media="",
			account_name=self.acc.name, purpose="qa", reference=None)
		self.assertEqual(out["skipped"], "template_needs_extras")
		self.assertEqual(self.calls, [])

	def test_sync_skips_a_bad_template_and_reports_it(self):
		"""C2: one template that cannot be stored does not stop the others."""
		bad = {k: v for k, v in META_CATALOG.items() if k != "status"}  # no status: this one cannot be read
		bad.update(name="qa_p4_bad", id="P4BAD")
		errors = []
		stored = wt._store_templates({"data": [bad, json.loads(json.dumps(META_SPM))]}, [self.acc.name], errors)
		self.assertEqual(stored, 1)
		self.assertTrue(frappe.db.exists("WhatsApp Templates", {"actual_name": "qa_p4_spm"}))
		self.assertFalse(frappe.db.exists("WhatsApp Templates", {"actual_name": "qa_p4_bad"}))
		self.assertEqual(len(errors), 1)
		self.assertIn("qa_p4_bad", errors[0])

	def test_library_form_buttons_need_no_input(self):
		"""M3: search shows a form button as FORMS; only URL and phone buttons take an input."""
		item = lib.normalise({"name": "delivery_failed_2_form", "language": "en_US", "category": "UTILITY",
			"body": "We were unable to deliver order {{1}} today.", "buttons": [{"type": "FORMS", "text": "Reschedule"}, {"type": "QUICK_REPLY", "text": "Stop"}]})
		self.assertEqual(item["button_inputs"], [])
		self.assertEqual([b["type"] for b in item["buttons"]], ["FLOW", "QUICK_REPLY"])
		self.assertEqual(lib.build_button_inputs([{"type": "FORMS"}, {"type": "QUICK_REPLY"}, {"type": "URL", "url": "https://e.com/"}]),
			[{"type": "URL", "url": {"base_url": "https://e.com/"}}])
		seen = self._lib_http()
		lib.create("delivery_failed_2_form", "qa_p4_form", "en_US", account_name=self.acc.name, button_inputs=[{"type": "FORMS"}])
		self.assertNotIn("library_template_button_inputs", seen["post"][0][1])

	def test_library_read_back_failure_is_not_an_error(self):
		"""L5: the template exists on Meta, so a failed read-back is a note, not an error."""
		self._lib_http(get_body={"error": {"message": "temporarily unavailable", "code": 2}})
		frappe.local.message_log = []
		res = lib.create("delivery_update_1", "qa_p4_readback", "en_US", account_name=self.acc.name,
			button_inputs=[{"type": "URL", "url": {"base_url": "https://www.example.com/"}}])
		self.assertIn("note", res)
		self.assertEqual(frappe.local.message_log, [])
		doc = frappe.get_doc("WhatsApp Templates", res["name"])
		self.assertEqual((doc.id, doc.status, doc.actual_name), ("P4LIB", "APPROVED", "qa_p4_readback"))

	def test_patch_maps_old_button_types_and_sets_the_type(self):
		"""H1: rows synced before Phase 4 kept Meta's raw button types and no template type."""
		from excom.patches.v1_0 import whatsapp_template_types_backfill as patch

		for name, kind, label in (("qa_p4_old_catalog", "CATALOG", "View"), ("qa_p4_old_coupon", "COPY_CODE", ""), ("qa_p4_old_call", "VOICE_CALL", "")):
			doc = frappe.new_doc("WhatsApp Templates")
			doc.update({"template_name": name, "actual_name": name, "template": "Hi", "language_code": "en", "category": "MARKETING",
				"template_type": "Standard", "status": "APPROVED"})
			doc.append("buttons", {"button_type": kind, "button_label": label})
			wt.upsert_doc_without_hooks(doc, "WhatsApp Button", "buttons")
		patch.execute()
		patch.execute()  # a second run changes nothing

		def row(name):
			parent = frappe.db.get_value("WhatsApp Templates", {"actual_name": name}, ["name", "template_type"], as_dict=True)
			return parent.template_type, frappe.db.get_value("WhatsApp Button", {"parent": parent.name}, ["button_type", "button_label"])

		self.assertEqual(row("qa_p4_old_catalog"), ("Catalog", ("Catalog", "View")))
		self.assertEqual(row("qa_p4_old_coupon"), ("Standard", ("Copy Code", "Copy offer code")))
		self.assertEqual(row("qa_p4_old_call"), ("Standard", ("Voice Call", "Call Now")))
