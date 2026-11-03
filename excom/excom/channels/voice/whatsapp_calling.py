"""WhatsApp Business Calling: who rings, who answers, and what the call leaves behind.

A WhatsApp number is a voice line whose provider is Meta (`providers/whatsapp.py`). The call lives
on the person's WhatsApp thread, so it sits beside their chat in the inbox.

Incoming (user-initiated):
  1. `calls` webhook, event connect, carries Meta's SDP offer. An Excom Call is created (provider
     WhatsApp, Inbound) and every agent of the number whose browser is ready (presence: heartbeat,
     available, not busy) gets `excom:call_ringing` with the offer. Nobody ready → reject at once.
  2. The browser that answers builds the SDP answer (ICE gathering complete, no trickle) and posts
     it to `api.whatsapp_calling.answer`. The first agent to claim the call wins (a Redis SET NX);
     the backend sends `pre_accept` then `accept` with the same answer. The others are told the
     call was answered elsewhere and stop ringing.
  3. The `terminate` webhook (or an agent hang-up → `terminate` action) ends it.

Outgoing (business-initiated) needs the person's permission, which this module caches per
(number, contact) in `Excom WhatsApp Call Permission`:
  1. GET /call_permissions (cached 5 minutes).
  2. No permission → send the `call_permission_request` interactive message inside the 24 h window,
     at most 1 per 24 h and 2 per 7 days per person.
  3. `call_permission_reply` (an inbound message, delivered through `excom_inbound_handlers`)
     updates the row.
  4. The browser makes the SDP offer → `connect`; RINGING / ACCEPTED / REJECTED statuses and the
     answer SDP (connect webhook) are pushed to that browser.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from excom.excom.channels.voice import handler, presence, routing
from excom.excom.channels.voice.providers import whatsapp as wa
from excom.excom.channels.voice.providers.base import CallEvent, Destination
from excom.excom.doctype.excom_call.excom_call import CLOSED_STATUSES

PROVIDER = wa.NAME
EV_SIGNAL = "excom:wa_call_signal"
# Meta gives 30–60 s to accept; the pop counts down from this.
RING_SECONDS = 45
SDP_TTL = 180
CLAIM_TTL = 4 * 3600
PERMISSION_STALE_SECONDS = 300
REQUESTS_PER_DAY = 1
REQUESTS_PER_WEEK = 2
UNANSWERED_REVOKE = 4
MAX_EVENTS = 40

_OFFER = "excom:wa_call:offer"
_ANSWER = "excom:wa_call:answer"
_CLAIM = "excom:wa_call:claim"
_DECLINED = "excom:wa_call:declined"


# ── cache helpers ─────────────────────────────────────────────────────────────


def _key(prefix: str, call_id: str) -> str:
	return frappe.cache.make_key(f"{prefix}:{call_id}")


def _put(prefix: str, call_id: str, value: str, ttl: int = SDP_TTL) -> None:
	frappe.cache.setex(_key(prefix, call_id), ttl, value)


def _get(prefix: str, call_id: str) -> str:
	value = frappe.cache.get(_key(prefix, call_id))
	return value.decode() if isinstance(value, bytes) else (value or "")


def claim(call_id: str, user: str) -> bool:
	"""First answer wins. SET NX is atomic across workers; the same agent may retry."""
	key = _key(_CLAIM, call_id)
	if frappe.cache.set(key, user, nx=True, ex=CLAIM_TTL):
		return True
	return _get(_CLAIM, call_id) == user


def release(call_id: str, user: str = "") -> None:
	if not user or _get(_CLAIM, call_id) == user:
		frappe.cache.delete(_key(_CLAIM, call_id))


# ── webhook ───────────────────────────────────────────────────────────────────


def handle_calls_change(change: dict, entry: dict) -> None:
	"""`calls` field: `value.calls[]` (connect / terminate) and `value.statuses[]` (outbound progress)."""
	from excom.excom.utils import get_channel_account

	value = change.get("value") or {}
	phone_id = (value.get("metadata") or {}).get("phone_number_id")
	account = get_channel_account(phone_id) if phone_id else None
	if not account or account.get("channel") != "whatsapp":
		frappe.log_error(title="Excom WhatsApp call: unknown number", message=f"phone_number_id {phone_id or '-'}")
		return
	contacts = value.get("contacts") or []
	for item in value.get("calls") or []:
		_safely(_on_call_item, account, item, contacts, value.get("errors") or [])
	for item in value.get("statuses") or []:
		if (item.get("type") or "call") == "call":
			_safely(_on_status_item, account, item, value.get("errors") or [])


def _safely(fn, *args) -> None:
	try:
		fn(*args)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom WhatsApp call: {fn.__name__} failed", message=frappe.get_traceback())


def _on_call_item(account, item: dict, contacts: list, errors: list) -> None:
	event = str(item.get("event") or "").lower()
	direction = wa.call_direction(item)
	if event == "connect" and direction == "Inbound":
		incoming(account, item, contacts)
	elif event == "connect":
		_outbound_answer(account, item)
	elif event == "terminate":
		terminated(account, item, contacts, errors)
	else:
		name = find_call(item)
		if name:
			_log(name, item)
			frappe.db.commit()


def find_call(item: dict) -> str | None:
	"""By Meta's call id, else by the Excom Call name we sent as biz_opaque_callback_data."""
	call_id = item.get("id")
	if call_id:
		name = frappe.db.get_value("Excom Call", {"provider_call_id": call_id}, "name")
		if name:
			return name
	opaque = str(item.get("biz_opaque_callback_data") or "")
	if opaque and frappe.db.exists("Excom Call", {"name": opaque, "provider": PROVIDER}):
		if call_id and not frappe.db.get_value("Excom Call", opaque, "provider_call_id"):
			frappe.db.set_value("Excom Call", opaque, "provider_call_id", call_id, update_modified=False)
		return opaque
	return None


def _contact(item: dict, contacts: list) -> dict:
	uid = item.get("from_user_id") or item.get("to_user_id") or ""
	number = item.get("from") if wa.call_direction(item) == "Inbound" else item.get("to")
	for c in contacts:
		if uid and c.get("user_id") == uid:
			return c
	for c in contacts:
		if number and c.get("wa_id") == number:
			return c
	return contacts[0] if len(contacts) == 1 else {}


def caller_of(item: dict, contacts: list) -> dict:
	"""phone, BSUID, username and profile name of the person on a call — any may be empty."""
	contact = _contact(item, contacts)
	inbound = wa.call_direction(item) == "Inbound"
	phone = (item.get("from") if inbound else item.get("to")) or contact.get("wa_id") or ""
	user_id = (item.get("from_user_id") if inbound else item.get("to_user_id")) or contact.get("user_id") or ""
	profile = contact.get("profile") or {}
	return {
		"phone": str(phone or ""),
		"user_id": str(user_id or ""),
		"username": contact.get("username") or profile.get("username") or "",
		"name": profile.get("name") or "",
	}


def _identity_for(account, who: dict) -> str | None:
	if not (who["phone"] or who["user_id"]):
		return None
	from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal

	return resolve_identity_internal(
		phone=who["phone"],
		channel="whatsapp",
		channel_user_id=who["phone"] or who["user_id"],
		display_name=who["name"] or who["phone"] or who["username"] or "",
		wa_user_id=who["user_id"],
		wa_username=who["username"],
		wa_waba=account.get("wa_business_id") or "",
	)


def ring_set_for(account: str, identity: str | None) -> tuple[list[str], str | None]:
	"""Agents of this number whose browser can ring now; the last agent they spoke to first."""
	users = [a["user"] for a in routing.line_agents(account)]
	sticky = routing.sticky_agent(identity) if identity else None
	ordered = ([sticky] if sticky in users else []) + [u for u in users if u != sticky]
	ready = [u for u in ordered if presence.can_ring_browser(u, account)]
	return ready, (sticky if sticky in ready else None)


def _new_call(account, item: dict, who: dict, identity, thread, direction: str, ring_set=None, agent=None, sticky=None):
	call = frappe.new_doc("Excom Call")
	call.provider = PROVIDER
	call.provider_call_id = item.get("id") or None
	call.direction = direction
	call.transport = "Browser"
	call.status = "Ringing"
	call.customer_number = who.get("phone") or ""
	call.business_number = account.get("wa_display_phone") or ""
	call.channel_account = account.name
	call.omni_identity = identity
	call.thread = thread
	call.display_name = who.get("name") or ""
	call.ring_set = json.dumps(ring_set or ([agent] if agent else []))
	call.sticky_agent = sticky
	call.agent = agent
	call.recording_status = "None"
	# what brought them: the voice_call button payload or the wa.me/call deep link
	call.ivr_selection = str(item.get("cta_payload") or item.get("deeplink_payload") or "")[:140]
	call.provider_events = json.dumps([_event_row(item)], default=str)
	if agent:
		call.team = handler._team_for(agent)
	call.insert(ignore_permissions=True)
	return call


def _thread_activity(thread: str, preview: str, inbound: bool) -> None:
	"""The call goes in the thread; a user's call also opens the 24-hour window (answered or not)."""
	updates = {"last_message_at": now_datetime(), "last_message_preview": preview,
		"last_message_direction": "Inbound" if inbound else "Outbound"}
	if inbound:
		updates["last_inbound_at"] = now_datetime()
	frappe.db.set_value("Excom Thread", thread, updates, update_modified=False)


def _timeline_stub(call, inbound: bool) -> None:
	try:
		frappe.get_doc({
			"doctype": "Excom Message", "thread": call.thread, "omni_identity": call.omni_identity,
			"direction": call.direction, "channel": "whatsapp", "account_doctype": "Excom Channel Account",
			"account": call.channel_account, "message_type": "Call",
			"content_text": _("Incoming WhatsApp call") if inbound else _("Outgoing WhatsApp call"),
			"content_json": json.dumps({"call": call.name, "whatsapp": True}),
			"delivery_status": "Delivered" if inbound else "Sent", "provider_timestamp": now_datetime(),
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Excom WhatsApp call: timeline stub failed", message=f"Call {call.name}")


def ensure_inbound_row(account, item: dict, contacts: list) -> tuple[str | None, bool]:
	"""(call name, created now). Idempotent on Meta's call id — Meta retries webhooks."""
	name = find_call(item)
	if name:
		return name, False
	who = caller_of(item, contacts)
	identity = _identity_for(account, who)
	thread = None
	if identity:
		from excom.excom.services.thread_service import upsert_thread

		thread = upsert_thread(identity, "whatsapp", account.name)
	ring_set, sticky = ring_set_for(account.name, identity)
	try:
		call = _new_call(account, item, who, identity, thread, "Inbound", ring_set=ring_set, sticky=sticky)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback()
		return frappe.db.get_value("Excom Call", {"provider_call_id": item.get("id")}, "name"), False
	if thread:
		_thread_activity(thread, _("Incoming WhatsApp call"), inbound=True)
		_timeline_stub(call, inbound=True)
	frappe.db.commit()
	return call.name, True


def incoming(account, item: dict, contacts: list) -> str | None:
	"""A person is calling the business: ring every ready agent's browser with Meta's offer."""
	call_id = item.get("id")
	if not call_id:
		return None
	name, created = ensure_inbound_row(account, item, contacts)
	if not name:
		return None
	sdp = (item.get("session") or {}).get("sdp") or ""
	if sdp:
		_put(_OFFER, call_id, sdp)
	if not created:
		return name  # a retried webhook: the pop is already up
	if (account.get("wa_calling_status") or "").upper() != "ENABLED":
		# A call arriving proves calling is on at Meta; agents' browsers pick the number up from this.
		frappe.db.set_value("Excom Channel Account", account.name, "wa_calling_status", "ENABLED", update_modified=False)
		frappe.db.commit()
	call = frappe.get_doc("Excom Call", name)
	ring_set = call.ring_set_users()
	if not ring_set:
		# Nobody can take it. Declining now spares the caller a minute of ringing; it lands in the
		# missed-call queue for a call back.
		try:
			wa.adapter_for(account).reject(call_id)
		except Exception:
			frappe.log_error(title="Excom WhatsApp call: reject failed", message=f"Call {name}")
		handler.apply_event(CallEvent(kind="ended", provider_call_id=call_id, status="Missed",
			hangup_cause="No agent available", failure_reason=""), account.name)
		return name
	handler.publish_to(ring_set, handler.EV_RINGING, ringing_payload(call, sdp))
	return name


def ringing_payload(call, sdp: str) -> dict:
	return {
		"provider_call_id": call.provider_call_id,
		"call": call.name,
		"channel": "whatsapp",
		"from_number": call.customer_number or "",
		"business_number": call.business_number or "",
		"account": call.channel_account,
		"omni_identity": call.omni_identity,
		"thread": call.thread,
		"display_name": call.display_name or (frappe.db.get_value("Omni Identity", call.omni_identity, "display_name") if call.omni_identity else "") or call.customer_number or _("WhatsApp caller"),
		"direction": "Inbound",
		"ring_seconds": RING_SECONDS,
		"sdp": sdp,
	}


def _wa_call(call_name: str):
	call = frappe.get_doc("Excom Call", call_name)
	if call.provider != PROVIDER:
		frappe.throw(_("That is not a WhatsApp call."))
	return call


def answer(call_name: str, sdp: str, user: str) -> dict:
	"""The agent's browser answered. pre_accept + accept with its SDP; first agent wins."""
	call = _wa_call(call_name)
	if call.direction != "Inbound" or call.status != "Ringing":
		frappe.throw(_("This call is no longer ringing."))
	if user not in call.ring_set_users():
		frappe.throw(_("This call was not offered to you."), frappe.PermissionError)
	call_id = call.provider_call_id
	if not claim(call_id, user):
		frappe.throw(_("Somebody else answered this call."))
	# The database has the last word in case the cache was flushed between two answers.
	row = frappe.db.sql("SELECT status, answered_by FROM `tabExcom Call` WHERE name = %s FOR UPDATE", call.name, as_dict=True)
	if row and (row[0].status != "Ringing" or (row[0].answered_by and row[0].answered_by != user)):
		release(call_id, user)
		frappe.throw(_("Somebody else answered this call."))
	adapter = wa.adapter_for(call.channel_account)
	try:
		adapter.pre_accept(call_id, sdp)
		adapter.accept(call_id, sdp, opaque=call.name)
	except Exception as exc:
		release(call_id, user)
		_log(call.name, {"event": "accept_failed", "error": str(getattr(exc, "code", "") or "")})
		frappe.db.commit()
		raise
	_mark_answered(call, user, "accepted")
	others = [u for u in call.ring_set_users() if u != user]
	handler.publish_to(others, EV_SIGNAL, {"type": "cancel", "account": call.channel_account,
		"provider_call_id": call_id, "answered_by": user})
	frappe.cache.delete(_key(_OFFER, call_id))
	return {"call": call.name, "thread": call.thread, "provider_call_id": call_id}


def _mark_answered(call, user: str, what: str) -> None:
	fields = {"status": "In Progress", "answered_by": user, "agent": user, "transport": "Browser"}
	team = handler._team_for(user)
	if team:
		fields["team"] = team
	with handler.as_system():
		frappe.db.set_value("Excom Call", call.name, fields)
		call.update(fields)
		presence.mark_busy(user, call.name)
		if call.thread and call.direction == "Inbound":
			handler._claim_thread(call.thread, user)
		_log(call.name, {"event": what})
		if call.omni_identity:
			# Meta resets the permission-request counters after any connected call.
			_reset_after_connected(call.channel_account, call.omni_identity)
		frappe.db.commit()
	handler.publish_to(set(call.ring_set_users()) | {user}, handler.EV_ANSWERED, {
		"call": call.name, "provider_call_id": call.provider_call_id, "answered_by": user,
		"status": "In Progress", "thread": call.thread, "channel": "whatsapp"})


def decline(call_name: str, user: str) -> dict:
	"""One agent declines. The call keeps ringing for the others; when all decline it is rejected."""
	call = _wa_call(call_name)
	ring_set = call.ring_set_users()
	if call.status != "Ringing" or call.direction != "Inbound" or user not in ring_set:
		return {"status": "ignored"}
	# sadd / smembers / expire_key prefix the name themselves
	key = f"{_DECLINED}:{call.provider_call_id}"
	frappe.cache.sadd(key, user)
	frappe.cache.expire_key(key, SDP_TTL)
	declined = {m.decode() if isinstance(m, bytes) else m for m in frappe.cache.smembers(key)}
	if not set(ring_set) - declined:
		try:
			wa.adapter_for(call.channel_account).reject(call.provider_call_id)
		except Exception:
			frappe.log_error(title="Excom WhatsApp call: reject failed", message=f"Call {call.name}")
		handler.apply_event(CallEvent(kind="ended", provider_call_id=call.provider_call_id, status="Missed",
			hangup_cause="Declined by every agent"), call.channel_account)
		return {"status": "rejected"}
	return {"status": "declined"}


def hangup(call_name: str, user: str) -> dict:
	"""End the call at Meta. An outbound call still ringing is recorded as cancelled."""
	call = _wa_call(call_name)
	if call.status in CLOSED_STATUSES:
		return {"status": "ignored"}
	if call.provider_call_id:
		try:
			wa.adapter_for(call.channel_account).hangup(call.provider_call_id)
		except Exception:
			# Already gone at Meta (the person hung up first): the terminate webhook settles it.
			frappe.log_error(title="Excom WhatsApp call: terminate failed", message=f"Call {call.name}")
	if call.direction == "Outbound" and call.status == "Ringing" and call.provider_call_id:
		handler.apply_event(CallEvent(kind="ended", provider_call_id=call.provider_call_id, status="Canceled",
			hangup_source="agent"), call.channel_account)
	elif call.direction == "Outbound" and call.status == "Ringing":
		frappe.db.set_value("Excom Call", call.name, {"status": "Canceled", "hangup_source": "agent"})
	else:
		frappe.db.set_value("Excom Call", call.name, "hangup_source", "agent", update_modified=False)
	frappe.db.commit()
	return {"status": "ok", "call": call.name}


def terminated(account, item: dict, contacts: list, errors: list) -> str | None:
	"""The call ended. Duration and status come from this webhook; there is no other record."""
	name = find_call(item)
	if not name and wa.call_direction(item) == "Inbound":
		name, _created = ensure_inbound_row(account, item, contacts)  # a connect we never saw
	if not name:
		return None
	call = frappe.get_doc("Excom Call", name)
	merged = dict(item, errors=errors)
	event = wa.adapter_for(account).normalize_event("ended", merged)
	event.provider_call_id = call.provider_call_id or event.provider_call_id
	status = wa.call_status_after_terminate(item, answered=bool(call.answered_by))
	if call.status in CLOSED_STATUSES and not event.duration:
		status = call.status  # Busy, Canceled or Missed already said what happened
	event.status = status
	if call.direction == "Outbound" and event.duration:
		event.cost = wa.call_cost(event.duration, float(account.get("wa_call_rate_per_minute") or 0))
	_log(name, item)
	frappe.db.commit()
	handler.apply_event(event, account.name)
	frappe.db.set_value("Excom Call", name, "reconciled", 1, update_modified=False)
	if call.direction == "Outbound" and call.omni_identity:
		_count_unanswered(account.name, call.omni_identity, answered=event.duration > 0 or status == "Completed")
	frappe.db.commit()
	release(call.provider_call_id or "")
	frappe.cache.delete(_key(_OFFER, call.provider_call_id or ""))
	audience = set(call.ring_set_users()) | {call.agent, call.answered_by}
	handler.publish_to(audience, EV_SIGNAL, {"type": "terminate", "account": account.name,
		"provider_call_id": call.provider_call_id, "status": status, "reason": event.failure_reason})
	return name


def _outbound_answer(account, item: dict) -> None:
	"""Business-initiated connect: Meta's SDP answer, for the browser that made the offer."""
	sdp = (item.get("session") or {}).get("sdp") or ""
	call_id = item.get("id") or ""
	if sdp and call_id:
		_put(_ANSWER, call_id, sdp)  # start_call reads it if this beat the call id home
	name = find_call(item)
	if not name:
		return
	_log(name, item)
	frappe.db.commit()
	agent = frappe.db.get_value("Excom Call", name, "agent")
	handler.publish_to([agent], EV_SIGNAL, {"type": "answer", "account": account.name,
		"provider_call_id": call_id, "call": name, "sdp": sdp})


def _on_status_item(account, item: dict, errors: list) -> None:
	"""RINGING / ACCEPTED / REJECTED for a call the business placed."""
	name = find_call(item)
	if not name:
		return
	call = frappe.get_doc("Excom Call", name)
	status = str(item.get("status") or "").upper()
	_log(name, item)
	frappe.db.commit()
	if status == "ACCEPTED" and call.status == "Ringing" and call.agent:
		_mark_answered(call, call.agent, "accepted by the person")
	elif status == "REJECTED" and call.status not in CLOSED_STATUSES:
		handler.apply_event(CallEvent(kind="ended", provider_call_id=call.provider_call_id, status="Busy",
			failure_reason=_("They declined the call.")), account.name)
	handler.publish_to([call.agent], EV_SIGNAL, {"type": "status", "account": account.name,
		"provider_call_id": call.provider_call_id, "status": status})


# ── event log ─────────────────────────────────────────────────────────────────


def _event_row(item: dict) -> dict:
	row = {"at": str(now_datetime())}
	row.update(wa.scrub_event(item) if any(k in item for k in ("id", "event", "status", "session")) else item)
	return row


def _log(name: str, item: dict) -> None:
	"""Append the scrubbed webhook (no numbers, user ids or SDP) to the call, bounded."""
	try:
		events = json.loads(frappe.db.get_value("Excom Call", name, "provider_events") or "[]")
	except ValueError:
		events = []
	if not isinstance(events, list):
		events = []
	events.append(_event_row(item))
	frappe.db.set_value("Excom Call", name, "provider_events", json.dumps(events[-MAX_EVENTS:], default=str), update_modified=False)


# ── business-initiated calls ──────────────────────────────────────────────────


def _thread_target(thread: str):
	t = frappe.get_doc("Excom Thread", thread)
	if t.channel != "whatsapp":
		frappe.throw(_("WhatsApp calls are made from a WhatsApp conversation."))
	account = frappe.get_doc("Excom Channel Account", t.account)
	identity = frappe.db.get_value("Omni Identity", t.omni_identity,
		["name", "primary_whatsapp", "primary_phone", "wa_bsuid", "display_name"], as_dict=True)
	to = (identity.primary_whatsapp or identity.primary_phone or identity.wa_bsuid or "") if identity else ""
	if not to:
		frappe.throw(_("This contact has no WhatsApp number or user id."))
	return t, account, identity, to


def _recipient_params(to: str) -> dict:
	from excom.excom.services import whatsapp_service as ws

	return {"recipient": to} if ws.is_bsuid(to) else {"user_wa_id": ws._clean_phone(to)}


def start_call(thread: str, sdp: str, user: str) -> dict:
	"""Place a call from the agent's browser: `connect` with its SDP offer."""
	from excom.excom.services import whatsapp_service as ws
	from excom.excom.services.whatsapp_errors import WhatsAppAPIError

	t, account, identity, to = _thread_target(thread)
	if (account.get("wa_calling_sip_status") or "").upper() == "ENABLED":
		frappe.throw(_("This number uses SIP for calls, so it cannot call from the browser."))
	if presence.is_busy(user):
		frappe.throw(_("You are already on a call."))
	ws.assert_template_allowed(to, "SERVICE", identity=t.omni_identity)
	state = permission(account.name, t.omni_identity, refresh=True)
	if not state["can_call"]:
		frappe.throw(state["reason"] or _("This person has not allowed calls."))

	who = {"phone": "" if ws.is_bsuid(to) else to, "name": identity.display_name or ""}
	call = _new_call(account, {"event": "start", "direction": wa.OUTBOUND}, who, t.omni_identity, t.name,
		"Outbound", agent=user)
	_thread_activity(t.name, _("Outgoing WhatsApp call"), inbound=False)
	_timeline_stub(call, inbound=False)
	frappe.db.commit()

	try:
		ref = wa.adapter_for(account).initiate_call(Destination(kind="whatsapp", ref=to, user=user), opts={"sdp": sdp, "opaque": call.name})
	except WhatsAppAPIError as exc:
		_after_refusal(account.name, t.omni_identity, exc)
		frappe.db.set_value("Excom Call", call.name, {"status": "Failed", "hangup_cause": str(exc.code or "")[:140]})
		frappe.db.commit()
		frappe.throw(wa.ERROR_TEXT.get(wa.cint_(exc.code)) or str(exc))
	except Exception:
		frappe.db.set_value("Excom Call", call.name, {"status": "Failed", "hangup_cause": "connect failed"})
		frappe.db.commit()
		raise

	frappe.db.set_value("Excom Call", call.name, "provider_call_id", ref.provider_call_id, update_modified=False)
	presence.mark_busy(user, call.name)
	frappe.db.commit()
	return {"call": call.name, "provider_call_id": ref.provider_call_id, "thread": t.name,
		"account": account.name, "sdp_answer": _get(_ANSWER, ref.provider_call_id)}


# ── permission ────────────────────────────────────────────────────────────────


def _permission_doc(account: str, identity: str):
	from excom.excom.doctype.excom_whatsapp_call_permission.excom_whatsapp_call_permission import permission_name

	name = permission_name(account, identity)
	if frappe.db.exists("Excom WhatsApp Call Permission", name):
		return frappe.get_doc("Excom WhatsApp Call Permission", name)
	doc = frappe.get_doc({"doctype": "Excom WhatsApp Call Permission", "channel_account": account,
		"omni_identity": identity, "status": "unknown", "can_request": 1, "can_call": 1, "requests_sent": "[]"})
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		return frappe.get_doc("Excom WhatsApp Call Permission", name)
	return doc


def _epoch(value):
	try:
		seconds = int(float(value))
	except (TypeError, ValueError):
		return None
	if seconds <= 0:
		return None
	from excom.excom.utils.site_time import epoch_to_site_time

	return epoch_to_site_time(seconds)


def parse_permission_response(data: dict) -> dict:
	"""GET /call_permissions → the row's fields (pure)."""
	perm = (data or {}).get("permission") or {}
	out = {"status": str(perm.get("status") or "no_permission").lower(),
		"expires_on": _epoch(perm.get("expiration_time") or perm.get("expiration"))}
	for action in (data or {}).get("actions") or []:
		limits = action.get("limits") or []
		if action.get("action_name") == "send_call_permission_request":
			out["can_request"] = 1 if action.get("can_perform_action") else 0
			out["request_limits"] = json.dumps(limits)
		elif action.get("action_name") == "start_call":
			out["can_call"] = 1 if action.get("can_perform_action") else 0
			out["call_limits"] = json.dumps(limits)
			resets = [lim.get("limit_expiration_time") for lim in limits if lim.get("limit_expiration_time")]
			out["call_limit_resets_on"] = _epoch(max(resets)) if resets else None
	return out


def refresh_permission(account: str, identity: str, doc=None):
	"""Ask Meta. No webhook arrives when a temporary permission lapses, so this is the truth."""
	doc = doc or _permission_doc(account, identity)
	row = frappe.db.get_value("Omni Identity", identity, ["primary_whatsapp", "primary_phone", "wa_bsuid"], as_dict=True) or {}
	to = row.get("primary_whatsapp") or row.get("primary_phone") or row.get("wa_bsuid") or ""
	if not to:
		return doc
	data = wa.graph(frappe.get_cached_doc("Excom Channel Account", account), "GET", "call_permissions",
		params=_recipient_params(to))
	doc.update(parse_permission_response(data))
	doc.checked_on = now_datetime()
	doc.last_error = ""
	doc.save(ignore_permissions=True)
	return doc


def _requests(doc) -> list:
	try:
		value = json.loads(doc.requests_sent or "[]")
	except ValueError:
		value = []
	return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def request_window(sent: list, now=None) -> dict:
	"""Excom's own count of permission requests: 1 per 24 h, 2 per 7 days (pure)."""
	now = get_datetime(now or now_datetime())
	times = sorted(get_datetime(s) for s in sent)
	day = [t for t in times if t > add_to_date(now, hours=-24)]
	week = [t for t in times if t > add_to_date(now, days=-7)]
	next_at = None
	if len(day) >= REQUESTS_PER_DAY:
		next_at = add_to_date(day[0], hours=24)
	if len(week) >= REQUESTS_PER_WEEK:
		candidate = add_to_date(week[0], days=7)
		next_at = max(next_at, candidate) if next_at else candidate
	return {"day": len(day), "week": len(week), "allowed": next_at is None, "next_at": next_at}


def summarise(doc, window_open: bool, now=None) -> dict:
	"""What the conversation's call button shows (pure apart from reading the row)."""
	now = get_datetime(now or now_datetime())
	status = doc.status or "unknown"
	expires = get_datetime(doc.expires_on) if doc.expires_on else None
	if status == "temporary" and expires and expires <= now:
		status = "no_permission"  # lapsed silently
	allowed = status in ("temporary", "permanent")
	can_call = allowed and bool(cint(doc.can_call))
	window = request_window(_requests(doc), now)
	can_request = (status != "permanent" and window_open and window["allowed"]
		and bool(cint(doc.can_request) or status == "unknown"))
	reason = ""
	if not allowed:
		reason = _("They have not allowed calls from this number yet.")
	elif not can_call:
		reason = _("The daily limit of calls to this person is reached{0}.").format(
			_(" until {0}").format(doc.call_limit_resets_on) if doc.call_limit_resets_on else "")
	request_reason = ""
	if status == "permanent":
		request_reason = _("They already allow calls permanently.")
	elif not window_open:
		request_reason = _("A permission request can only be sent within 24 hours of their last message (or as an approved template).")
	elif not window["allowed"]:
		request_reason = _("Already asked recently (1 request a day, 2 a week). Next possible {0}.").format(window["next_at"])
	elif not cint(doc.can_request) and status != "unknown":
		request_reason = _("WhatsApp does not allow another request to this person yet.")
	try:
		call_limits = json.loads(doc.call_limits or "[]")
	except ValueError:
		call_limits = []
	return {
		"status": status, "expires_on": str(doc.expires_on or ""), "permanent": status == "permanent",
		"can_call": can_call, "reason": reason,
		"can_request": can_request, "request_reason": request_reason,
		"requests_today": window["day"], "requests_this_week": window["week"],
		"next_request_at": str(window["next_at"] or ""),
		"call_limits": call_limits, "call_limit_resets_on": str(doc.call_limit_resets_on or ""),
		"consecutive_unanswered": cint(doc.consecutive_unanswered),
		"unanswered_warning": cint(doc.consecutive_unanswered) >= 2,
		"checked_on": str(doc.checked_on or ""), "last_response": doc.last_response or "",
		"response_source": doc.response_source or "",
	}


def permission(account: str, identity: str, refresh: bool = False, window_open: bool = True) -> dict:
	doc = _permission_doc(account, identity)
	stale = not doc.checked_on or (now_datetime() - get_datetime(doc.checked_on)).total_seconds() > PERMISSION_STALE_SECONDS
	if refresh or stale:
		try:
			doc = refresh_permission(account, identity, doc)
		except Exception as exc:
			doc.db_set("last_error", str(exc)[:500], update_modified=False)
			if refresh and doc.status == "unknown":
				raise
	frappe.db.commit()
	return summarise(doc, window_open)


def thread_permission(thread: str, refresh: bool = False) -> dict:
	from excom.excom.services.whatsapp_interactive import window_open

	t = frappe.get_doc("Excom Thread", thread)
	if t.channel != "whatsapp":
		return {"available": False}
	account = frappe.get_cached_doc("Excom Channel Account", t.account)
	out = {"available": True, "account": t.account, "thread": t.name, "window_open": window_open(t.name),
		"sip": (account.get("wa_calling_sip_status") or "").upper() == "ENABLED",
		"calling_status": account.get("wa_calling_status") or ""}
	out.update(permission(t.account, t.omni_identity, refresh=refresh, window_open=out["window_open"]))
	return out


def request_permission(thread: str, body: str = "") -> dict:
	"""Send the call permission request message into the conversation."""
	from excom.excom.services import whatsapp_interactive as wi
	from excom.excom.services.whatsapp_errors import WhatsAppAPIError

	t = frappe.get_doc("Excom Thread", thread)
	if t.channel != "whatsapp":
		frappe.throw(_("Call permission requests are sent in a WhatsApp conversation."))
	state = thread_permission(thread, refresh=True)
	if not state["can_request"]:
		frappe.throw(state["request_reason"] or _("A call permission request cannot be sent now."))
	doc = _permission_doc(t.account, t.omni_identity)
	try:
		message = wi.send_to_thread(thread, wi.call_permission_request(body))
	except WhatsAppAPIError as exc:
		_after_refusal(t.account, t.omni_identity, exc)
		frappe.db.commit()
		frappe.throw(wa.ERROR_TEXT.get(wa.cint_(exc.code)) or str(exc))
	sent = _requests(doc) + [str(now_datetime())]
	doc.reload()
	doc.requests_sent = json.dumps(sent[-10:])
	doc.last_request_message = message
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return {"message": message, "permission": thread_permission(thread)}


def _after_refusal(account: str, identity: str, exc) -> None:
	"""Remember what Meta's refusal says about this person."""
	code = wa.cint_(getattr(exc, "code", 0))
	doc = _permission_doc(account, identity)
	doc.last_error = f"{code}: {wa.ERROR_TEXT.get(code) or str(exc)}"[:500]
	if code == 138006:
		doc.status, doc.checked_on = "no_permission", now_datetime()
	elif code == 138012:
		doc.can_call = 0
	elif code == 138009:
		doc.can_request = 0
	elif code == 138017:
		doc.status = "permanent"
	doc.save(ignore_permissions=True)


def _reset_after_connected(account: str, identity: str) -> None:
	name = frappe.db.get_value("Excom WhatsApp Call Permission", {"channel_account": account, "omni_identity": identity}, "name")
	if name:
		frappe.db.set_value("Excom WhatsApp Call Permission", name, {"requests_sent": "[]", "consecutive_unanswered": 0},
			update_modified=False)


def _count_unanswered(account: str, identity: str, answered: bool) -> None:
	name = frappe.db.get_value("Excom WhatsApp Call Permission", {"channel_account": account, "omni_identity": identity}, "name")
	if not name:
		return
	current = cint(frappe.db.get_value("Excom WhatsApp Call Permission", name, "consecutive_unanswered"))
	frappe.db.set_value("Excom WhatsApp Call Permission", name, "consecutive_unanswered", 0 if answered else current + 1,
		update_modified=False)


def apply_permission_reply(account: str, identity: str, reply: dict) -> dict:
	"""`call_permission_reply`: accept (temporary or permanent) or reject (incl. automatic revoke)."""
	doc = _permission_doc(account, identity)
	response = str(reply.get("response") or "").lower()
	if response == "accept":
		permanent = bool(reply.get("is_permanent"))
		doc.status = "permanent" if permanent else "temporary"
		doc.expires_on = None if permanent else (_epoch(reply.get("expiration_timestamp")) or add_to_date(now_datetime(), days=7))
		doc.can_call = 1
		doc.consecutive_unanswered = 0
	elif response == "reject":
		doc.status, doc.expires_on = "no_permission", None
	else:
		return {"status": "ignored"}
	doc.last_response = response
	doc.response_source = str(reply.get("response_source") or "")[:140]
	doc.responded_on = now_datetime()
	doc.checked_on = now_datetime()
	doc.save(ignore_permissions=True)
	return {"status": doc.status}


def on_inbound(event: dict) -> None:
	"""`excom_inbound_handlers` hook: a call_permission_reply updates the cached permission.

	Signed payloads only (the dispatcher's default), so a forged reply cannot grant or withdraw it.
	"""
	if event.get("kind") != "call_permission" or not event.get("identity") or not event.get("account"):
		return
	payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
	apply_permission_reply(event["account"], event["identity"], payload)


# ── the browser's view ────────────────────────────────────────────────────────


def agent_numbers(user: str) -> list[str]:
	"""WhatsApp numbers this agent takes calls on: active, calling ENABLED (read from Meta in Admin, or
	set by the first call that arrives), and the agent on one of the number's teams (none = everyone)."""
	out = []
	for row in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "status": "Active"},
			fields=["name", "wa_calling_status"], order_by="creation asc"):
		if (row.wa_calling_status or "").upper() != "ENABLED":
			continue
		try:
			if any(a.get("user") == user for a in routing.line_agents(row.name)):
				out.append(row.name)
		except Exception:
			continue
	return out


def lines_for(user: str) -> list[dict]:
	lines = []
	for name in agent_numbers(user):
		account = frappe.get_cached_doc("Excom Channel Account", name)
		descriptor = wa.adapter_for(account).sdk_descriptor()
		lines.append({
			"account": name,
			"account_name": account.account_name or name,
			"business_number": account.get("wa_display_phone") or "",
			"calling_status": account.get("wa_calling_status") or "",
			"sip": (account.get("wa_calling_sip_status") or "").upper() == "ENABLED",
			"sdk": descriptor["sdk"],
			"options": descriptor["options"],
			"presence": presence.snapshot(user, name),
		})
	return lines
