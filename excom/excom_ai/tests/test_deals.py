"""Excom AI Phase 1G: deal support (HLD-007 §8.1). A price question gets a Quotation draft and a
reply whose prices ERPNext worked out from the agent's price list, both for a person; quote
follow-ups; reactivation suggestions for the customer's own owner."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import deals, responder
from excom.excom_ai.engine import ownership, proposals, runner, tasks

OWNER = "qa.deal.owner@example.com"
BACKUP = "qa.deal.backup@example.com"
TEAM = "QA Deals Desk"
BOX = "QA Deals Box"
GROUP = "QA Deal Pistols"
PRICE_LIST = "QA Deal USD"
PTYPE, PROVIDER = "_TD Scripted", "_TD Deals"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Deal%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Knowledge"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-deal.example"]}, pluck="name"):
		for q in frappe.get_all("Quotation", {"party_name": lead}, pluck="name"):
			frappe.delete_doc("Quotation", q, force=True, ignore_permissions=True)
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	accs = frappe.get_all("Excom Channel Account", {"account_name": BOX}, pluck="name")
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in frappe.get_all("Omni Identity", {"primary_email": ["like", "%qa-deal.example"]}, pluck="name"):
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.delete("Item Price", {"price_list": PRICE_LIST})
	for item in frappe.get_all("Item", {"item_group": GROUP}, pluck="name"):
		frappe.delete_doc("Item", item, force=True, ignore_permissions=True)
	for dt, name in (("Price List", PRICE_LIST), ("Item Group", GROUP)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.delete("Currency Exchange", {"from_currency": "USD", "to_currency": "INR", "exchange_rate": 83.33})
	for a in accs:
		frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestDeals(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent", "Sales Manager")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": "export@qa-deal-ours.example"}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Item Group", "item_group_name": GROUP, "parent_item_group": "All Item Groups"}).insert(
			ignore_permissions=True)
		hsn = frappe.db.get_value("GST HSN Code", {}) if frappe.db.table_exists("GST HSN Code") else None
		cls.items = {}
		for code, name, rate in (("QA-DEAL-SHARK", "Big Sharky", 1.00), ("QA-DEAL-PULSE", "Pulse", 0.49)):
			frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": name, "item_group": GROUP, "stock_uom": "Nos",
				"is_stock_item": 0, "gst_hsn_code": hsn}).insert(ignore_permissions=True)
			cls.items[code] = rate
		frappe.get_doc({"doctype": "Price List", "price_list_name": PRICE_LIST, "currency": "USD", "selling": 1, "enabled": 1}).insert(
			ignore_permissions=True)
		for code, rate in cls.items.items():
			frappe.get_doc({"doctype": "Item Price", "price_list": PRICE_LIST, "item_code": code, "price_list_rate": rate}).insert(
				ignore_permissions=True)
		frappe.get_doc({"doctype": "Currency Exchange", "date": nowdate(), "from_currency": "USD", "to_currency": "INR",
			"exchange_rate": 83.33, "for_selling": 1}).insert(ignore_permissions=True)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}], api_base_url="main")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		import os

		if not os.environ.get("QA_KEEP"):
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
		company = frappe.db.get_value("Company", {"default_currency": "INR"}, "name")
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Deal {self.n}", "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "company": company, "customer_types": "Export Importer",
			"products": [{"item_group": GROUP, "price_list": PRICE_LIST}],
			"channels": [{"channel_account": self.box.name, "purpose": "Outreach and replies"}]}).insert(ignore_permissions=True)
		a.db_set({"status": "Live", "brief_approved_on": frappe.utils.now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def conversation(self, agent, text):
		from excom.excom.services.thread_service import upsert_thread

		email = f"buyer{self.n}@poolbarn{self.n}.qa-deal.example"
		lead = frappe.get_doc({"doctype": "Lead", "first_name": f"QA Deal Buyer {self.n}", "email_id": email,
			"company_name": f"QA Pool Barn {self.n}", "ai_agent": agent.name}).insert(ignore_permissions=True)
		identity = frappe.db.get_value("Omni Identity", {"primary_email": email}) or frappe.get_doc({"doctype": "Omni Identity",
			"display_name": f"QA Deal Buyer {self.n}", "primary_email": email}).insert(ignore_permissions=True).name
		thread = upsert_thread(identity, "email", self.box.name)
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle"})
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": identity, "direction": "Inbound", "message_type": "Email",
			"content_text": text, "content_json": json.dumps({"subject": "price list"}), "delivery_status": "Delivered"}
			).insert(ignore_permissions=True)
		frappe.db.commit()
		return frappe._dict(lead=lead.name, thread=thread, message=msg.name, email=email)

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def run_now(self, t, *answers):
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		with self.scripted(*answers):
			runner.run_task(t)
		row = frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	# ---- prices from ERPNext

	def test_erpnext_prices_the_lines(self):
		a = self.agent()
		c = self.conversation(a, "price?")
		priced = deals.price(a, "Lead", c.lead, [{"item_code": "QA-DEAL-SHARK", "qty": 500}, {"item_code": "QA-DEAL-PULSE", "qty": 1000}])
		self.assertEqual((priced.price_list, priced.currency), (PRICE_LIST, "USD"))
		self.assertEqual({ln.item_code: ln.rate for ln in priced.lines}, self.items)
		self.assertFalse(priced.missing)
		self.assertFalse(frappe.db.exists("Quotation", {"party_name": c.lead}), "pricing saves nothing")

	# ---- the Pool Barn question

	def test_a_price_question_gets_a_priced_draft_and_a_quotation_for_a_person(self):
		a = self.agent()
		c = self.conversation(a, "Hi, could you send me your price list for Big Sharky and Pulse, and the minimum order to Brisbane?")
		out = self.run_now(tasks.add(a.name, "inbound", thread=c.thread, params={"message": c.message, "channel": "email"}),
			{"intent": "question", "language": "en", "summary": "price list", "asks_price": True})
		self.assertIn("negotiate", out)
		self.assertEqual(frappe.db.get_value("Excom Thread", c.thread, ["ai_mode", "ai_intent"]), ("Needs person", "negotiate"))
		t = frappe.get_value("Excom AI Task", {"agent": a.name, "kind": "draft_quote", "thread": c.thread})
		self.assertTrue(t, "a priced draft is prepared for the person")
		out = self.run_now(t, {"lines": [{"item_code": "QA-DEAL-SHARK", "qty": 500}, {"item_code": "QA-DEAL-PULSE", "qty": 1000},
			{"item_code": "NOT-OURS", "qty": 9}], "destination": "Brisbane", "intro": "Thank you for asking. Here are our prices.",
			"language": "en"})
		self.assertIn("2 lines priced", out)
		quote = frappe.get_doc("Excom AI Proposal", {"thread": c.thread, "action": "create_quotation"})
		reply = frappe.get_doc("Excom AI Proposal", {"thread": c.thread, "action": "reply"})
		self.assertEqual((quote.status, reply.status, reply.policy_action), ("Pending", "Pending", "prices_and_terms"))
		lines = {ln["item_code"]: ln["rate"] for ln in json.loads(quote.payload)["lines"]}
		self.assertEqual(lines, self.items, "only the agent's products, at the price list's prices")
		body = json.loads(reply.payload)["body"]
		self.assertIn("1.00", body)
		self.assertIn("0.49", body)
		self.assertIn("Brisbane", body)
		self.assertTrue(json.loads(reply.checks)["prices"]["ok"], json.loads(reply.checks)["prices"]["why"])

		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(quote.name, "approve"), "Done")
		frappe.set_user("Administrator")
		q = frappe.get_doc("Quotation", frappe.db.get_value("Excom AI Proposal", quote.name, "executed_name"))
		self.assertEqual((q.docstatus, q.selling_price_list, q.currency), (0, PRICE_LIST, "USD"))
		self.assertEqual({i.item_code: i.rate for i in q.items}, self.items)

		# the person approves the priced reply: it goes even though they hold the conversation
		frappe.set_user(OWNER)
		proposals.decide(reply.name, "approve")
		frappe.set_user("Administrator")
		with patch("excom.excom.channels.email.outbound.send_email_reply", return_value=c.message) as send:
			proposals.execute(reply.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", reply.name, "status"), "Sent")
		send.assert_called_once()

	# ---- follow-ups and reactivation

	def test_quote_follow_ups_and_reactivation(self):
		a = self.agent()
		c = self.conversation(a, "thanks")
		q = deals.price(a, "Lead", c.lead, [{"item_code": "QA-DEAL-SHARK", "qty": 100}]).doc
		q.transaction_date = add_days(nowdate(), -8)
		q.insert(ignore_permissions=True)
		specs = deals.plan(a)
		self.assertIn(("quote_follow_up", q.name), [(s["kind"], (s.get("subject") or (None, None))[1]) for s in specs])
		out = self.run_now(tasks.add(a.name, "quote_follow_up", subject=("Quotation", q.name)),
			{"body": "Did the quotation reach the right person? Happy to adjust the models if useful."})
		self.assertIn("follow-up on", out)
		self.assertTrue(frappe.db.exists("Excom AI Proposal", {"subject_name": q.name, "action": "reply", "status": "Pending"}))

		customer = frappe.db.get_value("Customer", {}, "name")
		k = frappe.get_doc({"doctype": "Excom AI Knowledge", "agent": a.name, "version": 1,
			"facts": json.dumps({"customers": {"data": {"customers": [customer]}}})}).insert(ignore_permissions=True)
		a.db_set("knowledge", k.name)
		a.reload()
		out = self.run_now(tasks.add(a.name, "reactivation", dedupe_key=f"react{self.n}"))
		self.assertIn("check-ins suggested", out)

	def test_the_responder_hands_price_talk_to_a_person_and_queues_the_quote(self):
		self.assertEqual(responder.rule_intent("how much for 500 pcs?"), None)
		self.assertTrue(responder.PRICE_TALK.search("¿Cuál es el precio por pieza?"))
