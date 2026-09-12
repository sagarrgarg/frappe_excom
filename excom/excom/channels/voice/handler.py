"""Everything that writes. None of it runs on the call's critical path.

The route endpoint publishes a screen pop, returns XML, and enqueues `persist_call`. Every later
webhook — answered, ended, recording ready — lands here too. All of it is idempotent on
`provider_call_id`, because providers retry webhooks and a duplicate must be free.

Two rules this module exists to enforce:

1. **Realtime goes to the ring set, never everybody.** The Exotel version published the caller's
   number and identity to every enabled System User and then broadcast unscoped on top. In an app
   whose entire visibility model is the team tree, that is a data leak wearing a feature's clothes.
2. **Elevated context is scoped.** Webhooks arrive as Guest and have to write. That elevation
   happens inside `as_system()` and is always undone, rather than being set on the request and left
   for whatever runs next on that worker.
"""

import json
from contextlib import contextmanager

import frappe
from frappe.utils import cint, now_datetime

from excom.excom.channels.voice import presence
from excom.excom.channels.voice.providers.base import CallDecision, CallEvent
from excom.excom.doctype.excom_call.excom_call import CLOSED_STATUSES
from excom.excom.utils.phone import normalize_phone

# Realtime event names. One name per fact — the Exotel version emitted four aliases for the same
# thing and they all became load-bearing.
EV_RINGING = "excom:call_ringing"
EV_ANSWERED = "excom:call_answered"
EV_ENDED = "excom:call_ended"
EV_UPDATED = "excom:call_updated"


@contextmanager
def as_system():
	"""Run as Administrator with permissions off, and always put it back.

	Webhook workers are shared. Setting the user and never resetting it, which is what the reference
	implementation does, leaks Administrator into the next job on that worker.
	"""
	prev_user = frappe.session.user
	prev_flag = getattr(frappe.flags, "ignore_permissions", False)
	frappe.set_user("Administrator")
	frappe.flags.ignore_permissions = True
	try:
		yield
	finally:
		frappe.flags.ignore_permissions = prev_flag
		frappe.set_user(prev_user)


# ── publishing ────────────────────────────────────────────────────────────────


def publish_to(users, event: str, payload: dict) -> None:
	"""Send an event to named users and nobody else."""
	for user in dict.fromkeys(u for u in (users or []) if u and u != "Guest"):
		frappe.publish_realtime(event, payload, user=user, after_commit=False)


def announce_ringing(decision: CallDecision, context: dict, provider_call_id: str) -> None:
	"""The screen pop. Fires before any database write, so it is instant even under load.

	Deliberately thin: an id, a number, and whatever display name we already had in hand. The
	browser fetches the rest once the pop is up, so a slow identity lookup never delays the ring.
	"""
	publish_to(
		decision.ring_set,
		EV_RINGING,
		{
			"provider_call_id": provider_call_id,
			"from_number": context.get("caller_number"),
			"business_number": context.get("business_number"),
			"account": context.get("account"),
			"omni_identity": context.get("identity"),
			"display_name": context.get("display_name") or context.get("caller_number"),
			"direction": "Inbound",
			"ring_seconds": decision.ring_seconds,
		},
	)


# ── persistence ───────────────────────────────────────────────────────────────


def persist_call(
	provider_call_id: str,
	account: str,
	direction: str,
	caller_number: str,
	business_number: str,
	decision_users: list[str] | None = None,
	sticky_agent: str | None = None,
	identity: str | None = None,
	agent: str | None = None,
	transport: str = "Browser",
	ivr_selection: str = "",
	raw: dict | None = None,
) -> str | None:
	"""Create the call record, its identity and its thread. Enqueued, never inline.

	Safe to call twice: the unique index on `provider_call_id` is the idempotency guarantee and the
	existence check in front of it is only an optimisation.
	"""
	if not provider_call_id:
		return None

	with as_system():
		existing = frappe.db.get_value(
			"Excom Call", {"provider_call_id": provider_call_id}, "name"
		)
		if existing:
			return existing

		account_doc = (
			frappe.get_cached_doc("Excom Channel Account", account)
			if account and frappe.db.exists("Excom Channel Account", account)
			else None
		)
		contact_number = normalize_phone(caller_number)

		# The last line of defence against a conversation opened with ourselves. A SIP address is
		# one of our own softphones, and our own DID is the line the call arrived on; neither is a
		# customer, and an identity built from either is junk that lands in everybody's inbox and
		# cannot sensibly be merged away. A call with no contact is the better failure.
		if str(caller_number or "").startswith("sip:"):
			contact_number = ""
		own_number = _digits((account_doc.get("voice_number") if account_doc else "") or "")
		if own_number and _digits(contact_number) == own_number:
			contact_number = ""

		if not identity and contact_number:
			from excom.excom.doctype.omni_identity.omni_identity import resolve_identity

			identity = resolve_identity(
				phone=contact_number, channel="voice", display_name=contact_number
			)

		thread = None
		if identity and account:
			from excom.excom.services.thread_service import upsert_thread

			thread = upsert_thread(identity, "voice", account)

		call = frappe.new_doc("Excom Call")
		call.provider_call_id = provider_call_id
		call.provider = account_doc.get("voice_provider") if account_doc else ""
		call.direction = direction
		call.transport = transport
		call.status = "Ringing"
		call.customer_number = contact_number
		call.business_number = normalize_phone(business_number)
		call.channel_account = account
		call.omni_identity = identity
		call.thread = thread
		call.sticky_agent = sticky_agent
		call.agent = agent
		call.ivr_selection = ivr_selection
		call.ring_set = json.dumps(decision_users or [])
		call.recording_status = "Pending" if _records(account_doc, direction) else "None"
		if raw:
			call.provider_events = json.dumps([_trim(raw)], default=str)
		if agent:
			call.team = _team_for(agent)
		call.insert(ignore_permissions=True)

		if thread:
			_write_timeline_stub(call, thread, identity, account, direction)

		frappe.db.commit()
		return call.name


def _records(account_doc, direction: str) -> bool:
	if not account_doc:
		return False
	policy = account_doc.get("voice_record_policy") or "All"
	if policy == "All":
		return True
	if policy == "Inbound only":
		return direction == "Inbound"
	if policy == "Outbound only":
		return direction == "Outbound"
	return False


def _team_for(user: str) -> str | None:
	try:
		from excom.excom.services.crm_visibility import team_for_user

		return team_for_user(user)
	except Exception:
		return None


def _write_timeline_stub(call, thread: str, identity: str, account: str, direction: str) -> None:
	"""A call is a message in the thread, so it renders beside WhatsApp and email."""
	try:
		message = frappe.new_doc("Excom Message")
		message.thread = thread
		message.omni_identity = identity
		message.direction = direction
		message.channel = "voice"
		message.account_doctype = "Excom Channel Account"
		message.account = account
		message.message_type = "Call"
		message.content_text = _preview(call)
		message.content_json = json.dumps({"call": call.name})
		message.delivery_status = "Delivered" if direction == "Inbound" else "Sent"
		message.provider_timestamp = now_datetime()
		message.insert(ignore_permissions=True)
	except Exception as exc:
		frappe.log_error(
			title="Excom Voice: timeline stub failed", message=f"Call {call.name}: {exc}"
		)


def _preview(call) -> str:
	if call.status in ("Ringing", "In Progress"):
		return (
			"Incoming call" if call.direction == "Inbound" else "Outgoing call"
		)
	if call.status == "Completed":
		return f"Call · {_mmss(call.duration)}" if call.duration else "Call completed"
	if call.status in ("Missed", "No Answer"):
		return "Missed call" if call.direction == "Inbound" else "No answer"
	return f"Call {call.status.lower()}"


def _mmss(seconds) -> str:
	seconds = cint(seconds)
	return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _trim(payload: dict, limit: int = 60) -> dict:
	"""Keep the event log readable and free of anything we should not be storing."""
	return {k: v for i, (k, v) in enumerate(sorted((payload or {}).items())) if i < limit}


# ── events ────────────────────────────────────────────────────────────────────


def _participants(event: CallEvent, account: str) -> tuple[str, str, str]:
	"""Work out the direction, the customer and our own number from one webhook.

	A webhook can describe either leg of a call, and neither leg puts the customer in a fixed
	field. Reading `From` as "the customer" — which is what this used to do — is wrong twice over:

	* On the B leg of a ``<Dial>``, `From` is the caller id we asked Plivo to present, so the
	  customer came out as **our own business number** and every such call opened a conversation
	  with ourselves.
	* On a leg the browser placed, `From` is a SIP address like
	  ``sip:<endpoint>_<auth id>@phone.plivo.com``. Stripped of its punctuation that becomes an
	  eighteen-digit contact — a conversation named after our own softphone.

	So rather than trusting a field, eliminate: whichever of the two numbers is neither a SIP
	address of ours nor this line's own number is the customer. If both are ours we return nothing
	and the record is created without a contact, which is a call with a missing name rather than a
	junk contact in everybody's inbox.
	"""
	line_number = normalize_phone(
		frappe.db.get_value("Excom Channel Account", account, "voice_number") or ""
	)
	raw_from = str(event.from_number or "")
	raw_to = str(event.to_number or "")

	# Plivo calls a browser-originated leg "inbound" — it is inbound to Plivo. The SIP address is
	# what actually says the call came from one of our own softphones, which makes it outbound.
	from_browser = raw_from.startswith("sip:")
	direction = "Outbound" if (from_browser or event.direction == "outbound") else "Inbound"

	customer = ""
	for candidate in (raw_from, raw_to):
		if candidate.startswith("sip:"):
			continue
		# Compared as bare digits, deliberately. We store a line in E.164 with its `+` and the
		# provider sends the same number without one; compared as strings those never matched,
		# which is precisely why our own number kept arriving in the inbox as a customer.
		digits = _digits(candidate)
		if not digits or digits == _digits(line_number):
			continue
		customer = f"+{digits}"
		break

	return direction, customer, line_number


def _digits(value: str) -> str:
	"""A phone number reduced to the only part two spellings of it agree on."""
	return "".join(ch for ch in str(value or "") if ch.isdigit())


def apply_event(event: CallEvent, account: str) -> dict:
	"""Fold one normalised provider event into the call record.

	Late and out-of-order webhooks are the norm, so this only ever moves the record forward: a
	closed call can still gain a duration, a cost or a recording, but nothing puts it back to
	Ringing.
	"""
	if not event.provider_call_id:
		return {"status": "ignored", "reason": "no call id"}

	with as_system():
		name = frappe.db.get_value(
			"Excom Call", {"provider_call_id": event.provider_call_id}, "name"
		)
		if not name:
			# The answer URL's enqueued write has not landed yet, or this is a leg we never saw.
			direction, customer, business = _participants(event, account)
			name = persist_call(
				provider_call_id=event.provider_call_id,
				account=account,
				direction=direction,
				caller_number=customer,
				business_number=business,
				transport="Browser" if str(event.from_number or "").startswith("sip:") else "Phone",
				raw=event.raw,
			)
			if not name:
				return {"status": "ignored", "reason": "could not create call"}

		call = frappe.get_doc("Excom Call", name)
		handler = {
			"ringing": _on_ringing,
			"answered": _on_answered,
			"ended": _on_ended,
			"recording_ready": _on_recording,
		}.get(event.kind)

		if not handler:
			_log_event(call, event)
			return {"status": "ignored", "reason": f"unhandled kind {event.kind}"}

		changed = handler(call, event) or {}
		_log_event(call, event, changed)
		frappe.db.commit()
		return {"status": "ok", "call": call.name, "changed": list(changed)}


def _on_ringing(call, event: CallEvent) -> dict:
	if call.status != "Ringing":
		return {}
	return _apply(call, {"status": "Ringing"})


def _on_answered(call, event: CallEvent) -> dict:
	"""Somebody picked up. Tell everyone else in the ring set to stop ringing."""
	answered_by = _user_for_destination(call, event.answered_destination)
	fields = {"status": "In Progress"}
	if answered_by:
		fields["answered_by"] = answered_by
		fields["agent"] = answered_by
		team = _team_for(answered_by)
		if team:
			fields["team"] = team
		# A SIP destination means they took it in the browser; a number means their handset rang.
		fields["transport"] = (
			"Browser" if str(event.answered_destination or "").startswith("sip:") else "Phone"
		)

	changed = _apply(call, fields)
	if answered_by:
		presence.mark_busy(answered_by, call.name)
		if call.thread and call.direction == "Inbound":
			_claim_thread(call.thread, answered_by)

	payload = {
		"call": call.name,
		"provider_call_id": call.provider_call_id,
		"answered_by": answered_by,
		"status": "In Progress",
		"thread": call.thread,
	}
	publish_to(call.ring_set_users(), EV_ANSWERED, payload)
	return changed


def _on_ended(call, event: CallEvent) -> dict:
	status = event.status or ("Completed" if event.duration else "Missed")
	if status == "No Answer" and call.direction == "Inbound" and not call.answered_by:
		status = "Missed"
	if call.status in CLOSED_STATUSES and status not in CLOSED_STATUSES:
		status = call.status

	fields = {"status": status}
	if event.duration:
		fields["duration"] = event.duration
		fields["talk_time"] = event.duration
	if event.bill_duration:
		fields["talk_time"] = event.bill_duration
	if event.cost:
		fields["cost"] = event.cost
	if event.hangup_cause:
		fields["hangup_cause"] = event.hangup_cause
	if event.hangup_source:
		fields["hangup_source"] = event.hangup_source

	# A call nobody answered has nothing to record. Leaving it Pending left the timeline card
	# saying "Recording is being prepared" with a spinner, for ever, on a missed call.
	if not event.duration and call.recording_status == "Pending":
		fields["recording_status"] = "None"

	changed = _apply(call, fields)

	for user in {call.agent, call.answered_by} | set(call.ring_set_users()):
		if user:
			presence.clear_busy(user)

	_refresh_timeline(call)
	audience = set(call.ring_set_users()) | {call.agent, call.answered_by}
	publish_to(
		audience,
		EV_ENDED,
		{
			"call": call.name,
			"provider_call_id": call.provider_call_id,
			"status": call.status,
			"duration": call.duration,
			"thread": call.thread,
			"missed": call.status in ("Missed", "No Answer"),
			# Why it failed, when the provider told us something an agent can act on. Without this
			# an outbound call that a carrier refused is indistinguishable from one nobody picked
			# up, and the agent redials it.
			"hangup_cause": call.hangup_cause or "",
			"reason": event.failure_reason or "",
		},
	)
	if call.thread:
		publish_to(audience, "excom:thread_updated", {"thread_id": call.thread})
	return changed


def _on_recording(call, event: CallEvent) -> dict:
	if event.recording_duration_ms <= 0 and not event.recording_url:
		# Session recordings report -1 until the final callback. Nothing to store yet.
		return {}
	fields = {
		"recording_status": "Ready" if event.recording_url else "Pending",
		"recording_id": event.recording_id or call.recording_id,
		"recording_url": event.recording_url or call.recording_url,
	}
	if event.recording_duration_ms > 0:
		fields["recording_duration_ms"] = event.recording_duration_ms
	changed = _apply(call, fields)
	if call.thread:
		publish_to(
			set(call.ring_set_users()) | {call.agent, call.answered_by},
			EV_UPDATED,
			{"call": call.name, "thread": call.thread, "recording_status": call.recording_status},
		)
	return changed


def _apply(call, fields: dict) -> dict:
	"""Write only what actually changed, so a retried webhook is a no-op at the database too."""
	changed = {k: v for k, v in fields.items() if v not in (None, "") and call.get(k) != v}
	if changed:
		frappe.db.set_value("Excom Call", call.name, changed, update_modified=True)
		call.update(changed)
	return changed


def _user_for_destination(call, destination: str) -> str | None:
	"""Who answered.

	We match against the ring set we computed ourselves rather than searching User for a phone
	number that looks similar. The Exotel version did `mobile_no LIKE %digits%` and took the first
	row, which silently hands a call to the wrong agent whenever two mobiles share a tail.
	"""
	if not destination:
		return None

	target = str(destination).strip()
	if target.startswith("sip:"):
		user = frappe.db.get_value(
			"Excom Voice Endpoint",
			{"sip_uri": target, "channel_account": call.channel_account},
			"user",
		)
		if user:
			return user

	normalized = normalize_phone(target)
	if not normalized:
		return None

	from excom.excom.channels.voice.routing import line_agents

	tail = normalized.lstrip("+")[-10:]
	for agent in line_agents(call.channel_account):
		mobile = (agent.get("mobile") or "").lstrip("+")
		if mobile and tail and mobile[-10:] == tail:
			return agent["user"]
	return None


def _claim_thread(thread: str, user: str) -> None:
	"""Answering a call claims the conversation, the same way replying to a chat does."""
	current = frappe.db.get_value("Excom Thread", thread, "assigned_to")
	if current:
		return
	updates = {"assigned_to": user}
	team = _team_for(user)
	if team:
		updates["assigned_team"] = team
	frappe.db.set_value("Excom Thread", thread, updates)


def _refresh_timeline(call) -> None:
	"""Bring the timeline stub and the thread preview up to date with the finished call."""
	if not call.thread:
		return
	text = _preview(call)
	name = _timeline_stub(call)
	if name:
		frappe.db.set_value(
			"Excom Message",
			name,
			{
				"content_text": text,
				"delivery_status": "Delivered"
				if call.status == "Completed"
				else "Failed"
				if call.status in ("Failed", "Missed", "No Answer", "Busy")
				else "Sent",
			},
		)
	frappe.db.set_value(
		"Excom Thread",
		call.thread,
		{"last_message_preview": text, "last_message_at": now_datetime()},
	)


def _timeline_stub(call) -> str | None:
	"""Find this call's message in the thread.

	Matched in Python on the parsed `content_json` rather than with `LIKE '%name%'`: a leading
	wildcard cannot use an index, and on a busy thread it is a full scan on the write path of every
	call that ends.
	"""
	rows = frappe.get_all(
		"Excom Message",
		filters={"thread": call.thread, "message_type": "Call"},
		fields=["name", "content_json"],
		order_by="creation desc",
		limit=50,
	)
	for row in rows:
		try:
			if (json.loads(row.content_json or "{}") or {}).get("call") == call.name:
				return row.name
		except ValueError:
			continue
	return None


def _log_event(call, event: CallEvent, changed: dict | None = None) -> None:
	"""Append to the per-call event log, bounded so a retry storm cannot grow a row without limit.

	Only ids and statuses. Never the payload wholesale — that is where phone numbers and, later,
	transcript fragments would end up sitting in a Code field forever.
	"""
	try:
		events = json.loads(call.provider_events or "[]")
	except ValueError:
		events = []
	if not isinstance(events, list):
		events = []
	events.append(
		{
			"at": str(now_datetime()),
			"kind": event.kind,
			"status": event.status or "",
			"changed": sorted(changed or {}),
		}
	)
	frappe.db.set_value(
		"Excom Call", call.name, "provider_events", json.dumps(events[-40:]), update_modified=False
	)
