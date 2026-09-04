"""Excom Notification — triggers WhatsApp template sends on doc events and schedules."""

import json
import frappe

from frappe import _
from frappe.model.document import Document
from frappe.utils.safe_exec import get_safe_globals, safe_exec
from frappe.desk.form.utils import get_pdf_link
from frappe.utils import add_to_date, nowdate, now_datetime, datetime

from excom.excom.utils import get_channel_account


class ExcomNotification(Document):
    """Notification."""

    def validate(self):
        """Validate."""
        if self.notification_type == "DocType Event":
            fields = frappe.get_doc("DocType", self.reference_doctype).fields
            fields += frappe.get_all(
                "Custom Field",
                filters={"dt": self.reference_doctype},
                fields=["fieldname"]
            )
            if not any(field.fieldname == self.field_name for field in fields): # noqa
                frappe.throw(_("Field name {0} does not exists").format(self.field_name))
        if self.custom_attachment:
            if not self.attach and not self.attach_from_field:
                frappe.throw(_("Either {0} a file or add a {1} to send attachemt").format(
                    frappe.bold(_("Attach")),
                    frappe.bold(_("Attach from field")),
                ))

        if self.set_property_after_alert:
            meta = frappe.get_meta(self.reference_doctype)
            if not meta.get_field(self.set_property_after_alert):
                frappe.throw(_("Field {0} not found on DocType {1}").format(
                    self.set_property_after_alert,
                    self.reference_doctype,
                ))

        self.validate_delay_fields()

    def _queue_delayed_notification(self, doc, doc_data, phone_number):
        """Create a Pending Excom Notification Log for delayed dispatch."""
        unit = (self.delay_unit or "Minutes").lower()
        value = int(self.delay_value or 1)
        kwargs = {"minutes": 0, "hours": 0, "days": 0}
        if unit == "minutes":
            kwargs["minutes"] = value
        elif unit == "hours":
            kwargs["hours"] = value
        else:
            kwargs["days"] = value

        scheduled_for = add_to_date(now_datetime(), **kwargs, as_datetime=True)
        to_number = self.format_number(phone_number) if phone_number else ""

        frappe.get_doc({
            "doctype": "Excom Notification Log",
            "notification": self.name,
            "status": "Pending",
            "scheduled_for": scheduled_for,
            "reference_doctype": doc_data.get("doctype"),
            "reference_name": doc_data.get("name"),
            "to_number": to_number,
            "template": self.template,
        }).insert(ignore_permissions=True)

    def validate_delay_fields(self):
        """When enable_delay is checked, delay_value must be a positive integer."""
        if not self.enable_delay:
            return
        if not self.delay_value or int(self.delay_value) <= 0:
            frappe.throw(
                _("Delay Value must be a positive integer when delay is enabled")
            )
        if not self.delay_unit:
            frappe.throw(
                _("Delay Unit is required when delay is enabled")
            )


    def send_scheduled_message(self) -> dict:
        """Specific to API endpoint Server Scripts."""
        safe_exec(
            self.condition, get_safe_globals(), dict(doc=self)
        )

        template = frappe.db.get_value(
            "WhatsApp Templates", self.template,
            fieldname='*'
        )

        if template and template.language_code:
            if self.get("_contact_list"):
                # send simple template without a doc to get field data.
                self.send_simple_template(template)
            elif self.get("_data_list"):
                # allow send a dynamic template using schedule event config
                # _doc_list shoud be [{"name": "xxx", "phone_no": "123"}]
                for data in self._data_list:
                    doc = frappe.get_doc(self.reference_doctype, data.get("name"))

                    self.send_template_message(doc, data.get("phone_no"), template, True)
        # return _globals.frappe.flags


    def send_simple_template(self, template):
        """Send simple template without a doc to get field data."""
        for contact in self._contact_list:
            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(contact),
                "type": "template",
                "template": {
                    "name": template.actual_name,
                    "language": {"code": template.language_code},
                    "components": [],
                },
            }
            self.notify(data, template_account=template.get("whatsapp_account"))


    def send_template_message(
        self,
        doc: Document,
        phone_no=None,
        default_template=None,
        ignore_condition=False,
        notification_log_name=None,
        force_send=False,
    ):
        """Specific to Document Event triggered Server Scripts."""
        if self.disabled:
            return

        doc_data = doc.as_dict()
        if self.condition and not ignore_condition:
            if not frappe.safe_eval(
                self.condition, get_safe_globals(), dict(doc=doc_data)
            ):
                return

        if self.recipient_source == "Subscriber List" and self.subscriber_list:
            self._send_to_subscriber_list(doc, doc_data, default_template, force_send)
            return

        template = default_template or frappe.get_doc("WhatsApp Templates", self.template)

        if template:
            if self.field_name:
                phone_number = phone_no or doc_data[self.field_name]
            else:
                phone_number = phone_no

            # Queue as delayed when enable_delay; processor will call back with force_send=True
            if self.enable_delay and not force_send and self.notification_type == "DocType Event":
                self._queue_delayed_notification(doc, doc_data, phone_number)
                return

            data = {
                "messaging_product": "whatsapp",
                "to": self.format_number(phone_number),
                "type": "template",
                "template": {
                    "name": template.actual_name,
                    "language": {
                        "code": template.language_code
                    },
                    "components": []
                }
            }

            # Pass parameter values
            if self.fields:
                parameters = []
                for field in self.fields:
                    if isinstance(doc, Document):
                        # get field with prettier value.
                        value = doc.get_formatted(field.field_name)
                    else: 
                        value = doc_data[field.field_name]
                        if isinstance(doc_data[field.field_name], (datetime.date, datetime.datetime)):
                            value = str(doc_data[field.field_name])

                    parameters.append({
                        "type": "text",
                        "text": value
                    })

                data['template']["components"] = [{
                    "type": "body",
                    "parameters": parameters
                }]

            if self.attach_document_print:
                # frappe.db.begin()
                key = doc.get_document_share_key()  # noqa
                frappe.db.commit()
                print_format = "Standard"
                doctype = frappe.get_doc("DocType", doc_data['doctype'])
                if doctype.custom:
                    if doctype.default_print_format:
                        print_format = doctype.default_print_format
                else:
                    default_print_format = frappe.db.get_value(
                        "Property Setter",
                        filters={
                            "doc_type": doc_data['doctype'],
                            "property": "default_print_format"
                        },
                        fieldname="value"
                    )
                    print_format = default_print_format if default_print_format else print_format
                link = get_pdf_link(
                    doc_data['doctype'],
                    doc_data['name'],
                    print_format=print_format
                )

                filename = f'{doc_data["name"]}.pdf'
                url = f'{frappe.utils.get_url()}{link}&key={key}'

            elif self.custom_attachment:
                filename = self.file_name

                if self.attach_from_field:
                    file_url = doc_data[self.attach_from_field]
                    if not file_url.startswith("http"):
                        # get share key so that private files can be sent
                        key = doc.get_document_share_key()
                        file_url = f'{frappe.utils.get_url()}{file_url}&key={key}'
                else:
                    file_url = self.attach

                if file_url.startswith("http"):
                    url = f'{file_url}'
                else:
                    url = f'{frappe.utils.get_url()}{file_url}'

            if template.header_type == 'DOCUMENT':
                data['template']['components'].append({
                    "type": "header",
                    "parameters": [{
                        "type": "document",
                        "document": {
                            "link": url,
                            "filename": filename
                        }
                    }]
                })
            elif template.header_type == 'IMAGE':
                data['template']['components'].append({
                    "type": "header",
                    "parameters": [{
                        "type": "image",
                        "image": {
                            "link": url
                        }
                    }]
                })
            self.content_type = template.header_type.lower()

            if template.buttons:
                button_fields = self.button_fields.split(",") if self.button_fields else []
                for idx, btn in enumerate(template.buttons):
                    if btn.button_type == "Visit Website" and btn.url_type == "Dynamic":
                        if button_fields:
                            data['template']['components'].append({
                                "type": "button",
                                "sub_type": "url",
                                "index": str(idx),
                                "parameters": [
                                    {"type": "text", "text": doc.get(button_fields.pop(0))}
                                ]
                            })


            self.notify(data, doc_data, template_account=template.whatsapp_account, force_send=force_send)

    def notify(self, data, doc_data=None, template_account=None, force_send=False):
        """Send WhatsApp template via whatsapp_service and record as Excom Message."""
        if template_account:
            account_doc = frappe.get_doc("Excom Channel Account", template_account)
        else:
            account_doc = get_channel_account(channel='whatsapp', account_type='outgoing')

        if not account_doc:
            frappe.throw(_("Please set a default outgoing WhatsApp Channel Account"))

        from excom.excom.services.whatsapp_service import send_template_message

        template_doc = frappe.get_doc("WhatsApp Templates", self.template)
        template_name = data["template"]["name"]
        language_code = data["template"]["language"]["code"]
        components = data["template"].get("components") or []
        to_number = data["to"]

        error_message = ""
        success = False
        result = {}
        try:
            result = send_template_message(
                account=account_doc,
                to=to_number,
                template_name=template_name,
                language_code=language_code,
                components=components if components else None,
            )

            self._create_excom_message(
                account_doc=account_doc,
                to_number=to_number,
                template_doc=template_doc,
                provider_message_id=result.get("provider_message_id", ""),
                delivery_status=result.get("status", "Sent"),
                doc_data=doc_data,
            )

            if doc_data and self.set_property_after_alert and self.property_value:
                if doc_data.doctype and doc_data.name:
                    fieldname = self.set_property_after_alert
                    value = self.property_value
                    meta = frappe.get_meta(doc_data.get("doctype"))
                    df = meta.get_field(fieldname)
                    if df:
                        if df.fieldtype in frappe.model.numeric_fieldtypes:
                            value = frappe.utils.cint(value)
                        frappe.db.set_value(doc_data.get("doctype"), doc_data.get("name"), fieldname, value)

            frappe.msgprint("Notification sent", indicator="green", alert=True)
            success = True

        except Exception as e:
            error_message = str(e)
            frappe.msgprint(
                f"Failed to send notification: {error_message}",
                indicator="red",
                alert=True,
            )
            if force_send:
                raise
        finally:
            frappe.get_doc({
                "doctype": "Excom Notification Log",
                "template": self.template,
                "meta_data": {"error": error_message} if not success else result,
            }).insert(ignore_permissions=True)

    def _create_excom_message(
        self, account_doc, to_number, template_doc,
        provider_message_id="", delivery_status="Sent", doc_data=None,
    ):
        """Create an Excom Thread + Message for the notification send."""
        from excom.excom.doctype.omni_identity.omni_identity import resolve_identity
        from excom.excom.services.thread_service import upsert_thread
        from excom.excom.api.chat import _build_template_preview

        try:
            identity_name = resolve_identity(
                phone=to_number,
                channel="whatsapp",
                channel_user_id=to_number,
                display_name=to_number,
            )
            thread_name = upsert_thread(identity_name, "whatsapp", account_doc.name)

            body_vars = []
            if self.fields:
                body_vars = [f.field_name for f in self.fields]
            preview = _build_template_preview(template_doc, body_vars)

            msg_data = {
                "doctype": "Excom Message",
                "thread": thread_name,
                "omni_identity": identity_name,
                "channel": "whatsapp",
                "account_doctype": "Excom Channel Account",
                "account": account_doc.name,
                "direction": "Outbound",
                "message_type": "Template",
                "provider_message_id": provider_message_id,
                "provider_timestamp": now_datetime(),
                "content_text": preview[:500],
                "delivery_status": delivery_status,
                "template": self.template,
            }
            if doc_data:
                msg_data["reference_doctype"] = doc_data.get("doctype")
                msg_data["reference_name"] = doc_data.get("name")

            frappe.get_doc(msg_data).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(title=f"Excom Message creation failed for notification {self.name}")


    def _send_to_subscriber_list(self, doc, doc_data, default_template=None, force_send=False):
        """Send the template to all active subscribers in the linked list."""
        subscribers = frappe.get_all(
            "Excom Subscriber",
            filters={
                "subscriber_list": self.subscriber_list,
                "status": "Subscribed",
            },
            fields=["omni_identity"],
        )

        for sub in subscribers:
            try:
                identity = frappe.get_doc("Omni Identity", sub.omni_identity)
                phone = identity.primary_whatsapp or identity.primary_phone
                if not phone:
                    continue

                self.send_template_message(
                    doc,
                    phone_no=phone,
                    default_template=default_template,
                    ignore_condition=True,
                    force_send=force_send,
                )
            except Exception:
                frappe.log_error(
                    title=f"Subscriber list send failed: {sub.omni_identity}",
                )

    def on_trash(self):
        """On delete remove from schedule."""
        frappe.cache().delete_value("excom_notification_map")


    def format_number(self, number):
        """Format number."""
        if (number.startswith("+")):
            number = number[1:len(number)]

        return number

    def get_documents_for_today(self):
        """get list of documents that will be triggered today"""
        docs = []

        diff_days = self.days_in_advance
        if self.doctype_event == "Days After":
            diff_days = -diff_days

        reference_date = add_to_date(nowdate(), days=diff_days)
        reference_date_start = reference_date + " 00:00:00.000000"
        reference_date_end = reference_date + " 23:59:59.000000"

        doc_list = frappe.get_all(
            self.reference_doctype,
            fields="name",
            filters=[
                {self.date_changed: (">=", reference_date_start)},
                {self.date_changed: ("<=", reference_date_end)},
            ],
        )

        for d in doc_list:
            doc = frappe.get_doc(self.reference_doctype, d.name)
            self.send_template_message(doc)
            # print(doc.name)


@frappe.whitelist()
def call_trigger_notifications():
    """Trigger notifications."""
    from excom.excom.api.chat import _check_manager_access
    _check_manager_access()
    try:
        # Directly call the trigger_notifications function
        trigger_notifications()  
    except Exception as e:
        # Log the error but do not show any popup or alert
        frappe.log_error(frappe.get_traceback(), "Error in call_trigger_notifications")
        # Optionally, you could raise the exception to be handled elsewhere if needed
        raise e

def trigger_notifications(method="daily"):
    if frappe.flags.in_import or frappe.flags.in_patch:
        # don't send notifications while syncing or patching
        return

    doctype_name = "Excom Notification"
    if not frappe.db.table_exists(doctype_name):
        doctype_name = "WhatsApp Notification"

    if method == "daily":
        doc_list = frappe.get_all(
            doctype_name, filters={"doctype_event": ("in", ("Days Before", "Days After")), "disabled": 0}
        )
        for d in doc_list:
            alert = frappe.get_doc(doctype_name, d.name)
            alert.get_documents_for_today()
           
