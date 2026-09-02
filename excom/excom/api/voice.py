import json
import frappe
from frappe import _
from excom.excom.channels.voice.handler import handle_inbound_routing, handle_status_webhook
from excom.excom.channels.voice.outbound import initiate_click_to_call
from excom.excom.channels.voice.recording import stream_call_recording

@frappe.whitelist(allow_guest=True)
def inbound_routing(**kwargs):
	"""
	Exotel Connect Dynamic URL GET Endpoint.
	Called by Exotel mid-call to fetch destination numbers.
	Root-level JSON formatting per Exotel Passthrough specification.
	"""
	params = frappe.local.form_dict or kwargs
	response = handle_inbound_routing(params)
	
	# Populate root-level keys on frappe.response so Exotel receives raw JSON without wrapper nesting
	frappe.response.clear()
	frappe.response["type"] = "json"
	for k, v in response.items():
		frappe.response[k] = v

	# Add select_data string for Exotel Passthrough applet backward compatibility
	dest_numbers = response.get("destination", {}).get("numbers") or []
	if dest_numbers:
		frappe.response["select_data"] = ",".join(dest_numbers)

	return

@frappe.whitelist(allow_guest=True)
def status_webhook(**kwargs):
	"""
	Provider Call Status Callback Webhook.
	Receives end-of-call status, duration, and recording URL.
	"""
	params = frappe.local.form_dict or kwargs
	token = params.get("token")
	account_name = params.get("account")
	
	# Verify webhook token if account specified
	if account_name and frappe.db.exists("Excom Channel Account", account_name):
		expected_token = frappe.db.get_value("Excom Channel Account", account_name, "voice_webhook_token")
		if expected_token and token != expected_token:
			frappe.throw(_("Invalid webhook verify token"), frappe.PermissionError)

	return handle_status_webhook(params, account_name=account_name)

@frappe.whitelist()
def initiate_call(to_number: str, account_name: str = None, thread_id: str = None):
	"""
	Initiates an outbound Click-to-Call (PSTN bridge).
	Rings the agent's mobile first, then bridges to the customer.
	"""
	return initiate_click_to_call(
		to_number=to_number,
		account_name=account_name,
		thread_id=thread_id,
		agent_user=frappe.session.user
	)

@frappe.whitelist()
def get_recording(call_id: str):
	"""
	Authenticated Audio Streaming Proxy.
	Streams the recording bytes without exposing Basic Auth credentials to the browser.
	"""
	return stream_call_recording(call_id)

@frappe.whitelist()
def get_active_call():
	"""
	Returns currently active/ringing call for the logged-in agent.
	"""
	user = frappe.session.user
	if user == "Guest":
		return None

	active_calls = frappe.get_all(
		"Excom Call",
		filters={
			"agent": user,
			"status": ["in", ["Ringing", "In-progress"]]
		},
		fields=["name", "provider_call_id", "direction", "status", "from_number", "to_number", "business_number", "thread", "omni_identity", "creation"],
		order_by="creation desc",
		limit=1
	)
	return active_calls[0] if active_calls else None

@frappe.whitelist()
def get_call_history(thread_id: str = None, omni_identity: str = None, limit: int = 20):
	"""
	Fetches call history for a thread or omni identity.
	"""
	filters = {}
	if thread_id:
		filters["thread"] = thread_id
	elif omni_identity:
		filters["omni_identity"] = omni_identity

	calls = frappe.get_all(
		"Excom Call",
		filters=filters,
		fields=["name", "provider_call_id", "direction", "status", "from_number", "to_number", "business_number", "agent", "duration", "talk_time", "cost", "recording_url", "creation"],
		order_by="creation desc",
		limit=limit
	)
	return calls