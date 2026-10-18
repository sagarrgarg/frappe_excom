"""
Auto-lifecycle hooks for Omni Identity.

- on_entity_created: auto-create identity when Customer/Supplier/Lead is inserted
- on_customer_updated: auto-merge when Lead converts to Customer
- on_party_link_created: auto-merge when Party Link joins Customer ↔ Supplier
- scan_merge_suggestions: daily batch scan for duplicate identities
- on_doc_event_for_rules: evaluates subscriber rules on every doc event
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


def on_entity_created(doc, method: str) -> None:
    """
    Called after_insert on Customer, Supplier, Lead, Contact.
    Creates an Omni Identity if one doesn't already exist and contact info is present.
    """
    if not frappe.db.table_exists("Omni Identity"):
        return

    try:
        doctype = doc.doctype
        if doctype == "Customer":
            from excom.excom.services.identity_sync import sync_single_customer
            sync_single_customer(doc.name)
        elif doctype == "Supplier":
            from excom.excom.services.identity_sync import sync_single_supplier
            sync_single_supplier(doc.name)
        elif doctype == "Lead":
            from excom.excom.services.identity_sync import sync_single_lead
            sync_single_lead(doc.name)
        elif doctype == "Contact":
            from excom.excom.services.identity_sync import sync_single_contact
            sync_single_contact(doc.name)
    except Exception:
        frappe.log_error(
            title=f"Excom: auto-create identity failed for {doc.doctype} {doc.name}",
        )


def on_entity_deleted(doc, method: str | None = None) -> None:
    """after_delete on Lead / Opportunity: drop the identity's link rows to the deleted record.

    Left behind, the row points at a name Frappe hands out again: deleting the newest record of a
    naming series reverts the series, so the next Lead is created under the same name and linking
    it to its identity hits the unique (linked_doctype, linked_name) index. Intake then fails for
    that lead. after_delete runs only once the deletion really happens (link checks passed or forced).
    """
    if not frappe.db.table_exists("Omni Identity Link"):
        return
    frappe.db.delete("Omni Identity Link", {"linked_doctype": doc.doctype, "linked_name": doc.name})


def on_customer_updated(doc, method: str) -> None:
    """
    Called on_update on Customer.
    If customer was converted from a Lead (has lead_name), merge Lead's identity
    into Customer's identity.
    """
    if not frappe.db.table_exists("Omni Identity"):
        return

    try:
        lead_name = getattr(doc, "lead_name", None)
        if not lead_name:
            return

        customer_oi = frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": "Customer", "linked_name": doc.name},
            "parent",
        )
        lead_oi = frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": "Lead", "linked_name": lead_name},
            "parent",
        )

        if not customer_oi or not lead_oi:
            return
        if customer_oi == lead_oi:
            return

        lead_status = frappe.db.get_value("Omni Identity", lead_oi, "status")
        if lead_status == "Merged":
            return

        lead_doc = frappe.get_doc("Omni Identity", lead_oi)
        master_doc = frappe.get_doc("Omni Identity", customer_oi)
        lead_doc.merge_into(master_doc)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title=f"Excom: auto-merge on Customer update failed for {doc.name}",
        )


def on_party_link_created(doc, method: str) -> None:
    """
    Called after_insert on Party Link.
    Merges the newer identity into the older one when two entities become linked.
    """
    if not frappe.db.table_exists("Omni Identity"):
        return

    try:
        primary_party = doc.primary_party
        primary_doctype = doc.primary_role  # "Customer" or "Supplier"
        secondary_party = doc.secondary_party
        secondary_doctype = doc.secondary_role

        primary_oi = frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": primary_doctype, "linked_name": primary_party},
            "parent",
        )
        secondary_oi = frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": secondary_doctype, "linked_name": secondary_party},
            "parent",
        )

        if not primary_oi or not secondary_oi:
            return
        if primary_oi == secondary_oi:
            return

        primary_status = frappe.db.get_value("Omni Identity", primary_oi, "status")
        secondary_status = frappe.db.get_value("Omni Identity", secondary_oi, "status")
        if primary_status == "Merged" or secondary_status == "Merged":
            return

        older, newer = sorted(
            [primary_oi, secondary_oi],
            key=lambda n: frappe.db.get_value("Omni Identity", n, "creation"),
        )

        newer_doc = frappe.get_doc("Omni Identity", newer)
        master_doc = frappe.get_doc("Omni Identity", older)
        newer_doc.merge_into(master_doc)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title=f"Excom: auto-merge on Party Link failed for {doc.name}",
        )


def scan_merge_suggestions() -> None:
    """
    Daily scheduled task: scans Active identities and flags pairs that
    share a phone, email, or WhatsApp for human review.
    """
    try:
        _scan_phone_duplicates()
        _scan_email_duplicates()
        _scan_whatsapp_duplicates()
        frappe.db.commit()
    except Exception:
        frappe.log_error(title="Excom: merge suggestion scan failed")


def _scan_phone_duplicates() -> None:
    """Find identities sharing the same normalized_phone suffix (last 10 digits)."""
    rows = frappe.db.sql(
        """
        SELECT a.name AS a_name, b.name AS b_name
        FROM `tabOmni Identity` a
        JOIN `tabOmni Identity` b
          ON b.name > a.name
          AND RIGHT(b.normalized_phone, 10) = RIGHT(a.normalized_phone, 10)
        WHERE a.status = 'Active' AND b.status = 'Active'
          AND a.normalized_phone != '' AND b.normalized_phone != ''
          AND a.needs_review = 0 AND b.needs_review = 0
        LIMIT 200
        """,
        as_dict=True,
    )
    for row in rows:
        _flag_for_review(row.b_name, row.a_name)


def _scan_email_duplicates() -> None:
    """Find identities sharing the same normalized_email."""
    rows = frappe.db.sql(
        """
        SELECT a.name AS a_name, b.name AS b_name
        FROM `tabOmni Identity` a
        JOIN `tabOmni Identity` b
          ON b.name > a.name
          AND b.normalized_email = a.normalized_email
        WHERE a.status = 'Active' AND b.status = 'Active'
          AND a.normalized_email != '' AND b.normalized_email != ''
          AND a.needs_review = 0 AND b.needs_review = 0
        LIMIT 200
        """,
        as_dict=True,
    )
    for row in rows:
        _flag_for_review(row.b_name, row.a_name)


def _scan_whatsapp_duplicates() -> None:
    """Find identities sharing the same primary_whatsapp."""
    rows = frappe.db.sql(
        """
        SELECT a.name AS a_name, b.name AS b_name
        FROM `tabOmni Identity` a
        JOIN `tabOmni Identity` b
          ON b.name > a.name
          AND b.primary_whatsapp = a.primary_whatsapp
        WHERE a.status = 'Active' AND b.status = 'Active'
          AND a.primary_whatsapp != '' AND b.primary_whatsapp != ''
          AND a.needs_review = 0 AND b.needs_review = 0
        LIMIT 200
        """,
        as_dict=True,
    )
    for row in rows:
        _flag_for_review(row.b_name, row.a_name)


def _flag_for_review(identity_name: str, duplicate_of: str) -> None:
    """Set needs_review=1 and potential_duplicate_of on the newer identity."""
    frappe.db.set_value(
        "Omni Identity",
        identity_name,
        {
            "needs_review": 1,
            "potential_duplicate_of": duplicate_of,
        },
        update_modified=False,
    )


def on_doc_event_for_rules(doc, method: str) -> None:
    """
    Generic handler wired to doc_events["*"]["after_insert"] and ["on_submit"].
    Evaluates subscriber auto-assignment rules for the document.
    """
    if frappe.flags.in_migrate or frappe.flags.in_install:
        return

    try:
        event_map = {
            "after_insert": "After Insert",
            "on_update": "After Save",
            "on_submit": "After Submit",
        }
        event = event_map.get(method)
        if not event:
            return

        from excom.excom.services.subscriber_rules import evaluate_rules_for_doc
        evaluate_rules_for_doc(doc, event)
    except Exception:
        frappe.log_error(
            title=f"Excom: rule evaluation failed for {doc.doctype} {doc.name}",
        )
