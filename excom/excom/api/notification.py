"""Push notification APIs for token management.

Mirrors raven/api/notification.py: subscribe/unsubscribe FCM tokens,
check if push is enabled, and manage Excom Cloud registration.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.frappeclient import FrappeClient
from frappe.utils import get_request_session

from excom.excom.notification import EXCOM_RELAY_PROJECT_NAME


def _excom_roles_allowed_for_relay_config() -> bool:
	user = frappe.session.user
	if not user or user == "Guest":
		return False
	return bool(set(frappe.get_roles(user)) & {"System Manager", "Excom Manager", "Excom User"})


@frappe.whitelist()
def get_frappe_relay_push_config() -> dict[str, object]:
	"""Fetch Firebase web config + VAPID from the Frappe push relay via the app server.

	Raven calls the relay directly from the browser; that requires the relay to allow
	cross-origin requests and breaks when ``push_relay_server_url`` is mistakenly set
	to this site's URL (no ``notification_relay`` app). This endpoint:

	- Runs same-origin in the browser (session cookie).
	- Ensures one-time relay registration so **Push Notification Settings** gets
	  ``api_key`` / ``api_secret`` (Frappe's "first push" credential flow), matching
	  desk behaviour for ``notification_relay.api.auth.get_credential``.
	"""
	if not _excom_roles_allowed_for_relay_config():
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	push_service = (
		frappe.db.get_single_value("Excom Settings", "push_notification_service") or "Frappe Cloud"
	)
	if push_service != "Frappe Cloud":
		frappe.throw(
			_("Excom push service is not Frappe Cloud; use boot firebase config or switch service.")
		)

	if not frappe.db.get_single_value("Push Notification Settings", "enable_push_notification_relay"):
		frappe.throw(_("Enable Push Notification Relay under Integrations > Push Notification Settings."))

	relay_base = (frappe.conf.get("push_relay_server_url") or "").strip().rstrip("/")
	if not relay_base:
		frappe.throw(
			_(
				"Set `push_relay_server_url` in site_config to the **Frappe notification relay** base URL "
				"(the host that runs the `notification_relay` app), not your ERPNext site URL."
			)
		)

	pns = frappe.get_single("Push Notification Settings")
	needs_relay_credential = not (pns.api_key and pns.get_password("api_secret"))
	if pns.enable_push_notification_relay and needs_relay_credential:
		try:
			from frappe.push_notification import PushNotification

			PushNotification(EXCOM_RELAY_PROJECT_NAME)._get_credential()  # noqa: SLF001
		except Exception as exc:
			frappe.log_error(title="Excom: push relay credential registration failed")
			frappe.throw(
				_(
					"Could not register this site with the push relay at {0}. "
					"Confirm `push_relay_server_url` points at the central relay (see Frappe Cloud / "
					"self-hosted relay docs), not this site. {1}"
				).format(relay_base, str(exc))
			)

	url = f"{relay_base}/api/method/notification_relay.api.get_config"
	try:
		sess = get_request_session()
		resp = sess.get(
			url,
			params={"project_name": EXCOM_RELAY_PROJECT_NAME},
			timeout=30,
		)
		body = resp.json()
	except json.JSONDecodeError:
		frappe.throw(_("Push relay returned a non-JSON response for get_config."))
	except Exception as exc:
		frappe.log_error(title="Excom: relay get_config request failed")
		frappe.throw(_("Failed to reach push relay get_config: {0}").format(str(exc)))

	if not resp.ok:
		exc_msg = str(body.get("exception") or body.get("exc_type") or "")
		hint = _(
			"`push_relay_server_url` must be the base URL of the Frappe notification relay "
			"(a site with the `notification_relay` Frappe app), not your own bench URL."
		)
		if "not installed" in exc_msg.lower() or "AppNotInstalled" in exc_msg:
			frappe.throw(f"{hint} {exc_msg}")
		frappe.throw(_("Push relay get_config failed ({0}). {1}").format(resp.status_code, exc_msg or body))

	payload = body.get("message")
	if isinstance(payload, str):
		try:
			payload = json.loads(payload)
		except json.JSONDecodeError:
			payload = None

	data = payload if isinstance(payload, dict) else body
	config = data.get("config") if isinstance(data, dict) else None
	vapid = data.get("vapid_public_key") if isinstance(data, dict) else None

	if not isinstance(config, dict) or not vapid or not str(vapid).strip():
		frappe.throw(_("Push relay returned an incomplete get_config payload for project {0}.").format(EXCOM_RELAY_PROJECT_NAME))

	return {"config": config, "vapid_public_key": str(vapid).strip()}


@frappe.whitelist()
def are_push_notifications_enabled() -> bool:
	"""Check whether push notifications are active for this site.

	- Frappe Cloud: delegates to Push Notification Settings relay flag.
	- Excom Cloud: always True (credentials validated at Settings save).
	"""
	try:
		push_service = (
			frappe.db.get_single_value("Excom Settings", "push_notification_service")
			or "Frappe Cloud"
		)

		if push_service == "Frappe Cloud":
			if not bool(
				frappe.db.get_single_value(
					"Push Notification Settings", "enable_push_notification_relay"
				)
			):
				return False
			# Relay cannot work without site_config (same requirement as desk / Raven www).
			return bool(frappe.conf.get("push_relay_server_url"))
		return True
	except frappe.DoesNotExistError:
		return False


@frappe.whitelist(methods=["POST"])
def subscribe(
	fcm_token: str,
	environment: str,
	device_information: str | None = None,
) -> str:
	"""Register a device FCM token for the current user.

	Args:
		fcm_token: Firebase Cloud Messaging device token.
		environment: 'Web' or 'Mobile'.
		device_information: Optional device name/model string.

	Returns:
		'Subscribed' on success.
	"""
	from excom.excom.api.chat import _check_excom_access
	_check_excom_access()
	if frappe.db.exists(
		"Excom Push Token",
		{"fcm_token": fcm_token, "user": frappe.session.user},
	):
		return "Already subscribed"

	frappe.get_doc({
		"doctype": "Excom Push Token",
		"fcm_token": fcm_token,
		"user": frappe.session.user,
		"environment": environment,
		"device_information": device_information,
	}).insert(ignore_permissions=True)

	return "Subscribed"


@frappe.whitelist(methods=["POST"])
def unsubscribe(fcm_token: str) -> str:
	"""Remove an FCM token for the current user (e.g. on logout).

	Uses delete_doc so ``Excom Push Token.on_trash`` runs and deregisters
	from the Frappe push relay (``frappe.db.delete`` would skip that).

	Args:
		fcm_token: The token to remove.

	Returns:
		'Unsubscribed' on success.
	"""
	from excom.excom.api.chat import _check_excom_access
	_check_excom_access()
	names = frappe.get_all(
		"Excom Push Token",
		filters={"fcm_token": fcm_token, "user": frappe.session.user},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("Excom Push Token", name, ignore_permissions=True)
	return "Unsubscribed"


@frappe.whitelist(methods=["POST"])
def register_site_on_excom_cloud() -> None:
	"""Register this site with the Excom Cloud push server.

	Stores the returned config and VAPID public key on Excom Settings.
	Only available to System Managers.
	"""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Excom Settings")

	if settings.push_notification_service != "Excom Cloud":
		frappe.throw(_("Push notification service is not set to Excom Cloud."))

	client = FrappeClient(
		url=settings.push_notification_server_url,
		api_key=settings.push_notification_api_key,
		api_secret=settings.get_password("push_notification_api_secret"),
	)

	response = client.post_api(
		"excom_cloud.api.notification.register_site",
		params={"site_name": urlparse(frappe.utils.get_url()).hostname},
	)

	settings.push_config = response.get("config")
	settings.vapid_public_key = response.get("vapid_public_key")
	settings.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def sync_user_tokens_to_excom_cloud() -> str:
	"""Enqueue a bulk sync of all local push tokens to Excom Cloud.

	Only Excom Managers or System Managers may trigger this.
	"""
	frappe.only_for(["System Manager", "Excom Manager"])
	frappe.enqueue(
		"excom.excom.excom_cloud_notifications.sync_users_tokens_to_excom_cloud",
		queue="long",
	)
	return "Token sync enqueued"
