"""One Google (or LinkedIn) credential. Admin-only: Excom Marketing has no permission on it at all.

Secrets live in Password fields (encrypted in __Auth; the document column only holds asterisks).
A service-account key is ~2.4 KB of JSON: Password columns and __Auth.password are TEXT on v15 and
v16 and Password fields have no length check, so the whole key fits (covered by the tests).
"""

import json
import re

import frappe
from frappe import _
from frappe.model.document import Document

PASSWORD_FIELDS = ("service_account_key", "oauth_client_secret", "refresh_token", "developer_token")
DEFAULT_SCOPES = {"Google": "https://www.googleapis.com/auth/adwords"}


class AdsConnection(Document):
	def validate(self):
		from excom.excom_ads.google_auth import OAUTH, SERVICE_ACCOUNT, normalise_customer_id, parse_scopes, parse_service_account_key
		from excom.excom_ads.providers.base import AdsProviderError

		if self.workspace:
			ws_company = frappe.db.get_value("Ads Workspace", self.workspace, "company")
			if ws_company and ws_company != self.company:
				frappe.throw(_("Workspace {0} belongs to company {1}, not {2}").format(self.workspace, ws_company, self.company))
		if self.login_customer_id:
			try:
				self.login_customer_id = normalise_customer_id(self.login_customer_id)
			except AdsProviderError as e:
				frappe.throw(_("Manager Account ID: {0}").format(str(e)))
		if self.api_version:
			self.api_version = self.api_version.strip().lower()
			if not re.fullmatch(r"v\d{1,3}", self.api_version):
				frappe.throw(_("API Version must look like v26"))
		self.scopes = "\n".join(parse_scopes(self.scopes)) or DEFAULT_SCOPES.get(self.provider, "")

		changed = [f for f in PASSWORD_FIELDS if self._new_secret(f)]
		if "service_account_key" in changed:
			try:
				info = parse_service_account_key(self.service_account_key)
			except AdsProviderError as e:
				# The message never quotes the pasted value.
				self.service_account_key = None
				frappe.throw(str(e), title=_("Service Account Key"))
			# Stored minified: newlines lost when pasting into a one-line field cannot break it.
			self.service_account_key = json.dumps(info, separators=(",", ":"))
			self.service_account_email = info.get("client_email")
		if self.auth_mode == SERVICE_ACCOUNT and not self._has_secret("service_account_key"):
			frappe.throw(_("Paste the service account key (JSON)"))
		if self.auth_mode == OAUTH:
			missing = [label for f, label in (("oauth_client_secret", _("OAuth Client Secret")), ("refresh_token", _("Refresh Token"))) if not self._has_secret(f)]
			if not self.oauth_client_id:
				missing.insert(0, _("OAuth Client ID"))
			if missing:
				frappe.throw(_("Missing: {0}").format(", ".join(missing)))
		if changed or self.has_value_changed("auth_mode") or self.has_value_changed("login_customer_id"):
			# A new credential has not been checked yet.
			self.token_status = "Not Checked"
			if self.status != "Disabled":
				self.status = "Not Tested"
			self.last_error = None

	def _new_secret(self, fieldname: str) -> bool:
		v = self.get(fieldname)
		return bool(v) and not self.is_dummy_password(v)

	def _has_secret(self, fieldname: str) -> bool:
		if self._new_secret(fieldname):
			return True
		if self.is_new() or not self.get(fieldname):
			return False
		return bool(self.get_password(fieldname, raise_exception=False))
