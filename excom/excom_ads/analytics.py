"""GA4 web analytics: discover and enable properties, daily snapshots, the Website read API.

Figures are labelled source "Analytics": what Google Analytics measured on the website, under its
own session attribution. They are never Verified (ERPNext) figures and never platform ad figures,
and nothing here adds them to either.

    Ads Analytics Property   one GA4 property (GA4-{property id}), read through an Ads Connection
                             whose Scopes include analytics.readonly
    Ads Web Snapshot         one row per property × report × day (property time zone) × dimensions:
                             Source report  = session source / medium / campaign name / campaign id
                             Landing Page   = landing page
                             metrics: sessions, total users, new users, engaged sessions, key
                             events, transactions, purchase revenue (property currency)

Jobs (enqueue only from the scheduler, long queue, job_id + deduplicate):
    Metrics   hourly: today and Ads Settings → ga4_lookback_days before it
    Settle    daily: the trailing ga4_settle_days (GA4 data changes for 24-48 h, key-event
              attribution for up to 12 days)
    Backfill  first time: ga4_backfill_days of history, newest window first; an interrupted
              backfill resumes from where it stopped (synced_from)

A re-pulled window replaces its rows: rows GA4 no longer returns for that window are removed, so
nothing stale survives. Names are a hash of the natural key, so any re-run writes the same rows.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime

from excom.excom_ads import permissions
from excom.excom_ads.doctype.ads_analytics_property.ads_analytics_property import normalise_property_id, property_doc_name
from excom.excom_ads.naming import web_snapshot_name
from excom.excom_ads.providers.base import PERMISSION, RATE_LIMITED, TOKEN, AdsProviderError, CallStats
from excom.excom_ads.providers.google_analytics import GA4Client

PROP = "Ads Analytics Property"
WEB = "Ads Web Snapshot"
SOURCE = "Analytics"
JOB_QUEUE = "long"
WINDOW_DAYS = 31
TEXT_LIMITS = {"session_source": 255, "session_medium": 255, "session_campaign_name": 255, "session_campaign_id": 140, "landing_page": 1000}
INT_METRICS = ("sessions", "total_users", "new_users", "engaged_sessions", "transactions")
FLOAT_METRICS = ("key_events", "purchase_revenue")
METRICS = INT_METRICS + FLOAT_METRICS
# GA4 placeholders that are not a campaign ID.
NO_ID = {"", "(not set)", "(none)", "(direct)", "(organic)", "(referral)", "0"}
FAILURES_BEFORE_ALERT = 3


def available() -> bool:
	return bool(frappe.db.exists("DocType", PROP))


# ─── discovery and enabling (admin; permission checks in api_analytics.py) ───

def discover(connection: str) -> list[dict]:
	"""Every GA4 property the connection can read, from the Admin API's account summaries."""
	client = GA4Client.for_connection(connection, stats=CallStats())
	found = []
	for acc in client.account_summaries():
		for p in acc.get("propertySummaries") or []:
			pid = str(p.get("property") or "").split("/")[-1]
			if not pid:
				continue
			found.append({"property_id": pid, "display_name": p.get("displayName") or pid, "property_type": p.get("propertyType") or "", "account": acc.get("account") or "", "account_name": acc.get("displayName") or ""})
	enabled = dict(frappe.get_all(PROP, filters={"property_id": ["in", [f["property_id"] for f in found] or ["-"]]}, fields=["property_id", "name"], as_list=True))
	for f in found:
		f["enabled_as"] = enabled.get(f["property_id"])
	found.sort(key=lambda f: (f["account_name"].lower(), f["display_name"].lower()))
	return found


def enable(connection: str, property_id: str, enabled: int = 1, workspace: str | None = None, brand: str | None = None) -> dict:
	"""Read the property through the connection (proves access; gives name, time zone, currency and
	website) and upsert the Ads Analytics Property. Disabling keeps the property and its history."""
	pid = normalise_property_id(property_id)
	name = property_doc_name(pid)
	if not cint(enabled):
		if frappe.db.exists(PROP, name):
			frappe.db.set_value(PROP, name, "sync_enabled", 0)
		return {"property": name if frappe.db.exists(PROP, name) else None, "enabled": 0}
	conn = frappe.get_doc("Ads Connection", connection)
	client = GA4Client.for_connection(connection, stats=CallStats())
	p = client.get_property(pid)
	try:
		streams = client.data_streams(pid)
	except AdsProviderError as e:
		if e.error_class == TOKEN:
			raise
		streams = []  # the website is a nicety; the property itself was readable
	web = next((s for s in streams if s.get("type") == "WEB_DATA_STREAM"), None) or {}
	web_data = web.get("webStreamData") or {}
	currency = p.get("currencyCode") if p.get("currencyCode") and frappe.db.exists("Currency", p.get("currencyCode")) else None
	values = {
		"property_name": (p.get("displayName") or pid)[:140],
		"time_zone": p.get("timeZone"),
		"currency": currency,
		"property_type": p.get("propertyType"),
		"ga_account": p.get("account") or p.get("parent"),
		"website": (web_data.get("defaultUri") or "")[:140] or None,
		"measurement_id": web_data.get("measurementId"),
	}
	if frappe.db.exists(PROP, name):
		doc = frappe.get_doc(PROP, name)
		if doc.company != conn.company:
			frappe.throw(_("GA4 property {0} is already enabled for company {1}").format(pid, doc.company))
	else:
		from excom.excom_ads.accounts import default_workspace

		doc = frappe.new_doc(PROP)
		doc.property_id = pid
		doc.workspace = workspace or conn.workspace or default_workspace(conn.company)
		doc.brand = brand
	doc.update(values)
	doc.connection = conn.name
	doc.sync_enabled = 1
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	enqueue(doc.name, "Metrics" if doc.synced_from else "Backfill", triggered_by=frappe.session.user)
	return {"property": doc.name, "enabled": 1}


# ─── jobs ─────────────────────────────────────────────────────────────────────

def enqueue(prop: str, job_type: str, since=None, until=None, triggered_by: str = "scheduler"):
	return frappe.enqueue(
		"excom.excom_ads.analytics.run_job",
		queue=JOB_QUEUE,
		timeout=3600,
		job_id=f"excom_ads_ga4::{frappe.local.site}::{prop}::{job_type}",
		deduplicate=True,
		prop=prop,
		job_type=job_type,
		since=str(since) if since else None,
		until=str(until) if until else None,
		triggered_by=triggered_by,
	)


def property_today(prop):
	"""GA4 reports days in the property's reporting time zone."""
	if prop.get("time_zone"):
		try:
			from datetime import datetime
			from zoneinfo import ZoneInfo

			return datetime.now(ZoneInfo(prop.time_zone)).date()
		except Exception:
			pass
	return getdate()


def window(prop, job_type: str, since=None, until=None):
	from excom.excom_ads.settings import get_settings

	if since and until:
		return getdate(since), getdate(until)
	s = get_settings()
	today = property_today(prop)
	if job_type == "Metrics":
		return getdate(add_days(today, -cint(s.ga4_lookback_days))), today
	if job_type == "Settle":
		return getdate(add_days(today, -cint(s.ga4_settle_days))), getdate(add_days(today, -1))
	if job_type == "Backfill":
		start = getdate(add_days(today, -cint(s.ga4_backfill_days)))
		end = getdate(add_days(prop.synced_from, -1)) if prop.get("synced_from") else today
		return start, end
	frappe.throw(_("Unknown job type {0}").format(job_type))


def backfill_incomplete(prop) -> bool:
	from excom.excom_ads.settings import get_settings

	if not prop.get("synced_from"):
		return True
	return getdate(prop.synced_from) > getdate(add_days(property_today(prop), -cint(get_settings().ga4_backfill_days)))


def run_job(prop: str, job_type: str, since=None, until=None, triggered_by: str = "scheduler") -> dict:
	doc = frappe.get_doc(PROP, prop)
	stats = CallStats()
	client = None
	result = {"records": 0, "errors": []}
	try:
		client = GA4Client.for_connection(doc.connection, stats=stats)
		a, b = window(doc, job_type, since, until)
		if a <= b:
			result = sync_reports(doc, client, a, b)
	except AdsProviderError as e:
		frappe.db.rollback()
		_finish(doc, job_type, "Failed", stats, client, result, e)
		return {"status": "Failed", "error_class": e.error_class, "error": str(e)}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom Ads: GA4 {job_type} failed for {prop}", message=frappe.get_traceback())
		err = AdsProviderError(str(e)[:500])
		_finish(doc, job_type, "Failed", stats, client, result, err)
		return {"status": "Failed", "error_class": err.error_class, "error": str(e)}
	status = ("Partial" if result["records"] else "Failed") if result["errors"] else "Success"
	first = result["errors"][0] if result["errors"] else None
	_finish(doc, job_type, status, stats, client, result, first)
	return {"status": status, "records": result["records"], "error_class": first.error_class if first else None, "errors": [str(e) for e in result["errors"]], "api_calls": stats.api_calls}


def _chunks(a, b, newest_first: bool = True) -> list:
	out, end = [], getdate(b)
	start = getdate(a)
	while end >= start:
		s = max(start, getdate(add_days(end, -(WINDOW_DAYS - 1))))
		out.append((s, end))
		end = getdate(add_days(s, -1))
	return out if newest_first else list(reversed(out))


def sync_reports(prop, client, since, until) -> dict:
	"""Pull every report for [since, until] in windows, newest first. Each fully written window
	extends synced_from / synced_until; the first error stops older windows, so the synced range
	never has a hole in it."""
	reports = ["Source"] + (["Landing Page"] if cint(prop.sync_landing_pages) else [])
	written, errors = 0, []
	for a, b in _chunks(since, until):
		ok = True
		for report in reports:
			try:
				rows = client.run_report(prop.property_id, report, a, b)
			except AdsProviderError as e:
				if e.error_class in (TOKEN, PERMISSION):
					raise  # the property is unreadable: nothing else will work either
				errors.append(e)
				ok = False
				break
			written += write_rows(prop, report, rows, a, b)
			frappe.db.commit()
		if not ok:
			break
		values = {
			"synced_from": min(getdate(a), getdate(prop.synced_from)) if prop.get("synced_from") else getdate(a),
			"synced_until": max(getdate(b), getdate(prop.synced_until)) if prop.get("synced_until") else getdate(b),
		}
		frappe.db.set_value(PROP, prop.name, values, update_modified=False)
		prop.update(values)
		frappe.db.commit()
	return {"records": written, "errors": errors}


def _clip(value, limit: int):
	if value is None:
		return None
	value = str(value)
	return value[:limit]


def _campaign_map(company: str | None, ids: set) -> dict:
	"""GA4 sessionCampaignId → Ads Campaign, by exact platform campaign ID within the property's
	company. Google Ads auto-tagging fills it with the Google campaign ID; manual tagging with utm_id.
	Two campaigns with the same ID (two networks) → no link rather than a guess."""
	ids = {i for i in ids if i and i.lower() not in NO_ID}
	if not ids or not company:
		return {}
	found: dict[str, list] = {}
	for r in frappe.get_all("Ads Campaign", filters={"external_id": ["in", sorted(ids)], "company": company}, fields=["name", "external_id", "provider"]):
		found.setdefault(r.external_id, []).append(r)
	out = {}
	for ext, rows in found.items():
		if len(rows) == 1:
			out[ext] = rows[0].name
		else:
			google = [r for r in rows if r.provider == "Google"]
			if len(google) == 1:
				out[ext] = google[0].name
	return out


WRITE_FIELDS = ("name", "owner", "modified_by", "creation", "modified", "docstatus", "property", "date", "report", "session_source", "session_medium", "session_campaign_name", "session_campaign_id", "landing_page", "campaign", "company", "workspace", "brand", "currency", "synced_at") + METRICS


def write_rows(prop, report: str, rows: list[dict], since, until) -> int:
	"""Replace the property's `report` rows for [since, until] with `rows` (one transaction)."""
	dims = ["session_source", "session_medium", "session_campaign_name", "session_campaign_id"] if report == "Source" else ["landing_page"]
	camps = _campaign_map(prop.company, {str(r.get("session_campaign_id") or "") for r in rows}) if report == "Source" else {}
	now = now_datetime()
	agg: dict[str, dict] = {}
	for r in rows:
		d = r.get("date")
		if not d or not (str(since) <= d <= str(until)):
			continue
		dv = {f: _clip(r.get(f), TEXT_LIMITS[f]) for f in dims}
		name = web_snapshot_name(prop.name, report, d, dv.get("session_source"), dv.get("session_medium"), dv.get("session_campaign_name"), dv.get("session_campaign_id"), dv.get("landing_page"))
		row = agg.get(name)
		if row is None:
			row = agg[name] = {"name": name, "date": d, **dv, "campaign": camps.get(str(dv.get("session_campaign_id") or "")), **{m: 0 for m in METRICS}}
		for m in METRICS:
			row[m] += flt(r.get(m))  # clipping can merge two long values into one row: add them up
	frappe.db.delete(WEB, {"property": prop.name, "report": report, "date": ["between", [since, until]]})
	user = frappe.session.user or "Administrator"
	values = []
	for row in agg.values():
		base = {
			"owner": user, "modified_by": user, "creation": now, "modified": now, "docstatus": 0, "property": prop.name, "report": report,
			"company": prop.company, "workspace": prop.workspace, "brand": prop.brand, "currency": prop.currency, "synced_at": now,
		}
		full = {**base, **row}
		for m in INT_METRICS:
			full[m] = int(round(full[m]))
		values.append(tuple(full.get(f) for f in WRITE_FIELDS))
	if values:
		# ignore_duplicates: a Metrics and a Settle job of the same property that overlap write the same
		# deterministic rows from the same source; the second copy is redundant, not a conflict.
		frappe.db.bulk_insert(WEB, list(WRITE_FIELDS), values, ignore_duplicates=True)
	return len(values)


def _finish(prop, job_type: str, status: str, stats, client, result: dict, err) -> None:
	now = now_datetime()
	values = {"last_sync_at": now, "last_job_type": job_type, "last_status": status, "api_calls": stats.api_calls, "rows_written": result.get("records", 0)}
	if client is not None and client.quota:
		values.update({"tokens_day_remaining": client.quota.get("tokensPerDay"), "tokens_hour_remaining": client.quota.get("tokensPerHour"), "quota_checked_at": now})
	if client is not None and status != "Failed":
		values["data_loss_other_row"] = 1 if client.metadata.get("dataLossFromOtherRow") else 0
		values["subject_to_thresholding"] = 1 if client.metadata.get("subjectToThresholding") else 0
	failures = cint(frappe.db.get_value(PROP, prop.name, "consecutive_failures"))
	if status == "Success":
		values.update({"error_class": None, "last_error": None, "consecutive_failures": 0, "last_success_at": now})
		failures = 0
	else:
		failures += 1 if status == "Failed" else 0
		values.update({"error_class": err.error_class if err else None, "last_error": f"{job_type}: {err}"[:1000] if err else None, "consecutive_failures": failures})
	frappe.db.set_value(PROP, prop.name, values, update_modified=False)
	_alert(prop, status, err, failures)
	frappe.db.commit()


def _alert(prop, status: str, err, failures: int) -> None:
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	keys = {"Token": f"ga4_token:{prop.name}", "Permission": f"ga4_permission:{prop.name}", "Rate Limited": f"ga4_rate:{prop.name}", "sync": f"ga4_sync:{prop.name}"}
	if status == "Success":
		for key in keys.values():
			resolve_alert(key, "GA4 sync works again")
		return
	if not err:
		return
	label = f"GA4 {prop.property_name}"
	if err.error_class == TOKEN:
		raise_alert("Token Problem", "Critical", f"{label}: Google sign-in is not working", f"{err}\nFix the credential on Ads Connection {prop.connection}.", ref=prop, dedupe_key=keys["Token"])
	elif err.error_class == PERMISSION:
		raise_alert("Permission Missing", "High", f"{label}: no access to the property", str(err), ref=prop, dedupe_key=keys["Permission"])
	elif err.error_class == RATE_LIMITED:
		raise_alert("Rate Limited", "Medium", f"{label}: Analytics quota used up", f"{err}\nThe next scheduled run continues where this one stopped.", ref=prop, dedupe_key=keys["Rate Limited"])
	elif failures >= FAILURES_BEFORE_ALERT:
		raise_alert("Sync Failed", "Medium", f"{label}: sync failing ({failures} in a row)", str(err), ref=prop, dedupe_key=keys["sync"])


# ─── reading (Website tab) ────────────────────────────────────────────────────

GROUPS = {
	# group_by -> (report, SELECT/GROUP BY columns)
	"source_medium": ("Source", ["session_source", "session_medium"]),
	"campaign": ("Source", ["session_campaign_name", "session_campaign_id", "campaign"]),
	"landing_page": ("Landing Page", ["landing_page"]),
	"date": ("Source", ["date"]),
}
SUMS = ", ".join(f"sum(w.{m}) as {m}" for m in METRICS)


def _derive(r: dict) -> dict:
	out = {k: (flt(v) if k in METRICS else v) for k, v in r.items()}
	s = flt(out.get("sessions"))
	out["engagement_rate"] = flt(out.get("engaged_sessions")) / s * 100 if s else None
	out["key_events_per_session"] = flt(out.get("key_events")) / s if s else None
	out["transactions_per_session"] = flt(out.get("transactions")) / s * 100 if s else None
	out["revenue_per_session"] = flt(out.get("purchase_revenue")) / s if s else None
	return out


def _properties_in_scope(filters: dict) -> list[dict]:
	if not frappe.has_permission(PROP, "read"):
		return []
	f = {k: v for k, v in filters.items() if k in ("company", "workspace", "brand") and v}
	return frappe.get_list(PROP, filters=f, fields=["name", "property_name", "property_id", "website", "currency", "time_zone", "company", "workspace", "brand", "sync_enabled", "last_sync_at", "last_success_at", "last_status", "error_class", "last_error", "synced_from", "synced_until", "data_loss_other_row", "subject_to_thresholding"], order_by="property_name asc", limit_page_length=0)


@frappe.whitelist()
def web_report(from_date, to_date, property: str | None = None, group_by: str = "source_medium", search: str | None = None, start: int = 0, page_length: int = 50, **kwargs) -> dict:
	"""GA4 sessions / key events / revenue for the range, grouped one way, inside the caller's scope.
	Totals are per currency (a property's currency). Users are counted per day and source by GA4, so
	a range total of users can count one person more than once: it is labelled, not hidden."""
	from excom.excom_ads.dashboard import _clean_filters, _dates

	permissions.require_access()
	if group_by not in GROUPS:
		frappe.throw(_("Unknown grouping {0}").format(group_by))
	a, b = _dates(from_date, to_date)
	start, page_length = max(cint(start), 0), min(max(cint(page_length), 1), 200)
	props = _properties_in_scope(_clean_filters(kwargs)) if available() else []
	out = {"source": SOURCE, "from_date": str(a), "to_date": str(b), "group_by": group_by, "properties": props, "totals": [], "series": [], "rows": [], "total_rows": 0}
	if property:
		props = [p for p in props if p.name == property]
		if not props:
			raise frappe.PermissionError(_("Not permitted"))
	if not props:
		return out
	report, cols = GROUPS[group_by]
	cond = ["w.property in %(props)s", "w.date between %(a)s and %(b)s"]
	params = {"props": tuple(p.name for p in props), "a": a, "b": b}
	scope_sql = permissions.query_conditions(WEB)
	if scope_sql:
		cond.append(scope_sql.replace(f"`tab{WEB}`.", "w."))
	base = " and ".join(cond)
	src = f"`tab{WEB}` w where {base} and w.report = %(source)s"
	totals = frappe.db.sql(f"select w.currency, {SUMS} from {src} group by w.currency", {**params, "source": "Source"}, as_dict=True)
	series = frappe.db.sql(f"select w.date, w.currency, {SUMS} from {src} group by w.date, w.currency order by w.date", {**params, "source": "Source"}, as_dict=True)
	gcols = ", ".join(f"w.{c}" for c in cols)
	where = f"{base} and w.report = %(report)s"
	gparams = {**params, "report": report}
	if search and group_by != "date":
		where += " and (" + " or ".join(f"w.{c} like %(search)s" for c in cols if c != "campaign") + ")"
		gparams["search"] = f"%{search}%"
	total_rows = frappe.db.sql(f"select count(*) from (select 1 from `tab{WEB}` w where {where} group by {gcols}, w.currency) t", gparams)[0][0]
	order = "w.date asc" if group_by == "date" else "sessions desc"
	rows = frappe.db.sql(f"select {gcols}, w.currency, {SUMS} from `tab{WEB}` w where {where} group by {gcols}, w.currency order by {order}, {gcols} limit %(start)s, %(page_length)s", {**gparams, "start": start, "page_length": page_length}, as_dict=True)
	if group_by == "campaign":
		names = {r.campaign for r in rows if r.campaign}
		titles = dict(frappe.get_all("Ads Campaign", filters={"name": ["in", sorted(names) or ["-"]]}, fields=["name", "campaign_name"], as_list=True)) if names else {}
		for r in rows:
			r["campaign_title"] = titles.get(r.campaign)
	out.update(
		{
			"totals": [_derive(t) for t in totals],
			"series": [_derive(r) | {"date": str(r.date)} for r in series],
			"rows": [_derive(r) | ({"date": str(r.date)} if group_by == "date" else {}) for r in rows],
			"total_rows": total_rows,
			"users_note": "GA4 counts users per day and per source: a total over several days or sources can count the same person more than once.",
		}
	)
	return out
