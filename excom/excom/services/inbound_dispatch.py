"""Hand every new inbound WhatsApp message to the features that care about it.

Apps (and excom's own features) register in hooks.py:

	excom_inbound_handlers = ["myapp.whatsapp.on_inbound"]

Each handler gets one `event` dict and decides from it whether the message is theirs:

	kind                 text | button | list | flow | address | order | location | reaction | media | contact
	                     | command (a text "/name …" naming one of the number's commands: payload {"command", "args"})
	                     | welcome (request_welcome). Any other "/word" text is kind text with the same
	                     payload as a hint; a plain text has payload None.
	message              Excom Message name (already stored)
	identity, thread     Omni Identity / Excom Thread names
	account, waba        Excom Channel Account name, WhatsApp Business Account id
	provider_message_id  Meta's wamid (for replies / typing indicator)
	reply_to             Excom Message name this message answers (quoted, or the message whose button was tapped)
	text                 what the person wrote or the title of what they tapped
	payload              button / list id, template quick-reply payload, parsed Flow answer
	                     (incl. flow_token), address, location, or the order (catalog_id, product_items)

	signed               False only when the site has no app secret and the payload was accepted unsigned

Handlers run in their own savepoint; one that fails is logged and never stops the others or
the message itself. Handlers must be quick — enqueue anything slow.

Trust rules for handlers:
  * Unsigned payloads (anyone could have POSTed them) are not delivered unless the handler sets
    `handler.accepts_unsigned = True` — an order, an Accept tap or a Flow answer must not be
    forgeable.
  * An id in `payload` (e.g. "RR:RR-0007:accept") is only a claim: check that the record it
    names belongs to `event["identity"]` before acting on it.
"""

import frappe


def dispatch(event: dict) -> None:
	for path in frappe.get_hooks("excom_inbound_handlers") or []:
		try:
			handler = frappe.get_attr(path)
		except Exception:
			frappe.log_error(title=f"Excom: inbound handler not found: {path}", message=frappe.get_traceback())
			continue
		if not event.get("signed", True) and not getattr(handler, "accepts_unsigned", False):
			continue
		frappe.db.savepoint("excom_inbound_handler")
		try:
			handler(event)
		except Exception:
			# the message itself was committed before dispatch, so a full rollback is safe when the
			# handler committed (or the savepoint is otherwise gone)
			try:
				frappe.db.rollback(save_point="excom_inbound_handler")
			except Exception:
				frappe.db.rollback()
			frappe.log_error(title=f"Excom: inbound handler failed: {path}", message=frappe.get_traceback())
	frappe.db.commit()
