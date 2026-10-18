"""Excom Ads seed data. Runs after install and after every migrate; idempotent."""

import frappe

from excom.excom_ads.permissions import MARKETING_ROLE


def after_install():
	seed()


def after_migrate():
	seed()


# The dashboard reads snapshots by (account, level, date range) and by (level, date range).
INDEXES = {
	"Ads Metric Snapshot": [("ads_snap_account_level_date", ["account", "level", "date"]), ("ads_snap_level_date", ["level", "date"])],
	# billing.summary reads the latest snapshot per account.
	"Ads Billing Snapshot": [("ads_billsnap_account_date", ["account", "snapshot_date"])],
}


def ensure_indexes():
	for doctype, indexes in INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for name, fields in indexes:
			frappe.db.add_index(doctype, fields, index_name=name)


def seed():
	# Marketing staff read ads data inside the scope their User Permissions give them. It is not an
	# inbox role: Excom Agent and Excom Admin keep their meaning.
	if not frappe.db.exists("Role", MARKETING_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": MARKETING_ROLE, "desk_access": 1}).insert(ignore_permissions=True)
	ensure_indexes()
	# Ads attribution fields on Lead / Opportunity + the indexes the lead → ad resolver reads.
	from excom.excom_ads import attribution_schema

	attribution_schema.apply()
