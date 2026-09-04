"""API endpoints for Omni Identity merge suggestions review."""

import frappe
from frappe import _

from excom.excom.api.chat import _check_manager_access


@frappe.whitelist()
def get_merge_suggestions(limit: int = 50, offset: int = 0) -> list:
    """
    Return identities flagged for review with their potential duplicates.

    Each result includes both the flagged identity and the identity it might
    be a duplicate of, along with the shared field for display.
    """
    _check_manager_access()
    limit = int(limit)
    offset = int(offset)

    rows = frappe.db.sql(
        """
        SELECT
            a.name AS source_name,
            a.display_name AS source_display_name,
            a.primary_phone AS source_phone,
            a.primary_email AS source_email,
            a.primary_whatsapp AS source_whatsapp,
            b.name AS target_name,
            b.display_name AS target_display_name,
            b.primary_phone AS target_phone,
            b.primary_email AS target_email,
            b.primary_whatsapp AS target_whatsapp
        FROM `tabOmni Identity` a
        JOIN `tabOmni Identity` b ON b.name = a.potential_duplicate_of
        WHERE a.needs_review = 1
          AND a.status = 'Active'
          AND b.status = 'Active'
        ORDER BY a.modified DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": limit, "offset": offset},
        as_dict=True,
    )

    for row in rows:
        shared = []
        if row.source_phone and row.target_phone and _phone_match(row.source_phone, row.target_phone):
            shared.append("Phone")
        if row.source_email and row.target_email and row.source_email.lower() == row.target_email.lower():
            shared.append("Email")
        if row.source_whatsapp and row.target_whatsapp and row.source_whatsapp == row.target_whatsapp:
            shared.append("WhatsApp")
        row["shared_fields"] = shared

    from excom.excom.services.crm_gateway import merge_pair_kinds
    ids = list({x for r in rows for x in (r.get("source_name"), r.get("target_name")) if x})
    kinds = merge_pair_kinds(ids) if ids else {}
    for r in rows:
        r["source_kinds"] = kinds.get(r.get("source_name"), [])
        r["target_kinds"] = kinds.get(r.get("target_name"), [])
    return rows


def _phone_match(a: str, b: str) -> bool:
    """Check if two phone strings share the same last 10 digits."""
    import re
    da = re.sub(r"[^\d]", "", a)
    db = re.sub(r"[^\d]", "", b)
    if not da or not db:
        return False
    return da[-10:] == db[-10:]


@frappe.whitelist()
def approve_merge(source: str, target: str) -> dict:
    """Approve a merge suggestion — merge source into target."""
    _check_manager_access()
    from excom.excom.doctype.omni_identity.omni_identity import merge_identities
    result = merge_identities(source, target)
    return result


@frappe.whitelist()
def dismiss_suggestion(identity: str) -> dict:
    """Dismiss a merge suggestion without merging."""
    _check_manager_access()
    frappe.db.set_value(
        "Omni Identity",
        identity,
        {"needs_review": 0, "potential_duplicate_of": ""},
        update_modified=False,
    )
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def get_suggestion_count() -> dict:
    """Return the count of pending merge suggestions for the sidebar badge."""
    _check_manager_access()
    count = frappe.db.count(
        "Omni Identity",
        {"needs_review": 1, "status": "Active"},
    )
    return {"count": count}
