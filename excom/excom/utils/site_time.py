"""Epoch → site time zone, independent of the OS clock of the box running the worker."""

import datetime

import frappe
from frappe.utils import convert_utc_to_system_timezone


def epoch_to_site_time(epoch: int | float) -> str:
	"""Meta/Gmail give UTC epoch seconds. Return 'YYYY-MM-DD HH:MM:SS' in System Settings → Time Zone
	(e.g. Asia/Kolkata), so a server running on UTC no longer stores times 5½ hours early."""
	utc = datetime.datetime.fromtimestamp(float(epoch), tz=datetime.timezone.utc).replace(tzinfo=None)
	return convert_utc_to_system_timezone(utc).strftime("%Y-%m-%d %H:%M:%S")


def now_site_time() -> datetime.datetime:
	return frappe.utils.now_datetime()
