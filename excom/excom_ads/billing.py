"""Ad account billing: what each platform really reports, what Excom calculates from it, and our
own top-up ledger for prepaid accounts.

Neither Meta nor Google lets an API read a prepaid balance. Meta's `balance` is the bill amount due;
Google exposes account budgets and billing setups only for accounts on monthly invoicing. So:

  reported_*   exactly what the platform returned (Ads Billing Snapshot, one row per account per
               account-local day; the hourly Structure sync updates it in place)
  calc_*       headroom (cap − spent, or adjusted limit − served), 7-day average daily spend from
               account-level Ads Metric Snapshot rows, days of headroom left, projected exhaustion
  ledger       top-ups − refunds ± adjustments − platform spend since the first top-up, from Ads
               Billing Record. Always labelled "estimated from our ledger".

Every figure carries an availability status. Number columns cannot hold NULL, so a value is only
returned (and shown) when its status is Available or Stale; anything else is None, never 0.
"""

import json

import frappe
from frappe.utils import add_days, cint, date_diff, flt, get_datetime, get_last_day, getdate, now_datetime

from excom.excom_ads import permissions

SNAP = "Ads Billing Snapshot"
RECORD = "Ads Billing Record"
METRICS = "Ads Metric Snapshot"
LEDGER_LABEL = "Estimated from our ledger"
AVG_WINDOW_DAYS = 7
MAX_DAYS_LEFT = 3650

# Availability of one figure.
NOT_READ = "Not Read"
AVAILABLE = "Available"
NOT_SET = "Not Set"  # the platform reports it, the account has none (no spend cap, no active budget)
UNSUPPORTED = "Unsupported"  # the platform does not expose it for this account
PERMISSION_MISSING = "Permission Missing"
NO_DATA = "No Data"  # nothing to read or compute from (field not returned, no synced results)
STALE = "Stale"  # an older value: the latest read failed, or the reading is older than the limit
ERROR = "Error"
HAS_VALUE = (AVAILABLE, STALE)
CARRY = (AVAILABLE, NOT_SET, STALE)

# availability field -> the value fields it covers. A figure whose read failed keeps its last
# good value from an earlier reading, marked Stale.
GROUPS = {
	"amount_spent_avail": ("reported_amount_spent",),
	"spend_cap_avail": ("reported_spend_cap",),
	"amount_due_avail": ("reported_amount_due",),
	"prepay_avail": ("reported_is_prepay",),
	"funding_avail": ("reported_funding_type",),
	"payment_model_avail": ("payment_model",),
	"billing_setup_avail": ("reported_billing_setup_status",),
	"account_budget_avail": (
		"reported_budget_id",
		"reported_budget_name",
		"reported_budget_status",
		"reported_budget_start",
		"reported_budget_end",
		"reported_approved_limit",
		"reported_adjusted_limit",
		"reported_amount_served",
		"reported_total_adjustments",
	),
}
NUMERIC = {
	"reported_amount_spent",
	"reported_spend_cap",
	"reported_amount_due",
	"reported_is_prepay",
	"reported_approved_limit",
	"reported_adjusted_limit",
	"reported_amount_served",
	"reported_total_adjustments",
	"calc_headroom",
	"calc_avg_daily_spend",
	"calc_avg_days",
	"calc_days_left",
}


# ─── writing a reading (Structure sync) ───────────────────────────────────────

def read_and_record(acc, provider) -> dict | None:
	"""Called by sync_structure. Never raises (except token errors, which the sync handles): billing
	must not fail the structure job. Returns the stored values, or None when not supported."""
	from excom.excom_ads.providers.base import TOKEN, AdsProviderError

	capability = getattr(provider, "capability", None)
	if not callable(capability) or not capability("read_billing").supported:
		return None  # this provider does not read billing: nothing to record
	try:
		reading = provider.read_billing()
	except AdsProviderError as e:
		if e.error_class == TOKEN:
			raise
		reading = _failed_reading(e, PERMISSION_MISSING if e.error_class == "Permission" else ERROR)
	except Exception as e:
		frappe.log_error(title=f"Excom Ads: billing read failed for {acc.name}", message=frappe.get_traceback())
		reading = _failed_reading(e, ERROR)
	savepoint = "excom_ads_billing"
	frappe.db.savepoint(savepoint)
	try:
		values = record_reading(acc, reading)
		frappe.db.commit()
		return values
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Excom Ads: could not store billing reading for {acc.name}", message=frappe.get_traceback())
		return None


def _failed_reading(err, status: str) -> dict:
	return {**{a: status for a in GROUPS}, "headroom": {"avail": status}, "errors": [err], "raw": {"error": str(err)[:500]}}


def record_reading(acc, reading: dict) -> dict:
	"""Upsert today's Ads Billing Snapshot from a provider reading, keep Ads Account in step, alert."""
	from excom.excom_ads.naming import billing_snapshot_name
	from excom.excom_ads.sync import account_today

	now = now_datetime()
	today = account_today(acc)
	name = billing_snapshot_name(acc.name, today)
	existing = frappe.db.get_value(SNAP, name, "*", as_dict=True)
	base = existing or _latest(acc.name)

	values = {"currency": reading.get("currency") or acc.currency}
	fresh = False
	for avail, fields in GROUPS.items():
		# A figure the provider does not return at all is one this platform does not expose.
		status = reading.get(avail) or UNSUPPORTED
		if status == ERROR and base and base.get(avail) in CARRY:
			values.update({f: base.get(f) for f in fields})
			values[avail] = STALE if base.get(avail) in HAS_VALUE else base.get(avail)
		else:
			values.update({f: reading.get(f) for f in fields})
			values[avail] = status
			fresh = fresh or (bool(reading.get(avail)) and status != ERROR)
	values["payment_model"] = values.get("payment_model") or "Unknown"

	h = reading.get("headroom") or {}
	hs = h.get("avail") or UNSUPPORTED
	values["calc_headroom_basis"] = h.get("basis")
	limit = used = None
	if hs == AVAILABLE and h.get("limit") is not None:
		limit, used = flt(h["limit"]), flt(h.get("used"))
		values["calc_headroom"], values["headroom_avail"] = limit - used, AVAILABLE
	elif hs == ERROR and base and base.get("headroom_avail") in HAS_VALUE:
		values["calc_headroom"], values["headroom_avail"] = base.get("calc_headroom"), STALE
		values["calc_headroom_basis"] = base.get("calc_headroom_basis")
	else:
		values["calc_headroom"], values["headroom_avail"] = None, (NO_DATA if hs == AVAILABLE else hs)

	values.update(calculate(acc, values, today))

	errors = reading.get("errors") or []
	values.update(
		{
			"account": acc.name,
			"snapshot_date": today,
			"provider": acc.provider,
			"company": acc.company,
			"workspace": acc.workspace,
			"brand": acc.brand,
			"read_at": now,
			"reported_as_of": now if fresh else (base.get("reported_as_of") if base else None),
			"read_status": ("Partial" if fresh else "Error") if errors else "OK",
			"last_error": "; ".join(str(e) for e in errors)[:1000] or None,
			"raw_json": json.dumps(reading.get("raw") or {}, sort_keys=True, default=str),
		}
	)
	row = {k: (0 if v is None and k in NUMERIC else v) for k, v in values.items()}
	if existing:
		# One row per account per day: an unchanged hourly reading only counts the read.
		changed = any(_differs(existing.get(k), v) for k, v in row.items() if k not in ("read_at", "reported_as_of"))
		row["read_count"] = cint(existing.read_count) + 1
		frappe.db.set_value(SNAP, name, row, update_modified=changed)
	else:
		frappe.get_doc({"doctype": SNAP, **row, "read_count": 1}).insert(ignore_permissions=True)

	_sync_account(acc, values, limit, used)
	ledger = ledger_estimate(acc, values.get("calc_avg_daily_spend") if values.get("avg_spend_avail") == AVAILABLE else None)
	check_alerts(acc, values, today, ledger)
	return values


def _latest(account: str):
	rows = frappe.get_all(SNAP, filters={"account": account}, fields=["*"], order_by="snapshot_date desc", limit=1)
	return rows[0] if rows else None


def _differs(cur, new) -> bool:
	if isinstance(cur, (int, float)) or isinstance(new, (int, float)):
		return round(flt(cur), 6) != round(flt(new), 6)
	return str(cur or "") != str(new or "")


def calculate(acc, v: dict, today) -> dict:
	"""Average daily spend, days of headroom left and projected exhaustion date."""
	avg, days, avg_note = average_daily_spend(acc, today)
	out = {
		"calc_avg_daily_spend": avg,
		"avg_spend_avail": AVAILABLE if avg is not None else NO_DATA,
		"calc_avg_days": days,
		"calc_days_left": None,
		"calc_exhaustion_date": None,
	}
	notes = [avg_note] if avg_note else []
	hs = v.get("headroom_avail")
	if hs not in HAS_VALUE:
		out["days_left_avail"] = out["exhaustion_avail"] = hs
	elif avg is None:
		out["days_left_avail"] = out["exhaustion_avail"] = NO_DATA
	elif flt(v.get("calc_headroom")) <= 0:
		out.update({"calc_days_left": 0.0, "calc_exhaustion_date": today, "days_left_avail": hs, "exhaustion_avail": hs})
		notes.append("The cap is reached: the platform stops delivery.")
	elif avg <= 0:
		out["days_left_avail"] = out["exhaustion_avail"] = NO_DATA
		notes.append(f"No spend in the last {days} days, so the headroom is not being used up.")
	else:
		left = flt(v.get("calc_headroom")) / avg
		out.update({"calc_days_left": round(left, 2), "days_left_avail": hs})
		if left > MAX_DAYS_LEFT:
			out["exhaustion_avail"] = NOT_SET
			notes.append("At the current pace the headroom lasts more than ten years.")
		else:
			out.update({"calc_exhaustion_date": add_days(today, int(left)), "exhaustion_avail": hs})
	out["calc_note"] = " ".join(notes) or None
	return out


def average_daily_spend(acc, today, window: int = AVG_WINDOW_DAYS):
	"""(average, days averaged, note) over the last `window` complete days, in the account currency.

	Platforms send no row for a day without delivery, so a day inside the synced range with no
	account-level row is a zero-spend day. Days outside the synced range are not counted."""
	f, u = acc.get("metrics_synced_from"), acc.get("metrics_synced_until")
	if not f or not u:
		return None, 0, "No daily results synced for this account yet."
	start = max(add_days(today, -window), getdate(f))
	end = min(add_days(today, -1), getdate(u))
	if end < start:
		return None, 0, f"No daily results synced for the last {window} days."
	days = date_diff(end, start) + 1
	total = frappe.db.sql(f"select coalesce(sum(spend), 0) from `tab{METRICS}` where account = %s and level = 'Account' and date between %s and %s", (acc.name, start, end))[0][0]
	note = f"Average over {days} synced day{'s' if days != 1 else ''} only." if days < window else None
	return round(flt(total) / days, 6), days, note


def _sync_account(acc, v: dict, limit, used) -> None:
	"""Ads Account keeps the latest platform figures (the Desk form and older readers use them)."""
	reported = [v.get(a) for a in GROUPS]
	values = {"billing_reported_at": now_datetime(), "can_billing": 1 if any(s in CARRY for s in reported) else 0}
	if v.get("amount_spent_avail") == AVAILABLE or limit is not None:
		values["billing_status"] = "Reported"
		values["reported_amount_spent"] = flt(v.get("reported_amount_spent")) if v.get("amount_spent_avail") == AVAILABLE else flt(used)
		values["reported_spend_cap"] = flt(limit) if limit is not None else 0
		values["has_spend_cap"] = 1 if limit is not None else 0
		values["reported_amount_due"] = flt(v.get("reported_amount_due")) if v.get("amount_due_avail") == AVAILABLE else 0
	elif v.get("headroom_avail") == STALE or v.get("amount_spent_avail") == STALE:
		values["billing_status"] = "Error"
	else:
		values.update({"reported_amount_spent": 0, "reported_spend_cap": 0, "has_spend_cap": 0, "reported_amount_due": 0})
		values["billing_status"] = "Permission Missing" if PERMISSION_MISSING in reported else "Error" if ERROR in reported else "Not Available"
	if v.get("payment_model_avail") == AVAILABLE and v.get("payment_model"):
		values["payment_model"] = v["payment_model"]
	frappe.db.set_value("Ads Account", acc.name, values, update_modified=False)


# ─── ledger ───────────────────────────────────────────────────────────────────

def ledger_estimate(acc, avg_daily_spend=None) -> dict:
	"""Prepaid balance estimated from our own records: top-ups − refunds ± adjustments − platform
	spend (account-level snapshots) since the first record. Never a platform figure."""
	out = {"label": LEDGER_LABEL, "avail": NO_DATA, "balance": None, "topups": None, "refunds": None, "adjustments": None, "spend_since": None, "since": None, "records": 0, "days_left": None, "spend_through": None, "warning": None}
	rows = frappe.db.sql(f"select record_type, sum(amount) as amount, min(posting_date) as first, count(*) as n from `tab{RECORD}` where account = %s group by record_type", acc.name, as_dict=True)
	if not rows:
		return out
	by = {r.record_type: flt(r.amount) for r in rows}
	since = min(getdate(r.first) for r in rows)
	out.update({"topups": by.get("Top-up", 0.0), "refunds": by.get("Refund", 0.0), "adjustments": by.get("Adjustment", 0.0), "since": str(since), "records": sum(cint(r.n) for r in rows)})
	f, u = acc.get("metrics_synced_from"), acc.get("metrics_synced_until")
	if not f or not u:
		out["warning"] = "No daily results synced yet, so spend since the first top-up is unknown."
		return out
	spend = frappe.db.sql(f"select coalesce(sum(spend), 0) from `tab{METRICS}` where account = %s and level = 'Account' and date >= %s", (acc.name, since))[0][0]
	balance = out["topups"] - out["refunds"] + out["adjustments"] - flt(spend)
	out.update({"avail": AVAILABLE, "balance": round(balance, 6), "spend_since": round(flt(spend), 6), "spend_through": str(getdate(u))})
	if getdate(f) > since:
		out["warning"] = f"Daily results are synced only from {getdate(f)}; spend between {since} and then is missing, so this estimate is too high."
	if avg_daily_spend and flt(avg_daily_spend) > 0:
		out["days_left"] = round(max(balance, 0) / flt(avg_daily_spend), 2)
	return out


# ─── alerts ───────────────────────────────────────────────────────────────────

def low_headroom_key(account: str) -> str:
	return f"low_headroom:{account}"


def cap_exhaustion_key(account: str) -> str:
	return f"cap_exhaustion:{account}"


def ledger_low_key(account: str) -> str:
	return f"ledger_low:{account}"


def check_alerts(acc, v: dict, today, ledger: dict | None = None) -> None:
	from excom.excom_ads.alerts import raise_alert, resolve_alert
	from excom.excom_ads.settings import get_settings

	s = get_settings()
	threshold = flt(s.low_headroom_days)
	cur = v.get("currency") or acc.currency
	name = acc.account_name or acc.name
	status, left = v.get("days_left_avail"), v.get("calc_days_left")
	# Unknown is not "cleared": a Stale / Error / Permission Missing reading leaves an open alert open.
	cleared = status in (NOT_SET, UNSUPPORTED) or (status == AVAILABLE and flt(left) >= threshold) or (status == NO_DATA and v.get("headroom_avail") in HAS_VALUE and flt(v.get("calc_headroom")) > 0 and v.get("avg_spend_avail") == AVAILABLE)
	low = status == AVAILABLE and flt(left) < threshold
	if low:
		headroom = flt(v.get("calc_headroom"))
		sev = "Critical" if headroom <= 0 else "High" if flt(left) < 1 else "Medium"
		what = "has reached its spend cap" if headroom <= 0 else f"has about {flt(left):.1f} days of headroom left"
		raise_alert(
			"Low Headroom",
			sev,
			f"{name} {what}",
			f"{v.get('calc_headroom_basis') or 'Headroom'}: {cur} {headroom:,.2f}. Average daily spend over the last {v.get('calc_avg_days')} days: {cur} {flt(v.get('calc_avg_daily_spend')):,.2f}. Projected to run out on {v.get('calc_exhaustion_date')}. Raise the cap or add funds before delivery stops.",
			ref=acc,
			dedupe_key=low_headroom_key(acc.name),
		)
	elif cleared:
		resolve_alert(low_headroom_key(acc.name), "Headroom is above the threshold again")

	exhaust = v.get("calc_exhaustion_date")
	before_month_end = status == AVAILABLE and exhaust and getdate(exhaust) <= get_last_day(today)
	if cint(s.cap_exhaustion_alerts) and before_month_end and not low:
		raise_alert(
			"Low Headroom",
			"Medium",
			f"{name}: cap runs out on {getdate(exhaust)}, before month end",
			f"At the last {v.get('calc_avg_days')} days' average spend ({cur} {flt(v.get('calc_avg_daily_spend')):,.2f}/day) the remaining headroom of {cur} {flt(v.get('calc_headroom')):,.2f} lasts until {getdate(exhaust)}.",
			ref=acc,
			dedupe_key=cap_exhaustion_key(acc.name),
		)
	elif low or cleared or not cint(s.cap_exhaustion_alerts) or (status == AVAILABLE and not before_month_end):
		resolve_alert(cap_exhaustion_key(acc.name), "Cap no longer projected to run out this month")

	if ledger and ledger.get("avail") == AVAILABLE and ledger.get("days_left") is not None:
		if flt(ledger["days_left"]) < threshold:
			raise_alert(
				"Low Headroom",
				"High" if flt(ledger["balance"]) <= 0 else "Medium",
				f"{name}: prepaid balance low ({LEDGER_LABEL.lower()})",
				f"{LEDGER_LABEL}: {cur} {flt(ledger['balance']):,.2f} left (top-ups since {ledger['since']} minus platform spend through {ledger['spend_through']}), about {flt(ledger['days_left']):.1f} days at the recent average. The platform does not report a prepaid balance: check it in the ad manager and record new top-ups in Ads Billing Record.",
				ref=acc,
				dedupe_key=ledger_low_key(acc.name),
			)
		else:
			resolve_alert(ledger_low_key(acc.name), "Estimated prepaid balance is above the threshold again")
	elif ledger and ledger.get("avail") == AVAILABLE and flt(ledger.get("balance")) > 0:
		resolve_alert(ledger_low_key(acc.name), "No recent spend")


def on_record_change(doc, method=None) -> None:
	"""A new or edited top-up moves the ledger estimate: re-check the ledger alert at once."""
	acc = frappe.get_doc("Ads Account", doc.account)
	latest = _latest(acc.name)
	avg = latest.calc_avg_daily_spend if latest and latest.avg_spend_avail == AVAILABLE else None
	ledger = ledger_estimate(acc, avg)
	from excom.excom_ads.alerts import resolve_alert

	if ledger["avail"] != AVAILABLE:
		resolve_alert(ledger_low_key(acc.name), "No ledger records")
		return
	if latest:
		check_alerts(acc, {**latest, "days_left_avail": None}, getdate(latest.snapshot_date), ledger)


# ─── reading for the screens ──────────────────────────────────────────────────

def _latest_many(accounts: list[str]) -> dict:
	if not accounts:
		return {}
	scope_sql = permissions.query_conditions(SNAP)
	where = f" and {scope_sql.replace(f'`tab{SNAP}`.', 's.')}" if scope_sql else ""
	rows = frappe.db.sql(
		f"""select s.* from `tab{SNAP}` s
		join (select account, max(snapshot_date) as d from `tab{SNAP}` where account in %(accounts)s group by account) m
			on m.account = s.account and m.d = s.snapshot_date
		where 1=1{where}""",
		{"accounts": tuple(accounts)},
		as_dict=True,
	)
	return {r.account: r for r in rows}


def _fig(value, status):
	return {"value": value if status in HAS_VALUE else None, "status": status or NOT_READ}


def present(acc, snap, stale_hours: int) -> dict:
	"""One account's billing for the API. A value is None unless its status is Available or Stale."""
	row = {
		"account": acc.name,
		"account_name": acc.account_name,
		"provider": acc.provider,
		"currency": (snap.currency if snap else None) or acc.currency,
		"payment_model": acc.payment_model,
		"billing_status": acc.billing_status,
	}
	if not snap:
		# Never read since billing snapshots exist: fall back to what the account form holds.
		reported = acc.billing_status == "Reported"
		row.update(
			{
				"as_of": acc.billing_reported_at,
				"read_at": None,
				"read_status": None,
				"last_error": None,
				"stale": False,
				"reported": None,
				"calculated": None,
				"amount_spent": flt(acc.reported_amount_spent) if reported else None,
				"spend_cap": flt(acc.reported_spend_cap) if reported and acc.has_spend_cap else None,
				"headroom": (flt(acc.reported_spend_cap) - flt(acc.reported_amount_spent)) if reported and acc.has_spend_cap else None,
				"amount_due": flt(acc.reported_amount_due) if reported and acc.provider == "Meta" else None,
			}
		)
		return row
	as_of = get_datetime(snap.reported_as_of) if snap.reported_as_of else None
	stale = not as_of or (now_datetime() - as_of).total_seconds() > cint(stale_hours) * 3600
	st = lambda s: STALE if stale and s == AVAILABLE else (s or NOT_READ)
	budget_status = st(snap.account_budget_avail)
	unlimited = budget_status in HAS_VALUE and snap.headroom_avail == NOT_SET
	reported = {
		"amount_spent": _fig(flt(snap.reported_amount_spent), st(snap.amount_spent_avail)),
		"spend_cap": _fig(flt(snap.reported_spend_cap), st(snap.spend_cap_avail)),
		"amount_due": _fig(flt(snap.reported_amount_due), st(snap.amount_due_avail)),
		"is_prepay": _fig(bool(snap.reported_is_prepay), st(snap.prepay_avail)),
		"funding_type": _fig(snap.reported_funding_type, st(snap.funding_avail)),
		"payment_model": _fig(snap.payment_model, st(snap.payment_model_avail)),
		"billing_setup_status": _fig(snap.reported_billing_setup_status, st(snap.billing_setup_avail)),
		"account_budget": {
			"status": budget_status,
			"value": {
				"id": snap.reported_budget_id,
				"name": snap.reported_budget_name,
				"budget_status": snap.reported_budget_status,
				"start": snap.reported_budget_start,
				"end": snap.reported_budget_end,
				"unlimited": unlimited,
				"approved_limit": None if unlimited else flt(snap.reported_approved_limit),
				"adjusted_limit": None if unlimited else flt(snap.reported_adjusted_limit),
				"amount_served": flt(snap.reported_amount_served),
				"total_adjustments": flt(snap.reported_total_adjustments),
			}
			if budget_status in HAS_VALUE
			else None,
		},
	}
	calculated = {
		"headroom": {**_fig(flt(snap.calc_headroom), st(snap.headroom_avail)), "basis": snap.calc_headroom_basis},
		"avg_daily_spend": {**_fig(flt(snap.calc_avg_daily_spend), st(snap.avg_spend_avail)), "days": cint(snap.calc_avg_days)},
		"days_left": _fig(flt(snap.calc_days_left), st(snap.days_left_avail)),
		"exhaustion_date": _fig(str(snap.calc_exhaustion_date) if snap.calc_exhaustion_date else None, st(snap.exhaustion_avail)),
		"note": snap.calc_note,
	}
	is_google = acc.provider == "Google"
	budget = reported["account_budget"]["value"] or {}
	row.update(
		{
			"as_of": snap.reported_as_of,
			"snapshot": snap.name,
			"snapshot_date": str(snap.snapshot_date),
			"read_at": snap.read_at,
			"read_status": snap.read_status,
			"last_error": snap.last_error,
			"stale": stale,
			"reported": reported,
			"calculated": calculated,
			"payment_model": snap.payment_model if snap.payment_model_avail in HAS_VALUE else acc.payment_model,
			# Flat figures for the overview table; Google's are its account budget's.
			"amount_spent": budget.get("amount_served") if is_google else reported["amount_spent"]["value"],
			"spend_cap": (budget.get("adjusted_limit") or budget.get("approved_limit")) if is_google else reported["spend_cap"]["value"],
			"headroom": calculated["headroom"]["value"],
			"amount_due": None if is_google else reported["amount_due"]["value"],
		}
	)
	return row


ACCOUNT_FIELDS = ["name", "account_name", "provider", "currency", "payment_model", "billing_status", "billing_reported_at", "reported_amount_spent", "reported_spend_cap", "has_spend_cap", "reported_amount_due", "metrics_synced_from", "metrics_synced_until"]


def summary(filters: dict | None = None, with_ledger: bool = True) -> list[dict]:
	"""Per account in the caller's scope: reported, calculated and ledger figures. Needs view_finance."""
	from excom.excom_ads.dashboard import _accounts_in_scope
	from excom.excom_ads.settings import get_settings

	permissions.require("view_finance")
	# Scope from get_list; the billing columns are permlevel 1 on Ads Account (hidden from a marketing
	# user's get_list), and the view_finance right checked above is what grants them here.
	names = [a.name for a in _accounts_in_scope(filters or {}, ["name"])]
	by_name = {a.name: a for a in frappe.get_all("Ads Account", filters={"name": ["in", names or ["-"]]}, fields=ACCOUNT_FIELDS)}
	accs = [by_name[n] for n in names if n in by_name]
	latest = _latest_many([a.name for a in accs])
	stale_hours = get_settings().billing_stale_hours
	with_records = set(frappe.get_all(RECORD, filters={"account": ["in", [a.name for a in accs] or ["-"]]}, pluck="account", distinct=True)) if with_ledger else set()
	out = []
	for a in accs:
		row = present(a, latest.get(a.name), stale_hours)
		if with_ledger:
			avg = (row.get("calculated") or {}).get("avg_daily_spend", {}).get("value")
			row["ledger"] = ledger_estimate(a, avg) if a.name in with_records else {"label": LEDGER_LABEL, "avail": NO_DATA, "balance": None, "records": 0}
		out.append(row)
	return out


@frappe.whitelist()
def get_billing(**kwargs) -> dict:
	from excom.excom_ads.dashboard import _clean_filters
	from excom.excom_ads.settings import get_settings

	permissions.require_access()
	permissions.require("view_finance")
	s = get_settings()
	return {
		"accounts": summary(_clean_filters(kwargs)),
		"stale_after_hours": s.billing_stale_hours,
		"low_headroom_days": s.low_headroom_days,
		"ledger_label": LEDGER_LABEL,
		"can_add_records": bool(frappe.has_permission(RECORD, "create")),
	}


@frappe.whitelist()
def get_records(account: str, start: int = 0, page_length: int = 50) -> list[dict]:
	permissions.require_access()
	permissions.require("view_finance")
	acc = frappe.get_doc("Ads Account", account)
	if not permissions.doc_allowed(acc):
		raise frappe.PermissionError
	return frappe.get_list(RECORD, filters={"account": account}, fields=["name", "record_type", "posting_date", "amount", "currency", "reference", "payment_entry", "note", "owner", "creation"], order_by="posting_date desc, creation desc", start=cint(start), page_length=min(max(cint(page_length), 1), 200))
