"""
One notes model. A note — typed in the chat as an internal note or in the Notes tab — is a Frappe
`Comment` on the party's record: the open Lead / Opportunity / Customer when there is one, otherwise the
conversation thread. Desk shows the same comment on the same document. The chat feed and the Notes
tab both read from here, so there is one owner doctype and nothing to keep in sync.
"""

import frappe
from frappe import _
from frappe.utils import escape_html

from excom.excom.services import crm_gateway as gw


def note_target(omni_identity: str, thread: str | None = None) -> tuple[str, str]:
	recs = gw.find_open_records_for_identity(omni_identity) if omni_identity else []
	if recs:
		return recs[0].doctype, recs[0].name
	if thread:
		return "Excom Thread", thread
	latest = frappe.get_all("Excom Thread", filters={"omni_identity": omni_identity}, order_by="last_message_at desc", pluck="name", limit=1)
	if latest:
		return "Excom Thread", latest[0]
	return "Omni Identity", omni_identity


def add_note(omni_identity: str, content: str, thread: str | None = None) -> dict:
	if not content or not content.strip():
		frappe.throw(_("Note content cannot be empty"))
	dt, name = note_target(omni_identity, thread)
	html = content if "<" in content and ">" in content else escape_html(content.strip()).replace("\n", "<br>")
	c = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": dt, "reference_name": name, "content": html, "comment_email": frappe.session.user, "comment_by": frappe.utils.get_fullname(frappe.session.user)})
	c.flags.ignore_permissions = True
	c.insert()
	for t in ([thread] if thread else frappe.get_all("Excom Thread", filters={"omni_identity": omni_identity}, pluck="name", limit=5)):
		frappe.publish_realtime("excom:message_received", {"thread": t, "message": f"cmt:{c.name}", "omni_identity": omni_identity, "direction": "Outbound", "preview": frappe.utils.strip_html(html)[:100], "is_internal": 1}, after_commit=True)
	return _row(c.as_dict(), dt, name)


def _row(c, dt: str, name: str) -> dict:
	return {"name": c.get("name"), "content": c.get("content"), "comment_email": c.get("comment_email") or c.get("owner"), "comment_by": c.get("comment_by") or frappe.utils.get_fullname(c.get("owner")), "owner": c.get("owner"), "creation": str(c.get("creation")), "on_doctype": dt, "on_name": name}


def targets_for_identity(omni_identity: str) -> list[tuple[str, str]]:
	"""Every document a note about this person may sit on: all linked ERP records, threads, the identity."""
	out: list[tuple[str, str]] = []
	for ln in frappe.get_all("Omni Identity Link", filters={"parent": omni_identity, "parenttype": "Omni Identity"}, fields=["linked_doctype", "linked_name"]):
		out.append((ln.linked_doctype, ln.linked_name))
	for t in frappe.get_all("Excom Thread", filters={"omni_identity": omni_identity}, pluck="name"):
		out.append(("Excom Thread", t))
	out.append(("Omni Identity", omni_identity))
	return out


def list_notes(omni_identity: str, limit: int = 100) -> list[dict]:
	rows: list[dict] = []
	for dt, name in targets_for_identity(omni_identity):
		if not frappe.db.exists("DocType", dt):
			continue
		for c in frappe.get_all("Comment", filters={"reference_doctype": dt, "reference_name": name, "comment_type": "Comment"}, fields=["name", "content", "comment_email", "comment_by", "owner", "creation"], limit=limit, ignore_permissions=True):
			rows.append(_row(c, dt, name))
	rows.sort(key=lambda r: r["creation"], reverse=True)
	return rows[:limit]


def feed_notes(thread_id: str, since=None) -> list[dict]:
	"""Notes rendered inside a conversation: comments on the thread and on the party's open record."""
	oi = frappe.db.get_value("Excom Thread", thread_id, "omni_identity")
	targets = [("Excom Thread", thread_id)]
	if oi:
		targets += [(r.doctype, r.name) for r in gw.find_open_records_for_identity(oi)]
		targets.append(("Omni Identity", oi))
	out = []
	for dt, name in targets:
		f = {"reference_doctype": dt, "reference_name": name, "comment_type": "Comment"}
		if since:
			f["creation"] = [">=", since]
		for c in frappe.get_all("Comment", filters=f, fields=["name", "content", "comment_email", "comment_by", "owner", "creation"], limit=200, ignore_permissions=True):
			out.append({
				"name": f"cmt:{c.name}", "thread": thread_id, "direction": "Outbound", "message_type": "Text", "content_text": frappe.utils.strip_html((c.content or "").replace("<br>", "\n")),
				"content_html": c.content, "delivery_status": "Read", "is_internal": 1, "created_by_user": c.comment_email or c.owner, "sender_name": c.comment_by or frappe.utils.get_fullname(c.owner),
				"creation": str(c.creation), "provider_timestamp": str(c.creation), "note_on": {"doctype": dt, "name": name}, "media_file": None, "reactions": {}, "reply_to": None,
			})
	return out
