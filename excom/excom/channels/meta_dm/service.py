"""
Instagram DMs + Facebook Messenger through the Graph API (P3 follow-up, 2026-09-03).

Polling is the source of truth: every minute each active instagram/messenger account lists the
conversations updated since its last poll (with overlap) and ingests messages it has not seen
(idempotent by provider_message_id). The Meta webhook (`entry[].messaging[]`) is only an accelerator
and goes through the same ingest.

Outbound: POST /{page_id}/messages (Facebook Login flow serves both platforms). Replies are allowed
inside the 24-hour customer-service window; with the HUMAN_AGENT tag (needs app permission) up to 7 days.
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime, time_diff_in_seconds

from excom.excom.utils.site_time import epoch_to_site_time
from excom.excom.utils.graph import graph_version

GRAPH = "https://graph.facebook.com"
PLATFORMS = {"instagram": "instagram", "messenger": "messenger"}
WINDOW_HOURS = 24
HUMAN_AGENT_DAYS = 7
OVERLAP_MINUTES = 10

# Graph refuses a request whose response would be too large ("Please reduce the amount of data
# you're asking for", code 1). 50 conversations x 25 messages with attachments hit that on busy
# Instagram accounts, so pages are small and attachments are fetched only for new messages.
CONVERSATIONS_PER_PAGE = 10
MESSAGES_PER_PAGE = 10
MAX_CONVERSATION_PAGES = 100  # the old 20 pages x 50 conversations, at 10 per page
MAX_MESSAGE_PAGES = 5  # per conversation, per poll
STOP_SLACK_HOURS = 6  # conversations older than the window by this much end the poll
POLL_ERROR_LOG_SECONDS = 3600  # one failure log per account per hour
MESSAGE_FIELDS = "id,created_time,from,to,message"


class GraphError(frappe.ValidationError):
	"""A non-200 Graph reply, with Meta's error code when the body carries one."""

	def __init__(self, status: int, body: str):
		self.status = status
		self.body = body or ""
		try:
			err = (json.loads(self.body) or {}).get("error") or {}
		except Exception:
			err = {}
		self.code = err.get("code")
		self.graph_message = err.get("message") or ""
		super().__init__(f"Graph {status}: {self.body[:300]}")

	@property
	def too_much_data(self) -> bool:
		return self.code == 1 and "reduce the amount of data" in self.graph_message.lower()


# ─── credentials ──────────────────────────────────────────────────────────────

def _creds(account) -> dict:
	token = account.get_password("meta_page_token", raise_exception=False) or ""
	if not token or not account.meta_page_id:
		frappe.throw(_("Meta page id / page token missing on account {0}").format(account.name))
	return {"token": token, "page_id": account.meta_page_id, "ig_id": account.meta_ig_user_id or "", "version": graph_version(account.meta_api_version)}


def _get(url: str, params: dict) -> dict:
	resp = requests.get(url, params=params, timeout=30)
	if resp.status_code != 200:
		raise GraphError(resp.status_code, resp.text)
	return resp.json()


def _get_shrinking(url: str, params: dict, sizes: dict, keys: tuple) -> dict:
	"""GET a page; if Graph says the reply is too large, retry the same page once with the page
	sizes in `keys` halved. The smaller sizes stay in `sizes` for the rest of the poll."""
	try:
		return _get(url, params(sizes))
	except GraphError as e:
		if not e.too_much_data or all(sizes[k] <= 1 for k in keys):
			raise
	for k in keys:
		sizes[k] = max(1, sizes[k] // 2)
	return _get(url, params(sizes))


def _post(url: str, params: dict, payload: dict) -> dict:
	resp = requests.post(url, params=params, json=payload, timeout=30)
	data = resp.json() if resp.content else {}
	if resp.status_code != 200:
		raise frappe.ValidationError(f"Graph {resp.status_code}: {json.dumps(data)[:300]}")
	return data


# ─── polling (authoritative) ──────────────────────────────────────────────────

def poll_all() -> None:
	"""Scheduler `all` (every minute): poll every active instagram/messenger account whose interval elapsed."""
	for a in frappe.get_all("Excom Channel Account", filters={"channel": ["in", list(PLATFORMS)], "status": "Active"}, fields=["name", "meta_poll_interval_minutes", "meta_last_polled_at"]):
		mins = max(1, int(a.meta_poll_interval_minutes or 1))
		if a.meta_last_polled_at and time_diff_in_seconds(now_datetime(), a.meta_last_polled_at) < mins * 60 - 5:
			continue
		try:
			poll_account(a.name)
		except Exception:
			_log_poll_failure(a.name)


def _log_poll_failure(account_name: str) -> None:
	"""A failing account is retried every minute; log it once an hour, not 60 times."""
	key = f"excom:meta_dm_poll_failed:{account_name}"
	if frappe.cache.get_value(key):
		return
	frappe.cache.set_value(key, 1, expires_in_sec=POLL_ERROR_LOG_SECONDS)
	frappe.log_error(title=f"Excom Meta DM poll failed: {account_name}", message=frappe.get_traceback())


def _graph_time(value: str):
	return get_datetime(value.replace("T", " ")[:19])


def poll_account(account_name: str) -> dict:
	account = frappe.get_doc("Excom Channel Account", account_name)
	c = _creds(account)
	since = add_to_date(get_datetime(account.meta_last_polled_at), minutes=-OVERLAP_MINUTES) if account.meta_last_polled_at else add_to_date(now_datetime(), days=-1)
	cutoff = since.replace(tzinfo=None) - frappe.utils.datetime.timedelta(hours=STOP_SLACK_HOURS)
	url = f"{GRAPH}/{c['version']}/{c['page_id']}/conversations"
	sizes = {"conversations": CONVERSATIONS_PER_PAGE, "messages": MESSAGES_PER_PAGE}
	after = None

	def params(sz):
		p = {
			"access_token": c["token"],
			"platform": PLATFORMS[account.channel],
			"fields": f"id,updated_time,participants,messages.limit({sz['messages']}){{{MESSAGE_FIELDS}}}",
			"limit": sz["conversations"],
		}
		if after:
			p["after"] = after  # page by cursor, so a smaller retried page never skips conversations
		return p

	pages = n = 0
	started = now_datetime()
	while pages < MAX_CONVERSATION_PAGES:
		data = _get_shrinking(url, params, sizes, ("conversations", "messages"))
		pages += 1
		stop = False
		for conv in data.get("data") or []:
			if conv.get("updated_time") and _graph_time(conv["updated_time"]) < cutoff:
				stop = True  # conversations come newest-first; older than the window → done
				break
			_fill_messages(c, conv, sizes, cutoff, _own_ids(account))
			n += ingest_conversation(account, conv)
		paging = data.get("paging") or {}
		after = (paging.get("cursors") or {}).get("after")
		if stop or not paging.get("next") or not after:
			break
	frappe.db.set_value("Excom Channel Account", account.name, "meta_last_polled_at", started, update_modified=False)
	frappe.db.commit()
	return {"account": account.name, "ingested": n, "pages": pages}


def _fill_messages(c: dict, conv: dict, sizes: dict, cutoff, own: set) -> None:
	"""Complete a conversation's messages in place: page further back while the oldest message
	is still inside the window, and fetch attachments for customer messages not stored yet."""
	box = conv.setdefault("messages", {})
	msgs = list(box.get("data") or [])
	paging = box.get("paging") or {}
	extra_pages = 0
	while (
		conv.get("id")
		and msgs
		and paging.get("next")
		and (paging.get("cursors") or {}).get("after")
		and msgs[-1].get("created_time")
		and _graph_time(msgs[-1]["created_time"]) >= cutoff
		and extra_pages < MAX_MESSAGE_PAGES
	):
		cursor = paging["cursors"]["after"]
		page = _get_shrinking(
			f"{GRAPH}/{c['version']}/{conv['id']}/messages",
			lambda sz: {"access_token": c["token"], "fields": MESSAGE_FIELDS, "limit": sz["messages"], "after": cursor},
			sizes,
			("messages",),
		)
		msgs.extend(page.get("data") or [])
		paging = page.get("paging") or {}
		extra_pages += 1

	ids = [m["id"] for m in msgs if m.get("id")]
	stored = set(frappe.get_all("Excom Message", {"provider_message_id": ["in", ids]}, pluck="provider_message_id")) if ids else set()
	for m in msgs:
		if (
			m.get("id")
			and m["id"] not in stored
			and "attachments" not in m
			and str((m.get("from") or {}).get("id")) not in own  # our own messages are never ingested
		):
			att = _get(f"{GRAPH}/{c['version']}/{m['id']}", {"access_token": c["token"], "fields": "attachments"})
			if att.get("attachments"):
				m["attachments"] = att["attachments"]
	box["data"] = msgs


def _own_ids(account) -> set[str]:
	return {str(account.meta_page_id or ""), str(account.meta_ig_user_id or "")} - {""}


def ingest_conversation(account, conv: dict) -> int:
	"""Pure transform + ingest for one Graph conversation object (testable without HTTP)."""
	from excom.excom.services.thread_service import ingest_inbound_message
	own = _own_ids(account)
	n = 0
	for m in reversed((conv.get("messages") or {}).get("data") or []):
		frm = m.get("from") or {}
		if str(frm.get("id")) in own:
			continue  # our own reply (already recorded on send, or sent from Meta's inbox — recorded as outbound below)
		text = m.get("message") or ""
		media = ""
		mtype = "Text"
		atts = ((m.get("attachments") or {}).get("data") or [])
		if atts:
			a = atts[0]
			media = ((a.get("image_data") or {}).get("url") or (a.get("video_data") or {}).get("url") or (a.get("file_url")) or "")
			mtype = "Image" if a.get("image_data") else "Video" if a.get("video_data") else "Document"
			if not text:
				text = a.get("name") or f"[{mtype}]"
		ts = m.get("created_time")
		provider_ts = ""
		if ts:
			try:
				provider_ts = epoch_to_site_time(get_datetime(ts.replace("T", " ")[:19]).replace(tzinfo=None).timestamp())
			except Exception:
				provider_ts = ""
		created = ingest_inbound_message(
			phone="",
			channel=account.channel,
			account=account.name,
			provider_message_id=m.get("id") or "",
			content_text=text,
			message_type=mtype,
			display_name=frm.get("name") or frm.get("username") or "",
			content_json={"conversation_id": conv.get("id"), "from": frm, "platform": account.channel},
			media_file=media,
			provider_timestamp=provider_ts,
			channel_user_id=str(frm.get("id") or ""),
		)
		if created:
			n += 1
	return n


# ─── webhook accelerator ──────────────────────────────────────────────────────

def handle_messaging(entry: dict, obj: str) -> int:
	"""`entry.messaging[]` from object 'page' (Messenger) or 'instagram'. Same ingest as polling."""
	channel = "instagram" if obj == "instagram" else "messenger"
	page_id = str(entry.get("id") or "")
	acc = frappe.db.get_value("Excom Channel Account", {"channel": channel, "status": "Active", "meta_page_id": page_id}, "name") or frappe.db.get_value("Excom Channel Account", {"channel": channel, "status": "Active", "meta_ig_user_id": page_id}, "name")
	if not acc:
		return 0
	account = frappe.get_doc("Excom Channel Account", acc)
	n = 0
	for ev in entry.get("messaging") or []:
		msg = ev.get("message") or {}
		if not msg or msg.get("is_echo"):
			continue
		sender = str((ev.get("sender") or {}).get("id") or "")
		if sender in _own_ids(account):
			continue
		conv = {"id": None, "messages": {"data": [{"id": msg.get("mid"), "created_time": None, "from": {"id": sender}, "message": msg.get("text") or "", "attachments": {"data": [{"file_url": a.get("payload", {}).get("url"), "image_data": {"url": a["payload"]["url"]} if a.get("type") == "image" else None} for a in msg.get("attachments") or [] if a.get("payload")]}}]}}
		n += ingest_conversation(account, conv)
	return n


# ─── outbound ─────────────────────────────────────────────────────────────────

def recipient_id(identity, channel: str) -> str:
	for ch in identity.get("channels") or []:
		if ch.channel_type == channel and ch.channel_user_id:
			return ch.channel_user_id
	frappe.throw(_("No {0} id on this contact — they must message you first").format(channel))


def window_status(thread) -> dict:
	last_inbound = thread.last_inbound_at
	if not last_inbound:
		return {"window_open": False, "last_inbound_at": None, "hours_remaining": 0, "human_agent_ok": False}
	diff = time_diff_in_seconds(now_datetime(), last_inbound)
	remaining = max(0, WINDOW_HOURS * 3600 - diff)
	account = frappe.db.get_value("Excom Channel Account", thread.account, ["meta_human_agent_tag"], as_dict=True) or frappe._dict()
	return {"window_open": diff < WINDOW_HOURS * 3600, "last_inbound_at": str(last_inbound), "hours_remaining": round(remaining / 3600, 1), "human_agent_ok": bool(account.meta_human_agent_tag) and diff < HUMAN_AGENT_DAYS * 86400}


def send_text(account, to_id: str, text: str, thread=None) -> dict:
	c = _creds(account)
	payload: dict = {"recipient": {"id": to_id}, "message": {"text": text[:1000]}, "messaging_type": "RESPONSE"}
	if thread is not None:
		w = window_status(thread)
		if not w["window_open"]:
			if w["human_agent_ok"]:
				payload["messaging_type"] = "MESSAGE_TAG"
				payload["tag"] = "HUMAN_AGENT"
			else:
				frappe.throw(_("The 24-hour reply window is closed. The customer must message first."))
	data = _post(f"{GRAPH}/{c['version']}/{c['page_id']}/messages", {"access_token": c["token"]}, payload)
	return {"provider_message_id": data.get("message_id") or "", "raw": data}


def send_media(account, to_id: str, media_type: str, file_url: str, caption: str = "", thread=None) -> dict:
	c = _creds(account)
	kind = {"Image": "image", "Video": "video", "Audio": "audio"}.get(media_type, "file")
	payload = {"recipient": {"id": to_id}, "message": {"attachment": {"type": kind, "payload": {"url": file_url, "is_reusable": True}}}, "messaging_type": "RESPONSE"}
	if thread is not None and not window_status(thread)["window_open"]:
		frappe.throw(_("The 24-hour reply window is closed. The customer must message first."))
	data = _post(f"{GRAPH}/{c['version']}/{c['page_id']}/messages", {"access_token": c["token"]}, payload)
	out = {"provider_message_id": data.get("message_id") or "", "raw": data}
	if caption:
		send_text(account, to_id, caption)
	return out
