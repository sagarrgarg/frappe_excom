"""Scheduler entry points for ad bills. They only enqueue; the work runs in bills.run_source /
bills.run_checks on the long queue, deduplicated per source. On a site without bill sources they
return after one cheap query."""

import frappe
from frappe.utils import getdate

from excom.excom_ads.settings import get_settings


def enqueue_bill_fetch(today=None) -> list[str]:
	"""Daily. Daily sources every day; Monthly sources on the 1st–7th, when the month's invoices are issued."""
	if not frappe.db.exists("DocType", "Ads Bill Source") or not get_settings().enabled:
		return []
	day = getdate(today).day
	queued = []
	for s in frappe.get_all("Ads Bill Source", filters={"enabled": 1, "fetch_schedule": ["in", ["Daily", "Monthly"]]}, fields=["name", "fetch_schedule"]):
		if s.fetch_schedule == "Monthly" and day > 7:
			continue
		from excom.excom_ads.bills import enqueue_source

		enqueue_source(s.name)
		queued.append(s.name)
	return queued


def enqueue_bill_checks() -> bool:
	"""Daily: GSTR-2B match status (from India Compliance's records) and TDS certificate reminders."""
	if not frappe.db.exists("DocType", "Ads Bill Document") or not frappe.db.exists("Ads Bill Document", {"status": "Submitted"}):
		return False
	frappe.enqueue("excom.excom_ads.bills.run_checks", queue="long", job_id=f"excom_ads_bill_checks::{frappe.local.site}", deduplicate=True)
	return True
