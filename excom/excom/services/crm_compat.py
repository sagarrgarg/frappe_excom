"""
crm_compat — version shim for native CRM attribution (RES-001 §3.2).

v15 : Lead.source / Lead.campaign_name, Opportunity.source / Opportunity.campaign  (Lead Source, Campaign)
v16 : utm_source / utm_medium / utm_campaign on both                                (UTM Source, UTM Medium, UTM Campaign)

Nothing outside crm_gateway.py / crm_compat.py may write these fields (F4, E8).
"""

import frappe


def _ensure(doctype: str, name: str, extra: dict | None = None) -> str:
	"""Return the row named `name` in `doctype`, creating it if missing."""
	if not name:
		return ""
	if frappe.db.exists(doctype, name):
		return name
	meta = frappe.get_meta(doctype)
	doc = frappe._dict({"doctype": doctype})
	# name field differs per doctype: Lead Source.source_name, Campaign.campaign_name, UTM *.name
	for candidate in ("source_name", "campaign_name", "utm_source", "utm_campaign", "utm_medium", "title"):
		if meta.has_field(candidate):
			doc[candidate] = name
			break
	doc.update(extra or {})
	d = frappe.get_doc(doc)
	d.flags.ignore_permissions = True
	d.insert(ignore_if_duplicate=True)
	return d.name


def attribution_doctypes() -> dict:
	"""Which attribution target doctypes exist on this version."""
	if frappe.db.exists("DocType", "UTM Source"):
		return {"source": "UTM Source", "campaign": "UTM Campaign", "medium": "UTM Medium", "mode": "utm"}
	return {"source": "Lead Source", "campaign": "Campaign", "medium": None, "mode": "legacy"}


def set_attribution(doc, source: str, campaign: str | None = None, medium: str | None = None) -> None:
	"""Write provenance onto a Lead/Opportunity through whichever fields this version has."""
	meta = frappe.get_meta(doc.doctype)
	if meta.has_field("utm_source"):
		doc.utm_source = _ensure("UTM Source", source)
		if campaign and meta.has_field("utm_campaign"):
			doc.utm_campaign = _ensure("UTM Campaign", campaign)
		if medium and meta.has_field("utm_medium"):
			doc.utm_medium = _ensure("UTM Medium", medium)
		return
	if meta.has_field("source"):
		doc.source = _ensure("Lead Source", source)
	campaign_field = "campaign_name" if meta.has_field("campaign_name") else ("campaign" if meta.has_field("campaign") else None)
	if campaign and campaign_field:
		setattr(doc, campaign_field, _ensure("Campaign", campaign))


def get_attribution(doc) -> dict:
	meta = frappe.get_meta(doc.doctype)
	if meta.has_field("utm_source"):
		return {"source": doc.get("utm_source"), "campaign": doc.get("utm_campaign"), "medium": doc.get("utm_medium")}
	return {"source": doc.get("source"), "campaign": doc.get("campaign_name") or doc.get("campaign"), "medium": None}


def seed_attribution_rows(names: list[str]) -> None:
	"""N1: create the attribution rows in whichever doctype the version provides."""
	target = attribution_doctypes()["source"]
	for n in names:
		_ensure(target, n)
