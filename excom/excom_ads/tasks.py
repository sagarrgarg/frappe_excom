"""Scheduler entry points. Each one only queues jobs; the work runs in sync.run_job.

On a site with no Ads Account these return after one cheap query.
"""

import frappe

from excom.excom_ads import sync
from excom.excom_ads.settings import get_settings

SKIP_STATUSES = ("Closed",)


def _accounts(skip_broken: bool = False) -> list[dict]:
	if not frappe.db.exists("DocType", "Ads Account") or not get_settings().enabled:
		return []
	# Manual accounts have no connector: their figures arrive through Ads Metric Import.
	rows = frappe.get_all("Ads Account", filters={"sync_enabled": 1, "status": ["not in", SKIP_STATUSES], "provider": ["!=", "Manual"]}, fields=["name", "error_class", "metrics_synced_from"])
	if skip_broken:
		# A revoked token or missing permission does not fix itself every 30 minutes. The hourly
		# structure job keeps checking and clears the error once the connection works again.
		rows = [r for r in rows if r.error_class not in ("Token", "Permission")]
	return rows


def enqueue_intraday():
	"""Every 30 minutes: today and the last couple of days. An account with no history gets its backfill first."""
	for r in _accounts(skip_broken=True):
		sync.enqueue(r.name, "Metrics" if r.metrics_synced_from else "Backfill")


def enqueue_structure():
	"""Hourly: account details, billing fields and the campaign / ad group / ad tree."""
	for r in _accounts():
		sync.enqueue(r.name, "Structure")


def enqueue_settle():
	"""Daily: re-pull the trailing settle window so late conversions and corrections land on the right day."""
	for r in _accounts(skip_broken=True):
		if r.metrics_synced_from:
			sync.enqueue(r.name, "Settle")


def purge_raw_payloads():
	"""Weekly: sync job rows older than 90 days are only noise."""
	if not frappe.db.exists("DocType", "Ads Sync Job"):
		return
	frappe.db.delete("Ads Sync Job", {"creation": ["<", frappe.utils.add_days(frappe.utils.now_datetime(), -90)]})


def wake_snoozed_alerts():
	"""Every 30 minutes: a snoozed alert whose time is up is open again and notifies again."""
	if not frappe.db.exists("DocType", "Ads Alert"):
		return
	from excom.excom_ads.alerts import _notify

	for name in frappe.get_all("Ads Alert", filters={"status": "Snoozed", "snoozed_until": ["<", frappe.utils.now_datetime()]}, pluck="name"):
		frappe.db.set_value("Ads Alert", name, {"status": "Open", "snoozed_until": None, "notified_severity": None})
		_notify(name)


def enqueue_relink():
	"""Daily backstop: link leads whose ad mirror arrived after them (structure syncs also do this)."""
	if not frappe.db.exists("DocType", "Ads Attribution Touch"):
		return
	if not frappe.db.exists("Ads Attribution Touch", {"link_status": ["in", ["Pending", "Partial"]]}):
		return
	from excom.excom_ads.attribution import enqueue_relink as _enqueue

	_enqueue()


def enqueue_budget_checks():
	"""Hourly: Overspend alerts for approved budget plans of the current month. Cheap no-op without plans."""
	if not get_settings().enabled:
		return
	from excom.excom_ads import budgets

	if not budgets.has_current_plans():
		return
	frappe.enqueue("excom.excom_ads.budgets.check_overspend", queue="long", job_id=f"excom_ads_budgets::{frappe.local.site}", deduplicate=True)


# ─── GA4 web analytics (analytics.py) and data quality (quality.py) ───────────

def _properties(skip_broken: bool = False) -> list[dict]:
	if not frappe.db.exists("DocType", "Ads Analytics Property") or not get_settings().enabled:
		return []
	rows = frappe.get_all("Ads Analytics Property", filters={"sync_enabled": 1}, fields=["name", "error_class", "synced_from", "time_zone"])
	if skip_broken:
		# Token / permission problems need a person; the daily run keeps trying and clears them.
		rows = [r for r in rows if r.error_class not in ("Token", "Permission")]
	return rows


def enqueue_analytics():
	"""Hourly: today and the last couple of days of each GA4 property; a new property gets its backfill first."""
	from excom.excom_ads import analytics

	for r in _properties(skip_broken=True):
		analytics.enqueue(r.name, "Metrics" if r.synced_from else "Backfill")


def enqueue_analytics_settle():
	"""Daily: re-pull the GA4 settle window, and resume a backfill that stopped early (quota)."""
	from excom.excom_ads import analytics

	for r in _properties():
		if not r.synced_from:
			analytics.enqueue(r.name, "Backfill")
			continue
		analytics.enqueue(r.name, "Settle")
		if analytics.backfill_incomplete(r):
			analytics.enqueue(r.name, "Backfill")


def enqueue_spend_checks():
	"""Daily: account-level vs campaign-level spend for every day of the settle window (Spend Mismatch)."""
	if not get_settings().enabled or not frappe.db.exists("DocType", "Ads Metric Snapshot"):
		return
	if not frappe.db.sql("select 1 from `tabAds Metric Snapshot` limit 1"):
		return
	frappe.enqueue("excom.excom_ads.quality.run_spend_checks", queue="long", job_id=f"excom_ads_quality::{frappe.local.site}::spend", deduplicate=True)


def check_stale_data():
	"""Hourly: Data Stale alerts for ad accounts and GA4 properties that stopped syncing (one cheap read)."""
	if not get_settings().enabled or not frappe.db.exists("DocType", "Ads Account"):
		return
	has_props = frappe.db.exists("DocType", "Ads Analytics Property") and frappe.db.exists("Ads Analytics Property", {"sync_enabled": 1})
	if not (frappe.db.exists("Ads Account", {"sync_enabled": 1}) or has_props or frappe.db.exists("Ads Alert", {"alert_type": "Data Stale", "status": ["!=", "Resolved"]})):
		return
	frappe.enqueue("excom.excom_ads.quality.run_stale_checks", queue="short", job_id=f"excom_ads_quality::{frappe.local.site}::stale", deduplicate=True)
