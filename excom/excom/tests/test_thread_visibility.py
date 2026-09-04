"""The Excom API, the permission hook and the Desk list must answer the same question the same way.

They did not: the API let a member of the shared inbox open an unclaimed chat while the hook and the
list denied the very same row, so a thread an agent could work in Excom vanished in Desk and in any
report. All three now go through excom_thread.can_access(); these tests ask each one directly and
fail if they ever drift apart again.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from excom.excom.api import chat
from excom.excom.doctype.excom_thread import excom_thread as td

IN_GENERAL = "qa.tv.general@example.com"
NO_TEAM = "qa.tv.loner@example.com"
USERS = (IN_GENERAL, NO_TEAM)


def _answers(user: str, thread: str) -> tuple[bool, bool, bool]:
	"""(excom api, permission hook, desk list) for one user and one thread."""
	frappe.set_user(user)
	try:
		api = chat._user_can_access_thread(thread)
		hook = td.has_permission(frappe.get_doc("Excom Thread", thread), "read", user)
		listed = bool(frappe.get_list("Excom Thread", filters={"name": thread}, fields=["name"], limit_page_length=0))
		return api, hook, listed
	finally:
		frappe.set_user("Administrator")


class TestThreadVisibilityAgreement(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._cleanup()
		for email in USERS:
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.flags.ignore_permissions = True
			u.insert(ignore_permissions=True)
			u.add_roles("Excom User")
		team = frappe.get_doc("Excom Team", "General")
		team.append("members", {"user": IN_GENERAL, "role": "Member"})
		team.flags.ignore_permissions = True
		team.save()
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		cls.identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA TV Person", "primary_phone": "+919900000778"}).insert(ignore_permissions=True).name
		cls.unclaimed = frappe.get_doc({
			"doctype": "Excom Thread", "omni_identity": cls.identity, "channel": ref.channel,
			"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": "qa-tv-1",
			"status": "Open", "last_message_at": now_datetime(),
		}).insert(ignore_permissions=True).name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._cleanup()
		super().tearDownClass()

	@classmethod
	def _cleanup(cls):
		for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA TV%"]}, pluck="name"):
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t})
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			frappe.db.delete("Omni Identity Link", {"parent": oi})
			frappe.db.delete("Omni Identity Channel", {"parent": oi})
			frappe.db.delete("Omni Identity Alias", {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		team = frappe.get_doc("Excom Team", "General")
		rows = [m for m in team.members if m.user in USERS]
		for r in rows:
			team.remove(r)
		if rows:
			team.flags.ignore_permissions = True
			team.save()
		for email in USERS:
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_the_shared_inbox_answers_yes_everywhere(self):
		api, hook, listed = _answers(IN_GENERAL, self.unclaimed)
		self.assertEqual((api, hook, listed), (True, True, True), "a General member must see an unclaimed chat in all three")

	def test_someone_in_no_team_is_refused_everywhere(self):
		api, hook, listed = _answers(NO_TEAM, self.unclaimed)
		self.assertEqual((api, hook, listed), (False, False, False), "an agent in no team must be refused by all three")

	def test_a_claimed_thread_follows_its_owner(self):
		frappe.db.set_value("Excom Thread", self.unclaimed, "assigned_to", NO_TEAM, update_modified=False)
		try:
			self.assertEqual(_answers(NO_TEAM, self.unclaimed), (True, True, True), "the assignee sees their own thread")
			api, hook, listed = _answers(IN_GENERAL, self.unclaimed)
			self.assertEqual((api, hook, listed), (False, False, False), "once claimed it leaves the shared inbox")
		finally:
			frappe.db.set_value("Excom Thread", self.unclaimed, "assigned_to", None, update_modified=False)
