"""
Fork rehearsal (P4 §4.4): an excom-owned "Excom Lead" behind the *same* gateway interface.

Not shipped as an app doctype: `install()` creates it as a site-level Custom DocType on a scratch
site, `use("shadow")` points the gateway's LEAD at it, and the contract suite runs unchanged.
`use("native")` puts everything back. Never call `use("shadow")` on a production site.

What it proves: no caller reaches around the gateway, the payload → field mapping is complete,
and a real fork is mechanical. What it does not do: fork Opportunity (quoting stays native).
"""

import frappe

from excom.excom.services import crm_gateway as gw

SHADOW_LEAD = "Excom Lead"

# Same conceptual fields as the native Lead subset excom uses (manifest native_fields_reused + excom_custom_fields).
FIELDS = [
	("lead_name", "Data", "Full Name", {"reqd": 1, "in_list_view": 1}),
	("company_name", "Data", "Organization", {}),
	("email_id", "Data", "Email", {"options": "Email"}),
	("mobile_no", "Data", "Mobile", {"options": "Phone"}),
	("whatsapp_no", "Data", "WhatsApp", {}),
	("status", "Select", "Status", {"options": "Lead\nOpen\nReplied\nOpportunity\nInterested\nConverted\nDo Not Contact", "default": "Lead", "in_list_view": 1}),
	("lead_owner", "Link", "Lead Owner", {"options": "User"}),
	("company", "Link", "Company", {"options": "Company"}),
	("territory", "Link", "Territory", {"options": "Territory"}),
	("country", "Link", "Country", {"options": "Country"}),
	("state", "Data", "State", {}),
	("city", "Data", "City", {}),
	("request_type", "Select", "Request Type", {"options": "\nProduct Enquiry\nRequest for Information\nSuggestions\nOther"}),
	("customer_type", "Select", "Customer Type", {"options": "\n" + "\n".join(gw.CUSTOMER_TYPES)}),
	("intake_stage", "Select", "Intake Stage", {"options": "\n".join(gw.INTAKE_STAGES), "default": "Captured"}),
	("intake_source", "Link", "Intake Source", {"options": "Excom Source"}),
	("omni_identity", "Link", "Omni Identity", {"options": "Omni Identity"}),
	("first_touch_at", "Datetime", "First Touch At", {}),
	("first_touch_channel", "Select", "First Touch Channel", {"options": "\n" + "\n".join(gw.FIRST_TOUCH_CHANNELS)}),
	("first_touch_by", "Link", "First Touch By", {"options": "User"}),
	("source_reference", "Data", "Source Reference", {}),
	("exhibition", "Data", "Exhibition", {}),
	("auto_ack_sent_at", "Datetime", "Auto-ack Sent At", {}),
	("qualification_status", "Select", "Qualification Status", {"options": "\nUnqualified\nIn Process\nQualified"}),
	("source", "Data", "Source", {}),  # attribution stored as plain text in the fork (no Lead Source master)
	("campaign_name", "Data", "Campaign", {}),
]


def install() -> str:
	"""Create the shadow doctype on this site (idempotent). Custom DocType, module Excom, naming EXL-.#####."""
	if frappe.db.exists("DocType", SHADOW_LEAD):
		reconcile()
		return SHADOW_LEAD
	doc = frappe.get_doc(
		{
			"doctype": "DocType", "name": SHADOW_LEAD, "module": "Excom", "custom": 1, "autoname": "EXL-.#####", "title_field": "lead_name",
			"track_changes": 1, "allow_rename": 0,
			"fields": [{"fieldname": fn, "fieldtype": ft, "label": lb, **extra} for fn, ft, lb, extra in FIELDS],
			"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}, {"role": "Excom Manager", "read": 1, "write": 1, "create": 1}, {"role": "Excom User", "read": 1, "write": 1, "create": 1}],
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return SHADOW_LEAD


def reconcile() -> dict:
	"""Bring an already-installed shadow in line with FIELDS.

	Returning early on "it exists" is not idempotent, it is a blind spot: the shadow was created
	before Excom Intake Source was renamed to Excom Source, so its link pointed at a doctype that no
	longer exists and nothing noticed. Adds missing fields and repairs drifted options and labels.
	"""
	doc = frappe.get_doc("DocType", SHADOW_LEAD)
	have = {f.fieldname: f for f in doc.fields}
	added, repaired = [], []
	for fn, ft, lb, extra in FIELDS:
		field = have.get(fn)
		if not field:
			doc.append("fields", {"fieldname": fn, "fieldtype": ft, "label": lb, **extra})
			added.append(fn)
			continue
		for key, value in {"fieldtype": ft, "label": lb, **extra}.items():
			if field.get(key) != value:
				field.set(key, value)
				repaired.append(f"{fn}.{key}")
	if added or repaired:
		doc.flags.ignore_permissions = True
		doc.save()
		frappe.db.commit()
	return {"added": added, "repaired": repaired}


def uninstall() -> None:
	if frappe.db.exists("DocType", SHADOW_LEAD):
		frappe.delete_doc("DocType", SHADOW_LEAD, force=True, ignore_permissions=True)
		frappe.db.commit()


_NATIVE = {"LEAD": gw.LEAD}


def use(backend: str = "native") -> str:
	"""Point the gateway at the shadow or back at native. Also swaps the conversion helpers."""
	if backend == "shadow":
		if not frappe.db.exists("DocType", SHADOW_LEAD):
			install()
		gw.LEAD = SHADOW_LEAD
		gw.KIND_DOCTYPES[:] = [gw.CUSTOMER, "Supplier", "Employee", gw.OPPORTUNITY, SHADOW_LEAD]
		gw.convert = _shadow_convert
	else:
		gw.LEAD = _NATIVE["LEAD"]
		gw.KIND_DOCTYPES[:] = [gw.CUSTOMER, "Supplier", "Employee", gw.OPPORTUNITY, gw.LEAD]
		gw.convert = _native_convert
	return gw.LEAD


_native_convert = gw.convert


def _shadow_convert(r, target: str):
	"""Lead → Opportunity without ERPNext's make_opportunity: the fork keeps quoting native, so the
	Opportunity is created from a Prospect-less party (opportunity_from = Lead is impossible for a
	non-native Lead). The rehearsal therefore maps the shadow lead into a native Opportunity by contact."""
	if r.doctype != SHADOW_LEAD or target != gw.OPPORTUNITY:
		frappe.throw(f"Shadow backend converts {SHADOW_LEAD} → Opportunity only")
	src = frappe.get_doc(SHADOW_LEAD, r.name)
	src.check_permission("write")
	new = frappe.get_doc(
		{
			"doctype": gw.OPPORTUNITY, "opportunity_from": gw.PROSPECT, "party_name": None,
			"customer_name": src.lead_name, "contact_email": src.email_id, "contact_mobile": src.mobile_no,
			"company": src.company, "territory": src.territory, "customer_type": src.customer_type,
			"omni_identity": src.omni_identity, "first_touch_at": src.first_touch_at, "first_touch_channel": src.first_touch_channel,
			"first_touch_by": src.first_touch_by, "source_reference": src.source_reference, "opportunity_owner": src.lead_owner,
			"pipeline_stage": "Qualified", "stage_entered_at": frappe.utils.now_datetime(), "status": "Open",
		}
	)
	# ERPNext requires opportunity_from + party_name; a real fork would relax that on its own Opportunity.
	# For the rehearsal we anchor the opportunity to a Prospect created from the shadow lead's organisation.
	prospect = frappe.get_doc({"doctype": gw.PROSPECT, "company_name": src.company_name or src.lead_name, "customer_type": src.customer_type, "omni_identity": src.omni_identity})
	prospect.flags.ignore_permissions = True
	prospect.insert(ignore_if_duplicate=True)
	new.opportunity_from = gw.PROSPECT
	new.party_name = prospect.name
	new.flags.ignore_mandatory = True
	new.flags.ignore_permissions = True
	new.insert()
	frappe.db.set_value(SHADOW_LEAD, r.name, "status", "Opportunity", update_modified=False)
	return gw.ref(new.doctype, new.name)
