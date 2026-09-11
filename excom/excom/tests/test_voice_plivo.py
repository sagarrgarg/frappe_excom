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

from excom.excom.channels.voice import presence, routing
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


def _sign(
	url: str, nonce: str, form: dict | None = None, token: str = AUTH_TOKEN, method: str = "POST"
) -> str:
	"""Sign a request the way Plivo does — using Plivo's own code.

	Deliberately not a hand-rolled signer. A test that signs with the same misreading as the code
	under test passes while every real webhook is rejected, which is exactly what happened: the
	published description omits that the query string is rebuilt sorted, separated from the body
	params by a dot, and joined to the nonce by another dot.
	"""
	from plivo.utils.signature_v3 import construct_get_url, construct_post_url, get_signature_v3

	params = dict(form or {})
	built = (
		construct_get_url(url, params).decode()
		if method.upper() == "GET"
		else construct_post_url(url, params).decode()
	)
	return get_signature_v3(token, built, nonce).decode().strip()


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

	def test_lowercase_headers_are_still_read(self):
		"""HTTP/2 requires lowercase header names.

		Behind a modern proxy the signature arrives as `x-plivo-signature-v3`, and an exact-case
		lookup returns nothing — so every genuine webhook was refused as unsigned while a test
		using the documented capitalisation passed.
		"""
		nonce, form = "nonce-lower", {"CallUUID": "abc"}
		signature = _sign(self.URL, nonce, form)
		for headers in (
			{"x-plivo-signature-v3": signature, "x-plivo-signature-v3-nonce": nonce},
			{"X-Plivo-Signature-V3": signature, "X-Plivo-Signature-V3-Nonce": nonce},
			{"X-PLIVO-SIGNATURE-V3": signature, "X-PLIVO-SIGNATURE-V3-NONCE": nonce},
			{"HTTP_X_PLIVO_SIGNATURE_V3": signature, "HTTP_X_PLIVO_SIGNATURE_V3_NONCE": nonce},
		):
			self.assertTrue(
				self.adapter.verify_webhook(self.URL, "POST", headers, form),
				f"rejected with headers spelled {list(headers)[0]!r}",
			)

	def test_missing_signature_is_rejected(self):
		self.assertFalse(self._verify({}, {"CallUUID": "abc"}))
		self.assertFalse(
			self.adapter.verify_webhook(self.URL, "POST", {}, {"CallUUID": "abc"})
		)

	def test_a_get_callback_is_accepted(self):
		nonce = "nonce-get"
		headers = {
			"X-Plivo-Signature-V3": _sign(self.URL, nonce, method="GET"),
			"X-Plivo-Signature-V3-Nonce": nonce,
		}
		self.assertTrue(self._verify(headers, form={}, method="GET"))

	def test_our_check_agrees_with_plivos_own_validator(self):
		"""The fence that matters. Ours is only a fallback, but a fallback that disagrees rejects
		every genuine webhook while looking like an attack in the log."""
		from plivo.utils.signature_v3 import validate_v3_signature

		cases = [
			("POST", {"CallUUID": "u-1", "Direction": "outbound", "To": "919250333699"}),
			("POST", {}),
			("POST", {"CallerName": "Somil Vaishya", "To": "+919250333699"}),
			("GET", {}),
			("GET", {"CallUUID": "u-3"}),
		]
		for method, form in cases:
			nonce = f"n-{method}-{len(form)}"
			signature = _sign(self.URL, nonce, form, method=method)
			theirs = validate_v3_signature(
				method, self.URL, nonce, AUTH_TOKEN, signature, dict(form)
			)
			ours = self.adapter._verify_v3_locally(
				self.URL, method, nonce, AUTH_TOKEN, signature, dict(form)
			)
			self.assertTrue(theirs, f"Plivo rejected its own signature for {method} {form}")
			self.assertEqual(ours, theirs, f"we disagree with Plivo on {method} {form}")


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


class TestPresence(FrappeTestCase):
	"""Presence has to survive a write-then-read.

	It did not: the writes used a raw `setex` on a hand-made key while the reads used `get_value`,
	which prefixes the key a second time and expects a pickle. Every read came back empty, so no
	agent ever entered a ring set and every inbound call answered "nobody is available". The
	symptom looked like a routing fault, which is why this is fenced off with a test.
	"""

	USER = "presence-test@example.com"
	ACCOUNT = "presence-test-account"

	def tearDown(self):
		presence.mark_unregistered(self.USER, self.ACCOUNT)
		presence.set_available(self.USER, False)
		presence.clear_busy(self.USER)

	def test_registration_round_trips(self):
		self.assertFalse(presence.is_registered(self.USER, self.ACCOUNT))
		presence.mark_registered(self.USER, self.ACCOUNT, "1.2.3.4")
		self.assertTrue(presence.is_registered(self.USER, self.ACCOUNT))
		presence.mark_unregistered(self.USER, self.ACCOUNT)
		self.assertFalse(presence.is_registered(self.USER, self.ACCOUNT))

	def test_availability_round_trips(self):
		self.assertFalse(presence.is_available(self.USER))
		presence.set_available(self.USER, True)
		self.assertTrue(presence.is_available(self.USER))
		presence.set_available(self.USER, False)
		self.assertFalse(presence.is_available(self.USER))

	def test_busy_round_trips(self):
		presence.mark_busy(self.USER, "CALL-1")
		self.assertTrue(presence.is_busy(self.USER))
		presence.clear_busy(self.USER)
		self.assertFalse(presence.is_busy(self.USER))

	def test_registration_alone_is_not_enough_to_ring(self):
		"""All three facts are required. A registered socket on an agent who has signed off, or who
		is already talking, must not be rung."""
		presence.mark_registered(self.USER, self.ACCOUNT)
		self.assertFalse(presence.can_ring_browser(self.USER, self.ACCOUNT))

		presence.set_available(self.USER, True)
		self.assertTrue(presence.can_ring_browser(self.USER, self.ACCOUNT))

		presence.mark_busy(self.USER, "CALL-1")
		self.assertFalse(presence.can_ring_browser(self.USER, self.ACCOUNT))

	def test_registration_is_per_line(self):
		"""An agent signed in on one line is not reachable on another."""
		presence.mark_registered(self.USER, self.ACCOUNT)
		self.assertFalse(presence.is_registered(self.USER, "some-other-line"))


class TestAliasSanitising(FrappeTestCase):
	"""Plivo rejects an endpoint alias containing anything but letters, numbers, - and _, with a
	400 whose message does not say which character was the problem."""

	def test_punctuation_and_spaces_are_stripped(self):
		from excom.excom.channels.voice.provisioning import _safe_alias

		alias = _safe_alias("Somil Vaishya", "Plivo Sales Line")
		self.assertRegex(alias, r"^[A-Za-z0-9_]+$")
		self.assertIn("Somil", alias)

	def test_an_email_shaped_name_is_still_valid(self):
		from excom.excom.channels.voice.provisioning import _safe_alias

		self.assertRegex(_safe_alias("rohit@gmail.com", "A Line"), r"^[A-Za-z0-9_]+$")

	def test_alias_stays_within_the_provider_limit(self):
		from excom.excom.channels.voice.provisioning import _safe_alias

		alias = _safe_alias("A Very Long Agent Name Indeed" * 4, "And A Very Long Line Name Too")
		self.assertLessEqual(len(alias), 60)
		self.assertRegex(alias, r"^[A-Za-z0-9_]+$")


class TestNumberNormalising(FrappeTestCase):
	"""A real address book is not in E.164.

	The first live outbound call refused with "09217025599 is not a valid phone number", because
	validation ran before conversion. Indian contacts are stored with the trunk 0, or bare, or with
	spaces — a provider takes none of those.
	"""

	def setUp(self):
		from excom.excom.channels.voice.outbound import to_e164

		self.convert = lambda raw: to_e164(raw, _account_stub())

	def test_the_number_that_broke_the_first_call(self):
		self.assertEqual(self.convert("09217025599"), "+919217025599")

	def test_the_shapes_a_contact_list_actually_holds(self):
		for raw in (
			"9217025599",
			"09217025599",
			"+919217025599",
			"919217025599",
			"092170 25599",
			"+91 92170-25599",
			"00919217025599",
		):
			self.assertEqual(self.convert(raw), "+919217025599", f"failed on {raw!r}")

	def test_double_zero_is_an_international_prefix_not_part_of_the_number(self):
		"""0092… is a dial-out to Pakistan, not an Indian number with stray zeros. Treating the 00
		as digits would silently call a different country."""
		self.assertEqual(self.convert("00929217025599"), "+929217025599")

	def test_an_international_number_keeps_its_own_country(self):
		self.assertEqual(self.convert("+14155550100"), "+14155550100")
		self.assertEqual(self.convert("0014155550100"), "+14155550100")

	def test_the_line_decides_what_local_means(self):
		from excom.excom.channels.voice.outbound import line_country_code, to_e164

		uk = _account_stub(voice_number="+442071838750")
		self.assertEqual(line_country_code(uk), "44")
		self.assertEqual(to_e164("2071838750", uk), "+442071838750")

	def test_rubbish_is_refused_rather_than_dialled(self):
		for raw in ("", "   ", "abc", "12"):
			with self.assertRaises(frappe.ValidationError):
				self.convert(raw)


class TestRingDestinations(FrappeTestCase):
	"""The phone is a fallback, not a second doorbell.

	Ringing a connected agent's browser and their personal mobile together meant their own phone
	went off for every call they were already sitting in front of. That is what the first day of
	real inbound traffic produced, and it is the complaint this fences off.
	"""

	AGENT = "ring-test@example.com"
	ACCOUNT = "ring-test-account"
	SIP = "sip:ringtest@phone.plivo.com"
	MOBILE = "+919000000001"

	def agent_row(self):
		return {"user": self.AGENT, "sip_uri": self.SIP, "mobile": self.MOBILE, "team": None}

	def tearDown(self):
		presence.mark_unregistered(self.AGENT, self.ACCOUNT)
		presence.set_available(self.AGENT, False)

	def kinds(self, ring_both=False):
		return [
			d.kind
			for d in routing._destinations_for(
				self.agent_row(), True, True, self.ACCOUNT, ring_both
			)
		]

	def test_a_connected_softphone_takes_the_call_alone(self):
		presence.mark_registered(self.AGENT, self.ACCOUNT)
		presence.set_available(self.AGENT, True)
		self.assertEqual(self.kinds(), ["sip"])

	def test_the_mobile_steps_in_when_the_browser_cannot(self):
		presence.set_available(self.AGENT, True)  # available, but never registered
		self.assertEqual(self.kinds(), ["pstn"])

	def test_a_line_may_ask_for_both(self):
		presence.mark_registered(self.AGENT, self.ACCOUNT)
		presence.set_available(self.AGENT, True)
		self.assertEqual(self.kinds(ring_both=True), ["sip", "pstn"])

	def test_signing_off_silences_both(self):
		presence.mark_registered(self.AGENT, self.ACCOUNT)
		self.assertEqual(self.kinds(), [])

	def test_a_call_in_progress_silences_both(self):
		presence.mark_registered(self.AGENT, self.ACCOUNT)
		presence.set_available(self.AGENT, True)
		presence.mark_busy(self.AGENT, "CALL-1")
		try:
			self.assertEqual(self.kinds(), [])
		finally:
			presence.clear_busy(self.AGENT)


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
