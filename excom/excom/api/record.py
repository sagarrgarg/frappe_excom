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
        ignore_permissions=True,
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
    doc.insert(ignore_permissions=True)
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
