"""Meta Business connection endpoints — manager only."""

import json

import frappe

from excom.excom.api.chat import _check_manager_access
from excom.excom.services import meta_connect


def _safe_extra(raw: str | None) -> dict:
	try:
		d = json.loads(raw or "{}")
	except ValueError:
		return {}
	d.pop("access_token", None)
	return d


@frappe.whitelist()
def get_connections() -> list:
	_check_manager_access()
	out = []
	for c in frappe.get_all("Excom Meta Connection", fields=["name", "business_id", "company", "status", "app_id", "api_version", "last_synced_at", "token_valid", "token_scopes", "webhook_verify_token"]):
		doc = frappe.get_doc("Excom Meta Connection", c.name)
		c["has_token"] = bool(doc.get_password("system_user_token", raise_exception=False))
		c["has_secret"] = bool(doc.get_password("app_secret", raise_exception=False))
		c["assets"] = [{"asset_type": a.asset_type, "asset_id": a.asset_id, "asset_name": a.asset_name, "page_id": a.page_id, "enabled": a.enabled, "linked_doctype": a.linked_doctype, "linked_name": a.linked_name, "extra": _safe_extra(a.extra)} for a in doc.assets]
		out.append(c)
	return out


@frappe.whitelist()
def discover(name: str) -> dict:
	_check_manager_access()
	return meta_connect.discover(name)


@frappe.whitelist()
def enable_asset(name: str, asset_type: str, asset_id: str, enable: int = 1) -> dict:
	_check_manager_access()
	return meta_connect.enable_asset(name, asset_type, asset_id, int(enable))


@frappe.whitelist()
def debug_token(name: str) -> dict:
	_check_manager_access()
	return meta_connect.debug_token(name)


@frappe.whitelist()
def exchange_token(name: str, short_lived_token: str) -> dict:
	_check_manager_access()
	return meta_connect.exchange_token(name, short_lived_token)


@frappe.whitelist()
def webhook_url() -> dict:
	_check_manager_access()
	return {"url": frappe.utils.get_url("/api/method/excom.excom.utils.webhook.webhook")}


# ─── Meta "Data Deletion Request" callback (App Dashboard → Settings → Basic) ─────────────────

def _all_app_secrets() -> list[str]:
	from excom.excom.utils.webhook import _candidate_secrets
	return _candidate_secrets()


def parse_signed_request(signed_request: str, secrets: list[str]) -> dict | None:
	"""Meta signed_request: base64url(HMAC-SHA256).base64url(json). Returns the payload if any secret verifies it."""
	import base64
	import hashlib
	import hmac
	try:
		sig_b64, payload_b64 = signed_request.split(".", 1)
		pad = lambda x: x + "=" * (-len(x) % 4)
		sig = base64.urlsafe_b64decode(pad(sig_b64))
		payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
	except Exception:
		return None
	for secret in secrets:
		expected = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
		if hmac.compare_digest(sig, expected):
			return payload
	return None


def delete_platform_user_data(platform_user_id: str) -> dict:
	"""Remove everything we hold that came from that Meta id: channel rows, their threads and messages.
	The Omni Identity itself stays if it has other channels (e.g. a phone) — those were not received from Meta."""
	deleted = {"channels": 0, "threads": 0, "messages": 0, "identities": 0}
	rows = frappe.get_all("Omni Identity Channel", filters={"channel_user_id": platform_user_id, "channel_type": ["in", ["instagram", "messenger", "facebook"]]}, fields=["parent", "channel_type"])
	for r in rows:
		for t in frappe.get_all("Excom Thread", filters={"omni_identity": r.parent, "channel": ["in", ["instagram", "messenger"]]}, pluck="name"):
			deleted["messages"] += frappe.db.count("Excom Message", {"thread": t})
			frappe.db.delete("Excom Message", {"thread": t})
			frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t})
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			deleted["threads"] += 1
		frappe.db.delete("Omni Identity Channel", {"parent": r.parent, "channel_user_id": platform_user_id})
		deleted["channels"] += 1
		oi = frappe.get_doc("Omni Identity", r.parent)
		if not oi.get("channels") and not oi.primary_phone and not oi.primary_email:
			frappe.db.delete("Omni Identity Link", {"parent": oi.name}); frappe.db.delete("Omni Identity Alias", {"parent": oi.name})
			frappe.delete_doc("Omni Identity", oi.name, force=True, ignore_permissions=True)
			deleted["identities"] += 1
	# lead-form payloads that carry this id
	for lg in frappe.get_all("Excom Intake Log", filters={"raw_payload": ["like", f"%{platform_user_id}%"]}, pluck="name"):
		frappe.db.set_value("Excom Intake Log", lg, "raw_payload", json.dumps({"redacted": "data deletion request"}), update_modified=False)
	return deleted


@frappe.whitelist(allow_guest=True, methods=["POST"])
def data_deletion_callback():
	"""Meta calls this when a user removes the app. Body: signed_request=<sig>.<payload>. Must answer
	{"url": <status page>, "confirmation_code": <code>} — Meta shows both to the user."""
	import secrets as _secrets
	signed = frappe.local.form_dict.get("signed_request") or ""
	payload = parse_signed_request(signed, _all_app_secrets())
	if not payload:
		frappe.throw("Invalid signed_request", frappe.AuthenticationError)
	user_id = str(payload.get("user_id") or "")
	code = _secrets.token_hex(8)
	req = frappe.get_doc({"doctype": "Excom Data Deletion Request", "platform": "Facebook", "platform_user_id": user_id, "confirmation_code": code, "status": "Received", "requested_at": frappe.utils.now_datetime()})
	req.flags.ignore_permissions = True
	req.insert()
	try:
		d = delete_platform_user_data(user_id)
		req.status = "Completed" if any(d.values()) else "Nothing to delete"
		req.details = json.dumps(d)
	except Exception:
		req.status = "Failed"
		frappe.log_error(title="Excom data deletion failed", message=frappe.get_traceback())
	req.completed_at = frappe.utils.now_datetime()
	req.save(ignore_permissions=True)
	frappe.db.commit()
	return {"url": f"{frappe.utils.get_url()}/excom-data-deletion?code={code}", "confirmation_code": code}


@frappe.whitelist()
def app_urls() -> dict:
	"""Everything the Meta App Dashboard asks for, for this site."""
	_check_manager_access()
	site = frappe.utils.get_url()
	return {
		"site": site,
		"privacy_policy_url": f"{site}/excom-privacy",
		"terms_url": f"{site}/excom-terms",
		"data_deletion_url": f"{site}/excom-data-deletion",
		"data_deletion_callback_url": f"{site}/api/method/excom.excom.api.meta.data_deletion_callback",
		"webhook_callback_url": f"{site}/api/method/excom.excom.utils.webhook.webhook",
		"app_domain": site.split("://", 1)[-1].split("/")[0],
	}
