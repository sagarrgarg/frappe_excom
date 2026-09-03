"""
Bulk Identity Sync — creates Omni Identities from existing ERPNext entities.

Walks the ERPNext entity graph (Customer, Supplier, Lead, Contact) and
creates one Omni Identity per "business entity group":

- Customer + its Contacts + Party-Linked Supplier = 1 identity
- Standalone Supplier + its Contacts = 1 identity
- Unconverted Lead = 1 identity
- Contacts are never standalone — they belong to their parent party
"""

from typing import Optional

import frappe
from frappe import _
from frappe.utils import now_datetime


def sync_all_identities(progress_key: str = "") -> dict:
    """
    Full idempotent sync of all ERPNext entities into Omni Identities.

    Args:
        progress_key: cache key for progress tracking (optional)

    Returns:
        Summary dict with created/skipped/error counts.
    """
    stats = {"created": 0, "skipped": 0, "errors": 0, "total": 0}
    processed_entities: set = set()

    customers = frappe.get_all("Customer", fields=["name", "customer_name"], limit=0)
    suppliers = frappe.get_all("Supplier", fields=["name", "supplier_name"], limit=0)
    leads = frappe.get_all(
        "Lead",
        fields=["name", "lead_name", "mobile_no", "phone", "email_id"],
        limit=0,
    )

    stats["total"] = len(customers) + len(suppliers) + len(leads)

    for i, customer in enumerate(customers):
        try:
            result = sync_single_customer(customer.name, processed_entities)
            if result:
                stats["created"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1
            frappe.log_error(title=f"Identity sync failed: Customer {customer.name}")

        if progress_key and i % 20 == 0:
            _update_progress(progress_key, i, stats["total"], "customers")

    for i, supplier in enumerate(suppliers):
        if _entity_key("Supplier", supplier.name) in processed_entities:
            stats["skipped"] += 1
            continue
        try:
            result = sync_single_supplier(supplier.name, processed_entities)
            if result:
                stats["created"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1
            frappe.log_error(title=f"Identity sync failed: Supplier {supplier.name}")

        if progress_key and i % 20 == 0:
            _update_progress(
                progress_key, len(customers) + i, stats["total"], "suppliers"
            )

    for i, lead in enumerate(leads):
        try:
            result = sync_single_lead(lead.name, processed_entities)
            if result:
                stats["created"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1
            frappe.log_error(title=f"Identity sync failed: Lead {lead.name}")

        if progress_key and i % 20 == 0:
            _update_progress(
                progress_key,
                len(customers) + len(suppliers) + i,
                stats["total"],
                "leads",
            )

    if progress_key:
        frappe.cache.set_value(
            progress_key,
            {"done": True, "stats": stats},
            expires_in_sec=3600,
        )

    frappe.db.commit()
    return stats


def sync_single_customer(
    customer_name: str, processed: Optional[set] = None
) -> Optional[str]:
    """
    Create or find an Omni Identity for a Customer and all its related entities.

    Returns the Omni Identity name, or None if already synced.
    """
    if processed is None:
        processed = set()

    if _is_already_linked("Customer", customer_name):
        processed.add(_entity_key("Customer", customer_name))
        return None

    customer = frappe.get_cached_doc("Customer", customer_name)

    linked_supplier = _get_party_linked_supplier(customer_name)
    contacts = _get_contacts_for_party("Customer", customer_name)
    if linked_supplier:
        contacts += _get_contacts_for_party("Supplier", linked_supplier)
        contacts = _dedupe_contacts(contacts)

    phone, email = _extract_contact_details(contacts, customer_name, "Customer")

    existing = _find_existing_identity(phone, email)
    if existing:
        links = [("Customer", customer_name, "Primary Contact")]
        if linked_supplier and not _is_already_linked("Supplier", linked_supplier):
            links.append(("Supplier", linked_supplier, "Primary Contact"))
            processed.add(_entity_key("Supplier", linked_supplier))
        for contact in contacts:
            if not _is_already_linked("Contact", contact.name):
                links.append(("Contact", contact.name, "Primary Contact"))
                processed.add(_entity_key("Contact", contact.name))
        processed.add(_entity_key("Customer", customer_name))
        return _attach_to_existing(existing, links, phone, email)

    identity = frappe.get_doc({
        "doctype": "Omni Identity",
        "display_name": customer.customer_name or customer_name,
        "primary_phone": phone,
        "primary_email": email,
        "primary_whatsapp": _digits_only(phone),
        "status": "Active",
        "is_master": 1,
    })

    identity.append("linked_entities", {
        "linked_doctype": "Customer",
        "linked_name": customer_name,
        "role": "Primary Contact",
    })
    processed.add(_entity_key("Customer", customer_name))

    if linked_supplier and not _is_already_linked("Supplier", linked_supplier):
        identity.append("linked_entities", {
            "linked_doctype": "Supplier",
            "linked_name": linked_supplier,
            "role": "Primary Contact",
        })
        processed.add(_entity_key("Supplier", linked_supplier))

    for contact in contacts:
        if not _is_already_linked("Contact", contact.name):
            identity.append("linked_entities", {
                "linked_doctype": "Contact",
                "linked_name": contact.name,
                "role": "Primary Contact",
            })
            processed.add(_entity_key("Contact", contact.name))

    identity.flags.ignore_validate = True
    identity.insert(ignore_permissions=True)
    return identity.name


def sync_single_supplier(
    supplier_name: str, processed: Optional[set] = None
) -> Optional[str]:
    """
    Create or find an Omni Identity for a standalone Supplier.

    Returns the Omni Identity name, or None if already synced.
    """
    if processed is None:
        processed = set()

    if _is_already_linked("Supplier", supplier_name):
        processed.add(_entity_key("Supplier", supplier_name))
        return None

    supplier = frappe.get_cached_doc("Supplier", supplier_name)
    contacts = _get_contacts_for_party("Supplier", supplier_name)
    phone, email = _extract_contact_details(contacts, supplier_name, "Supplier")

    existing = _find_existing_identity(phone, email)
    if existing:
        links = [("Supplier", supplier_name, "Primary Contact")]
        for contact in contacts:
            if not _is_already_linked("Contact", contact.name):
                links.append(("Contact", contact.name, "Primary Contact"))
                processed.add(_entity_key("Contact", contact.name))
        processed.add(_entity_key("Supplier", supplier_name))
        return _attach_to_existing(existing, links, phone, email)

    identity = frappe.get_doc({
        "doctype": "Omni Identity",
        "display_name": supplier.supplier_name or supplier_name,
        "primary_phone": phone,
        "primary_email": email,
        "primary_whatsapp": _digits_only(phone),
        "status": "Active",
        "is_master": 1,
    })

    identity.append("linked_entities", {
        "linked_doctype": "Supplier",
        "linked_name": supplier_name,
        "role": "Primary Contact",
    })
    processed.add(_entity_key("Supplier", supplier_name))

    for contact in contacts:
        if not _is_already_linked("Contact", contact.name):
            identity.append("linked_entities", {
                "linked_doctype": "Contact",
                "linked_name": contact.name,
                "role": "Primary Contact",
            })
            processed.add(_entity_key("Contact", contact.name))

    identity.flags.ignore_validate = True
    identity.insert(ignore_permissions=True)
    return identity.name


def sync_single_lead(
    lead_name: str, processed: Optional[set] = None
) -> Optional[str]:
    """
    Create an Omni Identity for an unconverted Lead.

    Skips leads that have already converted to Customer (their Customer
    identity covers them).

    Returns the Omni Identity name, or None if already synced or converted.
    """
    if processed is None:
        processed = set()

    if _is_already_linked("Lead", lead_name):
        processed.add(_entity_key("Lead", lead_name))
        return None

    lead = frappe.get_cached_doc("Lead", lead_name)

    if _lead_has_customer(lead):
        processed.add(_entity_key("Lead", lead_name))
        return None

    phone = lead.mobile_no or lead.phone or ""
    email = lead.email_id or ""
    display = lead.lead_name or lead.company_name or email or phone or "Unknown"

    existing = _find_existing_identity(phone, email)
    if existing:
        links = [("Lead", lead_name, "Decision Maker")]
        for contact in _get_contacts_for_party("Lead", lead_name):
            if not _is_already_linked("Contact", contact.name):
                links.append(("Contact", contact.name, "Primary Contact"))
                processed.add(_entity_key("Contact", contact.name))
        processed.add(_entity_key("Lead", lead_name))
        return _attach_to_existing(existing, links, phone, email)

    identity = frappe.get_doc({
        "doctype": "Omni Identity",
        "display_name": display,
        "primary_phone": phone,
        "primary_email": email,
        "primary_whatsapp": _digits_only(phone),
        "status": "Active",
        "is_master": 1,
    })

    identity.append("linked_entities", {
        "linked_doctype": "Lead",
        "linked_name": lead_name,
        "role": "Decision Maker",
    })
    processed.add(_entity_key("Lead", lead_name))

    contacts = _get_contacts_for_party("Lead", lead_name)
    for contact in contacts:
        if not _is_already_linked("Contact", contact.name):
            identity.append("linked_entities", {
                "linked_doctype": "Contact",
                "linked_name": contact.name,
                "role": "Primary Contact",
            })
            processed.add(_entity_key("Contact", contact.name))

    identity.flags.ignore_validate = True
    identity.insert(ignore_permissions=True)
    return identity.name


def sync_single_contact(
    contact_name: str, processed: Optional[set] = None
) -> Optional[str]:
    """
    Handle a newly created Contact.

    If the Contact is linked to a party (Customer/Supplier/Lead) that already
    has an Omni Identity, add the Contact as a linked entity on that identity
    and update phone/email if missing. If no parent party identity exists,
    create a new one only if the Contact has contact info.

    Returns the Omni Identity name, or None if skipped.
    """
    if processed is None:
        processed = set()

    if _is_already_linked("Contact", contact_name):
        processed.add(_entity_key("Contact", contact_name))
        return None

    contact = frappe.get_cached_doc("Contact", contact_name)
    phone = _get_contact_phone(contact)
    email = _get_contact_email(contact)

    if not phone and not email:
        return None

    parent_links = frappe.get_all(
        "Dynamic Link",
        filters={"parent": contact_name, "parenttype": "Contact"},
        fields=["link_doctype", "link_name"],
    )

    for dl in parent_links:
        if dl.link_doctype in ("Customer", "Supplier", "Lead"):
            existing_oi = frappe.db.get_value(
                "Omni Identity Link",
                {"linked_doctype": dl.link_doctype, "linked_name": dl.link_name},
                "parent",
            )
            if existing_oi:
                oi_doc = frappe.get_doc("Omni Identity", existing_oi)
                oi_doc.append("linked_entities", {
                    "linked_doctype": "Contact",
                    "linked_name": contact_name,
                    "role": "Primary Contact",
                })
                if not oi_doc.primary_phone and phone:
                    oi_doc.primary_phone = phone
                    oi_doc.primary_whatsapp = _digits_only(phone)
                if not oi_doc.primary_email and email:
                    oi_doc.primary_email = email
                oi_doc.flags.ignore_validate = True
                oi_doc.save(ignore_permissions=True)
                processed.add(_entity_key("Contact", contact_name))
                return oi_doc.name

    existing = _find_existing_identity(phone, email)
    if existing:
        links = [("Contact", contact_name, "Primary Contact")]
        for dl in parent_links:
            if dl.link_doctype in ("Customer", "Supplier", "Lead") and not _is_already_linked(dl.link_doctype, dl.link_name):
                links.append((dl.link_doctype, dl.link_name, "Primary Contact"))
                processed.add(_entity_key(dl.link_doctype, dl.link_name))
        processed.add(_entity_key("Contact", contact_name))
        return _attach_to_existing(existing, links, phone, email)

    display = contact.first_name or email or phone or "Unknown"
    identity = frappe.get_doc({
        "doctype": "Omni Identity",
        "display_name": display,
        "primary_phone": phone,
        "primary_email": email,
        "primary_whatsapp": _digits_only(phone),
        "status": "Active",
        "is_master": 1,
    })
    identity.append("linked_entities", {
        "linked_doctype": "Contact",
        "linked_name": contact_name,
        "role": "Primary Contact",
    })

    for dl in parent_links:
        if dl.link_doctype in ("Customer", "Supplier", "Lead"):
            if not _is_already_linked(dl.link_doctype, dl.link_name):
                identity.append("linked_entities", {
                    "linked_doctype": dl.link_doctype,
                    "linked_name": dl.link_name,
                    "role": "Primary Contact",
                })
                processed.add(_entity_key(dl.link_doctype, dl.link_name))

    identity.flags.ignore_validate = True
    identity.insert(ignore_permissions=True)
    processed.add(_entity_key("Contact", contact_name))
    return identity.name


def _find_existing_identity(phone: str = "", email: str = "") -> Optional[str]:
    """
    Resolve-before-create: an Active master identity with the same normalized phone/WhatsApp,
    email, or alias. Without this, every new Lead/Contact minted its own identity and one
    person ended up with three (P3 QA).
    """
    from excom.excom.doctype.omni_identity.omni_identity import normalize_phone, normalize_email

    norm_phone = normalize_phone(phone) if phone else ""
    norm_email = normalize_email(email) if email else ""
    if norm_phone and len(norm_phone) >= 8:
        for f in ("normalized_phone", "primary_whatsapp"):
            name = frappe.db.get_value("Omni Identity", {f: norm_phone, "status": ["!=", "Merged"]}, "name", order_by="is_master desc, creation asc")
            if name:
                return name
        alias = frappe.db.get_value("Omni Identity Alias", {"alias_value_normalized": norm_phone}, "parent")
        if alias and frappe.db.get_value("Omni Identity", alias, "status") != "Merged":
            return alias
    if norm_email:
        name = frappe.db.get_value("Omni Identity", {"normalized_email": norm_email, "status": ["!=", "Merged"]}, "name", order_by="is_master desc, creation asc")
        if name:
            return name
        alias = frappe.db.get_value("Omni Identity Alias", {"alias_value_normalized": norm_email}, "parent")
        if alias and frappe.db.get_value("Omni Identity", alias, "status") != "Merged":
            return alias
    return None


def _attach_to_existing(identity_name: str, links: list, phone: str = "", email: str = "") -> str:
    """Add entity links to an existing identity (deduped), filling missing phone/email."""
    oi = frappe.get_doc("Omni Identity", identity_name)
    have = {(l.linked_doctype, l.linked_name) for l in oi.get("linked_entities", [])}
    for dt, name, role in links:
        if (dt, name) not in have:
            oi.append("linked_entities", {"linked_doctype": dt, "linked_name": name, "role": role})
    if not oi.primary_phone and phone:
        oi.primary_phone = phone
        oi.primary_whatsapp = _digits_only(phone)
    if not oi.primary_email and email:
        oi.primary_email = email
    oi.flags.ignore_validate = True
    oi.save(ignore_permissions=True)
    return oi.name


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _entity_key(doctype: str, name: str) -> str:
    return f"{doctype}:{name}"


def _is_already_linked(doctype: str, name: str) -> bool:
    """Check if this entity is already linked to any Omni Identity."""
    return bool(
        frappe.db.get_value(
            "Omni Identity Link",
            {"linked_doctype": doctype, "linked_name": name},
            "parent",
        )
    )


def _get_party_linked_supplier(customer_name: str) -> str:
    """Check if Customer has a Party Link to a Supplier."""
    result = frappe.db.get_value(
        "Party Link",
        {"primary_role": "Customer", "primary_party": customer_name, "secondary_role": "Supplier"},
        "secondary_party",
    )
    if result:
        return result

    result = frappe.db.get_value(
        "Party Link",
        {"primary_role": "Supplier", "secondary_role": "Customer", "secondary_party": customer_name},
        "primary_party",
    )
    return result or ""


def _get_contacts_for_party(party_type: str, party_name: str) -> list:
    """Get all Contacts linked to a party via Dynamic Link."""
    contact_names = frappe.db.sql(
        """
        SELECT DISTINCT dl.parent
        FROM `tabDynamic Link` dl
        WHERE dl.link_doctype = %(dt)s
        AND dl.link_name = %(dn)s
        AND dl.parenttype = 'Contact'
        """,
        {"dt": party_type, "dn": party_name},
        as_dict=True,
    )
    contacts = []
    for row in contact_names:
        try:
            contacts.append(frappe.get_cached_doc("Contact", row.parent))
        except frappe.DoesNotExistError:
            pass
    return contacts


def _dedupe_contacts(contacts: list) -> list:
    """Remove duplicate Contact docs by name."""
    seen = set()
    unique = []
    for c in contacts:
        if c.name not in seen:
            seen.add(c.name)
            unique.append(c)
    return unique


def _extract_contact_details(
    contacts: list, party_name: str, party_type: str
) -> tuple:
    """
    Extract best phone and email from a list of Contacts.

    Falls back to the party doc's own fields if no Contact has details.
    Returns (phone, email).
    """
    phone, email = "", ""

    for contact in contacts:
        if not phone:
            phone = _get_contact_phone(contact)
        if not email:
            email = _get_contact_email(contact)
        if phone and email:
            break

    if not phone and not email and party_type in ("Customer", "Supplier"):
        try:
            party = frappe.get_cached_doc(party_type, party_name)
            phone = getattr(party, "mobile_no", "") or getattr(party, "phone", "") or ""
            email = getattr(party, "email_id", "") or ""
        except Exception:
            pass

    return phone, email


def _get_contact_phone(contact) -> str:
    """Extract first phone from Contact's phone child table."""
    for cp in contact.get("phone_nos", []):
        if cp.phone:
            return cp.phone
    return ""


def _get_contact_email(contact) -> str:
    """Extract first email from Contact's email child table."""
    for ce in contact.get("email_ids", []):
        if ce.email_id:
            return ce.email_id
    return ""


def _lead_has_customer(lead) -> bool:
    """Check if a Lead has been converted to a Customer."""
    if getattr(lead, "converted", 0):
        return True

    if frappe.db.exists(
        "Dynamic Link",
        {"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
    ):
        contact_name = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": "Lead", "link_name": lead.name, "parenttype": "Contact"},
            "parent",
        )
        if contact_name and frappe.db.exists(
            "Dynamic Link",
            {"link_doctype": "Customer", "parenttype": "Contact", "parent": contact_name},
        ):
            return True

    return False


def _digits_only(phone: str) -> str:
    """Strip non-digit characters."""
    import re
    if not phone:
        return ""
    return re.sub(r"[^\d]", "", phone)


def _update_progress(key: str, current: int, total: int, phase: str):
    """Update cache-based progress for UI polling."""
    frappe.cache.set_value(
        key,
        {
            "done": False,
            "current": current,
            "total": total,
            "phase": phase,
            "percent": round(current / max(total, 1) * 100),
        },
        expires_in_sec=3600,
    )
