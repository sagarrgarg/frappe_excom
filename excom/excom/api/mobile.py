"""Mobile app APIs for OAuth client management and site discovery.

Mirrors raven/api/raven_mobile.py: provides get_client_id (guest)
and create_oauth_client (manager) for the Excom mobile app.
"""

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils.change_log import get_versions
from frappe.utils.data import get_host_name_from_request


def _mobile_public_base_url() -> str:
	"""Origin for OAuth PKCE / deep links — public URL without bench webserver_port.

	Frappe's ``get_url()`` appends ``:webserver_port`` when the host has no port in
	the string, which breaks TLS reverse-proxy setups (e.g. ``https://dev.example.com:8002``).

	Resolution order:
	1. ``mobile_public_site_url`` in site_config
	2. ``mobile_public_site_url`` on Excom Settings (Desk)
	3. Same host logic as ``get_url`` but **without** the port suffix
	4. Last resort: ``get_url()`` (may still include port if no host/request)
	"""
	override = (frappe.conf.get("mobile_public_site_url") or "").strip()
	if not override:
		override = (frappe.db.get_single_value("Excom Settings", "mobile_public_site_url") or "").strip()
	if override:
		override = override.rstrip("/")
		if not override.startswith(("http://", "https://")):
			override = "https://" + override
		return override

	host_name = frappe.local.conf.host_name or frappe.local.conf.hostname
	if host_name:
		if not host_name.startswith(("http://", "https://")):
			host_name = "http://" + host_name
		return host_name.rstrip("/")

	request_host_name = get_host_name_from_request()
	if request_host_name:
		return request_host_name.rstrip("/")

	if frappe.local.site:
		protocol = "http://"
		if frappe.local.conf.ssl_certificate:
			protocol = "https://"
		elif frappe.local.conf.wildcard:
			domain = frappe.local.conf.wildcard.get("domain")
			if (
				domain
				and frappe.local.site.endswith(domain)
				and frappe.local.conf.wildcard.get("ssl_certificate")
			):
				protocol = "https://"
		return (protocol + frappe.local.site).rstrip("/")

	subdomain = frappe.db.get_single_value("Website Settings", "subdomain")
	if subdomain:
		subdomain = subdomain.strip()
		if not subdomain.startswith(("http://", "https://")):
			subdomain = "http://" + subdomain
		return subdomain.rstrip("/")

	return frappe.utils.get_url()


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def get_client_id() -> dict:
	"""Return OAuth client_id, site metadata, and branding for the mobile app.

	This is the first endpoint the mobile app hits after the user
	enters a site URL. It returns everything needed to initiate
	an OAuth2 PKCE flow.
	"""
	app_name = (
		frappe.get_website_settings("app_name")
		or frappe.get_system_settings("app_name")
	)
	if not app_name or app_name == "Frappe":
		app_name = "Excom"

	all_app_versions = get_versions()
	app_versions = {k: v["version"] for k, v in all_app_versions.items()}

	can_manage = False
	if frappe.session.user != "Guest":
		roles = frappe.get_roles(frappe.session.user)
		can_manage = bool(set(roles) & {"System Manager", "Excom Manager"})

	return {
		"client_id": frappe.db.get_single_value("Excom Settings", "oauth_client"),
		"system_timezone": frappe.get_system_settings("time_zone"),
		"app_name": app_name,
		"sitename": frappe.local.site,
		"excom_version": app_versions.get("excom", "0.0.0"),
		"frappe_version": app_versions.get("frappe", "0.0.0"),
		"logo": (
			frappe.db.get_single_value("Navbar Settings", "app_logo")
			or "/assets/excom/excom/excom-logo.svg"
		),
		"can_manage_mobile_oauth": can_manage,
		"site_url": _mobile_public_base_url(),
	}


@frappe.whitelist(methods=["POST"])
def create_oauth_client() -> dict:
	"""Create or update the OAuth Client used by the Excom mobile app.

	Only System Managers / Excom Managers should call this.
	Stores the resulting client reference on Excom Settings.oauth_client.
	"""
	frappe.only_for(["System Manager", "Excom Manager"])

	settings = frappe.get_doc("Excom Settings")
	existing = settings.oauth_client

	if not existing:
		oauth_client = frappe.new_doc("OAuth Client")
	else:
		oauth_client = frappe.get_doc("OAuth Client", existing)

	oauth_client.app_name = "Excom Mobile"
	oauth_client.scopes = "all openid"
	oauth_client.redirect_uris = "excom.app:"
	oauth_client.default_redirect_uri = "excom.app:"
	oauth_client.grant_type = "Authorization Code"
	oauth_client.response_type = "Code"
	oauth_client.allowed_roles = []
	oauth_client.append("allowed_roles", {"role": "Excom User"})
	oauth_client.save(ignore_permissions=True)

	settings.oauth_client = oauth_client.name
	settings.save(ignore_permissions=True)

	return {"message": "OAuth Client created successfully"}
