import frappe
from frappe.model.document import Document


class ExcomMessage(Document):
	def before_insert(self):
		self.check_idempotency()

	def check_idempotency(self):
		"""Skip duplicate inbound messages based on provider_message_id."""
		if self.provider_message_id and self.direction == "Inbound":
			existing = frappe.db.exists(
				"Excom Message",
				{"provider_message_id": self.provider_message_id},
			)
			if existing:
				frappe.throw(
					f"Duplicate message: {self.provider_message_id}",
					frappe.DuplicateEntryError,
				)


def on_doctype_update():
	# Inbound idempotency: ingest_inbound_message checks provider_message_id before inserting, but two
	# concurrent webhook retries can both pass that check — the database has to refuse the second row.
	# (Messages with no provider id store NULL, and MySQL allows many NULLs in a unique index.)
	if not frappe.db.sql("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='tabExcom Message' AND index_name='unique_provider_message_id'"):
		if frappe.db.sql("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='tabExcom Message' AND index_name='provider_message_id_index'"):
			frappe.db.sql_ddl("ALTER TABLE `tabExcom Message` DROP INDEX `provider_message_id_index`")
		frappe.db.add_unique("Excom Message", ["provider_message_id"], constraint_name="unique_provider_message_id")
	frappe.db.add_index("Excom Message", ["thread", "creation"])
	# the two per-minute schedulers scan these
	frappe.db.add_index("Excom Message", ["delivery_status", "scheduled_at"])
