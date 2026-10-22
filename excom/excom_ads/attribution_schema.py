"""Ads attribution fields on the native CRM records (Lead, Opportunity) and the indexes the lead
resolver reads. Custom Fields only, never ERPNext JSON. Idempotent: runs from setup.seed() after
every migrate, on v15 and v16 alike (none of these fields exists natively on either version).

The Lead keeps a copy of its FIRST ad touch (first touch wins); every observation, including later
ones, is an Ads Attribution Touch row. Same fieldnames on Lead and Opportunity, so ERPNext's own
Lead → Opportunity mapping carries them across.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from excom.excom.services import crm_compat
from excom.excom.services import crm_gateway as gw

LINK_STATUS_OPTIONS = "\nLinked\nPartial\nPending\nOrganic\nNo Ad IDs"
CLICK_ID_FIELDS = ("gclid", "gbraid", "wbraid", "fbclid", "fbc", "fbp", "ctwa_clid", "li_fat_id")
# Attribution rows (Lead Source on v15, UTM Source on v16) the ads intake paths name.
ATTRIBUTION_SOURCES = ["Click to WhatsApp", "Google Lead Form"]


def _fields(insert_after: str) -> list[dict]:
	ro = {"read_only": 1, "no_copy": 0}
	out = [
		{"fieldname": "ads_sec_attribution", "fieldtype": "Section Break", "label": "Ads Attribution", "collapsible": 1, "insert_after": insert_after},
		{"fieldname": "ads_touch_source", "fieldtype": "Data", "label": "Ad Touch Source", **ro},
		{"fieldname": "ads_provider", "fieldtype": "Data", "label": "Ad Platform", **ro},
		{"fieldname": "ads_first_touch_at", "fieldtype": "Datetime", "label": "First Ad Touch At", **ro},
		{"fieldname": "ads_first_touch", "fieldtype": "Link", "label": "First Ad Touch", "options": "Ads Attribution Touch", "search_index": 1, **ro},
		{"fieldname": "ads_link_status", "fieldtype": "Select", "label": "Ad Link Status", "options": LINK_STATUS_OPTIONS, "search_index": 1, "in_standard_filter": 1, **ro},
		{"fieldname": "ads_is_test", "fieldtype": "Check", "label": "Platform Test Lead", "default": "0", "in_standard_filter": 1, "description": "Sent by the ad platform's test tool. Kept out of every KPI.", **ro},
		{"fieldname": "ads_is_organic", "fieldtype": "Check", "label": "Organic (not from a paid ad)", "default": "0", **ro},
		{"fieldname": "ads_cb_links", "fieldtype": "Column Break"},
		{"fieldname": "ads_account", "fieldtype": "Link", "label": "Ad Account", "options": "Ads Account", **ro},
		{"fieldname": "ads_campaign", "fieldtype": "Link", "label": "Ad Campaign", "options": "Ads Campaign", "in_standard_filter": 1, **ro},
		{"fieldname": "ads_ad_group", "fieldtype": "Link", "label": "Ad Set / Ad Group", "options": "Ads Ad Group", **ro},
		{"fieldname": "ads_advertisement", "fieldtype": "Link", "label": "Ad", "options": "Ads Advertisement", **ro},
		{"fieldname": "ads_form_id", "fieldtype": "Data", "label": "Lead Form ID", **ro},
		{"fieldname": "ads_page_id", "fieldtype": "Data", "label": "Page ID", **ro},
		{"fieldname": "ads_cb_ids", "fieldtype": "Column Break"},
		{"fieldname": "ads_campaign_id", "fieldtype": "Data", "label": "Campaign ID", **ro},
		{"fieldname": "ads_campaign_name", "fieldtype": "Data", "label": "Campaign Name", "length": 255, **ro},
		{"fieldname": "ads_ad_group_id", "fieldtype": "Data", "label": "Ad Set / Ad Group ID", **ro},
		{"fieldname": "ads_ad_group_name", "fieldtype": "Data", "label": "Ad Set / Ad Group Name", "length": 255, **ro},
		{"fieldname": "ads_ad_id", "fieldtype": "Data", "label": "Ad ID", **ro},
		{"fieldname": "ads_ad_name", "fieldtype": "Data", "label": "Ad Name", "length": 255, **ro},
		{"fieldname": "ads_cb_clicks", "fieldtype": "Column Break"},
	]
	labels = {"gclid": "GCLID", "gbraid": "GBRAID", "wbraid": "WBRAID", "fbclid": "FBCLID", "fbc": "FBC", "fbp": "FBP", "ctwa_clid": "CTWA Click ID", "li_fat_id": "LinkedIn Click ID"}
	out += [{"fieldname": f"ads_{c}", "fieldtype": "Data", "label": labels[c], "length": 500, **ro} for c in CLICK_ID_FIELDS]
	# insert_after chains the fields in the order listed.
	for prev, cur in zip(out, out[1:]):
		cur["insert_after"] = prev["fieldname"]
	return out


def custom_fields() -> dict:
	return {gw.LEAD: _fields("auto_ack_sent_at"), gw.OPPORTUNITY: _fields("source_reference")}


# The resolver looks mirrors up by platform ID: a Lead carries ad / ad set / campaign IDs, not names.
INDEXES = {
	"Ads Campaign": [("ads_campaign_external_id", ["external_id"])],
	"Ads Ad Group": [("ads_ad_group_external_id", ["external_id"])],
	"Ads Advertisement": [("ads_advertisement_external_id", ["external_id"])],
	"Ads Attribution Touch": [("ads_touch_relink", ["link_status", "touch_at"])],
	# verified.py: leads / opportunities per campaign and day, and "did this lead reach an opportunity".
	gw.LEAD: [("ads_lead_campaign_created", ["ads_campaign", "creation"])],
	gw.OPPORTUNITY: [("ads_opp_campaign_created", ["ads_campaign", "creation"]), ("ads_opp_party_name", ["party_name"])],
}


def apply() -> None:
	fields = {}
	for dt, defs in custom_fields().items():
		if not frappe.db.exists("DocType", dt):
			continue
		native = {f.fieldname for f in frappe.get_meta(dt).fields if not getattr(f, "is_custom_field", 0)}
		fields[dt] = [d for d in defs if d["fieldname"] not in native]
	create_custom_fields(fields, ignore_validate=True, update=True)
	for doctype, indexes in INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for name, columns in indexes:
			frappe.db.add_index(doctype, columns, index_name=name)
	crm_compat.seed_attribution_rows(ATTRIBUTION_SOURCES)
	for dt in fields:
		frappe.clear_cache(doctype=dt)
