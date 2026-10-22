"""Meta Marketing API (Facebook + Instagram ads), read side.

Credential: the system-user token on the account's Excom Meta Connection. Excom owns that token;
this module only reads it, server-side, and never returns it.

Money: account and budget amounts come in the currency's minor unit (paise for INR), insights
spend comes in major units. `_money()` converts the first kind.
"""

import json

import frappe
import requests

from excom.excom_ads.redact import redact

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

GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v26.0"

# Currencies Meta reports without a minor unit (offset 1). Everything else is offset 100.
NO_MINOR_UNIT = {"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD", "VND"}

ACCOUNT_FIELDS = "name,account_id,account_status,disable_reason,currency,timezone_name,amount_spent,spend_cap,balance,business{id,name}"
CAMPAIGN_FIELDS = "id,name,objective,status,configured_status,effective_status,buying_type,bid_strategy,daily_budget,lifetime_budget,spend_cap,start_time,stop_time,created_time,updated_time"
ADSET_FIELDS = "id,name,campaign_id,status,configured_status,effective_status,optimization_goal,billing_event,destination_type,bid_amount,daily_budget,lifetime_budget,start_time,end_time,targeting,updated_time"
AD_FIELDS = "id,name,campaign_id,adset_id,status,configured_status,effective_status,creative{id,name},updated_time"
# Billing (read_billing). Verified for Graph v26 against
# https://developers.facebook.com/docs/marketing-api/reference/ad-account/ (fields unchanged in the
# v26.0 Marketing API changelog). amount_spent, spend_cap and balance are in the currency's minor unit.
BILLING_CORE_FIELDS = "amount_spent,spend_cap,balance,currency"
FUNDING_FIELDS = "is_prepay_account,funding_source_details"
# funding_source_details.type
FUNDING_TYPES = {0: "UNSET", 1: "CREDIT_CARD", 2: "FACEBOOK_WALLET", 3: "FACEBOOK_PAID_CREDIT", 4: "FACEBOOK_EXTENDED_CREDIT", 5: "ORDER", 6: "INVOICE", 7: "FACEBOOK_TOKEN", 8: "EXTERNAL_FUNDING", 9: "FEE", 10: "FX", 11: "DISCOUNT", 12: "PAYPAL_TOKEN", 13: "PAYPAL_BILLING_AGREEMENT", 14: "FS_NULL", 15: "EXTERNAL_DEPOSIT", 16: "TAX", 17: "DIRECT_DEBIT", 18: "DUMMY", 19: "ALTPAY", 20: "STORED_BALANCE"}
# Funding types that say how the account pays. Others (fees, tax, coupons, unknown) say nothing.
FUNDING_MODEL = {
	"CREDIT_CARD": "Card (Postpaid)",
	"PAYPAL_TOKEN": "Card (Postpaid)",
	"PAYPAL_BILLING_AGREEMENT": "Card (Postpaid)",
	"DIRECT_DEBIT": "Card (Postpaid)",
	"FACEBOOK_EXTENDED_CREDIT": "Credit Line",
	"INVOICE": "Monthly Invoicing",
	"FACEBOOK_WALLET": "Prepaid",
	"FACEBOOK_PAID_CREDIT": "Prepaid",
	"STORED_BALANCE": "Prepaid",
	"EXTERNAL_DEPOSIT": "Prepaid",
}
INSIGHT_FIELDS = "account_id,campaign_id,adset_id,ad_id,spend,impressions,reach,clicks,inline_link_clicks,actions,action_values,date_start,date_stop"

LEVELS = {"Account": "account", "Campaign": "campaign", "Ad Group": "adset", "Ad": "ad"}
ENTITY_KEY = {"Account": "account_id", "Campaign": "campaign_id", "Ad Group": "adset_id", "Ad": "ad_id"}
# Days per insights call. Ad level returns the most rows, so it gets the smallest windows.
WINDOW_DAYS = {"Account": 31, "Campaign": 31, "Ad Group": 14, "Ad": 7}

ACCOUNT_STATUS = {1: "Active", 2: "Disabled", 3: "Unsettled", 7: "Pending Review", 8: "Unsettled", 9: "Grace Period", 100: "Pending Closure", 101: "Closed", 201: "Active", 202: "Closed"}
DISABLE_REASON = {0: "", 1: "Ads integrity policy", 2: "Ads IP review", 3: "Payment risk", 4: "Account shut down", 5: "Ads AFC review", 6: "Business integrity", 7: "Permanently closed", 8: "Unused reseller account", 9: "Unused account"}
EFFECTIVE_STATUS = {
	"ACTIVE": "Active",
	"PAUSED": "Paused",
	"CAMPAIGN_PAUSED": "Paused",
	"ADSET_PAUSED": "Paused",
	"IN_PROCESS": "In Review",
	"PENDING_REVIEW": "In Review",
	"PREAPPROVED": "In Review",
	"DISAPPROVED": "Rejected",
	"WITH_ISSUES": "Error",
	"PENDING_BILLING_INFO": "Billing Issue",
	"ARCHIVED": "Archived",
	"DELETED": "Deleted",
}

# One action can be reported under several overlapping types. Take the first present, never a sum.
LEAD_ACTIONS = ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead")
PURCHASE_ACTIONS = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
MESSAGING_ACTIONS = ("onsite_conversion.messaging_conversation_started_7d",)

# Graph error codes → our error classes.
TOKEN_CODES = {190, 102, 463, 467}
RATE_CODES = {4, 17, 32, 613, 80000, 80001, 80002, 80003, 80004, 80005, 80006, 80008, 80009, 80014}
RETRY_CODES = {1, 2}
PERMISSION_CODES = {10, 200, 270, 294}


def http_get(url: str, params: dict | None):
	"""The single network call. Tests replace it. Returns (status_code, body_dict, headers)."""
	try:
		resp = requests.get(url, params=params, timeout=60)
	except requests.Timeout:
		return 504, {"error": {"code": 2, "is_transient": True, "message": "Timed out waiting for Meta"}}, {}
	except requests.RequestException as e:
		# The exception text quotes the full URL, access token included: keep only its type.
		return 503, {"error": {"code": 2, "is_transient": True, "message": f"Could not reach Meta ({type(e).__name__})"}}, {}
	try:
		body = resp.json() if resp.content else {}
	except ValueError:
		body = {"error": {"message": resp.text[:300]}}
	return resp.status_code, body, resp.headers


def classify(status: int, body: dict) -> AdsProviderError:
	err = (body or {}).get("error") or {}
	code = err.get("code")
	msg = err.get("error_user_msg") or err.get("message") or f"HTTP {status}"
	if code in TOKEN_CODES:
		cls = TOKEN
	elif code in RATE_CODES or status == 429:
		cls = RATE_LIMITED
	elif code in RETRY_CODES or status >= 500 or err.get("is_transient"):
		cls = RETRYABLE
	elif code in PERMISSION_CODES or (isinstance(code, int) and 200 <= code < 300) or status == 403:
		cls = PERMISSION
	elif code == 100 or status == 400:
		cls = INVALID
	else:
		cls = UNKNOWN
	return AdsProviderError(f"Meta {status} (code {code}): {msg}"[:500], cls, code=code)


def _regain_minutes(headers) -> int | None:
	"""X-Business-Use-Case-Usage: {"<biz>": [{"type": "ads_insights", "call_count": 28, "estimated_time_to_regain_access": 0, ...}]}"""
	raw = (headers or {}).get("X-Business-Use-Case-Usage") or (headers or {}).get("x-business-use-case-usage")
	if not raw:
		return None
	try:
		data = json.loads(raw)
	except ValueError:
		return None
	worst = None
	for entries in data.values():
		for e in entries or []:
			wait = int(e.get("estimated_time_to_regain_access") or 0)
			busiest = max(int(e.get(k) or 0) for k in ("call_count", "total_cputime", "total_time"))
			if wait or busiest >= 95:
				worst = max(worst or 0, wait or 5)
	return worst


def money(value, currency: str):
	if value in (None, ""):
		return None
	amount = float(value)
	return amount if (currency or "").upper() in NO_MINOR_UNIT else amount / 100.0


def status_of(effective_status: str | None) -> str:
	return EFFECTIVE_STATUS.get((effective_status or "").upper(), "Unknown")


def _pick(actions: list | None, types: tuple) -> float:
	by_type = {a.get("action_type"): a.get("value") for a in actions or []}
	for t in types:
		if by_type.get(t) not in (None, ""):
			return float(by_type[t])
	return 0.0


class MetaAdsProvider(AdsProvider):
	provider = "Meta"
	CAPABILITIES = {
		"read_account": Capability(True, (READS,), ("ads_read",)),
		"list_campaigns": Capability(True, (READS,), ("ads_read",)),
		"list_ad_groups": Capability(True, (READS,), ("ads_read",)),
		"list_ads": Capability(True, (READS,), ("ads_read",)),
		"daily_metrics": Capability(True, (READS,), ("ads_read",)),
		"read_billing": Capability(
			True,
			(READS,),
			("ads_read",),
			"Reads amount_spent, spend_cap and balance (the bill amount due, not a prepaid balance) with ads_read; "
			"is_prepay_account (needs ADVERTISE or MANAGE task access on the ad account) and funding_source_details.type "
			"(needs MANAGE task access) give the payment model and are marked Permission Missing without that access. "
			"Meta does not expose a prepaid balance: Excom estimates it from its own top-up ledger.",
		),
		"pause_entity": Capability(False, reason="Release 2: runs only through an approved Action Request."),
		"change_budget": Capability(False, reason="Release 2: runs only through an approved Action Request."),
	}

	def __init__(self, account):
		super().__init__(account)
		if not account.meta_connection:
			raise AdsProviderError(f"{account.name} has no Excom Meta Connection", INVALID)
		conn = frappe.get_doc("Excom Meta Connection", account.meta_connection)
		if conn.status and conn.status != "Active":
			raise AdsProviderError(f"Excom Meta Connection {conn.name} is {conn.status}", TOKEN)
		self._token = conn.get_password("system_user_token", raise_exception=False) or ""
		if not self._token:
			raise AdsProviderError(f"Excom Meta Connection {conn.name} has no system user token", TOKEN)
		self.scopes = {s.strip() for s in (conn.token_scopes or "").split(",") if s.strip()}
		from excom.excom_ads.settings import get_settings

		s = get_settings()
		self.version = s.meta_api_version or DEFAULT_VERSION
		self.max_pages = int(s.max_pages_per_call or 200)
		self.currency = account.currency or ""

	# ─── transport ────────────────────────────────────────────────────────────

	def _url(self, path: str) -> str:
		return f"{GRAPH}/{self.version}/{path.lstrip('/')}"

	def _get(self, url: str, params: dict | None) -> dict:
		if params is not None:
			params = {**params, "access_token": self._token}
		status, body, headers = http_get(url, params)
		self.stats.api_calls += 1
		wait = _regain_minutes(headers)
		if status != 200:
			err = classify(status, body)
			err.args = (redact(str(err), [self._token]),)
			if err.error_class == RATE_LIMITED and wait:
				err.retry_after = wait * 60
			raise err
		if wait:
			# The call worked but the account is at its limit: stop this job cleanly instead of being cut off.
			self.stats.usage["regain_minutes"] = wait
		return body

	def _paged(self, path: str, params: dict) -> list[dict]:
		out, url, pages = [], self._url(path), 0
		while url:
			if self.stats.usage.get("regain_minutes"):
				raise AdsProviderError(f"Meta usage limit reached; access returns in about {self.stats.usage['regain_minutes']} min", RATE_LIMITED, retry_after=self.stats.usage["regain_minutes"] * 60)
			body = self._get(url, params)
			out.extend(body.get("data") or [])
			url = (body.get("paging") or {}).get("next")
			params = None  # the next URL carries every parameter, including the token
			pages += 1
			if pages >= self.max_pages:
				raise AdsProviderError(f"Stopped after {pages} pages of {path}; raise Ads Settings > Max Pages if this account is that large", RETRYABLE)
		return out

	# ─── reads ────────────────────────────────────────────────────────────────

	def read_account(self) -> dict:
		a = self._get(self._url(self.account.external_id), {"fields": ACCOUNT_FIELDS})
		self._account_raw = a  # read_billing() reuses it instead of asking again
		currency = a.get("currency") or self.currency
		self.currency = currency
		code = a.get("account_status")
		return {
			"account_name": a.get("name"),
			"currency": currency,
			"timezone_name": a.get("timezone_name"),
			"platform_status": str(code) if code is not None else "",
			"status": ACCOUNT_STATUS.get(code, "Unknown"),
			"disable_reason": DISABLE_REASON.get(a.get("disable_reason"), str(a.get("disable_reason") or "")),
			"reported_amount_spent": money(a.get("amount_spent"), currency),
			# Meta returns "0" for "no cap"; has_spend_cap keeps that from reading as a zero-rupee cap.
			"reported_spend_cap": money(a.get("spend_cap"), currency),
			"has_spend_cap": 1 if money(a.get("spend_cap"), currency) else 0,
			"reported_amount_due": money(a.get("balance"), currency),
			"billing_status": "Reported" if a.get("amount_spent") is not None else "Not Available",
			"can_read": 1,
			"can_billing": 1,
			# Write needs ads_management; until debug_token has run we do not know, so it stays off.
			"can_write": 1 if "ads_management" in self.scopes else 0,
			"can_leads": 1 if "leads_retrieval" in self.scopes else 0,
		}

	def list_campaigns(self) -> list[dict]:
		rows = self._paged(f"{self.account.external_id}/campaigns", {"fields": CAMPAIGN_FIELDS, "limit": 200})
		c = self.currency
		return [
			{
				"external_id": r["id"],
				"campaign_name": r.get("name") or r["id"],
				"objective": r.get("objective"),
				"buying_type": r.get("buying_type"),
				"bid_strategy": r.get("bid_strategy"),
				"configured_status": r.get("configured_status") or r.get("status"),
				"effective_status": r.get("effective_status"),
				"status": status_of(r.get("effective_status")),
				"daily_budget": money(r.get("daily_budget"), c),
				"lifetime_budget": money(r.get("lifetime_budget"), c),
				"spend_cap": money(r.get("spend_cap"), c),
				"start_time": r.get("start_time"),
				"stop_time": r.get("stop_time"),
				"platform_created": r.get("created_time"),
				"platform_updated": r.get("updated_time"),
				"raw": r,
			}
			for r in rows
		]

	def list_ad_groups(self) -> list[dict]:
		rows = self._paged(f"{self.account.external_id}/adsets", {"fields": ADSET_FIELDS, "limit": 200})
		c = self.currency
		return [
			{
				"external_id": r["id"],
				"ad_group_name": r.get("name") or r["id"],
				"campaign_external_id": r.get("campaign_id"),
				"configured_status": r.get("configured_status") or r.get("status"),
				"effective_status": r.get("effective_status"),
				"status": status_of(r.get("effective_status")),
				"optimization_goal": r.get("optimization_goal"),
				"billing_event": r.get("billing_event"),
				"destination_type": r.get("destination_type"),
				"bid_amount": money(r.get("bid_amount"), c),
				"daily_budget": money(r.get("daily_budget"), c),
				"lifetime_budget": money(r.get("lifetime_budget"), c),
				"start_time": r.get("start_time"),
				"end_time": r.get("end_time"),
				"platform_updated": r.get("updated_time"),
				"targeting": r.get("targeting"),
				"raw": {k: v for k, v in r.items() if k != "targeting"},
			}
			for r in rows
		]

	def list_ads(self) -> list[dict]:
		rows = self._paged(f"{self.account.external_id}/ads", {"fields": AD_FIELDS, "limit": 200})
		return [
			{
				"external_id": r["id"],
				"ad_name": r.get("name") or r["id"],
				"campaign_external_id": r.get("campaign_id"),
				"ad_group_external_id": r.get("adset_id"),
				"configured_status": r.get("configured_status") or r.get("status"),
				"effective_status": r.get("effective_status"),
				"status": status_of(r.get("effective_status")),
				"creative_id": (r.get("creative") or {}).get("id"),
				"creative_name": (r.get("creative") or {}).get("name"),
				"platform_updated": r.get("updated_time"),
				"raw": r,
			}
			for r in rows
		]

	def daily_metrics(self, level: str, since, until) -> list[dict]:
		"""Daily rows for one level over [since, until], in windows small enough for a synchronous call."""
		from frappe.utils import add_days, getdate

		since, until = getdate(since), getdate(until)
		out, start = [], since
		step = WINDOW_DAYS[level]
		while start <= until:
			end = min(add_days(start, step - 1), until)
			params = {
				"level": LEVELS[level],
				"fields": INSIGHT_FIELDS,
				"time_increment": 1,
				"time_range": json.dumps({"since": str(start), "until": str(end)}),
				"use_unified_attribution_setting": "true",
				"limit": 500,
			}
			for r in self._paged(f"{self.account.external_id}/insights", params):
				out.append(self._metric_row(level, r, f"{start}..{end}"))
			start = add_days(end, 1)
		return out

	def _metric_row(self, level: str, r: dict, window: str) -> dict:
		entity = r.get(ENTITY_KEY[level])
		if level == "Account":
			entity = self.account.external_id
		actions, values = r.get("actions") or [], r.get("action_values") or []
		return {
			"level": level,
			"date": r.get("date_start"),
			"entity_external_id": entity,
			"campaign_external_id": r.get("campaign_id"),
			"ad_group_external_id": r.get("adset_id"),
			"ad_external_id": r.get("ad_id"),
			"spend": float(r.get("spend") or 0),
			"impressions": int(r.get("impressions") or 0),
			"reach": int(r.get("reach") or 0),
			"clicks": int(r.get("clicks") or 0),
			"link_clicks": int(r.get("inline_link_clicks") or 0),
			"leads": _pick(actions, LEAD_ACTIONS),
			"purchases": _pick(actions, PURCHASE_ACTIONS),
			"purchase_value": _pick(values, PURCHASE_ACTIONS),
			"messaging_conversations": _pick(actions, MESSAGING_ACTIONS),
			"metrics": {"actions": actions, "action_values": values},
			"source_window": window,
		}

	def read_billing(self) -> dict:
		"""Billing figures exactly as Meta reports them, with the availability of each (see billing.py).

		amount_spent / spend_cap / balance come with the account read (ads_read). The funding fields need
		task access the token may not have: a permission error on them marks them Permission Missing
		and never fails the read.
		"""
		from excom.excom_ads import billing as B

		a = getattr(self, "_account_raw", None) or self._get(self._url(self.account.external_id), {"fields": BILLING_CORE_FIELDS})
		c = a.get("currency") or self.currency
		spent, cap, due = money(a.get("amount_spent"), c), money(a.get("spend_cap"), c), money(a.get("balance"), c)
		out = {
			"currency": c,
			"reported_amount_spent": spent,
			"amount_spent_avail": B.AVAILABLE if spent is not None else B.NO_DATA,
			# "0" (or no value) is Meta's "no cap": Not Set, never a zero cap.
			"reported_spend_cap": cap if cap else None,
			"spend_cap_avail": B.AVAILABLE if cap else (B.NOT_SET if cap is not None else B.NO_DATA),
			"reported_amount_due": due,
			"amount_due_avail": B.AVAILABLE if due is not None else B.NO_DATA,
			"errors": [],
			"raw": {k: a.get(k) for k in ("amount_spent", "spend_cap", "balance", "currency")},
		}
		funding, errors = self._funding()
		out["errors"] += errors
		out["raw"].update({k: v for k, v in funding.items() if not k.endswith("_avail")})
		prepay, ftype = funding.get("is_prepay_account"), funding.get("funding_type")
		out.update(
			{
				"reported_is_prepay": 1 if prepay else 0,
				"prepay_avail": funding["prepay_avail"],
				"reported_funding_type": ftype,
				"funding_avail": funding["funding_avail"],
			}
		)
		model = payment_model(prepay if funding["prepay_avail"] == B.AVAILABLE else None, ftype if funding["funding_avail"] == B.AVAILABLE else None)
		if model:
			out["payment_model"], out["payment_model_avail"] = model, B.AVAILABLE
		else:
			out["payment_model"] = "Unknown"
			worst = [s for s in (funding["prepay_avail"], funding["funding_avail"]) if s in (B.PERMISSION_MISSING, B.ERROR)]
			out["payment_model_avail"] = worst[0] if worst else B.NO_DATA
		out["headroom"] = {
			"avail": out["spend_cap_avail"],
			"limit": cap if cap else None,
			"used": spent,
			"basis": "Meta spend cap − amount spent",
		}
		return out

	def _funding(self) -> tuple[dict, list]:
		"""is_prepay_account + funding_source_details, falling back to is_prepay_account alone when the
		token lacks MANAGE access. Returns ({is_prepay_account, funding_type, *_avail}, [errors])."""
		from excom.excom_ads import billing as B

		url = self._url(self.account.external_id)
		res = {"is_prepay_account": None, "funding_type": None, "prepay_avail": B.NO_DATA, "funding_avail": B.NO_DATA}
		errors = []
		for fields in (FUNDING_FIELDS, "is_prepay_account"):
			try:
				body = self._get(url, {"fields": fields})
			except AdsProviderError as e:
				if e.error_class == TOKEN:
					raise
				status = B.PERMISSION_MISSING if e.error_class == PERMISSION else B.ERROR
				if fields == FUNDING_FIELDS:
					res["funding_avail"] = status
					if status == B.PERMISSION_MISSING:
						continue  # try the prepay flag on its own: it needs less access
				res["prepay_avail"] = status
				if status == B.ERROR:
					errors.append(e)  # a missing permission is a status, not a failed read
				return res, errors
			if "is_prepay_account" in body:
				res["is_prepay_account"] = bool(body.get("is_prepay_account"))
				res["prepay_avail"] = B.AVAILABLE
			if fields == FUNDING_FIELDS:
				details = body.get("funding_source_details")
				if isinstance(details, dict) and details.get("type") not in (None, ""):
					res["funding_type"] = funding_type_name(details.get("type"))
					res["funding_avail"] = B.AVAILABLE
			return res, errors
		return res, errors


def funding_type_name(value) -> str:
	try:
		return FUNDING_TYPES.get(int(value), str(value))
	except (TypeError, ValueError):
		return str(value).upper()


def payment_model(is_prepay, funding_type) -> str | None:
	"""Our payment model from Meta's prepay flag and funding source type. None = cannot tell."""
	if is_prepay:
		return "Prepaid"
	model = FUNDING_MODEL.get(funding_type or "")
	if model:
		return model
	return None
