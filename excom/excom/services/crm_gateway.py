"""
crm_gateway — the ONLY module (with crm_compat.py) allowed to name native CRM doctypes
("Lead", "Opportunity", "Prospect", "Quotation") — RES-001 §4.1, P3 F3, enforced by scripts/crm-gates.sh.

Everything else in excom speaks this vocabulary:

    create_lead(payload) -> ref            get_record(ref) -> dict
    advance_stage(ref, stage)              convert(ref, target) -> ref
    set_attribution(ref, source, campaign=None, medium=None)
    link_identity(ref, identity)           list_pipeline(filters) -> [dict]
    promote_thread(thread, customer_type) -> ref

A `ref` is a frappe._dict {"doctype": ..., "name": ...}. Native helpers are reused, never
re-implemented (HLD-003 §2.1). If native CRM ever goes away, this file is what gets rewritten.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from excom.excom.services import crm_compat

LEAD = "Lead"
OPPORTUNITY = "Opportunity"
PROSPECT = "Prospect"
CUSTOMER = "Customer"
QUOTATION = "Quotation"

CUSTOMER_TYPES = ["Distributor", "Retailer", "Export Importer", "OEM", "Corporate Gifting", "Online B2C"]

# HLD-003 §6.1 — pipeline_stage superset → (types, sales_stage, probability)
STAGES: dict[str, dict] = {
	"Qualified": {"types": CUSTOMER_TYPES, "sales_stage": "Prospecting", "probability": 10},
	"Territory Check": {"types": ["Distributor", "Retailer"], "sales_stage": "Qualification", "probability": 20},
	"Pitch & Price Slab": {"types": ["Distributor"], "sales_stage": "Needs Analysis", "probability": 30},
	"Sample Kit": {"types": ["Distributor"], "sales_stage": "Value Proposition", "probability": 40},
	"Spec Confirmed": {"types": ["Export Importer", "OEM"], "sales_stage": "Needs Analysis", "probability": 30},
	"Compliance Check": {"types": ["Export Importer"], "sales_stage": "Qualification", "probability": 35},
	"Feasibility & Costing": {"types": ["OEM"], "sales_stage": "Perception Analysis", "probability": 35},
	"Curation & Mockup": {"types": ["Corporate Gifting"], "sales_stage": "Value Proposition", "probability": 35},
	"NDA / Brief": {"types": ["OEM"], "sales_stage": "Qualification", "probability": 20},
	"Sampling Loop": {"types": ["OEM"], "sales_stage": "Proposal/Price Quote", "probability": 50},
	"Quote": {"types": ["Export Importer", "OEM", "Corporate Gifting", "Distributor"], "sales_stage": "Proposal/Price Quote", "probability": 50},
	"Sample Shipment": {"types": ["Export Importer"], "sales_stage": "Identifying Decision Makers", "probability": 55},
	"Negotiation": {"types": ["Distributor", "Export Importer", "OEM"], "sales_stage": "Negotiation/Review", "probability": 70},
	"Approval": {"types": ["Corporate Gifting"], "sales_stage": "Negotiation/Review", "probability": 70},
	"Pro Forma": {"types": ["Export Importer"], "sales_stage": "Negotiation/Review", "probability": 80},
	"Advance / LC": {"types": ["Export Importer", "Corporate Gifting"], "sales_stage": "Negotiation/Review", "probability": 85},
	"Agreement & Onboarding": {"types": ["Distributor"], "sales_stage": "Negotiation/Review", "probability": 85},
	"Commercial Terms": {"types": ["OEM"], "sales_stage": "Negotiation/Review", "probability": 85},
	"Won": {"types": CUSTOMER_TYPES, "sales_stage": None, "probability": 100},
}

# Ordered per-type pipelines (HLD-003 §6.2–6.6). Retailer and Online B2C are short by design.
PIPELINES: dict[str, list[str]] = {
	"Distributor": ["Qualified", "Territory Check", "Pitch & Price Slab", "Sample Kit", "Negotiation", "Agreement & Onboarding", "Won"],
	"Retailer": ["Qualified", "Territory Check", "Won"],
	"Export Importer": ["Qualified", "Spec Confirmed", "Compliance Check", "Quote", "Sample Shipment", "Negotiation", "Pro Forma", "Advance / LC", "Won"],
	"OEM": ["Qualified", "NDA / Brief", "Spec Confirmed", "Feasibility & Costing", "Sampling Loop", "Commercial Terms", "Won"],
	"Corporate Gifting": ["Qualified", "Curation & Mockup", "Quote", "Approval", "Advance / LC", "Won"],
	"Online B2C": ["Qualified", "Won"],
}

INTAKE_STAGES = ["Captured", "Deduped", "Responded", "Classified", "Qualified"]
FIRST_TOUCH_CHANNELS = ["WhatsApp", "Email", "Call", "Instagram", "Facebook", "Web Chat", "Web Form", "Marketplace", "Manual"]


def ref(doctype: str, name: str) -> frappe._dict:
	return frappe._dict(doctype=doctype, name=name)


def lead_ref(name: str) -> frappe._dict:
	return ref(LEAD, name)


def opportunity_ref(name: str) -> frappe._dict:
	return ref(OPPORTUNITY, name)


def customer_ref(name: str) -> frappe._dict:
	return ref(CUSTOMER, name)


def is_lead(r) -> bool:
	return r and r.doctype == LEAD


def is_opportunity(r) -> bool:
	return r and r.doctype == OPPORTUNITY


def is_customer(r) -> bool:
	return r and r.doctype == CUSTOMER


def crm_doctypes() -> list[str]:
	"""Doctypes the identity panel / precedence ladder considers CRM records, in precedence order."""
	return [CUSTOMER, OPPORTUNITY, LEAD, PROSPECT]


# ─── create / read ────────────────────────────────────────────────────────────

def create_lead(payload: dict, ignore_permissions: bool = False) -> frappe._dict:
	"""
	payload keys (gateway vocabulary → native): name, email, phone, whatsapp, company_name,
	company, customer_type, territory, country, city, state, request_type, notes, owner,
	source, campaign, medium, source_reference, intake_source, first_touch_channel, first_touch_by,
	exhibition.
	"""
	name = (payload.get("name") or "").strip() or payload.get("company_name") or payload.get("email") or payload.get("phone") or "Unknown"
	doc = frappe.get_doc(
		{
			"doctype": LEAD,
			"lead_name": name,
			"company_name": payload.get("company_name"),
			"email_id": payload.get("email") or None,
			"mobile_no": payload.get("phone") or None,
			"whatsapp_no": payload.get("whatsapp") or payload.get("phone") or None,
			"company": payload.get("company") or frappe.defaults.get_global_default("company"),
			"territory": payload.get("territory") or None,
			"country": payload.get("country") or None,
			"city": payload.get("city") or None,
			"state": payload.get("state") or None,
			"request_type": payload.get("request_type") or None,
			"lead_owner": payload.get("owner") or None,
			"status": "Lead",
			"customer_type": payload.get("customer_type") or "",
			"intake_stage": "Captured",
			"intake_source": payload.get("intake_source") or None,
			"exhibition": payload.get("exhibition") or None,
			"notes": [{"note": payload["notes"]}] if payload.get("notes") and frappe.get_meta(LEAD).has_field("notes") else [],
		}
	)
	if payload.get("source"):
		crm_compat.set_attribution(doc, payload["source"], payload.get("campaign"), payload.get("medium"))
	stamp_provenance(doc, payload.get("first_touch_channel") or "Manual", payload.get("first_touch_by"), payload.get("source_reference"))
	doc.flags.ignore_permissions = ignore_permissions
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=ignore_permissions)
	return lead_ref(doc.name)


def stamp_provenance(doc, channel: str, by: str | None, reference: str | None) -> None:
	"""Write-once first-touch fields (HLD-003 §1.3 principle 5)."""
	if not doc.get("first_touch_at"):
		doc.first_touch_at = now_datetime()
		doc.first_touch_channel = channel if channel in FIRST_TOUCH_CHANNELS else "Manual"
		doc.first_touch_by = by or None
	if reference and not doc.get("source_reference"):
		doc.source_reference = reference


def get_record(r, include_meta: bool = False) -> dict:
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("read")
	out = doc.as_dict()
	out["_ref"] = {"doctype": r.doctype, "name": r.name}
	out["_attribution"] = crm_compat.get_attribution(doc)
	if r.doctype == OPPORTUNITY:
		out["_gates"] = json.loads(doc.get("gate_flags") or "{}")
		out["_pipeline"] = PIPELINES.get(doc.get("customer_type") or "", [])
	return out


def get_title(r) -> str:
	field = {LEAD: "lead_name", OPPORTUNITY: "customer_name", CUSTOMER: "customer_name", PROSPECT: "company_name"}.get(r.doctype, "name")
	return frappe.db.get_value(r.doctype, r.name, field) or r.name


def find_open_records_for_identity(identity: str) -> list[frappe._dict]:
	"""Precedence ladder (HLD-003 §4.3): Customer → Opportunity → open Lead. Returns refs, best first."""
	links = frappe.get_all(
		"Omni Identity Link",
		filters={"parent": identity, "parenttype": "Omni Identity", "linked_doctype": ["in", crm_doctypes()]},
		fields=["linked_doctype", "linked_name"],
	)
	out: list[frappe._dict] = []
	for dt in crm_doctypes():
		for ln in links:
			if ln.linked_doctype != dt or not frappe.db.exists(dt, ln.linked_name):
				continue
			if dt == LEAD and frappe.db.get_value(LEAD, ln.linked_name, "status") in ("Converted", "Do Not Contact"):
				continue
			if dt == OPPORTUNITY and frappe.db.get_value(OPPORTUNITY, ln.linked_name, "status") in ("Converted", "Lost", "Closed"):
				continue
			out.append(ref(dt, ln.linked_name))
	return out


# ─── identity ─────────────────────────────────────────────────────────────────

def link_identity(r, identity: str, role: str = "Unknown") -> None:
	"""Both directions: record.omni_identity + Omni Identity Link row (retains prior links)."""
	if frappe.get_meta(r.doctype).has_field("omni_identity"):
		frappe.db.set_value(r.doctype, r.name, "omni_identity", identity, update_modified=False)
	oi = frappe.get_doc("Omni Identity", identity)
	oi.add_link(r.doctype, r.name, role)
	oi.flags.ignore_validate = True
	oi.save(ignore_permissions=True)


# ─── stages ───────────────────────────────────────────────────────────────────

def stages_for(customer_type: str) -> list[str]:
	return PIPELINES.get(customer_type or "", [])


def map_stage_to_sales_stage(stage: str) -> tuple[str | None, int]:
	s = STAGES.get(stage)
	return (s["sales_stage"], s["probability"]) if s else (None, 0)


def write_stage(r, stage: str) -> None:
	"""Raw stage write: pipeline_stage + stage_entered_at + mapped sales_stage/probability (+ status on Won).
	Gate checks live in crm_flow.advance_stage; this only persists."""
	if r.doctype != OPPORTUNITY:
		frappe.throw(_("Stages apply to opportunities only"))
	sales_stage, probability = map_stage_to_sales_stage(stage)
	values = {"pipeline_stage": stage, "stage_entered_at": now_datetime(), "probability": probability}
	if sales_stage and frappe.db.exists("Sales Stage", sales_stage):
		values["sales_stage"] = sales_stage
	if stage == "Won":
		values["status"] = "Converted"
	frappe.db.set_value(OPPORTUNITY, r.name, values)


# ─── conversion (native helpers, never re-implemented) ────────────────────────

def convert(r, target: str) -> frappe._dict:
	"""Lead → Opportunity | Customer | Quotation; Opportunity → Quotation. Returns the new ref (saved)."""
	from erpnext.crm.doctype.lead.lead import make_customer, make_opportunity, make_quotation as lead_make_quotation
	from erpnext.crm.doctype.opportunity.opportunity import make_quotation as opp_make_quotation

	frappe.get_doc(r.doctype, r.name).check_permission("write")
	if r.doctype == LEAD and target == OPPORTUNITY:
		new = make_opportunity(r.name)
	elif r.doctype == LEAD and target == CUSTOMER:
		new = make_customer(r.name)
	elif r.doctype == LEAD and target == QUOTATION:
		new = lead_make_quotation(r.name)
	elif r.doctype == OPPORTUNITY and target == QUOTATION:
		new = opp_make_quotation(r.name)
	else:
		frappe.throw(_("Cannot convert {0} to {1}").format(r.doctype, target))
	# carry excom overlay fields forward
	src = frappe.get_doc(r.doctype, r.name)
	for f in ("customer_type", "omni_identity", "first_touch_at", "first_touch_channel", "first_touch_by", "source_reference"):
		if src.get(f) and frappe.get_meta(new.doctype).has_field(f) and not new.get(f):
			new.set(f, src.get(f))
	if new.doctype == OPPORTUNITY:
		new.pipeline_stage = "Qualified"
		new.stage_entered_at = now_datetime()
		if not new.get("opportunity_owner"):
			new.opportunity_owner = src.get("lead_owner")
	new.flags.ignore_mandatory = True
	new.insert()
	return ref(new.doctype, new.name)


def promote_thread(thread: str, customer_type: str = "", by: str | None = None) -> frappe._dict:
	"""Thread → Lead. Native make_lead_from_communication needs a Communication; excom threads are
	not Communications, so the Lead is built from the thread/identity directly (same field mapping)."""
	t = frappe.db.get_value("Excom Thread", thread, ["omni_identity", "display_name", "primary_phone", "channel", "account"], as_dict=True)
	if not t:
		frappe.throw(_("Thread not found"))
	oi = frappe.db.get_value("Omni Identity", t.omni_identity, ["display_name", "primary_email", "primary_phone", "primary_whatsapp"], as_dict=True) or frappe._dict()
	company = frappe.db.get_value("Excom Channel Account", t.account, "company") if t.account else None
	channel_map = {"whatsapp": "WhatsApp", "email": "Email", "instagram": "Instagram", "webchat": "Web Chat", "calls": "Call"}
	r = create_lead(
		{
			"name": t.display_name or oi.display_name,
			"email": oi.primary_email,
			"phone": oi.primary_phone or t.primary_phone,
			"whatsapp": oi.primary_whatsapp,
			"company": company,
			"customer_type": customer_type,
			"first_touch_channel": channel_map.get((t.channel or "").lower(), "Manual"),
			"first_touch_by": by,
			"source": "Organic " + channel_map.get((t.channel or "").lower(), "Manual"),
		}
	)
	link_identity(r, t.omni_identity)
	return r


# ─── lists ────────────────────────────────────────────────────────────────────

def list_pipeline(customer_type: str, filters: dict | None = None, limit: int = 500) -> list[dict]:
	f: dict = {"status": ["in", ["Open", "Quotation", "Replied"]]}
	if customer_type:
		f["customer_type"] = customer_type
	for k in ("company", "territory", "opportunity_owner"):
		if filters and filters.get(k):
			f[k] = filters[k]
	rows = frappe.get_list(
		OPPORTUNITY,
		filters=f,
		fields=[
			"name", "customer_name", "party_name", "opportunity_from", "customer_type", "pipeline_stage", "stage_entered_at",
			"next_action_at", "gate_flags", "opportunity_amount", "currency", "opportunity_owner", "omni_identity", "status",
			"expected_closing", "modified", "event_date", "sample_round", "incoterm",
		],
		order_by="stage_entered_at asc",
		limit=limit,
	)
	for r in rows:
		r["_ref"] = {"doctype": OPPORTUNITY, "name": r["name"]}
	return rows


def list_intake(filters: dict | None = None, limit: int = 500) -> list[dict]:
	f: dict = {"status": ["not in", ["Converted", "Do Not Contact"]], "intake_stage": ["!=", "Qualified"]}
	for k in ("company", "territory", "lead_owner", "intake_stage", "customer_type", "intake_source"):
		if filters and filters.get(k):
			f[k] = filters[k]
	rows = frappe.get_list(
		LEAD,
		filters=f,
		fields=[
			"name", "lead_name", "company_name", "email_id", "mobile_no", "status", "customer_type", "intake_stage", "intake_source",
			"first_touch_channel", "first_touch_at", "auto_ack_sent_at", "source_reference", "lead_owner", "omni_identity", "creation",
			"company", "territory", "country", "request_type",
		],
		order_by="creation desc",
		limit=limit,
	)
	for r in rows:
		r["_ref"] = {"doctype": LEAD, "name": r["name"]}
	return rows


def list_actions_due(user: str, limit: int = 200) -> list[dict]:
	"""Opportunities with next_action_at set, for the Today queue."""
	rows = frappe.get_list(
		OPPORTUNITY,
		filters={"status": ["in", ["Open", "Quotation", "Replied"]], "next_action_at": ["is", "set"], "opportunity_owner": user},
		fields=["name", "customer_name", "customer_type", "pipeline_stage", "next_action_at", "omni_identity", "opportunity_amount", "currency"],
		order_by="next_action_at asc",
		limit=limit,
	)
	for r in rows:
		r["_ref"] = {"doctype": OPPORTUNITY, "name": r["name"]}
	return rows
