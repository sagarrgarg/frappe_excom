"""One idea, two field names: Excom Thread.assigned_team and Lead/Opportunity.excom_team.

Both names are right where they sit. `assigned_team` reads properly beside `assigned_to` on a
doctype we own; on Lead we must stay prefixed, because an unprefixed custom field on a doctype we do
not own is how `customer_type` once shadowed ERPNext's own field and broke every Customer save.

What is not acceptable is the two names drifting apart silently. TEAM_FIELDS is the single registry,
and these tests hold it against the live schema: rename a field without updating the registry and a
test says so, instead of a query returning nothing at all a year from now.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.services import crm_visibility as vis


class TestTeamRegistry(FrappeTestCase):
	def test_every_registered_field_exists_and_links_to_a_team(self):
		for doctype, fieldname in vis.TEAM_FIELDS.items():
			with self.subTest(doctype=doctype):
				meta = frappe.get_meta(doctype)
				self.assertTrue(meta.has_field(fieldname), f"{doctype}.{fieldname} is in the registry but not in the schema")
				field = meta.get_field(fieldname)
				self.assertEqual(field.fieldtype, "Link")
				self.assertEqual(field.options, "Excom Team", f"{doctype}.{fieldname} must link to Excom Team")

	def test_the_registry_covers_everything_that_carries_an_owning_team(self):
		"""A Link to Excom Team on one of these doctypes means "this record belongs to that team",
		so it has to be in the registry or the visibility rule will not see it."""
		for doctype in list(vis.OWNER_FIELD) + ["Excom Thread"]:
			owning = [
				f.fieldname
				for f in frappe.get_meta(doctype).fields
				if f.fieldtype == "Link" and f.options == "Excom Team" and not f.fieldname.startswith(("from_", "to_", "parent_"))
			]
			self.assertEqual(
				sorted(owning),
				[vis.TEAM_FIELDS[doctype]],
				f"{doctype} carries a team field the registry does not name",
			)

	def test_the_accessors_do_not_care_which_name_a_doctype_uses(self):
		self.assertEqual(vis.team_field("Excom Thread"), "assigned_team")
		self.assertEqual(vis.team_field("Lead"), "excom_team")
		self.assertIsNone(vis.team_field("Excom Message"), "a doctype with no owning team returns None")
		thread = frappe._dict({"doctype": "Excom Thread", "assigned_team": "General"})
		lead = frappe._dict({"doctype": "Lead", "excom_team": "General"})
		self.assertEqual(vis.get_team(thread), vis.get_team(lead), "the same team, read two ways")
