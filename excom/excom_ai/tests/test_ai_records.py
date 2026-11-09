"""Excom AI records (HLD-007 §5, Appendix C): what each record guarantees on its own.

The master switch starts off; the engine's tick then reads one setting and returns, so the module
costs nothing until it is switched on.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import service_user
from excom.excom_ai.doctype.excom_ai_agent.excom_ai_agent import approve_brief
from excom.excom_ai.doctype.excom_ai_company.excom_ai_company import normalise_domain

OWNER = "qa.ai.owner@example.com"
BACKUP = "qa.ai.backup@example.com"
OUTSIDER = "qa.ai.outsider@example.com"
LEAD_MANAGER = "qa.ai.manager@example.com"
TEAM = "QA AI Desk"
TEAM_2 = "QA AI Other Desk"
BOX = "QA AI Box"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA %"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Enrollment", "Excom AI Readiness Gap", "Excom AI Task", "Excom AI Feedback",
		"Excom AI Sequence", "Excom AI Knowledge"):
		for name in frappe.get_all(dt, {"agent": ["in", agents or ["-"]]}, pluck="name"):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for dt, field in (("Excom AI Observation", "evidence_url"),):
		frappe.db.delete(dt, {field: ["like", "https://qa.example/%"]})
	frappe.db.delete("Excom AI Person", {"email": ["like", "%@qa-ai.example"]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%@qa-ai.example"]}, pluck="name"):
		frappe.db.delete("Excom AI Enrollment", {"lead": lead})
		_delete_lead(lead)
	frappe.db.delete("Excom AI Company", {"company_name": ["like", "QA %"]})
	frappe.db.delete("Excom AI Playbook", {"title": ["like", "QA %"]})
	frappe.db.delete("Excom AI Recipe", {"recipe_key": ["like", "qa_%"]})
	frappe.db.delete("Excom AI Glossary", {"term": ["like", "qa %"]})
	frappe.db.delete("Excom AI Readiness Rule", {"rule_key": ["like", "qa_%"]})
	for team in (TEAM, TEAM_2):
		if frappe.db.exists("Excom Team", team):
			frappe.delete_doc("Excom Team", team, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Channel Account", BOX):
		frappe.delete_doc("Excom Channel Account", BOX, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP, OUTSIDER, LEAD_MANAGER)
	frappe.db.commit()


def _delete_lead(name):
	"""A Lead brings a Contact and an Omni Identity with it (Excom's hooks); remove them all."""
	contacts = frappe.get_all("Dynamic Link", {"link_doctype": "Lead", "link_name": name, "parenttype": "Contact"}, pluck="parent")
	identities = set(frappe.get_all("Omni Identity Link", {"linked_name": ["in", [name, *contacts]]}, pluck="parent"))
	for oi in identities:
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for c in contacts:
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	frappe.delete_doc("Lead", name, force=True, ignore_permissions=True)


class TestAIRecords(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		for email in (OWNER, BACKUP, OUTSIDER, LEAD_MANAGER):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		team = frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM})
		team.append("members", {"user": OWNER, "role": "Manager"})
		team.append("members", {"user": BACKUP, "role": "Member"})
		team.append("members", {"user": LEAD_MANAGER, "role": "Manager"})
		team.insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM_2,
			"members": [{"user": OUTSIDER, "role": "Member"}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": "qa.ai.box@example.com"}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def agent(self, **kw):
		doc = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": kw.pop("agent_name", "QA Harish"),
			"agent_type": kw.pop("agent_type", "Sales"), "owner_user": OWNER, "team": TEAM, **kw})
		return doc.insert(ignore_permissions=True)

	# ---- off by default, nothing scheduled

	def test_off_by_default_and_the_tick_does_nothing(self):
		from unittest.mock import patch

		from excom.excom_ai.doctype.excom_ai_settings.excom_ai_settings import is_enabled
		from excom.excom_ai.engine import orchestrator

		self.assertFalse(is_enabled())
		events = frappe.get_hooks("scheduler_events") or {}
		jobs = [j for v in events.values() for j in (v if isinstance(v, list) else [x for l in v.values() for x in l])]
		self.assertEqual([j for j in jobs if "excom_ai" in j], ["excom.excom_ai.engine.orchestrator.tick"])
		with patch.object(frappe, "get_all") as get_all, patch.object(frappe, "enqueue") as enqueue:
			orchestrator.tick()
		get_all.assert_not_called()
		enqueue.assert_not_called()

	def test_seeds(self):
		sales = frappe.get_doc("Excom AI Agent Type", "Sales")
		modes = {r.action: r.mode for r in sales.default_policies}
		self.assertEqual((modes["first_email"], modes["quotation"], modes["submit_document"]), ("Review", "Review", "Off"))
		self.assertEqual(frappe.db.get_value("Excom AI Signal Code", "IMPORTS_CATEGORY", "status"), "Proposed")
		self.assertEqual(frappe.db.get_value("Excom AI Tool", "serper", "enabled"), 0)
		self.assertTrue(frappe.db.exists("Role", "Excom AI Sales"))
		self.assertTrue(frappe.get_meta("Lead").has_field("ai_agent"))
		self.assertTrue(frappe.get_meta("Prospect").has_field("ai_company_stop"))
		self.assertTrue(frappe.get_meta("Excom Thread").has_field("ai_mode"))

	# ---- the agent and its own user

	def test_an_agent_acts_as_its_own_user(self):
		a = self.agent()
		self.assertEqual({r.action for r in a.policies}, {r.action for r in frappe.get_doc("Excom AI Agent Type", "Sales").default_policies})
		user = frappe.get_doc("User", a.service_user)
		self.assertTrue(user.name.endswith("@ai-agents.invalid"))
		self.assertTrue(service_user.is_service_user(user.name))
		self.assertEqual({r.role for r in user.roles}, {"Excom Agent", "Excom AI Sales"})
		self.assertEqual((user.enabled, user.last_name), (1, "(AI)"))
		self.assertTrue(frappe.db.exists("Excom Team Member", {"parent": TEAM, "user": user.name}), "a member of its desk")
		self.assertEqual(a.budget_scope, f"Excom AI Agent:{a.name}")
		a.agent_type = "Accounts"
		a.save(ignore_permissions=True)
		self.assertEqual({r.role for r in frappe.get_doc("User", a.service_user).roles}, {"Excom Agent", "Excom AI Accounts"})
		a.status = "Retired"
		a.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("User", a.service_user, "enabled"), 0)

	def test_owner_and_backup(self):
		with self.assertRaises(frappe.ValidationError):
			self.agent(agent_name="QA No Backup", status="Shadow")
		with self.assertRaises(frappe.ValidationError):
			self.agent(agent_name="QA Same", backup_user=OWNER)
		with self.assertRaises(frappe.ValidationError):
			self.agent(agent_name="QA Outsider", backup_user=OUTSIDER)
		a = self.agent(agent_name="QA Bot Owner")
		with self.assertRaises(frappe.ValidationError):
			self.agent(agent_name="QA Bot", backup_user=a.service_user)
		self.agent(agent_name="QA Good", backup_user=BACKUP, status="Shadow")

	def test_custom_agents_review_and_replies_go_alone_only_for_safe_intents(self):
		custom = frappe.get_doc("Excom AI Agent Type", "Custom")
		self.assertNotIn("Auto", {r.mode for r in custom.default_policies}, "Custom works in Review only")
		c = self.agent(agent_name="QA Custom", agent_type="Custom")
		next(r for r in c.policies if r.action == "todo_note").mode = "Auto"
		with self.assertRaises(frappe.ValidationError):
			c.save(ignore_permissions=True)
		a = self.agent(agent_name="QA Replies")
		row = next(r for r in a.policies if r.action == "reply")
		row.mode, row.condition = "Auto", "ooo, thanks"
		a.save(ignore_permissions=True)
		for condition in ("", "ooo, price question"):
			a.reload()
			row = next(r for r in a.policies if r.action == "reply")
			row.mode, row.condition = "Auto", condition
			with self.assertRaises(frappe.ValidationError, msg=condition):
				a.save(ignore_permissions=True)

	def test_policy_ceilings(self):
		a = self.agent(agent_name="QA Ceilings")
		for action, mode in (("quotation", "Auto"), ("submit_document", "Review"), ("prices_and_terms", "Auto")):
			a.reload()
			next(r for r in a.policies if r.action == action).mode = mode
			with self.assertRaises(frappe.ValidationError, msg=action):
				a.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			t = frappe.get_doc("Excom AI Agent Type", "Custom")
			t.append("default_policies", {"action": "quotation", "mode": "Auto"})
			t.save(ignore_permissions=True)

	def test_live_needs_an_approved_brief_and_readiness(self):
		a = self.agent(agent_name="QA Live", backup_user=BACKUP, status="Shadow")
		a.status = "Live"
		with self.assertRaises(frappe.ValidationError):
			a.save(ignore_permissions=True)
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			approve_brief(a.name)
		frappe.set_user(OWNER)
		self.assertEqual(approve_brief(a.name), {"version": 1})
		frappe.set_user("Administrator")
		a.reload()
		a.status = "Live"
		with self.assertRaises(frappe.ValidationError, msg="readiness not checked"):
			a.save(ignore_permissions=True)
		a.reload()
		a.db_set("readiness_state", "Warnings")
		a.reload()
		a.status = "Live"
		a.save(ignore_permissions=True)

	def test_an_edited_brief_needs_approving_again(self):
		a = self.agent(agent_name="QA Brief", backup_user=BACKUP, status="Shadow", mission="Sell water guns")
		frappe.set_user(LEAD_MANAGER)
		approve_brief(a.name)  # the team's manager may approve
		frappe.set_user("Administrator")
		a.reload()
		a.db_set("readiness_state", "Ready")
		a.reload()
		a.status = "Live"
		a.save(ignore_permissions=True)
		a.mission = "Sell water guns and gulal"
		a.save(ignore_permissions=True)
		self.assertEqual((a.status, a.brief_approved_on), ("Shadow", None))

	def test_stopping_works_after_the_owner_left(self):
		a = self.agent(agent_name="QA Left", backup_user=BACKUP, status="Shadow")
		frappe.db.set_value("User", OWNER, "enabled", 0)
		try:
			a.status = "On Hold"
			a.save(ignore_permissions=True)
			a.status = "Retired"
			a.save(ignore_permissions=True)
			with self.assertRaises(frappe.ValidationError):
				approve_brief(a.name)
		finally:
			frappe.db.set_value("User", OWNER, "enabled", 1)

	def test_team_scoping_and_moves(self):
		from excom.excom_ai import permissions

		a = self.agent(agent_name="QA Scoped", backup_user=BACKUP)
		frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "note"}).insert(ignore_permissions=True)
		frappe.set_user(OUTSIDER)
		self.assertFalse(frappe.has_permission("Excom AI Agent", "read", a))
		self.assertFalse(frappe.get_list("Excom AI Proposal", {"agent": a.name}))
		self.assertFalse(frappe.get_list("Excom AI Agent", {"name": a.name}))
		frappe.set_user(LEAD_MANAGER)  # in the team, not named on the agent
		self.assertTrue(frappe.has_permission("Excom AI Agent", "read", a))
		self.assertTrue(frappe.get_list("Excom AI Proposal", {"agent": a.name}))
		frappe.set_user("Administrator")
		self.assertIsNone(permissions.visible_agents())

		a.reload()
		a.team, a.backup_user = TEAM_2, None
		a.owner_user = "Administrator"
		a.save(ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Excom Team Member", {"parent": TEAM, "user": a.service_user}), "left its old desk")
		self.assertTrue(frappe.db.exists("Excom Team Member", {"parent": TEAM_2, "user": a.service_user}))

	def test_a_service_user_disabled_by_hand_stays_disabled(self):
		a = self.agent(agent_name="QA Disabled")
		frappe.db.set_value("User", a.service_user, "enabled", 0)
		a.reload()
		a.agent_name = "QA Disabled Two"
		a.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("User", a.service_user, "enabled"), 0)

	def test_ai_fields_sit_together_on_native_forms(self):
		fields = [f.fieldname for f in frappe.get_meta("Lead").fields]
		i = fields.index("ai_section")
		self.assertEqual(fields[i:i + 4], ["ai_section", "ai_agent", "ai_person", "ai_email_status"])
		self.assertGreater(i, 0)

	def test_one_agent_does_outreach_per_mailbox(self):
		self.agent(agent_name="QA Box One", backup_user=BACKUP, status="Shadow", channels=[{"channel_account": BOX}])
		with self.assertRaises(frappe.ValidationError):
			self.agent(agent_name="QA Box Two", backup_user=BACKUP, status="Shadow", channels=[{"channel_account": BOX}])
		self.agent(agent_name="QA Box Replies", backup_user=BACKUP, status="Shadow",
			channels=[{"channel_account": BOX, "purpose": "Replies"}])

	# ---- research records

	def test_one_company_per_domain(self):
		self.assertEqual(normalise_domain("https://www.Pool-Barn.com.au/contact?x=1"), "pool-barn.com.au")
		c = frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Pool Barn", "website": "www.qa-poolbarn.example/about"}).insert()
		self.assertEqual((c.domain, c.website), ("qa-poolbarn.example", "https://www.qa-poolbarn.example/about"))
		with self.assertRaises(frappe.UniqueValidationError):
			frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Pool Barn 2", "domain": "HTTPS://QA-POOLBARN.EXAMPLE"}).insert()
		c.status = "Excluded"
		with self.assertRaises(frappe.ValidationError):
			c.save()

	def test_people_and_observations(self):
		c = frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Observed", "domain": "qa-observed.example"}).insert()
		p = frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": "Info@QA-AI.example", "is_inbox": 1}).insert()
		self.assertEqual((p.email, p.full_name, p.seniority), ("info@qa-ai.example", "General inbox", "Inbox"))
		obs = {"doctype": "Excom AI Observation", "company": c.name, "signal": "BUYER_TYPE", "value_text": "Distributor",
			"evidence_url": "https://qa.example/about"}
		with self.assertRaises(frappe.ValidationError, msg="signal still Proposed"):
			frappe.get_doc(obs).insert()
		frappe.db.set_value("Excom AI Signal Code", "BUYER_TYPE", "status", "Confirmed")
		try:
			frappe.get_doc(obs).insert()
		finally:
			frappe.db.set_value("Excom AI Signal Code", "BUYER_TYPE", "status", "Proposed")

	# ---- work records

	def test_one_proposal_per_step_and_reasons_for_rejections(self):
		a = self.agent(agent_name="QA Steps")
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA seq", "agent": a.name,
			"steps": [{"step": 1}, {"step": 2, "wait_days": 3}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name}).insert()
		base = {"doctype": "Excom AI Proposal", "agent": a.name, "action": "send_email", "enrollment": enr.name, "step": 1}
		first = frappe.get_doc(base).insert()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(base).insert()
		first.status = "Rejected"
		with self.assertRaises(frappe.ValidationError):
			first.save()
		first.reload()
		first.status = "Rejected"
		first.reject_reason = "Wrong tone"
		first.save()
		self.assertEqual(first.decided_by, "Administrator")
		second = frappe.get_doc(base).insert()  # a rejected step can be proposed again
		self.assertEqual(second.step_key, f"{enr.name}:1")
		second.db_set("status", "Expired")
		second.reload()
		second.save()
		self.assertIsNone(second.step_key)
		frappe.get_doc(base).insert()  # an expired one frees the step too
		with self.assertRaises(frappe.UniqueValidationError, msg="the database refuses a second holder"):
			frappe.get_doc({**base, "step": 9, "step_key": f"{enr.name}:1"}).db_insert()

	def test_one_open_enrollment_per_lead_and_agent(self):
		a = self.agent(agent_name="QA Enroll")
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA seq 2", "agent": a.name}).insert()
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA AI Lead", "email_id": "qa.lead@qa-ai.example",
			"customer_type": "Export Importer"} if frappe.get_meta("Lead").has_field("customer_type") else
			{"doctype": "Lead", "first_name": "QA AI Lead", "email_id": "qa.lead@qa-ai.example"}).insert(ignore_permissions=True)
		try:
			first = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "lead": lead.name}).insert()
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "lead": lead.name}).insert()
			first.status = "Replied"
			first.save()
			self.assertEqual(first.stop_reason, "Replied")
			frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "lead": lead.name}).insert()
		finally:
			frappe.db.delete("Excom AI Enrollment", {"lead": lead.name})
			_delete_lead(lead.name)

	# ---- knowledge records

	def test_playbook_versions_and_approval(self):
		pb = frappe.get_doc({"doctype": "Excom AI Playbook", "title": "QA playbook",
			"sections": [{"kind": "Banned claims", "content": "Never claim EN71."}]}).insert()
		frappe.set_user(OWNER)
		pb.status = "Approved"
		with self.assertRaises(frappe.PermissionError):
			pb.save(ignore_permissions=True)
		frappe.set_user("Administrator")
		pb.reload()
		pb.status = "Approved"
		pb.save()
		self.assertEqual((pb.approved_by, pb.version), ("Administrator", 1))
		pb.sections[0].content = "Never claim EN71 or ASTM."
		pb.save()
		self.assertEqual((pb.status, pb.version, pb.approved_by), ("In Review", 2, None), "an edit goes back to review")

	def test_recipes_only_read_allowed_data(self):
		ok = {"doctype": "Excom AI Recipe", "recipe_key": "qa_overseas", "label": "QA overseas", "kind": "Query spec",
			"query_spec": '{"doctype": "Customer", "filters": {"gst_category": "Overseas"}, "fields": ["name", "customer_group"]}'}
		if not frappe.get_meta("Customer").has_field("gst_category"):
			ok["query_spec"] = '{"doctype": "Customer", "fields": ["name", "customer_group"]}'
		frappe.get_doc(ok).insert()
		frappe.get_doc({**ok, "recipe_key": "qa_counts", "query_spec": '{"doctype": "Sales Order", '
			'"fields": ["customer", "count(name) as orders", "sum(grand_total) as total"], "group_by": "customer", '
			'"order_by": "customer asc", "filters": [["docstatus", "=", 1]], "limit": 50}'}).insert()
		for key, spec in (
			("qa_user", '{"doctype": "User", "fields": ["name"]}'),
			("qa_badkey", '{"doctype": "Customer", "sql": "select 1"}'),
			("qa_nofield", '{"doctype": "Customer", "fields": ["no_such_field"]}'),
			("qa_secret", '{"doctype": "Excom AI Tool", "fields": ["api_key"]}'),
			("qa_notjson", "{not json"),
			("qa_mailqueue", '{"doctype": "Email Queue", "fields": ["message"]}'),
			("qa_contact", '{"doctype": "Contact", "fields": ["email_id"]}'),
			("qa_dictfield", '{"doctype": "Customer", "fields": [{"x": "y"}]}'),
			("qa_badfilter", '{"doctype": "Customer", "filters": [["no_such", "=", 1]]}'),
			("qa_otherdt", '{"doctype": "Customer", "filters": [["User", "name", "=", "x"]]}'),
			("qa_order", '{"doctype": "Customer", "order_by": "(select 1)"}'),
			("qa_limit", '{"doctype": "Customer", "limit": 100000}'),
		):
			with self.assertRaises((frappe.ValidationError, frappe.PermissionError), msg=key):
				frappe.get_doc({**ok, "recipe_key": key, "query_spec": spec}).insert()

	def test_waiving_a_blocker(self):
		a = self.agent(agent_name="QA Gaps")
		frappe.get_doc({"doctype": "Excom AI Readiness Rule", "rule_key": "qa_price_list", "agent_type": "Sales",
			"title": "QA price list", "severity": "Blocker"}).insert()
		gap = frappe.get_doc({"doctype": "Excom AI Readiness Gap", "agent": a.name, "rule": "qa_price_list",
			"severity": "Info"}).insert()
		self.assertEqual(gap.severity, "Blocker")
		gap.status = "Waived"
		with self.assertRaises(frappe.ValidationError):
			gap.save()
		frappe.set_user(OWNER)
		gap.reload()
		gap.status, gap.waive_reason = "Waived", "Prices sent by hand this month"
		with self.assertRaises(frappe.PermissionError):
			gap.save(ignore_permissions=True)
		frappe.set_user("Administrator")
		gap.reload()
		gap.status, gap.waive_reason = "Waived", "Prices sent by hand this month"
		gap.save()
		self.assertEqual(gap.waived_by, "Administrator")

	def test_settings(self):
		s = frappe.get_single("Excom AI Settings")
		s.dnc_domains = []
		s.append("dnc_domains", {"domain": "https://www.Competitor.example/", "reason": "Competitor"})
		s.prompt_retention_days = 21
		s.save(ignore_permissions=True)
		self.assertEqual(s.dnc_domains[0].domain, "competitor.example")
		if frappe.db.exists("DocType", "AI App Policy"):
			self.assertEqual(frappe.db.get_value("AI App Policy", {"parent": "AI Settings", "calling_app": "excom"},
				"payload_retention_days"), 21)
		s.dnc_domains = []
		s.save(ignore_permissions=True)
