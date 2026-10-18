"""Developer helper: load every Excom Ads DocType JSON into the site without a full migrate.

    bench --site <site> execute excom.excom_ads.tests.dev.reload_all
"""

import glob
import json
import os

import frappe


def reload_all() -> list:
	if not frappe.db.exists("Module Def", "Excom Ads"):
		frappe.get_doc({"doctype": "Module Def", "module_name": "Excom Ads", "app_name": "excom"}).insert(ignore_permissions=True)
	frappe.clear_cache()
	frappe.setup_module_map()
	from frappe.modules.import_file import import_file_by_path

	base = frappe.get_app_path("excom", "excom_ads", "doctype")
	paths = sorted(glob.glob(os.path.join(base, "*", "*.json")))
	# Child tables first, so a parent's Table field finds its child doctype.
	paths.sort(key=lambda p: 0 if json.load(open(p)).get("istable") else 1)
	loaded = []
	for p in paths:
		import_file_by_path(p, force=True)
		loaded.append(os.path.basename(p)[:-5])
	# Excom DocTypes the ads module extends.
	for name in ("excom_meta_asset", "excom_source", "excom_thread"):
		frappe.reload_doc("excom", "doctype", name, force=True)
	from excom.excom_ads.setup import seed

	seed()
	frappe.db.commit()
	frappe.clear_cache()
	return loaded
