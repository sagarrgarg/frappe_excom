"""GST for ad bills, through India Compliance only. Nothing here re-implements GST rules: place of
supply, the tax template (CGST+SGST vs IGST, reverse charge), ITC classification and the GST
document-number rule all come from IC. Every function is safe to call when IC is not installed
(site isolation): `available()` is False and the bill goes to review instead.

IC functions used:
  overrides.transaction.get_gst_details      place of supply + taxes template + rows for the party
  utils.validate_invoice_number(throw=False)  name <= 16 chars, GST document-number characters
  utils.validate_gstin                       length + check digit
  constants.GST_TAX_TYPES / GST_RCM_TAX_TYPES gst_tax_type values IC writes on tax rows
IC itself (validate hook on Purchase Invoice) sets itc_classification ("Import Of Service" for an
Overseas supplier of services), gst_treatment and reconciliation_status.
"""

import frappe
from frappe.utils import flt


def available() -> bool:
	return "india_compliance" in frappe.get_installed_apps()


def apply_taxes(pi) -> str:
	"""Set place of supply and the GST template + rows on a draft Purchase Invoice. Returns the template ('' = none)."""
	from india_compliance.gst_india.overrides.transaction import get_gst_details

	party = frappe._dict(pi.as_dict())
	details = get_gst_details(party, "Purchase Invoice", pi.company, update_place_of_supply=True) or {}
	if details.get("place_of_supply"):
		pi.place_of_supply = details["place_of_supply"]
	template = details.get("taxes_and_charges") or ""
	pi.taxes_and_charges = template
	# Keep non-GST rows (none are added by us); replace GST rows with the template's.
	pi.set("taxes", [])
	for row in details.get("taxes") or []:
		pi.append("taxes", row)
	return template


def tax_types() -> tuple[tuple, tuple]:
	from india_compliance.gst_india.constants import GST_RCM_TAX_TYPES, GST_TAX_TYPES

	return GST_TAX_TYPES, GST_RCM_TAX_TYPES


def gst_breakup(pi) -> dict:
	"""{"cgst", "sgst", "igst", "cess", "charged", "rcm"} in the invoice currency, sign-correct.

	charged = GST the supplier charged us (what the bill's tax line must equal). Under reverse charge
	the input rows and the RCM liability rows cancel out, so charged is 0 and `rcm` is the self-assessed tax.
	"""
	gst, rcm = tax_types() if available() else ((), ())
	out = {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "cess": 0.0, "charged": 0.0, "rcm": 0.0}
	for row in pi.get("taxes") or []:
		t = row.get("gst_tax_type")
		if not t:
			continue
		amount = flt(row.tax_amount_after_discount_amount if row.get("tax_amount_after_discount_amount") is not None else row.tax_amount)
		if row.get("add_deduct_tax") == "Deduct":
			amount = -amount
		if t in rcm:
			out["rcm"] += -amount
			out["charged"] += amount
		elif t in gst:
			key = "cess" if t.startswith("cess") else t
			out[key] = out.get(key, 0.0) + amount
			out["charged"] += amount
	if pi.get("is_return"):
		out = {k: -v for k, v in out.items()}
	return {k: round(v, 2) for k, v in out.items()}


def valid_document_name(pi) -> bool:
	from india_compliance.gst_india.utils import validate_invoice_number

	return bool(validate_invoice_number(pi, throw=False))


def validate_gstin(gstin: str, label: str = "GSTIN") -> str:
	from excom.excom_ads.bill_suppliers import check_gstin

	return check_gstin(gstin, label)


# ─── GSTR-2B (read only: IC's GST Inward Supply rows, imported by IC) ─────────

def inward_supply_for(pi) -> dict | None:
	if not available() or not frappe.db.exists("DocType", "GST Inward Supply"):
		return None
	fields = ["name", "match_status", "return_period_2b", "action", "bill_no", "link_name"]
	row = frappe.db.get_value("GST Inward Supply", {"link_doctype": "Purchase Invoice", "link_name": pi.name}, fields, as_dict=True)
	if row:
		return {**row, "linked": True}
	if not (pi.get("supplier_gstin") and pi.get("company_gstin") and pi.get("bill_no")):
		return None
	rows = frappe.get_all(
		"GST Inward Supply",
		filters={"supplier_gstin": pi.supplier_gstin, "company_gstin": pi.company_gstin, "bill_no": pi.bill_no},
		fields=fields,
		limit=1,
	)
	return {**rows[0], "linked": False} if rows else None


def has_2b_data(company_gstin: str, since_period: str) -> bool:
	"""Has IC imported any GSTR-2B for this GSTIN for `since_period` (MMYYYY) or later?"""
	if not available() or not company_gstin or not frappe.db.exists("DocType", "GST Inward Supply"):
		return False
	periods = frappe.get_all(
		"GST Inward Supply",
		filters={"company_gstin": company_gstin, "is_downloaded_from_2b": 1, "return_period_2b": ["is", "set"]},
		pluck="return_period_2b",
		distinct=True,
	)
	key = lambda p: (p[2:], p[:2]) if p and len(p) == 6 else ("", "")
	return any(key(p) >= key(since_period) for p in periods)


def unlinked_2b_rows(supplier_gstins: list[str]) -> list[dict]:
	if not supplier_gstins or not available() or not frappe.db.exists("DocType", "GST Inward Supply"):
		return []
	return frappe.get_all(
		"GST Inward Supply",
		filters={
			"supplier_gstin": ["in", supplier_gstins],
			"is_downloaded_from_2b": 1,
			"is_reverse_charge": 0,
			"link_name": ["is", "not set"],
			"classification": ["in", ["B2B", "B2BA", "CDNR", "CDNRA"]],
		},
		fields=["name", "company", "company_gstin", "supplier_gstin", "supplier_name", "bill_no", "bill_date", "document_value", "return_period_2b"],
		limit=500,
	)
