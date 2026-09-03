"""
crm_flow — excom ↔ native CRM wiring (P3 §3.3 / HLD-003 §3.5).

Speaks only the gateway vocabulary. Responsibilities:
  resolve_or_create_lead · stamp provenance · evaluate_gates · advance_stage (+ stage log + thread system
  message) · on_conversion (identity links, thread re-pointing, provenance copy) · sticky assignment ·
  thread assignment sync.
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime, time_diff_in_seconds

from excom.excom.services import crm_gateway as gw

SYSTEM_SENDER = "Excom"


# ─── identity ↔ record ────────────────────────────────────────────────────────

def resolve_or_create_lead(identity: str, channel: str, payload: dict, ignore_permissions: bool = True) -> tuple[frappe._dict, bool]:
	"""HLD-003 §4.3 precedence: Customer → Opportunity → open Lead → new Lead. Returns (ref, created)."""
	existing = gw.find_open_records_for_identity(identity)
	if existing:
		best = existing[0]
		# a re-touch on an open record: keep provenance, just return it
		return best, False
	payload = dict(payload or {})
	payload.setdefault("first_touch_channel", channel)
	r = gw.create_lead(payload, ignore_permissions=ignore_permissions)
	gw.link_identity(r, identity)
	return r, True


def repoint_threads(identity: str, r) -> int:
	"""Open threads of the identity now point at this record (account_doctype/account are a Dynamic Link)."""
	# NOTE: excom threads use account_doctype/account for the *channel account*; the CRM context lives on
	# Omni Identity Link. We only post the system message here; the record is discoverable via the identity.
	return frappe.db.count("Excom Thread", {"omni_identity": identity, "status": ["in", ["Open", "Pending"]]})


def post_system_message(identity: str, text: str) -> None:
	"""Internal note on the identity's most recent open thread — the audit trail reps actually see."""
	thread = frappe.db.get_value("Excom Thread", {"omni_identity": identity, "status": ["in", ["Open", "Pending"]]}, "name", order_by="last_message_at desc")
	if not thread:
		return
	msg = frappe.get_doc(
		{
			"doctype": "Excom Message",
			"thread": thread,
			"omni_identity": identity,
			"direction": "Outbound",
			"message_type": "Text",
			"channel": frappe.db.get_value("Excom Thread", thread, "channel"),
			"content_text": text,
			"is_internal": 1,
			"delivery_status": "Sent",
			"created_by_user": frappe.session.user,
		}
	)
	msg.flags.ignore_permissions = True
	msg.insert(ignore_permissions=True)


# ─── gates (HLD-003 §6) ───────────────────────────────────────────────────────

def evaluate_gates(r) -> dict:
	"""Per-type gate evaluation → {gate: 0|1}. Server-side; Desk users are bound by the same rule."""
	doc = frappe.get_doc(r.doctype, r.name)
	ct = doc.get("customer_type") or ""
	flags: dict[str, int] = {}
	overrides = json.loads(doc.get("gate_flags") or "{}").get("_override", {})

	if ct in ("Distributor", "Retailer"):
		pins = [p.strip() for p in (doc.get("proposed_pincodes") or "").replace("\n", ",").split(",") if p.strip()]
		conflict = False
		if pins and frappe.db.exists("DocType", "Address"):
			# an active distributor Customer with a billing address in one of these pincodes
			conflict = bool(
				frappe.db.sql(
					"""SELECT 1 FROM `tabAddress` a JOIN `tabDynamic Link` dl ON dl.parent=a.name AND dl.link_doctype='Customer'
					   JOIN `tabCustomer` c ON c.name=dl.link_name AND c.customer_group='Distributor' AND c.disabled=0
					   WHERE a.pincode IN %(pins)s LIMIT 1""",
					{"pins": tuple(pins)},
				)
			)
		flags["territory"] = 0 if conflict else 1
	if ct == "Distributor":
		cust = gw.find_open_records_for_identity(doc.get("omni_identity")) if doc.get("omni_identity") else []
		customer = next((c for c in cust if gw.is_customer(c)), None)
		gstin = bool(customer and frappe.db.get_value("Customer", customer.name, "gstin")) if frappe.get_meta("Customer").has_field("gstin") else False
		agreement = bool(frappe.db.exists("File", {"attached_to_doctype": r.doctype, "attached_to_name": r.name}))
		flags["onboarding"] = 1 if (gstin and agreement) else 0
	if ct == "Export Importer":
		flags["compliance"] = 1 if (doc.get("country") and doc.get("incoterm")) else 0
		flags["payment"] = 1 if (doc.get("currency") and doc.get("incoterm")) else 0
	if ct == "OEM":
		flags["feasibility"] = 1 if (doc.get("items") and doc.get("opportunity_amount")) else 0
		flags["sampling"] = 1 if int(doc.get("sample_round") or 0) <= 3 else 0
	if ct == "Corporate Gifting":
		ev = doc.get("event_date")
		flags["date_feasible"] = 1 if (ev and frappe.utils.date_diff(ev, frappe.utils.nowdate()) >= 7) else 0
	for k, v in overrides.items():
		if v:
			flags[k] = 1
	return flags


REQUIRED_GATES = {
	# stage → gates that must be 1 to enter it
	"Pitch & Price Slab": ["territory"],
	"Sample Kit": ["territory"],
	"Agreement & Onboarding": ["territory"],
	"Quote": [],
	"Sample Shipment": ["compliance"],
	"Pro Forma": ["compliance"],
	"Advance / LC": ["payment", "date_feasible"],
	"Sampling Loop": ["feasibility"],
	"Commercial Terms": ["feasibility", "sampling"],
	"Curation & Mockup": ["date_feasible"],
	"Won": ["onboarding", "payment", "sampling", "date_feasible", "territory"],
}


def gate_status(r) -> dict:
	doc = frappe.db.get_value(r.doctype, r.name, ["customer_type", "pipeline_stage", "gate_flags"], as_dict=True) or frappe._dict()
	flags = evaluate_gates(r)
	frappe.db.set_value(r.doctype, r.name, "gate_flags", json.dumps({**flags, "_override": json.loads(doc.gate_flags or "{}").get("_override", {})}), update_modified=False)
	stages = gw.stages_for(doc.customer_type)
	blocked = {}
	for st in stages:
		missing = [g for g in REQUIRED_GATES.get(st, []) if g in flags and not flags[g]]
		if missing:
			blocked[st] = missing
	return {"flags": flags, "stages": stages, "current": doc.pipeline_stage, "blocked": blocked}


def override_gate(r, gate: str, reason: str) -> None:
	"""Manager override, always logged as a comment naming the user (HLD-003 §11.3)."""
	roles = set(frappe.get_roles())
	if not roles & {"System Manager", "Excom Manager", "Sales Manager"}:
		frappe.throw(_("Only managers can override a gate"), frappe.PermissionError)
	if not reason:
		frappe.throw(_("A reason is required to override a gate"))
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("write")
	flags = json.loads(doc.get("gate_flags") or "{}")
	flags.setdefault("_override", {})[gate] = 1
	frappe.db.set_value(r.doctype, r.name, "gate_flags", json.dumps(flags), update_modified=False)
	doc.add_comment("Comment", _("Gate <b>{0}</b> overridden by {1}: {2}").format(gate, frappe.session.user, frappe.utils.escape_html(reason)))


# ─── stage advance ────────────────────────────────────────────────────────────

def advance_stage(r, stage: str, note: str = "") -> dict:
	doc = frappe.get_doc(r.doctype, r.name)
	doc.check_permission("write")
	ct = doc.get("customer_type") or ""
	if not ct:
		frappe.throw(_("Classify the record (customer type) before moving it through a pipeline"))
	if stage not in gw.stages_for(ct):
		frappe.throw(_("{0} is not a stage of the {1} pipeline").format(stage, ct))
	status = gate_status(r)
	missing = status["blocked"].get(stage)
	if missing:
		frappe.throw(_("Blocked by gate: {0}").format(", ".join(missing)), title=_("Gate not cleared"))
	prev, prev_at = doc.get("pipeline_stage"), doc.get("stage_entered_at")
	gw.write_stage(r, stage)
	now = now_datetime()
	frappe.get_doc(
		{
			"doctype": "Excom Stage Change Log",
			"ref_doctype": r.doctype,
			"ref_name": r.name,
			"from_stage": prev or "",
			"to_stage": stage,
			"from_date": prev_at,
			"to_date": now,
			"duration": time_diff_in_seconds(now, prev_at) if prev_at else 0,
			"log_owner": frappe.session.user,
			"note": note,
		}
	).insert(ignore_permissions=True)
	if doc.get("omni_identity"):
		post_system_message(doc.omni_identity, _("Stage: {0} → {1}").format(prev or "—", stage))
	if stage == "Won" and doc.get("omni_identity"):
		post_system_message(doc.omni_identity, _("Opportunity {0} won").format(r.name))
	return gate_status(r)


# ─── conversion bookkeeping (HLD-003 §8.2) ────────────────────────────────────

def on_conversion(src, new) -> None:
	identity = frappe.db.get_value(src.doctype, src.name, "omni_identity") if frappe.get_meta(src.doctype).has_field("omni_identity") else None
	if not identity:
		# fall back to the identity link table
		row = frappe.db.get_value("Omni Identity Link", {"linked_doctype": src.doctype, "linked_name": src.name}, "parent")
		identity = row
	if identity:
		gw.link_identity(new, identity)  # prior link retained (history)
		post_system_message(identity, _("{0} {1} converted to {2} {3}").format(src.doctype, gw.get_title(src), new.doctype, new.name))
	if gw.is_customer(new):
		s = frappe.get_doc(src.doctype, src.name)
		vals = {f: s.get(f) for f in ("customer_type", "first_touch_at", "first_touch_channel", "source_reference") if s.get(f)}
		if vals:
			frappe.db.set_value(new.doctype, new.name, vals, update_modified=False)


def convert(r, target: str) -> frappe._dict:
	"""Gate: an Opportunity cannot be created from an unclassified Lead (HLD-003 R3)."""
	if gw.is_lead(r) and target == gw.OPPORTUNITY and not frappe.db.get_value(r.doctype, r.name, "customer_type"):
		frappe.throw(_("Set the customer type before creating an opportunity"))
	new = gw.convert(r, target)
	on_conversion(r, new)
	return new


# ─── sticky assignment + thread sync (HLD-003 §7.3/7.4) ───────────────────────

def sticky_owner(identity: str) -> str | None:
	"""Last owner of any prior record on this identity, if still an active user."""
	if not identity:
		return None
	links = frappe.get_all("Omni Identity Link", filters={"parent": identity, "linked_doctype": ["in", gw.crm_doctypes()]}, fields=["linked_doctype", "linked_name"])
	owners = []
	for ln in links:
		f = {gw.LEAD: "lead_owner", gw.OPPORTUNITY: "opportunity_owner", gw.CUSTOMER: "account_manager"}.get(ln.linked_doctype)
		if f and frappe.get_meta(ln.linked_doctype).has_field(f):
			v = frappe.db.get_value(ln.linked_doctype, ln.linked_name, [f, "modified"], as_dict=True)
			if v and v.get(f):
				owners.append((v.modified, v.get(f)))
	if not owners:
		return None
	owners.sort(reverse=True)
	user = owners[0][1]
	return user if frappe.db.get_value("User", user, "enabled") else None


def _assign(doc, user: str) -> None:
	from frappe.desk.form.assign_to import add
	try:
		add({"assign_to": [user], "doctype": doc.doctype, "name": doc.name, "description": _("Sticky assignment: prior owner on this contact")}, ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Excom sticky assignment failed", message=frappe.get_traceback())


def sync_thread_owner(doc) -> None:
	"""When the CRM record owner changes, the conversation follows (§7.4). One direction only."""
	identity = doc.get("omni_identity")
	owner = doc.get("lead_owner") if doc.doctype == gw.LEAD else doc.get("opportunity_owner")
	if not identity or not owner:
		return
	team = frappe.db.get_value("Excom Team Member", {"user": owner}, "parent")
	frappe.db.sql(
		"""UPDATE `tabExcom Thread` SET assigned_to=%(u)s, assigned_team=COALESCE(%(t)s, assigned_team)
		   WHERE omni_identity=%(oi)s AND status IN ('Open','Pending') AND COALESCE(assigned_to,'') <> %(u)s""",
		{"u": owner, "t": team, "oi": identity},
	)


# ─── doc_events ───────────────────────────────────────────────────────────────

def on_lead_created(doc, method=None):
	try:
		if not doc.get("first_touch_at"):
			gw.stamp_provenance(doc, "Manual", frappe.session.user if frappe.session.user != "Guest" else None, None)
			frappe.db.set_value(doc.doctype, doc.name, {"first_touch_at": doc.first_touch_at, "first_touch_channel": doc.first_touch_channel, "first_touch_by": doc.first_touch_by, "intake_stage": doc.get("intake_stage") or "Captured"}, update_modified=False)
		# identity_hooks.on_entity_created ran first and linked an identity; mirror it onto the record
		if not doc.get("omni_identity"):
			oi = frappe.db.get_value("Omni Identity Link", {"linked_doctype": gw.LEAD, "linked_name": doc.name}, "parent")
			if oi:
				frappe.db.set_value(doc.doctype, doc.name, "omni_identity", oi, update_modified=False)
				doc.omni_identity = oi
		prior = sticky_owner(doc.get("omni_identity")) if not doc.get("_assign") else None
		if prior and prior != doc.get("lead_owner"):
			_assign(doc, prior)
	except Exception:
		frappe.log_error(title=f"Excom crm_flow.on_lead_created failed for {doc.name}", message=frappe.get_traceback())


def on_lead_updated(doc, method=None):
	try:
		if doc.get("customer_type") and (doc.get("intake_stage") or "") in ("", "Captured", "Deduped", "Responded"):
			frappe.db.set_value(doc.doctype, doc.name, "intake_stage", "Classified", update_modified=False)
		if doc.has_value_changed("lead_owner"):
			sync_thread_owner(doc)
	except Exception:
		frappe.log_error(title=f"Excom crm_flow.on_lead_updated failed for {doc.name}", message=frappe.get_traceback())


def on_opportunity_created(doc, method=None):
	try:
		vals = {}
		if not doc.get("pipeline_stage"):
			vals["pipeline_stage"] = "Qualified"
			vals["stage_entered_at"] = now_datetime()
		if not doc.get("omni_identity") and doc.get("opportunity_from") == gw.LEAD and doc.get("party_name"):
			oi = frappe.db.get_value(gw.LEAD, doc.party_name, "omni_identity")
			if oi:
				vals["omni_identity"] = oi
		if not doc.get("customer_type") and doc.get("opportunity_from") == gw.LEAD and doc.get("party_name"):
			ct = frappe.db.get_value(gw.LEAD, doc.party_name, "customer_type")
			if ct:
				vals["customer_type"] = ct
		if vals:
			frappe.db.set_value(doc.doctype, doc.name, vals, update_modified=False)
			for k, v in vals.items():
				doc.set(k, v)
		if doc.get("omni_identity"):
			oi = frappe.get_doc("Omni Identity", doc.omni_identity)
			oi.add_link(gw.OPPORTUNITY, doc.name, "Unknown")
			oi.flags.ignore_validate = True
			oi.save(ignore_permissions=True)
			if doc.get("opportunity_from") == gw.LEAD:
				frappe.db.set_value(gw.LEAD, doc.party_name, {"status": gw.OPPORTUNITY, "intake_stage": "Qualified"}, update_modified=False)
		prior = sticky_owner(doc.get("omni_identity")) if not doc.get("_assign") else None
		if prior:
			_assign(doc, prior)
	except Exception:
		frappe.log_error(title=f"Excom crm_flow.on_opportunity_created failed for {doc.name}", message=frappe.get_traceback())


def on_opportunity_updated(doc, method=None):
	try:
		if doc.has_value_changed("opportunity_owner"):
			sync_thread_owner(doc)
		if doc.has_value_changed("pipeline_stage") and doc.get("pipeline_stage") and not frappe.flags.in_excom_stage_write:
			# Stage changed from Desk (not through advance_stage): keep the log + mapping consistent.
			prev = doc.get_doc_before_save()
			prev_stage, prev_at = (prev.get("pipeline_stage"), prev.get("stage_entered_at")) if prev else (None, None)
			sales_stage, prob = gw.map_stage_to_sales_stage(doc.pipeline_stage)
			vals = {"stage_entered_at": now_datetime(), "probability": prob}
			if sales_stage and frappe.db.exists("Sales Stage", sales_stage):
				vals["sales_stage"] = sales_stage
			frappe.db.set_value(doc.doctype, doc.name, vals, update_modified=False)
			now = now_datetime()
			frappe.get_doc({"doctype": "Excom Stage Change Log", "ref_doctype": doc.doctype, "ref_name": doc.name, "from_stage": prev_stage or "", "to_stage": doc.pipeline_stage, "from_date": prev_at, "to_date": now, "duration": time_diff_in_seconds(now, prev_at) if prev_at else 0, "log_owner": frappe.session.user}).insert(ignore_permissions=True)
		if doc.has_value_changed("status") and doc.status == "Lost" and doc.get("omni_identity"):
			# HLD-003 §8.3: thread → Pending, nurture in 90 days
			frappe.db.sql("UPDATE `tabExcom Thread` SET status='Pending' WHERE omni_identity=%s AND status='Open'", doc.omni_identity)
			frappe.db.set_value(doc.doctype, doc.name, "next_action_at", frappe.utils.add_days(now_datetime(), 90), update_modified=False)
	except Exception:
		frappe.log_error(title=f"Excom crm_flow.on_opportunity_updated failed for {doc.name}", message=frappe.get_traceback())


def claim_lead_for_identity(identity: str, user: str) -> str | None:
	"""Talk = claim: the open Lead on this identity with no active owner goes to `user` (lead_owner + ToDo)."""
	for r in gw.find_open_records_for_identity(identity):
		if not gw.is_lead(r):
			continue
		owner = frappe.db.get_value(r.doctype, r.name, "lead_owner")
		if owner and frappe.db.get_value("User", owner, "enabled"):
			return None
		frappe.db.set_value(r.doctype, r.name, "lead_owner", user, update_modified=False)
		doc = frappe.get_doc(r.doctype, r.name)
		if user not in (doc.get("_assign") or ""):
			_assign(doc, user)
		return r.name
	return None
