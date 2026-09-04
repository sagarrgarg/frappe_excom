"""An agent must be able to do an agent's job with an agent's permissions.

Every one of these worked before only because the API passed ignore_permissions, which meant the
permission layer was decoration: the moment anything called these paths honestly, an agent was
refused. Writing a note is the clearest case — a note is a Frappe Comment, and stock Frappe lets
only a System Manager create one.

These tests run as a plain Excom User with no manager role anywhere, and the API no longer bypasses.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

AGENT = "qa.rights.agent@example.com"
TEAM = "QA Rights Desk"
OTHER = "QA Rights Other Desk"


class TestAgentRights(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._cleanup()
		for name in (TEAM, OTHER):
			frappe.get_doc({"doctype": "Excom Team", "team_name": name}).insert(ignore_permissions=True)
		u = frappe.get_doc({"doctype": "User", "email": AGENT, "first_name": "QA Rights Agent", "send_welcome_email": 0})
		u.flags.ignore_permissions = True
		u.insert(ignore_permissions=True)
		u.add_roles("Excom User")
		team = frappe.get_doc("Excom Team", TEAM)
		team.append("members", {"user": AGENT, "role": "Member"})
		team.flags.ignore_permissions = True
		team.save()
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		cls.identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Rights Buyer",
			"primary_phone": "+919900000955"}).insert(ignore_permissions=True).name
		cls.thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": cls.identity,
			"channel": ref.channel, "account_doctype": ref.account_doctype, "account": ref.account,
			"thread_key": "qa-rights-1", "status": "Open", "assigned_to": AGENT, "assigned_team": TEAM,
			"last_message_at": now_datetime()}).insert(ignore_permissions=True).name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._cleanup()
		super().tearDownClass()

	@classmethod
	def _cleanup(cls):
		for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Rights%"]}, pluck="name"):
			frappe.db.delete("Excom Message", {"omni_identity": oi})
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t})
				frappe.db.delete("Excom Thread Transfer Log", {"thread": t})
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for tag in frappe.get_all("Excom Tag", {"name": ["like", "QA Rights%"]}, pluck="name"):
			frappe.delete_doc("Excom Tag", tag, force=True, ignore_permissions=True)
		for name in (TEAM, OTHER):
			if frappe.db.exists("Excom Team", name):
				frappe.delete_doc("Excom Team", name, force=True, ignore_permissions=True)
		if frappe.db.exists("User", AGENT):
			frappe.delete_doc("User", AGENT, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user(AGENT)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_an_agent_can_write_and_read_a_note(self):
		from excom.excom.api import record

		out = record.add_note(reference_doctype="Excom Thread", reference_name=self.thread,
		                      content="QA Rights: customer wants a sample")
		self.assertTrue(out.get("name"))
		notes = record.get_notes(reference_doctype="Excom Thread", reference_name=self.thread)
		self.assertTrue(any("sample" in (n.get("content") or "") for n in notes))

	def test_an_agent_cannot_rewrite_somebody_elses_note(self):
		"""Notes are append-only for an agent. A manager can correct one."""
		frappe.set_user("Administrator")
		other = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment",
			"reference_doctype": "Excom Thread", "reference_name": self.thread,
			"content": "QA Rights: a manager's note"}).insert(ignore_permissions=True)
		frappe.set_user(AGENT)
		doc = frappe.get_doc("Comment", other.name)
		doc.content = "QA Rights: quietly rewritten"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_an_agent_can_tag_and_untag_a_conversation(self):
		from excom.excom.api import chat

		chat.add_thread_tag(thread_id=self.thread, tag_name="QA Rights Hot")
		tags = [t.tag for t in frappe.get_doc("Excom Thread", self.thread).tags]
		self.assertIn("QA Rights Hot", tags)
		chat.remove_thread_tag(thread_id=self.thread, tag_name="QA Rights Hot")
		tags = [t.tag for t in frappe.get_doc("Excom Thread", self.thread).tags]
		self.assertNotIn("QA Rights Hot", tags)

	def test_an_agent_can_transfer_a_conversation_and_the_log_records_it(self):
		from excom.excom.api import chat

		chat.transfer_thread(thread_id=self.thread, target_team=OTHER, note="QA Rights: wrong desk")
		self.assertEqual(frappe.db.get_value("Excom Thread", self.thread, "assigned_team"), OTHER)
		logged = frappe.get_all("Excom Thread Transfer Log", filters={"thread": self.thread}, pluck="to_team")
		self.assertIn(OTHER, logged)
		frappe.set_user("Administrator")
		frappe.db.set_value("Excom Thread", self.thread, {"assigned_team": TEAM, "assigned_to": AGENT}, update_modified=False)
		frappe.db.commit()

	def test_an_agent_can_send_a_message(self):
		from excom.excom.api import chat

		fake = {"provider_message_id": "qa-rights-1", "status": "Sent"}
		with patch("excom.excom.services.whatsapp_service.send_text_message", return_value=fake):
			out = chat.send_message(thread_id=self.thread, message="QA Rights: on its way")
		self.assertTrue(out)

	def test_an_agent_can_read_the_activity_trail(self):
		from excom.excom.api import record

		rows = record.get_activity(reference_doctype="Excom Thread", reference_name=self.thread)
		self.assertIsInstance(rows, list)
