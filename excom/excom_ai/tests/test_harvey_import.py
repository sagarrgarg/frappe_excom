"""Excom AI Phase 1I: importing Harvey (HLD-007 Appendix A). Runs the real import from Harvey's
database on this machine into the test site, checks the counts match on both sides, runs it again
to show nothing doubles, then removes it. Heavy (hundreds of companies and emails): it runs when
EXCOM_HARVEY_IMPORT_TEST=1, or by name. EXCOM_HARVEY_DIR points it at a copy of Harvey's folder instead of ~/harvey."""

import os
import sqlite3

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import harvey_import

OWNER = "qa.hvy.owner@example.com"
BACKUP = "qa.hvy.backup@example.com"
TEAM = "QA Harvey Desk"
ROOT = os.path.expanduser(os.environ.get("EXCOM_HARVEY_DIR") or harvey_import.HARVEY)
DB = os.path.join(ROOT, "data", "harvey.db")


class TestHarveyImport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not os.path.exists(DB) or not os.environ.get("EXCOM_HARVEY_IMPORT_TEST"):
			raise __import__("unittest").SkipTest("set EXCOM_HARVEY_IMPORT_TEST=1 (and have ~/harvey or EXCOM_HARVEY_DIR) to run the import")
		frappe.flags.mute_emails = True
		purge_users(OWNER, BACKUP)
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent", "Sales Manager")
		if not frappe.db.exists("Excom Team", TEAM):
			frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [{"user": OWNER, "role": "Manager"},
				{"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.dnc_before = {r.domain for r in frappe.get_single("Excom AI Settings").dnc_domains}
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		name = frappe.db.get_value("Excom AI Agent", {"agent_name": "QA Harish"})
		if name:
			harvey_import.undo(name)
		s = frappe.get_single("Excom AI Settings")
		s.dnc_domains = [r for r in s.dnc_domains if r.domain in cls.dnc_before]
		s.save(ignore_permissions=True)
		frappe.db.delete("Excom AI Email Domain", {"pattern_source": ["is", "set"], "creation": [">=", frappe.utils.add_days(frappe.utils.now_datetime(), -1)]})
		if frappe.db.exists("Excom Team", TEAM):
			frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
		purge_users(OWNER, BACKUP)
		frappe.db.commit()
		super().tearDownClass()

	def test_import_counts_match_and_a_rerun_adds_nothing(self):
		conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
		promoted = conn.execute("SELECT COUNT(*) FROM prospects WHERE status IN ('contacted', 'replied', 'lost') AND IFNULL(email, '') != ''").fetchone()[0]
		r = harvey_import.run(OWNER, BACKUP, TEAM, agent_name="QA Harish", harvey_dir=ROOT)
		h, e = r["harvey"], r["excom"]
		self.assertEqual(e["sequences"], h["campaigns"])
		self.assertEqual(e["leads"], promoted, "everyone Harvey contacted is a Lead")
		self.assertGreaterEqual(e["companies"], h["companies"] - 5, "one row per domain (a few Harvey companies share one)")
		mapped = {harvey_import.OUTBOX_STATUS.get(k, "Cancelled"): 0 for k in h["outbox_by_status"]}
		for k, v in h["outbox_by_status"].items():
			mapped[harvey_import.OUTBOX_STATUS.get(k, "Cancelled")] += v
		self.assertEqual(e["proposals_by_status"], mapped, "every email, in the same state")
		agent = frappe.get_doc("Excom AI Agent", e["agent"])
		self.assertEqual((agent.status, agent.owner_user), ("Draft", OWNER), "Harish arrives in Draft: a person starts him")
		pb = frappe.get_doc("Excom AI Playbook", agent.playbooks[0].playbook)
		self.assertTrue({"Facts", "Banned claims", "Certifications"} <= {s.kind for s in pb.sections})
		self.assertEqual(frappe.db.count("Excom AI Proposal", {"agent": agent.name, "status": "Approved"}),
			h["outbox_by_status"].get("approved", 0), "approved emails wait for the agent to be Live")
		again = harvey_import.run(OWNER, BACKUP, TEAM, agent_name="QA Harish", harvey_dir=ROOT)["excom"]
		self.assertEqual((again["proposals"], again["leads"], again["sequences"]), (e["proposals"], e["leads"], e["sequences"]))
		self.assertEqual(frappe.db.count("Excom AI Proposal", {"agent": agent.name}), e["proposals"], "nothing doubled")
		self.assertEqual(frappe.db.get_value("Excom AI Settings", None, "enabled") or 0, 0, "the import switches nothing on")
