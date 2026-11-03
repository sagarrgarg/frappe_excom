import frappe
from frappe import _
from frappe.model.document import Document


class ExcomSettings(Document):
	def validate(self) -> None:
		if self.auto_cleanup_enabled and (not self.cleanup_retention_days or self.cleanup_retention_days < 1):
			self.cleanup_retention_days = 1

		pub = (getattr(self, "mobile_public_site_url", None) or "").strip()
		if pub:
			pub = pub.rstrip("/")
			if not pub.startswith(("http://", "https://")):
				pub = "https://" + pub
			if " " in pub or "\n" in pub:
				frappe.throw(_("Mobile public site URL must be a single URL"))
			self.mobile_public_site_url = pub

		# WhatsApp calling: a TURN password pasted into the ICE servers JSON goes to the encrypted field.
		from excom.excom.channels.voice.providers.whatsapp import move_inline_credential

		move_inline_credential(self)

		if getattr(self, "push_notification_service", None) == "Excom Cloud":
			if not self.push_notification_server_url:
				frappe.throw(_("Please enter the Push Notification Server URL"))
			if not self.push_notification_api_key:
				frappe.throw(_("Please enter the Push Notification API Key"))
			if not self.push_notification_api_secret:
				frappe.throw(_("Please enter the Push Notification API Secret"))


@frappe.whitelist()
def get_branding() -> dict:
	"""Return branding settings for the React frontend."""
	settings = frappe.get_single("Excom Settings")
	result = {
		"logo_gradient_from": settings.logo_gradient_from or "#3b82f6",
		"logo_gradient_to": settings.logo_gradient_to or "#9333ea",
		"show_app_name": bool(settings.show_app_name),
		"app_name": "",
	}

	if result["show_app_name"]:
		ws_name = frappe.db.get_single_value("Website Settings", "app_name")
		sys_name = frappe.get_system_settings("app_name")
		name = (ws_name or sys_name or "").strip()
		if not name or name == "Frappe":
			name = "Excom"
		result["app_name"] = name

	return result
