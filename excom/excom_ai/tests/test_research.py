"""Excom AI Phase 1C: research (HLD-007 §9, Appendix C.1). Discovery, profiling, contacts and
verification as task kinds, exclusions, and promotion into Leads and Prospects. The tools are
scripted here (their own adapters are tested in test_tools), so these tests check what research
does with what the tools say."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import research, tools
from excom.excom_ai.engine import ownership, registry, runner, tasks

OWNER = "qa.res.owner@example.com"
BACKUP = "qa.res.backup@example.com"
COLLEAGUE = "qa.res.colleague@example.com"
TEAM = "QA Research Desk"
CUSTOMER = "QA Res Existing Buyer"


def companies(*domains, country="Mexico"):
	return [{"name": f"QA Res {d}", "domain": d, "website": f"https://{d}", "country": country, "city": "Monterrey",
		"source_url": f"https://search.example/{d}", "external_id": f"qa:{d}", "buyer_type": "Distributor",
		"signals": {"IMPORTS_CATEGORY": "water guns", "COUNTRY": country}} for d in domains]


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Res%"]}, pluck="name")
	comps = frappe.get_all("Excom AI Company", {"company_name": ["like", "QA Res%"]}, pluck="name")
	frappe.db.delete("Excom AI Observation", {"company": ["in", comps or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%.qa-res.example"]}, pluck="name"):
		for p in frappe.get_all("Prospect Lead", {"lead": lead}, pluck="parent"):
			for a in frappe.get_all("Dynamic Link", {"link_doctype": "Prospect", "link_name": p, "parenttype": "Address"}, pluck="parent"):
				frappe.delete_doc("Address", a, force=True, ignore_permissions=True)
			frappe.delete_doc("Prospect", p, force=True, ignore_permissions=True)
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	frappe.db.delete("Excom AI Person", {"company": ["in", comps or ["-"]]})
	frappe.db.delete("Excom AI Company", {"name": ["in", comps or ["-"]]})
	for dt in ("Excom AI Task", "Excom AI Proposal"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for c in frappe.get_all("Contact", {"first_name": ["like", "QA Res%"]}, pluck="name"):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	if frappe.db.exists("Customer", CUSTOMER):
		frappe.delete_doc("Customer", CUSTOMER, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP, COLLEAGUE)
	frappe.db.commit()


class TestResearch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP, COLLEAGUE):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Customer", "customer_name": CUSTOMER, "website": "https://www.customer.qa-res.example"}).insert(
			ignore_permissions=True)
		for code in ("IMPORTS_CATEGORY", "COUNTRY", "EMAIL_STATUS"):
			frappe.db.set_value("Excom AI Signal Code", code, "status", "Confirmed")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for code in ("IMPORTS_CATEGORY", "COUNTRY", "EMAIL_STATUS"):
			frappe.db.set_value("Excom AI Signal Code", code, "status", "Proposed")
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)

	def agent(self, name="QA Res Lucía"):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": name, "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "audience": "importers and distributors",
			"customer_types": "Export Importer" if False else "",
			"keyword_rows": [{"keyword": "water guns", "resolved_as": "Vocabulary", "link_doctype": "Item Group",
				"link_name": frappe.db.get_value("Item Group", {}, "name"), "included": 1}],
			"markets": [{"country": "Mexico", "language": "es", "timezone": "America/Mexico_City"}]}).insert(ignore_permissions=True)
		a.db_set("status", "Shadow")
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def run_now(self, task):
		frappe.db.set_value("Excom AI Task", task, "status", "Running")
		frappe.db.commit()
		runner.run_task(task)
		row = frappe.db.get_value("Excom AI Task", task, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	def scripted(self, answers: dict):
		def use(key, method, **kw):
			kw.pop("cost", None)
			kw.pop("known", None)
			fn = answers.get((key, method))
			if fn is None:
				raise tools.ToolRefused(f"{key} off")
			return fn(**kw) if callable(fn) else fn
		return patch.object(tools, "use", side_effect=use), patch.object(tools, "available",
			side_effect=lambda key: any(k == key for k, _m in answers))

	# ---- exclusions

	def test_exclusions(self):
		self.assertEqual(research.exclusion("customer.qa-res.example")[0], "Existing customer")
		s = frappe.get_single("Excom AI Settings")
		s.append("dnc_domains", {"domain": "rival.qa-res.example", "reason": "Competitor"})
		s.save(ignore_permissions=True)
		self.addCleanup(lambda: frappe.db.delete("Excom AI DNC Domain", {"domain": "rival.qa-res.example"}))
		self.assertEqual(research.exclusion("shop.rival.qa-res.example")[0], "Competitor")
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA Res Colleague Lead", "email_id": "x@taken.qa-res.example",
			"lead_owner": COLLEAGUE}).insert(ignore_permissions=True)
		self.assertEqual(research.exclusion("taken.qa-res.example")[0], "Open lead or deal (people)")
		self.assertEqual(research.exclusion("free.qa-res.example"), (None, None))
		self.assertEqual(research.exclusion("gmail.com"), (None, None), "a free mail domain is nobody's company")
		frappe.db.set_value("Lead", lead.name, "status", "Do Not Contact")

	# ---- the research pipeline

	def test_discover_profile_contacts_verify(self):
		a = self.agent()
		answers = {
			("osm", "search"): lambda query: companies("one.qa-res.example", "two.qa-res.example", "customer.qa-res.example"),
			("web_reader", "profile"): lambda url: {"title": "Juguetes Uno", "description": "Importador y mayorista de pistolas de agua",
				"text": "water guns importador", "language": "es", "emails": ["ventas@one.qa-res.example"], "has_online_store": False},
			("email_finder", "find"): lambda domain, published, people: {"mx_type": "Google", "addresses": [
				{"email": f"ventas@{domain}", "source": "Published", "is_inbox": True},
				{"email": f"maria.lopez@{domain}", "source": "Pattern", "first_name": "María", "last_name": "López", "title": "Compras"}]},
			("reoon", "verify"): lambda email: {"status": "Verified" if email.startswith("ventas") else "Unknown"},
		}
		use, available = self.scripted(answers)
		with use, available:
			t = tasks.add(a.name, "discover", params={"source": "osm"})
			out = self.run_now(t)
			self.assertIn("3 from osm: 3 new", out)
			self.assertIn("1 excluded", out)
			excluded = frappe.get_doc("Excom AI Company", {"domain": "customer.qa-res.example"})
			self.assertEqual((excluded.status, excluded.exclusion_reason, excluded.customer), ("Excluded", "Existing customer", CUSTOMER))
			one = frappe.get_doc("Excom AI Company", {"domain": "one.qa-res.example"})
			self.assertEqual((one.agent, one.status, one.source), (a.name, "New", "OSM"))
			self.assertTrue(frappe.db.exists("Excom AI Observation", {"company": one.name, "signal": "IMPORTS_CATEGORY"}))
			self.assertFalse(frappe.db.exists("Excom AI Observation", {"company": one.name, "signal": "BUYER_TYPE"}),
				"a signal people have not confirmed is not collected")
			profile = frappe.get_value("Excom AI Task", {"agent": a.name, "kind": "profile_company", "subject_name": one.name})
			self.assertTrue(profile)
			self.assertFalse(frappe.db.exists("Excom AI Task", {"agent": a.name, "kind": "profile_company",
				"subject_name": excluded.name}), "an excluded company is not researched")

			self.assertIn("fit", self.run_now(profile))
			one.reload()
			self.assertEqual((one.status, one.language), ("Profiled", "es"))
			self.assertGreaterEqual(one.fit_score, research.FIT_FOR_CONTACTS)
			contacts = frappe.get_value("Excom AI Task", {"agent": a.name, "kind": "find_contacts", "subject_name": one.name})
			self.assertIn("2 addresses", self.run_now(contacts))
			inbox = frappe.get_doc("Excom AI Person", {"email": "ventas@one.qa-res.example"})
			named = frappe.get_doc("Excom AI Person", {"email": "maria.lopez@one.qa-res.example"})
			self.assertEqual((inbox.is_inbox, inbox.email_source), (1, "Published"))
			self.assertEqual((named.email_status, named.email_source), ("Guess", "Pattern"))
			for p in (inbox, named):
				self.run_now(frappe.get_value("Excom AI Task", {"agent": a.name, "kind": "verify_email", "subject_name": p.name}))
			self.assertEqual(frappe.db.get_value("Excom AI Person", inbox.name, ["email_status", "status", "verified_by"]),
				("Verified", "Ready", "Reoon"))
			self.assertEqual(frappe.db.get_value("Excom AI Person", named.name, ["email_status", "status"]), ("Guess", "New"),
				"an unconfirmed guess stays a guess and is not ready")
			summary = research.summary(a.name)
			self.assertEqual(summary["total"], 3)

		# discovery again: nothing doubles
		use, available = self.scripted(answers)
		with use, available:
			self.assertIn("0 new, 3 already known", self.run_now(tasks.add(a.name, "discover", dedupe_key="again", params={"source": "osm"})))

	def test_the_planner(self):
		a = self.agent("QA Res Planner")
		frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Res Planned", "domain": "planned.qa-res.example",
			"agent": a.name, "status": "New"}).insert(ignore_permissions=True)
		with patch.object(tools, "available", side_effect=lambda key: key == "osm"):
			specs = research.plan(a)
		kinds = [s["kind"] for s in specs]
		self.assertEqual(kinds.count("discover"), 1)
		self.assertIn("profile_company", kinds)
		self.assertIn("excom.excom_ai.research", registry.MODULES)

	# ---- promotion

	def test_promotion_makes_a_lead_and_a_prospect_once(self):
		a = self.agent("QA Res Promote")
		company = frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Res Promoted Co",
			"domain": "promo.qa-res.example", "country": "Mexico", "city": "Guadalajara", "agent": a.name,
			"status": "Contacts found", "fit_score": 80}).insert(ignore_permissions=True)
		person = frappe.get_doc({"doctype": "Excom AI Person", "company": company.name, "email": "compras@promo.qa-res.example",
			"is_inbox": 1, "email_status": "Verified", "status": "Ready", "language": "es"}).insert(ignore_permissions=True)
		out = research.promote(person.name, a)
		self.assertTrue(out["created"])
		lead = frappe.get_doc("Lead", out["lead"])
		self.assertEqual((lead.email_id, lead.lead_owner, lead.ai_agent, lead.ai_email_status), (person.email, OWNER, a.name, "Verified"))
		if frappe.get_meta("Lead").has_field("excom_team"):
			self.assertEqual(lead.excom_team, TEAM)
		prospect = frappe.get_doc("Prospect", out["prospect"])
		self.assertIn(lead.name, [r.lead for r in prospect.leads])
		self.assertEqual(prospect.ai_research, company.name)
		self.assertTrue(frappe.db.exists("Dynamic Link", {"link_doctype": "Prospect", "link_name": prospect.name, "parenttype": "Address"}))
		self.assertEqual(frappe.db.get_value("Excom AI Company", company.name, "status"), "Promoted")
		again = research.promote(person.name, a)
		self.assertEqual((again["lead"], again["created"]), (lead.name, False))

	def test_promotion_refuses_an_excluded_company(self):
		a = self.agent("QA Res Refuse")
		buyer = frappe.get_doc({"doctype": "Customer", "customer_name": "QA Res Second Buyer",
			"website": "https://buyer2.qa-res.example"}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "Customer", buyer.name, force=True, ignore_permissions=True)
		company = frappe.get_doc({"doctype": "Excom AI Company", "company_name": "QA Res Customer Co",
			"domain": "buyer2.qa-res.example", "agent": a.name, "status": "Contacts found"}).insert(ignore_permissions=True)
		person = frappe.get_doc({"doctype": "Excom AI Person", "company": company.name, "email": "info@buyer2.qa-res.example",
			"is_inbox": 1, "email_status": "Verified", "status": "Ready"}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			research.promote(person.name, a)
		self.assertEqual(frappe.db.get_value("Excom AI Company", company.name, ["status", "exclusion_reason"]),
			("Excluded", "Existing customer"))
		self.assertFalse(frappe.db.exists("Lead", {"email_id": person.email}))
