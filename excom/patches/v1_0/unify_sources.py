"""One Source list: every existing Lead Source (v15) / UTM Source (v16) without a matching Excom Source
gets one (type Manual, or Channel for the 'Organic …' rows), so Admin → Sources shows everything and the
attribution master becomes a mirror. Idempotent."""

import frappe


def execute():
	from excom.excom.services.crm_compat import attribution_doctypes
	target = attribution_doctypes()["source"]
	if not frappe.db.exists("DocType", target):
		return
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	have = {frappe.db.get_value("Excom Source", n, "source_name") for n in frappe.get_all("Excom Source", pluck="name")}
	n = 0
	for name in frappe.get_all(target, pluck="name"):
		if name in have or name.startswith("QA "):
			continue
		stype = "Channel" if name.startswith("Organic ") else ("Exhibition" if "xhibition" in name else "Manual")
		doc = frappe.get_doc({"doctype": "Excom Source", "source_name": name, "source_type": stype, "enabled": 1, "company": company, "sla_first_response": 0})
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.insert()
		n += 1
	frappe.db.commit()
	print(f"sources unified: {n} created")
