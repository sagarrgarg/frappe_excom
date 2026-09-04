import frappe
from frappe import _
from frappe.model.document import Document


class ExcomChannelAccount(Document):
	def on_update(self) -> None:
		self.ensure_single_default()
		if self.channel == "email":
			self._sync_email_authorized()
		if self.channel == "whatsapp" and self.wa_phone_id and not self.wa_display_phone and self.get_password("wa_token", raise_exception=False):
			# ask Meta for the real number once; never blocks the save
			try:
				from excom.excom.services.whatsapp_service import fetch_phone_profile
				fetch_phone_profile(self)
			except Exception:
				frappe.log_error(title=f"Excom: phone profile fetch failed {self.name}", message=frappe.get_traceback())

	def _has_own_refresh_token(self) -> bool:
		"""True if this account holds its own (per-mailbox) refresh token."""
		return bool(self.get_password("email_refresh_token", raise_exception=False))

	def _set_authorized_flag(self, authorized: bool) -> None:
		new_val = 1 if authorized else 0
		if self.email_authorized != new_val:
			frappe.db.set_value(
				self.doctype, self.name, "email_authorized", new_val, update_modified=False
			)
			frappe.db.commit()
			self.email_authorized = new_val

	def _sync_email_authorized(self) -> None:
		"""
		Sync email_authorized from this account's OWN stored refresh token.
		Does not consult Frappe's shared Connected App Token Cache, which cannot
		distinguish multiple Gmail mailboxes under one operator.
		"""
		self._set_authorized_flag(self._has_own_refresh_token())

	@frappe.whitelist()
	def check_email_authorization(self) -> dict:
		"""
		Called from the client script on form load. If we are returning from an
		OAuth flow (no own token yet, but the shared cache has a fresh one), this
		captures the tokens into the account's own encrypted fields.
		"""
		from excom.excom.api.chat import _check_manager_access
		_check_manager_access()
		if self.channel != "email":
			return {"authorized": False}

		# Already has its own credentials -- just report status, no network call.
		if self._has_own_refresh_token():
			self._set_authorized_flag(True)
			return {"authorized": True, "email": self.email_address}

		if not self.email_connected_app or not self.email_connected_user:
			return {"authorized": False}

		return self.finalize_email_authorization()

	@frappe.whitelist()
	def finalize_email_authorization(self) -> dict:
		"""
		Copy the just-issued OAuth tokens from Frappe's shared Token Cache into
		this account's own encrypted fields, after verifying the mailbox identity.
		Idempotent and safe to call at any time.
		"""
		from excom.excom.api.chat import _check_manager_access
		_check_manager_access()
		if self.channel != "email":
			return {"authorized": False}

		from excom.excom.services import gmail_service

		result = gmail_service.capture_tokens_from_connected_app(self.name)
		self._set_authorized_flag(bool(result.get("authorized")))
		return {
			"authorized": bool(result.get("authorized")),
			"email": result.get("email"),
			"error": result.get("error"),
		}

	@frappe.whitelist()
	def authorize_email_account(self, success_uri: str = "") -> str:
		"""
		Initiate Gmail OAuth flow as the current session user.

		- Sets email_connected_user to frappe.session.user so that the
		  Frappe callback state check always passes.
		- Ensures prompt=consent is in the Connected App query parameters so
		  Google always returns a fresh refresh_token (not just an access_token).
		- Returns the authorization URL to redirect the browser to.

		Args:
			success_uri: URL to redirect to after successful authorization.

		Returns:
			Google OAuth authorization URL.
		"""
		frappe.has_permission("Excom Channel Account", "write", throw=True)

		if self.channel != "email":
			frappe.throw(_("Not an email channel account"))

		if not self.email_connected_app:
			frappe.throw(_("Please set the Connected App field before authorizing"))

		current_user = frappe.session.user

		# Bind this account to the current user so that the OAuth callback
		# state check (token_cache.user == frappe.session.user) always matches.
		frappe.db.set_value(self.doctype, self.name, "email_connected_user", current_user)
		frappe.db.commit()

		connected_app = frappe.get_doc("Connected App", self.email_connected_app)

		# Ensure prompt=consent so Google always issues a fresh refresh_token.
		# Without this, re-authorizations may only return an access_token.
		existing_keys = [p.key for p in connected_app.query_parameters]
		if "prompt" not in existing_keys:
			connected_app.append("query_parameters", {"key": "prompt", "value": "consent"})
			connected_app.save(ignore_permissions=True)
			frappe.db.commit()
			connected_app.reload()

		# Initiate flow as current user — no explicit user= arg means frappe.session.user
		auth_url = connected_app.initiate_web_application_flow(
			success_uri=success_uri or f"/app/excom-channel-account/{self.name}"
		)

		return auth_url

	def ensure_single_default(self):
		"""Only one default incoming and one default outgoing per channel."""
		for field in ("is_default_incoming", "is_default_outgoing"):
			if not self.get(field):
				continue
			others = frappe.get_all(
				"Excom Channel Account",
				filters={"channel": self.channel, field: 1, "name": ["!=", self.name]},
			)
			for other in others:
				frappe.db.set_value("Excom Channel Account", other.name, field, 0)

	@property
	def token(self):
		"""Alias for backward compat — returns wa_token password."""
		return self.get_password("wa_token")

	@property
	def url(self):
		return self.wa_url

	@property
	def version(self):
		return self.wa_version

	@property
	def phone_id(self):
		return self.wa_phone_id

	@property
	def business_id(self):
		return self.wa_business_id

	@property
	def app_id(self):
		return self.wa_app_id

	@property
	def webhook_verify_token(self):
		return self.wa_webhook_verify_token

	@property
	def allow_auto_read_receipt(self):
		return self.wa_allow_auto_read_receipt
