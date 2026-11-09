"""Excom AI Phase 1H: monitoring (HLD-007 §13, §12.3). The live view, alerts, the morning digest,
the weekly coach, promotion suggestions, handover when the owner leaves, the dead-man switch when
they go quiet, and the evaluation gate that keeps a worse model or effort from going live."""

import json
from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, now_datetime

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import api, monitor
from excom.excom_ai.engine import ownership, proposals, runner, tasks

OWNER = "qa.mon.owner@example.com"
BACKUP = "qa.mon.backup@example.com"
NEWBIE = "qa.mon.newbie@example.com"
TEAM = "QA Monitor Desk"
PTYPE, PROVIDER = "_TM Scripted", "_TM Monitor"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Mon%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for name in agents:
		pbs = [r.playbook for r in frappe.get_doc("Excom AI Agent", name).playbooks]
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		for t in frappe.get_all("Excom Thread", {"ai_agent": name}, pluck="name"):
			frappe.db.set_value("Excom Thread", t, {"ai_agent": None, "ai_mode": None})
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		for pb in pbs:
			frappe.delete_doc("Excom AI Playbook", pb, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	if frappe.db.table_exists("AI Call Log"):
		frappe.db.delete("AI Call Log", {"budget_scope": ["in", [f"Excom AI Agent:{a}" for a in agents] or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-mon.example"]}, pluck="name"):
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP, NEWBIE]]})
	purge_users(OWNER, BACKUP, NEWBIE)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestMonitor(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP, NEWBIE):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [{"user": OWNER, "role": "Manager"},
			{"user": BACKUP, "role": "Member"}, {"user": NEWBIE, "role": "Member"}]}).insert(ignore_permissions=True)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}], api_base_url="main")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("User", OWNER, "enabled", 1)
		frappe.db.set_value("User", BACKUP, "enabled", 1)
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)
		frappe.cache.delete_keys("excom_ai_alert:")
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, **kw):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Mon {self.n}", "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, **kw}).insert(ignore_permissions=True)
		a.db_set({"status": "Live", "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def waiting_thread(self, agent, hours_ago):
		from excom.excom.services.thread_service import upsert_thread

		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Mon buyer {self.n}",
			"primary_email": f"b{self.n}@qa-mon.example"}).insert(ignore_permissions=True)
		account = frappe.db.get_value("Excom Channel Account", {"channel": "email"})
		thread = upsert_thread(oi.name, "email", account)
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Needs person", "status": "Open",
			"last_inbound_at": add_to_date(now_datetime(), hours=-hours_ago), "last_outbound_at": None})
		self.addCleanup(lambda: (frappe.delete_doc("Excom Thread", thread, force=True, ignore_permissions=True),
			frappe.delete_doc("Omni Identity", oi.name, force=True, ignore_permissions=True)))
		return thread

	# ---- live view and alerts

	def test_working_hours_skip_the_weekend(self):
		friday_5pm = datetime(2026, 11, 13, 17, 0)
		self.assertAlmostEqual(monitor.working_hours_waited(friday_5pm, datetime(2026, 11, 16, 9, 0)), 7 + 9, places=1)

	def test_the_live_view_and_alerts(self):
		a = self.agent(daily_ai_budget=1)
		thread = self.waiting_thread(a, hours_ago=80)
		view = monitor.live(a.name)
		self.assertEqual(view["channels"]["waiting"][0]["name"], thread)
		self.assertTrue(view["channels"]["waiting"][0]["late"])
		self.assertIn("first_email", view["health"]["modes"])
		if frappe.db.table_exists("AI Call Log"):
			frappe.get_doc({"doctype": "AI Call Log", "status": "Completed", "calling_app": "excom", "base_cost": 0.9,
				"budget_scope": f"Excom AI Agent:{a.name}"}).insert(ignore_permissions=True)
		sent = monitor.alerts(a)
		self.assertIn(f"late:{thread}", sent)
		if frappe.db.table_exists("AI Call Log"):
			self.assertIn("budget:daily", sent)
		self.assertEqual(monitor.alerts(a), [], "once a day, not every tick")
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "document_name": thread}))

	def test_the_morning_digest(self):
		a = self.agent()
		self.waiting_thread(a, hours_ago=2)
		body = monitor.digest_for(OWNER)
		self.assertIn(a.agent_name, body)
		self.assertIn("1 buyers waiting", body)
		with patch.object(monitor, "digest_due", return_value=True), patch("frappe.sendmail") as mail:
			monitor.morning_digest()
		self.assertIn(OWNER, mail.call_args.kwargs["recipients"])
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "subject": "Your AI agents this morning"}))

	# ---- the owner leaves, the owner goes quiet

	def test_the_owner_leaves_the_backup_takes_over_then_a_handover(self):
		a = self.agent()
		thread = self.waiting_thread(a, hours_ago=1)
		frappe.db.set_value("Excom Thread", thread, "assigned_to", OWNER)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA Mon lead", "email_id": f"lead{self.n}@qa-mon.example",
			"lead_owner": OWNER, "ai_agent": a.name}).insert(ignore_permissions=True)
		frappe.db.set_value("User", OWNER, "enabled", 0)
		self.assertEqual(ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name)), BACKUP, "the backup is in charge")
		frappe.set_user(OWNER)
		frappe.set_user("Administrator")
		out = api.handover(a.name, NEWBIE)
		self.assertEqual((out["threads"], out["leads"]), (1, 1))
		a.reload()
		self.assertEqual((a.owner_user, a.acting_owner), (NEWBIE, NEWBIE))
		self.assertEqual(frappe.db.get_value("Excom Thread", thread, "assigned_to"), NEWBIE)
		self.assertEqual(frappe.db.get_value("Lead", lead.name, "lead_owner"), NEWBIE)

	def test_the_owner_goes_quiet(self):
		a = self.agent()
		a.db_set("owner_last_seen", add_days(now_datetime(), -10))
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		self.assertEqual(frappe.db.get_value("Excom AI Agent", a.name, "status"), "On Hold")
		self.assertIn("working days", frappe.db.get_value("Excom AI Agent", a.name, "hold_reason"))

	# ---- promotion and the coach

	def test_promotion_is_suggested_after_twenty_clean_approvals(self):
		a = self.agent()
		self.assertEqual(monitor.promotion_suggestions(a), [])
		for _i in range(monitor.PROMOTE_AFTER):
			frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "send_email", "policy_action": "first_email",
				"status": "Sent", "decided_by": OWNER, "decided_on": now_datetime()}).insert(ignore_permissions=True)
		s = monitor.promotion_suggestions(a)
		self.assertEqual([(x["action"], x["from"], x["to"]) for x in s], [("first_email", "Review", "Auto")])
		frappe.set_user(OWNER)
		api.apply_promotion(a.name, "first_email")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Excom AI Policy", {"parent": a.name, "action": "first_email"}, "mode"), "Auto")

	def test_the_coach_suggests_playbook_changes_for_a_person(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		pb = frappe.get_doc({"doctype": "Excom AI Playbook", "title": f"QA Mon playbook {self.n}", "status": "Draft",
			"sections": [{"kind": "Facts", "content": "We make water guns."}]}).insert(ignore_permissions=True)
		a = self.agent(playbooks=[{"playbook": pb.name}])
		frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "send_email", "status": "Rejected",
			"reject_reason": "Wrong facts", "reject_note": "we do not hold EN71", "decided_by": OWNER,
			"decided_on": now_datetime()}).insert(ignore_permissions=True)
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps({"suggestions": [
			{"kind": "Banned claims", "text": "Never claim EN71.", "why": "a draft was rejected for claiming it"}]}))]}
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		t = tasks.add(a.name, "coach", dedupe_key=f"coach{self.n}")
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		with patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt)):
			runner.run_task(t)
		self.assertIn("1 playbook suggestions", frappe.db.get_value("Excom AI Task", t, "outcome"))
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "update_playbook"})
		self.assertEqual((p.status, p.policy_action), ("Pending", "playbook_change"))
		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(p.name, "approve"), "Done")
		frappe.set_user("Administrator")
		pb.reload()
		self.assertTrue(any(s.kind == "Banned claims" and "EN71" in s.content and s.source == "Coach" for s in pb.sections))

	# ---- the evaluation gate

	def test_a_worse_model_is_refused_by_the_evaluation_set(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		from ai_interface.services import evals as ai_evals

		from excom.excom_ai import evals
		from excom.excom_ai.builder import brief

		evals.seed()
		a = self.agent()
		frappe.db.set_value("AI Eval Set", evals.SET, "last_score", 70)
		with patch.object(brief, "ai_ready", return_value=True), patch.object(ai_evals, "gate", return_value=False) as gate:
			a.model = "cheaper-model"
			with self.assertRaises(frappe.ValidationError):
				a.save(ignore_permissions=True)
		self.assertIn("cheaper-model", gate.call_args.kwargs["notes"])
		a.reload()
		with patch.object(brief, "ai_ready", return_value=True), patch.object(ai_evals, "gate", return_value=True):
			a.model = "better-model"
			a.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Excom AI Agent", a.name, "model"), "better-model")
