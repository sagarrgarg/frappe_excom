"""Rate requests in Raven (HLD-005 D13): a bot posts each request in the sales channel with a
document card and opens a thread on it; sales answers by typing in that thread:

	offer 4150 for 500                 one rate, from qty 500
	offer 4150 for 500, 4100 for 1000  slabs
	offer 4150 for 500 valid 2d        validity in days (d) or hours (h)
	chat                               take the WhatsApp conversation over yourself
	approve / reject                   approver only, for an offer above the discount limit

Raven messages cannot carry buttons, so thread replies are the interface (they work on Raven
web and mobile). Everything here is a no-op when Raven is not installed or not configured.
"""

import frappe
from frappe import _
from frappe.utils import escape_html


def enabled() -> bool:
	if "raven" not in frappe.get_installed_apps():
		return False
	from excom.excom.services.commerce.rates import settings

	s = settings()
	return bool(s.raven_channel and s.raven_bot and frappe.db.exists("Raven Channel", s.raven_channel) and frappe.db.exists("Raven Bot", s.raven_bot))


def _bot():
	from excom.excom.services.commerce.rates import settings

	return frappe.get_doc("Raven Bot", settings().raven_bot)


def mention(user: str) -> str:
	name = frappe.db.get_value("User", user, "full_name") or user
	return f'<span class="mention" data-type="userMention" data-id="{escape_html(user)}" data-label="{escape_html(name)}">@{escape_html(name)}</span>'


def say(request: str, html: str) -> str:
	"""Post into the request's thread (or the channel if it has none). Returns the message id."""
	if not enabled():
		return ""
	from excom.excom.services.commerce.rates import settings

	req = frappe.db.get_value("Excom Rate Request", request, ["raven_thread"], as_dict=True) or {}
	try:
		return _bot().send_message(channel_id=req.get("raven_thread") or settings().raven_channel, text=html)
	except Exception:
		frappe.log_error(title=f"Excom: Raven message for {request} failed", message=frappe.get_traceback())
		return ""


def post_request(request: str) -> None:
	"""The request card in the sales channel, plus a thread for the answer."""
	if not enabled():
		return
	from excom.excom.services.commerce.rates import _symbol, settings

	req = frappe.get_doc("Excom Rate Request", request)
	who = req.customer or frappe.db.get_value("Omni Identity", req.omni_identity, "display_name") or req.omni_identity
	sym = _symbol(frappe.db.get_value("Price List", req.price_list, "currency") or "") if req.price_list else ""
	lines = [
		f"<b>Rate request {escape_html(req.name)}</b> — {escape_html(who)}",
		f"{req.qty:g} {escape_html(req.uom or '')} × {escape_html(req.item_name or req.item_code)} ({escape_html(req.item_code)})",
		f"List {sym}{req.list_rate:,.2f}" + (f" · asks {sym}{req.target_rate:,.2f}" if req.target_rate else ""),
	]
	if req.note:
		lines.append(_("Note: {0}").format(escape_html(req.note)))
	lines.append(_("Reply in this thread: <code>offer 4150 for 500</code> (slabs: <code>, 4100 for 1000</code>; <code>valid 2d</code>) · <code>chat</code> to take over"))
	if not req.customer:
		lines.append(_("⚠ No Customer is linked to this contact: an accepted offer cannot become a Sales Order until one is."))
	sales = _sales_user(req.customer)
	if sales:
		lines.append(mention(sales))
	try:
		bot = _bot()
		root = bot.send_message(channel_id=settings().raven_channel, text="<br>".join(lines), link_doctype="Excom Rate Request", link_document=req.name)
		frappe.db.commit()  # the card stays even if the thread cannot be opened
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: Raven post for {request} failed", message=frappe.get_traceback())
		return
	try:
		thread = _open_thread(root, [sales] if sales else [])
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: Raven thread for {request} failed", message=frappe.get_traceback())
		thread = ""
	req.db_set({"raven_message": root, "raven_thread": thread or None}, update_modified=False)
	frappe.db.commit()


def _open_thread(message_id: str, extra_users: list = ()) -> str:
	"""Raven's own create_thread, done as the bot: the thread holds the sales channel's members
	(sales and approvers) plus the customer's sales person — not every Raven user."""
	root = frappe.get_doc("Raven Message", message_id)
	parent = frappe.get_cached_doc("Raven Channel", root.channel_id)
	ch = frappe.get_doc({"doctype": "Raven Channel", "channel_name": message_id, "workspace": parent.workspace,
		"type": "Private", "is_thread": 1, "description": (root.content or "")[:140]})
	ch.flags.do_not_add_member = True
	ch.flags.ignore_channel_member_check = True
	ch.flags.is_created_by_bot = True
	ch.insert(ignore_permissions=True)
	members = frappe.get_all("Raven Channel Member", filters={"channel_id": parent.name}, pluck="user_id")
	for user in dict.fromkeys([*members, *[u for u in extra_users if u]]):
		if not frappe.db.exists("Raven User", user):
			continue
		member = frappe.get_doc({"doctype": "Raven Channel Member", "channel_id": ch.name, "user_id": user})
		member.flags.ignore_system_message = True
		member.insert(ignore_permissions=True)
	root.is_thread = 1
	root.save(ignore_permissions=True)
	return ch.name


def _sales_user(customer: str) -> str:
	"""The customer's sales person as a user (Customer → Sales Team → Sales Person → Employee → User)."""
	if not customer:
		return ""
	person = frappe.db.get_value("Sales Team", {"parenttype": "Customer", "parent": customer}, "sales_person", order_by="idx asc")
	employee = frappe.db.get_value("Sales Person", person, "employee") if person else ""
	return (frappe.db.get_value("Employee", employee, "user_id") if employee else "") or ""


def _thread_root(channel_id: str):
	"""The message a thread channel was opened on (a thread's id is its root message's id)."""
	if not frappe.get_cached_value("Raven Channel", channel_id, "is_thread"):
		return None
	return frappe.db.get_value("Raven Message", channel_id, ["link_doctype", "link_document", "is_bot_message", "bot", "channel_id"], as_dict=True)


def _bot_user() -> str:
	from excom.excom.services.commerce.rates import settings

	return frappe.db.get_value("Raven Bot", settings().raven_bot, "raven_user") or ""


def on_raven_message(doc, method=None) -> None:
	"""doc_events Raven Message after_insert: a human reply in a rate-request thread is a command.

	Anyone can post a message linked to a document, so the link alone proves nothing. A command
	counts only in the thread excom opened on a card its own bot posted in the configured channel.
	"""
	if doc.get("is_bot_message") or doc.get("message_type") not in ("Text", None, ""):
		return
	root = _thread_root(doc.channel_id)
	if not root or root.link_doctype != "Excom Rate Request" or not root.link_document or not enabled():
		return
	from excom.excom.services.commerce.rates import settings

	if not root.is_bot_message or root.bot != _bot_user() or root.channel_id != settings().raven_channel:
		return
	if frappe.db.get_value("Excom Rate Request", root.link_document, "raven_thread") != doc.channel_id:
		return
	text = (doc.get("content") or frappe.utils.strip_html(doc.get("text") or "")).strip()
	if not text:
		return
	frappe.enqueue("excom.excom.services.commerce.offers.on_command", request=root.link_document, text=text,
		user=doc.owner, queue="short", enqueue_after_commit=True, now=frappe.flags.in_test)
