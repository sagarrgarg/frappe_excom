"""
Excom Sticker — manages sticker media for WhatsApp.

Handles validation (webp format, size limits), upload to Meta's media
API on insert, and cleanup on deletion.
"""

import os

import frappe
import requests as http_requests
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password

from excom.excom.utils.errors import ExcomProviderError

MAX_STATIC_KB = 100
MAX_ANIMATED_KB = 500


class ExcomSticker(Document):
    """Controller for Excom Sticker DocType."""

    def validate(self) -> None:
        """Validate webp format, dimensions, and file size limits."""
        self._validate_format()
        self._validate_dimensions()
        self._validate_size()

    def _validate_dimensions(self) -> None:
        """Require exactly 512x512 pixels (Meta's requirement for all stickers)."""
        file_path = self._get_file_path()
        if not file_path or not os.path.exists(file_path):
            return
        try:
            from PIL import Image as PilImage
            with PilImage.open(file_path) as img:
                w, h = img.size
                if w != 512 or h != 512:
                    frappe.throw(
                        _("Sticker must be exactly 512x512 pixels. "
                          "This file is {0}x{1}. Resize it before uploading.").format(w, h),
                        title=_("Invalid Dimensions"),
                    )
        except ImportError:
            pass  # Pillow not available — skip silently, Meta will reject if wrong

    def after_insert(self) -> None:
        """Upload sticker to Meta and store the media_id."""
        self._upload_to_meta()

    def on_update(self) -> None:
        """Re-upload if sticker_file changed."""
        if self.has_value_changed("sticker_file"):
            self._upload_to_meta()

    def _validate_format(self) -> None:
        if not self.sticker_file:
            return
        ext = os.path.splitext(self.sticker_file)[1].lower()
        if ext != ".webp":
            frappe.throw(
                _("Sticker must be a .webp file. Got: {0}").format(ext),
                title=_("Invalid Format"),
            )

    def _validate_size(self) -> None:
        if not self.sticker_file:
            return
        file_path = self._get_file_path()
        if not file_path or not os.path.exists(file_path):
            return
        size_kb = os.path.getsize(file_path) / 1024
        self.file_size_kb = round(size_kb, 1)

        limit = MAX_ANIMATED_KB if self.is_animated else MAX_STATIC_KB
        if size_kb > limit:
            frappe.throw(
                _("{0} sticker must be under {1} KB. This file is {2} KB.").format(
                    "Animated" if self.is_animated else "Static", limit, round(size_kb, 1)
                ),
                title=_("File Too Large"),
            )

    def _get_file_path(self) -> str:
        """Resolve Frappe file URL to absolute path on disk."""
        if not self.sticker_file:
            return ""
        if self.sticker_file.startswith("/files/") or self.sticker_file.startswith("/private/files/"):
            return frappe.get_site_path(
                "public" if self.sticker_file.startswith("/files/") else "",
                self.sticker_file.lstrip("/"),
            )
        return ""

    def _upload_to_meta(self) -> None:
        """Upload the sticker file to Meta's media endpoint and store the media_id."""
        file_path = self._get_file_path()
        if not file_path or not os.path.exists(file_path):
            frappe.msgprint(
                _("Cannot upload to Meta: sticker file not found on disk."),
                indicator="orange",
            )
            return

        account = frappe.get_doc("Excom Channel Account", self.whatsapp_account)
        token = None
        try:
            token = account.get_password("wa_token")
        except Exception:
            pass
        if not token:
            token = get_decrypted_password(
                account.doctype, account.name, "wa_token", raise_exception=False
            )
        if not token:
            frappe.throw(
                _("No access token for account {0}").format(account.name),
                title=_("Auth Error"),
            )

        base_url = getattr(account, "wa_url", None) or "https://graph.facebook.com"
        version = getattr(account, "wa_version", None) or "v21.0"
        phone_id = getattr(account, "wa_phone_id", None)
        if not phone_id:
            frappe.throw(
                _("No Phone Number ID for account {0}").format(account.name),
                title=_("Config Error"),
            )

        url = f"{base_url}/{version}/{phone_id}/media"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            with open(file_path, "rb") as f:
                resp = http_requests.post(
                    url,
                    headers=headers,
                    files={"file": ("sticker.webp", f, "image/webp")},
                    data={"messaging_product": "whatsapp", "type": "image/webp"},
                    timeout=(10, 60),
                )
        except http_requests.exceptions.RequestException as e:
            frappe.log_error(
                "Excom Sticker Upload Error",
                f"{self.name}: {e}",
            )
            frappe.msgprint(
                _("Failed to upload sticker to Meta: {0}").format(str(e)),
                indicator="red",
            )
            return

        if resp.ok:
            data = resp.json()
            media_id = data.get("id", "")
            if media_id:
                self.db_set("media_id", media_id, update_modified=False)
                frappe.msgprint(
                    _("Sticker uploaded to Meta. Media ID: {0}").format(media_id),
                    indicator="green",
                )
        else:
            try:
                err = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:
                err = resp.text[:300]
            frappe.log_error(
                "Excom Sticker Upload Error",
                f"HTTP {resp.status_code}: {err}",
            )
            frappe.msgprint(
                _("Meta rejected the sticker upload: {0}").format(err),
                indicator="red",
            )
