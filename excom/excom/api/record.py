"""
Record-level endpoints for the P1 UI: Notes (core Comment), Activity (Version +
transfer log) and the per-user UI preference.

Comment and Version are readable only by System Manager through frappe.client,
so these thin readers check permission on the *parent* document and then read
with ignore_permissions. No new doctypes.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.api.chat import _check_excom_access


def _check_doc_read(doctype: str, name: str):
    if not doctype or not name:
        frappe.throw(_("Reference document is required"))
    if not frappe.db.exists(doctype, name):
        frappe.throw(_("{0} {1} not found").format(doctype, name), frappe.DoesNotExistError)
    if not frappe.has_permission(doctype, "read", doc=name):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


@frappe.whitelist()
def get_notes(reference_doctype: str, reference_name: str, limit: int = 50) -> list:
    """Comments of type 'Comment' on the linked record, newest first."""
    _check_excom_access()
    _check_doc_read(reference_doctype, reference_name)
    rows = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "comment_type": "Comment",
        },
        fields=["name", "content", "comment_email", "comment_by", "creation", "owner"],
        order_by="creation desc",
        limit=int(limit),
    )
    return rows


@frappe.whitelist(methods=["POST"])
def add_note(reference_doctype: str, reference_name: str, content: str) -> dict:
    """Add a Comment on the linked record (a note about the *party*, not a thread moment)."""
    _check_excom_access()
    _check_doc_read(reference_doctype, reference_name)
    content = (content or "").strip()
    if not content:
        frappe.throw(_("Note cannot be empty"))
    user = frappe.session.user
    doc = frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": frappe.utils.escape_html(content).replace("\n", "<br>"),
            "comment_email": user,
            "comment_by": frappe.utils.get_fullname(user),
        }
    )
    doc.insert()  # the agent's own Comment create right, not a bypass
    return {"name": doc.name, "creation": str(doc.creation)}


@frappe.whitelist()
def get_activity(reference_doctype: str = "", reference_name: str = "", thread_ids: str = "") -> list:
    """
    Merged activity feed: Version rows on the linked record + thread transfer log.
    Client merges in thread system messages. P3 replaces this with the CRM endpoint.
    """
    _check_excom_access()
    items: list = []

    if reference_doctype and reference_name:
        _check_doc_read(reference_doctype, reference_name)
        for c in frappe.get_all(
            "Comment",
            filters={"reference_doctype": reference_doctype, "reference_name": reference_name, "comment_type": "Comment"},
            fields=["name", "owner", "creation", "content"],
            order_by="creation desc",
            limit=50,
        ):
            items.append({"kind": "comment", "id": c.name, "by": frappe.utils.get_fullname(c.owner), "at": str(c.creation), "text": frappe.utils.strip_html((c.content or "").replace("<br>", " · "))})
        versions = frappe.get_all(
            "Version",
            filters={"ref_doctype": reference_doctype, "docname": reference_name},
            fields=["name", "owner", "creation", "data"],
            order_by="creation desc",
            limit=50,
            ignore_permissions=True,
        )
        for v in versions:
            changed = []
            try:
                data = json.loads(v.data or "{}")
                for row in data.get("changed", []) or []:
                    if len(row) >= 3:
                        changed.append({"field": row[0], "old": row[1], "new": row[2]})
            except (ValueError, TypeError):
                pass
            items.append(
                {
                    "kind": "version",
                    "id": v.name,
                    "by": frappe.utils.get_fullname(v.owner),
                    "at": str(v.creation),
                    "changed": changed,
                }
            )

    ids = []
    if thread_ids:
        try:
            ids = json.loads(thread_ids) if thread_ids.startswith("[") else [t for t in thread_ids.split(",") if t]
        except ValueError:
            ids = []
    if ids:
        for t in frappe.get_all("Excom Thread", filters={"name": ["in", ids], "closure_outcome": ["is", "set"]}, fields=["name", "closure_outcome", "closure_reason", "closed_by", "closed_at"], ignore_permissions=True):
            items.append({"kind": "closure", "id": f"close-{t.name}", "by": frappe.utils.get_fullname(t.closed_by), "at": str(t.closed_at), "outcome": t.closure_outcome, "reason": t.closure_reason})
        logs = frappe.db.sql(
            """
            SELECT tl.thread, tl.from_team, tl.to_team, tl.transferred_by, tl.note, tl.transferred_at,
                   ft.team_name AS from_team_name, tt.team_name AS to_team_name,
                   u.full_name AS transferred_by_name
            FROM `tabExcom Thread Transfer Log` tl
            LEFT JOIN `tabExcom Team` ft ON ft.name = tl.from_team
            LEFT JOIN `tabExcom Team` tt ON tt.name = tl.to_team
            LEFT JOIN `tabUser` u ON u.name = tl.transferred_by
            WHERE tl.thread IN %(ids)s
            ORDER BY tl.transferred_at DESC
            LIMIT 50
            """,
            {"ids": tuple(ids)},
            as_dict=True,
        )
        for lg in logs:
            items.append(
                {
                    "kind": "transfer",
                    "id": f"tl-{lg.thread}-{lg.transferred_at}",
                    "by": lg.transferred_by_name or lg.transferred_by,
                    "at": str(lg.transferred_at),
                    "from_team": lg.from_team_name or lg.from_team or "",
                    "to_team": lg.to_team_name or lg.to_team or "",
                    "note": lg.note or "",
                    "thread": lg.thread,
                }
            )

    items.sort(key=lambda x: x["at"], reverse=True)
    return items


@frappe.whitelist(methods=["POST"])
def set_ui_preference(mode: str = "") -> dict:
    """Per-user UI flag: 'next' | 'legacy' | '' (clear). Read from boot.sysdefaults.excom_ui."""
    _check_excom_access()
    mode = (mode or "").strip().lower()
    if mode not in ("", "next", "legacy"):
        frappe.throw(_("Invalid UI mode"))
    if mode:
        frappe.defaults.set_user_default("excom_ui", mode)
    else:
        frappe.defaults.clear_user_default("excom_ui")
    frappe.clear_cache(user=frappe.session.user)
    return {"mode": mode, "at": str(now_datetime())}


@frappe.whitelist(methods=["POST"])
def submit_ui_feedback(message: str = "", route: str = "", viewport: str = "", dpr: str = "", ui: str = "") -> dict:
    """One-line feedback from the UI switch link. Stored as a Comment on Excom Settings."""
    _check_excom_access()
    message = (message or "").strip()
    if not message:
        frappe.throw(_("Feedback cannot be empty"))
    body = f"[{ui or 'ui'}] {frappe.utils.escape_html(message)}<br><small>{frappe.utils.escape_html(route)} · {frappe.utils.escape_html(viewport)} · DPR {frappe.utils.escape_html(dpr)}</small>"
    frappe.get_doc(
        {
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": "Excom Settings",
            "reference_name": "Excom Settings",
            "content": body,
            "comment_email": frappe.session.user,
            "comment_by": frappe.utils.get_fullname(frappe.session.user),
        }
    ).insert(ignore_permissions=True)
    return {"ok": True}


OUTCOMES = ("Resolved", "Converted", "Lost", "Not Interested", "Spam", "Duplicate")


@frappe.whitelist()
def close_conversation(omni_identity: str, outcome: str = "Resolved", reason: str = "", note: str = "", close_crm: int = 1) -> dict:
    """Closure scene: archive every open thread of this contact with an outcome, write the doc-level
    activity log (Comment on the linked Lead/Opportunity/Customer and on each thread), and — for
    negative outcomes — close the CRM record too (Lead → Do Not Contact, Opportunity → Lost)."""
    from excom.excom.api.chat import _user_can_access_thread
    from excom.excom.services import crm_gateway as gw
    _check_excom_access()
    if outcome not in OUTCOMES:
        frappe.throw(_("Unknown outcome: {0}").format(outcome))
    user = frappe.session.user
    threads = frappe.get_all("Excom Thread", filters={"omni_identity": omni_identity, "status": ["!=", "Closed"]}, pluck="name")
    threads = [t for t in threads if _user_can_access_thread(t)]
    now = frappe.utils.now_datetime()
    summary = f"<b>Closed · {outcome}</b>" + (f" — {frappe.utils.escape_html(reason)}" if reason else "") + (f"<br>{frappe.utils.escape_html(note)}" if note else "")
    for t in threads:
        frappe.db.set_value("Excom Thread", t, {"status": "Closed", "closure_outcome": outcome, "closure_reason": reason[:140], "closed_by": user, "closed_at": now}, update_modified=True)
        frappe.get_doc("Excom Thread", t).add_comment("Comment", summary)
    crm_status = None
    ref = None
    recs = gw.find_open_records_for_identity(omni_identity) if omni_identity else []
    if recs:
        ref = recs[0]
        gw.add_timeline_comment(ref, summary + f"<br><span class='text-muted'>via Excom · {len(threads)} conversation(s)</span>")
        if int(close_crm or 0):
            crm_status = gw.close_record(ref, outcome, reason, note)
    frappe.db.commit()
    for t in threads:
        frappe.publish_realtime("excom:thread_updated", {"thread": t, "event": "closed"}, after_commit=True)
    return {"threads": threads, "crm": {"doctype": ref.doctype, "name": ref.name, "status": crm_status} if ref else None}


@frappe.whitelist()
def reopen_conversation(omni_identity: str, note: str = "") -> dict:
    """Undo a closure: threads back to Open, closure fields cleared, CRM record reopened if we closed it."""
    from excom.excom.api.chat import _user_can_access_thread
    from excom.excom.services import crm_gateway as gw
    _check_excom_access()
    threads = [t for t in frappe.get_all("Excom Thread", filters={"omni_identity": omni_identity, "status": "Closed"}, pluck="name") if _user_can_access_thread(t)]
    text = "<b>Reopened</b>" + (f" — {frappe.utils.escape_html(note)}" if note else "")
    for t in threads:
        frappe.db.set_value("Excom Thread", t, {"status": "Open", "closure_outcome": None, "closure_reason": None, "closed_by": None, "closed_at": None}, update_modified=True)
        frappe.get_doc("Excom Thread", t).add_comment("Comment", text)
    crm_status = None
    links = frappe.get_all("Omni Identity Link", filters={"parent": omni_identity, "parenttype": "Omni Identity", "linked_doctype": ["in", gw.crm_doctypes()]}, fields=["linked_doctype", "linked_name"])
    for ln in links:
        ref = frappe._dict(doctype=ln.linked_doctype, name=ln.linked_name)
        st = gw.reopen_record(ref)
        if st:
            crm_status = st
            gw.add_timeline_comment(ref, text + " <span class='text-muted'>via Excom</span>")
    frappe.db.commit()
    for t in threads:
        frappe.publish_realtime("excom:thread_updated", {"thread": t, "event": "reopened"}, after_commit=True)
    return {"threads": threads, "crm_status": crm_status}


@frappe.whitelist()
def get_identity_contact(omni_identity: str) -> dict:
	"""A contact that has no conversation yet (a migrated or web-form lead): enough to open the record pane,
	show Details / Tasks / Notes / Activity and offer 'Start conversation'."""
	_check_excom_access()
	oi = frappe.db.get_value("Omni Identity", omni_identity, ["name", "display_name", "primary_phone", "primary_email", "primary_whatsapp", "creation"], as_dict=True)
	if not oi:
		frappe.throw(_("Contact not found"), frappe.DoesNotExistError)
	from excom.excom.services.crm_gateway import company_for_kind, kinds_for_identities
	kinds = kinds_for_identities([omni_identity]).get(omni_identity, [])
	company = next((c for c in (company_for_kind(k) for k in kinds) if c), "")
	return {"name": oi.name, "display_name": oi.display_name, "primary_phone": oi.primary_phone, "primary_email": oi.primary_email, "primary_whatsapp": oi.primary_whatsapp, "avatar_url": None, "creation": str(oi.creation), "kinds": kinds, "company": company, "threads": frappe.db.count("Excom Thread", {"omni_identity": omni_identity})}


@frappe.whitelist()
def get_identity_notes(omni_identity: str) -> list:
	"""Notes tab: every note about this person, wherever it sits (linked records, threads, identity)."""
	_check_excom_access()
	from excom.excom.services.notes import list_notes
	return list_notes(omni_identity)


@frappe.whitelist()
def add_identity_note(omni_identity: str, content: str, thread: str = "") -> dict:
	_check_excom_access()
	from excom.excom.services.notes import add_note
	return add_note(omni_identity, content, thread or None)
