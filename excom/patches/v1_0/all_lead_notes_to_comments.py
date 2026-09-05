"""Every note on a Lead becomes a Comment, not only the ones that arrived from a source.

enquiry_notes_to_comments moved the notes on leads that came through an intake source. That left
every note an agent had typed by hand over the years sitting in ERPNext's Lead.notes child table,
where Excom cannot see it: open such a lead in Excom and its history looks empty while Desk shows
years of it. A note is a note, and Excom's notes model is the Comment.

Copies rather than moves: the original child rows are left alone, so nothing is lost and this can be
re-run. Author and timestamp are preserved, and a note already present as a Comment is skipped.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "CRM Note"):
		return
	rows = frappe.db.sql(
		"""SELECT n.name, n.parent, n.note, n.owner, n.creation
		   FROM `tabCRM Note` n JOIN `tabLead` l ON l.name = n.parent
		   WHERE n.parenttype = 'Lead'""",
		as_dict=True,
	)
	moved = skipped = 0
	for r in rows:
		text = frappe.utils.strip_html(r.note or "").replace("&nbsp;", " ").strip()
		if not text:
			skipped += 1
			continue
		if frappe.db.exists("Comment", {
			"reference_doctype": "Lead", "reference_name": r.parent,
			"comment_type": "Comment", "content": ["like", f"%{text[:60]}%"],
		}):
			skipped += 1
			continue
		c = frappe.get_doc({
			"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Lead",
			"reference_name": r.parent,
			"content": frappe.utils.escape_html(text).replace("\n", "<br>"),
			"comment_email": r.owner, "comment_by": frappe.utils.get_fullname(r.owner),
		})
		c.flags.ignore_permissions = True
		c.insert()
		# keep the note where it happened in the timeline, not where the patch ran
		frappe.db.set_value("Comment", c.name, {"creation": r.creation, "owner": r.owner}, update_modified=False)
		moved += 1
	frappe.db.commit()
	print(f"lead notes copied to comments: {moved} moved, {skipped} already there or empty")
