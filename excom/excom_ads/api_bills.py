"""Ad bill endpoints. Every one needs ads access, the bill right it acts with, and the record inside
the caller's scope (company / workspace / brand / account). Finance data also needs view_finance.

  review_queue         view_finance           bills waiting for a person, in scope
  propose_suppliers    approve_bills          what supplier masters are missing (read only)
  create_suppliers     approve_bills + Supplier create, confirm=1
  fetch_now            approve_bills          queue a fetch of one Ads Bill Source
  process_bill         view_finance + write   (re)build the draft of one bill (makers use this)
  approve_bill         approve_bills          submit the draft; never the person who prepared it
"""

import frappe
from frappe import _
from frappe.utils import cint

from excom.excom_ads import bills, permissions

QUEUE_STATUSES = ("Needs Review", "Draft Created", "Error", "Duplicate", "Fetched")


def _visible(doctype: str, name: str, ptype: str = "read"):
	if not name or not frappe.db.exists(doctype, name):
		raise frappe.DoesNotExistError(_("{0} {1} not found").format(_(doctype), name))
	doc = frappe.get_doc(doctype, name)
	if not permissions.doc_allowed(doc):
		raise frappe.PermissionError(_("Not permitted"))
	doc.check_permission(ptype)
	return doc


@frappe.whitelist()
def review_queue(company: str | None = None, status: str | None = None, limit: int = 50, start: int = 0) -> dict:
	permissions.require_access()
	permissions.require("view_finance")
	filters = dict(permissions.scope_filters("Ads Bill Document"))
	statuses = [status] if status in QUEUE_STATUSES + ("Submitted",) else list(QUEUE_STATUSES)
	filters["status"] = ["in", statuses]
	if company:
		filters["company"] = company
	limit = max(1, min(cint(limit) or 50, 200))
	fields = ["name", "status", "provider", "source_type", "document_type", "bill_no", "bill_date", "company", "supplier", "currency", "net_amount", "tax_amount", "total_amount", "purchase_invoice", "differences", "prepared_by", "pi_drafted_at", "gstr2b_status", "modified"]
	rows = frappe.get_all("Ads Bill Document", filters=filters, fields=fields, order_by="bill_date desc, modified desc", limit_start=max(0, cint(start)), limit_page_length=limit)
	can_approve = permissions.has_capability("approve_bills")
	me = frappe.session.user
	for r in rows:
		r["differences"] = [d[2:] if d.startswith("- ") else d for d in (r.differences or "").split("\n") if d.strip()]
		r["can_approve"] = bool(can_approve and r.purchase_invoice and r.status in (bills.DRAFT, bills.REVIEW) and r.prepared_by != me)
	total = frappe.db.count("Ads Bill Document", filters=filters)
	return {"rows": rows, "total": total, "can_approve": can_approve}


@frappe.whitelist()
def propose_suppliers() -> list:
	permissions.require_access()
	permissions.require("approve_bills")
	from excom.excom_ads import bill_suppliers

	return bill_suppliers.propose()


@frappe.whitelist(methods=["POST"])
def create_suppliers(keys, gstins=None, tax_withholding_category: str | None = None, confirm: int = 0) -> list:
	permissions.require_access()
	permissions.require("approve_bills")
	frappe.has_permission("Supplier", "create", throw=True)
	if not cint(confirm):
		frappe.throw(_("Confirm the proposal before suppliers are created."))
	keys = frappe.parse_json(keys) if isinstance(keys, str) else keys
	gstins = frappe.parse_json(gstins) if isinstance(gstins, str) else (gstins or {})
	if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
		frappe.throw(_("keys must be a list of legal entity keys"))
	if tax_withholding_category and not frappe.db.exists("Tax Withholding Category", tax_withholding_category):
		frappe.throw(_("Tax Withholding Category {0} not found").format(tax_withholding_category))
	from excom.excom_ads import bill_suppliers

	return bill_suppliers.create(keys, gstins if isinstance(gstins, dict) else {}, tax_withholding_category)


@frappe.whitelist(methods=["POST"])
def fetch_now(source: str) -> dict:
	permissions.require_access()
	permissions.require("approve_bills")
	src = _visible("Ads Bill Source", source)
	if not src.enabled:
		frappe.throw(_("Ads Bill Source {0} is disabled").format(src.name))
	job = bills.enqueue_source(src.name)
	return {"queued": bool(job), "already_running": job is None}


@frappe.whitelist(methods=["POST"])
def process_bill(name: str, rebuild: int = 0) -> dict:
	permissions.require_access()
	permissions.require("view_finance")
	_visible("Ads Bill Document", name, "write")
	status = bills.process(name, rebuild=bool(cint(rebuild)))
	doc = frappe.get_doc("Ads Bill Document", name)
	return {"status": status, "purchase_invoice": doc.purchase_invoice, "differences": [d[2:] for d in (doc.differences or "").split("\n") if d.strip()]}


@frappe.whitelist(methods=["POST"])
def approve_bill(name: str, acknowledge_differences: int = 0) -> dict:
	permissions.require_access()
	permissions.require("approve_bills")
	_visible("Ads Bill Document", name)
	return bills.approve(name, frappe.session.user, acknowledge_differences=bool(cint(acknowledge_differences)))
