"""One unit of agent work in the queue"""

import frappe
from frappe.model.document import Document


class ExcomAITask(Document):
	pass


def on_doctype_update():
	# Every tick: due tasks by status and time, an agent's queue, and running tasks.
	frappe.db.add_index("Excom AI Task", ["status", "not_before"])
	frappe.db.add_index("Excom AI Task", ["agent", "status"])
	frappe.db.add_index("Excom AI Task", ["agent", "dedupe_key"])
