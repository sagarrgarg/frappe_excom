"""
P3 §3.2 (N1) — native CRM schema as excom fixtures. Idempotent; runs after every migrate.
Custom Fields only (never edits ERPNext JSON). Doctype names appear here by design (allowed by crm-gates).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from excom.excom.services import crm_compat
from excom.excom.services.crm_gateway import CUSTOMER_TYPES, FIRST_TOUCH_CHANNELS, INTAKE_STAGES, STAGES

CT_OPTIONS = "\n" + "\n".join(CUSTOMER_TYPES)
STAGE_OPTIONS = "\n" + "\n".join(STAGES.keys())

CUSTOM_FIELDS = {
	"Lead": [
		{"fieldname": "excom_tab", "fieldtype": "Tab Break", "label": "Excom", "insert_after": "notes"},
		{"fieldname": "excom_sec_type", "fieldtype": "Section Break", "label": "Classification", "insert_after": "excom_tab"},
		{"fieldname": "customer_type", "fieldtype": "Select", "label": "Customer Type", "options": CT_OPTIONS, "insert_after": "excom_sec_type", "in_list_view": 1, "in_standard_filter": 1},
		{"fieldname": "intake_stage", "fieldtype": "Select", "label": "Intake Stage", "options": "\n" + "\n".join(INTAKE_STAGES), "insert_after": "customer_type", "in_standard_filter": 1},
		{"fieldname": "excom_cb_type", "fieldtype": "Column Break", "insert_after": "intake_stage"},
		{"fieldname": "omni_identity", "fieldtype": "Link", "label": "Omni Identity", "options": "Omni Identity", "read_only": 1, "insert_after": "excom_cb_type"},
		{"fieldname": "intake_source", "fieldtype": "Link", "label": "Intake Source", "options": "Excom Source", "read_only": 1, "insert_after": "omni_identity"},
		{"fieldname": "excom_team", "fieldtype": "Link", "label": "Team", "options": "Excom Team", "insert_after": "intake_source", "in_standard_filter": 1, "description": "Who this lead belongs to. Set when it is assigned; drives who can see it."},
		{"fieldname": "excom_sec_prov", "fieldtype": "Section Break", "label": "Provenance (write-once)", "insert_after": "intake_source", "collapsible": 1},
		{"fieldname": "first_touch_at", "fieldtype": "Datetime", "label": "First Touch At", "read_only": 1, "insert_after": "excom_sec_prov"},
		{"fieldname": "first_touch_channel", "fieldtype": "Select", "label": "First Touch Channel", "options": "\n" + "\n".join(FIRST_TOUCH_CHANNELS), "read_only": 1, "insert_after": "first_touch_at"},
		{"fieldname": "first_touch_by", "fieldtype": "Link", "label": "First Touch By", "options": "User", "read_only": 1, "insert_after": "first_touch_channel"},
		{"fieldname": "excom_cb_prov", "fieldtype": "Column Break", "insert_after": "first_touch_by"},
		{"fieldname": "source_reference", "fieldtype": "Data", "label": "Source Reference", "read_only": 1, "insert_after": "excom_cb_prov", "description": "Marketplace enquiry id"},
		{"fieldname": "exhibition", "fieldtype": "Data", "label": "Exhibition", "insert_after": "source_reference"},
		{"fieldname": "auto_ack_sent_at", "fieldtype": "Datetime", "label": "Auto-ack Sent At", "read_only": 1, "insert_after": "exhibition"},
	],
	"Opportunity": [
		{"fieldname": "excom_tab", "fieldtype": "Tab Break", "label": "Excom", "insert_after": "notes"},
		{"fieldname": "excom_sec_pipe", "fieldtype": "Section Break", "label": "Pipeline", "insert_after": "excom_tab"},
		{"fieldname": "customer_type", "fieldtype": "Select", "label": "Customer Type", "options": CT_OPTIONS, "insert_after": "excom_sec_pipe", "in_list_view": 1, "in_standard_filter": 1, "fetch_from": "party_name.customer_type", "fetch_if_empty": 1},
		{"fieldname": "pipeline_stage", "fieldtype": "Select", "label": "Pipeline Stage", "options": STAGE_OPTIONS, "insert_after": "customer_type", "in_standard_filter": 1},
		{"fieldname": "stage_entered_at", "fieldtype": "Datetime", "label": "Stage Entered At", "read_only": 1, "insert_after": "pipeline_stage"},
		{"fieldname": "excom_cb_pipe", "fieldtype": "Column Break", "insert_after": "stage_entered_at"},
		{"fieldname": "next_action_at", "fieldtype": "Datetime", "label": "Next Action At", "insert_after": "excom_cb_pipe", "mandatory_depends_on": "eval:doc.status=='Open' && doc.pipeline_stage && doc.pipeline_stage!='Won'"},
		{"fieldname": "omni_identity", "fieldtype": "Link", "label": "Omni Identity", "options": "Omni Identity", "read_only": 1, "insert_after": "next_action_at"},
		{"fieldname": "gate_flags", "fieldtype": "Small Text", "label": "Gate Flags (JSON)", "read_only": 1, "insert_after": "omni_identity"},
		{"fieldname": "excom_team", "fieldtype": "Link", "label": "Team", "options": "Excom Team", "insert_after": "gate_flags", "in_standard_filter": 1},
		{"fieldname": "excom_sec_type", "fieldtype": "Section Break", "label": "Type-specific", "insert_after": "gate_flags"},
		{"fieldname": "event_date", "fieldtype": "Date", "label": "Event Date", "insert_after": "excom_sec_type", "depends_on": "eval:doc.customer_type=='Corporate Gifting'"},
		{"fieldname": "design_by", "fieldtype": "Select", "label": "Design By", "options": "\nCustomer\nUs", "insert_after": "event_date", "depends_on": "eval:doc.customer_type=='OEM'"},
		{"fieldname": "sample_round", "fieldtype": "Int", "label": "Sample Round", "insert_after": "design_by", "depends_on": "eval:doc.customer_type=='OEM'"},
		{"fieldname": "excom_cb_type", "fieldtype": "Column Break", "insert_after": "sample_round"},
		{"fieldname": "incoterm", "fieldtype": "Link", "label": "Incoterm", "options": "Incoterm", "insert_after": "excom_cb_type", "depends_on": "eval:doc.customer_type=='Export Importer'"},
		{"fieldname": "proposed_pincodes", "fieldtype": "Small Text", "label": "Proposed Pincodes", "insert_after": "incoterm", "depends_on": "eval:['Distributor','Retailer'].includes(doc.customer_type)"},
		{"fieldname": "first_touch_at", "fieldtype": "Datetime", "label": "First Touch At", "read_only": 1, "insert_after": "proposed_pincodes", "hidden": 1},
		{"fieldname": "first_touch_channel", "fieldtype": "Data", "label": "First Touch Channel", "read_only": 1, "insert_after": "first_touch_at", "hidden": 1},
		{"fieldname": "first_touch_by", "fieldtype": "Link", "label": "First Touch By", "options": "User", "read_only": 1, "insert_after": "first_touch_channel", "hidden": 1},
		{"fieldname": "source_reference", "fieldtype": "Data", "label": "Source Reference", "read_only": 1, "insert_after": "first_touch_by", "hidden": 1},
	],
	"Prospect": [
		{"fieldname": "customer_type", "fieldtype": "Select", "label": "Customer Type", "options": CT_OPTIONS, "insert_after": "customer_group"},
		{"fieldname": "omni_identity", "fieldtype": "Link", "label": "Omni Identity", "options": "Omni Identity", "read_only": 1, "insert_after": "customer_type"},
	],
	"Customer": [
		{"fieldname": "excom_sec_prov", "fieldtype": "Section Break", "label": "Excom Provenance", "insert_after": "customer_group", "collapsible": 1},
		{"fieldname": "excom_customer_type", "fieldtype": "Select", "label": "Customer Type (Excom)", "options": CT_OPTIONS, "insert_after": "excom_sec_prov"},
		{"fieldname": "first_touch_at", "fieldtype": "Datetime", "label": "First Touch At", "read_only": 1, "insert_after": "excom_customer_type"},
		{"fieldname": "excom_cb_prov", "fieldtype": "Column Break", "insert_after": "first_touch_at"},
		{"fieldname": "first_touch_channel", "fieldtype": "Data", "label": "First Touch Channel", "read_only": 1, "insert_after": "excom_cb_prov"},
		{"fieldname": "source_reference", "fieldtype": "Data", "label": "Source Reference", "read_only": 1, "insert_after": "first_touch_channel"},
	],
}

ATTRIBUTION_SOURCES = [
	"IndiaMART Direct", "IndiaMART Buy Lead", "IndiaMART Call", "TradeIndia", "Meta Lead Ad", "Website", "Exhibition",
	"Cold Call", "Referral", "Walk-in", "Organic WhatsApp", "Organic Email", "Organic Instagram", "Organic Web Chat", "Organic Call", "Organic Manual",
]
LOST_REASONS = ["Price", "Timeline", "Territory conflict", "Spec infeasible", "Unresponsive", "Chose competitor"]
OPPORTUNITY_TYPES = ["Distributor", "Retailer", "Export", "OEM", "Corporate Gifting"]


def apply() -> None:
	"""Idempotent. Called from after_migrate and the P3 patch."""
	# Custom fields: only for doctypes present on this site (Prospect exists v13+; guard anyway)
	fields = {}
	for dt, defs in CUSTOM_FIELDS.items():
		if not frappe.db.exists("DocType", dt):
			continue
		# Never shadow a field the doctype already ships natively (v15 Customer.customer_type, v16 adds more):
		# a Custom Field with the same fieldname replaces the native definition in meta and breaks validation.
		native = {f.fieldname for f in frappe.get_meta(dt).fields if not getattr(f, "is_custom_field", 0)}
		safe = [d for d in defs if d["fieldname"] not in native]
		for d in defs:
			if d["fieldname"] in native:
				frappe.log_error(title="Excom crm_schema: skipped custom field", message=f"{dt}.{d['fieldname']} exists natively — not created")
		fields[dt] = safe
	create_custom_fields(fields, ignore_validate=True, update=True)

	crm_compat.seed_attribution_rows(ATTRIBUTION_SOURCES)

	for r in LOST_REASONS:
		if frappe.db.exists("DocType", "Opportunity Lost Reason") and not frappe.db.exists("Opportunity Lost Reason", r):
			frappe.get_doc({"doctype": "Opportunity Lost Reason", "lost_reason": r}).insert(ignore_permissions=True, ignore_if_duplicate=True)
	for t in OPPORTUNITY_TYPES:
		if frappe.db.exists("DocType", "Opportunity Type") and not frappe.db.exists("Opportunity Type", t):
			frappe.get_doc({"doctype": "Opportunity Type", "name": t, "__newname": t}).insert(ignore_permissions=True, ignore_if_duplicate=True)

	# Sales Stage rows referenced by the stage map must exist for native funnel reports
	for st in {s["sales_stage"] for s in STAGES.values() if s["sales_stage"]}:
		if frappe.db.exists("DocType", "Sales Stage") and not frappe.db.exists("Sales Stage", st):
			frappe.get_doc({"doctype": "Sales Stage", "stage_name": st}).insert(ignore_permissions=True, ignore_if_duplicate=True)

	frappe.db.commit()
	frappe.clear_cache(doctype="Lead")
	frappe.clear_cache(doctype="Opportunity")


DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def ensure_assignment_rules(desks: dict[str, list[str]], days: list[str] | None = None) -> dict:
	"""
	HLD-003 §7.2 — one Assignment Rule per pipeline. `desks` maps rule name → user emails.
	Called by an admin once teams are known (rules need at least one user, so this is not auto-seeded).

	`assignment_days` is mandatory on Assignment Rule, and this function used to omit it, so every
	call threw MandatoryError and no Excom rule was ever created on any site. Auto-assignment has
	therefore never run. Default is all seven days; pass `days` for a weekday-only desk.
	"""
	# Frappe evaluates these with the document's own fields as locals, so a field is named bare.
	# Every one of these used to carry a `doc.` prefix, which raises NameError inside
	# AssignmentRule.safe_eval, is swallowed there, and returns False. The rules matched nothing.
	spec = {
		"Excom Intake — Unclassified": ("Lead", "not customer_type", "Round Robin"),
		"Excom Distributor Desk": ("Opportunity", 'customer_type == "Distributor"', "Round Robin"),
		"Excom Retailer Desk": ("Opportunity", 'customer_type == "Retailer"', "Load Balancing"),
		"Excom Export Desk": ("Opportunity", 'customer_type == "Export Importer"', "Round Robin"),
		"Excom OEM Desk": ("Opportunity", 'customer_type == "OEM"', "Load Balancing"),
		"Excom Gifting Desk": ("Opportunity", 'customer_type == "Corporate Gifting"', "Round Robin"),
	}
	created, repaired = [], []
	for name, users in desks.items():
		if name not in spec or not users:
			continue
		doctype, cond, rule = spec[name]
		unassign = 'status == "Do Not Contact"' if doctype == "Lead" else 'status in ("Lost", "Closed")'
		if frappe.db.exists("Assignment Rule", name):
			# Repair a rule created by an older version rather than leaving a rule that matches
			# nothing: "it exists" is not the same as "it works".
			existing = frappe.get_doc("Assignment Rule", name)
			changed = False
			for field, value in (("assign_condition", cond), ("unassign_condition", unassign)):
				if existing.get(field) != value:
					existing.set(field, value)
					changed = True
			if not existing.get("assignment_days"):
				existing.set("assignment_days", [{"day": d} for d in (days or DAYS)])
				changed = True
			if changed:
				existing.flags.ignore_permissions = True
				existing.save()
				repaired.append(name)
			continue
		frappe.get_doc(
			{
				"doctype": "Assignment Rule",
				"__newname": name,
				"document_type": doctype,
				"assign_condition": cond,
				"unassign_condition": unassign,
				"rule": rule,
				"priority": 5,
				"assignment_days": [{"day": d} for d in (days or DAYS)],
				"users": [{"user": u} for u in users],
			}
		).insert(ignore_permissions=True)
		created.append(name)
	return {"created": created, "repaired": repaired}
