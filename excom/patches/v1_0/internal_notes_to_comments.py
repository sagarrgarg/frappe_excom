"""Internal notes used to be Excom Message rows (is_internal=1). Notes are now Comments on the party's
record (or the thread). Convert every old note, keeping author, time and text; then remove the message row."""

import frappe


def execute():
	if not frappe.db.has_column("Excom Message", "is_internal"):
		return
	from excom.excom.services.notes import note_target
	rows = frappe.get_all("Excom Message", filters={"is_internal": 1}, fields=["name", "thread", "omni_identity", "content_text", "created_by_user", "owner", "creation"])
	n = 0
	for m in rows:
		try:
			dt, name = note_target(m.omni_identity, m.thread)
			author = m.created_by_user or m.owner or "Administrator"
			c = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": dt, "reference_name": name, "content": frappe.utils.escape_html(m.content_text or "").replace("\n", "<br>"), "comment_email": author, "comment_by": frappe.utils.get_fullname(author)})
			c.flags.ignore_permissions = True
			c.insert()
			frappe.db.set_value("Comment", c.name, {"creation": m.creation, "owner": author}, update_modified=False)
			frappe.db.delete("Excom Message", {"name": m.name})
			n += 1
		except Exception:
			frappe.log_error(title=f"internal note → comment failed: {m.name}", message=frappe.get_traceback())
	frappe.db.commit()
	print(f"internal notes converted: {n}")
