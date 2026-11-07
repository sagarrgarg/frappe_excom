"""Other lane: bulk and automated mail out of the main inbox and its badge. No network.

Mail goes in through the real ingest (`_ingest_email_metadata`) with Gmail-shaped metadata, so the
labels, headers and sender rules are tested where they are applied.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.api import chat, worklist
from excom.excom.channels.email.inbound import _ingest_email_metadata
from excom.excom.services import inbox_lane

ACCOUNT = "QA Lane Mailbox"
OUR_ADDRESS = "qa.lane.desk@example.com"


def _meta(n: int, sender: str, labels: list, extra_headers: dict | None = None, sent: bool = False) -> dict:
	headers = {
		"From": OUR_ADDRESS if sent else sender,
		"To": sender if sent else OUR_ADDRESS,
		"Subject": f"QA lane {n}",
		"Date": "Sat, 07 Nov 2026 10:00:00 +0530",
		"Message-ID": f"<qa-lane-{n}@example.com>",
	}
	headers.update(extra_headers or {})
	return {
		"id": f"qa-lane-msg-{n}",
		"threadId": f"qa-lane-thread-{n}",
		"labelIds": labels + (["SENT"] if sent else ["INBOX", "UNREAD"]),
		"snippet": f"QA lane message {n}",
		"internalDate": "1794038400000",
		"headers": headers,
	}


class TestInboxLane(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.acc = frappe.get_doc({
			"doctype": "Excom Channel Account", "account_name": ACCOUNT, "channel": "email",
			"status": "Active", "email_address": OUR_ADDRESS,
		}).insert(ignore_permissions=True)
		frappe.db.commit()
		cls.n = 0

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		accs = frappe.get_all("Excom Channel Account", {"account_name": ACCOUNT}, pluck="name")
		identities = set()
		for t in frappe.get_all("Excom Thread", filters={"account": ["in", accs or ["-"]]}, fields=["name", "omni_identity"]):
			identities.add(t.omni_identity)
			frappe.db.delete("Excom Message", {"thread": t.name})
			frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
		for oi in identities:
			if not oi:
				continue
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for a in accs:
			frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _ingest(self, sender: str, labels: list, headers: dict | None = None, sent: bool = False) -> str:
		type(self).n += 1
		_ingest_email_metadata(self.acc.name, self.acc, _meta(self.n, sender, labels, headers, sent))
		return frappe.db.get_value("Excom Thread", {"account": self.acc.name, "omni_identity": frappe.db.get_value("Omni Identity Channel", {"channel_user_id": sender}, "parent")}, "name")

	def _lane(self, thread: str) -> str:
		return frappe.db.get_value("Excom Thread", thread, "inbox_lane") or ""

	# ── what goes where ───────────────────────────────────────────────────────

	def test_gmail_updates_and_promotions_go_to_other(self):
		for i, label in enumerate(("CATEGORY_UPDATES", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL")):
			t = self._ingest(f"qa.bulk{i}@bank.example.com", [label])
			self.assertEqual(self._lane(t), "Other", label)

	def test_a_person_writing_stays_in_the_inbox(self):
		t = self._ingest("qa.buyer@client.example.com", ["CATEGORY_PERSONAL"])
		self.assertEqual(self._lane(t), "")

	def test_list_headers_and_noreply_senders_are_other_even_when_gmail_says_personal(self):
		t = self._ingest("qa.list@shop.example.com", ["CATEGORY_PERSONAL"], {"List-Unsubscribe": "<mailto:u@shop.example.com>"})
		self.assertEqual(self._lane(t), "Other", "List-Unsubscribe")
		t = self._ingest("no-reply-billing@pay.example.com", ["CATEGORY_PERSONAL"])
		self.assertEqual(self._lane(t), "Other", "no-reply sender")
		t = self._ingest("qa.auto@sys.example.com", [], {"Auto-Submitted": "auto-generated"})
		self.assertEqual(self._lane(t), "Other", "Auto-Submitted")

	def test_info_address_is_not_taken_for_a_robot(self):
		t = self._ingest("info@smallfirm.example.com", ["CATEGORY_PERSONAL"])
		self.assertEqual(self._lane(t), "")

	# ── what keeps a conversation in the inbox ───────────────────────────────

	def test_a_conversation_we_replied_to_stays(self):
		sender = "qa.replied@vendor.example.com"
		t = self._ingest(sender, ["CATEGORY_UPDATES"])
		self.assertEqual(self._lane(t), "Other")
		self._ingest(sender, [], sent=True)
		self.assertEqual(self._lane(t), "", "our reply makes it a conversation")
		self._ingest(sender, ["CATEGORY_UPDATES"])
		self.assertEqual(self._lane(t), "", "and the next update from them stays in the inbox")

	def test_a_sender_with_a_crm_record_stays(self):
		sender = "qa.customer@client.example.com"
		t = self._ingest(sender, ["CATEGORY_PERSONAL"])
		oi = frappe.db.get_value("Excom Thread", t, "omni_identity")
		frappe.get_doc({"doctype": "Omni Identity Link", "parent": oi, "parenttype": "Omni Identity", "parentfield": "links", "linked_doctype": "Customer", "linked_name": "QA Lane Customer"}).db_insert()
		self._ingest(sender, ["CATEGORY_UPDATES"])
		self.assertEqual(self._lane(t), "")

	def test_a_sender_who_left_the_mailing_list_comes_back(self):
		sender = "qa.mixed@agency.example.com"
		t = self._ingest(sender, ["CATEGORY_PROMOTIONS"])
		self.assertEqual(self._lane(t), "Other")
		self._ingest(sender, ["CATEGORY_PERSONAL"])
		self.assertEqual(self._lane(t), "", "the lane follows the latest message")

	# ── a person's choice sticks ──────────────────────────────────────────────

	def test_moving_by_hand_sticks(self):
		sender = "qa.pinned@news.example.com"
		t = self._ingest(sender, ["CATEGORY_PROMOTIONS"])
		chat.set_inbox_lane([t], "")
		self._ingest(sender, ["CATEGORY_PROMOTIONS"])
		self.assertEqual(self._lane(t), "", "kept in the inbox by hand")
		chat.set_inbox_lane([t], "Other")
		self._ingest(sender, ["CATEGORY_PERSONAL"])
		self.assertEqual(self._lane(t), "Other", "kept in Other by hand")

	# ── the list and the badge ────────────────────────────────────────────────

	def test_the_inbox_and_its_badge_leave_other_out(self):
		news = self._ingest("qa.news@brand.example.com", ["CATEGORY_PROMOTIONS"])
		person = self._ingest("qa.person@client.example.com", ["CATEGORY_PERSONAL"])

		focused = {r["name"] for r in chat.get_threads(lane="focused", account=self.acc.name, limit=500)}
		other = {r["name"] for r in chat.get_threads(lane="other", account=self.acc.name, limit=500)}
		both = {r["name"] for r in chat.get_threads(account=self.acc.name, limit=500)}
		self.assertIn(person, focused)
		self.assertNotIn(news, focused)
		self.assertEqual(other & {news, person}, {news})
		self.assertTrue({news, person} <= both, "search and deep links see both lanes")

		w = worklist.get_worklist()
		frappe.db.set_value("Excom Thread", news, "unread_count", 0)
		self.assertEqual(worklist.get_worklist()["email"]["threads"], w["email"]["threads"], "Other never counts towards the badge")
		self.assertGreaterEqual(w["other_lane"]["threads"], 1)

	def test_backfill_sorts_existing_mail_from_stored_labels(self):
		t = self._ingest("qa.old@alerts.example.com", ["CATEGORY_UPDATES"])
		frappe.db.set_value("Excom Thread", t, "inbox_lane", "")
		res = inbox_lane.backfill()
		self.assertGreaterEqual(res["other"], 1)
		self.assertEqual(self._lane(t), "Other")
