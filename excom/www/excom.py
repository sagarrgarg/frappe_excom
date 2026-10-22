import json
import re

import frappe
import frappe.sessions
from frappe import _

no_cache = 1

SCRIPT_TAG_PATTERN = re.compile(r"\<script[^<]*\</script\>")
CLOSING_SCRIPT_TAG_PATTERN = re.compile(r"</script\>")


# Excom Marketing opens the app for the Marketing screens only: the frontend shows nothing else to
# it, and the inbox, CRM and broadcast APIs refuse it (they require Excom Agent / Excom Admin).
APP_ROLES = {"System Manager", "Excom Admin", "Excom Agent", "Excom Marketing"}


def check_app_access() -> None:
    if not APP_ROLES.intersection(frappe.get_roles(frappe.session.user)):
        frappe.throw(
            _("You do not have access to Excom. Contact your administrator to get the Excom Agent, Excom Admin or Excom Marketing role."),
            frappe.PermissionError,
        )


def get_context(context):
    csrf_token = frappe.sessions.get_csrf_token()
    frappe.db.commit()  # nosemgrep

    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/excom"
        raise frappe.Redirect

    check_app_access()

    try:
        boot = frappe.sessions.get()
    except Exception as e:
        raise frappe.SessionBootFailed from e

    # Web push: frappe-push-notification.js reads this for the v15 relay (Raven www parity).
    boot["push_relay_server_url"] = frappe.conf.get("push_relay_server_url")

    if "server_script_enabled" in frappe.conf:
        boot["server_script_enabled"] = frappe.conf.server_script_enabled
    else:
        boot["server_script_enabled"] = True

    boot_json = frappe.as_json(boot, indent=None, separators=(",", ":"))
    boot_json = SCRIPT_TAG_PATTERN.sub("", boot_json)
    boot_json = CLOSING_SCRIPT_TAG_PATTERN.sub("", boot_json)
    boot_json = json.dumps(boot_json)

    context.update(
        {
            "build_version": frappe.utils.get_build_version(),
            "boot": boot_json,
            "csrf_token": csrf_token,
        }
    )

    app_name = frappe.get_website_settings("app_name") or frappe.get_system_settings("app_name")

    if app_name and app_name != "Frappe":
        context["app_name"] = app_name + " | Excom"
    else:
        context["app_name"] = "Excom"

    context["sitename"] = boot.get("sitename")
    context["system_timezone"] = frappe.utils.get_system_timezone()

    favicon = frappe.get_website_settings("favicon")
    context["icon_96"] = favicon or "/assets/excom/excom/manifest/favicon-96x96.png"
    context["apple_touch_icon"] = favicon or "/assets/excom/excom/manifest/apple-touch-icon.png"
    context["favicon_svg"] = favicon or "/assets/excom/excom/manifest/favicon.svg"

    return context


@frappe.whitelist(methods=["POST"], allow_guest=True)
def get_context_for_dev():
    if frappe.session.user == "Guest":
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not frappe.conf.developer_mode:
        frappe.throw(_("This method is only meant for developer mode"))
    return json.loads(get_boot())


def get_boot():
    try:
        boot = frappe.sessions.get()
    except Exception as e:
        raise frappe.SessionBootFailed from e

    boot["push_relay_server_url"] = frappe.conf.get("push_relay_server_url")
    if "server_script_enabled" in frappe.conf:
        boot["server_script_enabled"] = frappe.conf.server_script_enabled
    else:
        boot["server_script_enabled"] = True

    boot_json = frappe.as_json(boot, indent=None, separators=(",", ":"))
    boot_json = SCRIPT_TAG_PATTERN.sub("", boot_json)
    boot_json = CLOSING_SCRIPT_TAG_PATTERN.sub("", boot_json)
    boot_json = json.dumps(boot_json)

    return boot_json
