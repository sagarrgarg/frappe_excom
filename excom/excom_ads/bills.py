"""Ad bills → ERPNext Purchase Invoices (India Compliance for GST).

    platform invoice API (bill_sources) ─┐
                                         ├─► Ads Bill Document ─► process() ─► draft Purchase Invoice
    finance uploads a PDF + types header ┘                         │                (or Needs Review / Duplicate)
                                                                   └─► auto-submit only if every rule holds
                                                                       and Ads Settings allows it; otherwise
                                                                       approve() by a second person.

Conservative by design: anything uncertain lands in the review queue with the reasons listed in
`differences`. A draft is built whenever it can be (so finance reviews a concrete document); a bill
that cannot be turned into a correct draft (no account map, unknown supplier, missing original for a
credit memo …) gets no draft at all.

Building the draft (`_build`):
  supplier / company     account map(s) of the billed ad accounts (or the map chosen by finance);
                         all must agree on company, supplier and billing address.
  supplier check         billing entity (Meta `entity`) → catalogue legal entity → must be the map's
                         supplier; GSTIN on the bill must be the supplier's.
  bill_no / bill_date    the platform invoice number; unique per supplier and fiscal year
                         (a second use → Duplicate, nothing created).
  lines                  one per campaign (Meta) or per account summary (Google); the rounding gap
                         to the invoice's pre-tax amount goes on the largest line.
  GST                    India Compliance `get_gst_details` from company GSTIN (billing address)
                         and supplier GSTIN: CGST+SGST in-state, IGST otherwise; Overseas supplier
                         → is_reverse_charge = 1 and IC's reverse-charge template; IC sets
                         itc_classification ("Import Of Service").
  TDS                    the map's Tax Withholding Category, else the supplier's. ERPNext computes
                         it on the value excluding GST (category basis "Net Total"). Never a
                         hard-coded rate.
  naming                 Ads Settings bill series (≤ 16 characters: valid GST document number).
  credit memo            debit note: is_return = 1, return_against the original's Purchase Invoice.
  stale draft            a re-check compares the draft with the current map / supplier / TDS
                         (`context_changes`): an untouched draft is rebuilt, an edited one is a
                         difference (never auto-submitted).
  submit guard           `before_purchase_invoice_submit` (doc_events) refuses to submit a Purchase
                         Invoice linked from an Ads Bill Document outside approve() / auto-submit.

Release 2 extension point: `EXTRACTORS` (AI extraction of a manual bill's PDF into its header).
Nothing calls an extractor in release 1.
"""

import hashlib
import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

from excom.excom_ads import alerts, bill_gst, bill_suppliers
from excom.excom_ads.settings import get_settings

FETCHED, DRAFT, SUBMITTED, REVIEW, DUPLICATE, ERROR = "Fetched", "Draft Created", "Submitted", "Needs Review", "Duplicate", "Error"
OPEN_STATUSES = (FETCHED, DRAFT, REVIEW, ERROR, DUPLICATE)
TOLERANCE = 1.0  # "to the rupee": a difference of one rupee or more is a difference
SERVICE_SAC = "998365"
ACCOUNT_KEY_TYPES = ("Ad Account", "WhatsApp Business Account", "Google Ads Customer")
HEADER_KEY_TYPES = ("Billing Setup", "Payments Profile")
FIGURE_FIELDS = ("bill_no", "bill_date", "document_type", "net_amount", "tax_amount", "total_amount", "cgst_amount", "sgst_amount", "igst_amount", "currency", "supplier_gstin", "company_gstin_on_bill", "original_bill_no", "account_map", "platform_account_ids", "billing_entity")

# Release 2: {"name": callable(bill_doc) -> dict of header fields}. A manual bill's PDF can be read
# by an extractor (ai_interface) into the same header fields finance types today. The result must go
# through process() like typed figures, and extraction never makes a bill eligible for auto-submit.
EXTRACTORS: dict = {}


class BillProblem(Exception):
	"""The bill cannot become a correct draft. Message is shown to finance."""


def bill_document_name(provider: str, platform_invoice_id: str) -> str:
	return hashlib.sha1(f"bill|{provider}|{platform_invoice_id}".encode()).hexdigest()


def _lines_text(items) -> str:
	return "\n".join(f"- {d}" for d in items)


def _money(v, currency="INR") -> str:
	return f"{currency} {flt(v):,.2f}"


def _norm_key(provider: str, key) -> str:
	s = str(key or "").strip()
	if provider == "Meta" and s.startswith("act_"):
		s = s[4:]
	if provider == "Google" or s.replace("-", "").isdigit():
		s = s.replace("-", "")
	return s


# ─── store ────────────────────────────────────────────────────────────────────

def upsert_from_api(bill: dict, source) -> tuple[str, bool]:
	"""Create or refresh the Ads Bill Document of one platform invoice. Idempotent on the platform's
	invoice id. Returns (name, needs_processing)."""
	name = bill_document_name(bill["provider"], bill["platform_invoice_id"])
	raw = json.dumps(bill.get("raw") or {}, sort_keys=True, default=str)[:500000]
	header = {
		"provider": bill["provider"],
		"document_type": bill["document_type"],
		"bill_no": bill["bill_no"],
		"bill_date": bill["bill_date"] or nowdate(),
		"due_date": bill.get("due_date"),
		"service_start": bill.get("service_start"),
		"service_end": bill.get("service_end"),
		"billing_entity": (bill.get("billing_entity") or "")[:140],
		"platform_account_ids": "\n".join(bill.get("account_ids") or []),
		"currency": bill.get("currency") or "INR",
		"net_amount": flt(bill.get("net_amount")),
		"tax_amount": flt(bill.get("tax_amount")),
		"total_amount": flt(bill.get("total_amount")),
	}
	if bill.get("original_invoice_ids"):
		header["original_bill_no"] = bill["original_invoice_ids"][0]
	live = {
		"amount_due": flt(bill.get("amount_due")) if bill.get("amount_due") is not None else 0,
		"payment_status": (bill.get("payment_status") or "")[:140],
		"pdf_url": bill.get("pdf_url") or "",
		"raw_payload": raw,
		"fetched_at": now_datetime(),
		"billing_setup": bill.get("billing_setup") or "",
	}
	lines = [{**ln, "currency": header["currency"]} for ln in bill.get("lines") or []]

	exists = frappe.db.get_value("Ads Bill Document", name, "name", for_update=True)
	if not exists:
		doc = frappe.new_doc("Ads Bill Document")
		doc.update(header)
		doc.update(live)
		doc.update({"source_type": "API", "extraction_method": "API", "status": FETCHED, "bill_source": source.name, "company": source.company, "workspace": source.workspace, "platform_invoice_id": bill["platform_invoice_id"]})
		for ln in lines:
			doc.append("lines", ln)
		doc.flags.from_pipeline = True
		doc.flags.ignore_permissions = True
		doc.insert(set_name=name)
		return name, True

	doc = frappe.get_doc("Ads Bill Document", name)
	changed = [f for f, v in header.items() if f not in ("bill_date", "due_date", "service_start", "service_end", "billing_entity", "platform_account_ids") and _differs(doc.get(f), v)]
	doc.update(live)
	doc.flags.from_pipeline = True
	doc.flags.ignore_permissions = True
	if changed and doc.purchase_invoice:
		# The platform re-issued figures after we built a draft: never overwrite them silently. They
		# wait in platform_update; Rebuild applies them (process(rebuild=True)).
		doc.platform_update = json.dumps({"header": header, "lines": lines}, sort_keys=True, default=str)
		if doc.status != SUBMITTED:
			doc.status = REVIEW
			doc.differences = _lines_text([platform_update_note(doc)])
		doc.save()
		alerts.raise_alert("Bill Needs Review", "High", _("{0} bill {1} changed on the platform").format(doc.provider, doc.bill_no), _("Open the Ads Bill Document to compare."), ref=doc, dedupe_key=f"bill-review:{doc.name}")
		return name, False
	if not changed and doc.platform_update and doc.status != SUBMITTED:
		doc.platform_update = None  # the platform went back to the figures the draft was built from
	if changed and not doc.purchase_invoice:
		doc.update(header)
		doc.set("lines", [])
		for ln in lines:
			doc.append("lines", ln)
	doc.save()
	# Re-check anything not settled: a map fixed since the last run clears the review by itself.
	return name, bool(changed) or doc.status in (FETCHED, ERROR, REVIEW, DUPLICATE)


def platform_update_note(doc) -> str:
	new = (json.loads(doc.platform_update or "{}") or {}).get("header") or {}
	changed = [f for f, v in new.items() if f in FIGURE_FIELDS and _differs(doc.get(f), v)]
	return _("The platform changed {0} after the draft was built (now {1}). Rebuild to use the new figures.").format(", ".join(changed) or _("the bill"), ", ".join(f"{f} {new[f]}" for f in changed))


def _apply_platform_update(doc) -> None:
	data = json.loads(doc.platform_update or "{}") or {}
	doc.update(data.get("header") or {})
	doc.set("lines", [])
	for ln in data.get("lines") or []:
		doc.append("lines", ln)
	doc.platform_update = None


def _api_notes(doc) -> list[str]:
	"""Findings that come from the platform record itself and must survive every re-check."""
	if doc.source_type != "API" or not doc.raw_payload:
		return []
	raw = json.loads(doc.raw_payload or "{}") or {}
	out = []
	if not (raw.get("invoice_date") or raw.get("issueDate")):
		out.append(_("The platform gave no invoice date; check the bill date against the PDF."))
	if raw.get("replacedInvoices"):
		out.append(_("Replaces earlier invoice(s) {0}: check they were credited before approving.").format(", ".join(str(r).split("/")[-1] for r in raw["replacedInvoices"])))
	return out


def _differs(a, b) -> bool:
	if isinstance(b, (int, float)) or isinstance(a, (int, float)):
		return abs(flt(a) - flt(b)) >= 0.005
	return str(a or "") != str(b or "")


# ─── process ──────────────────────────────────────────────────────────────────

def process(name: str, rebuild: bool = False) -> str:
	"""Build (or re-check) the draft Purchase Invoice of one bill document. Returns the new status.

	Errors in one bill never stop a fetch run: anything unexpected marks the bill Error.
	"""
	frappe.db.get_value("Ads Bill Document", name, "name", for_update=True)
	doc = frappe.get_doc("Ads Bill Document", name)
	if doc.status == SUBMITTED:
		return doc.status
	doc.flags.from_pipeline = True
	doc.flags.ignore_permissions = True
	sp = f"bill_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(sp)
	try:
		status = _process(doc, rebuild)
	except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
		raise  # the database already rolled the whole transaction back; the caller retries later
	except BillProblem as e:
		frappe.db.rollback(save_point=sp)
		doc.reload()
		doc.flags.from_pipeline = True
		doc.flags.ignore_permissions = True
		status = _set_status(doc, REVIEW, [str(e)])
	except Exception as e:
		frappe.db.rollback(save_point=sp)
		frappe.clear_messages()
		doc.reload()
		doc.flags.from_pipeline = True
		doc.flags.ignore_permissions = True
		doc.last_error = frappe.utils.strip_html(str(e))[:1000] or type(e).__name__
		status = _set_status(doc, ERROR, [_("Could not build the Purchase Invoice: {0}").format(doc.last_error)])
		frappe.log_error(title=f"Excom Ads: ad bill {doc.name} failed", message=frappe.get_traceback())
	return status


def _set_status(doc, status, differences) -> str:
	doc.status = status
	doc.differences = _lines_text(differences) if differences else ""
	if status != ERROR:
		doc.last_error = ""
	doc.save()
	key = f"bill-review:{doc.name}"
	if status in (REVIEW, ERROR, DUPLICATE):
		title = _("{0} bill {1}: {2}").format(doc.provider, doc.bill_no, _(status))
		# Amounts stay on the finance-only bill document; the alert says only how many findings.
		alerts.raise_alert("Bill Needs Review", "High" if status == ERROR else "Medium", title, _("{0} finding(s). Open the Ads Bill Document.").format(len(differences or [])), ref=doc, dedupe_key=key)
	else:
		alerts.resolve_alert(key, "Bill accepted")
	return status


def _process(doc, rebuild: bool) -> str:
	pi = None
	if doc.purchase_invoice:
		if not frappe.db.exists("Purchase Invoice", doc.purchase_invoice):
			doc.purchase_invoice = None
			doc.return_against = None
		else:
			pi = frappe.get_doc("Purchase Invoice", doc.purchase_invoice)
			if pi.docstatus == 1:
				doc.status = SUBMITTED
				doc.save()
				return SUBMITTED
			if pi.docstatus == 2:
				if not rebuild:
					raise BillProblem(_("Purchase Invoice {0} was cancelled. Use Rebuild to make a new draft.").format(pi.name))
				doc.purchase_invoice = None
				pi = None
			elif rebuild:
				if get_datetime(pi.modified) > get_datetime(doc.pi_drafted_at or pi.modified):
					raise BillProblem(_("Draft {0} was edited by {1}; delete it or approve it as is before rebuilding.").format(pi.name, pi.modified_by))
				frappe.delete_doc("Purchase Invoice", pi.name, ignore_permissions=True, force=True)
				doc.purchase_invoice = None
				pi = None

	if doc.platform_update and pi is None:
		_apply_platform_update(doc)  # nothing built on the old figures any more
	ctx = _context(doc)
	if pi is not None and not _edited(doc, pi) and context_changes(pi, ctx):
		# The account map (company, supplier, billing GSTIN, TDS …) changed since this draft was built
		# and nobody has touched the draft: build it again from the current map.
		frappe.delete_doc("Purchase Invoice", pi.name, ignore_permissions=True, force=True)
		doc.purchase_invoice = None
		doc.return_against = None
		pi = None
		if doc.platform_update:
			_apply_platform_update(doc)
			ctx = _context(doc)
	ctx.differences.extend(_api_notes(doc))
	if doc.platform_update:
		ctx.differences.append(platform_update_note(doc))
	if ctx.duplicate:
		doc.save()
		return _set_status(doc, DUPLICATE, ctx.duplicate)
	if pi is None:
		pi = _build(doc, ctx)
		doc.purchase_invoice = pi.name
		doc.return_against = pi.get("return_against")
		doc.pi_drafted_at = pi.modified
	else:
		_lines(doc, ctx)  # the same line findings as when the draft was built
	differences = ctx.differences + check(doc, pi, ctx)
	if not differences and _auto_submit_allowed(doc, pi):
		sp = f"bill_submit_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(sp)
		try:
			pi.flags.ignore_permissions = True
			with pipeline_submit(pi):
				pi.submit()
		except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			raise
		except Exception as e:
			# The draft stays; a person sees why ERPNext refused to submit it.
			frappe.db.rollback(save_point=sp)
			frappe.clear_messages()
			differences = [_("Automatic submission failed: {0}").format(frappe.utils.strip_html(str(e))[:500] or type(e).__name__)]
		else:
			doc.auto_submitted = 1
			doc.approved_by = None
			doc.approved_at = now_datetime()
			return _set_status(doc, SUBMITTED, [])
	return _set_status(doc, REVIEW if differences else DRAFT, differences)


def _auto_submit_allowed(doc, pi) -> bool:
	"""The switch is on, the figures came from the platform's API untouched, and nobody edited the draft."""
	if not cint(get_settings().bills_auto_submit) or doc.source_type != "API" or doc.prepared_by:
		return False
	return not (doc.pi_drafted_at and get_datetime(pi.modified) > get_datetime(doc.pi_drafted_at))


def _edited(doc, pi) -> bool:
	"""Changed by anyone since the pipeline drafted it (an unknown drafting time counts as edited)."""
	return not doc.pi_drafted_at or get_datetime(pi.modified) > get_datetime(doc.pi_drafted_at)


# ─── submit guard ─────────────────────────────────────────────────────────────

SUBMIT_FLAG = "excom_ads_bill_submit"


@contextmanager
def pipeline_submit(pi):
	"""Mark the one Purchase Invoice the pipeline itself submits (auto-submit or approve)."""
	previous = frappe.flags.get(SUBMIT_FLAG)
	frappe.flags[SUBMIT_FLAG] = pi.name
	try:
		yield
	finally:
		frappe.flags[SUBMIT_FLAG] = previous


def before_purchase_invoice_submit(pi, method=None):
	"""doc_events hook. A Purchase Invoice drafted from an Ads Bill Document is submitted only by the
	pipeline (rules checked) or approve() (maker-checker), never from the form or the REST API.
	Inert for every other Purchase Invoice."""
	if pi.is_new() or not frappe.db.table_exists("Ads Bill Document"):
		return
	bill = frappe.db.get_value("Ads Bill Document", {"purchase_invoice": pi.name}, "name")
	if not bill or frappe.flags.get(SUBMIT_FLAG) == pi.name:
		return
	frappe.throw(_("Purchase Invoice {0} was drafted from ad bill {1}. Approve it from the bill review (a second person checks it); it cannot be submitted directly.").format(pi.name, bill), frappe.PermissionError, title=_("Approve the ad bill"))


# ─── context: maps, supplier, duplicate ───────────────────────────────────────

def _account_ids(doc) -> list[str]:
	ids = {_norm_key(doc.provider, a) for a in (doc.platform_account_ids or "").split("\n") if a.strip()}
	ids |= {_norm_key(doc.provider, ln.platform_account_id) for ln in doc.lines if ln.platform_account_id}
	return sorted(ids)


def find_map(provider: str, key: str, key_types=ACCOUNT_KEY_TYPES) -> dict | None:
	key = _norm_key(provider, key)
	if not key:
		return None
	rows = frappe.get_all("Ads Bill Account Map", filters={"provider": provider, "enabled": 1, "key_type": ["in", list(key_types)]}, fields=["*"])
	match = [r for r in rows if _norm_key(provider, r.platform_key) == key]
	return match[0] if match else None


def _context(doc):
	ctx = frappe._dict(differences=[], duplicate=None, maps={})
	header = None
	if doc.account_map:
		header = frappe.db.get_value("Ads Bill Account Map", doc.account_map, "*", as_dict=True)
		if not header or not header.enabled:
			raise BillProblem(_("Account map {0} is missing or disabled.").format(doc.account_map))
	if not header and doc.billing_setup:
		header = find_map(doc.provider, doc.billing_setup, HEADER_KEY_TYPES)
	if not header and doc.provider == "Google" and doc.raw_payload:
		profile = (json.loads(doc.raw_payload or "{}") or {}).get("paymentsProfileId")
		header = find_map(doc.provider, profile, HEADER_KEY_TYPES) if profile else None
	ids = _account_ids(doc)
	missing = []
	for a in ids:
		m = find_map(doc.provider, a)
		if m:
			ctx.maps[a] = m
		elif not header:
			missing.append(a)
	if missing:
		raise BillProblem(_("No Ads Bill Account Map for {0} account(s) {1}. Add one, then rebuild.").format(doc.provider, ", ".join(missing)))
	if not header:
		if not ctx.maps:
			raise BillProblem(_("This bill names no ad account. Choose an Account Map on the document, then rebuild."))
		# The account with the most spend on the bill decides; every other map must agree with it.
		spend = {}
		for ln in doc.lines:
			k = _norm_key(doc.provider, ln.platform_account_id)
			spend[k] = spend.get(k, 0) + flt(ln.net_amount)
		header = ctx.maps[max(ctx.maps, key=lambda k: (spend.get(k, 0), k))]
	disagree = [m.platform_key for m in ctx.maps.values() if (m.company, m.supplier, m.billing_address) != (header.company, header.supplier, header.billing_address)]
	if disagree:
		raise BillProblem(_("Accounts {0} map to a different company, supplier or billing GSTIN than {1}; one bill can only become one Purchase Invoice.").format(", ".join(disagree), header.platform_key))
	if doc.company and header.company != doc.company:
		raise BillProblem(_("The account map puts this bill in {0}, but it was fetched for {1}.").format(header.company, doc.company))
	ctx.map = header
	doc.account_map = doc.account_map or header.name
	doc.company = header.company
	doc.workspace = header.workspace or doc.workspace
	doc.brand = header.brand or doc.brand
	doc.account = header.account or doc.account
	_supplier(doc, ctx)
	_duplicate(doc, ctx)
	return ctx


def _supplier(doc, ctx):
	m = ctx.map
	if not m.supplier or not frappe.db.exists("Supplier", m.supplier):
		raise BillProblem(_("Account map {0} has no Supplier.").format(m.name))
	smeta = frappe.get_meta("Supplier")
	sfields = ["name", "supplier_name", "disabled", "tax_withholding_category"] + [f for f in ("gstin", "gst_category", "is_reverse_charge_applicable") if smeta.has_field(f)]
	sup = frappe.db.get_value("Supplier", m.supplier, sfields, as_dict=True)
	if sup.disabled:
		raise BillProblem(_("Supplier {0} is disabled.").format(sup.name))
	ctx.supplier = sup
	doc.supplier = sup.name
	ctx.overseas = (sup.get("gst_category") or "") == "Overseas"
	if ctx.overseas and bill_gst.available() and not frappe.db.get_single_value("GST Settings", "enable_overseas_transactions"):
		raise BillProblem(_("{0} is an overseas supplier: enable Overseas Transactions in GST Settings (India Compliance) to book import of services under reverse charge.").format(sup.name))
	if not ctx.overseas and not sup.get("gstin") and bill_gst.available():
		raise BillProblem(_("Supplier {0} has no GSTIN. Domestic ad bills need the supplier's GSTIN for input tax credit.").format(sup.name))

	expected = bill_suppliers.key_for_supplier(sup.name)
	entity = (doc.billing_entity or "").strip()
	if m.billing_entity:
		if bill_suppliers.norm(entity) != bill_suppliers.norm(m.billing_entity):
			ctx.differences.append(_("Billing entity on the bill is '{0}', the account map expects '{1}'.").format(entity or "-", m.billing_entity))
	elif entity:
		got = bill_suppliers.match_entity(doc.provider, entity)
		if not got:
			ctx.differences.append(_("Billing entity '{0}' is not a known {1} legal entity: confirm the supplier ({2}).").format(entity, doc.provider, sup.supplier_name))
		elif got != expected:
			ctx.differences.append(_("Billing entity '{0}' is {1}, but the account map's supplier is {2}.").format(entity, bill_suppliers.LEGAL_ENTITIES[got]["supplier_name"], sup.supplier_name))
	elif doc.provider == "Meta" and doc.source_type == "API":
		ctx.differences.append(_("Meta reported no billing entity for this invoice: confirm the supplier ({0}).").format(sup.supplier_name))

	if doc.supplier_gstin and doc.supplier_gstin.strip().upper() != (sup.get("gstin") or ""):
		ctx.differences.append(_("Supplier GSTIN on the bill {0} is not {1}'s GSTIN {2}.").format(doc.supplier_gstin, sup.supplier_name, sup.get("gstin") or "-"))

	company_gstin = (m.company_gstin or frappe.db.get_value("Address", m.billing_address, "gstin") or "") if m.billing_address else ""
	ctx.company_gstin = company_gstin
	if bill_gst.available() and not company_gstin:
		raise BillProblem(_("Billing address {0} on the account map has no GSTIN.").format(m.billing_address or "-"))
	if doc.company_gstin_on_bill and doc.company_gstin_on_bill.strip().upper() != company_gstin:
		ctx.differences.append(_("The bill is addressed to GSTIN {0}, not the company GSTIN {1}.").format(doc.company_gstin_on_bill, company_gstin))
	gstin_check(m, ctx.differences, overseas=ctx.overseas)


def gstin_check(m, differences: list | None = None, overseas: bool = False) -> str:
	"""Compare the GSTIN the platform holds for this account with the company GSTIN. Raises / resolves
	the GSTIN Mismatch alert. Returns Matches / Mismatch / Not Entered."""
	held = (m.platform_gstin or "").strip().upper()
	company_gstin = (m.company_gstin or (frappe.db.get_value("Address", m.billing_address, "gstin") if m.billing_address else "") or "").upper()
	key = f"gstin-mismatch:{m.name}"
	if not held:
		alerts.resolve_alert(key, "GSTIN cleared on the map")
		if differences is not None and not overseas:
			differences.append(_("The GSTIN the platform holds for {0} is not recorded on the account map.").format(m.platform_key))
		return "Not Entered"
	if held != company_gstin:
		doc = m if isinstance(m, Document) else frappe.get_doc("Ads Bill Account Map", m.name)
		alerts.raise_alert(
			"GSTIN Mismatch",
			"High",
			_("GSTIN mismatch on {0} account {1}").format(m.provider, m.platform_key),
			_("The platform bills GSTIN {0} but the company GSTIN is {1}. Input tax credit is lost on bills to the wrong GSTIN and the platform will not re-issue them: correct it in the platform's billing settings.").format(held, company_gstin or "-"),
			ref=doc,
			dedupe_key=key,
		)
		if differences is not None:
			differences.append(_("The platform holds GSTIN {0} for {1}, not the company GSTIN {2} (GSTIN Mismatch).").format(held, m.platform_key, company_gstin or "-"))
		return "Mismatch"
	alerts.resolve_alert(key, "GSTIN corrected")
	return "Matches"


def _fiscal_year(date, company):
	from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

	try:
		return get_fiscal_year(date, company=company)
	except FiscalYearError:
		raise BillProblem(_("No fiscal year covers bill date {0} for {1}.").format(date, company)) from None


def _duplicate(doc, ctx):
	fy, start, end = _fiscal_year(doc.bill_date, doc.company)[:3]
	ctx.fiscal_year = fy
	pis = frappe.get_all(
		"Purchase Invoice",
		filters={"supplier": doc.supplier, "bill_no": doc.bill_no, "docstatus": ["!=", 2], "posting_date": ["between", [start, end]], "name": ["!=", doc.purchase_invoice or ""]},
		pluck="name",
	)
	others = frappe.get_all(
		"Ads Bill Document",
		filters={"supplier": doc.supplier, "bill_no": doc.bill_no, "bill_date": ["between", [start, end]], "name": ["!=", doc.name], "status": ["!=", DUPLICATE]},
		pluck="name",
	)
	if pis or others:
		ctx.duplicate = [_("Bill number {0} from {1} is already used in fiscal year {2}: {3}. Nothing was created.").format(doc.bill_no, doc.supplier, fy, ", ".join(pis + others))]


# ─── build ────────────────────────────────────────────────────────────────────

def _series(is_return: bool) -> str:
	s = get_settings()
	series = (s.bill_return_naming_series if is_return else s.bill_naming_series) or ("ADR-.YY.-.#####" if is_return else "ADB-.YY.-.#####")
	if series_length(series) > 16:
		raise BillProblem(_("Naming series {0} makes names longer than 16 characters (GST document number rule). Shorten it in Ads Settings.").format(series))
	return series


def series_length(series: str) -> int:
	n = 0
	for part in series.split("."):
		if part in ("YY", "MM", "DD"):
			n += 2
		elif part == "YYYY":
			n += 4
		elif part and set(part) == {"#"}:
			n += max(len(part), 1)
		else:
			n += len(part)
	return n


def _item(m) -> str:
	item = m.item_code or get_settings().bill_service_item
	if not item or not frappe.db.exists("Item", item):
		raise BillProblem(_("No service item: set one on the account map or Ads Settings > Bill Service Item."))
	stock, disabled = frappe.db.get_value("Item", item, ["is_stock_item", "disabled"])
	if stock or disabled:
		raise BillProblem(_("Item {0} must be an enabled non-stock service item.").format(item))
	return item


def _lines(doc, ctx) -> list[dict]:
	"""PI lines that sum exactly to the bill's pre-tax amount, rounding on the largest line."""
	net = flt(doc.net_amount, 2)
	rows = [frappe._dict(description=ln.description, account=_norm_key(doc.provider, ln.platform_account_id), campaign_id=ln.campaign_id, brand=ln.brand, cost_center=ln.cost_center, amount=flt(ln.net_amount, 2)) for ln in doc.lines if flt(ln.net_amount)]
	period = f" {doc.service_start} to {doc.service_end}" if doc.service_start and doc.service_end else ""
	if not rows or doc.document_type == "Credit Memo":
		# A debit note is one line: ERPNext checks returned quantities per item against the original.
		label = _("Credit memo {0} on bill {1}").format(doc.bill_no, doc.original_bill_no) if doc.document_type == "Credit Memo" else f"{doc.provider} advertising{period}"
		rows = [frappe._dict(description=label, account=rows[0].account if len(rows) == 1 else "", amount=net)]
	gap = round(net - sum(r.amount for r in rows), 2)
	if gap:
		largest = max(rows, key=lambda r: abs(r.amount))
		if abs(gap) <= max(TOLERANCE, 0.01 * len(rows)):
			largest.amount = round(largest.amount + gap, 2)
		else:
			ctx.differences.append(_("The lines add up to {0} but the bill's pre-tax amount is {1}; the gap is on a separate line.").format(_money(net - gap, doc.currency), _money(net, doc.currency)))
			rows.append(frappe._dict(description=_("Unreconciled difference to the bill total"), account="", amount=gap))
	negatives = [r for r in rows if r.amount < 0]
	if negatives:
		# A credit line inside an invoice: netted into the largest line rather than a negative rate.
		largest = max(rows, key=lambda r: r.amount)
		for r in negatives:
			largest.amount = round(largest.amount + r.amount, 2)
			rows.remove(r)
		ctx.differences.append(_("{0} credit line(s) were netted into '{1}'.").format(len(negatives), largest.description))
		if largest.amount <= 0:
			raise BillProblem(_("The bill's lines net to zero or less; enter it by hand."))
	return rows


def _original_pi(doc) -> str:
	ref = (doc.original_bill_no or "").strip()
	if not ref:
		raise BillProblem(_("Credit memo: enter the Original Bill Number (the invoice it credits), then rebuild."))
	orig = frappe.get_all("Ads Bill Document", filters={"provider": doc.provider, "name": ["!=", doc.name], "document_type": "Invoice"}, or_filters={"bill_no": ref, "platform_invoice_id": ref}, fields=["name", "purchase_invoice"], limit=1)
	pi_name = orig[0].purchase_invoice if orig else None
	if orig:
		doc.original_document = orig[0].name
	if not pi_name:
		pi_name = frappe.db.get_value("Purchase Invoice", {"supplier": doc.supplier, "bill_no": ref, "docstatus": 1, "is_return": 0}, "name")
	if not pi_name:
		raise BillProblem(_("Credit memo: no Purchase Invoice found for original bill {0}.").format(ref))
	if frappe.db.get_value("Purchase Invoice", pi_name, "docstatus") != 1:
		raise BillProblem(_("Credit memo: original Purchase Invoice {0} is not submitted yet; approve it first.").format(pi_name))
	return pi_name


def _build(doc, ctx):
	m, sup = ctx.map, ctx.supplier
	is_return = doc.document_type == "Credit Memo"
	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	pi = frappe.new_doc("Purchase Invoice")
	pi.naming_series = _series(is_return)
	pi.company = doc.company
	pi.supplier = sup.name
	pi.set_posting_time = 1
	pi.posting_date = doc.bill_date
	pi.bill_no = doc.bill_no
	pi.bill_date = doc.bill_date
	if doc.due_date and getdate(doc.due_date) >= getdate(doc.bill_date):
		pi.due_date = doc.due_date
	pi.currency = doc.currency or company_currency
	if pi.currency != company_currency:
		from erpnext.setup.utils import get_exchange_rate

		rate = flt(get_exchange_rate(pi.currency, company_currency, doc.bill_date))
		if not rate:
			raise BillProblem(_("No exchange rate {0} → {1} on {2}.").format(pi.currency, company_currency, doc.bill_date))
		pi.conversion_rate = rate
	pi.ignore_pricing_rule = 1
	pi.remarks = _("{0} ad bill {1} (Ads Bill Document {2})").format(doc.provider, doc.bill_no, doc.name)
	if is_return:
		pi.is_return = 1
		pi.return_against = _original_pi(doc)
	item = _item(m)
	cost_center = m.cost_center or frappe.get_cached_value("Company", doc.company, "cost_center")
	sign = -1 if is_return else 1
	tds_category = None if m.tds_not_applicable else (m.tax_withholding_category or sup.tax_withholding_category)
	item_meta = frappe.get_meta("Purchase Invoice Item")
	for r in _lines(doc, ctx):
		line_map = ctx.maps.get(r.account) or m
		row = {
			"item_code": item,
			"description": r.description,
			"qty": sign,
			"rate": abs(r.amount),
			"cost_center": r.cost_center or line_map.cost_center or cost_center,
		}
		if line_map.expense_account or m.expense_account:
			row["expense_account"] = line_map.expense_account or m.expense_account
		if item_meta.has_field("apply_tds"):
			row["apply_tds"] = 1 if tds_category else 0
		if tds_category and item_meta.has_field("tax_withholding_category"):
			row["tax_withholding_category"] = tds_category
		pi.append("items", row)
	pi.billing_address = m.billing_address
	pi.set_missing_values()
	# Re-assert what set_missing_values may have defaulted from the supplier / company.
	pi.billing_address = m.billing_address
	pi.posting_date = doc.bill_date
	_set_party_gst(pi, sup, ctx)
	pi.apply_tds = 1 if tds_category else 0
	if pi.meta.has_field("tax_withholding_category"):
		pi.tax_withholding_category = tds_category
	for it in pi.items:
		if it.meta.has_field("apply_tds"):
			it.apply_tds = 1 if tds_category else 0
		if tds_category and it.meta.has_field("tax_withholding_category"):
			it.tax_withholding_category = tds_category
		if it.meta.has_field("tax_withholding_category") and not tds_category:
			it.tax_withholding_category = None
	if bill_gst.available():
		if not bill_gst.apply_taxes(pi):
			ctx.differences.append(_("India Compliance found no GST template for {0} ({1}); no GST rows were added.").format(doc.company, "reverse charge" if ctx.overseas else "forward charge"))
	else:
		pi.set("taxes", [])
		ctx.differences.append(_("India Compliance is not installed: GST was not applied. Enter the taxes by hand."))
	pi.calculate_taxes_and_totals()
	pi.flags.ignore_permissions = True
	pi.insert()
	return frappe.get_doc("Purchase Invoice", pi.name)


def _set_party_gst(pi, sup, ctx):
	from frappe.contacts.doctype.address.address import get_default_address

	meta = pi.meta
	addr = get_default_address("Supplier", sup.name)
	if addr:
		pi.supplier_address = addr
	if meta.has_field("company_gstin"):
		pi.company_gstin = ctx.company_gstin
	if meta.has_field("supplier_gstin"):
		gstin = (frappe.db.get_value("Address", addr, "gstin") if addr and frappe.get_meta("Address").has_field("gstin") else None) or sup.get("gstin") or ""
		pi.supplier_gstin = "" if ctx.overseas else gstin
	if meta.has_field("gst_category"):
		pi.gst_category = sup.get("gst_category") or ("Overseas" if ctx.overseas else "Registered Regular")
	if meta.has_field("is_reverse_charge"):
		pi.is_reverse_charge = 1 if ctx.overseas else 0
	if meta.has_field("place_of_supply"):
		pi.place_of_supply = None  # IC derives it from the company GSTIN (recipient of the service)


# ─── checks (auto-submit rules) ───────────────────────────────────────────────

def _expected_tds(ctx):
	return None if ctx.map.tds_not_applicable else (ctx.map.tax_withholding_category or ctx.supplier.tax_withholding_category or None)


def context_changes(pi, ctx) -> list[str]:
	"""Where the draft no longer matches the current account map / supplier (it was built from an
	older one): company, supplier, billing address, company GSTIN, supplier address / GSTIN, TDS."""
	from frappe.contacts.doctype.address.address import get_default_address

	m, sup = ctx.map, ctx.supplier
	out = []
	if pi.company != m.company:
		out.append(_("Company on the Purchase Invoice is {0}, the account map's is {1}.").format(pi.company, m.company))
	if pi.supplier != sup.name:
		out.append(_("Supplier on the Purchase Invoice is {0}, the account map's is {1}.").format(pi.supplier, sup.name))
	if (pi.get("billing_address") or "") != (m.billing_address or ""):
		out.append(_("Billing address on the Purchase Invoice is {0}, the account map's is {1}.").format(pi.get("billing_address") or "-", m.billing_address or "-"))
	if pi.meta.has_field("company_gstin") and (pi.get("company_gstin") or "") != (ctx.company_gstin or ""):
		out.append(_("Company GSTIN on the Purchase Invoice is not the account map's."))
	addr = get_default_address("Supplier", sup.name)
	if addr and (pi.get("supplier_address") or "") != addr:
		out.append(_("Supplier address on the Purchase Invoice is {0}, the supplier's is {1}.").format(pi.get("supplier_address") or "-", addr))
	if pi.meta.has_field("supplier_gstin"):
		gstin = "" if ctx.overseas else ((frappe.db.get_value("Address", addr, "gstin") if addr and frappe.get_meta("Address").has_field("gstin") else None) or sup.get("gstin") or "")
		if (pi.get("supplier_gstin") or "") != gstin:
			out.append(_("Supplier GSTIN on the Purchase Invoice is {0}, the supplier's is {1}.").format(pi.get("supplier_gstin") or "-", gstin or "-"))
	tds = _expected_tds(ctx)
	cats = ({pi.get("tax_withholding_category")} if pi.meta.has_field("tax_withholding_category") else set()) | {it.get("tax_withholding_category") for it in pi.items if it.meta.has_field("tax_withholding_category")}
	cats -= {None, ""}
	if (cats and cats != {tds}) or (tds and not cats) or cint(pi.get("apply_tds")) != (1 if tds else 0):
		out.append(_("TDS on the Purchase Invoice ({0}) is not the account map's ({1}).").format(", ".join(sorted(cats)) or _("none"), tds or _("none")))
	return out


def check(doc, pi, ctx=None) -> list[str]:
	"""Every reason this draft may not be submitted unattended. Empty = all rules hold."""
	out = list(context_changes(pi, ctx)) if ctx else []
	cur = doc.currency
	sign = -1 if pi.get("is_return") else 1
	if abs(flt(pi.net_total) * sign - flt(doc.net_amount)) >= TOLERANCE:
		out.append(_("Pre-tax amount: Purchase Invoice {0}, bill {1}.").format(_money(pi.net_total, cur), _money(doc.net_amount, cur)))
	if not bill_gst.available():
		out.append(_("India Compliance is not installed: GST on this bill was not checked."))
	else:
		gst = bill_gst.gst_breakup(pi)
		if abs(gst["charged"] - flt(doc.tax_amount)) >= TOLERANCE:
			out.append(_("GST: Purchase Invoice {0}, bill {1}.").format(_money(gst["charged"], cur), _money(doc.tax_amount, cur)))
		overseas = pi.get("gst_category") == "Overseas"
		if overseas:
			if not pi.get("is_reverse_charge"):
				out.append(_("Overseas supplier but the Purchase Invoice is not reverse charge."))
			if pi.get("itc_classification") != "Import Of Service":
				out.append(_("ITC classification is {0}, expected Import Of Service.").format(pi.get("itc_classification") or "-"))
			if not gst["rcm"]:
				out.append(_("No reverse-charge GST was self-assessed."))
			if not bill_gst.valid_document_name(pi):
				out.append(_("Purchase Invoice name {0} is not a valid GST document number (16 characters or fewer).").format(pi.name))
		else:
			same_state = (pi.get("company_gstin") or "")[:2] == (pi.get("supplier_gstin") or "")[:2]
			if flt(doc.tax_amount):
				if same_state and not (gst["cgst"] and gst["sgst"] and not gst["igst"]):
					out.append(_("Supplier and company are in the same state: expected CGST + SGST."))
				if not same_state and not (gst["igst"] and not gst["cgst"] and not gst["sgst"]):
					out.append(_("Supplier and company are in different states: expected IGST."))
			for key, field in (("cgst", "cgst_amount"), ("sgst", "sgst_amount"), ("igst", "igst_amount")):
				if flt(doc.get(field)) and abs(gst[key] - flt(doc.get(field))) >= TOLERANCE:
					out.append(_("{0}: Purchase Invoice {1}, bill {2}.").format(key.upper(), _money(gst[key], cur), _money(doc.get(field), cur)))
	m = ctx.map if ctx else (frappe.db.get_value("Ads Bill Account Map", doc.account_map, "*", as_dict=True) if doc.account_map else None)
	if not (m and m.tds_not_applicable):
		cats = {pi.get("tax_withholding_category")} | {it.get("tax_withholding_category") for it in pi.items}
		cats.discard(None)
		cats.discard("")
		if not (pi.get("apply_tds") and cats):
			out.append(_("TDS is not set: give the account map or supplier a Tax Withholding Category (or mark No TDS once your adviser confirms)."))
	if pi.get("is_return") and not pi.get("return_against"):
		out.append(_("Debit note without the original Purchase Invoice."))
	hsn = frappe.db.get_value("Item", pi.items[0].item_code, "gst_hsn_code") if pi.items and frappe.get_meta("Item").has_field("gst_hsn_code") else SERVICE_SAC
	if hsn and hsn != SERVICE_SAC:
		out.append(_("Item {0} has SAC {1}, advertising services are {2}.").format(pi.items[0].item_code, hsn, SERVICE_SAC))
	return out


# ─── approval (maker-checker) ─────────────────────────────────────────────────

def makers(doc, pi) -> set:
	"""People who typed or changed this bill or its draft. None of them may approve it."""
	out = {doc.prepared_by} if doc.prepared_by else set()
	if pi and doc.pi_drafted_at and get_datetime(pi.modified) > get_datetime(doc.pi_drafted_at):
		out.add(pi.modified_by)
	return out


def approve(name: str, user: str, acknowledge_differences: bool = False) -> dict:
	frappe.db.get_value("Ads Bill Document", name, "name", for_update=True)
	doc = frappe.get_doc("Ads Bill Document", name)
	if doc.status not in (DRAFT, REVIEW) or not doc.purchase_invoice:
		frappe.throw(_("Only a bill with a draft Purchase Invoice can be approved (status is {0}).").format(_(doc.status)))
	pi = frappe.get_doc("Purchase Invoice", doc.purchase_invoice)
	if pi.docstatus != 0:
		frappe.throw(_("Purchase Invoice {0} is not a draft.").format(pi.name))
	if user in makers(doc, pi):
		frappe.throw(_("You prepared or edited this bill; another person must approve it (maker-checker)."), frappe.PermissionError)
	if not frappe.has_permission("Purchase Invoice", "submit", doc=pi, user=user):
		frappe.throw(_("You cannot submit Purchase Invoices for {0}.").format(pi.company), frappe.PermissionError)
	ctx = _context(doc)
	if ctx.duplicate:
		frappe.throw(_lines_text(ctx.duplicate))
	_lines(doc, ctx)
	differences = ctx.differences + _api_notes(doc) + ([platform_update_note(doc)] if doc.platform_update else []) + check(doc, pi, ctx)
	if doc.platform_update:
		frappe.throw(_("The platform re-issued this bill's figures; rebuild the draft before approving."))
	if differences and not cint(acknowledge_differences):
		frappe.throw(_("Review these before approving:") + "\n" + _lines_text(differences), title=_("Differences"))
	with pipeline_submit(pi):
		pi.submit()
	doc.flags.from_pipeline = True
	doc.flags.ignore_permissions = True
	doc.approved_by = user
	doc.approved_at = now_datetime()
	doc.auto_submitted = 0
	if differences:
		doc.differences = _lines_text(differences + [_("Approved with these differences by {0}.").format(user)])
	doc.status = SUBMITTED
	doc.save()
	alerts.resolve_alert(f"bill-review:{doc.name}", f"Approved by {user}")
	return {"status": SUBMITTED, "purchase_invoice": pi.name}


# ─── fetch runs ───────────────────────────────────────────────────────────────

def enqueue_source(source: str, user: str | None = None):
	return frappe.enqueue("excom.excom_ads.bills.run_source", queue="long", job_id=f"excom_ads_bills::{frappe.local.site}::{source}", deduplicate=True, source=source, timeout=1800)


def run_source(source: str) -> dict:
	"""Fetch one source's invoices, store each once, build drafts. Commits per bill."""
	from excom.excom_ads import bill_sources
	from excom.excom_ads.providers.base import AdsProviderError

	src = frappe.get_doc("Ads Bill Source", source)
	result = {"fetched": 0, "processed": 0, "statuses": {}}
	key = f"bill-fetch:{src.name}"
	try:
		bills, reader = bill_sources.fetch(src)
	except AdsProviderError as e:
		status = "Not On Invoicing" if bill_sources.is_not_on_invoicing(e) else "Failed"
		frappe.db.set_value("Ads Bill Source", src.name, {"last_fetched_at": now_datetime(), "last_status": status, "last_error": str(e)[:1000], "last_documents": 0})
		alerts.raise_alert("Sync Failed", "High" if e.needs_person else "Medium", _("Ad bill fetch failed: {0}").format(src.name), str(e)[:500], ref=src, dedupe_key=key)
		frappe.db.commit()
		return {**result, "error": str(e), "status": status}
	for bill in bills:
		if not bill.get("platform_invoice_id") or not bill.get("bill_no"):
			continue
		try:
			name, todo = upsert_from_api(bill, src)
			result["fetched"] += 1
			if todo:
				_attach_pdf(name, bill, reader)
				st = process(name)
				result["processed"] += 1
				result["statuses"][st] = result["statuses"].get(st, 0) + 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom Ads: could not store ad bill {bill.get('bill_no')}", message=frappe.get_traceback())
	frappe.db.set_value("Ads Bill Source", src.name, {"last_fetched_at": now_datetime(), "last_status": "Success", "last_error": "", "last_documents": result["fetched"]})
	alerts.resolve_alert(key, "Fetch succeeded")
	frappe.db.commit()
	return result


def _attach_pdf(name: str, bill: dict, reader) -> None:
	"""Best effort: the platform's PDF as a private file on the bill document."""
	if not bill.get("pdf_url") or frappe.db.get_value("Ads Bill Document", name, "attachment"):
		return
	try:
		content = reader.download_pdf(bill)
	except Exception:
		content = None
	if not content:
		return
	sp = f"bill_pdf_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(sp)
	try:
		# File.insert rejects a PDF that is malformed or carries JavaScript: the bill is kept without it.
		f = frappe.get_doc({"doctype": "File", "file_name": f"{bill['provider']}-{bill['bill_no']}.pdf"[:140], "attached_to_doctype": "Ads Bill Document", "attached_to_name": name, "attached_to_field": "attachment", "is_private": 1, "content": content})
		f.insert(ignore_permissions=True)
		frappe.db.set_value("Ads Bill Document", name, "attachment", f.file_url, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point=sp)
		frappe.clear_messages()


# ─── GSTR-2B ──────────────────────────────────────────────────────────────────

def check_gstr2b() -> dict:
	"""Read India Compliance's GSTR-2B match status for our platform Purchase Invoices. Never downloads 2B."""
	out = {"checked": 0, "missing": 0, "orphans": 0}
	if not bill_gst.available() or not frappe.db.exists("DocType", "GST Inward Supply"):
		return out
	missing_after = cint(get_settings().gstr2b_missing_after_days)
	rows = frappe.get_all("Ads Bill Document", filters={"status": SUBMITTED, "purchase_invoice": ["is", "set"]}, fields=["name", "purchase_invoice", "provider", "bill_no", "gstr2b_status"])
	our_bill_nos = set()
	for r in rows:
		pi = frappe.get_doc("Purchase Invoice", r.purchase_invoice)
		our_bill_nos.add((pi.get("supplier_gstin"), (pi.bill_no or "").upper()))
		out["checked"] += 1
		key = f"2b-missing:{r.name}"
		if pi.get("is_reverse_charge") or not pi.get("supplier_gstin") or pi.docstatus != 1:
			_set_2b(r.name, "Not Applicable", "")
			continue
		gis = bill_gst.inward_supply_for(pi)
		if gis:
			ms = gis.get("match_status") or ""
			status = "Matched" if gis["linked"] and ms in ("Exact Match", "Manual Match", "Suggested Match") else ("Mismatch" if ms == "Mismatch" else "In 2B, Not Linked")
			_set_2b(r.name, status, f"{ms or 'Unlinked'} · 2B {gis.get('return_period_2b') or '-'}")
			alerts.resolve_alert(key, "Found in GSTR-2B")
			continue
		period = getdate(pi.bill_date or pi.posting_date).strftime("%m%Y")
		if not bill_gst.has_2b_data(pi.company_gstin, period):
			_set_2b(r.name, "Not Checked", _("No GSTR-2B imported for {0} from {1}").format(pi.company_gstin, period))
			continue
		if getdate(pi.bill_date or pi.posting_date) > getdate(add_days(nowdate(), -missing_after)):
			_set_2b(r.name, "Not Checked", _("Not in 2B yet"))
			continue
		_set_2b(r.name, "Missing", _("Not in any imported GSTR-2B"))
		out["missing"] += 1
		doc = frappe.get_doc("Ads Bill Document", r.name)
		alerts.raise_alert("Bill Missing", "High", _("{0} bill {1} is missing from GSTR-2B").format(r.provider, r.bill_no), _("The supplier has not reported this invoice; input tax credit cannot be claimed until it appears. Ask the platform to file it."), ref=doc, dedupe_key=key)
	# 2B rows from platform suppliers we have no Purchase Invoice for.
	gstins = {e["gstin"] for e in bill_suppliers.LEGAL_ENTITIES.values() if e["gstin"]}
	sup_names = set(frappe.get_all("Ads Bill Account Map", pluck="supplier"))
	if sup_names and frappe.get_meta("Supplier").has_field("gstin"):
		gstins |= {g for g in frappe.get_all("Supplier", filters={"name": ["in", list(sup_names)]}, pluck="gstin") if g}
	for g in bill_gst.unlinked_2b_rows(sorted(gstins)):
		if (g.supplier_gstin, (g.bill_no or "").upper()) in our_bill_nos:
			continue
		if frappe.db.exists("Ads Bill Document", {"bill_no": g.bill_no, "status": ["!=", DUPLICATE]}):
			continue
		out["orphans"] += 1
		alerts.raise_alert(
			"Bill Missing",
			"Medium",
			_("GSTR-2B has {0} bill {1} with no Purchase Invoice").format(g.supplier_name or g.supplier_gstin, g.bill_no),
			_("Return period {0}. Fetch or upload the bill so its input tax credit is booked.").format(g.return_period_2b or "-"),
			scope={"company": g.company},
			dedupe_key=f"2b-orphan:{g.name}",
		)
	frappe.db.commit()
	return out


def _set_2b(name, status, detail):
	frappe.db.set_value("Ads Bill Document", name, {"gstr2b_status": status, "gstr2b_detail": (detail or "")[:140]}, update_modified=False)


# ─── TDS certificates ─────────────────────────────────────────────────────────

# Quarter → (first month, deadline month, deadline day, deadline year offset from the quarter's year)
QUARTERS = {
	"Q1 (Apr-Jun)": (4, 8, 15, 0),
	"Q2 (Jul-Sep)": (7, 11, 15, 0),
	"Q3 (Oct-Dec)": (10, 2, 15, 1),
	"Q4 (Jan-Mar)": (1, 6, 15, 0),
}


def quarter_of(date) -> tuple[str, object, object, object]:
	"""(quarter label, start, end, certificate deadline) of a date."""
	from frappe.utils import add_months, get_last_day

	d = getdate(date)
	for label, (first, dmonth, dday, yoff) in QUARTERS.items():
		if first <= d.month <= first + 2:
			start = getdate(f"{d.year}-{first:02d}-01")
			end = get_last_day(add_months(start, 2))
			deadline = getdate(f"{d.year + yoff}-{dmonth:02d}-{dday:02d}")
			return label, start, end, deadline
	raise ValueError(date)


def _tds_amount(pi) -> float:
	total = 0.0
	if pi.meta.has_field("tax_withholding_entries"):
		total = sum(flt(r.get("withholding_amount")) for r in pi.get("tax_withholding_entries") or [])
		if total:
			return total
	for t in pi.get("taxes") or []:
		if t.get("is_tax_withholding_account"):
			total += flt(t.base_tax_amount_after_discount_amount)
	return total


def check_tds_certificates(today=None) -> dict:
	"""Make sure a TDS Certificate Log exists for every platform / quarter with TDS deducted, and
	raise "TDS Certificate Due" from N days before the deadline until it is marked Sent."""
	today = getdate(today)
	days = cint(get_settings().tds_certificate_alert_days)
	out = {"logs": 0, "alerts": 0}
	rows = frappe.get_all("Ads Bill Document", filters={"status": SUBMITTED, "purchase_invoice": ["is", "set"], "bill_date": [">=", add_days(today, -500)]}, fields=["provider", "purchase_invoice", "company"])
	groups = {}
	for r in rows:
		pi = frappe.db.get_value("Purchase Invoice", r.purchase_invoice, ["name", "posting_date", "supplier", "docstatus"], as_dict=True)
		if not pi or pi.docstatus != 1:
			continue
		label, start, end, deadline = quarter_of(pi.posting_date)
		if not (add_days(deadline, -days) <= today):
			continue
		amount = _tds_amount(frappe.get_doc("Purchase Invoice", pi.name))
		if not amount:
			continue
		k = (r.company, pi.supplier, r.provider, label, str(start))
		g = groups.setdefault(k, {"amount": 0.0, "deadline": deadline, "date": pi.posting_date})
		g["amount"] += amount
	for (company, supplier, provider, label, _start), g in groups.items():
		fy = _fiscal_year(g["date"], company)[0]
		name = frappe.db.get_value("Ads TDS Certificate Log", {"company": company, "supplier": supplier, "fiscal_year": fy, "quarter": label}, "name")
		if not name:
			log = frappe.get_doc({"doctype": "Ads TDS Certificate Log", "company": company, "supplier": supplier, "provider": provider, "fiscal_year": fy, "quarter": label, "due_date": g["deadline"], "tds_amount": round(g["amount"], 2), "status": "Pending"})
			log.flags.ignore_permissions = True
			log.insert()
			name = log.name
			out["logs"] += 1
		else:
			frappe.db.set_value("Ads TDS Certificate Log", name, {"tds_amount": round(g["amount"], 2), "due_date": g["deadline"]}, update_modified=False)
		log = frappe.get_doc("Ads TDS Certificate Log", name)
		if log.status == "Pending":
			sev = "Critical" if today > getdate(g["deadline"]) else "High"
			alerts.raise_alert("TDS Certificate Due", sev, _("Send the {0} TDS certificate to {1} by {2}").format(label, supplier, frappe.utils.formatdate(g["deadline"])), _("Form 131 for {0} {1}. The platform needs it to give the TDS credit.").format(fy, label), ref=log, dedupe_key=f"tds-cert:{log.name}")
			out["alerts"] += 1
	frappe.db.commit()
	return out


def run_checks() -> dict:
	return {"gstr2b": check_gstr2b(), "tds": check_tds_certificates()}
