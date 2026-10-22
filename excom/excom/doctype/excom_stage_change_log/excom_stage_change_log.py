from frappe.model.document import Document


class ExcomStageChangeLog(Document):
	@staticmethod
	def clear_old_logs(days: int = 90) -> None:
		"""Log Settings: delete what is older than `days`."""
		from excom.utils.log_clearing import delete_older_than

		delete_older_than("Excom Stage Change Log", days)

