"""Hand every new inbound message, on any channel, to the features that care about it.

Apps (and excom's own features) register in hooks.py:

	excom_inbound_handlers = ["myapp.whatsapp.on_inbound"]

Each handler gets one `event` dict and decides from it whether the message is theirs:

	channel              whatsapp | email | instagram | messenger | webchat. A handler that only
	                     knows WhatsApp must check this: other channels arrive too.
	sender_verified      True when the platform vouches for who sent it (WhatsApp, Instagram,
	                     Messenger); False for email (a From line can be forged) and web chat
	                     (an anonymous visitor). Never act on account data for an unverified sender.
	kind                 email (any email) | text | media (Instagram, Messenger, web chat) | button | list | flow | address | order | location | reaction | media | contact
	                     | command (a text "/name …" naming one of the number's commands: payload {"command", "args"})
	                     | welcome (request_welcome). Any other "/word" text is kind text with the same
	                     payload as a hint; a plain text has payload None.
	                     | call_permission (payload: the call_permission_reply object)
	message              Excom Message name (already stored)
	identity, thread     Omni Identity / Excom Thread names
	account, waba        Excom Channel Account name, WhatsApp Business Account id
	provider_message_id  Meta's wamid (for replies / typing indicator)
	reply_to             Excom Message name this message answers (quoted, or the message whose button was tapped)
	text                 what the person wrote or the title of what they tapped
	payload              button / list id, template quick-reply payload, parsed Flow answer
	                     (incl. flow_token), address, location, or the order (catalog_id, product_items)

	signed               False only when the site has no app secret and the payload was accepted unsigned
	subject              email only: the subject line

Only live messages are dispatched. Email backfill, a mailbox moving servers and Meta's
conversation polling store history too; anything received more than FRESH_HOURS ago is stored
quietly, so an old email never wakes an agent.

The message and everything ingestion did with it are committed before the first handler runs.
Each handler then runs in its own transaction, committed when it returns; one that fails is
rolled back and logged and never stops the others or the message itself (one that hits a lock
clash gets a second try). Handlers must be quick — enqueue anything slow.

Trust rules for handlers:
  * Unsigned payloads (anyone could have POSTed them) are not delivered unless the handler sets
    `handler.accepts_unsigned = True` — an order, an Accept tap or a Flow answer must not be
    forgeable.
  * An id in `payload` (e.g. "RR:RR-0007:accept") is only a claim: check that the record it
    names belongs to `event["identity"]` before acting on it.
"""

import frappe
from frappe.utils import get_datetime, now_datetime, time_diff_in_hours

FRESH_HOURS = 24
VERIFIED = {"whatsapp", "instagram", "messenger"}


def message_event(message_name: str, kind: str = "text", payload=None, subject: str = "") -> None:
	"""Dispatch a stored inbound message from a channel other than the WhatsApp webhook (which
	builds its own richer event). Quietly does nothing for an old message."""
	try:
		msg = frappe.db.get_value(
			"Excom Message", message_name,
			["omni_identity", "thread", "reply_to", "account", "channel", "provider_message_id", "provider_timestamp",
			 "content_text", "direction"],
			as_dict=True,
		)
		if not msg or msg.direction != "Inbound" or not is_fresh(msg.provider_timestamp):
			return
		dispatch({
			"channel": msg.channel,
			"sender_verified": msg.channel in VERIFIED,
			"kind": kind,
			"message": message_name,
			"identity": msg.omni_identity,
			"thread": msg.thread,
			"reply_to": msg.reply_to or "",
			"account": msg.account,
			"waba": "",
			"provider_message_id": msg.provider_message_id or "",
			"text": msg.content_text or "",
			"payload": payload,
			"subject": subject,
			"signed": True,
		})
	except Exception:
		frappe.log_error(title="Excom: inbound dispatch failed", message=frappe.get_traceback())


def is_fresh(received_at) -> bool:
	if not received_at:
		return True
	try:
		return time_diff_in_hours(now_datetime(), get_datetime(received_at)) <= FRESH_HOURS
	except Exception:
		return True


def dispatch(event: dict) -> None:
	event.setdefault("channel", "whatsapp")
	event.setdefault("sender_verified", event["channel"] in VERIFIED)
	# What ingestion did (the message, a STOP recorded as an opt-out, ad attribution) is committed
	# before any handler runs, so a failing handler can never undo it.
	frappe.db.commit()
	for path in frappe.get_hooks("excom_inbound_handlers") or []:
		try:
			handler = frappe.get_attr(path)
		except Exception:
			frappe.log_error(title=f"Excom: inbound handler not found: {path}", message=frappe.get_traceback())
			continue
		if not event.get("signed", True) and not getattr(handler, "accepts_unsigned", False):
			continue
		for attempt in (1, 2):
			try:
				handler(event)
				frappe.db.commit()
				break
			except frappe.QueryDeadlockError:
				# A row the handler touched changed under it (MariaDB snapshot check, or a deadlock):
				# its work is rolled back; once more on a fresh snapshot.
				frappe.db.rollback()
				if attempt == 2:
					frappe.log_error(title=f"Excom: inbound handler gave up after a lock clash: {path}",
						message=frappe.get_traceback())
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Excom: inbound handler failed: {path}", message=frappe.get_traceback())
				break
