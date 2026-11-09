"""Excom AI Phase 2A: the builder for every type, and Custom agents (HLD-007 §8.5).

A Custom agent is built from keywords and a description; its abilities (watch, answer, chase, draft)
all end in proposals a person approves, and its policies never go past Review.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import constants
from excom.excom_ai.builder import api, service
from excom.excom_ai.engine import ownership, runner, tasks
from excom.excom_ai.packs import custom

OWNER = "qa.custom.owner@example.com"
BACKUP = "qa.custom.backup@example.com"
TEAM = "QA Custom Desk"
KEYWORD = "qa dormant buyers"
BUYERS = [f"QA Custom Buyer {i}" for i in range(3)]
BOX = "QA Custom Box"
PTYPE = "QA Custom Scripted"
PROVIDER = "QA Custom Provider"
DESCRIPTION = "Keep an eye on buyers who went quiet and remind the desk to call them."


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Custom%"]}, pluck="name")
	agents += frappe.get_all("Excom AI Agent", {"owner_user": OWNER}, pluck="name")
	for dt in ("Excom AI Knowledge", "Excom AI Readiness Gap", "Excom AI Proposal", "Excom AI Task"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [OWNER, BACKUP]]})
	for name in set(agents):
		if not frappe.db.exists("Excom AI Agent", name):
			continue
		pbs = [r.playbook for r in frappe.get_doc("Excom AI Agent", name).playbooks]
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		for pb in pbs:
			frappe.delete_doc("Excom AI Playbook", pb, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.delete("Excom AI Recipe", {"keywords": KEYWORD})
	for thread in frappe.get_all("Excom Thread", {"account": BOX}, pluck="name"):
		frappe.db.delete("Excom Message", {"thread": thread})
		frappe.delete_doc("Excom Thread", thread, force=True, ignore_permissions=True)
	frappe.db.delete("Omni Identity", {"primary_email": ["like", "%@qa-custom.example"]})
	for b in BUYERS:
		if frappe.db.exists("Customer", b):
			frappe.delete_doc("Customer", b, force=True, ignore_permissions=True)
	for dt, name in (("Excom Channel Account", BOX), ("Excom Team", TEAM)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP)
	frappe.db.commit()


class TestCustomPack(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc("User", OWNER).add_roles("Sales Manager", "Accounts User", "Purchase User", "Stock User")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		for b in BUYERS:
			frappe.get_doc({"doctype": "Customer", "customer_name": b, "customer_type": "Company"}).insert(ignore_permissions=True)
		cls.recipe = service.accept_recipe(KEYWORD, "Buyers who went quiet", {"doctype": "Customer",
			"fields": ["name", "customer_name"], "filters": [["customer_name", "like", "QA Custom Buyer%"]]}, "Custom")
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email",
			"status": "Active", "email_address": "desk@qa-custom-ours.example"}).insert(ignore_permissions=True)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}],
				api_base_url="main")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		was = frappe.db.get_single_value("Excom AI Settings", "enabled")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", was)
		type(self).n = getattr(type(self), "n", 0) + 1

	def build(self, abilities="watch, chase, draft", agent_type="Custom", keywords=KEYWORD, description=DESCRIPTION):
		frappe.set_user(OWNER)
		try:
			with patch("excom.excom_ai.builder.brief.ai_ready", return_value=False):
				name = api.create_agent(agent_type, keywords, persona=f"QA Custom {self.n}", backup=BACKUP, team=TEAM,
					description=description, abilities=abilities)["name"]
		finally:
			frappe.set_user("Administrator")
		return frappe.get_doc("Excom AI Agent", name)

	def shadow(self, agent):
		agent.db_set({"status": "Shadow", "brief_approved_on": frappe.utils.now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", agent.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", agent.name)

	def work(self, agent, spec, **params):
		t = tasks.add(agent.name, spec["kind"], dedupe_key=f"{spec['dedupe_key']}:{self.n}:{frappe.generate_hash(length=4)}",
			**params)
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		runner.run_task(t)
		row = frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def proposals(self, agent, action="create_todo"):
		return frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": action},
			["name", "status", "subject_doctype", "subject_name", "payload"])

	# ---- building

	def test_a_custom_agent_from_keywords_and_a_description(self):
		a = self.build(abilities="chase, Watch, fly, draft")
		self.assertEqual(a.agent_type, "Custom")
		self.assertEqual(a.abilities, "chase, watch, draft", "unknown abilities dropped, order kept")
		self.assertEqual(a.keyword_rows[0].recipe, self.recipe, "the saved recipe is what the keyword means")
		pb = frappe.get_doc("Excom AI Playbook", a.playbooks[0].playbook)
		rules = next(s for s in pb.sections if s.kind == "Process rules")
		self.assertEqual(rules.content, DESCRIPTION, "the description is its process rules")
		for row in a.policies:
			self.assertNotEqual(row.mode, "Auto", f"{row.action}: a Custom agent works in Review at most")
		self.assertEqual(constants.ceiling("first_email", "custom"), "Review")
		self.assertEqual(self.build(abilities="").abilities, "watch, draft", "the default abilities")

	def test_every_type_gets_a_draft_of_its_own(self):
		for agent_type in ("Accounts", "Purchase", "Compliance"):
			a = self.build(agent_type=agent_type, abilities="answer", description="")
			self.assertEqual(a.agent_type, agent_type)
			self.assertFalse(a.abilities, "abilities are for Custom agents")
			self.assertTrue(a.what_it_is and a.mission and a.rules_do, agent_type)
			self.assertEqual(a.status, "Draft")
			self.assertTrue(a.playbooks, agent_type)

	# ---- watch and chase

	def test_watch_and_chase_in_shadow(self):
		a = self.shadow(self.build())
		specs = custom.plan(a)
		self.assertEqual({s["kind"] for s in specs}, {"custom_watch", "custom_chase"})
		self.work(a, next(s for s in specs if s["kind"] == "custom_watch"))
		found = self.proposals(a)
		self.assertEqual({p.subject_name for p in found}, set(BUYERS), "each record it finds, once")
		self.assertTrue(all(p.status == "Shadow" for p in found), [p.status for p in found])
		self.assertFalse(frappe.db.exists("ToDo", {"allocated_to": OWNER, "reference_type": "Customer"}), "Shadow does nothing")
		self.assertEqual(json.loads(found[0].payload)["allocated_to"], OWNER)
		self.work(a, next(s for s in specs if s["kind"] == "custom_watch"))
		self.assertEqual(len(self.proposals(a)), len(BUYERS), "a record already reported is not reported again")
		# chase: records already followed up this week wait
		self.work(a, next(s for s in specs if s["kind"] == "custom_chase"))
		self.assertEqual(len(self.proposals(a)), len(BUYERS))
		frappe.db.set_value("Excom AI Proposal", {"agent": a.name}, "creation", frappe.utils.add_days(frappe.utils.now(), -8))
		frappe.db.commit()
		self.work(a, next(s for s in specs if s["kind"] == "custom_chase"))
		self.assertEqual(len(self.proposals(a)), 2 * len(BUYERS), "a week later, each one is chased")

	def test_without_the_abilities_nothing_is_planned(self):
		a = self.shadow(self.build(abilities="draft"))
		self.assertEqual(custom.plan(a), [])
		a.db_set("status", "Draft")
		a = self.shadow(self.build(abilities="watch"))
		a.db_set("status", "Draft")
		self.assertEqual(custom.plan(frappe.get_doc("Excom AI Agent", a.name)), [], "a Draft agent does not work")

	# ---- draft and answer (a scripted model)

	def test_a_draft_on_request(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		from excom.excom_ai import api as agent_api

		a = self.shadow(self.build())
		frappe.set_user(OWNER)
		try:
			task = agent_api.request_draft(a.name, "A short call script for a quiet buyer", "Customer", BUYERS[0])["task"]
		finally:
			frappe.set_user("Administrator")
		frappe.db.set_value("Excom AI Task", task, "status", "Running")
		frappe.db.commit()
		with self.scripted({"title": "Call script", "text": "Hello, it has been a while."}):
			runner.run_task(task)
		self.assertEqual(frappe.db.get_value("Excom AI Task", task, "status"), "Done",
			frappe.db.get_value("Excom AI Task", task, "error"))
		notes = self.proposals(a, "note")
		self.assertEqual(len(notes), 1)
		self.assertEqual((notes[0].subject_doctype, notes[0].subject_name), ("Customer", BUYERS[0]))
		self.assertEqual(json.loads(notes[0].payload)["title"], "Call script")
		b = self.shadow(self.build(abilities="watch"))
		frappe.set_user(OWNER)
		try:
			self.assertRaises(frappe.ValidationError, agent_api.request_draft, b.name, "anything")
		finally:
			frappe.set_user("Administrator")

	def test_answers_from_the_playbook_or_hands_over(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		from excom.excom.services.thread_service import upsert_thread

		a = self.build(abilities="answer")
		a.append("channels", {"channel_account": self.box.name, "purpose": "Outreach and replies"})
		a.save(ignore_permissions=True)
		a = self.shadow(a)
		email = f"buyer{self.n}@qa-custom.example"
		identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Custom Asker",
			"primary_email": email}).insert(ignore_permissions=True).name
		thread = upsert_thread(identity, "email", self.box.name)
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": a.name, "ai_mode": "Handle"})

		def ask(text, answer):
			msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
				"account_doctype": "Excom Channel Account", "omni_identity": identity, "direction": "Inbound",
				"message_type": "Email", "content_text": text, "content_json": json.dumps({"subject": "hours"}),
				"delivery_status": "Delivered"}).insert(ignore_permissions=True)
			frappe.db.commit()
			with self.scripted(answer):
				return self.work(a, {"kind": "inbound", "dedupe_key": f"inb:{msg.name}"}, thread=thread,
					params={"message": msg.name, "channel": "email"})

		out = ask("What are your opening hours?", {"body": "We are open 9 to 6, Monday to Saturday.", "answerable": True})
		self.assertIn("answer proposed", out)
		replies = self.proposals(a, "reply")
		self.assertEqual(len(replies), 1)
		self.assertEqual(replies[0].status, "Shadow")
		out = ask("Can you ship to the moon?", {"body": "", "answerable": False})
		self.assertIn("handed to a person", out)
		self.assertEqual(len(self.proposals(a, "reply")), 1)
