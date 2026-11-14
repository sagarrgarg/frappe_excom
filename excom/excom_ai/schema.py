"""Fields the AI agents add to native records (HLD-007 Appendix C.3). Custom Fields only, never
ERPNext JSON; idempotent, applied after every migrate.

Research stays in Excom AI Company / Person. A company becomes a Prospect, and a person a Lead,
only when someone there is about to be contacted; these fields tie the two together.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

RO = {"read_only": 1}


def fields() -> dict:
	return {
		"Prospect": [
			{"fieldname": "ai_section", "fieldtype": "Section Break", "label": "AI Agent", "collapsible": 1, "insert_after": "prospect_owner"},
			{"fieldname": "ai_agent", "fieldtype": "Link", "label": "AI Agent", "options": "Excom AI Agent", "search_index": 1, "in_standard_filter": 1, **RO},
			{"fieldname": "ai_research", "fieldtype": "Link", "label": "Research", "options": "Excom AI Company", **RO},
			{"fieldname": "ai_fit_score", "fieldtype": "Int", "label": "Fit Score", **RO},
			{"fieldname": "ai_cb", "fieldtype": "Column Break"},
			{"fieldname": "ai_company_stop", "fieldtype": "Check", "label": "Company Stop",
			 "description": "Someone at this company replied: no more cold email to anyone there."},
			{"fieldname": "ai_stop_reason", "fieldtype": "Small Text", "label": "Stop Reason"},
		],
		"Lead": [
			{"fieldname": "ai_section", "fieldtype": "Section Break", "label": "AI Agent", "collapsible": 1, "insert_after": "lead_owner"},
			{"fieldname": "ai_agent", "fieldtype": "Link", "label": "AI Agent", "options": "Excom AI Agent", "search_index": 1, "in_standard_filter": 1, **RO},
			{"fieldname": "ai_person", "fieldtype": "Link", "label": "Research", "options": "Excom AI Person", **RO},
			{"fieldname": "ai_email_status", "fieldtype": "Select", "label": "Email Status", "options": "\nVerified\nRisky\nGuess\nInvalid", **RO},
			{"fieldname": "ai_cb", "fieldtype": "Column Break"},
			{"fieldname": "ai_timezone", "fieldtype": "Data", "label": "Timezone", **RO},
			{"fieldname": "ai_language", "fieldtype": "Link", "label": "Writes In", "options": "Language", **RO},
		],
		"Contact": [
			# HLD-009: a person found through Apollo or Prospeo, and where they came from
			{"fieldname": "ai_linkedin_url", "fieldtype": "Data", "label": "LinkedIn", "options": "URL",
			 "insert_after": "designation"},
			{"fieldname": "ai_found_via", "fieldtype": "Small Text", "label": "Found Via", **RO,
			 "description": "The data provider, date and person who added this contact, for anyone asking where we got their details."},
			{"fieldname": "ai_people_id", "fieldtype": "Data", "label": "Data Provider ID", "hidden": 1, "search_index": 1, **RO},
		],
		"Opportunity": [
			{"fieldname": "ai_origin_agent", "fieldtype": "Link", "label": "Found By AI Agent", "options": "Excom AI Agent",
			 "insert_after": "opportunity_owner", **RO},
		],
	}


def chained() -> dict:
	"""Each field placed after the one before it, so the block stays together where the first lands."""
	out = {}
	for doctype, rows in fields().items():
		prev = None
		out[doctype] = []
		for row in rows:
			row = dict(row)
			if prev and not row.get("insert_after"):
				row["insert_after"] = prev
			prev = row["fieldname"]
			out[doctype].append(row)
	return out


def apply() -> None:
	create_custom_fields(chained(), ignore_validate=True, update=True)
