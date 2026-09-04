"""A lead nobody has been given to is visible to a Sales Master Manager and to nobody else.

It reaches other people three ways: a Sales Master Manager hands it over, a sales head places it on
their team, or auto-assignment does. These tests hold that line at the layer Desk, reports and the
REST API all go through, because the Excom UI is not the only way into a lead.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import crm_gateway as gw
from excom.excom.services import crm_visibility as vis

PARENT, CHILD, OTHER = "QA Vis Parent", "QA Vis Child", "QA Vis Other"
SMM = "qa.vis.smm@example.com"
HEAD = "qa.vis.head@example.com"
MEMBER = "qa.vis.member@example.com"
STRANGER = "qa.vis.stranger@example.com"
USERS = (SMM, HEAD, MEMBER, STRANGER)


def _user(email: str, roles: list[str]) -> None:
	doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.add_roles(*roles)


def _team(name: str, parent: str | None, members: list[tuple[str, str]]) -> None:
	doc = frappe.get_doc({"doctype": "Excom Team", "team_name": name, "parent_team": parent})
	for user, role in members:
		doc.append("members", {"user": user, "role": role})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _lead(title: str, **kwargs) -> str:
	doc = frappe.get_doc({"doctype": gw.LEAD, "lead_name": title, "first_name": title, **kwargs})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _visible(user: str) -> set[str]:
	frappe.set_user(user)
	try:
		return {r.name for r in frappe.get_list(gw.LEAD, filters={"lead_name": ["like", "QA Vis%"]}, fields=["name"], limit_page_length=0)}
	finally:
		frappe.set_user("Administrator")


class TestCrmVisibility(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._cleanup()
		_user(SMM, ["Sales Master Manager"])
		_user(HEAD, ["Excom User"])
		_user(MEMBER, ["Excom User"])
		_user(STRANGER, ["Excom User"])
		_team(PARENT, None, [(HEAD, "Manager")])
		_team(CHILD, PARENT, [(MEMBER, "Member")])
		_team(OTHER, None, [(STRANGER, "Member")])
		cls.unassigned = _lead("QA Vis Unassigned")
		# ERPNext stamps lead_owner with whoever inserted the row; a lead that arrived from a website
		# form or a marketplace has nobody on it, which is the state this whole rule is about.
		frappe.db.set_value(gw.LEAD, cls.unassigned, {"lead_owner": None, "excom_team": None}, update_modified=False)
		cls.on_child = _lead("QA Vis On Child Team", excom_team=CHILD)
		cls.owned = _lead("QA Vis Owned By Stranger", lead_owner=STRANGER)
		frappe.db.set_value(gw.LEAD, cls.on_child, {"lead_owner": None}, update_modified=False)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._cleanup()
		super().tearDownClass()

	@classmethod
	def _cleanup(cls):
		# The lead hooks create an Omni Identity and a Contact, and both hold the lead down.
		for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Vis%"]}, pluck="name"):
			frappe.db.delete("Omni Identity Link", {"parent": oi})
			frappe.db.delete("Omni Identity Channel", {"parent": oi})
			frappe.db.delete("Omni Identity Alias", {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for name in frappe.get_all(gw.LEAD, filters={"lead_name": ["like", "QA Vis%"]}, pluck="name"):
			frappe.db.delete("ToDo", {"reference_name": name})
			frappe.db.delete("Comment", {"reference_doctype": gw.LEAD, "reference_name": name})
			frappe.db.delete("Excom Stage Change Log", {"ref_name": name})
			for c in frappe.get_all("Dynamic Link", {"link_name": name, "link_doctype": gw.LEAD, "parenttype": "Contact"}, pluck="parent"):
				frappe.db.delete("Dynamic Link", {"parent": c})
				frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc(gw.LEAD, name, force=1, ignore_permissions=True, delete_permanently=True)
		for team in (CHILD, PARENT, OTHER):
			if frappe.db.exists("Excom Team", team):
				frappe.delete_doc("Excom Team", team, force=1, ignore_permissions=True)
		for user in USERS:
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=1, ignore_permissions=True)
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
		frappe.db.commit()

	def setUp(self):
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 1)
		frappe.clear_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)

	def test_an_unassigned_lead_is_visible_to_the_sales_master_manager_only(self):
		self.assertIn(self.unassigned, _visible(SMM))
		for user in (HEAD, MEMBER, STRANGER):
			self.assertNotIn(self.unassigned, _visible(user), f"{user} should not see an unplaced lead")

	def test_a_lead_placed_on_a_team_reaches_that_team(self):
		self.assertIn(self.on_child, _visible(MEMBER))

	def test_a_sales_head_sees_the_teams_beneath_theirs(self):
		self.assertIn(self.on_child, _visible(HEAD), "a manager of the parent team should see the child team's lead")
		self.assertNotIn(self.unassigned, _visible(HEAD))

	def test_a_neighbouring_team_sees_nothing(self):
		visible = _visible(STRANGER)
		self.assertNotIn(self.on_child, visible)
		self.assertNotIn(self.unassigned, visible)

	def test_an_owner_sees_their_own_lead(self):
		self.assertIn(self.owned, _visible(STRANGER))
		self.assertNotIn(self.owned, _visible(MEMBER))

	def test_the_form_view_applies_the_same_rule_as_the_list(self):
		doc = frappe.get_doc(gw.LEAD, self.unassigned)
		self.assertTrue(vis.can_read(doc, SMM))
		self.assertFalse(vis.can_read(doc, MEMBER))
		self.assertTrue(vis.can_read(frappe.get_doc(gw.LEAD, self.on_child), MEMBER))

	def test_the_switch_off_restores_erpnext_behaviour(self):
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
		frappe.clear_cache()
		self.assertEqual(vis.query_conditions(gw.LEAD, MEMBER), "")
		self.assertTrue(vis.can_read(frappe.get_doc(gw.LEAD, self.unassigned), MEMBER))

	def test_assignment_stamps_the_team_so_the_assignee_can_see_it(self):
		from frappe.desk.form.assign_to import add

		lead = _lead("QA Vis Auto Assigned")
		self.assertNotIn(lead, _visible(MEMBER))
		add({"assign_to": [MEMBER], "doctype": gw.LEAD, "name": lead, "description": "QA"}, ignore_permissions=True)
		frappe.db.commit()
		self.assertEqual(frappe.db.get_value(gw.LEAD, lead, "excom_team"), CHILD)
		self.assertIn(lead, _visible(MEMBER))
		self.assertIn(lead, _visible(HEAD), "the sales head above that team sees it too")

	def test_a_claim_from_inside_the_branch_leaves_the_team_alone(self):
		"""A sales head's placement outranks a later claim by one of their own people."""
		self.assertIsNone(vis.stamp_team(gw.LEAD, self.on_child, MEMBER))
		self.assertEqual(frappe.db.get_value(gw.LEAD, self.on_child, "excom_team"), CHILD)
		# The head runs the parent team, so the child team is inside their branch: still no move.
		self.assertIsNone(vis.stamp_team(gw.LEAD, self.on_child, HEAD))
		self.assertEqual(frappe.db.get_value(gw.LEAD, self.on_child, "excom_team"), CHILD)

	def test_handing_work_to_another_branch_moves_the_desk_with_it(self):
		"""Leaving the team behind is how a lead ends up visible to a desk that is not working it
		and invisible to the head of the desk that is."""
		lead = _lead("QA Vis Crossing Desks", excom_team=CHILD)  # _cleanup() removes it with the rest
		if True:
			self.assertEqual(vis.stamp_team(gw.LEAD, lead, STRANGER), OTHER)
			self.assertEqual(frappe.db.get_value(gw.LEAD, lead, "excom_team"), OTHER)
			self.assertIn(lead, _visible(STRANGER), "the desk now working it can see it")
			self.assertNotIn(lead, _visible(MEMBER), "the desk that let it go cannot")
			note = frappe.get_all("Comment", filters={"reference_doctype": gw.LEAD, "reference_name": lead}, pluck="content")
			self.assertTrue(any("Moved from" in (c or "") for c in note), "the move is recorded on the record")
