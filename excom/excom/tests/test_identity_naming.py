"""A contact called after its own phone number, for ever.

A call creates the contact before anybody knows who it is, so it is named after the number dialled.
When the Lead arrives later with the real name it is linked to that same contact — and the name was
dropped, because the branch that attaches to an existing contact never passed it on. On the live
site that showed as "Madam Bhagya" in the Leads list and +94774465807 in the conversation next to
it.

A conversation also keeps its own copy of the name, taken when it was created, so even fixing the
contact left the header showing the number.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.doctype.omni_identity.omni_identity import (
	is_unnamed,
	name_if_unnamed,
	rename_threads,
)

PHONE = "+94774465807"


class TestWhatCountsAsNamed(FrappeTestCase):
	"""Telling a placeholder from a name is what decides whether it is safe to write over it."""

	def test_a_phone_number_is_not_a_name(self):
		self.assertTrue(is_unnamed(PHONE, PHONE))
		self.assertTrue(is_unnamed("94774465807", PHONE))
		self.assertTrue(is_unnamed("+94 77 446 5807", PHONE))

	def test_a_number_we_do_not_hold_is_still_not_a_name(self):
		"""Contacts were made from one spelling and stored under another."""
		self.assertTrue(is_unnamed("+919876543210", ""))

	def test_nothing_and_unknown_are_not_names(self):
		self.assertTrue(is_unnamed(""))
		self.assertTrue(is_unnamed("   "))
		self.assertTrue(is_unnamed("Unknown"))

	def test_an_email_standing_in_for_a_name_is_not_one(self):
		self.assertTrue(is_unnamed("a@b.com", "", "a@b.com"))

	def test_a_real_name_is_a_name(self):
		self.assertFalse(is_unnamed("Madam Bhagya", PHONE))
		self.assertFalse(is_unnamed("Sujith - Wonder chain Trading LLC", PHONE))
		# A name may legitimately carry digits.
		self.assertFalse(is_unnamed("Shop 7 Traders", PHONE))


class TestNamingAContact(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self._purge()

	def tearDown(self):
		self._purge()

	def _purge(self):
		for oi in frappe.get_all("Omni Identity", {"primary_phone": PHONE}, pluck="name"):
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _identity(self, display_name):
		return frappe.get_doc({
			"doctype": "Omni Identity",
			"display_name": display_name,
			"primary_phone": PHONE,
		}).insert(ignore_permissions=True).name

	def test_a_contact_named_after_its_number_takes_the_real_name(self):
		oi = self._identity(PHONE)
		self.assertTrue(name_if_unnamed(oi, "Madam Bhagya"))
		self.assertEqual(frappe.db.get_value("Omni Identity", oi, "display_name"), "Madam Bhagya")

	def test_a_contact_that_has_a_name_keeps_it(self):
		oi = self._identity("Madam Bhagya")
		self.assertFalse(name_if_unnamed(oi, "Keells"))
		self.assertEqual(frappe.db.get_value("Omni Identity", oi, "display_name"), "Madam Bhagya")

	def test_one_placeholder_does_not_replace_another(self):
		"""Swapping a number for a different number helps nobody."""
		oi = self._identity(PHONE)
		self.assertFalse(name_if_unnamed(oi, "+919999999999"))
		self.assertEqual(frappe.db.get_value("Omni Identity", oi, "display_name"), PHONE)


class TestTheConversationFollowsTheContact(FrappeTestCase):
	"""Every conversation caches the contact's name. Nothing used to tell it when that changed."""

	def setUp(self):
		frappe.set_user("Administrator")
		self._purge()
		self.identity = frappe.get_doc({
			"doctype": "Omni Identity", "display_name": PHONE, "primary_phone": PHONE,
		}).insert(ignore_permissions=True).name
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		self.thread = frappe.get_doc({
			"doctype": "Excom Thread", "omni_identity": self.identity,
			"channel": ref.channel, "account_doctype": ref.account_doctype, "account": ref.account,
			"thread_key": "naming-test-1", "status": "Open",
			"last_message_at": frappe.utils.now_datetime(),
		}).insert(ignore_permissions=True).name
		frappe.db.commit()

	def tearDown(self):
		self._purge()

	def _purge(self):
		for oi in frappe.get_all("Omni Identity", {"primary_phone": PHONE}, pluck="name"):
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_the_thread_starts_out_showing_the_number(self):
		self.assertEqual(
			frappe.db.get_value("Excom Thread", self.thread, "display_name"), PHONE
		)

	def test_naming_the_contact_renames_the_conversation(self):
		name_if_unnamed(self.identity, "Madam Bhagya")
		self.assertEqual(
			frappe.db.get_value("Excom Thread", self.thread, "display_name"),
			"Madam Bhagya",
			"the header would otherwise go on showing the number for ever",
		)

	def test_renaming_the_contact_directly_also_reaches_it(self):
		"""Not only through name_if_unnamed — editing the contact must work too."""
		doc = frappe.get_doc("Omni Identity", self.identity)
		doc.display_name = "Keells"
		doc.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value("Excom Thread", self.thread, "display_name"), "Keells"
		)

	def test_rename_threads_is_safe_to_call_twice(self):
		rename_threads(self.identity, "Madam Bhagya")
		rename_threads(self.identity, "Madam Bhagya")
		self.assertEqual(
			frappe.db.get_value("Excom Thread", self.thread, "display_name"), "Madam Bhagya"
		)


class TestALeadNamesTheContactACallMade(FrappeTestCase):
	"""The live shape of it.

	A call creates the contact named after the number. The Lead is created afterwards with the real
	name and is linked to that same contact. The name used to stop there: `sync_single_lead` worked
	it out and then, on the branch that attaches to an existing contact, dropped it.
	"""

	LEAD_PHONE = "+94774465899"

	def setUp(self):
		frappe.set_user("Administrator")
		self._purge()

	def tearDown(self):
		self._purge()

	def _purge(self):
		# Inserting a Lead makes Frappe create a Contact as well, and both end up linked to the
		# identity. Drop the link rows first or deleting anything is refused for being linked.
		for oi in frappe.get_all("Omni Identity", {"primary_phone": self.LEAD_PHONE}, pluck="name"):
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
		frappe.db.commit()

		for dt, field in (("Contact", "mobile_no"), ("Lead", "mobile_no")):
			for name in frappe.get_all(dt, {field: self.LEAD_PHONE}, pluck="name"):
				frappe.db.delete("Dynamic Link", {"parent": name, "parenttype": dt})
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)

		for oi in frappe.get_all("Omni Identity", {"primary_phone": self.LEAD_PHONE}, pluck="name"):
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_the_lead_names_the_contact_the_call_left_unnamed(self):
		from excom.excom.services.identity_sync import sync_single_lead

		# 1. The call got there first and had only a number to go on.
		oi = frappe.get_doc({
			"doctype": "Omni Identity",
			"display_name": self.LEAD_PHONE,
			"primary_phone": self.LEAD_PHONE,
		}).insert(ignore_permissions=True).name

		# 2. The lead arrives with the name of an actual person.
		lead = frappe.get_doc({
			"doctype": "Lead",
			"lead_name": "Madam Bhagya",
			"company_name": "Keells",
			"mobile_no": self.LEAD_PHONE,
		})
		lead.flags.ignore_permissions = True
		lead.insert(ignore_permissions=True)
		frappe.db.commit()

		sync_single_lead(lead.name)
		frappe.db.commit()

		self.assertEqual(
			frappe.db.get_value("Omni Identity", oi, "display_name"),
			"Madam Bhagya",
			"the lead knows who this is; the contact was still calling them by their number",
		)
