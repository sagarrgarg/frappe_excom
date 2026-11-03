"""Consent: one place that records every opt-in / opt-out and keeps the identity flags in step.

Two layers:
  * Omni Identity flags (do_not_contact, whatsapp_opt_out, email_opt_out, wa_marketing_opt_out)
    are what senders check — fast, one row.
  * Excom Consent is the append-only ledger: who agreed or withdrew, when, how, and the evidence.
    That is the proof of consent the DPDP Act / Rules 2025 ask for (from about May 2027).

Every function is best-effort when called from a webhook: a ledger write must never stop an
inbound message or a status update from being stored.
"""

import frappe
from frappe.utils import now_datetime

# source → ledger method
_METHODS = {
	"whatsapp_stop": "WhatsApp Reply",
	"whatsapp_start": "WhatsApp Reply",
	"meta_user_preferences": "Meta Preference",
	"meta_131050": "Delivery Error",
	"unsubscribe_link": "Unsubscribe Link",
	"subscriber_list": "Subscriber List",
	"import": "Import",
	"manual": "Manual",
}


def record_consent(
	identity: str,
	action: str,
	channel: str = "whatsapp",
	purpose: str = "marketing",
	source: str = "manual",
	method: str = "",
	notice_version: str = "",
	evidence: str = "",
	reference_doctype: str = "",
	reference_name: str = "",
	notes: str = "",
) -> str:
	"""Append one consent event. Returns the Excom Consent name ('' if it could not be written)."""
	if not identity or action not in ("Granted", "Withdrawn"):
		return ""
	if not frappe.db.exists("DocType", "Excom Consent"):
		return ""  # before the migrate that adds the ledger
	try:
		doc = frappe.get_doc({
			"doctype": "Excom Consent",
			"omni_identity": identity,
			"action": action,
			"channel": channel if channel in ("whatsapp", "email", "all") else "whatsapp",
			"purpose": purpose or "marketing",
			"source": source or "",
			"method": method or _METHODS.get(source or "", "Manual"),
			"notice_version": notice_version or "",
			"evidence": evidence or "",
			"reference_doctype": reference_doctype or None,
			"reference_name": reference_name or None,
			"notes": notes or "",
			"recorded_on": now_datetime(),
		})
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:
		frappe.log_error(title=f"Excom: consent record failed for {identity}", message=frappe.get_traceback())
		return ""


def latest_consent(identity: str, channel: str = "whatsapp", purpose: str = "marketing") -> str:
	"""'Granted' / 'Withdrawn' / '' — the newest ledger entry for this identity, channel and purpose."""
	if not frappe.db.exists("DocType", "Excom Consent"):
		return ""
	rows = frappe.get_all(
		"Excom Consent",
		filters={"omni_identity": identity, "channel": ["in", [channel, "all"]], "purpose": ["in", [purpose, "all"]]},
		fields=["action"],
		order_by="recorded_on desc, creation desc",
		limit=1,
	)
	return rows[0].action if rows else ""


def set_marketing_opt_out(identity: str, source: str, reference_doctype: str = "", reference_name: str = "", notes: str = "") -> None:
	"""WhatsApp marketing stop (Meta 'Stop promotions', 131050). Utility / service messages still go."""
	if not identity:
		return
	current = frappe.db.get_value("Omni Identity", identity, "wa_marketing_opt_out")
	frappe.db.set_value(
		"Omni Identity", identity,
		{"wa_marketing_opt_out": 1, "wa_marketing_opt_out_on": now_datetime(), "wa_marketing_opt_out_source": source},
		update_modified=False,
	)
	if not current:
		record_consent(identity, "Withdrawn", "whatsapp", "marketing", source, reference_doctype=reference_doctype, reference_name=reference_name, notes=notes)


def _scope(source: str) -> tuple[str, str]:
	"""'meta_user_preferences@123' → ('meta_user_preferences', '123')."""
	base, _, waba = (source or "").partition("@")
	return base, waba


# opt-outs a WhatsApp "resume" may lift: ones that came from WhatsApp itself for that account
_RESUMABLE = {"meta_user_preferences", "meta_131050", "template_optout_button"}


def clear_marketing_opt_out(identity: str, source: str, reference_doctype: str = "", reference_name: str = "", notes: str = "") -> None:
	"""WhatsApp marketing resume (Meta 'resume' preference).

	Preferences are per business: a resume lifts only an opt-out that WhatsApp reported for the
	same account. An opt-out recorded another way (staff, import, another account) stays.
	"""
	if not identity:
		return
	current, current_source = frappe.db.get_value("Omni Identity", identity, ["wa_marketing_opt_out", "wa_marketing_opt_out_source"]) or (0, "")
	if current:
		cur_base, cur_waba = _scope(current_source)
		_, new_waba = _scope(source)
		if cur_base not in _RESUMABLE or (cur_waba and new_waba and cur_waba != new_waba):
			return
	frappe.db.set_value(
		"Omni Identity", identity,
		{"wa_marketing_opt_out": 0, "wa_marketing_opt_out_on": now_datetime(), "wa_marketing_opt_out_source": source},
		update_modified=False,
	)
	if current:
		record_consent(identity, "Granted", "whatsapp", "marketing", source, reference_doctype=reference_doctype, reference_name=reference_name, notes=notes)


def on_subscription_change(identity: str, subscriber_list: str, subscribed: bool, source: str = "subscriber_list", subscriber: str = "") -> None:
	"""A person joined or left a subscriber list."""
	record_consent(
		identity,
		"Granted" if subscribed else "Withdrawn",
		channel="all",
		purpose=f"list:{subscriber_list}",
		source=source,
		reference_doctype="Excom Subscriber" if subscriber else "",
		reference_name=subscriber,
	)
