"""Excom AI Phase 1E: sequencer and sender (HLD-007 §11.1, Appendix A). Windows in the buyer's
time zone, follow-up order and waits, caps, the company stop, paced sending, and the dry run.
Sending itself is stubbed at Excom's mail send, so nothing leaves."""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, nowdate

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import mail, sequencer
from excom.excom_ai.engine import inbound, ownership, proposals, registry, tasks

OWNER = "qa.seq.owner@example.com"
BACKUP = "qa.seq.backup@example.com"
TEAM = "QA Sequencer Desk"
BOX = "QA Sequencer Box"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Seq%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment", "Excom AI Sequence"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	comps = frappe.get_all("Excom AI Company", {"company_name": ["like", "QA Seq%"]}, pluck="name")
	frappe.db.delete("Excom AI Person", {"company": ["in", comps or ["-"]]})
	for c in comps:
		p = frappe.db.get_value("Excom AI Company", c, "prospect")
		frappe.db.delete("Excom AI Company", {"name": c})
		if p:
			for a in frappe.get_all("Dynamic Link", {"link_doctype": "Prospect", "link_name": p, "parenttype": "Address"}, pluck="parent"):
				frappe.delete_doc("Address", a, force=True, ignore_permissions=True)
			frappe.delete_doc("Prospect", p, force=True, ignore_permissions=True)
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-seq.example"]}, pluck="name"):
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	accs = frappe.get_all("Excom Channel Account", {"account_name": BOX}, pluck="name")
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, pluck="name"):
		frappe.db.delete("Excom Message", {"thread": t})
		frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for a in accs:
		frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP)
	frappe.db.commit()


class TestSequencer(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": "export@qa-seq-ours.example"}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, status="Live", warm_days=30, cap=40):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Seq {self.n}", "agent_type": "Sales",
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM,
			"markets": [{"country": "Mexico", "language": "es", "timezone": "America/Mexico_City"}],
			"channels": [{"channel_account": self.box.name, "purpose": "Outreach and replies", "daily_cap": cap,
				"warmup_start": add_days(nowdate(), -warm_days)}]}).insert(ignore_permissions=True)
		a.db_set({"status": status, "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def ready_people(self, agent, n=2, per_company=1):
		out = []
		type(self).batch = getattr(type(self), "batch", 0) + 1
		for k in range(n):
			i = f"{self.batch}-{k}"
			c = frappe.get_doc({"doctype": "Excom AI Company", "company_name": f"QA Seq Co {self.n}-{i}",
				"domain": f"co{self.n}-{i}.qa-seq.example", "country": "Mexico", "agent": agent.name, "status": "Contacts found",
				"fit_score": 90 - k}).insert(ignore_permissions=True)
			for j in range(per_company):
				out.append(frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": f"p{j}@{c.domain}",
					"is_inbox": 1, "email_status": "Verified", "status": "Ready", "language": "es"}).insert(ignore_permissions=True))
		frappe.db.commit()
		return out

	# ---- windows

	def test_windows_in_the_buyers_time(self):
		zone = "America/Mexico_City"
		with patch.object(sequencer, "get_system_timezone", return_value="Asia/Kolkata"):
			# Monday 2026-11-09 20:30 in India is 09:00 in Mexico City: inside the window
			self.assertTrue(sequencer.in_window(datetime(2026, 11, 9, 20, 30), zone))
			# Monday 10:00 in India is Sunday 22:30 in Mexico: the next window is Monday 08:00 there (19:30 India)
			nxt = sequencer.next_window(datetime(2026, 11, 9, 10, 0), zone, spread=False)
			self.assertEqual(nxt, datetime(2026, 11, 9, 19, 30))
			# Friday after hours -> Monday 08:00 there
			nxt = sequencer.next_window(datetime(2026, 11, 14, 6, 0), zone, spread=False)
			self.assertEqual((nxt.weekday(), nxt.hour, nxt.minute), (0, 19, 30))
			self.assertEqual(sequencer.add_business_days(datetime(2026, 11, 13, 20, 30), 3, zone).weekday(), 2, "Fri + 3 = Wed")

	# ---- enrolling

	def test_shadow_enrols_without_touching_the_crm_live_promotes(self):
		shadow = self.agent(status="Shadow")
		self.ready_people(shadow, n=2)
		made = sequencer.enrol_ready(shadow)
		self.assertEqual(len(made), 2)
		self.assertTrue(all(not frappe.db.get_value("Excom AI Enrollment", e, "lead") for e in made), "no Lead in Shadow")
		self.assertTrue(frappe.db.exists("Excom AI Task", {"agent": shadow.name, "kind": "write_sequence"}))
		live = self.agent()
		self.ready_people(live, n=1, per_company=2)
		made = sequencer.enrol_ready(live)
		self.assertEqual(len(made), 1, "one person per company")
		self.assertTrue(frappe.db.get_value("Excom AI Enrollment", made[0], "lead"))
		self.assertEqual(sequencer.enrol_ready(live), [], "the company is already enrolled")

	def test_enrolling_stays_within_todays_room(self):
		a = self.agent(warm_days=0, cap=40)  # first warm-up day: 5, less what agents sent from it today
		self.ready_people(a, n=7)
		room = sequencer.room_today(a)
		self.assertLessEqual(room, 5)
		self.assertEqual(len(sequencer.enrol_ready(a)), min(7, room))

	# ---- sending

	def enrolled(self, agent):
		person = self.ready_people(agent, n=1)[0]
		[enr] = sequencer.enrol_ready(agent, limit=1)
		enr = frappe.get_doc("Excom AI Enrollment", enr)
		t = frappe.get_doc("Excom AI Task", tasks.add(agent.name, "echo", dedupe_key=f"seq{self.n}"))
		props = {}
		for step in (1, 2, 3):
			props[step] = proposals.propose(t, agent, "send_email", policy_action="first_email" if step == 1 else "follow_up_email",
				payload={"subject": f"paso {step}", "body": "Hola. ¿Le interesa el catálogo?"}, enrollment=enr.name, step=step,
				kind="Sequence", to_email=person.email, channel_account=self.box.name)
		return enr, person, props

	def fake_send(self, thread, to, subject, body_html, **kw):
		m = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": frappe.db.get_value("Excom Thread", thread, "omni_identity"),
			"direction": "Outbound", "message_type": "Email", "content_text": subject, "delivery_status": "Sent",
			"created_by_user": frappe.session.user}).insert(ignore_permissions=True)
		self.sent.append((to, subject, kw.get("extra_headers")))
		return m.name

	def test_approval_waits_for_the_sender_and_follow_ups_keep_their_order(self):
		a = self.agent()
		enr, person, props = self.enrolled(a)
		frappe.set_user(OWNER)
		for step in (1, 2, 3):
			self.assertEqual(proposals.decide(props[step], "approve"), "Approved", "approving a send does not send it")
		frappe.set_user("Administrator")
		self.sent = []
		with patch("excom.excom.channels.email.outbound.send_email_reply", side_effect=self.fake_send), \
				patch.object(sequencer, "in_window", return_value=True):
			proposals.execute(props[2])
			self.assertEqual(frappe.db.get_value("Excom AI Proposal", props[2], "status"), "Approved")
			self.assertIn("step 1", frappe.db.get_value("Excom AI Proposal", props[2], "hold_reason"))
			proposals.execute(props[1])
			self.assertEqual(frappe.db.get_value("Excom AI Proposal", props[1], "status"), "Sent")
			proposals.execute(props[2])
		self.assertEqual(len(self.sent), 1, "step 2 waits its business days after step 1")
		row = frappe.db.get_value("Excom AI Proposal", props[2], ["status", "send_at", "hold_reason"], as_dict=True)
		self.assertEqual(row.status, "Approved")
		self.assertGreater(row.send_at, now_datetime())
		self.assertIn("List-Unsubscribe", self.sent[0][2])
		enr.reload()
		self.assertEqual((enr.current_step, bool(enr.thread)), (1, True))
		self.assertEqual(frappe.db.get_value("Excom Thread", enr.thread, ["ai_agent", "ai_mode"]), (a.name, "Handle"))

	def test_outside_hours_and_over_the_cap_it_waits(self):
		a = self.agent(warm_days=0, cap=40)
		enr, person, props = self.enrolled(a)
		frappe.db.set_value("Excom AI Proposal", props[1], "status", "Approved")
		with patch.object(sequencer, "in_window", return_value=False), \
				patch("excom.excom.channels.email.outbound.send_email_reply") as send:
			proposals.execute(props[1])
		send.assert_not_called()
		self.assertIn("office hours", frappe.db.get_value("Excom AI Proposal", props[1], "hold_reason"))
		with patch.object(sequencer, "in_window", return_value=True), patch.object(mail, "room", return_value=0), \
				patch("excom.excom.channels.email.outbound.send_email_reply") as send:
			proposals.execute(props[1])
		send.assert_not_called()
		self.assertIn("share for today", frappe.db.get_value("Excom AI Proposal", props[1], "hold_reason"))

	def test_a_reply_stops_the_whole_company(self):
		a = self.agent()
		enr, person, props = self.enrolled(a)
		second = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": enr.sequence, "company": enr.company,
			"status": "Active"}).insert(ignore_permissions=True)
		frappe.db.set_value("Excom AI Enrollment", enr.name, "thread", None)
		from excom.excom.services.thread_service import upsert_thread

		thread = upsert_thread(frappe.db.get_value("Omni Identity", {"primary_email": person.email}), "email", self.box.name)
		enr.db_set("thread", thread)
		frappe.db.commit()
		inbound.on_inbound({"kind": "email", "channel": "email", "thread": thread, "text": "Gracias, no por ahora",
			"message": None, "payload": {}})
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", second.name, "status"), "Replied")
		self.assertEqual(frappe.db.get_value("Excom AI Company", enr.company, "company_stop"), 1)
		frappe.db.set_value("Excom AI Proposal", props[1], "status", "Approved")
		proposals.execute(props[1])
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", props[1], "status"), "Cancelled")

	def test_send_due_is_paced(self):
		a = self.agent()
		enr, person, props = self.enrolled(a)
		frappe.db.set_value("Excom AI Proposal", props[1], "status", "Approved")
		t = tasks.add(a.name, "send_due", dedupe_key="paced")
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		self.sent = []
		with patch("excom.excom.channels.email.outbound.send_email_reply", side_effect=self.fake_send), \
				patch.object(sequencer, "in_window", return_value=True), patch("time.sleep"):
			from excom.excom_ai.engine import runner

			runner.run_task(t)
		self.assertIn("1 sent", frappe.db.get_value("Excom AI Task", t, "outcome"))
		self.assertIn("excom.excom_ai.sequencer", registry.MODULES)

	def test_dry_run_follows_windows_waits_and_the_warm_up(self):
		a = self.agent(warm_days=0, cap=40)
		self.ready_people(a, n=7)
		sequencer.enrol_ready(a, limit=7)
		plan = sequencer.dry_run(a.name, days=30)
		firsts = [p for p in plan if p["step"] == 1 and p["send_at"]]
		days = {}
		for p in firsts:
			days.setdefault(p["send_at"][:10], 0)
			days[p["send_at"][:10]] += 1
		self.assertLessEqual(max(days.values()), 8, "the warm-up spreads first emails over the days")
		for p in plan:
			if p["send_at"]:
				self.assertTrue(sequencer.in_window(p["send_at"], p["zone"]))
		by_enr = {}
		for p in plan:
			by_enr.setdefault(p["enrollment"], {})[p["step"]] = p["send_at"]
		for steps in by_enr.values():
			if steps.get(1) and steps.get(2):
				self.assertGreater(steps[2], steps[1])
