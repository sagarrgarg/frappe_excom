"""
P3 created Custom Field "Customer-customer_type", which replaced ERPNext's native Customer.customer_type
(Company / Individual / Partnership) in meta and made every Customer fail validation. Remove it and let
crm_schema.apply() create the renamed excom_customer_type instead. Native values were never overwritten
(the column is the native one), so nothing to restore.
"""

import frappe


def execute():
	cf = "Customer-customer_type"
	if frappe.db.exists("Custom Field", cf):
		# only ours: the native field is not a Custom Field row
		frappe.delete_doc("Custom Field", cf, force=True, ignore_permissions=True)
		frappe.db.delete("Property Setter", {"doc_type": "Customer", "field_name": "customer_type", "property": ["in", ["options", "label"]]})
		frappe.clear_cache(doctype="Customer")
	from excom.setup.crm_schema import apply
	apply()
	frappe.clear_cache(doctype="Customer")
