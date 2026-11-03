from frappe.model.document import Document


class ExcomWhatsAppEvent(Document):
	@staticmethod
	def clear_old_logs(days: int = 365) -> None:
		"""Log Settings: delete what is older than `days`."""
		from excom.utils.log_clearing import delete_older_than

		delete_older_than("Excom WhatsApp Event", days)
