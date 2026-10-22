"""Ads Settings with defaults, so a site that never opened the settings page still behaves sensibly."""

import frappe

DEFAULTS = {
	"enabled": 1,
	"meta_api_version": "v26.0",
	"settle_days": 28,
	"today_lookback_days": 2,
	"backfill_days": 90,
	"sync_ad_level_metrics": 1,
	"max_pages_per_call": 200,
	"alert_whatsapp_template": "",
	"ctwa_create_lead": 1,
	# Billing and budgets (billing.py, budgets.py)
	"low_headroom_days": 3,
	"cap_exhaustion_alerts": 1,
	"billing_stale_hours": 6,
	"overspend_threshold_pct": 15,
	# Ad bills → Purchase Invoices (bills.py)
	"bills_auto_submit": 0,
	"bill_service_item": "",
	"bill_naming_series": "ADB-.YY.-.#####",
	"bill_return_naming_series": "ADR-.YY.-.#####",
	"gstr2b_missing_after_days": 45,
	"tds_certificate_alert_days": 15,
	# GA4 web analytics (analytics.py)
	"ga4_lookback_days": 2,
	"ga4_settle_days": 14,
	"ga4_backfill_days": 480,
	"ga4_stale_hours": 36,
	# Data quality (quality.py). spend_mismatch_pct is a Float: read it with flt().
	"spend_mismatch_pct": 0.5,
	"spend_mismatch_min_amount": 1,
	"data_stale_hours": 6,
	# Scheduled reports (reports.py): approved WhatsApp template; empty = email only.
	"report_whatsapp_template": "",
}


# Counts where 0 would break a sync or an alert (no pages fetched, an empty window, alerting at once):
# the form refuses 0 and a stored 0 reads as the default.
POSITIVE = (
	"settle_days",
	"backfill_days",
	"max_pages_per_call",
	"billing_stale_hours",
	"gstr2b_missing_after_days",
	"tds_certificate_alert_days",
	"ga4_settle_days",
	"ga4_backfill_days",
	"ga4_stale_hours",
	"data_stale_hours",
)


def get_settings() -> frappe._dict:
	out = frappe._dict(DEFAULTS)
	if not frappe.db.exists("DocType", "Ads Settings"):
		return out
	saved = frappe.db.get_singles_dict("Ads Settings")
	if not saved:
		return out
	for k, default in DEFAULTS.items():
		v = saved.get(k)
		if v in (None, ""):
			continue
		v = type(default)(v) if isinstance(default, int) else v
		if k in POSITIVE and v < 1:
			continue
		out[k] = v
	return out
