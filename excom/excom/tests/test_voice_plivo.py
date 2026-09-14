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
from frappe.utils import add_to_date, now_datetime

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


class TestCallSurvivesOurOwnFailures(FrappeTestCase):
	"""What the caller hears when our bookkeeping breaks mid-call.

	The vendor is waiting on the dial action URL and, on Twilio, will execute whatever comes back
	as call control. A Frappe error page is not call control: the caller gets "an application error
	has occurred" and the line drops. This reached production as an HTTP 417 on a live call.
	"""

	def test_a_failure_still_answers_in_the_vendors_language(self):
		from unittest.mock import patch

		from excom.excom.api import voice

		with patch.object(voice, "_verified_account", return_value="VOICE-TEST"), patch.object(
			voice.providers, "for_account"
		) as for_account, patch.object(
			voice, "_handle_event", side_effect=RuntimeError("the database went away")
		):
			for_account.return_value.action_response.return_value = "<Response></Response>"
			response = voice.dial_action()

		self.assertEqual(response.status_code, 200)
		self.assertIn("xml", response.content_type)
		self.assertEqual(response.get_data(as_text=True), "<Response></Response>")

	def test_a_forged_webhook_is_still_refused(self):
		"""The signature check sits outside the guard deliberately. Answering a probe with valid
		call control would hand a stranger the phone line."""
		from unittest.mock import patch

		from excom.excom.api import voice

		with patch.object(
			voice, "_verified_account", side_effect=frappe.PermissionError("Unrecognised")
		):
			with self.assertRaises(frappe.PermissionError):
				voice.dial_action()


class TestReconcileGivesUp(FrappeTestCase):
	"""A row whose answer cannot change must leave the queue.

	The sweep takes the fifty oldest unreconciled calls. Sixteen legacy rows on a line with no
	adapter were therefore picked first every single time, threw, logged, and stayed — so they held
	sixteen of the fifty slots for ever and would have starved real calls once the backlog grew.
	"""

	def test_a_provider_with_no_adapter_is_permanent(self):
		from excom.excom.channels.voice.reconcile import _permanent

		row = frappe._dict(name="CALL-1", creation=now_datetime())
		exc = frappe.ValidationError("Exotel is not implemented yet. This line cannot place calls.")
		self.assertTrue(_permanent(row, exc))

	def test_a_transient_failure_is_retried(self):
		from excom.excom.channels.voice.reconcile import _permanent

		row = frappe._dict(name="CALL-2", creation=now_datetime())
		self.assertFalse(_permanent(row, ConnectionError("provider API timed out")))

	def test_a_call_older_than_the_cdr_retention_is_given_up_on(self):
		from excom.excom.channels.voice.reconcile import GIVE_UP_DAYS, _permanent

		row = frappe._dict(
			name="CALL-3", creation=add_to_date(now_datetime(), days=-(GIVE_UP_DAYS + 1))
		)
		self.assertTrue(_permanent(row, ConnectionError("still not in the CDR store")))


class TestOneCallOneRow(FrappeTestCase):
	"""persist_call promises in its docstring that it is safe to call twice.

	The unique index on provider_call_id is what makes that true, but only if losing the race
	returns the winner's row. It used to raise instead, so two webhooks for one call took the
	enqueued job down and wrote an Error Log row — three times on the live site.
	"""

	def tearDown(self):
		for name in frappe.get_all(
			"Excom Call", {"provider_call_id": ["like", "race-test-%"]}, pluck="name"
		):
			frappe.delete_doc("Excom Call", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_losing_the_insert_race_returns_the_row_that_won(self):
		from unittest.mock import patch

		from excom.excom.channels.voice import handler

		uid = "race-test-1"
		winner = frappe.get_doc(
			{"doctype": "Excom Call", "provider_call_id": uid, "direction": "Inbound", "status": "Ringing"}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# The existence check is only an optimisation, and under a race it sees nothing. Forcing
		# that is the only way to reach the insert the index then refuses.
		#
		# It must lie exactly once: the recovery path looks the same row up again, and a stub that
		# always answered None would make the fixed code return None instead of the winner.
		real = frappe.db.get_value
		looked = {"times": 0}

		def blind(doctype, filters=None, fieldname="name", *args, **kwargs):
			if doctype == "Excom Call" and filters == {"provider_call_id": uid}:
				looked["times"] += 1
				if looked["times"] == 1:
					return None
			return real(doctype, filters, fieldname, *args, **kwargs)

		with patch("frappe.db.get_value", side_effect=blind):
			got = handler.persist_call(
				provider_call_id=uid,
				account="",
				direction="Inbound",
				caller_number="+919900000123",
				business_number="+918041234567",
			)

		self.assertEqual(got, winner.name)
		self.assertEqual(
			frappe.db.count("Excom Call", {"provider_call_id": uid}),
			1,
			"the race must not leave a second row behind",
		)
		self.assertGreater(
			looked["times"], 1, "the stub never fired, so the insert was never reached"
		)


class TestABrowserLegIsNotACustomer(FrappeTestCase):
	"""A call the agent placed must never be answered as a call arriving.

	This reached a live line. A browser call from the Twilio softphone came back to the answer URL
	as `From: client:excom_pranav..., Direction: inbound` — Twilio calls such a leg inbound because
	it is inbound to Twilio — and the test for a browser leg looked for `sip:`, which is Plivo's
	shape. So Excom took the inbound path and rang the whole team about a call one of them was
	placing, while the customer's number was never dialled at all.
	"""

	def test_twilio_knows_its_own_softphone(self):
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		adapter = TwilioAdapter(_account_stub(voice_provider="Twilio"))
		self.assertTrue(
			adapter.originates_from_softphone(
				{"From": "client:excom_pranav_ggil_rbcolour_com", "Direction": "inbound"}
			),
			"a leg the browser placed must be recognised however the vendor labels its direction",
		)
		self.assertFalse(
			adapter.originates_from_softphone({"From": "+918979818152", "Direction": "inbound"}),
			"a real customer calling in is not a softphone",
		)

	def test_plivo_still_knows_its_own(self):
		adapter = PlivoAdapter(_account_stub())
		self.assertTrue(
			adapter.originates_from_softphone({"From": "sip:excomsomil_MATEST@phone.plivo.com"})
		)
		self.assertFalse(adapter.originates_from_softphone({"From": "+919900000123"}))

	def test_a_softphone_address_is_recognised_whatever_the_vendor_calls_it(self):
		from excom.excom.channels.voice.handler import is_softphone

		self.assertTrue(is_softphone("sip:excomsomil_MATEST@phone.plivo.com"))
		self.assertTrue(is_softphone("client:excom_pranav_ggil_rbcolour_com"))
		self.assertFalse(is_softphone("+918795194645"))
		self.assertFalse(is_softphone(""))
		self.assertFalse(is_softphone(None))

	def test_the_twilio_browser_leg_is_outbound_with_no_contact_made_from_it(self):
		"""The exact payload that misfired, run through the participant rule."""
		from excom.excom.channels.voice.handler import _participants

		event = CallEvent(
			kind="ringing",
			provider_call_id="CA77d89d674a42ea345f98fd89b0f83972",
			from_number="client:excom_pranav_ggil_rbcolour_com",
			to_number="+918795194645",
			direction="inbound",
			raw={},
		)
		direction, customer, business = _participants(event, "VOICE-TEST-TWILIO")
		self.assertEqual(direction, "Outbound")
		self.assertEqual(
			customer,
			"+918795194645",
			"the dialled number is the customer; the softphone that dialled it is not",
		)


class TestBrowserContextReachesTheAnswerUrl(FrappeTestCase):
	"""The leg is created by the SDK, so the call's context can only travel on the leg itself.

	Each vendor carries it differently, and sending Plivo's header names to Twilio meant the
	destination, thread, user and account never arrived — which is why the leg reached the answer
	URL with nothing attached to it.
	"""

	CONTEXT = {
		"to": "+918795194645",
		"thread": "THREAD-1",
		"user": "agent@example.com",
		"account": "Twilio International",
	}

	def test_plivo_sends_sip_headers(self):
		"""The names are Plivo's; the values are encoded, so assert the round trip, not the bytes."""
		adapter = PlivoAdapter(_account_stub())
		out = adapter.browser_context(dict(self.CONTEXT))
		self.assertEqual(sorted(out), ["X-PH-account", "X-PH-thread", "X-PH-to", "X-PH-user"])

		back = adapter.normalize_event("ringing", {"CallUUID": "u1", **out})
		self.assertEqual(back.sip_headers.get("to"), "+918795194645")
		self.assertEqual(back.sip_headers.get("user"), "agent@example.com")

	def test_twilio_sends_parameters_its_own_reader_strips_back(self):
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		adapter = TwilioAdapter(_account_stub(voice_provider="Twilio"))
		out = adapter.browser_context(dict(self.CONTEXT))
		self.assertEqual(out["ExcomTo"], "+918795194645")

		# The round trip is the point: whatever browser_context emits, normalize_event must read.
		event = adapter.normalize_event("ringing", {"CallSid": "CA1", "From": "client:x", **out})
		self.assertEqual(event.sip_headers.get("to"), "+918795194645")
		self.assertEqual(event.sip_headers.get("user"), "agent@example.com")
		self.assertEqual(event.sip_headers.get("thread"), "THREAD-1")
		self.assertEqual(event.sip_headers.get("account"), "Twilio International")

	def test_empty_values_are_left_out_rather_than_sent_blank(self):
		out = PlivoAdapter(_account_stub()).browser_context({"to": "+911", "thread": ""})
		self.assertNotIn("X-PH-thread", out)


class TestOneConversationOneRecord(FrappeTestCase):
	"""`<Dial>` creates a second leg, and Twilio gives it its own CallSid.

	Five of the twelve rows on the live Twilio line were that second leg recorded as a call in its
	own right — the far end of a conversation already saved, with its direction and transport read
	off the wrong leg. An outbound browser call to India appeared a second time as an inbound call
	on a handset.
	"""

	# Verbatim from the live account: the browser leg, then the leg <Dial> raised from it.
	PARENT = "CAfc6ce44f813ebf13c749fb41e1f8e083"
	CHILD = "CA8fa6a0fd03c72f8e8ea94333278d38bf"

	def _adapter(self):
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		return TwilioAdapter(_account_stub(voice_provider="Twilio"))

	def test_a_child_leg_reports_the_call_we_already_hold(self):
		event = self._adapter().normalize_event(
			"dial_event",
			{
				"CallSid": self.CHILD,
				"ParentCallSid": self.PARENT,
				"From": "+13185911821",
				"To": "+919326002507",
				"Direction": "outbound-dial",
				"CallStatus": "in-progress",
			},
		)
		self.assertEqual(
			event.provider_call_id,
			self.PARENT,
			"a webhook carrying ParentCallSid is about a leg of a call already recorded",
		)

	def test_the_parent_leg_still_reports_itself(self):
		event = self._adapter().normalize_event(
			"ringing",
			{
				"CallSid": self.PARENT,
				"From": "client:excom_somilsearchosis_gmail_com",
				"To": "919326002507",
				"Direction": "inbound",
			},
		)
		self.assertEqual(event.provider_call_id, self.PARENT)

	def test_plivo_reports_one_uuid_throughout(self):
		"""The same conversation on Plivo never had this problem, and must not acquire it."""
		uuid = "67dec1c8-e444-4e52-bd2f-a23b6a764b45"
		event = PlivoAdapter(_account_stub()).normalize_event(
			"dial_event", {"CallUUID": uuid, "From": "sip:agent@phone.plivo.com", "To": "919326002507"}
		)
		self.assertEqual(event.provider_call_id, uuid)


class TestTheWriteThatKnowsWins(FrappeTestCase):
	"""Which webhook reaches us first must not decide what the call says it was.

	`route()` enqueues the write that has dial()'s decision in hand, because the caller is listening
	to silence while the answer URL runs. The statusCallback on the leg `<Dial>` raises is handled
	inline, so it frequently lands first — and persist_call being idempotent then meant the guess
	stuck and the truth was discarded. A browser call to India was recorded as an inbound call on a
	handset that way.
	"""

	UID = "race-order-1"

	def tearDown(self):
		for name in frappe.get_all(
			"Excom Call", {"provider_call_id": ["like", "race-order-%"]}, pluck="name"
		):
			frappe.delete_doc("Excom Call", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_the_child_leg_arriving_first_does_not_settle_the_call(self):
		from excom.excom.channels.voice import handler

		# 1. The statusCallback on the dialled leg gets there first and opens the row.
		first = handler.persist_call(
			provider_call_id=self.UID,
			account="",
			direction="Inbound",
			caller_number="+918542866684",
			business_number="+13185911821",
			transport="",
		)
		self.assertTrue(first)

		# 2. The answer URL's write lands afterwards, carrying what dial() decided.
		second = handler.persist_call(
			provider_call_id=self.UID,
			account="",
			direction="Outbound",
			caller_number="+918542866684",
			business_number="+13185911821",
			agent="somilsearchosis@gmail.com",
			transport="Browser",
			authoritative=True,
		)
		self.assertEqual(second, first, "still one call, not two")

		row = frappe.db.get_value(
			"Excom Call", first, ["direction", "transport", "agent"], as_dict=True
		)
		self.assertEqual(row.direction, "Outbound")
		self.assertEqual(row.transport, "Browser")
		self.assertEqual(row.agent, "somilsearchosis@gmail.com")

	def test_a_guess_never_overwrites_the_truth(self):
		"""The other order must be safe too: the authoritative write first, a later guess second."""
		from excom.excom.channels.voice import handler

		name = handler.persist_call(
			provider_call_id=self.UID,
			account="",
			direction="Outbound",
			caller_number="+918542866684",
			business_number="+13185911821",
			transport="Browser",
			authoritative=True,
		)
		handler.persist_call(
			provider_call_id=self.UID,
			account="",
			direction="Inbound",
			caller_number="+918542866684",
			business_number="+13185911821",
			transport="Phone",
		)
		row = frappe.db.get_value("Excom Call", name, ["direction", "transport"], as_dict=True)
		self.assertEqual(row.direction, "Outbound")
		self.assertEqual(row.transport, "Browser")

	def test_how_the_agent_answered_outranks_the_answer_url(self):
		"""Once somebody has answered, the transport is an observation, not a plan."""
		from excom.excom.channels.voice import handler

		name = handler.persist_call(
			provider_call_id=self.UID, account="", direction="Inbound",
			caller_number="+918542866684", business_number="+13185911821", transport="",
		)
		frappe.db.set_value(
			"Excom Call", name,
			{"answered_by": "somilsearchosis@gmail.com", "transport": "Phone"},
			update_modified=False,
		)
		handler.persist_call(
			provider_call_id=self.UID, account="", direction="Inbound",
			caller_number="+918542866684", business_number="+13185911821",
			transport="Browser", authoritative=True,
		)
		self.assertEqual(
			frappe.db.get_value("Excom Call", name, "transport"),
			"Phone",
			"they picked up the handset; the plan does not get to say otherwise",
		)

	def test_outbound_dial_is_recognised_as_outbound(self):
		"""Twilio labels these legs outbound-dial and outbound-api, never a bare 'outbound'.

		Testing the bare word sent every one of them to Inbound, which is how a call placed from
		the browser came back as a call arriving.
		"""
		from excom.excom.channels.voice.handler import _participants

		ours = "+13185911821"
		real = frappe.db.get_value
		frappe.db.get_value = lambda dt, name=None, fieldname=None, *a, **k: (
			ours
			if (dt == "Excom Channel Account" and fieldname == "voice_number")
			else real(dt, name, fieldname, *a, **k)
		)
		self.addCleanup(setattr, frappe.db, "get_value", real)

		for label in ("outbound-dial", "outbound-api", "outbound"):
			event = CallEvent(
				kind="ringing", provider_call_id="CA1",
				from_number=ours, to_number="+918542866684",
				direction=label, raw={},
			)
			direction, customer, business = _participants(event, "VOICE-TEST")
			self.assertEqual(direction, "Outbound", "%s must read as outbound" % label)
			self.assertEqual(customer, "+918542866684", "our own caller id is not the customer")
			self.assertEqual(business, ours)


class TestTheCallIsIdentifiedTheVendorsWay(FrappeTestCase):
	"""`route()` used to read the call id as CallUUID or RequestUUID — both Plivo's names.

	On a Twilio line neither exists, so the id came out empty, and persist_call returns immediately
	on an empty id. The answer URL's entire write — the one that knows the direction, the transport
	and which agent placed the call — did nothing at all, on every Twilio call ever made.
	"""

	PLIVO_PAYLOAD = {
		"CallUUID": "22283d0f-14df-4195-abe6-56678ed3dfc5",
		"From": "sip:agent@phone.plivo.com", "To": "918542866684", "Direction": "inbound",
	}
	TWILIO_PAYLOAD = {
		"CallSid": "CAf276a3ad55517bd3d99cd69d8bdac34c",
		"From": "client:excom_somilsearchosis_gmail_com", "To": "918542866684",
		"Direction": "inbound",
	}

	def test_plivo_call_is_identified(self):
		event = PlivoAdapter(_account_stub()).normalize_event("ringing", self.PLIVO_PAYLOAD)
		self.assertEqual(event.provider_call_id, self.PLIVO_PAYLOAD["CallUUID"])

	def test_twilio_call_is_identified(self):
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		adapter = TwilioAdapter(_account_stub(voice_provider="Twilio"))
		event = adapter.normalize_event("ringing", self.TWILIO_PAYLOAD)
		self.assertEqual(
			event.provider_call_id,
			self.TWILIO_PAYLOAD["CallSid"],
			"reading Plivo's field names here left the id empty and the answer URL's write a no-op",
		)

	def test_neither_id_is_empty(self):
		"""An empty id is not a near miss: persist_call returns on it and writes nothing."""
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		for adapter, payload in (
			(PlivoAdapter(_account_stub()), self.PLIVO_PAYLOAD),
			(TwilioAdapter(_account_stub(voice_provider="Twilio")), self.TWILIO_PAYLOAD),
		):
			self.assertTrue(adapter.normalize_event("ringing", payload).provider_call_id)

	def _enqueued_by_route(self, payload, provider):
		"""Run route() against one answer-URL payload and return what it queued for persist_call.

		The adapter was always right about the id; `route()` was the one reading it by hand, so this
		has to go through route() to mean anything.
		"""
		from unittest.mock import patch

		from excom.excom.api import voice

		captured = {}

		def fake_enqueue(method, **kwargs):
			if "persist_call" in str(method):
				captured.update(kwargs)

		with patch.object(voice, "_verified_account", return_value="VOICE-TEST"), patch.object(
			voice, "_payload", return_value=payload
		), patch.object(voice.providers, "for_account", return_value=provider), patch.object(
			voice.frappe, "enqueue", side_effect=fake_enqueue
		), patch.object(
			voice, "_refuse_outbound", return_value=None
		), patch.object(
			voice.frappe, "get_cached_doc", return_value=frappe._dict(_account_stub())
		):
			voice.route()
		return captured

	def test_route_queues_the_twilio_call_under_the_id_twilio_gave_it(self):
		from excom.excom.channels.voice.providers.twilio import TwilioAdapter

		adapter = TwilioAdapter(_account_stub(voice_provider="Twilio"))
		queued = self._enqueued_by_route(dict(self.TWILIO_PAYLOAD), adapter)
		self.assertEqual(
			queued.get("provider_call_id"),
			self.TWILIO_PAYLOAD["CallSid"],
			"an empty id here makes the answer URL's whole write a no-op",
		)

	def test_route_still_queues_the_plivo_call_correctly(self):
		adapter = PlivoAdapter(_account_stub())
		queued = self._enqueued_by_route(dict(self.PLIVO_PAYLOAD), adapter)
		self.assertEqual(queued.get("provider_call_id"), self.PLIVO_PAYLOAD["CallUUID"])


class TestContextSurvivesTheWire(FrappeTestCase):
	"""Two of the four context headers never reached the live answer URL.

	It received X-PH-to and X-PH-thread and nothing else, and the two that vanished are the two a
	SIP header cannot carry: the agent is an email address, so it has an `@`, and the account is a
	line name, so it has spaces. Plivo drops those without a word — which is why no browser call
	has ever recorded which agent placed it.
	"""

	CONTEXT = {
		"to": "+918542866684",
		"thread": "7mrsqqndas",
		"user": "somilsearchosis@gmail.com",
		"account": "Plivo Sales Line",
	}

	def test_every_value_survives_a_sip_header(self):
		adapter = PlivoAdapter(_account_stub())
		wire = adapter.browser_context(dict(self.CONTEXT))

		for key, value in wire.items():
			self.assertRegex(
				value, r"^[A-Za-z0-9_-]+$",
				"%s carries %r, which a SIP header would drop" % (key, value),
			)

		back = adapter.normalize_event("ringing", {"CallUUID": "u1", **wire})
		self.assertEqual(back.sip_headers, self.CONTEXT, "what went out must come back unchanged")

	def test_the_agent_specifically_makes_it_through(self):
		"""The one that mattered: an email address, with its @."""
		adapter = PlivoAdapter(_account_stub())
		wire = adapter.browser_context({"user": "somilsearchosis@gmail.com"})
		back = adapter.normalize_event("ringing", {"CallUUID": "u1", **wire})
		self.assertEqual(back.sip_headers.get("user"), "somilsearchosis@gmail.com")

	def test_a_line_named_with_punctuation_also_survives(self):
		adapter = PlivoAdapter(_account_stub())
		name = "GGIL Export — Delhi (North)"
		wire = adapter.browser_context({"account": name})
		back = adapter.normalize_event("ringing", {"CallUUID": "u1", **wire})
		self.assertEqual(back.sip_headers.get("account"), name)

	def test_a_header_that_was_never_encoded_is_left_alone(self):
		"""Legs placed before this change are still in flight; their headers are plain text."""
		adapter = PlivoAdapter(_account_stub())
		back = adapter.normalize_event(
			"ringing", {"CallUUID": "u1", "X-PH-to": "+918542866684"}
		)
		self.assertEqual(back.sip_headers.get("to"), "+918542866684")
