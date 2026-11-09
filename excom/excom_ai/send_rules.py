"""What an AI agent may send, checked inside Excom's own send functions (HLD-007 §11, §12.5).

Excom's send paths call `check()` whenever the sender is an agent's own user, so no route
(executor, API call, a future tool) can skip these rules:

- Consent, on every channel: never to someone on do-not-contact or opted out of that channel,
  on this identity or any duplicate holding the same number. Outreach (first contact, follow-ups)
  also respects a WhatsApp "Stop promotions".
- The reply window: WhatsApp, Instagram and Messenger free text only within 24 hours of the
  person's last message. An agent never uses Meta's HUMAN_AGENT tag (Meta allows it for human
  agents only); `allow_meta_tag()` is how the Meta send knows.
- The channel account: an agent sends only from numbers and mailboxes listed on it.
- WhatsApp templates (HLD-008): only an APPROVED template on the agent's number, and only to a
  person who opted in (a "Granted" in the consent ledger) or has written to us on WhatsApp.

A person's own sends are untouched: they keep today's behaviour.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime, time_diff_in_hours

from excom.excom_ai.service_user import is_service_user

WINDOW_HOURS = 24
WINDOWED = ("whatsapp", "instagram", "messenger")


class AgentSendRefused(frappe.ValidationError):
	pass


def applies(user: str | None = None) -> bool:
	return is_service_user(user or frappe.session.user)


def allow_meta_tag(user: str | None = None) -> bool:
	return not applies(user)


def check(thread, purpose: str = "reply", user: str | None = None, window: bool = True,
		recipients: list[str] | None = None, template: str | None = None) -> None:
	"""Refuse (AgentSendRefused) unless an agent may send on this thread now. No-op for people.

	window=False for an approved WhatsApp template, the one way to write outside the window.
	recipients: every address an email goes to (to, cc, bcc). Each must be the conversation's own
	person, and none on a blocked domain: an agent never copies anyone in.
	"""
	user = user or frappe.session.user
	if not applies(user):
		return
	if isinstance(thread, str):
		thread = frappe.get_doc("Excom Thread", thread)
	_check_account(thread, user)
	if recipients is not None:
		_check_recipients(thread, recipients)
	_check_consent(thread, purpose)
	if template:
		marketing = _check_template(thread, template)
		_check_opt_in(thread, marketing)
	if window:
		_check_window(thread)


def _check_template(thread, template: str) -> bool:
	"""Approved, on this number, and (for the steward's) still in the category it was made for.
	Returns whether it is a MARKETING template."""
	from excom.excom.whatsapp_template_utils import template_is_linked_to_account

	t = frappe.db.get_value("WhatsApp Templates", template, ["status", "category", "xai_need"], as_dict=True) or {}
	if (t.get("status") or "").upper() != "APPROVED":
		raise AgentSendRefused(_("Template {0} is not approved by Meta ({1}).").format(template, t.get("status") or "-"))
	if not template_is_linked_to_account(template, thread.account):
		raise AgentSendRefused(_("Template {0} does not belong to {1}.").format(template, thread.account))
	category = (t.get("category") or "").upper()
	if t.get("xai_need"):
		wanted = (frappe.db.get_value("Excom AI Template Need", t["xai_need"], "category") or "").upper()
		if wanted and category != wanted:
			raise AgentSendRefused(_("Meta moved template {0} to {1}; it was made as {2}.").format(template, category, wanted))
	return category == "MARKETING"


def _check_opt_in(thread, marketing: bool = False) -> None:
	"""WhatsApp's policy: a business starts a conversation only with people who opted in."""
	if not whatsapp_opt_in(_identities(thread), marketing):
		raise AgentSendRefused(_("No WhatsApp opt-in: record the person's consent, or wait until they write to us."))


def whatsapp_opt_in(identities: list[str], marketing: bool = False) -> bool:
	"""A recorded WhatsApp "Granted" (transactional or marketing), or a WhatsApp message from them.
	Marketing needs a recorded marketing "Granted": having written to us is not consent to promotions."""
	from excom.excom.services.consent import latest_consent

	names = [n for n in identities or [] if n]
	purposes = ("marketing",) if marketing else ("transactional", "marketing")
	for name in names:
		if any(latest_consent(name, "whatsapp", purpose) == "Granted" for purpose in purposes):
			return True
	return bool(not marketing and names and frappe.db.exists("Excom Message", {"omni_identity": ["in", names],
		"direction": "Inbound", "channel": "whatsapp"}))


def number_opted_in(number: str | None) -> bool:
	"""For packs choosing a channel at writing time: may an agent start a WhatsApp conversation with
	this number? (Checked again when the template is sent.)"""
	if not number:
		return False
	from excom.excom.services.whatsapp_service import identities_for_recipient

	return whatsapp_opt_in(identities_for_recipient(number))


def _identities(thread) -> list[str]:
	names = [thread.omni_identity] if thread.omni_identity else []
	ident = frappe.db.get_value("Omni Identity", thread.omni_identity, ["primary_whatsapp", "primary_phone"],
		as_dict=True) if thread.omni_identity else None
	if ident:
		from excom.excom.services.whatsapp_service import identities_for_recipient

		for number in filter(None, (ident.primary_whatsapp, ident.primary_phone)):
			names += identities_for_recipient(number)
	return list(dict.fromkeys(n for n in names if n))


def _check_account(thread, user: str) -> None:
	agent = frappe.db.get_value("Excom AI Agent", {"service_user": user}, ["name", "status"], as_dict=True)
	if not agent or agent.status != "Live":
		raise AgentSendRefused(_("Only a Live agent sends."))
	if not frappe.db.exists("Excom AI Channel", {"parent": agent.name, "parenttype": "Excom AI Agent",
			"channel_account": thread.account}):
		raise AgentSendRefused(_("{0} is not one of this agent's numbers or mailboxes.").format(thread.account))


def addresses(raw) -> list[str]:
	"""'A <a@x.com>, b@y.com' (or a list of them) into lowercase addresses."""
	import re

	items = raw if isinstance(raw, list) else [raw]
	return [m.lower() for r in items if r for m in re.findall(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", str(r))]


def _check_recipients(thread, recipients: list[str]) -> None:
	own = set()
	if thread.omni_identity:
		own.add((frappe.db.get_value("Omni Identity", thread.omni_identity, "primary_email") or "").lower())
		own |= {(c or "").lower() for c in frappe.get_all("Omni Identity Channel",
			{"parent": thread.omni_identity, "parenttype": "Omni Identity"}, pluck="channel_user_id")}
	blocked = {r.domain for r in frappe.get_single("Excom AI Settings").dnc_domains or []}
	for addr in addresses(recipients):
		domain = addr.split("@", 1)[1]
		if domain in blocked or any(domain.endswith("." + b) for b in blocked):
			raise AgentSendRefused(_("{0} is on a do-not-contact domain.").format(addr))
		if addr not in own:
			raise AgentSendRefused(_("{0} is not this conversation's person: an agent writes to them only.").format(addr))


def _check_consent(thread, purpose: str) -> None:
	names = [thread.omni_identity] if thread.omni_identity else []
	ident = frappe.db.get_value("Omni Identity", thread.omni_identity,
		["primary_whatsapp", "primary_phone", "primary_email"], as_dict=True) if thread.omni_identity else None
	if thread.channel == "whatsapp" and ident:
		from excom.excom.services.whatsapp_service import identities_for_recipient

		for number in filter(None, (ident.primary_whatsapp, ident.primary_phone)):
			names += identities_for_recipient(number)
	elif thread.channel == "email" and ident and ident.primary_email:
		names += frappe.get_all("Omni Identity", {"primary_email": ident.primary_email}, pluck="name")
	names = list(dict.fromkeys(n for n in names if n))
	if not names:
		return
	flag = {"whatsapp": "whatsapp_opt_out", "email": "email_opt_out"}.get(thread.channel)
	fields = ["name", "do_not_contact", "wa_marketing_opt_out"] + ([flag] if flag else [])
	for row in frappe.get_all("Omni Identity", {"name": ["in", names]}, fields):
		if row.do_not_contact:
			raise AgentSendRefused(_("{0} is on do-not-contact.").format(row.name))
		if flag and row.get(flag):
			raise AgentSendRefused(_("{0} opted out of {1}.").format(row.name, thread.channel))
		if purpose == "outreach" and thread.channel == "whatsapp" and row.wa_marketing_opt_out:
			raise AgentSendRefused(_("{0} stopped promotions on WhatsApp.").format(row.name))


def _check_window(thread) -> None:
	if thread.channel not in WINDOWED:
		return
	last = frappe.db.get_value("Excom Thread", thread.name, "last_inbound_at")
	if not last or time_diff_in_hours(now_datetime(), last) >= WINDOW_HOURS:
		raise AgentSendRefused(_("The 24-hour reply window is closed: an agent writes here only after the buyer does."))
