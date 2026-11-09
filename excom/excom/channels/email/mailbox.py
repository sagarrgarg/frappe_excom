"""Excom's side of a mailbox: where each email sits, and keeping that in step both ways.

Every email Excom knows is an Excom Message with a folder (Inbox, Archive, Sent, Drafts, Spam,
Trash) and read / flagged marks, the JMAP model (providers/__init__.py). Gmail and a JMAP server
such as Stalwart both feed it through the same Sink, and both receive what people do here.

A conversation in Excom is one person on one mailbox, so it can hold many emails. What happens to
one email moves the conversation like this:

    an email arrives in Inbox            the conversation opens (if archived) and counts as unread
    an email arrives straight in Spam    a new conversation starts in Spam
    an email arrives in Archive / Trash  a new conversation starts archived (a mail filter skipped the inbox)
    we wrote first, from the mail app    a new conversation starts archived; it shows in Sent
    the last email leaves Inbox          the conversation is archived (or Spam, if it went to Spam)
    an email comes back to Inbox         the conversation opens again
    read / unread in the mail app        the unread count follows

and the other way, what people do in Excom is done in the mailbox (move_thread, set_thread_seen):

    Archive        its Inbox emails go to Archive
    Move to inbox  its archived emails from them go back to Inbox
    Spam           their emails go to Spam;  Not spam: back to Inbox
    Delete         every email goes to Trash; Restore: back to Inbox (theirs) or Sent (ours)
    open / mark unread    read marks on their emails

The mailbox is told in a background job after the change is saved here; if it refuses, the next
sync brings the mailbox's own state back.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe.utils import now_datetime

from excom.excom.channels.email import providers as P
from excom.excom.channels.email.providers import ARCHIVE, DRAFTS, INBOX, SENT, SPAM, TRASH

MSG = "Excom Message"
THREAD = "Excom Thread"


# ── coming in ─────────────────────────────────────────────────────────────────


class Sink:
	"""What a provider's sync() writes into (see providers/__init__.py)."""

	def __init__(self, account):
		self.account = account

	def known(self, sid: str) -> bool:
		from excom.excom.channels.email import bounces

		return bool(
			frappe.db.exists(MSG, {"provider_message_id": sid})
			or frappe.db.exists("Excom Email Draft", {"provider_message_id": sid})
			or bounces.known(sid)
		)

	def ingest(self, meta: dict):
		return ingest(self.account, meta)

	def update(self, sid: str, folder: str, seen: bool, flagged: bool) -> None:
		name = frappe.db.get_value(MSG, {"provider_message_id": sid}, "name")
		if name:
			apply_state(name, folder, seen, flagged)
			return
		from excom.excom.channels.email import drafts

		if folder != DRAFTS and drafts.left_drafts(self.account, sid):
			# A draft that was sent from the mail app keeps its id: it is a sent email now.
			meta = P.get(self.account).get_meta(self.account, P.raw_id(self.account, sid))
			if meta:
				ingest(self.account, meta)

	def remove(self, sid: str) -> None:
		from excom.excom.channels.email import drafts

		drafts.removed_in_mailbox(sid)
		name = frappe.db.get_value(MSG, {"provider_message_id": sid}, ["name", "thread"], as_dict=True)
		if name:
			frappe.db.delete(MSG, {"name": name.name})
			frappe.publish_realtime("excom:thread_updated", {"thread": name.thread, "event": "mail_deleted"}, after_commit=True)


def parse_address(raw: str) -> tuple[str, str]:
	"""'Display Name <email@example.com>' into (email, display name)."""
	if not raw:
		return "", ""
	m = re.match(r'^"?([^"<]*)"?\s*<([^>]+)>', raw.strip())
	if m:
		return m.group(2).strip(), m.group(1).strip()
	m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", raw)
	if m:
		return m.group(0), ""
	return raw.strip(), ""


def _time(meta: dict):
	if meta.get("received_at"):
		try:
			from excom.excom.utils.site_time import epoch_to_site_time

			return epoch_to_site_time(float(meta["received_at"]))
		except (ValueError, OSError, TypeError):
			pass
	return now_datetime()


def ingest(account, meta: dict) -> str | None:
	"""Add one email from the mailbox. Returns the Excom Message name (None for a draft)."""
	if meta.get("folder") == DRAFTS:
		from excom.excom.channels.email import drafts

		drafts.from_mailbox(account, meta)
		return None

	sid = P.store_id(account, meta["id"])
	headers = meta.get("headers") or {}
	internet_id = (headers.get("Message-ID") or "").strip()
	folder = meta.get("folder") or INBOX

	# The same email already here under another id: the mailbox moved servers (Gmail -> Stalwart).
	if internet_id:
		moved = frappe.db.get_value(
			MSG, {"account": account.name, "internet_message_id": internet_id, "provider_message_id": ["!=", sid]}, "name"
		)
		if moved:
			cj = _content(moved)
			cj.update({"gmail_message_id": sid, "provider": P.provider_name(account), "provider_thread_id": meta.get("thread_id", "")})
			frappe.db.set_value(MSG, moved, {"provider_message_id": sid, "provider_thread_id": meta.get("thread_id", ""), "content_json": json.dumps(cj)}, update_modified=False)
			apply_state(moved, folder, meta.get("seen"), meta.get("flagged"))
			return moved

	from_raw = headers.get("From", "")
	to_raw = headers.get("To", "")
	subject = headers.get("Subject") or "(No Subject)"
	from_email, from_name = parse_address(from_raw)
	from excom.excom.channels.email import bounces

	if bounces.is_bounce(from_email, subject, folder):
		# A delivery report: kept as a bounce, never as a person or a conversation.
		bounces.handle(account, meta)
		return None
	ours = (account.email_address or "").strip().lower()
	outbound = folder == SENT or bool(ours and from_email.lower() == ours)
	direction = "Outbound" if outbound else "Inbound"
	if outbound:
		contact_email, contact_name = parse_address(to_raw)
		contact_name = contact_name or contact_email
	else:
		contact_email, contact_name = from_email, from_name or from_email
	if not contact_email:
		return None

	from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
	from excom.excom.services.thread_service import upsert_thread

	identity = resolve_identity_internal(email=contact_email, channel="email", channel_user_id=contact_email, display_name=contact_name)
	is_new = not frappe.db.exists(THREAD, {"thread_key": f"email:{account.name}:{identity}"})
	# One conversation per person per mailbox (as for WhatsApp); the mailbox's own thread id is kept per email.
	thread = upsert_thread(identity, "email", account.name)

	from excom.excom.services.inbox_lane import LIST_HEADERS

	by_lower = {k.lower(): v for k, v in headers.items()}
	list_headers = {h: by_lower[h.lower()] for h in LIST_HEADERS if by_lower.get(h.lower())}
	snippet = meta.get("snippet") or ""
	content = {
		# "gmail_message_id" is the name the composer and older rows use for the mailbox's id; it holds
		# the stored id whatever the server (providers.store_id).
		"gmail_message_id": sid,
		"gmail_thread_id": meta.get("thread_id", "") if P.provider_name(account) == P.GMAIL else "",
		"provider": P.provider_name(account),
		"provider_thread_id": meta.get("thread_id", ""),
		"subject": subject,
		"from_email": from_email,
		"from_name": from_name,
		"to": to_raw,
		"cc": headers.get("Cc", ""),
		"date": headers.get("Date", ""),
		"message_id_header": internet_id,
		"in_reply_to": headers.get("In-Reply-To", ""),
		"references": headers.get("References", ""),
		"label_ids": meta.get("labels") or [],
		"snippet": snippet,
	}
	if list_headers:
		content["list_headers"] = list_headers
	ts = _time(meta)
	seen = bool(meta.get("seen")) or outbound
	msg = frappe.get_doc({
		"doctype": MSG,
		"thread": thread,
		"omni_identity": identity,
		"channel": "email",
		"account_doctype": "Excom Channel Account",
		"account": account.name,
		"direction": direction,
		"message_type": "Email",
		"provider_message_id": sid,
		"provider_timestamp": ts,
		"content_text": snippet[:150],
		"content_json": json.dumps(content),
		"delivery_status": "Sent" if outbound else "Delivered",
		"mail_folder": folder,
		"mail_seen": 1 if seen else 0,
		"mail_flagged": 1 if meta.get("flagged") else 0,
		"internet_message_id": internet_id[:500],
		"provider_thread_id": (meta.get("thread_id") or "")[:255],
	})
	msg.insert(ignore_permissions=True)

	preview = f"{subject}: {snippet}"[:120]
	now = now_datetime()
	from excom.excom.services import inbox_lane

	if outbound:
		if not frappe.db.get_value(THREAD, thread, "inbox_lane_pinned"):
			# Somebody here wrote to them: a conversation now, not a mailing list.
			frappe.db.set_value(THREAD, thread, "inbox_lane", "", update_modified=False)
		frappe.db.sql(
			"""UPDATE `tabExcom Thread` SET last_message_at = GREATEST(IFNULL(last_message_at, %(ts)s), %(ts)s),
				last_outbound_at = %(ts)s, last_message_preview = %(preview)s, last_message_direction = 'Outbound',
				status = IF(%(new)s, 'Closed', status), modified = %(now)s WHERE name = %(thread)s""",
			{"ts": ts, "now": now, "preview": preview, "thread": thread, "new": 1 if is_new else 0},
		)
	elif folder == INBOX:
		inbox_lane.classify(thread, identity, meta.get("categories") or meta.get("labels"), from_email, list_headers)
		frappe.db.sql(
			"""UPDATE `tabExcom Thread` SET last_message_at = %(ts)s, last_inbound_at = %(ts)s,
				unread_count = unread_count + %(unread)s, last_message_preview = %(preview)s,
				last_message_direction = 'Inbound',
				status = CASE WHEN status IN ('Closed', 'Spam') THEN 'Open' ELSE status END,
				modified = %(now)s WHERE name = %(thread)s""",
			{"ts": ts, "now": now, "preview": preview, "thread": thread, "unread": 0 if seen else 1},
		)
		frappe.publish_realtime(
			"excom:message_received",
			{"thread": thread, "message": msg.name, "omni_identity": identity, "direction": direction, "preview": preview},
			after_commit=True,
		)
	elif is_new:
		# Straight to Spam, Archive or Trash: start it where the mailbox put it, without a badge.
		frappe.db.set_value(
			THREAD, thread,
			{"status": "Spam" if folder == SPAM else "Closed", "last_message_at": ts, "last_inbound_at": ts,
			 "last_message_preview": preview, "last_message_direction": "Inbound"},
			update_modified=False,
		)
	frappe.publish_realtime("excom:thread_updated", {"thread": thread, "event": "new_email"}, after_commit=True)
	frappe.db.commit()
	if not outbound and folder == INBOX:
		# A new email in the inbox goes to the features that act on replies (AI agents among them).
		# Spam, archive and trash do not; old mail synced in later is skipped by message_event.
		from excom.excom.services.inbound_dispatch import message_event

		message_event(msg.name, kind="email", subject=subject,
			payload={"from_email": from_email, "in_reply_to": headers.get("In-Reply-To", ""),
				"list": bool(list_headers), "auto": is_automatic(by_lower, from_email, subject)})
	return msg.name


AUTO_SUBJECTS = ("out of office", "automatic reply", "auto-reply", "autoreply", "undeliverable", "delivery status notification",
	"mail delivery failed", "returned mail", "fuera de la oficina", "respuesta automática", "ausência", "abwesenheit")


def is_automatic(headers_lower: dict, from_email: str, subject: str) -> bool:
	"""An out-of-office, an auto-reply or a delivery report, not something a person wrote."""
	auto = (headers_lower.get("auto-submitted") or "").lower()
	if auto and auto != "no":
		return True
	if any(headers_lower.get(h) for h in ("x-autoreply", "x-autorespond", "x-auto-response-suppress")):
		return True
	if (headers_lower.get("precedence") or "").lower() in ("auto_reply", "bulk", "junk"):
		return True
	local = (from_email or "").split("@", 1)[0].lower()
	if local in ("mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply"):
		return True
	return (subject or "").strip().lower().startswith(AUTO_SUBJECTS)


def _content(name: str) -> dict:
	try:
		return json.loads(frappe.db.get_value(MSG, name, "content_json") or "{}") or {}
	except (TypeError, ValueError):
		return {}


def apply_state(name: str, folder: str, seen, flagged) -> None:
	"""The mailbox says this email is now here, read or not, flagged or not. Move the conversation to match."""
	row = frappe.db.get_value(MSG, name, ["thread", "direction", "mail_folder", "mail_seen", "mail_flagged"], as_dict=True)
	if not row:
		return
	folder = folder or row.mail_folder or INBOX
	seen = 1 if seen else 0
	flagged = 1 if flagged else 0
	if (row.mail_folder, int(row.mail_seen or 0), int(row.mail_flagged or 0)) == (folder, seen, flagged):
		return
	frappe.db.set_value(MSG, name, {"mail_folder": folder, "mail_seen": seen, "mail_flagged": flagged}, update_modified=False)
	_follow(row.thread, old=row.mail_folder, new=folder)
	if row.direction == "Inbound" and (seen != int(row.mail_seen or 0) or folder != row.mail_folder):
		recount_unread(row.thread)
	frappe.publish_realtime("excom:thread_updated", {"thread": row.thread, "event": "mail_state"}, after_commit=True)


def _in_folder(thread: str, folder: str) -> bool:
	return bool(frappe.db.exists(MSG, {"thread": thread, "mail_folder": folder}))


def _follow(thread: str, old: str | None, new: str) -> None:
	"""The conversation follows its emails (the table in the module docstring)."""
	if old == new:
		return
	status = frappe.db.get_value(THREAD, thread, "status")
	to = None
	if new == INBOX and status in ("Closed", "Spam"):
		to = "Open"
	elif old == INBOX and status in ("Open", "Pending") and not _in_folder(thread, INBOX):
		to = "Spam" if new == SPAM else "Closed"
	elif old == SPAM and status == "Spam" and not _in_folder(thread, SPAM):
		to = "Closed"
	if not to:
		return
	values = {"status": to}
	if to == "Open":
		values.update({"closure_outcome": None, "closure_reason": None, "closed_by": None, "closed_at": None})
	elif to == "Closed":
		values.update({"closed_at": now_datetime()})
	frappe.db.set_value(THREAD, thread, values, update_modified=False)


def recount_unread(thread: str) -> None:
	"""Unread = their emails not yet read, in Inbox or Archive (Spam and Trash do not nag)."""
	n = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabExcom Message` WHERE thread = %s AND direction = 'Inbound'
			AND channel = 'email' AND IFNULL(mail_seen, 0) = 0 AND mail_folder IN ('Inbox', 'Archive')""",
		thread,
	)[0][0]
	frappe.db.set_value(THREAD, thread, "unread_count", int(n or 0), update_modified=False)


# ── going out: what people do here, done in the mailbox ──────────────────────

# action -> (which emails, where they go)
_MOVES = {
	"archive": lambda m: ARCHIVE if m.mail_folder == INBOX else None,
	"inbox": lambda m: INBOX if m.mail_folder == ARCHIVE and m.direction == "Inbound" else None,
	"spam": lambda m: SPAM if m.mail_folder in (INBOX, ARCHIVE) and m.direction == "Inbound" else None,
	"not_spam": lambda m: INBOX if m.mail_folder == SPAM else None,
	"trash": lambda m: TRASH if m.mail_folder not in (TRASH, DRAFTS) else None,
	"restore": lambda m: (INBOX if m.direction == "Inbound" else SENT) if m.mail_folder == TRASH else None,
}


def _mail_rows(thread: str) -> list:
	return frappe.get_all(
		MSG,
		filters={"thread": thread, "channel": "email", "message_type": "Email", "provider_message_id": ["is", "set"]},
		fields=["name", "direction", "mail_folder", "mail_seen", "provider_message_id", "account"],
		order_by="provider_timestamp asc",
	)


def move_thread(thread: str, action: str) -> int:
	"""Do `action` (a key of _MOVES) to a conversation's emails, here now and in the mailbox after commit.
	Returns how many emails moved. Not an email conversation: nothing to do."""
	if frappe.db.get_value(THREAD, thread, "channel") != "email":
		return 0
	plan: dict[str, list] = {}
	for m in _mail_rows(thread):
		to = _MOVES[action](m)
		if to and to != m.mail_folder:
			frappe.db.set_value(MSG, m.name, "mail_folder", to, update_modified=False)
			plan.setdefault(to, []).append(m.provider_message_id)
	if plan:
		recount_unread(thread)
		_push(thread, "move", plan)
	return sum(len(v) for v in plan.values())


def set_thread_seen(thread: str, seen: bool) -> None:
	"""Opened (seen) or marked unread here: the same in the mailbox."""
	if frappe.db.get_value(THREAD, thread, "channel") != "email":
		return
	rows = [m for m in _mail_rows(thread) if m.direction == "Inbound"]
	if seen:
		ids = [m for m in rows if not m.mail_seen]
	else:
		ids = rows[-1:] if rows and all(m.mail_seen for m in rows) else []
	if not ids:
		return
	for m in ids:
		frappe.db.set_value(MSG, m.name, "mail_seen", 1 if seen else 0, update_modified=False)
	_push(thread, "seen" if seen else "unseen", {"": [m.provider_message_id for m in ids]})


def _push(thread: str, op: str, plan: dict) -> None:
	account = frappe.db.get_value(THREAD, thread, "account")
	if not account:
		return
	frappe.enqueue(
		"excom.excom.channels.email.mailbox.run_push",
		queue="short",
		enqueue_after_commit=True,
		account=account,
		op=op,
		plan=plan,
	)


def run_push(account: str, op: str, plan: dict) -> None:
	"""Background job: tell the mailbox. Emails synced before the mailbox moved servers are skipped."""
	acc = frappe.get_doc("Excom Channel Account", account)
	provider = P.get(acc)
	current = P.provider_name(acc)
	try:
		for folder, sids in plan.items():
			ids = [P.raw_id(acc, s) for s in sids if P.provider_of_stored(s) == current]
			if not ids:
				continue
			if op == "move":
				provider.move(acc, ids, folder)
			elif op in ("seen", "unseen"):
				provider.set_seen(acc, ids, op == "seen")
			elif op in ("flag", "unflag"):
				provider.set_flagged(acc, ids, op == "flag")
	except Exception:
		frappe.log_error(title=f"Excom mail: could not update {account}", message=frappe.get_traceback())


# ── folders in the inbox list ────────────────────────────────────────────────

MAIL_FOLDERS = ("inbox", "drafts", "scheduled", "sent", "archive", "spam", "trash")


def _has(alias: str, cond: str) -> str:
	return f"EXISTS (SELECT 1 FROM `tabExcom Message` fm WHERE fm.thread = {alias}.name AND {cond})"


def folder_where(folder: str, alias: str = "t") -> str | None:
	"""Conversations in a folder, as SQL; None for the inbox (the normal open-conversation rule)."""
	if folder in ("", "inbox"):
		return None
	if folder == "drafts":
		return f"EXISTS (SELECT 1 FROM `tabExcom Email Draft` fd WHERE fd.thread = {alias}.name)"
	if folder == "scheduled":
		return _has(alias, "fm.delivery_status = 'Scheduled'")
	if folder == "sent":
		return _has(alias, "fm.mail_folder = 'Sent'")
	if folder == "archive":
		# Archived conversations, less those that are wholly in Trash.
		return (
			f"{alias}.status = 'Closed' AND ({alias}.channel != 'email' OR "
			+ _has(alias, "fm.channel = 'email' AND IFNULL(fm.mail_folder, '') NOT IN ('Trash', 'Spam', 'Drafts')")
			+ ")"
		)
	if folder == "spam":
		return f"({alias}.status = 'Spam' OR " + _has(alias, "fm.mail_folder = 'Spam'") + ")"
	if folder == "trash":
		return _has(alias, "fm.mail_folder = 'Trash'")
	frappe.throw(f"Unknown folder {folder}")


def folder_counts(visibility: str) -> dict:
	"""Per mailbox: drafts, scheduled and unread spam, under the person's visibility rule."""
	vis = f" AND {visibility}" if visibility else ""
	out: dict = {}

	def add(key, sql):
		for acc, n in frappe.db.sql(sql):
			out.setdefault(acc, {})[key] = int(n or 0)

	add("drafts", f"SELECT t.account, COUNT(*) FROM `tabExcom Thread` t WHERE {folder_where('drafts')}{vis} GROUP BY t.account")
	add("scheduled", f"SELECT t.account, COUNT(*) FROM `tabExcom Thread` t WHERE {folder_where('scheduled')}{vis} GROUP BY t.account")
	add("spam", f"SELECT t.account, COUNT(*) FROM `tabExcom Thread` t WHERE t.channel = 'email' AND {_has('t', "fm.mail_folder = 'Spam' AND IFNULL(fm.mail_seen, 0) = 0")}{vis} GROUP BY t.account")
	return out


def set_message_flag(name: str, flagged: bool) -> None:
	"""Starred / flagged here: the same in the mailbox (Gmail STARRED, JMAP $flagged)."""
	row = frappe.db.get_value(MSG, name, ["thread", "provider_message_id", "mail_flagged"], as_dict=True)
	if not row or not row.provider_message_id or bool(row.mail_flagged) == bool(flagged):
		return
	frappe.db.set_value(MSG, name, "mail_flagged", 1 if flagged else 0, update_modified=False)
	_push(row.thread, "flag" if flagged else "unflag", {"": [row.provider_message_id]})
