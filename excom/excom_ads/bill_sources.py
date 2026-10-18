"""Platform invoices, read side. Only accounts on monthly invoicing (credit line) have an invoice API;
for card / prepaid accounts finance uploads the receipt as a manual Ads Bill Document. Nothing here
ever turns spend data into a bill.

Meta — Graph API v26.0, GET /{business_id}/business_invoices
  https://developers.facebook.com/docs/marketing-api/reference/business/business_invoices/
  Filters: issue_start_date (inclusive) / issue_end_date (exclusive), type = INV | CM | DM
  (PRO_FORMA is not a tax invoice and is never read). Nodes are OmegaCustomerTrx:
  https://developers.facebook.com/docs/marketing-api/reference/omega-customer-trx/
  id (internal), invoice_id (printed number), entity, ad_account_ids, billed_amount_details
  {net_amount, tax_amount, total_amount, *_in_millis}, amount_due, billing_period, currency,
  download_uri, due_date, invoice_date, payment_status; edge campaigns (InvoiceCampaign:
  ad_account_id, campaign_id, campaign_name, billed_amount_details).
  https://developers.facebook.com/docs/marketing-api/reference/billed-amount-details/
  https://developers.facebook.com/docs/marketing-api/reference/invoice-campaign/
  The reference names no permission: the system user needs access to the Business (finance role)
  and the token's business_management / ads_read scopes; a permission error is shown on the source.

Google — Google Ads API v26, InvoiceService.ListInvoices over REST
  GET customers/{customer_id}/invoices?billingSetup=customers/{cid}/billingSetups/{id}
      &issueYear=yyyy&issueMonth=JANUARY…  (all three required; invoices from 2019 on)
  https://developers.google.com/google-ads/api/docs/billing/invoice
  https://developers.google.com/google-ads/api/reference/rpc/v26/ListInvoicesRequest
  https://developers.google.com/google-ads/api/reference/rpc/v26/Invoice
  Invoice: id (printed "Invoice number"), type INVOICE | CREDIT_MEMO, issue_date, due_date,
  service_date_range, currency_code, subtotal/tax/total_amount_micros, corrected_invoice,
  replaced_invoices, pdf_url (needs the same OAuth token), account_summaries[] (customer,
  subtotal/tax/total_amount_micros), regulatory / export / adjustment amounts.
  Billing setups: GAQL FROM billing_setup (payments_account_info.payments_profile_id / name).
  The caller must be allowed to see the billing setup's invoices (else ACTION_NOT_PERMITTED);
  with consolidated billing one call returns every account under the billing setup.

Each fetcher returns normalised dicts (`bill` below); `bills.upsert_from_api` stores them.
Network calls go through `meta.http_get`, `google_auth.http_request` and `http_download` (PDFs),
so tests replace those and never touch the network.
"""

import calendar
from urllib.parse import urlencode, urlparse

import frappe
from frappe.utils import add_days, add_months, flt, get_first_day, getdate

from excom.excom_ads.providers.base import INVALID, RETRYABLE, TOKEN, AdsProviderError

META_TYPES = {"INV": "Invoice", "CM": "Credit Memo", "DM": "Debit Memo"}
META_CAMPAIGN_FIELDS = "ad_account_id,campaign_id,campaign_name,description,billed_amount_details"
META_FIELDS = (
	"id,invoice_id,type,invoice_type,entity,advertiser_name,ad_account_ids,amount,amount_due,billed_amount_details,"
	"billing_period,currency,download_uri,due_date,invoice_date,payment_status,payment_term,liability_type,"
	f"campaigns.limit(500){{{META_CAMPAIGN_FIELDS}}}"
)
MONTHS = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")
BILLING_SETUP_QUERY = (
	"SELECT billing_setup.id, billing_setup.status, billing_setup.payments_account_info.payments_account_id, "
	"billing_setup.payments_account_info.payments_profile_id, billing_setup.payments_account_info.payments_profile_name "
	"FROM billing_setup WHERE billing_setup.status = 'APPROVED'"
)
# PDFs are only ever downloaded from the platform's own hosts (no SSRF through a stored URL).
PDF_HOSTS = {
	"Meta": ("facebook.com", "fbcdn.net", "fbsbx.com"),
	"Google": ("google.com", "googleapis.com", "googleusercontent.com"),
}
PDF_MAX_BYTES = 15 * 1024 * 1024


def http_download(url: str, headers: dict | None = None, params: dict | None = None):
	"""The single network call for invoice PDFs. Tests replace it. Returns (status, content bytes, content_type)."""
	import requests

	try:
		resp = requests.get(url, headers=headers, params=params, timeout=60, stream=True, allow_redirects=True)
	except requests.RequestException as e:
		return 0, b"", type(e).__name__
	final = urlparse(resp.url or url)
	if final.scheme != "https":
		return 0, b"", "insecure redirect"
	data = b""
	for chunk in resp.iter_content(65536):
		data += chunk
		if len(data) > PDF_MAX_BYTES:
			return 0, b"", "too large"
	return resp.status_code, data, resp.headers.get("Content-Type") or ""


def pdf_host_allowed(provider: str, url: str) -> bool:
	try:
		u = urlparse(url or "")
	except ValueError:
		return False
	host = (u.hostname or "").lower()
	return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in PDF_HOSTS.get(provider, ()))


def months_back(lookback: int, today=None) -> list[tuple[int, int]]:
	"""[(year, month)] from `lookback` months ago up to this month."""
	today = getdate(today)
	out = []
	for i in range(max(0, int(lookback or 0)), -1, -1):
		d = add_months(get_first_day(today), -i)
		out.append((d.year, d.month))
	return out


def _date(value):
	if not value:
		return None
	try:
		return str(getdate(str(value)[:10]))
	except Exception:
		return None


def _month_end(d):
	d = getdate(d)
	return str(d.replace(day=calendar.monthrange(d.year, d.month)[1]))


# ─── Meta ─────────────────────────────────────────────────────────────────────

def _meta_amount(details: dict | None, key: str):
	"""BilledAmountDetails value in major units. `*_in_millis` is exact (1000 = 1.00) and preferred."""
	details = details or {}
	millis = details.get(f"{key}_in_millis")
	if millis not in (None, ""):
		try:
			return flt(str(millis).replace(",", "")) / 1000.0
		except ValueError:
			pass
	v = details.get(key)
	if v in (None, ""):
		return None
	try:
		return flt(str(v).replace(",", ""))
	except ValueError:
		return None


def _currency_amount(value):
	"""CurrencyAmount {amount, amount_in_hundredths, currency} → major units."""
	if not isinstance(value, dict):
		return flt(value) if value not in (None, "") else None
	if value.get("amount_in_hundredths") not in (None, ""):
		return flt(value["amount_in_hundredths"]) / 100.0
	if value.get("amount") not in (None, ""):
		return flt(str(value["amount"]).replace(",", ""))
	return None


def _act(account_id) -> str:
	s = str(account_id or "").strip()
	return s[4:] if s.startswith("act_") else s


def normalise_meta(row: dict, type_code: str) -> dict:
	d = row.get("billed_amount_details") or {}
	net, tax, total = _meta_amount(d, "net_amount"), _meta_amount(d, "tax_amount"), _meta_amount(d, "total_amount")
	if total is None and row.get("amount") not in (None, ""):
		total = flt(str(row["amount"]).replace(",", ""))
	if net is None and total is not None:
		net = total - (tax or 0)
	if total is None and net is not None:
		total = net + (tax or 0)
	currency = d.get("currency") or row.get("currency") or "INR"
	lines = []
	for c in (row.get("campaigns") or {}).get("data") or []:
		cd = c.get("billed_amount_details") or {}
		amt = _meta_amount(cd, "net_amount")
		if amt is None:
			continue
		lines.append(
			{
				"description": (c.get("campaign_name") or c.get("description") or f"Campaign {c.get('campaign_id')}")[:140],
				"campaign_id": str(c.get("campaign_id") or ""),
				"platform_account_id": _act(c.get("ad_account_id")),
				"net_amount": abs(amt),
				"tax_amount": abs(_meta_amount(cd, "tax_amount") or 0),
			}
		)
	period = _date(row.get("billing_period"))
	return {
		"provider": "Meta",
		"platform_invoice_id": str(row.get("id") or row.get("invoice_id")),
		"bill_no": str(row.get("invoice_id") or row.get("id")),
		"document_type": META_TYPES.get(type_code, "Invoice"),
		"bill_date": _date(row.get("invoice_date")),
		"due_date": _date(row.get("due_date")),
		"service_start": period,
		"service_end": _month_end(period) if period else None,
		"billing_entity": str(row.get("entity") or ""),
		"currency": currency.upper(),
		"net_amount": abs(net or 0),
		"tax_amount": abs(tax or 0),
		"total_amount": abs(total or 0),
		"amount_due": _currency_amount(row.get("amount_due")),
		"payment_status": str(row.get("payment_status") or ""),
		"pdf_url": row.get("download_uri") or "",
		"account_ids": sorted({_act(a) for a in row.get("ad_account_ids") or [] if a} | {ln["platform_account_id"] for ln in lines if ln["platform_account_id"]}),
		"lines": lines,
		"original_invoice_ids": [],
		"billing_setup": "",
		"raw": row,
	}


class MetaBillReader:
	def __init__(self, source):
		from excom.excom_ads.settings import get_settings

		if not source.meta_connection:
			raise AdsProviderError(f"Ads Bill Source {source.name} has no Excom Meta Connection", INVALID)
		conn = frappe.get_doc("Excom Meta Connection", source.meta_connection)
		if conn.status and conn.status != "Active":
			raise AdsProviderError(f"Excom Meta Connection {conn.name} is {conn.status}", TOKEN)
		self.business_id = str(conn.business_id or "").strip()
		if not self.business_id.isdigit():
			raise AdsProviderError(f"Excom Meta Connection {conn.name} has no Business ID", INVALID)
		self._token = conn.get_password("system_user_token", raise_exception=False) or ""
		if not self._token:
			raise AdsProviderError(f"Excom Meta Connection {conn.name} has no system user token", TOKEN)
		s = get_settings()
		# The ads module pins its Graph version in Ads Settings (as providers/meta.py does), not the
		# Excom connection's version used for messaging.
		self.version = s.meta_api_version or "v26.0"
		self.max_pages = int(s.max_pages_per_call or 200)
		self.calls = 0

	def _get(self, url, params):
		from excom.excom_ads.providers import meta

		if params is not None:
			params = {**params, "access_token": self._token}
		status, body, headers = meta.http_get(url, params)
		self.calls += 1
		if status != 200:
			err = meta.classify(status, body)
			err.args = (str(err).replace(self._token, "***"),)
			raise err
		return body or {}

	def fetch(self, since, until) -> list[dict]:
		"""Invoices, credit and debit memos issued in [since, until]."""
		out = []
		for code in META_TYPES:
			url = f"https://graph.facebook.com/{self.version}/{self.business_id}/business_invoices"
			params = {"fields": META_FIELDS, "type": code, "issue_start_date": str(getdate(since)), "issue_end_date": str(add_days(getdate(until), 1)), "limit": 100}
			pages = 0
			while url:
				body = self._get(url, params)
				out.extend(normalise_meta(r, code) for r in body.get("data") or [])
				url = (body.get("paging") or {}).get("next")
				params = None  # the next URL carries every parameter
				pages += 1
				if pages >= self.max_pages:
					raise AdsProviderError(f"Stopped after {pages} pages of Meta invoices", RETRYABLE)
		return out

	def download_pdf(self, bill: dict):
		url = bill.get("pdf_url")
		if not pdf_host_allowed("Meta", url):
			return None
		status, content, ctype = http_download(url, params={"access_token": self._token})
		return content if status == 200 and content[:5] == b"%PDF-" else None


# ─── Google ───────────────────────────────────────────────────────────────────

def _micros(v) -> float:
	try:
		return int(v or 0) / 1_000_000
	except (TypeError, ValueError):
		return 0.0


def _last(resource) -> str:
	return str(resource or "").rstrip("/").split("/")[-1]


def normalise_google(inv: dict, setup: dict) -> dict:
	total = abs(_micros(inv.get("totalAmountMicros")))
	tax = abs(_micros(inv.get("taxAmountMicros")))
	net = round(total - tax, 6)
	lines = []
	for s in inv.get("accountSummaries") or []:
		cid = _last(s.get("customer"))
		amt = _micros(s.get("subtotalAmountMicros"))
		if not amt and not cid:
			continue
		lines.append({"description": f"Google Ads account {cid}", "campaign_id": "", "platform_account_id": cid, "net_amount": abs(amt), "tax_amount": abs(_micros(s.get("taxAmountMicros")))})
	# Invoice-level items that are not inside an account summary. Added only when they close the gap
	# between the account lines and the invoice's pre-tax amount, so nothing is counted twice.
	gap = round(net - sum(ln["net_amount"] for ln in lines), 2)
	extras = [
		("Regulatory costs", abs(_micros(inv.get("regulatoryCostsSubtotalAmountMicros")))),
		("Export charges", abs(_micros(inv.get("exportChargeSubtotalAmountMicros")))),
		("Invoice adjustments", _micros(inv.get("adjustmentsSubtotalAmountMicros"))),
	]
	extras = [(d, a) for d, a in extras if a]
	if lines and extras and abs(gap - round(sum(a for _d, a in extras), 2)) < 0.01:
		lines.extend({"description": d, "campaign_id": "", "platform_account_id": "", "net_amount": a, "tax_amount": 0} for d, a in extras)
	sdr = inv.get("serviceDateRange") or {}
	is_credit = (inv.get("type") or "").upper() == "CREDIT_MEMO"
	originals = [_last(inv["correctedInvoice"])] if inv.get("correctedInvoice") else []
	return {
		"provider": "Google",
		"platform_invoice_id": str(inv.get("id")),
		"bill_no": str(inv.get("id")),
		"document_type": "Credit Memo" if is_credit else "Invoice",
		"bill_date": _date(inv.get("issueDate")),
		"due_date": _date(inv.get("dueDate")),
		"service_start": _date(sdr.get("startDate")),
		"service_end": _date(sdr.get("endDate")),
		# Google's API does not name the billing legal entity; the supplier comes from the account map.
		# (The payments profile name is the advertiser's own label, kept in the raw record.)
		"billing_entity": "",
		"currency": (inv.get("currencyCode") or "INR").upper(),
		"net_amount": net,
		"tax_amount": tax,
		"total_amount": total,
		"amount_due": None,
		"payment_status": "",
		"pdf_url": inv.get("pdfUrl") or "",
		"account_ids": sorted({ln["platform_account_id"] for ln in lines if ln["platform_account_id"]}),
		"lines": lines,
		"original_invoice_ids": originals,
		"replaced_invoice_ids": [_last(r) for r in inv.get("replacedInvoices") or []],
		"billing_setup": setup.get("id") or _last(inv.get("billingSetup")),
		"payments_profile_id": inv.get("paymentsProfileId") or setup.get("payments_profile_id") or "",
		"raw": {**inv, "_billing_setup": setup},
	}


class GoogleBillReader:
	def __init__(self, source):
		from excom.excom_ads import google_auth
		from excom.excom_ads.providers.google_ads import GoogleAdsClient

		if not source.google_connection:
			raise AdsProviderError(f"Ads Bill Source {source.name} has no Ads Connection", INVALID)
		self.client = GoogleAdsClient.for_connection(source.google_connection)
		self.customer_id = google_auth.normalise_customer_id(source.customer_id)
		self.billing_setup_id = str(source.billing_setup_id or "").strip()
		if self.billing_setup_id and not self.billing_setup_id.isdigit():
			raise AdsProviderError(f"Billing setup ID {self.billing_setup_id[:20]} is not a number", INVALID)

	def setups(self) -> list[dict]:
		if self.billing_setup_id:
			return [{"id": self.billing_setup_id}]
		out = []
		for r in self.client.search(self.customer_id, BILLING_SETUP_QUERY):
			b = r.get("billingSetup") or {}
			info = b.get("paymentsAccountInfo") or {}
			if b.get("id"):
				out.append({"id": str(b["id"]), "payments_profile_id": info.get("paymentsProfileId") or "", "payments_profile_name": info.get("paymentsProfileName") or "", "payments_account_id": info.get("paymentsAccountId") or ""})
		return out

	def fetch(self, months: list[tuple[int, int]]) -> list[dict]:
		out, seen = [], set()
		for setup in self.setups():
			for year, month in months:
				if year < 2019:
					continue
				q = urlencode({"billingSetup": f"customers/{self.customer_id}/billingSetups/{setup['id']}", "issueYear": str(year), "issueMonth": MONTHS[month - 1]}, safe="/")
				data = self.client.call("GET", f"customers/{self.customer_id}/invoices?{q}") or {}
				for inv in data.get("invoices") or []:
					if str(inv.get("id")) in seen:
						continue
					seen.add(str(inv.get("id")))
					out.append(normalise_google(inv, setup))
		return out

	def download_pdf(self, bill: dict):
		url = bill.get("pdf_url")
		if not pdf_host_allowed("Google", url):
			return None
		headers = {"Authorization": f"Bearer {self.client.creds.access_token()}"}
		status, content, ctype = http_download(url, headers=headers)
		return content if status == 200 and content[:5] == b"%PDF-" else None


def reader_for(source):
	if source.provider == "Meta":
		return MetaBillReader(source)
	if source.provider == "Google":
		return GoogleBillReader(source)
	raise AdsProviderError(f"No invoice API for {source.provider}; upload its bills as manual documents", INVALID)


def fetch(source, today=None) -> tuple[list[dict], object]:
	"""(bills, reader) for one Ads Bill Source over its lookback window."""
	reader = reader_for(source)
	months = months_back(source.lookback_months if source.lookback_months is not None else 2, today)
	if source.provider == "Meta":
		y, m = months[0]
		since = getdate(f"{y}-{m:02d}-01")
		return reader.fetch(since, getdate(today)), reader
	return reader.fetch(months), reader


def is_not_on_invoicing(err: AdsProviderError) -> bool:
	code = str(getattr(err, "code", "") or "")
	return code.startswith("invoiceError") or "NOT_INVOICED" in code or "BILLING_SETUP_NOT" in code

