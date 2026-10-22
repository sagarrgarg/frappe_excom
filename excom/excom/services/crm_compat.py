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
	# A Campaign named by naming series (v15 Selling Settings) is not found by its title: look it up
	# by the title field before creating another row with the same title.
	for candidate in ("source_name", "campaign_name"):
		if meta.has_field(candidate):
			found = frappe.db.get_value(doctype, {candidate: name}, "name")
			if found:
				return found
			break
	doc = frappe._dict({"doctype": doctype})
	if (meta.autoname or "").lower() == "prompt":
		# Named by prompt (UTM Source/Campaign/Medium in v16): the name must be given.
		doc["name"] = name
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


def fill_attribution(doctype: str, name: str, source: str | None = None, campaign: str | None = None, medium: str | None = None) -> dict:
	"""Fill attribution fields on a saved Lead/Opportunity that are still EMPTY, without a full save.

	First touch wins: a value someone already set is never overwritten. Used when the campaign of a
	lead becomes known after the lead was created (its ad mirror arrived later). Returns what was set.
	"""
	meta = frappe.get_meta(doctype)
	if meta.has_field("utm_source"):
		targets = {"utm_source": ("UTM Source", source), "utm_campaign": ("UTM Campaign", campaign), "utm_medium": ("UTM Medium", medium)}
	else:
		campaign_field = "campaign_name" if meta.has_field("campaign_name") else ("campaign" if meta.has_field("campaign") else None)
		targets = {"source": ("Lead Source", source)}
		if campaign_field:
			targets[campaign_field] = ("Campaign", campaign)
	targets = {f: t for f, t in targets.items() if t[1] and meta.has_field(f)}
	if not targets:
		return {}
	current = frappe.db.get_value(doctype, name, list(targets), as_dict=True) or {}
	values = {f: _ensure(dt, v) for f, (dt, v) in targets.items() if not current.get(f)}
	if values:
		frappe.db.set_value(doctype, name, values, update_modified=False)
	return values


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
