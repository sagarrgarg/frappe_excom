"""Push notification delivery for Excom messages.

Mirrors raven/notification.py: routes push notifications through
either Frappe Cloud (PushNotification relay) or Excom Cloud
(remote FrappeClient) based on Excom Settings configuration.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import frappe
from frappe.frappeclient import FrappeClient
from frappe.utils import get_datetime, get_system_timezone
from pytz import timezone, utc

MAX_NOTIFICATION_CONTENT_LENGTH = 1000

# Frappe push relay project id — must match ``new FrappePushNotification("…")`` and relay/Frappe Cloud registration.
EXCOM_RELAY_PROJECT_NAME = "excom"

# ── Public entry point ────────────────────────────────────────────


def send_push_for_inbound_message(thread_name: str, message_name: str) -> None:
	"""Route a push notification for an inbound message.

	Called from thread_service.ingest_inbound_message via after_response.
	"""
	if frappe.flags.in_test or frappe.flags.in_install or frappe.flags.in_patch:
		return

	try:
		settings = frappe.get_cached_doc("Excom Settings")
		push_service = settings.push_notification_service or "Frappe Cloud"

		if push_service == "Excom Cloud":
			_send_via_excom_cloud(thread_name, message_name, settings)
		else:
			_send_via_frappe_cloud(thread_name, message_name)
	except Exception:
		frappe.log_error(title="Excom: push notification delivery error")


# ── Frappe Cloud path ─────────────────────────────────────────────


def _send_via_frappe_cloud(thread_name: str, message_name: str) -> None:
	"""Send push via Frappe Framework's built-in PushNotification relay."""
	try:
		from frappe.push_notification import PushNotification

		push = PushNotification(EXCOM_RELAY_PROJECT_NAME)
		if not push.is_enabled():
			return
	except ImportError:
		return

	thread = frappe.db.get_value(
		"Excom Thread",
		thread_name,
		["display_name", "assigned_to", "assigned_team", "channel"],
		as_dict=True,
	)
	if not thread:
		return

	msg = frappe.db.get_value(
		"Excom Message",
		message_name,
		["content", "message_type", "owner"],
		as_dict=True,
	)
	if not msg:
		return

	title = thread.display_name or "New message"
	body = _truncate(msg.content or "You have a new message")
	link = f"{frappe.utils.get_url()}/excom"
	data = {
		"base_url": frappe.utils.get_url(),
		"sitename": frappe.local.site,
		"thread_id": thread_name,
		"channel": thread.channel or "",
		"type": "New message",
	}

	recipients = _get_notification_recipients(thread)
	if not recipients:
		return

	for user_id in recipients:
		if user_id == msg.owner:
			continue
		try:
			push.send_notification_to_user(
				user_id=user_id,
				title=title,
				body=body,
				data=data,
				link=link,
			)
		except Exception:
			frappe.log_error(title=f"Excom: push to {user_id} failed")


# ── Excom Cloud path ─────────────────────────────────────────────


def _send_via_excom_cloud(
	thread_name: str,
	message_name: str,
	settings: "Document",
) -> None:
	"""Send push via the remote Excom Cloud server."""
	thread = frappe.db.get_value(
		"Excom Thread",
		thread_name,
		["display_name", "assigned_to", "assigned_team", "channel"],
		as_dict=True,
	)
	if not thread:
		return

	msg = frappe.db.get_value(
		"Excom Message",
		message_name,
		["content", "message_type", "owner", "creation"],
		as_dict=True,
	)
	if not msg:
		return

	recipients = _get_notification_recipients(thread)
	if not recipients:
		return

	recipients = [u for u in recipients if u != msg.owner]
	if not recipients:
		return

	title = thread.display_name or "New message"
	body = _truncate(msg.content or "You have a new message")
	url = f"{frappe.utils.get_url()}/excom"

	data = {
		"base_url": frappe.utils.get_url(),
		"sitename": frappe.local.site,
		"thread_id": thread_name,
		"channel": thread.channel or "",
		"type": "New message",
		"creation": _get_millis(msg.creation) if msg.creation else "",
	}

	messages = [
		{
			"users": recipients,
			"notification": {"title": title, "body": body},
			"data": data,
			"tag": thread_name,
			"click_action": url,
		}
	]

	_post_to_excom_cloud(messages, settings)


def _post_to_excom_cloud(messages: list, settings: "Document") -> None:
	"""POST notification payload to the Excom Cloud push server."""
	client = FrappeClient(
		url=settings.push_notification_server_url,
		api_key=settings.push_notification_api_key,
		api_secret=settings.get_password("push_notification_api_secret"),
	)
	client.post_api(
		"excom_cloud.api.notification.send_to_users",
		params={
			"messages": json.dumps(messages),
			"site_name": urlparse(frappe.utils.get_url()).hostname,
		},
	)


# ── Helpers ───────────────────────────────────────────────────────


def _get_notification_recipients(thread: dict) -> list[str]:
	"""Determine which users should receive a push for this thread.

	Returns a list of user emails:
	- The directly assigned user (if any).
	- All members of the assigned team (if any).
	"""
	users: set[str] = set()

	if thread.assigned_to:
		users.add(thread.assigned_to)

	if thread.assigned_team:
		members = frappe.get_all(
			"Excom Team Member",
			filters={"parent": thread.assigned_team},
			fields=["user"],
		)
		for m in members:
			if m.user:
				users.add(m.user)

	users.discard("Administrator")
	from excom.excom_ai.service_user import humans

	return humans(users)


def _truncate(content: str) -> str:
	if not content:
		return ""
	return content[:MAX_NOTIFICATION_CONTENT_LENGTH]


def _get_millis(timestamp) -> str:
	"""Convert a Frappe datetime to milliseconds since epoch (UTC)."""
	dt = get_datetime(timestamp)
	tz = get_system_timezone()
	local_dt = timezone(tz).localize(dt)
	utc_dt = local_dt.astimezone(utc)
	return str(utc_dt.timestamp() * 1000)
