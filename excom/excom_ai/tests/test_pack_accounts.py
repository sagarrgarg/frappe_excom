"""Excom AI Phase 2B: the Accounts pack (HLD-007 §8.2). The collections ladder runs in Review for a
customer 90+ days overdue, writes to the billing contact only with amounts from the ledger, and stops
on payment and on a dispute; promises are recorded, followed up and a broken one moves the ladder
on; payment matches are suggestions that never post; the MSME list reaches finance weekly; the
Accounts readiness rules. The model is ai_interface's scripted adapter.

Invoices are fixtures written straight to the table (submitted, with an outstanding amount), so no
accounting setup or ledger entry is needed and nothing is posted.
"""

import json
import time
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, fmt_money, get_datetime, getdate, now_datetime, nowdate

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import checks, templates
from excom.excom_ai.builder import readiness
from excom.excom_ai.engine import inbound, ownership, proposals, runner, tasks
from excom.excom_ai.packs import accounts

OWNER = "qa.acc.owner@example.com"
BACKUP = "qa.acc.backup@example.com"
FINANCE = "qa.acc.finance@example.com"
TEAM = "QA Acc Desk"
BOX = "QA Acc Box"
DOMAIN = "qa-acc.example"
WA1, WA2 = "QA Acc WA 1", "QA Acc WA 2"
WABA = "990077665544"  # both numbers are on one WhatsApp business account
PHONE = "+91990077{:04d}"
REMINDER = "accounts.payment_reminder"
PTYPE, PROVIDER = "_TA Scripted", "_TA Accounts"
MICRO, BIG = "QA Acc Micro Supplier", "QA Acc Big Supplier"
WORDS = {"subject": "Your account with us",
	"opening": "We hope all is well. These invoices are now past their due date:",
	"closing": "Could you let us know when payment will be arranged, or tell us if anything on the list looks wrong?"}


def _company():
	return frappe.db.get_value("Company", {"default_currency": "INR"}, "name")


def _clear_steward(agents=None):
	"""Template needs and WhatsApp Templates rows made here (rows only: nothing is ever on Meta)."""
	agents = agents if agents is not None else frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Acc%"]},
		pluck="name")
	frappe.db.delete("Excom AI Template Need", {"wa_business_id": WABA})
	frappe.db.delete("Excom AI Template Need", {"agent": ["in", agents or ["-"]]})
	for t in frappe.get_all("WhatsApp Templates", {"template_name": ["like", "qa_acc_%"]}, pluck="name"):
		frappe.db.delete("WhatsApp Template Linked Account", {"parent": t})
		frappe.db.delete("WhatsApp Templates", {"name": t})


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Acc%"]}, pluck="name")
	_clear_steward(agents)
	customers = frappe.get_all("Customer", {"customer_name": ["like", "QA Acc%"]}, pluck="name")
	suppliers = frappe.get_all("Supplier", {"supplier_name": ["like", "QA Acc%"]}, pluck="name")
	bts = frappe.get_all("Bank Transaction", {"name": ["like", "QA-ACC-%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment", "Excom AI Sequence", "Excom AI Company",
			"Excom AI Knowledge", "Excom AI Readiness Gap"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	refs = customers + suppliers + bts + agents
	frappe.db.delete("ToDo", {"reference_name": ["in", refs or ["-"]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [OWNER, BACKUP, FINANCE]]})
	frappe.db.delete("Comment", {"reference_name": ["in", refs or ["-"]]})
	for dt in ("Sales Invoice", "Purchase Invoice", "Bank Transaction"):
		frappe.db.delete(dt, {"name": ["like", "QA-ACC-%"]})
	accs = frappe.get_all("Excom Channel Account", {"account_name": ["in", (BOX, WA1, WA2)]}, pluck="name")
	ids = set()
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	ids |= set(frappe.get_all("Omni Identity", {"primary_email": ["like", f"%{DOMAIN}"]}, pluck="name"))
	ids |= set(frappe.get_all("Omni Identity", {"primary_phone": ["like", PHONE[:9] + "%"]}, pluck="name"))
	ids |= set(frappe.get_all("Omni Identity", {"normalized_phone": ["like", PHONE[1:9] + "%"]}, pluck="name"))
	if ids and frappe.db.exists("DocType", "Excom Consent"):
		frappe.db.delete("Excom Consent", {"omni_identity": ["in", [i for i in ids if i] or ["-"]]})
	ids |= set(frappe.get_all("Omni Identity Link", {"linked_doctype": ["in", ("Customer", "Supplier")],
		"linked_name": ["in", customers + suppliers or ["-"]]}, pluck="parent"))
	for oi in ids:
		if oi and frappe.db.exists("Omni Identity", oi):
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for addr in frappe.get_all("Address", {"address_title": ["like", "QA Acc%"]}, pluck="name"):
		frappe.delete_doc("Address", addr, force=True, ignore_permissions=True)
	for c in set(frappe.get_all("Contact", {"email_id": ["like", f"%{DOMAIN}"]}, pluck="name")) | set(
			frappe.get_all("Contact", {"first_name": ["like", "QA Acc%"]}, pluck="name")):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	for name in customers:
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)
	for name in suppliers:
		frappe.delete_doc("Supplier", name, force=True, ignore_permissions=True)
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
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP, FINANCE]]})
	purge_users(OWNER, BACKUP, FINANCE)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


def _retry(fn, *args, **kwargs):
	"""Other packs' tests write the same settings row and naming series on this site at the same time:
	start from a fresh snapshot, and on a clash (MariaDB error 1020) try again."""
	for attempt in range(6):
		frappe.db.commit()
		try:
			out = fn(*args, **kwargs)
			frappe.db.commit()
			return out
		except frappe.QueryDeadlockError:
			frappe.db.rollback()
			if attempt == 5:
				raise
			time.sleep(0.5 + attempt)


def _switch(on: int):
	_retry(frappe.db.set_single_value, "Excom AI Settings", "enabled", on)


def _fixture(doctype: str, name: str, values: dict) -> str:
	"""A submitted document written straight to its table: a fixture with an outstanding amount,
	not an accounting event (no ledger entry is made)."""
	doc = frappe.new_doc(doctype)
	doc.update(values)
	doc.name = name
	doc.docstatus = 1
	doc.db_insert()
	return name


class TestAccountsPack(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_retry(_cleanup)
		for email, roles in ((OWNER, ("Excom Agent", "Accounts Manager")), (BACKUP, ("Excom Agent",)),
				(FINANCE, ("Excom Agent", "Accounts User"))):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles(*roles)
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [{"user": OWNER, "role": "Manager"},
			{"user": BACKUP, "role": "Member"}, {"user": FINANCE, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email",
			"status": "Active",
			"email_address": f"accounts@ours.{DOMAIN}"}).insert(ignore_permissions=True)
		cls.wa = []
		for name in (WA1, WA2):
			wa = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": name, "channel": "whatsapp",
				"status": "Active", "wa_business_id": WABA})
			wa.flags.ignore_validate = True
			wa.flags.ignore_mandatory = True
			cls.wa.append(wa.insert(ignore_permissions=True).name)
		cls.company = _company()
		cls.msme = frappe.db.exists("DocType",
			"MSME Registration Detail") and frappe.get_meta("Supplier").has_field("custom_msme_details")
		for name, rows in ((MICRO, [{"msme_registered": "Yes", "msme_type": "Micro", "msme_registration_no": "UDYAM-QA-ACC-1",
				"msme_contract_done": "Yes", "effective_from": add_days(nowdate(), -400)}] if cls.msme else []), (BIG, [])):
			s = frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company",
				"gst_category": "Unregistered"})
			if rows:
				s.set("custom_msme_details", rows)
			s.insert(ignore_permissions=True)
		cls.micro = frappe.db.get_value("Supplier", {"supplier_name": MICRO})
		cls.big = frappe.db.get_value("Supplier", {"supplier_name": BIG})
		for name, supplier, age in (("QA-ACC-PINV-PAST", cls.micro, 50), ("QA-ACC-PINV-NEAR", cls.micro, 38),
				("QA-ACC-PINV-FRESH", cls.micro, 10), ("QA-ACC-PINV-BIG", cls.big, 60)):
			_fixture("Purchase Invoice", name, {"supplier": supplier, "supplier_name": frappe.db.get_value("Supplier",
				supplier, "supplier_name"),
				"company": cls.company, "posting_date": add_days(nowdate(), -age), "bill_date": add_days(nowdate(), -age),
				"bill_no": f"BILL-{age}", "due_date": add_days(nowdate(), -age + 30), "currency": "INR", "conversion_rate": 1,
				"grand_total": 10000, "base_grand_total": 10000, "outstanding_amount": 10000})
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}],
				api_base_url="main")
		cls.rules_before = set(frappe.get_all("Excom AI Readiness Rule", {"rule_key": ["like", "accounts.%"]}, pluck="name"))
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		import os

		if not os.environ.get("QA_KEEP"):
			for name in set(frappe.get_all("Excom AI Readiness Rule", {"rule_key": ["like", "accounts.%"]},
				pluck="name")) - cls.rules_before:
				frappe.db.delete("Excom AI Readiness Gap", {"rule": name})
				frappe.delete_doc("Excom AI Readiness Rule", name, force=True, ignore_permissions=True)
			_retry(_cleanup)
		super().tearDownClass()

	def setUp(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		frappe.set_user("Administrator")
		_switch(1)
		self.addCleanup(_switch, 0)
		# Other packs' tests on this site flip the shared master switch off when they finish; what is
		# tested here is this pack, so its gates see the switch on throughout.
		engine_on = patch("excom.excom_ai.engine.gates.engine_on", return_value=True)
		engine_on.start()
		self.addCleanup(engine_on.stop)
		type(self).n = getattr(type(self), "n", 0) + 1

	# ---- fixtures

	def agent(self, status="Live", whatsapp=False, suffix=""):
		numbers = [{"channel_account": wa, "purpose": "Outreach and replies"} for wa in self.wa] if whatsapp else []
		a = _retry(lambda: frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Acc {self.n}{suffix}",
			"agent_type": "Accounts",
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM, "company": self.company,
			"channels": [{"channel_account": self.box.name,
				"purpose": "Outreach and replies"}, *numbers]}).insert(ignore_permissions=True))
		a.db_set({"status": status, "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def buyer(self, contacts=True, mobile=False, email=True):
		"""A customer 120 days overdue on one invoice and 95 on a part-paid one, with a third not due
		yet; a billing contact and a primary contact who is not the billing one. mobile: the billing
		contact has a WhatsApp number too; email=False: a mobile number only (no primary contact)."""
		c = _retry(lambda: frappe.get_doc({"doctype": "Customer", "customer_name": f"QA Acc Buyer {self.n}",
			"customer_type": "Company", "gst_category": "Unregistered", "language": "en"}).insert(ignore_permissions=True))
		out = frappe._dict(customer=c.name, billing=f"billing{self.n}@buyer.{DOMAIN}", boss=f"boss{self.n}@buyer.{DOMAIN}",
			old=f"QA-ACC-SINV-{self.n}-1", part=f"QA-ACC-SINV-{self.n}-2", fresh=f"QA-ACC-SINV-{self.n}-3",
			mobile=PHONE.format(self.n) if mobile or not email else None, first=f"QA Acc Billing {self.n}")
		if contacts and out.mobile:
			frappe.get_doc({"doctype": "Contact", "first_name": out.first, "is_billing_contact": 1,
				"email_ids": [{"email_id": out.billing, "is_primary": 1}] if email else [],
				"phone_nos": [{"phone": out.mobile, "is_primary_mobile_no": 1}],
				"links": [{"link_doctype": "Customer", "link_name": c.name}]}).insert(ignore_permissions=True)
		elif contacts:
			for first, email, billing, primary in (("QA Acc Boss", out.boss, 0, 1), ("QA Acc Billing", out.billing, 1, 0)):
				frappe.get_doc({"doctype": "Contact", "first_name": f"{first} {self.n}", "email_ids": [{"email_id": email,
					"is_primary": 1}],
					"is_billing_contact": billing, "is_primary_contact": primary,
					"links": [{"link_doctype": "Customer", "link_name": c.name}]}).insert(ignore_permissions=True)
		debtors = frappe.db.get_value("Company", self.company, "default_receivable_account")
		for name, due, total, outstanding in ((out.old, -120, 50000, 50000), (out.part, -95, 30000, 20000), (out.fresh,
			10, 5000, 5000)):
			_fixture("Sales Invoice", name, {"customer": c.name, "customer_name": c.customer_name, "company": self.company,
				"posting_date": add_days(nowdate(), due - 10), "due_date": add_days(nowdate(), due), "currency": "INR",
				"conversion_rate": 1, "grand_total": total, "base_grand_total": total, "outstanding_amount": outstanding,
				"debit_to": debtors, "is_return": 0})
		frappe.db.commit()
		return out

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def run_now(self, t, *answers):
		for _attempt in range(3):
			_switch(1)  # other packs' tests flip the shared master switch off when they finish
			frappe.db.set_value("Excom AI Task", t, "status", "Running")
			frappe.db.commit()
			with self.scripted(*answers):
				runner.run_task(t)
			row = frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)
			if not (row.status == "Queued" and not row.error):
				break  # a gate sent it back (the switch was off for a moment): run it again
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	def step(self, a, b, words=WORDS):
		"""Run the customer's next ladder step; returns its proposal (or None)."""
		frappe.db.delete("Excom AI Task", {"agent": a.name, "kind": "collections_step"})  # a fresh task each time
		out = self.run_now(tasks.add(a.name, "collections_step", subject=("Customer", b.customer)),
			*([words] if words else []))
		name = frappe.db.get_value("Excom AI Proposal", {"agent": a.name, "subject_name": b.customer,
			"action": ["in", ("payment_reminder", "create_todo")]}, order_by="creation desc")
		return frappe._dict(outcome=out, proposal=frappe.get_doc("Excom AI Proposal", name) if name else None)

	def send(self, name):
		"""The owner approves, and the paced sender sends it inside the customer's hours."""
		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(name, "approve"), "Approved", "a send waits for the paced sender")
		frappe.set_user("Administrator")
		with patch("excom.excom.channels.email.outbound.send_email_reply", return_value="QA-ACC-MSG") as send, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(name)
		return send

	def sent_step_one(self, a, b):
		p = self.step(a, b).proposal
		self.send(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")
		return p, frappe.db.get_value("Excom AI Proposal", p.name, "thread")

	def reply(self, a, thread, text, classified):
		"""The customer writes back on the conversation: the engine's inbound handler, then the
		accounts responder with the scripted model."""
		ident = frappe.db.get_value("Excom Thread", thread, "omni_identity")
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": ident, "direction": "Inbound", "message_type": "Email",
			"content_text": text, "content_json": json.dumps({"subject": "Re: your account"}), "delivery_status": "Delivered"}
			).insert(ignore_permissions=True)
		inbound.on_inbound({"thread": thread, "message": msg.name, "channel": "email", "text": text})
		t = frappe.db.get_value("Excom AI Task", {"agent": a.name, "kind": "inbound", "thread": thread, "status": "Queued"})
		self.assertTrue(t, "the reply reached the agent")
		return self.run_now(t, classified)

	# ---- the template steward (HLD-008): rows only, Meta is never called

	def steward_on(self):
		"""The steward and first-use review on for this pack's code, whatever the shared settings say."""
		real = frappe.get_cached_doc("Excom AI Settings")
		s = frappe._dict(real.as_dict(), enabled=1, templates_enabled=1, template_first_use_review=1)
		return patch.object(templates, "_settings", return_value=s)

	def wa_template(self, name: str, text: str, need: str | None = None) -> str:
		"""An APPROVED utility template on the first number, written straight to its table (after_insert
		would post it to Meta)."""
		doc = frappe.new_doc("WhatsApp Templates")
		doc.update({"template_name": name, "actual_name": name, "template": text, "language": "en", "language_code": "en",
			"category": "UTILITY", "status": "APPROVED", "whatsapp_account": self.wa[0], "header_type": "",
			"xai_need": need, "xai_first_use_done": 0})
		doc.name = f"{name}-en"
		doc.db_insert()
		frappe.db.commit()
		return doc.name

	def met_need(self, a, channel: str, key: str = REMINDER, template: str | None = None, subject: str = "",
			body: str = "") -> str:
		"""A need the steward has met: WhatsApp on the WABA (linked both ways to its template), email per agent."""
		wa = channel == "WhatsApp"
		doc = frappe.get_doc({"doctype": "Excom AI Template Need", "need_key": key, "channel": channel,
			"category": "UTILITY" if wa else "", "language": "en", "agent": a.name, "agents": a.name,
			"channel_account": self.wa[0] if wa else None, "wa_business_id": WABA if wa else "",
			"unique_key": templates.unique_key(key, channel, "en", a.name, self.wa[0] if wa else None), "status": "Met",
			"source": "Written", "template": template, "subject": subject, "body": body,
			"variable_map": json.dumps(templates.NEEDS[key]["variables"])}).insert(ignore_permissions=True)
		if template:
			frappe.db.set_value("WhatsApp Templates", template, {"xai_need": doc.name,
				"xai_variable_map": json.dumps(templates.NEEDS[key]["variables"])}, update_modified=False)
		frappe.db.commit()
		return doc.name

	def set_policy(self, a, action: str, mode: str):
		frappe.db.set_value("Excom AI Policy", {"parent": a.name, "action": action}, "mode", mode)
		frappe.db.commit()

	def enrollment(self, a, b):
		return accounts.enrollment_for(a, b.customer, create=False)

	def planned(self, a):
		with patch.object(accounts, "PER_TICK", 10000):
			return [(s["kind"], (s.get("subject") or (None, None))[1]) for s in accounts.plan(a)]

	# ---- the ladder in Review

	def test_the_ladder_runs_in_review_for_a_90_plus_customer(self):
		a, b = self.agent(), self.buyer()
		frappe.db.set_value("Sales Invoice", b.old, "due_date", add_days(nowdate(), -150))  # the oldest debt on the site
		self.assertIn(("collections_step", b.customer), self.planned(a), "a customer 150 days overdue is on the list")
		self.assertEqual(accounts.due_customers(a, self.company)[0], b.customer, "the most overdue first")
		p = self.step(a, b).proposal
		self.assertEqual((p.action, p.policy_action, p.status, p.kind, p.step), ("payment_reminder", "payment_reminder",
			"Pending",
			"Sequence", 1))
		self.assertEqual(p.to_email, b.billing, "the billing contact, not the primary contact")
		payload = json.loads(p.payload)
		ledger = {r.name: r.outstanding_amount for r in frappe.get_all("Sales Invoice", {"customer": b.customer,
			"due_date": ["<", nowdate()]}, ["name", "outstanding_amount"])}
		self.assertEqual(payload["invoices"], ledger, "the amounts are ERPNext's")
		self.assertEqual(payload["invoices"], {b.old: 50000, b.part: 20000})
		body = payload["body"]
		for text in (b.old, b.part, fmt_money(50000, currency="INR"), fmt_money(20000, currency="INR"),
				f"Total overdue: {fmt_money(70000, currency='INR')}", WORDS["opening"], WORDS["closing"]):
			self.assertIn(text, body)
		self.assertNotIn(b.fresh, body, "an invoice not yet due is not chased")
		self.assertNotIn(b.boss, json.dumps(payload))
		results = json.loads(p.checks)
		self.assertTrue(results["ok"], checks.failures(results))
		self.assertEqual(results["prices"]["why"], "amounts owed, written by code from the ledger")
		self.assertIn(b.fresh, p.preview_html, "step 1 carries the statement of every open invoice")
		self.assertNotIn(("collections_step", b.customer), self.planned(a), "nothing more for this customer today")

		send = self.send(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")
		send.assert_called_once()
		self.assertEqual(send.call_args.kwargs["to"], b.billing)
		self.assertIn("Days overdue", send.call_args.kwargs["body_html"])
		enr = self.enrollment(a, b)
		self.assertEqual((enr.status, enr.current_step), ("Active", 1))
		self.assertTrue(enr.thread and enr.last_sent_on)
		self.assertEqual(frappe.db.get_value("Excom Thread", enr.thread, "ai_agent"), a.name)

		# the wait passes: step 2, firmer, again for a person
		frappe.db.set_value("Excom AI Proposal", p.name, "sent_on", add_days(now_datetime(), -30))
		frappe.db.set_value("Excom AI Enrollment", enr.name, "next_due", add_days(now_datetime(), -1))
		second = self.step(a, b, {"subject": "Your account with us", "opening": "We wrote earlier about these invoices:",
			"closing": "On which date can we expect the payment?"}).proposal
		self.assertEqual((second.step, second.status, second.to_email), (2, "Pending", b.billing))

	def test_numbers_and_threats_from_the_model_never_pass(self):
		a, b = self.agent(), self.buyer()
		p = self.step(a, b, {"subject": "Final notice", "opening": "You owe 70,000 and we will take legal action.",
			"closing": "Pay now."}).proposal
		results = json.loads(p.checks)
		self.assertFalse(results["amounts"]["ok"])
		self.assertFalse(results["collections"]["ok"])
		self.assertEqual((p.status, p.risk), ("Pending", "High"), "a failed check always waits for a person")
		# the prices check knows what a statement is
		ok = checks.check(a, b.billing, "Statement", f"{fmt_money(123.45, currency='INR')} is due", kind="Statement")
		self.assertTrue(ok["prices"]["ok"])
		self.assertFalse(checks.check(a, b.billing, "Statement", f"{fmt_money(123.45, currency='INR')} is due",
			kind="Reply")["prices"]["ok"])

	def test_no_billing_contact_means_a_todo_not_a_message(self):
		a, b = self.agent(), self.buyer(contacts=False)
		out = self.step(a, b, words=None)
		self.assertIn("no billing contact", out.outcome)
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"agent": a.name, "action": "payment_reminder"}))
		self.assertTrue(frappe.db.exists("ToDo", {"reference_name": b.customer, "allocated_to": OWNER,
			"description": ["like", "%billing contact%"]}))

	# ---- stops

	def test_it_stops_on_payment(self):
		a, b = self.agent(), self.buyer()
		p = self.step(a, b).proposal
		frappe.set_user(OWNER)
		proposals.decide(p.name, "approve")
		frappe.set_user("Administrator")
		frappe.db.set_value("Sales Invoice", b.part, "outstanding_amount", 5000)  # part paid since it was written
		with patch("excom.excom.channels.email.outbound.send_email_reply") as send, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		send.assert_not_called()
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Cancelled", "the balance changed")
		fresh = self.step(a, b).proposal
		self.assertEqual(json.loads(fresh.payload)["invoices"], {b.old: 50000, b.part: 5000}, "rewritten from the ledger")

		for inv in (b.old, b.part, b.fresh):
			frappe.db.set_value("Sales Invoice", inv, "outstanding_amount", 0)
		self.assertNotIn(("collections_step", b.customer), self.planned(a))
		enr = self.enrollment(a, b) or frappe.get_doc("Excom AI Enrollment", fresh.enrollment)
		enr.reload()
		self.assertEqual(enr.status, "Completed")
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", fresh.name, "status"), "Cancelled")

	def test_a_dispute_stops_the_ladder_and_a_person_takes_it(self):
		a, b = self.agent(), self.buyer()
		_p, thread = self.sent_step_one(a, b)
		out = self.reply(a, thread, f"The goods on {b.old} arrived damaged. We will not pay that invoice.",
			{"intent": "dispute", "promise_date": "", "promise_amount": 0, "reference": "", "language": "en",
				"summary": "Goods on the oldest invoice arrived damaged."})
		self.assertIn("dispute", out)
		enr = self.enrollment(a, b)
		self.assertEqual(enr.status, "Stopped")
		self.assertIn("Dispute", enr.stop_reason)
		self.assertEqual(frappe.db.get_value("Excom Thread", thread, "ai_mode"), "Needs person")
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": b.customer, "content": ["like", "%disputes%"]}))
		self.assertNotIn(("collections_step", b.customer), self.planned(a))
		self.assertIn("left to a person", self.step(a, b, words=None).outcome)
		self.assertEqual(frappe.db.count("Excom AI Proposal", {"agent": a.name, "action": "payment_reminder"}), 1,
			"nothing more is written")

	# ---- promises

	def test_a_promise_is_recorded_followed_up_and_a_broken_one_moves_the_ladder_on(self):
		a, b = self.agent(), self.buyer()
		_p, thread = self.sent_step_one(a, b)
		when = add_days(nowdate(), 5)
		out = self.reply(a, thread, "We will clear everything by next week, on the 15th.",
			{"intent": "promise", "promise_date": str(when), "promise_amount": 0, "reference": "", "language": "en",
				"summary": "Will pay in full next week."})
		self.assertIn("promise", out)
		rec = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "record_promise"})
		self.assertEqual(rec.status, "Done", "recording a promise is an internal record (Auto)")
		payload = json.loads(rec.payload)
		self.assertEqual((payload["date"], payload["amount"], payload["overdue_then"]), (str(when), 70000, 70000))
		todo = frappe.get_doc("ToDo", rec.executed_name)
		self.assertEqual((todo.allocated_to, todo.reference_name, str(todo.date)), (OWNER, b.customer, str(when)),
			"the follow-up is on the owner's list for the promised date")
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": b.customer, "content": ["like", "%promised to pay%"]}))
		enr = self.enrollment(a, b)
		self.assertEqual(enr.status, "Paused")
		self.assertEqual(getdate(enr.next_due), getdate(add_days(when, accounts.GRACE_DAYS)),
			"looked at again after the date")
		self.assertNotIn(("collections_step", b.customer), self.planned(a), "a promise in date pauses the ladder")

		# the date passes and nothing came in: the next step at once, without the usual wait
		frappe.db.set_value("Excom AI Proposal", rec.name, "payload", json.dumps({**payload, "date": add_days(nowdate(),
			-2)}))
		frappe.db.set_value("Excom AI Enrollment", enr.name, "next_due", add_to_date(now_datetime(), hours=-1))
		self.assertEqual(self.planned(a)[0], ("collections_step", b.customer), "a due promise comes first")
		second = self.step(a, b, {"subject": "Your account with us", "opening": "We had expected your payment:",
			"closing": "On which date can we now expect it?"}).proposal
		self.assertEqual((second.step, second.status), (2, "Pending"))
		self.assertTrue(json.loads(second.payload)["broken_promise"])
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": b.customer, "content": ["like", "%was not kept%"]}))

	# ---- payment matching

	def test_a_match_is_a_suggestion_and_never_posts(self):
		a, b = self.agent(), self.buyer()
		bt = _fixture("Bank Transaction", f"QA-ACC-BT-{self.n}", {"company": self.company, "date": add_days(nowdate(), -2),
			"status": "Unreconciled", "deposit": 20000, "withdrawal": 0, "unallocated_amount": 20000, "allocated_amount": 0,
			"currency": "INR", "description": f"NEFT CR QA ACC BUYER {self.n} {b.part}", "reference_number": "UTR998877",
			"party_type": "Customer", "party": b.customer})
		frappe.db.commit()
		before = {dt: frappe.db.count(dt) for dt in ("Payment Entry", "Journal Entry", "GL Entry", "Payment Ledger Entry")}
		self.assertIn(("match_payments", None), self.planned(a))
		self.run_now(tasks.add(a.name, "match_payments", dedupe_key=f"match:{self.n}"))
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "suggest_match", "subject_name": bt})
		self.assertEqual(json.loads(p.payload)["invoice"], b.part)
		self.assertEqual(p.status, "Done")
		self.assertEqual(p.executed_doctype, "ToDo")
		self.assertEqual(frappe.db.get_value("ToDo", p.executed_name, "allocated_to"), OWNER)
		self.assertEqual({dt: frappe.db.count(dt) for dt in before}, before, "nothing posted")
		row = frappe.db.get_value("Bank Transaction", bt, ["status", "allocated_amount", "unallocated_amount"], as_dict=True)
		self.assertEqual((row.status, row.allocated_amount, row.unallocated_amount), ("Unreconciled", 0, 20000),
			"not reconciled")
		self.assertFalse(frappe.db.exists("Bank Transaction Payments", {"parent": bt}))
		self.assertEqual(frappe.db.get_value("Sales Invoice", b.part, "outstanding_amount"), 20000)
		self.run_now(tasks.add(a.name, "match_payments", dedupe_key=f"match:{self.n}:again"))
		self.assertEqual(frappe.db.count("Excom AI Proposal", {"action": "suggest_match", "subject_name": bt}), 1,
			"suggested once")

		# "we already paid": a suggestion for a person, and the ladder waits for them
		_p, thread = self.sent_step_one(a, b)
		self.reply(a, thread, "We paid this last week, UTR998877.", {"intent": "paid", "promise_date": "",
			"promise_amount": 0,
			"reference": "UTR998877", "language": "en", "summary": "Says it was paid by bank transfer."})
		said = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "suggest_match", "subject_name": b.customer})
		self.assertIn(bt, json.loads(said.payload)["bank_transactions"])
		self.assertEqual(said.status, "Done")
		self.assertEqual(self.enrollment(a, b).status, "Paused")
		self.assertEqual({dt: frappe.db.count(dt) for dt in before}, before, "still nothing posted")

	# ---- MSME

	def test_the_msme_list_reaches_finance_weekly(self):
		if not self.msme:
			self.skipTest("no MSME fields on Supplier on this site")
		a = self.agent()
		with patch.object(accounts, "MSME_HOUR", 0):
			kinds = [s["kind"] for s in accounts.plan(a)]
			self.assertIn("msme_watch", kinds)
			key = next(s["dedupe_key"] for s in accounts.plan(a) if s["kind"] == "msme_watch")
		rows = {r.name: r for r in accounts.msme_watch_list(self.company)}
		self.assertIn("QA-ACC-PINV-PAST", rows)
		self.assertIn("QA-ACC-PINV-NEAR", rows)
		self.assertNotIn("QA-ACC-PINV-FRESH", rows, "far from the limit")
		self.assertNotIn("QA-ACC-PINV-BIG", rows, "not a Micro or Small supplier")
		self.assertLess(rows["QA-ACC-PINV-PAST"].days_left, 0)
		self.assertEqual(rows["QA-ACC-PINV-PAST"].limit, 45)
		self.assertEqual(set(accounts.finance_people(a)), {OWNER, BACKUP, FINANCE})
		out = self.run_now(tasks.add(a.name, "msme_watch", dedupe_key=key))
		self.assertIn("MSME payables", out)
		for user in (OWNER, BACKUP, FINANCE):
			log = frappe.db.get_value("Notification Log", {"for_user": user, "subject": ["like", "MSME payables%"],
				"document_name": a.name}, "email_content")
			self.assertTrue(log, f"{user} got the list")
			self.assertIn("QA-ACC-PINV-PAST", log)
			self.assertNotIn("QA-ACC-PINV-BIG", log)
		self.assertTrue(frappe.db.exists("ToDo", {"reference_name": a.name, "allocated_to": OWNER, "description": ["like",
			"%MSME%"]}))
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"agent": a.name, "action": ["in", proposals.SENDS]}),
			"no external message")
		with patch.object(accounts, "MSME_HOUR", 0):
			self.assertNotIn("msme_watch", [s["kind"] for s in accounts.plan(a)], "once a week")

	# ---- statements on request

	def test_a_statement_on_request_waits_for_review(self):
		a, b = self.agent(), self.buyer()
		_p, thread = self.sent_step_one(a, b)
		self.reply(a, thread, "Can you send us our statement of account?", {"intent": "statement", "promise_date": "",
			"promise_amount": 0, "reference": "", "language": "en", "summary": "Asks for the statement."})
		t = frappe.db.get_value("Excom AI Task", {"agent": a.name, "kind": "prepare_statement", "subject_name": b.customer})
		self.run_now(t, {"subject": "Your statement of account", "note": "As requested, here is your statement of account."})
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_statement"})
		self.assertEqual((p.status, p.policy_action, p.to_email), ("Pending", "statement", b.billing))
		self.assertEqual(json.loads(p.payload)["invoices"], {b.old: 50000, b.part: 20000, b.fresh: 5000})
		self.assertIn(b.fresh, p.preview_html)
		self.assertTrue(json.loads(p.checks)["ok"], checks.failures(json.loads(p.checks)))

	# ---- the template steward (HLD-008)

	def opt_in(self, b, action="Granted"):
		from excom.excom.services.consent import record_consent

		ident = accounts.identity_for(phone=b.mobile, display=b.first)
		record_consent(ident, action, channel="whatsapp", purpose="transactional")
		frappe.db.commit()
		return ident

	def test_a_met_whatsapp_need_carries_the_reminder_and_its_first_use_waits(self):
		_clear_steward()
		a, b = self.agent(whatsapp=True), self.buyer(email=False)
		tpl = self.wa_template(f"qa_acc_steward_{self.n}", "Hello {{1}}, our invoices {{3}} are past their due date, "
			"{{2}} in all. Please let us know when payment is arranged.")
		need = self.met_need(a, "WhatsApp", template=tpl)
		self.set_policy(a, "payment_reminder", "Auto")
		self.opt_in(b)
		with self.steward_on(), patch.object(runner, "ai", side_effect=AssertionError("no model call")) as ai:
			p = self.step(a, b, words=None).proposal
		ai.assert_not_called()
		payload = json.loads(p.payload)
		self.assertEqual((payload["channel"], payload["template"], payload["template_need"]), ("whatsapp", tpl, need))
		self.assertEqual(payload["variables"], [b.first, fmt_money(70000, currency="INR"), f"{b.old}, {b.part}"],
			"name, total and invoice numbers filled by code from the contact and the ledger")
		self.assertIn(f"Hello {b.first}, our invoices {b.old}, {b.part} are past", payload["body"])
		self.assertEqual((p.channel_account, payload["mobile"]), (self.wa[0], b.mobile))
		self.assertEqual(p.status, "Pending", "the first send of a steward template waits for a person, even under Auto")
		self.assertIn("first send waits", json.loads(p.checks)["template"]["why"])
		recipient = json.loads(p.checks)["recipient"]
		self.assertTrue(recipient["ok"] and "opted in" in recipient["why"], recipient)

		# once a person has approved one send, the policy applies
		frappe.db.set_value("WhatsApp Templates", tpl, "xai_first_use_done", 1)
		frappe.db.set_value("Excom AI Proposal", p.name, {"status": "Cancelled", "step_key": None})
		with self.steward_on():
			again = self.step(a, b, words=None)
		self.assertNotEqual(again.proposal.name, p.name, again.outcome)
		again = again.proposal
		self.assertEqual((again.status, again.decided_by), ("Approved", "Autopilot"))

	def test_no_template_records_one_need_and_the_hand_made_template_is_used(self):
		_clear_steward()
		a, b = self.agent(whatsapp=True), self.buyer(email=False)
		hand = self.wa_template(f"qa_acc_payment_reminder_{self.n}", "Dear {{1}}, {{2}} is overdue on {{3}}. "
			"Kindly arrange the payment.")
		# a steward template whose need is not Met is never picked by its name
		self.wa_template(f"qa_acc_xai_payment_reminder_{self.n}", "Hi {{1}}, {{3}} are due: {{2}}. Thank you.",
			need="QA-ACC-NOT-MET")
		self.opt_in(b)
		with self.steward_on():
			p = self.step(a, b, words=None).proposal
		needs = frappe.get_all("Excom AI Template Need", {"need_key": REMINDER, "wa_business_id": WABA},
			["channel", "status", "agents", "language"])
		self.assertEqual(len(needs), 1, "two numbers on one WABA: one need")
		self.assertEqual((needs[0].channel, needs[0].status, needs[0].language, needs[0].agents), ("WhatsApp", "Open", "en",
			a.name))
		self.assertFalse(frappe.db.exists("Excom AI Template Need", {"agent": a.name, "channel": "Email"}),
			"no email need for a contact with no email")
		payload = json.loads(p.payload)
		self.assertEqual(payload["template"], hand, "the hand-made template, as before")
		self.assertNotIn("template_need", payload)
		self.assertEqual(payload["variables"], [b.first, fmt_money(70000, currency="INR"), f"{b.old}, {b.part}"])
		self.assertEqual(p.status, "Pending")
		self.assertFalse(templates.first_use_pending(hand), "first-use review is for the steward's templates")

	def test_an_approved_email_template_replaces_the_model_words(self):
		a, b = self.agent(), self.buyer()
		need = self.met_need(a, "Email", subject="Invoices past due for {{1}}", body="Dear {{1}},\nthe invoices below are "
			"past their due date.\n{{content}}\nCould you tell us when payment will be arranged, or if anything looks wrong?")
		with self.steward_on(), patch.object(runner, "ai", side_effect=AssertionError("no model call")) as ai:
			p = self.step(a, b, words=None).proposal
		ai.assert_not_called()
		payload = json.loads(p.payload)
		self.assertEqual(payload["template_need"], need)
		self.assertEqual(payload["subject"], f"Invoices past due for {b.first}")
		self.assertEqual(payload["opening"], f"Dear {b.first},\nthe invoices below are past their due date.")
		self.assertEqual(payload["closing"], "Could you tell us when payment will be arranged, or if anything looks wrong?")
		body = payload["body"]
		self.assertTrue(body.startswith(payload["opening"]) and body.endswith(payload["closing"]))
		for text in (b.old, b.part, f"Total overdue: {fmt_money(70000, currency='INR')}"):
			self.assertIn(text, body, "the invoice lines are still written by code")
		results = json.loads(p.checks)
		self.assertTrue(results["ok"], checks.failures(results))
		self.assertEqual((p.status, p.to_email), ("Pending", b.billing))
		# the same checks run on the template's own words
		frappe.db.set_value("Excom AI Template Need", need, {"subject": "Final notice",
			"body": "We will take legal action.\n{{content}}\nPay now."})
		frappe.db.set_value("Excom AI Proposal", p.name, {"status": "Cancelled", "step_key": None})
		with self.steward_on(), patch.object(runner, "ai", side_effect=AssertionError("no model call")):
			worse = self.step(a, b, words=None).proposal
		self.assertEqual(json.loads(worse.payload)["template_need"], need)
		self.assertFalse(json.loads(worse.checks)["collections"]["ok"])
		self.assertEqual(worse.risk, "High")

	def test_without_an_opt_in_no_whatsapp_is_written_but_the_need_is_kept(self):
		_clear_steward()
		a, b = self.agent(whatsapp=True), self.buyer(email=False)
		tpl = self.wa_template(f"qa_acc_noopt_{self.n}", "Hello {{1}}, our invoices {{3}} are past their due date, "
			"{{2}} in all. Please let us know when payment is arranged.")
		self.met_need(a, "WhatsApp", template=tpl)
		with self.steward_on():
			out = self.step(a, b, words=None)
		p = out.proposal
		self.assertFalse(p and json.loads(p.payload or "{}").get("channel") == "whatsapp", out.outcome)

	def test_a_template_send_needs_a_whatsapp_opt_in(self):
		from excom.excom.services.consent import record_consent

		_clear_steward()
		a, b = self.agent(whatsapp=True), self.buyer(email=False)
		tpl = self.wa_template(f"qa_acc_optin_{self.n}", "Hello {{1}}, our invoices {{3}} are past their due date, "
			"{{2}} in all. Please let us know when payment is arranged.")
		self.met_need(a, "WhatsApp", template=tpl)
		ident = self.opt_in(b)  # opted in when the reminder was written ...
		with self.steward_on():
			p = self.step(a, b, words=None).proposal
		self.assertEqual(json.loads(p.payload)["channel"], "whatsapp")
		self.opt_in(b, "Withdrawn")  # ... and withdrawn before it went: the send rules check again
		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(p.name, "approve"), "Approved")
		frappe.set_user("Administrator")

		def send():
			with self.steward_on(), patch("excom.excom.services.whatsapp_service.send_template_message",
					return_value={"provider_message_id": "wamid.QA-ACC"}) as meta, \
					patch("excom.excom_ai.sequencer.in_window", return_value=True):
				proposals.execute(p.name)
			return meta, frappe.db.get_value("Excom AI Proposal", p.name, ["status", "error"], as_dict=True)

		meta, row = send()
		self.assertEqual(row.status, "Failed")
		self.assertIn("opt-in", row.error)
		meta.assert_not_called()
		self.assertFalse(frappe.db.get_value("WhatsApp Templates", tpl, "xai_first_use_done"))

		record_consent(ident, "Granted", channel="whatsapp", purpose="transactional")
		frappe.db.set_value("Excom AI Proposal", p.name, {"status": "Approved", "error": None})
		frappe.db.commit()
		meta, row = send()
		self.assertEqual(row.status, "Sent", row.error)
		meta.assert_called_once()
		self.assertEqual(meta.call_args.kwargs["template_name"], f"qa_acc_optin_{self.n}")
		self.assertTrue(frappe.db.get_value("WhatsApp Templates", tpl, "xai_first_use_done"),
			"a person approved its first send")

	def test_a_statement_carries_a_whatsapp_notice_when_email_goes_unanswered(self):
		from excom.excom.services.thread_service import upsert_thread

		_clear_steward()
		a, b = self.agent(whatsapp=True), self.buyer(mobile=True)
		thread = upsert_thread(accounts.identity_for(phone=b.mobile, display=b.first), "whatsapp", self.wa[0])
		words = {"subject": "Your statement of account", "note": "As requested, here is your statement of account."}

		def prepare():
			frappe.db.delete("Excom AI Proposal", {"agent": a.name, "action": "send_statement"})
			frappe.db.delete("Excom AI Task", {"agent": a.name, "kind": "prepare_statement"})
			with self.steward_on():
				self.run_now(tasks.add(a.name, "prepare_statement", subject=("Customer", b.customer), thread=thread), words)
			return frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_statement"})

		# asked on WhatsApp, no template yet: the statement goes by email alone and the need is recorded
		p = prepare()
		self.assertNotIn("notice", json.loads(p.payload))
		self.assertEqual(frappe.db.count("Excom AI Template Need", {"need_key": "accounts.statement_ready",
			"wa_business_id": WABA}), 1)
		_clear_steward()
		tpl = self.wa_template(f"qa_acc_statement_{self.n}", "Hello {{1}}, your statement of account as of {{2}} is "
			"ready and is on its way by email.")
		self.met_need(a, "WhatsApp", key="accounts.statement_ready", template=tpl)
		p = prepare()
		notice = json.loads(p.payload)["notice"]
		self.assertEqual((notice["template"], notice["mobile"], notice["account"]), (tpl, b.mobile, self.wa[0]))
		self.assertEqual(notice["variables"], [b.first, frappe.utils.formatdate(nowdate())])
		self.assertEqual((p.status, p.to_email), ("Pending", b.billing), "the statement still goes by email, for review")
		self.assertIn("on its way by email", p.preview_html)

		# sent: the email first, then the notice; with no WhatsApp opt-in the notice is refused and
		# noted, and the statement still counts as sent
		frappe.set_user(OWNER)
		self.assertEqual(proposals.decide(p.name, "approve"), "Approved")
		frappe.set_user("Administrator")
		with self.steward_on(), patch("excom.excom.channels.email.outbound.send_email_reply", return_value="QA-ACC-MSG") as mail, \
				patch("excom.excom.services.whatsapp_service.send_template_message") as meta, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		mail.assert_called_once()
		meta.assert_not_called()
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": p.name, "content": ["like", "%notice was not sent%opt-in%"]}))

	# ---- readiness

	def test_the_accounts_readiness_rules(self):
		accounts.seed()
		keys = {r[0] for r in accounts.RULES}
		self.assertEqual(set(frappe.get_all("Excom AI Readiness Rule", {"agent_type": "Accounts", "rule_key": ["in",
			list(keys)]},
			pluck="rule_key")), keys)
		a, b = self.agent(), self.buyer()
		type(self).n += 1  # a second customer, with no contact at all
		nobody = self.buyer(contacts=False)
		with patch.object(accounts, "customers_with_dues", return_value=[b.customer]):
			self.assertTrue(accounts.billing_contacts_rule(a, {})[0])
		with patch.object(accounts, "customers_with_dues", return_value=[b.customer, nobody.customer]):
			ok, detail = accounts.billing_contacts_rule(a, {})
			self.assertFalse(ok)
			self.assertIn("1 of 2", detail)
		self.assertTrue(accounts.reminder_channel_rule(a, {})[0])
		self.assertFalse(accounts.reminder_channel_rule(frappe._dict(name=a.name, channels=[]), {})[0])
		self.assertEqual(accounts.dispute_rule(a, {})[0], bool(accounts.dispute_fields()))
		if self.msme:
			ok, detail = accounts.msme_fields_rule(a, {})
			self.assertTrue(ok, detail)
		with patch.object(accounts, "_orphan_msme_columns", return_value=["msme_type_old"]), \
				patch.object(accounts, "msme_source", return_value=frappe._dict(kind="orphaned", fields=["msme_type_old"])):
			self.assertFalse(accounts.msme_fields_rule(a, {})[0], "status Frappe cannot see is not usable")
		if not frappe.db.exists("Bank Transaction", {"name": ["not like", "QA-ACC-%"]}):
			_fixture("Bank Transaction", f"QA-ACC-BT-{self.n}", {"company": self.company, "date": add_days(nowdate(), -2),
				"status": "Unreconciled", "deposit": 100, "unallocated_amount": 100, "currency": "INR"})
			self.assertTrue(accounts.bank_feed_rule(a, {})[0])
			_fixture("Bank Transaction", f"QA-ACC-BT-{self.n}-old", {"company": self.company, "date": add_days(nowdate(), -40),
				"status": "Unreconciled", "deposit": 100, "unallocated_amount": 100, "currency": "INR"})
			ok, detail = accounts.bank_feed_rule(a, {})
			self.assertFalse(ok)
			self.assertIn("1 unreconciled", detail)
		ok, detail = accounts.payment_terms_rule(a, {})
		self.assertEqual(ok, False if not frappe.db.count("Payment Terms Template") else ok)
		self.assertIn("payment terms templates", detail)
		result = readiness.run(a.name)
		self.assertIn(result["state"], ("Blocked", "Warnings", "Ready"))
		gaps = frappe.get_all("Excom AI Readiness Gap", {"agent": a.name, "status": "Open"}, pluck="rule")
		self.assertTrue(set(gaps) <= keys, "only the Accounts type's rules apply")

	# ---- review fixes (2026-11-14)

	def approve(self, name):
		frappe.set_user(OWNER)
		status = proposals.decide(name, "approve")
		frappe.set_user("Administrator")
		return status

	def test_a_reminder_never_takes_over_a_conversation_a_person_or_another_agent_has(self):
		a, b = self.agent(), self.buyer()
		first, thread = self.sent_step_one(a, b)
		enr = self.enrollment(a, b)
		frappe.db.set_value("Excom AI Proposal", first.name, "sent_on", add_days(now_datetime(), -30))
		# a person took the conversation (a handover): the next step is not written there
		frappe.db.set_value("Excom Thread", thread, "ai_mode", "Needs person")
		frappe.db.set_value("Excom AI Enrollment", enr.name, "next_due", add_days(now_datetime(), -1))
		out = self.step(a, b, words=None).outcome
		self.assertIn("not written", out)
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"agent": a.name, "action": "payment_reminder", "step": 2}))
		self.assertEqual(frappe.db.get_value("Excom Thread", thread, "ai_mode"), "Needs person", "left as the person set it")
		enr.reload()
		self.assertEqual(enr.status, "Paused")
		self.assertIn("a person has the conversation", enr.stop_reason)
		self.assertTrue(frappe.db.exists("ToDo", {"reference_name": b.customer, "allocated_to": OWNER, "status": "Open",
			"description": ["like", "%was not written%"]}), "the owner is told")

		# handed back: step 2 is written; a colleague takes the conversation before it goes out
		frappe.db.set_value("Excom Thread", thread, "ai_mode", "Handle")
		frappe.db.set_value("Excom AI Enrollment", enr.name, "next_due", add_days(now_datetime(), -1))
		second = self.step(a, b).proposal
		self.assertEqual((second.action, second.step), ("payment_reminder", 2))
		self.assertEqual(self.approve(second.name), "Approved")
		frappe.db.set_value("Excom Thread", thread, {"ai_agent": None, "ai_mode": None, "assigned_to": FINANCE})
		with patch("excom.excom.channels.email.outbound.send_email_reply") as send, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(second.name)
		send.assert_not_called()
		row = frappe.db.get_value("Excom AI Proposal", second.name, ["status", "hold_reason"], as_dict=True)
		self.assertEqual(row.status, "Cancelled")
		self.assertIn(FINANCE, row.hold_reason)
		t = frappe.db.get_value("Excom Thread", thread, ["ai_agent", "ai_mode", "assigned_to"], as_dict=True)
		self.assertEqual((t.ai_agent, t.ai_mode, t.assigned_to), (None, None, FINANCE), "not taken over")

	def test_a_promise_is_judged_only_on_its_own_ladder_and_invoices(self):
		a, b = self.agent(), self.buyer()
		_p, thread = self.sent_step_one(a, b)
		self.reply(a, thread, "We will clear everything on the 15th.", {"intent": "promise",
			"promise_date": str(add_days(nowdate(), 5)), "promise_amount": 0, "reference": "", "language": "en",
			"summary": "Will pay in full."})
		rec = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "record_promise"})
		payload = json.loads(rec.payload)
		self.assertEqual(payload["invoices"], {b.old: 50000, b.part: 20000}, "the invoices the promise is about")
		# paid in full; meanwhile the third invoice fell due: the promise was kept all the same
		for inv in (b.old, b.part):
			frappe.db.set_value("Sales Invoice", inv, "outstanding_amount", 0)
		frappe.db.set_value("Sales Invoice", b.fresh, "due_date", add_days(nowdate(), -1))
		frappe.db.set_value("Excom AI Proposal", rec.name, "payload", json.dumps({**payload,
			"date": str(add_days(nowdate(), -2))}))
		enr = self.enrollment(a, b)
		frappe.db.set_value("Excom AI Enrollment", enr.name, "next_due", add_to_date(now_datetime(), hours=-1))
		out = self.step(a, b, words=None).outcome
		self.assertFalse(frappe.db.exists("Comment", {"reference_name": b.customer, "content": ["like", "%was not kept%"]}),
			out)
		self.assertEqual(json.loads(frappe.db.get_value("Excom AI Proposal", rec.name, "payload"))["judged"], "kept")

		# that ladder ends; a new one starts when the customer falls behind again: the old promise
		# (made on the earlier ladder, its date long past) is not this ladder's to judge
		frappe.db.set_value("Excom AI Proposal", rec.name, "payload", json.dumps({**payload,
			"date": str(add_days(nowdate(), -2))}))  # not judged
		accounts.close(frappe.get_doc("Excom AI Enrollment", enr.name), "Completed", "QA: paid")
		frappe.db.set_value("Sales Invoice", b.old, "outstanding_amount", 50000)
		new = accounts.enrollment_for(a, b.customer)
		self.assertNotEqual(new.name, enr.name)
		self.assertIsNone(accounts.last_promise(b.customer, a, new))
		frappe.db.set_value("Excom AI Enrollment", new.name, {"status": "Paused",
			"next_due": add_to_date(now_datetime(), hours=-1)})
		fresh = self.step(a, b)
		self.assertEqual((fresh.proposal.step, json.loads(fresh.proposal.payload)["broken_promise"]), (1, False),
			fresh.outcome)
		self.assertFalse(frappe.db.exists("Comment", {"reference_name": b.customer, "content": ["like", "%was not kept%"]}))

	def test_a_promise_waiting_for_review_pauses_the_ladder_a_week_at_most(self):
		a, b = self.agent(), self.buyer()
		self.set_policy(a, "create_record", "Review")
		_p, thread = self.sent_step_one(a, b)
		when = add_days(nowdate(), 60)
		self.reply(a, thread, "We will pay in two months.", {"intent": "promise", "promise_date": str(when),
			"promise_amount": 0, "reference": "", "language": "en", "summary": "Will pay in two months."})
		rec = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "record_promise"})
		self.assertEqual(rec.status, "Pending")
		enr = self.enrollment(a, b)
		self.assertEqual(enr.status, "Paused")
		self.assertLessEqual(get_datetime(enr.next_due), add_days(now_datetime(), accounts.PROMISE_REVIEW_DAYS),
			"a reply alone cannot stall the ladder until its own date")
		self.assertIsNone(accounts.last_promise(b.customer, a, enr), "not on record until a person approves it")
		# approved: now the ladder waits for the promised date
		self.approve(rec.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", rec.name, "status"), "Done")
		self.assertEqual(getdate(self.enrollment(a, b).next_due), getdate(add_days(when, accounts.GRACE_DAYS)))

	def test_an_approved_reminder_waits_out_a_quiet_date_but_answers_do_not(self):
		from excom.excom_ai.engine import gates

		a, b = self.agent(), self.buyer()
		p = self.step(a, b).proposal
		self.assertEqual(self.approve(p.name), "Approved")
		today = getdate(nowdate())
		quiet = patch.object(gates, "is_quiet_date", side_effect=lambda day=None: getdate(day or nowdate()) == today)
		with quiet, patch("excom.excom.channels.email.outbound.send_email_reply") as send, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		send.assert_not_called()
		row = frappe.db.get_value("Excom AI Proposal", p.name, ["status", "send_at", "hold_reason"], as_dict=True)
		self.assertEqual(row.status, "Approved")
		self.assertEqual(get_datetime(row.send_at), get_datetime(add_days(today, 1)), "tomorrow, then its window")
		self.assertIn("Quiet date", row.hold_reason)
		with quiet:
			for action, kind in (("send_statement", "Reply"), ("reply", "Reply")):
				self.assertIsNone(gates.quiet_until(frappe._dict(action=action, kind=kind)), "an answer to someone who wrote")
			for action, kind in (("send_email", "Sequence"), ("po_chase", "Reply"), ("send_rfq", "Reply")):
				self.assertIsNotNone(gates.quiet_until(frappe._dict(action=action, kind=kind)), action)
		with patch.object(gates, "is_quiet_date", return_value=False), \
				patch("excom.excom.channels.email.outbound.send_email_reply", return_value="QA-ACC-MSG") as send, \
				patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		send.assert_called_once()
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")

	def test_reminders_go_in_the_customers_own_office_hours(self):
		"""The real window (no patched in_window): the customer's billing address is in New York and the
		site's clock is the system zone's."""
		from datetime import date, datetime
		from zoneinfo import ZoneInfo

		from excom.excom_ai import sequencer

		a, b = self.agent(), self.buyer()
		frappe.get_doc({"doctype": "Address", "address_title": f"QA Acc Buyer {self.n}", "address_type": "Billing",
			"address_line1": "1 Main Street", "city": "New York", "country": "United States",
			"links": [{"link_doctype": "Customer", "link_name": b.customer}]}).insert(ignore_permissions=True)
		zone = "America/New_York"
		self.assertEqual(accounts.customer_zone(b.customer, a), zone)
		p = self.step(a, b).proposal
		self.assertEqual(self.approve(p.name), "Approved")

		def at(hour):  # Wednesday 18 November 2026 at this hour in New York, as the site's clock reads it
			return sequencer.from_zone(datetime(2026, 11, 18, hour, 0, tzinfo=ZoneInfo(zone)))

		def send(when):
			with patch("excom.excom.channels.email.outbound.send_email_reply", return_value="QA-ACC-MSG") as mail, \
					patch.object(accounts, "now_datetime", return_value=when):
				proposals.execute(p.name)
			return mail, frappe.db.get_value("Excom AI Proposal", p.name, ["status", "send_at", "hold_reason"], as_dict=True)

		mail, row = send(at(3))  # 03:00 there
		mail.assert_not_called()
		self.assertEqual(row.status, "Approved")
		self.assertIn(zone, row.hold_reason)
		local = sequencer.to_zone(row.send_at, zone)
		self.assertEqual((local.date(), local.hour), (date(2026, 11, 18), 8), "the start of their next office hours")
		mail, row = send(at(11))  # 11:00 there
		mail.assert_called_once()
		self.assertEqual(row.status, "Sent")

	def test_amounts_are_in_the_currency_owed_and_never_added_across_currencies(self):
		a, b = self.agent(), self.buyer()
		debtors = frappe.db.get_value("Company", self.company, "default_receivable_account")
		common = {"customer": b.customer, "customer_name": b.customer, "company": self.company, "debit_to": debtors,
			"posting_date": add_days(nowdate(), -40), "due_date": add_days(nowdate(), -30), "currency": "USD",
			"conversion_rate": 83}
		# a dollar invoice on the customer's rupee account: ERPNext keeps what is owed in rupees
		usd = _fixture("Sales Invoice", f"QA-ACC-SINV-{self.n}-USD", {**common, "grand_total": 1000, "base_grand_total": 83000,
			"outstanding_amount": 83000, "party_account_currency": "INR", "is_return": 0})
		# a dollar credit note on the same rupee account
		_fixture("Sales Invoice", f"QA-ACC-SINV-{self.n}-CN", {**common, "grand_total": -10, "base_grand_total": -830,
			"outstanding_amount": -830, "party_account_currency": "INR", "is_return": 1})
		frappe.db.commit()
		rows = {r.name: r for r in accounts.open_invoices(b.customer, self.company)}
		self.assertEqual((rows[usd].currency, rows[usd].amount, rows[usd].outstanding_amount), ("INR", 83000, 83000))
		html = accounts.statement_table(list(rows.values()))
		self.assertIn(fmt_money(83000, currency="INR"), html)
		self.assertNotIn(fmt_money(1000, currency="INR"), html, "never a dollar figure labelled in rupees")
		self.assertEqual(accounts.credits(b.customer, self.company), {"INR": 830})
		# owed on a dollar account as well: a total per currency, never one sum
		_fixture("Sales Invoice", f"QA-ACC-SINV-{self.n}-USD2", {**common, "grand_total": 500, "base_grand_total": 41500,
			"outstanding_amount": 500, "party_account_currency": "USD", "is_return": 0})
		lines = accounts.invoice_lines(accounts.open_invoices(b.customer, self.company, overdue_only=True), "en")
		self.assertIn(f"Total overdue: {fmt_money(500, currency='USD')}", lines)
		self.assertIn(f"Total overdue: {fmt_money(70000 + 83000, currency='INR')}", lines)

	def test_a_customer_on_another_accounts_agents_ladder_is_left_to_it(self):
		a, b = self.agent(), self.buyer()
		self.step(a, b)
		first = self.enrollment(a, b)
		other = self.agent(suffix=" Two")
		self.assertNotIn(b.customer, accounts.due_customers(other, self.company))
		frappe.db.delete("Excom AI Task", {"agent": other.name, "kind": "collections_step"})
		out = self.run_now(tasks.add(other.name, "collections_step", subject=("Customer", b.customer)))
		self.assertIn("another Accounts agent", out)
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"agent": other.name, "action": "payment_reminder"}))
		self.assertIsNone(accounts.enrollment_for(other, b.customer, create=False), "no second ladder")
		# the first ladder closes: the customer is free for another agent
		accounts.close(first, "Completed", "QA")
		self.assertIn(b.customer, accounts.due_customers(other, self.company))

	def test_says_paid_matching_reads_only_that_customers_invoices(self):
		a, b = self.agent(), self.buyer()
		type(self).n += 1
		c = self.buyer()
		bt = _fixture("Bank Transaction", f"QA-ACC-BT-{self.n}", {"company": self.company, "date": add_days(nowdate(), -1),
			"status": "Unreconciled", "deposit": 20000, "withdrawal": 0, "unallocated_amount": 20000, "allocated_amount": 0,
			"currency": "INR", "description": f"NEFT CR {c.part}", "reference_number": "UTR-QA-C", "party_type": "Customer",
			"party": c.customer})
		frappe.db.commit()
		seen = []
		real = frappe.get_all

		def spy(doctype, *args, **kwargs):
			if doctype == "Sales Invoice":
				seen.append(dict(args[0] if args else kwargs.get("filters") or {}))
			return real(doctype, *args, **kwargs)

		with patch("frappe.get_all", side_effect=spy):
			self.assertEqual(accounts.candidates_for_customer(b.customer, self.company), [], "c's payment is not b's")
		self.assertTrue(seen)
		self.assertTrue(all(f.get("customer") == b.customer for f in seen), "only that customer's open invoices are read")
		self.assertEqual(accounts.candidates_for_customer(c.customer, self.company), [bt])

	def test_the_whatsapp_recipient_check_is_real(self):
		b = self.buyer(email=False)
		ok, why = accounts.whatsapp_recipient(b.mobile)
		self.assertFalse(ok, "no opt-in yet")
		self.assertIn("opt-in", why)
		ident = self.opt_in(b)
		ok, why = accounts.whatsapp_recipient(b.mobile)
		self.assertTrue(ok, why)
		frappe.db.set_value("Omni Identity", ident, "do_not_contact", 1)
		ok, why = accounts.whatsapp_recipient(b.mobile)
		self.assertFalse(ok)
		self.assertIn("do-not-contact", why)
		self.assertFalse(accounts.whatsapp_recipient("12")[0], "not a mobile number")

	def test_a_reply_nobody_could_read_reaches_a_person(self):
		a, b = self.agent(), self.buyer()
		_p, thread = self.sent_step_one(a, b)
		ident = frappe.db.get_value("Excom Thread", thread, "omni_identity")
		text = "About the invoices: see the attached remittance."
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": ident, "direction": "Inbound", "message_type": "Email",
			"content_text": text, "content_json": json.dumps({"subject": "Re: your account"}), "delivery_status": "Delivered"}
			).insert(ignore_permissions=True)
		inbound.on_inbound({"thread": thread, "message": msg.name, "channel": "email", "text": text})
		enr = self.enrollment(a, b)
		self.assertEqual(enr.status, "Replied")
		t = frappe.db.get_value("Excom AI Task", {"agent": a.name, "kind": "inbound", "thread": thread, "status": "Queued"})
		self.assertEqual(accounts.unread_replies(a), 0, "just now, and its task is still queued")
		# the responder failed for good: the ladder would sit in Replied for ever
		frappe.db.set_value("Excom AI Task", t, {"status": "Failed", "error": "TimeoutError: the model did not answer"})
		frappe.db.set_value("Excom AI Enrollment", enr.name, "modified", add_to_date(now_datetime(), hours=-3),
			update_modified=False)
		self.assertEqual(accounts.unread_replies(a), 1)
		enr.reload()
		self.assertEqual(enr.status, "Stopped")
		self.assertIn("could not read the reply", enr.stop_reason)
		self.assertTrue(frappe.db.exists("ToDo", {"reference_name": b.customer, "allocated_to": OWNER,
			"description": ["like", "%replied to a collections reminder%"]}))
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER,
			"subject": ["like", "Collections: a reply needs you%"]}))
		self.assertEqual(accounts.unread_replies(a), 0, "once")
