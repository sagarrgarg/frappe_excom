"""Create whatsapp template."""

# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import os
import json
import frappe
import magic
from frappe import _
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request, make_request
from frappe.desk.form.utils import get_pdf_link

from excom.excom.utils import get_channel_account, get_wa_credentials
from excom.excom.whatsapp_template_utils import (
    get_body_variable_samples,
    get_header_variable_samples,
    is_text_header,
)

_TEMPLATE_CONTENT_FIELDS = frozenset({
    "template", "header_type", "header", "footer", "category",
    "header_variable_samples", "body_variable_samples", "sample",
})


class WhatsAppTemplates(Document):
    """Create whatsapp template."""

    def validate(self):
        self._validate_body_variable_samples_json()
        self._validate_header_variable_samples_json()
        self.set_whatsapp_account()
        self._auto_link_same_business_accounts()
        if not self.language_code or self.has_value_changed("language"):
            lang_code = frappe.db.get_value("Language", self.language) or "en"
            self.language_code = lang_code.replace("-", "_")

        self.validate_button_count()

        if (self.header_type or "").upper() in ("IMAGE", "VIDEO", "DOCUMENT") and self.sample:
            self.get_session_id()
            self.get_media_id()

        if not self.is_new() and self._has_content_changed():
            self.update_template()

    def validate_button_count(self):
        """Enforce Meta's button limits: max 3 Quick Reply, max 2 CTA."""
        if not self.buttons:
            return
        quick_reply = sum(1 for b in self.buttons if b.button_type == "Quick Reply")
        cta = sum(1 for b in self.buttons if b.button_type in ("Visit Website", "Call Phone"))
        if quick_reply > 3:
            frappe.throw(
                _("Quick Reply buttons cannot exceed 3 (found {0})").format(quick_reply)
            )
        if cta > 2:
            frappe.throw(
                _("CTA buttons (Visit Website / Call Phone) cannot exceed 2 (found {0})").format(cta)
            )

    def _validate_body_variable_samples_json(self) -> None:
        """Ensure ``body_variable_samples`` is a JSON array of scalars when set."""
        val = self.get("body_variable_samples")
        if val in (None, "", []):
            return
        data = val
        if isinstance(val, str):
            if not val.strip():
                self.body_variable_samples = None
                return
            try:
                data = json.loads(val)
            except json.JSONDecodeError:
                frappe.throw(_("Body Variable Samples must be valid JSON"))
        if not isinstance(data, list):
            frappe.throw(_("Body Variable Samples must be a JSON array"))
        for i, x in enumerate(data):
            if x is not None and not isinstance(x, (str, int, float)):
                frappe.throw(
                    _("Body variable sample {0} must be a string or number").format(i + 1)
                )

    def _validate_header_variable_samples_json(self) -> None:
        """Ensure ``header_variable_samples`` is a JSON array of scalars when set."""
        val = self.get("header_variable_samples")
        if val in (None, "", []):
            return
        data = val
        if isinstance(val, str):
            if not val.strip():
                self.header_variable_samples = None
                return
            try:
                data = json.loads(val)
            except json.JSONDecodeError:
                frappe.throw(_("Header Variable Samples must be valid JSON"))
        if not isinstance(data, list):
            frappe.throw(_("Header Variable Samples must be a JSON array"))
        for i, x in enumerate(data):
            if x is not None and not isinstance(x, (str, int, float)):
                frappe.throw(
                    _("Header variable sample {0} must be a string or number").format(i + 1)
                )

    def set_whatsapp_account(self):
        """Prefer primary from linked accounts; else default outgoing channel account."""
        links = self.get("linked_whatsapp_accounts") or []
        if links and links[0].channel_account and not self.whatsapp_account:
            self.whatsapp_account = links[0].channel_account
        if not self.whatsapp_account:
            default_account = get_channel_account(channel="whatsapp", account_type="outgoing")
            if not default_account:
                frappe.throw(_("Please set a default outgoing WhatsApp Channel Account"))
            self.whatsapp_account = default_account.name

    def _auto_link_same_business_accounts(self) -> None:
        """Auto-link all WhatsApp channel accounts sharing the same WABA business ID.

        Templates belong to a WABA (business ID), not a single phone number.
        All active accounts under the same business ID can send this template.
        """
        if not self.whatsapp_account:
            return

        biz_id = frappe.db.get_value(
            "Excom Channel Account", self.whatsapp_account, "wa_business_id"
        )
        if not biz_id:
            return

        sibling_accounts = frappe.get_all(
            "Excom Channel Account",
            filters={
                "status": "Active",
                "channel": "WhatsApp",
                "wa_business_id": biz_id,
            },
            pluck="name",
        )

        existing = {
            getattr(r, "channel_account", None)
            for r in self.get("linked_whatsapp_accounts") or []
        }

        for acct_name in sibling_accounts:
            if acct_name not in existing:
                self.append("linked_whatsapp_accounts", {"channel_account": acct_name})
                existing.add(acct_name)

    def _has_content_changed(self) -> bool:
        """Return True if any template content field changed (triggers Meta API update).

        Linking accounts or editing metadata should not push to Meta.
        Button child table changes are also tracked.
        """
        for field in _TEMPLATE_CONTENT_FIELDS:
            if self.has_value_changed(field):
                return True

        old_buttons = frappe.get_all(
            "WhatsApp Button",
            filters={"parent": self.name, "parenttype": self.doctype},
            fields=["button_type", "button_label", "website_url", "example_url",
                     "url_type", "phone_number"],
            order_by="idx asc",
        )
        new_buttons = [
            {
                "button_type": b.button_type, "button_label": b.button_label,
                "website_url": b.get("website_url"), "example_url": b.get("example_url"),
                "url_type": b.get("url_type"), "phone_number": b.get("phone_number"),
            }
            for b in (self.buttons or [])
        ]
        if len(old_buttons) != len(new_buttons):
            return True
        for old, new in zip(old_buttons, new_buttons):
            if dict(old) != new:
                return True

        return False

    def get_session_id(self):
        """Upload media to Meta for template header sample."""
        creds = self._get_creds()
        file_path = self.get_absolute_path(self.sample)
        mime = magic.Magic(mime=True)
        file_type = mime.from_file(file_path)

        payload = {
            'file_length': os.path.getsize(file_path),
            'file_type': file_type,
            'messaging_product': 'whatsapp'
        }

        response = make_post_request(
            f"{creds['url']}/{creds['version']}/{creds['app_id']}/uploads",
            headers=creds['headers'],
            data=json.loads(json.dumps(payload))
        )
        self._session_id = response['id']

    def get_media_id(self):
        creds = self._get_creds()
        headers = {"authorization": f"OAuth {creds['token']}"}
        file_name = self.get_absolute_path(self.sample)
        with open(file_name, mode='rb') as file:
            file_content = file.read()

        response = make_post_request(
            f"{creds['url']}/{creds['version']}/{self._session_id}",
            headers=headers,
            data=file_content
        )
        self._media_id = response['h']

    def get_absolute_path(self, file_name):
        if file_name.startswith('/files/'):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}/public{file_name}'
        if file_name.startswith('/private/'):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}{file_name}'
        return file_path


    def after_insert(self):
        if self.template_name:
            self.actual_name = self.template_name.lower().replace(" ", "_")

        creds = self._get_creds()
        data = {
            "name": self.actual_name,
            "language": self.language_code,
            "category": self.category,
            "components": [],
        }

        body = {
            "type": "BODY",
            "text": self.template,
        }
        samples = get_body_variable_samples(self)
        if samples:
            body["example"] = {"body_text": [samples]}

        data["components"].append(body)
        if self.header_type:
            data["components"].append(self.get_header())

        if self.footer:
            data["components"].append({"type": "FOOTER", "text": self.footer})

        if self.buttons:
            button_block = {"type": "BUTTONS", "buttons": []}
            for btn in self.buttons:
                b = {"type": btn.button_type, "text": btn.button_label}

                if btn.button_type == "Visit Website":
                    b["type"] = "URL"
                    b["url"] = btn.website_url
                    if btn.url_type == "Dynamic" and btn.example_url:
                        b["example"] = [v.strip() for v in btn.example_url.split(",")]
                elif btn.button_type == "Call Phone":
                    b["type"] = "PHONE_NUMBER"
                    b["phone_number"] = btn.phone_number
                elif btn.button_type == "Quick Reply":
                    b["type"] = "QUICK_REPLY"

                button_block["buttons"].append(b)
            data["components"].append(button_block)

        try:
            response = make_post_request(
                f"{creds['url']}/{creds['version']}/{creds['business_id']}/message_templates",
                headers=creds['headers'],
                data=json.dumps(data),
            )
            self.id = response["id"]
            self.status = response["status"]
            self.db_update()
        except Exception:
            error_detail = _extract_meta_error()
            frappe.throw(
                msg=_("Meta rejected the template: {0}").format(error_detail),
                title=_("Template Creation Failed"),
            )

    def update_template(self):
        """Update template on Meta.

        Only called when content fields have actually changed
        (guarded by ``_has_content_changed`` in validate).
        """
        if not self.id:
            return

        creds = self._get_creds()
        data = {"components": []}

        body = {
            "type": "BODY",
            "text": self.template,
        }
        samples = get_body_variable_samples(self)
        if samples:
            body["example"] = {"body_text": [samples]}
        data["components"].append(body)

        if self.header_type:
            data["components"].append(self.get_header())

        if self.footer:
            data["components"].append({"type": "FOOTER", "text": self.footer})

        if self.buttons:
            button_block = {"type": "BUTTONS", "buttons": []}
            for btn in self.buttons:
                b = {"type": btn.button_type, "text": btn.button_label}

                if btn.button_type == "Visit Website":
                    b["type"] = "URL"
                    b["url"] = btn.website_url
                    if btn.url_type == "Dynamic" and btn.example_url:
                        b["example"] = [v.strip() for v in btn.example_url.split(",")]
                elif btn.button_type == "Call Phone":
                    b["type"] = "PHONE_NUMBER"
                    b["phone_number"] = btn.phone_number
                elif btn.button_type == "Quick Reply":
                    b["type"] = "QUICK_REPLY"

                button_block["buttons"].append(b)
            data["components"].append(button_block)

        try:
            make_post_request(
                f"{creds['url']}/{creds['version']}/{self.id}",
                headers=creds['headers'],
                data=json.dumps(data),
            )
        except Exception:
            error_detail = _extract_meta_error()
            frappe.log_error(
                title="WhatsApp Template Update Failed",
                message=f"Template: {self.name}\nMeta error: {error_detail}",
            )
            frappe.throw(
                msg=_("Meta rejected the template update: {0}").format(error_detail),
                title=_("Template Update Failed"),
            )

    def _get_creds(self) -> dict:
        """Get WhatsApp API credentials from the linked Excom Channel Account."""
        account_doc = frappe.get_doc("Excom Channel Account", self.whatsapp_account)
        return get_wa_credentials(account_doc)

    def on_trash(self):
        creds = self._get_creds()
        url = f"{creds['url']}/{creds['version']}/{creds['business_id']}/message_templates?name={self.actual_name}"
        try:
            make_request("DELETE", url, headers=creds['headers'])
        except Exception:
            error_detail = _extract_meta_error()
            if "not found" in error_detail.lower():
                frappe.msgprint(
                    _("Template already deleted on Meta — removed locally."),
                    alert=True,
                )
            else:
                frappe.throw(
                    msg=_("Meta rejected the delete: {0}").format(error_detail),
                    title=_("Template Delete Failed"),
                )

    def get_header(self):
        """Build Meta API header component for template create/update.

        Meta format:
        - TEXT: header_text is a flat list ``["val"]``
        - IMAGE/VIDEO/DOCUMENT: header_handle with uploaded media ID
        - LOCATION: no parameters (location supplied at send time)
        """
        fmt = (self.header_type or "").strip().upper()
        header = {"type": "HEADER", "format": fmt}
        if is_text_header(self.header_type):
            header["text"] = self.header
            h_samples = get_header_variable_samples(self)
            if h_samples:
                header["example"] = {"header_text": h_samples}
        elif fmt == "LOCATION":
            pass
        elif hasattr(self, "_media_id") and self._media_id:
            header["example"] = {"header_handle": [self._media_id]}

        return header


def _meta_error_from(exc: Exception) -> str:
    """Meta's own words, taken from the failed response attached to a requests HTTPError."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return ""
    try:
        err = ((resp.json() or {}).get("error")) or {}
        parts = [p for p in (err.get("error_user_title"), err.get("error_user_msg"), err.get("message"), (f"code {err.get('code')}" if err.get("code") else "")) if p]
        return " — ".join(parts) if parts else (resp.text or "")[:300]
    except Exception:
        return (getattr(resp, "text", "") or "")[:300]


def _extract_meta_error() -> str:
    """Extract a human-readable error message from the last Meta API response."""
    try:
        resp = getattr(frappe.flags, "integration_request", None)
        if resp and hasattr(resp, "json"):
            err = resp.json().get("error", {})
            user_msg = err.get("error_user_msg") or ""
            api_msg = err.get("message") or ""
            title = err.get("error_user_title") or ""
            parts = [p for p in (title, user_msg, api_msg) if p]
            if parts:
                return " — ".join(parts)
        if resp and hasattr(resp, "text"):
            return resp.text[:300]
    except Exception:
        pass
    return "Unknown error (no details from Meta)"


def _merge_template_linked_accounts(doc, account_names: list[str]) -> None:
    """Link multiple channel accounts to a template doc (dedup-safe)."""
    existing = {
        getattr(r, "channel_account", None)
        for r in doc.get("linked_whatsapp_accounts") or []
    }
    for name in account_names:
        if name not in existing:
            doc.append("linked_whatsapp_accounts", {"channel_account": name})
            existing.add(name)
    if not doc.whatsapp_account and account_names:
        doc.whatsapp_account = account_names[0]


def _extract_header_samples(example: dict) -> str | None:
    """Extract header variable samples from Meta's example block.

    Handles both positional (``header_text``) and named
    (``header_text_named_params``) formats from the API.

    Returns JSON string or None.
    """
    if not example:
        return None

    hdr_ex = example.get("header_text")
    if hdr_ex and isinstance(hdr_ex, list) and len(hdr_ex) > 0:
        row = hdr_ex[0]
        if isinstance(row, list):
            return json.dumps(row)
        return json.dumps([row])

    named = example.get("header_text_named_params")
    if named and isinstance(named, list):
        samples = [
            p.get("example", "") if isinstance(p, dict) else str(p)
            for p in named
        ]
        if any(s for s in samples):
            return json.dumps(samples)

    return None


def _fetch_all_templates(creds: dict, biz_id: str) -> dict:
    """GET /{waba}/message_templates following paging.next.

    Meta pages at 25; a busy WABA has more. Some WABAs / API versions reject the explicit field list or a
    large limit with a plain 400, so a first failure is retried with the bare request the vendor apps use.
    """
    base = (creds["url"] or "https://graph.facebook.com").rstrip("/")
    version = creds["version"] or "v21.0"
    if not version.startswith("v"):
        version = "v" + version
    root = f"{base}/{version}/{biz_id}/message_templates"
    attempts = [f"{root}?fields=name,status,language,category,id,components&limit=100", root]
    last_exc = None
    for start_url in attempts:
        data: list = []
        url = start_url
        pages = 0
        try:
            while url and pages < 50:
                frappe.flags.integration_request = None
                page = make_request("GET", url, headers=creds["headers"]) or {}
                data.extend(page.get("data") or [])
                url = (page.get("paging") or {}).get("next")
                pages += 1
            return {"data": data, "pages": pages}
        except Exception as e:
            last_exc = e
            continue
    raise last_exc



@frappe.whitelist()
def fetch():
    """Fetch templates from Meta for all active WhatsApp channel accounts.

    Groups accounts by ``wa_business_id`` so each WABA is queried once,
    and every account under that business ID is auto-linked to each template.
    """
    accounts = frappe.get_all(
        "Excom Channel Account",
        filters={"status": "Active", "channel": ["in", ["whatsapp", "WhatsApp"]]},
        fields=["name", "wa_business_id"],
    )
    if accounts and not any(a.wa_business_id for a in accounts):
        frappe.msgprint(_("No active WhatsApp account has a Business Account ID (wa_business_id) — set it on the account (Meta → WhatsApp Manager → Business account id) or enable the number from Admin → Meta Business."), indicator="red")
        return "No WhatsApp Business Account ID on any account"
    if not accounts:
        frappe.msgprint(_("No active WhatsApp channel account."), indicator="red")
        return "No active WhatsApp account"

    biz_groups: dict[str, list[str]] = {}
    for acct in accounts:
        bid = acct.wa_business_id or ""
        if not bid:
            continue
        biz_groups.setdefault(bid, []).append(acct.name)

    errors: list[str] = []

    for biz_id, acct_names in biz_groups.items():
        representative = acct_names[0]
        account_doc = frappe.get_doc("Excom Channel Account", representative)
        creds = get_wa_credentials(account_doc)

        frappe.flags.integration_request = None  # never report a stale response from an earlier call
        try:
            response = _fetch_all_templates(creds, biz_id)
        except Exception as e:
            detail = _meta_error_from(e) or _extract_meta_error()
            if detail.startswith("Unknown error"):
                detail = frappe.utils.strip_html(str(e))[:300] or detail  # credentials / network / decrypt problems never reach Meta
            errors.append(f"{representative}: {detail}")
            continue

        for template in response.get("data") or []:
            if frappe.db.exists("WhatsApp Templates", {"actual_name": template["name"]}):
                doc = frappe.get_doc("WhatsApp Templates", {"actual_name": template["name"]})
            else:
                doc = frappe.new_doc("WhatsApp Templates")
                doc.template_name = template["name"]
                doc.actual_name = template["name"]

            doc.status = template["status"]
            doc.language_code = template["language"]
            doc.category = template["category"]
            doc.id = template["id"]
            _merge_template_linked_accounts(doc, acct_names)

            for component in template.get("components") or []:
                ctype = (component.get("type") or "").upper()

                if ctype == "HEADER":
                    fmt = (component.get("format") or "").upper()
                    doc.header_type = fmt
                    if fmt == "TEXT":
                        doc.header = component.get("text") or ""
                        doc.header_variable_samples = _extract_header_samples(
                            component.get("example") or {}
                        )
                    else:
                        doc.header_variable_samples = None

                elif ctype == "FOOTER":
                    doc.footer = component.get("text") or ""

                elif ctype == "BODY":
                    doc.template = component.get("text") or ""
                    if component.get("example"):
                        body_text = component["example"].get("body_text")
                        if body_text and isinstance(body_text, list) and body_text:
                            doc.body_variable_samples = json.dumps(body_text[0])
                            doc.sample_values = ""

                elif ctype == "BUTTONS":
                    doc.set("buttons", [])
                    frappe.db.delete(
                        "WhatsApp Button",
                        {"parent": doc.name, "parenttype": "WhatsApp Templates"},
                    )
                    type_map = {
                        "URL": "Visit Website",
                        "PHONE_NUMBER": "Call Phone",
                        "QUICK_REPLY": "Quick Reply",
                        "FLOW": "Flow",
                    }
                    for i, button in enumerate(component.get("buttons", []), start=1):
                        btn: dict = {
                            "button_type": type_map.get(button["type"], button["type"]),
                            "button_label": button.get("text"),
                            "sequence": i,
                        }
                        if button["type"] == "URL":
                            btn["website_url"] = button.get("url") or ""
                            btn["url_type"] = (
                                "Dynamic" if "{{" in btn["website_url"] else "Static"
                            )
                            if button.get("example"):
                                btn["example_url"] = ",".join(button["example"])
                        elif button["type"] == "PHONE_NUMBER":
                            btn["phone_number"] = button.get("phone_number")
                        elif button["type"] == "FLOW":
                            btn["flow"] = button.get("flow")

                        doc.append("buttons", btn)

            upsert_doc_without_hooks(doc, "WhatsApp Button", "buttons")
            _persist_linked_accounts(doc)

    if errors:
        frappe.msgprint(
            _("Some accounts failed: {0}").format("<br>".join(errors)),
            title=_("Partial Fetch"),
            indicator="orange",
        )
        return f"Fetched with {len(errors)} error(s)"

    return "Successfully fetched templates from Meta"

def _persist_linked_accounts(doc) -> None:
    """Insert linked_whatsapp_accounts rows that don't already exist in the DB."""
    for row in doc.get("linked_whatsapp_accounts") or []:
        acc = getattr(row, "channel_account", None) or (row.get("channel_account") if isinstance(row, dict) else None)
        if not acc:
            continue
        if frappe.db.exists(
            "WhatsApp Template Linked Account",
            {"parent": doc.name, "channel_account": acc},
        ):
            continue
        row.parent = doc.name
        row.parenttype = doc.doctype
        row.parentfield = "linked_whatsapp_accounts"
        row.db_insert()


def upsert_doc_without_hooks(doc, child_dt, child_field):
    """Insert or update a parent document and its children without hooks."""
    if frappe.db.exists(doc.doctype, doc.name):
        doc.db_update()
        frappe.db.delete(child_dt, {"parent": doc.name, "parenttype": doc.doctype})
    else:
        doc.db_insert()
    for d in doc.get(child_field):
        d.parent = doc.name
        d.parenttype = doc.doctype
        d.parentfield = child_field
        d.db_insert()
    frappe.db.commit()
