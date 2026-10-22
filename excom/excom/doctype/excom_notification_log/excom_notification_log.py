# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt

from frappe.utils import now_datetime
from frappe.model.document import Document


class ExcomNotificationLog(Document):
    """Tracks queued and processed Excom notifications."""
    @staticmethod
    def clear_old_logs(days: int = 90) -> None:
        """Log Settings: delete what is older than `days`."""
        from excom.utils.log_clearing import delete_older_than

        delete_older_than("Excom Notification Log", days)


    def validate(self):
        """Maintain lifecycle timestamps for pending entries."""
        if self.status == "Pending" and not self.pending_since:
            self.pending_since = now_datetime()
