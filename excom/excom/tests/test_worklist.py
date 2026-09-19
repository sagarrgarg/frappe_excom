"""What is waiting for an agent, and whether the number can be trusted.

The inbox badge used to be a sum the browser worked out over the hundred conversations it had
loaded, so on a busy desk it was simply wrong. A task fell due and said nothing at all. A missed
call was known only to the Calls page. These hold the replacement to the one promise that matters:
a count must never offer something the list then refuses to open.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from excom.excom.api import worklist
from excom.excom.tests.fixtures import purge_user

AGENT = "qa.worklist.agent@example.com"
OTHER = "qa.worklist.other@example.com"
MY_TEAM = "QA Worklist Desk"
THEIR_TEAM = "QA Worklist Other Desk"


def _cleanup():
	frappe.set_user("Administrator")
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Worklist%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for t in frappe.get_all("ToDo", {"description": ["like", "QA Worklist%"]}, pluck="name"):
		frappe.delete_doc("ToDo", t, force=True, ignore_permissions=True)
	for team in (MY_TEAM, THEIR_TEAM):
		if frappe.db.exists("Excom Team", team):
			frappe.delete_doc("Excom Team", team, force=True, ignore_permissions=True)
	for u in (AGENT, OTHER):
		purge_user(u)
	frappe.db.commit()


class TestWorklist(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()

		for name in (MY_TEAM, THEIR_TEAM):
			frappe.get_doc({"doctype": "Excom Team", "team_name": name}).insert(ignore_permissions=True)

		for email, team in ((AGENT, MY_TEAM), (OTHER, THEIR_TEAM)):
			u = frappe.get_doc({
				"doctype": "User", "email": email, "first_name": email.split("@")[0],
				"send_welcome_email": 0,
			})
			u.flags.ignore_permissions = True
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
			doc = frappe.get_doc("Excom Team", team)
			doc.append("members", {"user": email, "role": "Member"})
			doc.flags.ignore_permissions = True
			doc.save()

		ref = frappe.get_all(
			"Excom Thread", fields=["account_doctype", "account"], limit=1
		)[0]
		cls.ref = ref

		# Two unread conversations on the agent's desk, one WhatsApp and one email, plus one on
		# somebody else's desk that must never reach his count.
		cls.threads = {}
		for key, channel, team, unread in (
			("wa", "whatsapp", MY_TEAM, 3),
			("mail", "email", MY_TEAM, 1),
			("theirs", "whatsapp", THEIR_TEAM, 9),
		):
			identity = frappe.get_doc({
				"doctype": "Omni Identity",
				"display_name": f"QA Worklist {key}",
				"primary_phone": f"+9199000{abs(hash(key)) % 90000 + 10000}",
			}).insert(ignore_permissions=True).name
			cls.threads[key] = frappe.get_doc({
				"doctype": "Excom Thread", "omni_identity": identity, "channel": channel,
				"account_doctype": ref.account_doctype, "account": ref.account,
				"thread_key": f"qa-worklist-{key}", "status": "Open",
				"assigned_team": team, "unread_count": unread,
				"last_message_at": frappe.utils.now_datetime(),
			}).insert(ignore_permissions=True).name

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")
		for t in frappe.get_all("ToDo", {"description": ["like", "QA Worklist%"]}, pluck="name"):
			frappe.delete_doc("ToDo", t, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _task(self, text, due, owner=AGENT, allocated_to=None, status="Open"):
		frappe.set_user("Administrator")
		doc = frappe.get_doc({
			"doctype": "ToDo", "description": f"QA Worklist {text}",
			"date": due, "status": status,
			"allocated_to": allocated_to,
		})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		# `owner` is set by Frappe from the session, so put it where the test needs it.
		frappe.db.set_value("ToDo", doc.name, "owner", owner, update_modified=False)
		frappe.db.commit()
		return doc.name

	# ── the counts ────────────────────────────────────────────────────────────

	def test_each_channel_is_counted_on_its_own(self):
		"""Adding them together says neither: a WhatsApp waiting and an email waiting are not the
		same kind of waiting."""
		frappe.set_user(AGENT)
		w = worklist.get_worklist()
		self.assertEqual(w["whatsapp"]["threads"], 1)
		self.assertEqual(w["whatsapp"]["messages"], 3)
		self.assertEqual(w["email"]["threads"], 1)
		self.assertEqual(w["email"]["messages"], 1)

	def test_the_count_never_offers_another_desks_work(self):
		"""The one promise: a badge must not point at something the inbox will refuse to open."""
		frappe.set_user(AGENT)
		w = worklist.get_worklist()
		self.assertEqual(
			w["unread_threads"], 2,
			"the third conversation is on another desk and must not be counted",
		)

	def test_conversations_are_counted_not_messages(self):
		"""One person sending six lines is one person waiting."""
		frappe.set_user(AGENT)
		w = worklist.get_worklist()
		self.assertEqual(w["unread_threads"], 2)
		self.assertEqual(w["whatsapp"]["messages"] + w["email"]["messages"], 4)

	# ── tasks ─────────────────────────────────────────────────────────────────

	def test_an_overdue_task_is_counted(self):
		self._task("call back", add_days(today(), -2), allocated_to=AGENT)
		frappe.set_user(AGENT)
		w = worklist.get_worklist()
		self.assertEqual(w["tasks"]["count"], 1)
		self.assertIn("call back", w["tasks"]["items"][0]["description"])

	def test_a_task_the_agent_made_for_themselves_is_theirs(self):
		"""The Tasks tab creates a ToDo with no assignee at all. Asking only for `allocated_to`
		loses exactly the tasks somebody set themselves."""
		self._task("follow up", add_days(today(), -1), owner=AGENT, allocated_to=None)
		frappe.set_user(AGENT)
		self.assertEqual(worklist.get_worklist()["tasks"]["count"], 1)

	def test_a_task_due_later_is_not_overdue(self):
		self._task("later", add_days(today(), 3), allocated_to=AGENT)
		frappe.set_user(AGENT)
		self.assertEqual(worklist.get_worklist()["tasks"]["count"], 0)

	def test_a_finished_task_is_not_waiting(self):
		self._task("done already", add_days(today(), -5), allocated_to=AGENT, status="Closed")
		frappe.set_user(AGENT)
		self.assertEqual(worklist.get_worklist()["tasks"]["count"], 0)

	def test_another_persons_task_is_not_counted(self):
		self._task("not mine", add_days(today(), -2), owner=OTHER, allocated_to=OTHER)
		frappe.set_user(AGENT)
		self.assertEqual(worklist.get_worklist()["tasks"]["count"], 0)

	# ── the tasks page ────────────────────────────────────────────────────────

	def test_every_task_is_reachable_without_opening_its_conversation(self):
		"""The whole point of the page: a task used to be findable only from inside the
		conversation it hung off."""
		self._task("overdue one", add_days(today(), -2), allocated_to=AGENT)
		self._task("due today", today(), allocated_to=AGENT)
		self._task("next week", add_days(today(), 7), allocated_to=AGENT)
		frappe.set_user(AGENT)

		self.assertEqual(len(worklist.get_my_tasks(view="open")), 3)
		self.assertEqual(len(worklist.get_my_tasks(view="overdue")), 1)
		self.assertEqual(len(worklist.get_my_tasks(view="today")), 1)
		self.assertEqual(len(worklist.get_my_tasks(view="upcoming")), 1)

	def test_finished_tasks_have_their_own_view(self):
		self._task("closed one", add_days(today(), -2), allocated_to=AGENT, status="Closed")
		frappe.set_user(AGENT)
		self.assertEqual(len(worklist.get_my_tasks(view="open")), 0)
		self.assertEqual(len(worklist.get_my_tasks(view="done")), 1)

	def test_the_list_says_which_contact_each_task_is_about(self):
		"""So the page can be read without opening every row."""
		frappe.set_user("Administrator")
		identity = frappe.db.get_value("Omni Identity", {"display_name": "QA Worklist wa"}, "name")
		name = self._task("about someone", add_days(today(), -1), allocated_to=AGENT)
		frappe.db.set_value("ToDo", name, {
			"reference_type": "Omni Identity", "reference_name": identity,
		}, update_modified=False)
		frappe.db.commit()

		frappe.set_user(AGENT)
		rows = worklist.get_my_tasks(view="overdue")
		self.assertEqual(rows[0]["reference_label"], "QA Worklist wa")
		self.assertEqual(
			rows[0]["omni_identity"], identity,
			"the row has to know which conversation to open",
		)

	def test_a_task_attached_to_nothing_still_appears(self):
		"""Not every task is about a customer."""
		self._task("buy a headset", add_days(today(), -1), allocated_to=AGENT)
		frappe.set_user(AGENT)
		rows = worklist.get_my_tasks(view="overdue")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["reference_label"], "")
		self.assertIsNone(rows[0]["omni_identity"])
