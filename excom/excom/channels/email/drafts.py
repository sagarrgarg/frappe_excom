"""Drafts, kept in step with the mailbox's Drafts folder (Gmail or a JMAP server).

Typing in Excom saves an Excom Email Draft and the same draft in the mailbox, so it is there in
Gmail, Outlook or the Stalwart webmail too. A draft started in one of those shows up here under its
conversation; its text stays on the mail server until somebody opens it in Excom. Sending sends
that same draft, so it leaves Drafts everywhere at once; discarding deletes it everywhere.

A draft is not an Excom Message: until it is sent it must not count as something we said.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.channels.email import providers as P

DT = "Excom Email Draft"
FIELDS = ["name", "thread", "account", "author", "origin", "to_addr", "cc", "bcc", "subject", "body_html",
	"snippet", "in_reply_to_message", "provider_draft_id", "provider_message_id", "synced_at", "sync_error", "modified"]


def _public(row) -> dict:
	return {
		"name": row.name, "thread": row.thread, "to": row.to_addr or "", "cc": row.cc or "", "bcc": row.bcc or "",
		"subject": row.subject or "", "body_html": row.body_html or "", "in_reply_to_message": row.in_reply_to_message or "",
		"author": row.author, "author_name": frappe.utils.get_fullname(row.author) if row.author else "",
		"origin": row.origin, "saved_at": str(row.modified), "synced": bool(row.synced_at) and not row.sync_error,
		"sync_error": row.sync_error or "",
	}


def _ours(account, row) -> bool:
	"""The mailbox draft is on this mailbox's current server (not left behind on Gmail after a move)."""
	return bool(row.provider_message_id) and P.provider_of_stored(row.provider_message_id) == P.provider_name(account)


def _draft_handle(account, row) -> str:
	if not _ours(account, row):
		return ""
	if row.provider_draft_id:
		return row.provider_draft_id
	handle = P.get(account).draft_id_for(account, P.raw_id(account, row.provider_message_id))
	if handle:
		frappe.db.set_value(DT, row.name, "provider_draft_id", handle, update_modified=False)
	return handle


def for_thread(thread: str) -> dict | None:
	"""The conversation's latest draft, its text fetched from the mailbox if it was written there."""
	rows = frappe.get_all(DT, filters={"thread": thread}, fields=FIELDS, order_by="modified desc", limit=1)
	if not rows:
		return None
	row = rows[0]
	if row.origin == "Mailbox" and not row.body_html and row.provider_message_id:
		account = frappe.get_doc("Excom Channel Account", row.account)
		try:
			full = P.get(account).fetch_full(account, P.raw_id(account, row.provider_message_id))
			if not full.get("deleted"):
				h = full.get("headers") or {}
				row.body_html = full.get("body_html") or frappe.utils.escape_html(full.get("body_text") or "").replace("\n", "<br>")
				row.to_addr = h.get("To", row.to_addr)
				row.cc = h.get("Cc", row.cc)
				row.subject = h.get("Subject", row.subject)
				frappe.db.set_value(DT, row.name, {"body_html": row.body_html, "to_addr": row.to_addr, "cc": row.cc, "subject": row.subject}, update_modified=False)
		except Exception:
			frappe.log_error(title=f"Excom draft: could not read {row.name} from the mailbox")
	return _public(row)


def mail_for(thread: str, to: str, cc: str, bcc: str, subject: str, body_html: str, in_reply_to_message: str = "") -> dict:
	from excom.excom.channels.email.outbound import reply_headers

	return {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body_html": body_html,
		"from_name": "", **reply_headers(thread, in_reply_to_message)}


def save(thread: str, to: str = "", cc: str = "", bcc: str = "", subject: str = "", body_html: str = "",
		in_reply_to_message: str = "", name: str = "") -> dict:
	"""Save here, then in the mailbox. A mailbox that cannot be reached leaves the draft saved here, with the reason."""
	t = frappe.db.get_value("Excom Thread", thread, ["name", "account", "channel"], as_dict=True)
	if not t or t.channel != "email":
		frappe.throw(_("Drafts are for email conversations"))
	if in_reply_to_message and not frappe.db.exists("Excom Message", in_reply_to_message):
		# the composer knows the answered email by its mailbox id
		in_reply_to_message = frappe.db.get_value("Excom Message", {"provider_message_id": in_reply_to_message, "thread": thread}, "name") or ""
	doc = frappe.get_doc(DT, name) if name and frappe.db.exists(DT, name) else frappe.new_doc(DT)
	if not doc.is_new() and doc.thread != thread:
		frappe.throw(_("That draft belongs to another conversation"), frappe.PermissionError)
	doc.update({
		"thread": thread, "account": t.account, "author": frappe.session.user, "to_addr": to, "cc": cc, "bcc": bcc,
		"subject": subject, "body_html": body_html, "in_reply_to_message": in_reply_to_message or None,
		"snippet": frappe.utils.strip_html_tags(body_html or "")[:150],
	})
	if doc.is_new():
		doc.origin = "Excom"
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)

	account = frappe.get_doc("Excom Channel Account", t.account)
	try:
		r = P.get(account).save_draft(account, mail_for(thread, to, cc, bcc, subject, body_html, in_reply_to_message), draft_id=_draft_handle(account, doc))
		frappe.db.set_value(DT, doc.name, {
			"provider_draft_id": r.get("draft_id", ""), "provider_message_id": P.store_id(account, r.get("id", "")),
			"provider_thread_id": r.get("thread_id", ""), "synced_at": now_datetime(), "sync_error": "",
		}, update_modified=False)
	except Exception as e:
		frappe.db.set_value(DT, doc.name, "sync_error", str(e)[:500], update_modified=False)
		frappe.log_error(title=f"Excom draft: mailbox save failed for {t.account}", message=frappe.get_traceback())
	frappe.publish_realtime("excom:thread_updated", {"thread": thread, "event": "draft"}, after_commit=True)
	return _public(frappe.get_doc(DT, doc.name))


def discard(name: str) -> None:
	row = frappe.get_doc(DT, name)
	account = frappe.get_doc("Excom Channel Account", row.account)
	try:
		handle = _draft_handle(account, row)
		if handle:
			P.get(account).delete_draft(account, handle)
	except Exception:
		frappe.log_error(title=f"Excom draft: mailbox delete failed for {row.account}", message=frappe.get_traceback())
	frappe.db.delete(DT, {"name": name})
	frappe.publish_realtime("excom:thread_updated", {"thread": row.thread, "event": "draft"}, after_commit=True)


def handle_for_send(name: str, mail: dict) -> str:
	"""Bring the mailbox draft up to the final text and return its handle, so sending sends it.
	"" when there is no mailbox copy to send (then a fresh email goes, and the draft row is removed)."""
	if not name or not frappe.db.exists(DT, name):
		return ""
	row = frappe.get_doc(DT, name)
	account = frappe.get_doc("Excom Channel Account", row.account)
	handle = _draft_handle(account, row)
	if not handle:
		return ""
	try:
		return P.get(account).save_draft(account, mail, draft_id=handle).get("draft_id", "")
	except Exception:
		frappe.log_error(title=f"Excom draft: could not update before sending {name}", message=frappe.get_traceback())
		return ""


def sent(name: str, account, handle: str) -> None:
	"""The draft went out. Without a mailbox handle a stale mailbox copy may remain: delete it."""
	if not name or not frappe.db.exists(DT, name):
		return
	if not handle:
		try:
			row = frappe.get_doc(DT, name)
			old = _draft_handle(account, row)
			if old:
				P.get(account).delete_draft(account, old)
		except Exception:
			frappe.log_error(title=f"Excom draft: stale mailbox copy left for {name}")
	thread = frappe.db.get_value(DT, name, "thread")
	frappe.db.delete(DT, {"name": name})
	frappe.publish_realtime("excom:thread_updated", {"thread": thread, "event": "draft"}, after_commit=True)


# ── from the mailbox ──────────────────────────────────────────────────────────


def from_mailbox(account, meta: dict) -> None:
	"""A draft written in Gmail or a mail app: file it under the conversation with the person it is to."""
	sid = P.store_id(account, meta["id"])
	if frappe.db.exists(DT, {"provider_message_id": sid}):
		return
	from excom.excom.channels.email.mailbox import parse_address

	h = meta.get("headers") or {}
	email, name = parse_address(h.get("To", ""))
	if not email:
		return  # no recipient yet: nothing to file it under
	from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
	from excom.excom.services.thread_service import upsert_thread

	identity = resolve_identity_internal(email=email, channel="email", channel_user_id=email, display_name=name or email)
	key = f"email:{account.name}:{identity}"
	new = not frappe.db.exists("Excom Thread", {"thread_key": key})
	thread = upsert_thread(identity, "email", account.name)
	if new:
		frappe.db.set_value("Excom Thread", thread, {"status": "Closed", "last_message_preview": f"Draft: {h.get('Subject') or ''}"[:120]}, update_modified=False)
	frappe.get_doc({
		"doctype": DT, "thread": thread, "account": account.name, "origin": "Mailbox",
		"to_addr": h.get("To", ""), "cc": h.get("Cc", ""), "bcc": h.get("Bcc", ""), "subject": h.get("Subject", ""),
		"snippet": meta.get("snippet", ""), "provider_message_id": sid, "provider_thread_id": meta.get("thread_id", ""),
		"synced_at": now_datetime(),
	}).insert(ignore_permissions=True)
	frappe.publish_realtime("excom:thread_updated", {"thread": thread, "event": "draft"}, after_commit=True)


def removed_in_mailbox(sid: str) -> None:
	for name, thread in frappe.get_all(DT, filters={"provider_message_id": sid}, fields=["name", "thread"], as_list=True):
		frappe.db.delete(DT, {"name": name})
		frappe.publish_realtime("excom:thread_updated", {"thread": thread, "event": "draft"}, after_commit=True)


def left_drafts(account, sid: str) -> bool:
	"""The mailbox says this draft is not a draft any more (sent from the mail app)."""
	rows = frappe.get_all(DT, filters={"provider_message_id": sid}, pluck="name")
	for n in rows:
		frappe.db.delete(DT, {"name": n})
	return bool(rows)
