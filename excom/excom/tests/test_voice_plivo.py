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

from excom.excom.channels.voice import outbound, presence, routing
from excom.excom.channels.voice.handler import _participants
from excom.excom.utils.phone import phone_variants
from excom.excom.channels.voice.providers.base import CallDecision, CallEvent, Destination
from excom.excom.channels.voice.outbound import (
	KNOWN_COUNTRY_CODES,
	_is_international,
	country_code_of,
	line_for_destination,
)
from excom.excom.channels.voice.providers.plivo import PlivoAdapter, endpoint_uri, registered_aor

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
		# The auth id suffix is not decoration: it is the address the browser registered at.
		self.assertEqual(
			dial.find("User").text, "sip:excompriya123_MATESTAUTHID000000@phone.plivo.com"
		)
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


class TestRegisteredAddress(FrappeTestCase):
	"""An endpoint has two names, and dialling the wrong one is a silent missed call.

	Its identity is ``sip:<username>@phone.plivo.com``. Its live registration — because the Browser
	SDK builds the SIP user from the JWT as ``sub`` + ``_`` + ``iss`` — is at
	``sip:<username>_<auth_id>@phone.plivo.com``. Dial the identity and Plivo answers `Endpoint Not
	Registered` (2020) before a single packet of audio: the caller hears two rings and a drop, and
	Excom files a miss against an agent who was sitting right there with the tab open.
	"""

	def setUp(self):
		self.adapter = _adapter()

	def test_browser_destination_is_dialled_at_the_registered_address(self):
		decision = CallDecision(
			destinations=[Destination(kind="sip", ref="sip:excompriya123@phone.plivo.com")],
			record=False,
		)
		root = ET.fromstring(self.adapter.render_decision(decision))
		self.assertEqual(
			root.find("Dial/User").text, "sip:excompriya123_MATESTAUTHID000000@phone.plivo.com"
		)

	def test_phone_destinations_are_left_alone(self):
		"""The suffix belongs to SIP registration. A mobile number must survive untouched."""
		decision = CallDecision(
			destinations=[Destination(kind="pstn", ref="+919812345678")], record=False
		)
		root = ET.fromstring(self.adapter.render_decision(decision))
		self.assertEqual(root.find("Dial/Number").text, "+919812345678")

	def test_suffixing_is_idempotent(self):
		"""Rendering an already-registered address twice must not stack suffixes."""
		once = registered_aor("sip:excompriya123@phone.plivo.com", "MATESTAUTHID000000")
		self.assertEqual(registered_aor(once, "MATESTAUTHID000000"), once)

	def test_the_answered_leg_maps_back_to_the_endpoint(self):
		"""Plivo echoes the address it dialled. Left suffixed, the Excom Voice Endpoint lookup
		misses and the call is credited to nobody."""
		event = self.adapter.normalize_event(
			"dial_action",
			{
				"CallUUID": "uuid-9",
				"DialStatus": "completed",
				"DialBLegTo": "sip:excompriya123_MATESTAUTHID000000@phone.plivo.com",
				"DialBLegDuration": "42",
			},
		)
		self.assertEqual(event.answered_destination, "sip:excompriya123@phone.plivo.com")

	def test_a_phone_leg_is_reported_as_itself(self):
		event = self.adapter.normalize_event(
			"dial_action",
			{"CallUUID": "uuid-9", "DialStatus": "completed", "DialBLegTo": "+919812345678"},
		)
		self.assertEqual(event.answered_destination, "+919812345678")

	def test_the_two_names_round_trip(self):
		identity = "sip:excompriya123@phone.plivo.com"
		self.assertEqual(
			endpoint_uri(registered_aor(identity, "MATESTAUTHID000000"), "MATESTAUTHID000000"),
			identity,
		)


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
	"""Caller lookup is exact matching against a bounded set of spellings, never a LIKE.

	One number, four spellings, and different parts of the system pick different ones: a telephony
	webhook sends 919876543210, an imported contact list holds 09876543210, somebody types
	9876543210, and an E.164 field stores +919876543210. Matched as strings those are four people,
	which is how one contact ended up with two conversations.
	"""

	def test_variants_cover_the_ways_a_number_is_written(self):
		for given in ("+919876543210", "919876543210", "09876543210", "9876543210"):
			variants = phone_variants(given)
			for expected in ("+919876543210", "919876543210", "9876543210", "09876543210"):
				self.assertIn(expected, variants, f"{given} did not offer {expected}")

	def test_variants_are_deduplicated_and_bounded(self):
		variants = phone_variants("+919876543210")
		self.assertEqual(len(variants), len(set(variants)))
		self.assertLess(len(variants), 10)

	def test_no_wildcards_ever_reach_a_query(self):
		for value in phone_variants("+919876543210"):
			self.assertNotIn("%", value)

	def test_a_foreign_number_is_not_given_indian_spellings(self):
		"""The trap in the obvious implementation.

		"Anything sharing the last ten digits" would fold +1 921 702 5599 into +91 92170 25599:
		same final ten digits, different continent, different person. A US number gets its own two
		spellings and nothing else.
		"""
		variants = phone_variants("+19217025599")
		self.assertIn("19217025599", variants)
		self.assertNotIn("919217025599", variants)
		self.assertNotIn("09217025599", variants)

	def test_a_number_we_cannot_place_is_left_alone(self):
		self.assertEqual(phone_variants("+442071234567"), ["442071234567", "+442071234567"])

	def test_nothing_in_nothing_out(self):
		self.assertEqual(phone_variants(""), [])
		self.assertEqual(phone_variants("not a number"), [])


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


class TestInternationalGate(FrappeTestCase):
	"""Which numbers count as abroad, and therefore need permission and eat the daily cap.

	The gate is only worth having if it agrees with the map. It used to decide by asking whether
	the target started with the first one, two or three digits of the line's own number — and from
	an Indian line (+91...) the one-digit case matches every number that begins with 9. Pakistan
	(+92), Sri Lanka (+94) and the UAE (+971) were all filed as domestic, which meant they skipped
	the international switch and never counted against the minutes cap. Those are not obscure
	destinations for an Indian desk; they are among the ones it dials most.
	"""

	def setUp(self):
		self.india = _account_stub(voice_number="+918041234567")
		self.usa = _account_stub(voice_number="+14155550100")

	def _intl(self, number, account=None):
		return _is_international(number, account or self.india)

	def test_home_country_is_not_international(self):
		self.assertFalse(self._intl("+919250333699"))
		self.assertFalse(self._intl("+919812345678"))

	def test_neighbours_sharing_our_first_digit_are_international(self):
		"""The regression. Every one of these begins with 9, as +91 does."""
		for number, where in [
			("+923001234567", "Pakistan"),
			("+94771234567", "Sri Lanka"),
			("+971501234567", "UAE"),
			("+9613456789", "Lebanon"),
			("+905321234567", "Turkey"),
			("+989121234567", "Iran"),
		]:
			with self.subTest(where=where):
				self.assertTrue(self._intl(number), f"{where} must count as international")

	def test_the_obvious_ones_are_still_international(self):
		for number in ("+14155552671", "+442071234567", "+8613800138000", "+61412345678"):
			self.assertTrue(self._intl(number))

	def test_the_gate_follows_the_line_not_a_hardcoded_country(self):
		"""From a US line, India is abroad and the US is home — the mirror of the case above."""
		self.assertFalse(self._intl("+12125551234", self.usa))
		self.assertTrue(self._intl("+919250333699", self.usa))

	def test_an_unknown_country_code_is_treated_as_international(self):
		"""Of the two ways to be wrong, asking for permission is the one you can undo."""
		self.assertTrue(self._intl("+8821612345678"))

	def test_a_line_we_cannot_place_gates_nothing(self):
		"""Better an ungated line than one that refuses every call it is asked to make."""
		nowhere = _account_stub(voice_number="")
		self.assertFalse(self._intl("+14155552671", nowhere))

	def test_country_code_is_read_longest_first(self):
		"""+971 is the UAE, not India with a stray 1 in front of the number."""
		self.assertEqual(country_code_of("+971501234567"), "971")
		self.assertEqual(country_code_of("+919250333699"), "91")
		self.assertEqual(country_code_of("+14155552671"), "1")
		self.assertEqual(country_code_of("+442071234567"), "44")

	def test_three_digit_codes_precede_the_two_digit_codes_they_start_with(self):
		"""A table in the wrong order silently reclassifies whole countries, so check the table."""
		for i, code in enumerate(KNOWN_COUNTRY_CODES):
			for longer in KNOWN_COUNTRY_CODES[i + 1 :]:
				self.assertFalse(
					longer.startswith(code) and len(longer) > len(code),
					f"+{longer} is listed after +{code} and will never be matched",
				)


class TestLineForDestination(FrappeTestCase):
	"""With two provider accounts, the destination decides which one carries the call.

	A provider account is tied to a country: the Indian one can dial India and not America, the
	American one can dial America and not India, and each shows its own caller id. Before this, the
	line came from an unordered `limit=1` over the agent's endpoints — so with two lines the
	database chose, and chose differently each time. An Indian number went out on the American line
	at international rates showing a +1 caller id, or an American number went out on the Indian line
	and the carrier barred it.
	"""

	INDIA = "VOICE-TEST-IN"
	USA = "VOICE-TEST-US"

	def setUp(self):
		self.lines = {
			self.INDIA: _account_stub(name=self.INDIA, voice_number="+918041234567"),
			self.USA: _account_stub(
				name=self.USA, voice_number="+12125550143", voice_allow_international=1
			),
		}
		# line_for_destination reads each candidate through the document cache; stand in for it so
		# the test needs no fixtures and cannot be perturbed by whatever the site happens to hold.
		self._real = frappe.get_cached_doc
		frappe.get_cached_doc = lambda dt, name=None, *a, **k: (
			self.lines[name] if dt == "Excom Channel Account" and name in self.lines
			else self._real(dt, name, *a, **k)
		)
		self.addCleanup(setattr, frappe, "get_cached_doc", self._real)

	def _line(self, number):
		return line_for_destination("agent@example.com", number, [self.INDIA, self.USA])

	def test_a_domestic_number_stays_on_the_domestic_line(self):
		self.assertEqual(self._line("+919250333699"), self.INDIA)

	def test_an_american_number_goes_out_on_the_american_line(self):
		"""Local call, right caller id, and it can never be refused as a barred destination."""
		self.assertEqual(self._line("+14155552671"), self.USA)

	def test_a_country_neither_line_lives_in_goes_to_the_international_one(self):
		"""Only one of these two lines is allowed to dial abroad at all."""
		self.assertEqual(self._line("+442071234567"), self.USA)

	def test_the_country_match_wins_over_the_ordering(self):
		"""India is first in the list, but an American number must not take it."""
		self.assertEqual(
			line_for_destination("agent@example.com", "+14155552671", [self.INDIA, self.USA]),
			self.USA,
		)
		self.assertEqual(
			line_for_destination("agent@example.com", "+919250333699", [self.USA, self.INDIA]),
			self.INDIA,
		)

	def test_with_one_line_everything_goes_to_it(self):
		"""The single-line site, which is every site until a second account is added."""
		self.assertEqual(
			line_for_destination("agent@example.com", "+14155552671", [self.INDIA]), self.INDIA
		)

	def test_an_unrecognised_number_falls_back_rather_than_guessing(self):
		self.assertEqual(self._line("+8821612345678"), self.USA)

	def test_no_lines_at_all_returns_nothing(self):
		self.assertIsNone(line_for_destination("agent@example.com", "+14155552671", []))


class TestTransportAcrossLines(FrappeTestCase):
	"""A softphone signed in to one line must not disqualify the agent from dialling on another.

	The browser holds one line at a time — the SDK hands out a single client — and the line is
	chosen from the destination. So an agent signed in to the American desk who dials an Indian
	number is the ordinary case. Asking "are you registered on *this* line" refused that call
	outright, before the client was ever handed the plan it would have switched on, so the switch
	could never happen: the call was rejected for a condition the client was about to fix.
	"""

	US = "VOICE-US"
	INDIA = "VOICE-IN"

	def setUp(self):
		self.lines = [self.INDIA, self.US]
		self.registered = set()
		self.endpoints = {(u, a) for u in ("agent@example.com",) for a in self.lines}

		self._lines = outbound.agent_lines
		self._is_reg = presence.is_registered
		self._exists = frappe.db.exists
		outbound.agent_lines = lambda user="": list(self.lines)
		presence.is_registered = lambda user, account: account in self.registered
		frappe.db.exists = lambda dt, filters=None, *a, **k: (
			((filters or {}).get("user"), (filters or {}).get("channel_account")) in self.endpoints
			if dt == "Excom Voice Endpoint"
			else self._exists(dt, filters, *a, **k)
		)
		self.addCleanup(setattr, outbound, "agent_lines", self._lines)
		self.addCleanup(setattr, presence, "is_registered", self._is_reg)
		self.addCleanup(setattr, frappe.db, "exists", self._exists)

	def _transport(self, target_line, account_doc):
		return outbound.preferred_transport("agent@example.com", target_line, account_doc)

	def test_signed_in_elsewhere_still_dials_in_the_browser(self):
		"""The regression. Registered on the American line, dialling out on the Indian one."""
		self.registered = {self.US}
		india = _account_stub(name=self.INDIA, voice_allow_browser_calls=1, voice_allow_phone_calls=1)
		self.assertEqual(self._transport(self.INDIA, india), "Browser")

	def test_signed_in_on_the_line_itself_dials_in_the_browser(self):
		self.registered = {self.INDIA}
		india = _account_stub(name=self.INDIA, voice_allow_browser_calls=1, voice_allow_phone_calls=1)
		self.assertEqual(self._transport(self.INDIA, india), "Browser")

	def test_no_softphone_anywhere_falls_back_to_the_handset(self):
		"""The fallback that must survive: a closed tab still rings the agent's phone."""
		self.registered = set()
		india = _account_stub(name=self.INDIA, voice_allow_browser_calls=1, voice_allow_phone_calls=1)
		self.assertEqual(self._transport(self.INDIA, india), "Phone")

	def test_a_line_with_no_softphone_for_this_agent_is_not_browser(self):
		"""Registered somewhere, but no endpoint on the line being dialled."""
		self.registered = {self.US}
		self.endpoints = {("agent@example.com", self.US)}
		india = _account_stub(name=self.INDIA, voice_allow_browser_calls=1, voice_allow_phone_calls=1)
		self.assertEqual(self._transport(self.INDIA, india), "Phone")

	def test_registered_line_reports_which_desk_the_browser_is_on(self):
		self.registered = {self.US}
		self.assertEqual(presence.registered_line("agent@example.com", self.lines), self.US)
		self.registered = set()
		self.assertIsNone(presence.registered_line("agent@example.com", self.lines))

	def test_registered_line_copes_with_nothing_to_check(self):
		self.assertIsNone(presence.registered_line("agent@example.com", []))
		self.assertIsNone(presence.registered_line("agent@example.com", None))


class TestFailureReasons(FrappeTestCase):
	"""A call that a carrier refused must not look like a call nobody answered."""

	def setUp(self):
		self.adapter = _adapter()

	def test_a_barred_country_explains_itself(self):
		event = self.adapter.normalize_event(
			"hangup",
			{
				"CallUUID": "uuid-x",
				"CallStatus": "failed",
				"HangupCauseName": "Destination Country Barred",
			},
		)
		self.assertEqual(event.status, "Failed")
		self.assertIn("Geo Permissions", event.failure_reason)

	def test_an_ordinary_hangup_adds_nothing(self):
		event = self.adapter.normalize_event(
			"hangup",
			{"CallUUID": "uuid-y", "CallStatus": "completed", "HangupCauseName": "Normal Hangup"},
		)
		self.assertEqual(event.failure_reason, "")

	def test_the_dial_action_path_explains_itself_too(self):
		"""The browser transport ends here, not on the hangup URL."""
		event = self.adapter.normalize_event(
			"dial_action",
			{
				"CallUUID": "uuid-z",
				"DialStatus": "failed",
				"DialHangupCause": "Destination Country Barred",
			},
		)
		self.assertIn("Geo Permissions", event.failure_reason)


class TestWhoTheCustomerIs(FrappeTestCase):
	"""Given a webhook for a leg we have no record of, who is the call actually with?

	Every one of these produced a real junk conversation on the live site. A webhook can describe
	either leg, and neither leg puts the customer in a fixed field, so reading `From` as "the
	customer" opened conversations with ourselves: with our own DID, because that is the caller id
	we ask Plivo to present on the B leg of a dial; and with an eighteen-digit number, which is what
	is left of `sip:<endpoint>_<auth id>@phone.plivo.com` once the punctuation is stripped out of
	it.
	"""

	LINE = "VOICE-TEST"
	OURS = "+918041234567"

	def setUp(self):
		self._real = frappe.db.get_value
		frappe.db.get_value = lambda dt, name=None, fieldname=None, *a, **k: (
			self.OURS
			if (dt == "Excom Channel Account" and fieldname == "voice_number")
			else self._real(dt, name, fieldname, *a, **k)
		)
		self.addCleanup(setattr, frappe.db, "get_value", self._real)

	def _who(self, **kwargs):
		event = CallEvent(kind="ended", provider_call_id="uuid-1", **kwargs)
		return _participants(event, self.LINE)

	def test_a_stranger_calling_in_is_the_customer(self):
		direction, customer, business = self._who(
			from_number="918542866684", to_number="918041234567", direction="inbound"
		)
		self.assertEqual(direction, "Inbound")
		self.assertEqual(customer, "+918542866684")
		self.assertEqual(business, "+918041234567")

	def test_our_own_caller_id_on_the_b_leg_is_not_a_customer(self):
		"""<Dial callerId="+91804..."> makes `From` our own number on the leg to the customer."""
		direction, customer, _business = self._who(
			from_number="918041234567", to_number="918542866684", direction="outbound"
		)
		self.assertEqual(direction, "Outbound")
		self.assertEqual(customer, "+918542866684")

	def test_a_softphone_is_never_a_customer(self):
		"""The leg the browser placed. Plivo calls it inbound, because it is inbound to Plivo."""
		direction, customer, _business = self._who(
			from_number="sip:excompriya123_MATESTAUTHID000000@phone.plivo.com",
			to_number="919250333699",
			direction="inbound",
		)
		self.assertEqual(direction, "Outbound", "a call from our own softphone is outbound")
		self.assertEqual(customer, "+919250333699")

	def test_both_ends_ours_yields_no_contact_rather_than_a_wrong_one(self):
		_direction, customer, _business = self._who(
			from_number="sip:excompriya123_MATESTAUTHID000000@phone.plivo.com",
			to_number="918041234567",
			direction="inbound",
		)
		self.assertEqual(customer, "", "better a call with no contact than a contact that is us")

	def test_the_sip_username_never_becomes_a_phone_number(self):
		"""The exact shape that reached production: 18 digits of endpoint username."""
		_direction, customer, _business = self._who(
			from_number="sip:excompriya123456789012345678_MATESTAUTHID000000@phone.plivo.com",
			to_number="918787879696",
			direction="inbound",
		)
		self.assertNotIn("123456789012345678", customer)
		self.assertEqual(customer, "+918787879696")


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
