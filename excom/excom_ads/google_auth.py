"""Google sign-in for every Google integration: Ads now, GA4 / Search Console / Merchant Center later.

One Ads Connection holds one credential, either

  Service Account      a JSON key; the service account's email is added as a user on the Google
                       Ads manager account. Token: a signed JWT exchanged at Google's token endpoint
                       (https://developers.google.com/identity/protocols/oauth2/service-account#httprest).
  OAuth Refresh Token  client id + secret + refresh token from an Internal consent screen.

Secrets are read with get_password() inside this module only. They are never returned, logged or
put in an error: every message built here passes through `scrub()`.

All Google HTTP goes through `http_request()`, so tests replace that one function and never touch
the network. Access tokens are cached in Redis, encrypted with the site key, for their lifetime
minus a safety margin; the cache key changes whenever the credential changes.
"""

import hashlib
import json
import re
import time

import frappe
import requests

from excom.excom_ads.providers.base import INVALID, PERMISSION, RATE_LIMITED, RETRYABLE, TOKEN, AdsProviderError

# Fixed, never taken from the pasted key file: a key whose token_uri pointed elsewhere would send
# a signed assertion to a third party.
TOKEN_URI = "https://oauth2.googleapis.com/token"
ADWORDS_SCOPE = "https://www.googleapis.com/auth/adwords"
# GA4 Data API runReport and Admin API accountSummaries / properties / dataStreams accept either.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
ANALYTICS_SCOPES = (ANALYTICS_SCOPE, "https://www.googleapis.com/auth/analytics")
JWT_LIFETIME = 3600
TOKEN_MARGIN = 300  # refresh this many seconds before Google's expiry
SERVICE_ACCOUNT = "Service Account"
OAUTH = "OAuth Refresh Token"

# OAuth token-endpoint errors (RFC 6749 §5.2) → our classes.
TOKEN_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_request", "unsupported_grant_type"}
SCOPE_ERRORS = {"invalid_scope", "access_denied"}


def http_request(method: str, url: str, headers: dict | None = None, json_body=None, form: dict | None = None, timeout: int = 60):
	"""The single network call for Google. Tests replace it. Returns (status_code, body, headers).

	body is the decoded JSON (dict, or list for streaming endpoints) or {"error": {...}} for a
	non-JSON answer (an HTML error page from a proxy, for example).
	"""
	try:
		resp = requests.request(method, url, headers=headers, json=json_body, data=form, timeout=timeout)
	except requests.Timeout:
		return 504, {"error": {"code": 504, "status": "DEADLINE_EXCEEDED", "message": "Timed out waiting for Google"}}, {}
	except requests.RequestException as e:
		return 503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": f"Could not reach Google ({type(e).__name__})"}}, {}
	try:
		body = resp.json() if resp.content else {}
	except ValueError:
		body = {"error": {"code": resp.status_code, "message": re.sub(r"\s+", " ", resp.text or "")[:300]}}
	return resp.status_code, body, resp.headers


def scrub(text, secrets) -> str:
	"""Remove every secret (and the long parts of a key) from a message. Defence in depth: Google
	does not echo credentials, but nothing here relies on that."""
	out = str(text or "")
	for s in sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True):
		out = out.replace(s, "***")
	# Anything that looks like a bearer token, refresh token or PEM block.
	out = re.sub(r"-----BEGIN [A-Z ]+-----.*?(-----END [A-Z ]+-----|$)", "***", out, flags=re.S)
	out = re.sub(r"\bya29\.[\w\-\.]+", "***", out)
	out = re.sub(r"\b1//[\w\-]{20,}", "***", out)
	return out


def normalise_customer_id(value) -> str:
	"""'123-456-7890' → '1234567890'. Raises on anything that is not a customer ID (keeps GAQL and
	URLs free of injected text)."""
	digits = re.sub(r"[\s\-]", "", str(value or ""))
	if not re.fullmatch(r"\d{6,12}", digits):
		raise AdsProviderError(f"'{str(value)[:20]}' is not a Google Ads customer ID (10 digits, dashes optional)", INVALID)
	return digits


def parse_service_account_key(raw: str) -> dict:
	"""Validate a pasted key file. Errors never quote the content."""
	try:
		info = json.loads(raw)
	except (TypeError, ValueError):
		raise AdsProviderError("The service account key is not valid JSON. Paste the whole key file as downloaded from Google Cloud.", INVALID) from None
	if not isinstance(info, dict) or info.get("type") != "service_account":
		raise AdsProviderError("This is not a service account key (its \"type\" must be \"service_account\").", INVALID)
	for k in ("client_email", "private_key"):
		if not info.get(k):
			raise AdsProviderError(f"The service account key has no {k}.", INVALID)
	try:
		from cryptography.hazmat.primitives import serialization

		serialization.load_pem_private_key(info["private_key"].encode(), password=None)
	except Exception:
		raise AdsProviderError("The service account key's private_key cannot be read. Download a new key file.", INVALID) from None
	return info


class GoogleCredentials:
	"""A loaded Ads Connection credential. Holds secrets in memory for one job; never serialise it."""

	def __init__(self, conn):
		self.connection = conn.name
		self.provider = conn.provider
		self.mode = conn.auth_mode or SERVICE_ACCOUNT
		self.login_customer_id = normalise_customer_id(conn.login_customer_id) if conn.login_customer_id else ""
		self.scopes = parse_scopes(conn.scopes)
		self.stats_calls = 0
		self._token = None
		self._token_expires = 0.0
		self.granted_scopes = ""
		get = lambda f: conn.get_password(f, raise_exception=False) or ""
		self.developer_token = get("developer_token")
		if self.mode == SERVICE_ACCOUNT:
			raw = get("service_account_key")
			if not raw:
				raise AdsProviderError(f"Ads Connection {conn.name} has no service account key", TOKEN)
			try:
				self._sa = parse_service_account_key(raw)
			except AdsProviderError as e:
				raise AdsProviderError(f"Ads Connection {conn.name}: {e}", TOKEN) from None
			self._secrets = [raw, self._sa.get("private_key", ""), self._sa.get("private_key_id", ""), self.developer_token]
		else:
			self._client_id = conn.oauth_client_id or ""
			self._client_secret = get("oauth_client_secret")
			self._refresh = get("refresh_token")
			if not (self._client_id and self._client_secret and self._refresh):
				raise AdsProviderError(f"Ads Connection {conn.name} is missing the OAuth client ID, client secret or refresh token", TOKEN)
			self._secrets = [self._client_secret, self._refresh, self.developer_token]

	@classmethod
	def load(cls, name: str, provider: str = "Google") -> "GoogleCredentials":
		if not name or not frappe.db.exists("Ads Connection", name):
			raise AdsProviderError(f"Ads Connection {name or '(none)'} does not exist", INVALID)
		conn = frappe.get_doc("Ads Connection", name)
		if conn.provider != provider:
			raise AdsProviderError(f"Ads Connection {name} is a {conn.provider} connection, not {provider}", INVALID)
		if conn.status == "Disabled":
			raise AdsProviderError(f"Ads Connection {name} is disabled", INVALID)
		return cls(conn)

	@property
	def secrets(self) -> list:
		return [s for s in self._secrets + [self._token or ""] if s]

	def scrub(self, text) -> str:
		return scrub(text, self.secrets)

	# ─── tokens ───────────────────────────────────────────────────────────────

	def _cache_key(self, scopes: tuple) -> str:
		h = hashlib.sha256("\x00".join([frappe.local.site or "", self.connection, self.mode, *self._secrets, *scopes]).encode()).hexdigest()
		return f"excom_ads:google_token:{h}"

	def access_token(self, scopes: tuple = (ADWORDS_SCOPE,), force: bool = False) -> str:
		if self._token and not force and time.time() < self._token_expires:
			return self._token
		key = self._cache_key(tuple(sorted(scopes)))
		if not force:
			cached = _cache_get(key)
			if cached:
				self._token, self._token_expires, self.granted_scopes = cached["token"], cached["expires"], cached.get("scopes", "")
				if time.time() < self._token_expires:
					return self._token
		token, lifetime, granted = self._exchange(scopes)
		self._token, self._token_expires, self.granted_scopes = token, time.time() + max(60, lifetime - TOKEN_MARGIN), granted
		_cache_set(key, {"token": token, "expires": self._token_expires, "scopes": granted}, int(self._token_expires - time.time()))
		return token

	def forget_token(self, scopes: tuple = (ADWORDS_SCOPE,)) -> None:
		self._token, self._token_expires = None, 0.0
		frappe.cache.delete_value(self._cache_key(tuple(sorted(scopes))))

	def _exchange(self, scopes: tuple):
		if self.mode == SERVICE_ACCOUNT:
			import jwt

			now = int(time.time())
			claims = {"iss": self._sa["client_email"], "scope": " ".join(scopes), "aud": TOKEN_URI, "iat": now, "exp": now + JWT_LIFETIME}
			headers = {"kid": self._sa["private_key_id"]} if self._sa.get("private_key_id") else None
			assertion = jwt.encode(claims, self._sa["private_key"], algorithm="RS256", headers=headers)
			form = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
		else:
			form = {"grant_type": "refresh_token", "client_id": self._client_id, "client_secret": self._client_secret, "refresh_token": self._refresh}
		status, body, headers = http_request("POST", TOKEN_URI, headers={"Content-Type": "application/x-www-form-urlencoded"}, form=form, timeout=30)
		self.stats_calls += 1
		body = body if isinstance(body, dict) else {}
		if status == 200 and body.get("access_token"):
			granted = body.get("scope") or " ".join(scopes)
			if self.mode == OAUTH and body.get("scope") and not set(scopes) <= set(granted.split()):
				missing = ", ".join(sorted(set(scopes) - set(granted.split())))
				raise AdsProviderError(f"Ads Connection {self.connection}: the refresh token was not granted {missing}. Sign in again and allow access.", PERMISSION)
			return body["access_token"], int(body.get("expires_in") or 3600), granted
		raise self._token_error(status, body, headers)

	def _token_error(self, status: int, body: dict, headers) -> AdsProviderError:
		err = body.get("error")
		desc = body.get("error_description") or ""
		if isinstance(err, dict):  # a proxy or Google front end answered instead of the OAuth server
			err, desc = err.get("status") or "", err.get("message") or desc
		err = str(err or "")
		if status == 429:
			cls = RATE_LIMITED
		elif status >= 500:
			cls = RETRYABLE
		elif err in SCOPE_ERRORS:
			cls = PERMISSION
		elif err in TOKEN_ERRORS or status in (400, 401, 403):
			cls = TOKEN
		else:
			cls = RETRYABLE if not status else TOKEN
		hint = {
			"invalid_grant": "The credential was revoked, expired or the service account key was deleted. Paste a new key or sign in again.",
			"invalid_client": "The OAuth client ID or secret is wrong.",
			"unauthorized_client": "This client may not use this sign-in method.",
		}.get(err, "")
		msg = f"Google sign-in failed for Ads Connection {self.connection} (HTTP {status} {err}): {desc} {hint}".strip()
		return AdsProviderError(self.scrub(msg)[:500], cls, retry_after=_retry_after_header(headers), code=err or status)


def parse_scopes(value) -> list[str]:
	out = []
	for s in re.split(r"[\s,]+", value or ""):
		if s and s not in out:
			out.append(s)
	return out


def _retry_after_header(headers) -> int | None:
	try:
		v = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
		return int(v) if v else None
	except (TypeError, ValueError):
		return None


def _cache_get(key: str):
	# Fernet directly rather than password.decrypt(), which frappe.throw()s (and queues a user
	# message) on a stale entry after an encryption-key change. A bad entry is simply a miss.
	from cryptography.fernet import Fernet
	from frappe.utils.password import get_encryption_key

	try:
		raw = frappe.cache.get_value(key, expires=True)  # expires=True: never pin a miss in frappe.local.cache
		if not raw:
			return None
		return json.loads(Fernet(get_encryption_key().encode()).decrypt(raw.encode() if isinstance(raw, str) else raw))
	except Exception:
		return None


def _cache_set(key: str, value: dict, ttl: int) -> None:
	from frappe.utils.password import encrypt

	try:
		frappe.cache.set_value(key, encrypt(json.dumps(value)), expires_in_sec=max(ttl, 1))
	except Exception:
		pass  # a cache outage costs one extra token exchange per job, nothing more
