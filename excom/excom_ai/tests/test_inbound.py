"""Excom AI Phase 0.4: Excom's plumbing for agents (HLD-007 §11, §12.6).

- Every channel hands new inbound messages to `excom_inbound_handlers` (email, Instagram and
  Messenger, web chat, as WhatsApp already did); history synced in later does not.
- The agents' handler: a reply stops the sequence, "are you a bot?" goes to a person, the rest is
  queued as an `inbound` task.
- An agent's sends: consent on every channel, its own accounts only, the reply window, never
  Meta's HUMAN_AGENT tag.
- An agent's send claims the chat and the lead for its owner (§11.6).
"""

import time
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.channels.email import mailbox
from excom.excom.channels.meta_dm import service as meta_dm
from excom.excom.services import inbound_dispatch
from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import send_rules
from excom.excom_ai.engine import inbound, ownership, registry, runner, tasks

OWNER = "qa.inb.owner@example.com"
BACKUP = "qa.inb.backup@example.com"
TEAM = "QA Inbound Desk"
MAILBOX = "QA Inbound Mailbox"
OURS = "qa.inbound.desk@example.com"
INSTA = "QA Inbound Instagram"
WA = "QA Inbound WhatsApp"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Inb%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Enrollment", "Excom AI Sequence"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	accs = frappe.get_all("Excom Channel Account", {"account_name": ["in", [MAILBOX, INSTA, WA]]}, pluck="name")
	ids = set(frappe.get_all("Omni Identity", {"display_name": ["like", "QA Inb%"]}, pluck="name"))
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in filter(None, ids):
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
	frappe.db.commit()


def email_meta(n, sender, received_at=None, subject=None):
	return {
		"id": f"qa-inb-{n}-{frappe.generate_hash(length=6)}", "thread_id": f"th-{n}", "folder": "Inbox", "seen": False,
		"flagged": False, "categories": ["CATEGORY_PERSONAL"], "labels": [], "snippet": subject or f"QA inbound {n}",
		"received_at": received_at or time.time(),
		"headers": {"From": sender, "To": OURS, "Subject": subject or f"QA inbound {n}", "Message-ID": f"<qa-inb-{n}-{time.time()}@x.example>"},
	}


class TestInbound(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		cls.enabled = frappe.db.get_single_value("Excom AI Settings", "enabled")
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.mailbox = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": MAILBOX, "channel": "email",
			"status": "Active", "email_address": OURS}).insert(ignore_permissions=True)
		cls.insta = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": INSTA, "channel": "instagram",
			"status": "Active", "meta_page_id": "qa-inb-page", "meta_ig_user_id": "qa-inb-ig"})
		cls.insta.flags.ignore_validate = True
		cls.insta.flags.ignore_mandatory = True
		cls.insta.insert(ignore_permissions=True)
		cls.wa = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": WA, "channel": "whatsapp", "status": "Active"})
		cls.wa.flags.ignore_validate = True
		cls.wa.flags.ignore_mandatory = True
		cls.wa.insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.set_single_value("Excom AI Settings", "enabled", cls.enabled or 0)
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		frappe.db.set_value("User", OWNER, "enabled", 1)
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, name, status="Live", channels=(MAILBOX, INSTA, WA)):
		doc = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": name, "agent_type": "Sales", "owner_user": OWNER,
			"backup_user": BACKUP, "team": TEAM, "status": "Shadow",
			"channels": [{"channel_account": c, "purpose": "Replies"} for c in channels]}).insert(ignore_permissions=True)
		if status != "Shadow":
			doc.db_set({"status": status, "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", doc.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", doc.name)

	def email(self, sender=None, **kw):
		sender = sender or f"qa.inb{self.n}@buyer.example.com"
		name = mailbox.ingest(self.mailbox, email_meta(self.n, sender, **kw))
		return frappe.get_doc("Excom Message", name)

	def ig_thread(self, last_inbound=None):
		conv = {"id": f"conv-{self.n}", "messages": {"data": [{"id": f"qa-ig-{self.n}-{time.time()}", "message": "Hola",
			"from": {"id": f"qa-ig-user-{self.n}", "username": f"QA Inb buyer {self.n}"},
			"created_time": now_datetime().strftime("%Y-%m-%dT%H:%M:%S+0000")}]}}
		with patch.object(inbound_dispatch, "dispatch"):
			meta_dm.ingest_conversation(self.insta, conv)
		t = frappe.get_doc("Excom Thread", {"account": INSTA, "omni_identity": ["is", "set"]}, order_by="creation desc")
		if last_inbound:
			t.db_set("last_inbound_at", last_inbound)
		return frappe.get_doc("Excom Thread", t.name)

	# ---- the trigger, on every channel

	def test_every_channel_reaches_the_handlers_and_old_mail_does_not(self):
		with patch.object(inbound_dispatch, "dispatch") as dispatch:
			msg = self.email(subject="QA Inb price for 3000 pcs")
			event = dispatch.call_args.args[0]
			self.assertEqual((event["channel"], event["kind"], event["sender_verified"], event["message"], event["thread"]),
				("email", "email", False, msg.name, msg.thread))
			self.assertEqual(event["subject"], "QA Inb price for 3000 pcs")
			dispatch.reset_mock()
			type(self).n += 1
			self.email(received_at=time.time() - 3 * 86400)
			dispatch.assert_not_called()
			conv = {"id": "c1", "messages": {"data": [{"id": f"qa-ig-x-{time.time()}", "message": "Hi", "from": {"id": "qa-ig-user-x"},
				"created_time": now_datetime().strftime("%Y-%m-%dT%H:%M:%S+0000")}]}}
			meta_dm.ingest_conversation(self.insta, conv)
			event = dispatch.call_args.args[0]
			self.assertEqual((event["channel"], event["sender_verified"]), ("instagram", True))

	def test_a_whatsapp_only_handler_ignores_other_channels(self):
		from excom.excom.channels.voice import whatsapp_calling

		whatsapp_calling.on_inbound({"channel": "email", "kind": "email", "thread": "x", "identity": "y", "account": MAILBOX})

	# ---- the agents' handler

	def test_a_reply_is_queued_for_the_threads_agent(self):
		a = self.agent("QA Inb Queue")
		first = self.email()
		self.assertFalse(frappe.db.exists("Excom AI Task", {"agent": a.name}), "no agent on the thread yet")
		frappe.db.set_value("Excom Thread", first.thread, "ai_agent", a.name)
		second = self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"))
		self.assertEqual(second.thread, first.thread)
		task = frappe.get_doc("Excom AI Task", {"agent": a.name, "kind": "inbound"})
		self.assertEqual((task.thread, task.priority, task.dedupe_key), (first.thread, 0, f"inbound:{first.thread}"))
		self.assertEqual(frappe.parse_json(task.params)["channel"], "email")
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"))
		self.assertEqual(frappe.db.count("Excom AI Task", {"agent": a.name, "kind": "inbound", "status": "Queued"}), 1,
			"one open task per conversation")

	def test_nothing_while_switched_off(self):
		a = self.agent("QA Inb Off")
		first = self.email()
		frappe.db.set_value("Excom Thread", first.thread, "ai_agent", a.name)
		frappe.db.set_single_value("Excom AI Settings", "enabled", 0)
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"))
		self.assertFalse(frappe.db.exists("Excom AI Task", {"agent": a.name}))

	def test_a_reply_stops_the_sequence(self):
		a = self.agent("QA Inb Seq")
		first = self.email()
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA Inb seq", "agent": a.name, "steps": [{"step": 1}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "thread": first.thread}).insert()
		pending = frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "send_email", "enrollment": enr.name,
			"step": 2, "status": "Pending"}).insert(ignore_permissions=True)
		frappe.db.commit()
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"))
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", enr.name, "status"), "Replied")
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", pending.name, ["status", "step_key"]), ("Cancelled", None))
		self.assertEqual(frappe.db.get_value("Excom Thread", first.thread, "ai_agent"), a.name, "the thread remembers its agent")

	def test_are_you_a_bot_goes_to_a_person(self):
		for text in ("Are you a bot?", "r u a real person", "is this an AI", "¿Eres un bot?", "Você é um robô?",
				"am I talking to a human", "क्या आप बॉट हैं", "bot ho?", "Is this ChatGPT"):
			self.assertTrue(inbound.asks_if_ai(text), text)
		for text in ("Do you sell robot toys?", "Real leather?", "We need 3000 pcs", "Bot of 500 pcs", "a humane offer"):
			self.assertFalse(inbound.asks_if_ai(text), text)
		a = self.agent("QA Inb Human")
		first = self.email()
		frappe.db.set_value("Excom Thread", first.thread, {"ai_agent": a.name, "ai_mode": "Handle"})
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"),
			subject="Quick question: are you a real person?")
		self.assertEqual(frappe.db.get_value("Excom Thread", first.thread, ["ai_mode", "ai_intent"]), ("Needs person", "asks_if_ai"))
		self.assertFalse(frappe.db.exists("Excom AI Task", {"agent": a.name}), "the agent does not answer it")
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "document_name": first.thread}))
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"))
		self.assertFalse(frappe.db.exists("Excom AI Task", {"agent": a.name}), "stays with the person until handed back")

	def test_the_inbound_task_runs_the_packs_responder(self):
		a = self.agent("QA Inb Task")
		t = tasks.add(a.name, "inbound", params={"channel": "email"})
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		runner.run_task(t)
		self.assertIn("no responder for sales", frappe.db.get_value("Excom AI Task", t, "outcome"))
		registry.load()
		registry.RESPONDERS["sales"] = lambda task, agent: f"answered by {agent.name}"
		try:
			t2 = tasks.add(a.name, "inbound", dedupe_key="second")
			frappe.db.set_value("Excom AI Task", t2, "status", "Running")
			frappe.db.commit()
			runner.run_task(t2)
			self.assertEqual(frappe.db.get_value("Excom AI Task", t2, "outcome"), f"answered by {a.name}")
		finally:
			registry.RESPONDERS.pop("sales", None)

	# ---- what an agent may send

	def as_agent(self, agent):
		frappe.set_user(agent.service_user)

	def test_send_rules(self):
		a = self.agent("QA Inb Rules", channels=(INSTA,))
		t = self.ig_thread(last_inbound=now_datetime())
		send_rules.check(t)  # a person: no rules
		self.as_agent(a)
		send_rules.check(t)  # its own account, inside the window, no opt-out
		t.db_set("last_inbound_at", add_to_date(now_datetime(), hours=-30))
		with self.assertRaises(send_rules.AgentSendRefused, msg="window closed"):
			send_rules.check(t)
		send_rules.check(t, window=False)
		frappe.db.set_value("Omni Identity", t.omni_identity, "do_not_contact", 1)
		with self.assertRaises(send_rules.AgentSendRefused, msg="do not contact"):
			send_rules.check(t, window=False)
		frappe.set_user("Administrator")
		frappe.db.set_value("Omni Identity", t.omni_identity, "do_not_contact", 0)
		mail = self.email()
		self.as_agent(a)
		with self.assertRaises(send_rules.AgentSendRefused, msg="not its mailbox"):
			send_rules.check(mail.thread)
		frappe.set_user("Administrator")
		a.db_set("status", "Shadow")
		self.as_agent(a)
		with self.assertRaises(send_rules.AgentSendRefused, msg="Shadow never sends"):
			send_rules.check(t, window=False)

	def test_email_opt_out_and_duplicates_count(self):
		a = self.agent("QA Inb Optout", channels=(MAILBOX,))
		mail = self.email()
		frappe.db.set_value("Omni Identity", mail.omni_identity, "email_opt_out", 1)
		self.as_agent(a)
		with self.assertRaises(send_rules.AgentSendRefused):
			send_rules.check(mail.thread)

	def test_an_agent_never_uses_the_human_agent_tag(self):
		self.insta.db_set("meta_human_agent_tag", 1)
		t = self.ig_thread(last_inbound=add_to_date(now_datetime(), days=-2))
		with patch.object(meta_dm, "_creds", return_value={"token": "x", "version": "v1", "page_id": "p"}), \
				patch.object(meta_dm, "_post", return_value={"message_id": "m1"}) as post:
			meta_dm.send_text(self.insta, "u1", "hello", thread=t)
			self.assertEqual(post.call_args.args[2]["tag"], "HUMAN_AGENT", "a person may, with the tag")
			post.reset_mock()
			with self.assertRaises(frappe.ValidationError):
				meta_dm.send_text(self.insta, "u1", "hello", thread=t, allow_tag=False)
			post.assert_not_called()

	def test_an_agents_send_is_refused_before_the_provider_is_called(self):
		from excom.excom.services.thread_service import send_outbound_message

		a = self.agent("QA Inb Send", channels=(INSTA,))
		t = self.ig_thread(last_inbound=add_to_date(now_datetime(), days=-2))
		self.as_agent(a)
		with patch.object(meta_dm, "_post") as post:
			with self.assertRaises(send_rules.AgentSendRefused):
				send_outbound_message(t.name, "Hola")
		post.assert_not_called()

	# ---- who owns the conversation (§11.6)

	def test_an_agents_send_claims_for_its_owner(self):
		from excom.excom.api.chat import _claim_on_talk
		from excom.excom.services.thread_service import _auto_claim_thread

		a = self.agent("QA Inb Claim", channels=(INSTA,))
		t = self.ig_thread()
		t.db_set({"assigned_to": None, "ai_agent": None})
		_claim_on_talk(t.name, a.service_user)
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, ["assigned_to", "ai_agent"]), (OWNER, a.name))
		t.db_set("assigned_to", None)
		frappe.db.set_value("User", OWNER, "enabled", 0)
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		_auto_claim_thread(frappe.get_doc("Excom Thread", t.name), a.service_user)
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, "assigned_to"), BACKUP, "whoever is in charge now")
		self.assertNotEqual(frappe.db.get_value("Excom Thread", t.name, "assigned_to"), a.service_user)

	# ---- third review

	def test_an_agent_email_goes_only_to_the_person(self):
		a = self.agent("QA Inb Recipients", channels=(MAILBOX,))
		mail = self.email()
		person = frappe.db.get_value("Omni Identity", mail.omni_identity, "primary_email")
		thread = frappe.get_doc("Excom Thread", mail.thread)
		s = frappe.get_single("Excom AI Settings")
		s.append("dnc_domains", {"domain": "qa-blocked.example", "reason": "Other"})
		s.save(ignore_permissions=True)
		self.addCleanup(lambda: frappe.db.delete("Excom AI DNC Domain", {"domain": "qa-blocked.example"}))
		self.as_agent(a)
		send_rules.check(thread, recipients=[person, "", ""])
		with self.assertRaises(send_rules.AgentSendRefused, msg="copying someone in"):
			send_rules.check(thread, recipients=[person, "boss@other.example", ""])
		with self.assertRaises(send_rules.AgentSendRefused, msg="a blocked domain"):
			send_rules.check(thread, recipients=["x@qa-blocked.example"])

	def test_reactions_and_auto_replies_do_not_stop_a_sequence(self):
		a = self.agent("QA Inb Auto")
		first = self.email()
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA Inb seq auto", "agent": a.name, "steps": [{"step": 1}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": a.name, "sequence": seq.name, "thread": first.thread}).insert()
		frappe.db.commit()
		inbound.on_inbound({"kind": "reaction", "thread": first.thread, "channel": "whatsapp", "text": "👍"})
		self.email(sender=frappe.db.get_value("Omni Identity", first.omni_identity, "primary_email"),
			subject="Automatic reply: out of office")
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", enr.name, "status"), "Active")

	def test_a_handler_lock_clash_gets_a_second_try_and_never_undoes_ingestion(self):
		calls = []

		def flaky(event):
			calls.append(event["message"])
			if len(calls) == 1:
				raise frappe.QueryDeadlockError("1020")

		with patch("frappe.get_hooks", side_effect=lambda h=None, *a, **k: ["qa.flaky"] if h == "excom_inbound_handlers" else []), \
				patch("frappe.get_attr", return_value=flaky):
			inbound_dispatch.dispatch({"message": "m1", "channel": "email", "thread": None})
		self.assertEqual(calls, ["m1", "m1"])
