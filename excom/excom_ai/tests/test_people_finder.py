"""HLD-009: the People Finder. Apollo and Prospeo adapters (HTTP scripted, never the network), the
research step `find_people`, what a person without an address may and may not do, what is charged,
and the Find people button on Leads."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import mail, people_finder, research, tools
from excom.excom_ai.engine import ownership, runner, tasks
from excom.excom_ai.tools import people as pt

OWNER = "qa.pf.owner@example.com"
OUTSIDER = "qa.pf.outsider@example.com"
TEAM = "QA People Desk"
DOMAIN = "acme.qa-pf.example"
LEAD_DOMAIN = "buyer.qa-pf.example"  # its own: a lead at DOMAIN would rightly exclude the research company


class Resp:
	def __init__(self, status, data):
		self.status_code, self._data = status, data

	def json(self):
		if isinstance(self._data, (dict, list)):
			return self._data
		raise ValueError("not json")


def post(status, data):
	return patch.object(pt.requests, "post", return_value=Resp(status, data))


class TestAdapters(FrappeTestCase):
	"""Request shapes, mapping, no-match and what is charged."""

	def tool(self, cls):
		record = frappe._dict(name=cls.key, label=cls.key)
		inst = cls(record)
		inst.secret = lambda field="api_key": "secret-key"
		return inst

	def test_cleaning(self):
		self.assertEqual(pt.linkedin("linkedin.com/in/Maria-Lopez-12/"), "https://www.linkedin.com/in/maria-lopez-12")
		self.assertEqual(pt.linkedin("https://es.linkedin.com/in/maria"), "https://www.linkedin.com/in/maria")
		for bad in ("https://www.linkedin.com/company/acme", "https://evil.example/in/x", "javascript:alert(1)", ""):
			self.assertIsNone(pt.linkedin(bad), bad)
		self.assertIsNone(pt.email("email_not_unlocked@domain.com"))
		self.assertIsNone(pt.email("lucia.nuñez@acme.mx"))  # Frappe would refuse to store it
		self.assertEqual(pt.email(" Ana.Ruiz@Acme.MX "), "ana.ruiz@acme.mx")
		self.assertEqual(pt.clean("Jefe\x00 de\nCompras  " * 3, 20), "Jefe de Compras Jefe")
		ranked = pt.rank([pt._candidate("Apollo", "1", "A", None, None, "Marketing Director", "director"),
			pt._candidate("Apollo", "2", "B", None, None, "Gerente de Compras", "manager"),
			pt._candidate("Apollo", "3", "C", None, None, "Owner", "owner")], ["compras", "owner"])
		self.assertEqual([c["external_id"] for c in ranked], ["2", "3", "1"])

	def test_apollo_search_and_enrich(self):
		apollo = self.tool(pt.Apollo)
		with post(200, {"people": [{"id": "a1", "first_name": "Carlos", "last_name_obfuscated": "Ra***z",
				"name": "Carlos Ra***z", "title": "Jefe de Compras", "seniority": "manager", "has_email": True}]}) as p:
			out = apollo.search("https://www.acme.mx/", ["compras"], limit=5)
		self.assertEqual(p.call_args.args[0], "https://api.apollo.io/api/v1/mixed_people/api_search")
		self.assertEqual(p.call_args.kwargs["headers"]["x-api-key"], "secret-key")
		self.assertEqual(p.call_args.kwargs["json"]["q_organization_domains_list"], ["acme.mx"])
		self.assertEqual((out[0]["full_name"], out[0]["last_name"], out[0]["seniority"]), ("Carlos", None, "Manager"))
		self.assertEqual(apollo.charged, 0.0)
		with post(200, {"person": {"id": "a1", "first_name": "Carlos", "last_name": "Ramírez", "title": "Jefe de Compras",
				"linkedin_url": "http://www.linkedin.com/in/carlos-r", "email": "Carlos@Acme.mx", "email_status": "verified"}}) as p:
			person = apollo.enrich(external_id="a1")
		body = p.call_args.kwargs["json"]
		self.assertEqual((body["id"], body["reveal_phone_number"], body["reveal_personal_emails"]), ("a1", False, False))
		self.assertEqual((person["email"], person["email_status"], person["linkedin_url"]),
			("carlos@acme.mx", "Verified", "https://www.linkedin.com/in/carlos-r"))
		self.assertEqual(apollo.charged, pt.APOLLO_CREDIT)
		with post(200, {"person": None}):
			self.assertIsNone(apollo.enrich(linkedin_url="https://www.linkedin.com/in/nobody"))
		self.assertEqual(apollo.charged, 0.0)
		with post(200, {"person": {"id": "a2", "first_name": "X", "email": "email_not_unlocked@domain.com",
				"email_status": "verified"}}):
			self.assertIsNone(apollo.enrich(external_id="a2")["email"])
		with self.assertRaises(tools.ToolRefused):
			apollo.enrich(first_name="Ana")  # not enough to look anyone up

	def test_apollo_account_problems_refuse_and_are_kept(self):
		apollo = self.tool(pt.Apollo)
		with patch.object(frappe.db, "set_value") as sv, post(403, {"error_details": {"code": "API_INACCESSIBLE"}}):
			with self.assertRaises(tools.ToolRefused):
				apollo.search(DOMAIN, ["compras"])
		self.assertIn("master API key", sv.call_args.args[3])
		with patch.object(frappe.db, "set_value"), post(401, "Invalid access credentials."):
			with self.assertRaises(tools.ToolRefused):
				apollo.search(DOMAIN, [])
		with post(429, {}):
			with self.assertRaises(tools.ToolRefused):
				apollo.search(DOMAIN, [])
		with post(500, {"error": "boom"}), self.assertRaises(RuntimeError):
			apollo.search(DOMAIN, [])

	def test_prospeo_search_and_enrich(self):
		prospeo = self.tool(pt.Prospeo)
		with post(200, {"error": False, "free": False, "results": [{"person": {"person_id": "p1", "first_name": "Marta",
				"last_name": "Gil", "full_name": "Marta Gil", "current_job_title": "Purchasing Director",
				"linkedin_url": "https://www.linkedin.com/in/marta-gil"}}]}) as p:
			out = prospeo.search(DOMAIN, ["purchasing"])
		self.assertEqual(p.call_args.args[0], "https://api.prospeo.io/search-person")
		self.assertEqual(p.call_args.kwargs["headers"]["X-KEY"], "secret-key")
		self.assertEqual(p.call_args.kwargs["json"]["filters"]["company"]["websites"]["include"], [DOMAIN])
		self.assertEqual((out[0]["external_id"], out[0]["linkedin_url"]), ("p1", "https://www.linkedin.com/in/marta-gil"))
		self.assertEqual(prospeo.charged, pt.PROSPEO_CREDIT)
		with post(400, {"error": True, "error_code": "NO_RESULTS"}):
			self.assertEqual(prospeo.search(DOMAIN, []), [])
		self.assertEqual(prospeo.charged, 0.0)
		with post(200, {"error": False, "free_enrichment": False, "person": {"person_id": "p1", "full_name": "Marta Gil",
				"linkedin_url": "https://www.linkedin.com/in/marta-gil", "email": {"status": "VERIFIED", "revealed": True,
				"email": "marta.gil@acme.qa-pf.example"}}}) as p:
			person = prospeo.enrich(linkedin_url="https://www.linkedin.com/in/marta-gil")
		self.assertEqual(p.call_args.kwargs["json"], {"only_verified_email": True,
			"data": {"linkedin_url": "https://www.linkedin.com/in/marta-gil"}})
		self.assertEqual((person["email"], person["email_status"]), ("marta.gil@acme.qa-pf.example", "Verified"))
		self.assertEqual(prospeo.charged, pt.PROSPEO_CREDIT)
		with post(400, {"error": True, "error_code": "NO_MATCH"}):
			self.assertIsNone(prospeo.enrich(email="who@acme.qa-pf.example"))
		self.assertEqual(prospeo.charged, 0.0)  # Prospeo does not bill a no-match
		with post(200, {"error": False, "free_enrichment": True, "person": {"person_id": "p1", "full_name": "Marta Gil",
				"email": {"status": "VERIFIED", "revealed": True, "email": "marta.gil@acme.qa-pf.example"}}}):
			prospeo.enrich(external_id="p1")
		self.assertEqual(prospeo.charged, 0.0)  # a repeat within 90 days is free
		with patch.object(frappe.db, "set_value"), post(400, {"error": True, "error_code": "INSUFFICIENT_CREDITS"}):
			with self.assertRaises(tools.ToolRefused):
				prospeo.enrich(external_id="p1")

	def test_the_key_never_comes_back(self):
		prospeo = self.tool(pt.Prospeo)
		with post(400, {"error": True, "error_code": "INVALID_REQUEST", "echo": "secret-key"}):
			with self.assertRaises(RuntimeError) as e:
				prospeo.enrich(external_id="p1")
		self.assertNotIn("secret-key", str(e.exception))

	def test_budget_counts_only_what_is_billed(self):
		name = "prospeo"
		before = frappe.db.get_value("Excom AI Tool", name, ["enabled", "monthly_budget", "used_this_month"], as_dict=True)
		self.addCleanup(lambda: (frappe.db.set_value("Excom AI Tool", name, dict(before)), frappe.db.commit()))
		doc = frappe.get_doc("Excom AI Tool", name)
		doc.update({"enabled": 1, "api_key": "secret-key", "monthly_budget": 10, "used_this_month": 1})
		doc.save(ignore_permissions=True)
		with post(400, {"error": True, "error_code": "NO_MATCH"}):
			tools.use(name, "enrich", email="who@acme.qa-pf.example")
		self.assertEqual(frappe.db.get_value("Excom AI Tool", name, "used_this_month"), 1)
		with post(200, {"error": False, "person": {"person_id": "p9", "full_name": "A B", "email": {"status": "VERIFIED",
				"revealed": True, "email": "a.b@acme.qa-pf.example"}}}):
			tools.use(name, "enrich", external_id="p9")
		self.assertAlmostEqual(frappe.db.get_value("Excom AI Tool", name, "used_this_month"), 1 + pt.PROSPEO_CREDIT)
		frappe.db.set_value("Excom AI Tool", name, "used_this_month", 10)
		with self.assertRaises(tools.ToolRefused):
			tools.use(name, "enrich", external_id="p9")


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA PF%"]}, pluck="name")
	comps = frappe.get_all("Excom AI Company", {"company_name": ["like", "QA PF%"]}, pluck="name")
	frappe.db.delete("Excom AI Observation", {"company": ["in", comps or ["-"]]})
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
	for c in frappe.db.sql_list("""SELECT DISTINCT parent FROM `tabContact Email` WHERE email_id LIKE %s""", "%qa-pf.example"):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	from excom.excom_ai.tests.test_ai_records import _delete_lead

	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-pf.example"]}, pluck="name"):
		_delete_lead(lead)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	purge_users(OWNER, OUTSIDER)
	frappe.db.commit()


class TestFindPeople(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, OUTSIDER):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [{"user": OWNER, "role": "Manager"}]}).insert(
			ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		frappe.flags.excom_ai_fake_tools = True
		self.addCleanup(setattr, frappe.flags, "excom_ai_fake_tools", False)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)
		comps = frappe.get_all("Excom AI Company", {"company_name": ["like", "QA PF%"]}, pluck="name")
		frappe.db.delete("Excom AI Person", {"company": ["in", comps or ["-"]]})
		frappe.db.delete("Excom AI Company", {"name": ["in", comps or ["-"]]})
		frappe.db.commit()

	def agent(self):
		name = frappe.db.get_value("Excom AI Agent", {"agent_name": "QA PF Ana"})
		if name:
			return frappe.get_doc("Excom AI Agent", name)
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": "QA PF Ana", "agent_type": "Sales", "owner_user": OWNER,
			"team": TEAM, "audience": "importers", "markets": [{"country": "Mexico", "language": "es"}]}).insert(
			ignore_permissions=True)
		a.db_set("status", "Shadow")
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def company(self, agent, domain=DOMAIN, **values):
		return frappe.get_doc({"doctype": "Excom AI Company", "company_name": f"QA PF {domain}", "domain": domain,
			"website": f"https://{domain}", "agent": agent.name, "status": "Contacts found", "fit_score": 80,
			"contacts_checked_on": frappe.utils.now_datetime(), "language": "es", **values}).insert(ignore_permissions=True)

	def run_now(self, task):
		frappe.db.set_value("Excom AI Task", task, "status", "Running")
		frappe.db.commit()
		runner.run_task(task)
		row = frappe.db.get_value("Excom AI Task", task, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	def people(self, company):
		return {p.first_name: p for p in frappe.get_all("Excom AI Person", {"company": company}, ["*"])}

	def test_search_then_waterfall(self):
		a = self.agent()
		c = self.company(a)
		specs = [s for s in research.plan(a) if s["kind"] == "find_people"]
		self.assertEqual([s["subject"][1] for s in specs], [c.name])
		out = self.run_now(tasks.add(a.name, "find_people", subject=("Excom AI Company", c.name)))
		self.assertIn("2 people found by Apollo", out)
		people = self.people(c.name)
		self.assertEqual(set(people), {"Carlos", "Lucía"})  # the intern with no email is skipped
		carlos, lucia = people["Carlos"], people["Lucía"]
		self.assertEqual((carlos.apollo_id, carlos.email_source, carlos.provider_email_status, carlos.linkedin_url),
			("ap-1", "Apollo", "Verified", "https://www.linkedin.com/in/carlos-ramirez-qa"))
		# Apollo had Lucía's LinkedIn but no address: Prospeo found it from the LinkedIn URL
		self.assertEqual((lucia.apollo_id, lucia.prospeo_id, lucia.email_source, lucia.email),
			("ap-2", "pr-2", "Prospeo", f"lucia.nunez@{DOMAIN}"))
		self.assertTrue(frappe.db.get_value("Excom AI Company", c.name, "people_checked_on"))
		self.assertEqual(frappe.db.count("Excom AI Task", {"agent": a.name, "kind": "verify_email", "status": "Queued",
			"subject_name": ["in", [carlos.name, lucia.name]]}), 2)
		# once per company: not planned again, and a second run spends nothing
		self.assertFalse([s for s in research.plan(a) if s["kind"] == "find_people"])
		with patch.object(tools, "use") as use:
			self.assertIn("already searched", research.find_people(frappe._dict(subject_name=c.name), a))
		use.assert_not_called()

	def test_linkedin_only_person_is_kept_but_never_written_to(self):
		a = self.agent()
		c = self.company(a)
		with patch.object(pt.Prospeo, "fake_enrich", return_value=None):
			self.run_now(tasks.add(a.name, "find_people", subject=("Excom AI Company", c.name)))
		lucia = self.people(c.name)["Lucía"]
		self.assertEqual((lucia.email, lucia.linkedin_url), (None, "https://www.linkedin.com/in/lucia-nunez-qa"))
		self.assertFalse(frappe.db.exists("Excom AI Task", {"kind": "verify_email", "subject_name": lucia.name}))
		self.assertFalse([s for s in research.plan(a) if s["kind"] == "verify_email" and s["subject"][1] == lucia.name])
		self.assertIn("no address", research.verify_email(frappe._dict(subject_name=lucia.name), a))
		with self.assertRaises(frappe.ValidationError):
			research.promote(lucia.name, a)
		with self.assertRaises(frappe.ValidationError):  # an address or a LinkedIn URL, at least
			frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "first_name": "Nobody"}).insert(ignore_permissions=True)

	def test_known_people_are_updated_not_doubled(self):
		a = self.agent()
		c = self.company(a)
		frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": f"carlos.ramirez@{DOMAIN}",
			"email_source": "Hunter", "first_name": "Carlos", "status": "New"}).insert(ignore_permissions=True)
		self.run_now(tasks.add(a.name, "find_people", subject=("Excom AI Company", c.name)))
		rows = frappe.get_all("Excom AI Person", {"company": c.name, "first_name": "Carlos"}, ["email_source", "apollo_id",
			"linkedin_url"])
		self.assertEqual(len(rows), 1)  # the same address: the row we had gains the LinkedIn link and Apollo's id
		self.assertEqual((rows[0].email_source, rows[0].apollo_id, rows[0].linkedin_url),
			("Hunter", "ap-1", "https://www.linkedin.com/in/carlos-ramirez-qa"))

	def test_an_excluded_company_spends_nothing(self):
		a = self.agent()
		c = self.company(a, domain="dnc.qa-pf.example")
		settings = frappe.get_single("Excom AI Settings")
		settings.append("dnc_domains", {"domain": "dnc.qa-pf.example", "reason": "Competitor"})
		settings.save(ignore_permissions=True)
		self.addCleanup(lambda: (frappe.db.delete("Excom AI DNC Domain", {"domain": "dnc.qa-pf.example"}), frappe.db.commit()))
		with patch.object(tools, "use") as use:
			out = research.find_people(frappe._dict(subject_name=c.name), a)
		use.assert_not_called()
		self.assertIn("excluded", out)

	def test_provider_refusals_fall_through_and_never_fail_the_task(self):
		a = self.agent()
		c = self.company(a)
		with patch.object(pt.Apollo, "fake_search", side_effect=tools.ToolRefused("no master key")):
			out = self.run_now(tasks.add(a.name, "find_people", subject=("Excom AI Company", c.name)))
		self.assertIn("by Prospeo", out)
		self.assertEqual(self.people(c.name)["Marta"].prospeo_id, "pr-1")
		c2 = self.company(a, domain="b.qa-pf.example")
		with patch.object(pt.Apollo, "fake_enrich", side_effect=RuntimeError("Apollo: HTTP 500")):
			out = self.run_now(tasks.add(a.name, "find_people", subject=("Excom AI Company", c2.name)))
		self.assertTrue(frappe.db.get_value("Excom AI Company", c2.name, "people_checked_on"))

	def test_verified_by_the_provider_only_when_no_verifier_answered(self):
		a = self.agent()
		c = self.company(a)
		p = frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": f"x.y@{DOMAIN}", "first_name": "X",
			"email_source": "Apollo", "provider_email_status": "Verified", "status": "New"}).insert(ignore_permissions=True)
		frappe.flags.excom_ai_fake_tools = False
		with patch.object(tools, "available", return_value=False):
			research.verify_email(frappe._dict(subject_name=p.name), a)
		row = frappe.db.get_value("Excom AI Person", p.name, ["email_status", "verified_by", "status"], as_dict=True)
		self.assertEqual((row.email_status, row.verified_by, row.status), ("Verified", "Apollo", "Ready"))
		frappe.db.set_value("Excom AI Person", p.name, {"email_status": "Unknown", "status": "New"})
		with patch.object(tools, "available", side_effect=lambda k: k == "reoon"), patch.object(tools, "use",
				return_value={"status": "Invalid"}):
			research.verify_email(frappe._dict(subject_name=p.name), a)
		self.assertEqual(frappe.db.get_value("Excom AI Person", p.name, ["email_status", "verified_by"]), ("Invalid", "Reoon"))

	def test_footer_names_the_provider(self):
		a = self.agent()
		c = self.company(a)
		frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": f"f.g@{DOMAIN}", "first_name": "F",
			"email_source": "Prospeo", "status": "New"}).insert(ignore_permissions=True)
		real = frappe.db.get_value
		with patch.object(frappe.db, "get_value", side_effect=lambda dt, *args, **kw: f"F.G@{DOMAIN}"
				if dt == "Omni Identity" else real(dt, *args, **kw)):
			self.assertEqual(mail.data_source("ID-1"), "Prospeo.io")
		self.assertIn("Prospeo.io", mail.footer("ID-1", "", "Prospeo.io"))
		self.assertNotIn("contact database", mail.footer("ID-1", "", None))

	# ---- the Find people button

	def lead(self):
		existing = frappe.db.get_value("Lead", {"email_id": f"buyer@{LEAD_DOMAIN}"})
		if existing:
			return existing
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA PF Buyer", "company_name": "QA PF Acme",
			"email_id": f"buyer@{LEAD_DOMAIN}", "lead_owner": OWNER}).insert(ignore_permissions=True)
		if lead.meta.has_field("excom_team"):
			lead.db_set("excom_team", TEAM)
		frappe.db.commit()
		return lead.name

	def test_find_and_add_people_on_a_lead(self):
		lead = self.lead()
		self.assertEqual(people_finder.record_domain("Lead", lead), LEAD_DOMAIN)
		found = people_finder.find_people("Lead", lead)
		self.assertEqual(found["provider"], "Apollo")
		names = [c["full_name"] for c in found["candidates"]]
		self.assertIn("Carlos", names)
		self.assertNotIn("Pedro", names)  # Marketing Intern: a title to skip
		carlos = next(c["pick"] for c in found["candidates"] if c["full_name"] == "Carlos")
		self.assertEqual(carlos, "Apollo:ap-1")  # the candidate's own id, never a place in a list
		with patch.object(research, "enrich_one", wraps=research.enrich_one) as enrich:
			out = people_finder.add_people("Lead", lead, frappe.as_json([carlos]))
			again = people_finder.add_people("Lead", lead, [carlos])
		self.assertEqual(enrich.call_count, 1)  # looked up once: the second add pays nothing
		self.assertEqual([a["email"] for a in out["added"]], [f"carlos.ramirez@{LEAD_DOMAIN}"])
		contact = frappe.get_doc("Contact", out["added"][0]["contact"])
		self.assertEqual(contact.ai_linkedin_url, "https://www.linkedin.com/in/carlos-ramirez-qa")
		self.assertIn("Apollo", contact.ai_found_via)
		self.assertTrue(any(l.link_doctype == "Lead" and l.link_name == lead for l in contact.links))
		self.assertTrue(again["added"][0]["reused"])  # already on file: linked, not copied
		with self.assertRaises(frappe.ValidationError):
			people_finder.add_people("Lead", lead, ["Apollo:somebody-else"])
		with self.assertRaises(frappe.ValidationError):
			people_finder.add_people("Lead", lead, [f"Apollo:{i}" for i in range(6)])
		# a new search knows the contact by LinkedIn: free from now on
		found = people_finder.find_people("Lead", lead)
		self.assertEqual(next(c["known_email"] for c in found["candidates"] if c["pick"] == carlos),
			f"carlos.ramirez@{LEAD_DOMAIN}")

	def test_only_checked_research_addresses_are_reused(self):
		a = self.agent()
		c = self.company(a)
		p = frappe.get_doc({"doctype": "Excom AI Person", "company": c.name, "email": f"guess@{DOMAIN}", "first_name": "G",
			"apollo_id": "ap-9", "email_source": "Pattern", "email_status": "Guess", "status": "New"}).insert(ignore_permissions=True)
		cand = {"provider": "Apollo", "external_id": "ap-9"}
		self.assertEqual(people_finder._known(cand), (None, None))  # a pattern guess is not handed out
		p.db_set({"email_status": "Verified", "email_source": "Hunter"})
		self.assertEqual(people_finder._known(cand), (f"guess@{DOMAIN}", "Hunter"))
		p.db_set("consent_verdict", "Blocked")
		self.assertEqual(people_finder._known(cand), (None, None))

	def test_only_picks_from_the_kept_list(self):
		lead = self.lead()
		frappe.cache.delete_value(people_finder._key("Lead", lead))
		with self.assertRaises(frappe.ValidationError):
			people_finder.add_people("Lead", lead, ["Apollo:ap-1"])

	def test_who_may_find_people(self):
		lead = self.lead()
		with self.assertRaises(frappe.ValidationError):
			people_finder.find_people("Customer", "anything")
		frappe.set_user(OUTSIDER)
		try:
			with patch("excom.excom.services.crm_visibility.is_enforced", return_value=True), \
					self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				people_finder.find_people("Lead", lead)
		finally:
			frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "people_on_demand", 0)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "people_on_demand", 1)
		with self.assertRaises(frappe.ValidationError):
			people_finder.find_people("Lead", lead)

	def test_daily_limit(self):
		lead = self.lead()
		people_finder.find_people("Lead", lead)
		frappe.db.set_single_value("Excom AI Settings", "people_daily_per_user", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "people_daily_per_user", 25)
		day = frappe.cache.make_key(f"excom_ai_people_day:{frappe.session.user}:{frappe.utils.nowdate()}")
		frappe.cache.set(day, 1)
		self.addCleanup(frappe.cache.delete, day)
		with self.assertRaises(frappe.ValidationError):
			people_finder.add_people("Lead", lead, ["Apollo:ap-2"])
		self.assertEqual(frappe.cache.get(day), b"1")  # a refused call gives its count back
