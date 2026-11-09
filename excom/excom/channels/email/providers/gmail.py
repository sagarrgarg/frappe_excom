"""Gmail as a mail server for Excom, in the JMAP terms of providers/__init__.py.

Gmail has labels, not folders. Translated:
    TRASH -> Trash, SPAM -> Spam, DRAFT -> Drafts, INBOX -> Inbox, SENT -> Sent, none of them -> Archive
    UNREAD absent -> read, STARRED -> flagged, CATEGORY_* -> categories (for the Other lane)

Moving is label editing: to Archive takes INBOX (and SPAM, TRASH) off, to Spam puts SPAM on and
INBOX off, and so on. SENT and DRAFT are Gmail's own and never edited; a sent message taken out of
Trash is back in Sent because its SENT label never left.

Sync reads Gmail's history: new messages, deletions and every label change, so mail archived,
read, starred or binned in Gmail shows up the same in Excom. Tokens and the older calls live in
services/gmail_service.py.
"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import frappe
import requests
from frappe import _
from frappe.utils import add_days, now_datetime

from excom.excom.channels.email.providers import (
	ARCHIVE, DRAFTS, INBOX, SENT, SPAM, TRASH, pick_folder, store_id,
)
from excom.excom.services import gmail_service as gs

API = gs.GMAIL_API_BASE
DOCTYPE = "Excom Channel Account"

LABEL_FOLDER = {"TRASH": TRASH, "SPAM": SPAM, "DRAFT": DRAFTS, "INBOX": INBOX, "SENT": SENT}

# Labels to put on and take off for each move (SENT/DRAFT are Gmail's and stay as they are).
MOVES = {
	INBOX: (["INBOX"], ["SPAM", "TRASH"]),
	ARCHIVE: ([], ["INBOX", "SPAM", "TRASH"]),
	SENT: ([], ["INBOX", "SPAM", "TRASH"]),
	SPAM: (["SPAM"], ["INBOX", "TRASH"]),
	TRASH: (["TRASH"], ["INBOX", "SPAM"]),
}

METADATA_HEADERS = [
	"From", "To", "Cc", "Bcc", "Subject", "Date", "Message-ID", "In-Reply-To", "References",
	# bulk / automated mail markers, for the Other lane (services/inbox_lane.py)
	"List-Unsubscribe", "List-Id", "Precedence", "Auto-Submitted",
]

INITIAL_SYNC_LIMIT = 500  # messages on a first sync; older mail stays in Gmail, reachable by search


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _call(account, method: str, path: str, *, params=None, json=None, timeout=30, ok_404=False):
	"""One Gmail API call, with one token refresh on 401."""
	url = f"{API}/{path}"
	resp = requests.request(method, url, headers=gs._headers(account.name), params=params, json=json, timeout=timeout)
	if resp.status_code == 401:
		gs._force_token_refresh(account.name)
		resp = requests.request(method, url, headers=gs._headers(account.name), params=params, json=json, timeout=timeout)
	if resp.status_code == 404 and ok_404:
		return None
	resp.raise_for_status()
	return resp.json() if resp.content else {}


# ── labels <-> folders ────────────────────────────────────────────────────────


def folder_of(labels) -> str:
	return pick_folder(LABEL_FOLDER[l] for l in (labels or ()) if l in LABEL_FOLDER)


def state_of(labels) -> tuple[str, bool, bool]:
	labels = labels or []
	return folder_of(labels), "UNREAD" not in labels, "STARRED" in labels


def to_meta(raw: dict) -> dict:
	"""A Gmail message (format=metadata) as Excom's meta."""
	headers = {h["name"]: h["value"] for h in (raw.get("payload") or {}).get("headers", [])} if "payload" in raw else dict(raw.get("headers") or {})
	labels = raw.get("labelIds") or []
	folder, seen, flagged = state_of(labels)
	try:
		received = int(raw.get("internalDate") or 0) / 1000
	except (TypeError, ValueError):
		received = 0
	return {
		"id": raw["id"],
		"thread_id": raw.get("threadId", ""),
		"folder": folder,
		"seen": seen,
		"flagged": flagged,
		"categories": [l for l in labels if l.startswith("CATEGORY_")],
		"labels": labels,
		"snippet": (raw.get("snippet") or "")[:150],
		"received_at": received,
		"headers": headers,
	}


def get_meta(account, msg_id: str) -> dict | None:
	raw = _call(account, "GET", f"messages/{msg_id}", params={"format": "metadata", "metadataHeaders": METADATA_HEADERS}, timeout=15, ok_404=True)
	return to_meta(raw) if raw else None


# ── the interface ─────────────────────────────────────────────────────────────


def check(account) -> dict:
	try:
		p = _call(account, "GET", "profile", timeout=15)
		return {"ok": True, "email": p.get("emailAddress", ""), "error": "", "total": p.get("messagesTotal", 0)}
	except Exception as e:
		return {"ok": False, "email": "", "error": str(e)}


def sync(account, sink) -> None:
	if account.get("email_last_history_id"):
		if _sync_changes(account, sink):
			return
	_sync_recent(account, sink)


def _set_cursor(account, history_id) -> None:
	account.email_last_history_id = str(history_id or "")
	frappe.db.set_value(DOCTYPE, account.name, "email_last_history_id", account.email_last_history_id, update_modified=False)
	frappe.db.commit()


def _sync_recent(account, sink) -> None:
	"""First sync (or Gmail forgot our place): recent mail in every folder, Spam and Trash included."""
	start = _call(account, "GET", "profile", timeout=15).get("historyId")  # before listing, so nothing slips between
	since = account.get("email_sync_from_date") or add_days(now_datetime(), -7)
	q = f"after:{frappe.utils.getdate(since).strftime('%Y/%m/%d')}"
	ids, token = [], ""
	while len(ids) < INITIAL_SYNC_LIMIT:
		page = _call(account, "GET", "messages", params={"q": q, "includeSpamTrash": "true", "maxResults": 100, **({"pageToken": token} if token else {})})
		ids += [m["id"] for m in page.get("messages", [])]
		token = page.get("nextPageToken")
		if not token:
			break
	for mid in reversed(ids[:INITIAL_SYNC_LIMIT]):  # oldest first, so threads read in order
		_bring(account, sink, mid)
	_set_cursor(account, start)


def _sync_changes(account, sink) -> bool:
	"""Everything since the last history id. False when Gmail no longer has that far back."""
	latest: dict[str, list] = {}  # message id -> its labels after the last change we saw
	deleted: set[str] = set()
	added: set[str] = set()
	token, history_id = "", account.email_last_history_id
	while True:
		params = {"startHistoryId": account.email_last_history_id, "maxResults": 500,
			"historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]}
		if token:
			params["pageToken"] = token
		page = _call(account, "GET", "history", params=params, ok_404=True)
		if page is None:
			_set_cursor(account, "")
			return False
		for h in page.get("history", []):
			for kind in ("messagesAdded", "labelsAdded", "labelsRemoved"):
				for item in h.get(kind, []):
					m = item.get("message") or {}
					if m.get("id"):
						latest[m["id"]] = m.get("labelIds") or []
						deleted.discard(m["id"])
						if kind == "messagesAdded":
							added.add(m["id"])
			for item in h.get("messagesDeleted", []):
				mid = (item.get("message") or {}).get("id")
				if mid:
					deleted.add(mid)
					latest.pop(mid, None)
		history_id = page.get("historyId", history_id)
		token = page.get("nextPageToken")
		if not token:
			break
	for mid in deleted:
		sink.remove(store_id(account, mid))
	for mid, labels in latest.items():
		if sink.known(store_id(account, mid)):
			sink.update(store_id(account, mid), *state_of(labels))
		else:
			_bring(account, sink, mid)
	_set_cursor(account, history_id)
	return True


def _bring(account, sink, mid: str) -> None:
	sid = store_id(account, mid)
	try:
		meta = get_meta(account, mid)
		if not meta:
			return
		if sink.known(sid):
			sink.update(sid, meta["folder"], meta["seen"], meta["flagged"])
		else:
			sink.ingest(meta)
	except Exception:
		frappe.log_error(title=f"Email fetch failed: {mid}")


def fetch_full(account, msg_id: str) -> dict:
	return gs.get_message_full(account.name, msg_id)


def fetch_attachment(account, msg_id: str, attachment_id: str) -> bytes:
	return gs.get_attachment(account.name, msg_id, attachment_id)


# Headers Excom may add to a message (cold mail's unsubscribe headers, RFC 2369 / 8058).
EXTRA_HEADERS = {"list-unsubscribe", "list-unsubscribe-post", "precedence", "auto-submitted"}


def _raw(account, mail: dict) -> str:
	msg = MIMEMultipart("alternative")
	msg["From"] = formataddr((mail.get("from_name") or "", account.email_address)) if mail.get("from_name") else account.email_address
	for h, k in (("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")):
		if mail.get(k):
			msg[h] = mail[k]
	msg["Subject"] = mail.get("subject") or ""
	if mail.get("in_reply_to"):
		msg["In-Reply-To"] = mail["in_reply_to"]
		msg["References"] = mail.get("references") or mail["in_reply_to"]
	for name, value in (mail.get("headers") or {}).items():
		if name.lower() in EXTRA_HEADERS and value:
			msg[name] = value
	html = mail.get("body_html") or ""
	msg.attach(MIMEText(frappe.utils.strip_html_tags(html), "plain", "utf-8"))
	msg.attach(MIMEText(html, "html", "utf-8"))
	return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _message_body(account, mail: dict) -> dict:
	body = {"raw": _raw(account, mail)}
	if mail.get("thread_id"):
		body["threadId"] = mail["thread_id"]
	return body


def send(account, mail: dict) -> dict:
	if mail.get("draft_id"):
		sent = _call(account, "POST", "drafts/send", json={"id": mail["draft_id"]})
	else:
		sent = _call(account, "POST", "messages/send", json=_message_body(account, mail))
	meta = None
	try:
		meta = get_meta(account, sent["id"])  # Gmail writes the Message-ID; read it back for threading
	except Exception:
		pass
	return {
		"id": sent.get("id", ""),
		"thread_id": sent.get("threadId", ""),
		"message_id_header": ((meta or {}).get("headers") or {}).get("Message-ID", ""),
		"labels": sent.get("labelIds", []),
	}


def _modify(account, ids, add, remove) -> None:
	ids = [i for i in ids if i]
	for k in range(0, len(ids), 1000):
		_call(account, "POST", "messages/batchModify", json={"ids": ids[k:k + 1000], "addLabelIds": add, "removeLabelIds": remove})


def move(account, ids, folder: str) -> None:
	if folder not in MOVES:
		frappe.throw(_("Mail cannot be moved to {0}").format(folder))
	add, remove = MOVES[folder]
	_modify(account, ids, add, remove)


def set_seen(account, ids, seen: bool) -> None:
	_modify(account, ids, [] if seen else ["UNREAD"], ["UNREAD"] if seen else [])


def set_flagged(account, ids, flagged: bool) -> None:
	_modify(account, ids, ["STARRED"] if flagged else [], [] if flagged else ["STARRED"])


def save_draft(account, mail: dict, draft_id: str = "") -> dict:
	body = {"message": _message_body(account, mail)}
	d = _call(account, "PUT", f"drafts/{draft_id}", json=body, ok_404=True) if draft_id else None
	if d is None:  # new, or deleted in Gmail meanwhile
		d = _call(account, "POST", "drafts", json=body)
	m = d.get("message") or {}
	return {"draft_id": d.get("id", ""), "id": m.get("id", ""), "thread_id": m.get("threadId", "")}


def delete_draft(account, draft_id: str) -> None:
	if draft_id:
		_call(account, "DELETE", f"drafts/{draft_id}", ok_404=True)


def draft_id_for(account, msg_id: str) -> str:
	"""Gmail keeps drafts apart from their message: find the draft that holds this message."""
	token = ""
	for _page in range(20):
		page = _call(account, "GET", "drafts", params={"maxResults": 500, **({"pageToken": token} if token else {})})
		for d in page.get("drafts", []):
			if (d.get("message") or {}).get("id") == msg_id:
				return d["id"]
		token = page.get("nextPageToken")
		if not token:
			break
	return ""


def search(account, query: str, limit: int = 20) -> list:
	page = _call(account, "GET", "messages", params={"q": query, "maxResults": min(int(limit), 50)})
	out = []
	for m in page.get("messages", [])[:limit]:
		try:
			meta = get_meta(account, m["id"])
			if meta:
				out.append(meta)
		except Exception:
			continue
	return out
