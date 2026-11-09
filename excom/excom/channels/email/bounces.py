"""Delivery reports: an email we sent did not arrive (HLD-007 §11.1).

A bounce is recognised at ingest (from MAILER-DAEMON or postmaster, or a delivery-failure
subject) and kept as an Excom Mail Bounce, never as a person or a conversation. The failed
address and the original email are found from the report itself: the DSN fields
(Final-Recipient, Status) and the original's Message-ID quoted inside it, else the address
matched against what this mailbox sent recently.

Then everything registered in `excom_bounce_handlers` (hooks.py) is told, with one event:

	{"bounce", "account", "recipient", "kind": Hard | Soft | Unknown, "status_code",
	 "original_message", "thread", "identity"}

Excom AI marks the address invalid and stops the sequence (excom_ai/mail.py).
"""

from __future__ import annotations

import re

import frappe
from frappe.utils import add_days, now_datetime

BOUNCE_SENDERS = ("mailer-daemon", "postmaster", "mail-daemon", "mailerdaemon")
BOUNCE_SUBJECTS = ("undeliverable", "delivery status notification (failure)", "delivery status notification (delay)",
	"mail delivery failed", "returned mail", "undelivered mail returned to sender", "failure notice",
	"delivery failure", "message not delivered", "mail delivery subsystem", "delivery has failed")
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
FINAL = re.compile(r"(?:final|original)-recipient:\s*rfc822;\s*<?([^\s>;]+@[^\s>;]+)>?", re.I)
STATUS = re.compile(r"\bstatus:\s*([245]\.\d{1,3}\.\d{1,3})", re.I)
SMTP = re.compile(r"\b([45])[0-9]{2}[ -]([245]\.\d{1,3}\.\d{1,3})?")
MSGID = re.compile(r"message-id:\s*(<[^>\s]+>)", re.I)
HARD_WORDS = ("does not exist", "user unknown", "no such user", "address not found", "mailbox unavailable",
	"recipient address rejected", "invalid recipient", "unknown user", "account has been disabled", "no longer available")
SOFT_WORDS = ("mailbox full", "over quota", "temporarily", "try again later", "delayed", "greylist")


def is_bounce(from_email: str, subject: str, folder: str = "Inbox") -> bool:
	if folder in ("Sent", "Drafts"):
		return False
	local = (from_email or "").split("@", 1)[0].lower()
	return local in BOUNCE_SENDERS or (subject or "").strip().lower().startswith(BOUNCE_SUBJECTS)


def known(sid: str) -> bool:
	return bool(sid) and bool(frappe.db.exists("Excom Mail Bounce", {"provider_message_id": sid}))


def parse(text: str, ours: str = "") -> dict:
	"""Recipient, status code, kind and the original Message-ID from a delivery report's text."""
	text = text or ""
	recipient = (FINAL.search(text) or [None, None])[1]
	if not recipient:
		ignore = {(ours or "").lower()}
		for addr in EMAIL.findall(text):
			a = addr.lower()
			if a not in ignore and a.split("@", 1)[0] not in BOUNCE_SENDERS:
				recipient = a
				break
	code = (STATUS.search(text) or [None, None])[1]
	if not code:
		m = SMTP.search(text)
		code = (m.group(2) or f"{m.group(1)}.0.0") if m else None
	low = text.lower()
	if code and code.startswith("5"):
		kind = "Hard"
	elif code and code.startswith("4"):
		kind = "Soft"
	elif any(w in low for w in HARD_WORDS):
		kind = "Hard"
	elif any(w in low for w in SOFT_WORDS):
		kind = "Soft"
	else:
		kind = "Unknown"
	ids = MSGID.findall(text)
	return {"recipient": (recipient or "").lower().strip(".<>"), "status_code": code, "kind": kind, "message_ids": ids}


def handle(account, meta: dict) -> str | None:
	"""Record one delivery report and tell the handlers. Returns the Excom Mail Bounce name."""
	from excom.excom.channels.email import providers as P

	sid = P.store_id(account, meta["id"])
	if known(sid):
		return None
	headers = meta.get("headers") or {}
	text = meta.get("snippet") or ""
	try:
		full = P.get(account).fetch_full(account, P.raw_id(account, sid)) or {}
		text = "\n".join(filter(None, [full.get("body_text"), frappe.utils.strip_html_tags(full.get("body_html") or ""), text]))
	except Exception:
		frappe.log_error(title="Excom: could not read a delivery report", message=frappe.get_traceback())
	info = parse(text, account.email_address)
	original = _original(account, info, headers)
	thread = frappe.db.get_value("Excom Message", original, "thread") if original else None
	doc = frappe.get_doc({
		"doctype": "Excom Mail Bounce", "account": account.name, "provider_message_id": sid,
		"received_on": now_datetime(), "kind": info["kind"], "recipient": info["recipient"] or None,
		"status_code": info["status_code"], "original_message": original, "thread": thread,
		"reason": text[:1000], "subject": (headers.get("Subject") or "")[:140],
	}).insert(ignore_permissions=True)
	event = {"bounce": doc.name, "account": account.name, "recipient": info["recipient"], "kind": info["kind"],
		"status_code": info["status_code"], "original_message": original, "thread": thread,
		"identity": frappe.db.get_value("Excom Thread", thread, "omni_identity") if thread else None}
	frappe.db.commit()
	for path in frappe.get_hooks("excom_bounce_handlers") or []:
		try:
			frappe.get_attr(path)(event)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom: bounce handler failed: {path}", message=frappe.get_traceback())
	return doc.name


def _original(account, info: dict, headers: dict) -> str | None:
	"""The email that bounced: by the Message-ID quoted in the report or the report's own
	In-Reply-To, else the latest email this mailbox sent to the failed address."""
	ids = list(info["message_ids"]) + [h for h in (headers.get("In-Reply-To"), ) if h]
	for mid in ids:
		name = frappe.db.get_value("Excom Message", {"account": account.name, "direction": "Outbound",
			"internet_message_id": mid.strip()})
		if name:
			return name
	if info["recipient"]:
		identity = frappe.db.get_value("Omni Identity", {"primary_email": info["recipient"]})
		if identity:
			return frappe.db.get_value("Excom Message", {"account": account.name, "direction": "Outbound",
				"omni_identity": identity, "creation": [">=", add_days(now_datetime(), -30)]}, "name",
				order_by="creation desc")
	return None


def bounce_rate(account: str, days: int = 7) -> tuple[int, int]:
	"""(hard bounces, emails sent) from this mailbox in the last days."""
	since = add_days(now_datetime(), -days)
	sent = frappe.db.count("Excom Message", {"account": account, "direction": "Outbound", "channel": "email",
		"creation": [">=", since]})
	hard = frappe.db.count("Excom Mail Bounce", {"account": account, "kind": "Hard", "received_on": [">=", since]})
	return hard, sent
