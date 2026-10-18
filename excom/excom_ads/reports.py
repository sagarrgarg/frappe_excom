"""Marketing reports (plan §21): one Ads Report Template → an HTML summary, an Excel file and a CSV,
optionally a PDF, sent on a schedule (report_schedule.py) or previewed / sent now from here.

One source of truth: every figure comes from the functions behind the Marketing screens
(dashboard.overview, billing via overview, budgets.plan_vs_actual, the Leads screen's verified lead
counts), called with the template's filters for an explicit date range. The same range and filters
on the screen show the same numbers.

Scope: a report is always rendered **as a person** (`as_user`): each user recipient gets the
report computed inside their own scope and rights, so nobody receives a company, brand, account or
finance figure they could not open on the screen. Someone whose scope does not cover the
template's scope is skipped, not sent a partial report. External email addresses (no login) get
the template owner's view, only while the owner holds the export_data right, and never billing.

Every report states how fresh its data is and names each source that is stale, partial,
disconnected or not syncing (`disclosure`).
"""

import csv
import io
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, date_diff, escape_html, flt, fmt_money, get_datetime, get_first_day, get_last_day, getdate, now_datetime

from excom.excom_ads import permissions

TEMPLATE = "Ads Report Template"
DELIVERY = "Ads Report Delivery"
PRESETS = ("Yesterday", "Last 7 days", "Last 30 days", "Month to date", "Last month")
SECTIONS = (
	("sec_kpi", "KPI summary vs previous period"),
	("sec_trend", "Daily trend"),
	("sec_network", "By network"),
	("sec_brand", "By brand"),
	("sec_campaigns", "Top campaigns"),
	("sec_billing", "Billing"),
	("sec_budgets", "Budgets: plan vs actual"),
	("sec_alerts", "Open alerts"),
	("sec_freshness", "Data freshness"),
)
NA = "—"
MAX_ALERTS = 20
CSV_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


# ─── period and time zone ─────────────────────────────────────────────────────

def valid_tz(name: str | None) -> bool:
	if not name:
		return False
	try:
		from zoneinfo import ZoneInfo

		ZoneInfo(name)
		return True
	except Exception:
		return False


def template_tz(t) -> str:
	"""The template's own time zone, else its company's ad accounts' (the day the figures are
	booked on), else the system time zone."""
	tz = (t.get("time_zone") or "").strip()
	if valid_tz(tz):
		return tz
	f = {"company": t.get("company"), "timezone_name": ["is", "set"]}
	if t.get("workspace"):
		f["workspace"] = t.get("workspace")
	for name in frappe.get_all("Ads Account", filters=f, pluck="timezone_name", order_by="creation asc", limit=5):
		if valid_tz(name):
			return name
	return frappe.utils.get_system_timezone()


def local_now(tz: str) -> datetime:
	from zoneinfo import ZoneInfo

	return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)


def period(preset: str, today) -> tuple:
	"""Complete days only: a scheduled morning report never shows a half day."""
	today = getdate(today)
	y = add_days(today, -1)
	if preset == "Yesterday":
		return y, y
	if preset == "Last 7 days":
		return add_days(today, -7), y
	if preset == "Last 30 days":
		return add_days(today, -30), y
	if preset == "Month to date":
		return get_first_day(y), y
	if preset == "Last month":
		first = get_first_day(add_months(today, -1))
		return first, get_last_day(first)
	frappe.throw(_("Unknown report period {0}").format(preset))


def template_filters(t) -> dict:
	"""The template's scope and filters in the dashboard's vocabulary (dashboard.FILTER_FIELDS)."""
	return {k: t.get(k) for k in ("company", "workspace", "brand", "provider", "account") if t.get(k)}


@contextmanager
def as_user(user: str):
	"""Run as `user`: every permission check, User Permission and scope below is theirs."""
	previous = frappe.session.user
	if user == previous:
		yield
		return
	frappe.set_user(user)
	permissions.clear_cache()
	try:
		yield
	finally:
		frappe.set_user(previous)
		permissions.clear_cache()


# ─── collecting (inside the viewer's scope) ───────────────────────────────────

def collect(t, a, b, external: bool = False, today=None) -> dict:
	"""Everything the template's sections need, for the current session user. Raises
	PermissionError when the user has no ads access."""
	from excom.excom_ads import dashboard

	permissions.require_access()
	f = template_filters(t)
	ov = dashboard.overview(str(a), str(b), **f)
	finance = (not external) and permissions.has_capability("view_finance")
	data = {"filters": f, "overview": ov, "finance": finance, "external": external, "from_date": getdate(a), "to_date": getdate(b)}
	data["accounts"] = dashboard._accounts_in_scope(f, ["name", "account_name", "provider", "sync_enabled", "status", "last_metrics_sync", "error_class"])
	data["disclosure"] = disclosure(ov, data["accounts"], b, today=today)
	if t.get("sec_campaigns"):
		from excom.excom_ads import leads_dashboard

		campaigns = [r.get("campaign") for r in ov.get("top_campaigns") or [] if r.get("campaign")]
		data["verified_leads"] = leads_dashboard.verified_leads_by_campaign(campaigns, a, b)
	if t.get("sec_budgets"):
		from excom.excom_ads import budgets

		data["budgets"] = budgets.plan_vs_actual(f, a, b)
	if t.get("sec_alerts"):
		af = {"status": ["in", ["Open", "Acknowledged"]], **{k: v for k, v in f.items() if k in ("company", "workspace", "brand", "account")}}
		data["alerts"] = frappe.get_list("Ads Alert", filters=af, fields=["title", "alert_type", "severity", "status", "last_seen", "occurrences"], order_by="last_seen desc", limit_page_length=MAX_ALERTS)
	return data


def disclosure(ov: dict, accounts: list, to_date, today=None) -> dict:
	"""Plain statements about every source that is not fully trustworthy right now."""
	from excom.excom_ads.settings import get_settings

	fr = ov.get("freshness") or {}
	lines: list[tuple[str, str]] = []  # (level, text); level: bad / warn / info
	if not fr.get("accounts"):
		lines.append(("warn", _("No ad accounts in this report's scope: there are no figures to report.")))
	for e in fr.get("errors") or []:
		lines.append(("bad", _("Disconnected or failing: {0} ({1} error). Its figures stop at its last good sync.").format(e.get("account_name"), e.get("error_class"))))
	hours = fr.get("stale_after_hours")
	for s in fr.get("stale") or []:
		last = s.get("last_metrics_sync")
		lines.append(("warn", _("Stale: {0}, last synced {1} (older than {2} hours). Its figures may be incomplete.").format(s.get("account_name"), _when(last) if last else _("never"), hours)))
	for m in fr.get("manual") or []:
		lines.append(("info", _("Manual figures: {0}, last import {1}.").format(m.get("account_name"), _when(m.get("last_import")) if m.get("last_import") else _("never"))))
	off = [a for a in accounts if not cint(a.get("sync_enabled")) and a.get("provider") != "Manual"]
	for a in off:
		lines.append(("warn", _("Not syncing: {0} has sync switched off; its figures stop at its last sync.").format(a.get("account_name"))))
	names = [a.get("name") for a in accounts]
	if names:
		partial = frappe.get_list(
			"Ads Sync Job",
			filters={"account": ["in", names], "status": "Partial", "job_type": ["in", ["Metrics", "Settle", "Backfill"]], "creation": [">", add_days(now_datetime(), -1)]},
			fields=["account"],
			distinct=True,
			limit_page_length=50,
		)
		by_name = {a.get("name"): a.get("account_name") for a in accounts}
		for p in partial:
			lines.append(("warn", _("Partial: a recent sync of {0} finished with errors; some rows may be missing.").format(by_name.get(p.account, p.account))))
	for tot in ov.get("totals") or []:
		if cint(tot.get("rows_unconverted")):
			lines.append(("warn", _("Partial: {0} day rows have no exchange rate yet; their spend ({1} in account currency) is not in the {2} totals.").format(cint(tot.get("rows_unconverted")), _num(tot.get("spend_unconverted")), tot.get("currency") or "")))
	settle = cint(get_settings().settle_days) or 28
	if date_diff(getdate(today or getdate()), getdate(to_date)) <= settle:
		lines.append(("info", _("Platforms revise recent days for up to {0} days (late conversions, corrections): these figures can still change.").format(settle)))
	problem = any(level in ("bad", "warn") for level, _t in lines)
	as_of = fr.get("as_of")
	headline = _("Some figures are stale, partial or disconnected: see below.") if problem else _("All ad sources are fresh: data as of {0}.").format(_when(as_of) if as_of else NA)
	return {"ok": not problem, "headline": headline, "as_of": str(as_of) if as_of else None, "lines": lines}


def _when(v) -> str:
	if not v:
		return NA
	return get_datetime(v).strftime("%Y-%m-%d %H:%M")


def _num(v) -> str:
	return f"{flt(v):,.2f}"


# ─── tables: one model for HTML, Excel and CSV ────────────────────────────────

@dataclass
class V:
	"""A cell with its own kind (KPI rows mix money and counts) and currency."""

	v: object
	kind: str = "num"
	cur: str | None = None


@dataclass
class Table:
	key: str
	title: str
	columns: list  # [(label, kind)]
	rows: list = field(default_factory=list)
	note: str = ""
	currency_col: int | None = None


def _m(r: dict, k: str):
	v = r.get(k)
	return None if v is None else flt(v)


def _metric_table(key, title, rows, label_col, label_of, note=""):
	t = Table(key, title, [(label_col, "text"), (_("Currency"), "text"), (_("Spend"), "money"), (_("Impressions"), "int"), (_("Clicks"), "int"), (_("CTR"), "pct"), (_("CPC"), "money"), (_("Leads (platform)"), "int"), (_("CPL (platform)"), "money"), (_("Purchases"), "int"), (_("Purchase value"), "money"), (_("ROAS"), "ratio")], note=note, currency_col=1)
	for r in rows:
		t.rows.append([label_of(r), r.get("currency"), _m(r, "spend"), _m(r, "impressions"), _m(r, "clicks"), r.get("ctr"), r.get("cpc"), _m(r, "leads"), r.get("cpl"), _m(r, "purchases"), _m(r, "purchase_value"), r.get("roas")])
	return t


KPIS = (
	("spend", "Spend", "money", False),
	("impressions", "Impressions", "int", True),
	("clicks", "Clicks", "int", True),
	("ctr", "CTR", "pct", True),
	("cpc", "CPC", "money", False),
	("leads", "Leads (platform)", "int", True),
	("cpl", "CPL (platform)", "money", False),
	("purchases", "Purchases (platform)", "int", True),
	("purchase_value", "Purchase value (platform)", "money", True),
	("roas", "ROAS (platform)", "ratio", True),
	("conversations", "Messaging conversations", "int", True),
)


def build_tables(t, data: dict) -> list[Table]:
	ov = data["overview"]
	out: list[Table] = []
	if t.get("sec_kpi"):
		prev = {p.get("currency"): p for p in (ov.get("previous") or {}).get("totals") or []}
		pf, pt = (ov.get("previous") or {}).get("from_date"), (ov.get("previous") or {}).get("to_date")
		tab = Table("kpi", _("KPI summary"), [(_("Metric"), "text"), (_("Currency"), "text"), (_("This period"), "num"), (_("Previous period"), "num"), (_("Change %"), "change")], note=_("Platform-reported. Previous period: {0} to {1}.").format(pf, pt), currency_col=1)
		for cur in ov.get("totals") or []:
			p = prev.get(cur.get("currency")) or {}
			for k, label, kind, _up in KPIS:
				c, q = cur.get(k), p.get(k)
				change = (flt(c) - flt(q)) / abs(flt(q)) if c is not None and q is not None and flt(q) else None
				tab.rows.append([_(label), cur.get("currency"), V(c, kind, cur.get("currency")), V(q, kind, cur.get("currency")), change])
		ver = ov.get("verified") or {}
		for v in ver.get("totals") or [] if isinstance(ver, dict) else []:
			for k, label, kind in (("leads", "Leads (verified, ERPNext)", "int"), ("opportunities", "Opportunities (verified)", "int"), ("orders", "Orders (verified)", "int"), ("net_revenue", "Net revenue (verified)", "money")):
				if v.get(k) is not None:
					tab.rows.append([_(label), v.get("currency"), V(v.get(k), kind, v.get("currency")), V(None, kind), None])
		if not tab.rows:
			tab.note = _("No platform figures in this period.")
		out.append(tab)
	if t.get("sec_trend"):
		tab = Table("trend", _("Daily trend"), [(_("Date"), "text"), (_("Currency"), "text"), (_("Spend"), "money"), (_("Impressions"), "int"), (_("Clicks"), "int"), (_("Leads (platform)"), "int"), (_("Purchases"), "int"), (_("Purchase value"), "money")], note=_("Platform-reported, by the ad account's reporting day."), currency_col=1)
		for r in ov.get("series") or []:
			tab.rows.append([r.get("date"), r.get("currency"), _m(r, "spend"), _m(r, "impressions"), _m(r, "clicks"), _m(r, "leads"), _m(r, "purchases"), _m(r, "purchase_value")])
		out.append(tab)
	if t.get("sec_network"):
		out.append(_metric_table("network", _("By network"), ov.get("by_provider") or [], _("Network"), lambda r: r.get("provider")))
	if t.get("sec_brand"):
		out.append(_metric_table("brand", _("By brand"), ov.get("by_brand") or [], _("Brand"), lambda r: r.get("brand") or _("Unallocated"), note=_("Campaign-level rows: brand is allocated per campaign.")))
	if t.get("sec_campaigns"):
		verified = data.get("verified_leads") or {}
		available = verified.get("available", False) if isinstance(verified, dict) else False
		counts = verified.get("counts", {}) if available else {}
		tab = Table("campaigns", _("Top campaigns"), [(_("Campaign"), "text"), (_("Network"), "text"), (_("Status"), "text"), (_("Currency"), "text"), (_("Spend"), "money"), (_("Clicks"), "int"), (_("CTR"), "pct"), (_("Leads (platform)"), "int"), (_("CPL (platform)"), "money"), (_("Leads (verified)"), "int"), (_("CPL (verified)"), "money"), (_("Purchases"), "int"), (_("ROAS"), "ratio")], currency_col=3,
			note=_("Top 10 by spend. Platform leads are what the network reports under its own attribution; verified leads are ERPNext Leads whose first ad touch is the campaign (test leads excluded, created in the period, only leads you can open). The two are never added together.") if available else _("Top 10 by spend. Verified (ERPNext) leads are not available to this recipient: {0}").format(verified.get("reason") or _("no permission to read leads")))
		for r in ov.get("top_campaigns") or []:
			vl = counts.get(r.get("campaign")) if available else None
			vl = flt(vl) if available else None
			vcpl = (flt(r.get("spend")) / vl) if vl else None
			tab.rows.append([r.get("campaign_name") or r.get("campaign") or _("(no campaign)"), r.get("provider"), r.get("status"), r.get("currency"), _m(r, "spend"), _m(r, "clicks"), r.get("ctr"), _m(r, "leads"), r.get("cpl"), vl, vcpl, _m(r, "purchases"), r.get("roas")])
		out.append(tab)
	if t.get("sec_billing") and data.get("finance") and ov.get("billing") is not None:
		tab = Table("billing", _("Billing"), [(_("Ad account"), "text"), (_("Network"), "text"), (_("Currency"), "text"), (_("Payment model"), "text"), (_("Amount spent (reported)"), "money"), (_("Spend cap / limit"), "money"), (_("Headroom (calculated)"), "money"), (_("Amount due (reported)"), "money"), (_("As of"), "text"), (_("Stale"), "text")], currency_col=2,
			note=_("Reported = exactly what the platform returned; calculated = ours. Blank = the platform does not report it (not zero)."))
		for r in ov.get("billing") or []:
			tab.rows.append([r.get("account_name"), r.get("provider"), r.get("currency"), r.get("payment_model"), r.get("amount_spent"), r.get("spend_cap"), r.get("headroom"), r.get("amount_due"), _when(r.get("as_of")) if r.get("as_of") else None, _("Yes") if r.get("stale") else _("No")])
		out.append(tab)
	if t.get("sec_budgets") and data.get("budgets") is not None:
		pv = data["budgets"]
		tab = Table("budgets", _("Budgets: plan vs actual"), [(_("Month"), "text"), (_("Network"), "text"), (_("Brand"), "text"), (_("Currency"), "text"), (_("Planned"), "money"), (_("Planned to date"), "money"), (_("Actual (platform)"), "money"), (_("Variance %"), "pct"), (_("Pacing"), "ratio"), (_("Target CPL"), "money"), (_("Actual CPL"), "money")], currency_col=3,
			note=_("Approved plans only, whole months {0} to {1}.").format(pv.get("from_date"), pv.get("to_date")))
		for r in pv.get("rows") or []:
			tab.rows.append([r.get("month"), r.get("provider"), r.get("brand") or "", r.get("currency"), r.get("planned_spend"), r.get("planned_to_date"), r.get("actual_spend"), r.get("variance_pct"), r.get("pacing"), r.get("target_cpl"), r.get("actual_cpl")])
		if not tab.rows:
			tab.note = _("No approved budget plan covers this period.")
		out.append(tab)
	if t.get("sec_alerts") and data.get("alerts") is not None:
		tab = Table("alerts", _("Open alerts"), [(_("Severity"), "text"), (_("Type"), "text"), (_("Alert"), "text"), (_("Status"), "text"), (_("Last seen"), "text"), (_("Seen"), "int")])
		for r in data["alerts"]:
			tab.rows.append([r.severity, r.alert_type, r.title, r.status, _when(r.last_seen), flt(r.occurrences)])
		if not tab.rows:
			tab.note = _("No open alerts.")
		elif len(tab.rows) >= MAX_ALERTS:
			tab.note = _("The {0} most recent; see the Alerts screen for all.").format(MAX_ALERTS)
		out.append(tab)
	if t.get("sec_freshness"):
		fr = ov.get("freshness") or {}
		stale = {s.get("account") for s in fr.get("stale") or []}
		errors = {e.get("account"): e.get("error_class") for e in fr.get("errors") or []}
		tab = Table("freshness", _("Data freshness"), [(_("Ad account"), "text"), (_("Network"), "text"), (_("Last metrics sync"), "text"), (_("State"), "text")])
		for a in data.get("accounts") or []:
			if a.get("provider") == "Manual":
				state = _("Manual import")
			elif not cint(a.get("sync_enabled")):
				state = _("Sync off")
			elif a.get("name") in errors:
				state = _("Error: {0}").format(errors[a.get("name")])
			elif a.get("name") in stale:
				state = _("Stale")
			else:
				state = _("Fresh")
			tab.rows.append([a.get("account_name"), a.get("provider"), _when(a.get("last_metrics_sync")) if a.get("last_metrics_sync") else _("never"), state])
		out.append(tab)
	return out


# ─── formatting ───────────────────────────────────────────────────────────────

def _fmt(value, kind: str, cur: str | None) -> str:
	if isinstance(value, V):
		value, kind, cur = value.v, value.kind, value.cur or cur
	if value is None or value == "":
		return NA
	if kind == "money":
		v = flt(value)
		return fmt_money(v, precision=0 if abs(v) >= 1000 else 2, currency=cur) if cur else f"{v:,.2f}"
	if kind == "int":
		return f"{flt(value):,.0f}"
	if kind == "num":
		return f"{flt(value):,.2f}"
	if kind == "pct":
		return f"{flt(value):.2f}%"
	if kind == "ratio":
		return f"{flt(value):.2f}×"
	if kind == "change":
		return f"{flt(value) * 100:+.0f}%"
	return str(value)


def _raw(value, kind: str):
	"""Excel / CSV value: numbers stay numbers, not available stays blank (never 0)."""
	if isinstance(value, V):
		value, kind = value.v, value.kind
	if value is None:
		return ""
	if kind in ("money", "num", "pct", "ratio"):
		return round(flt(value), 4)
	if kind == "int":
		return round(flt(value), 2)
	if kind == "change":
		return round(flt(value) * 100, 2)
	return str(value)


def _csv_safe(v):
	"""A text cell starting with = + - @ would run as a formula in a spreadsheet: make it literal."""
	if isinstance(v, str) and v.startswith(CSV_TRIGGERS):
		return "'" + v
	return v


@dataclass
class Rendered:
	subject: str
	html: str
	xlsx: bytes
	csv: bytes
	pdf: bytes | None
	file_base: str
	summary: str
	disclosure: dict
	from_date: str
	to_date: str

	def attachments(self) -> list[dict]:
		out = [{"fname": f"{self.file_base}.xlsx", "fcontent": self.xlsx}, {"fname": f"{self.file_base}.csv", "fcontent": self.csv}]
		if self.pdf:
			out.append({"fname": f"{self.file_base}.pdf", "fcontent": self.pdf})
		return out

	@property
	def size(self) -> int:
		return len(self.html.encode()) + sum(len(a["fcontent"]) for a in self.attachments())


def _scope_label(t) -> str:
	bits = [t.company]
	for k, label in (("workspace", _("workspace")), ("brand", _("brand")), ("provider", _("network")), ("account", _("account"))):
		if t.get(k):
			bits.append(f"{label} {t.get(k)}")
	return " · ".join(bits)


def _head_rows(t, data, viewer: str) -> list[list]:
	d = data["disclosure"]
	rows = [[t.template_name], [_("Period"), str(data["from_date"]), str(data["to_date"])], [_("Scope"), _scope_label(t)], [_("Rendered for"), viewer, _("Generated"), _when(now_datetime())], [_("Data freshness"), d["headline"]]]
	rows += [["", text] for _level, text in d["lines"]]
	rows.append([_("Blank cells: not available (never zero)."), ""])
	return rows


def _sheet_rows(tables: list[Table], head: list[list]) -> list[list]:
	rows = [list(r) for r in head] + [[]]
	for tab in tables:
		rows.append([tab.title])
		if tab.note:
			rows.append([tab.note])
		rows.append([c[0] for c in tab.columns])
		for r in tab.rows:
			rows.append([_raw(v, tab.columns[i][1]) for i, v in enumerate(r)])
		rows.append([])
	return rows


def to_xlsx(rows: list[list]) -> bytes:
	from frappe.utils.xlsxutils import make_xlsx

	return make_xlsx(rows, "Report").getvalue()


def to_csv(rows: list[list]) -> bytes:
	buf = io.StringIO()
	w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
	for r in rows:
		w.writerow([_csv_safe(v) for v in r])
	return buf.getvalue().encode("utf-8-sig")


# Email-safe HTML: inline styles, no scripts, no external resources; every value escaped.
CSS_TABLE = "border-collapse:collapse;width:100%;font-size:12px;margin:4px 0 16px"
CSS_TH = "text-align:left;border-bottom:1px solid #d0d5dd;padding:4px 6px;color:#475467;font-weight:600;white-space:nowrap"
CSS_TD = "border-bottom:1px solid #eaecf0;padding:4px 6px;color:#101828"
LEVEL_COLOR = {"bad": "#b42318", "warn": "#b54708", "info": "#475467"}


def to_html(t, data, tables: list[Table], viewer: str, pdf_note: str) -> str:
	d = data["disclosure"]
	e = escape_html
	box_color = "#ecfdf3" if d["ok"] else "#fffaeb"
	parts = [
		"<div style=\"font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#101828;max-width:960px\">",
		f"<h2 style=\"margin:0 0 4px;font-size:18px\">{e(t.template_name)}</h2>",
		f"<p style=\"margin:0 0 12px;color:#475467;font-size:12px\">{e(str(data['from_date']))} to {e(str(data['to_date']))} · {e(_scope_label(t))} · {e(_('rendered for'))} {e(viewer)}</p>",
		f"<div style=\"background:{box_color};border-radius:6px;padding:8px 10px;margin:0 0 16px;font-size:12px\"><strong>{e(d['headline'])}</strong>",
	]
	if d["lines"]:
		parts.append("<ul style=\"margin:6px 0 0;padding-left:18px\">")
		for level, text in d["lines"]:
			parts.append(f"<li style=\"color:{LEVEL_COLOR.get(level, '#475467')}\">{e(text)}</li>")
		parts.append("</ul>")
	parts.append("</div>")
	for tab in tables:
		parts.append(f"<h3 style=\"margin:12px 0 2px;font-size:14px\">{e(tab.title)}</h3>")
		if tab.note:
			parts.append(f"<p style=\"margin:0 0 4px;color:#475467;font-size:11px\">{e(tab.note)}</p>")
		if not tab.rows:
			continue
		parts.append(f"<table style=\"{CSS_TABLE}\"><thead><tr>")
		for label, kind in tab.columns:
			align = "right" if kind not in ("text",) else "left"
			parts.append(f"<th style=\"{CSS_TH};text-align:{align}\">{e(label)}</th>")
		parts.append("</tr></thead><tbody>")
		for r in tab.rows:
			cur = r[tab.currency_col] if tab.currency_col is not None else None
			parts.append("<tr>")
			for i, v in enumerate(r):
				kind = tab.columns[i][1]
				align = "left" if kind == "text" and not isinstance(v, V) else "right"
				parts.append(f"<td style=\"{CSS_TD};text-align:{align}\">{e(_fmt(v, kind, cur))}</td>")
			parts.append("</tr>")
		parts.append("</tbody></table>")
	parts.append(f"<p style=\"color:#667085;font-size:11px;margin-top:16px\">{e(_('Platform figures are as reported by each ad network under its own attribution; verified figures come from ERPNext. They are never added together. — means not available, never zero.'))} {e(pdf_note)}</p>")
	parts.append("</div>")
	return "".join(parts)


# ─── PDF (optional) ───────────────────────────────────────────────────────────

def pdf_engine() -> str | None:
	"""'chrome' on Frappe v16 when chromium_path is set in common_site_config (Frappe's own Chrome
	PDF generator), 'wkhtmltopdf' when that binary exists, else None. Nothing is downloaded or
	installed: without an engine, reports go without a PDF and say so."""
	try:
		from frappe.utils.pdf import get_chrome_pdf  # noqa: F401  (v16 only)

		if shutil.which(frappe.get_common_site_config().get("chromium_path") or ""):
			return "chrome"
	except ImportError:
		pass
	if shutil.which("wkhtmltopdf"):
		return "wkhtmltopdf"
	return None


def pdf_note() -> str:
	return "" if pdf_engine() else _("No PDF: this server has no PDF engine (Frappe v16 with chromium_path, or wkhtmltopdf); the Excel and CSV files carry the same figures.")


def make_pdf(html: str) -> bytes | None:
	engine = pdf_engine()
	if not engine:
		return None
	doc = f"<!doctype html><html><head><meta charset=\"utf-8\"></head><body>{html}</body></html>"
	try:
		if engine == "chrome":
			from frappe.utils.pdf import get_chrome_pdf

			return get_chrome_pdf(None, doc, {}, None, pdf_generator="chrome")
		from frappe.utils.pdf import get_pdf

		return get_pdf(doc)
	except Exception:
		# The report still goes out with Excel and CSV.
		frappe.log_error(title="Excom Ads: report PDF failed", message=frappe.get_traceback())
		return None


# ─── render ───────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
	return re.sub(r"[^A-Za-z0-9]+", "-", s or "report").strip("-")[:60] or "report"


def render(t, a, b, viewer: str, external: bool = False, with_pdf: bool = True, today=None) -> Rendered:
	"""Collect and format for the CURRENT session user (call inside as_user)."""
	data = collect(t, a, b, external=external, today=today)
	tables = build_tables(t, data)
	note = pdf_note()
	html = to_html(t, data, tables, viewer, note)
	rows = _sheet_rows(tables, _head_rows(t, data, viewer))
	ov = data["overview"]
	d = data["disclosure"]
	tot = (ov.get("totals") or [{}])[0] if ov.get("totals") else {}
	summary = _("Spend {0}, platform leads {1}. {2}").format(_fmt(tot.get("spend"), "money", tot.get("currency")), _fmt(tot.get("leads"), "int", None), d["headline"]) if tot else d["headline"]
	subject = f"{t.template_name}: {a} to {b}" + ("" if d["ok"] else " " + _("(some data stale or partial)"))
	return Rendered(
		subject=subject[:140],
		html=html,
		xlsx=to_xlsx(rows),
		csv=to_csv(rows),
		pdf=make_pdf(html) if with_pdf else None,
		file_base=f"{_slug(t.template_name)}_{a}_{b}",
		summary=summary[:900],
		disclosure=d,
		from_date=str(a),
		to_date=str(b),
	)


def current_period(t, today=None) -> tuple:
	today = getdate(today) if today else local_now(template_tz(t)).date()
	return period(t.date_preset or "Last 7 days", today), today


# ─── endpoints ────────────────────────────────────────────────────────────────

def _visible_template(name: str):
	if not frappe.db.exists(TEMPLATE, name):
		raise frappe.DoesNotExistError(_("Report {0} not found").format(name))
	t = frappe.get_doc(TEMPLATE, name)
	if not permissions.doc_allowed(t) or not frappe.has_permission(TEMPLATE, "read", doc=t):
		raise frappe.PermissionError(_("Not permitted"))
	return t


@frappe.whitelist()
def list_templates() -> dict:
	permissions.require_access()
	from excom.excom_ads import report_schedule
	from excom.excom_ads.settings import get_settings

	rows = frappe.get_list(TEMPLATE, fields=["name", "template_name", "enabled", "company", "workspace", "brand", "provider", "account", "date_preset", "schedule_enabled", "frequency", "send_time", "weekday", "day_of_month", "time_zone", "via_email", "via_whatsapp", "last_period_key", "last_sent_at", "owner"], order_by="template_name asc", limit_page_length=200)
	for r in rows:
		r["next_run"] = report_schedule.next_run_label(r) if r.schedule_enabled and r.enabled else None
	return {
		"templates": rows,
		"can_send": permissions.has_capability("export_data"),
		"can_create": bool(frappe.has_permission(TEMPLATE, "create")),
		"pdf_available": bool(pdf_engine()),
		"whatsapp_configured": bool(get_settings().report_whatsapp_template),
	}


@frappe.whitelist()
def preview(template: str) -> dict:
	"""Render now, for the caller, inside the caller's scope. Nothing is sent or stored."""
	permissions.require_access()
	t = _visible_template(template)
	(a, b), today = current_period(t)
	r = render(t, a, b, viewer=frappe.session.user, with_pdf=False, today=today)
	return {"html": r.html, "subject": r.subject, "from_date": r.from_date, "to_date": r.to_date, "disclosure": {"ok": r.disclosure["ok"], "headline": r.disclosure["headline"], "lines": [{"level": lv, "text": tx} for lv, tx in r.disclosure["lines"]]}, "size": r.size, "pdf_available": bool(pdf_engine())}


@frappe.whitelist()
def download(template: str, fmt: str = "xlsx"):
	"""The caller's own rendering as a file. Taking data out needs export_data."""
	permissions.require_access()
	permissions.require("export_data")
	if fmt not in ("xlsx", "csv"):
		frappe.throw(_("Pick xlsx or csv"))
	t = _visible_template(template)
	(a, b), today = current_period(t)
	r = render(t, a, b, viewer=frappe.session.user, with_pdf=False, today=today)
	frappe.response["filename"] = f"{r.file_base}.{fmt}"
	frappe.response["filecontent"] = r.xlsx if fmt == "xlsx" else r.csv
	frappe.response["type"] = "binary"


@frappe.whitelist(methods=["POST"])
def send_now(template: str) -> dict:
	"""Send to every recipient now (each in their own scope). Needs export_data (admins hold it)."""
	permissions.require_access()
	permissions.require("export_data")
	t = _visible_template(template)
	if not frappe.has_permission(TEMPLATE, "write", doc=t):
		raise frappe.PermissionError(_("Not permitted"))
	from excom.excom_ads import report_schedule

	key = report_schedule.manual_period_key(now_datetime())
	frappe.enqueue("excom.excom_ads.report_schedule.run", queue="long", template=t.name, period_key=key, triggered_by=frappe.session.user, job_id=f"excom_ads_report::{frappe.local.site}::{t.name}::{key}", deduplicate=True, enqueue_after_commit=True)
	return {"queued": True, "period_key": key}


@frappe.whitelist()
def deliveries(template: str, start: int = 0, page_length: int = 50) -> list[dict]:
	"""Delivery log of one report (who, when, which channel, outcome). Holds no figures."""
	permissions.require_access()
	t = _visible_template(template)
	return frappe.get_list(DELIVERY, filters={"template": t.name}, fields=["name", "period_key", "period_from", "period_to", "channel", "status", "recipient_user", "recipient_email", "rendered_as", "sent_at", "size_bytes", "attachments", "attempts", "error", "triggered_by", "creation"], order_by="creation desc", start=max(cint(start), 0), page_length=min(max(cint(page_length), 1), 200))
