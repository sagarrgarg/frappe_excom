"""Three user roles instead of two: Excom Admin, Excom Manager, Excom User.

Excom Manager used to gate both jobs at once — running people and desks, and running the system.
Splitting them means the config endpoints now want Excom Admin, so everybody who holds Excom
Manager today is granted Excom Admin as well: the split must not lock anybody out of a screen they
were using yesterday. Removing Admin from whoever should not keep it is a decision for a person,
not for a patch.
"""

import frappe


def execute():
	from excom.setup import seed_roles

	seed_roles()
	if not frappe.db.exists("Role", "Excom Admin"):
		return
	granted = []
	for user in frappe.get_all("Has Role", filters={"role": "Excom Manager", "parenttype": "User"}, pluck="parent"):
		if not frappe.db.exists("User", user):
			continue
		if frappe.db.exists("Has Role", {"parent": user, "role": "Excom Admin"}):
			continue
		doc = frappe.get_doc("User", user)
		doc.add_roles("Excom Admin")
		granted.append(user)
	frappe.db.commit()
	if granted:
		frappe.log_error(
			title="Excom: Excom Admin granted to existing managers",
			message="Review who should keep it:\n" + "\n".join(granted),
		)
