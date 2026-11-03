"""Ask Best Price: the request form, and the Rate Request it creates (HLD-005 D9–D11, D13).

The form is a *static* WhatsApp Flow (no endpoint): its first screen is filled per message with
the dealer's items and today's rate, and the answer comes back as an `nfm_reply` carrying our
flow_token. The token is signed, so an answer can only create a request for the contact it was
sent to — a forged or copied token is ignored.
"""

import hashlib
import hmac

import frappe
import frappe.utils.password
from frappe import _
from frappe.utils import flt

MAX_FORM_ITEMS = 200  # Meta's Dropdown limit
TOKEN_PREFIX = "RR1"


# ── flow token ───────────────────────────────────────────────────────────────

def _sig(payload: str) -> str:
	key = frappe.utils.password.get_encryption_key().encode()
	return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:24]


def make_token(identity: str, rate_update: str = "") -> str:
	payload = f"{TOKEN_PREFIX}:{identity}:{rate_update or '-'}:{frappe.generate_hash(length=10)}"
	return f"{payload}:{_sig(payload)}"


def read_token(token: str) -> dict | None:
	"""{identity, rate_update} for a token excom signed, else None."""
	parts = (token or "").split(":")
	if len(parts) != 5 or parts[0] != TOKEN_PREFIX:
		return None
	payload, sig = ":".join(parts[:4]), parts[4]
	if not hmac.compare_digest(_sig(payload), sig):
		return None
	return {"identity": parts[1], "rate_update": "" if parts[2] == "-" else parts[2]}


# ── the form ─────────────────────────────────────────────────────────────────

def form_items(price_list: str, first: list = None) -> list:
	"""Dropdown options: id = item code, title = name (≤ 30), description = today's rate."""
	from excom.excom.services.commerce.rates import _symbol, current_prices

	codes = set(frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1}, pluck="item_code"))
	prices = current_prices(price_list, codes)
	sym = _symbol(frappe.db.get_value("Price List", price_list, "currency") or "")
	by_item = {}
	for (code, _uom), (rate, name) in prices.items():
		if code not in by_item:
			by_item[code] = {"id": code, "title": (name or code)[:30], "description": f"{sym}{rate:,.2f} today"[:300]}
	order = list(dict.fromkeys([c for c in (first or []) if c in by_item] + sorted(by_item, key=lambda c: by_item[c]["title"])))
	return [by_item[c] for c in order][:MAX_FORM_ITEMS]


def send_request_form(thread: str, identity: str, account, rate_update: str = "", reply_to_message: str = "") -> str:
	"""Send the Ask Best Price form into the thread. Returns the Excom Message name ('' if not set up)."""
	from excom.excom.services import whatsapp_interactive as wi
	from excom.excom.services.commerce.handlers import _changed_items
	from excom.excom.services.commerce.rates import settings

	s = settings()
	flow = frappe.db.get_value("WhatsApp Flow", s.request_flow, ["flow_id", "status"], as_dict=True) if s.request_flow else None
	price_list = account.get("catalog_price_list")
	if not flow or not flow.flow_id or not price_list:
		return ""
	if flow.status not in ("Published", "Draft"):  # Blocked / Deprecated / Throttled: Meta refuses or the dealer cannot submit
		return ""
	items = form_items(price_list, first=_changed_items(rate_update) if rate_update else [])
	if not items:
		return ""
	interactive = wi.flow(
		flow_id=flow.flow_id,
		cta=s.request_flow_cta or _("Ask best price"),
		body=_("Choose the item and quantity — our sales team will reply with our best price."),
		flow_token=make_token(identity, rate_update),
		screen=s.request_flow_screen or "REQUEST",
		data={"items": items},
		draft=flow.status != "Published",
	)
	try:
		return wi.send_to_thread(thread, interactive, reply_to_message=reply_to_message,
			reference=("Excom Rate Update", rate_update) if rate_update else None)
	except Exception:
		# the caller falls back to the plain text reply
		frappe.log_error(title="Excom: Ask Best Price form not sent", message=frappe.get_traceback())
		return ""


def _price_list_for(customer: str) -> str:
	"""The customer's selling price list, as ERPNext would pick it: Customer, then Customer Group."""
	if not customer:
		return ""
	pl, group = frappe.db.get_value("Customer", customer, ["default_price_list", "customer_group"]) or ("", "")
	if not pl and group:
		pl = frappe.db.get_value("Customer Group", group, "default_price_list")
	return pl or ""


# ── the answer ───────────────────────────────────────────────────────────────

def on_flow_answer(event: dict) -> str:
	"""A completed request form → Excom Rate Request (+ Raven post). Returns the request name or ''."""
	answer = event.get("payload") or {}
	token = answer.get("flow_token") or ""
	claim = read_token(token)
	if not claim or claim["identity"] != event.get("identity"):
		return ""  # not ours, forged, or sent from another contact's token
	if frappe.db.exists("Excom Rate Request", {"flow_token": token}):
		return ""  # the same answer again
	item_code = str(answer.get("item") or "").strip()
	qty = flt(answer.get("qty"))
	if not item_code or qty <= 0 or not frappe.db.exists("Item", item_code):
		from excom.excom.services.commerce.handlers import send_text

		send_text(event.get("thread"), _("Sorry, we could not read the item or quantity — please try again."), reply_to_message=event.get("message") or "")
		return ""

	from excom.excom.services.commerce.orders import customer_for
	from excom.excom.services.commerce.rates import current_prices

	account = frappe.get_doc("Excom Channel Account", event["account"])
	customer = customer_for(event.get("identity"))
	price_list = _price_list_for(customer) or account.get("catalog_price_list")
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	uom = str(answer.get("uom") or "").strip()
	if uom != stock_uom and not frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "parenttype": "Item", "uom": uom}):
		uom = stock_uom  # not a unit this item is sold in
	prices = current_prices(price_list, {item_code}) if price_list else {}
	list_rate = (prices.get((item_code, uom)) or next((v for k, v in prices.items() if k[0] == item_code), (0, "")))[0]

	req = frappe.get_doc({
		"doctype": "Excom Rate Request",
		"status": "Open",
		"customer": customer or None,
		"omni_identity": event["identity"],
		"excom_thread": event.get("thread"),
		"rate_update": claim["rate_update"] or None,
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name"),
		"qty": qty,
		"uom": uom,
		"price_list": price_list,
		"list_rate": list_rate,
		"target_rate": flt(answer.get("target_rate")) or None,
		"note": str(answer.get("note") or "")[:1000],
		"flow_token": token,
		"request_message": event.get("message"),
	}).insert(ignore_permissions=True)
	frappe.db.commit()

	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.handlers import send_text

	try:  # sales hears about it even if the acknowledgement cannot be sent
		send_text(event.get("thread"), _("Request {0} received: {1} × {2}. Our sales team will reply shortly.").format(req.name, f"{qty:g}", req.item_name or item_code),
			reply_to_message=event.get("message") or "", reference=("Excom Rate Request", req.name))
	except Exception:
		frappe.log_error(title=f"Excom: acknowledgement for {req.name} failed", message=frappe.get_traceback())
	raven_bridge.post_request(req.name)
	return req.name
