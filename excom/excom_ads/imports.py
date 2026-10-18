"""Manual / CSV import for ad networks without a connector (release 1).

An Ads Metric Import names a Manual Ads Account and carries a CSV or XLSX file:

    date, campaign, campaign_id, spend, impressions, clicks, leads, purchases, purchase_value, currency

    date            YYYY-MM-DD (an XLSX date cell works too), in the network's reporting day; not in the future
    campaign        the campaign name; and/or
    campaign_id     the network's campaign ID (wins over the name when both are given)
    spend           required, ≥ 0, in the account currency ("." decimals, "," thousands allowed)
    impressions, clicks                    whole numbers ≥ 0 (blank = 0)
    leads, purchases, purchase_value       numbers ≥ 0 (blank = 0)
    currency        optional; when given it must be the account's currency

Validate checks every row and lists every problem with its row number; nothing is written while
any row is wrong (all or nothing). Import then
  - matches each campaign to the account's campaign mirrors: by campaign ID (mirror
    `{account}-{id}`), else by exact name (case-insensitive) when exactly one mirror has it, else
    creates a mirror (ID = the given ID, or `m-` + a hash of the name, so the same name always
    lands on the same mirror);
  - replaces the account's Campaign- and Account-level Ads Metric Snapshots for every date in the
    file (re-importing a file, or a corrected one, writes the same rows; dates not in the file are
    untouched), Account level = the sum of the day's campaign rows;
  - converts spend to company currency at ERPNext's dated rate, like the connectors do.
These are platform-reported figures (what the network's own report says), shown with the Platform
badge and never added to verified figures.
"""

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from excom.excom_ads import permissions
from excom.excom_ads.naming import snapshot_name

DT = "Ads Metric Import"
SNAP = "Ads Metric Snapshot"
COLUMNS = ("date", "campaign", "campaign_id", "spend", "impressions", "clicks", "leads", "purchases", "purchase_value", "currency")
ALIASES = {"day": "date", "campaign_name": "campaign", "amount_spent": "spend", "cost": "spend", "conversion_value": "purchase_value", "purchases_value": "purchase_value"}
INT_COLUMNS = ("impressions", "clicks")
FLOAT_COLUMNS = ("leads", "purchases", "purchase_value")
MAX_ROWS = 20000
MAX_ERRORS_STORED = 500
EARLIEST = date(2000, 1, 1)
EXAMPLE = ["2026-10-01", "Diwali Search", "", "1250.50", "48210", "932", "14", "3", "8400", ""]


# ─── reading ──────────────────────────────────────────────────────────────────

def _file_content(doc) -> tuple[bytes, str]:
	"""The attached file, only if it is attached to THIS import (never any other private file)."""
	url = doc.import_file or ""
	files = frappe.get_all("File", filters={"file_url": url}, fields=["name", "owner", "attached_to_doctype", "attached_to_name"], order_by="creation asc")
	mine = [f for f in files if f.attached_to_doctype == DT and f.attached_to_name == doc.name]
	if not url or not mine:
		frappe.throw(_("Attach the file to this import (save the form after uploading it)."))
	# Saving an Attach field that names an existing URL makes a new File record pointing at it. Only
	# read a file the caller uploaded (or that was uploaded to this import), never someone else's.
	original = files[0]
	if original.name != mine[0].name and original.owner != frappe.session.user and not permissions.is_super():
		frappe.throw(_("That file was uploaded by someone else. Upload your own copy."), frappe.PermissionError)
	f = frappe.get_doc("File", mine[0].name)
	content = f.get_content()
	if isinstance(content, str):
		content = content.encode()
	return content, (f.file_name or url).lower()


def _table(content: bytes, filename: str) -> list[list]:
	if filename.endswith(".xlsx"):
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		return [list(r) for r in read_xlsx_file_from_attached_file(fcontent=content)]
	if filename.endswith(".csv") or filename.endswith(".txt"):
		try:
			text = content.decode("utf-8-sig")
		except UnicodeDecodeError:
			frappe.throw(_("The CSV file is not UTF-8 text. Save it as 'CSV UTF-8' and attach it again."))
		return [r for r in csv.reader(io.StringIO(text))]
	frappe.throw(_("Attach a .csv or .xlsx file."))


def _header(cells) -> list[str]:
	out = []
	for c in cells:
		key = re.sub(r"[^a-z0-9]+", "_", str(c or "").strip().lower()).strip("_")
		out.append(ALIASES.get(key, key))
	return out


def _num(value):
	"""'1,234.50' / 1234.5 / '' → float or None (blank). Raises ValueError on anything else."""
	if value is None:
		return None
	if isinstance(value, (int, float)) and not isinstance(value, bool):
		return float(value)
	s = str(value).strip().replace(" ", "")
	if not s:
		return None
	if not re.fullmatch(r"-?\d{1,3}(,\d{2,3})*(\.\d+)?|-?\d+(\.\d+)?", s):
		raise ValueError(s)
	return float(s.replace(",", ""))


def _day(value):
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	s = str(value or "").strip()
	if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]) or (len(s) > 10 and not re.fullmatch(r"[ T]00:00(:00)?", s[10:])):
		raise ValueError(s)
	return datetime.strptime(s[:10], "%Y-%m-%d").date()


def parse(doc) -> tuple[list[dict], list[dict], int]:
	"""(valid rows, errors [{row, column, error}], data rows seen). Row numbers are the file's lines."""
	from excom.excom_ads.sync import account_today

	acc = frappe.get_doc("Ads Account", doc.account)
	content, filename = _file_content(doc)
	table = _table(content, filename)
	errors: list[dict] = []
	if not table:
		return [], [{"row": 1, "column": "", "error": "The file is empty."}], 0
	header = _header(table[0])
	missing = [c for c in ("date", "spend") if c not in header]
	if "campaign" not in header and "campaign_id" not in header:
		missing.append("campaign or campaign_id")
	if missing:
		return [], [{"row": 1, "column": ", ".join(missing), "error": f"Missing column(s): {', '.join(missing)}. Use the template's header row."}], 0
	unknown = [h for h in header if h and h not in COLUMNS]
	if unknown:
		errors.append({"row": 1, "column": ", ".join(unknown), "error": "Unknown column(s); remove or rename them (see the template)."})
	idx = {h: i for i, h in enumerate(header) if h in COLUMNS}
	today = account_today(acc)
	currency = (acc.currency or "").upper()
	rows, seen, data_rows = [], {}, 0
	for line, cells in enumerate(table[1:], start=2):
		if not any(str(c or "").strip() for c in cells):
			continue
		data_rows += 1
		if data_rows > MAX_ROWS:
			errors.append({"row": line, "column": "", "error": f"More than {MAX_ROWS} rows: split the file."})
			break

		def cell(col):
			i = idx.get(col)
			return cells[i] if i is not None and i < len(cells) else None

		row, bad = {"row": line}, False

		def err(column, message):
			nonlocal bad
			bad = True
			errors.append({"row": line, "column": column, "error": message})

		try:
			d = _day(cell("date"))
			if d > today:
				err("date", f"{d} is in the future for this account")
			elif d < EARLIEST:
				err("date", f"{d} is too far back")
			row["date"] = d
		except (ValueError, TypeError):
			err("date", f"'{str(cell('date') or '')[:30]}' is not a date (use YYYY-MM-DD)")
		name = str(cell("campaign") or "").strip()
		cid = str(cell("campaign_id") or "").strip()
		if isinstance(cell("campaign_id"), float) and float(cell("campaign_id")).is_integer():
			cid = str(int(cell("campaign_id")))  # Excel turns long IDs into numbers
		if not name and not cid:
			err("campaign", "Give the campaign name or campaign_id")
		if len(name) > 140:
			err("campaign", "Campaign name longer than 140 characters")
		if cid and not re.fullmatch(r"[\w.\-:~]{1,100}", cid):
			err("campaign_id", "Campaign ID may only hold letters, digits and . - _ : ~ (max 100)")
		row["campaign"], row["campaign_id"] = name, cid
		for col in ("spend", *INT_COLUMNS, *FLOAT_COLUMNS):
			raw = cell(col)
			try:
				v = _num(raw)
			except ValueError:
				err(col, f"'{str(raw)[:30]}' is not a number")
				continue
			if v is None:
				if col == "spend":
					err(col, "Spend is required (0 is allowed)")
				v = 0.0
			if v < 0:
				err(col, "Cannot be negative")
			if col in INT_COLUMNS and not float(v).is_integer():
				err(col, "Must be a whole number")
			row[col] = int(v) if col in INT_COLUMNS and float(v).is_integer() else v
		cur = str(cell("currency") or "").strip().upper()
		if cur and currency and cur != currency:
			err("currency", f"{cur} is not the account currency {currency}")
		if bad:
			continue
		key = (row["date"], cid.lower() if cid else f"name:{name.lower()}")
		if key in seen:
			errors.append({"row": line, "column": "campaign", "error": f"Same date and campaign as row {seen[key]}"})
			continue
		seen[key] = line
		rows.append(row)
	if not acc.currency:
		errors.insert(0, {"row": 0, "column": "", "error": f"Set the currency on Ads Account {acc.name} first."})
	return rows, errors, data_rows


# ─── endpoints ────────────────────────────────────────────────────────────────

def _require_import(name: str):
	permissions.require("manage_connections")
	if not frappe.db.exists(DT, name):
		raise frappe.DoesNotExistError(_("Import {0} not found").format(name))
	doc = frappe.get_doc(DT, name)
	if not frappe.has_permission(DT, "write", doc=doc) or not permissions.doc_allowed(doc):
		raise frappe.PermissionError(_("Not permitted"))
	if frappe.db.get_value("Ads Account", doc.account, "provider") != "Manual":
		frappe.throw(_("Imports are for Manual ad accounts only."))
	return doc


def _record(doc, status: str, rows: list, errors: list, data_rows: int, **extra) -> dict:
	dates = [r["date"] for r in rows]
	values = {
		"status": status,
		"rows_total": data_rows,
		"rows_valid": len(rows),
		"rows_error": len({e["row"] for e in errors}),
		"errors": json.dumps(errors[:MAX_ERRORS_STORED], indent=1, default=str) if errors else None,
		"date_from": min(dates) if dates else None,
		"date_to": max(dates) if dates else None,
		**extra,
	}
	if errors:
		values["summary"] = f"{len(errors)} problem(s) in {values['rows_error']} row(s); nothing was imported. Fix the file and validate again." + (f" Showing the first {MAX_ERRORS_STORED}." if len(errors) > MAX_ERRORS_STORED else "")
	elif status == "Validated":
		values["summary"] = f"{len(rows)} row(s) for {len(set(dates))} day(s) are valid. Import replaces this account's figures for those days."
	frappe.db.set_value(DT, doc.name, values)
	frappe.db.commit()
	return {**values, "name": doc.name, "errors": errors[:MAX_ERRORS_STORED]}


@frappe.whitelist(methods=["POST"])
def validate_import(name: str) -> dict:
	doc = _require_import(name)
	rows, errors, n = parse(doc)
	return _record(doc, "Failed" if errors else "Validated", rows, errors, n)


@frappe.whitelist(methods=["POST"])
def run_import(name: str) -> dict:
	"""Re-validates the file (it may have changed since), then replaces the file's dates."""
	doc = _require_import(name)
	rows, errors, n = parse(doc)
	if errors:
		return _record(doc, "Failed", rows, errors, n)
	result = apply(doc, rows)
	return _record(doc, "Imported", rows, [], n, imported_at=now_datetime(), imported_by=frappe.session.user, campaigns_created=result["campaigns_created"], snapshots_written=result["snapshots_written"], summary=f"Imported {len(rows)} row(s): {result['snapshots_written']} daily rows written for {result['days']} day(s), {result['campaigns_created']} new campaign(s).")


@frappe.whitelist()
def download_template():
	permissions.require_access()
	buf = io.StringIO()
	w = csv.writer(buf)
	w.writerow(COLUMNS)
	w.writerow(EXAMPLE)
	frappe.response["filename"] = "ads_metric_import_template.csv"
	frappe.response["filecontent"] = buf.getvalue()
	frappe.response["type"] = "download"


# ─── writing ──────────────────────────────────────────────────────────────────

def _name_id(name: str) -> str:
	return "m-" + hashlib.sha1(name.strip().lower().encode()).hexdigest()[:12]


def _campaigns(acc, rows: list[dict]) -> tuple[dict, int]:
	"""(row key → campaign mirror name, campaigns created)."""
	existing = frappe.get_all("Ads Campaign", filters={"account": acc.name}, fields=["name", "external_id", "campaign_name", "brand"])
	by_id = {c.external_id: c for c in existing}
	by_name: dict[str, list] = {}
	for c in existing:
		by_name.setdefault((c.campaign_name or "").strip().lower(), []).append(c)
	out, created = {}, 0
	scope = {"account": acc.name, "workspace": acc.workspace, "company": acc.company, "provider": acc.provider}
	for r in rows:
		key = (r["campaign_id"].lower(), r["campaign"].lower())
		if key in out:
			continue
		ext = r["campaign_id"]
		if not ext:
			hits = by_name.get(r["campaign"].lower()) or []
			if len(hits) == 1:
				out[key] = hits[0].name
				continue
			ext = _name_id(r["campaign"])
		if ext in by_id:
			c = by_id[ext]
			if r["campaign"] and r["campaign"] != c.campaign_name:
				frappe.db.set_value("Ads Campaign", c.name, "campaign_name", r["campaign"], update_modified=True)
			out[key] = c.name
			continue
		doc = frappe.get_doc({"doctype": "Ads Campaign", **scope, "external_id": ext, "campaign_name": r["campaign"] or ext, "status": "Unknown", "synced_at": now_datetime()})
		doc.flags.ignore_links = True  # the account and its workspace were just read
		doc.insert(ignore_permissions=True)
		by_id[ext] = frappe._dict(name=doc.name, external_id=ext, campaign_name=doc.campaign_name, brand=doc.brand)
		out[key] = doc.name
		created += 1
	return out, created


SNAP_FIELDS = (
	"name", "owner", "modified_by", "creation", "modified", "docstatus", "date", "level", "account", "campaign", "entity_external_id",
	"workspace", "company", "brand", "provider", "breakdown_key", "breakdown_value", "attribution_key", "currency", "spend", "impressions",
	"reach", "clicks", "link_clicks", "leads", "purchases", "purchase_value", "messaging_conversations", "company_currency", "exchange_rate",
	"spend_company_currency", "metrics_json", "synced_at", "source_window",
)


def apply(doc, rows: list[dict]) -> dict:
	from excom.excom_ads.sync import _Rates

	acc = frappe.get_doc("Ads Account", doc.account)
	camp_of, created = _campaigns(acc, rows)
	camp_meta = {c.name: c for c in frappe.get_all("Ads Campaign", filters={"account": acc.name}, fields=["name", "external_id", "brand"])}
	rates = _Rates(acc)
	now = now_datetime()
	user = frappe.session.user
	cells: dict[tuple, dict] = {}
	metric_cols = ("spend", *INT_COLUMNS, *FLOAT_COLUMNS)
	for r in rows:
		camp = camp_of[(r["campaign_id"].lower(), r["campaign"].lower())]
		for level, entity in (("Campaign", camp), ("Account", None)):
			cell = cells.setdefault((level, entity, r["date"]), {m: 0 for m in metric_cols})
			for m in metric_cols:
				cell[m] += r[m]
	dates = sorted({r["date"] for r in rows})
	frappe.db.delete(SNAP, {"account": acc.name, "level": ["in", ["Account", "Campaign"]], "date": ["in", dates]})
	values = []
	for (level, camp, d), m in cells.items():
		ext = camp_meta[camp].external_id if camp else acc.external_id
		rate = rates.get(acc.currency, d)
		row = {
			"name": snapshot_name(acc.name, level, ext, d), "owner": user, "modified_by": user, "creation": now, "modified": now, "docstatus": 0,
			"date": d, "level": level, "account": acc.name, "campaign": camp, "entity_external_id": ext, "workspace": acc.workspace, "company": acc.company,
			"brand": (camp_meta[camp].brand if camp else None) or acc.brand, "provider": acc.provider, "breakdown_key": "none", "breakdown_value": "", "attribution_key": "default",
			"currency": acc.currency, "reach": 0, "link_clicks": 0, "messaging_conversations": 0, "company_currency": rates.to, "exchange_rate": rate,
			"spend_company_currency": flt(m["spend"]) * rate, "metrics_json": json.dumps({"source": "import", "import": doc.name}), "synced_at": now, "source_window": f"import:{doc.name}",
			**{k: (int(v) if k in INT_COLUMNS else flt(v)) for k, v in m.items()},
		}
		values.append(tuple(row.get(f) for f in SNAP_FIELDS))
	frappe.db.bulk_insert(SNAP, list(SNAP_FIELDS), values)
	updates = {"last_metrics_sync": now, "metrics_synced_from": min(dates), "metrics_synced_until": max(dates)}
	if acc.metrics_synced_from:
		updates["metrics_synced_from"] = min(getdate(acc.metrics_synced_from), dates[0])
	if acc.metrics_synced_until:
		updates["metrics_synced_until"] = max(getdate(acc.metrics_synced_until), dates[-1])
	frappe.db.set_value("Ads Account", acc.name, updates, update_modified=False)
	return {"campaigns_created": created, "snapshots_written": len(values), "days": len(dates)}
