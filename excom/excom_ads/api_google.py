"""Whitelisted endpoints for Google Ads connections. Admin work: every call needs the
manage_connections right, a connection inside the caller's scope and role permission on Ads
Connection (which Excom Marketing does not have). Responses never carry a credential."""

import frappe
from frappe import _

from excom.excom_ads import google_connect, permissions


def _require_connection(name: str, ptype: str = "read"):
	permissions.require("manage_connections")
	if not name or not frappe.db.exists("Ads Connection", name):
		raise frappe.DoesNotExistError(_("Ads Connection {0} not found").format(name))
	doc = frappe.get_doc("Ads Connection", name)
	if not permissions.doc_allowed(doc) or not frappe.has_permission("Ads Connection", ptype, doc=doc):
		raise frappe.PermissionError(_("Not permitted"))
	if doc.provider != "Google":
		frappe.throw(_("{0} is not a Google connection").format(name))
	return doc


def _provider_error(e) -> None:
	frappe.throw(str(e), title=_("Google Ads"))


@frappe.whitelist(methods=["POST"])
def test_connection(connection: str) -> dict:
	_require_connection(connection, "write")
	return google_connect.test(connection)


@frappe.whitelist()
def list_customers(connection: str) -> list[dict]:
	from excom.excom_ads.providers.base import AdsProviderError

	_require_connection(connection)
	try:
		return google_connect.discover(connection)
	except AdsProviderError as e:
		_provider_error(e)


@frappe.whitelist(methods=["POST"])
def enable_customer(connection: str, customer_id: str, enabled: int = 1, workspace: str | None = None) -> dict:
	from excom.excom_ads.providers.base import AdsProviderError

	conn = _require_connection(connection, "write")
	if not frappe.has_permission("Ads Account", "create" if int(enabled) else "write"):
		raise frappe.PermissionError(_("Not permitted"))
	from excom.excom_ads.google_auth import normalise_customer_id
	from excom.excom_ads.naming import account_name

	try:
		existing = account_name("Google", normalise_customer_id(customer_id))
	except AdsProviderError as e:
		_provider_error(e)
	if frappe.db.exists("Ads Account", existing) and not permissions.doc_allowed(frappe.get_doc("Ads Account", existing)):
		raise frappe.PermissionError(_("Not permitted"))
	if int(enabled):
		from excom.excom_ads.accounts import default_workspace

		# The account lands in this workspace (unless it exists already): the caller must see it.
		workspace = workspace or conn.workspace or default_workspace(conn.company)
		ws = frappe.get_doc("Ads Workspace", workspace)
		if not permissions.doc_allowed(ws) or ws.company != conn.company:
			raise frappe.PermissionError(_("Workspace {0} is not available for {1}").format(workspace, conn.company))
	try:
		return google_connect.enable(connection, customer_id, enabled, workspace)
	except AdsProviderError as e:
		_provider_error(e)
