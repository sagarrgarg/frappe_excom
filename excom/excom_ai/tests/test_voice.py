"""Excom AI Phase 3: an agent takes the inbound calls nobody answers (HLD-007 §11.4).

The voice service on the VM is tested on its own (apps/excom/voice_service); here: when an agent
takes a call, the call-control documents for Twilio and Plivo, the handoff after an unanswered dial,
the three endpoints the voice service calls, and the call summary.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.channels.voice.providers.base import CallDecision, CallEvent, Destination
from excom.excom.channels.voice.providers.plivo import PlivoAdapter
from excom.excom.channels.voice.providers.twilio import TwilioAdapter
from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import voice
from excom.excom_ai.engine import ownership, runner

OWNER = "qa.voice.owner@example.com"
BACKUP = "qa.voice.backup@example.com"
TEAM = "QA Voice Desk"
LINE = "QA Voice Line"
PTYPE = "QA Voice Scripted"
PROVIDER = "QA Voice Provider"
URL = "wss://voice.qa.example/stream"
KEY = "qa-voice-key-123"
CALL_ID = "CA-QA-VOICE-"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Voice%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [OWNER, BACKUP]]})
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.delete("Excom Call", {"provider_call_id": ["like", f"{CALL_ID}%"]})
	for dt, name in (("Excom Channel Account", LINE), ("Excom Team", TEAM)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP)
	frappe.db.commit()


def _stub(provider):
	doc = frappe._dict(name=LINE, account_name=LINE, voice_provider=provider, voice_auth_id="ACQA000",
		voice_number="+15550001111")
	doc.get_password = lambda *a, **k: "secret"
	return doc


class TestVoice(FrappeTestCase):
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
		channel = "voice" if frappe.db.exists("Excom Channel", "voice") else frappe.db.get_value("Excom Channel", {})
		cls.line = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": LINE, "channel": channel,
			"status": "Active"}).insert(ignore_permissions=True)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}],
				api_base_url="main")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("Excom AI Settings")
		saved = {k: s.get(k) for k in ("enabled", "voice_service_url")}
		frappe.db.set_single_value("Excom AI Settings", {"enabled": 1, "voice_service_url": URL})
		s = frappe.get_single("Excom AI Settings")
		s.voice_service_key = KEY
		s.save(ignore_permissions=True)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.addCleanup(lambda: (frappe.db.set_single_value("Excom AI Settings", saved),
			frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")))
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, mode="Auto", status="Live", agent_type="Sales"):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Voice Rosa {self.n}", "agent_type": agent_type,
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM, "company": frappe.defaults.get_global_default("company"),
			"channels": [{"channel_account": self.line.name, "purpose": "Calls"}]}).insert(ignore_permissions=True)
		a.reload()
		row = next((p for p in a.policies if p.action == "voice_call"), None) or a.append("policies", {"action": "voice_call"})
		row.mode = mode
		a.save(ignore_permissions=True)
		a.db_set({"status": status, "brief_approved_on": frappe.utils.now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		self.addCleanup(self.retire, a)
		return frappe.get_doc("Excom AI Agent", a.name)

	def retire(self, agent):
		agent.db_set("status", "Retired")
		frappe.db.commit()

	# ---- who takes the call

	def test_only_a_live_agent_with_voice_on_takes_calls(self):
		off = self.agent(mode="Off")
		self.assertIsNone(voice.offer(LINE, f"{CALL_ID}{self.n}a", "+5215550001"), "policy Off")
		self.retire(off)
		shadow = self.agent(status="Shadow")
		self.assertIsNone(voice.offer(LINE, f"{CALL_ID}{self.n}b", "+5215550001"), "Shadow takes no calls")
		self.retire(shadow)
		live = self.agent()
		url, token = voice.offer(LINE, f"{CALL_ID}{self.n}c", "+5215550001")
		self.assertEqual(url, URL)
		self.assertEqual(voice.offered(f"{CALL_ID}{self.n}c"), token)
		frappe.db.set_single_value("Excom AI Settings", "voice_service_url", "")
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.assertIsNone(voice.offer(LINE, f"{CALL_ID}{self.n}d", "+5215550001"), "no voice service, no calls")
		self.retire(live)

	def test_a_custom_agent_never_takes_calls(self):
		from excom.excom_ai.constants import ceiling

		self.assertEqual(ceiling("voice_call", "custom"), "Review")
		self.assertEqual(ceiling("voice_call", "sales"), "Auto")

	# ---- call control

	def test_twilio_and_plivo_documents(self):
		ai = CallDecision(ai_stream=URL, ai_token="tok123")
		twiml = TwilioAdapter(_stub("Twilio")).render_decision(ai)
		self.assertIn("<Connect><Stream", twiml)
		self.assertIn(f'url="{URL}"', twiml)
		self.assertIn('<Parameter name="token" value="tok123"', twiml)
		people = CallDecision(destinations=[Destination(kind="pstn", ref="+15550002222", user=OWNER)], ai_stream=URL,
			ai_token="tok123")
		self.assertIn("<Dial", TwilioAdapter(_stub("Twilio")).render_decision(people), "people are rung first")
		self.assertNotIn("<Stream", TwilioAdapter(_stub("Twilio")).render_decision(people))

		plivo = PlivoAdapter(_stub("Plivo"))
		xml = plivo.render_decision(ai)
		self.assertIn('bidirectional="true"', xml)
		self.assertIn('extraHeaders="token=tok123"', xml)
		self.assertIn(f"{URL}?provider=plivo</Stream>", xml)
		dial = plivo.render_decision(people)
		self.assertIn('redirect="true"', dial, "the dial action's reply is run, to hand over when nobody answers")
		self.assertIn('redirect="false"', plivo.render_decision(CallDecision(destinations=people.destinations)))
		self.assertIn("<Hangup", plivo.end_document())

	def test_an_unanswered_dial_goes_to_the_agent(self):
		agent = self.agent()
		call_id = f"{CALL_ID}{self.n}"
		voice.offer(LINE, call_id, "+5215550001")
		twilio = TwilioAdapter(_stub("Twilio"))
		self.assertIn("<Stream", voice.after_dial(twilio, CallEvent(kind="ended", provider_call_id=call_id, status="No Answer")))
		self.assertIsNone(voice.after_dial(twilio, CallEvent(kind="ended", provider_call_id=call_id, status="Completed")),
			"someone answered")
		self.assertIsNone(voice.after_dial(twilio, CallEvent(kind="ended", provider_call_id="other", status="No Answer")))
		self.retire(agent)

	# ---- the voice service's endpoints

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def test_a_call_from_greeting_to_summary(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		agent = self.agent()
		call_id = f"{CALL_ID}{self.n}"
		call = frappe.get_doc({"doctype": "Excom Call", "direction": "Inbound", "status": "No Answer",
			"provider_call_id": call_id, "channel_account": LINE, "customer_number": "+5215550001"}).insert(
			ignore_permissions=True).name
		_url, token = voice.offer(LINE, call_id, "+5215550001")
		frappe.set_user("Guest")
		try:
			with patch("frappe.get_request_header", return_value="wrong"):
				self.assertRaises(frappe.PermissionError, voice.start, token)
			with patch("frappe.get_request_header", return_value=KEY):
				out = voice.start(token)
				self.assertTrue(out["ok"])
				self.assertRaises(frappe.PermissionError, voice.start, token)  # a token works once
				self.assertIn(agent.agent_name.split()[0], out["greeting"])
				with self.scripted({"say": "We are open nine to six.", "intent": "question", "needs_person": False,
						"end_call": False}):
					reply = voice.turn(token, "What are your opening hours?")
				self.assertEqual(reply, {"say": "We are open nine to six.", "end_call": False})
				reply = voice.turn(token, "Am I talking to a real person?")
				self.assertTrue(reply["end_call"], "asked if AI: a colleague calls back")
				self.assertNotIn("AI", reply["say"])
				voice.end(token, json.dumps([{"speaker": "caller", "text": "What are your opening hours?", "at_ms": 1000}]), 42,
					"agent_end")
				self.assertRaises(frappe.DoesNotExistError, voice.turn, token, "hello?")
				self.assertEqual(voice.end(token, "[]", 42, "agent_end"), {"ok": True, "already": True}, "a retried end")
				self.assertRaises(frappe.DoesNotExistError, voice.start, token)
		finally:
			frappe.set_user("Administrator")
		row = frappe.db.get_value("Excom Call", call, ["status", "transcript_status", "answered_by", "talk_time", "transcript"],
			as_dict=True)
		self.assertEqual((row.status, row.transcript_status, row.answered_by, row.talk_time),
			("Completed", "Ready", agent.service_user, 42))
		self.assertIn("opening hours", row.transcript)
		self.assertEqual(frappe.db.get_value("Excom AI Task", {"agent": agent.name, "kind": "voice_call"}, "status"), "Done")
		task = frappe.db.get_value("Excom AI Task", {"agent": agent.name, "kind": "call_summary"})
		frappe.db.set_value("Excom AI Task", task, "status", "Running")
		frappe.db.commit()
		with self.scripted({"summary": "A buyer asked our hours, then asked for a person.", "sentiment": "Neutral",
				"next_action": "Call them back.", "callback_needed": True}):
			runner.run_task(task)
		self.assertEqual(frappe.db.get_value("Excom AI Task", task, "status"), "Done",
			frappe.db.get_value("Excom AI Task", task, ["outcome", "error"]))
		self.assertEqual(frappe.db.get_value("Excom Call", call, ["summary_status", "sentiment", "next_action"]),
			("Ready", "Neutral", "Call them back."))
		todo = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "create_todo"}, ["payload"])
		self.assertEqual(len(todo), 1)
		self.assertIn("+5215550001", json.loads(todo[0].payload)["text"])
		self.retire(agent)
