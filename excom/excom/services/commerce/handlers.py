"""What happens when a dealer taps a rate-update button or sends a cart (HLD-005 D10, D12).

Registered in hooks.py → excom_inbound_handlers. Every reply here goes inside the 24-hour window
the tap itself opened (service messages: free for the first 1,000 a month per number, and not
counted against Meta's per-user marketing limit).

Payloads (set by rates.button_payloads):  RU:<rate update>:view | catalog | ask
"""

import frappe
from frappe import _

from excom.excom.services import whatsapp_interactive as wi


def on_inbound(event: dict) -> None:
	kind, payload = event.get("kind"), event.get("payload")
	if kind == "button" and isinstance(payload, str) and payload.startswith("RU:"):
		_rate_update_button(event, payload)
	elif kind == "button" and isinstance(payload, str) and payload.startswith("RR:"):
		from excom.excom.services.commerce.offers import on_offer_button

		on_offer_button(event, payload)
	elif kind == "flow" and isinstance(payload, dict) and str(payload.get("flow_token") or "").startswith("RR1:"):
		from excom.excom.services.commerce.requests import on_flow_answer

		on_flow_answer(event)
	elif kind == "order":
		from excom.excom.services.commerce.orders import on_order

		on_order(event)
	elif kind == "address":
		from excom.excom.services.commerce.orders import on_address

		on_address(event)


def _rate_update_button(event: dict, payload: str) -> None:
	parts = payload.split(":")
	if len(parts) != 3:
		return
	_, name, action = parts
	ru = frappe.db.get_value("Excom Rate Update", name, ["name", "status", "price_list"], as_dict=True)
	thread = event.get("thread")
	if not ru or ru.status not in ("Sending", "Published") or not thread:  # taps can arrive while it is still sending
		return
	account = frappe.get_doc("Excom Channel Account", event["account"])
	if event.get("provider_message_id"):
		wi.mark_read(account, event["provider_message_id"], typing=True)
	from excom.excom.services.commerce.rates import settings

	s = settings()
	reference = ("Excom Rate Update", name)
	reply_to = event.get("message") or ""
	if action == "view":
		interactive = view_and_order(name, account, s.view_message or _("Today's changed rates"))
		if interactive:
			wi.send_to_thread(thread, interactive, reply_to_message=reply_to, reference=reference)
	elif action == "catalog":
		if not account.get("wa_catalog_id"):
			return
		top = _changed_items(name)[:1]
		wi.send_to_thread(thread, wi.catalog_message(s.catalogue_message or _("Our full catalogue"), top[0] if top else ""),
			reply_to_message=reply_to, reference=reference)
	elif action == "ask":
		from excom.excom.services.commerce.requests import send_request_form

		# the request form when it is set up (Rate Request → Raven), otherwise the text reply
		if not send_request_form(thread, event.get("identity"), account, rate_update=name, reply_to_message=reply_to):
			send_text(thread, s.ask_price_reply or _("Tell us the item and quantity you need."), reply_to_message=reply_to, reference=reference)


def _changed_items(rate_update: str) -> list[str]:
	"""Changed items, biggest change first (one entry per item)."""
	rows = frappe.get_all("Excom Rate Update Item", filters={"parent": rate_update, "parenttype": "Excom Rate Update"},
		fields=["item_code", "change", "catalog_status"])
	# items Meta rejected are not in the catalogue: a product message naming them fails or drops them
	rows = [r for r in rows if not str(r.catalog_status or "").startswith("error")]
	rows.sort(key=lambda r: abs(r.change or 0), reverse=True)
	return list(dict.fromkeys(r.item_code for r in rows))


def view_and_order(rate_update: str, account, body: str) -> dict | None:
	"""1 item → product; 2–10 → product carousel; more → product list by item group (≤ 30)."""
	catalog_id = account.get("wa_catalog_id")
	if not catalog_id:
		return None
	items = _changed_items(rate_update)
	if not items:
		return None
	if len(items) == 1:
		return wi.product(catalog_id, items[0], body=body)
	if len(items) <= 10:
		return wi.product_carousel(catalog_id, body, items)
	sections: dict[str, list] = {}
	for code in items[:30]:
		group = frappe.db.get_value("Item", code, "item_group") or _("Products")
		sections.setdefault(group, []).append(code)
	groups = list(sections.items())
	if len(groups) > 10:  # Meta allows 10 sections: fold the rest into the last
		head, tail = groups[:9], groups[9:]
		groups = head + [(_("More"), [c for _g, codes in tail for c in codes])]
	return wi.product_list(catalog_id, _("New rates"), body, [{"title": g[:24], "retailer_ids": codes} for g, codes in groups])


def send_text(thread: str, text: str, reply_to_message: str = "", reference: tuple = None) -> str:
	"""Plain-text reply from the system (not an agent): sent and recorded, thread not claimed."""
	from excom.excom.services.thread_service import record_outbound_message
	from excom.excom.services.whatsapp_service import send_text_message

	t = frappe.get_doc("Excom Thread", thread)
	identity = frappe.db.get_value("Omni Identity", t.omni_identity, ["primary_whatsapp", "primary_phone", "wa_bsuid"], as_dict=True) or {}
	to = identity.get("primary_whatsapp") or identity.get("primary_phone") or identity.get("wa_bsuid")
	if not to or not wi.window_open(thread):
		return ""
	from excom.excom.services.whatsapp_service import assert_template_allowed

	assert_template_allowed(to, "SERVICE", identity=t.omni_identity)
	account = frappe.get_doc(t.account_doctype, t.account)
	result = send_text_message(account, to, text)
	return record_outbound_message(t, content_text=text, message_type="Text", provider_message_id=result.get("provider_message_id", ""),
		reply_to=reply_to_message, reference=reference)
