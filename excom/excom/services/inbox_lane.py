"""Which conversations belong in the main inbox, and which in Other.

Bank alerts, payment advices, newsletters and platform notices sat between customer enquiries and
made up a third of the unread count. Outlook's answer is Focused / Other: automated and bulk mail
goes to a second view, nothing is deleted, and a person can move a sender for good with one click.

Gmail has already sorted every message it hands us: each one carries CATEGORY_PERSONAL,
CATEGORY_UPDATES, CATEGORY_PROMOTIONS, CATEGORY_SOCIAL or CATEGORY_FORUMS in its labels, and we
store the labels. That is the main signal. Two more catch what Gmail calls personal but nobody
wrote by hand: list headers (List-Unsubscribe, List-Id, Precedence: bulk, Auto-Submitted) and a
no-reply style sender address.

What never goes to Other on its own, whatever Gmail says (Gmail files real business mail under
Updates often enough):
  * a conversation somebody here has replied to,
  * a sender with a CRM record (Lead, Customer, Contact…) linked to their identity,
  * a sender who also reaches us on another channel (WhatsApp, Instagram, calls…).

A lane set by hand (`inbox_lane_pinned`) is never changed by new mail. Otherwise the lane follows
the latest inbound message, so a sender who stops sending newsletters and writes a real message
comes back to the main inbox.
"""

from __future__ import annotations

import json
import re

import frappe

OTHER = "Other"

BULK_CATEGORIES = ("CATEGORY_PROMOTIONS", "CATEGORY_UPDATES", "CATEGORY_SOCIAL", "CATEGORY_FORUMS")

# The local part of an address nobody reads replies to. Not "info@": that is often a real business.
_AUTOMATED_SENDER = re.compile(
	r"^(no-?reply|do-?not-?reply|notifications?|alerts?|mailer-daemon|postmaster|newsletters?|news"
	r"|updates?|billing|statements?|ebill|bounces?)([._+-][^@]*)?@",
	re.IGNORECASE,
)

# Headers asked of Gmail alongside the usual ones (gmail_service.get_message_metadata).
LIST_HEADERS = ("List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted")


def looks_automated(label_ids: list | None, from_email: str = "", headers: dict | None = None) -> bool:
	"""True when the message reads as bulk or machine-sent rather than written to us by a person."""
	labels = set(label_ids or [])
	headers = headers or {}
	if "CATEGORY_PERSONAL" not in labels and labels.intersection(BULK_CATEGORIES):
		return True
	if headers.get("List-Unsubscribe") or headers.get("List-Id"):
		return True
	if (headers.get("Precedence") or "").strip().lower() in ("bulk", "list", "junk"):
		return True
	auto = (headers.get("Auto-Submitted") or "").strip().lower()
	if auto and auto != "no":
		return True
	return bool(from_email and _AUTOMATED_SENDER.match(from_email.strip()))


def is_engaged(thread: str, identity: str | None) -> bool:
	"""A conversation that has become a relationship stays in the main inbox."""
	if frappe.db.exists("Excom Message", {"thread": thread, "direction": "Outbound"}):
		return True
	if not identity:
		return False
	if frappe.db.exists("Omni Identity Link", {"parent": identity, "parenttype": "Omni Identity"}):
		return True
	return bool(
		frappe.db.exists("Excom Thread", {"omni_identity": identity, "channel": ["!=", "email"], "name": ["!=", thread]})
	)


def classify(thread: str, identity: str | None, label_ids: list | None, from_email: str = "", headers: dict | None = None) -> str:
	"""Set the lane from the latest inbound message, unless somebody set it by hand. Returns the lane."""
	current = frappe.db.get_value("Excom Thread", thread, ["inbox_lane", "inbox_lane_pinned"], as_dict=True)
	if not current:
		return ""
	if current.inbox_lane_pinned:
		return current.inbox_lane or ""
	lane = OTHER if looks_automated(label_ids, from_email, headers) and not is_engaged(thread, identity) else ""
	if (current.inbox_lane or "") != lane:
		frappe.db.set_value("Excom Thread", thread, "inbox_lane", lane, update_modified=False)
	return lane


def set_lane(threads: list[str], lane: str) -> None:
	"""A person moved these conversations: the lane sticks until somebody moves them back."""
	lane = OTHER if lane == OTHER else ""
	for t in threads:
		frappe.db.set_value("Excom Thread", t, {"inbox_lane": lane, "inbox_lane_pinned": 1}, update_modified=False)


def focused_clause(alias: str = "t") -> str:
	return f"IFNULL({alias}.inbox_lane, '') != '{OTHER}'"


def other_clause(alias: str = "t") -> str:
	return f"{alias}.inbox_lane = '{OTHER}'"


def backfill(limit: int | None = None) -> dict:
	"""Sort the email threads that are already there, from the labels stored on their messages.

	Only the latest inbound message of each thread counts, as it would for new mail. List headers
	were not stored before this change, so old mail is sorted on Gmail's categories and the sender.
	"""
	rows = frappe.db.sql(
		"""
		SELECT t.name, t.omni_identity
		FROM `tabExcom Thread` t
		WHERE t.channel = 'email' AND IFNULL(t.inbox_lane_pinned, 0) = 0
		"""
		+ (" LIMIT %(limit)s" if limit else ""),
		{"limit": limit},
		as_dict=True,
	)
	moved = 0
	for r in rows:
		last = frappe.db.get_value(
			"Excom Message",
			{"thread": r.name, "direction": "Inbound"},
			"content_json",
			order_by="provider_timestamp desc",
		)
		if not last:
			continue
		try:
			data = json.loads(last) or {}
		except (TypeError, ValueError):
			continue
		if classify(r.name, r.omni_identity, data.get("label_ids"), data.get("from_email") or "", data.get("list_headers")) == OTHER:
			moved += 1
	return {"threads": len(rows), "other": moved}
