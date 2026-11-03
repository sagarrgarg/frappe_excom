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
from excom.excom.utils.graph import graph_version


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

	# all asterisks would match the masked column of a Password field
	if not verify_token or set(verify_token) == {"*"}:
		return Response("Missing verify token", status=403)

	match = frappe.db.exists("Excom Channel Account", {"wa_webhook_verify_token": verify_token}) or (
		frappe.db.exists("DocType", "Excom Meta Connection") and frappe.db.exists("Excom Meta Connection", {"webhook_verify_token": verify_token})
	) or _account_verify_token_matches(verify_token)
	if not match:
		return Response("No matching account", status=403)

	return Response(hub_challenge, status=200)


def _account_verify_token_matches(verify_token: str) -> bool:
	"""The account's token is a Password field (the column only holds asterisks), so compare decrypted values.

	Needed for the webhook overrides (services/whatsapp_account.py): Meta checks the override URL
	with the account's token before saving it.
	"""
	from frappe.utils.password import get_decrypted_password

	for name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp"}, pluck="name"):
		stored = get_decrypted_password("Excom Channel Account", name, "wa_webhook_verify_token", raise_exception=False) or ""
		if stored and hmac.compare_digest(stored.encode(), verify_token.encode()):
			return True
	return False


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
	try:
		from excom.excom.services.meta_connect import app_secrets
		out.extend(app_secrets())
	except Exception:
		pass
	# a secret that cannot be decrypted (e.g. a restored backup with another encryption key) must not leak a msgprint
	frappe.local.message_log = []
	if frappe.db.exists("DocType", "Excom Source"):
		for src_name in frappe.get_all("Excom Source", filters={"source_type": "Meta Lead Ads", "enabled": 1}, pluck="name"):
			try:
				secret = frappe.get_doc("Excom Source", src_name).get_password("api_secret", raise_exception=False)
				if secret:
					out.append(secret)
			except Exception:
				pass
	frappe.local.message_log = []
	return out


def _verify_hmac_signature() -> bool:
	"""Validate X-Hub-Signature-256 against every configured Meta app secret.

	P3 §3.9 S2: once ANY secret is configured, unsigned requests are rejected. Only a site with no
	secret anywhere still accepts unsigned traffic, and every such acceptance is logged. Such a
	payload is processed as *unsigned*: it may store messages, but it may not change consent or
	move an identity (see _signed()).
	"""
	signature_header = frappe.request.headers.get("X-Hub-Signature-256", "")
	secrets = _candidate_secrets()
	frappe.flags.excom_webhook_signed = False
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
			frappe.flags.excom_webhook_signed = True
			return True
	return False


def _signed() -> bool:
	"""True when the payload being processed carried a valid Meta signature.

	Consent changes (Meta preferences, 131050, STOP/START) and identity moves (number / user id
	changes) are only applied from signed payloads: without a secret anyone could POST them.
	"""
	return bool(frappe.flags.excom_webhook_signed)


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
		signed=bool(frappe.flags.excom_webhook_signed),
		queue="short",
		now=frappe.flags.in_test,
	)

	return Response("", status=200)


def _process_webhook_payload(data_str: str, signed: bool = True):
	"""
	Background job: parse a Meta webhook payload and dispatch each change by `field`
	(P3 §3.7). One Meta URL carries WhatsApp messages, lead ads, IG/Messenger and Page
	comments; a handler exception must never drop the sibling changes.

	`signed` is False only for a payload accepted without a signature (no app secret anywhere).
	"""
	frappe.flags.excom_webhook_signed = signed
	data = json.loads(data_str)
	entries = data.get("entry") or []
	if isinstance(entries, dict):
		entries = [entries]
	obj = data.get("object") or ""
	for entry in entries:
		if entry.get("messaging"):
			try:
				from excom.excom.channels.meta_dm.service import handle_messaging
				handle_messaging(entry, obj)
			except Exception:
				frappe.log_error(title="Excom webhook handler failed: messaging", message=frappe.get_traceback())
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
	"""WhatsApp messages + status updates for one phone number."""
	value = change.get("value") or {}
	phone_id = (value.get("metadata") or {}).get("phone_number_id")
	channel_account = get_channel_account(phone_id) if phone_id else None
	if not channel_account:
		return
	account_name = channel_account.name
	contacts = value.get("contacts") or []
	for message in value.get("messages") or []:
		contact = _contact_for(message, contacts)
		if message.get("type") == "system":
			_handle_system_message(message, contact, channel_account)
			if _signed():
				_dispatch_system(message, channel_account)
			continue
		_process_inbound_message(message, (contact.get("profile") or {}).get("name"), channel_account, account_name, contact=contact)
	if value.get("statuses"):
		_update_message_status(value)


def _contact_for(message: dict, contacts: list) -> dict:
	"""The `contacts[]` entry for a message: matched by business-scoped user id, then by number.

	Since 2026 a contact carries `user_id` (BSUID) always, `wa_id` only while the number is
	visible, and `username` when the person has one.
	"""
	uid = message.get("from_user_id") or ""
	frm = message.get("from") or ""
	for c in contacts:
		if uid and c.get("user_id") == uid:
			return c
	for c in contacts:
		if frm and c.get("wa_id") == frm:
			return c
	return contacts[0] if len(contacts) == 1 else {}


def _whatsapp_user(message: dict, contact: dict) -> tuple[str, str, str]:
	"""(phone, business-scoped user id, username) for an inbound message — any may be empty."""
	phone = message.get("from") or contact.get("wa_id") or ""
	user_id = message.get("from_user_id") or contact.get("user_id") or ""
	username = contact.get("username") or (contact.get("profile") or {}).get("username") or ""
	return phone, user_id, username


def _handle_system_message(message: dict, contact: dict, channel_account) -> None:
	"""user_changed_number / user_changed_user_id: keep the person on the same identity."""
	system = message.get("system") or {}
	kind = system.get("type") or ""
	if kind not in ("user_changed_number", "user_changed_user_id", "customer_changed_number", "customer_identity_changed"):
		_record_event("messages", f"system:{kind or 'unknown'}", "Info", message, channel_account=channel_account)
		return
	if not _signed():
		_record_event("messages", f"system:{kind} (unsigned, ignored)", "Warning", message, channel_account=channel_account)
		return
	from excom.excom.doctype.omni_identity.omni_identity import relink_whatsapp_user

	previous_user_id = system.get("previous_user_id") or message.get("from_user_id") or contact.get("user_id") or ""
	new_user_id = system.get("user_id") or system.get("new_user_id") or ""
	new_phone = system.get("wa_id") or system.get("new_wa_id") or ""
	identity = relink_whatsapp_user(previous_user_id=previous_user_id, new_user_id=new_user_id, new_phone=new_phone, old_phone=message.get("from") or "", wa_waba=channel_account.get("wa_business_id") or "")
	_record_event(
		"messages", f"system:{kind}", "Info", message, channel_account=channel_account,
		# Meta's `body` spells out the old and new numbers; the event keeps the identity link only
		subject=identity or "", summary="relinked" if identity else "no matching identity",
	)


def _handle_leadgen(change: dict, entry: dict) -> None:
	from excom.excom.intake.adapters.meta import handle_leadgen
	handle_leadgen(change)


def _log_unhandled(field: str, change: dict) -> None:
	"""feed/comments/mentions/messaging arrive here until their handlers exist (UX-001 Comments view)."""
	frappe.log_error(title=f"Excom webhook: unhandled field '{field}'", message=json.dumps(change)[:2000])


# ─── account / template / preference events ──────────────────────────────────

# Severity from Meta's event words, matched per word (so "REGISTERED" is not "RED").
_CRITICAL_STEMS = ("RESTRICT", "VIOLAT", "DISABL", "DELET", "REMOV", "LOCK", "FLAG", "SUSPEND")
_CRITICAL_WORDS = {"BAN", "BANNED"}
_WARNING_STEMS = ("DOWNGRAD", "PAUS", "WARN", "DEFER", "DENI", "INCORRECT", "REJECT")
_WARNING_WORDS = {"RED", "LOW", "YELLOW"}


def _severity(*values) -> str:
	import re as _re

	words = [w for v in values for w in _re.split(r"[^A-Z]+", str(v or "").upper()) if w]
	if any(w in _CRITICAL_WORDS or w.startswith(_CRITICAL_STEMS) for w in words):
		return "Critical"
	if any(w in _WARNING_WORDS or w.startswith(_WARNING_STEMS) for w in words):
		return "Warning"
	return "Info"


def _accounts_for(entry: dict, value: dict) -> list:
	"""Channel accounts an event is about.

	A phone number id or display number in the event picks that number; otherwise every number
	of the WhatsApp Business Account — taken from value.waba_info.waba_id (account_update),
	value.waba_id, or entry.id (which is the WABA for most fields, but not for account_update).
	"""
	phone_id = (value.get("metadata") or {}).get("phone_number_id") or value.get("phone_number_id")
	if phone_id:
		name = frappe.db.get_value("Excom Channel Account", {"wa_phone_id": phone_id}, "name")
		if name:
			return [name]
	display = "".join(ch for ch in str(value.get("display_phone_number") or "") if ch.isdigit())
	if display:
		for row in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp"}, fields=["name", "wa_display_phone"]):
			if "".join(ch for ch in (row.wa_display_phone or "") if ch.isdigit()) == display:
				return [row.name]
	waba_id = (value.get("waba_info") or {}).get("waba_id") or value.get("waba_id") or entry.get("id") or ""
	if not waba_id:
		return []
	return frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "wa_business_id": str(waba_id)}, pluck="name")


_PII_KEYS = {"wa_id", "user_id", "parent_user_id", "previous_user_id", "from", "from_user_id", "recipient_id",
	"recipient_user_id", "body", "profile", "contacts", "username", "input", "new_wa_id", "new_user_id", "detail"}


def _scrub(obj):
	"""Payload copy without personal identifiers (DPDP data minimisation): the identity link in
	`subject` is what admins need; numbers, user ids and message text are not kept in the event log."""
	if isinstance(obj, dict):
		return {k: ("…" if k in _PII_KEYS else _scrub(v)) for k, v in obj.items()}
	if isinstance(obj, list):
		return [_scrub(v) for v in obj]
	return obj


def _record_event(field: str, event: str, severity: str, payload, channel_account=None, waba_id: str = "", subject: str = "", summary: str = "") -> None:
	"""Store the event, keep the latest on the account, tell admins about critical ones."""
	try:
		account = getattr(channel_account, "name", None) or channel_account or None
		if frappe.db.exists("DocType", "Excom WhatsApp Event"):
			frappe.get_doc({
				"doctype": "Excom WhatsApp Event",
				"received_on": frappe.utils.now_datetime(),
				"field": field,
				"event": (event or "")[:140],
				"severity": severity,
				"channel_account": account,
				"waba_id": waba_id or "",
				"subject": (subject or "")[:140],
				"summary": (summary or "")[:1000],
				"payload": json.dumps(_scrub(payload))[:60000],
			}).insert(ignore_permissions=True)
		if account and severity != "Info":
			frappe.db.set_value("Excom Channel Account", account, "wa_last_platform_event",
				f"{frappe.utils.now_datetime():%Y-%m-%d %H:%M} {field} {event}: {summary}"[:500], update_modified=False)
		if severity == "Critical" and _signed():
			_alert_admins(f"WhatsApp: {field} {event}", summary or json.dumps(_scrub(payload))[:500], account)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title=f"Excom webhook: could not record {field} event", message=frappe.get_traceback())


def _alert_admins(subject: str, message: str, account=None) -> None:
	users = set(frappe.get_all("Has Role", filters={"role": ["in", ["System Manager", "Excom Admin"]], "parenttype": "User"}, pluck="parent"))
	users.discard("Guest")
	users.discard("Administrator")
	since = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-6)
	for user in users:
		if not frappe.db.get_value("User", user, "enabled"):
			continue
		if frappe.db.exists("Notification Log", {"for_user": user, "subject": subject[:140], "creation": [">", since]}):
			continue  # the same alert within 6 h: one notification is enough
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"subject": subject[:140],
			"email_content": message,
			"document_type": "Excom Channel Account" if account else None,
			"document_name": account or None,
		}).insert(ignore_permissions=True)


def _handle_template_status(change: dict, entry: dict) -> None:
	value = change.get("value") or {}
	_process_status_update(change)
	event = value.get("event") or ""
	category = (value.get("message_template_category") or "").upper()
	if category and value.get("message_template_id"):
		name = frappe.db.get_value("WhatsApp Templates", {"id": str(value["message_template_id"])}, "name")
		if name:
			frappe.db.set_value("WhatsApp Templates", name, "category", category, update_modified=False)
	_record_event(change.get("field"), event, _severity(event, value.get("reason")), value, waba_id=entry.get("id") or "",
		subject=value.get("message_template_name") or str(value.get("message_template_id") or ""), summary=value.get("reason") or "")


def _handle_template_category(change: dict, entry: dict) -> None:
	"""Meta will move (advance notice) or has moved a template to another category.

	Advance notice (24 h before): `new_category` is still the *current* category and
	`correct_category` the one coming. Completed: `previous_category` and `new_category`.
	The stored category decides the endpoint (/marketing_messages for MARKETING) and whether
	marketing opt-outs apply, so it changes only on the completed notice.
	"""
	value = change.get("value") or {}
	template_id = value.get("message_template_id")
	subject = value.get("message_template_name") or str(template_id or "")
	if "correct_category" in value:
		coming = (value.get("correct_category") or "").upper()
		_record_event("template_category_update", f"{(value.get('new_category') or '?').upper()} → {coming or '?'} (notice)", "Warning", value,
			waba_id=str(entry.get("id") or ""), subject=subject,
			summary=f"Meta will recategorise this template to {coming} at {value.get('category_update_timestamp') or 'the stated time'}")
		return
	new_category = (value.get("new_category") or "").upper()
	if new_category and template_id:
		name = frappe.db.get_value("WhatsApp Templates", {"id": str(template_id)}, "name")
		if name:
			frappe.db.set_value("WhatsApp Templates", name, "category", new_category, update_modified=True)
	_record_event("template_category_update", f"{(value.get('previous_category') or '?').upper()} → {new_category or '?'}",
		"Warning" if new_category == "MARKETING" else "Info", value, waba_id=str(entry.get("id") or ""), subject=subject,
		summary=f"Template is now {new_category}")


def _handle_template_quality(change: dict, entry: dict) -> None:
	value = change.get("value") or {}
	score = (value.get("new_quality_score") or "").upper()
	template_id = value.get("message_template_id")
	if score and template_id:
		name = frappe.db.get_value("WhatsApp Templates", {"id": str(template_id)}, "name")
		if name:
			frappe.db.set_value("WhatsApp Templates", name, "quality_score", score, update_modified=False)
	_record_event("message_template_quality_update", f"{value.get('previous_quality_score') or '?'} → {score or '?'}",
		_severity(score), value, waba_id=entry.get("id") or "", subject=value.get("message_template_name") or str(template_id or ""))


def _handle_phone_quality(change: dict, entry: dict) -> None:
	"""ONBOARDING / THROUGHPUT_UPGRADE and limit changes for one number (matched by display number)."""
	value = change.get("value") or {}
	event = value.get("event") or ""
	limit = value.get("max_daily_conversations_per_business") or value.get("current_limit") or value.get("messaging_limit_tier")
	accounts = _accounts_for(entry, value)
	for account in accounts:
		updates = {}
		if limit:
			updates["wa_messaging_limit"] = str(limit)[:140]
		if event == "THROUGHPUT_UPGRADE":
			updates["wa_throughput"] = "HIGH"
		if updates:
			frappe.db.set_value("Excom Channel Account", account, updates, update_modified=False)
		_record_event("phone_number_quality_update", event, _severity(event), value, channel_account=account,
			waba_id=str(entry.get("id") or ""), subject=value.get("display_phone_number") or "", summary=f"limit {limit}" if limit else "")
	if not accounts:
		_record_event("phone_number_quality_update", event, _severity(event), value, waba_id=str(entry.get("id") or ""))


def _handle_capability(change: dict, entry: dict) -> None:
	value = change.get("value") or {}
	limit = value.get("max_daily_conversations_per_business") or value.get("max_daily_conversation_per_phone") or value.get("max_daily_conversations_per_phone")
	for account in _accounts_for(entry, value):
		if limit:
			frappe.db.set_value("Excom Channel Account", account, "wa_messaging_limit", str(limit)[:140], update_modified=False)
	_record_event("business_capability_update", f"limit {limit}" if limit else "capability", "Info", value, waba_id=entry.get("id") or "")


_ACCOUNT_CRITICAL = {"ACCOUNT_VIOLATION", "ACCOUNT_RESTRICTION", "ACCOUNT_DELETED", "ACCOUNT_OFFBOARDED", "PARTNER_REMOVED", "DISABLED_UPDATE"}


def _handle_account_update(change: dict, entry: dict) -> None:
	value = change.get("value") or {}
	event = value.get("event") or ""
	waba_id = str((value.get("waba_info") or {}).get("waba_id") or value.get("waba_id") or entry.get("id") or "")
	if event == "MM_LITE_TERMS_SIGNED" and _signed():
		for account in _accounts_for(entry, value):
			frappe.db.set_value("Excom Channel Account", account, "wa_marketing_terms_signed", 1, update_modified=False)
	restrictions = value.get("restriction_info") if isinstance(value.get("restriction_info"), list) else []
	ban_state = (value.get("ban_info") or {}).get("waba_ban_state") or ""
	reason = ban_state or (restrictions[0].get("restriction_type") if restrictions else "") or value.get("reason") or ""
	if event == "DISABLED_UPDATE" and str(ban_state).upper() == "REINSTATE":
		severity = "Info"  # the account is back
	elif event in _ACCOUNT_CRITICAL:
		severity = "Critical"
	else:
		severity = _severity(event, reason)
	_record_event("account_update", event, severity, value, waba_id=waba_id, summary=str(reason))


def _handle_account_alert(change: dict, entry: dict) -> None:
	"""account_alerts: the alert sits in value.alert_info (severity CRITICAL / WARNING / INFORMATIONAL)."""
	value = change.get("value") or {}
	info = value.get("alert_info") or {}
	level = (info.get("alert_severity") or "").upper()
	severity = {"CRITICAL": "Critical", "WARNING": "Warning", "INFORMATIONAL": "Info"}.get(level) or _severity(info.get("alert_type"))
	_record_event("account_alerts", info.get("alert_type") or "alert", severity, value, waba_id=str(entry.get("id") or ""),
		subject=str(value.get("entity_id") or ""), summary=info.get("alert_description") or info.get("alert_status") or "")


def _handle_user_preferences(change: dict, entry: dict) -> None:
	"""Meta 'Stop promotions' / 'Resume' from the WhatsApp app: marketing consent for that person.

	Only `marketing_messages` exists today; service and utility messages are unaffected. The person
	is resolved (or created, so an opt-out is honoured even before they are imported). Preferences
	are per business, so the opt-out carries the WhatsApp account and a resume only lifts an
	opt-out that came from the same account. Applied from signed payloads only.
	"""
	value = change.get("value") or {}
	phone_id = (value.get("metadata") or {}).get("phone_number_id")
	channel_account = get_channel_account(phone_id) if phone_id else None
	if not _signed():
		_record_event("user_preferences", "unsigned, ignored", "Warning", value, channel_account=channel_account)
		return
	from excom.excom.doctype.omni_identity.omni_identity import resolve_identity_internal
	from excom.excom.services.consent import clear_marketing_opt_out, set_marketing_opt_out

	waba = channel_account.get("wa_business_id") if channel_account else ""
	for pref in value.get("user_preferences") or []:
		try:
			if (pref.get("category") or "marketing_messages") != "marketing_messages":
				continue
			phone, user_id = pref.get("wa_id") or "", pref.get("user_id") or ""
			if not phone and not user_id:
				continue
			identity = resolve_identity_internal(phone=phone, channel="whatsapp", channel_user_id=phone or user_id, wa_user_id=user_id, wa_waba=waba or "")
			choice = (pref.get("value") or "").lower()
			if choice == "stop":
				set_marketing_opt_out(identity, source=_scoped("meta_user_preferences", channel_account), notes=pref.get("detail") or "")
			elif choice == "resume":
				clear_marketing_opt_out(identity, source=_scoped("meta_user_preferences", channel_account), notes=pref.get("detail") or "")
			_record_event("user_preferences", f"marketing {choice}", "Info", pref, channel_account=channel_account, subject=identity)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title="Excom webhook: user preference not applied", message=f"{json.dumps(_scrub(pref))}\n{frappe.get_traceback()}")


def _handle_info(change: dict, entry: dict) -> None:
	value = change.get("value") or {}
	event = value.get("event") or value.get("decision") or value.get("type") or change.get("field")
	_record_event(change.get("field"), str(event), _severity(event), value, waba_id=entry.get("id") or "")


FIELD_HANDLERS = {
	"messages": _handle_messages,
	"message_template_status_update": _handle_template_status,
	"template_category_update": _handle_template_category,
	"message_template_quality_update": _handle_template_quality,
	"message_template_components_update": _handle_info,
	"phone_number_quality_update": _handle_phone_quality,
	"phone_number_name_update": _handle_info,
	"business_capability_update": _handle_capability,
	"account_update": _handle_account_update,
	"account_alerts": _handle_account_alert,
	"account_review_update": _handle_info,
	"security": _handle_info,
	"user_preferences": _handle_user_preferences,
	"business_username_updates": _handle_info,
	"business_username_update": _handle_info,
	"payment_configuration_update": _handle_info,
	"leadgen": _handle_leadgen,
}


_CONSENT_STOP_ALL = {"STOP ALL", "STOPALL", "UNSUBSCRIBE", "OPT OUT", "OPTOUT"}
_CONSENT_STOP_WA = {"STOP", "CANCEL", "END", "QUIT"}
_CONSENT_START = {"START", "UNSTOP", "SUBSCRIBE", "RESUME"}


_MARKETING_STOP_BUTTONS = {"STOP PROMOTIONS", "STOP PROMOTION", "STOP OFFERS", "STOP MARKETING", "UNSUBSCRIBE FROM PROMOTIONS"}


def _apply_consent_keyword(identity: str, content_text: str, message: dict = None, channel_account=None) -> None:
	"""Honour a WhatsApp STOP / START reply, or the template "Stop promotions" button.

	Meta does not relay a template opt-out button tap as a preference, so it is acted on here:
	the button stops marketing only. A plain-text STOP suppresses every WhatsApp template
	(``send_transactional`` and the send gate read these flags); UNSUBSCRIBE / STOP ALL
	suppresses every channel; START / SUBSCRIBE opts back in — but only from a STOP the person
	sent on WhatsApp, never from an opt-out someone recorded another way. Best-effort — it never
	breaks inbound ingestion, and only applies to signed payloads.
	"""
	word = " ".join((content_text or "").strip().upper().split())
	payload = ((message or {}).get("button") or {}).get("payload") or ""
	if not identity or not _signed():
		return
	try:
		from excom.excom.doctype.omni_identity.omni_identity import clear_opt_out, set_opt_out
		from excom.excom.services.consent import set_marketing_opt_out

		if word in _MARKETING_STOP_BUTTONS or payload.upper().startswith("STOP_PROMOTIONS"):
			set_marketing_opt_out(identity, source=_scoped("template_optout_button", channel_account))
		elif word in _CONSENT_STOP_ALL:
			set_opt_out(identity, channel="all", source="whatsapp_stop")
		elif word in _CONSENT_STOP_WA:
			set_opt_out(identity, channel="whatsapp", source="whatsapp_stop")
		elif word in _CONSENT_START:
			row = frappe.db.get_value("Omni Identity", identity, ["opt_out_source", "whatsapp_opt_out", "do_not_contact", "email_opt_out"], as_dict=True) or {}
			if row.get("opt_out_source") == "whatsapp_stop" and (row.get("whatsapp_opt_out") or row.get("do_not_contact")):
				clear_opt_out(identity, channel="whatsapp", source="whatsapp_start")
				if row.get("do_not_contact"):
					# a STOP ALL sent on WhatsApp: START brings WhatsApp back, email stays stopped
					frappe.db.set_value("Omni Identity", identity, {"do_not_contact": 0, "email_opt_out": 1}, update_modified=False)
	except Exception:
		frappe.log_error(title="Excom: consent keyword handling failed")


def _scoped(source: str, channel_account) -> str:
	"""A marketing opt-out source tagged with the WhatsApp account it came from ('src@waba')."""
	waba = channel_account.get("wa_business_id") if channel_account else ""
	return f"{source}@{waba}" if waba else source


def _process_inbound_message(message, sender_profile_name, channel_account, account_name, contact=None):
	"""Route a single inbound message through the service layer."""
	message_type = message.get("type", "text")
	phone, wa_user_id, wa_username = _whatsapp_user(message, contact or {})
	if not phone and not wa_user_id:
		frappe.log_error(title="Excom webhook: WhatsApp message with no sender", message=json.dumps(message)[:2000])
		return
	provider_msg_id = message.get("id", "")
	provider_ts = ""
	if message.get("timestamp"):
		from excom.excom.utils.site_time import epoch_to_site_time
		provider_ts = epoch_to_site_time(int(message["timestamp"]))

	is_reply = bool(message.get("context") and "forwarded" not in message.get("context", {}))
	reply_to_id = message["context"]["id"] if is_reply else ""

	content_text, content_json, media_file, excom_msg_type, kind, payload = _parse_inbound(message, message_type, channel_account)
	if message_type == "reaction":
		reply_to_id = (message.get("reaction") or {}).get("message_id", "")

	message_name = ingest_inbound_message(
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
		wa_user_id=wa_user_id,
		wa_username=wa_username,
		wa_waba=channel_account.get("wa_business_id") or "",
		# a welcome request is not a message from the person: it does not open the 24 h window
		opens_window=message_type != "request_welcome",
	)
	if message_name and message_type == "request_welcome":
		_record_event("messages", "request_welcome", "Info", message, channel_account=channel_account,
			subject=frappe.db.get_value("Excom Message", message_name, "omni_identity") or "")

	# Click-to-WhatsApp ads: Meta adds `referral` (ad id, headline, ctwa_clid …) to the message that
	# came from the ad. Only a newly stored message is attributed, so a redelivery adds nothing.
	if message_name and message.get("referral"):
		_attribute_referral(message_name, message, channel_account, sender_profile_name or "")

	# A STOP/START reply governs future marketing sends; act on it after the
	# message itself is stored, so the inbox still shows what they wrote.
	if message_name and excom_msg_type in ("Text", "Button") and content_text:
		_apply_consent_keyword(frappe.db.get_value("Excom Message", message_name, "omni_identity"), content_text, message, channel_account)

	# Every newly stored message goes to the features registered in excom_inbound_handlers.
	if message_name:
		_dispatch_inbound(message_name, kind, content_text, payload, provider_msg_id, channel_account)


def _parse_inbound(message: dict, message_type: str, channel_account) -> tuple:
	"""(content_text, content_json, media_file, Excom message_type, dispatch kind, payload) for one message.

	The text is what the inbox shows; the payload is what code acts on — the id of the button or
	row tapped (never its title), the template quick-reply payload, the parsed Flow answer, the
	address, the location or the cart order.
	"""
	type_map = {
		"text": "Text", "image": "Image", "video": "Video", "audio": "Audio", "document": "Document",
		"sticker": "Sticker", "location": "Location", "reaction": "Reaction", "button": "Button",
		"interactive": "Interactive", "contacts": "Contact", "order": "Order",
	}
	excom_msg_type = type_map.get(message_type, "Text")
	if excom_msg_type == "Order" and "Order" not in (frappe.get_meta("Excom Message").get_field("message_type").options or "").split("\n"):
		excom_msg_type = "Interactive"  # code deployed before `bench migrate`: keep the order, not the label
	content_text, content_json, media_file, kind, payload = "", message, "", "text" if message_type == "text" else "other", None

	if message_type == "text":
		content_text = (message.get("text") or {}).get("body", "")
		# a WhatsApp command ("/tickets …") stays a text message; handlers get its name
		from excom.excom.services.whatsapp_account import parse_command

		command = parse_command(content_text)
		if command:
			kind, payload = "command", command

	elif message_type == "request_welcome":
		# UNVERIFIED (gone from Meta's current docs): the person opened the chat for the first time
		content_text, kind, payload = "👋 Opened the chat (welcome request)", "welcome", message.get("request_welcome") or {}

	elif message_type == "reaction":
		content_text = (message.get("reaction") or {}).get("emoji", "")
		kind = "reaction"

	elif message_type == "interactive":
		data = message.get("interactive") or {}
		itype = data.get("type")
		if itype == "product":
			# the person asked about one product from a catalogue message (prices are ×1000)
			prod = data.get("product") or {}
			price = _num(prod.get("price_amount")) / 1000 if prod.get("price_amount") is not None else None
			content_text = "🛍 " + " · ".join(str(p) for p in (prod.get("title") or prod.get("retailer_id"), f"{prod.get('currency_code') or ''} {price:,.2f}".strip() if price is not None else "") if p)
			excom_msg_type, kind, payload = "Interactive", "product", prod
		elif itype == "button_reply":
			reply = data.get("button_reply") or {}
			content_text, excom_msg_type, kind, payload = reply.get("title", ""), "Button", "button", reply.get("id", "")
		elif itype == "list_reply":
			reply = data.get("list_reply") or {}
			# the title only: broadcast metrics count the tap by its label (the description stays in content_json)
			content_text, excom_msg_type, kind, payload = reply.get("title", ""), "Interactive", "list", reply.get("id", "")
		elif itype == "nfm_reply" or data.get("nfm_reply"):
			nfm = data.get("nfm_reply") or {}
			try:
				response = json.loads(nfm.get("response_json") or "{}")
			except (json.JSONDecodeError, TypeError):
				response = {}
			if not isinstance(response, dict):
				response = {"value": response}
			content_json = {"interactive": data, "flow_response": response}
			payload = response
			# Meta documents two address-reply shapes; both carry `values` and no flow_token
			is_address = nfm.get("name") == "address_message" or data.get("action") == "address_message" or (
				isinstance(response.get("values"), dict) and "flow_token" not in response)
			if is_address:
				values = response.get("values") if isinstance(response.get("values"), dict) else response
				content_text = "📍 " + ", ".join(str(values.get(k)) for k in ("name", "house_number", "floor_number", "tower_number", "building_name", "address", "landmark_area", "city", "state", "in_pin_code") if values.get(k))
				excom_msg_type, kind = "Location", "address"
			else:
				shown = {k: v for k, v in response.items() if v and k != "flow_token"}
				content_text = ", ".join(f"{k}: {v}" for k, v in shown.items()) or "Flow completed"
				excom_msg_type, kind = "Flow", "flow"

	elif message_type in ("image", "audio", "video", "document", "sticker"):
		content_text = (message.get(message_type) or {}).get("caption", "")
		media_file = _download_media(message, message_type, channel_account)
		kind = "media"

	elif message_type == "button":
		# a template quick-reply tap: `payload` is what excom set at send time
		button = message.get("button") or {}
		content_text, kind, payload = button.get("text", ""), "button", button.get("payload") or button.get("text", "")

	elif message_type == "order":
		order = message.get("order") or {}
		items = order.get("product_items") or []
		total = sum(_num(i.get("item_price")) * _num(i.get("quantity")) for i in items)
		currency = (items[0].get("currency") if items else "") or ""
		lines = [f"{i.get('quantity')} × {i.get('product_retailer_id')}" for i in items]
		content_text = f"🛒 Order: {len(items)} item(s){f' · {currency} {total:,.2f}' if items else ''}" + (f"\n{chr(10).join(lines)}" if lines else "") + (f"\n{order.get('text')}" if order.get("text") else "")
		kind, payload = "order", order

	elif message_type == "location":
		loc = message.get("location") or {}
		label = loc.get("name") or loc.get("address") or ""
		content_text = f"📍 {label} ({loc.get('latitude')}, {loc.get('longitude')})".replace("📍  (", "📍 (")
		kind, payload = "location", loc

	elif message_type == "contacts":
		people = message.get("contacts") or []
		content_text = ", ".join(((c.get("name") or {}).get("formatted_name") or "") for c in people) or "Contact card"
		kind = "contact"

	else:
		content_text = str((message.get(message_type) or {}).get(message_type, "") if isinstance(message.get(message_type), dict) else message.get(message_type) or "")

	return content_text, content_json, media_file, excom_msg_type, kind, payload


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _dispatch_system(message: dict, channel_account) -> None:
	"""System messages (number / user id changes) have no Excom Message; handlers get the change."""
	try:
		from excom.excom.services.inbound_dispatch import dispatch

		dispatch({
			"kind": "system", "message": "", "identity": "", "thread": "", "reply_to": "",
			"account": channel_account.name, "waba": channel_account.get("wa_business_id") or "",
			"provider_message_id": message.get("id") or "", "text": "", "payload": message.get("system") or {},
			"signed": True,
		})
	except Exception:
		frappe.log_error(title="Excom: system message dispatch failed", message=frappe.get_traceback())


def _dispatch_inbound(message_name: str, kind: str, text: str, payload, provider_msg_id: str, channel_account) -> None:
	try:
		msg = frappe.db.get_value("Excom Message", message_name, ["omni_identity", "thread", "reply_to"], as_dict=True) or {}
		from excom.excom.services.inbound_dispatch import dispatch

		dispatch({
			"kind": kind,
			"message": message_name,
			"identity": msg.get("omni_identity"),
			"thread": msg.get("thread"),
			"reply_to": msg.get("reply_to") or "",
			"account": channel_account.name,
			"waba": channel_account.get("wa_business_id") or "",
			"provider_message_id": provider_msg_id,
			"text": text,
			"payload": payload,
			"signed": _signed(),
		})
	except Exception:
		frappe.log_error(title="Excom: inbound dispatch failed", message=frappe.get_traceback())


def _attribute_referral(message_name, message, channel_account, display_name) -> None:
	"""Ad touch + thread fields + the conversation's Lead. Never breaks inbound ingestion."""
	frappe.db.savepoint("excom_ctwa")
	try:
		from excom.excom_ads.attribution import on_whatsapp_message

		on_whatsapp_message(message_name, message, channel_account, display_name)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback(save_point="excom_ctwa")
		frappe.log_error(title=f"Excom: click-to-WhatsApp attribution failed for {message_name}", message=frappe.get_traceback())


def _download_media(message, message_type, channel_account):
	"""Download media from WhatsApp and save as Frappe File. Returns file URL."""
	try:
		token = channel_account.get_password("wa_token")
		base_url = f"{channel_account.wa_url or 'https://graph.facebook.com'}/{graph_version(channel_account.wa_version)}/"
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
	"""Apply every status in the batch (Meta sends several per call) to its Excom Message.

	From Graph v24 a status carries `pricing` (category: marketing / marketing_lite / utility /
	authentication / service…, type: regular / free_customer_service / free_entry_point) instead
	of `conversation`. A failure carries a code that decides what happens next: 131050 means the
	person stopped marketing messages, so the identity is marked before the next campaign.
	"""
	for status_entry in data.get("statuses") or []:
		try:
			_apply_status(status_entry)
		except Exception:
			frappe.log_error(title="Excom webhook: status update failed", message=frappe.get_traceback())


def _apply_status(status_entry: dict) -> None:
	from excom.excom.services import whatsapp_errors

	provider_id = status_entry.get("id")
	status = status_entry.get("status")
	if not (provider_id and status):
		return

	failure_reason, failure_code = "", None
	if status == "failed":
		errors = status_entry.get("errors") or []
		if errors:
			err = errors[0]
			failure_code = err.get("code")
			title = err.get("title", "")
			detail = (err.get("error_data") or {}).get("details", "") or err.get("message", "")
			failure_reason = f"[{failure_code}] {title}: {detail}".strip(": ")

	pricing = status_entry.get("pricing") or {}
	message_name = update_delivery_status(
		provider_id, status,
		failure_reason=failure_reason,
		failure_code=failure_code,
		pricing_category=pricing.get("category") or "",
		pricing_type=pricing.get("type") or "",
	)
	if not message_name:
		return

	if not _signed():
		return
	msg = frappe.db.get_value("Excom Message", message_name, ["omni_identity", "account"], as_dict=True) or {}
	identity = msg.get("omni_identity")
	waba = frappe.db.get_value("Excom Channel Account", msg.get("account"), "wa_business_id") if msg.get("account") else ""
	recipient_user_id = status_entry.get("recipient_user_id") or ""
	if identity and recipient_user_id and not frappe.db.get_value("Omni Identity", identity, "wa_bsuid"):
		_attach_recipient_bsuid(identity, recipient_user_id, waba or "")

	if failure_code is not None and identity and whatsapp_errors.classify(failure_code) == whatsapp_errors.OPTED_OUT:
		from excom.excom.services.consent import set_marketing_opt_out

		set_marketing_opt_out(identity, source=f"meta_131050@{waba}" if waba else "meta_131050", reference_doctype="Excom Message", reference_name=message_name)
		frappe.db.commit()


def _attach_recipient_bsuid(identity: str, user_id: str, waba: str = "") -> None:
	from excom.excom.doctype.omni_identity.omni_identity import _attach_bsuid, find_by_bsuid

	if find_by_bsuid(user_id):
		return  # already someone's — do not move it here from a status webhook
	doc = frappe.get_doc("Omni Identity", identity)
	if _attach_bsuid(doc, user_id, wa_waba=waba):
		doc.save(ignore_permissions=True)
		frappe.db.commit()
