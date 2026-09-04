"""Role permissions for the CRM doctypes Excom drives.

Before this, "Excom User" carried no permission on Lead or Opportunity at all: every agent worked
through a blanket Sales Manager role, which grants read, write and delete on every lead in the
company. That makes team scoping meaningless, so the roles are given their own reach here and the
scope in services/crm_visibility.py does the narrowing.

Written as Custom DocPerm rows so ERPNext's own DocPerms stay untouched and a bench migrate of
erpnext cannot silently revert us. apply() is idempotent; revert() removes exactly what we added.
"""

import frappe

from excom.excom.services import crm_gateway as gw

AGENT = "Excom User"
MANAGER = "Excom Manager"
ADMIN = "Excom Admin"
# The owner's model puts a Sales Master Manager at the top: an unplaced lead is theirs to hand out.
# ERPNext ships that role with no permission on Lead or Opportunity whatsoever, so it gets one here.
SALES_HEAD = "Sales Master Manager"

# doctype -> {role: (read, write, create, delete)}
MATRIX: dict[str, dict[str, tuple[int, int, int, int]]] = {
	gw.LEAD: {AGENT: (1, 1, 1, 0), MANAGER: (1, 1, 1, 1), SALES_HEAD: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	gw.OPPORTUNITY: {AGENT: (1, 1, 1, 0), MANAGER: (1, 1, 1, 1), SALES_HEAD: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	gw.QUOTATION: {AGENT: (1, 0, 0, 0), MANAGER: (1, 1, 1, 0), ADMIN: (1, 1, 1, 0)},
	"Contact": {AGENT: (1, 1, 1, 0), MANAGER: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	"Address": {AGENT: (1, 1, 1, 0), MANAGER: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	# The party records an agent may consult but not edit. api/crm.py enforces the same split at
	# the field level (MANAGER_ONLY_EDIT); this is the same rule one layer down.
	gw.CUSTOMER: {AGENT: (1, 0, 0, 0), MANAGER: (1, 1, 0, 0), ADMIN: (1, 1, 0, 0)},
	"Supplier": {AGENT: (1, 0, 0, 0), MANAGER: (1, 1, 0, 0), ADMIN: (1, 1, 0, 0)},
	"Employee": {AGENT: (1, 0, 0, 0), MANAGER: (1, 0, 0, 0), ADMIN: (1, 0, 0, 0)},
	# Needed for the record pane: activity, notes and the assignment todos.
	"Communication": {AGENT: (1, 1, 1, 0), MANAGER: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	"ToDo": {AGENT: (1, 1, 1, 1), MANAGER: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
	# Every note in Excom — the internal note on a chat and the note on the Notes tab — is a Frappe
	# Comment, and stock Frappe lets only System Manager create one. Writing a note is the most
	# basic thing an agent does, and it worked only because the API bypassed permissions. Notes are
	# append-only on purpose: an agent adds their own and reads the team's, and nobody quietly
	# rewrites somebody else's note. A manager can correct one.
	"Comment": {AGENT: (1, 0, 1, 0), MANAGER: (1, 1, 1, 1), ADMIN: (1, 1, 1, 1)},
}


def _rows() -> list[tuple[str, str, tuple[int, int, int, int]]]:
	for doctype, roles in MATRIX.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for role, perms in roles.items():
			if frappe.db.exists("Role", role):
				yield doctype, role, perms


def apply() -> dict:
	"""Grant the matrix. Safe to run on every migrate."""
	from frappe.permissions import add_permission, update_permission_property

	touched = []
	for doctype, role, (r, w, c, d) in _rows():
		if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
			add_permission(doctype, role, 0)
			touched.append(f"{doctype}:{role}")
		for prop, value in (("read", r), ("write", w), ("create", c), ("delete", d), ("report", r), ("export", 0), ("share", w)):
			update_permission_property(doctype, role, 0, prop, value)
	frappe.clear_cache()
	return {"granted": touched}


def revert() -> dict:
	"""Remove every permission this module added. The escape hatch if the model is wrong."""
	removed = []
	for doctype, role, _ in _rows():
		for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
			removed.append(f"{doctype}:{role}")
	frappe.clear_cache()
	return {"removed": removed}
