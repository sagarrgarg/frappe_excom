"""Who sees which conversation, and who may reply from which number or mailbox.

The rule (excom_thread.can_access): your own, your desk's, and any conversation nobody owns on an
account your team is allowed on (an account with no allowed teams is open to every agent).
Replying also needs the account, unless the conversation is assigned to you.

Checked three ways that must agree: the Python rule, the SQL the inbox uses, and the account list.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.api import chat
from excom.excom.doctype.excom_thread.excom_thread import can_access, can_send_from

ME = "qa.acc.me@example.com"
COLLEAGUE = "qa.acc.colleague@example.com"
MY_TEAM = "QA Acc Domestic"
OTHER_TEAM = "QA Acc Export"
EMPTY_DESK = "QA Acc Empty Desk"
MINE = "QA Acc Domestic Number"  # allowed: my team
OPEN = "QA Acc Open Page"  # no allowed teams: open to all agents
THEIRS = "QA Acc Export Number"  # allowed: the other team


def _cleanup():
	frappe.set_user("Administrator")
	accs = frappe.get_all("Excom Channel Account", {"account_name": ["like", "QA Acc %"]}, pluck="name")
	ids = set()
	for t in frappe.get_all("Excom Thread", filters={"account": ["in", accs or ["-"]]}, fields=["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in filter(None, ids):
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for a in accs:
		frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	for team in (MY_TEAM, OTHER_TEAM, EMPTY_DESK):
		if frappe.db.exists("Excom Team", team):
			frappe.delete_doc("Excom Team", team, force=True, ignore_permissions=True)
	for u in (ME, COLLEAGUE):
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestAccountAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for email in (ME, COLLEAGUE):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		for name, members in ((MY_TEAM, [ME, COLLEAGUE]), (OTHER_TEAM, []), (EMPTY_DESK, [])):
			doc = frappe.get_doc({"doctype": "Excom Team", "team_name": name})
			for m in members:
				doc.append("members", {"user": m, "role": "Member"})
			doc.insert(ignore_permissions=True)
		cls.acc = {}
		for label, teams in ((MINE, [MY_TEAM]), (OPEN, []), (THEIRS, [OTHER_TEAM])):
			doc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": label, "channel": "whatsapp", "status": "Active"})
			for t in teams:
				doc.append("allowed_teams", {"team": t})
			cls.acc[label] = doc.insert(ignore_permissions=True).name

		cls.t = {}
		cases = {
			"unowned_mine": (MINE, None, None),
			"unowned_mine_on_empty_desk": (MINE, None, EMPTY_DESK),  # the 1,652 parked GGIL chats
			"unowned_open": (OPEN, None, None),
			"unowned_theirs": (THEIRS, None, None),
			"colleague_owned_mine": (MINE, COLLEAGUE, None),
			"my_desk_on_theirs": (THEIRS, None, MY_TEAM),
			"assigned_me_on_theirs": (THEIRS, ME, None),
			"other_desk_unowned_theirs": (THEIRS, None, OTHER_TEAM),
		}
		for i, (key, (acc, owner, team)) in enumerate(cases.items()):
			oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Acc {key}", "primary_phone": f"+9188877{i:05d}"}).insert(ignore_permissions=True).name
			cls.t[key] = frappe.get_doc({
				"doctype": "Excom Thread", "omni_identity": oi, "channel": "whatsapp",
				"account_doctype": "Excom Channel Account", "account": cls.acc[acc],
				"thread_key": f"qa-acc-{key}", "status": "Open", "assigned_to": owner, "assigned_team": team,
				"unread_count": 1, "last_message_at": frappe.utils.now_datetime(),
			}).insert(ignore_permissions=True).name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(ME)
		frappe.local.excom_open_accounts = {}

	def tearDown(self):
		frappe.set_user("Administrator")

	SEES = {
		"unowned_mine": True,
		"unowned_mine_on_empty_desk": True,
		"unowned_open": True,
		"unowned_theirs": False,
		"colleague_owned_mine": False,
		"my_desk_on_theirs": True,
		"assigned_me_on_theirs": True,
		"other_desk_unowned_theirs": False,
	}

	def test_who_sees_what(self):
		for key, expected in self.SEES.items():
			self.assertEqual(can_access(self.t[key], ME), expected, key)

	def test_the_inbox_lists_exactly_what_can_be_opened(self):
		listed = {r["name"] for r in chat.get_threads(limit=1000)}
		for key, expected in self.SEES.items():
			self.assertEqual(self.t[key] in listed, expected, f"{key}: the list and the rule disagree")

	def test_replying_needs_the_account(self):
		self.assertTrue(can_send_from(self.t["unowned_mine"], ME))
		self.assertTrue(can_send_from(self.t["unowned_open"], ME))
		self.assertFalse(can_send_from(self.t["my_desk_on_theirs"], ME), "seen through the desk, but not my number")
		self.assertTrue(can_send_from(self.t["assigned_me_on_theirs"], ME), "assigned to me: I may answer it")
		with self.assertRaises(frappe.PermissionError):
			chat._check_send_access(self.t["my_desk_on_theirs"])
		rows = {r["name"]: r for r in chat.get_threads(limit=1000)}
		self.assertEqual(rows[self.t["my_desk_on_theirs"]]["can_send"], 0)
		self.assertEqual(rows[self.t["unowned_mine"]]["can_send"], 1)

	def test_the_account_list(self):
		accounts = {a["name"]: a for a in chat.get_inbox_accounts()}
		self.assertIn(self.acc[MINE], accounts)
		self.assertIn(self.acc[OPEN], accounts)
		self.assertEqual(accounts[self.acc[MINE]]["can_send"], 1)
		self.assertEqual(accounts[self.acc[MINE]]["unread_threads"], 2, "my number's two unowned chats; not the colleague's")
		theirs = accounts.get(self.acc[THEIRS])
		self.assertIsNotNone(theirs, "listed: two of my conversations sit on it")
		self.assertEqual(theirs["can_send"], 0)
		self.assertEqual(theirs["open_threads"], 2)

	def test_admins_see_everything(self):
		frappe.set_user("Administrator")
		listed = {r["name"] for r in chat.get_threads(limit=1000)}
		self.assertTrue(set(self.t.values()) <= listed)
