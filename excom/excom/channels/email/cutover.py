"""Turning on two-way mail for mailboxes that were already syncing one way (Gmail, Inbox only).

1. fill_folders(): every email already here gets its folder, read and flagged marks and its
   Message-ID from what was stored at the time it came in. Read follows Excom, not the old
   snapshot: a conversation people here have read counts as read, so switching this on does not
   bring back hundreds of old unread emails. The one-off Gmail draft stub becomes a real draft.

2. align(account), in the background per mailbox, once:
   - asks the mailbox where each of those emails is now (moved, binned or marked spam in Gmail
     since), and moves them here to match, without opening or closing any conversation;
   - then makes the mailbox match what people did here: emails Excom has as read are marked read
     there, and conversations archived here have their emails archived there.
   After that, sync keeps both sides in step (channels/email/mailbox.py).
"""

from __future__ import annotations

import json

import frappe

from excom.excom.channels.email import providers as P
from excom.excom.channels.email.providers.gmail import state_of


def fill_folders() -> dict:
	rows = frappe.db.sql(
		"""SELECT m.name, m.thread, m.direction, m.content_json, m.provider_message_id, m.account
		FROM `tabExcom Message` m WHERE m.channel = 'email' AND m.message_type = 'Email'
		AND IFNULL(m.mail_folder, '') = '' ORDER BY m.thread, m.provider_timestamp""",
		as_dict=True,
	)
	unread = dict(frappe.db.sql("SELECT name, unread_count FROM `tabExcom Thread` WHERE channel = 'email'"))
	by_thread: dict[str, list] = {}
	for r in rows:
		by_thread.setdefault(r.thread, []).append(r)
	done = drafts = 0
	for thread, msgs in by_thread.items():
		# The newest N emails from them stay unread, N being the conversation's unread count here.
		left = int(unread.get(thread) or 0)
		inbound_new_first = [m.name for m in reversed(msgs) if m.direction == "Inbound"]
		unseen = set(inbound_new_first[:left])
		for m in msgs:
			try:
				cj = json.loads(m.content_json or "{}") or {}
			except (TypeError, ValueError):
				cj = {}
			labels = cj.get("label_ids") or []
			if not m.provider_message_id:  # scheduled, or failed before reaching the mailbox
				continue
			folder, _seen, flagged = state_of(labels)
			if m.direction == "Outbound" and folder in (P.INBOX, P.ARCHIVE):
				folder = P.SENT
			if folder == P.DRAFTS:
				_to_draft(m, cj)
				drafts += 1
				continue
			frappe.db.set_value("Excom Message", m.name, {
				"mail_folder": folder,
				"mail_seen": 0 if m.name in unseen else 1,
				"mail_flagged": 1 if flagged else 0,
				"internet_message_id": (cj.get("message_id_header") or "")[:500],
				"provider_thread_id": (cj.get("provider_thread_id") or cj.get("gmail_thread_id") or "")[:255],
			}, update_modified=False)
			done += 1
	frappe.db.commit()
	return {"emails": done, "drafts": drafts}


def _to_draft(m, cj: dict) -> None:
	if not frappe.db.exists("Excom Email Draft", {"provider_message_id": m.provider_message_id}):
		frappe.get_doc({
			"doctype": "Excom Email Draft", "thread": m.thread, "account": m.account, "origin": "Mailbox",
			"to_addr": cj.get("to", ""), "cc": cj.get("cc", ""), "subject": cj.get("subject", ""),
			"snippet": cj.get("snippet", ""), "provider_message_id": m.provider_message_id,
			"provider_thread_id": cj.get("gmail_thread_id", ""),
		}).insert(ignore_permissions=True)
	frappe.db.delete("Excom Message", {"name": m.name})


def enqueue_align() -> None:
	for name in frappe.get_all("Excom Channel Account", {"channel": "email", "status": "Active", "email_authorized": 1}, pluck="name"):
		frappe.enqueue(
			"excom.excom.channels.email.cutover.align", queue="long", timeout=7200,
			account_name=name, enqueue_after_commit=True, job_id=f"excom_mail_align:{name}", deduplicate=True,
		)


def align(account_name: str) -> dict:
	account = frappe.get_doc("Excom Channel Account", account_name)
	provider = P.get(account)
	current = P.provider_name(account)
	rows = frappe.get_all(
		"Excom Message",
		filters={"account": account_name, "channel": "email", "message_type": "Email", "provider_message_id": ["is", "set"]},
		fields=["name", "thread", "direction", "mail_folder", "mail_seen", "mail_flagged", "provider_message_id"],
	)
	moved = gone = 0
	for r in rows:
		if P.provider_of_stored(r.provider_message_id) != current:
			continue
		try:
			meta = provider.get_meta(account, P.raw_id(account, r.provider_message_id))
		except Exception:
			frappe.log_error(title=f"Excom mail align: could not read {r.name}")
			continue
		if not meta:
			gone += 1
			continue
		folder = meta["folder"]
		if r.direction == "Outbound" and folder in (P.INBOX, P.ARCHIVE):
			folder = P.SENT
		if folder != r.mail_folder or int(bool(meta["flagged"])) != int(r.mail_flagged or 0):
			# Where it is now, but no conversation opens or closes because of the switch-over.
			frappe.db.set_value("Excom Message", r.name, {"mail_folder": folder, "mail_flagged": 1 if meta["flagged"] else 0}, update_modified=False)
			moved += 1
		r.mail_folder = folder
		r.remote_seen = bool(meta["seen"])
	frappe.db.commit()

	# The mailbox catches up with Excom: read here -> read there; archived here -> archived there.
	read_here = [P.raw_id(account, r.provider_message_id) for r in rows
		if r.get("remote_seen") is False and r.direction == "Inbound" and r.mail_seen]
	for k in range(0, len(read_here), 500):
		provider.set_seen(account, read_here[k:k + 500], True)
	closed = set(frappe.get_all("Excom Thread", {"account": account_name, "status": "Closed"}, pluck="name"))
	to_archive = [r for r in rows if r.thread in closed and r.mail_folder == P.INBOX and "remote_seen" in r]
	if to_archive:
		provider.move(account, [P.raw_id(account, r.provider_message_id) for r in to_archive], P.ARCHIVE)
		for r in to_archive:
			frappe.db.set_value("Excom Message", r.name, "mail_folder", P.ARCHIVE, update_modified=False)
	frappe.db.commit()
	out = {"emails": len(rows), "moved": moved, "gone": gone, "marked_read": len(read_here), "archived": len(to_archive)}
	frappe.logger("excom").info(f"mail align {account_name}: {out}")
	return out
