"""Google Analytics 4, read side: the Data API (reports) and the Admin API (discovery).

Verified against the official docs (October 2026):
  runReport   POST https://analyticsdata.googleapis.com/v1beta/properties/{id}:runReport
              body: dateRanges, dimensions, metrics, limit (default 10,000; "a maximum of 250,000
              rows per request"), offset, keepEmptyRows, returnPropertyQuota, orderBys.
              https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport
  response    dimensionHeaders, metricHeaders, rows, rowCount, metadata (currencyCode, timeZone,
              dataLossFromOtherRow, subjectToThresholding, samplingMetadatas), propertyQuota.
              https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/RunReportResponse
              https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData
  quota       Standard property: 200,000 tokens per property per day, 40,000 per hour, 14,000 per
              project per property per hour, 10 concurrent requests, 10 server errors per project
              per property per hour. An exhausted bucket answers 429 RESOURCE_EXHAUSTED.
              https://developers.google.com/analytics/devguides/reporting/data/v1/quotas
              PropertyQuota.tokensPerDay / tokensPerHour / tokensPerProjectPerHour {consumed, remaining}
              https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/PropertyQuota
  schema      date (YYYYMMDD, property time zone), sessionSource, sessionMedium, sessionCampaignName,
              sessionCampaignId, landingPage; sessions, totalUsers, newUsers, engagedSessions,
              keyEvents, transactions, purchaseRevenue ("revenue from purchases minus refunded
              transaction revenue"). `conversions` is deprecated in favour of `keyEvents` (Data API
              changelog, May 2024).
              https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema
              https://developers.google.com/analytics/devguides/reporting/data/v1/changelog
  admin       GET https://analyticsadmin.googleapis.com/v1beta/accountSummaries (pageSize ≤ 200),
              GET .../v1beta/properties/{id} (displayName, timeZone, currencyCode, propertyType),
              GET .../v1beta/properties/{id}/dataStreams (webStreamData.defaultUri / measurementId).
              https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list
              https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/properties
              https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/properties.dataStreams
  scopes      https://www.googleapis.com/auth/analytics.readonly (or .../auth/analytics).
  freshness   Standard properties: typically 12 hours; "Data processing can take 24-48 hours" and
              key-event attribution can change for up to 12 days.
              https://support.google.com/analytics/answer/11198161
  retention   The 2 / 14-month data-retention setting "does not affect standard aggregated reports"
              (explorations and funnels only), so the Data API's aggregated reports reach further back;
              the backfill length is our setting, chosen to bound quota use.
              https://support.google.com/analytics/answer/7667196

All HTTP goes through google_auth.http_request (tests replace it). Credentials come from an Ads
Connection whose Scopes list includes the analytics scope; nothing here returns or logs a secret.
"""

import re
import time
from urllib.parse import quote

from excom.excom_ads import google_auth
from excom.excom_ads.providers.base import INVALID, PERMISSION, RATE_LIMITED, RETRYABLE, TOKEN, UNKNOWN, AdsProviderError

DATA_API = "https://analyticsdata.googleapis.com/v1beta"
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
PAGE_ROWS = 100_000  # below Google's 250,000 per request cap; a day of sources rarely needs a second page
MAX_PAGES = 50
RETRIES = 2
# Stop starting new requests when the property has fewer hourly tokens than this left: the rest of
# the hour stays usable for people opening GA4 reports and other tools on the same property.
MIN_HOURLY_TOKENS = 1000
_sleep = time.sleep  # tests replace it

METRICS = ("sessions", "totalUsers", "newUsers", "engagedSessions", "keyEvents", "transactions", "purchaseRevenue")
# Our snapshot column per GA4 metric.
METRIC_FIELDS = {"sessions": "sessions", "totalUsers": "total_users", "newUsers": "new_users", "engagedSessions": "engaged_sessions", "keyEvents": "key_events", "transactions": "transactions", "purchaseRevenue": "purchase_revenue"}
REPORTS = {
	"Source": ("date", "sessionSource", "sessionMedium", "sessionCampaignName", "sessionCampaignId"),
	"Landing Page": ("date", "landingPage"),
}
DIMENSION_FIELDS = {"sessionSource": "session_source", "sessionMedium": "session_medium", "sessionCampaignName": "session_campaign_name", "sessionCampaignId": "session_campaign_id", "landingPage": "landing_page"}


def has_analytics_scope(creds) -> bool:
	return any(s in creds.scopes for s in google_auth.ANALYTICS_SCOPES)


def _detail_reason(err: dict) -> str:
	for d in err.get("details") or []:
		if str(d.get("@type") or "").endswith("google.rpc.ErrorInfo") and d.get("reason"):
			return str(d["reason"])
	return ""


def _retry_delay(err: dict, headers) -> int | None:
	for d in err.get("details") or []:
		if str(d.get("@type") or "").endswith("google.rpc.RetryInfo"):
			m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s?\s*", str(d.get("retryDelay") or ""))
			if m:
				return max(1, int(float(m.group(1)) + 0.999))
	return google_auth._retry_after_header(headers)


def classify(status: int, body, headers=None, scrub=lambda s: s) -> AdsProviderError:
	"""A GA4 (google.rpc.Status) error → AdsProviderError with our class and a hint a person can act on."""
	body = body if isinstance(body, dict) else {}
	err = body.get("error") if isinstance(body.get("error"), dict) else {}
	grpc = str(err.get("status") or "").upper()
	reason = _detail_reason(err)
	message = err.get("message") or f"HTTP {status}"
	hint = ""
	if status == 401 or grpc == "UNAUTHENTICATED":
		cls = TOKEN
	elif status == 429 or grpc == "RESOURCE_EXHAUSTED":
		cls = RATE_LIMITED
		hint = " The property's Analytics quota is used up for now; the next scheduled run tries again."
	elif status == 403 or grpc == "PERMISSION_DENIED":
		cls = PERMISSION
		hint = {
			"ACCESS_TOKEN_SCOPE_INSUFFICIENT": " The credential was not granted the analytics.readonly scope: add it to the connection's Scopes and, for OAuth, sign in again.",
			"SERVICE_DISABLED": " Enable the Google Analytics Data API and Admin API in the Google Cloud project of this credential.",
		}.get(reason, " Add the service account email (or the signed-in user) as a Viewer on the GA4 property.")
	elif status >= 500 or grpc in ("INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"):
		cls = RETRYABLE
	elif status in (400, 404) or grpc in ("INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION"):
		cls = INVALID
	else:
		cls = UNKNOWN
	code = reason or grpc or status
	text = f"Google Analytics HTTP {status} {grpc}".strip() + f": {message}{hint}"
	return AdsProviderError(scrub(text)[:500], cls, retry_after=_retry_delay(err, headers) if cls == RATE_LIMITED else None, code=code)


class GA4Client:
	"""Authenticated GA4 reads through one Ads Connection. `stats` counts HTTP calls (token exchanges included)."""

	def __init__(self, creds: google_auth.GoogleCredentials, stats=None):
		if not has_analytics_scope(creds):
			raise AdsProviderError(
				f"Ads Connection {creds.connection} does not list the {google_auth.ANALYTICS_SCOPE} scope. Add it to the connection's Scopes "
				"(for an OAuth refresh token, sign in again and allow Analytics access).",
				PERMISSION,
			)
		self.creds = creds
		self.stats = stats
		self.scope = next(s for s in google_auth.ANALYTICS_SCOPES if s in creds.scopes)
		self.quota: dict = {}
		self.metadata: dict = {}

	@classmethod
	def for_connection(cls, name: str, stats=None) -> "GA4Client":
		return cls(google_auth.GoogleCredentials.load(name), stats)

	def _count(self, n: int = 1):
		if self.stats is not None and n > 0:
			self.stats.api_calls += n

	def call(self, method: str, url: str, body: dict | None = None) -> dict:
		refreshed, attempt = False, 0
		scopes = (self.scope,)
		while True:
			before = self.creds.stats_calls
			try:
				token = self.creds.access_token(scopes, force=refreshed)
			except AdsProviderError as e:
				if e.error_class == RETRYABLE and attempt < RETRIES:
					attempt += 1
					_sleep(2**attempt)
					continue
				raise
			finally:
				self._count(self.creds.stats_calls - before)
			status, data, headers = google_auth.http_request(method, url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json_body=body)
			self._count()
			if status == 200:
				return data if isinstance(data, dict) else {}
			err = classify(status, data, headers, self.creds.scrub)
			if err.error_class == TOKEN and not refreshed:
				self.creds.forget_token(scopes)
				refreshed = True
				continue
			if err.error_class == RETRYABLE and attempt < RETRIES:
				attempt += 1
				_sleep(2**attempt)
				continue
			raise err

	# ─── admin ────────────────────────────────────────────────────────────────

	def account_summaries(self) -> list[dict]:
		out, token = [], None
		for _ in range(MAX_PAGES):
			url = f"{ADMIN_API}/accountSummaries?pageSize=200" + (f"&pageToken={quote(token, safe='')}" if token else "")
			data = self.call("GET", url)
			out.extend(data.get("accountSummaries") or [])
			token = data.get("nextPageToken")
			if not token:
				return out
		raise AdsProviderError(f"Stopped after {MAX_PAGES} pages of Analytics account summaries", RETRYABLE)

	def get_property(self, property_id: str) -> dict:
		return self.call("GET", f"{ADMIN_API}/properties/{_pid(property_id)}")

	def data_streams(self, property_id: str) -> list[dict]:
		return self.call("GET", f"{ADMIN_API}/properties/{_pid(property_id)}/dataStreams?pageSize=200").get("dataStreams") or []

	# ─── data ─────────────────────────────────────────────────────────────────

	def run_report(self, property_id: str, report: str, since, until) -> list[dict]:
		"""Every row of one report for [since, until], as {field: value} dicts with our column names.
		Updates self.quota (remaining tokens) and self.metadata (dataLossFromOtherRow, thresholding)."""
		dims = REPORTS[report]
		body = {
			"dateRanges": [{"startDate": str(since), "endDate": str(until)}],
			"dimensions": [{"name": d} for d in dims],
			"metrics": [{"name": m} for m in METRICS],
			"keepEmptyRows": False,
			"returnPropertyQuota": True,
			"limit": str(PAGE_ROWS),
			# A stable order keeps offset paging consistent between pages.
			"orderBys": [{"dimension": {"dimensionName": d}} for d in dims],
		}
		url = f"{DATA_API}/properties/{_pid(property_id)}:runReport"
		out, offset = [], 0
		for _ in range(MAX_PAGES):
			self.check_quota()
			body["offset"] = str(offset)
			data = self.call("POST", url, body)
			self._note(data)
			rows = data.get("rows") or []
			dim_names = [h.get("name") for h in data.get("dimensionHeaders") or []] or list(dims)
			met_names = [h.get("name") for h in data.get("metricHeaders") or []] or list(METRICS)
			for r in rows:
				out.append(_row(dim_names, met_names, r))
			offset += len(rows)
			total = int(data.get("rowCount") or 0)
			if not rows or offset >= total:
				return out
		raise AdsProviderError(f"Stopped after {MAX_PAGES} pages ({MAX_PAGES * PAGE_ROWS} rows) of a GA4 report", RETRYABLE)

	def _note(self, data: dict) -> None:
		q = data.get("propertyQuota") or {}
		for key in ("tokensPerDay", "tokensPerHour", "tokensPerProjectPerHour"):
			if isinstance(q.get(key), dict) and q[key].get("remaining") is not None:
				self.quota[key] = int(q[key]["remaining"])
		meta = data.get("metadata") or {}
		for key in ("dataLossFromOtherRow", "subjectToThresholding"):
			if meta.get(key):
				self.metadata[key] = True
		for key in ("currencyCode", "timeZone"):
			if meta.get(key):
				self.metadata[key] = meta[key]

	def check_quota(self) -> None:
		"""Refuse to start a request when the property is nearly out of hourly or daily tokens."""
		for key in ("tokensPerHour", "tokensPerProjectPerHour", "tokensPerDay"):
			left = self.quota.get(key)
			if left is not None and left < MIN_HOURLY_TOKENS:
				raise AdsProviderError(f"Google Analytics quota nearly used up ({key}: {left} tokens left); stopped before the next report. The next run continues.", RATE_LIMITED, code="QUOTA_LOW")


def _pid(property_id) -> str:
	v = str(property_id or "")
	if v.startswith("properties/"):
		v = v.split("/", 1)[1]
	if not re.fullmatch(r"\d{4,15}", v):
		raise AdsProviderError(f"'{v[:20]}' is not a GA4 property ID", INVALID)
	return v


def _row(dim_names: list, met_names: list, r: dict) -> dict:
	out = {}
	for name, v in zip(dim_names, r.get("dimensionValues") or []):
		value = v.get("value")
		if name == "date":
			s = str(value or "")
			out["date"] = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else None
		elif name in DIMENSION_FIELDS:
			out[DIMENSION_FIELDS[name]] = value
	for name, v in zip(met_names, r.get("metricValues") or []):
		if name in METRIC_FIELDS:
			try:
				out[METRIC_FIELDS[name]] = float(v.get("value") or 0)
			except (TypeError, ValueError):
				out[METRIC_FIELDS[name]] = 0.0
	return out
