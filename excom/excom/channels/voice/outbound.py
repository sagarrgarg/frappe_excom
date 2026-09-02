import frappe
from .providers.exotel import ExotelAdapter
from .providers.airtel import AirtelIQAdapter

def get_voice_provider_adapter(account_doc):
	if account_doc.voice_provider == "Exotel":
		return ExotelAdapter(account_doc)
	elif account_doc.voice_provider == "Airtel IQ":
		return AirtelIQAdapter(account_doc)
	else:
		return ExotelAdapter(account_doc)

def initiate_click_to_call(to_number: str, account_name: str = None, thread_id: str = None, agent_user: str = None) -> dict:
	"""
	PSTN 2-leg Click-to-Call initiation.
	1. Validates agent mobile number.
	2. Resolves voice channel account.
	3. Triggers Provider API (bridges Agent -> Customer).
	4. Creates Excom Call and Excom Message stub.
	"""
	agent = agent_user or frappe.session.user
	if agent == "Guest":
		frappe.throw("Authentication required to make outbound calls.", frappe.PermissionError)

	agent_mobile = frappe.db.get_value("User", agent, "mobile_no")
	if not agent_mobile:
		frappe.throw(f"Your User profile ({agent}) does not have a registered Mobile Number. Please update Mobile No in User settings.")

	# Resolve account
	if account_name and frappe.db.exists("Excom Channel Account", account_name):
		account = frappe.get_doc("Excom Channel Account", account_name)
	else:
		accs = frappe.get_all("Excom Channel Account", filters={"channel": "voice", "status": "Active"}, limit=1)
		if not accs:
			frappe.throw("No active Voice Channel Account found in Excom Settings.")
		account = frappe.get_doc("Excom Channel Account", accs[0].name)

	# Provider Adapter
	adapter = get_voice_provider_adapter(account)
	call_ref = adapter.initiate_call(
		from_number=agent_mobile.strip(),
		to_number=to_number.strip(),
		caller_id=account.voice_phone_number
	)

	# Create Excom Call document
	call_doc = frappe.new_doc("Excom Call")
	call_doc.provider_call_id = call_ref.provider_call_id
	call_doc.direction = "Outbound"
	call_doc.status = "Ringing"
	call_doc.from_number = agent_mobile.strip()
	call_doc.to_number = to_number.strip()
	call_doc.business_number = account.voice_phone_number
	call_doc.agent = agent
	call_doc.channel_account = account.name
	if not thread_id or not frappe.db.exists("Excom Thread", thread_id):
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
		from excom.excom.services.thread_service import upsert_thread
		clean_to = to_number.strip()
		identity_name = resolve_identity(phone=clean_to, channel="voice", display_name=clean_to)
		thread_id = upsert_thread(identity_name, "voice", account.name)

	call_doc.thread = thread_id
	call_doc.omni_identity = frappe.db.get_value("Excom Thread", thread_id, "omni_identity")

	call_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Create Timeline Message Stub if thread linked
	if call_doc.thread:
		try:
			msg = frappe.new_doc("Excom Message")
			msg.thread = call_doc.thread
			msg.omni_identity = call_doc.omni_identity
			msg.direction = "Outbound"
			msg.channel = "voice"
			msg.account_doctype = "Excom Channel Account"
			msg.account = account.name
			msg.message_type = "Call"
			msg.content_text = f"Outbound call initiated to {to_number.strip()}"
			msg.delivery_status = "Sent"
			msg.insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(f"Error creating Excom Message call stub: {e}", "Excom Voice")

	# Emit realtime update
	frappe.publish_realtime(
		"excom_call_status_update",
		{
			"call_id": call_doc.name,
			"provider_call_id": call_doc.provider_call_id,
			"status": "Ringing",
			"direction": "Outbound",
			"to_number": to_number,
			"agent": agent
		},
		user=agent
	)

	return {
		"status": "success",
		"call_id": call_doc.name,
		"provider_call_id": call_doc.provider_call_id,
		"message": "Call initiated. Your phone will ring first."
	}