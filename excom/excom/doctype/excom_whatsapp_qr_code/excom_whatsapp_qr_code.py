"""A WhatsApp QR code / short link, mirrored from Meta's /{phone}/message_qrdls.

Creating, editing the message or deleting one here does the same on Meta first. Rows written by
the "sync from Meta" action set flags.from_meta and are not pushed back.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class ExcomWhatsAppQRCode(Document):
	def _account(self):
		if not self.channel_account:
			frappe.throw(_("Choose the WhatsApp number."))
		account = frappe.get_doc("Excom Channel Account", self.channel_account)
		if (account.channel or "").lower() != "whatsapp":
			frappe.throw(_("{0} is not a WhatsApp number.").format(self.channel_account))
		return account

	def validate(self):
		from excom.excom.services import whatsapp_account as wa

		self.prefilled_message = wa.validate_prefilled_message(self.prefilled_message)
		self.image_format = wa._qr_format(self.image_format)

	def before_save(self):
		# Meta is asked last, after our own checks (Frappe runs before_insert before validate)
		if not self.is_new() or self.flags.from_meta:
			return
		from excom.excom.services import whatsapp_account as wa

		account = self._account()
		wa.assert_qr_room(account.name)
		made = wa.create_qr_code(account, self.prefilled_message, self.image_format)
		self.code = made.get("code") or ""
		if not self.code:
			frappe.throw(_("Meta did not return a QR code."))
		self.prefilled_message = made.get("prefilled_message") or self.prefilled_message
		self.deep_link_url = made.get("deep_link_url") or ""
		self.qr_image_url = made.get("qr_image_url") or ""
		self.last_synced_on = now_datetime()

	def after_insert(self):
		if self.qr_image_url and not self.flags.from_meta:
			from excom.excom.services import whatsapp_account as wa

			wa.attach_qr_image(self)

	def on_update(self):
		if self.flags.from_meta or not self.code:
			return
		before = self.get_doc_before_save()
		if before and before.prefilled_message != self.prefilled_message:
			from excom.excom.services import whatsapp_account as wa

			wa.update_qr_code(self._account(), self.code, self.prefilled_message)
			self.db_set("last_synced_on", now_datetime(), update_modified=False)

	def on_trash(self):
		if self.flags.from_meta or not self.code:
			return
		from excom.excom.services import whatsapp_account as wa
		from excom.excom.services.whatsapp_errors import WhatsAppAPIError

		try:
			wa.delete_qr_code(self._account(), self.code)
		except WhatsAppAPIError as e:
			# already gone on Meta (deleted in WhatsApp Manager): let the local copy go too
			if str(e.code) != "100" or "exist" not in str(e).lower():
				raise
