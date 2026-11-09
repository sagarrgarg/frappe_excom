"""A JMAP mail server (Stalwart) for Excom, in the terms of providers/__init__.py.

JMAP (RFC 8620 core, RFC 8621 mail and submission) already works the way Excom thinks about mail,
so the mapping is short:

    Mailbox role inbox/archive/sent/drafts/junk/trash -> Inbox/Archive/Sent/Drafts/Spam/Trash
    a message in several mailboxes                    -> the first by PRECEDENCE (pick_folder)
    a message only in mailboxes without a role        -> Archive
    keyword $seen -> read, $flagged -> flagged, $draft -> listed in labels

Stalwart has no Archive mailbox out of the box, so the first check() (or any call that needs the
mailbox map) creates one with role "archive". The map of folder -> mailbox id is kept on the
account (jmap_mailboxes) and re-read when a message turns up in a mailbox we do not know.

Moving a message sets its mailboxIds to just the target mailbox. Read and flagged are keyword
patches. JMAP messages cannot be edited, so saving a draft creates a new one and destroys the old
one in the same request; the draft handle is simply the message id. Sending submits a draft with
EmailSubmission/set and, when the server accepts it, moves it from Drafts to Sent and drops
$draft. Mail with Bcc is sent with an explicit envelope and no Bcc header, so the server can never
show the Bcc list to the other recipients.

Sync keeps the Email state string on the account (jmap_email_state). Without one it reads recent
mail (from email_sync_from_date, else the last 7 days, at most INITIAL_SYNC_LIMIT messages); with
one it replays Email/changes. If the server has forgotten that state (cannotCalculateChanges) it
starts again from recent mail.

Ids. Every function here takes and returns the server's own (raw) ids: callers turn Excom's stored
ids back with providers.raw_id() before calling in. The exception is what goes to the sync sink
and the `id` of a meta dict: those are Excom's site-wide ids from providers.store_id(), because a
JMAP id is only unique inside one JMAP account. A meta also carries `raw_id`, and its `thread_id`
is the raw JMAP threadId.

Push (RFC 8620 §7.2). The HTTP endpoint lives elsewhere; this module gives it three helpers:
    push_token(account)                 the secret for the callback URL (made once, kept encrypted)
    ensure_push(account, callback_url)  make sure the server pushes to that URL; returns its id
    handle_push(account, payload)       answer a PushVerification; True when a StateChange means
                                        Excom should sync now

Errors. A wrong login raises JMAPAuthError (and marks the mailbox not authorized); a server that
cannot be reached or answers nonsense raises JMAPError; a JMAP method error raises JMAPMethodError
with the server's error type. The password is never logged or put in a message.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime, timedelta, timezone
from email.utils import format_datetime, getaddresses, make_msgid
from html import escape
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import cint
from frappe.utils.password import set_encrypted_password

from excom.excom.channels.email.providers import (
	ARCHIVE, DRAFTS, FOLDER_OF_ROLE, FOLDERS, INBOX, ROLE_OF, SENT, pick_folder, store_id,
)

DOCTYPE = "Excom Channel Account"

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"
USING = [CORE, MAIL, SUBMISSION]

TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60
SESSION_TTL = 600  # seconds the session object is cached
DEFAULT_MAX_OBJECTS = 500  # when the server does not say maxObjectsInGet / maxObjectsInSet
INITIAL_SYNC_LIMIT = 2000  # messages on a first sync; older mail stays on the server, reachable by search
PAGE_SIZE = 250
MAX_CHANGES = 500
MAX_BODY_BYTES = 5_000_000

# The extra headers Excom wants for the Other lane (services/inbox_lane.py), as JMAP properties.
HEADER_PROPERTIES = {
	"List-Unsubscribe": "header:List-Unsubscribe:asText",
	"List-Id": "header:List-Id:asText",
	"Precedence": "header:Precedence:asText",
	"Auto-Submitted": "header:Auto-Submitted:asText",
}

META_PROPERTIES = [
	"id", "threadId", "mailboxIds", "keywords", "messageId", "inReplyTo", "references",
	"from", "to", "cc", "bcc", "subject", "receivedAt", "sentAt", "preview",
	*HEADER_PROPERTIES.values(),
]

FULL_PROPERTIES = [
	"id", "threadId", "messageId", "subject", "from", "to", "cc", "bcc", "sentAt", "receivedAt",
	"inReplyTo", "references", "bodyStructure", "htmlBody", "textBody", "attachments", "bodyValues",
]

# Names for mailboxes Excom creates when the server has none with that role.
NEW_MAILBOX_NAME = {ARCHIVE: "Archive", SENT: "Sent", DRAFTS: "Drafts", "Spam": "Junk", "Trash": "Trash"}


class JMAPError(frappe.ValidationError):
	"""The mail server could not be reached, or said no."""


class JMAPAuthError(frappe.AuthenticationError):
	"""The mail server refused the login.

	A subclass with a non-401 status on purpose: Frappe treats a bare AuthenticationError (and any
	401) as Excom's own session ending, and would sign the person out of Excom.
	"""

	http_status_code = 417


class JMAPMethodError(JMAPError):
	"""A method in a JMAP request came back as ["error", {...}]."""

	def __init__(self, error_type: str, description: str = "", method: str = ""):
		self.type = error_type or "serverFail"
		self.description = description or ""
		self.method = method
		text = f"{method}: {self.type}" if method else self.type
		if self.description:
			text += f" ({self.description})"
		super().__init__(_("The mail server reported an error: {0}").format(text))


# ── the account ───────────────────────────────────────────────────────────────


def _save(account, values: dict) -> None:
	"""Keep the account object and its database row in step."""
	for k, v in values.items():
		setattr(account, k, v)
	_write_db(account.name, values)


def _write_db(name: str, values: dict) -> None:
	frappe.db.set_value(DOCTYPE, name, values, update_modified=False)


def _label(account) -> str:
	return account.get("email_address") or account.name


def _loads(text) -> dict:
	if isinstance(text, dict):
		return text
	try:
		return json.loads(text) if text else {}
	except ValueError:
		return {}


# ── HTTP ──────────────────────────────────────────────────────────────────────


def _http(account) -> requests.Session:
	"""A requests session signed in with HTTP Basic (Stalwart takes passwords and app passwords)."""
	s = requests.Session()
	user = account.get("jmap_username") or account.get("email_address") or ""
	s.auth = (user, account.get_password("jmap_password", raise_exception=False) or "")
	s.headers.update({"Accept": "application/json"})
	return s


def _http_request(account, method: str, url: str, *, timeout=TIMEOUT, **kwargs) -> requests.Response:
	try:
		with _http(account) as s:
			return s.request(method, url, timeout=timeout, **kwargs)
	except requests.RequestException as e:
		raise JMAPError(
			_("Could not reach the mail server for {0}: {1}").format(_label(account), str(e)[:300])
		) from None


def _auth_failed(account):
	"""Mark the mailbox as not signed in (kept even if the caller rolls back) and stop."""
	frappe.cache.delete_value(_cache_key(account))
	if cint(account.get("email_authorized")) != 0:
		_save(account, {"email_authorized": 0})
		frappe.db.commit()
	raise JMAPAuthError(
		_("The mail server refused the login for {0}. Check the login and password.").format(_label(account))
	)


def _json(account, resp) -> dict:
	try:
		return resp.json()
	except ValueError:
		raise JMAPError(
			_("The mail server for {0} sent an answer that is not JSON (HTTP {1}).").format(_label(account), resp.status_code)
		) from None


def _problem(resp) -> tuple[str, str]:
	"""(type, text) of a request-level error (RFC 7807 problem details, RFC 8620 §3.6.1)."""
	try:
		p = resp.json()
	except ValueError:
		return "", (resp.text or "")[:200]
	if not isinstance(p, dict):
		return "", str(p)[:200]
	return p.get("type") or "", p.get("detail") or p.get("title") or p.get("type") or ""


# ── session ───────────────────────────────────────────────────────────────────


def _cache_key(account) -> str:
	return f"excom:jmap_session:{account.name}"


def _server(account) -> str:
	server = (account.get("jmap_server_url") or "").strip().rstrip("/")
	if not server:
		raise JMAPError(_("Mailbox {0} has no JMAP server URL.").format(_label(account)))
	if "://" not in server:
		server = "https://" + server
	return server


def _session(account, refresh: bool = False) -> dict:
	"""The JMAP session (RFC 8620 §2), cached for a few minutes."""
	server = _server(account)
	sig = [server, account.get("jmap_username") or ""]
	key = _cache_key(account)
	if not refresh:
		cached = frappe.cache.get_value(key)
		if cached and cached.get("sig") == sig:
			return cached

	well_known = f"{server}/.well-known/jmap"
	url = account.get("jmap_session_url") if not refresh and account.get("jmap_session_url") else well_known
	resp = _http_request(account, "GET", url, allow_redirects=True)
	if resp.status_code != 200 and url != well_known and resp.status_code != 401:
		url = well_known
		resp = _http_request(account, "GET", url, allow_redirects=True)
	if resp.status_code == 401:
		_auth_failed(account)
	if resp.status_code != 200:
		raise JMAPError(
			_("The mail server at {0} did not give a JMAP session (HTTP {1}).").format(server, resp.status_code)
		)
	data = _json(account, resp)
	base = resp.url or url
	account_id = (data.get("primaryAccounts") or {}).get(MAIL)
	if not account_id or not data.get("apiUrl"):
		raise JMAPError(_("The mail server at {0} has no mail account for {1}.").format(server, _label(account)))

	caps = data.get("capabilities") or {}
	core = caps.get(CORE) or {}
	sess = {
		"sig": sig,
		"session_url": base,
		"apiUrl": urljoin(base, data["apiUrl"]),
		"downloadUrl": urljoin(base, data.get("downloadUrl") or ""),
		"uploadUrl": urljoin(base, data.get("uploadUrl") or ""),
		"eventSourceUrl": urljoin(base, data.get("eventSourceUrl") or ""),
		"accountId": account_id,
		"username": data.get("username") or "",
		"state": data.get("state") or "",
		"maxObjectsInGet": cint(core.get("maxObjectsInGet")) or DEFAULT_MAX_OBJECTS,
		"maxObjectsInSet": cint(core.get("maxObjectsInSet")) or DEFAULT_MAX_OBJECTS,
		"submission": SUBMISSION in caps,
	}
	frappe.cache.set_value(key, sess, expires_in_sec=SESSION_TTL)

	changed = {}
	if account.get("jmap_session_url") != base:
		changed["jmap_session_url"] = base
	if account.get("jmap_account_id") != account_id:
		changed["jmap_account_id"] = account_id
		if account.get("jmap_account_id"):
			# a different JMAP account: the old cursor and ids mean nothing here
			changed.update(jmap_email_state="", jmap_mailboxes="", jmap_identity_id="")
	if changed:
		_save(account, changed)
	return sess


# ── method calls ──────────────────────────────────────────────────────────────


def _with_account(call, account_id: str) -> list:
	name, args, call_id = call
	args = dict(args)
	# PushSubscription is per user, not per account (RFC 8620 §7.2)
	if not name.startswith("PushSubscription/"):
		args.setdefault("accountId", account_id)
	return [name, args, call_id]


def _call(account, calls: list) -> list:
	"""Send method calls ([name, args, call_id], ...) in one request; return the methodResponses.

	accountId is filled in for you. Back-references work as JMAP has them, e.g.
	{"#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"}}. A method-level error
	raises JMAPMethodError. A 401/404 from apiUrl, or a urn:ietf:params:jmap:error:* problem,
	re-reads the session once and tries again.
	"""
	sess = _session(account)
	names = {c[2]: c[0] for c in calls}
	data = None
	for attempt in (1, 2):
		body = {"using": USING, "methodCalls": [_with_account(c, sess["accountId"]) for c in calls]}
		resp = _http_request(account, "POST", sess["apiUrl"], json=body)
		if resp.status_code == 200:
			data = _json(account, resp)
			break
		ptype, text = _problem(resp)
		session_trouble = resp.status_code in (401, 404) or ptype.startswith("urn:ietf:params:jmap:error:")
		if attempt == 1 and session_trouble:
			sess = _session(account, refresh=True)
			continue
		if resp.status_code == 401:
			_auth_failed(account)
		raise JMAPError(
			_("The mail server for {0} refused the request (HTTP {1}): {2}").format(
				_label(account), resp.status_code, text or ptype
			)
		)

	if data.get("sessionState") and data["sessionState"] != sess.get("state"):
		frappe.cache.delete_value(_cache_key(account))  # the session changed; read it again next time

	responses = data.get("methodResponses") or []
	for name, args, call_id in responses:
		if name == "error":
			raise JMAPMethodError((args or {}).get("type"), (args or {}).get("description"), names.get(call_id, ""))
	return responses


def _pick(responses: list, call_id: str, name: str) -> dict:
	for n, args, cid in responses:
		if cid == call_id and n == name:
			return args or {}
	raise JMAPError(_("The mail server did not answer {0}.").format(name))


def _set_error(err: dict) -> str:
	err = err or {}
	text = err.get("description") or err.get("type") or "unknown"
	if err.get("description") and err.get("type"):
		text = f"{err['type']}: {err['description']}"
	if err.get("properties"):
		text += " (" + ", ".join(err["properties"]) + ")"
	return text


def _chunks(items: list, size: int):
	for k in range(0, len(items), max(1, size)):
		yield items[k:k + size]


def _clean_ids(ids) -> list:
	if isinstance(ids, str):
		ids = [ids]
	return [i for i in dict.fromkeys(ids or []) if i]


# ── mailboxes and identity ────────────────────────────────────────────────────


def _mailboxes(account, refresh: bool = False) -> dict:
	"""{"Inbox": id, "Archive": id, ..., "_names": {id: role or name}, "_account": accountId}."""
	sess = _session(account)
	if not refresh:
		data = _loads(account.get("jmap_mailboxes"))
		if data.get("_account") == sess["accountId"] and all(f in data for f in FOLDERS if f != INBOX):
			return data

	r = _call(account, [["Mailbox/get", {"ids": None, "properties": ["id", "name", "role", "parentId"]}, "0"]])
	boxes = _pick(r, "0", "Mailbox/get").get("list") or []
	data, names = {}, {}
	# top-level mailboxes win when two share a role
	for b in sorted(boxes, key=lambda b: (b.get("parentId") is not None, b.get("name") or "")):
		role = (b.get("role") or "").lower()
		names[b["id"]] = role or b.get("name") or b["id"]
		folder = FOLDER_OF_ROLE.get(role)
		if folder and folder not in data:
			data[folder] = b["id"]

	missing = [f for f in FOLDERS if f != INBOX and f not in data]
	for folder in list(missing):
		# a top-level mailbox with no role but the right name (say "Archive") is taken as it is
		want = {folder.lower(), NEW_MAILBOX_NAME[folder].lower()}
		same = next((b for b in boxes if not b.get("role") and not b.get("parentId") and (b.get("name") or "").lower() in want), None)
		if same:
			data[folder] = same["id"]
			missing.remove(folder)
	if missing:
		data.update(_create_mailboxes(account, missing, names))

	data["_names"] = names
	data["_account"] = sess["accountId"]
	_save(account, {"jmap_mailboxes": json.dumps(data, sort_keys=True)})
	return data


def _create_mailboxes(account, folders: list, names: dict) -> dict:
	"""Make the missing folders, with their role; without it if the server will not take the role."""
	made = {}
	todo = {f: {"name": NEW_MAILBOX_NAME[f], "parentId": None, "role": ROLE_OF[f]} for f in folders}
	for with_role in (True, False):
		if not todo:
			break
		create = {f: (v if with_role else {k: x for k, x in v.items() if k != "role"}) for f, v in todo.items()}
		r = _call(account, [["Mailbox/set", {"create": create}, "0"]])
		res = _pick(r, "0", "Mailbox/set")
		for f, obj in (res.get("created") or {}).items():
			made[f] = obj["id"]
			names[obj["id"]] = ROLE_OF[f] if with_role else NEW_MAILBOX_NAME[f]
			todo.pop(f, None)
		if not with_role:
			for f, err in (res.get("notCreated") or {}).items():
				frappe.log_error(title=f"Excom JMAP: could not create {f} for {account.name}", message=_set_error(err))
	return made


class _Boxes:
	"""The mailbox map for one run, re-read once if a message sits in a mailbox we do not know."""

	def __init__(self, account):
		self.account = account
		self.data = _mailboxes(account)
		self.refreshed = False

	def id_of(self, folder: str) -> str:
		if folder not in self.data and not self.refreshed:
			self.data, self.refreshed = _mailboxes(self.account, refresh=True), True
		if folder not in self.data:
			raise JMAPError(_("The mail server has no {0} folder for {1}.").format(folder, _label(self.account)))
		return self.data[folder]

	def place(self, mailbox_ids) -> tuple[str, list]:
		"""(folder, labels) for an Email's mailboxIds."""
		ids = [i for i, on in (mailbox_ids or {}).items() if on]
		names = self.data.get("_names") or {}
		if any(i not in names for i in ids) and not self.refreshed:
			self.data, self.refreshed = _mailboxes(self.account, refresh=True), True
			names = self.data.get("_names") or {}
		folder_of_id = {v: k for k, v in self.data.items() if k in FOLDERS}
		folder = pick_folder(folder_of_id[i] for i in ids if i in folder_of_id)
		return folder, sorted(names.get(i, i) for i in ids)


def _identity(account, refresh: bool = False) -> dict:
	"""The sending identity: the one with the mailbox's address, else the first."""
	if account.get("jmap_identity_id") and not refresh:
		return {"id": account.jmap_identity_id, "email": account.get("email_address") or ""}
	r = _call(account, [["Identity/get", {"ids": None}, "0"]])
	found = _pick(r, "0", "Identity/get").get("list") or []
	want = (account.get("email_address") or "").strip().lower()
	chosen = next((i for i in found if (i.get("email") or "").strip().lower() == want), found[0] if found else None)
	if not chosen and want:
		# some servers start with no identities; make one for the mailbox's own address
		r = _call(account, [["Identity/set", {"create": {"me": {"email": account.email_address, "name": ""}}}, "0"]])
		made = (_pick(r, "0", "Identity/set").get("created") or {}).get("me")
		if made:
			chosen = {"id": made["id"], "email": account.email_address}
	if not chosen:
		raise JMAPError(_("The mail server has no sending identity for {0}.").format(_label(account)))
	if account.get("jmap_identity_id") != chosen["id"]:
		_save(account, {"jmap_identity_id": chosen["id"]})
	return {"id": chosen["id"], "email": chosen.get("email") or account.get("email_address") or ""}


# ── Email <-> meta ────────────────────────────────────────────────────────────


def _addr(a: dict) -> str:
	name, email = (a.get("name") or "").strip(), a.get("email") or ""
	if not name:
		return email
	if re.search(r'[()<>\[\]:;@\\,."]', name):
		name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
	return f"{name} <{email}>"


def _addrs(items) -> str:
	return ", ".join(_addr(a) for a in (items or []) if a)


def _bracket(ids) -> str:
	return " ".join(f"<{i}>" for i in (ids or []) if i)


def _parse_date(value) -> datetime | None:
	if not value:
		return None
	try:
		dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None
	return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _utc(dt: datetime) -> str:
	return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_meta(account, email: dict, boxes: _Boxes) -> dict:
	"""A JMAP Email (META_PROPERTIES) as Excom's meta. `id` is the store id."""
	keywords = email.get("keywords") or {}
	folder, labels = boxes.place(email.get("mailboxIds"))
	if keywords.get("$draft"):
		labels.append("$draft")
	sent = _parse_date(email.get("sentAt")) or _parse_date(email.get("receivedAt"))
	received = _parse_date(email.get("receivedAt"))
	headers = {
		"From": _addrs(email.get("from")),
		"To": _addrs(email.get("to")),
		"Cc": _addrs(email.get("cc")),
		"Bcc": _addrs(email.get("bcc")),
		"Subject": email.get("subject") or "",
		"Date": format_datetime(sent) if sent else "",
		"Message-ID": _bracket((email.get("messageId") or [])[:1]),
		"In-Reply-To": _bracket(email.get("inReplyTo")),
		"References": _bracket(email.get("references")),
	}
	for header, prop in HEADER_PROPERTIES.items():
		headers[header] = (email.get(prop) or "").strip()
	return {
		"id": store_id(account, email["id"]),
		"raw_id": email["id"],
		"thread_id": email.get("threadId") or "",
		"folder": folder,
		"seen": bool(keywords.get("$seen")),
		"flagged": bool(keywords.get("$flagged")),
		"categories": [],
		"labels": labels,
		"snippet": (email.get("preview") or "")[:150],
		"received_at": int(received.timestamp()) if received else 0,
		"headers": {k: v for k, v in headers.items() if v},
	}


def _get_emails(account, ids: list, properties: list) -> list:
	"""Email/get in batches of maxObjectsInGet. Ids the server no longer has are left out."""
	out = []
	for chunk in _chunks(_clean_ids(ids), _session(account)["maxObjectsInGet"]):
		r = _call(account, [["Email/get", {"ids": chunk, "properties": properties}, "0"]])
		out += _pick(r, "0", "Email/get").get("list") or []
	return out


def get_meta(account, msg_id: str) -> dict | None:
	"""One message's meta, or None when the server no longer has it."""
	found = _get_emails(account, [msg_id], META_PROPERTIES)
	return to_meta(account, found[0], _Boxes(account)) if found else None


# ── the interface ─────────────────────────────────────────────────────────────


def check(account) -> dict:
	"""Sign in, (re)discover the session, the folders (making Archive if needed) and the identity."""
	try:
		_session(account, refresh=True)
		_mailboxes(account, refresh=True)
		identity = _identity(account, refresh=True)
	except JMAPError as e:
		return {"ok": False, "email": account.get("email_address") or "", "error": str(e)}
	except frappe.AuthenticationError as e:
		return {"ok": False, "email": account.get("email_address") or "", "error": str(e)}
	except Exception as e:
		frappe.log_error(title=f"Excom JMAP check failed: {account.name}")
		return {"ok": False, "email": account.get("email_address") or "", "error": str(e)}
	_save(account, {"email_authorized": 1})
	return {"ok": True, "email": identity.get("email") or account.get("email_address") or "", "error": ""}


def sync(account, sink) -> None:
	state = account.get("jmap_email_state")
	if state:
		try:
			_sync_changes(account, sink, state)
			return
		except JMAPMethodError as e:
			if e.type != "cannotCalculateChanges":
				raise
			_save(account, {"jmap_email_state": ""})  # the server forgot our place: start again
	_sync_recent(account, sink)


def _since(account) -> datetime:
	day = account.get("email_sync_from_date")
	if day:
		tz = ZoneInfo(frappe.utils.get_system_timezone() or "UTC")
		return datetime.combine(frappe.utils.getdate(day), dtime.min, tzinfo=tz)
	return datetime.now(timezone.utc) - timedelta(days=7)


def _bring(account, sink, meta: dict) -> None:
	"""Update the sink if it knows the message, else hand it over; one bad message does not stop the sync."""
	try:
		if sink.known(meta["id"]):
			sink.update(meta["id"], meta["folder"], meta["seen"], meta["flagged"])
		else:
			sink.ingest(meta)
	except Exception:
		frappe.log_error(title=f"Excom JMAP: could not take in {meta.get('raw_id')} for {account.name}")


def _sync_recent(account, sink) -> None:
	"""First sync: recent mail in every mailbox, newest first from the server, handed over oldest first."""
	boxes = _Boxes(account)
	page = min(PAGE_SIZE, _session(account)["maxObjectsInGet"])
	query = {"filter": {"after": _utc(_since(account))}, "sort": [{"property": "receivedAt", "isAscending": False}]}
	metas, state, position = [], None, 0
	while position < INITIAL_SYNC_LIMIT:
		limit = min(page, INITIAL_SYNC_LIMIT - position)
		r = _call(account, [
			["Email/query", {**query, "position": position, "limit": limit}, "q"],
			["Email/get", {"#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"}, "properties": META_PROPERTIES}, "g"],
		])
		ids = _pick(r, "q", "Email/query").get("ids") or []
		got = _pick(r, "g", "Email/get")
		if state is None:
			state = got.get("state")  # the earliest state: anything after it is replayed next time
		metas += [to_meta(account, e, boxes) for e in got.get("list") or []]
		if len(ids) < limit:
			break
		position += len(ids)
	for meta in reversed(metas):
		_bring(account, sink, meta)
	_save(account, {"jmap_email_state": state or ""})


def _sync_changes(account, sink, state: str) -> None:
	boxes = _Boxes(account)
	# created + updated never exceed maxChanges, so each Email/get fits in one call
	max_changes = min(MAX_CHANGES, _session(account)["maxObjectsInGet"])
	while True:
		r = _call(account, [
			["Email/changes", {"sinceState": state, "maxChanges": max_changes}, "c"],
			["Email/get", {"#ids": {"resultOf": "c", "name": "Email/changes", "path": "/created"}, "properties": META_PROPERTIES}, "gc"],
			["Email/get", {"#ids": {"resultOf": "c", "name": "Email/changes", "path": "/updated"}, "properties": META_PROPERTIES}, "gu"],
		])
		changes = _pick(r, "c", "Email/changes")
		for e in (_pick(r, "gc", "Email/get").get("list") or []) + (_pick(r, "gu", "Email/get").get("list") or []):
			_bring(account, sink, to_meta(account, e, boxes))
		for raw in changes.get("destroyed") or []:
			try:
				sink.remove(store_id(account, raw))
			except Exception:
				frappe.log_error(title=f"Excom JMAP: could not remove {raw} for {account.name}")
		new_state = changes.get("newState") or state
		_save(account, {"jmap_email_state": new_state})
		if not changes.get("hasMoreChanges") or new_state == state:
			break
		state = new_state


def fetch_full(account, msg_id: str) -> dict:
	"""The whole message for reading: headers, bodies and the list of attachments."""
	r = _call(account, [["Email/get", {
		"ids": [msg_id], "properties": FULL_PROPERTIES,
		"fetchHTMLBodyValues": True, "fetchTextBodyValues": True, "maxBodyValueBytes": MAX_BODY_BYTES,
	}, "0"]])
	found = _pick(r, "0", "Email/get").get("list") or []
	if not found:
		return {"id": msg_id, "deleted": True, "body_html": "", "body_text": "", "attachments": [], "headers": {}}
	e = found[0]
	values = e.get("bodyValues") or {}

	def value(part):
		return (values.get(part.get("partId")) or {}).get("value") or ""

	# htmlBody falls back to text/plain parts when a message has no HTML; Excom then shows body_text
	html_parts = e.get("htmlBody") or []
	body_html = ""
	if any(p.get("type") == "text/html" for p in html_parts):
		body_html = "".join(
			value(p) if p.get("type") == "text/html"
			else f'<div style="white-space:pre-wrap">{escape(value(p))}</div>' if p.get("type") == "text/plain"
			else ""
			for p in html_parts
		)
	body_text = "\n".join(
		value(p) if p.get("type") == "text/plain" else _plain_text(value(p))
		for p in e.get("textBody") or [] if (p.get("type") or "").startswith("text/")
	)

	attachments = []
	for p in e.get("attachments") or []:
		name = p.get("name")
		if not p.get("blobId") or (not name and p.get("disposition") != "attachment"):
			continue  # unnamed inline parts (images in the HTML and the like)
		attachments.append({
			"filename": name or "attachment",
			"mimeType": p.get("type") or "application/octet-stream",
			"size": p.get("size") or 0,
			"attachmentId": p["blobId"],
		})

	sent = _parse_date(e.get("sentAt")) or _parse_date(e.get("receivedAt"))
	headers = {
		"Subject": e.get("subject") or "",
		"From": _addrs(e.get("from")),
		"To": _addrs(e.get("to")),
		"Cc": _addrs(e.get("cc")),
		"Bcc": _addrs(e.get("bcc")),
		"Date": format_datetime(sent) if sent else "",
		"Message-ID": _bracket((e.get("messageId") or [])[:1]),
		"In-Reply-To": _bracket(e.get("inReplyTo")),
		"References": _bracket(e.get("references")),
	}
	return {
		"id": e["id"],
		"threadId": e.get("threadId") or "",
		"headers": {k: v for k, v in headers.items() if v},
		"body_html": body_html,
		"body_text": body_text,
		"attachments": attachments,
		"deleted": False,
	}


def _expand(template: str, **values) -> str:
	"""Fill an RFC 6570 level-1 template like downloadUrl."""
	for k, v in values.items():
		template = template.replace("{" + k + "}", quote(str(v), safe=""))
	return template


def fetch_attachment(account, msg_id: str, attachment_id: str, name: str = "", mime_type: str = "") -> bytes:
	"""The attachment's bytes. attachment_id is the blobId from fetch_full()."""
	sess = _session(account)
	if not sess.get("downloadUrl"):
		raise JMAPError(_("The mail server for {0} gives no download address.").format(_label(account)))
	url = _expand(
		sess["downloadUrl"], accountId=sess["accountId"], blobId=attachment_id,
		type=mime_type or "application/octet-stream", name=name or "attachment",
	)
	resp = _http_request(account, "GET", url, timeout=DOWNLOAD_TIMEOUT)
	if resp.status_code == 401:
		_auth_failed(account)
	if resp.status_code == 404:
		raise frappe.DoesNotExistError(_("That attachment is no longer on the mail server."))
	if resp.status_code != 200:
		raise JMAPError(_("The mail server would not give the attachment (HTTP {0}).").format(resp.status_code))
	return resp.content


# ── writing mail ──────────────────────────────────────────────────────────────


def _plain_text(html: str) -> str:
	if not html:
		return ""
	try:
		from frappe.core.utils import html_to_plain_text

		return html_to_plain_text(html)
	except Exception:
		return frappe.utils.strip_html_tags(html)


def _parse_addresses(value) -> list:
	"""'A <a@x>, b@y' (or a list of them) as JMAP EmailAddress objects."""
	if not value:
		return []
	parts = value if isinstance(value, (list, tuple)) else [value]
	out, seen = [], set()
	for name, email in getaddresses([str(p) for p in parts]):
		email = (email or "").strip()
		if "@" in email and email.lower() not in seen:
			seen.add(email.lower())
			out.append({"name": (name or "").strip() or None, "email": email})
	return out


def _message_ids(value) -> list:
	"""'<a@x> <b@y>' as ["a@x", "b@y"] (JMAP keeps message ids without angle brackets)."""
	if not value:
		return []
	text = " ".join(value) if isinstance(value, (list, tuple)) else str(value)
	return re.findall(r"<([^<>\s]+)>", text) or [t.strip("<>") for t in text.split() if t.strip("<>")]


def _from_email(account) -> str:
	return account.get("email_address") or _session(account).get("username") or ""


EXTRA_HEADERS = {"list-unsubscribe", "list-unsubscribe-post", "precedence", "auto-submitted"}


def _new_email(account, mail: dict, boxes: _Boxes, with_bcc: bool = True) -> dict:
	"""An Email/set create object for a draft in Drafts, from Excom's mail dict."""
	html = mail.get("body_html") or ""
	from_email = _from_email(account)
	domain = from_email.rsplit("@", 1)[-1] if "@" in from_email else "excom.local"
	in_reply_to = _message_ids(mail.get("in_reply_to"))
	references = _message_ids(mail.get("references"))
	references += [i for i in in_reply_to if i not in references]
	email = {
		"mailboxIds": {boxes.id_of(DRAFTS): True},
		"keywords": {"$draft": True, "$seen": True},
		"from": [{"name": mail.get("from_name") or None, "email": from_email}],
		"subject": mail.get("subject") or "",
		"messageId": [make_msgid(domain=domain)[1:-1]],  # ours, so the reply threading is known at once
		"sentAt": _utc(datetime.now(timezone.utc)),
		"htmlBody": [{"partId": "html", "type": "text/html"}],
		"textBody": [{"partId": "text", "type": "text/plain"}],
		"bodyValues": {"html": {"value": html}, "text": {"value": _plain_text(html)}},
	}
	for key in ("to", "cc", "bcc") if with_bcc else ("to", "cc"):
		addrs = _parse_addresses(mail.get(key))
		if addrs:
			email[key] = addrs
	if in_reply_to:
		email["inReplyTo"] = in_reply_to
	if references:
		email["references"] = references
	for name, value in (mail.get("headers") or {}).items():
		if name.lower() in EXTRA_HEADERS and value:
			email[f"header:{name}:asText"] = value  # RFC 8621 §4.1.3: a raw header set on create
	return email


def _created(res: dict, creation_id: str, what: str) -> dict:
	made = (res.get("created") or {}).get(creation_id)
	if not made:
		err = (res.get("notCreated") or {}).get(creation_id)
		raise JMAPError(_("The mail server would not take the {0}: {1}").format(what, _set_error(err)))
	return made


def _destroy(account, ids: list) -> None:
	"""Email/set destroy; ids already gone are fine."""
	for chunk in _chunks(_clean_ids(ids), _session(account)["maxObjectsInSet"]):
		r = _call(account, [["Email/set", {"destroy": chunk}, "0"]])
		failed = {i: e for i, e in (_pick(r, "0", "Email/set").get("notDestroyed") or {}).items() if (e or {}).get("type") != "notFound"}
		if failed:
			raise JMAPError(_("The mail server would not delete the message: {0}").format(_set_error(next(iter(failed.values())))))


def send(account, mail: dict) -> dict:
	"""Send a fresh message, or the draft mail["draft_id"]; it ends up in Sent without $draft."""
	boxes = _Boxes(account)
	identity = _identity(account)
	drafts, sent = boxes.id_of(DRAFTS), boxes.id_of(SENT)
	on_success = {"#sub": {f"mailboxIds/{drafts}": None, f"mailboxIds/{sent}": True, "keywords/$draft": None}}

	draft_id = mail.get("draft_id") or ""
	draft = None
	if draft_id:
		found = _get_emails(account, [draft_id], ["id", "threadId", "messageId", "bcc"])
		draft = found[0] if found else None
	bcc = _parse_addresses(mail.get("bcc")) or ((draft or {}).get("bcc") or [])

	if draft and not bcc:
		# submit the draft as it was saved
		r = _call(account, [["EmailSubmission/set", {
			"create": {"sub": {"identityId": identity["id"], "emailId": draft_id}},
			"onSuccessUpdateEmail": on_success,
		}, "s"]])
		_created(_pick(r, "s", "EmailSubmission/set"), "sub", _("message for sending"))
		_check_moved(account, r)
		return {
			"id": draft_id,
			"thread_id": draft.get("threadId") or "",
			"message_id_header": _bracket((draft.get("messageId") or [])[:1]),
		}

	# a fresh message; with Bcc, no Bcc header and an explicit envelope instead
	email = _new_email(account, mail, boxes, with_bcc=False)
	submission = {"identityId": identity["id"], "emailId": "#draft"}
	if bcc:
		rcpt = _parse_addresses([a["email"] for a in (email.get("to") or []) + (email.get("cc") or []) + bcc])
		submission["envelope"] = {
			"mailFrom": {"email": email["from"][0]["email"], "parameters": None},
			"rcptTo": [{"email": a["email"], "parameters": None} for a in rcpt],
		}
	r = _call(account, [
		["Email/set", {"create": {"draft": email}}, "e"],
		["EmailSubmission/set", {"create": {"sub": submission}, "onSuccessUpdateEmail": on_success}, "s"],
	])
	made = _created(_pick(r, "e", "Email/set"), "draft", _("message"))
	try:
		_created(_pick(r, "s", "EmailSubmission/set"), "sub", _("message for sending"))
	except JMAPError:
		try:
			_destroy(account, [made["id"]])  # do not leave a half-sent copy in Drafts
		except Exception:
			frappe.log_error(title=f"Excom JMAP: could not clear unsent draft for {account.name}")
		raise
	_check_moved(account, r)
	if draft_id:
		try:
			_destroy(account, [draft_id])  # the draft was sent as a fresh copy (it had Bcc)
		except Exception:
			frappe.log_error(title=f"Excom JMAP: could not delete sent draft {draft_id} for {account.name}")
	thread_id = made.get("threadId")
	if not thread_id:
		found = _get_emails(account, [made["id"]], ["id", "threadId"])
		thread_id = found[0].get("threadId") if found else ""
	return {"id": made["id"], "thread_id": thread_id or "", "message_id_header": _bracket(email["messageId"])}


def _check_moved(account, responses: list) -> None:
	"""The implicit Email/set of onSuccessUpdateEmail: the mail is sent either way, so only note a failure."""
	for name, args, call_id in responses:
		if name == "Email/set" and call_id == "s" and (args or {}).get("notUpdated"):
			frappe.log_error(
				title=f"Excom JMAP: sent mail not moved to Sent for {account.name}",
				message=json.dumps(args.get("notUpdated"))[:2000],
			)


def _update(account, ids, patch: dict) -> None:
	"""The same Email/set patch on each id, in batches of maxObjectsInSet; ids already gone are fine."""
	for chunk in _chunks(_clean_ids(ids), _session(account)["maxObjectsInSet"]):
		r = _call(account, [["Email/set", {"update": {i: patch for i in chunk}}, "0"]])
		failed = {i: e for i, e in (_pick(r, "0", "Email/set").get("notUpdated") or {}).items() if (e or {}).get("type") != "notFound"}
		if failed:
			raise JMAPError(
				_("The mail server would not change {0} message(s): {1}").format(len(failed), _set_error(next(iter(failed.values()))))
			)


def move(account, ids, folder: str) -> None:
	if folder not in FOLDERS:
		frappe.throw(_("Mail cannot be moved to {0}").format(folder))
	target = _Boxes(account).id_of(folder)
	_update(account, ids, {"mailboxIds": {target: True}})


def set_seen(account, ids, seen: bool) -> None:
	_update(account, ids, {"keywords/$seen": True if seen else None})


def set_flagged(account, ids, flagged: bool) -> None:
	_update(account, ids, {"keywords/$flagged": True if flagged else None})


def save_draft(account, mail: dict, draft_id: str = "") -> dict:
	"""A new draft in Drafts; the old one (if any) is destroyed in the same request."""
	args = {"create": {"draft": _new_email(account, mail, _Boxes(account))}}
	if draft_id:
		args["destroy"] = [draft_id]
	r = _call(account, [["Email/set", args, "0"]])
	res = _pick(r, "0", "Email/set")
	made = _created(res, "draft", _("draft"))
	err = (res.get("notDestroyed") or {}).get(draft_id)
	if err and err.get("type") != "notFound":
		frappe.log_error(title=f"Excom JMAP: old draft {draft_id} not deleted for {account.name}", message=_set_error(err))
	return {"draft_id": made["id"], "id": made["id"], "thread_id": made.get("threadId") or ""}


def delete_draft(account, draft_id: str) -> None:
	if draft_id:
		_destroy(account, [draft_id])


def draft_id_for(account, msg_id: str) -> str:
	"""In JMAP a draft is just the message, so its handle is its id."""
	return msg_id or ""


def search(account, query: str, limit: int = 20) -> list:
	"""The server's own full-text search, newest first."""
	limit = max(1, min(cint(limit) or 20, _session(account)["maxObjectsInGet"]))
	r = _call(account, [
		["Email/query", {"filter": {"text": query or ""}, "sort": [{"property": "receivedAt", "isAscending": False}], "position": 0, "limit": limit}, "q"],
		["Email/get", {"#ids": {"resultOf": "q", "name": "Email/query", "path": "/ids"}, "properties": META_PROPERTIES}, "g"],
	])
	boxes = _Boxes(account)
	return [to_meta(account, e, boxes) for e in _pick(r, "g", "Email/get").get("list") or []]


# ── push (RFC 8620 §7.2) ──────────────────────────────────────────────────────


def push_token(account) -> str:
	"""The secret the push callback URL carries; made once and kept encrypted on the account.

	The endpoint finds the account by this token and passes the request body to handle_push().
	"""
	token = account.get_password("jmap_push_token", raise_exception=False)
	if not token:
		token = frappe.generate_hash(length=32)
		set_encrypted_password(DOCTYPE, account.name, token, "jmap_push_token")
		account.jmap_push_token = token
	return token


def ensure_push(account, callback_url: str, force: bool = False) -> str:
	"""Have the server push Email and Mailbox changes to callback_url; returns the subscription id.

	Build callback_url with push_token(account) first. With jmap_push off, an existing subscription
	is removed and "" is returned. An existing subscription is kept (its expiry pushed out when it
	is close); force=True replaces it, e.g. when the site's address changed. The server answers a
	new subscription with a PushVerification to the URL, which handle_push() completes.
	"""
	push_id = account.get("jmap_push_id") or ""
	if not cint(account.get("jmap_push")) or force:
		if push_id:
			try:
				_call(account, [["PushSubscription/set", {"destroy": [push_id]}, "0"]])
			except JMAPError:
				pass
			_save(account, {"jmap_push_id": "", "jmap_push_verified": 0})
			push_id = ""
		if not cint(account.get("jmap_push")):
			return ""

	if push_id:
		r = _call(account, [["PushSubscription/get", {"ids": [push_id], "properties": ["id", "expires", "types"]}, "0"]])
		found = _pick(r, "0", "PushSubscription/get").get("list") or []
		if found:
			expires = _parse_date(found[0].get("expires"))
			if expires and expires < datetime.now(timezone.utc) + timedelta(days=1):
				later = _utc(datetime.now(timezone.utc) + timedelta(days=7))
				_call(account, [["PushSubscription/set", {"update": {push_id: {"expires": later}}}, "0"]])
			return push_id

	r = _call(account, [["PushSubscription/set", {"create": {"push": {
		"deviceClientId": f"excom-{account.name}",
		"url": callback_url,
		"types": ["Email", "Mailbox"],
	}}}, "0"]])
	made = _created(_pick(r, "0", "PushSubscription/set"), "push", _("push subscription"))
	_save(account, {"jmap_push_id": made["id"], "jmap_push_verified": 0})
	frappe.db.commit()  # the verification may reach the endpoint (another request) any moment
	return made["id"]


def handle_push(account, payload: dict) -> bool:
	"""Act on one push from the server. True when Excom should sync this mailbox now.

	PushVerification: send the code back to the server (completing ensure_push) and mark
	jmap_push_verified. StateChange: True if it names this mailbox's account with an Email state
	other than the one Excom already has (or any Mailbox change).
	"""
	payload = payload or {}
	kind = payload.get("@type")
	if kind == "PushVerification":
		sub_id, code = payload.get("pushSubscriptionId") or "", payload.get("verificationCode") or ""
		if not sub_id or not code:
			return False
		if account.get("jmap_push_id") and sub_id != account.jmap_push_id:
			return False  # a subscription that is not ours (any more)
		r = _call(account, [["PushSubscription/set", {"update": {sub_id: {"verificationCode": code}}}, "0"]])
		res = _pick(r, "0", "PushSubscription/set")
		if sub_id in (res.get("notUpdated") or {}):
			raise JMAPError(_("The mail server did not accept the push verification: {0}").format(_set_error(res["notUpdated"][sub_id])))
		_save(account, {"jmap_push_id": sub_id, "jmap_push_verified": 1})
		return False
	if kind == "StateChange":
		changed = payload.get("changed") or {}
		account_id = account.get("jmap_account_id")
		types = changed.get(account_id) if account_id else next(iter(changed.values()), None)
		if not types:
			return False
		if "Mailbox" in types:
			return True
		return bool(types.get("Email")) and types["Email"] != account.get("jmap_email_state")
	return False
