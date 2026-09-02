import frappe
from werkzeug.wrappers import Response
from .providers.exotel import ExotelAdapter
from .providers.airtel import AirtelIQAdapter

def get_voice_provider_adapter(account_doc):
	if account_doc.voice_provider == "Exotel":
		return ExotelAdapter(account_doc)
	elif account_doc.voice_provider == "Airtel IQ":
		return AirtelIQAdapter(account_doc)
	return ExotelAdapter(account_doc)

def stream_call_recording(call_id: str):
	"""
	Authenticated Proxy for Call Recording playback.
	Validates user permissions and streams audio bytes from provider.
	Never exposes provider Basic Auth credentials to client browser.
	"""
	if frappe.session.user == "Guest":
		frappe.throw("Authentication required to access call recordings.", frappe.PermissionError)

	if not frappe.db.exists("Excom Call", call_id):
		frappe.throw("Call record not found.", frappe.DoesNotExistError)

	call_doc = frappe.get_doc("Excom Call", call_id)
	if not call_doc.recording_url:
		frappe.throw("No recording available for this call.")

	# Permission verification
	if not frappe.has_permission("Excom Call", "read", call_doc):
		frappe.throw("You do not have permission to play this call recording.", frappe.PermissionError)

	account = frappe.get_doc("Excom Channel Account", call_doc.channel_account)
	adapter = get_voice_provider_adapter(account)
	
	try:
		stream_resp = adapter.fetch_recording_stream(call_doc.recording_url)
		if not stream_resp.ok:
			frappe.throw(f"Provider recording stream failed with status {stream_resp.status_code}")

		return Response(
			stream_resp.iter_content(chunk_size=1024*32),
			content_type=stream_resp.headers.get("Content-Type", "audio/mp3"),
			direct_passthrough=True
		)
	except Exception as e:
		frappe.log_error(f"Error streaming call recording {call_id}: {e}", "Excom Recording Proxy")
		frappe.throw(f"Could not load recording: {e}")