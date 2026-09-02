import re
import json
import frappe
from frappe.utils import now_datetime
from .routing import resolve_voice_account, resolve_ring_destination, build_exotel_routing_response
from .providers.exotel import ExotelAdapter

def handle_inbound_routing(params: dict):
	"""
	Inbound Dynamic URL endpoint (<5s execution budget).
	1. Resolves account & caller identity.
	2. Resolves destination ring list (Sticky-then-team).
	3. Emits realtime incoming call event for desk screen pop to all logged-in agent sockets.
	4. Returns JSON destination payload immediately.
	5. Enqueues background persistence of call record.
	"""
	# Check if this request is actually an end-of-call status callback
	is_end_of_call = (
		params.get("DialCallStatus") in ["completed", "busy", "no-answer", "failed", "canceled", "cancelled"]
		or params.get("CallStatus") in ["completed", "busy", "no-answer", "failed", "canceled", "cancelled"]
		or params.get("RecordingUrl")
		or (params.get("CallType") and params.get("CallType") != "call-attempt")
		or int(params.get("DialCallDuration") or 0) > 0
	)
	if is_end_of_call:
		return handle_status_webhook(params)

	caller = params.get("CallFrom") or params.get("From") or ""
	business_num = params.get("CallTo") or params.get("To") or ""
	call_sid = params.get("CallSid") or params.get("Sid") or ""
	digits = params.get("digits") or ""

	account = resolve_voice_account(business_num)
	ring_numbers = resolve_ring_destination(caller, business_num, account) if account else []
	response_payload = build_exotel_routing_response(ring_numbers, business_num, account)

	# Publish realtime screen pop to ALL enabled system user browser rooms
	active_users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, fields=["name"])
	for u in active_users:
		frappe.publish_realtime(
			"excom_incoming_call",
			{
				"provider_call_id": call_sid,
				"from_number": caller,
				"business_number": business_num,
				"account": account.name if account else None,
				"ring_numbers": ring_numbers
			},
			user=u.name,
			after_commit=False
		)
		frappe.publish_realtime(
			"excom:incoming_call",
			{
				"provider_call_id": call_sid,
				"from_number": caller,
				"business_number": business_num,
				"account": account.name if account else None,
				"ring_numbers": ring_numbers
			},
			user=u.name,
			after_commit=False
		)
	# Also broadcast without user filter
	frappe.publish_realtime(
		"excom_incoming_call",
		{
			"provider_call_id": call_sid,
			"from_number": caller,
			"business_number": business_num,
			"account": account.name if account else None,
			"ring_numbers": ring_numbers
		},
		after_commit=False
	)

	# Enqueue asynchronous creation of Excom Call & Thread Message stub
	frappe.enqueue(
		"excom.excom.channels.voice.handler.persist_inbound_call_async",
		call_sid=call_sid,
		caller=caller,
		business_num=business_num,
		account_name=account.name if account else None,
		digits=digits,
		raw_params=params,
		queue="short"
	)

	return response_payload

def persist_inbound_call_async(call_sid, caller, business_num, account_name, digits, raw_params):
	"""Asynchronous persistence of inbound call record & Excom Message thread stub."""
	frappe.set_user("Administrator")
	frappe.flags.ignore_permissions = True
	if not call_sid or frappe.db.exists("Excom Call", {"provider_call_id": call_sid}):
		return

	from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
	from excom.excom.services.thread_service import upsert_thread

	if not account_name and business_num:
		acc_doc = resolve_voice_account(business_num)
		account_name = acc_doc.name if acc_doc else None

	clean_caller = (caller or "").strip()
	identity_name = resolve_identity(phone=clean_caller, channel="voice", display_name=clean_caller)
	thread_name = upsert_thread(identity_name, "voice", account_name) if account_name else None

	call_doc = frappe.new_doc("Excom Call")
	call_doc.provider_call_id = call_sid
	call_doc.direction = "Inbound"
	call_doc.status = "Ringing"
	call_doc.from_number = caller
	call_doc.to_number = business_num
	call_doc.business_number = business_num
	call_doc.channel_account = account_name
	call_doc.omni_identity = identity_name
	call_doc.thread = thread_name
	call_doc.ivr_selection = digits
	call_doc.raw_event_payload = json.dumps(raw_params)
	call_doc.insert(ignore_permissions=True)

	if thread_name:
		try:
			msg = frappe.new_doc("Excom Message")
			msg.thread = thread_name
			msg.omni_identity = identity_name
			msg.direction = "Inbound"
			msg.channel = "voice"
			msg.account_doctype = "Excom Channel Account"
			msg.account = account_name
			msg.message_type = "Call"
			msg.content_text = f"Incoming Call from {caller}"
			if frappe.db.has_column("Excom Message", "excom_call"):
				msg.excom_call = call_doc.name
			msg.delivery_status = "Delivered"
			msg.provider_timestamp = now_datetime()
			msg.insert(ignore_permissions=True)

			frappe.db.set_value("Excom Thread", thread_name, {
				"last_message_preview": f"Incoming Call from {caller}",
				"last_message_at": now_datetime()
			})
		except Exception as e:
			frappe.log_error(f"Failed creating inbound Excom Message stub: {e}", "Excom Voice")

	frappe.db.commit()

def handle_status_webhook(params: dict, account_name: str = None):
	"""Status callback webhook processor."""
	frappe.set_user("Administrator")
	frappe.flags.ignore_permissions = True
	call_sid = params.get("CallSid") or params.get("Sid")
	if not call_sid:
		return {"status": "ignored", "reason": "No CallSid"}

	caller = params.get("CallFrom") or params.get("From") or ""
	business_num = params.get("CallTo") or params.get("To") or ""
	digits = params.get("digits") or ""

	if not account_name and business_num:
		acc_doc = resolve_voice_account(business_num)
		account_name = acc_doc.name if acc_doc else None

	call_id = frappe.db.get_value("Excom Call", {"provider_call_id": call_sid}, "name")
	if not call_id:
		persist_inbound_call_async(call_sid, caller, business_num, account_name, digits, params)
		call_id = frappe.db.get_value("Excom Call", {"provider_call_id": call_sid}, "name")

	status_raw = (params.get("DialCallStatus") or params.get("Status") or params.get("CallStatus") or "completed").lower()
	status_map = {
		"completed": "Completed",
		"busy": "Busy",
		"no-answer": "Missed",
		"failed": "Failed",
		"canceled": "Missed",
		"cancelled": "Missed"
	}
	new_status = status_map.get(status_raw, "Completed")
	duration = int(params.get("DialCallDuration") or params.get("Duration") or params.get("Legs[0][Duration]") or 0)
	rec_url = params.get("RecordingUrl") or params.get("Legs[0][RecordingUrl]")

	dial_whom = params.get("DialWhomNumber") or params.get("DialWhom") or params.get("AgentPhoneNumber") or ""
	if duration == 0 and not dial_whom and new_status == "Completed":
		new_status = "Missed"

	update_fields = {"status": new_status}
	if duration:
		update_fields["duration"] = duration
		update_fields["talk_time"] = duration
	if rec_url:
		update_fields["recording_url"] = rec_url

	# Resolve agent & team from DialWhomNumber if present
	if dial_whom:
		digits_whom = re.sub(r"\D", "", str(dial_whom))
		if len(digits_whom) >= 10:
			clean_digits = digits_whom[-10:]
			matched_users = frappe.get_all("User", filters=[["mobile_no", "like", f"%{clean_digits}%"]], fields=["name"])
			if not matched_users:
				matched_users = frappe.get_all("User", filters=[["phone", "like", f"%{clean_digits}%"]], fields=["name"])
			if matched_users:
				agent_user = matched_users[0].name
				update_fields["agent"] = agent_user
				team_member = frappe.db.get_value("Excom Team Member", {"user": agent_user}, "parent")
				if team_member:
					update_fields["team"] = team_member

	if call_id:
		thread_id = frappe.db.get_value("Excom Call", call_id, "thread")
		if not thread_id:
			from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
			from excom.excom.services.thread_service import upsert_thread
			caller_num = caller or frappe.db.get_value("Excom Call", call_id, "from_number")
			acc_num = business_num or frappe.db.get_value("Excom Call", call_id, "business_number")
			if caller_num and acc_num:
				id_name = resolve_identity(phone=caller_num, channel="voice", display_name=caller_num)
				acc = resolve_voice_account(acc_num)
				if acc:
					thread_id = upsert_thread(id_name, "voice", acc.name)
					update_fields["thread"] = thread_id
					update_fields["omni_identity"] = id_name
					update_fields["channel_account"] = acc.name

		frappe.db.set_value("Excom Call", call_id, update_fields)

		# Assign thread to agent if agent resolved
		if update_fields.get("agent") and thread_id:
			frappe.db.set_value("Excom Thread", thread_id, "assigned_to", update_fields["agent"])

		# Safe update of Excom Message timeline stub content
		try:
			msg_name = None
			if frappe.db.has_column("Excom Message", "excom_call"):
				msg_name = frappe.db.get_value("Excom Message", {"excom_call": call_id}, "name")
			if not msg_name and thread_id:
				msg_name = frappe.db.get_value("Excom Message", {"thread": thread_id, "message_type": "Call"}, "name", order_by="creation desc")

			if msg_name:
				if new_status == "Completed":
					txt = f"Call completed ({duration}s)" if duration else "Call completed"
				elif new_status in ["Missed", "no-answer"]:
					txt = f"Missed Call from {caller or 'customer'}"
				elif new_status == "Busy":
					txt = f"Call busy from {caller or 'customer'}"
				else:
					txt = f"Call {new_status.lower()}"

				frappe.db.set_value("Excom Message", msg_name, {
					"content_text": txt,
					"delivery_status": "Delivered" if new_status == "Completed" else "Failed"
				})

				if thread_id:
					frappe.db.set_value("Excom Thread", thread_id, {
						"last_message_preview": txt,
						"last_message_at": now_datetime()
					})
		except Exception as e:
			frappe.log_error(f"Error updating Excom Message in status webhook: {e}", "Excom Voice Webhook")

		frappe.db.commit()

		# Emit realtime status update & thread list refresh to all user sockets
		active_users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, fields=["name"])
		for u in active_users:
			frappe.publish_realtime(
				"excom_call_status_update",
				{
					"call_id": call_id,
					"provider_call_id": call_sid,
					"status": new_status,
					"duration": duration,
					"thread_id": thread_id
				},
				user=u.name,
				after_commit=False
			)
			if thread_id:
				frappe.publish_realtime(
					"excom_thread_updated",
					{"thread_id": thread_id},
					user=u.name,
					after_commit=False
				)
				frappe.publish_realtime(
					"excom:thread_updated",
					{"thread_id": thread_id},
					user=u.name,
					after_commit=False
				)

		# Broadcast to all sockets
		frappe.publish_realtime(
			"excom_call_status_update",
			{
				"call_id": call_id,
				"provider_call_id": call_sid,
				"status": new_status,
				"duration": duration,
				"thread_id": thread_id
			},
			after_commit=False
		)
		if thread_id:
			frappe.publish_realtime("excom_thread_updated", {"thread_id": thread_id}, after_commit=False)
			frappe.publish_realtime("excom:thread_updated", {"thread_id": thread_id}, after_commit=False)

	return {"status": "updated", "call_id": call_id}
