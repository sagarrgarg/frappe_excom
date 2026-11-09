"""One unit of agent work in the queue"""

import frappe
from frappe.model.document import Document

OPEN = ("Queued", "Running", "Held")


class ExcomAITask(Document):
	pass


def on_doctype_update():
	# Every tick: due tasks by status and time, an agent's queue, and running tasks.
	frappe.db.add_index("Excom AI Task", ["status", "not_before"])
	frappe.db.add_index("Excom AI Task", ["agent", "status"])
	frappe.db.add_index("Excom AI Task", ["agent", "dedupe_key"])
	ensure_open_key()


def ensure_open_key():
	"""One open task per (agent, dedupe key), held by the database itself.

	`open_key` is a stored generated column, agent:dedupe_key while the task is Queued, Running or
	Held and NULL otherwise, with a unique index. Whatever code moves a task's status, the key
	follows, and two workers adding the same task at once cannot both succeed (a check in code
	cannot do that under MariaDB's snapshot reads). Frappe never writes it: it is not a field.
	"""
	if "open_key" in frappe.db.get_table_columns("Excom AI Task"):
		return
	# Older duplicates (before the key existed): keep the oldest open one of each key.
	frappe.db.sql("""UPDATE `tabExcom AI Task` t JOIN (
			SELECT agent, dedupe_key, MIN(creation) first FROM `tabExcom AI Task`
			WHERE status IN ('Queued', 'Running', 'Held') AND IFNULL(dedupe_key, '') != ''
			GROUP BY agent, dedupe_key HAVING COUNT(*) > 1) d
		ON d.agent = t.agent AND d.dedupe_key = t.dedupe_key
		SET t.status = 'Skipped', t.outcome = 'Duplicate of an open task with the same key'
		WHERE t.status IN ('Queued', 'Held') AND t.creation > d.first""")
	frappe.db.sql_ddl("""ALTER TABLE `tabExcom AI Task`
		ADD COLUMN `open_key` VARCHAR(280) AS (IF(status IN ('Queued', 'Running', 'Held') AND IFNULL(dedupe_key, '') != '',
			CONCAT(agent, ':', dedupe_key), NULL)) PERSISTENT,
		ADD UNIQUE INDEX `open_key` (`open_key`)""")
