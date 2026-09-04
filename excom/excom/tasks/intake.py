"""Scheduled intake: pull due sources (IndiaMART / TradeIndia), Meta reconciliation, payload purge (S7)."""

import frappe
from frappe.utils import add_days, add_to_date, now_datetime, get_datetime

FREQ_MINUTES = {"Every 5 Minutes": 5, "Every 15 Minutes": 15, "Hourly": 60, "Daily": 1440}


def pull_due_sources() -> None:
	"""Cron */5: run every enabled Pull/Both source whose frequency has elapsed."""
	now = now_datetime()
	for s in frappe.get_all("Excom Source", filters={"enabled": 1, "mode": ["in", ["Pull", "Both"]], "source_type": ["in", ["IndiaMART", "TradeIndia", "Meta Lead Ads"]]}, fields=["name", "source_type", "pull_frequency", "last_synced_at"]):
		mins = FREQ_MINUTES.get(s.pull_frequency, 15)
		if s.last_synced_at and (now - get_datetime(s.last_synced_at)).total_seconds() < mins * 60 - 30:
			continue
		frappe.enqueue("excom.excom.tasks.intake.pull_source", queue="short", source=s.name, job_name=f"excom-intake-{s.name}", deduplicate=True)


def pull_source(source: str) -> dict:
	src = frappe.get_doc("Excom Source", source)
	from excom.excom.intake import adapters

	fn = {"IndiaMART": adapters.indiamart.pull, "TradeIndia": adapters.tradeindia.pull, "Meta Lead Ads": adapters.meta.reconcile}.get(src.source_type)
	if not fn:
		return {"skipped": True}
	try:
		result = fn(src)
		src.db_set("last_synced_at", now_datetime(), update_modified=False)
		src.db_set("last_success_at", now_datetime(), update_modified=False)
		frappe.db.commit()
		return result
	except Exception:
		frappe.db.rollback()
		src.db_set("last_synced_at", now_datetime(), update_modified=False)  # advance so we don't hammer; watermark stays on last_success_at
		frappe.db.commit()
		frappe.log_error(title=f"Excom intake pull failed: {source}", message=frappe.get_traceback())
		return {"error": True}


def reconcile_meta_leads() -> None:
	for s in frappe.get_all("Excom Source", filters={"enabled": 1, "source_type": "Meta Lead Ads"}, pluck="name"):
		frappe.enqueue("excom.excom.tasks.intake.pull_source", queue="long", source=s)


def purge_old_payloads(days: int = 90) -> None:
	"""S7 / DPDP: raw payloads (buyer PII) purge at 90 days; dedupe keys stay forever."""
	cutoff = add_days(now_datetime(), -days)
	frappe.db.sql("UPDATE `tabExcom Source Log` SET raw_payload='', mapped_payload='' WHERE received_at < %s AND raw_payload <> ''", cutoff)
	frappe.db.commit()


def stale_source_alarm() -> list[str]:
	"""RES-001 R3: no successful pull in 3× frequency → error log (feeds token_monitor)."""
	out = []
	now = now_datetime()
	for s in frappe.get_all("Excom Source", filters={"enabled": 1, "mode": ["in", ["Pull", "Both"]]}, fields=["name", "pull_frequency", "last_success_at"]):
		mins = FREQ_MINUTES.get(s.pull_frequency, 15) * 3
		if not s.last_success_at or (now - get_datetime(s.last_success_at)).total_seconds() > mins * 60:
			out.append(s.name)
	if out:
		frappe.log_error(title="Excom intake: stale sources", message="\n".join(out))
	return out
