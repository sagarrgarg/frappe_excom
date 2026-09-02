app_name = "excom"
app_title = "Excom"
app_publisher = "Sagar Ratan Garg"
app_description = "An omnichannel communication engine that unifies, automates, and governs every external conversation."
app_email = "sagar1ratan1garg11@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext"]

add_to_apps_screen = [
	{
		"name": "excom",
		"logo": "/assets/excom/excom/excom-logo.svg",
		"title": "Excom",
		"route": "/excom",
	}
]

website_route_rules = [
	{"from_route": "/excom/<path:app_path>", "to_route": "excom"},
]

# Includes in <head>
# ------------------

app_include_js = ["/assets/excom/js/excom_navbar.js", "/assets/excom/js/excom_voice_desk.js"]

# include js, css files in header of web template
# web_include_css = "/assets/excom/css/excom.css"
# web_include_js = "/assets/excom/js/excom.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "excom/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "excom/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "excom.utils.jinja_methods",
# 	"filters": "excom.utils.jinja_filters"
# }

# Installation
# ------------

after_install = "excom.setup.after_install"
after_migrate = ["excom.setup.after_migrate"]

boot_session = "excom.boot.boot_session"

# Uninstallation
# ------------

# before_uninstall = "excom.uninstall.before_uninstall"
# after_uninstall = "excom.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "excom.utils.before_app_install"
# after_app_install = "excom.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "excom.utils.before_app_uninstall"
# after_app_uninstall = "excom.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "excom.notifications.get_notification_config"

# Permissions
# -----------

permission_query_conditions = {
	"Excom Thread": "excom.excom.doctype.excom_thread.excom_thread.get_permission_query_conditions",
}

has_permission = {
	"Excom Thread": "excom.excom.doctype.excom_thread.excom_thread.has_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Triggers Excom Notification automation for every DocType event.

doc_events = {
	"*": {
		"validate": "excom.excom.utils.run_server_script_for_doc_event",
		"on_update": [
			"excom.excom.utils.run_server_script_for_doc_event",
			"excom.excom.services.identity_hooks.on_doc_event_for_rules",
		],
		"after_insert": [
			"excom.excom.utils.run_server_script_for_doc_event",
			"excom.excom.services.identity_hooks.on_doc_event_for_rules",
		],
		"on_submit": [
			"excom.excom.utils.run_server_script_for_doc_event",
			"excom.excom.services.identity_hooks.on_doc_event_for_rules",
		],
		"on_cancel": "excom.excom.utils.run_server_script_for_doc_event",
		"on_trash": "excom.excom.utils.run_server_script_for_doc_event",
	},
	"Customer": {
		"after_insert": "excom.excom.services.identity_hooks.on_entity_created",
		"on_update": "excom.excom.services.identity_hooks.on_customer_updated",
	},
	"Supplier": {
		"after_insert": "excom.excom.services.identity_hooks.on_entity_created",
	},
	"Lead": {
		"after_insert": "excom.excom.services.identity_hooks.on_entity_created",
	},
	"Contact": {
		"after_insert": "excom.excom.services.identity_hooks.on_entity_created",
	},
	"Party Link": {
		"after_insert": "excom.excom.services.identity_hooks.on_party_link_created",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	# Runs every minute: process delayed notification log queue.
	"all": [
		"excom.excom.utils.process_pending_whatsapp_notification_logs",
		"excom.excom.utils.trigger_whatsapp_notifications_all",
		"excom.excom.channels.email.inbound.poll_all_email_accounts",
		"excom.excom.services.broadcast_schedule.process_due_scheduled_broadcasts",
		"excom.excom.services.delivery_watchdog.check_stale_messages",
		"excom.excom.channels.voice.reconcile.reconcile_pending_calls",
	],
	"hourly": [
		"excom.excom.utils.trigger_whatsapp_notifications_hourly",
		"excom.excom.utils.trigger_whatsapp_notifications_hourly_long",
	],
	"daily": [
		"excom.excom.utils.trigger_whatsapp_notifications_daily",
		"excom.excom.utils.trigger_whatsapp_notifications_daily_long",
		"excom.excom.tasks.cleanup.cleanup_stale_identities",
		"excom.excom.services.identity_hooks.scan_merge_suggestions",
		"excom.excom.tasks.token_monitor.check_token_expiry",
	],
	"daily_maintenance": [
		"excom.excom.scheduler.daily.sync_invalid_tokens",
	],
	"weekly": [
		"excom.excom.utils.trigger_whatsapp_notifications_weekly",
		"excom.excom.utils.trigger_whatsapp_notifications_weekly_long",
	],
	"monthly": [
		"excom.excom.utils.trigger_whatsapp_notifications_monthly",
		"excom.excom.utils.trigger_whatsapp_notifications_monthly_long",
	],
}

# Testing
# -------

# before_tests = "excom.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "excom.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "excom.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["excom.utils.before_request"]
after_request = ["excom.excom.middleware.add_webchat_cors_headers"]

# Job Events
# ----------
# before_job = ["excom.utils.before_job"]
# after_job = ["excom.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"excom.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

