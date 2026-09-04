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
