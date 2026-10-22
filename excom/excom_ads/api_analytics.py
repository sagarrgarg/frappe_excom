"""Whitelisted endpoints for GA4 properties. Admin work: the manage_connections right, a Google Ads
Connection inside the caller's scope with role permission on it (Excom Marketing has none), and role
permission on Ads Analytics Property. Responses never carry a credential."""

import frappe
from frappe import _

from excom.excom_ads import analytics, permissions
from excom.excom_ads.api_google import _require_connection
from excom.excom_ads.providers.base import AdsProviderError


def _provider_error(e) -> None:
	frappe.throw(str(e), title=_("Google Analytics"))


@frappe.whitelist()
def list_properties(connection: str) -> list[dict]:
	_require_connection(connection)
	try:
		return analytics.discover(connection)
	except AdsProviderError as e:
		_provider_error(e)


@frappe.whitelist(methods=["POST"])
def enable_property(connection: str, property_id: str, enabled: int = 1, workspace: str | None = None, brand: str | None = None) -> dict:
	from excom.excom_ads.doctype.ads_analytics_property.ads_analytics_property import normalise_property_id, property_doc_name

	conn = _require_connection(connection, "write")
	name = property_doc_name(normalise_property_id(property_id))
	exists = frappe.db.exists(analytics.PROP, name)
	if not frappe.has_permission(analytics.PROP, "write" if exists else "create"):
		raise frappe.PermissionError(_("Not permitted"))
	if exists and not permissions.doc_allowed(frappe.get_doc(analytics.PROP, name)):
		raise frappe.PermissionError(_("Not permitted"))
	if frappe.utils.cint(enabled) and not exists:
		from excom.excom_ads.accounts import default_workspace

		# The property lands in this workspace: the caller must see it, and it must be the connection's company.
		workspace = workspace or conn.workspace or default_workspace(conn.company)
		ws = frappe.get_doc("Ads Workspace", workspace)
		if not permissions.doc_allowed(ws) or ws.company != conn.company:
			raise frappe.PermissionError(_("Workspace {0} is not available for {1}").format(workspace, conn.company))
		if brand:
			b = frappe.get_doc("Ads Brand", brand)
			if not permissions.doc_allowed(b) or b.workspace != workspace:
				raise frappe.PermissionError(_("Brand {0} is not available in workspace {1}").format(brand, workspace))
	try:
		return analytics.enable(connection, property_id, enabled, workspace, brand)
	except AdsProviderError as e:
		_provider_error(e)


@frappe.whitelist(methods=["POST"])
def sync_property(property: str, job_type: str = "Metrics") -> dict:
	"""Queue a GA4 sync now (Metrics / Settle / Backfill)."""
	permissions.require("manage_connections")
	if job_type not in ("Metrics", "Settle", "Backfill"):
		frappe.throw(_("Unknown job type {0}").format(job_type))
	if not frappe.db.exists(analytics.PROP, property):
		raise frappe.DoesNotExistError(_("Analytics property {0} not found").format(property))
	doc = frappe.get_doc(analytics.PROP, property)
	if not permissions.doc_allowed(doc) or not frappe.has_permission(analytics.PROP, "write", doc=doc):
		raise frappe.PermissionError(_("Not permitted"))
	analytics.enqueue(doc.name, job_type, triggered_by=frappe.session.user)
	return {"queued": True, "property": doc.name, "job_type": job_type}
