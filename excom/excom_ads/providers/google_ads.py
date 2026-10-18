"""Google Ads API, read side, over REST (GAQL through googleAds:search).

Verified against the official docs for v26 (released 2026-10-14):
  REST     POST https://googleads.googleapis.com/v26/customers/{id}/googleAds:search
           {"query": ..., "pageToken": ...}; pages are a fixed 10,000 rows with nextPageToken.
           https://developers.google.com/google-ads/api/rest/common/search
  Headers  Authorization: Bearer; login-customer-id = the manager account, digits only, when the
           credential reaches the account through a manager.
           https://developers.google.com/google-ads/api/rest/auth
  Tokens   Developer tokens were sunset on 2026-09-09: access levels follow the Google Cloud
           project that owns the OAuth client / service account. The header is "optional and
           ignored", so it is sent only when the connection has one.
           https://developers.google.com/google-ads/api/docs/first-call/dev-token
  Errors   google.rpc.Status with GoogleAdsFailure in details (errors[].errorCode).
           https://developers.google.com/google-ads/api/docs/best-practices/understand-api-errors

Money: every *_micros value is the account currency × 1,000,000, whatever the currency.

What maps to what (Ads Metric Snapshot):
  spend          metrics.cost_micros / 1e6
  impressions    metrics.impressions
  clicks         metrics.clicks
  leads          metrics.conversions of conversion actions whose category is a new-lead category
                 (LEAD_CATEGORIES). Qualified / converted lead stages are later steps of the same
                 lead and are not added. Accounts without such actions report 0 leads.
  purchases      metrics.conversions of PURCHASE actions; purchase_value their conversions_value.
  metrics_json   cost_micros, conversions, conversions_value, all_conversions and the per-category
                 breakdown, so any other reading can be derived later without a re-pull.
  reach, link_clicks, messaging_conversations: not reported by Google at this grain (listed in
  metrics_json.not_reported). The columns cannot hold "unknown" and stay 0.

Campaign types: Performance Max has no ad groups or ads (it uses asset groups), so it shows up at
Account and Campaign level only; nothing is invented below that. Video campaigns can be read but
not changed through the API; they are flagged read-only in their raw config.
"""

import re
import time

import frappe
from frappe.utils import add_days, getdate

from excom.excom_ads import google_auth
from excom.excom_ads.providers.base import (
	INVALID,
	PERMISSION,
	RATE_LIMITED,
	READS,
	RETRYABLE,
	TOKEN,
	UNKNOWN,
	AdsProvider,
	AdsProviderError,
	Capability,
)

API = "https://googleads.googleapis.com"
DEFAULT_VERSION = "v26"
PAGE_ROWS = 10000  # fixed by Google for :search
RETRIES = 2  # extra attempts for transient (5xx / UNAVAILABLE / DEADLINE_EXCEEDED) answers, in-call
_sleep = time.sleep  # tests replace it

# Days per metrics query. Ad level returns the most rows.
WINDOW_DAYS = {"Account": 31, "Campaign": 31, "Ad Group": 31, "Ad": 14}

# ConversionActionCategory values that mean "a new lead". QUALIFIED_LEAD and CONVERTED_LEAD are
# later stages of a lead already counted, so they stay out (kept in metrics_json.by_category).
LEAD_CATEGORIES = ("SUBMIT_LEAD_FORM", "IMPORTED_LEAD", "PHONE_CALL_LEAD", "MESSAGE_LEAD", "REQUEST_QUOTE", "BOOK_APPOINTMENT", "CONTACT", "SIGNUP")
PURCHASE_CATEGORIES = ("PURCHASE",)
NOT_REPORTED = ["reach", "link_clicks", "messaging_conversations"]

CUSTOMER_STATUS = {"ENABLED": "Active", "SUSPENDED": "Disabled", "CANCELED": "Closed", "CLOSED": "Closed"}
CUSTOMER_STATUS_REASON = {"SUSPENDED": "Suspended by Google", "CANCELED": "Cancelled", "CLOSED": "Closed"}
VIDEO_CHANNELS = {"VIDEO"}
MANUAL_BIDDING = {"MANUAL_CPC", "ENHANCED_CPC", "MANUAL_CPM", "MANUAL_CPV"}

ACCOUNT_QUERY = "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone, customer.status, customer.manager, customer.test_account FROM customer"
CUSTOMER_CLIENT_QUERY = (
	"SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, customer_client.time_zone, "
	"customer_client.status, customer_client.manager, customer_client.level, customer_client.test_account, customer_client.hidden "
	"FROM customer_client"
)
CAMPAIGN_QUERY = (
	"SELECT campaign.id, campaign.name, campaign.status, campaign.serving_status, campaign.primary_status, campaign.primary_status_reasons, "
	"campaign.advertising_channel_type, campaign.advertising_channel_sub_type, campaign.bidding_strategy_type, "
	"campaign.start_date_time, campaign.end_date_time, campaign_budget.amount_micros, campaign_budget.total_amount_micros, "
	"campaign_budget.period, campaign_budget.explicitly_shared FROM campaign"
)
# Ad groups and ads of removed campaigns are years of noise on an old account; skipped. A removed
# ad group or ad inside a live campaign is still read, so it shows as Deleted.
AD_GROUP_QUERY = (
	"SELECT campaign.id, campaign.advertising_channel_type, campaign.bidding_strategy_type, ad_group.id, ad_group.name, ad_group.status, "
	"ad_group.primary_status, ad_group.primary_status_reasons, ad_group.type, ad_group.cpc_bid_micros "
	"FROM ad_group WHERE campaign.status != 'REMOVED'"
)
AD_QUERY = (
	"SELECT campaign.id, campaign.advertising_channel_type, ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type, "
	"ad_group_ad.status, ad_group_ad.primary_status, ad_group_ad.primary_status_reasons, "
	"ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.review_status, ad_group_ad.ad_strength "
	"FROM ad_group_ad WHERE campaign.status != 'REMOVED'"
)
METRIC_FIELDS = "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value, metrics.all_conversions"
# Per level: (resource, id fields selected before segments.date)
LEVEL_FROM = {
	"Account": ("customer", ""),
	"Campaign": ("campaign", "campaign.id, "),
	"Ad Group": ("ad_group", "campaign.id, ad_group.id, "),
	"Ad": ("ad_group_ad", "campaign.id, ad_group.id, ad_group_ad.ad.id, "),
}


# ─── pure helpers ─────────────────────────────────────────────────────────────

def micros(value):
	if value in (None, ""):
		return None
	return int(value) / 1_000_000


def _num(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _int(value) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def campaign_status(status, primary=None, serving=None, reasons=()) -> str:
	"""Our status from Google's configured status, primary status and serving status."""
	s, p, sv, r = (status or "").upper(), (primary or "").upper(), (serving or "").upper(), set(reasons or ())
	if s == "REMOVED" or p == "REMOVED":
		return "Deleted"
	if s == "PAUSED":
		return "Paused"
	if s != "ENABLED":
		return "Unknown"
	if sv == "SUSPENDED":
		return "Error"  # Google gives no reason for a suspended campaign; the raw value is kept
	if p in ("ELIGIBLE", "LEARNING", "LIMITED"):
		return "Active"
	if p == "PAUSED":
		return "Paused"
	if p == "ENDED" or sv == "ENDED":
		return "Archived"
	if p == "PENDING":
		return "In Review" if r & {"MOST_ADS_UNDER_REVIEW", "MOST_ASSET_GROUPS_UNDER_REVIEW"} else "Active"
	if p == "NOT_ELIGIBLE":
		return "Rejected" if r & {"HAS_ADS_DISAPPROVED", "HAS_ASSET_GROUPS_DISAPPROVED"} else "Error"
	if p == "MISCONFIGURED":
		return "Error"
	if not p:
		return {"SERVING": "Active", "PENDING": "Active"}.get(sv, "Unknown")
	return "Unknown"


def ad_group_status(status, primary=None, reasons=()) -> str:
	s, p, r = (status or "").upper(), (primary or "").upper(), set(reasons or ())
	if s == "REMOVED" or p == "REMOVED":
		return "Deleted"
	if s == "PAUSED":
		return "Paused"
	if s != "ENABLED":
		return "Unknown"
	if p in ("ELIGIBLE", "LIMITED", ""):
		return "Active"
	if p == "PAUSED":
		return "Paused"
	if p == "PENDING":
		return "In Review" if "MOST_ADS_UNDER_REVIEW" in r else "Active"
	if p == "NOT_ELIGIBLE":
		if "HAS_ADS_DISAPPROVED" in r:
			return "Rejected"
		if "CAMPAIGN_ENDED" in r:
			return "Archived"
		if "CAMPAIGN_REMOVED" in r:
			return "Deleted"
		return "Error"
	return "Unknown"


def ad_status(status, primary=None, reasons=(), approval=None, review=None) -> str:
	s, p, r = (status or "").upper(), (primary or "").upper(), set(reasons or ())
	a, rv = (approval or "").upper(), (review or "").upper()
	if s == "REMOVED" or p == "REMOVED":
		return "Deleted"
	if s == "PAUSED":
		return "Paused"
	if s != "ENABLED":
		return "Unknown"
	if a == "DISAPPROVED" or "AD_GROUP_AD_DISAPPROVED" in r:
		return "Rejected"  # also while under appeal: it does not serve until the appeal succeeds
	if p == "PAUSED":
		return "Paused"
	if p in ("ELIGIBLE", "LIMITED"):
		return "Active"
	if rv == "REVIEW_IN_PROGRESS" or p == "PENDING" or "AD_GROUP_AD_UNDER_REVIEW" in r:
		return "In Review"
	if p == "NOT_ELIGIBLE":
		if "CAMPAIGN_ENDED" in r:
			return "Archived"
		if r & {"CAMPAIGN_REMOVED", "AD_GROUP_REMOVED"}:
			return "Deleted"
		return "Error"
	return "Active" if not p else "Unknown"


def _retry_seconds(value) -> int | None:
	"""protobuf Duration JSON ('30s', '1.5s') or a number → whole seconds."""
	if value in (None, ""):
		return None
	if isinstance(value, dict):  # {"seconds": "30"}
		value = value.get("seconds")
	m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s?\s*", str(value))
	return max(1, int(float(m.group(1)) + 0.999)) if m else None


def classify(status: int, body, headers=None, scrub=lambda s: s) -> AdsProviderError:
	"""A Google Ads REST error → AdsProviderError with our class, Google's error code and request ID."""
	if isinstance(body, list):  # searchStream wraps everything, errors included, in an array
		body = next((b for b in body if isinstance(b, dict) and b.get("error")), {})
	body = body if isinstance(body, dict) else {}
	err = body.get("error") if isinstance(body.get("error"), dict) else body
	grpc = str(err.get("status") or "").upper()
	message = err.get("message") or f"HTTP {status}"
	kind = value = request_id = None
	gmessage = None
	retry = None
	for d in err.get("details") or []:
		t = str(d.get("@type") or "")
		if t.endswith("GoogleAdsFailure"):
			request_id = d.get("requestId")
			for e in d.get("errors") or []:
				code = e.get("errorCode") or {}
				if code and kind is None:
					kind, value = next(iter(code.items()))
					gmessage = e.get("message")
				q = ((e.get("details") or {}).get("quotaErrorDetails") or {}).get("retryDelay")
				retry = retry or _retry_seconds(q)
		elif t.endswith("google.rpc.RetryInfo"):
			retry = retry or _retry_seconds(d.get("retryDelay"))
	value = str(value or "")
	if kind == "authenticationError":
		cls = PERMISSION if value == "NOT_ADS_USER" else INVALID if value == "CUSTOMER_NOT_FOUND" else TOKEN
	elif kind == "authorizationError":
		cls = INVALID if value == "CUSTOMER_NOT_ENABLED" else PERMISSION
	elif kind == "quotaError":
		cls = RATE_LIMITED
	elif kind == "internalError":
		cls = RETRYABLE
	elif kind:
		cls = INVALID
	elif status == 401 or grpc == "UNAUTHENTICATED":
		cls = TOKEN
	elif status == 403 or grpc == "PERMISSION_DENIED":
		cls = PERMISSION
	elif status == 429 or grpc == "RESOURCE_EXHAUSTED":
		cls = RATE_LIMITED
	elif status >= 500 or grpc in ("INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"):
		cls = RETRYABLE
	elif status in (400, 404) or grpc in ("INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION"):
		cls = INVALID
	else:
		cls = UNKNOWN
	hint = {
		"USER_PERMISSION_DENIED": " Check the manager account ID (login-customer-id) on the Ads Connection.",
		"NOT_ADS_USER": " Add the service account email (or the signed-in Google user) as a user on the Google Ads manager account.",
		"CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION": " The Google Cloud project only has Test access; apply for Explorer or Basic access in the Cloud console.",
		"CUSTOMER_NOT_ENABLED": " The Google Ads account is not enabled (cancelled, closed or setup not finished).",
	}.get(value, "")
	if retry is None:
		retry = google_auth._retry_after_header(headers)
	code = f"{kind}.{value}" if kind else grpc or status
	head = " ".join(str(x) for x in ("Google Ads HTTP", status, grpc, code if kind else "") if x)
	text = f"{head}: {gmessage or message}{hint}"
	if request_id:
		text += f" (request {request_id})"
	return AdsProviderError(scrub(text)[:500], cls, retry_after=retry if cls == RATE_LIMITED else None, code=code)


def _v(row: dict, path: str):
	"""row['campaignBudget']['amountMicros'] from 'campaign_budget.amount_micros' (REST JSON is camelCase)."""
	cur = row
	for part in path.split("."):
		if not isinstance(cur, dict):
			return None
		cur = cur.get(re.sub(r"_([a-z])", lambda m: m.group(1).upper(), part))
	return cur


# ─── transport ────────────────────────────────────────────────────────────────

class GoogleAdsClient:
	"""Authenticated GAQL reads. Shared by the provider (one account) and discovery (a connection)."""

	def __init__(self, creds: google_auth.GoogleCredentials, version: str = DEFAULT_VERSION, max_pages: int = 200, stats=None):
		self.creds = creds
		self.version = version or DEFAULT_VERSION
		self.max_pages = max_pages
		self.stats = stats

	@classmethod
	def for_connection(cls, name: str, stats=None) -> "GoogleAdsClient":
		from excom.excom_ads.settings import get_settings

		creds = google_auth.GoogleCredentials.load(name)
		version = frappe.db.get_value("Ads Connection", name, "api_version") or DEFAULT_VERSION
		return cls(creds, version, int(get_settings().max_pages_per_call or 200), stats)

	def _count(self, n: int = 1):
		if self.stats is not None and n > 0:
			self.stats.api_calls += n

	def _headers(self, login_customer_id: str | None, force_token: bool = False) -> dict:
		h = {"Authorization": f"Bearer {self.creds.access_token(force=force_token)}", "Content-Type": "application/json"}
		if self.creds.developer_token:
			h["developer-token"] = self.creds.developer_token
		login = self.creds.login_customer_id if login_customer_id is None else login_customer_id
		if login:
			h["login-customer-id"] = login
		return h

	def call(self, method: str, path: str, body: dict | None = None, login_customer_id: str | None = None):
		url = f"{API}/{self.version}/{path.lstrip('/')}"
		refreshed = False
		attempt = 0
		while True:
			before = self.creds.stats_calls
			try:
				headers = self._headers(login_customer_id, force_token=refreshed)
			except AdsProviderError as e:
				if e.error_class == RETRYABLE and attempt < RETRIES:
					attempt += 1
					_sleep(2**attempt)
					continue
				raise
			finally:
				self._count(self.creds.stats_calls - before)  # token exchanges are calls too
			status, data, resp_headers = google_auth.http_request(method, url, headers=headers, json_body=body)
			self._count()
			if status == 200:
				return data
			err = classify(status, data, resp_headers, self.creds.scrub)
			if err.error_class == TOKEN and not refreshed:
				# A cached token Google no longer accepts (revoked early, clock skew): one fresh exchange.
				self.creds.forget_token()
				refreshed = True
				continue
			if err.error_class == RETRYABLE and attempt < RETRIES:
				attempt += 1
				_sleep(2**attempt)
				continue
			raise err

	def search(self, customer_id: str, query: str, login_customer_id: str | None = None) -> list[dict]:
		customer_id = google_auth.normalise_customer_id(customer_id)
		out, token, pages = [], None, 0
		while True:
			body = {"query": query}
			if token:
				body["pageToken"] = token
			data = self.call("POST", f"customers/{customer_id}/googleAds:search", body, login_customer_id) or {}
			out.extend(data.get("results") or [])
			token = data.get("nextPageToken")
			pages += 1
			if not token:
				return out
			if pages >= self.max_pages:
				raise AdsProviderError(f"Stopped after {pages} pages ({pages * PAGE_ROWS} rows) of a Google Ads query; raise Ads Settings > Max Pages if this account is that large", RETRYABLE)

	def list_accessible_customers(self) -> list[str]:
		data = self.call("GET", "customers:listAccessibleCustomers", login_customer_id="") or {}
		return [n.split("/")[-1] for n in data.get("resourceNames") or []]


# ─── provider ─────────────────────────────────────────────────────────────────

class GoogleAdsProvider(AdsProvider):
	provider = "Google"
	CAPABILITIES = {
		"read_account": Capability(True, (READS,), (google_auth.ADWORDS_SCOPE,)),
		"list_campaigns": Capability(True, (READS,), (google_auth.ADWORDS_SCOPE,)),
		"list_ad_groups": Capability(True, (READS,), (google_auth.ADWORDS_SCOPE,), "Performance Max campaigns have asset groups, not ad groups, and are not listed here."),
		"list_ads": Capability(True, (READS,), (google_auth.ADWORDS_SCOPE,), "Performance Max campaigns have no individual ads."),
		"daily_metrics": Capability(True, (READS,), (google_auth.ADWORDS_SCOPE,), "Reach and link clicks are not reported by Google at daily account/campaign/ad group/ad grain."),
		"read_billing": Capability(False, reason="Not read in release 1. Google Ads billing (account budgets, invoices) needs billing setup access; shown as not available."),
		"read_leads": Capability(False, reason="Google lead form leads arrive through the lead form webhook, not this connector."),
		"pause_entity": Capability(False, reason="Release 2: runs only through an approved Action Request."),
		"change_budget": Capability(False, reason="Release 2: runs only through an approved Action Request."),
	}

	def __init__(self, account):
		super().__init__(account)
		conn = account.get("connection")
		if not conn:
			raise AdsProviderError(f"{account.name} has no Ads Connection", INVALID)
		self.client = GoogleAdsClient.for_connection(conn, stats=self.stats)
		self.customer_id = google_auth.normalise_customer_id(account.external_id)
		self.currency = account.currency or ""
		self.timezone = account.timezone_name or ""

	def _search(self, query: str) -> list[dict]:
		return self.client.search(self.customer_id, query)

	def _local_dt(self, value):
		"""'2026-10-01 00:00:00' in the customer's time zone → aware ISO string for sync._dt()."""
		if not value:
			return None
		from datetime import datetime
		from zoneinfo import ZoneInfo

		try:
			d = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
		except ValueError:
			try:
				d = datetime.strptime(str(value)[:10], "%Y-%m-%d")
			except ValueError:
				return None
		try:
			return d.replace(tzinfo=ZoneInfo(self.timezone)).isoformat() if self.timezone else d.isoformat()
		except Exception:
			return d.isoformat()

	# ─── reads ────────────────────────────────────────────────────────────────

	def read_account(self) -> dict:
		rows = self._search(ACCOUNT_QUERY)
		if not rows:
			raise AdsProviderError(f"Google Ads returned no customer record for {self.customer_id}", INVALID)
		c = rows[0].get("customer") or {}
		if c.get("manager"):
			raise AdsProviderError(f"{self.customer_id} is a manager account. Manager accounts have no campaigns of their own: enable its client accounts instead.", INVALID)
		self.currency = c.get("currencyCode") or self.currency
		self.timezone = c.get("timeZone") or self.timezone
		raw_status = c.get("status") or ""
		return {
			"account_name": c.get("descriptiveName") or None,
			"currency": self.currency,
			"timezone_name": self.timezone,
			"platform_status": raw_status,
			"status": CUSTOMER_STATUS.get(raw_status, "Unknown"),
			"disable_reason": CUSTOMER_STATUS_REASON.get(raw_status, ""),
			# Billing is not read in release 1: "not available", never a zero balance.
			"reported_amount_spent": None,
			"reported_spend_cap": None,
			"has_spend_cap": 0,
			"reported_amount_due": None,
			"billing_status": "Not Available",
			"can_read": 1,
			"can_billing": 0,
			"can_write": 0,
			"can_leads": 0,
		}

	def list_campaigns(self) -> list[dict]:
		out = []
		for row in self._search(CAMPAIGN_QUERY):
			c, b = row.get("campaign") or {}, row.get("campaignBudget") or {}
			period = (b.get("period") or "DAILY").upper()
			shared = bool(b.get("explicitlyShared"))
			channel = c.get("advertisingChannelType")
			raw = {"campaign": c, "campaign_budget": b}
			if channel in VIDEO_CHANNELS:
				raw["api_read_only"] = True
			out.append(
				{
					"external_id": str(c.get("id")),
					"campaign_name": c.get("name") or str(c.get("id")),
					"objective": channel,
					"buying_type": c.get("advertisingChannelSubType"),
					"bid_strategy": c.get("biddingStrategyType"),
					"configured_status": c.get("status"),
					"effective_status": c.get("primaryStatus") or c.get("servingStatus"),
					"status": campaign_status(c.get("status"), c.get("primaryStatus"), c.get("servingStatus"), c.get("primaryStatusReasons")),
					# A shared budget is one pool for several campaigns: putting it on each would
					# count it several times. It stays in the raw config.
					"daily_budget": micros(b.get("amountMicros")) if period == "DAILY" and not shared else None,
					"lifetime_budget": micros(b.get("totalAmountMicros")) if period == "CUSTOM_PERIOD" and not shared else None,
					"spend_cap": None,
					"start_time": self._local_dt(c.get("startDateTime")),
					"stop_time": self._local_dt(c.get("endDateTime")),
					"platform_created": None,
					"platform_updated": None,
					"raw": raw,
				}
			)
		return out

	def list_ad_groups(self) -> list[dict]:
		out = []
		for row in self._search(AD_GROUP_QUERY):
			c, g = row.get("campaign") or {}, row.get("adGroup") or {}
			manual = (c.get("biddingStrategyType") or "") in MANUAL_BIDDING
			out.append(
				{
					"external_id": str(g.get("id")),
					"ad_group_name": g.get("name") or str(g.get("id")),
					"campaign_external_id": str(c.get("id")) if c.get("id") else None,
					"configured_status": g.get("status"),
					"effective_status": g.get("primaryStatus"),
					"status": ad_group_status(g.get("status"), g.get("primaryStatus"), g.get("primaryStatusReasons")),
					"optimization_goal": None,
					"billing_event": None,
					"destination_type": None,
					# cpc_bid_micros is returned even when automated bidding ignores it.
					"bid_amount": micros(g.get("cpcBidMicros")) if manual else None,
					"daily_budget": None,
					"lifetime_budget": None,
					"start_time": None,
					"end_time": None,
					"platform_updated": None,
					"targeting": {},
					"raw": {"ad_group": g, "campaign": c, **({"api_read_only": True} if c.get("advertisingChannelType") in VIDEO_CHANNELS else {})},
				}
			)
		return out

	def list_ads(self) -> list[dict]:
		out = []
		for row in self._search(AD_QUERY):
			c, g, aga = row.get("campaign") or {}, row.get("adGroup") or {}, row.get("adGroupAd") or {}
			ad, policy = aga.get("ad") or {}, aga.get("policySummary") or {}
			gid, aid = str(g.get("id")), str(ad.get("id"))
			out.append(
				{
					# An ad is identified within its ad group (resource name adGroupAds/{group}~{ad}).
					"external_id": f"{gid}~{aid}",
					"ad_name": ad.get("name") or f"{(ad.get('type') or 'Ad').replace('_', ' ').title()} {aid}",
					"campaign_external_id": str(c.get("id")) if c.get("id") else None,
					"ad_group_external_id": gid,
					"configured_status": aga.get("status"),
					"effective_status": aga.get("primaryStatus") or policy.get("approvalStatus"),
					"status": ad_status(aga.get("status"), aga.get("primaryStatus"), aga.get("primaryStatusReasons"), policy.get("approvalStatus"), policy.get("reviewStatus")),
					"creative_id": aid,
					"creative_name": ad.get("type"),
					"platform_updated": None,
					"raw": {"ad_group_ad": aga, **({"api_read_only": True} if c.get("advertisingChannelType") in VIDEO_CHANNELS else {})},
				}
			)
		return out

	def daily_metrics(self, level: str, since, until) -> list[dict]:
		since, until = getdate(since), getdate(until)
		out, start = [], since
		while start <= until:
			end = min(add_days(start, WINDOW_DAYS[level] - 1), until)
			out.extend(self._metrics_window(level, start, end))
			start = add_days(end, 1)
		return out

	def _metrics_window(self, level: str, start, end) -> list[dict]:
		resource, ids = LEVEL_FROM[level]
		where = f"WHERE segments.date BETWEEN '{start}' AND '{end}'"
		base = self._search(f"SELECT {ids}segments.date, {METRIC_FIELDS} FROM {resource} {where}")
		# Conversion-action segments cannot be selected with cost or impressions, hence a second query.
		cats = ", ".join(f"'{c}'" for c in LEAD_CATEGORIES + PURCHASE_CATEGORIES + ("QUALIFIED_LEAD", "CONVERTED_LEAD"))
		by_cat = self._search(f"SELECT {ids}segments.date, segments.conversion_action_category, metrics.conversions, metrics.conversions_value FROM {resource} {where} AND segments.conversion_action_category IN ({cats})")
		window = f"{start}..{end}"
		rows: dict[tuple, dict] = {}

		def entry(r):
			key = self._entity(level, r)
			date = _v(r, "segments.date")
			if not key[0] or not date:
				return None
			k = (key[0], date)
			if k not in rows:
				rows[k] = {"keys": key, "date": date, "m": {}, "cats": {}}
			return rows[k]

		for r in base:
			e = entry(r)
			if e is not None:
				e["m"] = r.get("metrics") or {}
		for r in by_cat:
			e = entry(r)
			if e is None:
				continue
			cat = _v(r, "segments.conversion_action_category") or "UNKNOWN"
			m = r.get("metrics") or {}
			c = e["cats"].setdefault(cat, {"conversions": 0.0, "conversions_value": 0.0})
			c["conversions"] += _num(m.get("conversions"))
			c["conversions_value"] += _num(m.get("conversionsValue"))
		return [self._metric_row(level, e, window) for e in rows.values()]

	def _entity(self, level: str, r: dict) -> tuple:
		cid = _v(r, "campaign.id")
		gid = _v(r, "ad_group.id")
		aid = _v(r, "ad_group_ad.ad.id")
		cid, gid = (str(cid) if cid else None), (str(gid) if gid else None)
		ad_key = f"{gid}~{aid}" if gid and aid else None
		entity = {"Account": self.customer_id, "Campaign": cid, "Ad Group": gid, "Ad": ad_key}[level]
		return entity, cid, gid, ad_key

	def _metric_row(self, level: str, e: dict, window: str) -> dict:
		entity, cid, gid, ad_key = e["keys"]
		m, cats = e["m"], e["cats"]
		pick = lambda names, f: round(sum(cats.get(n, {}).get(f, 0.0) for n in names), 6)
		cost_micros = _int(m.get("costMicros"))
		return {
			"level": level,
			"date": e["date"],
			"entity_external_id": entity,
			"campaign_external_id": cid,
			"ad_group_external_id": gid,
			"ad_external_id": ad_key,
			"spend": cost_micros / 1_000_000,
			"impressions": _int(m.get("impressions")),
			"reach": 0,
			"clicks": _int(m.get("clicks")),
			"link_clicks": 0,
			"leads": pick(LEAD_CATEGORIES, "conversions"),
			"purchases": pick(PURCHASE_CATEGORIES, "conversions"),
			"purchase_value": pick(PURCHASE_CATEGORIES, "conversions_value"),
			"messaging_conversations": 0,
			"metrics": {
				"cost_micros": cost_micros,
				"conversions": _num(m.get("conversions")),
				"conversions_value": _num(m.get("conversionsValue")),
				"all_conversions": _num(m.get("allConversions")),
				"by_category": {k: cats[k] for k in sorted(cats)},
				"lead_categories": list(LEAD_CATEGORIES),
				"not_reported": NOT_REPORTED,
			},
			"source_window": window,
		}
