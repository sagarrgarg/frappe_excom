"""Excom AI Phase 4: the learning loop. Playbook versions (the last approved one stays in use while a
newer one is reviewed), subject-line tests, and the cost check against the evaluation set."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import learning, writer
from excom.excom_ai.engine import prompts

OWNER = "qa.learn.owner@example.com"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Learn%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for name in agents:
		pbs = [r.playbook for r in frappe.get_doc("Excom AI Agent", name).playbooks]
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		for pb in pbs:
			frappe.delete_doc("Excom AI Playbook", pb, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for seq in frappe.get_all("Excom AI Sequence", {"title": ["like", "QA Learn%"]}, pluck="name"):
		frappe.delete_doc("Excom AI Sequence", seq, force=True, ignore_permissions=True)
	purge_users(OWNER)
	frappe.db.commit()


class TestLearning(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		u = frappe.get_doc({"doctype": "User", "email": OWNER, "first_name": "qa.learn", "send_welcome_email": 0})
		u.insert(ignore_permissions=True)
		u.add_roles("Excom Agent", "Sales User")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Learn {self.n}", "agent_type": "Sales",
			"owner_user": OWNER}).insert(ignore_permissions=True)
		frappe.db.commit()
		return a

	# ---- playbook versions

	def test_the_approved_version_stays_in_use_while_a_new_one_is_reviewed(self):
		a = self.agent()
		pb = frappe.get_doc({"doctype": "Excom AI Playbook", "title": "QA Learn Playbook", "status": "Draft", "sections": [
			{"kind": "Process rules", "content": "Always answer within a day."}]}).insert(ignore_permissions=True)
		a.append("playbooks", {"playbook": pb.name})
		a.save(ignore_permissions=True)
		self.assertNotIn("within a day", prompts.agent_context(a), "a draft reaches no prompt")
		pb.status = "Approved"
		pb.save(ignore_permissions=True)
		self.assertEqual(pb.approved_version, pb.version)
		pb.append("sections", {"kind": "Process rules", "content": "Offer a call to every buyer.", "source": "Coach"})
		pb.save(ignore_permissions=True)
		self.assertEqual(pb.status, "In Review", "an edit goes back to review")
		context = prompts.agent_context(a)
		self.assertIn("within a day", context, "the approved version is still used")
		self.assertNotIn("Offer a call", context, "the new text waits for approval")
		self.assertIn(f"(v{pb.approved_version})", context)
		pb.status = "Approved"
		pb.save(ignore_permissions=True)
		self.assertIn("Offer a call", prompts.agent_context(a))
		pb.status = "Retired"
		pb.save(ignore_permissions=True)
		self.assertNotIn("within a day", prompts.agent_context(a))

	# ---- subject-line tests

	def test_each_buyer_gets_one_style_and_the_split_is_even(self):
		row = frappe._dict(step=1, subject_test="A short question\n\nThe product and their city\n")
		self.assertEqual(writer.subject_styles(row), ["A short question", "The product and their city"])
		picks = [writer.subject_variant(row, f"ENR-{i}") for i in range(400)]
		self.assertEqual(picks[:50], [writer.subject_variant(row, f"ENR-{i}") for i in range(50)], "the same buyer, the same style")
		self.assertTrue(140 < picks.count("A short question") < 260, picks.count("A short question"))
		self.assertIsNone(writer.subject_variant(frappe._dict(step=1, subject_test=""), "ENR-1"))

	def sequence(self, a, styles):
		return frappe.get_doc({"doctype": "Excom AI Sequence", "title": f"QA Learn Seq {self.n}", "agent": a.name,
			"steps": [{"step": 1, "wait_days": 0, "purpose": "intro", "subject_test": "\n".join(styles)}]}).insert(
			ignore_permissions=True)

	def sent(self, a, seq, style, n):
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name})
		enr.flags.ignore_validate = enr.flags.ignore_mandatory = True
		enr.insert(ignore_permissions=True)
		for _i in range(n):
			p = frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "send_email", "status": "Sent",
				"step": 1, "enrollment": enr.name, "sent_on": add_days(now_datetime(), -3),
				"payload": json.dumps({"subject": "x", "body": "y", "subject_variant": style})})
			p.flags.ignore_validate = p.flags.ignore_mandatory = True
			p.insert(ignore_permissions=True)

	def test_results_per_style(self):
		a = self.agent()
		seq = self.sequence(a, ["Question", "Product"])
		self.sent(a, seq, "Question", 3)
		self.sent(a, seq, "Product", 2)
		res = {r["style"]: r for r in learning.subject_results(a.name)}
		self.assertEqual((res["Question"]["sent"], res["Product"]["sent"]), (3, 2))
		self.assertEqual(res["Question"]["answered"], 0, "no thread, no reply")

	def test_a_clear_winner_is_proposed_and_kept(self):
		a = self.agent()
		seq = self.sequence(a, ["Question", "Product"])
		results = [{"sequence": seq.name, "step": 1, "style": "Question", "sent": 60, "answered": 15},
			{"sequence": seq.name, "step": 1, "style": "Product", "sent": 60, "answered": 3}]
		with patch.object(learning, "subject_results", return_value=results):
			won = learning.winners(a.name)
			self.assertEqual(len(won), 1)
			self.assertEqual(won[0]["keep"], "Question")
			self.assertLess(won[0]["p"], 0.1)
			close = [dict(results[0], answered=5), dict(results[1], answered=4)]
		with patch.object(learning, "subject_results", return_value=close):
			self.assertEqual(learning.winners(a.name), [], "too close to call")
		few = [dict(results[0], sent=10), dict(results[1], sent=10)]
		with patch.object(learning, "subject_results", return_value=few):
			self.assertEqual(learning.winners(a.name), [], "too few sends")
		proposal = SimpleNamespace(payload=json.dumps(won[0]), subject_name=seq.name)
		learning.keep_subject(proposal, a)
		self.assertEqual(frappe.get_doc("Excom AI Sequence", seq.name).steps[0].subject_test, "Question")
		from excom.excom_ai.engine import registry

		self.assertRaises(registry.Cancel, learning.keep_subject, SimpleNamespace(
			payload=json.dumps(dict(won[0], keep="Gone")), subject_name=seq.name), a)

	def test_keeping_a_subject_is_a_persons_decision(self):
		from excom.excom_ai.constants import ceiling
		from excom.excom_ai.engine.proposals import ACTION_POLICY

		self.assertEqual(ACTION_POLICY["keep_subject"], "playbook_change")
		self.assertEqual(ceiling("playbook_change", "sales"), "Review")

	# ---- cost tuning

	def test_the_cheapest_passing_pair_is_recommended(self):
		from excom.excom_ai.monitor import _trial

		runs = {("cheap", "low"): (False, 70, 0.01), ("mid", "low"): (True, 95, 0.03), ("big", "high"): (True, 100, 0.20)}
		last = {}

		def gate(eval_set, notes=""):
			key = (frappe.flags.excom_ai_trial["model"], frappe.flags.excom_ai_trial["effort"])
			last["run"] = runs[key]
			return runs[key][0]

		if not frappe.db.exists("AI Eval Set", "Excom Sales reply intents"):
			self.skipTest("the evaluation set is not installed")
		with patch("excom.excom_ai.builder.brief.ai_ready", return_value=True), \
				patch("ai_interface.services.evals.gate", side_effect=gate), \
				patch.object(learning, "_last_run", side_effect=lambda s: ("RUN", last["run"][1], last["run"][2])):
			out = learning.cost_check([{"model": m, "effort": e} for m, e in runs])
		self.assertEqual([(r["model"], r["passes"]) for r in out], [("mid", True), ("big", True), ("cheap", False)])
		self.assertTrue(out[0].get("recommended"))
		self.assertIsNone(frappe.flags.excom_ai_trial, "the trial settings are cleared")
