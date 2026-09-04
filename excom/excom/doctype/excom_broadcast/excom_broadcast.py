"""Excom Broadcast controller — submittable doctype for sending bulk messages."""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from excom.excom.whatsapp_template_utils import (
    body_variable_slot_count,
    get_body_variable_samples,
    header_variable_slot_count,
    template_is_linked_to_account,
)


def _wa_template_variable_count(template_name: str) -> int:
    """Body variable count — prefers placeholder count, then sample length."""
    return body_variable_slot_count(template_name)


class ExcomBroadcast(Document):
    def validate(self):
        if self.channel == "WhatsApp" and not self.wa_template:
            frappe.throw(_("WhatsApp Template is required for WhatsApp broadcasts"))
        if self.channel == "Email":
            if not self.email_subject:
                frappe.throw(_("Subject is required for Email broadcasts"))
            if not self.email_body:
                frappe.throw(_("Email Body is required for Email broadcasts"))

        if self.channel == "WhatsApp" and self.wa_template:
            if self.wa_channel_account and not template_is_linked_to_account(
                self.wa_template, self.wa_channel_account
            ):
                frappe.throw(
                    _("This WhatsApp template is not linked to the selected channel account (phone number).")
                )
            hdr = frappe.db.get_value(
                "WhatsApp Templates",
                self.wa_template,
                ["header_type", "header", "header_variable_samples"],
                as_dict=True,
            )
            if hdr and header_variable_slot_count(hdr):
                frappe.throw(
                    _(
                        "Broadcasts do not support templates with variables in the TEXT header. "
                        "Create a Meta template without header placeholders, or use an IMAGE/VIDEO/DOCUMENT header."
                    ),
                    title=_("WhatsApp template"),
                )
            self._validate_whatsapp_variables()

    def before_submit(self) -> None:
        """Ensure a future ``scheduled_at`` when the user schedules a send."""
        scheduled = self.get("scheduled_at")
        if scheduled:
            at = get_datetime(scheduled)
            if at <= now_datetime():
                frappe.throw(_("Scheduled At must be in the future"))

    def _validate_whatsapp_variables(self) -> None:
        """Validate legacy same/per mode or per-slot JSON (wa_variable_slots)."""
        n = _wa_template_variable_count(self.wa_template)
        slots_raw = (self.get("wa_variable_slots") or "").strip()

        if slots_raw:
            try:
                slots = json.loads(slots_raw)
            except json.JSONDecodeError:
                frappe.throw(_("Variable Slots must be valid JSON"))
            if not isinstance(slots, list):
                frappe.throw(_("Variable Slots must be a JSON array"))
            if len(slots) > 0:
                if n == 0:
                    frappe.throw(_("This template has no body variables; clear Variable Slots"))
                if len(slots) != n:
                    frappe.throw(
                        _("Variable Slots has {0} entries but the template has {1} placeholders").format(
                            len(slots), n
                        )
                    )
                for idx, slot in enumerate(slots):
                    if not isinstance(slot, dict):
                        frappe.throw(_("Variable slot {0} must be an object").format(idx + 1))
                    kind = slot.get("kind")
                    if kind == "literal":
                        val = slot.get("value")
                        if val is None or (isinstance(val, str) and val.strip() == ""):
                            frappe.throw(
                                _("Variable {0}: enter a value for \"same for everyone\"").format(
                                    idx + 1
                                )
                            )
                    elif kind == "subscriber_field":
                        field = (slot.get("field") or "").strip()
                        if not field or "." not in field:
                            frappe.throw(
                                _("Variable {0}: choose a valid subscriber field").format(idx + 1)
                            )
                    else:
                        frappe.throw(
                            _('Variable {0}: `kind` must be "literal" or "subscriber_field"').format(
                                idx + 1
                            )
                        )
                return

        mode = self.wa_variable_mode or "same_for_all"
        if mode == "mixed":
            frappe.throw(_("Set Variable Slots JSON for mixed mode, or choose Same / Per subscriber"))
        if n > 0 and mode == "same_for_all":
            try:
                vals = json.loads(self.get("wa_template_variables") or "[]")
            except json.JSONDecodeError:
                frappe.throw(_("Template Variables must be valid JSON"))
            if not isinstance(vals, list) or len(vals) != n:
                frappe.throw(
                    _("Template Variables must be a JSON array of {0} strings").format(n)
                )
        if n > 0 and mode == "per_subscriber":
            try:
                mapping = json.loads(self.get("wa_variable_mapping") or "[]")
            except json.JSONDecodeError:
                frappe.throw(_("Variable Mapping must be valid JSON"))
            if not isinstance(mapping, list) or len(mapping) != n:
                frappe.throw(
                    _("Variable Mapping must be a JSON array of {0} field paths").format(n)
                )
            for idx, path in enumerate(mapping):
                if not path or "." not in str(path):
                    frappe.throw(_("Variable {0}: mapping must be a dotted field path").format(idx + 1))

    def on_submit(self):
        """Queue the broadcast for sending, or defer until ``scheduled_at``."""
        active = frappe.db.count(
            "Excom Subscriber",
            {"subscriber_list": self.subscriber_list, "status": "Subscribed"},
        )
        self.db_set("total_recipients", active, update_modified=False)

        scheduled = self.get("scheduled_at")
        if scheduled and get_datetime(scheduled) > now_datetime():
            self.db_set("status", "Scheduled", update_modified=False)
            return

        self.db_set("status", "Queued", update_modified=False)

        frappe.enqueue(
            "excom.excom.services.broadcast_service.execute_broadcast",
            broadcast_name=self.name,
            queue="long",
            timeout=3600,
            is_async=True,
        )

    def on_cancel(self):
        if self.status == "Sending":
            frappe.throw(_("Cannot cancel a broadcast that is currently sending"))
        self.db_set("status", "Draft", update_modified=False)

    @frappe.whitelist()
    def preview_email(self) -> dict:
        """
        Render the email body with the first subscriber's data for preview.

        Returns:
            {"subject": "...", "body": "...", "subscriber": "..."}
        """
        from excom.excom.api.chat import _check_excom_access
        _check_excom_access()
        if self.channel != "Email":
            frappe.throw(_("Preview is only for Email broadcasts"))

        first_sub = frappe.db.get_value(
            "Excom Subscriber",
            {"subscriber_list": self.subscriber_list, "status": "Subscribed"},
            "omni_identity",
        )
        if not first_sub:
            return {"subject": self.email_subject, "body": self.email_body, "subscriber": ""}

        from excom.excom.services.broadcast_service import render_email_body

        rendered = render_email_body(self.email_body, first_sub)
        return {
            "subject": self.email_subject,
            "body": rendered,
            "subscriber": first_sub,
        }
