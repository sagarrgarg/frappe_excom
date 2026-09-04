"""A contact belongs to a desk, and so does everything hanging off them.

Endpoints that take an omni_identity used to check only "is this an Excom user at all", which let
any agent read another desk's contact, their linked records and their activity trail — and close or
reopen their conversations. A contact nobody has claimed stays open to everybody, or nobody could
start the first conversation with a new lead.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from excom.excom.api.chat import _check_identity_access

MINE = "qa.ident.mine@example.com"
THEIRS = "qa.ident.theirs@example.com"
TEAM, OTHER = "QA Ident Desk", "QA Ident Other"


def _cleanup():
	frappe.set_user("Administrator")
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Ident%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for t in (TEAM, OTHER):
		if frappe.db.exists("Excom Team", t):
			frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
	for u in (MINE, THEIRS):
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestIdentityAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for name in (TEAM, OTHER):
			frappe.get_doc({"doctype": "Excom Team", "team_name": name}).insert(ignore_permissions=True)
		for email, team in ((MINE, TEAM), (THEIRS, OTHER)):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.flags.ignore_permissions = True
			u.insert(ignore_permissions=True)
			u.add_roles("Excom User")
			doc = frappe.get_doc("Excom Team", team)
			doc.append("members", {"user": email, "role": "Member"})
			doc.flags.ignore_permissions = True
			doc.save()
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		cls.owned = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Ident Owned",
			"primary_phone": "+919900000981"}).insert(ignore_permissions=True).name
		frappe.get_doc({"doctype": "Excom Thread", "omni_identity": cls.owned, "channel": ref.channel,
			"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": "qa-ident-1",
			"status": "Open", "assigned_team": TEAM, "last_message_at": now_datetime()}).insert(ignore_permissions=True)
		cls.unclaimed = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Ident Unclaimed",
			"primary_phone": "+919900000982"}).insert(ignore_permissions=True).name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_the_desk_working_a_contact_may_reach_them(self):
		frappe.set_user(MINE)
		_check_identity_access(self.owned)  # no throw

	def test_another_desk_may_not(self):
		frappe.set_user(THEIRS)
		with self.assertRaises(frappe.PermissionError):
			_check_identity_access(self.owned)

	def test_a_contact_nobody_owns_stays_open(self):
		"""Otherwise the first conversation with a new lead could never be started."""
		for user in (MINE, THEIRS):
			frappe.set_user(user)
			_check_identity_access(self.unclaimed)  # no throw
			frappe.set_user("Administrator")

	def test_an_outsider_cannot_close_somebody_elses_conversation(self):
		from excom.excom.api import record

		frappe.set_user(THEIRS)
		with self.assertRaises(frappe.PermissionError):
			record.close_conversation(omni_identity=self.owned, outcome="Resolved")

	def test_an_outsider_cannot_read_their_activity(self):
		from excom.excom.api import crm

		frappe.set_user(THEIRS)
		with self.assertRaises(frappe.PermissionError):
			crm.get_records_for_identity(omni_identity=self.owned)
