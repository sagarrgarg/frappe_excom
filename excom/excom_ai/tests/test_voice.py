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
KEY = "qa-voice-key-0123456789abcdef-0123"  # at least voice.MIN_KEY_LENGTH
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
		saved = {k: s.get(k) for k in ("enabled", "voice_service_url", "voice_recording_notice", "voice_max_seconds")}
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

	# ---- review fixes (2026-11-14)

	def started(self, agent=None, call_id=None):
		"""A call offered and started through the endpoints, as the voice service would."""
		agent = agent or self.agent()
		call_id = call_id or f"{CALL_ID}{self.n}"
		_url, token = voice.offer(LINE, call_id, "+5215550009")
		with patch("frappe.get_request_header", return_value=KEY):
			out = voice.start(token)
		return agent, call_id, token, out

	def summary_task(self, agent):
		return frappe.get_doc("Excom AI Task", {"agent": agent.name, "kind": "call_summary"})

	def test_the_key_is_compared_as_bytes_and_must_be_long(self):
		for sent in ("ключ-не-тот", "qa-voice-key-é" * 3, ""):
			with patch("frappe.get_request_header", return_value=sent):
				self.assertRaises(frappe.PermissionError, voice._verify_voice_key)  # 403, never a TypeError (500)
		with patch("frappe.get_request_header", return_value=KEY):
			voice._verify_voice_key()
		s = frappe.get_single("Excom AI Settings")
		s.voice_service_key = "short-key"
		self.assertRaises(frappe.ValidationError, s.save, ignore_permissions=True)
		s.reload()
		s.voice_max_seconds = 1800
		self.assertRaises(frappe.ValidationError, s.save, ignore_permissions=True)
		with patch.object(voice, "_voice_key", return_value="short-key"):
			self.assertFalse(voice.voice_ready(), "a short key: agents take no calls")
			with patch("frappe.get_request_header", return_value="short-key"):
				self.assertRaises(frappe.PermissionError, voice._verify_voice_key)

	def test_the_stream_address_ends_with_stream(self):
		for url in ("wss://voice.qa.example", "wss://voice.qa.example/", "wss://voice.qa.example/stream"):
			frappe.db.set_single_value("Excom AI Settings", "voice_service_url", url)
			frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
			self.assertEqual(voice.stream_url(), "wss://voice.qa.example/stream")

	def test_spoken_identity_questions_go_to_a_person(self):
		for text in ("am I talking to a computer", "Is it an AI?", "are you a bot", "I want a real person",
				"can I speak to a human please", "is this a recording", "you're a robot", "Is this a real person",
				"¿Estoy hablando con una máquina?", "quiero hablar con una persona", "क्या आप इंसान हैं",
				"machine ho kya", "Am I talking to a real person?"):
			self.assertTrue(voice.asks_for_person(text), text)
		for text in ("Do you sell robot toys?", "What are your opening hours?", "Is it available in red?",
				"are you really open on Sunday", "we are a real estate agency", "Is it a machine washable fabric?"):
			self.assertFalse(voice.asks_for_person(text), text)

	def test_a_declined_call_still_gets_its_callback(self):
		agent = self.agent()
		call_id = f"{CALL_ID}{self.n}"
		_url, token = voice.offer(LINE, call_id, "+5215550009")
		self.retire(agent)  # the agent can no longer work when the call connects
		with patch("frappe.get_request_header", return_value=KEY):
			out = voice.start(token)
			self.assertFalse(out["ok"])
			self.assertIn("call you back", out["say"])
			self.assertEqual(voice.end(token, "[]", 0, "agent_end"), {"ok": True})
		task = self.summary_task(agent)
		params = json.loads(task.params)
		self.assertTrue(params["needs_person"])
		self.assertEqual(params["reason"], "declined")
		self.assertEqual(voice.call_summary(task, agent)[:8], "a callba", "no transcript, still a callback")
		todo = frappe.get_all("Excom AI Proposal", {"agent": agent.name, "action": "create_todo"}, ["payload"])
		self.assertEqual(len(todo), 1)
		self.assertIn("+5215550009", json.loads(todo[0].payload)["text"])

	def test_a_lost_end_report_is_recovered_with_a_callback(self):
		agent, call_id, token, out = self.started()
		self.assertTrue(out["ok"])
		vc = frappe.get_doc("Excom AI Task", {"agent": agent.name, "kind": "voice_call"})
		# The engine reaped it (no end came): the task kind closes the call from its state.
		self.assertIn("without a report", voice.voice_call(vc, agent))
		self.assertIsNone(voice.offered(call_id))
		with patch("frappe.get_request_header", return_value=KEY):
			self.assertEqual(voice.end(token, "[]", 5, "caller_hangup"), {"ok": True, "already": True},
				"a late end report is acknowledged, not applied twice")
		params = json.loads(self.summary_task(agent).params)
		self.assertEqual((params["needs_person"], params["reason"]), (True, "no_end_report"))
		self.assertNotIn("transcript", params, "the transcript is not copied into task params")
		# The state expired too: still a callback from what the task knows.
		frappe.db.delete("Excom AI Task", {"agent": agent.name, "kind": "call_summary"})
		voice.voice_call(vc, agent)
		params = json.loads(self.summary_task(agent).params)
		self.assertEqual((params["needs_person"], params["caller"]), (True, "+5215550009"))

	def test_a_turn_that_outlives_the_call_does_not_bring_it_back(self):
		agent, call_id, token, _out = self.started()

		def slow_model(*args, **kwargs):  # the call ends while the model thinks
			voice.end(token, "[]", 7, "caller_hangup")
			return frappe._dict(data={"say": "We open at nine.", "intent": "question", "needs_person": False,
				"end_call": False})

		with patch("frappe.get_request_header", return_value=KEY), patch.object(runner, "ai", side_effect=slow_model):
			self.assertEqual(voice.turn(token, "When do you open?")["say"], "We open at nine.")
			self.assertIsNone(voice._live_state(token), "the ended call's state is not saved again")
			self.assertRaises(frappe.DoesNotExistError, voice.turn, token, "hello?")
			self.assertEqual(voice.end(token, "[]", 7, "caller_hangup"), {"ok": True, "already": True})
		self.assertEqual(frappe.db.count("Excom AI Task", {"agent": agent.name, "kind": "call_summary"}), 1)

	def test_turns_past_the_longest_call_are_refused(self):
		agent, call_id, token, out = self.started()
		self.assertEqual(out["max_seconds"], voice.DEFAULT_MAX_SECONDS)
		state = voice._live_state(token)
		state["started"] = str(frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=-(out["max_seconds"] + 60)))
		voice._save(token, state)
		with patch("frappe.get_request_header", return_value=KEY), patch.object(runner, "ai") as model:
			reply = voice.turn(token, "One more thing")
		model.assert_not_called()
		self.assertTrue(reply["end_call"])
		self.assertEqual(voice._live_state(token)["reason"], "max_seconds")

	def test_a_turn_before_start_is_refused(self):
		self.agent()
		_url, token = voice.offer(LINE, f"{CALL_ID}{self.n}", "+5215550009")
		with patch("frappe.get_request_header", return_value=KEY):
			self.assertRaises(frappe.PermissionError, voice.turn, token, "hello")

	def test_the_service_promised_a_colleague(self):
		agent, _call_id, token, _out = self.started()
		with patch("frappe.get_request_header", return_value=KEY):
			voice.end(token, "[]", 600, "max_seconds")
		self.assertTrue(json.loads(self.summary_task(agent).params)["needs_person"])

	def test_recording_notice_and_english_fallback(self):
		frappe.db.set_single_value("Excom AI Settings", "voice_recording_notice", "This call may be recorded.")
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		agent = self.agent()
		with patch.object(voice, "language_of", return_value="hi"):
			_agent, _call_id, _token, out = self.started(agent)
		self.assertTrue(out["greeting"].endswith("This call may be recorded."))
		self.assertEqual((out["language"], out["reply_language"]), ("hi", "en"), "no Hindi voice: the agent speaks English")
		self.assertEqual(voice.speaks(frappe._dict(voice="custom-hi"), "hi"), "hi")
		self.assertEqual(voice.speaks(frappe._dict(), "es"), "es")
		frappe.db.set_single_value("Excom AI Settings", "voice_recording_notice", "")
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		_a, _c, _t, out = self.started(call_id=f"{CALL_ID}{self.n}x")
		self.assertTrue(out["greeting"].endswith("How can I help you?"), "no notice: the greeting as before")

	def test_a_handed_call_the_service_never_started_is_missed(self):
		agent = self.agent()
		call_id = f"{CALL_ID}{self.n}"
		_url, token = voice.offer(LINE, call_id, "+5215550009")
		self.assertFalse(voice.never_answered(call_id), "offered, not handed: people may still answer")
		from excom.excom.channels.voice.providers.base import CallEvent as Event

		voice.after_dial(TwilioAdapter(_stub("Twilio")), Event(kind="ended", provider_call_id=call_id, status="No Answer"))
		self.assertTrue(voice.never_answered(call_id), "handed to the agent, nothing started yet")
		with patch("frappe.get_request_header", return_value=KEY):
			voice.start(token)
		self.assertFalse(voice.never_answered(call_id), "the agent answered")
		self.retire(agent)
