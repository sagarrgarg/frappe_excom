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
