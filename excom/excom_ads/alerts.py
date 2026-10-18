"""Ads alerts: one open record per problem, counted up on repeats, resolved when it clears.

    raise_alert("Token Problem", "Critical", title, message, ref=account_doc, dedupe_key="token:Meta-act_1")
    resolve_alert("token:Meta-act_1")          # the condition cleared by itself

Delivery follows Ads Notification Policy rows (company, type, minimum severity, channels, quiet
hours). With no matching policy, High and Critical alerts go to Excom Admin users on the Desk.
Recipients only ever hear about alerts inside their own scope (permissions.doc_allowed).
A repeat of an open alert notifies again only when its severity goes up.
"""

import frappe
from frappe.utils import get_datetime, get_time, now_datetime

SEVERITY_RANK = {"Info": 0, "Medium": 1, "High": 2, "Critical": 3}
SCOPE_FIELDS = ("company", "workspace", "brand", "account")


def raise_alert(alert_type: str, severity: str, title: str, message: str = "", ref=None, dedupe_key: str | None = None, scope: dict | None = None) -> str:
	"""Create or refresh an alert. Never raises: an alert must not break the job that noticed the problem."""
	try:
		return _raise(alert_type, severity, title, message, ref, dedupe_key, scope)
	except Exception:
		frappe.log_error(title=f"Excom Ads: could not raise alert {alert_type}", message=frappe.get_traceback())
		return ""


def _raise(alert_type, severity, title, message, ref, dedupe_key, scope):
	now = now_datetime()
	values = {"alert_type": alert_type, "severity": severity, "title": title[:140], "message": (message or "")[:2000], "last_seen": now}
	values.update(_scope_of(ref, scope))
	if ref is not None:
		values["reference_doctype"], values["reference_name"] = ref.doctype, ref.name
	key = dedupe_key or f"{alert_type}:{values.get('reference_doctype')}:{values.get('reference_name')}"
	existing = frappe.db.get_value("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}, ["name", "severity", "occurrences", "notified_severity"], as_dict=True)
	if existing:
		values["occurrences"] = (existing.occurrences or 1) + 1
		# Never downgrade an open alert: the worst seen is what a person needs to know.
		if SEVERITY_RANK[severity] < SEVERITY_RANK.get(existing.severity, 0):
			values["severity"] = existing.severity
		frappe.db.set_value("Ads Alert", existing.name, values, update_modified=True)
		name = existing.name
		escalated = SEVERITY_RANK[values["severity"]] > SEVERITY_RANK.get(existing.notified_severity or "Info", -1)
		if escalated or not existing.notified_severity:
			_notify(name)
		return name
	doc = frappe.get_doc({"doctype": "Ads Alert", **values, "dedupe_key": key, "first_seen": now, "occurrences": 1, "status": "Open"})
	doc.insert(ignore_permissions=True)
	_notify(doc.name)
	return doc.name


def resolve_alert(dedupe_key: str, resolution: str = "Cleared automatically") -> int:
	names = frappe.get_all("Ads Alert", filters={"dedupe_key": dedupe_key, "status": ["!=", "Resolved"]}, pluck="name")
	for n in names:
		frappe.db.set_value("Ads Alert", n, {"status": "Resolved", "resolved_by": "system", "resolved_at": now_datetime(), "resolution": resolution})
	return len(names)


def _scope_of(ref, scope) -> dict:
	out = {}
	if ref is not None:
		for f in SCOPE_FIELDS:
			if ref.get(f):
				out[f] = ref.get(f)
		if ref.doctype == "Ads Account":
			out["account"] = ref.name
		if ref.doctype == "Ads Workspace":
			out["workspace"] = ref.name
	if scope:
		out.update({k: v for k, v in scope.items() if k in SCOPE_FIELDS and v})
	if out.get("account") and not out.get("company"):
		acc = frappe.db.get_value("Ads Account", out["account"], ["company", "workspace", "brand"], as_dict=True) or {}
		for f in ("company", "workspace", "brand"):
			out.setdefault(f, acc.get(f))
	return {k: v for k, v in out.items() if v}


# ─── delivery ─────────────────────────────────────────────────────────────────

def _notify(name: str) -> None:
	# After commit, so a rolled-back job never sends a notification about something that did not happen.
	# Tests deliver explicitly; queuing there only piles up jobs no worker will run.
	if frappe.flags.in_test:
		return
	frappe.enqueue("excom.excom_ads.alerts.deliver", queue="short", name=name, enqueue_after_commit=True, job_id=f"excom_ads_alert::{frappe.local.site}::{name}", deduplicate=True)


def deliver(name: str) -> dict:
	alert = frappe.get_doc("Ads Alert", name)
	if alert.status in ("Resolved", "Snoozed") and not (alert.status == "Snoozed" and alert.snoozed_until and get_datetime(alert.snoozed_until) < now_datetime()):
		return {"sent": 0}
	sent = {"desk": 0, "email": 0, "whatsapp": 0}
	for policy in _policies(alert):
		if _quiet(policy) and alert.severity != "Critical":
			continue
		users = _recipients(policy, alert)
		if policy.get("via_desk"):
			sent["desk"] += _desk(alert, users)
		if policy.get("via_email"):
			sent["email"] += _email(alert, users)
		if policy.get("via_whatsapp"):
			sent["whatsapp"] += _whatsapp(alert, users)
	frappe.db.set_value("Ads Alert", name, {"notified_at": now_datetime(), "notified_severity": alert.severity}, update_modified=False)
	return sent


def _policies(alert) -> list:
	rows = frappe.get_all("Ads Notification Policy", filters={"enabled": 1}, fields=["name", "company", "alert_type", "min_severity", "via_desk", "via_email", "via_whatsapp", "quiet_from", "quiet_to"])
	rank = SEVERITY_RANK[alert.severity]
	match = [r for r in rows if (not r.company or r.company == alert.company) and (not r.alert_type or r.alert_type == alert.alert_type) and rank >= SEVERITY_RANK.get(r.min_severity or "High", 2)]
	if match or rows:
		return match
	# No policy configured at all: admins hear about High and Critical on the Desk.
	if rank >= SEVERITY_RANK["High"]:
		return [frappe._dict(name=None, via_desk=1, roles=["Excom Admin", "System Manager"])]
	return []


def _quiet(policy) -> bool:
	if not policy.get("quiet_from") or not policy.get("quiet_to"):
		return False
	now = now_datetime().time()
	a, b = get_time(policy.quiet_from), get_time(policy.quiet_to)
	return a <= now < b if a <= b else (now >= a or now < b)


def _recipients(policy, alert) -> list[str]:
	from excom.excom_ads.permissions import doc_allowed

	users = set()
	roles = list(policy.get("roles") or [])
	if policy.get("name"):
		for r in frappe.get_all("Ads Notification Recipient", filters={"parent": policy.name, "parenttype": "Ads Notification Policy"}, fields=["recipient_type", "role", "user"]):
			if r.recipient_type == "User" and r.user:
				users.add(r.user)
			elif r.role:
				roles.append(r.role)
	if roles:
		users |= set(frappe.get_all("Has Role", filters={"role": ["in", roles], "parenttype": "User"}, pluck="parent"))
	enabled = set(frappe.get_all("User", filters={"name": ["in", list(users)], "enabled": 1, "user_type": "System User"}, pluck="name")) if users else set()
	enabled.discard("Guest")
	return sorted(u for u in enabled if doc_allowed(alert, u))


def _desk(alert, users) -> int:
	for u in users:
		frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert", "document_type": "Ads Alert", "document_name": alert.name, "subject": f"[{alert.severity}] {alert.title}", "email_content": alert.message or ""}).insert(ignore_permissions=True)
	return len(users)


def _email(alert, users) -> int:
	emails = [e for e in frappe.get_all("User", filters={"name": ["in", users]}, pluck="email") if e]
	if not emails:
		return 0
	frappe.sendmail(recipients=emails, subject=f"[{alert.severity}] {alert.title}", message=f"<p>{frappe.utils.escape_html(alert.message or alert.title)}</p><p><a href='{frappe.utils.get_url_to_form('Ads Alert', alert.name)}'>Open the alert</a></p>", reference_doctype="Ads Alert", reference_name=alert.name)
	return len(emails)


def _whatsapp(alert, users) -> int:
	"""Through Excom's consent-checked transactional sender, with the approved template set in Ads
	Settings (variables: severity, title, detail). Holds no WhatsApp credentials itself."""
	from excom.excom_ads.settings import get_settings

	template = get_settings().alert_whatsapp_template
	if not template:
		return 0
	from excom.excom.services import outbound

	sent = 0
	for u in users:
		phone = frappe.db.get_value("User", u, "mobile_no")
		if not phone:
			continue
		r = outbound.send_transactional(purpose="ads_alert", phone=phone, channel="whatsapp", whatsapp_template=template, whatsapp_variables=[alert.severity, alert.title[:60], (alert.message or "-")[:500]], reference=("Ads Alert", alert.name))
		sent += 1 if (r or {}).get("ok") else 0
	return sent
