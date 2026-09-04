"""Three user roles, each a capability tier, and none of them a shortcut to seeing more.

  Excom User    — work the inbox
  Excom Manager — that, plus run people and desks
  Excom Admin   — that, plus run the system

Capability and scope used to be the same lever: Excom Manager was a blanket "sees every
conversation in the company" bypass, so letting somebody add a member to a desk also handed them
every chat. Scope now comes from the team tree alone. These tests hold both halves of that.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from excom.excom.api import chat

ADMIN = "qa.tier.admin@example.com"
MANAGER = "qa.tier.manager@example.com"
AGENT = "qa.tier.agent@example.com"
TEAM, OTHER = "QA Tier Desk", "QA Tier Other"
USERS = (ADMIN, MANAGER, AGENT)


def _cleanup():
	frappe.set_user("Administrator")
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Tier%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for t in (TEAM, OTHER):
		if frappe.db.exists("Excom Team", t):
			frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
	for u in USERS:
		if frappe.db.exists("User", u):
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestRoleTiers(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for name in (TEAM, OTHER):
			frappe.get_doc({"doctype": "Excom Team", "team_name": name}).insert(ignore_permissions=True)
		for email, role in ((ADMIN, "Excom Admin"), (MANAGER, "Excom Manager"), (AGENT, "Excom User")):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.flags.ignore_permissions = True
			u.insert(ignore_permissions=True)
			u.add_roles(role)
		# the manager runs OTHER; the conversation lives on TEAM, which is nothing to do with them
		doc = frappe.get_doc("Excom Team", OTHER)
		doc.append("members", {"user": MANAGER, "role": "Manager"})
		doc.flags.ignore_permissions = True
		doc.save()
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Tier Buyer",
			"primary_phone": "+919900000991"}).insert(ignore_permissions=True).name
		cls.thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": identity,
			"channel": ref.channel, "account_doctype": ref.account_doctype, "account": ref.account,
			"thread_key": "qa-tier-1", "status": "Open", "assigned_team": TEAM,
			"last_message_at": now_datetime()}).insert(ignore_permissions=True).name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── capability ──
	def test_an_agent_cannot_run_people_or_the_system(self):
		frappe.set_user(AGENT)
		with self.assertRaises(frappe.PermissionError):
			chat._check_manager_access()
		with self.assertRaises(frappe.PermissionError):
			chat._check_admin_access()

	def test_a_manager_runs_people_but_not_the_system(self):
		frappe.set_user(MANAGER)
		chat._check_manager_access()  # no throw
		with self.assertRaises(frappe.PermissionError):
			chat._check_admin_access()

	def test_an_admin_runs_both(self):
		frappe.set_user(ADMIN)
		chat._check_manager_access()
		chat._check_admin_access()

	def test_every_tier_may_open_excom_at_all(self):
		for user in USERS:
			frappe.set_user(user)
			chat._check_excom_access()  # an admin who is not also an agent must still get in
			frappe.set_user("Administrator")

	# ── scope, which is a different axis ──
	def test_a_manager_of_another_desk_is_refused(self):
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.PermissionError):
			chat._check_thread_access(self.thread)

	def test_an_admin_sees_everything(self):
		frappe.set_user(ADMIN)
		chat._check_thread_access(self.thread)  # no throw

	def test_scope_follows_the_team_not_the_role(self):
		"""Put the manager on the desk and the same conversation becomes theirs."""
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Excom Team", TEAM)
		doc.append("members", {"user": MANAGER, "role": "Member"})
		doc.flags.ignore_permissions = True
		doc.save()
		frappe.db.commit()
		try:
			frappe.set_user(MANAGER)
			chat._check_thread_access(self.thread)  # no throw
		finally:
			frappe.set_user("Administrator")
			doc = frappe.get_doc("Excom Team", TEAM)
			for row in [m for m in doc.members if m.user == MANAGER]:
				doc.remove(row)
			doc.flags.ignore_permissions = True
			doc.save()
			frappe.db.commit()
