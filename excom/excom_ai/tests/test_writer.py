"""Excom AI Phase 1D: the Writer, the code checks, the AI checker, Needs you and the Shadow
comparison (HLD-007 §10.2, §12.5, §15). The model is ai_interface's scripted adapter."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import checks, needs_you, writer
from excom.excom_ai.engine import ownership, runner, tasks

OWNER = "qa.wri.owner@example.com"
BACKUP = "qa.wri.backup@example.com"
OUTSIDER = "qa.wri.outsider@example.com"
TEAM = "QA Writer Desk"
BOX = "QA Writer Box"
BUYER = "compras@tienda.qa-wri.example"
PTYPE, PROVIDER = "_TW Scripted", "_TW Writer"

ES_1 = ("pistolas de agua para su tienda", "Vi que su tienda vende juguetes de verano en Monterrey. Fabricamos pistolas de agua "
	"en la India y las enviamos a distribuidores de la región. ¿Le interesa ver el catálogo de esta temporada?")
ES_2 = ("modelos para el verano", "Tenemos modelos pequeños y grandes con fotos y medidas para su catálogo. ¿Qué tamaños vende más?")
ES_3 = ("una pregunta corta", "¿Prefiere que le escriba más adelante en la temporada?")


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Wri%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment", "Excom AI Sequence"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	comps = frappe.get_all("Excom AI Company", {"company_name": ["like", "QA Wri%"]}, pluck="name")
	frappe.db.delete("Excom AI Person", {"company": ["in", comps or ["-"]]})
	frappe.db.delete("Excom AI Company", {"name": ["in", comps or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-wri.example"]}, pluck="name"):
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	accs = frappe.get_all("Excom Channel Account", {"account_name": BOX}, pluck="name")
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, pluck="name"):
		frappe.db.delete("Excom Message", {"thread": t})
		frappe.db.delete("Comment", {"reference_name": t})
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
	frappe.db.delete("Excom AI Postal Address", {"address": ["like", "QA Wri%"]})
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP]]})
	purge_users(OWNER, BACKUP, OUTSIDER)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestWriter(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP, OUTSIDER):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": "export@qa-wri-ours.example"}).insert(ignore_permissions=True)
		cls.company = frappe.db.get_value("Company", {}, "name")
		s = frappe.get_single("Excom AI Settings")
		s.append("postal_addresses", {"company": cls.company, "address": "QA Wri Plot 1, Delhi, India"})
		s.save(ignore_permissions=True)
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
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)

	def agent(self, name="QA Wri Lucía", status="Live"):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": name, "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "company": self.company,
			"markets": [{"country": "Mexico", "language": "es", "timezone": "America/Mexico_City"}],
			"channels": [{"channel_account": self.box.name, "purpose": "Outreach and replies"}]}).insert(ignore_permissions=True)
		a.db_set({"status": status, "brief_approved_on": frappe.utils.now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def buyer(self, agent, email=BUYER):
		company = frappe.get_doc({"doctype": "Excom AI Company", "company_name": f"QA Wri {email}", "domain": email.split("@")[1],
			"country": "Mexico", "agent": agent.name, "status": "Contacts found", "language": "es",
			"research_notes": "toy shop, summer range"}).insert(ignore_permissions=True)
		person = frappe.get_doc({"doctype": "Excom AI Person", "company": company.name, "email": email, "is_inbox": 1,
			"email_status": "Verified", "status": "Ready", "language": "es"}).insert(ignore_permissions=True)
		from excom.excom_ai import research

		lead = research.promote(person.name, agent)["lead"]
		seq = writer.default_sequence(agent)
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": agent.name, "sequence": seq, "lead": lead,
			"person": person.name, "company": company.name, "language": "es"}).insert(ignore_permissions=True)
		frappe.db.commit()
		return enr

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(a if isinstance(a, str) else json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def run_now(self, task):
		frappe.db.set_value("Excom AI Task", task, "status", "Running")
		frappe.db.commit()
		runner.run_task(task)
		row = frappe.db.get_value("Excom AI Task", task, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	# ---- the checks

	def test_code_checks(self):
		a = self.agent("QA Wri Checks")
		ok = checks.check(a, BUYER, ES_1[0], ES_1[1], kind="Reply", account=self.box.name, language="es")
		self.assertTrue(ok["ok"], checks.failures(ok))
		cases = {
			"recipient": dict(to_email="not an email"),
			"phrases": dict(body=ES_1[1] + " Act now."),
			"language": dict(body="We make water guns in India and ship them to distributors in your region. Would you like the catalogue?"),
			"shape": dict(subject="x" * 95),
			"signature": dict(body=ES_1[1] + "\n\nSaludos,\nQA Wri Checks"),
		}
		for name, kw in cases.items():
			args = {"to_email": BUYER, "subject": ES_1[0], "body": ES_1[1], **kw}
			r = checks.check(a, args["to_email"], args["subject"], args["body"], kind="Reply", account=self.box.name, language="es")
			self.assertFalse(r[name]["ok"], name)
		human = checks.check(a, BUYER, "hola", "No soy un bot, soy una persona real de la fábrica. ¿Le interesa?", kind="Reply", language="es")
		self.assertFalse(human["phrases"]["ok"], "never claims to be a person")
		tags = checks.check(a, BUYER, "hola {{first_name}}", ES_1[1], kind="Reply", language="es")
		self.assertFalse(tags["shape"]["ok"])
		price = checks.check(a, BUYER, ES_1[0], "Le ofrecemos el modelo grande a US$ 2.35 por pieza. ¿Le interesa?", kind="Reply", language="es")
		self.assertFalse(price["prices"]["ok"], "a price not from the agent's price lists")
		leak = checks.check(a, BUYER, "x", checks.WRITER_RULES.split("\n")[10], kind="Reply")
		self.assertFalse(leak["leak"]["ok"])

	def test_cold_mail_needs_a_verified_address_footer_and_no_customer(self):
		a = self.agent("QA Wri Cold")
		enr = self.buyer(a, "ventas@cold.qa-wri.example")
		person = frappe.get_doc("Excom AI Person", enr.person)
		identity = frappe.db.get_value("Omni Identity", {"primary_email": person.email})
		html = writer.compose(a, ES_1[1], None, identity, "Sequence")
		good = checks.check(a, person.email, ES_1[0], ES_1[1], person=person, lead=enr.lead, account=self.box.name,
			language="es", composed_html=html)
		self.assertTrue(good["ok"], checks.failures(good))
		no_footer = checks.check(a, person.email, ES_1[0], ES_1[1], person=person, lead=enr.lead, account=self.box.name,
			language="es", composed_html="<p>x</p>")
		self.assertFalse(no_footer["footer"]["ok"])
		person.db_set("email_status", "Guess")
		person.reload()
		self.assertFalse(checks.check(a, person.email, ES_1[0], ES_1[1], person=person, lead=enr.lead, language="es",
			composed_html=html)["recipient"]["ok"], "a guessed address is not written to cold")

	# ---- the writer

	def test_a_sequence_is_written_checked_and_proposed(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		a = self.agent()
		enr = self.buyer(a)
		seq = {"language": "es", "emails": [{"step": 1, "subject": ES_1[0], "body": ES_1[1]},
			{"step": 2, "subject": ES_2[0], "body": ES_2[1] + " Act now."}, {"step": 3, "subject": ES_3[0], "body": ES_3[1]}]}
		with self.scripted(seq,
				{"verdict": "Pass", "notes": "fine", "subject": ES_1[0], "body": ES_1[1]},
				{"subject": ES_2[0], "body": ES_2[1]},  # the rewrite of step 2 (it used a banned phrase)
				{"verdict": "Pass", "notes": "fine", "subject": ES_2[0], "body": ES_2[1]},
				{"verdict": "Fail", "notes": "too vague", "subject": ES_3[0], "body": ES_3[1]}):
			t = tasks.add(a.name, "write_sequence", subject=("Excom AI Enrollment", enr.name))
			self.assertIn("3 emails written in es", self.run_now(t))
		props = {p.step: p for p in frappe.get_all("Excom AI Proposal", {"enrollment": enr.name},
			["name", "step", "status", "checks", "checker_verdict", "preview_html", "policy_action", "risk", "payload"])}
		self.assertEqual(sorted(props), [1, 2, 3])
		self.assertEqual((props[1].status, props[1].policy_action), ("Pending", "first_email"))
		self.assertEqual(props[2].policy_action, "follow_up_email")
		self.assertTrue(json.loads(props[1].checks)["ok"])
		self.assertNotIn("Act now", json.loads(props[2].payload)["body"], "the rewrite fixed the banned phrase")
		self.assertEqual((props[3].checker_verdict, props[3].risk), ("Fail", "High"))
		self.assertIn("excom.excom_ai.mail.unsubscribe", props[1].preview_html)
		self.assertIn("QA Wri Plot 1", props[1].preview_html)

		# Needs you: the owner sees them; bulk approval skips the one the checker failed
		frappe.set_user(OWNER)
		q = needs_you.queue()
		self.assertEqual({p.name for p in q["proposals"] if p.enrollment == enr.name}, {p.name for p in props.values()})
		with patch("excom.excom_ai.engine.proposals.execute"):
			out = needs_you.approve_step(a.name)
		self.assertEqual(set(out["done"]), {props[1].name, props[2].name})
		self.assertEqual(out["skipped_failing_checks"], 1)
		frappe.set_user(OUTSIDER)
		self.assertFalse(needs_you.queue()["proposals"], "someone outside the agent's people sees nothing")
		frappe.set_user("Administrator")

	def test_follow_ups_never_go_on_autopilot_when_a_check_fails(self):
		a = self.agent("QA Wri Auto")
		frappe.db.set_value("Excom AI Policy", {"parent": a.name, "action": "follow_up_email"}, "mode", "Auto")
		a.reload()
		enr = self.buyer(a, "auto@auto.qa-wri.example")
		from excom.excom_ai.engine import proposals

		t = frappe.get_doc("Excom AI Task", tasks.add(a.name, "echo", dedupe_key="autofail"))
		with patch.object(proposals, "execute"):
			p = proposals.propose(t, a, "send_email", policy_action="follow_up_email", enrollment=enr.name, step=2, checks_ok=False)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p, "status"), "Pending")

	# ---- conversations and the Shadow comparison

	def test_take_over_hand_back_and_the_shadow_comparison(self):
		a = self.agent("QA Wri Shadow", status="Shadow")
		from excom.excom.services.thread_service import upsert_thread
		from excom.excom_ai.engine import proposals

		identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "QA Wri Buyer", "primary_email": "chat@qa-wri.example"}
			).insert(ignore_permissions=True)
		thread = upsert_thread(identity.name, "email", self.box.name)
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": a.name, "ai_mode": "Handle", "assigned_to": OWNER})
		t = frappe.get_doc("Excom AI Task", tasks.add(a.name, "echo", dedupe_key="shadowcmp"))
		p = proposals.propose(t, a, "reply", thread=thread, payload={"subject": "re", "body": "Hola, sí tenemos stock."},
			subject=("Excom Thread", thread))
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p, "status"), "Shadow")
		frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": identity.name, "direction": "Outbound",
			"message_type": "Email", "content_text": "Hola, sí, tenemos stock para junio.", "delivery_status": "Sent",
			"created_by_user": OWNER}).insert(ignore_permissions=True)
		frappe.set_user(OWNER)
		rows = needs_you.shadow_comparison(a.name)
		row = next(r for r in rows if r.name == p)
		self.assertIn("junio", row.person_sent.content_text)
		needs_you.rate_shadow(p, "About the same", "close enough")
		self.assertEqual(needs_you.shadow_score(a.name)["About the same"], 1)
		self.assertEqual(needs_you.take_over(thread)["ai_mode"], "Hold")
		self.assertEqual(frappe.db.get_value("Excom Thread", thread, "assigned_to"), OWNER)
		self.assertEqual(needs_you.hand_back(thread)["ai_mode"], "Handle")
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			needs_you.take_over(thread)
		frappe.set_user("Administrator")
		frappe.db.delete("Excom Message", {"thread": thread})
		frappe.delete_doc("Excom Thread", thread, force=True, ignore_permissions=True)
		frappe.delete_doc("Omni Identity", identity.name, force=True, ignore_permissions=True)
