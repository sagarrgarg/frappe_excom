"""
Intake spine (P3 §3.4 / RES-001 §7.3). One pipeline, many adapters:

    adapter → Excom Intake Log (raw, dedupe_key = unique index)   # idempotency is a DB constraint (F6)
            → map via field_map
            → resolve_identity(...)                               # exists today
            → crm_gateway.create_lead + set_attribution + provenance
            → thread_service.upsert_thread
            → enqueue auto-ack (marketplace only)
            → sticky owner / Assignment Rule (via crm_flow hooks)
            → log.status = Processed
"""

import json
import re
import traceback

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.services import crm_flow
from excom.excom.services import crm_gateway as gw
from excom.excom.utils.phone import normalize_phone as _normalize_phone_util

MARKETPLACE_TYPES = {"IndiaMART", "TradeIndia", "Meta Lead Ads"}
CHANNEL_FOR_TYPE = {"Website": "Web Form", "IndiaMART": "Marketplace", "TradeIndia": "Marketplace", "Meta Lead Ads": "Facebook", "Exhibition": "Manual", "Manual": "Manual"}

# Default vendor → gateway key maps, used when a source has no field_map rows.
DEFAULT_MAPS = {
	"IndiaMART": {"SENDER_NAME": "name", "SENDER_MOBILE": "phone", "SENDER_EMAIL": "email", "SENDER_COMPANY": "company_name", "SENDER_CITY": "city", "SENDER_STATE": "state", "SENDER_COUNTRY_ISO": "country", "QUERY_MESSAGE": "message", "QUERY_PRODUCT_NAME": "product", "SUBJECT": "subject", "QUERY_TYPE": "query_type"},
	"TradeIndia": {"sender_name": "name", "sender_mobile": "phone", "sender_email": "email", "sender_co": "company_name", "sender_city": "city", "sender_state": "state", "sender_country": "country", "subject": "subject", "message": "message", "product_name": "product"},
	"Website": {"name": "name", "email": "email", "phone": "phone", "message": "message", "company": "company_name", "city": "city", "country": "country"},
	"Meta Lead Ads": {"full_name": "name", "email": "email", "phone_number": "phone", "company_name": "company_name", "city": "city", "country": "country"},
}


def record(source, dedupe_key: str, raw: dict, received_at=None) -> tuple[str, bool]:
	"""Insert the log row. Returns (log name, is_new). A duplicate dedupe_key returns the existing row."""
	if not dedupe_key:
		frappe.throw(_("dedupe_key is required"))
	existing = frappe.db.get_value("Excom Intake Log", {"dedupe_key": dedupe_key}, "name")
	if existing:
		return existing, False
	try:
		log = frappe.get_doc(
			{"doctype": "Excom Intake Log", "source": source.name, "dedupe_key": dedupe_key, "status": "Received", "raw_payload": json.dumps(raw, ensure_ascii=False, default=str), "received_at": received_at or now_datetime()}
		)
		log.insert(ignore_permissions=True)
		frappe.db.commit()
		return log.name, True
	except frappe.UniqueValidationError:
		frappe.db.rollback()
		return frappe.db.get_value("Excom Intake Log", {"dedupe_key": dedupe_key}, "name"), False


def enqueue(log_name: str) -> None:
	frappe.enqueue("excom.excom.services.intake.process_log", queue="short", log_name=log_name, enqueue_after_commit=True)


def _transform(value, kind: str):
	if value is None:
		return None
	v = str(value)
	if kind == "strip":
		return v.strip()
	if kind == "lower":
		return v.strip().lower()
	if kind == "phone_e164":
		return normalize_phone(v)
	if kind == "first_word":
		return v.strip().split(" ")[0]
	if kind == "last_words":
		return " ".join(v.strip().split(" ")[1:])
	if kind == "yes_no":
		return 1 if v.strip().lower() in ("yes", "y", "true", "1") else 0
	return v


def normalize_phone(v: str) -> str:
	"""E.164 with India as the default country: '9900000782' → '+919900000782', '91 99…' → '+9199…'."""
	try:
		out = _normalize_phone_util(v)
	except Exception:
		out = re.sub(r"[^\d+]", "", v or "")
	if not out:
		return ""
	if out.startswith("+"):
		return out
	digits = out.lstrip("0")
	if len(digits) == 10:
		return "+91" + digits
	if len(digits) == 12 and digits.startswith("91"):
		return "+" + digits
	return "+" + digits if len(digits) > 10 else "+91" + digits


def country_name(v: str) -> str | None:
	"""Vendors send ISO codes ('IN') or names; ERPNext wants the Country name. Unknown → None (never a broken link)."""
	if not v:
		return None
	v = str(v).strip()
	if frappe.db.exists("Country", v):
		return v
	if len(v) == 2:
		return frappe.db.get_value("Country", {"code": v.lower()}, "name")
	return frappe.db.get_value("Country", {"country_name": v}, "name")


def _get(raw: dict, key: str):
	"""dotted-path lookup: 'field_data.email' or plain key."""
	cur = raw
	for part in key.split("."):
		if isinstance(cur, dict) and part in cur:
			cur = cur[part]
		else:
			return None
	return cur


def map_payload(source, raw: dict) -> dict:
	rows = source.get("field_map") or []
	mapping = {r.source_key: (r.target_fieldname, r.transform) for r in rows} if rows else {k: (v, "") for k, v in DEFAULT_MAPS.get(source.source_type, {}).items()}
	out: dict = {}
	for src_key, (target, transform) in mapping.items():
		val = _get(raw, src_key)
		if val in (None, ""):
			continue
		out[target] = _transform(val, transform or "")
	# Meta lead ads: field_data is a list of {name, values:[...]}
	if source.source_type == "Meta Lead Ads" and isinstance(raw.get("field_data"), list):
		for fd in raw["field_data"]:
			k = fd.get("name")
			v = (fd.get("values") or [None])[0]
			if k and v and k in mapping:
				out[mapping[k][0]] = _transform(v, mapping[k][1] or "")
	if out.get("phone"):
		out["phone"] = normalize_phone(out["phone"])
	if out.get("country"):
		out["country"] = country_name(out["country"])
		if not out["country"]:
			out.pop("country")
	if out.get("email"):
		out["email"] = str(out["email"]).strip().lower()
	return out


def process_log(log_name: str, force: bool = False) -> None:
	# Guest endpoints enqueue this; the worker inherits Guest and cannot create Leads. Ingestion is a system action.
	if frappe.session.user == "Guest":
		frappe.set_user("Administrator")
	log = frappe.get_doc("Excom Intake Log", log_name)
	if log.status == "Processed" and not force:
		return
	# Claim atomically: two workers (webhook retry + poll) must never process the same log at once.
	claimed = frappe.db.sql(
		"UPDATE `tabExcom Intake Log` SET status = 'Processing' WHERE name = %s AND status IN ('Received', 'Failed') " + ("" if not force else "OR name = %s AND status = 'Processed'"),
		(log_name,) if not force else (log_name, log_name),
	)
	frappe.db.commit()
	if not frappe.db.get_value("Excom Intake Log", log_name, "status") == "Processing":
		return
	log.reload()
	source = frappe.get_doc("Excom Intake Source", log.source)
	try:
		raw = json.loads(log.raw_payload or "{}")
		mapped = map_payload(source, raw)
		log.mapped_payload = json.dumps(mapped, ensure_ascii=False)
		if not (mapped.get("phone") or mapped.get("email")):
			log.status = "Ignored"
			log.error = "No phone or email after mapping"
			log.save(ignore_permissions=True)
			return
		from excom.excom.doctype.omni_identity.omni_identity import resolve_identity

		identity = resolve_identity(phone=mapped.get("phone", ""), email=mapped.get("email", ""), channel="", channel_user_id="", display_name=mapped.get("name") or mapped.get("company_name") or "")
		identity_name = identity.name if hasattr(identity, "name") else identity
		log.omni_identity = identity_name

		notes = "\n".join(filter(None, [mapped.get("subject"), mapped.get("product"), mapped.get("message"), f"Page: {raw.get('page_url')}" if raw.get("page_url") else ""]))
		utm = raw.get("utm") or {}
		payload = {
			"name": mapped.get("name"), "email": mapped.get("email"), "phone": mapped.get("phone"), "company_name": mapped.get("company_name"),
			"city": mapped.get("city"), "state": mapped.get("state"), "country": mapped.get("country"), "customer_type": mapped.get("customer_type") or "",
			"company": source.company, "owner": source.default_lead_owner, "notes": notes,
			"source": source.source_name, "campaign": utm.get("campaign") or mapped.get("campaign"), "medium": utm.get("medium") or mapped.get("medium"),
			"source_reference": log.dedupe_key, "intake_source": source.name, "first_touch_channel": CHANNEL_FOR_TYPE.get(source.source_type, "Manual"),
		}
		r, created = crm_flow.resolve_or_create_lead(identity_name, payload["first_touch_channel"], payload, ignore_permissions=True)
		log.lead_doctype, log.lead = r.doctype, r.name
		if created:
			frappe.db.set_value(r.doctype, r.name, "intake_stage", "Deduped", update_modified=False)

		# thread on the auto-ack channel account, so the reply lands in the inbox
		thread = None
		if source.channel_account:
			from excom.excom.services.thread_service import upsert_thread
			ch = frappe.db.get_value("Excom Channel Account", source.channel_account, "channel")
			thread = upsert_thread(identity_name, ch, source.channel_account)
			log.thread = thread
			crm_flow.post_system_message(identity_name, _("Enquiry from {0} ({1})").format(source.source_name, log.dedupe_key))
		if created and source.source_type in MARKETPLACE_TYPES and source.channel_account and source.auto_ack_template and mapped.get("phone"):
			frappe.enqueue("excom.excom.services.intake.send_auto_ack", queue="short", log_name=log.name, enqueue_after_commit=True)

		log.status = "Processed"
		log.processed_at = now_datetime()
		log.error = ""
		log.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		log.reload()
		log.status = "Failed"
		log.error = traceback.format_exc()
		log.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.log_error(title=f"Excom intake failed: {log.name}", message=log.error)


def send_auto_ack(log_name: str) -> None:
	"""HLD-003 §4.5 — pre-approved utility template to the enquirer, posted into the thread; stamps auto_ack_sent_at."""
	log = frappe.get_doc("Excom Intake Log", log_name)
	source = frappe.get_doc("Excom Intake Source", log.source)
	if not (log.thread and source.auto_ack_template):
		return
	from excom.excom.api.chat import send_template_to_thread

	tpl = frappe.db.get_value("WhatsApp Templates", source.auto_ack_template, ["name", "language_code"], as_dict=True)
	if not tpl:
		return
	frappe.set_user("Administrator")
	try:
		send_template_to_thread(thread_id=log.thread, template_name=tpl.name, language_code=tpl.language_code or "en", variables="[]")
		if log.lead:
			frappe.db.set_value(log.lead_doctype, log.lead, {"auto_ack_sent_at": now_datetime(), "intake_stage": "Responded"}, update_modified=False)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title=f"Excom auto-ack failed: {log_name}", message=frappe.get_traceback())


def ingest(source, dedupe_key: str, raw: dict, received_at=None, sync: bool = False) -> dict:
	"""Adapter entry point: log + enqueue (or process inline)."""
	name, is_new = record(source, dedupe_key, raw, received_at)
	if not is_new:
		st = frappe.db.get_value("Excom Intake Log", name, "status")
		if st != "Failed":  # Received / Processing / Processed / Duplicate / Ignored → already handled or in flight
			return {"log": name, "duplicate": True}
	if sync:
		process_log(name)
	else:
		enqueue(name)
	return {"log": name, "duplicate": False}
