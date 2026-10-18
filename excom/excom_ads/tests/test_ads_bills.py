"""Excom Ads, ad bills → ERPNext Purchase Invoices with India Compliance GST. Meta business_invoices
(Graph v26.0) and Google Ads InvoiceService (v26) are faked with recorded shapes; no network.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_bills'}"

Fixtures (prefix "QA Bills") live on the site's default company (A) and "_Test Ads Company B":
company GSTIN addresses in Haryana and Karnataka, QA suppliers for the platform legal entities, a
service item (SAC 998365), a 2% Tax Withholding Category, account maps, a Meta connection and a
Google Ads connection. Everything is removed afterwards, Purchase Invoices cancelled then deleted.
Tests that need India Compliance skip on a site without it.
"""

import json
import re
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from excom.excom_ads import api_bills, bill_sources, bill_suppliers, bill_tasks, bills, google_auth, permissions
from excom.excom_ads.providers import meta

P = "QA Bills"
COMPANY_B = "_Test Ads Company B"
GSTIN_HR = "06AAACQ1234B1Z6"
GSTIN_KA = "29AAACQ1234B1ZY"
GSTIN_B = "06AAACQ5678C1ZG"
GSTIN_FB = "06AABCF5150G1ZZ"  # Facebook India Online Services (Haryana), as given by the user
GSTIN_GOOGLE = "06AACCG0527D1Z8"  # a syntactically valid Haryana GSTIN on Google India's PAN
SUP_FB = f"{P} FB India"
SUP_IE = f"{P} Meta Ireland"
SUP_GOOGLE = f"{P} Google India"
SUPPLIERS = (SUP_FB, SUP_IE, SUP_GOOGLE)
ITEM = f"{P} Ad Service"
TWC = f"{P} TDS 2pc"
META_CONN = f"{P} Meta"
GOOGLE_CONN = f"{P} Google"
SRC_META = f"{P} Meta Src"
SRC_GOOGLE = f"{P} Google Src"
WS_A, WS_B = f"{P} WS A", f"{P} WS B"
BUSINESS = "990011223344"
CID = "9990001111"
SETUP_ID = "5550001"
TOKEN = "QA_BILLS_SYSTEM_USER_TOKEN_123"
U_MAKER = "qa_bills_maker@example.com"
U_CHECKER = "qa_bills_checker@example.com"
U_NORIGHT = "qa_bills_noright@example.com"
U_B = "qa_bills_b@example.com"
U_MKT = "qa_bills_mkt@example.com"
USERS = (U_MAKER, U_CHECKER, U_NORIGHT, U_B, U_MKT)
BILL_DATE = "2026-10-02"
SERIES, RSERIES = "QAB-.YY.-.#####", "QAR-.YY.-.#####"


def _ic() -> bool:
	return "india_compliance" in frappe.get_installed_apps()


# ─── recorded shapes ──────────────────────────────────────────────────────────

def meta_invoice(node_id, invoice_id, account, net, tax, entity="FBIN", campaigns=None, inv_type="INV", date=BILL_DATE):
	"""One OmegaCustomerTrx as /{business_id}/business_invoices returns it (amounts as strings, *_in_millis)."""
	m = lambda v: str(int(round(v * 1000)))
	camps = campaigns if campaigns is not None else [("c1", f"{P} Diwali", net * 0.6), ("c2", f"{P} Leads", net * 0.4)]
	return {
		"id": node_id,
		"invoice_id": invoice_id,
		"type": inv_type,
		"invoice_type": inv_type,
		"entity": entity,
		"advertiser_name": "QA Advertiser",
		"ad_account_ids": [account],
		"billed_amount_details": {"currency": "INR", "net_amount": f"{net:.2f}", "net_amount_in_millis": m(net), "tax_amount": f"{tax:.2f}", "tax_amount_in_millis": m(tax), "total_amount": f"{net + tax:.2f}", "total_amount_in_millis": m(net + tax)},
		"amount_due": {"amount": f"{net + tax:.2f}", "amount_in_hundredths": str(int(round((net + tax) * 100))), "currency": "INR"},
		"billing_period": "2026-09-01",
		"currency": "INR",
		"download_uri": f"https://business.facebook.com/ads/manage/billing_transaction/?pdf=true&id={node_id}",
		"due_date": "2026-11-01T00:00:00+0000",
		"invoice_date": f"{date}T00:00:00+0000",
		"payment_status": "UNPAID",
		"payment_term": "NET_30",
		"campaigns": {"data": [{"ad_account_id": account, "campaign_id": cid, "campaign_name": name, "billed_amount_details": {"net_amount": f"{amt:.3f}", "net_amount_in_millis": m(amt)}} for cid, name, amt in camps]},
	}


class FakeMeta:
	def __init__(self):
		self.rows = {"INV": [], "CM": [], "DM": []}
		self.calls = []
		self.fail = None

	def __call__(self, url, params):
		self.calls.append((url, dict(params or {})))
		if self.fail:
			return self.fail
		assert f"/v26.0/{BUSINESS}/business_invoices" in url, url
		assert params["access_token"] == TOKEN
		return 200, {"data": self.rows.get(params["type"], []), "paging": {}}, {}


def google_invoice(inv_id, net, tax, inv_type="INVOICE", corrected=None, regulatory=0.0, month="2026-10"):
	mic = lambda v: str(int(round(v * 1_000_000)))
	sign = -1 if inv_type == "CREDIT_MEMO" else 1
	inv = {
		"resourceName": f"customers/{CID}/invoices/{inv_id}",
		"id": inv_id,
		"type": inv_type,
		"billingSetup": f"customers/{CID}/billingSetups/{SETUP_ID}",
		"paymentsAccountId": "1234-5678-9012-3456",
		"paymentsProfileId": "1111-2222-3333",
		"issueDate": f"{month}-03",
		"dueDate": f"{month}-28",
		"serviceDateRange": {"startDate": "2026-09-01", "endDate": "2026-09-30"},
		"currencyCode": "INR",
		"subtotalAmountMicros": mic(sign * net),
		"taxAmountMicros": mic(sign * tax),
		"totalAmountMicros": mic(sign * (net + tax + regulatory)),
		"regulatoryCostsSubtotalAmountMicros": mic(sign * regulatory),
		"pdfUrl": f"https://ads.google.com/payments/apis-secure/doc/invoice?id={inv_id}",
		"accountSummaries": [{"customer": f"customers/{CID}", "subtotalAmountMicros": mic(sign * net), "taxAmountMicros": mic(sign * tax), "totalAmountMicros": mic(sign * (net + tax))}],
	}
	if corrected:
		inv["correctedInvoice"] = f"customers/{CID}/invoices/{corrected}"
	return inv


class FakeGoogle:
	def __init__(self):
		self.invoices = []
		self.calls = []

	def __call__(self, method, url, headers=None, json_body=None, form=None, timeout=60):
		self.calls.append((method, url))
		if url == google_auth.TOKEN_URI:
			return 200, {"access_token": "ya29.QA_BILLS_ACCESS", "expires_in": 3600, "scope": google_auth.ADWORDS_SCOPE, "token_type": "Bearer"}, {}
		assert headers["Authorization"] == "Bearer ya29.QA_BILLS_ACCESS"
		if url.endswith(f"customers/{CID}/googleAds:search"):
			assert "FROM billing_setup" in json_body["query"]
			return 200, {"results": [{"billingSetup": {"resourceName": f"customers/{CID}/billingSetups/{SETUP_ID}", "id": SETUP_ID, "status": "APPROVED", "paymentsAccountInfo": {"paymentsAccountId": "1234-5678-9012-3456", "paymentsProfileId": "1111-2222-3333", "paymentsProfileName": "QA Payments Profile"}}}]}, {}
		u = urlparse(url)
		if u.path.endswith(f"customers/{CID}/invoices") and method == "GET":
			q = parse_qs(u.query)
			assert q["billingSetup"] == [f"customers/{CID}/billingSetups/{SETUP_ID}"], q
			month = f"{q['issueYear'][0]}-{bill_sources.MONTHS.index(q['issueMonth'][0]) + 1:02d}"
			return 200, {"invoices": [i for i in self.invoices if i["issueDate"].startswith(month)]}, {}
		return 404, {"error": {"code": 404, "status": "NOT_FOUND", "message": url}}, {}


def _pdf_bytes() -> bytes:
	from io import BytesIO

	from pypdf import PdfWriter

	w = PdfWriter()
	w.add_blank_page(width=72, height=72)
	buf = BytesIO()
	w.write(buf)
	return buf.getvalue()


PDF = _pdf_bytes()


def fake_pdf(url, headers=None, params=None):
	return 200, PDF, "application/pdf"


# ─── fixtures ─────────────────────────────────────────────────────────────────

def _company_a():
	return frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]


def _ensure_company_b():
	from excom.excom_ads.tests.test_ads_permissions import _ensure_company_b as ensure

	ensure()


def _tds_account(company):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = frappe.db.get_value("Account", {"company": company, "account_name": f"{P} TDS Payable"}, "name")
	if name:
		return name
	parent = frappe.db.get_value("Account", {"company": company, "account_name": "Duties and Taxes", "is_group": 1}, "name") or frappe.db.get_value("Account", {"company": company, "root_type": "Liability", "is_group": 1}, "name")
	return frappe.get_doc({"doctype": "Account", "account_name": f"{P} TDS Payable", "parent_account": parent, "company": company, "account_type": "Tax", "root_type": "Liability", "is_group": 0}).insert(ignore_permissions=True).name or f"{P} TDS Payable - {abbr}"


def _address(title, company=None, supplier=None, gstin=None, state=None, country="India", category="Registered Regular"):
	d = {"doctype": "Address", "address_title": title, "address_type": "Billing", "address_line1": "1 QA Road", "city": state or "Dublin", "state": state, "country": country, "is_your_company_address": 1 if company else 0, "links": [{"link_doctype": "Company" if company else "Supplier", "link_name": company or supplier}]}
	if gstin:
		d.update({"gstin": gstin, "gst_category": category})
		if state == "Haryana":
			d["pincode"] = "122001"
		if state == "Karnataka":
			d["pincode"] = "560001"
	elif country != "India":
		d["gst_category"] = "Overseas"
	return frappe.get_doc(d).insert(ignore_permissions=True).name


def _pis():
	return frappe.get_all("Purchase Invoice", filters={"supplier": ["in", list(SUPPLIERS)]}, fields=["name", "docstatus", "is_return"], order_by="is_return desc, creation desc")


class TestAdsBills(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		if not _ic():
			cls.skip_reason = "india_compliance is not installed on this site"
			return
		cls.skip_reason = None
		cls._neutralise_ambient_hooks()
		cls.company = _company_a()
		_ensure_company_b()
		for c in (cls.company, COMPANY_B):
			if not frappe.get_all("Purchase Taxes and Charges Template", filters={"company": c, "tax_category": "Reverse Charge In-State"}, limit=1):
				cls.skip_reason = f"India Compliance GST templates are not set up for {c}"
				return
		cls._clean()
		if not cls._cover_fiscal_year():
			cls.skip_reason = f"no Fiscal Year covers {BILL_DATE}"
			return
		cls.prior_settings = {k: frappe.db.get_single_value("Ads Settings", k) for k in ("bills_auto_submit", "bill_naming_series", "bill_return_naming_series", "bill_service_item", "gstr2b_missing_after_days")}
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS_A, "company": cls.company})
		ins({"doctype": "Ads Workspace", "workspace_name": WS_B, "company": COMPANY_B})
		cls.addr_hr = _address(f"{P} HR", company=cls.company, gstin=GSTIN_HR, state="Haryana")
		cls.addr_ka = _address(f"{P} KA", company=cls.company, gstin=GSTIN_KA, state="Karnataka")
		cls.addr_b = _address(f"{P} B HR", company=COMPANY_B, gstin=GSTIN_B, state="Haryana")
		ins({"doctype": "Item", "item_code": ITEM, "item_name": ITEM, "item_group": "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups", "stock_uom": "Nos", "is_stock_item": 0, "is_purchase_item": 1, "gst_hsn_code": "998365"})
		twc = {"doctype": "Tax Withholding Category", "name": TWC, "category_name": TWC, "rates": [{"from_date": "2026-04-01", "to_date": "2027-03-31", "tax_withholding_rate": 2, "single_threshold": 1, "cumulative_threshold": 1}], "accounts": [{"company": cls.company, "account": _tds_account(cls.company)}, {"company": COMPANY_B, "account": _tds_account(COMPANY_B)}]}
		if frappe.get_meta("Tax Withholding Category").has_field("tax_deduction_basis"):
			twc["tax_deduction_basis"] = "Net Total"
		ins(twc)
		sup = lambda name, cat, gstin=None, country="India", rc=0: ins({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company", "supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"), "country": country, "gst_category": cat, "gstin": gstin, "is_reverse_charge_applicable": rc})
		sup(SUP_FB, "Registered Regular", GSTIN_FB)
		sup(SUP_GOOGLE, "Registered Regular", GSTIN_GOOGLE)
		sup(SUP_IE, "Overseas", country="Ireland", rc=1)
		_address(f"{P} FB India", supplier=SUP_FB, gstin=GSTIN_FB, state="Haryana")
		_address(f"{P} Google India", supplier=SUP_GOOGLE, gstin=GSTIN_GOOGLE, state="Haryana")
		_address(f"{P} Meta Ireland", supplier=SUP_IE, country="Ireland")
		cmap = lambda key, supplier, address, company=None, held=None, **kw: ins({"doctype": "Ads Bill Account Map", "provider": kw.pop("provider", "Meta"), "key_type": kw.pop("key_type", "Ad Account"), "platform_key": key, "company": company or cls.company, "workspace": WS_B if company == COMPANY_B else WS_A, "billing_address": address, "platform_gstin": held, "supplier": supplier, "item_code": ITEM, **kw}).name
		cls.map_in = cmap("act_9901", SUP_FB, cls.addr_hr, held=GSTIN_HR, tax_withholding_category=TWC)
		cls.map_ka = cmap("9903", SUP_FB, cls.addr_ka, held=GSTIN_KA, tax_withholding_category=TWC)
		cls.map_ie = cmap("9902", SUP_IE, cls.addr_hr, billing_entity="FBIE", tds_not_applicable=1)
		cls.map_bad = cmap("9904", SUP_FB, cls.addr_hr, held=GSTIN_KA, tax_withholding_category=TWC)
		cls.map_google = cmap(CID, SUP_GOOGLE, cls.addr_hr, held=GSTIN_HR, provider="Google", key_type="Google Ads Customer", tax_withholding_category=TWC)
		cls.map_b = cmap("9905", SUP_FB, cls.addr_b, company=COMPANY_B, held=GSTIN_B, tax_withholding_category=TWC)
		ins({"doctype": "Excom Meta Connection", "connection_name": META_CONN, "business_id": BUSINESS, "company": cls.company, "status": "Active", "app_id": "qa", "app_secret": "qa", "system_user_token": TOKEN})
		ins({"doctype": "Ads Connection", "connection_name": GOOGLE_CONN, "provider": "Google", "company": cls.company, "auth_mode": "OAuth Refresh Token", "oauth_client_id": "qa-client.apps.googleusercontent.com", "oauth_client_secret": "GOCSPX-qa-bills-secret", "refresh_token": "1//0qa-bills-refresh-token-abcdefghijklmnop"})
		ins({"doctype": "Ads Bill Source", "source_name": SRC_META, "provider": "Meta", "company": cls.company, "workspace": WS_A, "meta_connection": META_CONN, "lookback_months": 1})
		ins({"doctype": "Ads Bill Source", "source_name": SRC_GOOGLE, "provider": "Google", "company": cls.company, "workspace": WS_A, "google_connection": GOOGLE_CONN, "customer_id": CID, "lookback_months": 1})
		roles = {U_MAKER: ["Accounts User", "Purchase User", "Excom Marketing"], U_CHECKER: ["Accounts Manager", "Purchase User", "Excom Marketing"], U_NORIGHT: ["Accounts Manager", "Purchase User"], U_B: ["Accounts User", "Excom Marketing"], U_MKT: ["Excom Marketing"]}
		for u, rs in roles.items():
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": r} for r in rs]})
			ins({"doctype": "User Permission", "user": u, "allow": "Company", "for_value": COMPANY_B if u == U_B else cls.company, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_MAKER, "view_finance": 1, "approve_bills": 1})
		ins({"doctype": "Ads Access Profile", "user": U_CHECKER, "view_finance": 1, "approve_bills": 1})
		for k, v in {"bills_auto_submit": 0, "bill_naming_series": SERIES, "bill_return_naming_series": RSERIES, "bill_service_item": None, "gstr2b_missing_after_days": 45}.items():
			frappe.db.set_single_value("Ads Settings", k, v)
		frappe.db.commit()
		frappe.clear_cache()

	@classmethod
	def _cover_fiscal_year(cls) -> bool:
		"""The bill date's fiscal year must apply to both test companies (a Fiscal Year limited to
		other companies is extended for the run and put back afterwards)."""
		from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

		cls.fy_rows_added = []
		for c in (cls.company, COMPANY_B):
			try:
				get_fiscal_year(BILL_DATE, company=c)
				continue
			except FiscalYearError:
				pass
			fy = frappe.db.get_value("Fiscal Year", {"year_start_date": ["<=", BILL_DATE], "year_end_date": [">=", BILL_DATE], "disabled": 0}, "name")
			if not fy:
				return False
			doc = frappe.get_doc("Fiscal Year", fy)
			doc.append("companies", {"company": c})
			doc.save(ignore_permissions=True)
			cls.fy_rows_added.append((fy, c))
			frappe.cache.delete_value("fiscal_years")
		return True

	@classmethod
	def _neutralise_ambient_hooks(cls):
		"""A test must not depend on how this site happens to be configured (see tests/run.py).
		location_based_series, when installed, refuses every Purchase Invoice unless the site has a
		"Location" accounting dimension; that is a site setup choice unrelated to ad bills."""
		cls._restore = []
		if "location_based_series" in frappe.get_installed_apps() and not frappe.db.exists("Accounting Dimension", {"document_type": "Location", "disabled": 0}):
			import location_based_series.events.validation as lbs

			cls._restore.append((lbs, "validate_doc", lbs.validate_doc))
			lbs.validate_doc = lambda doc, method=None: None
		if "location_based_series" in frappe.get_installed_apps():
			# ...and names Purchase Invoices from the Location code instead of the naming series.
			import location_based_series.events.naming as lbs_naming

			cls._restore.append((lbs_naming, "custom_autoname", lbs_naming.custom_autoname))
			lbs_naming.custom_autoname = lambda doc, method=None: None
		# Overseas suppliers (import of services) need IC's overseas switch; restored afterwards.
		cls.prior_overseas = frappe.db.get_single_value("GST Settings", "enable_overseas_transactions")
		frappe.db.set_single_value("GST Settings", "enable_overseas_transactions", 1)
		frappe.clear_document_cache("GST Settings", "GST Settings")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		if getattr(cls, "skip_reason", None) is None and hasattr(cls, "prior_settings"):
			for k, v in cls.prior_settings.items():
				frappe.db.set_single_value("Ads Settings", k, v)
			cls._clean()
		for module, attr, value in getattr(cls, "_restore", []):
			setattr(module, attr, value)
		for fy, c in getattr(cls, "fy_rows_added", []):
			frappe.db.delete("Fiscal Year Company", {"parent": fy, "company": c})
			frappe.cache.delete_value("fiscal_years")
		if hasattr(cls, "prior_overseas"):
			frappe.db.set_single_value("GST Settings", "enable_overseas_transactions", cls.prior_overseas or 0)
			frappe.clear_document_cache("GST Settings", "GST Settings")
			frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		for r in _pis():
			if r.docstatus == 1:
				pi = frappe.get_doc("Purchase Invoice", r.name)
				pi.flags.ignore_permissions = True
				pi.flags.ignore_links = True
				pi.cancel()
		for r in _pis():
			for dt in ("GL Entry", "Payment Ledger Entry"):
				frappe.db.delete(dt, {"voucher_no": r.name})
			if frappe.db.exists("DocType", "Tax Withholding Entry"):
				frappe.db.delete("Tax Withholding Entry", {"parent": r.name})
			frappe.delete_doc("Purchase Invoice", r.name, force=True, ignore_permissions=True, delete_permanently=True)
		docs = frappe.get_all("Ads Bill Document", filters={"supplier": ["in", list(SUPPLIERS)]}, pluck="name")
		docs += frappe.get_all("Ads Bill Document", filters={"bill_source": ["like", f"{P}%"]}, pluck="name")
		docs += frappe.get_all("Ads Bill Document", filters={"bill_no": ["like", "QAB%"]}, pluck="name")
		docs = sorted(set(docs))
		maps = frappe.get_all("Ads Bill Account Map", filters={"supplier": ["in", list(SUPPLIERS)]}, pluck="name")
		logs = frappe.get_all("Ads TDS Certificate Log", filters={"supplier": ["in", list(SUPPLIERS)]}, pluck="name")
		srcs = frappe.get_all("Ads Bill Source", filters={"source_name": ["like", f"{P}%"]}, pluck="name")
		refs = docs + maps + logs + srcs
		frappe.db.delete("Ads Alert", {"reference_name": ["in", refs or ["-"]]})
		frappe.db.delete("Ads Alert", {"dedupe_key": ["like", "2b-orphan:QA%"]})
		# Orphan-bill alerts are keyed by the GST Inward Supply name (a hash): drop any whose 2B row is gone,
		# so a run that stopped half-way leaves nothing behind either.
		for a in frappe.get_all("Ads Alert", filters={"dedupe_key": ["like", "2b-orphan:%"]}, fields=["name", "dedupe_key"]):
			if not frappe.db.exists("GST Inward Supply", a.dedupe_key.split(":", 1)[1]):
				frappe.db.delete("Ads Alert", {"name": a.name})
		frappe.db.delete("Ads Alert", {"title": ["like", f"%{P}%"]})
		frappe.db.delete("File", {"attached_to_doctype": "Ads Bill Document", "attached_to_name": ["in", docs or ["-"]]})
		frappe.db.delete("Ads Bill Document Line", {"parent": ["in", docs or ["-"]]})
		frappe.db.delete("Ads Bill Document", {"name": ["in", docs or ["-"]]})
		frappe.db.delete("Ads Bill Account Map", {"name": ["in", maps or ["-"]]})
		frappe.db.delete("Ads TDS Certificate Log", {"name": ["in", logs or ["-"]]})
		frappe.db.delete("Ads Bill Source", {"name": ["in", srcs or ["-"]]})
		if frappe.db.exists("DocType", "GST Inward Supply"):
			for n in frappe.get_all("GST Inward Supply", filters={"supplier_gstin": ["in", [GSTIN_FB, GSTIN_GOOGLE]], "company_gstin": ["in", [GSTIN_HR, GSTIN_KA, GSTIN_B]]}, pluck="name"):
				frappe.delete_doc("GST Inward Supply", n, force=True, ignore_permissions=True)
		for dt, name in (("Excom Meta Connection", META_CONN), ("Ads Connection", GOOGLE_CONN)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
		addrs = frappe.get_all("Address", filters={"address_title": ["like", f"{P}%"]}, pluck="name")
		for a in addrs:
			frappe.delete_doc("Address", a, force=True, ignore_permissions=True)
		for s in SUPPLIERS:
			if frappe.db.exists("Supplier", s):
				frappe.delete_doc("Supplier", s, force=True, ignore_permissions=True)
		if frappe.db.exists("Tax Withholding Category", TWC):
			frappe.delete_doc("Tax Withholding Category", TWC, force=True, ignore_permissions=True)
		for acc in frappe.get_all("Account", filters={"account_name": f"{P} TDS Payable"}, pluck="name"):
			if not frappe.db.exists("GL Entry", {"account": acc}):
				frappe.delete_doc("Account", acc, force=True, ignore_permissions=True)
		if frappe.db.exists("Item", ITEM):
			frappe.delete_doc("Item", ITEM, force=True, ignore_permissions=True)
		frappe.db.delete("Ads Workspace", {"workspace_name": ["in", [WS_A, WS_B]]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", list(USERS)]})
		frappe.db.delete("User Permission", {"user": ["in", list(USERS)]})
		frappe.db.delete("Notification Log", {"for_user": ["in", list(USERS)]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.sql("delete from `tabSeries` where name like 'QAB-%%' or name like 'QAR-%%'")
		frappe.db.commit()

	def setUp(self):
		if self.skip_reason:
			self.skipTest(self.skip_reason)
		frappe.set_user("Administrator")
		self.meta = FakeMeta()
		self.google = FakeGoogle()
		self._saved = (meta.http_get, google_auth.http_request, bill_sources.http_download)
		meta.http_get = self.meta
		google_auth.http_request = self.google
		bill_sources.http_download = fake_pdf
		frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 0)

	def tearDown(self):
		meta.http_get, google_auth.http_request, bill_sources.http_download = self._saved
		frappe.set_user("Administrator")
		permissions.clear_cache()
		frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 0)
		frappe.db.commit()

	# ─── helpers ──────────────────────────────────────────────────────────────

	def _as(self, user):
		frappe.set_user(user)
		permissions.clear_cache()

	def _run_meta(self, *rows, cm=()):
		self.meta.rows["INV"] = list(rows)
		self.meta.rows["CM"] = list(cm)
		out = bills.run_source(SRC_META)
		self.assertNotIn("error", out, out)
		return out

	def _doc(self, provider, node_id):
		return frappe.get_doc("Ads Bill Document", bills.bill_document_name(provider, node_id))

	def _pi(self, doc):
		self.assertTrue(doc.purchase_invoice, doc.differences or doc.last_error)
		return frappe.get_doc("Purchase Invoice", doc.purchase_invoice)

	def _gst(self, pi):
		from excom.excom_ads.bill_gst import gst_breakup

		return gst_breakup(pi)

	def _approve(self, doc, user=U_CHECKER, ack=1):
		self._as(user)
		try:
			return api_bills.approve_bill(doc.name, acknowledge_differences=ack)
		finally:
			self._as("Administrator")

	def _manual(self, user, company, account_map, bill_no, net=5000.0, tax=900.0, **kw):
		self._as(user)
		doc = frappe.get_doc({"doctype": "Ads Bill Document", "provider": "Meta", "document_type": "Invoice", "company": company, "account_map": account_map, "bill_no": bill_no, "bill_date": BILL_DATE, "currency": "INR", "net_amount": net, "tax_amount": tax, "billing_entity": "Facebook India Online Services Pvt Ltd", **kw})
		doc.insert()
		api_bills.process_bill(doc.name)
		self._as("Administrator")
		return frappe.get_doc("Ads Bill Document", doc.name)

	# ─── Meta invoice → draft PI ──────────────────────────────────────────────

	def test_meta_invoice_in_state_builds_draft_with_cgst_sgst_and_tds(self):
		self._run_meta(meta_invoice("8800001", "3199000001", "9901", 10000.0, 1800.0, campaigns=[("c1", f"{P} Diwali", 6000.333), ("c2", f"{P} Leads", 3999.665)]))
		doc = self._doc("Meta", "8800001")
		self.assertEqual(doc.status, bills.DRAFT, doc.differences)
		self.assertEqual((doc.source_type, doc.supplier, doc.company, doc.account_map), ("API", SUP_FB, self.company, self.map_in))
		self.assertTrue(doc.attachment, "the platform PDF is attached")
		self.assertEqual(json.loads(doc.raw_payload)["invoice_id"], "3199000001", "the raw API record is kept")
		pi = self._pi(doc)
		self.assertEqual((pi.docstatus, pi.supplier, pi.bill_no, str(pi.bill_date), pi.company_gstin, pi.supplier_gstin), (0, SUP_FB, "3199000001", BILL_DATE, GSTIN_HR, GSTIN_FB))
		self.assertEqual(len(pi.items), 2, "one line per campaign")
		self.assertAlmostEqual(sum(i.amount for i in pi.items), 10000.0, places=2)
		self.assertEqual(round(pi.net_total, 2), 10000.0)
		g = self._gst(pi)
		self.assertEqual((g["cgst"], g["sgst"], g["igst"], g["charged"]), (900.0, 900.0, 0.0, 1800.0), "same state → CGST + SGST from IC")
		self.assertEqual(pi.place_of_supply, "06-Haryana")
		self.assertEqual(pi.itc_classification, "All Other ITC")
		self.assertTrue(pi.apply_tds)
		cats = {pi.get("tax_withholding_category")} | {i.get("tax_withholding_category") for i in pi.items}
		self.assertIn(TWC, cats)
		self.assertLessEqual(len(pi.name), 16)
		self.assertTrue(pi.name.startswith("QAB-"))

	def test_refetch_is_idempotent(self):
		row = meta_invoice("8800002", "3199000002", "9901", 2000.0, 360.0)
		self._run_meta(row)
		first = self._doc("Meta", "8800002")
		self._run_meta(row)
		self._run_meta(row)
		self.assertEqual(frappe.db.count("Ads Bill Document", {"platform_invoice_id": "8800002"}), 1)
		self.assertEqual(frappe.db.count("Purchase Invoice", {"bill_no": "3199000002", "supplier": SUP_FB}), 1)
		self.assertEqual(self._doc("Meta", "8800002").purchase_invoice, first.purchase_invoice)

	def test_unreconciled_lines_and_platform_changes_go_to_review(self):
		frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 1)
		row = meta_invoice("8800026", "3199000026", "9901", 1000.0, 180.0, campaigns=[("c1", f"{P} A", 700.0), ("c2", f"{P} B", 200.0)])
		self._run_meta(row)
		doc = self._doc("Meta", "8800026")
		self.assertEqual(doc.status, bills.REVIEW)
		self.assertIn("separate line", doc.differences)
		pi = self._pi(doc)
		self.assertEqual(pi.docstatus, 0)
		self.assertEqual([round(i.amount, 2) for i in pi.items], [700.0, 200.0, 100.0])
		self._run_meta(row)
		self.assertEqual(self._doc("Meta", "8800026").status, bills.REVIEW, "a re-check keeps the line finding: never auto-submitted")
		changed = meta_invoice("8800026", "3199000026", "9901", 1100.0, 198.0, campaigns=[("c1", f"{P} A", 1100.0)])
		self._run_meta(changed)
		doc = self._doc("Meta", "8800026")
		self.assertEqual((doc.status, round(doc.net_amount, 2)), (bills.REVIEW, 1000.0), "figures are never overwritten under a draft")
		self.assertIn("changed net_amount", doc.differences)
		self._run_meta(changed)
		self.assertEqual(self._doc("Meta", "8800026").differences.count("changed"), 1, "a repeat does not pile up notes")
		self.assertEqual(bills.process(doc.name), bills.REVIEW, "a re-check without rebuild keeps the finding")
		frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 0)
		self.assertEqual(bills.process(doc.name, rebuild=True), bills.DRAFT, frappe.db.get_value("Ads Bill Document", doc.name, "differences"))
		doc = self._doc("Meta", "8800026")
		self.assertEqual((round(doc.net_amount, 2), doc.platform_update), (1100.0, None))
		self.assertEqual(round(self._pi(doc).net_total, 2), 1100.0)
		self.assertEqual(frappe.db.count("Purchase Invoice", {"bill_no": "3199000026"}), 1, "the old draft was replaced")

	def test_inter_state_gets_igst(self):
		self._run_meta(meta_invoice("8800003", "3199000003", "9903", 4000.0, 720.0))
		doc = self._doc("Meta", "8800003")
		self.assertEqual(doc.status, bills.DRAFT, doc.differences)
		pi = self._pi(doc)
		g = self._gst(pi)
		self.assertEqual((g["igst"], g["cgst"], g["sgst"]), (720.0, 0.0, 0.0), "company GSTIN in Karnataka, supplier in Haryana → IGST")
		self.assertEqual(pi.company_gstin, GSTIN_KA)

	def test_overseas_supplier_is_reverse_charge_import_of_service(self):
		self._run_meta(meta_invoice("8800004", "3199000004", "9902", 5000.0, 0.0, entity="FBIE"))
		doc = self._doc("Meta", "8800004")
		self.assertEqual(doc.status, bills.DRAFT, doc.differences)
		pi = self._pi(doc)
		self.assertEqual((pi.is_reverse_charge, pi.gst_category, pi.itc_classification), (1, "Overseas", "Import Of Service"))
		g = self._gst(pi)
		self.assertEqual(g["charged"], 0.0, "the supplier charged no GST")
		self.assertEqual(g["rcm"], 900.0, "18% self-assessed under reverse charge")
		self.assertEqual(round(pi.grand_total, 2), 5000.0)
		from india_compliance.gst_india.utils import validate_invoice_number

		self.assertTrue(validate_invoice_number(pi, throw=False), f"{pi.name} must be a valid GST document number")

	def test_gstin_mismatch_needs_review_and_alerts(self):
		self._run_meta(meta_invoice("8800005", "3199000005", "9904", 1000.0, 180.0))
		doc = self._doc("Meta", "8800005")
		self.assertEqual(doc.status, bills.REVIEW)
		self.assertIn("GSTIN Mismatch", doc.differences)
		self.assertTrue(doc.purchase_invoice, "a draft is still built for the reviewer")
		alert = frappe.db.get_value("Ads Alert", {"dedupe_key": f"gstin-mismatch:{self.map_bad}", "status": ["!=", "Resolved"]}, ["alert_type", "severity", "company"], as_dict=True)
		self.assertEqual((alert.alert_type, alert.severity, alert.company), ("GSTIN Mismatch", "High", self.company))
		self.assertEqual(frappe.db.get_value("Ads Bill Account Map", self.map_bad, "gstin_status"), "Mismatch")
		review = frappe.db.get_value("Ads Alert", {"dedupe_key": f"bill-review:{doc.name}"}, "message")
		self.assertNotIn("1,000", review or "", "alert text carries no amounts (finance data stays on the bill)")

	def test_duplicate_bill_number_creates_nothing(self):
		self._run_meta(meta_invoice("8800006", "3199000006", "9901", 1000.0, 180.0), meta_invoice("8800007", "3199000006", "9901", 1000.0, 180.0))
		a, b = self._doc("Meta", "8800006"), self._doc("Meta", "8800007")
		self.assertEqual(sorted([a.status, b.status]), sorted([bills.DRAFT, bills.DUPLICATE]))
		dup = a if a.status == bills.DUPLICATE else b
		self.assertFalse(dup.purchase_invoice)
		self.assertEqual(frappe.db.count("Purchase Invoice", {"bill_no": "3199000006", "supplier": SUP_FB}), 1)

	def test_unknown_entity_and_missing_map(self):
		self._run_meta(meta_invoice("8800008", "3199000008", "9901", 1000.0, 180.0, entity="SOMETHING_ELSE"), meta_invoice("8800009", "3199000009", "7777", 1000.0, 180.0))
		unknown, unmapped = self._doc("Meta", "8800008"), self._doc("Meta", "8800009")
		self.assertEqual(unknown.status, bills.REVIEW)
		self.assertIn("not a known Meta legal entity", unknown.differences)
		self.assertEqual(unmapped.status, bills.REVIEW)
		self.assertFalse(unmapped.purchase_invoice, "no account map → no draft")
		self.assertIn("No Ads Bill Account Map", unmapped.differences)

	# ─── credit memos ─────────────────────────────────────────────────────────

	def test_meta_credit_memo_becomes_debit_note_against_original(self):
		self._run_meta(meta_invoice("8800010", "3199000010", "9901", 3000.0, 540.0))
		orig = self._doc("Meta", "8800010")
		cm = meta_invoice("8800011", "3199000011", "9901", 500.0, 90.0, inv_type="CM", campaigns=[])
		self._run_meta(cm=[cm])
		credit = self._doc("Meta", "8800011")
		self.assertEqual((credit.document_type, credit.status), ("Credit Memo", bills.REVIEW))
		self.assertIn("Original Bill Number", credit.differences, "Meta does not say which invoice a credit memo corrects")
		self._approve(orig)
		credit.original_bill_no = "3199000010"
		credit.save()
		self.assertEqual(credit.prepared_by, "Administrator", "typing the original makes the editor a maker")
		self.assertEqual(bills.process(credit.name), bills.DRAFT, frappe.db.get_value("Ads Bill Document", credit.name, "differences"))
		credit.reload()
		note = self._pi(credit)
		self.assertEqual((note.is_return, note.return_against), (1, orig.purchase_invoice))
		self.assertEqual(round(note.net_total, 2), -500.0)
		self.assertEqual(self._gst(note)["charged"], 90.0)
		self.assertTrue(note.name.startswith("QAR-") and len(note.name) <= 16)

	def test_google_invoice_and_corrected_credit_memo(self):
		# GST is charged on the regulatory costs too: 18% of 20,100.
		self.google.invoices = [google_invoice("4100000001", 20000.0, 3618.0, regulatory=100.0)]
		out = bills.run_source(SRC_GOOGLE)
		self.assertNotIn("error", out, out)
		doc = self._doc("Google", "4100000001")
		self.assertEqual(doc.status, bills.DRAFT, doc.differences)
		pi = self._pi(doc)
		self.assertEqual([round(i.amount, 2) for i in pi.items], [20000.0, 100.0], "account summary + regulatory costs line")
		self.assertEqual(round(pi.net_total, 2), 20100.0)
		self.assertEqual(self._gst(pi)["cgst"], 1809.0)
		self.assertTrue(doc.attachment)
		self._approve(doc)
		self.google.invoices.append(google_invoice("4100000002", 1000.0, 180.0, inv_type="CREDIT_MEMO", corrected="4100000001"))
		bills.run_source(SRC_GOOGLE)
		credit = self._doc("Google", "4100000002")
		self.assertEqual((credit.document_type, credit.original_bill_no, credit.status), ("Credit Memo", "4100000001", bills.DRAFT), credit.differences)
		note = self._pi(credit)
		self.assertEqual((note.is_return, note.return_against), (1, pi.name))

	# ─── auto-submit ──────────────────────────────────────────────────────────

	def test_auto_submit_only_when_every_rule_holds_and_switch_is_on(self):
		self._run_meta(meta_invoice("8800012", "3199000012", "9901", 1500.0, 270.0))
		self.assertEqual(self._doc("Meta", "8800012").status, bills.DRAFT, "switch off: never submitted")
		frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 1)
		self._run_meta(meta_invoice("8800013", "3199000013", "9901", 1500.0, 270.0), meta_invoice("8800014", "3199000014", "9904", 1500.0, 270.0), meta_invoice("8800015", "3199000015", "9901", 1500.0, 200.0))
		clean, mismatch, wrong_tax = self._doc("Meta", "8800013"), self._doc("Meta", "8800014"), self._doc("Meta", "8800015")
		self.assertEqual((clean.status, clean.auto_submitted), (bills.SUBMITTED, 1))
		self.assertEqual(self._pi(clean).docstatus, 1)
		self.assertEqual(mismatch.status, bills.REVIEW, "GSTIN mismatch is never auto-submitted")
		self.assertEqual(self._pi(mismatch).docstatus, 0)
		self.assertEqual(wrong_tax.status, bills.REVIEW)
		self.assertIn("GST: Purchase Invoice", wrong_tax.differences, "tax split must match the invoice to the rupee")
		manual = self._manual(U_MAKER, self.company, self.map_in, "QAB-MAN-1", net=1000.0, tax=180.0)
		self.assertEqual(manual.status, bills.DRAFT, "a manual bill always waits for a second person")
		self.assertEqual(self._pi(manual).docstatus, 0)
		self.assertEqual(self._doc("Meta", "8800012").status, bills.DRAFT, "an existing draft is not submitted by a refetch")

	def test_no_tds_category_blocks_auto_submit(self):
		frappe.db.set_value("Ads Bill Account Map", self.map_ka, "tax_withholding_category", None)
		try:
			frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 1)
			self._run_meta(meta_invoice("8800016", "3199000016", "9903", 1000.0, 180.0))
			doc = self._doc("Meta", "8800016")
			self.assertEqual(doc.status, bills.REVIEW)
			self.assertIn("TDS is not set", doc.differences)
		finally:
			frappe.db.set_value("Ads Bill Account Map", self.map_ka, "tax_withholding_category", TWC)

	# ─── approval: right + maker-checker ──────────────────────────────────────

	def test_approve_needs_right_and_a_second_person(self):
		manual = self._manual(U_MAKER, self.company, self.map_in, "QAB-MAN-2", net=2000.0, tax=360.0)
		self.assertEqual((manual.status, manual.prepared_by), (bills.DRAFT, U_MAKER))
		self.assertRaises(frappe.PermissionError, self._approve, manual, U_NORIGHT)
		self.assertRaises(frappe.PermissionError, self._approve, manual, U_MAKER)
		self.assertRaises(frappe.PermissionError, self._approve, manual, U_MKT)
		res = self._approve(manual, U_CHECKER)
		self.assertEqual(res["status"], bills.SUBMITTED)
		manual.reload()
		self.assertEqual((manual.status, manual.approved_by, self._pi(manual).docstatus), (bills.SUBMITTED, U_CHECKER, 1))

		self._run_meta(meta_invoice("8800017", "3199000017", "9901", 1200.0, 216.0))
		api_doc = self._doc("Meta", "8800017")
		self.assertFalse(api_doc.prepared_by)
		self._as(U_CHECKER)
		pi = frappe.get_doc("Purchase Invoice", api_doc.purchase_invoice)
		pi.remarks = "edited by the checker"
		pi.save()
		self._as("Administrator")
		self.assertRaises(frappe.PermissionError, self._approve, api_doc, U_CHECKER)
		self.assertEqual(self._approve(api_doc, U_MAKER)["status"], bills.SUBMITTED)

	def test_approve_with_differences_needs_acknowledgement(self):
		self._run_meta(meta_invoice("8800018", "3199000018", "9904", 1000.0, 180.0))
		doc = self._doc("Meta", "8800018")
		self.assertEqual(doc.status, bills.REVIEW)
		self.assertRaises(frappe.ValidationError, self._approve, doc, U_CHECKER, 0)
		self.assertEqual(self._approve(doc, U_CHECKER, 1)["status"], bills.SUBMITTED)
		self.assertIn("Approved with these differences", frappe.db.get_value("Ads Bill Document", doc.name, "differences"))

	# ─── scope ────────────────────────────────────────────────────────────────

	def test_company_b_user_sees_nothing_of_company_a(self):
		self._run_meta(meta_invoice("8800019", "3199000019", "9901", 1000.0, 180.0))
		a_doc = self._doc("Meta", "8800019")
		b_doc = self._manual("Administrator", COMPANY_B, self.map_b, "QAB-B-1", net=800.0, tax=144.0)
		self.assertEqual(b_doc.status, bills.DRAFT, b_doc.differences)
		self.assertEqual(self._pi(b_doc).company_gstin, GSTIN_B)
		self._as(U_B)
		names = {r["name"] for r in api_bills.review_queue()["rows"]}
		self.assertIn(b_doc.name, names)
		self.assertNotIn(a_doc.name, names)
		self.assertEqual(set(frappe.get_list("Ads Bill Document", filters={"name": ["in", [a_doc.name, b_doc.name]]}, pluck="name")), {b_doc.name})
		self.assertFalse(frappe.has_permission("Ads Bill Document", "read", doc=frappe.get_doc("Ads Bill Document", a_doc.name)))
		self.assertEqual(set(frappe.get_list("Ads Bill Account Map", filters={"name": ["in", [self.map_in, self.map_b]]}, pluck="name")), {self.map_b})
		self.assertRaises(frappe.PermissionError, api_bills.process_bill, a_doc.name)
		self.assertRaises(frappe.PermissionError, api_bills.approve_bill, a_doc.name)
		self._as(U_MAKER)
		self.assertNotIn(b_doc.name, {r["name"] for r in api_bills.review_queue()["rows"]})
		self._as(U_MKT)
		self.assertRaises(frappe.PermissionError, api_bills.review_queue)
		self.assertEqual(frappe.get_list("Ads Bill Document", filters={"name": a_doc.name}, pluck="name"), [], "no view_finance → no bills")

	# ─── masters, naming, extension point ─────────────────────────────────────

	def test_supplier_proposal_and_confirmed_creation(self):
		keys = {p["key"]: p for p in api_bills.propose_suppliers()}
		self.assertEqual(set(keys), set(bill_suppliers.LEGAL_ENTITIES))
		self.assertEqual(keys["meta_in"]["exists"], SUP_FB, "found by GSTIN, never duplicated")
		if not keys["google_in"]["exists"]:
			self.assertEqual(keys["google_in"]["needs"], ["gstin"], "Google India's GSTIN comes from its invoice")
		self.assertRaises(frappe.ValidationError, api_bills.create_suppliers, ["meta_ie"], confirm=0)
		self.assertRaises(frappe.ValidationError, bill_suppliers.create, ["linkedin_in"], {"linkedin_in": GSTIN_HR})  # Haryana GSTIN for a Karnataka entity
		self._as(U_NORIGHT)
		self.assertRaises(frappe.PermissionError, api_bills.propose_suppliers)
		self._as("Administrator")
		existing = frappe.db.get_value("Supplier", {"supplier_name": "LinkedIn Singapore Pte Ltd"}, "name")
		created = api_bills.create_suppliers(["linkedin_sg"], confirm=1)
		try:
			if not existing:
				self.assertEqual(len(created), 1)
				sup = frappe.get_doc("Supplier", created[0]["supplier"])
				self.assertEqual((sup.gst_category, sup.country, sup.is_reverse_charge_applicable), ("Overseas", "Singapore", 1))
			self.assertEqual(api_bills.create_suppliers(["linkedin_sg"], confirm=1), [], "idempotent")
		finally:
			for c in created:
				frappe.delete_doc("Supplier", c["supplier"], force=True, ignore_permissions=True)

	def test_naming_series_and_document_number_length(self):
		self.assertEqual(bills.series_length("ADB-.YY.-.#####"), 12)
		self.assertGreater(bills.series_length("ACC-PINV-.YYYY.-.#####"), 16)
		frappe.db.set_single_value("Ads Settings", "bill_naming_series", "ACC-PINV-.YYYY.-.#####")
		try:
			self._run_meta(meta_invoice("8800020", "3199000020", "9901", 1000.0, 180.0))
			doc = self._doc("Meta", "8800020")
			self.assertEqual(doc.status, bills.REVIEW)
			self.assertIn("16 characters", doc.differences)
			self.assertFalse(doc.purchase_invoice)
		finally:
			frappe.db.set_single_value("Ads Settings", "bill_naming_series", SERIES)
		self.assertEqual(bills.process(doc.name), bills.DRAFT)
		self.assertLessEqual(len(frappe.db.get_value("Ads Bill Document", doc.name, "purchase_invoice")), 16)
		self.assertEqual(bills.EXTRACTORS, {}, "release 1 never calls an AI extractor")

	def test_manual_bill_cannot_claim_api_or_be_changed_after_submit(self):
		self._run_meta(meta_invoice("8800025", "3199000025", "9901", 100.0, 18.0))
		other_pi = self._doc("Meta", "8800025").purchase_invoice
		self._as(U_MAKER)
		doc = frappe.get_doc({"doctype": "Ads Bill Document", "provider": "Meta", "company": self.company, "account_map": self.map_in, "bill_no": "QAB-MAN-3", "bill_date": BILL_DATE, "currency": "INR", "net_amount": 100, "tax_amount": 18, "source_type": "API", "status": "Submitted", "purchase_invoice": other_pi, "approved_by": U_CHECKER})
		doc.insert()
		self.assertEqual((doc.source_type, doc.status, doc.purchase_invoice, doc.approved_by, doc.prepared_by), ("Manual", "Fetched", None, None, U_MAKER))
		doc.status = "Submitted"
		doc.purchase_invoice = other_pi
		doc.save()
		self.assertEqual((doc.status, doc.purchase_invoice), ("Fetched", None), "pipeline fields cannot be written by a person")
		api_bills.process_bill(doc.name)
		self._as(U_CHECKER)
		api_bills.approve_bill(doc.name, acknowledge_differences=1)
		self._as(U_MAKER)
		doc = frappe.get_doc("Ads Bill Document", doc.name)
		self.assertEqual(doc.status, bills.SUBMITTED)
		doc.net_amount = 99
		self.assertRaises(frappe.ValidationError, doc.save)
		self._as("Administrator")

	# ─── GSTR-2B and TDS certificates ─────────────────────────────────────────

	def test_gstr2b_match_missing_and_orphan(self):
		self._run_meta(meta_invoice("8800021", "3199000021", "9901", 1000.0, 180.0), meta_invoice("8800022", "3199000022", "9901", 1000.0, 180.0), meta_invoice("8800023", "3199000023", "9902", 1000.0, 0.0, entity="FBIE"))
		matched, missing, rc = (self._doc("Meta", n) for n in ("8800021", "8800022", "8800023"))
		for d in (matched, missing, rc):
			self._approve(d)
		gis = lambda **kw: frappe.get_doc({"doctype": "GST Inward Supply", "company": self.company, "company_gstin": GSTIN_HR, "supplier_gstin": GSTIN_FB, "supplier_name": "FB India", "doc_type": "Invoice", "classification": "B2B", "is_downloaded_from_2b": 1, "return_period_2b": "102026", "bill_date": BILL_DATE, "document_value": 1180, **kw}).insert(ignore_permissions=True)
		gis(bill_no="3199000021", link_doctype="Purchase Invoice", link_name=frappe.db.get_value("Ads Bill Document", matched.name, "purchase_invoice"), match_status="Exact Match")
		orphan = gis(bill_no="3199099999")
		frappe.db.set_single_value("Ads Settings", "gstr2b_missing_after_days", 0)
		try:
			out = bills.check_gstr2b()
		finally:
			frappe.db.set_single_value("Ads Settings", "gstr2b_missing_after_days", 45)
		st = lambda d: frappe.db.get_value("Ads Bill Document", d.name, "gstr2b_status")
		self.assertEqual((st(matched), st(missing), st(rc)), ("Matched", "Missing", "Not Applicable"))
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"2b-missing:{missing.name}", "alert_type": "Bill Missing"}))
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"2b-orphan:{orphan.name}", "alert_type": "Bill Missing", "company": self.company}))
		self.assertGreaterEqual(out["orphans"], 1)
		frappe.db.delete("Ads Alert", {"dedupe_key": f"2b-orphan:{orphan.name}"})

	def test_tds_certificate_log_and_due_alert(self):
		self._run_meta(meta_invoice("8800024", "3199000024", "9901", 50000.0, 9000.0))
		doc = self._doc("Meta", "8800024")
		self._approve(doc)
		label, start, end, deadline = bills.quarter_of(BILL_DATE)
		self.assertEqual((label, str(deadline)), ("Q3 (Oct-Dec)", "2027-02-15"))
		self.assertEqual(str(bills.quarter_of("2027-01-10")[3]), "2027-06-15")
		self.assertEqual(str(bills.quarter_of("2026-05-10")[3]), "2026-08-15")
		self.assertEqual(str(bills.quarter_of("2026-08-10")[3]), "2026-11-15")
		self.assertEqual(bills.check_tds_certificates(today=add_days(deadline, -30))["logs"], 0, "no reminder a month ahead")
		out = bills.check_tds_certificates(today=add_days(deadline, -10))
		log = frappe.db.get_value("Ads TDS Certificate Log", {"supplier": SUP_FB, "company": self.company, "quarter": label}, ["name", "status", "tds_amount"], as_dict=True)
		self.assertTrue(log, out)
		self.assertEqual(log.status, "Pending")
		self.assertGreater(log.tds_amount, 0)
		key = f"tds-cert:{log.name}"
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": key, "alert_type": "TDS Certificate Due", "status": "Open"}))
		cert = frappe.get_doc("Ads TDS Certificate Log", log.name)
		cert.sent_date = add_days(deadline, -5)
		self.assertRaises(frappe.ValidationError, cert.save)
		cert.reload()
		cert.sent_date = add_days(deadline, -5)
		cert.certificate_reference = "QA131-0001"
		cert.save()
		self.assertEqual(cert.status, "Sent")
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}))

	# ─── QA Fix regressions ───────────────────────────────────────────────────

	def _set_map(self, name, **values):
		m = frappe.get_doc("Ads Bill Account Map", name)
		m.update(values)
		m.save(ignore_permissions=True)

	def test_qa_fix_stale_draft_is_rebuilt_or_held_after_the_map_changes(self):
		self._run_meta(meta_invoice("8800030", "3199000030", "9901", 1000.0, 180.0, campaigns=[("c1", "QA Fix A", 1000.0)]))
		doc = self._doc("Meta", "8800030")
		old = self._pi(doc)
		self.assertEqual((doc.status, old.billing_address, old.company_gstin), (bills.DRAFT, self.addr_hr, GSTIN_HR), doc.differences)
		try:
			# Finance moves the account to the Karnataka GSTIN: the untouched draft is built again.
			self._set_map(self.map_in, billing_address=self.addr_ka, platform_gstin=GSTIN_KA)
			self.assertEqual(bills.process(doc.name), bills.DRAFT, frappe.db.get_value("Ads Bill Document", doc.name, "differences"))
			doc.reload()
			new = self._pi(doc)
			self.assertNotEqual(new.creation, old.creation, "a new draft (the series may give it the deleted draft's name)")
			self.assertEqual(frappe.db.count("Purchase Invoice", {"bill_no": "3199000030"}), 1, "the stale draft is gone")
			self.assertEqual((new.billing_address, new.company_gstin), (self.addr_ka, GSTIN_KA))
			self.assertEqual(self._gst(new)["igst"], 180.0, "Karnataka company, Haryana supplier → IGST")
			# A draft someone edited is never rebuilt; with the map changed again it must not be auto-submitted.
			self._as(U_CHECKER)
			pi = frappe.get_doc("Purchase Invoice", new.name)
			pi.remarks = "QA Fix edited"
			pi.save()
			self._as("Administrator")
			self._set_map(self.map_in, billing_address=self.addr_hr, platform_gstin=GSTIN_HR, tax_withholding_category=None, tds_not_applicable=1)
			frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 1)
			frappe.db.set_value("Ads Bill Document", doc.name, "prepared_by", None)
			self.assertEqual(bills.process(doc.name), bills.REVIEW)
			doc.reload()
			self.assertEqual(doc.purchase_invoice, new.name, "an edited draft is kept")
			self.assertEqual(frappe.db.get_value("Purchase Invoice", new.name, "docstatus"), 0, "never auto-submitted on a stale map")
			self.assertIn("Billing address on the Purchase Invoice", doc.differences)
			self.assertIn("TDS on the Purchase Invoice", doc.differences)
			ctx = bills._context(doc)
			self.assertTrue(any("Company GSTIN" in d for d in bills.check(doc, frappe.get_doc("Purchase Invoice", new.name), ctx)))
		finally:
			frappe.db.set_single_value("Ads Settings", "bills_auto_submit", 0)
			self._set_map(self.map_in, billing_address=self.addr_hr, platform_gstin=GSTIN_HR, tax_withholding_category=TWC, tds_not_applicable=0)

	def test_qa_fix_credit_line_keeps_its_sign_and_is_netted(self):
		self._run_meta(meta_invoice("8800031", "3199000031", "9901", 1000.0, 180.0, campaigns=[("c1", "QA Fix spend", 1200.0), ("c2", "QA Fix credit", -200.0)]))
		doc = self._doc("Meta", "8800031")
		self.assertEqual(sorted(round(ln.net_amount, 2) for ln in doc.lines), [-200.0, 1200.0], "line signs are kept")
		self.assertEqual(round(doc.net_amount, 2), 1000.0)
		self.assertNotIn("separate line", doc.differences or "", "the lines reconcile with the bill")
		self.assertIn("netted", doc.differences)
		pi = self._pi(doc)
		self.assertEqual([round(i.amount, 2) for i in pi.items], [1000.0])
		self.assertEqual(round(pi.net_total, 2), 1000.0)

	def test_qa_fix_ad_bill_purchase_invoice_is_submitted_only_through_approval(self):
		self.assertIn("excom.excom_ads.bills.before_purchase_invoice_submit", frappe.get_hooks("doc_events").get("Purchase Invoice", {}).get("before_submit", []))
		self._run_meta(meta_invoice("8800032", "3199000032", "9901", 1000.0, 180.0))
		doc = self._doc("Meta", "8800032")
		pi = self._pi(doc)
		self.assertRaises(frappe.PermissionError, pi.submit)
		self.assertEqual(frappe.db.get_value("Purchase Invoice", pi.name, "docstatus"), 0)
		self._as(U_CHECKER)
		try:
			self.assertRaises(frappe.PermissionError, frappe.get_doc("Purchase Invoice", pi.name).submit)
		finally:
			self._as("Administrator")
		self.assertEqual(self._approve(doc)["status"], bills.SUBMITTED)
		self.assertEqual(frappe.db.get_value("Purchase Invoice", pi.name, "docstatus"), 1)
		self.assertFalse(frappe.flags.get(bills.SUBMIT_FLAG), "the approval flag does not outlive the submit")
		# Any other Purchase Invoice is untouched by the hook.
		bills.before_purchase_invoice_submit(frappe.get_doc({"doctype": "Purchase Invoice", "name": "QA-FIX-NOT-AN-AD-BILL"}))

	# ─── scheduler and fetch errors ───────────────────────────────────────────

	def test_scheduler_only_enqueues_and_fetch_errors_are_recorded(self):
		queued = []
		real = frappe.enqueue
		frappe.enqueue = lambda method, **kw: queued.append((method, kw)) or object()
		try:
			names = bill_tasks.enqueue_bill_fetch(today="2026-10-18")
		finally:
			frappe.enqueue = real
		self.assertIn(SRC_META, names)
		job = [kw for m, kw in queued if kw.get("source") == SRC_META][0]
		self.assertEqual((job["queue"], job["deduplicate"]), ("long", True))
		self.assertIn(SRC_META, job["job_id"])
		self.meta.fail = (400, {"error": {"code": 190, "message": f"Invalid OAuth access token {TOKEN}"}}, {})
		out = bills.run_source(SRC_META)
		self.assertEqual(out["status"], "Failed")
		src = frappe.get_doc("Ads Bill Source", SRC_META)
		self.assertEqual(src.last_status, "Failed")
		self.assertNotIn(TOKEN, src.last_error or "", "the token never lands in an error")
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"bill-fetch:{SRC_META}", "status": "Open"}))
		self.meta.fail = None
		self._run_meta()
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"bill-fetch:{SRC_META}", "status": ["!=", "Resolved"]}))


class TestBillNormalisation(FrappeTestCase):
	"""Pure parsing of recorded payloads; runs with or without India Compliance."""

	def test_meta_amounts_prefer_millis_and_strip_act_prefix(self):
		b = bill_sources.normalise_meta(meta_invoice("1", "2", "act_55", 1234.5, 222.21), "INV")
		self.assertEqual((b["net_amount"], b["tax_amount"], b["total_amount"], b["account_ids"]), (1234.5, 222.21, 1456.71, ["55"]))
		self.assertEqual((b["bill_date"], b["service_start"], b["service_end"], b["document_type"]), (BILL_DATE, "2026-09-01", "2026-09-30", "Invoice"))
		self.assertEqual(bill_sources.normalise_meta(meta_invoice("1", "2", "55", 10, 1.8, inv_type="CM"), "CM")["document_type"], "Credit Memo")

	def test_google_credit_memo_amounts_are_positive_with_original(self):
		b = bill_sources.normalise_google(google_invoice("9", 100.0, 18.0, inv_type="CREDIT_MEMO", corrected="8"), {"id": SETUP_ID})
		self.assertEqual((b["document_type"], b["net_amount"], b["tax_amount"], b["original_invoice_ids"]), ("Credit Memo", 100.0, 18.0, ["8"]))

	def test_qa_fix_line_signs_are_kept_and_only_the_header_is_normalised(self):
		b = bill_sources.normalise_meta(meta_invoice("1", "2", "55", 1000.0, 180.0, campaigns=[("c1", "QA Fix spend", 1200.0), ("c2", "QA Fix credit", -200.0)]), "INV")
		self.assertEqual([ln["net_amount"] for ln in b["lines"]], [1200.0, -200.0], "a credit line inside an invoice stays negative")
		self.assertEqual(b["net_amount"], 1000.0)
		cm = bill_sources.normalise_meta(meta_invoice("3", "4", "55", -500.0, -90.0, inv_type="CM", campaigns=[("c1", "QA Fix a", -300.0), ("c2", "QA Fix b", -200.0)]), "CM")
		self.assertEqual((cm["net_amount"], cm["tax_amount"], [ln["net_amount"] for ln in cm["lines"]]), (500.0, 90.0, [300.0, 200.0]), "a negative credit memo is turned as a whole")
		g = bill_sources.normalise_google(google_invoice("9", 100.0, 18.0, inv_type="CREDIT_MEMO", corrected="8"), {"id": SETUP_ID})
		self.assertEqual([ln["net_amount"] for ln in g["lines"]], [100.0])
		inv = google_invoice("10", 1000.0, 162.0)
		inv["accountSummaries"].append({"customer": f"customers/{CID}9", "subtotalAmountMicros": str(-100_000_000), "taxAmountMicros": str(-18_000_000)})
		inv["totalAmountMicros"] = str(int(1062 * 1_000_000))
		g2 = bill_sources.normalise_google(inv, {"id": SETUP_ID})
		self.assertEqual(([ln["net_amount"] for ln in g2["lines"]], g2["net_amount"]), ([1000.0, -100.0], 900.0))

	def test_pdf_hosts_are_restricted(self):
		self.assertTrue(bill_sources.pdf_host_allowed("Meta", "https://business.facebook.com/x.pdf"))
		self.assertFalse(bill_sources.pdf_host_allowed("Meta", "https://evil.example.com/facebook.com"))
		self.assertFalse(bill_sources.pdf_host_allowed("Google", "http://ads.google.com/x"))
		self.assertFalse(bill_sources.pdf_host_allowed("Google", "https://google.com.evil.io/x"))

	def test_entity_matching(self):
		self.assertEqual(bill_suppliers.match_entity("Meta", "FBIE"), "meta_ie")
		self.assertEqual(bill_suppliers.match_entity("Meta", "Facebook India Online Services Pvt. Ltd."), "meta_in")
		self.assertIsNone(bill_suppliers.match_entity("Meta", "Google India"))
		self.assertTrue(all(re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z]\wZ\w", g) for g in (GSTIN_HR, GSTIN_KA, GSTIN_B, GSTIN_FB)))
		self.assertEqual(getdate(bills.quarter_of("2026-12-31")[2]), getdate("2026-12-31"))
