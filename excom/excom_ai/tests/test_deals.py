"""Excom AI Phase 1G: deal support (HLD-007 §8.1). A price question gets a Quotation draft and a
reply whose prices ERPNext worked out from the agent's price list, both for a person; quote
follow-ups; reactivation suggestions for the customer's own owner. On WhatsApp outside the 24-hour
window the order update and the quotation's validity go as approved templates (HLD-008); Meta is
never called."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from excom.excom.doctype.whatsapp_templates import whatsapp_templates as wt
from excom.excom.services import whatsapp_service as ws
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
WA_BOX = "QA Deals WA"
WABA = "QADEALWABA"


class FakeResp:
	def __init__(self, status, body):
		self.status_code, self._b, self.content, self.text, self.ok = status, body, b"x", json.dumps(body), status < 400

	def json(self):
		return self._b


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Deal%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Knowledge"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	for t in frappe.get_all("WhatsApp Templates", {"template_name": ["like", "qa_deal_%"]}, pluck="name"):
		for child in ("WhatsApp Button", "WhatsApp Template Linked Account"):
			frappe.db.delete(child, {"parent": t})
		frappe.db.delete("WhatsApp Templates", t)
	frappe.db.delete("Excom AI Template Need", {"wa_business_id": ["like", f"{WABA}%"]})
	frappe.db.delete("Excom AI Template Need", {"agent": ["in", agents or ["-"]]})
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-deal.example"]}, pluck="name"):
		for q in frappe.get_all("Quotation", {"party_name": lead}, pluck="name"):
			frappe.delete_doc("Quotation", q, force=True, ignore_permissions=True)
		for cust in frappe.get_all("Customer", {"lead_name": lead}, pluck="name"):
			for so in frappe.get_all("Sales Order", {"customer": cust}, pluck="name"):
				frappe.delete_doc("Sales Order", so, force=True, ignore_permissions=True)
			frappe.delete_doc("Customer", cust, force=True, ignore_permissions=True)
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	accs = frappe.get_all("Excom Channel Account", {"account_name": ["in", [BOX, *frappe.get_all("Excom Channel Account",
		{"account_name": ["like", f"{WA_BOX}%"]}, pluck="account_name")]]}, pluck="name")
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in frappe.get_all("Omni Identity", {"primary_email": ["like", "%qa-deal.example"]}, pluck="name"):
		frappe.db.delete("Excom Consent", {"omni_identity": oi})
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
		frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", a)
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
		# the steward and first-use review on (HLD-008's defaults), whatever the shared settings say
		from excom.excom_ai import templates

		s = frappe._dict(frappe.get_cached_doc("Excom AI Settings").as_dict(), enabled=1, templates_enabled=1,
			template_first_use_review=1)
		steward = patch.object(templates, "_settings", return_value=s)
		steward.start()
		self.addCleanup(steward.stop)
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, wa=None):
		company = frappe.db.get_value("Company", {"default_currency": "INR"}, "name")
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Deal {self.n}", "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "company": company, "customer_types": "Export Importer",
			"products": [{"item_group": GROUP, "price_list": PRICE_LIST}],
			"channels": [{"channel_account": self.box.name, "purpose": "Outreach and replies"}]
				+ ([{"channel_account": wa.name, "purpose": "Outreach and replies"}] if wa else [])}).insert(ignore_permissions=True)
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

	# ---- WhatsApp outside the 24-hour window: approved templates (HLD-008 §6)

	def wa_account(self):
		return frappe.get_doc({"doctype": "Excom Channel Account", "account_name": f"{WA_BOX} {self.n}", "channel": "whatsapp",
			"status": "Active", "wa_phone_id": f"QADEALPH{self.n}", "wa_business_id": f"{WABA}{self.n}",
			"wa_token": "qa-token-0000000000"}).insert(ignore_permissions=True)

	def on_whatsapp(self, agent, c, wa, hours_since_inbound=48):
		"""The buyer's WhatsApp conversation on the agent's number, the latest one; its window closed by default."""
		from excom.excom.services.thread_service import upsert_thread

		identity = frappe.db.get_value("Omni Identity", {"primary_email": c.email})
		frappe.db.set_value("Omni Identity", identity, "primary_whatsapp", f"9199007{self.n:05d}")
		thread = upsert_thread(identity, "whatsapp", wa.name)
		now = frappe.utils.now_datetime()
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": agent.name, "ai_mode": "Handle", "last_message_at": now,
			"last_inbound_at": frappe.utils.add_to_date(now, hours=-hours_since_inbound)})
		frappe.db.set_value("Excom Thread", c.thread, "last_message_at", frappe.utils.add_to_date(now, days=-3))
		frappe.db.commit()
		return frappe._dict(identity=identity, thread=thread)

	def approved_template(self, agent, wa, key, text):
		"""A Met need and its APPROVED WhatsApp template on the agent's number, stored without calling Meta."""
		name = f"qa_deal_{key.split('.')[1]}_{self.n}"
		need = frappe.get_doc({"doctype": "Excom AI Template Need", "need_key": key, "channel": "WhatsApp", "category": "UTILITY",
			"language": "en", "agent": agent.name, "agents": agent.name, "channel_account": wa.name,
			"wa_business_id": wa.wa_business_id, "unique_key": f"{key}|WhatsApp|{wa.wa_business_id}|en", "status": "Met",
			"source": "Written"}).insert(ignore_permissions=True)
		doc = frappe.new_doc("WhatsApp Templates")
		doc.update({"template_name": name, "actual_name": name, "template": text, "language": "en", "language_code": "en",
			"category": "UTILITY", "template_type": "Standard", "status": "APPROVED", "id": f"QADEAL{self.n}",
			"whatsapp_account": wa.name, "xai_need": need.name})
		doc.name = f"{name}-en"
		wt.upsert_doc_without_hooks(doc, "WhatsApp Button", "buttons")
		need.db_set("template", doc.name)
		frappe.db.commit()
		return doc.name

	def sales_order(self, agent, c):
		customer = frappe.get_doc({"doctype": "Customer", "customer_name": f"QA Pool Barn {self.n}", "customer_type": "Company",
			"lead_name": c.lead}).insert(ignore_permissions=True)
		due = add_days(nowdate(), 7)
		so = frappe.get_doc({"doctype": "Sales Order", "customer": customer.name, "company": agent.company, "transaction_date": nowdate(),
			"delivery_date": due, "po_no": f"PO-QA-{self.n}", "selling_price_list": PRICE_LIST, "currency": "USD",
			"conversion_rate": 83.33, "plc_conversion_rate": 83.33,
			"items": [{"item_code": "QA-DEAL-SHARK", "qty": 10, "rate": 1, "delivery_date": due}]})
		so.flags.ignore_validate = so.flags.ignore_mandatory = True  # a record the task reads (this site's location rules aside)
		so.insert(ignore_permissions=True)
		frappe.db.commit()
		return so

	def order_update(self, agent, so, key):
		return self.run_now(tasks.add(agent.name, "order_update", subject=("Sales Order", so.name), dedupe_key=key,
			params={"event": "delivered"}))

	def meta(self):
		"""Meta's send, replaced: the calls are recorded and nothing leaves."""
		calls = []
		orig = ws.http_requests.post
		ws.http_requests.post = lambda url, **kw: (calls.append((url, kw.get("json"))) or FakeResp(200,
			{"messages": [{"id": f"wamid.QADEAL{len(calls)}"}]}))
		self.addCleanup(setattr, ws.http_requests, "post", orig)
		return calls

	def approve(self, name):
		frappe.set_user(OWNER)
		proposals.decide(name, "approve")
		frappe.set_user("Administrator")

	def test_order_update_outside_the_window_goes_as_the_approved_template(self):
		from excom.excom.services.consent import record_consent
		from excom.excom_ai import send_rules
		from excom.excom_ai.engine import identity as as_agent

		wa = self.wa_account()
		a = self.agent(wa)
		c = self.conversation(a, "thanks for the order")
		w = self.on_whatsapp(a, c, wa)
		tname = self.approved_template(a, wa, "sales.order_update", "Hello {{1}}, your order {{2}} is now {{3}}.")
		so = self.sales_order(a, c)
		calls = self.meta()

		out = self.order_update(a, so, f"ou{self.n}a")
		self.assertIn("WhatsApp template", out)
		p = frappe.get_doc("Excom AI Proposal", {"subject_name": so.name, "action": "reply"})
		payload = json.loads(p.payload)
		self.assertEqual((p.status, p.thread, payload["channel"], payload["template"]), ("Pending", w.thread, "whatsapp", tname))
		self.assertEqual(payload["variables"], [f"QA Deal Buyer {self.n}", f"PO-QA-{self.n}", "delivered"])
		self.assertIn(f"your order PO-QA-{self.n} is now delivered", p.preview_html)

		# no opt-in: the send rules refuse it, and Meta is never called
		with as_agent.as_user(a.service_user), self.assertRaises(send_rules.AgentSendRefused):
			send_rules.check(w.thread, purpose="outreach", window=False, template=tname)
		self.approve(p.name)
		proposals.execute(p.name)
		row = frappe.db.get_value("Excom AI Proposal", p.name, ["status", "error"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("opt-in", row.error)
		self.assertEqual(calls, [])

		# with the buyer's WhatsApp opt-in recorded, the next update goes, through send_template_to_thread
		record_consent(w.identity, "Granted", channel="whatsapp", purpose="transactional")
		frappe.db.commit()
		self.order_update(a, so, f"ou{self.n}b")
		p2 = frappe.get_value("Excom AI Proposal", {"subject_name": so.name, "action": "reply", "status": "Pending"})
		self.approve(p2)
		with patch("excom.excom.api.chat.send_template_to_thread", wraps=__import__("excom.excom.api.chat",
				fromlist=["x"]).send_template_to_thread) as via:
			proposals.execute(p2)
		row = frappe.db.get_value("Excom AI Proposal", p2, ["status", "error", "message"], as_dict=True)
		self.assertEqual(row.status, "Sent", row.error)
		via.assert_called_once()
		self.assertEqual(len(calls), 1)
		sent = calls[0][1]["template"]
		self.assertEqual(sent["name"], f"qa_deal_order_update_{self.n}")
		self.assertEqual([x["text"] for x in sent["components"][0]["parameters"]],
			[f"QA Deal Buyer {self.n}", f"PO-QA-{self.n}", "delivered"])
		self.assertEqual(frappe.db.get_value("Excom Message", row.message, ["thread", "template"]), (w.thread, tname))
		self.assertTrue(frappe.db.get_value("WhatsApp Templates", tname, "xai_first_use_done"), "a person approved its first send")

	def test_without_a_template_the_need_is_recorded_and_email_stays(self):
		wa = self.wa_account()
		a = self.agent(wa)
		c = self.conversation(a, "thanks for the order")
		self.on_whatsapp(a, c, wa)
		so = self.sales_order(a, c)
		self.meta()
		out = self.order_update(a, so, f"ou{self.n}a")
		self.assertNotIn("WhatsApp template", out)
		p = frappe.get_doc("Excom AI Proposal", {"subject_name": so.name, "action": "reply"})
		payload = json.loads(p.payload)
		self.assertEqual(p.thread, c.thread, "free text cannot go on WhatsApp now: the buyer's email conversation")
		self.assertNotIn("template", payload)
		self.assertEqual(payload["body"], f"Your order PO-QA-{self.n}: it has been delivered.")
		self.order_update(a, so, f"ou{self.n}b")
		needs = frappe.get_all("Excom AI Template Need", {"wa_business_id": wa.wa_business_id}, ["need_key", "status", "agents",
			"unique_key"])
		self.assertEqual(len(needs), 1, needs)
		self.assertEqual((needs[0].need_key, needs[0].status, needs[0].agents), ("sales.order_update", "Open", a.name))

	def test_inside_the_window_the_free_text_reply_stays(self):
		wa = self.wa_account()
		a = self.agent(wa)
		c = self.conversation(a, "thanks")
		w = self.on_whatsapp(a, c, wa, hours_since_inbound=2)
		self.approved_template(a, wa, "sales.order_update", "Hello {{1}}, your order {{2}} is now {{3}}.")
		so = self.sales_order(a, c)
		self.order_update(a, so, f"ou{self.n}a")
		p = frappe.get_doc("Excom AI Proposal", {"subject_name": so.name, "action": "reply"})
		payload = json.loads(p.payload)
		self.assertEqual(p.thread, w.thread)
		self.assertNotIn("template", payload)
		self.assertEqual(payload["body"], f"Your order PO-QA-{self.n}: it has been delivered.")

	def test_quote_validity_outside_the_window_without_the_model(self):
		wa = self.wa_account()
		a = self.agent(wa)
		c = self.conversation(a, "thanks")
		w = self.on_whatsapp(a, c, wa)
		self.approved_template(a, wa, "sales.quote_follow_up", "Quotation {{1}} is valid until {{2}}.")
		q = deals.price(a, "Lead", c.lead, [{"item_code": "QA-DEAL-SHARK", "qty": 100}]).doc
		q.transaction_date = add_days(nowdate(), -8)
		q.insert(ignore_permissions=True)
		out = self.run_now(tasks.add(a.name, "quote_follow_up", subject=("Quotation", q.name)))  # no model answer scripted
		self.assertIn("WhatsApp template", out)
		p = frappe.get_doc("Excom AI Proposal", {"subject_name": q.name, "action": "reply"})
		payload = json.loads(p.payload)
		self.assertEqual((p.thread, p.policy_action, p.status), (w.thread, "follow_up_email", "Pending"))
		self.assertEqual(payload["variables"], [q.name, frappe.utils.formatdate(q.valid_till)])
