import frappe


def execute():
	"""P3 N1: custom fields + attribution/stage seed rows (idempotent; also runs after every migrate)."""
	from excom.setup.crm_schema import apply
	apply()
