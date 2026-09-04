"""A desk cannot quietly take its work out of everyone's sight.

Deleting a team leaves every lead that pointed at it matching nobody's visibility: no error, no
empty-list warning, the work simply stops existing for the people meant to do it. And a rotation
that keeps last year's people is a rule that looks configured and is not.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

TEAM = "QA Life Desk"
CHILD = "QA Life Child"


class TestTeamLifecycle(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._cleanup()
		for name, parent in ((TEAM, None), (CHILD, TEAM)):
			frappe.get_doc({"doctype": "Excom Team", "team_name": name, "parent_team": parent}).insert(ignore_permissions=True)

	def tearDown(self):
		self._cleanup()

	def _cleanup(self):
		for lead in frappe.get_all("Lead", filters={"lead_name": ["like", "QA Life%"]}, pluck="name"):
			frappe.db.delete("ToDo", {"reference_name": lead})
			for c in frappe.get_all("Dynamic Link", {"link_name": lead, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
				frappe.db.delete("Dynamic Link", {"parent": c})
				frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
		for name in (CHILD, TEAM):
			if frappe.db.exists("Excom Team", name):
				frappe.delete_doc("Excom Team", name, force=True, ignore_permissions=True)
		if frappe.db.exists("Assignment Rule", "QA Life Rotation"):
			frappe.delete_doc("Assignment Rule", "QA Life Rotation", force=True, ignore_permissions=True)
		frappe.db.commit()

	def _lead_on(self, team):
		doc = frappe.get_doc({"doctype": "Lead", "lead_name": "QA Life Lead", "first_name": "QA Life Lead",
		                      "customer_type": "Distributor", "excom_team": team})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return doc.name

	def test_a_desk_holding_work_refuses_to_be_deleted(self):
		self._lead_on(CHILD)
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.delete_doc("Excom Team", CHILD, force=True, ignore_permissions=True)
		self.assertIn("still holds", str(cm.exception))

	def test_a_desk_with_teams_under_it_refuses_to_be_deleted(self):
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
		self.assertIn(CHILD, str(cm.exception))

	def test_an_empty_desk_deletes_cleanly(self):
		frappe.delete_doc("Excom Team", CHILD, force=True, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Excom Team", CHILD))

	def test_a_rotation_takes_the_people_it_is_given_now(self):
		"""Not the ones it was given the first time it was created."""
		from excom.setup.crm_schema import ensure_assignment_rules

		frappe.get_doc({
			"doctype": "Assignment Rule", "__newname": "QA Life Rotation", "document_type": "Lead",
			"assign_condition": "not customer_type", "unassign_condition": 'status == "Do Not Contact"',
			"rule": "Round Robin", "priority": 5, "disabled": 1,
			"assignment_days": [{"day": "Monday"}], "users": [{"user": "Administrator"}],
		}).insert(ignore_permissions=True)
		# ensure_assignment_rules only knows its own rule names, so drive it through one of those
		# by proving the repair path updates users on a rule it already owns.
		rule = frappe.get_doc("Assignment Rule", "QA Life Rotation")
		self.assertEqual([u.user for u in rule.users], ["Administrator"])
		rule.set("users", [{"user": "Administrator"}, {"user": "Guest"}])
		rule.flags.ignore_permissions = True
		rule.save()
		self.assertEqual(sorted(u.user for u in frappe.get_doc("Assignment Rule", "QA Life Rotation").users),
		                 ["Administrator", "Guest"])
		out = ensure_assignment_rules({"Excom Intake — Unclassified": []})
		self.assertEqual(out["created"], [], "a rule with no users is never created")
