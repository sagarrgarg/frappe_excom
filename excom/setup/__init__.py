"""
Post-install and post-migrate setup for Excom.

Ensures all required seed data (channels, etc.) exists in the database.
Runs after every install and every migrate so channel records are never missing.
"""

import frappe


CHANNELS = [
	{
		"name": "whatsapp",
		"channel_label": "WhatsApp",
		"allows_multiple_accounts": 1,
		"is_enabled": 1,
		"description": "WhatsApp Business API channel.",
	},
	{
		"name": "email",
		"channel_label": "Email",
		"allows_multiple_accounts": 1,
		"is_enabled": 1,
		"description": "Email channel integrated via Gmail API (OAuth2). Bodies stored in Gmail, only metadata synced.",
	},
	{
		"name": "instagram",
		"channel_label": "Instagram",
		"allows_multiple_accounts": 1,
		"is_enabled": 1,
		"description": "Instagram DMs via the Graph API (polled every minute; webhook as accelerator).",
	},
	{
		"name": "messenger",
		"channel_label": "Messenger",
		"allows_multiple_accounts": 1,
		"is_enabled": 1,
		"description": "Facebook Messenger via the Graph API (polled every minute; webhook as accelerator).",
	},
	{
		"name": "webchat",
		"channel_label": "Web Chat",
		"allows_multiple_accounts": 1,
		"is_enabled": 1,
		"description": "Embeddable web chat widget for website visitors.",
	},
]


ROLES = [
	{
		"role_name": "Excom Admin",
		"desk_access": 1,
		"search_bar": 1,
		"notifications": 1,
	},
	{
		"role_name": "Excom Manager",
		"desk_access": 1,
		"search_bar": 1,
		"notifications": 1,
	},
	{
		"role_name": "Excom User",
		"desk_access": 1,
		"search_bar": 1,
		"notifications": 1,
	},
]


GENERAL_TEAM_NAME = "General"


def after_install():
	"""Called once after bench install-app excom."""
	seed_roles()
	seed_channels()
	seed_general_team()
	from excom.setup.crm_schema import apply as apply_crm_schema
	apply_crm_schema()
	from excom.setup.crm_permissions import apply as apply_crm_permissions
	apply_crm_permissions()


SHARED_DOCTYPES = [
	# Also shipped by frappe_whatsapp. Whichever app migrates last wins, and excom's copies carry ten
	# load-bearing fields the other does not (linked accounts, variable samples, review timestamps).
	# Reloading them at the end of every migrate makes excom's definition the one that survives.
	("excom", "doctype", "whatsapp_templates"),
	("excom", "doctype", "whatsapp_button"),
	("excom", "doctype", "whatsapp_template_linked_account"),
	("excom", "doctype", "whatsapp_flow"),
	("excom", "doctype", "whatsapp_flow_field"),
	("excom", "doctype", "whatsapp_flow_screen"),
]

REQUIRED_SHARED_FIELDS = {
	"WhatsApp Templates": ["linked_whatsapp_accounts", "header_variable_samples", "body_variable_samples", "approved_at", "submitted_at", "rejected_at", "paused_at", "rejection_reason"],
}


def reclaim_shared_doctypes() -> list:
	"""Re-apply excom's definition of the doctypes another installed app also ships, then report anything
	still missing so a broken migrate order is visible instead of silent data loss."""
	for app, kind, name in SHARED_DOCTYPES:
		try:
			frappe.reload_doc(app, kind, name, force=True)
		except Exception:
			frappe.log_error(title=f"Excom: could not reclaim {name}", message=frappe.get_traceback())
	missing = []
	for doctype, fields in REQUIRED_SHARED_FIELDS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		meta = frappe.get_meta(doctype)
		missing += [f"{doctype}.{f}" for f in fields if not meta.has_field(f)]
	if missing:
		frappe.log_error(title="Excom: shared doctype fields missing after migrate", message=", ".join(missing))
	return missing


def after_migrate():
	"""Called after every bench migrate. Ensures seed data is intact."""
	from excom.setup.crm_schema import apply as apply_crm_schema
	apply_crm_schema()
	seed_roles()
	from excom.setup.crm_permissions import apply as apply_crm_permissions
	apply_crm_permissions()
	seed_channels()
	seed_general_team()
	reclaim_shared_doctypes()

def seed_roles():
	"""Create Excom Manager and Excom User roles if they don't exist."""
	for role_def in ROLES:
		if frappe.db.exists("Role", role_def["role_name"]):
			continue
		doc = frappe.get_doc({
			"doctype": "Role",
			"role_name": role_def["role_name"],
			"desk_access": role_def.get("desk_access", 1),
			"search_bar": role_def.get("search_bar", 1),
			"notifications": role_def.get("notifications", 1),
		})
		doc.insert(ignore_permissions=True)
	frappe.db.commit()


def seed_general_team():
	"""Create the permanent General team if it doesn't exist."""
	if frappe.db.exists("Excom Team", GENERAL_TEAM_NAME):
		return

	doc = frappe.get_doc({
		"doctype": "Excom Team",
		"team_name": GENERAL_TEAM_NAME,
		"description": "Default team for unassigned conversations. Members can view and claim threads from the General inbox.",
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def seed_channels():
	"""Create any missing Excom Channel records.

	Temporarily sets frappe.flags.in_migrate so the Excom Channel
	controller allows the programmatic insert (it blocks UI creation).
	"""
	was_in_migrate = frappe.flags.in_migrate
	frappe.flags.in_migrate = True

	try:
		for ch in CHANNELS:
			if frappe.db.exists("Excom Channel", ch["name"]):
				continue

			doc = frappe.get_doc({
				"doctype": "Excom Channel",
				"__newname": ch["name"],
				"channel_label": ch["channel_label"],
				"allows_multiple_accounts": ch["allows_multiple_accounts"],
				"is_enabled": ch["is_enabled"],
				"description": ch["description"],
			})
			doc.insert(ignore_permissions=True)

		frappe.db.commit()
	finally:
		frappe.flags.in_migrate = was_in_migrate
