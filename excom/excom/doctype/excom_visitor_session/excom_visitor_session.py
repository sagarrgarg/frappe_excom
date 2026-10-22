import frappe
from frappe.model.document import Document


class ExcomVisitorSession(Document):
    @staticmethod
    def clear_old_logs(days: int = 90) -> None:
        """Log Settings: delete what is older than `days`."""
        from excom.utils.log_clearing import delete_older_than

        delete_older_than("Excom Visitor Session", days)

