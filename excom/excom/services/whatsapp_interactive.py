"""Interactive WhatsApp messages (non-template), checked against Meta's limits before sending.

Every builder returns the `interactive` object; `send()` posts it to one person and
`send_to_thread()` also records it in the inbox. Interactive messages are only accepted inside
the 24-hour customer service window (the person messaged us, or tapped a template button, in
the last 24 h); outside it Meta answers 131047 and a template must be sent instead.

Limits (developers.facebook.com/documentation/business-messaging/whatsapp/messages/…, 2026-11):
  reply buttons  1–3 buttons, title ≤ 20 (unique), id ≤ 256, body ≤ 1024, footer ≤ 60, header text ≤ 60
  list           button ≤ 20, ≤ 10 sections, ≤ 10 rows in total, section/row title ≤ 24,
                 row description ≤ 72, row id ≤ 200, header text ≤ 60, body ≤ 4096
  CTA URL        display text ≤ 20, body ≤ 1024
  Flow           CTA ≤ 30, flow_message_version "3"; the first screen can be filled per message
  product list   header text required, ≤ 30 products, ≤ 10 sections (title ≤ 24)
  product carousel 2–10 product cards from one catalogue
  address        India only (+91 recipients)
  voice_call     body ≤ 1024, display text ≤ 20, ttl_minutes 1–43200, payload ≤ 512
  call permission request  body optional (≤ 1024), no header or footer
"""

import json

import frappe
from frappe import _

from excom.excom.utils.errors import ExcomValidationError

MAX_BODY = 1024
MAX_LIST_BODY = 4096
MAX_FOOTER = 60
MAX_HEADER_TEXT = 60


class InteractiveLimitError(ExcomValidationError, frappe.ValidationError):
	"""Also a frappe.ValidationError, so Desk shows the message instead of a server error."""

	title = "WhatsApp interactive message"


def _check(cond: bool, msg: str) -> None:
	if not cond:
		raise InteractiveLimitError(msg)


def _len(value, limit: int, what: str) -> str:
	value = "" if value is None else str(value)
	_check(len(value) <= limit, _("{0} is {1} characters; WhatsApp allows {2}.").format(what, len(value), limit))
	return value


def _header(header) -> dict | None:
	"""header: None, "text", {"type": "text", "text": ...} or {"type": "image|video|document", "link"|"id": ...}."""
	if not header:
		return None
	if isinstance(header, str):
		return {"type": "text", "text": _len(header, MAX_HEADER_TEXT, _("Header"))}
	kind = (header.get("type") or "text").lower()
	if kind == "text":
		return {"type": "text", "text": _len(header.get("text"), MAX_HEADER_TEXT, _("Header"))}
	_check(kind in ("image", "video", "document"), _("Header type {0} is not allowed here").format(kind))
	keys = ("id", "link", "filename") if kind == "document" else ("id", "link")  # filename is for documents only
	media = {k: v for k, v in header.items() if k in keys and v}
	_check(bool(media.get("id") or media.get("link")), _("A media header needs an id or a link"))
	return {"type": kind, kind: media}


def _with_common(interactive: dict, body: str, footer: str = "", header=None, body_limit: int = MAX_BODY) -> dict:
	if body:
		interactive["body"] = {"text": _len(body, body_limit, _("Body"))}
	if footer:
		interactive["footer"] = {"text": _len(footer, MAX_FOOTER, _("Footer"))}
	h = _header(header)
	if h:
		interactive["header"] = h
	return interactive


# ── builders ──────────────────────────────────────────────────────────────────

def reply_buttons(body: str, buttons: list, header=None, footer: str = "") -> dict:
	"""buttons: [{"id": "RU:RU-0001:view", "title": "View & Order"}, …] (1–3)."""
	_check(body, _("Reply buttons need a body"))
	_check(1 <= len(buttons or []) <= 3, _("Reply buttons: 1 to 3 buttons"))
	titles = [b.get("title") for b in buttons]
	ids = [b.get("id") for b in buttons]
	_check(len(set(titles)) == len(titles), _("Reply button titles must be different"))
	_check(all(ids) and len(set(ids)) == len(ids), _("Every reply button needs its own id"))
	return _with_common({"type": "button", "action": {"buttons": [
		{"type": "reply", "reply": {"id": _len(b.get("id"), 256, _("Button id")), "title": _len(b.get("title"), 20, _("Button title"))}}
		for b in buttons
	]}}, body, footer, header)


def list_message(body: str, button: str, sections: list, header: str = "", footer: str = "") -> dict:
	"""sections: [{"title": "Sugar", "rows": [{"id": ..., "title": ..., "description": ...}]}]."""
	_check(body, _("A list needs a body"))
	_check(1 <= len(sections or []) <= 10, _("A list has 1 to 10 sections"))
	rows = sum(len(s.get("rows") or []) for s in sections)
	_check(1 <= rows <= 10, _("A list has 1 to 10 rows in total (got {0})").format(rows))
	_check(len(sections) == 1 or all(s.get("title") for s in sections), _("With several sections every section needs a title"))
	out_sections = []
	for s in sections:
		sec = {"rows": [{
			"id": _len(r.get("id"), 200, _("Row id")),
			"title": _len(r.get("title"), 24, _("Row title")),
			**({"description": _len(r.get("description"), 72, _("Row description"))} if r.get("description") else {}),
		} for r in s.get("rows") or []]}
		# Meta marks the section title required even for a single section
		sec["title"] = _len(s.get("title") or button, 24, _("Section title"))
		out_sections.append(sec)
	_check(not header or isinstance(header, str), _("A list header is text only"))
	return _with_common({"type": "list", "action": {"button": _len(button, 20, _("List button")), "sections": out_sections}},
		body, footer, header, body_limit=MAX_LIST_BODY)


def cta_url(body: str, display_text: str, url: str, header=None, footer: str = "") -> dict:
	_check(body, _("A link button needs a body"))
	_check(str(url or "").startswith(("https://", "http://")), _("The link must start with http(s)://"))
	return _with_common({"type": "cta_url", "action": {"name": "cta_url", "parameters": {
		"display_text": _len(display_text, 20, _("Link button text")), "url": url,
	}}}, body, footer, header)


def flow(flow_id: str, cta: str, body: str, flow_token: str, screen: str = "", data: dict = None,
		header=None, footer: str = "", draft: bool = False, action: str = "navigate") -> dict:
	"""A WhatsApp Flow. With action "navigate" the first `screen` receives `data` — a static Flow
	filled in for this one person (e.g. their items and today's rates), no endpoint needed."""
	_check(flow_id, _("A Flow message needs the Flow id (create and publish the Flow first)"))
	_check(flow_token, _("A Flow message needs a flow_token so the answer can be matched"))
	params = {
		"flow_message_version": "3",
		"flow_token": _len(flow_token, 512, _("Flow token")),
		"flow_id": str(flow_id),
		"flow_cta": _len(cta, 30, _("Flow button text")),
		"flow_action": action,
	}
	if draft:
		params["mode"] = "draft"
	if action == "navigate":
		_check(screen, _("A navigate Flow needs the first screen id"))
		params["flow_action_payload"] = {"screen": screen, **({"data": data} if data else {})}
	return _with_common({"type": "flow", "action": {"name": "flow", "parameters": params}}, body, footer, header)


def location_request(body: str) -> dict:
	_check(body, _("A location request needs a body"))
	return {"type": "location_request_message", "body": {"text": _len(body, MAX_BODY, _("Body"))}, "action": {"name": "send_location"}}


def address_request(body: str, values: dict = None) -> dict:
	"""India only: WhatsApp's own address form (name, phone, PIN code, house, area, city, state…)."""
	_check(body, _("An address request needs a body"))
	parameters = {"country": "IN"}
	if values:
		parameters["values"] = values
	return {"type": "address_message", "body": {"text": _len(body, MAX_BODY, _("Body"))}, "action": {"name": "address_message", "parameters": parameters}}


def product(catalog_id: str, retailer_id: str, body: str = "", footer: str = "") -> dict:
	_check(catalog_id and retailer_id, _("A product message needs the catalogue id and the product's retailer id"))
	return _with_common({"type": "product", "action": {"catalog_id": str(catalog_id), "product_retailer_id": str(retailer_id)}}, body, footer)


def product_list(catalog_id: str, header: str, body: str, sections: list, footer: str = "") -> dict:
	"""sections: [{"title": "Water guns", "retailer_ids": ["FM097", "FM099"]}] — ≤ 30 products, ≤ 10 sections."""
	_check(catalog_id, _("A product list needs the catalogue id"))
	_check(header and body, _("A product list needs a header and a body"))
	_check(isinstance(header, str), _("A product list header is text only"))
	_check(1 <= len(sections or []) <= 10, _("A product list has 1 to 10 sections"))
	total = sum(len(s.get("retailer_ids") or []) for s in sections)
	_check(1 <= total <= 30, _("A product list shows 1 to 30 products (got {0})").format(total))
	return _with_common({"type": "product_list", "action": {"catalog_id": str(catalog_id), "sections": [
		{"title": _len(s.get("title") or _("Products"), 24, _("Section title")),
		 "product_items": [{"product_retailer_id": str(r)} for r in s.get("retailer_ids") or []]}
		for s in sections
	]}}, body, footer, header)


def product_carousel(catalog_id: str, body: str, retailer_ids: list) -> dict:
	"""2–10 product cards from one catalogue; image, title and price come from the catalogue."""
	_check(catalog_id, _("A product carousel needs the catalogue id"))
	_check(body, _("A product carousel needs a body"))
	_check(2 <= len(retailer_ids or []) <= 10, _("A product carousel has 2 to 10 cards (got {0})").format(len(retailer_ids or [])))
	return {"type": "carousel", "body": {"text": _len(body, MAX_BODY, _("Body"))}, "action": {"cards": [
		{"card_index": i, "type": "product", "action": {"product_retailer_id": str(r), "catalog_id": str(catalog_id)}}
		for i, r in enumerate(retailer_ids)
	]}}


def catalog_message(body: str, thumbnail_retailer_id: str = "", footer: str = "") -> dict:
	_check(body, _("A catalogue message needs a body"))
	params = {"thumbnail_product_retailer_id": str(thumbnail_retailer_id)} if thumbnail_retailer_id else {}
	return _with_common({"type": "catalog_message", "action": {"name": "catalog_message", **({"parameters": params} if params else {})}}, body, footer)


def voice_call(body: str, display_text: str = "", ttl_minutes: int = 0, payload: str = "") -> dict:
	"""A "Call on WhatsApp" button (WhatsApp Business Calling). The person taps it to call us;
	`payload` comes back as `cta_payload` on the call. ttl_minutes 1–43200 (Meta's default 7 days)."""
	_check(body, _("A call button needs a body"))
	params = {}
	if display_text:
		params["display_text"] = _len(display_text, 20, _("Call button text"))
	if ttl_minutes:
		try:
			ttl = int(ttl_minutes)
		except (TypeError, ValueError):
			ttl = 0
		_check(1 <= ttl <= 43200, _("The call button lasts 1 to 43200 minutes (30 days)"))
		params["ttl_minutes"] = ttl
	if payload:
		params["payload"] = _len(payload, 512, _("Call button payload"))
	action = {"name": "voice_call", **({"parameters": params} if params else {})}
	return {"type": "voice_call", "body": {"text": _len(body, MAX_BODY, _("Body"))}, "action": action}


def call_permission_request(body: str = "") -> dict:
	"""Ask the person to allow business-initiated calls. No header or footer is allowed; the body is
	optional. Limits: 1 request per 24 h and 2 per 7 days per person (counted with templates)."""
	interactive = {"type": "call_permission_request", "action": {"name": "call_permission_request"}}
	if body:
		interactive["body"] = {"text": _len(body, MAX_BODY, _("Body"))}
	return interactive


def preview_text(interactive: dict) -> str:
	"""A readable line for the inbox: body plus the choices offered."""
	body = ((interactive.get("body") or {}).get("text") or "").strip()
	kind = interactive.get("type")
	action = interactive.get("action") or {}
	extra = ""
	if kind == "button":
		extra = " · ".join(f"[{b['reply']['title']}]" for b in action.get("buttons") or [])
	elif kind == "list":
		extra = f"[{action.get('button')}]"
	elif kind == "cta_url":
		extra = f"[{(action.get('parameters') or {}).get('display_text')}]"
	elif kind == "flow":
		extra = f"[{(action.get('parameters') or {}).get('flow_cta')}]"
	elif kind in ("product_list", "carousel"):
		n = sum(len(s.get("product_items") or []) for s in action.get("sections") or []) or len(action.get("cards") or [])
		extra = _("[{0} products]").format(n)
	elif kind == "product":
		extra = f"[{action.get('product_retailer_id')}]"
	elif kind == "catalog_message":
		extra = _("[View catalogue]")
	elif kind == "address_message":
		extra = _("[Send address]")
	elif kind == "location_request_message":
		extra = _("[Send location]")
	elif kind == "voice_call":
		extra = f"[📞 {(action.get('parameters') or {}).get('display_text') or _('Call Now')}]"
	elif kind == "call_permission_request":
		extra = _("[Allow calls]")
	return f"{body} {extra}".strip()


# ── sending ───────────────────────────────────────────────────────────────────

def send(account, to: str, interactive: dict, reply_to: str = "", identity: str = "") -> dict:
	"""Post one interactive message. reply_to: the WhatsApp message id it answers (quoted).

	Refused before Meta when the person said STOP / do not contact (marketing opt-outs do not
	apply: these are replies inside a conversation the person opened).
	"""
	from excom.excom.services import whatsapp_service as ws

	if interactive.get("type") == "address_message":
		indian = to.strip().startswith("IN.") if ws.is_bsuid(to) else ws._clean_phone(to).startswith("91")
		_check(indian, _("WhatsApp's address form only works for Indian (+91) numbers"))
	ws.assert_template_allowed(to, "SERVICE", identity=identity)
	payload = {"messaging_product": "whatsapp", **ws._recipient(to), "type": "interactive", "interactive": interactive}
	if reply_to:
		payload["context"] = {"message_id": reply_to}
	return ws._call_api(account, payload)


def window_open(thread: str) -> bool:
	"""True when the person wrote to us (or tapped a button) within the last 24 hours."""
	last = frappe.db.get_value("Excom Thread", thread, "last_inbound_at")
	if not last:
		return False
	return frappe.utils.time_diff_in_seconds(frappe.utils.now_datetime(), last) < 24 * 3600


def send_to_thread(thread: str, interactive: dict, reply_to_message: str = "", reference: tuple = None) -> str:
	"""Send an interactive message into an inbox thread and record it there.

	reply_to_message: the Excom Message it answers (quoted on WhatsApp).
	reference: (doctype, name) the message belongs to, e.g. ("Sales Order", "SO-0001").
	Returns the Excom Message name. Raises InteractiveLimitError when the 24-hour window is closed.
	"""
	from excom.excom.services.thread_service import record_outbound_message

	t = frappe.get_doc("Excom Thread", thread)
	_check(t.channel == "whatsapp", _("Interactive messages are WhatsApp only"))
	_check(window_open(thread), _("The 24-hour window is closed: send an approved template instead."))
	identity = frappe.get_doc("Omni Identity", t.omni_identity)
	to = identity.primary_whatsapp or identity.primary_phone or identity.get("wa_bsuid")
	_check(bool(to), _("No WhatsApp number or user id on this contact"))
	account = frappe.get_doc(t.account_doctype, t.account)
	reply_to_wamid = frappe.db.get_value("Excom Message", reply_to_message, "provider_message_id") if reply_to_message else ""
	result = send(account, to, interactive, reply_to=reply_to_wamid or "", identity=t.omni_identity)
	return record_outbound_message(
		thread=t,
		content_text=preview_text(interactive),
		message_type="Flow" if interactive.get("type") == "flow" else "Interactive",
		content_json={"interactive": interactive},
		provider_message_id=result.get("provider_message_id", ""),
		reply_to=reply_to_message,
		reference=reference,
	)


def mark_read(account, message_id: str, typing: bool = False) -> None:
	"""Blue ticks for an inbound message; with typing=True also shows "typing…" for up to 25 s
	(or until our reply arrives). Best-effort: a failure never blocks the reply."""
	from excom.excom.services import whatsapp_service as ws

	payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
	if typing:
		payload["typing_indicator"] = {"type": "text"}
	try:
		token, base_url, version, phone_id = ws._resolve_credentials(account)
		r = ws.http_requests.post(f"{base_url}/{version}/{phone_id}/messages", json=payload,
			headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=(5, 15))
		if getattr(r, "status_code", 200) >= 400:
			frappe.log_error(title="Excom: WhatsApp read/typing indicator refused", message=f"{r.status_code}: {(getattr(r, 'text', '') or '')[:500]}")
	except Exception:
		frappe.log_error(title="Excom: WhatsApp read/typing indicator failed", message=json.dumps(payload))
