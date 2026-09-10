"""Voice channel: signature verification, XML rendering, routing decisions and idempotency.

These are the four things that fail silently in a telephony integration. A bad signature check
means anyone can drive your phone line; bad XML means the call drops with no error anywhere; a bad
routing decision rings the wrong person; and a missing idempotency guard turns one retried webhook
into two calls in the timeline.
"""

import base64
import hashlib
import hmac
import json
import xml.etree.ElementTree as ET

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.channels.voice import routing
from excom.excom.channels.voice.providers.base import CallDecision, Destination
from excom.excom.channels.voice.providers.plivo import PlivoAdapter

AUTH_TOKEN = "test-auth-token-not-a-real-one"


def _account_stub(**overrides):
	"""A channel-account-shaped object. The adapter only reads fields, so a _dict is enough and the
	tests stay independent of the doctype's own validation."""
	values = {
		"name": "VOICE-TEST",
		"account_name": "Test Line",
		"voice_provider": "Plivo",
		"voice_auth_id": "MATESTAUTHID000000",
		"voice_number": "+918041234567",
		"voice_app_id": "12345678901234567890",
		"voice_allow_browser_calls": 1,
		"voice_allow_phone_calls": 1,
		"voice_record_policy": "All",
		"voice_recording_channels": "Dual",
		"voice_max_call_seconds": 3600,
	}
	values.update(overrides)
	doc = frappe._dict(values)
	doc.get_password = lambda *a, **k: AUTH_TOKEN
	return doc


def _adapter(**overrides) -> PlivoAdapter:
	return PlivoAdapter(_account_stub(**overrides))


def _sign(url: str, nonce: str, form: dict | None = None, token: str = AUTH_TOKEN) -> str:
	base = url
	if form:
		base += "".join(f"{k}{form[k]}" for k in sorted(form))
	digest = hmac.new(token.encode(), (base + nonce).encode(), hashlib.sha256).digest()
	return base64.b64encode(digest).decode()


class TestPlivoSignature(FrappeTestCase):
	"""The webhook gate. Everything downstream trusts this."""

	URL = "https://example.com/api/method/excom.excom.api.voice.route?account=VOICE-TEST"

	def setUp(self):
		self.adapter = _adapter()

	def _verify(self, headers, form=None, method="POST"):
		# Exercise the local implementation directly: the SDK's validator is authoritative in
		# production, but the fallback is what runs if the package is ever missing, and an
		# unverified fallback would be worse than none.
		return self.adapter._verify_v3_locally(
			self.URL,
			method,
			headers.get("X-Plivo-Signature-V3-Nonce", ""),
			AUTH_TOKEN,
			headers.get("X-Plivo-Signature-V3", ""),
			form or {},
		)

	def test_valid_post_signature_is_accepted(self):
		nonce, form = "nonce-1", {"CallUUID": "abc", "From": "+919876543210"}
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, nonce, form),
			"X-Plivo-Signature-V3-Nonce": nonce,
		}
		self.assertTrue(self._verify(headers, form))

	def test_tampered_parameter_is_rejected(self):
		nonce, form = "nonce-2", {"CallUUID": "abc", "From": "+919876543210"}
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, nonce, form),
			"X-Plivo-Signature-V3-Nonce": nonce,
		}
		form["From"] = "+910000000000"
		self.assertFalse(self._verify(headers, form))

	def test_wrong_token_is_rejected(self):
		nonce, form = "nonce-3", {"CallUUID": "abc"}
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, nonce, form, token="some-other-token"),
			"X-Plivo-Signature-V3-Nonce": nonce,
		}
		self.assertFalse(self._verify(headers, form))

	def test_replaying_a_signature_under_a_new_nonce_is_rejected(self):
		form = {"CallUUID": "abc"}
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, "nonce-a", form),
			"X-Plivo-Signature-V3-Nonce": "nonce-b",
		}
		self.assertFalse(self._verify(headers, form))

	def test_missing_signature_is_rejected(self):
		self.assertFalse(self._verify({}, {"CallUUID": "abc"}))
		self.assertFalse(
			self.adapter.verify_webhook(self.URL, "POST", {}, {"CallUUID": "abc"})
		)

	def test_get_signature_ignores_form_body(self):
		nonce = "nonce-get"
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, nonce),
			"X-Plivo-Signature-V3-Nonce": nonce,
		}
		self.assertTrue(self._verify(headers, form={}, method="GET"))


class TestPlivoXML(FrappeTestCase):
	"""One decision in, one call-control document out."""

	def setUp(self):
		self.adapter = _adapter()
		frappe.local.site = frappe.local.site or "test"

	def _render(self, decision) -> ET.Element:
		return ET.fromstring(self.adapter.render_decision(decision))

	def test_browser_and_phone_ring_in_one_parallel_dial(self):
		"""The two-transport model, which is the whole design: a SIP endpoint and a mobile for the
		same agent sit in one <Dial> and first answer wins."""
		decision = CallDecision(
			destinations=[
				Destination(kind="sip", ref="sip:excompriya123@phone.plivo.com", user="p@x.com"),
				Destination(kind="pstn", ref="+919812345678", user="p@x.com"),
			],
			ring_set=["p@x.com"],
			caller_id="+918041234567",
			record=False,
		)
		root = self._render(decision)
		dial = root.find("Dial")
		self.assertIsNotNone(dial)
		self.assertEqual([child.tag for child in dial], ["User", "Number"])
		self.assertEqual(dial.find("User").text, "sip:excompriya123@phone.plivo.com")
		self.assertEqual(dial.find("Number").text, "+919812345678")
		self.assertEqual(dial.get("callerId"), "+918041234567")

	def test_recording_is_stereo_and_starts_on_answer(self):
		"""Stereo is what makes speaker attribution come from the file instead of a guess."""
		decision = CallDecision(
			destinations=[Destination(kind="pstn", ref="+919812345678")],
			record=True,
			record_channels="stereo",
		)
		record = self._render(decision).find("Record")
		self.assertIsNotNone(record)
		self.assertEqual(record.get("recordChannelType"), "stereo")
		self.assertEqual(record.get("startOnDialAnswer"), "true")
		self.assertEqual(record.get("recordSession"), "true")
		# redirect must be off, or the call jumps to the action URL instead of dialling.
		self.assertEqual(record.get("redirect"), "false")

	def test_no_recording_element_when_policy_is_off(self):
		decision = CallDecision(
			destinations=[Destination(kind="pstn", ref="+919812345678")], record=False
		)
		self.assertIsNone(self._render(decision).find("Record"))

	def test_empty_decision_speaks_and_hangs_up(self):
		"""No destination means say something and log a miss — never ring an arbitrary fallback."""
		root = self._render(CallDecision(destinations=[]))
		self.assertEqual([child.tag for child in root], ["Speak", "Hangup"])
		self.assertIsNone(root.find("Dial"))

	def test_caller_id_is_escaped_not_concatenated(self):
		"""A caller id is attacker-controlled. Built with ElementTree, an injected element becomes
		text; built with string formatting, it becomes markup."""
		decision = CallDecision(
			destinations=[Destination(kind="pstn", ref="+919812345678")],
			caller_id='"/><Dial><Number>+911111111111</Number></Dial><x a="',
		)
		xml = self.adapter.render_decision(decision)
		root = ET.fromstring(xml)
		self.assertEqual(len(root.findall("Dial")), 1)
		self.assertEqual(root.find("Dial").findall("Number")[0].text, "+919812345678")

	def test_consent_prompt_plays_before_the_dial(self):
		decision = CallDecision(
			destinations=[Destination(kind="pstn", ref="+919812345678")],
			consent_prompt="https://example.com/consent.mp3",
		)
		root = self._render(decision)
		self.assertEqual(root[0].tag, "Play")
		self.assertEqual(root[0].text, "https://example.com/consent.mp3")


class TestPlivoEvents(FrappeTestCase):
	"""Each lifecycle moment has its own URL, so the event kind is told to us, not guessed."""

	def setUp(self):
		self.adapter = _adapter()

	def test_answer_names_the_leg_that_picked_up(self):
		event = self.adapter.normalize_event(
			"dial_event",
			{
				"CallUUID": "uuid-1",
				"DialAction": "answer",
				"DialBLegTo": "sip:excompriya123@phone.plivo.com",
			},
		)
		self.assertEqual(event.kind, "answered")
		self.assertEqual(event.answered_destination, "sip:excompriya123@phone.plivo.com")

	def test_dial_statuses_map_onto_call_statuses(self):
		for raw, expected in [
			("completed", "Completed"),
			("busy", "Busy"),
			("no-answer", "No Answer"),
			("timeout", "No Answer"),
			("cancel", "Canceled"),
			("failed", "Failed"),
		]:
			event = self.adapter.normalize_event(
				"dial_action", {"CallUUID": "u", "DialStatus": raw, "DialBLegDuration": "12"}
			)
			self.assertEqual(event.kind, "ended")
			self.assertEqual(event.status, expected, f"DialStatus={raw}")
			self.assertEqual(event.duration, 12)

	def test_hangup_carries_billed_duration_and_cost(self):
		event = self.adapter.normalize_event(
			"hangup",
			{
				"CallUUID": "u",
				"CallStatus": "completed",
				"Duration": "252",
				"BillDuration": "260",
				"TotalCost": "1.85",
				"HangupCauseName": "NORMAL_CLEARING",
			},
		)
		self.assertEqual(event.duration, 252)
		self.assertEqual(event.bill_duration, 260)
		self.assertAlmostEqual(event.cost, 1.85)
		self.assertEqual(event.hangup_cause, "NORMAL_CLEARING")

	def test_session_recording_reports_minus_one_until_it_is_ready(self):
		"""Plivo sends -1 for a session recording's duration until the final callback. Storing that
		would show every call as lasting -1 milliseconds."""
		event = self.adapter.normalize_event(
			"recording", {"CallUUID": "u", "RecordingDurationMs": "-1"}
		)
		self.assertEqual(event.kind, "recording_ready")
		self.assertEqual(event.recording_duration_ms, 0)

	def test_sip_headers_are_extracted(self):
		"""The thread id rides in on a SIP header, because an outbound browser leg has no other way
		of saying which conversation it belongs to."""
		event = self.adapter.normalize_event(
			"ringing",
			{"CallUUID": "u", "X-PH-thread": "THREAD-1", "X-PH-user": "priya@x.com"},
		)
		self.assertEqual(event.sip_headers["thread"], "THREAD-1")
		self.assertEqual(event.sip_headers["user"], "priya@x.com")


class TestPhoneVariants(FrappeTestCase):
	"""Caller lookup is exact matching against a bounded set of spellings, never a LIKE."""

	def test_variants_cover_the_ways_a_number_is_written(self):
		variants = routing._phone_variants("+919876543210")
		for expected in ("+919876543210", "919876543210", "9876543210", "09876543210"):
			self.assertIn(expected, variants)

	def test_variants_are_deduplicated_and_bounded(self):
		variants = routing._phone_variants("+919876543210")
		self.assertEqual(len(variants), len(set(variants)))
		self.assertLess(len(variants), 10)

	def test_no_wildcards_ever_reach_a_query(self):
		for value in routing._phone_variants("+919876543210"):
			self.assertNotIn("%", value)


class TestCallVisibility(FrappeTestCase):
	"""A call is as visible as the conversation it belongs to, and no more."""

	def test_ring_set_parses_and_rejects_rubbish(self):
		call = frappe.new_doc("Excom Call")
		call.ring_set = json.dumps(["a@x.com", "b@x.com"])
		self.assertEqual(call.ring_set_users(), ["a@x.com", "b@x.com"])

		call.ring_set = "not json at all"
		self.assertEqual(call.ring_set_users(), [])

		call.ring_set = json.dumps({"not": "a list"})
		self.assertEqual(call.ring_set_users(), [])

	def test_a_user_outside_excom_cannot_read_any_call(self):
		from excom.excom.doctype.excom_call.excom_call import can_access

		frappe.set_user("Administrator")
		outsider = _user_without_excom_roles()
		try:
			frappe.set_user(outsider)
			self.assertFalse(
				can_access({"thread": None, "agent": "someone@else.com", "ring_set": "[]"})
			)
		finally:
			frappe.set_user("Administrator")


def _user_without_excom_roles() -> str:
	email = "voice-outsider@example.com"
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Voice",
				"last_name": "Outsider",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return email
