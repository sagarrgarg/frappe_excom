"""Offers on rate requests (HLD-005 D13–D17).

Sales → (Raven thread) `offer 4150 for 500[, 4100 for 1000] [valid 2d]` → guardrail → WhatsApp:
  * deeper than `max_discount_pct_without_approval` below list (or no list rate to compare
    with) waits for an approver's `approve`; more than 50 % below list needs `confirm` at the end;
  * the offer is staged (`pending_offer`) and only becomes the live offer once it was sent —
    with a new `offer_version` that is part of every button, so an old message's Accept can
    never accept terms the dealer did not see;
  * inside the 24-hour window it is reply buttons, outside it the approved offer template;
  * Accept (this version, valid, once) → draft Sales Order at the offered rate.
An offer never becomes an Item Price: it is for this request only.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, flt, get_datetime, now_datetime

_SLAB = re.compile(r"(\d+(?:\.\d+)?)\s*(?:for|@|x)\s*(\d+(?:\.\d+)?)", re.I)
_VALID = re.compile(r"\bvalid\s+(\d+(?:\.\d+)?)\s*(d|day|days|h|hr|hrs|hour|hours)\b", re.I)
_DIGIT_COMMA = re.compile(r"(?<=\d),(?=\d)")
SALES_ROLES = {"Excom Agent", "Excom Admin", "Sales User", "Sales Manager", "System Manager"}
CONFIRM_BELOW_PCT = 50


def parse_offer(text: str):
	"""'offer 4,150 for 500, 4100 for 1,000 valid 2d' → ([(4150, 500), (4100, 1000)], 48.0) — or None.

	Digit-grouping commas (4,150) are removed first; slabs are separated by ", ".
	"""
	text = (text or "").strip()
	if not text.lower().startswith("offer"):
		return None
	body = _DIGIT_COMMA.sub("", text[5:])
	body = re.sub(r"\bconfirm\s*$", "", body, flags=re.I)
	valid = None
	m = _VALID.search(body)
	if m:
		n = flt(m.group(1))
		valid = n * 24 if m.group(2).lower().startswith("d") else n
		body = body[: m.start()] + body[m.end():]
	slabs = [(flt(r), flt(q)) for r, q in _SLAB.findall(body) if flt(r) > 0 and flt(q) > 0]
	if not slabs:
		return None
	return sorted(slabs, key=lambda s: s[1]), valid


# ── commands from the Raven thread ───────────────────────────────────────────

def on_command(request: str, text: str, user: str) -> None:
	from excom.excom.services.commerce import raven_bridge

	try:
		_command(request, text, user)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: Raven command on {request} failed", message=frappe.get_traceback())
		raven_bridge.say(request, _("That did not work: {0}").format(escape_html(str(e)[:200])))


def _command(request: str, text: str, user: str) -> None:
	from excom.excom.services.commerce import raven_bridge

	req = frappe.get_doc("Excom Rate Request", request)
	cmd = text.strip().lower()
	if req.status in ("Accepted", "Expired", "Declined"):
		if cmd.startswith(("offer", "approve", "reject")) or cmd == "chat":
			raven_bridge.say(request, _("{0} is {1} — nothing changed.").format(request, req.status))
		return

	if cmd.startswith("offer") or cmd == "chat":
		if not _is_sales(user):
			raven_bridge.say(request, _("Only the sales team can make offers or take chats ({0}).").format(raven_bridge.mention(user)))
			return

	if cmd.startswith("offer"):
		parsed = parse_offer(text)
		if not parsed:
			raven_bridge.say(request, _("Could not read that. Write e.g. <code>offer 4150 for 500</code> or <code>offer 4150 for 500, 4100 for 1000 valid 2d</code>."))
			return
		_claim(req, user)
		make_offer(request, parsed[0], parsed[1], user, confirmed=bool(re.search(r"\bconfirm\s*$", text, re.I)))
	elif cmd == "chat":
		take_over(request, user)
	elif cmd in ("approve", "approved"):
		approve(request, user)
	elif cmd in ("reject", "rejected"):
		reject(request, user)


def _claim(req, user: str) -> None:
	from excom.excom.services.commerce import raven_bridge

	if not req.claimed_by:
		req.db_set({"claimed_by": user, "status": "Claimed" if req.status == "Open" else req.status}, update_modified=False)
		raven_bridge.say(req.name, _("Claimed by {0}").format(raven_bridge.mention(user)))


def discount_pct(list_rate, slabs) -> float | None:
	"""How far the deepest slab is below list (None when there is no list rate to compare with)."""
	if not flt(list_rate):
		return None
	lowest = min(r for r, _q in slabs)
	return max((flt(list_rate) - lowest) / flt(list_rate) * 100, 0.0)


def make_offer(request: str, slabs: list, valid_hours: float, user: str, confirmed: bool = False) -> None:
	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.rates import settings

	s = settings()
	req = frappe.get_doc("Excom Rate Request", request)
	pct = discount_pct(req.list_rate, slabs)
	if pct is not None and pct > CONFIRM_BELOW_PCT and not confirmed:
		raven_bridge.say(request, _("That is {0}% below list. If you mean it, send it again with <code>confirm</code> at the end.").format(f"{pct:.0f}"))
		return
	limit = flt(s.max_discount_pct_without_approval)
	# no list rate to compare with, or a 0 % limit, means every offer is approved
	needs_approval = pct is None or limit <= 0 or pct > limit
	req.db_set("pending_offer", json.dumps({"slabs": slabs, "valid_hours": flt(valid_hours or s.offer_validity_hours or 24), "by": user}), update_modified=False)
	req.db_set("offered_by", user, update_modified=False)
	if needs_approval and not _is_approver(user):
		req.db_set({"status": "Pending Approval", "approved_by": None}, update_modified=False)
		frappe.db.commit()
		why = _("no list rate to compare with") if pct is None else _("{0}% below list").format(f"{pct:.1f}")
		approvers = " ".join(raven_bridge.mention(u) for u in _approvers()[:5])
		raven_bridge.say(request, _("Needs approval ({0}). {1} reply <code>approve</code> or <code>reject</code>.").format(why, approvers))
		return
	req.db_set("approved_by", user if needs_approval else None, update_modified=False)
	frappe.db.commit()
	send_offer(request)


def approve(request: str, user: str) -> None:
	from excom.excom.services.commerce import raven_bridge

	if not _is_approver(user):
		raven_bridge.say(request, _("Only {0} can approve.").format(escape_html(_approver_role())))
		return
	# exactly once, even if two approvers answer together
	frappe.db.sql("""UPDATE `tabExcom Rate Request` SET approved_by = %s
		WHERE name = %s AND status = 'Pending Approval' AND IFNULL(approved_by, '') = ''""", (user, request))
	if not frappe.db.sql("SELECT ROW_COUNT()")[0][0]:
		raven_bridge.say(request, _("Nothing to approve on {0}.").format(request))
		return
	frappe.db.commit()
	send_offer(request)


def reject(request: str, user: str) -> None:
	"""Drop the staged offer. A live offer the dealer already has stays valid."""
	from excom.excom.services.commerce import raven_bridge

	req = frappe.get_doc("Excom Rate Request", request)
	if req.status != "Pending Approval" or not _is_approver(user):
		raven_bridge.say(request, _("Nothing to reject, or you are not an approver."))
		return
	live = bool(req.offers) and req.valid_till and get_datetime(req.valid_till) > now_datetime()
	req.db_set({"status": "Offered" if live else "Claimed", "approved_by": None, "pending_offer": None}, update_modified=False)
	raven_bridge.say(request, _("Rejected by {0}. Make a new offer.").format(raven_bridge.mention(user)))


def take_over(request: str, user: str) -> None:
	"""`chat`: the WhatsApp conversation becomes this person's (with their team), like a claim in the inbox."""
	from excom.excom.services.commerce import raven_bridge

	req = frappe.get_doc("Excom Rate Request", request)
	if req.excom_thread:
		from excom.excom.doctype.excom_thread.excom_thread import can_access
		from excom.excom.services.crm_visibility import team_for_user

		if not can_access(req.excom_thread, user):
			holder = frappe.db.get_value("Excom Thread", req.excom_thread, "assigned_to")
			raven_bridge.say(request, _("{0} cannot open this conversation{1}. Ask an Excom Admin to transfer it.").format(
				raven_bridge.mention(user), _(" (it is with {0})").format(raven_bridge.mention(holder)) if holder else ""))
			return
		update = {"assigned_to": user}
		team = team_for_user(user)
		if team:
			update["assigned_team"] = team
		frappe.db.set_value("Excom Thread", req.excom_thread, update, update_modified=False)
		frappe.publish_realtime("excom:thread_updated", {"thread": req.excom_thread, "event": "assigned"}, after_commit=True)
	_claim(req, user)
	req.db_set({"status": "Handled in Chat", "pending_offer": None}, update_modified=False)
	raven_bridge.say(request, _("{0} is taking this over in the WhatsApp chat ({1}).").format(raven_bridge.mention(user), escape_html(req.excom_thread or "")))


def _approver_role() -> str:
	from excom.excom.services.commerce.rates import settings

	return settings().approver_role or "Sales Manager"


def _is_approver(user: str) -> bool:
	roles = frappe.get_roles(user)
	return _approver_role() in roles or "System Manager" in roles


def _is_sales(user: str) -> bool:
	return bool(SALES_ROLES.intersection(frappe.get_roles(user))) or _is_approver(user)


def _approvers() -> list:
	users = frappe.get_all("Has Role", filters={"role": _approver_role(), "parenttype": "User"}, pluck="parent")
	return [u for u in dict.fromkeys(users) if u not in ("Administrator", "Guest") and frappe.db.get_value("User", u, "enabled")]


# ── to the dealer ────────────────────────────────────────────────────────────

def offer_text(price_list, slabs, valid_till) -> tuple[str, str]:
	from excom.excom.services.commerce.rates import _symbol

	sym = _symbol(frappe.db.get_value("Price List", price_list, "currency") or "") if price_list else ""
	text = ", ".join(f"{sym}{flt(r):,.2f} from {flt(q):g}" for r, q in sorted(slabs, key=lambda s: s[1]))
	till = ""
	if valid_till:
		till = f"{frappe.utils.format_datetime(valid_till, 'd MMM, h:mm a')} {frappe.utils.get_system_timezone()}"
	return text, till


def send_offer(request: str) -> None:
	"""Send the staged offer; only a successful send makes it the live offer (new version)."""
	from excom.excom.services import whatsapp_interactive as wi
	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.rates import settings

	req = frappe.get_doc("Excom Rate Request", request)
	staged = json.loads(req.pending_offer or "null")
	if not staged:
		raven_bridge.say(request, _("No offer to send on {0}.").format(request))
		return
	slabs = [tuple(s) for s in staged["slabs"]]
	version = cint(req.offer_version) + 1
	valid_till = add_to_date(now_datetime(), hours=flt(staged.get("valid_hours") or 24))  # from the moment it is sent
	text, till = offer_text(req.price_list, slabs, valid_till)
	item = req.item_name or req.item_code
	buttons = [{"id": f"RR:{req.name}:accept:{version}", "title": _("Accept")}, {"id": f"RR:{req.name}:decline:{version}", "title": _("Decline")},
		{"id": f"RR:{req.name}:talk:{version}", "title": _("Talk to sales")}]
	try:
		if req.excom_thread and wi.window_open(req.excom_thread):
			body = _("Offer for you ({0}): {1} — {2}. Valid till {3}.").format(req.name, item, text, till)
			msg = wi.send_to_thread(req.excom_thread, wi.reply_buttons(body, buttons), reply_to_message=req.request_message or "",
				reference=("Excom Rate Request", req.name))
		else:
			msg = _send_offer_template(req, item, slabs, text, till, [b["id"] for b in buttons], settings())
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: offer {request} not sent", message=frappe.get_traceback())
		live = bool(req.offers) and req.valid_till and get_datetime(req.valid_till) > now_datetime()
		frappe.db.set_value("Excom Rate Request", request, {"pending_offer": None, "approved_by": req.approved_by if req.status == "Offered" else None,
			"status": "Offered" if live else ("Claimed" if req.claimed_by else "Open")}, update_modified=False)
		frappe.db.commit()
		raven_bridge.say(request, _("Could not send the offer on WhatsApp: {0}").format(escape_html(str(e)[:200])))
		return
	req.reload()
	req.set("offers", [{"rate": r, "qty": q} for r, q in slabs])
	req.update({"status": "Offered", "offer_message": msg or None, "offer_version": version, "valid_till": valid_till, "pending_offer": None})
	req.flags.ignore_links = True  # users written by commands are checked when they are set
	req.save(ignore_permissions=True)
	frappe.db.commit()
	raven_bridge.say(request, _("Offer sent: {0}, valid till {1}.").format(escape_html(text), escape_html(till)))


def _send_offer_template(req, item, slabs, text, till, payloads, s) -> str:
	if not s.offer_template:
		raise frappe.ValidationError(_("The 24-hour window is closed and no Offer Template is set in Excom Commerce Settings."))
	from excom.excom.api.chat import _build_template_components, _build_template_preview
	from excom.excom.services.thread_service import record_outbound_message
	from excom.excom.services.whatsapp_service import send_template_message

	template = frappe.get_doc("WhatsApp Templates", s.offer_template)
	rate, qty = sorted(slabs, key=lambda s: s[1])[0]
	values = {"request": req.name, "item": item, "qty": f"{flt(qty):g}", "rate": f"{flt(rate):,.2f}",
		"list_rate": f"{flt(req.list_rate):,.2f}", "offer": text, "valid_till": till}
	if not template.uses_named_parameters():
		from excom.excom.whatsapp_template_utils import body_variable_slot_count

		values = [req.name, item, text, till][: body_variable_slot_count(template)]
	thread = frappe.get_doc("Excom Thread", req.excom_thread)
	identity = frappe.db.get_value("Omni Identity", req.omni_identity, ["primary_whatsapp", "primary_phone", "wa_bsuid"], as_dict=True) or {}
	to = identity.get("primary_whatsapp") or identity.get("primary_phone") or identity.get("wa_bsuid")
	account = frappe.get_doc(thread.account_doctype, thread.account)
	components = _build_template_components(template, values, account=account, button_payloads=payloads)
	result = send_template_message(account, to, template.actual_name or template.name, template.language_code or "en", components=components, identity=req.omni_identity)
	return record_outbound_message(thread, content_text=_build_template_preview(template, values), message_type="Template",
		provider_message_id=result.get("provider_message_id", ""), reference=("Excom Rate Request", req.name), template=template.name)


# ── the dealer's answer ──────────────────────────────────────────────────────

def on_offer_button(event: dict, payload: str) -> None:
	"""RR:<request>:accept|decline|talk:<version> — only from the contact, only for the live version."""
	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.handlers import send_text

	parts = payload.split(":")
	if len(parts) != 4:
		return
	_p, name, action, version = parts
	req = frappe.db.get_value("Excom Rate Request", name, ["name", "omni_identity", "status", "valid_till", "excom_thread", "offer_version", "claimed_by"], as_dict=True)
	if not req or req.omni_identity != event.get("identity"):
		return  # the payload is only a claim: this offer was not made to this contact
	thread, reply_to = event.get("thread") or req.excom_thread, event.get("message") or ""
	if action == "talk":
		send_text(thread, _("Our sales team will contact you shortly."), reply_to_message=reply_to)
		raven_bridge.say(name, _("The dealer wants to talk. {0}").format(raven_bridge.mention(req.claimed_by) if req.claimed_by else ""))
		return
	if cint(version) != cint(req.offer_version):
		send_text(thread, _("This offer was replaced by a newer one — please use the latest message."), reply_to_message=reply_to)
		return
	if req.status == "Accepted":
		send_text(thread, _("You already accepted {0}.").format(name), reply_to_message=reply_to)
		return
	expired = req.status == "Expired" or (req.valid_till and get_datetime(req.valid_till) < now_datetime())
	if expired:
		if req.status == "Offered":  # a new offer waiting for approval keeps the request alive
			frappe.db.set_value("Excom Rate Request", name, "status", "Expired")
		send_text(thread, _("Sorry, this offer has expired. Tap Ask Best Price again and we will send a fresh one."), reply_to_message=reply_to)
		if action == "accept":
			raven_bridge.say(name, _("The dealer tried to accept {0} after it expired.").format(name))
		return
	if req.status not in ("Offered", "Pending Approval"):  # Pending: a new offer is waiting, the live one still counts
		send_text(thread, _("Thank you — our team will be in touch shortly."), reply_to_message=reply_to)
		return
	if action == "accept":
		accept(name, thread, reply_to, cint(version))
	elif action == "decline":
		frappe.db.set_value("Excom Rate Request", name, {"status": "Declined", "pending_offer": None})
		send_text(thread, _("Thank you — noted. Ask us any time for a fresh price."), reply_to_message=reply_to)
		raven_bridge.say(name, _("The dealer declined the offer."))


def best_slab(req) -> tuple:
	"""(rate, qty) for the requested qty: the deepest slab the qty reaches, else the first slab at its qty."""
	slabs = sorted(((flt(o.rate), flt(o.qty)) for o in req.offers), key=lambda s: s[1])
	reached = [s for s in slabs if flt(req.qty) >= s[1]]
	if reached:
		return reached[-1][0], flt(req.qty)
	return slabs[0][0], slabs[0][1]


def accept(request: str, thread: str, reply_to: str, version: int) -> None:
	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.handlers import send_text
	from excom.excom.services.commerce.orders import create_draft_sales_order

	# exactly once, for this version (two taps at the same time create one order)
	frappe.db.sql("""UPDATE `tabExcom Rate Request` SET status = 'Accepted', pending_offer = NULL
		WHERE name = %s AND status IN ('Offered', 'Pending Approval') AND offer_version = %s""", (request, version))
	if not frappe.db.sql("SELECT ROW_COUNT()")[0][0]:
		return
	frappe.db.commit()
	req = frappe.get_doc("Excom Rate Request", request)
	if not req.customer:
		send_text(thread, _("Thank you — accepted. Our team will contact you to confirm the order."), reply_to_message=reply_to)
		raven_bridge.say(request, _("Accepted, but the contact has no linked Customer: create the Sales Order by hand."))
		return
	rate, qty = best_slab(req)
	try:
		so, _skipped = create_draft_sales_order(req.customer, {"product_items": [{"product_retailer_id": req.item_code, "quantity": qty}]},
			frappe.db.get_value("Excom Thread", thread, "account") if thread else "", rates={req.item_code: rate}, uom=req.uom)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom: Sales Order for accepted offer {request} failed", message=frappe.get_traceback())
		send_text(thread, _("Thank you — accepted. Our team will confirm the order shortly."), reply_to_message=reply_to)
		raven_bridge.say(request, _("Accepted, but the Sales Order could not be created: {0}").format(escape_html(str(e)[:200])))
		return
	frappe.db.set_value("Excom Rate Request", request, "sales_order", so)
	frappe.db.commit()
	note = _(" for {0} (the minimum for this rate)").format(f"{qty:g}") if flt(qty) != flt(req.qty) else ""
	send_text(thread, _("Thank you — offer accepted. Order {0}{1} is being prepared.").format(so, note), reply_to_message=reply_to, reference=("Sales Order", so))
	raven_bridge.say(request, _("Accepted → draft Sales Order {0}{1}").format(escape_html(so), escape_html(note)))


# ── scheduler: reminders and expiry ──────────────────────────────────────────

def run_scheduler() -> None:
	if not frappe.db.exists("DocType", "Excom Rate Request"):
		return
	from excom.excom.services.commerce import raven_bridge
	from excom.excom.services.commerce.handlers import send_text
	from excom.excom.services.commerce.rates import settings

	s = settings()
	now = now_datetime()
	for row in frappe.get_all("Excom Rate Request", filters={"status": "Offered", "valid_till": ["<", now]}, fields=["name", "excom_thread", "offer_message"]):
		frappe.db.set_value("Excom Rate Request", row.name, "status", "Expired")
		raven_bridge.say(row.name, _("The offer on {0} expired unanswered.").format(row.name))
		if row.excom_thread:
			try:  # only inside the 24 h window; otherwise the dealer is told when they tap
				send_text(row.excom_thread, _("Our offer {0} has expired. Tap Ask Best Price any time for a fresh one.").format(row.name), reply_to_message=row.offer_message or "")
			except Exception:
				frappe.log_error(title=f"Excom: expiry notice for {row.name} failed", message=frappe.get_traceback())
	minutes = int(s.reply_reminder_min or 0)
	if minutes > 0:
		due = add_to_date(now, minutes=-minutes)
		for row in frappe.get_all("Excom Rate Request", filters={"status": ["in", ["Open", "Claimed", "Pending Approval"]], "reminder_sent": 0, "creation": ["<", due]},
				fields=["name", "status", "customer", "claimed_by"]):
			frappe.db.set_value("Excom Rate Request", row.name, "reminder_sent", 1, update_modified=False)
			who = [row.claimed_by] if row.claimed_by else [raven_bridge._sales_user(row.customer)]
			if row.status == "Pending Approval":
				who = _approvers()[:5]
			mentions = " ".join(raven_bridge.mention(u) for u in who if u)
			raven_bridge.say(row.name, _("{0} is still {1} after {2} minutes. {3}").format(row.name, row.status, minutes, mentions))
	frappe.db.commit()
