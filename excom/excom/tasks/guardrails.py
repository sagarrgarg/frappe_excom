"""
Native-CRM guardrails (RES-001 §2.3 G2–G6). A config-drift detector: logs, never throws.
Scheduled daily; also callable from Desk / bench for an on-demand report.
"""

import frappe
from frappe.utils import add_days, nowdate

GO_LIVE_DATE = "2026-09-03"


def assert_native_crm_only(log: bool = True) -> list[str]:
	findings: list[str] = []

	if frappe.db.exists("DocType", "ERPNext CRM Settings") and frappe.db.get_single_value("ERPNext CRM Settings", "enabled"):
		findings.append("G2: ERPNext CRM Settings.enabled = 1 (Frappe CRM ↔ ERPNext bridge is on)")

	if frappe.db.exists("DocType", "CRM Settings"):
		meta = frappe.get_meta("CRM Settings")
		if meta.has_field("enable_frappe_crm_data_synchronization") and frappe.db.get_single_value("CRM Settings", "enable_frappe_crm_data_synchronization"):
			findings.append("G3: CRM Settings.enable_frappe_crm_data_synchronization = 1")
		if frappe.db.exists("DocType", "Frappe CRM Allowed User") and frappe.db.count("Frappe CRM Allowed User"):
			findings.append("G3: CRM Settings.allowed_users is not empty")

	if frappe.get_meta("Email Account").has_field("create_lead_from_incoming_email"):
		bad = frappe.get_all("Email Account", filters={"create_lead_from_incoming_email": 1}, pluck="name")
		if bad:
			findings.append(f"G4: Email Account(s) create CRM Leads from incoming email: {', '.join(bad)}")

	shadow = frappe.get_all(
		"Custom Field",
		filters={"dt": ["in", ["Quotation", "Customer", "Item", "Prospect"]], "fieldname": ["in", ["crm_deal", "crm_product_code"]]},
		fields=["dt", "fieldname"],
	)
	if shadow:
		findings.append("G5: Frappe CRM bridge fields present: " + ", ".join(f"{s.dt}.{s.fieldname}" for s in shadow))

	for dt in ("CRM Lead", "CRM Deal"):
		if frappe.db.exists("DocType", dt):
			n = frappe.db.count(dt, {"creation": [">=", GO_LIVE_DATE]})
			if n:
				findings.append(f"G6: {n} {dt} row(s) created after go-live {GO_LIVE_DATE} — a shadow funnel is active")

	if frappe.db.exists("Installed Application", {"app_name": "crm"}) or "crm" in frappe.get_installed_apps():
		findings.append("G1: Frappe CRM app is still installed on this site (uninstall after N1 sign-off)")

	if log and findings:
		frappe.log_error(title="Excom native-CRM guardrails", message="\n".join(findings))
	return findings
