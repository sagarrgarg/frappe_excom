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
	if acc.provider == "Manual":
		# No connector: figures come from Ads Metric Import. Nothing to pull, nothing to fail.
		return {"status": "Skipped", "records": 0, "error_class": None, "errors": []}
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
		from excom.excom_ads.redact import redact

		log = frappe.log_error(title=f"Excom Ads: {job_type} failed for {account}", message=redact(frappe.get_traceback()))
		err = AdsProviderError(redact(str(e))[:500])
		job.error_log = log.name if log else None
		_finish(job, acc, provider, "Failed", result, err)
		return {"status": "Failed", "error_class": err.error_class, "error": str(e)}
	# Errors with nothing written is a failure, not a partial success.
	status = ("Partial" if result["records"] else "Failed") if result["errors"] else "Success"
	first = _worst(result["errors"])
	_finish(job, acc, provider, status, result, first)
	return {"status": status, "records": result["records"], "error_class": first.error_class if first else None, "errors": [str(e) for e in result["errors"]]}


ERROR_RANK = {"Token": 5, "Permission": 4, "Invalid": 3, "Rate Limited": 2, "Retryable": 1}


def _worst(errors: list):
	"""The error a person most needs to act on, not merely the first one raised."""
	return max(errors, key=lambda e: ERROR_RANK.get(e.error_class, 0)) if errors else None


def _finish(job, acc, provider, status: str, result: dict, err: AdsProviderError | None):
	job.status = status
	job.finished_at = now_datetime()
	job.api_calls = provider.stats.api_calls if provider else 0
	job.records_written = result.get("records", 0)
	from excom.excom_ads.redact import redact

	job.error_class = err.error_class if err else None
	job.error = redact(str(err))[:1000] if err else None
	job.save(ignore_permissions=True)
	if status == "Success":
		values = {"error_class": None, "last_error": None, "consecutive_failures": 0}
	else:
		values = {"error_class": err.error_class if err else None, "last_error": redact(f"{job.job_type}: {err}")[:1000] if err else None, "consecutive_failures": (acc.consecutive_failures or 0) + (1 if status == "Failed" else 0)}
	frappe.db.set_value("Ads Account", acc.name, values, update_modified=False)
	_alert_on_result(acc, status, err, values.get("consecutive_failures") or 0)
	frappe.db.commit()


# Consecutive failures before a plain (retryable) error is worth a person's attention.
FAILURES_BEFORE_ALERT = 3


def _alert_on_result(acc, status: str, err, failures: int) -> None:
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	keys = {"Token": f"token:{acc.name}", "Permission": f"permission:{acc.name}"}
	if status == "Success":
		for key in (*keys.values(), f"sync:{acc.name}", f"rate:{acc.name}"):
			resolve_alert(key)
		return
	if not err:
		return
	if err.error_class == "Token":
		raise_alert("Token Problem", "Critical", f"{acc.account_name}: connection token is not working", f"{err}\nRe-authorise the connection ({acc.meta_connection or acc.get('connection') or acc.provider}). Metric syncs for this account are paused until it works again.", ref=acc, dedupe_key=keys["Token"])
	elif err.error_class == "Permission":
		raise_alert("Permission Missing", "High", f"{acc.account_name}: permission missing", str(err), ref=acc, dedupe_key=keys["Permission"])
	elif err.error_class == "Rate Limited" and failures >= FAILURES_BEFORE_ALERT:
		raise_alert("Rate Limited", "Medium", f"{acc.account_name}: platform rate limit", str(err), ref=acc, dedupe_key=f"rate:{acc.name}")
	elif failures >= FAILURES_BEFORE_ALERT:
		raise_alert("Sync Failed", "Medium", f"{acc.account_name}: sync failing ({failures} in a row)", str(err), ref=acc, dedupe_key=f"sync:{acc.name}")


ACCOUNT_STATUS_SEVERITY = {"Disabled": "Critical", "Unsettled": "Critical", "Closed": "High", "Pending Closure": "High", "Pending Review": "High", "Grace Period": "High"}


def _alert_on_account_status(acc, old_status: str | None) -> None:
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	key = f"account_status:{acc.name}"
	if acc.status == "Active":
		resolve_alert(key, "Account is active again")
		return
	sev = ACCOUNT_STATUS_SEVERITY.get(acc.status)
	if sev and acc.status != old_status:
		reason = f" Reason: {acc.disable_reason}." if acc.disable_reason else ""
		raise_alert("Account Status", sev, f"{acc.account_name} is {acc.status}", f"The platform reports this ad account as {acc.status}.{reason} Ads on it may not deliver.", ref=acc, dedupe_key=key)


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
	old_status = acc.status
	_apply_account(acc, provider.read_account(), last_structure_sync=now_datetime())
	_alert_on_account_status(acc, old_status)
	# Billing reading → today's Ads Billing Snapshot (+ headroom alerts). Never fails the job.
	from excom.excom_ads.billing import read_and_record

	read_and_record(acc, provider)
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
	if written:
		relink_snapshots(acc)
		frappe.db.commit()
		_relink_leads(acc)
	return {"records": written, "errors": errors}


def _relink_leads(acc) -> None:
	"""Leads that arrived before their campaign / ad set / ad mirror get linked now that it exists."""
	try:
		from excom.excom_ads.attribution import relink_pending

		relink_pending(provider=acc.provider)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title=f"Excom Ads: lead relink failed after structure sync of {acc.name}", message=frappe.get_traceback())


def _apply_account(acc, details: dict, **extra) -> None:
	details = {k: v for k, v in details.items() if not (k == "account_name" and not v)}
	# Number columns cannot hold "unknown"; billing_status says whether the amounts were reported.
	details.update({k: 0 for k, v in details.items() if v is None and k.startswith("reported_")})
	details.update(extra)
	if any(k.startswith("reported_") for k in details):
		details["billing_reported_at"] = now_datetime()
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
		for k in NAME_FIELDS:
			if values.get(k):
				values[k] = str(values[k])[:NAME_LENGTH]
		name = mirror_name(acc.name, values["external_id"])
		seen.add(name)
		if name in existing:
			# Brand is allocated per mirror by people (or rules); a sync never overwrites it.
			values.pop("brand", None)
		if doctype == "Ads Advertisement":
			_alert_on_ad_status(acc, name, values)
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


def _alert_on_ad_status(acc, name: str, values: dict) -> None:
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	key = f"ad_rejected:{name}"
	if values.get("status") == "Rejected":
		if frappe.db.get_value("Ads Advertisement", name, "status") != "Rejected":
			raise_alert("Ad Disapproved", "High", f"Ad rejected: {values.get('ad_name')}", f"{acc.account_name} · the platform rejected this ad. Check its review feedback in the ad manager.", scope={**_scope(acc)}, dedupe_key=key)
	elif frappe.db.exists("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}):
		resolve_alert(key, "Ad is no longer rejected")


NAME_FIELDS = ("campaign_name", "ad_group_name", "ad_name", "creative_name")
NAME_LENGTH = 500


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
	account_level_ok = False
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
		account_level_ok = account_level_ok or level == "Account"
	if account_level_ok or written:
		values = {"last_metrics_sync": now_datetime()}
		# The window counts as synced once account totals are in: totals, billing averages and the
		# backfill → settle hand-over depend on them. A failing lower level (ads, say) is reported on
		# the job and the account but must not wedge the account in endless backfills.
		if account_level_ok:
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
	campaign_brand = {c.name: c.brand for c in frappe.get_all("Ads Campaign", filters={"account": acc.name}, fields=["name", "brand"])}
	ad_groups = set(frappe.get_all("Ads Ad Group", filters={"account": acc.name}, pluck="name")) if level in ("Ad Group", "Ad") else set()
	ads = set(frappe.get_all("Ads Advertisement", filters={"account": acc.name}, pluck="name")) if level == "Ad" else set()
	company_currency = rates.to
	now = now_datetime()
	seen, reported_entities = set(), set()
	for r in rows:
		if not r.get("date") or not r.get("entity_external_id"):
			continue
		name = snapshot_name(acc.name, level, r["entity_external_id"], r["date"])
		seen.add(name)
		reported_entities.add(str(r["entity_external_id"]))
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
		# Links and brand are written on every pass, not only on insert: a row that arrived before its
		# campaign mirror (first backfill runs before the first structure sync) gets linked on re-pull,
		# and a row follows its campaign's brand allocation, falling back to the account's brand.
		if level != "Account":
			for field, ext, pool in (("campaign", r.get("campaign_external_id"), campaign_brand), ("ad_group", r.get("ad_group_external_id"), ad_groups), ("advertisement", r.get("ad_external_id"), ads)):
				n = mirror_name(acc.name, ext) if ext else None
				values[field] = n if n in pool else None
			values["brand"] = campaign_brand.get(values.get("campaign")) or acc.brand
		if name in existing:
			frappe.db.set_value(dt, name, values, update_modified=True)
			continue
		doc = frappe.get_doc({"doctype": dt, **_scope(acc), **values, "level": level, "date": r["date"], "entity_external_id": r["entity_external_id"], "breakdown_key": "none", "attribution_key": "default"})
		try:
			doc.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			# Another job for this account (Metrics and Settle can overlap) wrote it first: same key, update it.
			frappe.db.set_value(dt, name, values, update_modified=True)
	# A day missing from a re-pull for an entity the platform still reports had its results removed
	# (refunded or invalid clicks): zero it. An entity missing altogether may just be filtered out of
	# the report (deleted or archived objects), so its history is left untouched.
	gone = existing - seen
	if gone:
		for row in frappe.get_all(dt, filters={"name": ["in", list(gone)]}, fields=["name", "entity_external_id"]):
			if level == "Account" or str(row.entity_external_id) in reported_entities:
				frappe.db.set_value(dt, row.name, {**{f: 0 for f in SNAPSHOT_FIELDS}, "spend_company_currency": 0, "synced_at": now}, update_modified=True)
	return len(seen)


RELINK_DAYS = 400


def relink_snapshots(acc) -> None:
	"""Link snapshot rows to mirrors that appeared after them and re-apply each campaign's brand.

	Runs after every structure sync. One UPDATE … JOIN per level, limited to rows that need it."""
	since = add_days(getdate(), -RELINK_DAYS)
	p = {"acc": acc.name, "since": since, "acc_brand": acc.brand}
	frappe.db.sql(
		"""update `tabAds Metric Snapshot` s
		join `tabAds Campaign` c on c.account = s.account and c.external_id = s.entity_external_id
		set s.campaign = c.name, s.brand = coalesce(c.brand, %(acc_brand)s)
		where s.account = %(acc)s and s.level = 'Campaign' and s.date >= %(since)s
			and (s.campaign is null or not (s.brand <=> coalesce(c.brand, %(acc_brand)s)))""",
		p,
	)
	frappe.db.sql(
		"""update `tabAds Metric Snapshot` s
		join `tabAds Ad Group` g on g.account = s.account and g.external_id = s.entity_external_id
		left join `tabAds Campaign` c on c.name = g.campaign
		set s.ad_group = g.name, s.campaign = g.campaign, s.brand = coalesce(c.brand, %(acc_brand)s)
		where s.account = %(acc)s and s.level = 'Ad Group' and s.date >= %(since)s
			and (s.ad_group is null or not (s.campaign <=> g.campaign) or not (s.brand <=> coalesce(c.brand, %(acc_brand)s)))""",
		p,
	)
	frappe.db.sql(
		"""update `tabAds Metric Snapshot` s
		join `tabAds Advertisement` a on a.account = s.account and a.external_id = s.entity_external_id
		left join `tabAds Campaign` c on c.name = a.campaign
		set s.advertisement = a.name, s.ad_group = a.ad_group, s.campaign = a.campaign, s.brand = coalesce(c.brand, %(acc_brand)s)
		where s.account = %(acc)s and s.level = 'Ad' and s.date >= %(since)s
			and (s.advertisement is null or not (s.campaign <=> a.campaign) or not (s.brand <=> coalesce(c.brand, %(acc_brand)s)))""",
		p,
	)
