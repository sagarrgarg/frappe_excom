"""Excom AI Phase 1F: the responder (HLD-007 §11, Appendix A). Rules before the model, routing by
intent, interested buyers into Opportunities, replies proposed and sent on their conversation,
new enquiries qualified, and the reply-intents eval set. The model is ai_interface's scripted adapter."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import responder
from excom.excom_ai.engine import ownership, proposals, runner, tasks

OWNER = "qa.rsp.owner@example.com"
BACKUP = "qa.rsp.backup@example.com"
TEAM = "QA Responder Desk"
BOX = "QA Responder Box"
PTYPE, PROVIDER = "_TR Scripted", "_TR Responder"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Rsp%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment", "Excom AI Sequence"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-rsp.example"]}, pluck="name"):
		for o in frappe.get_all("Opportunity", {"party_name": lead}, pluck="name"):
			frappe.delete_doc("Opportunity", o, force=True, ignore_permissions=True)
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	accs = frappe.get_all("Excom Channel Account", {"account_name": BOX}, pluck="name")
	ids = set()
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in ids | set(frappe.get_all("Omni Identity", {"primary_email": ["like", "%qa-rsp.example"]}, pluck="name")):
		if oi and frappe.db.exists("Omni Identity", oi):
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
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
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP]]})
	purge_users(OWNER, BACKUP)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestResponder(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent", "Sales User", "Sales Manager")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": "export@qa-rsp-ours.example"}).insert(ignore_permissions=True)
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
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Rsp {self.n}", "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "customer_types": "Export Importer",
			"channels": [{"channel_account": self.box.name, "purpose": "Outreach and replies"}]}).insert(ignore_permissions=True)
		a.db_set({"status": "Live", "brief_approved_on": frappe.utils.now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def conversation(self, agent, text, email=None):
		"""A buyer (a Lead, as promotion leaves them) who wrote `text` on the agent's mailbox."""
		from excom.excom.services.thread_service import upsert_thread

		email = email or f"buyer{self.n}@shop{self.n}.qa-rsp.example"
		lead = frappe.get_doc({"doctype": "Lead", "first_name": f"QA Rsp Buyer {self.n}", "email_id": email,
			"company_name": f"QA Rsp Shop {self.n}"}).insert(ignore_permissions=True)
		identity = frappe.db.get_value("Omni Identity", {"primary_email": email}) or frappe.get_doc({"doctype": "Omni Identity",
			"display_name": f"QA Rsp Buyer {self.n}", "primary_email": email}).insert(ignore_permissions=True).name
		thread = upsert_thread(identity, "email", self.box.name)
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle", "ai_stage": "Initial outreach"})
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": identity, "direction": "Inbound", "message_type": "Email",
			"content_text": text, "content_json": json.dumps({"subject": "water guns"}), "delivery_status": "Delivered"}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		return frappe._dict(lead=lead.name, identity=identity, thread=thread, message=msg.name)

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def respond(self, agent, c, *answers, automatic=False):
		t = tasks.add(agent.name, "inbound", thread=c.thread, dedupe_key=f"inb{self.n}",
			params={"message": c.message, "channel": "email", "automatic": automatic})
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		with self.scripted(*answers):
			runner.run_task(t)
		row = frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	# ---- the rules

	def test_rules_before_the_model(self):
		self.assertEqual(responder.rule_intent("Please remove me from your list"), "unsubscribe")
		self.assertEqual(responder.rule_intent("STOP"), "unsubscribe")
		self.assertEqual(responder.rule_intent("No thanks"), "unsubscribe")
		self.assertIsNone(responder.rule_intent("Yes or no, thanks for asking: send the catalogue"))
		self.assertEqual(responder.rule_intent("Por favor denme de baja"), "unsubscribe")
		self.assertIsNone(responder.rule_intent("Los juguetes de baja calidad no nos sirven"), "low quality is not an opt-out")
		self.assertEqual(responder.rule_intent("I will report you under GDPR"), "escalate")
		self.assertIsNone(responder.rule_intent("Can you send samples?"))
		self.assertEqual(responder.clean("Sounds good, send samples.\n\nOn Mon, 9 Nov 2026 Lucía wrote:\n> Hola"),
			"Sounds good, send samples.")

	# ---- routing

	def test_an_opt_out_is_honoured_without_a_reply(self):
		a = self.agent()
		c = self.conversation(a, "Please take me off your mailing list.")
		self.assertIn("opted out", self.respond(a, c))
		self.assertEqual(frappe.db.get_value("Omni Identity", c.identity, "email_opt_out"), 1)
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, ["ai_mode", "ai_intent"]), ("Hold", "unsubscribe"))
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"thread": c.thread}))

	def test_price_talk_goes_to_a_person(self):
		a = self.agent()
		c = self.conversation(a, "What is your price per piece and the minimum order to Monterrey?")
		out = self.respond(a, c, {"intent": "question", "language": "en", "summary": "asks price", "asks_price": True})
		self.assertIn("negotiate", out)
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, "ai_mode"), "Needs person")
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"thread": c.thread}), "the agent never answers price talk")

	def test_an_interested_buyer_becomes_an_opportunity_and_gets_a_drafted_reply(self):
		a = self.agent()
		c = self.conversation(a, "These look good for our summer range. Can you send two samples to Leeds?")
		reply = "Gracias. Le preparo el envío con mi colega, que le confirma la fecha. ¿A qué dirección en Leeds lo enviamos?"
		out = self.respond(a, c, {"intent": "interested", "language": "en", "summary": "wants samples", "asks_price": False},
			{"body": "Thank you. My colleague will confirm the samples and the date. Which address in Leeds should they go to?"},
			{"verdict": "Pass", "notes": "ok", "subject": "Re: water guns", "body": reply})
		self.assertIn("interested: reply proposed", out)
		self.assertEqual(frappe.db.get_value("Lead", c.lead, "customer_type"), "Export Importer", "classified first")
		opp = frappe.db.get_value("Opportunity", {"party_name": c.lead}, ["name", "ai_origin_agent"], as_dict=True)
		self.assertTrue(opp)
		self.assertEqual(opp.ai_origin_agent, a.name)
		p = frappe.get_doc("Excom AI Proposal", {"thread": c.thread, "action": "reply"})
		self.assertEqual((p.status, p.intent, p.kind), ("Pending", "interested", "Reply"))
		self.assertEqual(json.loads(p.payload)["subject"], "Re: water guns")
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "document_name": c.thread}))
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, "ai_stage"), "Engaged")

		# sending the approved reply: on its conversation, threaded on the buyer's email
		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(p.name, "approve"), "Approved", "a reply waits for the sender too")
		frappe.set_user("Administrator")
		with patch("excom.excom.channels.email.outbound.send_email_reply", return_value=c.message) as send:
			proposals.execute(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")
		self.assertEqual(send.call_args.kwargs["to"], frappe.db.get_value("Lead", c.lead, "email_id"))

	def test_an_out_of_office_keeps_the_sequence_going(self):
		a = self.agent()
		c = self.conversation(a, "I am out of the office until Monday.")
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": f"QA Rsp seq {self.n}", "agent": a.name,
			"steps": [{"step": 1}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "thread": c.thread,
			"status": "Replied", "stop_reason": "Replied on email"}).insert()
		out = self.respond(a, c, {"intent": "ooo", "language": "en", "summary": "away", "asks_price": False})
		self.assertIn("automatic reply", out)
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", enr.name, "status"), "Active")

	def test_not_interested_closes_it_for_the_agent(self):
		a = self.agent()
		c = self.conversation(a, "Thank you, but we are not interested at this time.")
		self.respond(a, c, {"intent": "not_interested", "language": "en", "summary": "no", "asks_price": False})
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, ["ai_mode", "ai_stage"]), ("Hold", "Closed lost"))

	def test_autopilot_never_replies_on_a_conversation_a_person_took(self):
		a = self.agent()
		c = self.conversation(a, "hello")
		frappe.db.set_value("Excom Thread", c.thread, "ai_mode", "Hold")
		t = frappe.get_doc("Excom AI Task", tasks.add(a.name, "echo", dedupe_key=f"hold{self.n}"))
		with patch.object(proposals, "execute"):
			p = proposals.propose(t, a, "reply", thread=c.thread, payload={"body": "hola"}, subject=("Excom Thread", c.thread))
		frappe.db.set_value("Excom AI Proposal", p, {"status": "Approved", "decided_by": "Autopilot"})
		with patch("excom.excom.channels.email.outbound.send_email_reply") as send:
			proposals.execute(p)
		send.assert_not_called()
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p, "status"), "Cancelled")

	# ---- new enquiries

	def test_a_new_enquiry_is_qualified_for_the_desks_agent(self):
		a = self.agent()
		c = self.conversation(a, "enquiry")
		if frappe.get_meta("Lead").has_field("excom_team"):
			frappe.db.set_value("Lead", c.lead, "excom_team", TEAM)
		frappe.db.set_value("Excom Thread", c.thread, {"ai_agent": None, "ai_mode": None})
		frappe.get_doc("Lead", c.lead).add_comment("Comment", "We import toys to Mexico and want 2 containers of water guns.")
		responder.on_enquiry({"lead": c.lead, "thread": c.thread, "created": True})
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, "ai_agent"), a.name)
		t = frappe.get_value("Excom AI Task", {"agent": a.name, "kind": "qualify_enquiry", "subject_name": c.lead})
		self.assertTrue(t)
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		with self.scripted({"customer_type": "Export Importer", "summary": "imports toys to Mexico", "language": "en", "wants": "water guns"},
				{"body": "Thank you for your enquiry. Which models interest you most?"},
				{"verdict": "Pass", "notes": "ok", "subject": "re", "body": "Thank you for your enquiry. Which models interest you most?"}):
			runner.run_task(t)
		self.assertEqual(frappe.db.get_value("Excom AI Task", t, "status"), "Done")
		upd = frappe.get_doc("Excom AI Proposal", {"subject_name": c.lead, "action": "update_lead"})
		self.assertEqual(json.loads(upd.payload)["customer_type"], "Export Importer")
		self.assertTrue(frappe.db.exists("Excom AI Proposal", {"thread": c.thread, "action": "reply"}))

	def test_the_reply_intents_eval_set(self):
		from excom.excom_ai import evals

		evals.seed()
		es = frappe.get_doc("AI Eval Set", evals.SET)
		self.assertGreaterEqual(len(es.cases), 25)
		self.assertEqual(es.target, "excom.excom_ai.responder.eval_classify")
		from ai_interface.services.evals import check_target

		check_target(es.target)  # declared in excom's hooks
		self.assertEqual(responder.eval_classify({"text": "STOP"}), {"intent": "unsubscribe"})
