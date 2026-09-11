"""Giving an agent a softphone, and taking it away.

One SIP endpoint per agent per line. Provisioning is idempotent and driven from the admin screen or
in bulk for a whole line, because asking an administrator to create endpoints by hand is how you end
up with half a team unable to take calls and nobody knowing why.

The password Plivo gets is generated here and stored encrypted. It never leaves the server: the
browser logs in with a short-lived token from `mint_token()`.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.channels.voice import providers, routing
from excom.excom.channels.voice.providers.base import CAP_WEBRTC

# Long enough that a working day needs one silent refresh, short enough that a token lifted from a
# browser is worthless by tomorrow. Refreshed by the client at 50 minutes.
TOKEN_TTL = 3600


def _safe_alias(full_name: str, account_name: str) -> str:
	"""A label the provider will accept.

	Plivo allows letters, numbers, hyphen and underscore in an endpoint alias — no spaces and no
	punctuation. A prettier alias is rejected with a 400 that only says "Invalid Endpoint Alias
	name", so this is sanitised here rather than left to whoever names a channel account.
	"""
	import re

	parts = [re.sub(r"[^A-Za-z0-9]+", "_", str(p or "")).strip("_") for p in (full_name, account_name)]
	alias = "_".join(["Excom", *[p for p in parts if p]])
	return re.sub(r"_+", "_", alias)[:60].strip("_") or "Excom_agent"


def ensure_endpoint(user: str, account: str) -> str:
	"""The agent's endpoint on this line, created if it does not exist yet."""
	existing = frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": account, "status": ["!=", "Deprovisioned"]},
		"name",
	)
	if existing:
		if frappe.db.get_value("Excom Voice Endpoint", existing, "status") == "Suspended":
			frappe.throw(_("{0}'s softphone on this line is suspended.").format(user))
		return existing

	account_doc = frappe.get_doc("Excom Channel Account", account)
	provider = providers.for_account(account_doc)
	if CAP_WEBRTC not in provider.capabilities():
		frappe.throw(
			_("Browser calling is not available on this line, so softphones cannot be created.")
		)

	full_name = frappe.db.get_value("User", user, "full_name") or user
	alias = _safe_alias(full_name, account_doc.account_name)

	# Reuse before create. The provider call and the row that records it are not one transaction, so
	# a rollback after a successful create leaves an orphan at the provider; creating again would
	# quietly give one agent two registrations and send half their calls to a socket nobody watches.
	ref = provider.find_endpoint(alias) or provider.provision_endpoint(user, alias)

	doc = frappe.new_doc("Excom Voice Endpoint")
	doc.user = user
	doc.channel_account = account
	doc.provider = account_doc.get("voice_provider")
	doc.endpoint_id = ref.endpoint_id
	doc.endpoint_username = ref.username
	doc.endpoint_password = ref.password
	doc.sip_uri = ref.sip_uri
	doc.alias = alias
	doc.status = "Active"
	doc.insert(ignore_permissions=True)
	# The endpoint already exists at the provider by this point, so the row that remembers it must
	# survive too. Without this an enqueued job or a console run rolls the row back and leaves the
	# provider holding an endpoint nothing in Excom knows about.
	frappe.db.commit()

	routing.clear_caches(account)
	return doc.name


def remove_endpoint(name: str, delete_remote: bool = True) -> None:
	"""Take a softphone away. The row is kept as Deprovisioned so call history still resolves the
	SIP URI back to a person."""
	doc = frappe.get_doc("Excom Voice Endpoint", name)
	if delete_remote and doc.endpoint_id:
		provider = providers.for_account(doc.channel_account)
		provider.deprovision_endpoint(doc.endpoint_id)
	doc.status = "Deprovisioned"
	doc.save(ignore_permissions=True)
	routing.clear_caches(doc.channel_account)


def sync_line(account: str) -> dict:
	"""Give every agent who works this line a softphone, and retire the ones who no longer do.

	Idempotent. Safe to press twice, and safe to press after a team change — which is exactly when
	an administrator will press it.
	"""
	agents = {a["user"] for a in routing.line_agents(account)}
	existing = {
		row.user: row.name
		for row in frappe.get_all(
			"Excom Voice Endpoint",
			filters={"channel_account": account, "status": ["!=", "Deprovisioned"]},
			fields=["name", "user"],
		)
	}

	created, removed, failed = [], [], []
	for user in sorted(agents - set(existing)):
		try:
			ensure_endpoint(user, account)
			created.append(user)
		except Exception as exc:
			failed.append({"user": user, "error": str(exc)[:200]})
			frappe.log_error(
				f"Could not provision a softphone for {user} on {account}: {exc}",
				"Excom Voice Provisioning",
			)

	for user in sorted(set(existing) - agents):
		try:
			remove_endpoint(existing[user])
			removed.append(user)
		except Exception as exc:
			failed.append({"user": user, "error": str(exc)[:200]})

	routing.clear_caches(account)
	return {"created": created, "removed": removed, "failed": failed}


def mint_token(user: str, account: str) -> dict:
	"""A short-lived login credential for this agent's own softphone.

	Deliberately takes no user parameter from the request — the caller is always the subject. An
	endpoint that mints a token for an arbitrary user is an endpoint that hands anyone a phone line
	billed to the company.
	"""
	name = frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": account, "status": "Active"},
		"name",
	)
	if not name:
		frappe.throw(
			_("You do not have a softphone on this line yet. An administrator can set one up."),
			frappe.DoesNotExistError,
		)

	endpoint = frappe.get_doc("Excom Voice Endpoint", name)
	account_doc = frappe.get_cached_doc("Excom Channel Account", account)
	provider = providers.for_account(account_doc)

	token = provider.mint_access_token(endpoint.endpoint_username, TOKEN_TTL)
	descriptor = provider.sdk_descriptor()

	return {
		"token": token,
		"username": endpoint.endpoint_username,
		"sip_uri": endpoint.sip_uri,
		"account": account,
		"expires_in": TOKEN_TTL,
		"refresh_after": int(TOKEN_TTL * 0.83),
		"sdk": descriptor.get("sdk"),
		"options": descriptor.get("options", {}),
		"capabilities": sorted(provider.capabilities()),
	}


def note_registration(user: str, account: str, ip: str = "") -> None:
	"""Heartbeat bookkeeping. Liveness lives in the cache; the doctype only keeps a last-seen for
	an administrator debugging why somebody is not getting calls."""
	from excom.excom.channels.voice import presence

	presence.mark_registered(user, account, ip)
	name = frappe.db.get_value(
		"Excom Voice Endpoint",
		{"user": user, "channel_account": account, "status": "Active"},
		"name",
	)
	if not name:
		return
	# update_modified=False: a heartbeat is not a document edit and must not churn the modified
	# timestamp that the admin list sorts on.
	frappe.db.set_value(
		"Excom Voice Endpoint",
		name,
		{"last_registered_at": now_datetime(), "last_seen_ip": ip or ""},
		update_modified=False,
	)
