"""
CRM SLA + escalation ladder (P3 §3.11 / HLD-003 §10). Hourly, patterned on delivery_watchdog.

Targets: first response per intake source (sla_first_response) · classification 4 business hours ·
next_action_at overdue · reply latency 4 business hours on open opportunities.
Ladder: breach → in-app notification to owner · +30 min → WhatsApp to owner's mobile ·
+2 h → team manager · +1 day → unassign back to the intake queue (logged).
Elapsed-time model with a holiday list; the working-hours model arrives in P5 if false breaches appear.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime, time_diff_in_seconds

from excom.excom.services import crm_gateway as gw

BUSINESS_HOURS = 4 * 3600
LADDER = [(0, 1), (30 * 60, 2), (2 * 3600, 3), (24 * 3600, 4)]  # (seconds since breach, level)


def _mode() -> str:
	try:
		return frappe.db.get_single_value("Excom Settings", "crm_sla_escalation") or "In-app only"
	except Exception:
		return "In-app only"


def _go_live():
	try:
		d = frappe.db.get_single_value("Excom Settings", "crm_go_live_date")
	except Exception:
		d = None
	return get_datetime(d) if d else get_datetime("2026-09-03")


def check_crm_sla() -> dict:
	"""Never touches records created before the CRM go-live date; escalation rungs beyond in-app
	require Excom Settings.crm_sla_escalation = "Full ladder"."""
	out = {"first_response": 0, "classification": 0, "next_action": 0, "reply_latency": 0}
	if _mode() == "Off" or frappe.flags.in_test:
		return out
	now = now_datetime()
	go_live = _go_live()
	sla_by_source = {s.name: s.sla_first_response for s in frappe.get_all("Excom Source", fields=["name", "sla_first_response"])}

	# 1) first response + 2) classification — on open Leads
	for lead in gw.list_intake({}, limit=2000):
		if get_datetime(lead["creation"]) < go_live:
			continue
		age = time_diff_in_seconds(now, lead["creation"])
		target = sla_by_source.get(lead.get("intake_source")) or 0
		responded = bool(lead.get("auto_ack_sent_at") or lead.get("_thread_last_outbound_at"))
		if target and age > target and not responded:
			out["first_response"] += _escalate(gw.lead_ref(lead["name"]), "first_response", age - target, lead.get("lead_owner"))
		if not lead.get("customer_type") and age > BUSINESS_HOURS:
			out["classification"] += _escalate(gw.lead_ref(lead["name"]), "classification", age - BUSINESS_HOURS, lead.get("lead_owner"))

	# 3) next_action overdue + 4) reply latency — on open Opportunities
	for opp in gw.list_pipeline("", {}, limit=5000):
		if get_datetime(opp.get("stage_entered_at") or opp["modified"]) < go_live:
			continue
		owner = opp.get("opportunity_owner")
		if opp.get("next_action_at") and get_datetime(opp["next_action_at"]) < now:
			out["next_action"] += _escalate(gw.opportunity_ref(opp["name"]), "next_action", time_diff_in_seconds(now, opp["next_action_at"]), owner)
		if opp.get("omni_identity"):
			t = frappe.db.get_value("Excom Thread", {"omni_identity": opp["omni_identity"], "status": "Open"}, ["last_inbound_at", "last_outbound_at"], as_dict=True, order_by="last_message_at desc")
			if t and t.last_inbound_at and (not t.last_outbound_at or t.last_outbound_at < t.last_inbound_at):
				waited = time_diff_in_seconds(now, t.last_inbound_at)
				if waited > BUSINESS_HOURS:
					out["reply_latency"] += _escalate(gw.opportunity_ref(opp["name"]), "reply_latency", waited - BUSINESS_HOURS, owner)
	frappe.db.commit()
	return out


def _escalate(r, kind: str, over_by: int, owner: str | None) -> int:
	"""Advance the ladder for (record, kind). Returns 1 if a step fired."""
	key = f"sla:{r.doctype}:{r.name}:{kind}"
	level = frappe.cache.get_value(key) or 0
	target = 0
	for secs, lvl in LADDER:
		if over_by >= secs:
			target = lvl
	if target <= level:
		return 0
	if _mode() != "Full ladder":
		target = min(target, 1)
		if target <= level:
			return 0
	title = gw.get_title(r)
	msg = {
		"first_response": _("First response overdue on {0}").format(title),
		"classification": _("Lead {0} not classified within 4 hours").format(title),
		"next_action": _("Next action overdue on {0}").format(title),
		"reply_latency": _("Customer waiting on {0} for over 4 hours").format(title),
	}[kind]
	if target >= 1 and owner:
		_notify_user(owner, msg, r)
	if target >= 2 and owner:
		_whatsapp_user(owner, msg)
	if target >= 3:
		manager = _team_manager(owner)
		if manager:
			_notify_user(manager, _("Escalation: ") + msg, r)
	if target >= 4 and gw.is_lead(r):
		_unassign(r, msg)
	frappe.cache.set_value(key, target, expires_in_sec=7 * 86400)
	return 1


def _notify_user(user: str, message: str, r) -> None:
	try:
		frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": message, "document_type": r.doctype, "document_name": r.name, "from_user": "Administrator"}).insert(ignore_permissions=True)
		frappe.publish_realtime("excom:sla_breach", {"doctype": r.doctype, "name": r.name, "message": message}, user=user)
	except Exception:
		frappe.log_error(title="Excom SLA notify failed", message=frappe.get_traceback())


def _whatsapp_user(user: str, message: str) -> None:
	mobile = frappe.db.get_value("User", user, "mobile_no")
	acc = frappe.db.get_value("Excom Channel Account", {"channel": "whatsapp", "status": "Active", "is_default_outgoing": 1}, "name") or frappe.db.get_value("Excom Channel Account", {"channel": "whatsapp", "status": "Active"}, "name")
	if not (mobile and acc):
		return
	try:
		from excom.excom.services.whatsapp_service import send_text_message
		send_text_message(frappe.get_doc("Excom Channel Account", acc), mobile, f"[Excom SLA] {message}")
	except Exception:
		frappe.log_error(title="Excom SLA WhatsApp escalation failed", message=frappe.get_traceback())


def _team_manager(user: str | None) -> str | None:
	if not user:
		return None
	team = frappe.db.get_value("Excom Team Member", {"user": user}, "parent")
	if not team:
		return None
	return frappe.db.get_value("Excom Team Member", {"parent": team, "role": "Manager", "user": ["!=", user]}, "user")


def _unassign(r, reason: str) -> None:
	from frappe.desk.form.assign_to import clear
	try:
		clear(r.doctype, r.name)
		frappe.db.set_value(r.doctype, r.name, "lead_owner", None, update_modified=False)
		frappe.get_doc(r.doctype, r.name).add_comment("Comment", _("Returned to intake queue by SLA ladder: {0}").format(reason))
	except Exception:
		frappe.log_error(title="Excom SLA unassign failed", message=frappe.get_traceback())
