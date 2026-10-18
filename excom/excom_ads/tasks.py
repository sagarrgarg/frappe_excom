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
	rows = frappe.get_all("Ads Account", filters={"sync_enabled": 1, "status": ["not in", SKIP_STATUSES]}, fields=["name", "error_class", "metrics_synced_from"])
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
