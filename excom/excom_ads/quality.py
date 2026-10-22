"""Data quality: do the numbers we hold agree with themselves, and are they fresh?

Spend Mismatch (nightly, acceptance tests 1 and 16)
  For every ad account and every day of the settle window (Ads Settings → settle_days, up to
  yesterday in the account's time zone), account-level spend is compared with the sum of its
  campaign-level spend, both in the account currency as the platform reported them. A day is a
  mismatch when the gap is above spend_mismatch_pct % of the account-level spend AND at least
  spend_mismatch_min_amount (tiny absolute gaps from rounding are ignored). A day with only one of
  the two levels is a mismatch too (one sync is missing). One alert per account lists the days; it
  is resolved when the window is clean again.

Data Stale (hourly)
  An enabled, connected ad account (not Manual) whose last metrics sync is older than
  data_stale_hours, or an enabled GA4 property whose last successful sync is older than
  ga4_stale_hours. Resolved on recovery. An account enabled less than the threshold ago is not
  stale yet.

`check_spend(account)` / `spend_report()` are pure reads and also feed the Overview's data-quality
note (`summary()`), inside the caller's scope.
"""

import frappe
from frappe.utils import add_days, add_to_date, cint, flt, get_datetime, getdate, now_datetime

from excom.excom_ads import permissions
from excom.excom_ads.settings import get_settings

SNAP = "Ads Metric Snapshot"
MAX_DAYS_LISTED = 10


def _thresholds() -> tuple[float, float]:
	s = get_settings()
	return flt(s.spend_mismatch_pct), flt(s.spend_mismatch_min_amount)


def is_mismatch(account_spend, campaign_spend, pct: float, min_amount: float) -> bool:
	"""None = that level has no row for the day. Both None → nothing to compare."""
	if account_spend is None and campaign_spend is None:
		return False
	a, c = flt(account_spend), flt(campaign_spend)
	gap = abs(a - c)
	if gap < max(min_amount, 0.005):
		return False
	if account_spend is None or campaign_spend is None:
		return True
	base = abs(a) or abs(c)  # the account-level figure is the reference
	return base > 0 and gap / base * 100 > pct


def spend_days(accounts: list[str], since, until, scope_sql: str = "") -> dict[str, list[dict]]:
	"""{account: [{date, account_spend, campaign_spend, gap, currency}]} for mismatched days only."""
	if not accounts:
		return {}
	pct, min_amount = _thresholds()
	extra = f" and {scope_sql}" if scope_sql else ""
	rows = frappe.db.sql(
		f"""select s.account, s.date, max(s.currency) as currency,
			sum(case when s.level = 'Account' then s.spend end) as account_spend,
			sum(case when s.level = 'Campaign' then s.spend end) as campaign_spend
		from `tab{SNAP}` s
		where s.account in %(accounts)s and s.level in ('Account', 'Campaign') and s.date between %(a)s and %(b)s{extra}
		group by s.account, s.date order by s.account, s.date""",
		{"accounts": tuple(accounts), "a": getdate(since), "b": getdate(until)},
		as_dict=True,
	)
	out: dict[str, list] = {}
	for r in rows:
		if is_mismatch(r.account_spend, r.campaign_spend, pct, min_amount):
			out.setdefault(r.account, []).append(
				{
					"date": str(r.date),
					"account_spend": None if r.account_spend is None else flt(r.account_spend, 2),
					"campaign_spend": None if r.campaign_spend is None else flt(r.campaign_spend, 2),
					"gap": flt(abs(flt(r.account_spend) - flt(r.campaign_spend)), 2),
					"currency": r.currency,
				}
			)
	return out


def _settle_window(acc) -> tuple:
	from excom.excom_ads.sync import account_today

	today = account_today(acc)
	return getdate(add_days(today, -cint(get_settings().settle_days))), getdate(add_days(today, -1))


def _fmt(d: dict) -> str:
	a = "no account row" if d["account_spend"] is None else f"account {d['account_spend']:,.2f}"
	c = "no campaign rows" if d["campaign_spend"] is None else f"campaigns {d['campaign_spend']:,.2f}"
	return f"{d['date']}: {a} vs {c} {d['currency'] or ''}".strip()


def check_spend(account: str) -> list[dict]:
	"""Raise or resolve the Spend Mismatch alert of one account. Returns the mismatched days."""
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	acc = frappe.get_doc("Ads Account", account)
	a, b = _settle_window(acc)
	days = spend_days([acc.name], a, b).get(acc.name, [])
	key = f"spend_mismatch:{acc.name}"
	if not days:
		resolve_alert(key, "Account and campaign spend agree again")
		return []
	pct, min_amount = _thresholds()
	listed = "\n".join(_fmt(d) for d in days[:MAX_DAYS_LISTED])
	more = f"\n… and {len(days) - MAX_DAYS_LISTED} more day(s)" if len(days) > MAX_DAYS_LISTED else ""
	raise_alert(
		"Spend Mismatch",
		"Medium",
		f"{acc.account_name}: account and campaign spend disagree on {len(days)} day(s)",
		f"Account-level spend differs from the sum of campaign spend by more than {pct:g}% (and {min_amount:g}) on:\n{listed}{more}\n"
		"A re-sync of the window usually fixes it; if it persists, a campaign is missing from the campaign-level pull.",
		ref=acc,
		dedupe_key=key,
	)
	return days


def run_spend_checks() -> dict:
	"""Nightly job: every account with snapshots in its settle window."""
	if not get_settings().enabled:
		return {"checked": 0, "mismatched": 0}
	accounts = frappe.get_all("Ads Account", filters={"status": ["!=", "Closed"]}, pluck="name")
	checked = mismatched = 0
	for name in accounts:
		try:
			days = check_spend(name)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom Ads: spend check failed for {name}", message=frappe.get_traceback())
			continue
		checked += 1
		mismatched += 1 if days else 0
		frappe.db.commit()
	return {"checked": checked, "mismatched": mismatched}


def stale_accounts(now=None, names: list | None = None) -> list[dict]:
	hours = cint(get_settings().data_stale_hours)
	if hours <= 0:
		return []
	now = now or now_datetime()
	cutoff = add_to_date(now, hours=-hours)
	f = {"sync_enabled": 1, "status": ["!=", "Closed"], "provider": ["!=", "Manual"]}
	if names is not None:
		f["name"] = ["in", list(names) or ["-"]]
	rows = frappe.get_all("Ads Account", filters=f, fields=["name", "account_name", "last_metrics_sync", "creation"])
	return [r for r in rows if (get_datetime(r.last_metrics_sync) < cutoff if r.last_metrics_sync else get_datetime(r.creation) < cutoff)]


def stale_properties(now=None, names: list | None = None) -> list[dict]:
	if not frappe.db.exists("DocType", "Ads Analytics Property"):
		return []
	hours = cint(get_settings().ga4_stale_hours)
	if hours <= 0:
		return []
	cutoff = add_to_date(now or now_datetime(), hours=-hours)
	f = {"sync_enabled": 1}
	if names is not None:
		f["name"] = ["in", list(names) or ["-"]]
	rows = frappe.get_all("Ads Analytics Property", filters=f, fields=["name", "property_name", "last_success_at", "creation"])
	return [r for r in rows if (get_datetime(r.last_success_at) < cutoff if r.last_success_at else get_datetime(r.creation) < cutoff)]


def run_stale_checks(now=None, names: list | None = None) -> dict:
	"""Hourly: raise Data Stale for stale sources, resolve the ones that recovered. `names` limits the
	check to those accounts / properties (None = all)."""
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	if not get_settings().enabled:
		return {"stale": 0, "resolved": 0}
	s = get_settings()
	stale = {f"data_stale:{r.name}": r for r in stale_accounts(now, names)}
	stale_props = {f"data_stale:{r.name}": r for r in stale_properties(now, names)}
	for key, r in stale.items():
		acc = frappe.get_doc("Ads Account", r.name)
		when = f"last metrics sync {r.last_metrics_sync}" if r.last_metrics_sync else "never synced metrics"
		raise_alert("Data Stale", "Medium", f"{acc.account_name}: ad figures are stale", f"Enabled ad account, {when} (threshold {s.data_stale_hours} h). Figures for it are incomplete until it syncs again; see its last error on the account.", ref=acc, dedupe_key=key)
	for key, r in stale_props.items():
		prop = frappe.get_doc("Ads Analytics Property", r.name)
		when = f"last successful sync {r.last_success_at}" if r.last_success_at else "never synced"
		raise_alert("Data Stale", "Medium", f"GA4 {prop.property_name}: website figures are stale", f"Enabled GA4 property, {when} (threshold {s.ga4_stale_hours} h).", ref=prop, dedupe_key=key)
	resolved = 0
	f = {"alert_type": "Data Stale", "status": ["!=", "Resolved"], "dedupe_key": ["like", "data_stale:%"]}
	if names is not None:
		f["dedupe_key"] = ["in", [f"data_stale:{n}" for n in names] or ["-"]]
	open_keys = frappe.get_all("Ads Alert", filters=f, pluck="dedupe_key")
	for key in set(open_keys) - set(stale) - set(stale_props):
		resolved += resolve_alert(key, "Syncing again")
	frappe.db.commit()
	return {"stale": len(stale) + len(stale_props), "resolved": resolved}


# ─── read API (Overview note) ─────────────────────────────────────────────────

def summary(filters: dict, from_date, to_date) -> dict | None:
	"""Mismatched days in the viewed range for the accounts the caller sees, plus stale sources.
	A brand-scoped user does not see account-level totals, so the comparison is not shown to them."""
	scope = permissions.get_scope()
	if scope is None:
		return None
	pct, min_amount = _thresholds()
	out = {"threshold_pct": pct, "min_amount": min_amount, "spend_mismatch": None, "stale_after_hours": cint(get_settings().data_stale_hours)}
	if scope.get("brand") is not None or filters.get("brand") or filters.get("campaign"):
		return out
	f = {k: v for k, v in filters.items() if k in ("company", "workspace", "provider") and v}
	if filters.get("account"):
		f["name"] = filters["account"]
	accs = frappe.get_list("Ads Account", filters=f, fields=["name", "account_name"], limit_page_length=0)
	names = {a.name: a.account_name for a in accs}
	scope_sql = permissions.query_conditions(SNAP)
	days = spend_days(sorted(names), from_date, to_date, scope_sql.replace(f"`tab{SNAP}`.", "s.") if scope_sql else "")
	out["spend_mismatch"] = [{"account": acc, "account_name": names.get(acc), "days": len(d), "worst": max(d, key=lambda x: x["gap"]), "dates": [x["date"] for x in d[:MAX_DAYS_LISTED]]} for acc, d in days.items()]
	return out
