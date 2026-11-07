"""Show the Excom logo on the v16 Desktop.

v16 copies an app's logo into its Desktop Icon when the icon is first created, and never again.
The old hook pointed at a file that did not exist, so sites either show no logo or carry a
hand-uploaded copy of an old Excom icon. Point those at the logo the app now ships. A logo
someone chose that is not an old Excom icon is left alone. v15 has no `logo_url` on Desktop Icon
(its apps screen reads the hook directly), so there is nothing to do there.
"""

import frappe

LOGO = "/assets/excom/images/excom-logo.svg"
OLD_LOGOS = {"", "/assets/excom/excom/excom-logo.svg"}
# file names of the old Excom icons, as people uploaded them to /files/
OLD_ICON_FILES = {
	"android-chrome-512x512.png",
	"android-chrome-192x192.png",
	"apple-touch-icon.png",
	"favicon-96x96.png",
	"favicon.svg",
}


def execute():
	if not frappe.db.table_exists("Desktop Icon") or not frappe.db.has_column("Desktop Icon", "logo_url"):
		return
	changed = False
	for icon in frappe.get_all("Desktop Icon", filters={"app": "excom", "icon_type": "App"}, fields=["name", "logo_url"]):
		url = (icon.logo_url or "").strip()
		if url in OLD_LOGOS or (url.startswith("/files/") and url.rsplit("/", 1)[-1] in OLD_ICON_FILES):
			frappe.db.set_value("Desktop Icon", icon.name, "logo_url", LOGO, update_modified=False)
			changed = True
	if changed:
		# the desktop grid and boot info are cached per user
		frappe.cache.delete_key("desktop_icons")
		frappe.cache.delete_key("bootinfo")
