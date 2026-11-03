"""WhatsApp cart order → draft Sales Order (HLD-005 D6, D12, D18).

The cart's prices come from the Meta catalogue (one public price), so they are never trusted:
the order is re-priced by ERPNext from the customer's own price list and pricing rules. The
Sales Order stays a draft — sales checks stock and credit and submits it.

A sender with no linked Customer gets an acknowledgement and the order stays in the inbox
(a comment on the thread) for sales; no Sales Order is guessed.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate


def on_order(event: dict) -> None:
	message = event.get("message")
	thread = event.get("thread")
	if not message or not thread:
		return
	if frappe.db.get_value("Excom Message", message, "reference_doctype") == "Sales Order":
		return  # already turned into an order
	order = event.get("payload") or {}
	from excom.excom.services.commerce.handlers import send_text
	from excom.excom.services.commerce.rates import settings

	s = settings()
	customer = customer_for(event.get("identity"))
	if not customer:
		frappe.get_doc("Excom Thread", thread).add_comment(
			"Comment", _("WhatsApp order from a contact with no linked Customer — link the customer, then create the Sales Order. Items: {0}").format(
				", ".join(f"{i.get('quantity')} × {i.get('product_retailer_id')}" for i in order.get("product_items") or [])))
		send_text(thread, _("Thank you — we received your order. Our team will contact you shortly."), reply_to_message=message)
		return

	try:
		so, skipped = create_draft_sales_order(customer, order, event.get("account"))
	except Exception as e:
		# stock item without a warehouse, a held customer, … — sales finishes it by hand
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: WhatsApp order → Sales Order failed ({customer})", message=frappe.get_traceback())
		frappe.get_doc("Excom Thread", thread).add_comment("Comment", _("Could not create the Sales Order from this WhatsApp order for {0}: {1}").format(customer, str(e)[:300]))
		send_text(thread, _("Thank you — we received your order. Our team will confirm it shortly."), reply_to_message=message)
		frappe.db.commit()
		return
	if not so:
		send_text(thread, _("Thank you — we received your order. Our team will contact you shortly."), reply_to_message=message)
		return
	frappe.db.set_value("Excom Message", message, {"reference_doctype": "Sales Order", "reference_name": so}, update_modified=False)
	ack = (s.order_ack_message or _("Thank you — order {order} received.")).replace("{order}", so)
	if skipped:
		ack += "\n" + _("Not available: {0}").format(", ".join(skipped))
	send_text(thread, ack, reply_to_message=message, reference=("Sales Order", so))
	_ask_address_if_missing(customer, thread, message)


def customer_for(identity: str) -> str:
	"""The Customer linked to this contact (directly, or through a linked Contact)."""
	if not identity:
		return ""
	links = frappe.get_all("Omni Identity Link", filters={"parent": identity, "parenttype": "Omni Identity"}, fields=["linked_doctype", "linked_name"])
	for link in links:
		if link.linked_doctype == "Customer" and frappe.db.exists("Customer", link.linked_name):
			return link.linked_name
	for link in links:
		if link.linked_doctype == "Contact":
			customer = frappe.db.get_value("Dynamic Link", {"parenttype": "Contact", "parent": link.linked_name, "link_doctype": "Customer"}, "link_name")
			if customer:
				return customer
	return ""


def create_draft_sales_order(customer: str, order: dict, account_name: str = "") -> tuple[str, list]:
	"""Returns (Sales Order name or '', [retailer ids that could not be ordered])."""
	from excom.excom.services.commerce.rates import settings

	s = settings()
	company = (frappe.db.get_value("Excom Channel Account", account_name, "company") if account_name else "") or frappe.defaults.get_global_default("company")
	fallback_price_list = s.order_price_list or (frappe.db.get_value("Excom Channel Account", account_name, "catalog_price_list") if account_name else "")
	delivery = add_days(nowdate(), int(s.default_delivery_days or 7))
	items, skipped = [], []
	for line in order.get("product_items") or []:
		code = str(line.get("product_retailer_id") or "")
		qty = flt(line.get("quantity"))
		ok = code and qty > 0 and frappe.db.get_value("Item", code, ["disabled", "is_sales_item"], as_dict=True)
		if not ok or ok.disabled or not ok.is_sales_item:
			skipped.append(code or "?")
			continue
		items.append({"item_code": code, "qty": qty, "delivery_date": delivery})
	if not items:
		return "", skipped

	owner = s.order_owner or "Administrator"
	previous = frappe.session.user
	frappe.set_user(owner)  # the draft belongs to a real user, not to the webhook's Guest session
	try:
		so = frappe.new_doc("Sales Order")
		so.update({
			"customer": customer,
			"company": company,
			"transaction_date": nowdate(),
			"delivery_date": delivery,
			"order_type": "Sales",
		})
		for it in items:
			so.append("items", it)
		# ERPNext picks the price list as the desk does (customer, then customer group, then selling
		# settings); excom's fallback only applies when it finds none.
		so.set_missing_values()
		if not so.selling_price_list and fallback_price_list:
			so.selling_price_list = fallback_price_list
			for row in so.items:
				row.price_list_rate = row.rate = 0
			so.set_missing_values()
		so.calculate_taxes_and_totals()
		so.flags.ignore_permissions = True
		so.insert()
		if order.get("text"):
			so.add_comment("Comment", _("Customer's note on WhatsApp: {0}").format(order["text"]))
		return so.name, skipped
	finally:
		frappe.set_user(previous)


def _ask_address_if_missing(customer: str, thread: str, message: str) -> None:
	"""No shipping address on file and an Indian number: send WhatsApp's own address form."""
	has_address = frappe.db.sql(
		"""SELECT 1 FROM `tabDynamic Link` dl JOIN `tabAddress` a ON a.name = dl.parent
		WHERE dl.parenttype = 'Address' AND dl.link_doctype = 'Customer' AND dl.link_name = %s LIMIT 1""",
		customer,
	)
	if has_address:
		return
	from excom.excom.services import whatsapp_interactive as wi

	t = frappe.db.get_value("Excom Thread", thread, ["omni_identity"], as_dict=True)
	phone = frappe.db.get_value("Omni Identity", t.omni_identity, "primary_whatsapp") or ""
	if not phone.startswith("91"):
		return
	try:
		wi.send_to_thread(thread, wi.address_request(_("Where should we deliver this order?")), reply_to_message=message)
	except Exception:
		frappe.log_error(title="Excom: address request after order failed", message=frappe.get_traceback())


def on_address(event: dict) -> None:
	"""The dealer answered WhatsApp's address form: save it as the customer's shipping address
	(only when they have none yet — an existing address is never overwritten from a chat)."""
	customer = customer_for(event.get("identity"))
	values = (event.get("payload") or {}).get("values") if isinstance((event.get("payload") or {}).get("values"), dict) else (event.get("payload") or {})
	if not customer or not values:
		return
	if frappe.db.exists("Dynamic Link", {"parenttype": "Address", "link_doctype": "Customer", "link_name": customer}):
		return
	line1 = ", ".join(str(values.get(k)) for k in ("house_number", "floor_number", "tower_number", "building_name") if values.get(k)) or values.get("address") or ""
	line2 = ", ".join(str(values.get(k)) for k in ("address", "landmark_area") if values.get(k) and str(values.get(k)) != line1)
	if not line1 or not values.get("city"):
		return
	frappe.get_doc({
		"doctype": "Address",
		"address_title": customer,
		"address_type": "Shipping",
		"address_line1": line1[:140],
		"address_line2": line2[:140],
		"city": values.get("city"),
		"state": values.get("state") or "",
		"pincode": values.get("in_pin_code") or "",
		"country": "India",
		"phone": values.get("phone_number") or "",
		"is_shipping_address": 1,
		"links": [{"link_doctype": "Customer", "link_name": customer}],
	}).insert(ignore_permissions=True)
