"""Enquiry text used to land in ERPNext's Lead.notes child table, invisible in Excom. Move every note on a
lead that came from a source into a Comment (the one notes model), keeping author and time. Idempotent."""

import frappe


def execute():
	if not frappe.db.exists("DocType", "CRM Note"):
		return
	rows = frappe.db.sql(
		"""SELECT n.name, n.parent, n.note, n.owner, n.creation FROM `tabCRM Note` n
		   JOIN `tabLead` l ON l.name = n.parent
		   WHERE n.parenttype = 'Lead' AND (l.intake_source IS NOT NULL OR l.source_reference IS NOT NULL)""",
		as_dict=True,
	)
	moved = 0
	for r in rows:
		text = frappe.utils.strip_html(r.note or "").strip()
		if not text:
			continue
		if frappe.db.exists("Comment", {"reference_doctype": "Lead", "reference_name": r.parent, "comment_type": "Comment", "content": ["like", f"%{text[:60]}%"]}):
			continue
		c = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Lead", "reference_name": r.parent, "content": frappe.utils.escape_html(text).replace("\n", "<br>"), "comment_email": r.owner, "comment_by": frappe.utils.get_fullname(r.owner)})
		c.flags.ignore_permissions = True
		c.insert()
		frappe.db.set_value("Comment", c.name, {"creation": r.creation, "owner": r.owner}, update_modified=False)
		moved += 1
	frappe.db.commit()
	print(f"enquiry notes moved to comments: {moved}")
