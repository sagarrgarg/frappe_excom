"""Google Ads connection: test, discover the accounts it reaches, enable one as an Ads Account.

No permission checks here: whitelisted wrappers in api_google.py do them. Nothing returned from
this module carries a credential.
"""

import frappe
from frappe.utils import now_datetime

from excom.excom_ads import accounts, google_auth
from excom.excom_ads.providers.base import INVALID, PERMISSION, TOKEN, AdsProviderError, CallStats
from excom.excom_ads.providers.google_ads import ACCOUNT_QUERY, CUSTOMER_CLIENT_QUERY, GoogleAdsClient

# Accessible accounts whose trees are read when the connection has no manager account set.
MAX_ROOTS = 25
STATUS = {"ENABLED": "Enabled", "SUSPENDED": "Suspended", "CANCELED": "Cancelled", "CLOSED": "Closed"}


def _dashed(cid: str) -> str:
	return f"{cid[:3]}-{cid[3:6]}-{cid[6:]}" if len(cid) == 10 else cid


def _customer(c: dict, level: int = 0) -> dict:
	cid = str(c.get("id") or "")
	return {
		"customer_id": cid,
		"customer_id_display": _dashed(cid),
		"name": c.get("descriptiveName") or "",
		"currency": c.get("currencyCode") or "",
		"time_zone": c.get("timeZone") or "",
		"status": STATUS.get(c.get("status") or "", c.get("status") or ""),
		"manager": bool(c.get("manager")),
		"test_account": bool(c.get("testAccount")),
		"hidden": bool(c.get("hidden")),
		"level": int(c.get("level") or level),
	}


def discover(connection: str) -> list[dict]:
	"""Every customer the connection reaches: the manager's tree, or (no manager set) the tree under
	each directly accessible account. Managers are listed for context but cannot be enabled."""
	client = GoogleAdsClient.for_connection(connection, stats=CallStats())
	login = client.creds.login_customer_id
	found: dict[str, dict] = {}
	if login:
		for row in client.search(login, CUSTOMER_CLIENT_QUERY, login_customer_id=login):
			c = _customer(row.get("customerClient") or {})
			found.setdefault(c["customer_id"], c)
	else:
		roots = client.list_accessible_customers()
		for root in roots[:MAX_ROOTS]:
			try:
				rows = client.search(root, CUSTOMER_CLIENT_QUERY, login_customer_id=root)
			except AdsProviderError as e:
				if e.error_class in (TOKEN,):
					raise
				continue  # one unreadable account (e.g. cancelled) must not hide the others
			for row in rows:
				c = _customer(row.get("customerClient") or {})
				found.setdefault(c["customer_id"], c)
	enabled = dict(frappe.get_all("Ads Account", filters={"provider": "Google", "external_id": ["in", list(found) or ["-"]]}, fields=["external_id", "name"], as_list=True))
	out = []
	for c in found.values():
		c["enabled_as"] = enabled.get(c["customer_id"])
		out.append(c)
	out.sort(key=lambda c: (not c["manager"], c["level"], c["name"].lower()))
	return out


def enable(connection: str, customer_id: str, enabled: int = 1, workspace: str | None = None) -> dict:
	"""Read the customer through the connection (proves access, gives name/currency/time zone) and
	upsert the Ads Account. Disabling never deletes the account or its history."""
	cid = google_auth.normalise_customer_id(customer_id)
	conn = frappe.get_doc("Ads Connection", connection)
	from excom.excom_ads.naming import account_name

	name = account_name("Google", cid)
	if not int(enabled):
		if frappe.db.exists("Ads Account", name):
			accounts.disable(name)
		return {"account": name if frappe.db.exists("Ads Account", name) else None, "enabled": 0}
	client = GoogleAdsClient.for_connection(connection, stats=CallStats())
	rows = client.search(cid, ACCOUNT_QUERY)
	if not rows:
		frappe.throw(frappe._("Google Ads returned nothing for {0}").format(_dashed(cid)))
	c = _customer(rows[0].get("customer") or {})
	if c["manager"]:
		frappe.throw(frappe._("{0} is a manager account. Enable its client accounts instead.").format(_dashed(cid)))
	acc = accounts.upsert_google_ad_account(conn, c, workspace=workspace)
	frappe.db.commit()
	from excom.excom_ads import sync

	sync.enqueue(acc, "Structure", triggered_by=frappe.session.user)
	return {"account": acc, "enabled": 1}


def test(connection: str) -> dict:
	"""One cheap read that proves the credential works, recorded on the connection. A failure
	raises a Token Problem / Permission Missing alert; the next success resolves it."""
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	conn = frappe.get_doc("Ads Connection", connection)
	keys = {TOKEN: f"connection_token:{conn.name}", PERMISSION: f"connection_permission:{conn.name}"}
	stats = CallStats()
	values = {"last_checked_at": now_datetime()}
	try:
		client = GoogleAdsClient.for_connection(connection, stats=stats)
		accessible = client.list_accessible_customers()
		if client.creds.login_customer_id:
			rows = client.search(client.creds.login_customer_id, ACCOUNT_QUERY, login_customer_id=client.creds.login_customer_id)
			if not (rows and (rows[0].get("customer") or {}).get("manager")):
				raise AdsProviderError(f"{_dashed(client.creds.login_customer_id)} is not a manager account; clear Manager Account ID or set the manager's ID", INVALID)
	except AdsProviderError as e:
		values.update({"status": "Disabled" if conn.status == "Disabled" else "Error", "last_error": str(e)[:1000], "token_status": {TOKEN: "Token Problem", PERMISSION: "Permission Missing"}.get(e.error_class, "Error")})
		frappe.db.set_value("Ads Connection", conn.name, values, update_modified=False)
		if e.error_class == TOKEN:
			raise_alert("Token Problem", "Critical", f"{conn.connection_name}: Google sign-in is not working", f"{e}\nFix the credential on Ads Connection {conn.name}. Syncs of the ad accounts using it fail until then.", ref=conn, dedupe_key=keys[TOKEN])
		elif e.error_class == PERMISSION:
			raise_alert("Permission Missing", "High", f"{conn.connection_name}: Google Ads access missing", str(e), ref=conn, dedupe_key=keys[PERMISSION])
		frappe.db.commit()
		return {"ok": False, "error_class": e.error_class, "error": str(e), "api_calls": stats.api_calls}
	values.update({"status": "Active" if conn.status != "Disabled" else "Disabled", "token_status": "OK", "last_error": None, "last_ok_at": now_datetime(), "granted_scopes": "\n".join(client.creds.granted_scopes.split()), "accessible_accounts": len(accessible)})
	frappe.db.set_value("Ads Connection", conn.name, values, update_modified=False)
	for key in keys.values():
		resolve_alert(key, "Connection works again")
	frappe.db.commit()
	return {"ok": True, "accessible_accounts": len(accessible), "granted_scopes": values["granted_scopes"].split(), "api_calls": stats.api_calls}
