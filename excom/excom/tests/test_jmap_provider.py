"""The JMAP mail provider (channels/email/providers/jmap.py) against an in-memory JMAP server.

FakeJMAP below plays a Stalwart server closely enough for Excom: /.well-known/jmap redirects to
/jmap/session, mailboxes are Stalwart's defaults (no Archive), and the core and mail methods
Excom uses keep real state (Email state counter, changes log, back-references, onSuccessUpdateEmail,
push subscriptions, blob downloads). requests' transport is patched, so nothing leaves the machine.

    cd sites && ../env/bin/python runtests_q.py frappe.localhost excom.excom.tests.test_jmap_provider
"""

from __future__ import annotations

import base64
import io
import json
import re
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.parse import unquote, urlparse

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from requests.structures import CaseInsensitiveDict

from excom.excom.channels.email import providers
from excom.excom.channels.email.providers import jmap

ACCOUNT = "QA JMAP Box"
HOST = "https://mail.qa.test"
USER, PASSWORD = "qa.box@qa.test", "app-pass-123"


def _iso(dt: datetime) -> str:
	return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pointer(value, path: str):
	"""JSON pointer with JMAP's "*" (RFC 8620 §3.7)."""
	for i, part in enumerate(path.strip("/").split("/") if path.strip("/") else []):
		if part == "*":
			rest = "/".join(path.strip("/").split("/")[i + 1:])
			out = []
			for item in value:
				v = _pointer(item, rest)
				out += v if isinstance(v, list) else [v]
			return out
		value = value[int(part)] if isinstance(value, list) else value[part]
	return value


class MethodError(Exception):
	def __init__(self, type_, description=""):
		self.type, self.description = type_, description


class FakeJMAP:
	"""A small JMAP server: one user, one account, real state."""

	def __init__(self):
		self.account_id = "a1"
		self.api_path = "/jmap/"
		self.max_get = 500
		self.state = 0
		self.forgotten_before = 0  # Email/changes from an older state -> cannotCalculateChanges
		self.log = []  # (state, kind, email id)
		self.emails = {}
		self.blobs = {}
		self.submissions = []
		self.push = {}
		self.refuse_role = False
		self.reject_submission = ""
		self.calls = []  # every method name, in order
		self.n = 0
		self.mailboxes = {}
		for name, role in (("Inbox", "inbox"), ("Deleted Items", "trash"), ("Drafts", "drafts"), ("Junk Mail", "junk"), ("Sent Items", "sent")):
			self._mailbox(name, role)
		self.identities = [
			{"id": "i-other", "name": "Shared", "email": "shared@qa.test"},
			{"id": "i-me", "name": "QA Box", "email": USER},
		]

	# ── helpers ──

	def _id(self, prefix):
		self.n += 1
		return f"{prefix}{self.n}"

	def _mailbox(self, name, role=None, parent=None):
		mid = self._id("mb")
		self.mailboxes[mid] = {"id": mid, "name": name, "role": role, "parentId": parent}
		return mid

	def box(self, role=None, name=None):
		return next(m["id"] for m in self.mailboxes.values() if (role and m["role"] == role) or (name and m["name"] == name))

	def _change(self, kind, eid):
		self.state += 1
		self.log.append((self.state, kind, eid))

	def add_email(self, *, box="inbox", subject="Hello", sender=("Asha Rao", "asha@cust.test"), to=None, cc=None,
			received=None, keywords=None, html="<p>Hi there</p>", text="Hi there", attachments=(), headers=None,
			message_id=None, in_reply_to=None, references=None, mailbox_ids=None):
		eid = self._id("m")
		received = received or datetime.now(timezone.utc)
		parts_att = []
		for a in attachments:
			bid = self._id("blob")
			self.blobs[bid] = a["data"]
			parts_att.append({"partId": bid, "blobId": bid, "name": a.get("name"), "type": a["type"], "size": len(a["data"]), "disposition": a.get("disposition", "attachment")})
		self.emails[eid] = {
			"id": eid, "blobId": self._id("blob"),
			"threadId": self._thread_for(in_reply_to) or self._id("t"),
			"mailboxIds": mailbox_ids or {self.box(box): True},
			"keywords": dict(keywords or {}),
			"messageId": [message_id or f"{eid}@cust.test"],
			"inReplyTo": in_reply_to, "references": references,
			"from": [{"name": sender[0], "email": sender[1]}] if sender else None,
			"to": to if to is not None else [{"name": None, "email": USER}],
			"cc": cc, "bcc": None,
			"subject": subject,
			"receivedAt": _iso(received), "sentAt": received.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(),
			"preview": text[:256],
			"_headers": dict(headers or {}),
			"htmlBody": [{"partId": "1", "type": "text/html"}] if html else [{"partId": "2", "type": "text/plain"}],
			"textBody": [{"partId": "2", "type": "text/plain"}],
			"bodyValues": {"1": html or "", "2": text or ""},
			"attachments": parts_att,
		}
		self._change("created", eid)
		return eid

	def _thread_for(self, in_reply_to):
		for e in self.emails.values():
			if in_reply_to and set(in_reply_to) & set(e.get("messageId") or []):
				return e["threadId"]
		return None

	def server_update(self, eid, **fields):
		self.emails[eid].update(fields)
		self._change("updated", eid)

	def server_destroy(self, eid):
		del self.emails[eid]
		self._change("destroyed", eid)

	def in_box(self, eid, role):
		return bool(self.emails[eid]["mailboxIds"].get(self.box(role)))

	# ── HTTP ──

	def handle(self, request):
		url = urlparse(request.url)
		if f"{url.scheme}://{url.netloc}" != HOST:
			raise requests.ConnectionError(f"no route to {url.netloc}")
		auth = request.headers.get("Authorization") or ""
		good = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
		if auth != good:
			return self._resp(request, 401, {"type": "about:blank", "status": 401, "detail": "You have to authenticate first."})
		if url.path == "/.well-known/jmap":
			return self._resp(request, 307, b"", {"Location": "/jmap/session"})
		if url.path == "/jmap/session" and request.method == "GET":
			return self._resp(request, 200, self.session())
		if url.path == self.api_path and request.method == "POST":
			return self._resp(request, 200, self.api(json.loads(request.body)))
		m = re.match(r"^/jmap/download/([^/]+)/([^/]+)/([^/]+)$", url.path)
		if m and request.method == "GET":
			blob = self.blobs.get(unquote(m.group(2)))
			if m.group(1) != self.account_id or blob is None:
				return self._resp(request, 404, {"type": "about:blank", "status": 404})
			return self._resp(request, 200, blob, {"Content-Type": "application/octet-stream"})
		return self._resp(request, 404, {"type": "about:blank", "status": 404, "detail": "Not found"})

	def _resp(self, request, status, body, headers=None):
		r = requests.Response()
		r.status_code = status
		r.request = request
		r.url = request.url
		r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
		r._content_consumed = True
		r.raw = io.BytesIO(r._content)
		r.headers = CaseInsensitiveDict(headers or {"Content-Type": "application/json"})
		r.encoding = "utf-8"
		r.reason = "OK" if status == 200 else "Error"
		return r

	def session(self):
		return {
			"capabilities": {
				"urn:ietf:params:jmap:core": {"maxObjectsInGet": self.max_get, "maxObjectsInSet": self.max_get, "maxCallsInRequest": 16},
				"urn:ietf:params:jmap:mail": {}, "urn:ietf:params:jmap:submission": {},
			},
			"accounts": {self.account_id: {"name": USER, "isPersonal": True, "isReadOnly": False}},
			"primaryAccounts": {"urn:ietf:params:jmap:mail": self.account_id, "urn:ietf:params:jmap:submission": self.account_id},
			"username": USER,
			"apiUrl": self.api_path,  # relative, to check it is resolved
			"downloadUrl": f"{HOST}/jmap/download/{{accountId}}/{{blobId}}/{{name}}?accept={{type}}",
			"uploadUrl": "/jmap/upload/{accountId}/",
			"eventSourceUrl": "/jmap/eventsource/?types={types}&closeafter={closeafter}&ping={ping}",
			"state": "sess1",
		}

	def api(self, body):
		out, self.created_ids = [], {}
		for name, args, call_id in body["methodCalls"]:
			self.calls.append(name)
			try:
				args = self._resolve(args, out)
				if not name.startswith("PushSubscription/") and args.get("accountId") != self.account_id:
					raise MethodError("accountNotFound")
				fn = getattr(self, "m_" + name.replace("/", "_"), None)
				if not fn:
					raise MethodError("unknownMethod", name)
				res = fn(args, call_id)
				out += res if isinstance(res, list) else [[name, res, call_id]]
			except MethodError as e:
				out.append(["error", {"type": e.type, "description": e.description}, call_id])
		return {"methodResponses": out, "sessionState": "sess1"}

	def _resolve(self, args, out):
		args = dict(args)
		for key in [k for k in args if k.startswith("#")]:
			ref = args.pop(key)
			src = next((r for r in out if r[2] == ref["resultOf"] and r[0] == ref["name"]), None)
			if src is None:
				raise MethodError("invalidResultReference")
			args[key[1:]] = _pointer(src[1], ref["path"])
		return args

	def _ref(self, eid):
		return self.created_ids.get(eid[1:], eid) if eid and eid.startswith("#") else eid

	# ── methods ──

	def m_Mailbox_get(self, args, _cid):
		return {"accountId": self.account_id, "state": "mb", "list": [dict(m) for m in self.mailboxes.values()], "notFound": []}

	def m_Mailbox_set(self, args, _cid):
		created, not_created = {}, {}
		for cid, obj in (args.get("create") or {}).items():
			if obj.get("role") and self.refuse_role:
				not_created[cid] = {"type": "invalidProperties", "properties": ["role"], "description": "role not allowed"}
				continue
			if any(m["name"] == obj["name"] and m["parentId"] == obj.get("parentId") for m in self.mailboxes.values()):
				not_created[cid] = {"type": "alreadyExists"}
				continue
			created[cid] = {"id": self._mailbox(obj["name"], obj.get("role"), obj.get("parentId"))}
		return {"accountId": self.account_id, "created": created, "notCreated": not_created}

	def m_Identity_get(self, args, _cid):
		return {"accountId": self.account_id, "state": "id", "list": self.identities, "notFound": []}

	def m_Email_query(self, args, _cid):
		f = args.get("filter") or {}
		found = list(self.emails.values())
		if f.get("after"):
			found = [e for e in found if e["receivedAt"] >= f["after"]]
		if f.get("text"):
			t = f["text"].lower()
			found = [e for e in found if t in (e["subject"] + " " + e["bodyValues"]["2"]).lower()]
		desc = not (args.get("sort") or [{"isAscending": True}])[0].get("isAscending", True)
		found.sort(key=lambda e: e["receivedAt"], reverse=desc)
		pos, limit = args.get("position", 0), min(args.get("limit") or 1000, 1000)
		ids = [e["id"] for e in found[pos:pos + limit]]
		return {"accountId": self.account_id, "queryState": "q", "canCalculateChanges": False, "position": pos, "ids": ids, "total": len(found)}

	def m_Email_get(self, args, _cid):
		ids = args.get("ids")
		ids = list(self.emails) if ids is None else ids
		if len(ids) > self.max_get:
			raise MethodError("requestTooLarge")
		props = args.get("properties") or ["id", "threadId", "mailboxIds", "keywords", "subject"]
		found, missing = [], []
		for i in ids:
			e = self.emails.get(self._ref(i))
			if not e:
				missing.append(i)
				continue
			item = {}
			for p in props:
				m = re.match(r"^header:([^:]+):asText$", p)
				if m:
					item[p] = e["_headers"].get(m.group(1))
				elif p == "bodyValues":
					want = set()
					if args.get("fetchHTMLBodyValues"):
						want |= {x["partId"] for x in e["htmlBody"]}
					if args.get("fetchTextBodyValues"):
						want |= {x["partId"] for x in e["textBody"]}
					item[p] = {k: {"value": v, "isEncodingProblem": False, "isTruncated": False} for k, v in e["bodyValues"].items() if k in want}
				elif p == "bodyStructure":
					item[p] = {"type": "multipart/mixed", "subParts": e["htmlBody"] + e["attachments"]}
				else:
					item[p] = json.loads(json.dumps(e.get(p)))
			found.append(item)
		return {"accountId": self.account_id, "state": str(self.state), "list": found, "notFound": missing}

	def m_Email_changes(self, args, _cid):
		since = args["sinceState"]
		if not since.isdigit() or int(since) < self.forgotten_before or int(since) > self.state:
			raise MethodError("cannotCalculateChanges", "state too old")
		pending = [x for x in self.log if x[0] > int(since)]
		taken, ids = [], []
		for entry in pending:
			if entry[2] not in ids and len(ids) >= args.get("maxChanges", 500):
				break
			taken.append(entry)
			if entry[2] not in ids:
				ids.append(entry[2])
		kinds = {}
		for _s, kind, eid in taken:
			before = kinds.get(eid)
			if kind == "destroyed":
				kinds[eid] = None if before == "created" else "destroyed"
			elif kind == "updated":
				kinds[eid] = before or "updated"
			else:
				kinds[eid] = "created"
		new_state = taken[-1][0] if taken else int(since)
		pick = lambda k: [e for e, v in kinds.items() if v == k]
		return {"accountId": self.account_id, "oldState": since, "newState": str(new_state),
			"hasMoreChanges": len(taken) < len(pending), "created": pick("created"), "updated": pick("updated"), "destroyed": pick("destroyed")}

	def _patch(self, e, patch):
		for key, value in patch.items():
			if "/" in key:
				top, sub = key.split("/", 1)
				if value is None:
					e[top].pop(sub, None)
				else:
					e[top][sub] = value
			else:
				e[key] = value
		if not e["mailboxIds"]:
			raise MethodError("invalidProperties", "an email must be in a mailbox")

	def m_Email_set(self, args, _cid):
		old = str(self.state)
		created, not_created, updated, not_updated, destroyed, not_destroyed = {}, {}, {}, {}, [], {}
		for cid, obj in (args.get("create") or {}).items():
			if not obj.get("mailboxIds") or any(m not in self.mailboxes for m in obj["mailboxIds"]):
				not_created[cid] = {"type": "invalidProperties", "properties": ["mailboxIds"]}
				continue
			eid = self._id("m")
			values = {k: v["value"] for k, v in obj.get("bodyValues", {}).items()}
			self.emails[eid] = {
				"id": eid, "blobId": self._id("blob"), "threadId": self._thread_for(obj.get("inReplyTo")) or self._id("t"),
				"mailboxIds": dict(obj["mailboxIds"]), "keywords": dict(obj.get("keywords") or {}),
				"messageId": obj.get("messageId"), "inReplyTo": obj.get("inReplyTo"), "references": obj.get("references"),
				"from": obj.get("from"), "to": obj.get("to"), "cc": obj.get("cc"), "bcc": obj.get("bcc"),
				"subject": obj.get("subject"), "receivedAt": _iso(datetime.now(timezone.utc)), "sentAt": obj.get("sentAt"),
				"preview": values.get("text", "")[:256], "_headers": {},
				"htmlBody": [{"partId": "1", "type": "text/html"}], "textBody": [{"partId": "2", "type": "text/plain"}],
				"bodyValues": {"1": values.get("html", ""), "2": values.get("text", "")}, "attachments": [], "_create": obj,
			}
			self.created_ids[cid] = eid
			created[cid] = {"id": eid, "blobId": self.emails[eid]["blobId"], "threadId": self.emails[eid]["threadId"], "size": 100}
			self._change("created", eid)
		for eid, patch in (args.get("update") or {}).items():
			eid = self._ref(eid)
			if eid not in self.emails:
				not_updated[eid] = {"type": "notFound"}
				continue
			self._patch(self.emails[eid], patch)
			updated[eid] = None
			self._change("updated", eid)
		for eid in args.get("destroy") or []:
			if eid not in self.emails:
				not_destroyed[eid] = {"type": "notFound"}
				continue
			self.server_destroy(eid)
			destroyed.append(eid)
		return {"accountId": self.account_id, "oldState": old, "newState": str(self.state), "created": created or None, "notCreated": not_created or None,
			"updated": updated or None, "notUpdated": not_updated or None, "destroyed": destroyed or None, "notDestroyed": not_destroyed or None}

	def m_EmailSubmission_set(self, args, cid):
		created, not_created, made = {}, {}, {}
		for sub_cid, obj in (args.get("create") or {}).items():
			eid = self._ref(obj.get("emailId"))
			if eid not in self.emails:
				not_created[sub_cid] = {"type": "invalidProperties", "properties": ["emailId"], "description": "no such email"}
				continue
			if obj.get("identityId") not in {i["id"] for i in self.identities}:
				not_created[sub_cid] = {"type": "invalidProperties", "properties": ["identityId"]}
				continue
			if self.reject_submission:
				not_created[sub_cid] = {"type": "forbiddenToSend", "description": self.reject_submission}
				continue
			sid = self._id("sub")
			self.submissions.append({"id": sid, "emailId": eid, "identityId": obj["identityId"], "envelope": obj.get("envelope")})
			created[sub_cid] = {"id": sid}
			made["#" + sub_cid] = eid
		out = [["EmailSubmission/set", {"accountId": self.account_id, "created": created or None, "notCreated": not_created or None}, cid]]
		patches = args.get("onSuccessUpdateEmail") or {}
		if made and patches:
			updates = {made[k]: v for k, v in patches.items() if k in made}
			saved, self.created_ids = self.created_ids, {}
			out.append(["Email/set", self.m_Email_set({"update": updates}, cid), cid])
			self.created_ids = saved
		return out

	def m_PushSubscription_set(self, args, _cid):
		if "accountId" in args:
			raise MethodError("invalidArguments", "PushSubscription has no accountId")
		created, updated, not_updated, destroyed = {}, {}, {}, []
		for cid, obj in (args.get("create") or {}).items():
			pid = self._id("push")
			self.push[pid] = {**obj, "id": pid, "verified": False, "code": f"code-{pid}", "expires": _iso(datetime.now(timezone.utc) + timedelta(days=7))}
			created[cid] = {"id": pid, "expires": self.push[pid]["expires"]}
		for pid, patch in (args.get("update") or {}).items():
			sub = self.push.get(pid)
			if not sub:
				not_updated[pid] = {"type": "notFound"}
			elif "verificationCode" in patch and patch["verificationCode"] != sub["code"]:
				not_updated[pid] = {"type": "invalidProperties", "properties": ["verificationCode"]}
			else:
				sub["verified"] = sub["verified"] or "verificationCode" in patch
				sub.update({k: v for k, v in patch.items() if k == "expires"})
				updated[pid] = None
		for pid in args.get("destroy") or []:
			if self.push.pop(pid, None):
				destroyed.append(pid)
		return {"created": created or None, "updated": updated or None, "notUpdated": not_updated or None, "destroyed": destroyed or None}

	def m_PushSubscription_get(self, args, _cid):
		ids = args.get("ids") or list(self.push)
		return {"list": [{"id": p, "expires": self.push[p]["expires"], "types": self.push[p]["types"]} for p in ids if p in self.push], "notFound": [p for p in ids if p not in self.push]}


class Sink:
	"""Excom's side of sync, in memory."""

	def __init__(self):
		self.store, self.ingested, self.updates, self.removed = {}, [], [], []

	def known(self, sid):
		return sid in self.store

	def ingest(self, meta):
		self.store[meta["id"]] = dict(meta)
		self.ingested.append(meta)

	def update(self, sid, folder, seen, flagged):
		self.updates.append((sid, folder, seen, flagged))
		self.store[sid].update(folder=folder, seen=seen, flagged=flagged)

	def remove(self, sid):
		self.removed.append(sid)
		self.store.pop(sid, None)


def _has_columns() -> bool:
	return "jmap_email_state" in frappe.db.get_table_columns("Excom Channel Account")


class TestJMAPProvider(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.real_row = _has_columns()
		if cls.real_row:
			if frappe.db.exists("Excom Channel Account", ACCOUNT):
				frappe.delete_doc("Excom Channel Account", ACCOUNT, force=True, ignore_permissions=True)
			try:
				frappe.get_doc({
					"doctype": "Excom Channel Account", "account_name": ACCOUNT, "channel": "email",
					"email_address": USER, "email_provider": "JMAP", "jmap_server_url": HOST,
					"jmap_username": USER, "jmap_password": PASSWORD, "jmap_push": 1,
				}).insert(ignore_permissions=True)
				frappe.db.commit()
			except Exception:
				cls.real_row = False

	@classmethod
	def tearDownClass(cls):
		if frappe.db.exists("Excom Channel Account", ACCOUNT):
			frappe.delete_doc("Excom Channel Account", ACCOUNT, force=True, ignore_permissions=True)
			frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.fake = FakeJMAP()
		fake = self.fake
		patches = [mock.patch.object(requests.adapters.HTTPAdapter, "send", new=lambda _adapter, request, **kw: fake.handle(request))]
		if not self.real_row:
			patches.append(mock.patch.object(jmap, "_write_db", new=lambda name, values: None))
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.acc = self._account()
		frappe.cache.delete_value(jmap._cache_key(self.acc))
		self.addCleanup(frappe.cache.delete_value, jmap._cache_key(self.acc))

	def _account(self, **extra):
		acc = frappe._dict(
			name=ACCOUNT, channel="email", email_address=USER, email_provider="JMAP",
			jmap_server_url=HOST, jmap_username=USER, jmap_password=PASSWORD, jmap_push=1,
			jmap_session_url="", jmap_account_id="", jmap_identity_id="", jmap_email_state="",
			jmap_mailboxes="", jmap_push_id="", jmap_push_verified=0, jmap_push_token="",
			email_sync_from_date=None, email_authorized=0, **extra,
		)
		acc.get_password = lambda fieldname, raise_exception=True: acc.get(fieldname) or None
		return acc

	def sid(self, raw):
		return providers.store_id(self.acc, raw)

	def synced(self):
		sink = Sink()
		jmap.sync(self.acc, sink)
		return sink

	# ── session, mailboxes, identity ──

	def test_check_discovers_session_mailboxes_identity(self):
		res = jmap.check(self.acc)
		self.assertTrue(res["ok"], res)
		self.assertEqual(res["email"], USER)
		self.assertEqual(self.acc.jmap_session_url, f"{HOST}/jmap/session")  # followed the redirect
		self.assertEqual(self.acc.jmap_account_id, "a1")
		self.assertEqual(self.acc.jmap_identity_id, "i-me")  # the one with the mailbox's address
		self.assertEqual(self.acc.email_authorized, 1)
		sess = jmap._session(self.acc)
		self.assertEqual(sess["apiUrl"], f"{HOST}/jmap/")
		self.assertEqual(sess["uploadUrl"], f"{HOST}/jmap/upload/{{accountId}}/")

		boxes = json.loads(self.acc.jmap_mailboxes)
		self.assertEqual(boxes["Inbox"], self.fake.box("inbox"))
		self.assertEqual(boxes["Spam"], self.fake.box("junk"))
		self.assertEqual(boxes["Trash"], self.fake.box("trash"))
		archive = self.fake.mailboxes[boxes["Archive"]]
		self.assertEqual((archive["name"], archive["role"]), ("Archive", "archive"))  # created

		if self.real_row:
			row = frappe.db.get_value("Excom Channel Account", ACCOUNT, ["jmap_account_id", "jmap_identity_id", "jmap_session_url", "email_authorized"], as_dict=True)
			self.assertEqual((row.jmap_account_id, row.jmap_identity_id, row.email_authorized), ("a1", "i-me", 1))

		# a second check finds Archive and does not make another
		self.assertTrue(jmap.check(self.acc)["ok"])
		self.assertEqual(sum(1 for m in self.fake.mailboxes.values() if m["role"] == "archive"), 1)

	def test_check_with_a_real_account_document(self):
		if not self.real_row:
			self.skipTest("Excom Channel Account has no JMAP fields yet (migrate)")
		doc = frappe.get_doc("Excom Channel Account", ACCOUNT)
		self.assertTrue(jmap.check(doc)["ok"])
		self.assertEqual(frappe.db.get_value("Excom Channel Account", ACCOUNT, "jmap_account_id"), "a1")
		frappe.cache.delete_value(jmap._cache_key(doc))

	def test_archive_without_role_when_server_refuses_it(self):
		self.fake.refuse_role = True
		self.assertTrue(jmap.check(self.acc)["ok"])
		archive = self.fake.mailboxes[json.loads(self.acc.jmap_mailboxes)["Archive"]]
		self.assertEqual((archive["name"], archive["role"]), ("Archive", None))

	def test_wrong_password(self):
		self.acc.email_authorized = 1
		self.acc.jmap_password = "wrong"
		res = jmap.check(self.acc)
		self.assertFalse(res["ok"])
		self.assertIn("refused the login", res["error"])
		self.assertNotIn("wrong", res["error"])
		self.assertEqual(self.acc.email_authorized, 0)
		with self.assertRaises(jmap.JMAPAuthError) as ctx:
			jmap.sync(self.acc, Sink())
		self.assertIsInstance(ctx.exception, frappe.AuthenticationError)
		self.assertNotEqual(ctx.exception.http_status_code, 401)  # would sign the Excom user out

	def test_network_error_is_clear(self):
		self.acc.jmap_server_url = "https://down.qa.test"
		with self.assertRaises(jmap.JMAPError) as ctx:
			jmap.search(self.acc, "x", 5)
		self.assertIn("Could not reach the mail server", str(ctx.exception))

	def test_rediscovers_when_api_url_moves(self):
		self.assertTrue(jmap.check(self.acc)["ok"])
		self.fake.api_path = "/jmap/v2/"  # the cached apiUrl now 404s
		self.assertEqual(jmap.search(self.acc, "nothing", 5), [])
		self.assertEqual(jmap._session(self.acc)["apiUrl"], f"{HOST}/jmap/v2/")

	def test_method_error_carries_type_and_description(self):
		with self.assertRaises(jmap.JMAPMethodError) as ctx:
			jmap._call(self.acc, [["Nope/get", {}, "0"]])
		self.assertEqual(ctx.exception.type, "unknownMethod")
		self.assertIn("Nope/get", str(ctx.exception))

	# ── sync ──

	def _seed(self):
		f = self.fake
		now = datetime.now(timezone.utc)
		ids = frappe._dict()
		ids.old = f.add_email(subject="Too old", received=now - timedelta(days=30))
		ids.news = f.add_email(subject="Weekly news", sender=("News, Inc.", "news@list.test"), received=now - timedelta(days=3),
			keywords={"$seen": True}, headers={"List-Unsubscribe": " <mailto:unsub@list.test>", "List-Id": "<news.list.test>", "Precedence": "bulk"})
		ids.ask = f.add_email(subject="Order 42", received=now - timedelta(days=2), keywords={"$flagged": True},
			cc=[{"name": "Ravi", "email": "ravi@cust.test"}], message_id="ask42@cust.test",
			in_reply_to=["q1@qa.test"], references=["q0@qa.test", "q1@qa.test"])
		ids.junk = f.add_email(box="junk", subject="You won", received=now - timedelta(days=1))
		ids.both = f.add_email(subject="Sent to self", received=now - timedelta(hours=5),
			mailbox_ids={f.box("inbox"): True, f.box("sent"): True}, keywords={"$seen": True})
		ids.custom = f.add_email(subject="In a folder", received=now - timedelta(hours=1),
			mailbox_ids={f._mailbox("Projects"): True})  # a mailbox Excom has not seen yet
		return ids

	def test_initial_sync(self):
		ids = self._seed()
		self.acc.email_sync_from_date = frappe.utils.add_days(frappe.utils.today(), -10)
		sink = self.synced()
		got = {m["raw_id"]: m for m in sink.ingested}
		self.assertNotIn(ids.old, got)
		self.assertEqual(set(got), {ids.news, ids.ask, ids.junk, ids.both, ids.custom})
		self.assertEqual([m["raw_id"] for m in sink.ingested], [ids.news, ids.ask, ids.junk, ids.both, ids.custom])  # oldest first
		self.assertTrue(all(m["id"] == self.sid(m["raw_id"]) for m in sink.ingested))

		news, ask = got[ids.news], got[ids.ask]
		self.assertEqual((news["folder"], news["seen"], news["flagged"]), ("Inbox", True, False))
		self.assertEqual((ask["folder"], ask["seen"], ask["flagged"]), ("Inbox", False, True))
		self.assertEqual(got[ids.junk]["folder"], "Spam")
		self.assertEqual(got[ids.both]["folder"], "Inbox")
		self.assertEqual(got[ids.both]["labels"], ["inbox", "sent"])
		self.assertEqual(got[ids.custom]["folder"], "Archive")
		self.assertEqual(got[ids.custom]["labels"], ["Projects"])
		self.assertEqual(news["headers"]["From"], '"News, Inc." <news@list.test>')
		self.assertEqual(news["headers"]["List-Unsubscribe"], "<mailto:unsub@list.test>")
		self.assertEqual(news["headers"]["Precedence"], "bulk")
		self.assertEqual(news["categories"], [])
		self.assertEqual(ask["headers"]["From"], "Asha Rao <asha@cust.test>")
		self.assertEqual(ask["headers"]["To"], USER)
		self.assertEqual(ask["headers"]["Cc"], "Ravi <ravi@cust.test>")
		self.assertEqual(ask["headers"]["Message-ID"], "<ask42@cust.test>")
		self.assertEqual(ask["headers"]["In-Reply-To"], "<q1@qa.test>")
		self.assertEqual(ask["headers"]["References"], "<q0@qa.test> <q1@qa.test>")
		self.assertNotIn("List-Unsubscribe", ask["headers"])
		e = self.fake.emails[ids.ask]
		self.assertEqual(ask["received_at"], int(datetime.fromisoformat(e["receivedAt"].replace("Z", "+00:00")).timestamp()))
		self.assertEqual(email_date(ask["headers"]["Date"]), int(datetime.fromisoformat(e["sentAt"]).timestamp()))
		self.assertEqual(ask["snippet"], "Hi there")
		self.assertEqual(ask["thread_id"], e["threadId"])
		self.assertEqual(self.acc.jmap_email_state, str(self.fake.state))
		if self.real_row:
			self.assertEqual(frappe.db.get_value("Excom Channel Account", ACCOUNT, "jmap_email_state"), str(self.fake.state))

	def test_initial_sync_defaults_to_last_7_days_and_pages(self):
		now = datetime.now(timezone.utc)
		self.fake.max_get = 2  # forces several query pages and Email/get batches
		recent = [self.fake.add_email(subject=f"r{k}", received=now - timedelta(hours=k)) for k in range(5)]
		self.fake.add_email(subject="old", received=now - timedelta(days=8))
		sink = self.synced()
		self.assertEqual(sorted(m["raw_id"] for m in sink.ingested), sorted(recent))

	def test_incremental_changes(self):
		ids = self._seed()
		sink = self.synced()
		f = self.fake
		f.server_update(ids.ask, mailboxIds={f.box("junk"): True})  # moved to Junk elsewhere
		f.server_update(ids.junk, keywords={"$seen": True})  # read elsewhere
		f.server_update(ids.news, keywords={"$seen": True, "$flagged": True})
		f.server_destroy(ids.both)
		new = f.add_email(subject="Fresh one")
		gone_soon = f.add_email(subject="Created and deleted")
		f.server_destroy(gone_soon)
		sink.ingested.clear()
		jmap.sync(self.acc, sink)
		updates = {u[0]: u[1:] for u in sink.updates}
		self.assertEqual(updates[self.sid(ids.ask)], ("Spam", False, True))
		self.assertEqual(updates[self.sid(ids.junk)], ("Spam", True, False))
		self.assertEqual(updates[self.sid(ids.news)], ("Inbox", True, True))
		self.assertIn(self.sid(ids.both), sink.removed)
		self.assertEqual([m["raw_id"] for m in sink.ingested], [new])
		self.assertEqual(self.acc.jmap_email_state, str(f.state))
		# nothing new: nothing happens
		sink.ingested.clear(); sink.updates.clear()
		jmap.sync(self.acc, sink)
		self.assertEqual((sink.ingested, sink.updates), ([], []))

	def test_changes_in_pages(self):
		self.fake.max_get = 2  # maxChanges follows maxObjectsInGet, so 5 changes take 3 pages
		sink = self.synced()
		made = [self.fake.add_email(subject=f"n{k}") for k in range(5)]
		jmap.sync(self.acc, sink)
		self.assertEqual([m["raw_id"] for m in sink.ingested], made)
		self.assertEqual(self.fake.calls.count("Email/changes"), 3)
		self.assertEqual(self.acc.jmap_email_state, str(self.fake.state))

	def test_cannot_calculate_changes_resyncs(self):
		ids = self._seed()
		sink = self.synced()
		self.fake.server_update(ids.news, mailboxIds={self.fake.box("trash"): True})
		self.fake.forgotten_before = self.fake.state  # the server dropped the history we need
		jmap.sync(self.acc, sink)
		self.assertIn((self.sid(ids.news), "Trash", True, False), sink.updates)
		self.assertIn("Email/query", self.fake.calls[self.fake.calls.index("Email/changes"):])
		self.assertEqual(self.acc.jmap_email_state, str(self.fake.state))

	def test_get_meta(self):
		ids = self._seed()
		meta = jmap.get_meta(self.acc, ids.junk)
		self.assertEqual((meta["id"], meta["folder"]), (self.sid(ids.junk), "Spam"))
		self.assertIsNone(jmap.get_meta(self.acc, "m-gone"))

	# ── changing mail ──

	def test_move_seen_flagged(self):
		ids = self._seed()
		f = self.fake
		jmap.move(self.acc, [ids.news, ids.both, "m-gone"], "Archive")
		archive = json.loads(self.acc.jmap_mailboxes)["Archive"]
		self.assertEqual(f.emails[ids.news]["mailboxIds"], {archive: True})
		self.assertEqual(f.emails[ids.both]["mailboxIds"], {archive: True})  # every other place dropped
		jmap.move(self.acc, [ids.news], "Spam")
		self.assertEqual(f.emails[ids.news]["mailboxIds"], {f.box("junk"): True})
		with self.assertRaises(frappe.ValidationError):
			jmap.move(self.acc, [ids.news], "Elsewhere")

		jmap.set_seen(self.acc, [ids.ask], True)
		self.assertTrue(f.emails[ids.ask]["keywords"].get("$seen"))
		jmap.set_seen(self.acc, [ids.ask], False)
		self.assertNotIn("$seen", f.emails[ids.ask]["keywords"])
		self.assertTrue(f.emails[ids.ask]["keywords"].get("$flagged"))  # other keywords untouched
		jmap.set_flagged(self.acc, [ids.ask], False)
		self.assertNotIn("$flagged", f.emails[ids.ask]["keywords"])
		jmap.set_flagged(self.acc, [ids.news], True)
		self.assertTrue(f.emails[ids.news]["keywords"]["$flagged"])

	def test_batches_follow_max_objects_in_set(self):
		self.fake.max_get = 2
		made = [self.fake.add_email(subject=f"b{k}") for k in range(5)]
		before = self.fake.calls.count("Email/set")
		jmap.set_seen(self.acc, made, True)
		self.assertEqual(self.fake.calls.count("Email/set") - before, 3)
		self.assertTrue(all(self.fake.emails[m]["keywords"].get("$seen") for m in made))

	def _mail(self, **kw):
		return {"to": "Asha Rao <asha@cust.test>, ravi@cust.test", "cc": "", "bcc": "", "subject": "Re: Order 42",
			"body_html": "<p>Thanks, <b>shipped</b> today.</p>", "in_reply_to": "<ask42@cust.test>",
			"references": "<q1@qa.test>", "thread_id": "", "from_name": "QA Box", "draft_id": "", **kw}

	def test_save_draft_then_replace(self):
		self._seed()
		f = self.fake
		d1 = jmap.save_draft(self.acc, self._mail(subject="first"))
		e = f.emails[d1["id"]]
		self.assertEqual(d1["draft_id"], d1["id"])
		self.assertEqual(e["mailboxIds"], {f.box("drafts"): True})
		self.assertEqual(e["keywords"], {"$draft": True, "$seen": True})
		self.assertEqual(e["to"], [{"name": "Asha Rao", "email": "asha@cust.test"}, {"name": None, "email": "ravi@cust.test"}])
		self.assertEqual(e["inReplyTo"], ["ask42@cust.test"])
		self.assertEqual(e["references"], ["q1@qa.test", "ask42@cust.test"])
		self.assertEqual(e["from"], [{"name": "QA Box", "email": USER}])
		self.assertIn("shipped", e["bodyValues"]["2"])
		self.assertNotIn("<b>", e["bodyValues"]["2"])
		self.assertEqual(d1["thread_id"], f.emails[next(i for i, x in f.emails.items() if x["messageId"] == ["ask42@cust.test"])]["threadId"])

		d2 = jmap.save_draft(self.acc, self._mail(subject="second", bcc="boss@qa.test"), draft_id=d1["draft_id"])
		self.assertNotIn(d1["id"], f.emails)  # old one gone, same request
		self.assertEqual(f.emails[d2["id"]]["subject"], "second")
		self.assertEqual(f.emails[d2["id"]]["bcc"], [{"name": None, "email": "boss@qa.test"}])
		self.assertEqual(jmap.draft_id_for(self.acc, d2["id"]), d2["id"])
		jmap.delete_draft(self.acc, d2["draft_id"])
		self.assertNotIn(d2["id"], f.emails)
		jmap.delete_draft(self.acc, d2["draft_id"])  # already gone: fine

	def test_send_fresh(self):
		self._seed()
		f = self.fake
		res = jmap.send(self.acc, self._mail())
		e = f.emails[res["id"]]
		self.assertEqual(e["mailboxIds"], {f.box("sent"): True})
		self.assertNotIn("$draft", e["keywords"])
		self.assertTrue(res["message_id_header"].startswith("<") and res["message_id_header"].endswith(">"))
		self.assertEqual(res["message_id_header"], f"<{e['messageId'][0]}>")
		self.assertTrue(e["messageId"][0].endswith("@qa.test"))
		self.assertEqual(res["thread_id"], e["threadId"])
		sub = f.submissions[-1]
		self.assertEqual((sub["emailId"], sub["identityId"], sub["envelope"]), (res["id"], "i-me", None))
		self.assertEqual(f.calls[-2:], ["Email/set", "EmailSubmission/set"])  # one request

	def test_send_with_bcc_uses_an_envelope(self):
		self._seed()
		res = jmap.send(self.acc, self._mail(cc="ravi@cust.test", bcc="Boss <boss@qa.test>"))
		e = self.fake.emails[res["id"]]
		self.assertIsNone(e["bcc"])  # never in the message the recipients get
		env = self.fake.submissions[-1]["envelope"]
		self.assertEqual(env["mailFrom"]["email"], USER)
		self.assertEqual([r["email"] for r in env["rcptTo"]], ["asha@cust.test", "ravi@cust.test", "boss@qa.test"])

	def test_send_a_saved_draft(self):
		self._seed()
		f = self.fake
		d = jmap.save_draft(self.acc, self._mail())
		res = jmap.send(self.acc, self._mail(draft_id=d["draft_id"]))
		self.assertEqual(res["id"], d["id"])  # that very draft was submitted
		self.assertEqual(f.submissions[-1]["emailId"], d["id"])
		self.assertEqual(f.emails[d["id"]]["mailboxIds"], {f.box("sent"): True})
		self.assertNotIn("$draft", f.emails[d["id"]]["keywords"])
		self.assertEqual(res["message_id_header"], f"<{f.emails[d['id']]['messageId'][0]}>")

	def test_send_refused(self):
		self._seed()
		self.fake.reject_submission = "Sending quota exceeded"
		before = set(self.fake.emails)
		with self.assertRaises(jmap.JMAPError) as ctx:
			jmap.send(self.acc, self._mail())
		self.assertIn("Sending quota exceeded", str(ctx.exception))
		self.assertEqual(set(self.fake.emails), before)  # no stray draft left behind

	# ── reading ──

	def test_fetch_full_and_attachment(self):
		f = self.fake
		eid = f.add_email(subject="Invoice", html="<p>See <b>attached</b></p>", text="See attached",
			cc=[{"name": "Ravi", "email": "ravi@cust.test"}],
			attachments=[{"name": "invoice.pdf", "type": "application/pdf", "data": b"%PDF-1.4 fake"},
				{"name": None, "type": "image/png", "data": b"\x89PNG", "disposition": "inline"}])
		full = jmap.fetch_full(self.acc, eid)
		self.assertFalse(full["deleted"])
		self.assertEqual(full["body_html"], "<p>See <b>attached</b></p>")
		self.assertEqual(full["body_text"], "See attached")
		self.assertEqual(full["headers"]["Subject"], "Invoice")
		self.assertEqual(full["headers"]["From"], "Asha Rao <asha@cust.test>")
		self.assertEqual(full["headers"]["Cc"], "Ravi <ravi@cust.test>")
		self.assertEqual(full["headers"]["Message-ID"], f"<{eid}@cust.test>")
		self.assertTrue(full["headers"]["Date"])
		self.assertEqual(len(full["attachments"]), 1)  # the unnamed inline image is left out
		att = full["attachments"][0]
		self.assertEqual((att["filename"], att["mimeType"], att["size"]), ("invoice.pdf", "application/pdf", 13))
		self.assertEqual(jmap.fetch_attachment(self.acc, eid, att["attachmentId"], name="invoice.pdf"), b"%PDF-1.4 fake")
		with self.assertRaises(frappe.DoesNotExistError):
			jmap.fetch_attachment(self.acc, eid, "blob-gone")

		plain = f.add_email(subject="Plain", html="", text="Just text")
		full = jmap.fetch_full(self.acc, plain)
		self.assertEqual((full["body_html"], full["body_text"]), ("", "Just text"))

		gone = jmap.fetch_full(self.acc, "m-gone")
		self.assertTrue(gone["deleted"])
		self.assertEqual((gone["body_html"], gone["attachments"], gone["headers"]), ("", [], {}))

	def test_search(self):
		ids = self._seed()
		found = jmap.search(self.acc, "order", 10)
		self.assertEqual([m["raw_id"] for m in found], [ids.ask])
		self.assertEqual(found[0]["id"], self.sid(ids.ask))
		self.assertEqual(len(jmap.search(self.acc, "", 2)), 2)

	# ── push ──

	def test_push_flow(self):
		jmap.check(self.acc)
		token = jmap.push_token(self.acc)
		self.assertEqual(jmap.push_token(self.acc), token)  # made once
		url = f"https://excom.qa.test/api/method/excom.api.jmap_push?token={token}"
		push_id = jmap.ensure_push(self.acc, url)
		sub = self.fake.push[push_id]
		self.assertEqual((sub["url"], sub["deviceClientId"], sub["types"]), (url, f"excom-{ACCOUNT}", ["Email", "Mailbox"]))
		self.assertEqual((self.acc.jmap_push_id, self.acc.jmap_push_verified), (push_id, 0))
		self.assertEqual(jmap.ensure_push(self.acc, url), push_id)  # kept
		self.assertEqual(len(self.fake.push), 1)

		# the server proves the URL works; Excom sends the code back
		sync_now = jmap.handle_push(self.acc, {"@type": "PushVerification", "pushSubscriptionId": push_id, "verificationCode": sub["code"]})
		self.assertFalse(sync_now)
		self.assertTrue(self.fake.push[push_id]["verified"])
		self.assertEqual(self.acc.jmap_push_verified, 1)
		self.assertFalse(jmap.handle_push(self.acc, {"@type": "PushVerification", "pushSubscriptionId": "push-other", "verificationCode": "x"}))

		self.acc.jmap_email_state = "7"
		change = lambda types: {"@type": "StateChange", "changed": {"a1": types}}
		self.assertTrue(jmap.handle_push(self.acc, change({"Email": "8"})))
		self.assertFalse(jmap.handle_push(self.acc, change({"Email": "7"})))
		self.assertTrue(jmap.handle_push(self.acc, change({"Mailbox": "m2"})))
		self.assertFalse(jmap.handle_push(self.acc, {"@type": "StateChange", "changed": {"other": {"Email": "9"}}}))
		self.assertFalse(jmap.handle_push(self.acc, {"@type": "Nonsense"}))

		# push switched off: the subscription goes
		self.acc.jmap_push = 0
		self.assertEqual(jmap.ensure_push(self.acc, url), "")
		self.assertEqual(self.fake.push, {})
		self.assertEqual(self.acc.jmap_push_id, "")


def email_date(value: str) -> int:
	from email.utils import parsedate_to_datetime

	return int(parsedate_to_datetime(value).timestamp())
