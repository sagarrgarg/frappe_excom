"""
Subscriber Rule Engine — evaluates rules against documents and
auto-subscribes matching contacts to subscriber lists.
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import now_datetime


def evaluate_rules_for_doc(doc, event: str) -> None:
    """
    Find all enabled rules matching this doc's doctype and event,
    evaluate each rule's condition, and auto-subscribe the resolved identity.

    Args:
        doc: The Frappe document triggering the event
        event: "After Insert", "After Save", or "After Submit"
    """
    if not frappe.db.table_exists("tabExcom Subscriber Rule"):
        return

    rules = frappe.get_all(
        "Excom Subscriber Rule",
        filters={
            "enabled": 1,
            "reference_doctype": doc.doctype,
            "event": event,
        },
        fields=["name", "subscriber_list", "condition", "identity_field", "identity_field_type"],
    )

    if not rules:
        return

    for rule in rules:
        try:
            _apply_rule(doc, rule)
        except Exception:
            frappe.log_error(
                title=f"Excom: subscriber rule '{rule.name}' failed on {doc.doctype} {doc.name}",
            )


def _apply_rule(doc, rule: dict) -> None:
    """Evaluate a single rule against a document."""
    if rule.condition and rule.condition.strip():
        match = _safe_eval_condition(rule.condition.strip(), doc)
        if not match:
            return

    entity_name = _resolve_field(doc, rule.identity_field)
    if not entity_name:
        return

    identity_name = _resolve_identity(entity_name, rule.identity_field_type)
    if not identity_name:
        return

    _subscribe_if_needed(identity_name, rule.subscriber_list)


def _safe_eval_condition(condition: str, doc) -> bool:
    """Evaluate a Python expression with 'doc' and frappe.utils in scope."""
    eval_globals = {"__builtins__": {}}
    eval_locals = {
        "doc": doc,
        "frappe": frappe,
        "utils": frappe.utils,
    }
    try:
        return bool(frappe.safe_eval(condition, eval_globals, eval_locals))
    except Exception:
        return False


def _resolve_field(doc, field_path: str) -> Optional[str]:
    """Get a value from a document using a dot-notation field path."""
    parts = field_path.split(".")
    value = doc
    for part in parts:
        if hasattr(value, part):
            value = getattr(value, part)
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return str(value) if value else None


def _resolve_identity(entity_name: str, entity_type: str) -> Optional[str]:
    """
    Find or create an Omni Identity for the given entity.
    Returns the Omni Identity name, or None.
    """
    existing = frappe.db.get_value(
        "Omni Identity Link",
        {"linked_doctype": entity_type, "linked_name": entity_name},
        "parent",
    )
    if existing:
        status = frappe.db.get_value("Omni Identity", existing, "status")
        if status != "Merged":
            return existing

    try:
        if entity_type == "Customer":
            from excom.excom.services.identity_sync import sync_single_customer
            sync_single_customer(entity_name)
        elif entity_type == "Supplier":
            from excom.excom.services.identity_sync import sync_single_supplier
            sync_single_supplier(entity_name)
        elif entity_type == "Lead":
            from excom.excom.services.identity_sync import sync_single_lead
            sync_single_lead(entity_name)
        elif entity_type == "Contact":
            pass
    except Exception:
        return None

    existing = frappe.db.get_value(
        "Omni Identity Link",
        {"linked_doctype": entity_type, "linked_name": entity_name},
        "parent",
    )
    return existing


def _subscribe_if_needed(identity_name: str, subscriber_list: str) -> None:
    """Add identity to subscriber list if not already subscribed."""
    exists = frappe.db.exists(
        "Excom Subscriber",
        {"subscriber_list": subscriber_list, "omni_identity": identity_name},
    )
    if exists:
        return
    # An automatic rule never re-adds someone who left this list or asked not to be contacted.
    from excom.excom.services.consent import latest_consent

    if frappe.db.get_value("Omni Identity", identity_name, "do_not_contact") or latest_consent(
        identity_name, channel="all", purpose=f"list:{subscriber_list}"
    ) == "Withdrawn":
        return

    frappe.get_doc({
        "doctype": "Excom Subscriber",
        "subscriber_list": subscriber_list,
        "omni_identity": identity_name,
        "status": "Subscribed",
        "subscribed_on": now_datetime(),
    }).insert(ignore_permissions=True)


def test_rule_against_doc(rule_name: str, doc_name: str) -> dict:
    """
    Dry-run a rule against a specific document.

    Returns whether the condition matches and what identity would be resolved.
    """
    rule = frappe.get_doc("Excom Subscriber Rule", rule_name)
    doc = frappe.get_doc(rule.reference_doctype, doc_name)

    condition_match = True
    if rule.condition and rule.condition.strip():
        condition_match = _safe_eval_condition(rule.condition.strip(), doc)

    entity_name = _resolve_field(doc, rule.identity_field)
    identity_name = None
    if entity_name:
        identity_name = frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": rule.identity_field_type, "linked_name": entity_name},
            "parent",
        )

    already_subscribed = False
    if identity_name:
        already_subscribed = bool(frappe.db.exists(
            "Excom Subscriber",
            {"subscriber_list": rule.subscriber_list, "omni_identity": identity_name},
        ))

    return {
        "condition_match": condition_match,
        "entity_name": entity_name,
        "identity_name": identity_name,
        "already_subscribed": already_subscribed,
        "would_subscribe": condition_match and bool(identity_name) and not already_subscribed,
    }


def backfill_rule(rule_name: str) -> dict:
    """
    Retroactively apply a rule to all existing documents of the reference DocType.

    Fetches docs in batches, evaluates the condition, resolves identity,
    and subscribes matches. Returns counts of added/skipped/errors.
    """
    rule = frappe.get_doc("Excom Subscriber Rule", rule_name)
    if not rule.enabled:
        frappe.throw(_("Rule must be enabled before backfilling"))

    added = 0
    skipped = 0
    errors = 0
    batch_size = 100
    offset = 0

    while True:
        doc_names = frappe.get_all(
            rule.reference_doctype,
            limit_page_length=batch_size,
            limit_start=offset,
            order_by="creation asc",
            pluck="name",
        )
        if not doc_names:
            break

        for doc_name in doc_names:
            try:
                doc = frappe.get_doc(rule.reference_doctype, doc_name)

                if rule.condition and rule.condition.strip():
                    if not _safe_eval_condition(rule.condition.strip(), doc):
                        skipped += 1
                        continue

                entity_name = _resolve_field(doc, rule.identity_field)
                if not entity_name:
                    skipped += 1
                    continue

                identity_name = _resolve_identity(entity_name, rule.identity_field_type)
                if not identity_name:
                    skipped += 1
                    continue

                already = frappe.db.exists(
                    "Excom Subscriber",
                    {"subscriber_list": rule.subscriber_list, "omni_identity": identity_name},
                )
                if already:
                    skipped += 1
                    continue

                _subscribe_if_needed(identity_name, rule.subscriber_list)
                added += 1
            except Exception:
                errors += 1
                frappe.log_error(
                    title=f"Excom: backfill rule '{rule_name}' failed on {doc_name}",
                )

        frappe.db.commit()
        offset += batch_size

    return {"added": added, "skipped": skipped, "errors": errors}
