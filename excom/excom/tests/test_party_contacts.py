"""The several people on one deal.

Two things are worth testing here and the figures are not among them. The first is that a contact
added from Excom becomes *reachable* — the whole reason for putting a person on a record rather than
in a note is that somebody can ring them, and that depends on a hook firing that this module does not
own. The second is that the panel does not claim a conversation belongs to somebody it does not: a
contact added to a lead joins the lead's existing identity without taking it over, so "open chat" on
that row would open the wrong person's thread.

Permissions are checked through a record the caller cannot touch, because "can edit this lead" is the
only thing standing between an agent and every contact in the site.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.api import party_contacts as pc
from excom.excom.services import crm_gateway as gw
from excom.excom.tests.fixtures import purge_user

AGENT = "qa.pc.agent@example.com"
STRANGER = "qa.pc.stranger@example.com"
USERS = (AGENT, STRANGER)

# Two ways of writing one telephone, which is how they arrive in real life.
NUMBER = "+919900000881"
SAME_NUMBER_TYPED_DIFFERENTLY = "09900000881"
OTHER_NUMBER = "+919900000882"


def _cleanup():
	frappe.set_user("Administrator")
	for contact in frappe.get_all("Contact", {"first_name": ["like", "QA PC%"]}, pluck="name"):
		frappe.db.delete("Omni Identity Link", {"linked_doctype": "Contact", "linked_name": contact})
		frappe.delete_doc("Contact", contact, force=True, ignore_permissions=True)
	for lead in frappe.get_all(gw.LEAD, {"lead_name": ["like", "QA PC%"]}, pluck="name"):
		frappe.db.delete("Dynamic Link", {"link_doctype": gw.LEAD, "link_name": lead})
		for oi in frappe.get_all(
			"Omni Identity Link", {"linked_doctype": gw.LEAD, "linked_name": lead}, pluck="parent"
		):
			_purge_identity(oi)
		frappe.delete_doc(gw.LEAD, lead, force=True, ignore_permissions=True)
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA PC%"]}, pluck="name"):
		_purge_identity(oi)
	for u in USERS:
		purge_user(u)
	frappe.db.commit()


def _purge_identity(oi: str) -> None:
	if not frappe.db.exists("Omni Identity", oi):
		return
	for thread in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
		frappe.db.delete("Excom Message", {"thread": thread})
		frappe.delete_doc("Excom Thread", thread, force=True, ignore_permissions=True)
	for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
		frappe.db.delete(child, {"parent": oi})
	frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)


LEAD_NUMBER = "+919900000880"


def _lead(title: str, mobile_no: str = LEAD_NUMBER) -> str:
	"""A lead with a telephone, as one really arrives.

	The number matters to these tests: ERPNext builds a Contact of its own out of the lead's name and
	number, so a lead without one produces a contact nobody can ring and the panel under test would be
	exercised against something that does not happen in practice.
	"""
	doc = frappe.get_doc({
		"doctype": gw.LEAD, "lead_name": title, "first_name": title, "mobile_no": mobile_no,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _user(email: str, roles: list[str]) -> None:
	purge_user(email)
	doc = frappe.get_doc({
		"doctype": "User", "email": email, "first_name": email.split("@")[0],
		"send_welcome_email": 0,
	})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.add_roles(*roles)


class TestPartyContacts(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		# What a real agent here carries: the Excom role for the inbox, Sales User for the CRM record.
		# Sales Master Manager on top, because a lead nobody has been handed is visible to that role
		# and no other, and these tests work on a lead nobody has been handed.
		_user(AGENT, ["Excom Agent", "Sales User", "Sales Master Manager"])
		_user(STRANGER, ["Excom Agent", "Sales User"])
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		# FrappeTestCase rolls back once per class, so anything a sibling test left behind is still
		# here. Each test starts from a lead of its own.
		frappe.set_user("Administrator")
		self.lead = _lead("QA PC Spices Ltd")
		frappe.db.commit()
		# ERPNext puts a Contact of its own on every lead, built from the lead's name and number. It
		# belongs on the panel and is asserted on its own below; the rest of these tests are about the
		# people an agent adds, so they are counted apart from it.
		self.erpnext_own = {r["name"] for r in self._list()}

	def tearDown(self):
		frappe.set_user("Administrator")
		for contact in frappe.get_all("Contact", {"first_name": ["like", "QA PC%"]}, pluck="name"):
			frappe.db.delete("Omni Identity Link", {"linked_doctype": "Contact", "linked_name": contact})
			frappe.delete_doc("Contact", contact, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _add(self, first_name, **kw):
		return pc.add_contact(doctype=gw.LEAD, name=self.lead, first_name=first_name, **kw)

	def _list(self):
		return pc.list_contacts(doctype=gw.LEAD, name=self.lead)

	def _added(self):
		"""The panel without the contact ERPNext made for itself."""
		return [r for r in self._list() if r["name"] not in self.erpnext_own]

	# ── the deal has several people on it ─────────────────────────────────────

	def test_a_lead_arrives_with_its_own_person_already_on_it(self):
		"""ERPNext makes a Contact out of the lead itself, and the panel shows it.

		Worth pinning down rather than filtering out: it means the panel is never empty on a real lead,
		and the person an agent has actually been speaking to is the first row they see.
		"""
		frappe.set_user(AGENT)
		rows = self._list()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["mobile_no"], LEAD_NUMBER)

	def test_everybody_added_comes_back(self):
		"""The point of the whole feature: several people, one deal."""
		frappe.set_user(AGENT)
		self._add("QA PC Buyer", mobile_no=NUMBER, designation="Purchase Manager")
		self._add("QA PC Owner", mobile_no=OTHER_NUMBER, designation="Owner")
		self._add("QA PC Accounts", email_id="qa.pc.accounts@example.com")

		rows = self._added()
		self.assertEqual(len(rows), 3)
		self.assertEqual(
			{r["designation"] for r in rows}, {"Purchase Manager", "Owner", ""}
		)
		# The lead's own person is still there alongside them, which is the whole thread of people.
		self.assertEqual(len(self._list()), 4)

	def test_the_main_contact_is_listed_first(self):
		frappe.set_user(AGENT)
		self._add("QA PC Zzz Buyer", mobile_no=NUMBER)
		self._add("QA PC Aaa Owner", mobile_no=OTHER_NUMBER, is_primary=1)

		rows = self._list()
		self.assertTrue(rows[0]["is_primary"])
		self.assertIn("Owner", rows[0]["full_name"])

	def test_a_contact_added_elsewhere_shows_up_here_too(self):
		"""Excom reads the same links the Desk form writes, so a contact added in Desk is not
		invisible here."""
		frappe.set_user("Administrator")
		doc = frappe.get_doc({
			"doctype": "Contact", "first_name": "QA PC From Desk",
			"phone_nos": [{"phone": OTHER_NUMBER, "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": gw.LEAD, "link_name": self.lead}],
		})
		doc.insert(ignore_permissions=True)

		frappe.set_user(AGENT)
		self.assertEqual([r["name"] for r in self._added()], [doc.name])

	# ── reachable, which is the reason for adding them ────────────────────────

	def test_somebody_added_here_becomes_reachable(self):
		"""A contact with a number must end up on an identity, or the panel is an address book.

		This leans on `Contact.after_insert` → identity sync, which this module does not own. If that
		hook ever stops running, the feature quietly turns into a list of names nobody can ring, and
		this is the test that says so.
		"""
		frappe.set_user(AGENT)
		res = self._add("QA PC Reachable", mobile_no=NUMBER)

		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.db.exists(
				"Omni Identity Link",
				{"linked_doctype": "Contact", "linked_name": res["contact"]},
			),
			"the contact was added but no identity links to them, so nobody can be reached",
		)

	def test_the_number_comes_back_on_the_row(self):
		"""The row is what a call button is wired to, so the number has to be on it."""
		frappe.set_user(AGENT)
		self._add("QA PC Dialable", mobile_no=NUMBER)
		self.assertEqual(self._added()[0]["mobile_no"], NUMBER)

	def test_a_person_nobody_can_reach_is_refused(self):
		frappe.set_user(AGENT)
		with self.assertRaises(frappe.ValidationError):
			self._add("QA PC Ghost")

	def test_a_nameless_person_is_refused(self):
		frappe.set_user(AGENT)
		with self.assertRaises(frappe.ValidationError):
			self._add("", mobile_no=NUMBER)

	# ── whose conversation is whose ───────────────────────────────────────────

	def test_a_contact_who_joined_somebody_elses_identity_is_not_offered_that_chat(self):
		"""The honesty test for the panel.

		Excom attaches a new contact to the lead's existing identity and leaves that identity's number
		alone — so the thread there belongs to whoever was on it first. Reporting
		`own_conversation` true would put an agent into the wrong person's conversation, and a wrong
		thread is worse than no button.
		"""
		frappe.set_user("Administrator")
		identity = frappe.get_doc({
			"doctype": "Omni Identity", "display_name": "QA PC First Person",
			"primary_phone": OTHER_NUMBER,
			"linked_entities": [{"linked_doctype": gw.LEAD, "linked_name": self.lead}],
		})
		identity.flags.ignore_validate = True
		identity.insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(AGENT)
		# A different telephone from the identity's.
		self._add("QA PC Second Person", mobile_no=NUMBER)

		row = next(r for r in self._list() if "Second" in r["full_name"])
		self.assertIsNotNone(
			row["omni_identity"], "the contact should still be attached to the lead's identity"
		)
		self.assertFalse(
			row["own_conversation"],
			"that thread is the first person's; offering it here would open the wrong chat",
		)

	def test_a_contact_whose_own_number_is_on_the_identity_does_get_the_chat(self):
		frappe.set_user(AGENT)
		res = self._add("QA PC Only Person", mobile_no=NUMBER)

		frappe.set_user("Administrator")
		identity = frappe.db.get_value(
			"Omni Identity Link",
			{"linked_doctype": "Contact", "linked_name": res["contact"]},
			"parent",
		)
		frappe.db.set_value("Omni Identity", identity, "primary_phone", NUMBER)
		frappe.db.commit()

		frappe.set_user(AGENT)
		row = self._added()[0]
		self.assertTrue(row["own_conversation"])

	def test_the_same_telephone_written_differently_still_counts_as_theirs(self):
		"""+91 99000 00881 and 09900000881 are one telephone, and a panel that thinks otherwise
		refuses to offer a chat that does exist."""
		frappe.set_user(AGENT)
		res = self._add("QA PC Formats", mobile_no=SAME_NUMBER_TYPED_DIFFERENTLY)

		frappe.set_user("Administrator")
		identity = frappe.db.get_value(
			"Omni Identity Link",
			{"linked_doctype": "Contact", "linked_name": res["contact"]},
			"parent",
		)
		frappe.db.set_value("Omni Identity", identity, "primary_phone", NUMBER)
		frappe.db.commit()

		frappe.set_user(AGENT)
		self.assertTrue(self._added()[0]["own_conversation"])

	# ── one person, not two records of them ───────────────────────────────────

	def test_a_number_already_on_file_is_linked_rather_than_duplicated(self):
		frappe.set_user("Administrator")
		first = frappe.get_doc({
			"doctype": "Contact", "first_name": "QA PC Known Person",
			"phone_nos": [{"phone": NUMBER, "is_primary_mobile_no": 1}],
		})
		first.insert(ignore_permissions=True)
		other_lead = _lead("QA PC Other Company")
		frappe.db.commit()

		frappe.set_user(AGENT)
		res = pc.add_contact(
			doctype=gw.LEAD, name=other_lead, first_name="QA PC Known Person", mobile_no=NUMBER
		)
		self.assertTrue(res["reused"])
		self.assertEqual(res["contact"], first.name)

	def test_the_same_telephone_in_another_format_is_still_the_same_person(self):
		frappe.set_user(AGENT)
		created = self._add("QA PC One Person", mobile_no=NUMBER)

		other_lead = _lead("QA PC Second Company")
		res = pc.add_contact(
			doctype=gw.LEAD, name=other_lead, first_name="QA PC One Person",
			mobile_no=SAME_NUMBER_TYPED_DIFFERENTLY,
		)
		self.assertEqual(res["contact"], created["contact"])

	def test_adding_the_same_person_to_the_same_lead_twice_says_so(self):
		"""Not an error, but not a silent success either — an agent must not think they added
		somebody new."""
		frappe.set_user(AGENT)
		self._add("QA PC Twice", mobile_no=NUMBER)
		res = self._add("QA PC Twice", mobile_no=NUMBER)

		self.assertTrue(res["already_here"])
		self.assertEqual(len(self._added()), 1)

	def test_a_different_number_that_ends_the_same_way_is_a_different_person(self):
		"""The tail match is a shortlist, not the answer."""
		frappe.set_user(AGENT)
		self._add("QA PC Tail One", mobile_no="+919900000881")
		res = self._add("QA PC Tail Two", mobile_no="+441900000881")

		self.assertFalse(res["reused"])
		self.assertEqual(len(self._added()), 2)

	# ── changing a person ─────────────────────────────────────────────────────

	def test_editing_one_field_leaves_the_other_alone(self):
		"""A form that saves the role must not clear the main-contact flag on the way."""
		frappe.set_user(AGENT)
		res = self._add("QA PC Editable", mobile_no=NUMBER, designation="Owner", is_primary=1)

		pc.update_contact(doctype=gw.LEAD, name=self.lead, contact=res["contact"], designation="Director")
		row = self._added()[0]
		self.assertEqual(row["designation"], "Director")
		self.assertTrue(row["is_primary"], "the main-contact flag was cleared by an unrelated edit")

		pc.update_contact(doctype=gw.LEAD, name=self.lead, contact=res["contact"], is_primary=0)
		row = self._added()[0]
		self.assertEqual(row["designation"], "Director", "the role was cleared by an unrelated edit")
		self.assertFalse(row["is_primary"])

	def test_a_contact_on_another_record_cannot_be_edited_through_this_one(self):
		"""Otherwise write permission on one lead is write permission on every contact in the site."""
		frappe.set_user("Administrator")
		outsider = frappe.get_doc({
			"doctype": "Contact", "first_name": "QA PC Outsider",
			"phone_nos": [{"phone": OTHER_NUMBER, "is_primary_mobile_no": 1}],
		})
		outsider.insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(AGENT)
		with self.assertRaises(frappe.PermissionError):
			pc.update_contact(
				doctype=gw.LEAD, name=self.lead, contact=outsider.name, designation="Hijacked"
			)
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.get_value("Contact", outsider.name, "designation"))

	# ── removing a person ─────────────────────────────────────────────────────

	def test_removing_somebody_takes_them_off_the_deal_and_no_further(self):
		frappe.set_user(AGENT)
		res = self._add("QA PC Leaving", mobile_no=NUMBER)
		other_lead = _lead("QA PC Keeps Them")
		pc._link(res["contact"], gw.LEAD, other_lead)

		out = pc.unlink_contact(doctype=gw.LEAD, name=self.lead, contact=res["contact"])
		self.assertTrue(out["changed"])
		self.assertEqual(self._added(), [])

		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.db.exists("Contact", res["contact"]), "the person was deleted, not unlinked"
		)
		self.assertTrue(
			frappe.db.exists(
				"Dynamic Link",
				{"parenttype": "Contact", "parent": res["contact"], "link_name": other_lead},
			),
			"their other record lost them too",
		)

	def test_removing_somebody_who_is_not_there_changes_nothing(self):
		frappe.set_user("Administrator")
		outsider = frappe.get_doc({
			"doctype": "Contact", "first_name": "QA PC Not Here",
			"phone_nos": [{"phone": OTHER_NUMBER, "is_primary_mobile_no": 1}],
		})
		outsider.insert(ignore_permissions=True)

		frappe.set_user(AGENT)
		out = pc.unlink_contact(doctype=gw.LEAD, name=self.lead, contact=outsider.name)
		self.assertFalse(out["changed"])

	def test_only_a_crm_record_is_accepted(self):
		"""The doctype arrives from the browser, so it is checked against the gateway rather than
		trusted — otherwise this reads any table in the site that has contacts hanging off it."""
		frappe.set_user(AGENT)
		with self.assertRaises(frappe.ValidationError):
			pc.list_contacts(doctype="User", name="Administrator")

	def test_a_record_that_does_not_exist_says_so(self):
		frappe.set_user(AGENT)
		with self.assertRaises(frappe.DoesNotExistError):
			pc.list_contacts(doctype=gw.LEAD, name="QA-PC-NO-SUCH-LEAD")


class TestWhoMayLook(FrappeTestCase):
	"""Somebody else's lead.

	The contact panel is a second door onto a CRM record, and a second door is where a permission rule
	gets left out. Both agents here are real, fully-roled agents — the only difference is that the
	lead was never handed to one of them, which is the case the rule exists for.

	The suite runner pins `enforce_crm_visibility` off, so it is switched on for these tests and put
	back afterwards. Without it every agent can read every lead and these assertions would pass
	against a missing check.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		_user(AGENT, ["Excom Agent", "Sales User", "Sales Master Manager"])
		_user(STRANGER, ["Excom Agent", "Sales User"])
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = _lead("QA PC Somebody Elses Deal")
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 1)
		frappe.clear_cache()
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
		frappe.clear_cache()
		for contact in frappe.get_all("Contact", {"first_name": ["like", "QA PC%"]}, pluck="name"):
			frappe.db.delete("Omni Identity Link", {"linked_doctype": "Contact", "linked_name": contact})
			frappe.delete_doc("Contact", contact, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_the_switch_is_really_on_for_these_tests(self):
		"""Named and asserted, because if the switch silently stayed off the two tests below would
		pass whether the endpoints check anything or not."""
		from excom.excom.services import crm_visibility as vis

		self.assertFalse(vis.can_read(frappe.get_doc(gw.LEAD, self.lead), STRANGER))

	def test_a_lead_they_were_never_given_gives_its_contacts_away_to_nobody(self):
		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			pc.list_contacts(doctype=gw.LEAD, name=self.lead)

	def test_a_lead_they_were_never_given_cannot_gain_a_contact(self):
		frappe.set_user(STRANGER)
		with self.assertRaises(frappe.PermissionError):
			pc.add_contact(
				doctype=gw.LEAD, name=self.lead, first_name="QA PC Sneaked In", mobile_no=NUMBER
			)

	def test_the_agent_whose_lead_it_is_can_do_both(self):
		"""The other half of the rule. A check that refuses everybody is not a permission check."""
		frappe.set_user(AGENT)
		before = len(pc.list_contacts(doctype=gw.LEAD, name=self.lead))
		pc.add_contact(
			doctype=gw.LEAD, name=self.lead, first_name="QA PC Rightful", mobile_no=NUMBER
		)
		self.assertEqual(len(pc.list_contacts(doctype=gw.LEAD, name=self.lead)), before + 1)
