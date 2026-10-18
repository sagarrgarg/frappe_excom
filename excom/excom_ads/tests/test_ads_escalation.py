"""Excom Ads alert escalation: an Open alert past its policy's threshold goes once to the escalation
recipients who may see it; acknowledged / fresh alerts do not; quiet hours hold non-critical ones.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_escalation'}"
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom_ads import alerts, escalation, permissions, tasks
from excom.excom_ads.tests.test_ads_permissions import COMPANY_B, _ensure_company_b

P = "QA Escalate"
U_FIRST = "qa.escalate.first@example.com"
U_BOSS = "qa.escalate.boss@example.com"
U_BOSS_B = "qa.escalate.boss.b@example.com"
USERS = [U_FIRST, U_BOSS, U_BOSS_B]
POLICY = f"{P} Policy"


class TestAdsEscalation(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		_ensure_company_b()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		for u in USERS:
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": "Excom Marketing"}]})
		for u, c in ((U_FIRST, cls.company), (U_BOSS, cls.company), (U_BOSS_B, COMPANY_B)):
			ins({"doctype": "User Permission", "user": u, "allow": "Company", "for_value": c, "apply_to_all_doctypes": 1})
		ins({
			"doctype": "Ads Notification Policy", "policy_name": POLICY, "enabled": 1, "company": cls.company, "alert_type": "Other", "min_severity": "Medium", "via_desk": 1,
			"recipients": [{"recipient_type": "User", "user": U_FIRST}],
			"escalate_after_minutes": 30,
			"escalation_recipients": [{"recipient_type": "User", "user": U_BOSS}, {"recipient_type": "User", "user": U_BOSS_B}],
		})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		names = frappe.get_all("Ads Alert", {"title": ["like", f"%{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Notification Log", {"document_type": "Ads Alert", "document_name": ["in", names]})
		frappe.db.delete("Ads Alert", {"name": ["in", names]})
		frappe.db.delete("Ads Notification Recipient", {"parent": POLICY})
		frappe.db.delete("Ads Notification Policy", {"name": POLICY})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		frappe.db.set_value("Ads Notification Policy", POLICY, {"quiet_from": None, "quiet_to": None, "escalate_after_minutes": 30})
		names = frappe.get_all("Ads Alert", {"title": ["like", f"%{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Notification Log", {"document_type": "Ads Alert", "document_name": ["in", names]})
		frappe.db.delete("Ads Alert", {"name": ["in", names]})
		frappe.db.commit()

	def _alert(self, title, minutes_ago=45, severity="High", company=None):
		name = alerts.raise_alert("Other", severity, f"{P} {title}", "Something needs a person", scope={"company": company or self.company}, dedupe_key=f"qa-escalate:{title}")
		then = add_to_date(now_datetime(), minutes=-minutes_ago)
		frappe.db.set_value("Ads Alert", name, {"first_seen": then, "notified_at": then}, update_modified=False)
		frappe.db.commit()
		return name

	def _logs(self, name) -> dict[str, list[str]]:
		out: dict[str, list[str]] = {}
		for r in frappe.get_all("Notification Log", {"document_type": "Ads Alert", "document_name": name}, ["for_user", "subject"]):
			out.setdefault(r.for_user, []).append(r.subject)
		return out

	def test_escalates_once_to_in_scope_people(self):
		name = self._alert("unanswered")
		out = escalation.escalate_due()
		self.assertGreaterEqual(out["escalated"], 1)
		logs = self._logs(name)
		self.assertEqual(list(logs), [U_BOSS], "the company B escalation contact does not hear about a company A alert")
		self.assertTrue(logs[U_BOSS][0].startswith("[High] [Escalated]"), logs)
		a = frappe.db.get_value("Ads Alert", name, ["escalated", "escalated_at", "escalated_to", "title"], as_dict=True)
		self.assertEqual(a.escalated, 1)
		self.assertTrue(a.escalated_at)
		self.assertEqual(a.escalated_to, U_BOSS)
		self.assertFalse(a.title.startswith("[Escalated]"), "the stored alert keeps its own title")
		escalation.escalate_due()
		self.assertEqual(len(self._logs(name)[U_BOSS]), 1, "escalation happens once")

	def test_acknowledged_or_fresh_alerts_are_not_escalated(self):
		acked = self._alert("acknowledged")
		frappe.db.set_value("Ads Alert", acked, {"status": "Acknowledged", "acknowledged_by": "Administrator"})
		fresh = self._alert("fresh", minutes_ago=10)
		snoozed = self._alert("snoozed")
		frappe.db.set_value("Ads Alert", snoozed, {"status": "Snoozed", "snoozed_until": add_to_date(now_datetime(), hours=2)})
		frappe.db.commit()
		escalation.escalate_due()
		for n in (acked, fresh, snoozed):
			self.assertEqual(frappe.db.get_value("Ads Alert", n, "escalated"), 0, n)
			self.assertEqual(self._logs(n), {})

	def test_quiet_hours_hold_non_critical_but_not_critical(self):
		frappe.db.set_value("Ads Notification Policy", POLICY, {"quiet_from": "00:00:00", "quiet_to": "23:59:59"})
		frappe.db.commit()
		high = self._alert("quiet high")
		crit = self._alert("quiet critical", severity="Critical")
		out = escalation.escalate_due()
		self.assertGreaterEqual(out["quiet"], 1)
		self.assertEqual(frappe.db.get_value("Ads Alert", high, "escalated"), 0, "held, and still pending for after quiet hours")
		self.assertEqual(frappe.db.get_value("Ads Alert", crit, "escalated"), 1)
		frappe.db.set_value("Ads Notification Policy", POLICY, {"quiet_from": None, "quiet_to": None})
		frappe.db.commit()
		escalation.escalate_due()
		self.assertEqual(frappe.db.get_value("Ads Alert", high, "escalated"), 1, "goes out once quiet hours are over")

	def test_qa_fix_alert_held_for_quiet_hours_is_delivered_once_they_end(self):
		# Quiet 22:00 → 06:00, across midnight.
		frappe.db.set_value("Ads Notification Policy", POLICY, {"quiet_from": "22:00:00", "quiet_to": "06:00:00"})
		frappe.db.commit()
		night = datetime(2026, 10, 17, 23, 30)
		early = datetime(2026, 10, 18, 5, 45)
		morning = datetime(2026, 10, 18, 6, 30)
		name = self._alert("QA Fix quiet", minutes_ago=0)
		unmatched = alerts.raise_alert("Sync Failed", "Info", f"{P} QA Fix no policy", "x", scope={"company": self.company}, dedupe_key="qa-escalate:QA Fix no policy")
		with patch("excom.excom_ads.alerts.now_datetime", return_value=night):
			alerts.deliver(name)
			alerts.deliver(unmatched)
			self.assertEqual(self._logs(name), {}, "held during quiet hours")
			self.assertEqual(frappe.db.get_value("Ads Alert", name, "quiet_held"), POLICY, "not marked fully notified")
			self.assertFalse(frappe.db.get_value("Ads Alert", unmatched, "quiet_held"), "no matching policy: nothing is held")
			self.assertEqual(alerts.held_due(), [], "still quiet: nothing to re-deliver")
			alerts.raise_alert("Other", "High", f"{P} QA Fix quiet", "repeat", scope={"company": self.company}, dedupe_key="qa-escalate:QA Fix quiet")
			self.assertEqual(self._logs(name), {}, "a repeat at the same severity does not break quiet hours")
		with patch("excom.excom_ads.alerts.now_datetime", return_value=early):
			self.assertEqual(alerts.held_due(), [], "05:45 is still inside 22:00-06:00")
			self.assertEqual(alerts.deliver_held(name), {"desk": 0, "email": 0, "whatsapp": 0})
		with patch("excom.excom_ads.alerts.now_datetime", return_value=morning):
			self.assertEqual(alerts.held_due(), [name], "the held alert, and not the one no policy matched")
			queued = []
			with patch("frappe.enqueue", side_effect=lambda method, **kw: queued.append((method, kw))):
				tasks.wake_snoozed_alerts()
			held_jobs = [kw for m, kw in queued if m == "excom.excom_ads.alerts.deliver_held"]
			self.assertEqual([kw["name"] for kw in held_jobs], [name])
			self.assertTrue(held_jobs[0]["deduplicate"])
			self.assertEqual(alerts.deliver_held(name)["desk"], 1)
			self.assertEqual(self._logs(name), {U_FIRST: [f"[High] {P} QA Fix quiet"]})
			self.assertFalse(frappe.db.get_value("Ads Alert", name, "quiet_held"))
			self.assertEqual(alerts.held_due(), [])
			alerts.deliver_held(name)
			self.assertEqual(len(self._logs(name)[U_FIRST]), 1, "never sent twice to a policy that already delivered")

	def test_escalation_contacts_are_not_normal_recipients(self):
		name = self._alert("delivery", minutes_ago=0)
		alerts.deliver(name)
		self.assertEqual(list(self._logs(name)), [U_FIRST], "only the policy's recipients on normal delivery")

	def test_a_claimed_alert_is_not_sent_again(self):
		name = self._alert("claimed")
		frappe.db.set_value("Ads Alert", name, "escalated", 1)
		frappe.db.commit()
		self.assertEqual(escalation._escalate_one(name, escalation._escalation_policies(), now_datetime()), "skip")
		self.assertEqual(self._logs(name), {})

	def test_scheduler_entry_is_cheap_and_deduplicated(self):
		with patch("frappe.enqueue") as enq:
			escalation.enqueue_escalations()
		self.assertFalse(enq.called, "nothing waiting, nothing queued")
		self._alert("waiting")
		with patch("frappe.enqueue") as enq:
			escalation.enqueue_escalations()
		self.assertTrue(enq.called)
		self.assertTrue(enq.call_args.kwargs["deduplicate"])
		frappe.db.set_value("Ads Notification Policy", POLICY, "escalate_after_minutes", 0)
		frappe.db.commit()
		with patch("frappe.enqueue") as enq:
			escalation.enqueue_escalations()
		self.assertFalse(enq.called, "no escalation policy, no job")
