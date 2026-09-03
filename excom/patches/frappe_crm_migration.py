"""
Frappe CRM → ERPNext-native CRM migration (one-off, idempotent, resumable).

    bench --site <site> execute excom.patches.frappe_crm_migration.run --kwargs "{'dry_run': 1}"
    bench --site <site> execute excom.patches.frappe_crm_migration.run --kwargs "{'dry_run': 0}"
    bench --site <site> execute excom.patches.frappe_crm_migration.report

What it does
- CRM Lead  → Lead      (same name, so links/URLs survive; series counters bumped).
- CRM Deal  → Opportunity (from the migrated Lead; amount/currency/closing/probability/stage).
- Comments and FCRM Notes on each CRM Lead/Deal → Comments on the new record (timeline).
- Omni Identity link by phone/email through the normal identity hooks.
- Marker: Lead.source_reference = "CRM Lead:<name>" / Opportunity.source_reference = "CRM Deal:<name>".
  Re-running skips anything already migrated; a crash mid-way is safe to resume.
Nothing in Frappe CRM is modified or deleted. Disabling the CRM bridge / uninstalling the app
stays a manual decision afterwards.
"""

import json

import frappe
from frappe.utils import cint, cstr, flt, get_datetime, now_datetime

LEAD_STATUS = {  # Frappe CRM → ERPNext Lead.status
	"New": "Lead", "Contacted": "Replied", "Nurture": "Interested", "Qualified": "Interested",
	"Unqualified": "Do Not Contact", "Junk": "Do Not Contact",
}
INTAKE_STAGE = {"New": "Captured", "Contacted": "Responded", "Nurture": "Responded", "Qualified": "Qualified", "Unqualified": "Responded", "Junk": "Responded"}
DEAL_STATUS = {"Qualification": ("Open", "Qualified"), "Negotiation": ("Open", "Negotiation"), "Ready to Close": ("Open", "Negotiation"), "Won": ("Closed", "Won"), "Lost": ("Lost", "")}
EMPLOYEES = {"1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"}


def _log(msg):
	print(msg)
	frappe.logger("excom").info(msg)


def _existing(doctype: str, marker: str) -> str | None:
	return frappe.db.get_value(doctype, {"source_reference": marker}, "name")


def _link_value(doctype: str, value: str) -> str | None:
	"""Only keep a Link value that exists natively (Lead Source names match; territories/industries may not)."""
	if not value:
		return None
	return value if frappe.db.exists(doctype, value) else None


def _copy_timeline(src_dt: str, src_name: str, dst_doc) -> int:
	n = 0
	for c in frappe.get_all("Comment", filters={"reference_doctype": src_dt, "reference_name": src_name, "comment_type": "Comment"}, fields=["content", "owner", "creation"], order_by="creation asc"):
		cm = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": dst_doc.doctype, "reference_name": dst_doc.name, "content": c.content, "comment_email": c.owner, "comment_by": frappe.utils.get_fullname(c.owner)})
		cm.flags.ignore_permissions = True
		cm.insert()
		frappe.db.set_value("Comment", cm.name, {"creation": c.creation, "owner": c.owner}, update_modified=False)
		n += 1
	if frappe.db.exists("DocType", "FCRM Note"):
		for note in frappe.get_all("FCRM Note", filters={"reference_doctype": src_dt, "reference_docname": src_name}, fields=["title", "content", "owner", "creation"], order_by="creation asc"):
			html = (f"<b>{frappe.utils.escape_html(note.title)}</b><br>" if note.title else "") + (note.content or "")
			cm = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": dst_doc.doctype, "reference_name": dst_doc.name, "content": html, "comment_email": note.owner, "comment_by": frappe.utils.get_fullname(note.owner)})
			cm.flags.ignore_permissions = True
			cm.insert()
			frappe.db.set_value("Comment", cm.name, {"creation": note.creation, "owner": note.owner}, update_modified=False)
			n += 1
	return n


def _bump_series(prefix_like: str, doctype: str):
	"""Make sure future native names never collide with migrated ones (same CRM-LEAD-.YYYY.- series)."""
	rows = frappe.db.sql(f"SELECT SUBSTRING(name, 1, LENGTH(name) - 5) AS p, MAX(CAST(RIGHT(name, 5) AS UNSIGNED)) AS m FROM `tab{doctype}` WHERE name LIKE %s GROUP BY p", (prefix_like,))
	for p, m in rows:
		cur = frappe.db.get_value("Series", p, "current") or 0
		if cint(m) > cint(cur):
			if cur == 0 and not frappe.db.exists("Series", p):
				frappe.db.sql("INSERT INTO `tabSeries` (name, current) VALUES (%s, %s)", (p, cint(m)))
			else:
				frappe.db.sql("UPDATE `tabSeries` SET current = %s WHERE name = %s", (cint(m), p))
			_log(f"series {p}: {cur} → {m}")


def _lead_values(cl) -> dict:
	status = LEAD_STATUS.get(cl.status, "Lead")
	v = {
		"doctype": "Lead",
		"salutation": _link_value("Salutation", cl.salutation),
		"first_name": cl.first_name or cl.lead_name or cl.organization or "Unknown",
		"middle_name": cl.middle_name, "last_name": cl.last_name,
		"gender": _link_value("Gender", cl.gender),
		"job_title": cl.job_title,
		"email_id": (cl.email or "").strip().lower() or None,
		"mobile_no": cl.mobile_no, "phone": cl.phone, "website": cl.website,
		"company_name": cl.organization,
		"territory": _link_value("Territory", cl.territory),
		"industry": _link_value("Industry Type", cl.industry),
		"source": _link_value("Lead Source", cl.source),
		"lead_owner": cl.lead_owner if cl.lead_owner and frappe.db.exists("User", cl.lead_owner) else None,
		"no_of_employees": cl.no_of_employees if cl.no_of_employees in EMPLOYEES else None,
		"annual_revenue": flt(cl.annual_revenue),
		"status": status,
		"intake_stage": INTAKE_STAGE.get(cl.status, "Captured"),
		"source_reference": f"CRM Lead:{cl.name}",
		"first_touch_at": cl.creation,
		"first_touch_by": cl.owner if frappe.db.exists("User", cl.owner) else None,
		"notes": None,
	}
	if cl.facebook_lead_id:
		v["source_reference"] += f" | fb:{cl.facebook_lead_id}"
	return {k: val for k, val in v.items() if val not in (None, "")}


def _insert_lead(cl, dry_run: bool) -> str | None:
	marker = f"CRM Lead:{cl.name}"
	if _existing("Lead", marker) or frappe.db.exists("Lead", cl.name):
		return None
	if dry_run:
		return cl.name
	values = _lead_values(cl)
	shared_email = None
	if values.get("email_id"):
		# ERPNext requires a unique email per Lead; Frappe CRM did not. Keep the first, note it on the rest.
		other = frappe.db.get_value("Lead", {"email_id": values["email_id"]}, "name")
		if other:
			shared_email = (values.pop("email_id"), other)
	doc = frappe.get_doc(values)
	doc.name = cl.name  # keep the identifier
	doc.flags.ignore_permissions = True
	doc.flags.ignore_email_validation = True
	doc.flags.name_set = True
	doc.flags.migrating = True
	try:
		doc.insert(set_name=cl.name)
	except frappe.DuplicateEntryError:
		doc.flags.name_set = False
		doc.name = None
		doc.insert()
	frappe.db.set_value("Lead", doc.name, {"creation": cl.creation, "modified": cl.modified, "owner": cl.owner, "modified_by": cl.modified_by}, update_modified=False)
	if shared_email:
		doc.add_comment("Comment", f"Email {frappe.utils.escape_html(shared_email[0])} not copied: already on Lead {shared_email[1]} (ERPNext keeps one Lead per email).")
	_copy_timeline("CRM Lead", cl.name, doc)
	return doc.name


def _insert_deal(cd, lead_name: str | None, dry_run: bool) -> str | None:
	marker = f"CRM Deal:{cd.name}"
	if _existing("Opportunity", marker):
		return None
	if dry_run:
		return cd.name
	status, stage = DEAL_STATUS.get(cd.status, ("Open", "Qualified"))
	party_dt, party = ("Lead", lead_name) if lead_name else (("Customer", cd.erpnext_customer) if cd.erpnext_customer and frappe.db.exists("Customer", cd.erpnext_customer) else (None, None))
	if not party:
		# no lead and no customer: create a Lead from the deal's contact so the opportunity has a party
		stub = frappe.get_doc({"doctype": "Lead", "first_name": cd.first_name or cd.organization or cd.lead_name or "Unknown", "last_name": cd.last_name, "email_id": (cd.email or "").lower() or None, "mobile_no": cd.mobile_no, "company_name": cd.organization, "status": "Opportunity", "source_reference": f"CRM Deal:{cd.name} (party)"})
		stub.flags.ignore_permissions = True; stub.flags.ignore_email_validation = True
		stub.insert()
		party_dt, party = "Lead", stub.name
	v = {
		"doctype": "Opportunity", "opportunity_from": party_dt, "party_name": party,
		"status": status, "pipeline_stage": stage or None,
		"opportunity_owner": cd.deal_owner if cd.deal_owner and frappe.db.exists("User", cd.deal_owner) else None,
		"source": _link_value("Lead Source", cd.source),
		"currency": cd.currency if cd.currency and frappe.db.exists("Currency", cd.currency) else None,
		"conversion_rate": flt(cd.exchange_rate) or 1,
		"opportunity_amount": flt(cd.deal_value) or flt(cd.expected_deal_value),
		"probability": flt(cd.probability),
		"expected_closing": cd.expected_closure_date,
		"contact_email": (cd.email or "").lower() or None, "contact_mobile": cd.mobile_no, "job_title": cd.job_title,
		"territory": _link_value("Territory", cd.territory), "industry": _link_value("Industry Type", cd.industry),
		"website": cd.website, "no_of_employees": cd.no_of_employees if cd.no_of_employees in EMPLOYEES else None,
		"annual_revenue": flt(cd.annual_revenue), "customer_name": cd.organization,
		"order_lost_reason": cd.lost_notes if status == "Lost" else None,
		"source_reference": marker, "first_touch_at": cd.creation, "stage_entered_at": cd.modified,
		"transaction_date": get_datetime(cd.creation).date(),
	}
	doc = frappe.get_doc({k: val for k, val in v.items() if val not in (None, "")})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.flags.migrating = True
	doc.insert()
	frappe.db.set_value("Opportunity", doc.name, {"creation": cd.creation, "modified": cd.modified, "owner": cd.owner, "modified_by": cd.modified_by}, update_modified=False)
	if lead_name:
		frappe.db.set_value("Lead", lead_name, {"status": "Converted" if status == "Closed" else "Opportunity", "intake_stage": "Qualified"}, update_modified=False)
	_copy_timeline("CRM Deal", cd.name, doc)
	return doc.name


def run(dry_run: int = 1, limit: int = 0, batch: int = 100) -> dict:
	"""Migrate everything not yet migrated. dry_run=1 only counts. limit>0 stops after N leads (rehearsal)."""
	dry_run = cint(dry_run)
	if not frappe.db.exists("DocType", "CRM Lead"):
		return {"error": "Frappe CRM is not installed on this site"}
	frappe.flags.in_import = True  # frappe skips gravatar lookups (one HTTP call per email) under this flag
	frappe.flags.mute_emails = True
	leads = frappe.get_all("CRM Lead", fields=["*"], order_by="creation asc")
	deals = frappe.get_all("CRM Deal", fields=["*"], order_by="creation asc")
	done_leads = skipped_leads = errors = 0
	failed: list = []
	for i, cl in enumerate(leads):
		if limit and done_leads >= limit:
			break
		if not dry_run:
			frappe.db.savepoint("crm_mig_lead")
		try:
			r = _insert_lead(cl, dry_run)
			if r:
				done_leads += 1
			else:
				skipped_leads += 1
		except Exception as e:
			errors += 1
			failed.append({"lead": cl.name, "error": cstr(e)[:200]})
			if not dry_run:
				frappe.db.rollback(save_point="crm_mig_lead")  # only this lead; earlier successes in the batch survive
			frappe.log_error(title=f"CRM migration: lead {cl.name}", message=frappe.get_traceback())
			continue
		if not dry_run and (i + 1) % batch == 0:
			frappe.db.commit()
			_log(f"leads: {done_leads} migrated, {skipped_leads} skipped, {errors} failed ({i + 1}/{len(leads)})")
	if not dry_run:
		frappe.db.commit()
	done_deals = skipped_deals = 0
	if not limit:
		for cd in deals:
			if not dry_run:
				frappe.db.savepoint("crm_mig_deal")
			try:
				lead_name = cd.lead if cd.lead and frappe.db.exists("Lead", cd.lead) else None
				r = _insert_deal(cd, lead_name, dry_run)
				if r:
					done_deals += 1
				else:
					skipped_deals += 1
			except Exception as e:
				errors += 1
				failed.append({"deal": cd.name, "error": cstr(e)[:200]})
				if not dry_run:
					frappe.db.rollback(save_point="crm_mig_deal")
				frappe.log_error(title=f"CRM migration: deal {cd.name}", message=frappe.get_traceback())
		if not dry_run:
			frappe.db.commit()
			_bump_series("CRM-LEAD-%", "Lead")
			_bump_series("CRM-OPP-%", "Opportunity")
			frappe.db.commit()
	out = {"dry_run": dry_run, "crm_leads": len(leads), "leads_migrated": done_leads, "leads_skipped_already_done": skipped_leads, "crm_deals": len(deals), "deals_migrated": done_deals, "deals_skipped": skipped_deals, "errors": errors, "failed": failed[:20]}
	_log(json.dumps(out, default=str))
	return out


def report() -> dict:
	"""How much of Frappe CRM is already on the native side."""
	return {
		"crm_leads": frappe.db.count("CRM Lead") if frappe.db.exists("DocType", "CRM Lead") else 0,
		"native_leads_from_crm": frappe.db.count("Lead", {"source_reference": ["like", "CRM Lead:%"]}),
		"crm_deals": frappe.db.count("CRM Deal") if frappe.db.exists("DocType", "CRM Deal") else 0,
		"native_opps_from_crm": frappe.db.count("Opportunity", {"source_reference": ["like", "CRM Deal:%"]}),
		"bridge_enabled": frappe.db.get_single_value("ERPNext CRM Settings", "enabled") if frappe.db.exists("DocType", "ERPNext CRM Settings") else None,
	}


def rollback_migrated(confirm: str = "") -> dict:
	"""Delete everything this migration created (marker-based). Pass confirm='yes'."""
	if confirm != "yes":
		return {"error": "pass confirm='yes'"}
	n_o = n_l = 0
	for o in frappe.get_all("Opportunity", {"source_reference": ["like", "CRM Deal:%"]}, pluck="name"):
		frappe.db.delete("Comment", {"reference_doctype": "Opportunity", "reference_name": o}); frappe.db.delete("Excom Stage Change Log", {"ref_name": o})
		frappe.delete_doc("Opportunity", o, force=True, ignore_permissions=True); n_o += 1
	for l in frappe.get_all("Lead", {"source_reference": ["like", "CRM Lead:%"]}, pluck="name") + frappe.get_all("Lead", {"source_reference": ["like", "CRM Deal:%"]}, pluck="name"):
		frappe.db.delete("Comment", {"reference_doctype": "Lead", "reference_name": l}); frappe.db.delete("ToDo", {"reference_name": l})
		for c in frappe.get_all("Dynamic Link", {"link_name": l, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c}); frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.db.delete("Omni Identity Link", {"linked_doctype": "Lead", "linked_name": l})
		frappe.delete_doc("Lead", l, force=True, ignore_permissions=True); n_l += 1
	frappe.db.commit()
	return {"opportunities_deleted": n_o, "leads_deleted": n_l}
