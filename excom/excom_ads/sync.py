"""Ads sync engine: one job = one account × one job type, recorded as an Ads Sync Job.

Structure  account details + campaigns + ad groups + ads (mirrors)
Metrics    daily rows for a recent window (intraday refresh)
Settle     re-pull of the trailing settle window, because platforms revise results for weeks
Backfill   first-time history

Rows are named from their natural keys (see naming.py), so running any job twice writes the same
rows. A failure never reads as success: the job is Failed or Partial and the account shows why.
"""

import json
from datetime import datetime

import frappe
from frappe.utils import add_days, getdate, now_datetime

from excom.excom_ads.naming import mirror_name, snapshot_name
from excom.excom_ads.providers.base import RATE_LIMITED, TOKEN, AdsProviderError, get_provider

JOB_QUEUE = "long"
METRIC_LEVELS = ("Account", "Campaign", "Ad Group", "Ad")
MIRRORS = {
	"Ads Campaign": "list_campaigns",
	"Ads Ad Group": "list_ad_groups",
	"Ads Advertisement": "list_ads",
}
SNAPSHOT_FIELDS = ("spend", "impressions", "reach", "clicks", "link_clicks", "leads", "purchases", "purchase_value", "messaging_conversations")


# ─── entry points ─────────────────────────────────────────────────────────────

def enqueue(account: str, job_type: str, since=None, until=None, triggered_by: str = "scheduler"):
	"""Queue one job. A second request for the same account and job type while one is waiting or running is dropped."""
	return frappe.enqueue(
		"excom.excom_ads.sync.run_job",
		queue=JOB_QUEUE,
		timeout=3600,
		job_id=f"excom_ads::{frappe.local.site}::{account}::{job_type}",
		deduplicate=True,
		account=account,
		job_type=job_type,
		since=str(since) if since else None,
		until=str(until) if until else None,
		triggered_by=triggered_by,
	)


def run_job(account: str, job_type: str, since=None, until=None, triggered_by: str = "scheduler") -> dict:
	acc = frappe.get_doc("Ads Account", account)
	job = frappe.get_doc({"doctype": "Ads Sync Job", "account": account, "job_type": job_type, "status": "Running", "started_at": now_datetime(), "triggered_by": triggered_by})
	job.insert(ignore_permissions=True)
	frappe.db.commit()
	result = {"records": 0, "errors": []}
	provider = None
	try:
		provider = get_provider(acc)
		if job_type == "Structure":
			result = sync_structure(acc, provider)
		else:
			since, until = _window(acc, job_type, since, until)
			job.window_from, job.window_to = since, until
			result = sync_metrics(acc, provider, since, until)
	except AdsProviderError as e:
		frappe.db.rollback()
		_finish(job, acc, provider, "Failed", result, e)
		return {"status": "Failed", "error_class": e.error_class, "error": str(e)}
	except Exception as e:
		frappe.db.rollback()
		log = frappe.log_error(title=f"Excom Ads: {job_type} failed for {account}", message=frappe.get_traceback())
		err = AdsProviderError(str(e)[:500])
		job.error_log = log.name if log else None
		_finish(job, acc, provider, "Failed", result, err)
		return {"status": "Failed", "error_class": err.error_class, "error": str(e)}
	# Errors with nothing written is a failure, not a partial success.
	status = ("Partial" if result["records"] else "Failed") if result["errors"] else "Success"
	first = result["errors"][0] if result["errors"] else None
	_finish(job, acc, provider, status, result, first)
	return {"status": status, "records": result["records"], "error_class": first.error_class if first else None, "errors": [str(e) for e in result["errors"]]}


def _finish(job, acc, provider, status: str, result: dict, err: AdsProviderError | None):
	job.status = status
	job.finished_at = now_datetime()
	job.api_calls = provider.stats.api_calls if provider else 0
	job.records_written = result.get("records", 0)
	job.error_class = err.error_class if err else None
	job.error = str(err)[:1000] if err else None
	job.save(ignore_permissions=True)
	if status == "Success":
		values = {"error_class": None, "last_error": None, "consecutive_failures": 0}
	else:
		values = {"error_class": err.error_class if err else None, "last_error": f"{job.job_type}: {err}"[:1000] if err else None, "consecutive_failures": (acc.consecutive_failures or 0) + (1 if status == "Failed" else 0)}
	frappe.db.set_value("Ads Account", acc.name, values, update_modified=False)
	frappe.db.commit()


def _window(acc, job_type: str, since, until):
	from excom.excom_ads.settings import get_settings

	s = get_settings()
	today = account_today(acc)
	if since and until:
		return getdate(since), getdate(until)
	if job_type == "Metrics":
		return add_days(today, -s.today_lookback_days), today
	if job_type == "Settle":
		return add_days(today, -s.settle_days), add_days(today, -1)
	if job_type == "Backfill":
		return add_days(today, -s.backfill_days), today
	frappe.throw(f"Unknown job type {job_type}")


def account_today(acc):
	"""Platforms report days in the ad account's own time zone, so 'today' is computed there."""
	if acc.timezone_name:
		try:
			from zoneinfo import ZoneInfo

			return datetime.now(ZoneInfo(acc.timezone_name)).date()
		except Exception:
			pass
	return getdate()


# ─── structure ────────────────────────────────────────────────────────────────

def sync_structure(acc, provider) -> dict:
	written, errors = 0, []
	_apply_account(acc, provider.read_account(), last_structure_sync=now_datetime())
	known: dict[str, set] = {}
	for doctype, method in MIRRORS.items():
		try:
			rows = getattr(provider, method)()
		except AdsProviderError as e:
			if e.error_class == TOKEN:
				raise
			errors.append(e)
			if e.error_class == RATE_LIMITED:
				break
			continue
		known[doctype] = _upsert_mirrors(acc, doctype, rows, known)
		written += len(rows)
		frappe.db.commit()
	return {"records": written, "errors": errors}


def _apply_account(acc, details: dict, **extra) -> None:
	details = {k: v for k, v in details.items() if not (k == "account_name" and not v)}
	# Number columns cannot hold "unknown"; billing_status says whether the amounts were reported.
	details.update({k: 0 for k, v in details.items() if v is None and k.startswith("reported_")})
	details.update(extra, billing_reported_at=now_datetime())
	frappe.db.set_value("Ads Account", acc.name, details, update_modified=False)
	acc.reload()


def _scope(acc) -> dict:
	return {"account": acc.name, "workspace": acc.workspace, "company": acc.company, "brand": acc.brand, "provider": acc.provider}


def _upsert_mirrors(acc, doctype: str, rows: list[dict], known: dict) -> set:
	existing = set(frappe.get_all(doctype, filters={"account": acc.name}, pluck="name"))
	campaigns = known.get("Ads Campaign") or set(frappe.get_all("Ads Campaign", filters={"account": acc.name}, pluck="name"))
	ad_groups = known.get("Ads Ad Group") or set(frappe.get_all("Ads Ad Group", filters={"account": acc.name}, pluck="name"))
	now = now_datetime()
	seen = set()
	for r in rows:
		r = dict(r)
		raw = r.pop("raw", None)
		values = {**_scope(acc), **r, "config_json": json.dumps(raw, sort_keys=True, default=str) if raw is not None else None, "not_returned_since": None}
		if doctype == "Ads Ad Group":
			values["targeting_json"] = json.dumps(values.pop("targeting", None) or {}, sort_keys=True)
		cid = values.pop("campaign_external_id", None)
		gid = values.pop("ad_group_external_id", None)
		if doctype in ("Ads Ad Group", "Ads Advertisement"):
			cname = mirror_name(acc.name, cid) if cid else None
			values["campaign"] = cname if cname in campaigns else None
			if doctype == "Ads Ad Group":
				values["campaign_external_id"] = cid
		if doctype == "Ads Advertisement":
			gname = mirror_name(acc.name, gid) if gid else None
			values["ad_group"] = gname if gname in ad_groups else None
		for k in ("start_time", "stop_time", "end_time", "platform_created", "platform_updated"):
			if k in values:
				values[k] = _dt(values[k])
		name = mirror_name(acc.name, values["external_id"])
		seen.add(name)
		if name in existing:
			doc = frappe.get_doc(doctype, name)
			if _changed(doc, values):
				doc.update(values)
				doc.synced_at = now
				doc.save(ignore_permissions=True)
			else:
				frappe.db.set_value(doctype, name, "synced_at", now, update_modified=False)
		else:
			frappe.get_doc({"doctype": doctype, **values, "synced_at": now}).insert(ignore_permissions=True)
	# Records the platform stopped returning are kept and marked, never deleted.
	gone = existing - seen
	if gone:
		for name in gone:
			if not frappe.db.get_value(doctype, name, "not_returned_since"):
				frappe.db.set_value(doctype, name, "not_returned_since", now, update_modified=False)
	return existing | seen


def _changed(doc, values: dict) -> bool:
	for k, v in values.items():
		cur = doc.get(k)
		if isinstance(cur, datetime) or isinstance(v, datetime):
			if _dt(cur) != _dt(v):
				return True
		elif isinstance(cur, (int, float)) or isinstance(v, (int, float)):
			if float(cur or 0) != float(v or 0):
				return True
		elif (cur or None) != (v or None):
			return True
	return False


def _dt(value):
	"""Platform timestamps ('2026-10-01T10:00:00+0530') → naive datetime in the site time zone."""
	if not value:
		return None
	if isinstance(value, datetime):
		return value.replace(tzinfo=None) if value.tzinfo is None else _to_site(value)
	s = str(value)
	try:
		if len(s) > 5 and s[-5] in "+-" and s[-3] != ":":
			s = s[:-2] + ":" + s[-2:]
		d = datetime.fromisoformat(s.replace("Z", "+00:00"))
	except ValueError:
		return None
	return d if d.tzinfo is None else _to_site(d)


def _to_site(d):
	from frappe.utils import get_system_timezone
	from zoneinfo import ZoneInfo

	return d.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None, microsecond=0)


# ─── metrics ──────────────────────────────────────────────────────────────────

def sync_metrics(acc, provider, since, until) -> dict:
	from excom.excom_ads.settings import get_settings

	if not acc.currency:
		# Currency comes with the account read; metrics without it would be amounts in nothing.
		_apply_account(acc, provider.read_account())
	s = get_settings()
	levels = [lvl for lvl in METRIC_LEVELS if lvl != "Ad" or s.sync_ad_level_metrics]
	written, errors = 0, []
	rates = _Rates(acc)
	for level in levels:
		try:
			rows = provider.daily_metrics(level, since, until)
		except AdsProviderError as e:
			if e.error_class == TOKEN:
				raise
			errors.append(e)
			if e.error_class == RATE_LIMITED:
				break
			continue
		written += _upsert_snapshots(acc, level, rows, since, until, rates)
		frappe.db.commit()
	if not errors or written:
		values = {"last_metrics_sync": now_datetime()}
		if not errors:
			values["metrics_synced_until"] = max(getdate(until), getdate(acc.metrics_synced_until)) if acc.metrics_synced_until else getdate(until)
			values["metrics_synced_from"] = min(getdate(since), getdate(acc.metrics_synced_from)) if acc.metrics_synced_from else getdate(since)
		frappe.db.set_value("Ads Account", acc.name, values, update_modified=False)
	return {"records": written, "errors": errors}


class _Rates:
	"""Spend in company currency, using ERPNext's rate for each day. Unknown rate → 0, shown as unavailable."""

	def __init__(self, acc):
		self.to = frappe.get_cached_value("Company", acc.company, "default_currency") if acc.company else None
		self.cache = {}

	def get(self, currency: str, date) -> float:
		if not currency or not self.to:
			return 0.0
		if currency == self.to:
			return 1.0
		key = (currency, str(date))
		if key not in self.cache:
			try:
				from erpnext.setup.utils import get_exchange_rate

				self.cache[key] = float(get_exchange_rate(currency, self.to, str(date)) or 0)
			except Exception:
				self.cache[key] = 0.0
		return self.cache[key]


def _upsert_snapshots(acc, level: str, rows: list[dict], since, until, rates: _Rates) -> int:
	dt = "Ads Metric Snapshot"
	existing = set(frappe.get_all(dt, filters={"account": acc.name, "level": level, "date": ["between", [since, until]]}, pluck="name"))
	campaigns = set(frappe.get_all("Ads Campaign", filters={"account": acc.name}, pluck="name"))
	ad_groups = set(frappe.get_all("Ads Ad Group", filters={"account": acc.name}, pluck="name"))
	ads = set(frappe.get_all("Ads Advertisement", filters={"account": acc.name}, pluck="name")) if level == "Ad" else set()
	company_currency = rates.to
	now = now_datetime()
	seen = set()
	for r in rows:
		if not r.get("date") or not r.get("entity_external_id"):
			continue
		name = snapshot_name(acc.name, level, r["entity_external_id"], r["date"])
		seen.add(name)
		rate = rates.get(acc.currency, r["date"])
		values = {f: r.get(f) or 0 for f in SNAPSHOT_FIELDS}
		values.update(
			{
				"currency": acc.currency,
				"company_currency": company_currency,
				"exchange_rate": rate,
				"spend_company_currency": (r.get("spend") or 0) * rate,
				"metrics_json": json.dumps(r.get("metrics") or {}, sort_keys=True),
				"synced_at": now,
				"source_window": r.get("source_window"),
			}
		)
		if name in existing:
			frappe.db.set_value(dt, name, values, update_modified=True)
			continue
		links = {}
		for field, ext, pool in (("campaign", r.get("campaign_external_id"), campaigns), ("ad_group", r.get("ad_group_external_id"), ad_groups), ("advertisement", r.get("ad_external_id"), ads)):
			n = mirror_name(acc.name, ext) if ext else None
			links[field] = n if n in pool else None
		doc = frappe.get_doc({"doctype": dt, **_scope(acc), **values, **links, "level": level, "date": r["date"], "entity_external_id": r["entity_external_id"], "breakdown_key": "none", "attribution_key": "default"})
		doc.insert(ignore_permissions=True)
	# A day/entity the platform no longer reports inside a re-pulled window had its results removed
	# (for example a refunded or invalid-click day). Zero it rather than keep a stale number.
	for name in existing - seen:
		frappe.db.set_value(dt, name, {**{f: 0 for f in SNAPSHOT_FIELDS}, "spend_company_currency": 0, "synced_at": now}, update_modified=True)
	return len(seen)
