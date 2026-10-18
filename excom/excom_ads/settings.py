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
}


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
		out[k] = type(default)(v) if isinstance(default, int) else v
	return out
