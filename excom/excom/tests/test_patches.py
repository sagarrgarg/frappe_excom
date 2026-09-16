"""Patches have to survive the sites they will actually meet.

Excom grew out of `frappe_whatsapp`, so a site upgraded from it carries that app's tables and a site
where Excom was installed fresh does not. A patch that assumes the first shape stops `bench migrate`
dead on the second — and stops it before every Excom patch that comes after, which is how one
missing legacy table left a whole site unmigrated.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.patches.v1_0 import add_performance_indexes

LEGACY = ("WhatsApp Message", "WhatsApp Notification Log")


class TestPerformanceIndexPatch(FrappeTestCase):
	def test_a_site_that_never_had_the_whatsapp_app_still_migrates(self):
		"""The failure verbatim: `has_index` on a table that does not exist raises 1146.

		MariaDB answers a question about a missing table with an error rather than a no, so the
		patch has to ask whether the table is there before asking anything about its indexes.
		"""
		asked = []

		def table_exists(doctype, cached=True):
			return doctype not in LEGACY

		def has_index(table, index):
			asked.append(table)
			if table[len("tab"):] in LEGACY:
				raise frappe.db.ProgrammingError(
					1146, "Table 'site.%s' doesn't exist" % table
				)
			return True

		with patch.object(frappe.db, "table_exists", side_effect=table_exists), patch.object(
			frappe.db, "has_index", side_effect=has_index
		):
			add_performance_indexes.execute()   # must not raise

		for table in asked:
			self.assertNotIn(
				table[len("tab"):], LEGACY,
				"%s was asked about despite not existing — that is the 1146" % table,
			)

	def test_it_still_indexes_the_tables_that_are_there(self):
		"""Skipping the missing ones must not turn the patch into a no-op."""
		created = []

		with patch.object(frappe.db, "table_exists", return_value=True), patch.object(
			frappe.db, "has_index", return_value=False
		), patch.object(frappe.db, "sql_ddl", side_effect=lambda q: created.append(q)):
			add_performance_indexes.execute()

		self.assertTrue(created, "nothing was indexed at all")
		self.assertTrue(
			any("tabExcom Thread" in q for q in created),
			"Excom's own tables must still be indexed",
		)

	def test_an_index_that_already_exists_is_left_alone(self):
		"""Migrate re-runs patches on sites where an earlier attempt half-finished."""
		created = []

		with patch.object(frappe.db, "table_exists", return_value=True), patch.object(
			frappe.db, "has_index", return_value=True
		), patch.object(frappe.db, "sql_ddl", side_effect=lambda q: created.append(q)):
			add_performance_indexes.execute()

		self.assertEqual(created, [], "an existing index must not be created twice")
