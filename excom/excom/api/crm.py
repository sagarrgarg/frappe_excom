"""
api/crm — thin endpoints for the CRM UI (P3 §3.3 / HLD-003 §3.4). Every call goes through frappe
permissions (frappe.get_list / doc.check_permission); no ignore_permissions here.
Vocabulary is the gateway's: refs are {"doctype","name"}; doctype strings never appear in this file.
"""

import json

import frappe
from frappe import _

from excom.excom.api.chat import _check_excom_access
from excom.excom.services import crm_flow
from excom.excom.services import crm_gateway as gw
from excom.excom.utils.ratelimit import user_rate_limit


def _ref(doctype: str, name: str):
	if doctype not in gw.crm_doctypes():
		frappe.throw(_("Not a CRM record type"))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} not found").format(doctype, name), frappe.DoesNotExistError)
	return gw.ref(doctype, name)


# ─── schema-driven Details tab (E2: a Custom Field added in Desk shows on reload) ─────────────

TYPE_FIELDS = {
	# fields surfaced first per customer type; everything else follows in meta order
	"Distributor": ["proposed_pincodes", "territory", "annual_revenue", "no_of_employees"],
	"Retailer": ["proposed_pincodes", "territory"],
	"Export Importer": ["country", "incoterm", "currency", "conversion_rate"],
	"OEM": ["design_by", "sample_round"],
	"Corporate Gifting": ["event_date"],
	"Online B2C": [],
}
HIDDEN = {"naming_series", "amended_from", "gate_flags", "omni_identity", "image", "address_html", "contact_html", "notes_html", "open_activities_html", "all_activities_html", "language", "blog_subscriber", "unsubscribed", "disabled", "title", "fax", "phone_ext", "base_opportunity_amount", "base_total", "total", "first_response_time", "lost_reasons", "competitors"}


# Compact layouts: what an agent needs while chatting, in one screen. Everything else folds under
# "More"; Excom's own bookkeeping is read-only meta. Missing fieldnames are skipped, so a site
# without a custom field still renders.
COMPACT_LAYOUT: dict[str, list[dict]] = {
	gw.LEAD: [
		{"label": "", "fields": ["first_name", "last_name", "mobile_no", "email_id", "company_name", "territory", "city", "country"]},
		{"label": "Deal", "fields": ["customer_type", "status", "lead_owner", "source", "campaign_name"]},
		{"label": "More", "collapsed": True, "fields": ["salutation", "middle_name", "gender", "job_title", "phone", "whatsapp_no", "website", "industry", "market_segment", "no_of_employees", "annual_revenue", "state", "request_type", "lead_type", "customer"]},
		{"label": "Excom", "collapsed": True, "meta": True, "fields": ["intake_source", "intake_stage", "first_touch_at", "first_touch_channel", "first_touch_by", "source_reference", "exhibition", "auto_ack_sent_at", "qualification_status", "qualified_by", "qualified_on"]},
	],
	gw.OPPORTUNITY: [
		{"label": "", "fields": ["party_name", "customer_name", "contact_mobile", "contact_email", "opportunity_amount", "currency", "expected_closing", "probability"]},
		{"label": "Deal", "fields": ["customer_type", "status", "opportunity_owner", "opportunity_type", "source", "campaign"]},
		{"label": "More", "collapsed": True, "fields": ["contact_person", "job_title", "whatsapp", "phone", "website", "territory", "city", "state", "country", "industry", "market_segment", "customer_group", "no_of_employees", "annual_revenue", "conversion_rate", "transaction_date", "order_lost_reason"]},
		{"label": "Excom", "collapsed": True, "meta": True, "fields": ["pipeline_stage", "stage_entered_at", "next_action_at", "first_touch_at", "first_touch_channel", "first_touch_by", "source_reference"]},
	],
}


def _field_dict(df, can_write: bool, first: list[str]) -> dict:
	return {
		"fieldname": df.fieldname, "label": df.label, "fieldtype": df.fieldtype, "options": df.options,
		"reqd": bool(df.reqd), "read_only": bool(df.read_only) or not can_write, "description": df.description,
		"priority": 0 if df.fieldname in first else 1,
	}


@frappe.whitelist()
def get_field_schema(doctype: str, customer_type: str = "") -> dict:
	_check_excom_access()
	if doctype not in gw.crm_doctypes():
		frappe.throw(_("Not a CRM record type"))
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	meta = frappe.get_meta(doctype)
	can_write = frappe.has_permission(doctype, "write")
	first = TYPE_FIELDS.get(customer_type or "", [])
	layout = COMPACT_LAYOUT.get(doctype)
	if layout:
		sections, used = [], set()
		for i, sec in enumerate(layout):
			fields = []
			names = list(sec["fields"])
			if i == 0:
				names = names + [f for f in first if f not in names]  # type-specific fields ride in the first section
			for fn in names:
				df = meta.get_field(fn)
				if not df or df.hidden or fn in used:
					continue
				used.add(fn)
				d = _field_dict(df, can_write, first)
				if sec.get("meta"):
					d["read_only"] = True
				fields.append(d)
			if fields:
				sections.append({"label": sec["label"], "collapsed": bool(sec.get("collapsed")), "meta": bool(sec.get("meta")), "fields": fields})
		return {"doctype": doctype, "customer_type": customer_type, "sections": sections, "can_write": can_write, "stages": gw.stages_for(customer_type), "compact": True}
	sections, current = [], {"label": "", "fields": []}
	for df in meta.fields:
		if df.fieldtype in ("Section Break", "Tab Break"):
			if current["fields"]:
				sections.append(current)
			current = {"label": df.label or "", "fields": []}
			continue
		if df.fieldtype in ("Column Break", "HTML", "Button", "Table MultiSelect", "Attach Image", "Image", "Geolocation", "Signature", "Barcode"):
			continue
		if df.fieldname in HIDDEN or df.hidden:
			continue
		dep = df.depends_on or ""
		if customer_type and "customer_type" in dep and customer_type not in dep:
			continue
		current["fields"].append(_field_dict(df, can_write, first))
	if current["fields"]:
		sections.append(current)
	for s in sections:
		s["fields"].sort(key=lambda f: f["priority"])
	return {"doctype": doctype, "customer_type": customer_type, "sections": sections, "can_write": can_write, "stages": gw.stages_for(customer_type)}


# ─── records ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_record(doctype: str, name: str) -> dict:
	_check_excom_access()
	r = _ref(doctype, name)
	out = gw.get_record(r)
	if gw.is_opportunity(r):
		out["_gate_status"] = crm_flow.gate_status(r)
	out["_stage_log"] = frappe.get_all("Excom Stage Change Log", filters={"ref_doctype": doctype, "ref_name": name}, fields=["from_stage", "to_stage", "from_date", "to_date", "duration", "log_owner", "note"], order_by="to_date desc", limit=50)
	return out


@frappe.whitelist(methods=["POST"])
def update_record(doctype: str, name: str, values: str | dict) -> dict:
	"""Field edits from the Details tab. Attribution/provenance/stage fields are refused here."""
	_check_excom_access()
	r = _ref(doctype, name)
	vals = json.loads(values) if isinstance(values, str) else (values or {})
	blocked = {"source", "campaign_name", "campaign", "utm_source", "utm_campaign", "utm_medium", "first_touch_at", "first_touch_channel", "first_touch_by", "source_reference", "pipeline_stage", "stage_entered_at", "gate_flags", "omni_identity", "status"}
	vals = {k: v for k, v in vals.items() if k not in blocked}
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("write")
	doc.update(vals)
	doc.save()
	return gw.get_record(r)


@frappe.whitelist(methods=["POST"])
def set_stage(name: str, stage: str, note: str = "") -> dict:
	_check_excom_access()
	return crm_flow.advance_stage(gw.opportunity_ref(name), stage, note)


@frappe.whitelist()
def get_gate_status(name: str) -> dict:
	_check_excom_access()
	return crm_flow.gate_status(_ref(gw.OPPORTUNITY, name))


@frappe.whitelist(methods=["POST"])
def override_gate(name: str, gate: str, reason: str) -> dict:
	_check_excom_access()
	r = _ref(gw.OPPORTUNITY, name)
	crm_flow.override_gate(r, gate, reason)
	return crm_flow.gate_status(r)


@frappe.whitelist(methods=["POST"])
def classify_lead(name: str, customer_type: str, territory: str = "", country: str = "", request_type: str = "", industry: str = "") -> dict:
	_check_excom_access()
	if customer_type not in gw.CUSTOMER_TYPES:
		frappe.throw(_("Unknown customer type"))
	r = _ref(gw.LEAD, name)
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("write")
	doc.customer_type = customer_type
	doc.intake_stage = "Classified"
	for k, v in (("territory", territory), ("country", country), ("request_type", request_type), ("industry", industry)):
		if v:
			doc.set(k, v)
	doc.save()
	return gw.get_record(r)


@frappe.whitelist(methods=["POST"])
def bulk_classify(names: str | list, customer_type: str) -> dict:
	_check_excom_access()
	names = json.loads(names) if isinstance(names, str) else names
	done, failed = [], []
	for n in names:
		try:
			classify_lead(n, customer_type)
			done.append(n)
		except Exception as e:
			failed.append({"name": n, "error": str(e)})
	return {"done": done, "failed": failed}


@frappe.whitelist(methods=["POST"])
def promote_thread(thread: str, customer_type: str = "") -> dict:
	"""Thread → Lead (agent-promoted path, HLD-003 §4.4)."""
	from excom.excom.api.chat import _check_thread_access
	_check_thread_access(thread)
	if not frappe.has_permission(gw.LEAD, "create"):
		frappe.throw(_("Not permitted to create leads"), frappe.PermissionError)
	identity = frappe.db.get_value("Excom Thread", thread, "omni_identity")
	existing = gw.find_open_records_for_identity(identity) if identity else []
	if existing:
		return {"ref": existing[0], "created": False, "title": gw.get_title(existing[0])}
	r = gw.promote_thread(thread, customer_type, by=frappe.session.user)
	crm_flow.post_system_message(identity, _("Promoted to {0} {1}").format(r.doctype, r.name))
	return {"ref": r, "created": True, "title": gw.get_title(r)}


@frappe.whitelist(methods=["POST"])
def convert(doctype: str, name: str, target: str) -> dict:
	_check_excom_access()
	new = crm_flow.convert(_ref(doctype, name), target)
	return {"ref": new, "title": gw.get_title(new)}


@frappe.whitelist(methods=["POST"])
def set_next_action(doctype: str, name: str, next_action_at: str) -> dict:
	_check_excom_access()
	r = _ref(doctype, name)
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("write")
	frappe.db.set_value(r.doctype, r.name, "next_action_at", next_action_at or None)
	return {"ok": True}


# ─── lists ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def get_intake_queue(filters: str | dict | None = None) -> list:
	_check_excom_access()
	f = json.loads(filters) if isinstance(filters, str) else (filters or {})
	rows = gw.list_intake(f)
	_join_threads(rows)
	# SLA state: first response target from the intake source
	sla = {s.name: s.sla_first_response for s in frappe.get_all("Excom Intake Source", fields=["name", "sla_first_response"])}
	now = frappe.utils.now_datetime()
	owners = {r.get("lead_owner") for r in rows if r.get("lead_owner")}
	enabled = {u.name for u in frappe.get_all("User", filters={"name": ["in", list(owners)], "enabled": 1}, fields=["name"])} if owners else set()
	for r in rows:
		if r.get("lead_owner") and r["lead_owner"] not in enabled:
			r["lead_owner_disabled"] = r["lead_owner"]
			r["lead_owner"] = None
		target = sla.get(r.get("intake_source")) or 0
		age = frappe.utils.time_diff_in_seconds(now, r["creation"])
		r["_age_seconds"] = age
		r["_sla_seconds"] = target
		r["_sla_breached"] = bool(target and age > target and not r.get("auto_ack_sent_at") and not r.get("_thread_last_outbound_at"))
	return rows


@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def get_pipeline(customer_type: str = "", filters: str | dict | None = None) -> dict:
	_check_excom_access()
	f = json.loads(filters) if isinstance(filters, str) else (filters or {})
	rows = gw.list_pipeline(customer_type, f)
	_join_threads(rows)
	stages = gw.stages_for(customer_type) if customer_type else list(gw.STAGES.keys())
	cols = {s: [] for s in stages}
	for r in rows:
		try:
			r["_gates"] = json.loads(r.get("gate_flags") or "{}")
		except ValueError:
			r["_gates"] = {}
		r["_gates"].pop("_override", None)
		cols.setdefault(r.get("pipeline_stage") or stages[0], []).append(r)
	return {"stages": stages, "columns": cols, "count": len(rows)}


@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def get_today() -> dict:
	"""The rep's day: overdue next actions, SLA-breaching intake, today's actions, unassigned for my teams."""
	_check_excom_access()
	user = frappe.session.user
	actions = gw.list_actions_due(user)
	_join_threads(actions)
	now = frappe.utils.now_datetime()
	today = frappe.utils.nowdate()
	overdue = [a for a in actions if a.get("next_action_at") and a["next_action_at"] < now]
	due_today = [a for a in actions if a.get("next_action_at") and a["next_action_at"] >= now and str(a["next_action_at"])[:10] == today]
	intake = [r for r in get_intake_queue({}) if r.get("_sla_breached")]
	unassigned = [r for r in gw.list_intake({}) if not r.get("lead_owner")][:50]
	_join_threads(unassigned)
	return {"overdue": overdue, "sla": intake[:50], "today": due_today, "unassigned": unassigned}


def _join_threads(rows: list) -> None:
	"""Attach the freshest open thread per identity (one query)."""
	ids = [r.get("omni_identity") for r in rows if r.get("omni_identity")]
	if not ids:
		return
	threads = frappe.db.sql(
		"""SELECT t.omni_identity, t.name, t.channel, t.last_inbound_at, t.last_outbound_at, t.last_message_at, t.unread_count, t.assigned_to
		   FROM `tabExcom Thread` t WHERE t.omni_identity IN %(ids)s AND t.status IN ('Open','Pending') ORDER BY t.last_message_at DESC""",
		{"ids": tuple(set(ids))},
		as_dict=True,
	)
	by: dict = {}
	for t in threads:
		by.setdefault(t.omni_identity, t)
	for r in rows:
		t = by.get(r.get("omni_identity"))
		if t:
			r["_thread"] = t.name
			r["_thread_channel"] = t.channel
			r["_thread_last_inbound_at"] = t.last_inbound_at
			r["_thread_last_outbound_at"] = t.last_outbound_at
			r["_thread_unread"] = t.unread_count


@frappe.whitelist()
def get_records_for_identity(omni_identity: str) -> list:
	"""CRM records linked to an identity, precedence order — used by the record header chip."""
	_check_excom_access()
	out = []
	for r in gw.find_open_records_for_identity(omni_identity):
		if frappe.has_permission(r.doctype, "read", doc=r.name):
			row = {"doctype": r.doctype, "name": r.name, "title": gw.get_title(r)}
			if gw.is_opportunity(r):
				row.update(frappe.db.get_value(r.doctype, r.name, ["customer_type", "pipeline_stage", "next_action_at", "opportunity_amount", "currency"], as_dict=True) or {})
			elif gw.is_lead(r):
				row.update(frappe.db.get_value(r.doctype, r.name, ["customer_type", "intake_stage", "status"], as_dict=True) or {})
			out.append(row)
	return out


@frappe.whitelist()
def get_options() -> dict:
	_check_excom_access()
	return {"customer_types": gw.CUSTOMER_TYPES, "pipelines": gw.PIPELINES, "stages": {k: {"sales_stage": v["sales_stage"], "probability": v["probability"]} for k, v in gw.STAGES.items()}, "intake_stages": gw.INTAKE_STAGES}
