"""Two roles instead of three: Excom Admin and Excom Agent.

Excom User is renamed to Excom Agent, and the middle tier — Excom Manager — is removed. Every check
that used to ask for Excom Manager now asks for Excom Admin, because running people and desks and
running the system are the same job at this size.

Nobody is promoted by this patch: a person holding Excom Manager today becomes an Excom Agent, and
somebody has to hand out Excom Admin deliberately. A patch that silently handed the WhatsApp tokens
and the Meta connection to every desk head would be the wrong default. The demoted names go to the
error log so they can be reviewed and re-granted.

The role is moved by rewriting the `Has Role` row rather than by User.add_roles(): a user attached
to a Role Profile has their role list rebuilt from that profile on every save, so adding a role to
the user document silently loses it. Role Profiles carry `Has Role` rows of their own, and they are
rewritten here too — miss them and everybody in the profile loses Excom the moment the old role is
deleted. Rows on a Workspace, Report or Page are deleted instead: those belong to the JSON that
ships with the app, and the migrate that runs this patch re-creates them.
"""

import frappe

OLD_AGENT = "Excom User"
OLD_MANAGER = "Excom Manager"
AGENT = "Excom Agent"
# Where a role row means "a person holds this role" rather than "this screen wants this role".
PEOPLE_PARENTS = ("User", "Role Profile")


def execute():
	from excom.setup import seed_roles

	seed_roles()

	demoted = frappe.db.sql_list(
		"""SELECT DISTINCT parent FROM `tabHas Role` WHERE role = %s AND parenttype = 'User'""",
		OLD_MANAGER,
	)

	for old in (OLD_AGENT, OLD_MANAGER):
		frappe.db.sql(
			"""UPDATE `tabHas Role` SET role = %(new)s
			   WHERE role = %(old)s AND parenttype IN %(parents)s""",
			{"new": AGENT, "old": old, "parents": PEOPLE_PARENTS},
		)
		# Anything else naming the old role is a screen definition, not a person.
		frappe.db.sql(
			"""DELETE FROM `tabHas Role` WHERE role = %(old)s AND parenttype NOT IN %(parents)s""",
			{"old": old, "parents": PEOPLE_PARENTS},
		)

	# Somebody who held both old roles now holds Excom Agent twice.
	frappe.db.sql(
		"""DELETE h FROM `tabHas Role` h
		   JOIN `tabHas Role` keep
		     ON keep.parent = h.parent AND keep.parenttype = h.parenttype
		    AND keep.role = h.role AND keep.name < h.name
		   WHERE h.role = %s""",
		AGENT,
	)

	# A user attached to a Role Profile has their own role rows rebuilt from that profile whenever
	# the user document is saved, so the profile is the source of truth for them. Give anybody whose
	# profile now carries Excom Agent the row on their own record too, or they hold the role only in
	# theory until somebody happens to save their user.
	profiles = frappe.db.sql_list(
		"""SELECT DISTINCT parent FROM `tabHas Role` WHERE parenttype = 'Role Profile' AND role = %s""",
		AGENT,
	)
	if profiles:
		frappe.db.sql(
			"""INSERT INTO `tabHas Role` (name, parent, parenttype, parentfield, role, creation, modified, owner, modified_by)
			   SELECT SUBSTRING(MD5(CONCAT(u.name, %(role)s)), 1, 10), u.name, 'User', 'roles', %(role)s, NOW(), NOW(), 'Administrator', 'Administrator'
			   FROM `tabUser` u
			   WHERE u.role_profile_name IN %(profiles)s
			     AND NOT EXISTS (
			         SELECT 1 FROM `tabHas Role` h
			         WHERE h.parent = u.name AND h.parenttype = 'User' AND h.role = %(role)s
			     )""",
			{"role": AGENT, "profiles": profiles},
		)

	# Permission rows that named the old roles: the doctype JSONs are reloaded by this migrate, but
	# the Custom DocPerm rows setup/crm_permissions.py wrote on Lead, Opportunity and the rest are
	# data, and nothing else would ever remove them.
	for old in (OLD_AGENT, OLD_MANAGER):
		for name in frappe.get_all("Custom DocPerm", filters={"role": old}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
		frappe.db.delete("DocPerm", {"role": old})

	from excom.setup.crm_permissions import apply as apply_crm_permissions

	apply_crm_permissions()

	for old in (OLD_AGENT, OLD_MANAGER):
		if frappe.db.exists("Role", old):
			frappe.delete_doc("Role", old, force=1, ignore_permissions=True)

	# The desk workspace carried a role row of its own; deleting it leaves the sidebar visible to
	# System Manager alone until the shipped definition is read again.
	frappe.reload_doc("excom", "workspace", "excom", force=True)

	frappe.db.commit()
	frappe.clear_cache()

	if demoted:
		frappe.log_error(
			title="Excom: former managers are now agents",
			message=(
				"The Excom Manager tier was removed. These people are Excom Agents now and hold no "
				"admin rights until somebody grants them Excom Admin:\n" + "\n".join(demoted)
			),
		)
	agents = frappe.db.count("Has Role", {"role": AGENT, "parenttype": "User"})
	print(f"excom roles collapsed: {agents} users hold Excom Agent, {len(demoted)} former managers demoted")
