"""API endpoints for managing Excom Subscriber Rules."""

import frappe
from frappe import _

from excom.excom.api.chat import _check_manager_access


@frappe.whitelist()
def search_doctypes(search: str = "", limit: int = 20) -> list:
    """Search DocTypes for the rule reference_doctype picker."""
    _check_manager_access()
    limit = min(int(limit), 50)
    params: dict = {"limit": limit}
    where = "istable = 0 AND issingle = 0 AND module != 'Core'"
    if search:
        params["q"] = f"%{search}%"
        where += " AND (name LIKE %(q)s OR module LIKE %(q)s)"

    return frappe.db.sql(
        f"""
        SELECT name, module
        FROM `tabDocType`
        WHERE {where}
        ORDER BY name ASC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist()
def search_subscriber_lists(search: str = "", limit: int = 20) -> list:
    """Search Excom Subscriber Lists for the rule picker."""
    _check_manager_access()
    limit = min(int(limit), 50)
    params: dict = {"limit": limit}
    where = "1=1"
    if search:
        params["q"] = f"%{search}%"
        where += " AND (list_name LIKE %(q)s OR name LIKE %(q)s)"

    return frappe.db.sql(
        f"""
        SELECT name, list_name
        FROM `tabExcom Subscriber List`
        WHERE {where}
        ORDER BY list_name ASC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist()
def get_doctype_fields(doctype: str) -> list:
    """Return Link fields of a DocType for the identity_field picker."""
    _check_manager_access()
    if not doctype or not frappe.db.exists("DocType", doctype):
        return []

    return frappe.db.sql(
        """
        SELECT fieldname, label, options
        FROM `tabDocField`
        WHERE parent = %(dt)s
          AND fieldtype = 'Link'
          AND options IN ('Customer', 'Supplier', 'Lead', 'Contact')
        ORDER BY label ASC
        """,
        {"dt": doctype},
        as_dict=True,
    )


@frappe.whitelist()
def get_rules(subscriber_list: str = "") -> list:
    """Return all subscriber rules, optionally filtered by list."""
    _check_manager_access()
    filters: dict = {}
    if subscriber_list:
        filters["subscriber_list"] = subscriber_list

    return frappe.get_all(
        "Excom Subscriber Rule",
        filters=filters,
        fields=[
            "name", "rule_name", "enabled", "subscriber_list",
            "reference_doctype", "event", "condition",
            "identity_field", "identity_field_type", "description",
        ],
        order_by="rule_name asc",
    )


@frappe.whitelist()
def create_rule(
    rule_name: str,
    subscriber_list: str,
    reference_doctype: str,
    event: str,
    identity_field: str,
    identity_field_type: str,
    condition: str = "",
    description: str = "",
) -> dict:
    """Create a new subscriber rule."""
    _check_manager_access()

    doc = frappe.get_doc({
        "doctype": "Excom Subscriber Rule",
        "rule_name": rule_name,
        "subscriber_list": subscriber_list,
        "reference_doctype": reference_doctype,
        "event": event,
        "identity_field": identity_field,
        "identity_field_type": identity_field_type,
        "condition": condition,
        "description": description,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True, "name": doc.name}


@frappe.whitelist()
def update_rule(
    name: str,
    rule_name: str = "",
    subscriber_list: str = "",
    reference_doctype: str = "",
    event: str = "",
    identity_field: str = "",
    identity_field_type: str = "",
    condition: str = "",
    description: str = "",
    enabled: int = 1,
) -> dict:
    """Update an existing subscriber rule."""
    _check_manager_access()

    doc = frappe.get_doc("Excom Subscriber Rule", name)
    if rule_name:
        doc.rule_name = rule_name
    if subscriber_list:
        doc.subscriber_list = subscriber_list
    if reference_doctype:
        doc.reference_doctype = reference_doctype
    if event:
        doc.event = event
    if identity_field:
        doc.identity_field = identity_field
    if identity_field_type:
        doc.identity_field_type = identity_field_type
    doc.condition = condition
    doc.description = description
    doc.enabled = int(enabled)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def toggle_rule(name: str, enabled: int) -> dict:
    """Enable or disable a subscriber rule."""
    _check_manager_access()
    frappe.db.set_value("Excom Subscriber Rule", name, "enabled", int(enabled))
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def test_rule(name: str, doc_name: str) -> dict:
    """
    Dry-run a rule against a specific document.
    Returns whether the condition matches and the identity that would be resolved.
    """
    _check_manager_access()
    from excom.excom.services.subscriber_rules import test_rule_against_doc
    return test_rule_against_doc(name, doc_name)


@frappe.whitelist()
def apply_rule_to_existing(name: str) -> dict:
    """
    Retroactively apply a rule to all existing documents of its reference DocType.
    Runs in the foreground and returns counts of added/skipped/errors.
    """
    _check_manager_access()
    from excom.excom.services.subscriber_rules import backfill_rule
    return backfill_rule(name)
