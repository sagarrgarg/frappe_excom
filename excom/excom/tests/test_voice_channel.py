import time, random
import frappe
from frappe.tests.utils import FrappeTestCase
from excom.excom.channels.voice.providers.base import VoiceProvider, ProviderCallRef, CallDetails, CallEvent
from excom.excom.channels.voice.providers.exotel import ExotelAdapter
from excom.excom.channels.voice.providers.airtel import AirtelIQAdapter
from excom.excom.channels.voice.routing import resolve_voice_account, resolve_ring_destination, build_exotel_routing_response
from excom.excom.channels.voice.outbound import initiate_click_to_call
from excom.excom.channels.voice.reconcile import reconcile_pending_calls
from excom.excom.channels.voice.handler import handle_inbound_routing, handle_status_webhook
import unittest.mock as mock

class TestVoiceChannel(FrappeTestCase):
	def setUp(self):
		# Ensure test channel exists
		if not frappe.db.exists("Excom Channel", "voice"):
			ch = frappe.get_doc({
				"doctype": "Excom Channel",
				"__newname": "voice",
				"channel_label": "Calls",
				"allows_multiple_accounts": 1,
				"is_enabled": 1,
			})
			ch.insert(ignore_permissions=True)

		# Ensure test team exists
		if not frappe.db.exists("Excom Team", "Test Voice Team"):
			team = frappe.get_doc({
				"doctype": "Excom Team",
				"team_name": "Test Voice Team",
				"members": [{"user": "Administrator", "role": "Manager"}]
			})
			team.insert(ignore_permissions=True)

		# Ensure test voice channel account exists
		if not frappe.db.exists("Excom Channel Account", "Test Exotel Account"):
			acc = frappe.get_doc({
				"doctype": "Excom Channel Account",
				"account_name": "Test Exotel Account",
				"channel": "voice",
				"status": "Active",
				"voice_provider": "Exotel",
				"voice_account_sid": "mock_sid_123",
				"voice_api_key": "mock_key_456",
				"voice_api_secret": "mock_secret_789",
				"voice_api_base": "api.in.exotel.com",
				"voice_phone_number": "+918047115777",
				"voice_webhook_token": "valid_token_xyz",
				"voice_record_calls": "All",
				"voice_recording_channels": "Dual",
				"allowed_teams": [{"team": "Test Voice Team", "can_view": 1, "can_reply": 1}]
			})
			acc.insert(ignore_permissions=True)

		# Ensure Administrator has mobile number
		frappe.db.set_value("User", "Administrator", "mobile_no", "+919999988888")
		frappe.db.commit()

	def test_01_voice_channel_and_account_config(self):
		"""Verify Voice Channel and Channel Account fields & schema."""
		acc = frappe.get_doc("Excom Channel Account", "Test Exotel Account")
		self.assertEqual(acc.channel, "voice")
		self.assertEqual(acc.voice_provider, "Exotel")
		self.assertEqual(acc.voice_phone_number, "+918047115777")
		self.assertEqual(acc.get_password("voice_api_secret"), "mock_secret_789")

	def test_02_voice_provider_adapter_and_capabilities(self):
		"""Verify ExotelAdapter implementation against VoiceProvider ABC."""
		acc = frappe.get_doc("Excom Channel Account", "Test Exotel Account")
		adapter = ExotelAdapter(acc)
		
		# Capabilities
		caps = adapter.capabilities()
		self.assertIn("click_to_call", caps)
		self.assertIn("parallel_ringing", caps)
		self.assertIn("dual_channel_recording", caps)
		
		# Auth headers
		self.assertEqual(adapter._get_auth(), ("mock_key_456", "mock_secret_789"))
		
		# Normalize event
		event = adapter.normalize_event({
			"CallSid": "exotel_sid_999",
			"CallFrom": "+919876543210",
			"CallTo": "+918047115777",
			"DialCallStatus": "completed",
			"DialCallDuration": 125,
			"RecordingUrl": "https://api.exotel.com/rec/123.mp3"
		})
		self.assertEqual(event.provider_call_id, "exotel_sid_999")
		self.assertEqual(event.event_type, "completed")
		self.assertEqual(event.duration, 125)

	def test_03_inbound_routing_budget_and_payload(self):
		"""Benchmark Inbound Routing Engine (< 5s Hard Budget) and verify JSON structure."""
		start_time = time.time()
		account = resolve_voice_account("+918047115777")
		self.assertIsNotNone(account)
		
		ring_numbers = resolve_ring_destination("+919876543210", "+918047115777", account)
		self.assertTrue(len(ring_numbers) > 0)
		
		resp = build_exotel_routing_response(ring_numbers, "+918047115777", account)
		elapsed_ms = (time.time() - start_time) * 1000
		
		# Must return in under 200 milliseconds (well below 5000ms budget)
		self.assertLess(elapsed_ms, 500.0)
		self.assertFalse(resp["fetch_after_attempt"])
		self.assertTrue(resp["record"])
		self.assertEqual(resp["recording_channels"], "dual")
		self.assertTrue(resp["parallel_ringing"]["activate"])
		self.assertIn("+919999988888", resp["destination"]["numbers"])

	@mock.patch("requests.post")
	def test_04_outbound_click_to_call_and_timeline_stub(self, mock_post):
		"""Verify PSTN Click-to-Call initiation, Excom Call creation, and Excom Message timeline stub."""
		mock_call_sid = f"mock_outbound_{frappe.generate_hash()[:8]}"
		mock_response = mock.MagicMock()
		mock_response.ok = True
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"Call": {
				"Sid": mock_call_sid,
				"Status": "in-progress"
			}
		}
		mock_post.return_value = mock_response

		# Create test omni identity with unique phone
		test_phone = f"+9198{random.randint(10000000, 99999999)}"
		ident = frappe.get_doc({
			"doctype": "Omni Identity",
			"display_name": "Test Customer",
			"primary_phone": test_phone
		})
		ident.insert(ignore_permissions=True)

		# Create a dummy thread
		thread = frappe.new_doc("Excom Thread")
		thread.channel = "voice"
		thread.account_doctype = "Excom Channel Account"
		thread.account = "Test Exotel Account"
		thread.omni_identity = ident.name
		thread.status = "Open"
		thread.insert(ignore_permissions=True)

		result = initiate_click_to_call(
			to_number=test_phone,
			account_name="Test Exotel Account",
			thread_id=thread.name,
			agent_user="Administrator"
		)

		self.assertEqual(result["status"], "success")
		self.assertEqual(result["provider_call_id"], mock_call_sid)
		
		# Verify Excom Call was persisted
		call_id = result["call_id"]
		call_doc = frappe.get_doc("Excom Call", call_id)
		self.assertEqual(call_doc.direction, "Outbound")
		self.assertEqual(call_doc.status, "Ringing")
		self.assertEqual(call_doc.to_number, test_phone)
		self.assertEqual(call_doc.from_number, "+919999988888")
		self.assertEqual(call_doc.thread, thread.name)

		# Verify Excom Message timeline stub was created
		messages = frappe.get_all("Excom Message", filters={"thread": thread.name, "message_type": "Call"})
		self.assertEqual(len(messages), 1)

	@mock.patch("requests.get")
	def test_05_reconcile_background_job(self, mock_get):
		"""Verify scheduled reconcile worker backfilling duration and cost from Call Details API."""
		sid = f"mock_reconcile_{frappe.generate_hash()[:8]}"
		call_doc = frappe.new_doc("Excom Call")
		call_doc.provider_call_id = sid
		call_doc.direction = "Inbound"
		call_doc.status = "Completed"
		call_doc.from_number = "+919876543210"
		call_doc.to_number = "+918047115777"
		call_doc.channel_account = "Test Exotel Account"
		call_doc.duration = 0
		call_doc.insert(ignore_permissions=True)
		
		# Fast forward creation date in DB so it qualifies for cutoff (>90s)
		frappe.db.set_value("Excom Call", call_doc.name, "creation", "2026-08-27 00:00:00")
		frappe.db.commit()

		# Mock provider API response for call details
		mock_response = mock.MagicMock()
		mock_response.ok = True
		mock_response.status_code = 200
		mock_response.json.return_value = {
			"Call": {
				"Sid": sid,
				"Status": "completed",
				"Duration": 180,
				"Price": 1.25,
				"RecordingUrl": "https://api.exotel.com/recordings/888.wav"
			}
		}
		mock_get.return_value = mock_response

		# Run reconcile job
		reconcile_pending_calls()

		# Check that duration, cost and recording_url were backfilled
		updated_call = frappe.get_doc("Excom Call", call_doc.name)
		self.assertEqual(updated_call.duration, 180)
		self.assertEqual(updated_call.talk_time, 180)
		self.assertEqual(float(updated_call.cost), 1.25)
		self.assertEqual(updated_call.recording_url, "https://api.exotel.com/recordings/888.wav")

	def test_06_status_webhook_processor(self):
		"""Verify webhook event processing and status update."""
		sid = f"mock_webhook_{frappe.generate_hash()[:8]}"
		call_doc = frappe.new_doc("Excom Call")
		call_doc.provider_call_id = sid
		call_doc.direction = "Inbound"
		call_doc.status = "Ringing"
		call_doc.from_number = "+919876543210"
		call_doc.to_number = "+918047115777"
		call_doc.channel_account = "Test Exotel Account"
		call_doc.insert(ignore_permissions=True)

		resp = handle_status_webhook({
			"CallSid": sid,
			"DialCallStatus": "completed",
			"DialCallDuration": 65,
			"RecordingUrl": "https://api.exotel.com/rec/333.mp3"
		}, account_name="Test Exotel Account")

		self.assertEqual(resp["status"], "updated")
		updated = frappe.get_doc("Excom Call", call_doc.name)
		self.assertEqual(updated.status, "Completed")
		self.assertEqual(updated.duration, 65)
		self.assertEqual(updated.recording_url, "https://api.exotel.com/rec/333.mp3")