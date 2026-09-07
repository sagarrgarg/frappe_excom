import frappe
from frappe.model.document import Document


class OmniIdentityLink(Document):
	pass


def on_doctype_update():
	# One ERP entity belongs to exactly one identity. The v1_0 patch enforce_unique_identity_links
	# added this index, but a fresh install marks every patch as completed without running it, so
	# new sites had no constraint at all. Adding it here covers both paths.
	if frappe.db.sql(
		"""SELECT 1 FROM information_schema.statistics
		   WHERE table_schema=DATABASE() AND table_name='tabOmni Identity Link' AND index_name='uniq_entity'"""
	):
		return
	try:
		frappe.db.sql_ddl(
			"ALTER TABLE `tabOmni Identity Link` ADD UNIQUE INDEX `uniq_entity` (`linked_doctype`, `linked_name`)"
		)
	except Exception:
		# An older site carrying duplicates must not have its migrate blocked; the patch cleans those up.
		frappe.log_error(title="Excom: could not add uniq_entity on Omni Identity Link")
