"""Every whitelisted endpoint either checks access or is on an explicit, justified allowlist.

This is the regression fence for the audit of 2026-09-04: 22 endpoints (identity merge, flow
publish, template fetch, notification trigger, subscriber import, mailbox OAuth) were reachable by
any logged-in user, and the WhatsApp Flow endpoint accepted unsigned POSTs from anyone.
"""

import ast
import os

import frappe
from frappe.tests.utils import FrappeTestCase

APP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GUARDS = {
	"_check_excom_access", "_check_manager_access", "_check_thread_access", "_check_doc_read",
	"_check_broadcast_access", "_assert_allowed", "_assert_in_scope", "_source_by_token",
	"has_permission", "only_for", "check_permission", "_validate_session", "_verify_hmac_signature",
	"_caller_ok", "parse_signed_request",
}
# Public by design. Each line says why it is safe to leave open.
ALLOWED = {
	"excom.excom.api.mobile.get_client_id": "public bootstrap: site name, branding, OAuth client id",
	"excom.excom.api.notification.get_frappe_relay_push_config": "public push relay config, no secrets",
	"excom.excom.api.notification.are_push_notifications_enabled": "boolean flag",
	"excom.excom.doctype.excom_settings.excom_settings.get_branding": "logo and app name for the login shell",
	"excom.excom.api.webchat.get_config": "widget config for a public account id, rate limited",
	"excom.excom.api.webchat.create_session": "visitor session, rate limited",
	"excom.excom.api.unsubscribe.unsubscribe": "signed one-purpose link (HMAC in the URL)",
	"excom.www.excom.get_context_for_dev": "refuses unless developer_mode",
	"excom.excom.channels.whatsapp.api.webhook": "delegates to utils.webhook.webhook which verifies HMAC",
	"excom.excom.channels.whatsapp.api.handle_flow_request": "delegates to api.flow_endpoint which verifies HMAC",
	"excom.excom.utils.webhook.webhook": "dispatcher: GET verifies hub.verify_token, POST verifies X-Hub-Signature-256 in post()",
}


def _whitelisted():
	for d, _, files in os.walk(APP):
		for f in files:
			if not f.endswith(".py") or f.startswith("qa_") or "/tests/" in os.path.join(d, f):
				continue
			path = os.path.join(d, f)
			try:
				tree = ast.parse(open(path, errors="ignore").read())
			except SyntaxError:
				continue
			mod = "excom." + os.path.relpath(path, APP)[:-3].replace("/", ".")
			for n in ast.walk(tree):
				if isinstance(n, ast.FunctionDef) and any("whitelist" in ast.dump(dec) for dec in n.decorator_list):
					names = {x.attr if isinstance(x, ast.Attribute) else x.id for x in ast.walk(n) if isinstance(x, (ast.Name, ast.Attribute))}
					yield f"{mod}.{n.name}", bool(GUARDS & names)


class TestEndpointGuards(FrappeTestCase):
	def test_every_whitelisted_endpoint_checks_access(self):
		unguarded = sorted(name for name, guarded in _whitelisted() if not guarded and name not in ALLOWED)
		self.assertEqual(unguarded, [], "whitelisted without an access check (add a guard, or justify it in ALLOWED):\n  " + "\n  ".join(unguarded))

	def test_allowlist_entries_still_exist(self):
		names = {n for n, _ in _whitelisted()}
		self.assertEqual(sorted(set(ALLOWED) - names), [], "ALLOWED lists endpoints that no longer exist")


# ─── permission bypasses ─────────────────────────────────────────────────────
# ignore_permissions is not forbidden — a Guest-facing endpoint, a signed callback and the
# scheduler all legitimately write records no session user could write. What is forbidden is using
# it to paper over a permission the role should simply have been given: that was how an agent came
# to be unable to write a note, and nobody noticed for months because the API never asked.
#
# So: every bypass in the API layer must sit behind a check, or be named here with its reason.

GUARDS_BEFORE_BYPASS = {
	"_check_manager_access", "_check_thread_access", "_check_excom_access", "_assert_allowed",
	"only_for", "_assert_in_scope", "_check_access",
}

BYPASS_ALLOWED = {
	"meta.data_deletion_callback": "Meta calls this signed, with no session user at all",
	"meta.delete_platform_user_data": "called by the signed deletion callback above",
	"webchat.create_session": "a website visitor is Guest and owns nothing yet",
	"webchat.send_visitor_message": "same visitor, still Guest",
	"flow_endpoint.save_flow_data": "runs after the HMAC signature is verified",
	"email.send_scheduled_emails": "the scheduler, sending as the user who scheduled it",
	"email.schedule_email": "internal helper; the whitelisted caller checks thread access",
	"chat._to_absolute_url": "internal helper that republishes a file for Meta to fetch",
	"notification.register_site_on_excom_cloud": "System Manager only, writes the site's own config",
	"mobile.create_oauth_client": "System Manager only, writes the site's own OAuth client",
}


def _api_bypasses():
	api_dir = os.path.join(APP, "excom", "api")
	for fname in sorted(os.listdir(api_dir)):
		if not fname.endswith(".py"):
			continue
		path = os.path.join(api_dir, fname)
		src = open(path, errors="ignore").read()
		lines = src.splitlines()
		try:
			tree = ast.parse(src)
		except SyntaxError:
			continue
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef):
				continue
			body = "\n".join(lines[node.lineno - 1:node.end_lineno])
			if "ignore_permissions=True" not in body:
				continue
			guarded = any(g in body for g in GUARDS_BEFORE_BYPASS)
			yield f"{fname[:-3]}.{node.name}", guarded


class TestPermissionBypasses(FrappeTestCase):
	def test_every_api_bypass_is_guarded_or_justified(self):
		loose = sorted(name for name, guarded in _api_bypasses() if not guarded and name not in BYPASS_ALLOWED)
		self.assertEqual(loose, [], "ignore_permissions with no check in front of it:\n  " + "\n  ".join(loose))

	def test_the_justified_list_has_no_stale_entries(self):
		names = {n for n, _ in _api_bypasses()}
		self.assertEqual(sorted(set(BYPASS_ALLOWED) - names), [],
		                 "these no longer bypass anything; drop them from BYPASS_ALLOWED")
