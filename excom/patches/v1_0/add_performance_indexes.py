"""
Add missing database indexes for query performance.

Phase 1.3.1 — covers WhatsApp Message, WhatsApp Notification Log,
Excom Message, and Excom Thread.

The first two belong to `frappe_whatsapp`, the app Excom grew out of. A site upgraded from it has
them; a site where Excom was installed fresh never did, and asking MariaDB about an index on a
table that does not exist raises rather than answering no. That took a whole migration down with
it — and with it every Excom patch that comes after.
"""

import frappe


def execute():
    indexes = [
        # WhatsApp Message — common query patterns
        ("tabWhatsApp Message", "idx_wa_msg_to_creation", ["`to`", "`creation`"]),
        ("tabWhatsApp Message", "idx_wa_msg_from_creation", ["`from`", "`creation`"]),
        ("tabWhatsApp Message", "idx_wa_msg_message_id", ["`message_id`"]),
        ("tabWhatsApp Message", "idx_wa_msg_status_type_from", ["`status`", "`type`", "`from`"]),
        ("tabWhatsApp Message", "idx_wa_msg_bulk_ref", ["`bulk_message_reference`"]),

        # WhatsApp Notification Log — pending processor query
        ("tabWhatsApp Notification Log", "idx_wa_notif_log_status_sched", ["`status`", "`scheduled_for`"]),

        # Excom Message — idempotency + thread listing
        ("tabExcom Message", "idx_excom_msg_provider_id", ["`provider_message_id`"]),
        ("tabExcom Message", "idx_excom_msg_thread_creation", ["`thread`", "`creation`"]),

        # Excom Thread — inbox query + identity lookup
        ("tabExcom Thread", "idx_excom_thread_key", ["`thread_key`"]),
        ("tabExcom Thread", "idx_excom_thread_last_msg", ["`last_message_at`"]),
        ("tabExcom Thread", "idx_excom_thread_identity_ch_acc", ["`omni_identity`", "`channel`", "`account`"]),
    ]

    for table, idx_name, columns in indexes:
        # `table_exists` takes the doctype, so strip the prefix back off. An index is only ever
        # worth anything against rows, and a table that was never created has none.
        if not frappe.db.table_exists(table[len("tab"):], cached=False):
            continue
        if not frappe.db.has_index(table, idx_name):
            col_str = ", ".join(columns)
            frappe.db.sql_ddl(
                f"CREATE INDEX `{idx_name}` ON `{table}` ({col_str})"
            )
