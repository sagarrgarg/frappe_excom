"""WhatsApp template types beyond plain text/media templates (HLD-005 Phase 4).

One place for the rules and JSON shapes of:
  copy-code (coupon) buttons, limited-time offers, media-card and product-card carousels,
  catalogue, multi-product (MPM) and single-product (SPM) templates, voice-call buttons and
  the call-permission-request component.

Three jobs:
  - validate_template(doc): Meta's creation rules, checked before the template is posted.
  - create side: meta_button(), carousel_component(), lto_component() build the create payload;
    apply_meta_components() reads Meta's GET response back into the doc (sync round-trip).
  - send side: send_components() turns the per-send `extras` dict into Meta components and
    refuses missing or invalid values before Meta does (131008, 131009, 132000).

Shapes come from Meta's docs (developers.facebook.com/documentation/business-messaging/whatsapp,
fetched 2026-11-04). The ones Meta does not show are marked UNVERIFIED below.

`extras` (send time, one JSON object):
	{
	  "coupon_code": "WINTER25",                       # Copy Code button
	  "offer_expiration_ms": 1767225600000,            # LTO with countdown (or "offer_expires_at": "2026-12-31 18:00:00", site time)
	  "catalog_id": "1948…",                           # default: the account's Catalogue ID
	  "cards": [                                       # carousels, in card order
	    {"header_media": "/files/a.jpg",               #   media card header (image / video)
	     "product_retailer_id": "SKU1",                #   product card header
	     "body": ["v1"],                               #   card body values (list, or {name: value})
	     "buttons": ["payload-or-url-suffix", ""]}     #   per card button: quick-reply payload / URL suffix
	  ],
	  "thumbnail_product_retailer_id": "SKU1",         # Catalog and MPM thumbnail
	  "sections": [{"title": "Popular", "product_retailer_ids": ["SKU1", "SKU2"]}],   # MPM
	  "product_retailer_id": "SKU1",                   # SPM header
	  "voice_call": {"ttl_minutes": 100, "payload": "lead-42"}                        # Voice Call button (optional)
	}
"""

import json
import time

import frappe
from frappe import _

TYPE_STANDARD = "Standard"
TYPE_LTO = "Limited-Time Offer"
TYPE_MEDIA_CAROUSEL = "Media Carousel"
TYPE_PRODUCT_CAROUSEL = "Product Carousel"
TYPE_CATALOG = "Catalog"
TYPE_MPM = "Multi-Product"
TYPE_SPM = "Single-Product"
TEMPLATE_TYPES = (TYPE_STANDARD, TYPE_LTO, TYPE_MEDIA_CAROUSEL, TYPE_PRODUCT_CAROUSEL, TYPE_CATALOG, TYPE_MPM, TYPE_SPM)
CAROUSEL_TYPES = (TYPE_MEDIA_CAROUSEL, TYPE_PRODUCT_CAROUSEL)

# Meta's limits
BODY_MAX = 1024
HEADER_TEXT_MAX = 60
FOOTER_MAX = 60
BUTTON_LABEL_MAX = 25
URL_MAX = 2000
PHONE_MAX = 20
QR_PAYLOAD_MAX = 256
# Copy code: the coupon page allows 20 characters, the LTO page 15. LTO templates use the
# stricter 15; every other template uses the coupon page's 20.
COUPON_CODE_MAX = 20
LTO_COUPON_CODE_MAX = 15
LTO_TEXT_MAX = 16
LTO_BODY_MAX = 600
CARDS_MIN, CARDS_MAX = 2, 10
PRODUCT_CAROUSEL_CREATE_CARDS = 2  # created with exactly 2 cards; a send may carry 2–10
CARD_BODY_MAX = 160
CARD_BUTTONS_MAX = 2
SPM_BODY_MAX = 160
MPM_SECTIONS_MAX = 10
MPM_PRODUCTS_MAX = 30
MPM_SECTION_TITLE_MAX = 24
VOICE_LABEL_MAX = 20
VOICE_TTL_TEMPLATE = (1440, 43200)  # on the template
VOICE_TTL_SEND = (1, 43200)  # override at send time
VOICE_PAYLOAD_MAX = 512

# excom button type → Meta create type
BUTTON_TO_META = {
	"Quick Reply": "QUICK_REPLY",
	"Visit Website": "URL",
	"Call Phone": "PHONE_NUMBER",
	"Flow": "FLOW",
	"Copy Code": "COPY_CODE",
	"Catalog": "CATALOG",
	"MPM": "MPM",
	"SPM": "SPM",
	"Voice Call": "VOICE_CALL",
}
META_TO_BUTTON = {v: k for k, v in BUTTON_TO_META.items()}
COMMERCE_BUTTONS = ("Catalog", "MPM", "SPM")
CARD_BUTTON_TYPES = {
	TYPE_MEDIA_CAROUSEL: ("Quick Reply", "Visit Website", "Call Phone"),
	TYPE_PRODUCT_CAROUSEL: ("SPM", "Visit Website"),
}

COPY_CODE_DEFAULT_LABEL = "Copy offer code"
VOICE_CALL_DEFAULT_LABEL = "Call Now"
# buttons whose label may be left empty (Meta fixes or defaults it)
LABEL_OPTIONAL = ("Copy Code", "Voice Call")

# Send-time sub_type per button. Meta's catalogue example uses "CATALOG" in capitals; the
# others are lowercase.
SEND_SUB_TYPE = {"Copy Code": "copy_code", "Catalog": "CATALOG", "MPM": "mpm", "Voice Call": "voice_call"}
# UNVERIFIED: Meta's voice-call template send example has no "index". Every other button
# component needs one, so it is sent; set False if Meta rejects it.
VOICE_CALL_SEND_INDEX = True
# UNVERIFIED: create-time shape of a carousel card body (Meta shows only the send side).
# UNVERIFIED: call-permission-request component type casing (Meta's example is lowercase).
CALL_PERMISSION_COMPONENT = "call_permission_request"


class TemplateTypeError(frappe.ValidationError):
	pass


def _fail(msg: str):
	frappe.throw(msg, exc=TemplateTypeError, title=_("WhatsApp template"))


def _check_len(value, limit: int, what: str) -> None:
	if value and len(str(value)) > limit:
		_fail(_("{0} is {1} characters; WhatsApp allows {2}.").format(what, len(str(value)), limit))


def _get(obj, key, default=None):
	if isinstance(obj, dict):
		return obj.get(key, default)
	val = getattr(obj, key, default)
	return default if val is None else val


def template_type(doc) -> str:
	return _get(doc, "template_type") or TYPE_STANDARD


def placeholder_names(text: str) -> list:
	from excom.excom.whatsapp_template_utils import ordered_placeholder_names

	return ordered_placeholder_names(text or "")


def is_named(names: list) -> bool:
	return bool(names) and not all(n.isdigit() for n in names)


def body_example(text: str, samples: list, key: str = "body_text", what: str = "") -> dict | None:
	"""Meta example block for a body: positional `body_text` or `body_text_named_params`.

	Every variable needs a real sample; a made-up one would go to Meta's review."""
	names = placeholder_names(text)
	if not names:
		return None
	samples = list(samples or [])
	if len(samples) < len(names) or any(str(s if s is not None else "").strip() == "" for s in samples[:len(names)]):
		_fail(_("{0}: enter a sample for each variable ({1} needed).").format(what or _("Body"), len(names)))
	if is_named(names):
		return {f"{key}_named_params": [{"param_name": n, "example": str(samples[i])} for i, n in enumerate(names)]}
	return {key: [[str(samples[i]) for i in range(len(names))]]}


def parameter_names(doc) -> list:
	"""Every placeholder Meta numbers or names together: body, text header and card bodies."""
	from excom.excom.whatsapp_template_utils import is_text_header

	names = placeholder_names(_get(doc, "template") or "")
	if is_text_header(_get(doc, "header_type")):
		names += placeholder_names(_get(doc, "header") or "")
	if template_type(doc) == TYPE_MEDIA_CAROUSEL:
		for c in _get(doc, "cards") or []:
			names += placeholder_names(_get(c, "card_body") or "")
	return names


def uses_named(doc) -> bool:
	"""Meta's parameter_format is one setting for the whole template, cards included."""
	return is_named(parameter_names(doc))


def _check_parameter_format(doc) -> None:
	names = parameter_names(doc)
	if any(n.isdigit() for n in names) and not all(n.isdigit() for n in names):
		_fail(_("Use either numbered variables ({{1}}) or named ones ({{name}}) in the whole template, body, header and cards, not both."))


def json_list(raw) -> list:
	if raw in (None, "", []):
		return []
	if isinstance(raw, list):
		return raw
	try:
		val = json.loads(raw)
	except (TypeError, ValueError):
		return []
	return val if isinstance(val, list) else []


def has_dynamic_url(url: str) -> bool:
	return "{{" in (url or "")


# ── validation (create) ──────────────────────────────────────────────────────

def infer_template_type(doc) -> str:
	"""A type for templates saved without one (Desk, old rows): read off the buttons and header."""
	kinds = {b.button_type for b in (doc.get("buttons") or [])}
	if doc.get("cards"):
		return TYPE_PRODUCT_CAROUSEL if doc.get("card_button_1_type") == "SPM" or not doc.get("card_header_format") else TYPE_MEDIA_CAROUSEL
	if (doc.get("lto_text") or "").strip():
		return TYPE_LTO
	if "Catalog" in kinds:
		return TYPE_CATALOG
	if "MPM" in kinds:
		return TYPE_MPM
	if "SPM" in kinds or (doc.get("header_type") or "").upper() == "PRODUCT":
		return TYPE_SPM
	return TYPE_STANDARD


def validate_template(doc) -> None:
	"""Meta's rules for the new types and buttons. Plain templates pass unchanged."""
	if not doc.get("template_type"):
		doc.template_type = infer_template_type(doc)
	ttype = doc.template_type
	buttons = doc.get("buttons") or []
	kinds = [b.button_type for b in buttons]
	category = (doc.get("category") or "").upper()
	header_type = (doc.get("header_type") or "").upper()

	_check_parameter_format(doc)
	if kinds.count("Copy Code") > 1:
		_fail(_("A template can have only one Copy Code button."))
	if kinds.count("Voice Call") > 1:
		_fail(_("A template can have only one Voice Call button."))
	for kind in COMMERCE_BUTTONS:
		if kind in kinds and ttype not in {"Catalog": (TYPE_CATALOG,), "MPM": (TYPE_MPM,), "SPM": (TYPE_SPM,)}[kind]:
			_fail(_("A {0} button belongs on a {1} template (this one is {2}).").format(kind, {"Catalog": TYPE_CATALOG, "MPM": TYPE_MPM, "SPM": TYPE_SPM}[kind], ttype))
	code_max = LTO_COUPON_CODE_MAX if ttype == TYPE_LTO else COUPON_CODE_MAX
	for b in buttons:
		if b.button_type == "Copy Code":
			if not (b.get("example_code") or "").strip():
				_fail(_("Copy Code button: enter an example code (Meta needs a sample to review)."))
			_check_len(b.example_code, code_max, _("Copy Code example"))
		elif b.button_type == "Voice Call":
			if not (b.get("button_label") or "").strip():
				b.button_label = VOICE_CALL_DEFAULT_LABEL  # Meta: text is optional, "Call Now" by default
			_check_len(b.button_label, VOICE_LABEL_MAX, _("Voice Call button label"))
			ttl = int(b.get("ttl_minutes") or 0)
			if ttl and not (VOICE_TTL_TEMPLATE[0] <= ttl <= VOICE_TTL_TEMPLATE[1]):
				_fail(_("Voice Call button: the call link lasts {0}–{1} minutes on a template (you set {2}).").format(*VOICE_TTL_TEMPLATE, ttl))

	if doc.get("call_permission_request") and category not in ("MARKETING", "UTILITY"):
		_fail(_("A call-permission request template must be Marketing or Utility."))

	if ttype in (TYPE_LTO, TYPE_MEDIA_CAROUSEL, TYPE_PRODUCT_CAROUSEL, TYPE_CATALOG, TYPE_MPM, TYPE_SPM) and category != "MARKETING":
		_fail(_("{0} templates must be in the Marketing category.").format(ttype))

	if ttype not in CAROUSEL_TYPES and doc.get("cards"):
		_fail(_("Carousel cards are only used on carousel templates; remove them or change the template type."))

	if ttype == TYPE_LTO:
		_validate_lto(doc, kinds, header_type)
	elif ttype in CAROUSEL_TYPES:
		_validate_carousel(doc, ttype, header_type)
	elif ttype == TYPE_CATALOG:
		if header_type:
			_fail(_("A catalogue template has no header (WhatsApp shows the catalogue thumbnail)."))
		_only_one(kinds, "Catalog")
	elif ttype == TYPE_MPM:
		if header_type != "TEXT" or not (doc.get("header") or "").strip():
			_fail(_("A multi-product template needs a text header."))
		_check_len(doc.header, HEADER_TEXT_MAX, _("Header"))
		_only_one(kinds, "MPM")
	elif ttype == TYPE_SPM:
		if header_type not in ("", "PRODUCT"):
			_fail(_("A single-product template's header is the product itself; clear the header."))
		doc.header_type = "PRODUCT"
		_check_len(doc.template, SPM_BODY_MAX, _("Body"))
		_only_one(kinds, "SPM")
	elif header_type == "PRODUCT":
		_fail(_("A product header is only used on single-product templates."))


def _only_one(kinds: list, kind: str) -> None:
	if kinds.count(kind) != 1:
		_fail(_("This template needs exactly one {0} button.").format(kind))
	if "Quick Reply" in kinds:
		_fail(_("Catalogue and product templates cannot have Quick Reply buttons."))


def _validate_lto(doc, kinds: list, header_type: str) -> None:
	if (doc.get("footer") or "").strip():
		_fail(_("A limited-time offer cannot have a footer."))
	if header_type not in ("", "IMAGE", "VIDEO"):
		_fail(_("A limited-time offer header must be an image or a video (or none)."))
	text = (doc.get("lto_text") or "").strip()
	if not text:
		_fail(_("Enter the offer text (up to {0} characters).").format(LTO_TEXT_MAX))
	_check_len(text, LTO_TEXT_MAX, _("Offer text"))
	_check_len(doc.template, LTO_BODY_MAX, _("Body"))
	extra = [k for k in kinds if k not in ("Copy Code", "Visit Website")]
	if extra:
		_fail(_("A limited-time offer takes only a Copy Code and a Visit Website button (found {0}).").format(", ".join(extra)))
	if kinds.count("Visit Website") != 1:
		_fail(_("A limited-time offer needs exactly one Visit Website button."))
	if "Copy Code" in kinds and kinds[0] != "Copy Code":
		_fail(_("Put the Copy Code button first (Meta expects it at index 0)."))


def card_button_types(doc) -> list:
	"""The button types every card shares, in order (Meta: all cards have the same structure).
	A product card has one button, so its (hidden) second type is ignored."""
	if template_type(doc) == TYPE_PRODUCT_CAROUSEL:
		return [t for t in (_get(doc, "card_button_1_type"),) if t]
	return [t for t in (_get(doc, "card_button_1_type"), _get(doc, "card_button_2_type")) if t]


def _validate_carousel(doc, ttype: str, header_type: str) -> None:
	cards = doc.get("cards") or []
	if header_type or (doc.get("footer") or "").strip() or (doc.get("buttons") or []):
		_fail(_("A carousel has only a body and cards: clear the header, footer and buttons (put buttons on the cards)."))
	if ttype == TYPE_PRODUCT_CAROUSEL and not doc.get("id") and len(cards) != PRODUCT_CAROUSEL_CREATE_CARDS:
		_fail(_("Create a product carousel with exactly {0} cards; once approved it can send up to {1}.").format(PRODUCT_CAROUSEL_CREATE_CARDS, CARDS_MAX))
	if not (CARDS_MIN <= len(cards) <= CARDS_MAX):
		_fail(_("A carousel needs {0}–{1} cards (found {2}).").format(CARDS_MIN, CARDS_MAX, len(cards)))
	if ttype == TYPE_PRODUCT_CAROUSEL:
		doc.card_button_2_type = None  # hidden on product carousels; never validated or sent
	elif doc.get("card_button_2_type") and not doc.get("card_button_1_type"):
		_fail(_("Set Card Button 1 Type before Card Button 2 Type."))
	types = card_button_types(doc)
	allowed = CARD_BUTTON_TYPES[ttype]
	for t in types:
		if t not in allowed:
			_fail(_("{0} cards take {1} buttons, not {2}.").format(ttype, " / ".join(allowed), t))
	if ttype == TYPE_PRODUCT_CAROUSEL:
		if len(types) != 1:
			_fail(_("Each product card has exactly one button: View (SPM) or Visit Website."))
		if any((c.get("card_body") or "").strip() for c in cards):
			_fail(_("Product cards take their text from the catalogue; clear the card bodies."))
	else:
		fmt = (doc.get("card_header_format") or "").upper()
		if fmt not in ("IMAGE", "VIDEO"):
			_fail(_("Set the Card Header Format (Image or Video)."))
		with_body = [bool((c.get("card_body") or "").strip()) for c in cards]
		if any(with_body) and not all(with_body):
			_fail(_("If one card has body text, every card must (Meta keeps the cards the same height)."))
		if not doc.get("id"):
			for i, c in enumerate(cards, start=1):
				if not (c.get("header_sample") or c.get("header_handle")):
					_fail(_("Card {0}: attach a sample {1} (Meta reviews every card).").format(i, fmt.lower()))
	for i, c in enumerate(cards, start=1):
		_check_len(c.get("card_body"), CARD_BODY_MAX, _("Card {0} body").format(i))
		for j, t in enumerate(types, start=1):
			label = (c.get(f"button_{j}_text") or "").strip()
			value = (c.get(f"button_{j}_value") or "").strip()
			if not label:
				_fail(_("Card {0}: enter the text of button {1}.").format(i, j))
			_check_len(label, BUTTON_LABEL_MAX, _("Card {0} button {1}").format(i, j))
			if t == "Visit Website":
				if not value.startswith(("http://", "https://")):
					_fail(_("Card {0}: button {1} needs a URL starting with https://").format(i, j))
				_check_len(value, URL_MAX, _("Card {0} button {1} URL").format(i, j))
				if has_dynamic_url(value):
					if not value.rstrip().endswith("}}") or len(placeholder_names(value)) != 1:
						_fail(_("Card {0}: a URL can hold one variable, at the end.").format(i))
					if not (c.get(f"button_{j}_example") or "").strip():
						_fail(_("Card {0}: enter an example for the URL variable of button {1}.").format(i, j))
			elif t == "Call Phone":
				if not value:
					_fail(_("Card {0}: button {1} needs a phone number.").format(i, j))
				_check_len(value, PHONE_MAX, _("Card {0} phone number").format(i))


def validate_for_meta(doc) -> None:
	"""Checks for a create or content update about to be posted (after the card uploads):
	every card body variable has a sample and every media card has an uploaded handle."""
	if template_type(doc) != TYPE_MEDIA_CAROUSEL:
		return
	fmt = (doc.get("card_header_format") or "image").lower()
	for i, c in enumerate(doc.get("cards") or [], start=1):
		body_example(c.get("card_body") or "", json_list(c.get("body_variable_samples")), what=_("Card {0} body").format(i))
		if not (c.get("header_handle") or "").strip():
			if c.get("header_sample_url"):
				_fail(_("Card {0}: attach the sample {1} again before changing this carousel. WhatsApp needs an uploaded sample; the copy from template sync is only a preview link.").format(i, fmt))
			_fail(_("Card {0}: attach a sample {1} (Meta reviews every card).").format(i, fmt))


# ── create payload ───────────────────────────────────────────────────────────

def meta_button(btn) -> dict | None:
	"""Create payload for the new button types; None for the types whatsapp_templates handles."""
	kind = btn.button_type
	if kind == "Copy Code":
		return {"type": "COPY_CODE", "example": (btn.get("example_code") or "").strip()}
	if kind in ("Catalog", "MPM", "SPM"):
		return {"type": BUTTON_TO_META[kind], "text": btn.button_label}
	if kind == "Voice Call":
		b = {"type": "VOICE_CALL", "text": btn.button_label}
		if btn.get("ttl_minutes"):
			b["ttl_minutes"] = int(btn.ttl_minutes)
		return b
	return None


def lto_component(doc) -> dict:
	return {"type": "LIMITED_TIME_OFFER", "limited_time_offer": {
		"text": (doc.get("lto_text") or "").strip(), "has_expiration": bool(doc.get("lto_has_expiration")),
	}}


def carousel_component(doc) -> dict:
	"""CAROUSEL create component. Media handles must already be on the cards (header_handle)."""
	ttype = template_type(doc)
	types = card_button_types(doc)
	cards = []
	for k, c in enumerate(doc.get("cards") or [], start=1):
		comps = []
		if ttype == TYPE_PRODUCT_CAROUSEL:
			comps.append({"type": "HEADER", "format": "PRODUCT"})
		else:
			header = {"type": "HEADER", "format": (doc.get("card_header_format") or "IMAGE").upper()}
			# only an uploaded handle; Meta's synced preview link (header_sample_url) is refused as a handle
			if c.get("header_handle"):
				header["example"] = {"header_handle": [c.header_handle]}
			comps.append(header)
			if (c.get("card_body") or "").strip():
				body = {"type": "BODY", "text": c.card_body}
				ex = body_example(c.card_body, json_list(c.get("body_variable_samples")), what=_("Card {0} body").format(k))
				if ex:
					body["example"] = ex
				comps.append(body)
		buttons = []
		for j, t in enumerate(types, start=1):
			label = c.get(f"button_{j}_text") or ""
			value = (c.get(f"button_{j}_value") or "").strip()
			if t == "Quick Reply":
				buttons.append({"type": "QUICK_REPLY", "text": label})
			elif t == "Visit Website":
				b = {"type": "URL", "text": label, "url": value}
				if has_dynamic_url(value) and c.get(f"button_{j}_example"):
					b["example"] = [c.get(f"button_{j}_example").strip()]
				buttons.append(b)
			elif t == "Call Phone":
				buttons.append({"type": "PHONE_NUMBER", "text": label, "phone_number": value})
			elif t == "SPM":
				buttons.append({"type": "SPM", "text": label})
		if buttons:
			comps.append({"type": "BUTTONS", "buttons": buttons})
		cards.append({"components": comps})
	return {"type": "CAROUSEL", "cards": cards}


def call_permission_component() -> dict:
	return {"type": CALL_PERMISSION_COMPONENT}


# ── sync (Meta → excom) ──────────────────────────────────────────────────────

def reset_type_fields(doc) -> None:
	"""Clear the Phase 4 fields before a fetched template is read in, so nothing stale survives."""
	doc.template_type = TYPE_STANDARD
	doc.lto_text = None
	doc.lto_has_expiration = 0
	doc.call_permission_request = 0
	doc.card_header_format = None
	doc.card_button_1_type = None
	doc.card_button_2_type = None
	doc.set("cards", [])


def button_from_meta(button: dict) -> dict:
	"""Child-row values for the new button types (merged into the row whatsapp_templates builds)."""
	kind = (button.get("type") or "").upper()
	row = {}
	if kind == "COPY_CODE":
		ex = button.get("example")
		row["example_code"] = (ex[0] if isinstance(ex, list) and ex else ex) or ""
		row["button_label"] = button.get("text") or COPY_CODE_DEFAULT_LABEL
	elif kind == "VOICE_CALL":
		try:
			row["ttl_minutes"] = int(button.get("ttl_minutes") or 0) or None
		except (TypeError, ValueError):
			row["ttl_minutes"] = None
		row["button_label"] = button.get("text") or VOICE_CALL_DEFAULT_LABEL
	return row


def apply_meta_components(doc, components: list, old_cards: list | None = None, old_format: str = "") -> None:
	"""LIMITED_TIME_OFFER, CAROUSEL and call-permission components into the doc; the type last.

	old_cards / old_format: the cards and card header format stored before this sync, so a card
	whose content Meta did not change keeps its local sample and uploaded handle."""
	kinds = set()
	for comp in components or []:
		ctype = (comp.get("type") or "").upper()
		if ctype == "LIMITED_TIME_OFFER":
			lto = comp.get("limited_time_offer") or {}
			doc.lto_text = lto.get("text") or ""
			doc.lto_has_expiration = 1 if lto.get("has_expiration") else 0
			kinds.add(TYPE_LTO)
		elif ctype == "CAROUSEL":
			_cards_from_meta(doc, comp.get("cards") or [], old_cards or [], old_format)
			kinds.add("carousel")
		elif ctype == CALL_PERMISSION_COMPONENT.upper():
			doc.call_permission_request = 1
		elif ctype == "HEADER" and (comp.get("format") or "").upper() == "PRODUCT":
			doc.header_type = "PRODUCT"
		elif ctype == "BUTTONS":
			for b in comp.get("buttons") or []:
				kinds.add((b.get("type") or "").upper())
	if TYPE_LTO in kinds:
		doc.template_type = TYPE_LTO
	elif "carousel" in kinds:
		doc.template_type = doc.template_type if doc.template_type in CAROUSEL_TYPES else TYPE_MEDIA_CAROUSEL
	elif "CATALOG" in kinds:
		doc.template_type = TYPE_CATALOG
	elif "MPM" in kinds:
		doc.template_type = TYPE_MPM
	elif "SPM" in kinds or (doc.get("header_type") or "").upper() == "PRODUCT":
		doc.template_type = TYPE_SPM
	else:
		doc.template_type = TYPE_STANDARD


# card fields that are the card's content on Meta; equal content keeps the local sample
CARD_CONTENT_FIELDS = ("card_body", "button_1_text", "button_1_value", "button_1_example",
	"button_2_text", "button_2_value", "button_2_example")
CARD_LOCAL_FIELDS = ("header_sample", "handle_sample", "header_handle")


def _same_card(old, new: dict) -> bool:
	return all((_get(old, f) or "") == (new.get(f) or "") for f in CARD_CONTENT_FIELDS)


def _cards_from_meta(doc, cards: list, old_cards: list = (), old_format: str = "") -> None:
	old_format = (old_format or "").upper()
	doc.set("cards", [])
	product = False
	types: list = []
	for card in cards:
		row = {}
		card_types = []
		for comp in card.get("components") or []:
			ctype = (comp.get("type") or "").upper()
			if ctype == "HEADER":
				fmt = (comp.get("format") or "").upper()
				if fmt == "PRODUCT":
					product = True
				else:
					doc.card_header_format = fmt
					# GET returns a preview link here, not an upload handle: never stored as the handle
					link = ((comp.get("example") or {}).get("header_handle") or [None])[0]
					row["header_sample_url"] = link if isinstance(link, str) else None
			elif ctype == "BODY":
				row["card_body"] = comp.get("text") or ""
				row["body_variable_samples"] = json.dumps(_samples_from_example(comp.get("example") or {})) if comp.get("example") else None
			elif ctype == "BUTTONS":
				for j, b in enumerate((comp.get("buttons") or [])[:CARD_BUTTONS_MAX], start=1):
					kind = META_TO_BUTTON.get((b.get("type") or "").upper(), b.get("type"))
					card_types.append(kind)
					row[f"button_{j}_text"] = b.get("text") or ""
					if kind == "Visit Website":
						row[f"button_{j}_value"] = b.get("url") or ""
						ex = b.get("example")
						row[f"button_{j}_example"] = (ex[0] if isinstance(ex, list) and ex else ex) or None
					elif kind == "Call Phone":
						row[f"button_{j}_value"] = b.get("phone_number") or ""
		types = types or card_types
		k = len(doc.get("cards") or [])
		old = old_cards[k] if k < len(old_cards) else None
		if old is not None and not product and (doc.get("card_header_format") or "") == old_format and _same_card(old, row):
			for f in CARD_LOCAL_FIELDS:
				if _get(old, f):
					row[f] = _get(old, f)
		doc.append("cards", row)
	doc.card_button_1_type = types[0] if len(types) > 0 else None
	doc.card_button_2_type = types[1] if len(types) > 1 and not product else None
	doc.template_type = TYPE_PRODUCT_CAROUSEL if product else TYPE_MEDIA_CAROUSEL


def _samples_from_example(example: dict) -> list:
	rows = example.get("body_text")
	if isinstance(rows, list) and rows:
		return rows[0] if isinstance(rows[0], list) else rows
	named = example.get("body_text_named_params")
	if isinstance(named, list):
		return [p.get("example", "") if isinstance(p, dict) else str(p) for p in named]
	return []


# ── send ─────────────────────────────────────────────────────────────────────

def parse_extras(raw) -> dict:
	if raw in (None, "", {}):
		return {}
	if isinstance(raw, dict):
		return raw
	try:
		val = json.loads(raw)
	except (TypeError, ValueError):
		_fail(_("Template extras must be a JSON object."))
	if val in (None, ""):
		return {}
	if not isinstance(val, dict):
		_fail(_("Template extras must be a JSON object."))
	return val


def needs_extras(doc) -> bool:
	"""True when sending this template needs values beyond the variables and header media."""
	ttype = template_type(doc)
	if ttype in (TYPE_MEDIA_CAROUSEL, TYPE_PRODUCT_CAROUSEL, TYPE_MPM, TYPE_SPM):
		return True
	if ttype == TYPE_LTO and doc.get("lto_has_expiration"):
		return True
	return any(b.button_type == "Copy Code" for b in (doc.get("buttons") or []))


def refuse_without_extras(doc, where: str) -> None:
	"""For send paths that cannot pass `extras` (notifications, transactional sends, resend):
	a clear refusal instead of an obscure Meta error."""
	if needs_extras(doc):
		_fail(_("Template {0} ({1}) needs send-time values (coupon code, offer end, cards or products) that {2} cannot supply. Send it from the inbox or a broadcast.").format(
			_get(doc, "template_name") or _get(doc, "name") or "", template_type(doc), where))


def expiration_ms(extras: dict, now_ms: int | None = None) -> int:
	"""Offer end as Unix epoch milliseconds, from `offer_expiration_ms` or `offer_expires_at` (site time)."""
	raw = extras.get("offer_expiration_ms")
	if raw in (None, ""):
		at = extras.get("offer_expires_at")
		if not at:
			_fail(_("This limited-time offer shows a countdown: set when the offer ends."))
		try:
			raw = site_time_ms(at)
		except Exception:
			_fail(_("Offer end must be a date and time, e.g. 2026-12-31 18:00:00."))
	try:
		ms = int(raw)
	except (TypeError, ValueError):
		_fail(_("Offer end must be a time in milliseconds."))
	now_ms = int(time.time() * 1000) if now_ms is None else now_ms
	if ms < 10**11:
		_fail(_("Offer end looks like seconds, not milliseconds ({0}).").format(ms))
	if ms <= now_ms:
		_fail(_("The offer end time is in the past."))
	return ms


def site_time_ms(value) -> int:
	"""A datetime (string or object) in the site's time zone, as Unix epoch milliseconds."""
	from zoneinfo import ZoneInfo

	from frappe.utils import get_datetime, get_system_timezone

	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return int(dt.timestamp() * 1000)


def _values(text: str, values, what: str) -> list:
	"""Text parameters for one card body: a list in order, or {name: value} for named placeholders."""
	names = placeholder_names(text)
	if not names:
		return []
	if values is not None and not isinstance(values, (list, dict)):
		_fail(_("{0}: send the values as a list, or an object of name: value.").format(what))
	if isinstance(values, dict):
		missing = [n for n in names if values.get(n) in (None, "")]
		if missing:
			_fail(_("{0} needs a value for: {1}").format(what, ", ".join(missing)))
		vals = [values[n] for n in names]
	else:
		vals = list(values or [])
		if len(vals) < len(names) or any(str(v).strip() == "" for v in vals[:len(names)]):
			_fail(_("{0} needs {1} value(s); you sent {2}.").format(what, len(names), len([v for v in vals if str(v).strip()])))
	named = is_named(names)
	out = []
	for i, n in enumerate(names):
		p = {"type": "text", "text": str(vals[i])}
		if named:
			p["parameter_name"] = n
		out.append(p)
	return out


def _catalog_id(extras: dict, account) -> str:
	cid = str(extras.get("catalog_id") or (getattr(account, "wa_catalog_id", None) if account is not None else "") or "").strip()
	if not cid:
		_fail(_("Product templates need a catalogue: set the Catalogue ID on the WhatsApp account."))
	return cid


def _product(retailer_id, catalog_id: str, what: str) -> dict:
	rid = str(retailer_id or "").strip()
	if not rid:
		_fail(_("{0}: enter the product's retailer id (Content ID in Commerce Manager).").format(what))
	return {"type": "product", "product": {"product_retailer_id": rid, "catalog_id": catalog_id}}


def send_components(doc, extras: dict, account=None, media_fn=None, dry_run: bool = False, now_ms: int | None = None) -> dict:
	"""Components the new types add at send time, split by where they go:
	{"header": [...], "after_body": [...], "buttons": [...], "carousel": [...]}.

	media_fn(file_url) -> {"id": …} or {"link": …} uploads a card's header media; dry_run checks
	the values only (broadcast validation) and never uploads.
	"""
	extras = extras or {}
	if not isinstance(extras, dict):
		_fail(_("Template extras must be a JSON object."))
	ttype = template_type(doc)
	out = {"header": [], "after_body": [], "buttons": [], "carousel": []}

	if ttype == TYPE_SPM or (doc.get("header_type") or "").upper() == "PRODUCT":
		out["header"].append({"type": "header", "parameters": [
			_product(extras.get("product_retailer_id"), _catalog_id(extras, account), _("Single-product template"))]})

	if ttype == TYPE_LTO and doc.get("lto_has_expiration"):
		out["after_body"].append({"type": "limited_time_offer", "parameters": [
			{"type": "limited_time_offer", "limited_time_offer": {"expiration_time_ms": expiration_ms(extras, now_ms)}}]})

	code_max = LTO_COUPON_CODE_MAX if ttype == TYPE_LTO else COUPON_CODE_MAX
	for i, btn in enumerate(doc.get("buttons") or []):
		kind = btn.button_type
		if kind == "Copy Code":
			code = str(extras.get("coupon_code") or "").strip()
			if not code:
				_fail(_("This template has a Copy Code button: enter the coupon code."))
			_check_len(code, code_max, _("Coupon code"))
			out["buttons"].append({"type": "button", "sub_type": SEND_SUB_TYPE[kind], "index": str(i),
				"parameters": [{"type": "coupon_code", "coupon_code": code}]})
		elif kind == "Catalog":
			thumb = str(extras.get("thumbnail_product_retailer_id") or "").strip()
			# Meta: without parameters the first catalogue item is the thumbnail, so the component is optional
			if thumb:
				out["buttons"].append({"type": "button", "sub_type": SEND_SUB_TYPE[kind], "index": str(i),
					"parameters": [{"type": "action", "action": {"thumbnail_product_retailer_id": thumb}}]})
		elif kind == "MPM":
			out["buttons"].append({"type": "button", "sub_type": SEND_SUB_TYPE[kind], "index": str(i),
				"parameters": [{"type": "action", "action": _mpm_action(extras)}]})
		elif kind == "Voice Call":
			vc = extras.get("voice_call") or {}
			if not isinstance(vc, dict):
				_fail(_("voice_call must be an object: {\"ttl_minutes\": 100, \"payload\": \"…\"}."))
			params = []
			if vc.get("ttl_minutes") not in (None, ""):
				try:
					ttl = int(vc["ttl_minutes"])
				except (TypeError, ValueError):
					_fail(_("Call link time must be a whole number of minutes (got {0}).").format(str(vc["ttl_minutes"])[:20]))
				if not (VOICE_TTL_SEND[0] <= ttl <= VOICE_TTL_SEND[1]):
					_fail(_("Call link time must be {0}–{1} minutes.").format(*VOICE_TTL_SEND))
				params.append({"type": "ttl_minutes", "ttl_minutes": ttl})
			if vc.get("payload"):
				_check_len(vc["payload"], VOICE_PAYLOAD_MAX, _("Call button payload"))
				params.append({"type": "payload", "payload": str(vc["payload"])})
			if params:
				comp = {"type": "button", "sub_type": SEND_SUB_TYPE[kind], "parameters": params}
				if VOICE_CALL_SEND_INDEX:
					comp["index"] = str(i)
				out["buttons"].append(comp)

	if ttype in CAROUSEL_TYPES:
		out["carousel"].append({"type": "carousel", "cards": _send_cards(doc, extras, account, media_fn, dry_run)})
	return out


def _mpm_action(extras: dict) -> dict:
	sections = extras.get("sections") or []
	if not isinstance(sections, list):
		_fail(_("Product sections must be a list."))
	if not sections:
		_fail(_("A multi-product template needs at least one section of products."))
	if len(sections) > MPM_SECTIONS_MAX:
		_fail(_("At most {0} sections (found {1}).").format(MPM_SECTIONS_MAX, len(sections)))
	out, total = [], 0
	for s in sections:
		if not isinstance(s, dict):
			_fail(_("Each product section must be an object: {\"title\": …, \"product_retailer_ids\": […]}."))
		title = str(s.get("title") or "").strip()
		if not title:
			_fail(_("Every product section needs a title."))
		_check_len(title, MPM_SECTION_TITLE_MAX, _("Section title"))
		raw_ids = s.get("product_retailer_ids") or []
		if not isinstance(raw_ids, list):
			_fail(_("Section {0}: product_retailer_ids must be a list.").format(title))
		ids = [str(r).strip() for r in raw_ids if str(r).strip()]
		if not ids:
			_fail(_("Section {0} has no products.").format(title))
		total += len(ids)
		out.append({"title": title, "product_items": [{"product_retailer_id": r} for r in ids]})
	if total > MPM_PRODUCTS_MAX:
		_fail(_("At most {0} products in all (found {1}).").format(MPM_PRODUCTS_MAX, total))
	thumb = str(extras.get("thumbnail_product_retailer_id") or "").strip() or out[0]["product_items"][0]["product_retailer_id"]
	return {"thumbnail_product_retailer_id": thumb, "sections": out}


def _send_cards(doc, extras: dict, account, media_fn, dry_run: bool) -> list:
	ttype = template_type(doc)
	defs = doc.get("cards") or []
	sent = extras.get("cards") or []
	if not isinstance(sent, list):
		_fail(_("Cards must be a list."))
	if ttype == TYPE_MEDIA_CAROUSEL and not defs:
		_fail(_("This carousel's cards are missing here (template sync could not read them). Sync templates again, then send."))
	if ttype == TYPE_MEDIA_CAROUSEL and len(sent) != len(defs):
		_fail(_("This carousel was approved with {0} cards; send exactly {0} (you sent {1}).").format(len(defs), len(sent)))
	if ttype == TYPE_PRODUCT_CAROUSEL and not (CARDS_MIN <= len(sent) <= CARDS_MAX):
		_fail(_("A product carousel sends {0}–{1} cards (you sent {2}).").format(CARDS_MIN, CARDS_MAX, len(sent)))
	types = card_button_types(doc)
	catalog_id = _catalog_id(extras, account) if ttype == TYPE_PRODUCT_CAROUSEL else ""
	fmt = (doc.get("card_header_format") or "IMAGE").lower()
	cards = []
	for k, card in enumerate(sent):
		card = card or {}
		what = _("Card {0}").format(k + 1)
		if not isinstance(card, dict):
			_fail(_("{0} must be an object.").format(what))
		cdef = defs[k] if k < len(defs) else (defs[0] if defs else {})
		comps = []
		if ttype == TYPE_PRODUCT_CAROUSEL:
			comps.append({"type": "header", "parameters": [_product(card.get("product_retailer_id"), catalog_id, what)]})
		else:
			media = str(card.get("header_media") or "").strip()
			if not media:
				_fail(_("{0}: attach the {1} for the card header.").format(what, fmt))
			ref = {"link": media} if dry_run or media_fn is None else media_fn(media)
			comps.append({"type": "header", "parameters": [{"type": fmt, fmt: ref}]})
			params = _values(_get(cdef, "card_body") or "", card.get("body"), _("{0} body").format(what))
			if params:
				comps.append({"type": "body", "parameters": params})
		values = card.get("buttons") or []
		if not isinstance(values, list):
			_fail(_("{0}: buttons must be a list, one value per card button.").format(what))
		for j, t in enumerate(types):
			val = str(values[j]).strip() if j < len(values) and values[j] is not None else ""
			if t == "Quick Reply" and val:
				_check_len(val, QR_PAYLOAD_MAX, _("{0} quick-reply payload").format(what))
				comps.append({"type": "button", "sub_type": "quick_reply", "index": j, "parameters": [{"type": "payload", "payload": val}]})
			elif t == "Visit Website" and has_dynamic_url(_get(cdef, f"button_{j + 1}_value") or ""):
				if not val:
					_fail(_("{0}: enter the link ending for button {1}.").format(what, j + 1))
				# UNVERIFIED for product cards: Meta shows this only for media cards
				comps.append({"type": "button", "sub_type": "url", "index": j, "parameters": [{"type": "text", "text": val}]})
		cards.append({"card_index": k, "components": comps})
	return cards


def preview_lines(doc, extras: dict) -> list:
	"""Short inbox lines describing the parts of the message a plain preview would miss."""
	extras = extras if isinstance(extras, dict) else {}
	ttype = template_type(doc)
	lines = []
	if ttype == TYPE_LTO:
		end = ""
		ms = extras.get("offer_expiration_ms")
		if ms:
			from datetime import datetime, timezone

			try:
				at = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
			except (TypeError, ValueError, OverflowError):
				at = None
			if at:
				try:
					from zoneinfo import ZoneInfo

					from frappe.utils import get_system_timezone

					end = " · " + _("ends {0}").format(at.astimezone(ZoneInfo(get_system_timezone())).strftime("%d %b %Y %H:%M"))
				except Exception:
					end = " · " + _("ends {0} UTC").format(at.strftime("%d %b %Y %H:%M"))
		elif extras.get("offer_expires_at"):
			end = " · " + _("ends {0}").format(extras["offer_expires_at"])
		lines.append(f"⏳ {doc.get('lto_text') or _('Offer')}{end}")
	if extras.get("coupon_code") and any(b.button_type == "Copy Code" for b in (doc.get("buttons") or [])):
		lines.append(_("🏷 Code: {0}").format(extras["coupon_code"]))
	if ttype in CAROUSEL_TYPES:
		cards = extras.get("cards") if isinstance(extras.get("cards"), list) else []
		lines.append(_("🗂 Carousel: {0} card(s)").format(len(cards)))
		defs = doc.get("cards") or []
		for k, c in enumerate(cards[:10]):
			c = c if isinstance(c, dict) else {}
			if ttype == TYPE_PRODUCT_CAROUSEL:
				lines.append(f"  {k + 1}. {c.get('product_retailer_id') or ''}")
			else:
				body = _get(defs[k], "card_body") if k < len(defs) else ""
				raw = c.get("body")
				if isinstance(raw, dict):
					# {name: value}: in the order the placeholders appear, not the dict's order
					vals = [raw.get(n, "{{" + n + "}}") for n in placeholder_names(body or "")]
				else:
					vals = raw if isinstance(raw, list) else []
				if body:
					from excom.excom.api.chat import _replace_placeholders

					lines.append(f"  {k + 1}. {_replace_placeholders(body, vals or [])}")
				else:
					lines.append(f"  {k + 1}. [{(doc.get('card_header_format') or 'image').lower()}]")
	if ttype == TYPE_CATALOG:
		lines.append(_("🛍 Catalogue"))
	if ttype == TYPE_MPM:
		sections = [s for s in (extras.get("sections") or []) if isinstance(s, dict)] if isinstance(extras.get("sections"), list) else []
		n = sum(len(s.get("product_retailer_ids") or []) for s in sections)
		lines.append(_("🛍 {0} product(s) in {1} section(s)").format(n, len(sections)))
	if ttype == TYPE_SPM and extras.get("product_retailer_id"):
		lines.append(_("🛍 Product {0}").format(extras["product_retailer_id"]))
	if doc.get("call_permission_request"):
		lines.append(_("📞 Asks permission to call"))
	if any(b.button_type == "Voice Call" for b in (doc.get("buttons") or [])):
		lines.append(_("📞 Call button"))
	return lines
