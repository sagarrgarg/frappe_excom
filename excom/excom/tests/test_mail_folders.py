"""Mail folders, both ways: what the mailbox does shows here, what people do here is done in the
mailbox, drafts live in both places, and a mailbox can move servers without losing its history.

A fake server stands in for Gmail / JMAP behind the provider interface (channels/email/providers),
so the rules are tested where they live (channels/email/mailbox.py, drafts.py) with no network.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.api import chat
from excom.excom.api import email as email_api
from excom.excom.channels.email import cutover, drafts, mailbox
from excom.excom.channels.email import providers as P
from excom.excom.channels.email.providers import gmail

ACCOUNT = "QA Folders Mailbox"
OURS = "qa.folders.desk@example.com"


class FakeServer:
	"""Records what Excom asks of the mailbox; answers like a server would."""

	def __init__(self):
		self.calls = []
		self.metas = {}
		self.n = 0

	def __getattr__(self, name):  # any provider function not modelled below: record it
		def f(account, *a, **k):
			self.calls.append((name, a, k))
		return f

	def move(self, account, ids, folder):
		self.calls.append(("move", tuple(sorted(ids)), folder))

	def set_seen(self, account, ids, seen):
		self.calls.append(("seen", tuple(sorted(ids)), seen))

	def save_draft(self, account, mail, draft_id=""):
		self.n += 1
		self.calls.append(("save_draft", mail["subject"], draft_id))
		return {"draft_id": f"d{self.n}", "id": f"draftmsg{self.n}-{frappe.generate_hash(length=6)}", "thread_id": "th1"}

	def delete_draft(self, account, draft_id):
		self.calls.append(("delete_draft", draft_id))

	def draft_id_for(self, account, mid):
		return "found-" + mid

	def send(self, account, mail):
		self.n += 1
		self.calls.append(("send", mail.get("draft_id", ""), mail.get("in_reply_to", "")))
		sid = f"sent{self.n}-{frappe.generate_hash(length=6)}"
		return {"id": sid, "thread_id": "th1", "message_id_header": f"<{sid}@example.com>"}

	def fetch_full(self, account, mid):
		return {"deleted": False, "body_html": "<p>written in Gmail</p>", "body_text": "", "headers": {"To": "x@example.com", "Subject": "From Gmail"}, "attachments": []}

	def get_meta(self, account, mid):
		return self.metas.get(mid)


def meta(n, sender, folder="Inbox", seen=False, to=OURS, message_id=None, thread="th1"):
	return {
		"id": f"qa-fold-{n}", "thread_id": thread, "folder": folder, "seen": seen, "flagged": False,
		"categories": ["CATEGORY_PERSONAL"], "labels": [], "snippet": f"QA folders {n}", "received_at": 1794038400 + n,
		"headers": {"From": sender, "To": to, "Subject": f"QA folders {n}", "Message-ID": message_id or f"<qa-fold-{n}@example.com>"},
	}


def _clean():
	frappe.set_user("Administrator")
	accs = frappe.get_all("Excom Channel Account", {"account_name": ACCOUNT}, pluck="name")
	ids = set()
	for t in frappe.get_all("Excom Thread", filters={"account": ["in", accs or ["-"]]}, fields=["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Excom Email Draft", {"thread": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in filter(None, ids):
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for a in accs:
		frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestMailFolders(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_clean()
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": ACCOUNT, "channel": "email",
			"status": "Active", "email_address": OURS}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_clean()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.server = FakeServer()
		self.pushes = []
		self._p = [
			patch.object(P, "get", lambda account: self.server),
			patch.object(mailbox, "_push", lambda thread, op, plan: self.pushes.append((op, plan))),
		]
		for p in self._p:
			p.start()
		self.sink = mailbox.Sink(self.acc)
		type(self).k = getattr(type(self), "k", 0)

	def tearDown(self):
		for p in self._p:
			p.stop()

	def _new(self, sender=None, **kw):
		type(self).k += 1
		sender = sender or f"qa.fold{self.k}@client.example.com"
		name = mailbox.ingest(self.acc, meta(self.k, sender, **kw))
		return name, frappe.db.get_value("Excom Message", name, "thread") if name else None

	def _t(self, thread, field="status"):
		return frappe.db.get_value("Excom Thread", thread, field)

	# ── the mailbox moves the conversation ───────────────────────────────────

	def test_where_new_mail_lands(self):
		_m, t = self._new()
		self.assertEqual((self._t(t), self._t(t, "unread_count")), ("Open", 1), "Inbox: open and unread")
		_m, t = self._new(folder="Spam")
		self.assertEqual((self._t(t), self._t(t, "unread_count")), ("Spam", 0), "straight to Spam: a Spam conversation, no badge")
		_m, t = self._new(folder="Archive")
		self.assertEqual(self._t(t), "Closed", "a mail filter skipped the inbox")
		_m, t = self._new(sender=OURS, to="qa.fold.newperson@client.example.com", folder="Sent")
		self.assertEqual(self._t(t), "Closed", "we wrote first from Gmail: in Sent, not in the inbox")
		self.assertEqual(frappe.db.get_value("Excom Message", _m, "direction"), "Outbound")
		_m, t = self._new(seen=True)
		self.assertEqual(self._t(t, "unread_count"), 0, "already read in Gmail")

	def test_archived_binned_or_spammed_in_gmail_moves_the_conversation(self):
		m, t = self._new()
		sid = frappe.db.get_value("Excom Message", m, "provider_message_id")
		self.sink.update(sid, "Archive", False, False)
		self.assertEqual(self._t(t), "Closed", "its last email left Inbox")
		self.sink.update(sid, "Inbox", False, False)
		self.assertEqual(self._t(t), "Open", "back in Inbox")
		self.sink.update(sid, "Spam", False, False)
		self.assertEqual(self._t(t), "Spam")
		self.sink.update(sid, "Inbox", False, False)
		self.sink.update(sid, "Trash", False, False)
		self.assertEqual(self._t(t), "Closed")
		self.assertIn(t, {r["name"] for r in chat.get_threads(folder="trash", account=self.acc.name, limit=500)})

	def test_one_email_leaving_does_not_archive_a_conversation_with_others_in_inbox(self):
		sender = "qa.fold.two@client.example.com"
		m1, t = self._new(sender=sender)
		self._new(sender=sender)
		self.sink.update(frappe.db.get_value("Excom Message", m1, "provider_message_id"), "Archive", False, False)
		self.assertEqual(self._t(t), "Open")

	def test_read_and_unread_in_gmail(self):
		sender = "qa.fold.read@client.example.com"
		m1, t = self._new(sender=sender)
		m2, _ = self._new(sender=sender)
		self.assertEqual(self._t(t, "unread_count"), 2)
		for m in (m1, m2):
			self.sink.update(frappe.db.get_value("Excom Message", m, "provider_message_id"), "Inbox", True, False)
		self.assertEqual(self._t(t, "unread_count"), 0)
		self.sink.update(frappe.db.get_value("Excom Message", m2, "provider_message_id"), "Inbox", False, True)
		self.assertEqual(self._t(t, "unread_count"), 1)
		self.assertEqual(frappe.db.get_value("Excom Message", m2, "mail_flagged"), 1)

	def test_deleted_for_good_in_gmail(self):
		m, _t = self._new()
		self.sink.remove(frappe.db.get_value("Excom Message", m, "provider_message_id"))
		self.assertFalse(frappe.db.exists("Excom Message", m))

	# ── what people do here, done in the mailbox ─────────────────────────────

	def test_archive_spam_trash_restore_are_done_in_the_mailbox(self):
		m, t = self._new()
		sid = frappe.db.get_value("Excom Message", m, "provider_message_id")
		chat.archive_thread(t)
		self.assertEqual(frappe.db.get_value("Excom Message", m, "mail_folder"), "Archive")
		self.assertEqual(self.pushes[-1], ("move", {"Archive": [sid]}))
		chat.unarchive_thread(t)
		self.assertEqual(self.pushes[-1], ("move", {"Inbox": [sid]}))
		chat.mark_spam(t)
		self.assertEqual(self.pushes[-1], ("move", {"Spam": [sid]}))
		self.assertIn(t, {r["name"] for r in chat.get_threads(folder="spam", account=self.acc.name, limit=500)})
		chat.not_spam(t)
		self.assertEqual((self._t(t), self.pushes[-1]), ("Open", ("move", {"Inbox": [sid]})))
		chat.trash_thread(t)
		self.assertEqual((self._t(t), self.pushes[-1]), ("Closed", ("move", {"Trash": [sid]})))
		self.assertNotIn(t, {r["name"] for r in chat.get_threads(folder="archive", account=self.acc.name, limit=500)}, "wholly in Trash: not in Archive")
		chat.restore_thread(t)
		self.assertEqual((self._t(t), self.pushes[-1]), ("Open", ("move", {"Inbox": [sid]})))

	def test_restore_puts_our_emails_back_in_sent(self):
		sender = "qa.fold.restore@client.example.com"
		_m, t = self._new(sender=sender)
		type(self).k += 1
		ours = mailbox.ingest(self.acc, meta(self.k, OURS, to=sender, folder="Sent"))
		chat.trash_thread(t)
		chat.restore_thread(t)
		self.assertEqual(frappe.db.get_value("Excom Message", ours, "mail_folder"), "Sent")

	def test_reading_here_marks_read_in_the_mailbox(self):
		m, t = self._new()
		sid = frappe.db.get_value("Excom Message", m, "provider_message_id")
		chat.mark_read(t)
		self.assertEqual(self.pushes[-1], ("seen", {"": [sid]}))
		n = len(self.pushes)
		chat.mark_read(t)
		self.assertEqual(len(self.pushes), n, "already read: the mailbox is not asked again")
		chat.mark_unread(t)
		self.assertEqual(self.pushes[-1], ("unseen", {"": [sid]}))

	def test_the_background_job_calls_the_server(self):
		m, _t = self._new()
		sid = frappe.db.get_value("Excom Message", m, "provider_message_id")
		mailbox.run_push(self.acc.name, "move", {"Archive": [sid]})
		mailbox.run_push(self.acc.name, "seen", {"": [sid]})
		self.assertIn(("move", (sid,), "Archive"), self.server.calls)
		self.assertIn(("seen", (sid,), True), self.server.calls)
		mailbox.run_push(self.acc.name, "move", {"Archive": ["jmap:Other:abc"]})
		self.assertEqual(self.server.calls[-1], ("seen", (sid,), True), "an id from the other server is skipped")

	def test_whatsapp_conversations_are_left_alone(self):
		self.assertEqual(mailbox.move_thread("no-such-thread", "archive"), 0)

	# ── folders in the list ──────────────────────────────────────────────────

	def test_sent_scheduled_and_folder_counts(self):
		sender = "qa.fold.sent@client.example.com"
		_m, t = self._new(sender=sender)
		with patch.object(chat, "_claim_on_talk", lambda *a, **k: None):
			email_api.send_email(t, sender, "Re: hello", "<p>hi</p>")
		self.assertIn(("send", "", ""), self.server.calls)
		self.assertIn(t, {r["name"] for r in chat.get_threads(folder="sent", account=self.acc.name, limit=500)})
		later = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=3)
		email_api.send_email(t, sender, "Later", "<p>x</p>", send_at=str(later))
		self.assertIn(t, {r["name"] for r in chat.get_threads(folder="scheduled", account=self.acc.name, limit=500)})
		box = {a["name"]: a for a in chat.get_inbox_accounts()}[self.acc.name]
		self.assertGreaterEqual(box["folders"].get("scheduled", 0), 1)
		with self.assertRaises(frappe.ValidationError):
			chat.get_threads(folder="nonsense")

	def test_a_reply_threads_with_standard_headers(self):
		m, t = self._new()
		sid = frappe.db.get_value("Excom Message", m, "provider_message_id")
		email_api.send_email(t, "x@client.example.com", "Re", "<p>r</p>", in_reply_to_gmail_id=sid)
		self.assertEqual(self.server.calls[-1], ("send", "", f"<qa-fold-{self.k}@example.com>"))
		sent = frappe.get_all("Excom Message", {"thread": t, "direction": "Outbound"}, ["mail_folder", "internet_message_id"])[0]
		self.assertEqual(sent.mail_folder, "Sent")
		self.assertTrue(sent.internet_message_id.startswith("<sent"))

	# ── drafts, both places ───────────────────────────────────────────────────

	def test_a_draft_is_saved_in_the_mailbox_and_sent_as_that_draft(self):
		_m, t = self._new()
		d = email_api.save_email_draft(t, to="x@client.example.com", subject="Quote", body_html="<p>v1</p>")
		self.assertEqual(self.server.calls[-1], ("save_draft", "Quote", ""))
		d = email_api.save_email_draft(t, to="x@client.example.com", subject="Quote", body_html="<p>v2</p>", draft_name=d["name"])
		self.assertEqual(self.server.calls[-1], ("save_draft", "Quote", "d1"), "the same mailbox draft, replaced")
		self.assertTrue(d["synced"])
		self.assertEqual(email_api.get_email_draft(t)["body_html"], "<p>v2</p>")
		self.assertIn(t, {r["name"] for r in chat.get_threads(folder="drafts", account=self.acc.name, limit=500)})
		self.assertFalse(frappe.db.exists("Excom Message", {"thread": t, "direction": "Outbound"}), "a draft is not something we sent")
		email_api.send_email(t, "x@client.example.com", "Quote", "<p>final</p>", draft_name=d["name"])
		self.assertEqual(self.server.calls[-2:], [("save_draft", "Quote", "d2"), ("send", "d3", "")], "brought up to date, then sent as the draft")
		self.assertIsNone(email_api.get_email_draft(t))

	def test_discarding_deletes_it_in_the_mailbox(self):
		_m, t = self._new()
		d = email_api.save_email_draft(t, to="x@client.example.com", subject="Drop", body_html="<p>x</p>")
		email_api.discard_email_draft(d["name"])
		self.assertEqual(self.server.calls[-1], ("delete_draft", "d1"))
		self.assertFalse(frappe.db.exists("Excom Email Draft", d["name"]))

	def test_scheduling_a_draft_takes_it_out_of_drafts(self):
		_m, t = self._new()
		d = email_api.save_email_draft(t, to="x@client.example.com", subject="Tomorrow", body_html="<p>x</p>")
		email_api.send_email(t, "x@client.example.com", "Tomorrow", "<p>x</p>", send_at=str(frappe.utils.add_to_date(frappe.utils.now_datetime(), days=1)), draft_name=d["name"])
		self.assertFalse(frappe.db.exists("Excom Email Draft", d["name"]))

	def test_a_draft_written_in_gmail_shows_up_and_opens_with_its_text(self):
		person = "qa.fold.gmaildraft@client.example.com"
		type(self).k += 1
		mailbox.ingest(self.acc, meta(self.k, OURS, to=person, folder="Drafts"))
		row = frappe.get_all("Excom Email Draft", {"provider_message_id": f"qa-fold-{self.k}"}, ["name", "thread", "origin"])[0]
		self.assertEqual(row.origin, "Mailbox")
		self.assertEqual(self._t(row.thread), "Closed", "a draft to someone new does not open an inbox conversation")
		self.assertEqual(drafts.for_thread(row.thread)["body_html"], "<p>written in Gmail</p>")
		# Sent from Gmail: Gmail keeps the id and moves it to Sent.
		sid = f"qa-fold-{self.k}"
		self.server.metas[sid] = meta(self.k, OURS, to=person, folder="Sent")
		self.sink.update(sid, "Sent", True, False)
		self.assertFalse(frappe.db.exists("Excom Email Draft", row.name))
		self.assertEqual(frappe.db.get_value("Excom Message", {"provider_message_id": sid}, "direction"), "Outbound")

	def test_a_draft_deleted_in_gmail_goes(self):
		person = "qa.fold.gone@client.example.com"
		type(self).k += 1
		mailbox.ingest(self.acc, meta(self.k, OURS, to=person, folder="Drafts"))
		self.sink.remove(f"qa-fold-{self.k}")
		self.assertFalse(frappe.db.exists("Excom Email Draft", {"provider_message_id": f"qa-fold-{self.k}"}))

	# ── moving servers ───────────────────────────────────────────────────────

	def test_moving_to_stalwart_keeps_the_history(self):
		sender = "qa.fold.move@client.example.com"
		m, t = self._new(sender=sender, message_id="<same-everywhere@example.com>")
		jm = frappe._dict(self.acc.as_dict(), email_provider="JMAP", name=self.acc.name)
		again = mailbox.ingest(jm, {**meta(999, sender, message_id="<same-everywhere@example.com>"), "id": "Mabc"})
		self.assertEqual(again, m, "the same email, found by its Message-ID")
		self.assertEqual(frappe.db.get_value("Excom Message", m, "provider_message_id"), f"jmap:{self.acc.name}:Mabc")
		self.assertEqual(frappe.db.count("Excom Message", {"thread": t}), 1)

	def test_ids_are_kept_apart_per_server(self):
		jm = frappe._dict(name="Box", email_provider="JMAP")
		self.assertEqual(P.store_id(jm, "M1"), "jmap:Box:M1")
		self.assertEqual(P.raw_id(jm, "jmap:Box:M1"), "M1")
		self.assertEqual(P.provider_of_stored("18c2f"), P.GMAIL)
		self.assertEqual(P.pick_folder(["Sent", "Inbox"]), "Inbox")
		self.assertEqual(P.pick_folder([]), "Archive")

	# ── switching it on ──────────────────────────────────────────────────────

	def test_switching_on_keeps_what_was_read_here(self):
		sender = "qa.fold.cut@client.example.com"
		m1, t = self._new(sender=sender)
		m2, _ = self._new(sender=sender)
		m3, _ = self._new(sender=sender)
		frappe.db.set_value("Excom Message", {"thread": t}, "mail_folder", "")
		frappe.db.set_value("Excom Thread", t, "unread_count", 1)
		cutover.fill_folders()
		self.assertEqual([frappe.db.get_value("Excom Message", m, "mail_seen") for m in (m1, m2, m3)], [1, 1, 0], "only the newest stays unread")


class TestGmailTranslation(FrappeTestCase):
	"""Gmail labels onto folders, and Gmail's history onto the sink."""

	def test_labels_to_folders(self):
		self.assertEqual(gmail.state_of(["INBOX", "UNREAD", "CATEGORY_UPDATES"]), ("Inbox", False, False))
		self.assertEqual(gmail.state_of(["SENT", "STARRED"]), ("Sent", True, True))
		self.assertEqual(gmail.state_of(["SENT", "INBOX"]), ("Inbox", True, False))
		self.assertEqual(gmail.state_of(["TRASH", "SENT"]), ("Trash", True, False))
		self.assertEqual(gmail.state_of(["SPAM", "UNREAD"]), ("Spam", False, False))
		self.assertEqual(gmail.state_of(["DRAFT"]), ("Drafts", True, False))
		self.assertEqual(gmail.state_of(["IMPORTANT"]), ("Archive", True, False))
		self.assertEqual(gmail.MOVES["Archive"], ([], ["INBOX", "SPAM", "TRASH"]))

	def test_history_becomes_sink_calls(self):
		acc = frappe._dict(name="G", email_provider="Gmail", email_last_history_id="100")
		pages = [
			{"history": [
				{"messagesAdded": [{"message": {"id": "new1", "labelIds": ["INBOX", "UNREAD"]}}]},
				{"labelsRemoved": [{"message": {"id": "old1", "labelIds": ["SENT"]}, "labelIds": ["INBOX"]}]},
				{"labelsAdded": [{"message": {"id": "old2", "labelIds": ["SPAM"]}, "labelIds": ["SPAM"]}]},
			], "historyId": "150", "nextPageToken": "p2"},
			{"history": [{"messagesDeleted": [{"message": {"id": "old3"}}]},
				{"labelsRemoved": [{"message": {"id": "old2", "labelIds": ["INBOX"]}, "labelIds": ["SPAM"]}]}], "historyId": "160"},
		]
		seen = []

		class S:
			def known(self, sid):
				return sid != "new1"

			def update(self, sid, *state):
				seen.append(("update", sid, state))

			def remove(self, sid):
				seen.append(("remove", sid))

			def ingest(self, m):
				seen.append(("ingest", m["id"]))

		calls = iter(pages)
		with patch.object(gmail, "_call", lambda *a, **k: next(calls)), \
			patch.object(gmail, "get_meta", lambda account, mid: {"id": mid, "folder": "Inbox", "seen": False, "flagged": False}), \
			patch.object(gmail, "_set_cursor", lambda account, h: seen.append(("cursor", h))):
			self.assertTrue(gmail._sync_changes(acc, S()))
		self.assertIn(("remove", "old3"), seen)
		self.assertIn(("update", "old1", ("Sent", True, False)), seen, "archived: only SENT left")
		self.assertIn(("update", "old2", ("Inbox", True, False)), seen, "the last change wins")
		self.assertIn(("ingest", "new1"), seen)
		self.assertEqual(seen[-1], ("cursor", "160"))
