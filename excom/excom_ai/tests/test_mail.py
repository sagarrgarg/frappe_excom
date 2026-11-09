"""Excom AI Phase 1B: mail for agents (HLD-007 §11.1). Unsubscribe headers and link, bounces, photo
cards, pacing and mailbox health. A fake server stands in for Gmail / JMAP and DNS answers are
scripted, so nothing touches the network. (The end-to-end test on a real Stalwart mailbox is the
manual step this phase defers to deployment.)"""

import time
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from excom.excom.channels.email import bounces, mailbox
from excom.excom.channels.email import providers as P
from excom.excom.channels.email.providers import gmail, jmap
from excom.excom.tests.fixtures import purge_users
from excom.excom.tests.test_mail_folders import FakeServer
from excom.excom_ai import mail
from excom.excom_ai.builder import readiness

OWNER = "qa.mail.owner@example.com"
BACKUP = "qa.mail.backup@example.com"
TEAM = "QA Mail Desk"
BOX = "QA Agent Mailbox"
OURS = "qa.agent.box@qa-mail.example"
BUYER = "comprador@qa-buyer.example"


def dsn(recipient=BUYER, code="5.1.1", original="<qa-orig@qa-mail.example>"):
	return (f"This is the mail system at host mx.qa-mail.example.\n\nI'm sorry to have to inform you that your message "
		f"could not be delivered.\n\nReporting-MTA: dns; mx.qa-mail.example\nFinal-Recipient: rfc822; {recipient}\n"
		f"Action: failed\nStatus: {code}\nDiagnostic-Code: smtp; 550 5.1.1 <{recipient}>: Recipient address rejected: "
		f"User unknown\n\nMessage-ID: {original}\nSubject: Water guns for your stores\n")


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Mail%"]}, pluck="name")
	frappe.db.delete("Excom AI Enrollment", {"agent": ["in", agents or ["-"]]})
	frappe.db.delete("Excom AI Sequence", {"agent": ["in", agents or ["-"]]})
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	accs = frappe.get_all("Excom Channel Account", {"account_name": BOX}, pluck="name")
	frappe.db.delete("Excom Mail Bounce", {"account": ["in", accs or ["-"]]})
	ids = set(frappe.get_all("Omni Identity", {"primary_email": ["like", "%qa-buyer.example"]}, pluck="name"))
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in filter(None, ids):
		frappe.db.delete("Excom Consent", {"omni_identity": oi}) if frappe.db.table_exists("Excom Consent") else None
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for lead in frappe.get_all("Lead", {"email_id": ["like", "%qa-buyer.example"]}, pluck="name"):
		frappe.db.delete("Excom AI Enrollment", {"lead": lead})
		from excom.excom_ai.tests.test_ai_records import _delete_lead

		_delete_lead(lead)
	for a in accs:
		frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP]]})
	purge_users(OWNER, BACKUP)
	frappe.db.commit()


class TestAgentMail(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.acc = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email",
			"status": "Active", "email_address": OURS}).insert(ignore_permissions=True)
		cls.agent = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": "QA Mail Lucía", "agent_type": "Sales",
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM,
			"channels": [{"channel_account": cls.acc.name, "purpose": "Outreach and replies", "daily_cap": 30,
				"warmup_start": add_days(nowdate(), -3)}]}).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.server = FakeServer()
		self._p = patch.object(P, "get", lambda account: self.server)
		self._p.start()
		self.addCleanup(self._p.stop)
		type(self).k = getattr(type(self), "k", 0) + 1

	def receive(self, sender, subject, folder="Inbox", snippet="hello"):
		meta = {"id": f"qa-mail-{self.k}-{time.time()}", "thread_id": "th", "folder": folder, "seen": False, "flagged": False,
			"labels": [], "snippet": snippet, "received_at": time.time(),
			"headers": {"From": sender, "To": OURS, "Subject": subject, "Message-ID": f"<qa-in-{time.time()}@x>"}}
		return mailbox.ingest(self.acc, meta), meta

	def sent_to(self, address, internet_id="<qa-orig@qa-mail.example>"):
		"""An email this mailbox sent to a buyer (as an agent's send would leave it)."""
		name, _meta = self.receive(address, "Hola")
		thread = frappe.db.get_value("Excom Message", name, "thread")
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.acc.name,
			"account_doctype": "Excom Channel Account", "omni_identity": frappe.db.get_value("Excom Message", name, "omni_identity"),
			"direction": "Outbound", "message_type": "Email", "content_text": "Water guns", "delivery_status": "Sent",
			"internet_message_id": internet_id, "provider_message_id": f"qa-out-{time.time()}",
			"created_by_user": self.agent.service_user}).insert(ignore_permissions=True)
		return msg

	# ---- unsubscribe

	def test_cold_mail_carries_unsubscribe_headers(self):
		h = mail.cold_headers("OI-QA", self.acc)
		self.assertIn("excom.excom_ai.mail.unsubscribe?i=OI-QA&t=", h["List-Unsubscribe"])
		self.assertIn(f"mailto:{OURS}", h["List-Unsubscribe"])
		self.assertEqual(h["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
		raw = gmail._raw(self.acc, {"to": BUYER, "subject": "s", "body_html": "<p>x</p>", "headers": h})
		import base64

		text = base64.urlsafe_b64decode(raw).decode()
		self.assertIn("List-Unsubscribe-Post: List-Unsubscribe=One-Click", text)
		self.assertNotIn("X-Evil", gmail._raw(self.acc, {"to": BUYER, "headers": {"X-Evil": "1"}}), "only known headers")
		boxes = type("B", (), {"id_of": lambda self, f: "drafts"})()
		with patch.object(jmap, "_from_email", return_value=OURS):
			email = jmap._new_email(self.acc, {"to": BUYER, "subject": "s", "body_html": "x", "headers": h}, boxes)
		self.assertEqual(email["header:List-Unsubscribe-Post:asText"], "List-Unsubscribe=One-Click")

	def test_the_unsubscribe_link_opts_out_and_stops_sequences(self):
		msg = self.sent_to(BUYER)
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA Mail seq", "agent": self.agent.name,
			"steps": [{"step": 1}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": self.agent.name, "sequence": seq.name,
			"thread": msg.thread}).insert()
		identity = msg.omni_identity
		mail.unsubscribe(i=identity, t="wrong")
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "email_opt_out"), 0, "a wrong signature does nothing")
		mail.unsubscribe(i=identity, t=mail._token(identity))
		self.assertEqual(frappe.db.get_value("Omni Identity", identity, "email_opt_out"), 1)
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", enr.name, "status"), "Opted out")
		if frappe.db.table_exists("Excom Consent"):
			self.assertTrue(frappe.db.exists("Excom Consent", {"omni_identity": identity, "action": "Withdrawn", "channel": "email"}))

	# ---- bounces

	def test_reading_delivery_reports(self):
		hard = bounces.parse(dsn())
		self.assertEqual((hard["recipient"], hard["status_code"], hard["kind"], hard["message_ids"]),
			(BUYER, "5.1.1", "Hard", ["<qa-orig@qa-mail.example>"]))
		soft = bounces.parse("Delivery to the following recipient has been delayed:\n\n  x@qa-buyer.example\n\n"
			"Status: 4.2.2 mailbox full", OURS)
		self.assertEqual((soft["recipient"], soft["kind"]), ("x@qa-buyer.example", "Soft"))
		outlook = bounces.parse("Your message to y@qa-buyer.example couldn't be delivered. y wasn't found at "
			"qa-buyer.example. Remote server returned '550 5.1.10 RESOLVER.ADR.RecipientNotFound; Recipient not found'", OURS)
		self.assertEqual((outlook["recipient"], outlook["kind"]), ("y@qa-buyer.example", "Hard"))
		self.assertTrue(bounces.is_bounce("MAILER-DAEMON@mx.example", "anything"))
		self.assertTrue(bounces.is_bounce("someone@x.example", "Undeliverable: Water guns"))
		self.assertFalse(bounces.is_bounce("buyer@x.example", "Re: Water guns"))

	def test_a_bounce_is_kept_apart_and_stops_the_sequence(self):
		msg = self.sent_to(BUYER)
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA Mail Lead", "email_id": BUYER}).insert(ignore_permissions=True)
		seq = frappe.get_doc({"doctype": "Excom AI Sequence", "title": "QA Mail seq 2", "agent": self.agent.name,
			"steps": [{"step": 1}]}).insert()
		enr = frappe.get_doc({"doctype": "Excom AI Enrollment", "agent": self.agent.name, "sequence": seq.name,
			"lead": lead.name}).insert()
		frappe.db.commit()
		identities = frappe.db.count("Omni Identity")
		threads = frappe.db.count("Excom Thread")
		self.server.fetch_full = lambda account, mid: {"body_text": dsn(), "body_html": "", "headers": {}, "attachments": []}
		name, meta = self.receive("Mail Delivery System <MAILER-DAEMON@mx.qa-mail.example>",
			"Undelivered Mail Returned to Sender")
		self.assertIsNone(name, "not a message in anyone's conversation")
		self.assertEqual((frappe.db.count("Omni Identity"), frappe.db.count("Excom Thread")), (identities, threads),
			"no person and no conversation for a bounce")
		b = frappe.get_doc("Excom Mail Bounce", {"provider_message_id": P.store_id(self.acc, meta["id"])})
		self.assertEqual((b.recipient, b.kind, b.original_message, b.thread), (BUYER, "Hard", msg.name, msg.thread))
		self.assertTrue(mailbox.Sink(self.acc).known(b.provider_message_id), "the next sync skips it")
		self.assertEqual(frappe.db.get_value("Lead", lead.name, "ai_email_status"), "Invalid")
		self.assertEqual(frappe.db.get_value("Excom AI Enrollment", enr.name, "status"), "Bounced")

	def test_a_mailbox_that_bounces_too_much_pauses(self):
		for n in range(20):
			self.sent_to(f"b{n}@qa-buyer.example", internet_id=f"<qa-o{n}@qa-mail.example>")
		for n in range(2):
			frappe.get_doc({"doctype": "Excom Mail Bounce", "account": self.acc.name, "kind": "Hard",
				"provider_message_id": f"qa-b-{n}-{time.time()}", "received_on": frappe.utils.now_datetime(),
				"recipient": f"b{n}@qa-buyer.example"}).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.db.set_value("Excom AI Channel", {"parent": self.agent.name}, {"paused": 0, "pause_reason": None}))
		self.assertTrue(mail.check_bounce_rate(self.acc.name))
		row = frappe.get_doc("Excom AI Channel", {"parent": self.agent.name})
		self.assertEqual(row.paused, 1)
		self.assertIn("hard bounces", row.pause_reason)
		self.assertEqual(mail.allowance(row), 0, "a paused mailbox sends nothing")

	# ---- photo cards and pacing

	def test_photo_cards(self):
		with_photo = frappe.db.get_value("Item", {"image": ["is", "set"], "disabled": 0})
		without = frappe.db.get_value("Item", {"image": ["is", "not set"], "disabled": 0})
		html = mail.photo_cards([x for x in (without, with_photo) if x])
		if with_photo:
			self.assertIn("<img", html)
			self.assertEqual(html.count("<img"), 1, "items without a photo are left out")
		self.assertEqual(mail.photo_cards([without] if without else []), "")

	def test_warm_up_caps_and_paced_sends(self):
		row = frappe._dict(daily_cap=30, warmup_start=nowdate(), paused=0)
		self.assertEqual(mail.allowance(row), 5)
		row.warmup_start = add_days(nowdate(), -3)
		self.assertEqual(mail.allowance(row), 14)
		row.warmup_start = add_days(nowdate(), -40)
		self.assertEqual(mail.allowance(row), 30, "up to the cap")
		self.assertEqual(mail.allowance(frappe._dict(daily_cap=30)), 0, "not warming up: no cold mail")
		agent = frappe.get_doc("Excom AI Agent", self.agent.name)
		self.sent_to("pace@qa-buyer.example", internet_id="<qa-pace@qa-mail.example>")
		self.assertGreaterEqual(mail.sent_today(self.acc.name), 1, "the agent's send counts")
		self.assertEqual(mail.room(agent, self.acc.name),
			max(0, mail.allowance(agent.channels[0]) - mail.sent_today(self.acc.name)))
		slept, calls = [], []

		def boom():
			raise RuntimeError("smtp down")

		out = mail.send_paced([boom] + [lambda n=n: calls.append(n) or n for n in range(11)], sleep=slept.append)
		self.assertEqual(len(out), 8, "at most 8 a run")
		self.assertIsInstance(out[0], RuntimeError, "one failure does not stop the rest")
		self.assertEqual(len(slept), 7)
		self.assertTrue(all(4 <= s <= 15 for s in slept))

	# ---- health

	def test_mailbox_health(self):
		answers = {
			("qa-mail.example", "TXT"): ["v=spf1 include:mx.qa-mail.example -all"],
			("_dmarc.qa-mail.example", "TXT"): ["v=DMARC1; p=quarantine"],
			("qa-mail.example", "MX"): ["10 mx.qa-mail.example."],
			("stalwart._domainkey.qa-mail.example", "TXT"): ["v=DKIM1; k=rsa; p=MIIBIjAN"],
			("qa-mail.example.dbl.spamhaus.org", "A"): [],
		}
		fake = lambda name, rtype="TXT": answers.get((name, rtype), [])  # noqa: E731
		with patch.object(mail, "dns_txt", side_effect=fake):
			h = mail.check_domain("qa-mail.example")
			self.assertTrue(h["ok"])
			self.assertEqual(h["dkim_selector"], "stalwart")
			answers[("qa-mail.example.dbl.spamhaus.org", "A")] = ["127.255.255.254"]
			self.assertIsNone(mail.check_domain("qa-mail.example")["blocklisted"], "a refused lookup is unknown, not clean")
			answers[("qa-mail.example.dbl.spamhaus.org", "A")] = ["127.0.1.2"]
			self.assertTrue(mail.check_domain("qa-mail.example")["blocklisted"])
			answers[("qa-mail.example.dbl.spamhaus.org", "A")] = []
			del answers[("stalwart._domainkey.qa-mail.example", "TXT")]
			self.assertFalse(mail.check_domain("qa-mail.example")["ok"], "no DKIM")
		with patch.object(mail, "dns_txt", side_effect=OSError("offline")):
			self.assertFalse(mail.check_domain("qa-mail.example")["ok"])

	def test_readiness_reads_the_mailbox_health(self):
		agent = frappe.get_doc("Excom AI Agent", self.agent.name)
		ok, detail = readiness.mailbox(agent, {})
		self.assertFalse(ok)
		self.assertIn("14 days", detail)
		row = agent.channels[0]
		frappe.db.set_value("Excom AI Channel", row.name, {"warmup_start": add_days(nowdate(), -20),
			"health": frappe.as_json({"spf": "v=spf1", "dkim": "p=x", "dmarc": None, "mx": ["10 mx"]})})
		self.addCleanup(frappe.db.set_value, "Excom AI Channel", row.name, {"warmup_start": add_days(nowdate(), -3), "health": None})
		agent.reload()
		ok, detail = readiness.mailbox(agent, {})
		self.assertFalse(ok)
		self.assertIn("DMARC", detail)
		frappe.db.set_value("Excom AI Channel", row.name, "health", frappe.as_json({"spf": "v", "dkim": "p", "dmarc": "v", "mx": ["m"]}))
		agent.reload()
		self.assertTrue(readiness.mailbox(agent, {})[0])
