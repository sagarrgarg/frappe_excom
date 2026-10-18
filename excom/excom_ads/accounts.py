"""Ad accounts found by Excom's Meta discovery become Ads Accounts when enabled.

Excom keeps the token on Excom Meta Connection; the Ads Account only links to it.
"""

import json

import frappe


def default_workspace(company: str) -> str:
	"""One workspace per company is created on first use, named after the company."""
	name = frappe.db.get_value("Ads Workspace", {"company": company, "enabled": 1}, "name", order_by="creation asc")
	if name:
		return name
	ws = frappe.get_doc({"doctype": "Ads Workspace", "workspace_name": company, "company": company})
	ws.insert(ignore_permissions=True)
	return ws.name


def upsert_meta_ad_account(conn, row) -> str:
	from excom.excom_ads.naming import account_name

	extra = json.loads(row.extra or "{}")
	external_id = row.asset_id if row.asset_id.startswith("act_") else f"act_{row.asset_id}"
	name = account_name("Meta", external_id)
	if frappe.db.exists("Ads Account", name):
		doc = frappe.get_doc("Ads Account", name)
	else:
		if not conn.company:
			frappe.throw(frappe._("Set a Company on {0} before enabling ad accounts").format(conn.name))
		doc = frappe.new_doc("Ads Account")
		doc.provider = "Meta"
		doc.external_id = external_id
		doc.workspace = default_workspace(conn.company)
	doc.account_name = row.asset_name or external_id
	doc.meta_connection = conn.name
	doc.sync_enabled = 1
	for src, dst in (("currency", "currency"), ("timezone_name", "timezone_name")):
		if extra.get(src) and not doc.get(dst):
			doc.set(dst, extra[src])
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.name


def disable(name: str) -> None:
	if frappe.db.exists("Ads Account", name):
		frappe.db.set_value("Ads Account", name, "sync_enabled", 0)
