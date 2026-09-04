"""A user who has the Excom User role but belongs to no team sees an empty inbox and cannot
open or answer anything. Granting the role therefore puts the agent in the shared inbox."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

USER = "qa.onboard.agent@example.com"


def _drop_from_teams(user: str) -> None:
	for team in frappe.get_all("Excom Team", pluck="name"):
		doc = frappe.get_doc("Excom Team", team)
		rows = [m for m in doc.members if m.user == user]
		for r in rows:
			doc.remove(r)
		if rows:
			doc.flags.ignore_permissions = True
			doc.save()


class TestAgentOnboarding(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.tearDown()
		u = frappe.get_doc({"doctype": "User", "email": USER, "first_name": "QA Onboard", "send_welcome_email": 0})
		u.flags.ignore_permissions = True
		u.insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		_drop_from_teams(USER)
		if frappe.db.exists("User", USER):
			frappe.delete_doc("User", USER, force=1, ignore_permissions=True)
		frappe.db.commit()

	def test_granting_the_agent_role_joins_the_shared_inbox(self):
		from excom.excom.api import admin

		res = admin.set_user_roles(user=USER, roles=json.dumps(["Excom User"]))
		self.assertEqual(res["added_to_team"], "General")
		self.assertTrue(frappe.db.exists("Excom Team Member", {"parent": "General", "user": USER}))

	def test_a_second_grant_does_not_move_an_existing_member(self):
		from excom.excom.api import admin

		team = frappe.get_doc("Excom Team", "General")
		team.append("members", {"user": USER, "role": "Manager"})
		team.flags.ignore_permissions = True
		team.save()
		res = admin.set_user_roles(user=USER, roles=json.dumps(["Excom User"]))
		self.assertIsNone(res["added_to_team"])
		self.assertEqual(frappe.db.get_value("Excom Team Member", {"parent": "General", "user": USER}, "role"), "Manager")

	def test_an_agent_in_a_team_can_read_the_inbox(self):
		from excom.excom.api import admin, chat

		admin.set_user_roles(user=USER, roles=json.dumps(["Excom User"]))
		frappe.db.commit()
		frappe.set_user(USER)
		try:
			rows = chat.get_threads(limit=5)
		finally:
			frappe.set_user("Administrator")
		self.assertIsInstance(rows, list)
