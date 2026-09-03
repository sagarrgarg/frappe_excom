"""Webhook handler for WhatsApp Cloud API."""
import datetime
import hashlib
import hmac
import json

import frappe
import requests as http_requests
from werkzeug.wrappers import Response

from excom.excom.utils import get_channel_account
from excom.excom.services.thread_service import (
	ingest_inbound_message,
	update_delivery_status,
)


@frappe.whitelist(allow_guest=True)
def webhook():
	"""Meta webhook entry point."""
	if frappe.request.method == "GET":
		return get()
	return post()


def get():
	"""Verify webhook challenge — multiple accounts may share the same token."""
	hub_challenge = frappe.form_dict.get("hub.challenge")
	verify_token = frappe.form_dict.get("hub.verify_token")

	if not verify_token:
		return Response("Missing verify token", status=403)

	match = frappe.db.exists(
		"Excom Channel Account",
		{"wa_webhook_verify_token": verify_token},
	)
	if not match:
		return Response("No matching account", status=403)

	return Response(hub_challenge, status=200)


def _candidate_secrets() -> list[str]:
	"""App secrets that may sign traffic to this endpoint: WhatsApp accounts + Meta lead-ad sources."""
	out = []
	for acc_name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "status": "Active"}, pluck="name"):
		try:
			secret = frappe.get_doc("Excom Channel Account", acc_name).get_password("wa_app_secret", raise_exception=False)
			if secret:
				out.append(secret)
		except Exception:
			pass
	if frappe.db.exists("DocType", "Excom Intake Source"):
		for src_name in frappe.get_all("Excom Intake Source", filters={"source_type": "Meta Lead Ads", "enabled": 1}, pluck="name"):
			try:
				secret = frappe.get_doc("Excom Intake Source", src_name).get_password("api_secret", raise_exception=False)
				if secret:
					out.append(secret)
			except Exception:
				pass
	return out


def _verify_hmac_signature() -> bool:
	"""Validate X-Hub-Signature-256 against every configured Meta app secret.

	P3 §3.9 S2: once ANY secret is configured, unsigned requests are rejected. Only a site with no
	secret anywhere still accepts unsigned traffic, and every such acceptance is logged.
	"""
	signature_header = frappe.request.headers.get("X-Hub-Signature-256", "")
	secrets = _candidate_secrets()
	if not signature_header:
		if secrets:
			return False
		frappe.log_error(title="Excom: webhook accepted WITHOUT signature (no app secret configured)", message=frappe.request.path)
		return True

	raw_payload = frappe.request.get_data(as_text=False)
	expected_prefix = "sha256="
	if not signature_header.startswith(expected_prefix):
		return False
	provided_sig = signature_header[len(expected_prefix):]
	for secret in secrets:
		expected_sig = hmac.new(secret.encode("utf-8"), raw_payload, hashlib.sha256).hexdigest()
		if hmac.compare_digest(provided_sig, expected_sig):
			return True
	return False


def post():
	"""Accept Meta webhook immediately (return 200) and enqueue processing.

	Validates HMAC signature before processing. Meta requires a response
	within 20 seconds or it marks the webhook as failed and retries.
	"""
	if not _verify_hmac_signature():
		frappe.log_error(
			title="Excom: webhook HMAC validation failed",
			message="Invalid or missing X-Hub-Signature-256 header",
		)
		return Response("Invalid signature", status=403)

	data = frappe.local.form_dict

	try:
		from excom.excom.utils import _notification_log_doctype
		frappe.get_doc({
			"doctype": _notification_log_doctype(),
			"template": "Webhook",
			"meta_data": json.dumps(data),
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error("Excom: webhook audit log insert failed")

	frappe.enqueue(
		"excom.excom.utils.webhook._process_webhook_payload",
		data_str=json.dumps(data),
		queue="short",
		now=frappe.flags.in_test,
	)

	return Response("", status=200)


def _process_webhook_payload(data_str: str):
	"""
	Background job: parse a Meta webhook payload and dispatch each change by `field`
	(P3 §3.7). One Meta URL carries WhatsApp messages, lead ads, IG/Messenger and Page
	comments; a handler exception must never drop the sibling changes.
	"""
	data = json.loads(data_str)
	entries = data.get("entry") or []
	if isinstance(entries, dict):
		entries = [entries]
	for entry in entries:
		for change in entry.get("changes") or []:
			field = change.get("field") or "messages"
			handler = FIELD_HANDLERS.get(field)
			if not handler:
				_log_unhandled(field, change)
				continue
			try:
				handler(change, entry)
			except Exception:
				frappe.log_error(title=f"Excom webhook handler failed: {field}", message=frappe.get_traceback())


def _handle_messages(change: dict, entry: dict) -> None:
	"""Existing WhatsApp path: messages + status updates."""
	value = change.get("value") or {}
	phone_id = (value.get("metadata") or {}).get("phone_number_id")
	channel_account = get_channel_account(phone_id) if phone_id else None
	if not channel_account:
		return
	account_name = channel_account.name
	sender_profile_name = next((c.get("profile", {}).get("name") for c in value.get("contacts", [])), None)
	messages = value.get("messages", [])
	if messages:
		for message in messages:
			_process_inbound_message(message, sender_profile_name, channel_account, account_name)
	else:
		_process_status_update(change)


def _handle_leadgen(change: dict, entry: dict) -> None:
	from excom.excom.intake.adapters.meta import handle_leadgen
	handle_leadgen(change)


def _log_unhandled(field: str, change: dict) -> None:
	"""feed/comments/mentions/messaging arrive here until their handlers exist (UX-001 Comments view)."""
	frappe.log_error(title=f"Excom webhook: unhandled field '{field}'", message=json.dumps(change)[:2000])


FIELD_HANDLERS = {
	"messages": _handle_messages,
	"message_template_status_update": lambda change, entry: _process_status_update(change),
	"leadgen": _handle_leadgen,
}


def _process_inbound_message(message, sender_profile_name, channel_account, account_name):
	"""Route a single inbound message through the service layer."""
	message_type = message.get("type", "text")
	phone = message.get("from", "")
	provider_msg_id = message.get("id", "")
	provider_ts = ""
	if message.get("timestamp"):
		provider_ts = datetime.datetime.fromtimestamp(int(message["timestamp"])).strftime("%Y-%m-%d %H:%M:%S")

	is_reply = bool(message.get("context") and "forwarded" not in message.get("context", {}))
	reply_to_id = message["context"]["id"] if is_reply else ""

	content_text = ""
	content_json = message
	media_file = ""
	excom_msg_type = "Text"

	type_map = {
		"text": "Text",
		"image": "Image",
		"video": "Video",
		"audio": "Audio",
		"document": "Document",
		"sticker": "Sticker",
		"location": "Location",
		"reaction": "Reaction",
		"button": "Button",
		"interactive": "Interactive",
		"contacts": "Contact",
	}
	excom_msg_type = type_map.get(message_type, "Text")

	if message_type == "text":
		content_text = message.get("text", {}).get("body", "")

	elif message_type == "reaction":
		content_text = message.get("reaction", {}).get("emoji", "")
		reply_to_id = message.get("reaction", {}).get("message_id", "")

	elif message_type == "interactive":
		interactive_data = message.get("interactive", {})
		interactive_type = interactive_data.get("type")

		if interactive_type == "button_reply":
			content_text = interactive_data.get("button_reply", {}).get("title", "")
			excom_msg_type = "Button"
		elif interactive_type == "list_reply":
			content_text = interactive_data.get("list_reply", {}).get("title", "")
			excom_msg_type = "Interactive"
		elif interactive_type == "nfm_reply":
			nfm_reply = interactive_data.get("nfm_reply", {})
			try:
				flow_response = json.loads(nfm_reply.get("response_json", "{}"))
			except json.JSONDecodeError:
				flow_response = {}
			parts = [f"{k}: {v}" for k, v in flow_response.items() if v]
			content_text = ", ".join(parts) if parts else "Flow completed"
			content_json = {"interactive": interactive_data, "flow_response": flow_response}
			excom_msg_type = "Flow"

	elif message_type in ("image", "audio", "video", "document", "sticker"):
		content_text = message.get(message_type, {}).get("caption", "")
		media_file = _download_media(message, message_type, channel_account)

	elif message_type == "button":
		content_text = message.get("button", {}).get("text", "")

	else:
		content_text = str(message.get(message_type, {}).get(message_type, ""))

	ingest_inbound_message(
		phone=phone,
		channel="whatsapp",
		account=account_name,
		provider_message_id=provider_msg_id,
		content_text=content_text,
		message_type=excom_msg_type,
		display_name=sender_profile_name or "",
		content_json=content_json,
		media_file=media_file,
		reply_to_provider_id=reply_to_id,
		provider_timestamp=provider_ts,
	)


def _download_media(message, message_type, channel_account):
	"""Download media from WhatsApp and save as Frappe File. Returns file URL."""
	try:
		token = channel_account.get_password("wa_token")
		base_url = f"{channel_account.wa_url}/{channel_account.wa_version}/"
		media_id = message[message_type]["id"]
		headers = {"Authorization": f"Bearer {token}"}

		response = http_requests.get(f"{base_url}{media_id}/", headers=headers)
		if response.status_code != 200:
			return ""

		media_data = response.json()
		media_url = media_data.get("url")
		mime_type = media_data.get("mime_type", "application/octet-stream")
		file_extension = mime_type.split("/")[-1].split(";")[0]

		media_response = http_requests.get(media_url, headers=headers)
		if media_response.status_code != 200:
			return ""

		file_name = f"{frappe.generate_hash(length=10)}.{file_extension}"
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": file_name,
			"content": media_response.content,
			"is_private": 1,
		}).save(ignore_permissions=True)

		return file_doc.file_url
	except Exception:
		frappe.log_error("Excom: media download failed")
		return ""


def _process_status_update(data):
	"""Handle message status and template status webhook events."""
	if data.get("field") == "message_template_status_update":
		_update_template_status(data["value"])
	elif data.get("field") == "messages":
		_update_message_status(data["value"])


def _update_template_status(data):
	"""Update template status and approval lifecycle timestamps from webhook."""
	event = data.get("event", "")
	template_id = data.get("message_template_id")
	if not template_id:
		return

	now = frappe.utils.now_datetime()
	updates = {"status": event}

	lifecycle_map = {
		"APPROVED": "approved_at",
		"REJECTED": "rejected_at",
		"PAUSED": "paused_at",
		"PENDING": "submitted_at",
	}
	ts_field = lifecycle_map.get(event.upper())
	if ts_field:
		updates[ts_field] = now

	reason = data.get("reason") or data.get("rejection_reason") or ""
	if reason and event.upper() == "REJECTED":
		updates["rejection_reason"] = reason

	name = frappe.db.get_value("WhatsApp Templates", {"id": template_id}, "name")
	if name:
		frappe.db.set_value("WhatsApp Templates", name, updates, update_modified=True)


def _update_message_status(data):
	"""Update delivery status on Excom Message, including error info from Meta."""
	status_entry = data.get("statuses", [{}])[0]
	provider_id = status_entry.get("id")
	status = status_entry.get("status")

	if not (provider_id and status):
		return

	failure_reason = ""
	if status == "failed":
		errors = status_entry.get("errors", [])
		if errors:
			err = errors[0]
			code = err.get("code", "")
			title = err.get("title", "")
			detail = err.get("error_data", {}).get("details", "") or err.get("message", "")
			failure_reason = f"[{code}] {title}: {detail}".strip(": ")

	update_delivery_status(provider_id, status, failure_reason=failure_reason)
