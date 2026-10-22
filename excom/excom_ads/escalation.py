"""Alert escalation (plan §22): an alert nobody has picked up goes, once, to the next people.

An Ads Notification Policy with `escalate_after_minutes` > 0 and an "Escalate To" table escalates
every alert it matches (same matching as delivery: company, type, minimum severity) that is still
**Open** — not acknowledged, snoozed or resolved — that many minutes after it was notified
(`notified_at`, else `first_seen`). Escalation:

  * happens once per alert: `escalated` is claimed with a conditional UPDATE, so two workers (or a
    retried job) never both send;
  * uses the policy's own channels (Desk / email / WhatsApp) through alerts.py's senders;
  * respects quiet hours (Critical alerts ignore them, as in delivery); a quiet policy simply waits
    for the next run, the alert is not marked; a policy that still holds the alert for its quiet
    hours (`quiet_held`, its normal recipients not told yet) does not escalate it either;
  * reaches only people who may see the alert (alerts._recipients → permissions.doc_allowed).

Scheduler: `enqueue_escalations` (every 5 minutes) only queues `escalate_due` when an escalation
policy exists and an un-escalated Open alert is waiting.
"""

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from excom.excom_ads import alerts

POLICY = "Ads Notification Policy"
ALERT = "Ads Alert"
BATCH = 500


def _ready() -> bool:
	return bool(frappe.db.exists("DocType", ALERT) and frappe.get_meta(ALERT).has_field("escalated") and frappe.get_meta(POLICY).has_field("escalate_after_minutes"))


def _escalation_policies() -> dict[str, frappe._dict]:
	if not _ready():
		return {}
	rows = frappe.get_all(POLICY, filters={"enabled": 1, "escalate_after_minutes": [">", 0]}, fields=["name", "company", "alert_type", "min_severity", "escalate_after_minutes"])
	return {r.name: r for r in rows}


def _candidates(policies: dict, now, limit: int = BATCH) -> list[str]:
	"""Open, un-escalated alerts each escalation policy matches (delivery's own matching: company,
	type, minimum severity) and whose time is up. One indexed query per escalation policy."""
	names: list[str] = []
	for p in policies.values():
		cond = ["status = 'Open'", "escalated = 0", "coalesce(notified_at, first_seen) <= %(cutoff)s", "severity in %(sev)s"]
		params = {"cutoff": add_to_date(now, minutes=-cint(p.escalate_after_minutes)), "sev": tuple(s for s, r in alerts.SEVERITY_RANK.items() if r >= alerts.SEVERITY_RANK.get(p.min_severity or "High", 2))}
		if p.company:
			cond.append("company = %(company)s")
			params["company"] = p.company
		if p.alert_type:
			cond.append("alert_type = %(alert_type)s")
			params["alert_type"] = p.alert_type
		names += [r[0] for r in frappe.db.sql(f"select name from `tab{ALERT}` where {' and '.join(cond)} order by first_seen asc limit {int(limit)}", params)]
	return list(dict.fromkeys(names))[:limit]


def enqueue_escalations() -> None:
	"""Every 5 minutes. A query per escalation policy when there is nothing to do."""
	policies = _escalation_policies()
	if not policies or not _candidates(policies, now_datetime(), limit=1):
		return
	frappe.enqueue("excom.excom_ads.escalation.escalate_due", queue="short", job_id=f"excom_ads_escalate::{frappe.local.site}", deduplicate=True)


def escalate_due(now=None) -> dict:
	"""Escalate every Open, un-escalated alert past its policy's threshold. Returns counts."""
	out = {"checked": 0, "escalated": 0, "quiet": 0}
	policies = _escalation_policies()
	if not policies:
		return out
	now = get_datetime(now) if now else now_datetime()
	for name in _candidates(policies, now):
		out["checked"] += 1
		try:
			r = _escalate_one(name, policies, now)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom Ads: escalation failed for {name}", message=frappe.get_traceback())
			continue
		if r == "escalated":
			out["escalated"] += 1
		elif r == "quiet":
			out["quiet"] += 1
		frappe.db.commit()
	return out


def _escalate_one(name: str, policies: dict, now) -> str:
	alert = frappe.get_doc(ALERT, name)
	if alert.status != "Open" or cint(alert.escalated):
		return "skip"
	since = get_datetime(alert.notified_at or alert.first_seen)
	# A policy still holding the alert for its quiet hours has not told its own people yet: nothing to escalate.
	held = set(alerts._held(alert))
	due = [policies[p.name] for p in alerts._policies(alert) if p.get("name") in policies and p.get("name") not in held]
	due = [p for p in due if (now - since).total_seconds() >= cint(p.escalate_after_minutes) * 60]
	if not due:
		return "skip"
	full = [frappe.get_doc(POLICY, p.name) for p in due]
	sendable = [p for p in full if alert.severity == "Critical" or not alerts._quiet(p)]
	if not sendable:
		return "quiet"
	# Claim first: whoever flips escalated 0 → 1 sends; anyone else (a parallel or retried job) stops.
	frappe.db.sql(f"update `tab{ALERT}` set escalated = 1, escalated_at = %s where name = %s and escalated = 0 and status = 'Open'", (now, name))
	if not frappe.db.sql("select row_count()")[0][0]:
		return "skip"
	minutes = int((now - since).total_seconds() // 60)
	alert.title = f"[Escalated] {alert.title}"[:140]
	alert.message = f"Open for {minutes} min and not acknowledged.\n{alert.message or ''}".strip()
	reached: set[str] = set()
	for policy in sendable:
		users = alerts._recipients(policy, alert, table="escalation_recipients")
		if not users:
			continue
		reached |= set(users)
		if policy.get("via_desk"):
			alerts._desk(alert, users)
		if policy.get("via_email"):
			alerts._email(alert, users)
		if policy.get("via_whatsapp"):
			alerts._whatsapp(alert, users)
	frappe.db.set_value(ALERT, name, "escalated_to", ", ".join(sorted(reached))[:1000] or "(nobody in scope)", update_modified=False)
	return "escalated"
