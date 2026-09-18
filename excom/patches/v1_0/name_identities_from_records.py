"""Give a name to contacts that are still called after their own phone number.

A call creates the contact before anybody knows who it is, so it is named after the number dialled.
When the Lead arrived later with the real name it was linked to that same contact and the name was
dropped, so a conversation showed +94774465807 while the Leads list beside it showed Madam Bhagya.

The code no longer does that. This is for the ones it already made: take the name from whichever
linked record has one, and tell the conversations, which keep their own copy.

Safe to re-run, and it never renames a contact somebody has already named.
"""

import frappe

# Most specific first: a person's own record beats the company they were filed under.
SOURCES = (
	("Contact", ("full_name", "first_name")),
	("Lead", ("lead_name", "company_name")),
	("Customer", ("customer_name",)),
	("Supplier", ("supplier_name",)),
)


def execute():
	from excom.excom.doctype.omni_identity.omni_identity import is_unnamed, name_if_unnamed

	if not frappe.db.table_exists("Omni Identity"):
		return

	candidates = [
		row
		for row in frappe.get_all(
			"Omni Identity",
			fields=["name", "display_name", "primary_phone", "primary_email"],
		)
		if is_unnamed(row.display_name, row.primary_phone, row.primary_email)
	]
	if not candidates:
		return

	links = frappe.get_all(
		"Omni Identity Link",
		filters={
			"parent": ["in", [c.name for c in candidates]],
			"parenttype": "Omni Identity",
			"linked_doctype": ["in", [s[0] for s in SOURCES]],
		},
		fields=["parent", "linked_doctype", "linked_name"],
	)
	by_identity: dict[str, list] = {}
	for link in links:
		by_identity.setdefault(link.parent, []).append(link)

	named = 0
	for identity in candidates:
		for doctype, fieldnames in SOURCES:
			match = next(
				(l for l in by_identity.get(identity.name, []) if l.linked_doctype == doctype),
				None,
			)
			if not match or not frappe.db.exists(doctype, match.linked_name):
				continue
			row = frappe.db.get_value(doctype, match.linked_name, list(fieldnames), as_dict=True)
			proposed = next((str(row.get(f) or "").strip() for f in fieldnames if row.get(f)), "")
			# name_if_unnamed decides whether this is really better, and renames the
			# conversations that cached the old one.
			if proposed and name_if_unnamed(identity.name, proposed):
				named += 1
				break

	frappe.db.commit()
	if named:
		print(f"  named {named} of {len(candidates)} contacts from their linked records")
