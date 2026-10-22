"""Delete old rows of a log-type doctype, for Frappe's Log Settings.

A doctype with a static `clear_old_logs(days)` on its controller is listed
in Log Settings (Desk > Log Settings), where its retention can be set, and
cleared by Frappe's daily `run_log_clean_up`; `default_log_clearing_doctypes`
in hooks.py gives the starting retention.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, now_datetime


def delete_older_than(doctype: str, days: int, *, where: dict | None = None, children: tuple[str, ...] = ()) -> int:
	"""Delete `doctype` rows created more than `days` ago (and only those
	matching `where`), with their child-table rows. Returns how many."""
	days = max(cint(days), 1)
	filters = {"creation": ["<", add_days(now_datetime(), -days)], **(where or {})}
	names = frappe.get_all(doctype, filters=filters, pluck="name", limit=50000)
	for start in range(0, len(names), 1000):
		chunk = names[start : start + 1000]
		for child in children:
			frappe.db.delete(child, {"parent": ["in", chunk], "parenttype": doctype})
		frappe.db.delete(doctype, {"name": ["in", chunk]})
	return len(names)
