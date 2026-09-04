"""Excom Subscriber List controller."""

import frappe
from frappe import _
from frappe.model.document import Document


class ExcomSubscriberList(Document):
    def validate(self):
        self.refresh_counts()

    def refresh_counts(self):
        """Recompute subscriber counts from Excom Subscriber records."""
        total = frappe.db.count("Excom Subscriber", {"subscriber_list": self.name})
        active = frappe.db.count(
            "Excom Subscriber",
            {"subscriber_list": self.name, "status": "Subscribed"},
        )
        self.total_subscribers = total
        self.active_subscribers = active

    @frappe.whitelist()
    def add_subscribers_from_doctype(
        self,
        doctype: str,
        filters: dict = None,
        limit: int = 0,
    ) -> dict:
        """
        Bulk-add subscribers by importing from an ERPNext DocType.

        For each matching record, resolves or creates an Omni Identity
        and adds it to this list.

        Args:
            doctype: Customer, Supplier, or Lead
            filters: Frappe-style filters dict
            limit: Max records to import (0 = all)

        Returns:
            {"added": int, "skipped": int}
        """
        from excom.excom.api.chat import _check_manager_access
        _check_manager_access()
        from excom.excom.services.identity_sync import (
            sync_single_customer,
            sync_single_supplier,
            sync_single_lead,
        )

        sync_map = {
            "Customer": sync_single_customer,
            "Supplier": sync_single_supplier,
            "Lead": sync_single_lead,
        }

        sync_fn = sync_map.get(doctype)
        if not sync_fn:
            frappe.throw(
                _("Unsupported doctype: {0}. Use Customer, Supplier, or Lead.").format(doctype)
            )

        query_filters = filters or {}
        records = frappe.get_all(
            doctype,
            filters=query_filters,
            pluck="name",
            limit=limit or 0,
        )

        added, skipped = 0, 0
        for name in records:
            sync_fn(name)

            oi_name = frappe.db.get_value(
                "Omni Identity Link",
                {"linked_doctype": doctype, "linked_name": name},
                "parent",
            )
            if not oi_name:
                skipped += 1
                continue

            if frappe.db.exists(
                "Excom Subscriber",
                {"subscriber_list": self.name, "omni_identity": oi_name},
            ):
                skipped += 1
                continue

            frappe.get_doc({
                "doctype": "Excom Subscriber",
                "subscriber_list": self.name,
                "omni_identity": oi_name,
                "status": "Subscribed",
                "subscribed_on": frappe.utils.now_datetime(),
            }).insert(ignore_permissions=True)
            added += 1

        self.refresh_counts()
        self.save(ignore_permissions=True)
        frappe.db.commit()

        return {"added": added, "skipped": skipped}
