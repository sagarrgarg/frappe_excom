import frappe

no_cache = 1


def get_context(context):
	context.no_breadcrumbs = True
	context.company = frappe.db.get_single_value("Website Settings", "app_name") or frappe.defaults.get_global_default("company") or "Us"
	context.site = frappe.utils.get_url()
	context.title = f"Terms of Service — {context.company} messaging"
