"""Alerts to people: an in-app notification and a realtime event. (Push and WhatsApp to the
owner's own number come with Phase 1H.)"""

from __future__ import annotations

import frappe


def alert(users, subject: str, message: str = "", doctype: str | None = None, name: str | None = None):
	sent = set()
	for user in users or []:
		if not user or user in sent or not frappe.db.get_value("User", user, "enabled"):
			continue
		sent.add(user)
		try:
			frappe.get_doc({
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": message,
				"document_type": doctype,
				"document_name": name,
			}).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title="Excom AI: alert failed", message=f"{user}: {subject}")
		frappe.publish_realtime("excom_ai_alert", {"subject": subject, "doctype": doctype, "name": name}, user=user)


def admins() -> list[str]:
	settings = frappe.get_cached_doc("Excom AI Settings")
	users = [r.user for r in settings.alert_users or []]
	if users:
		return users
	return [
		r.parent for r in frappe.get_all("Has Role", {"role": "Excom Admin", "parenttype": "User"}, ["parent"])
		if r.parent not in ("Administrator", "Guest")
	]
