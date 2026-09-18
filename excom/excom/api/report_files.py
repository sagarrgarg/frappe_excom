"""The activity report as a file somebody can send to somebody else.

A report read on a screen is a report one person saw. A workbook or a PDF is one a manager can put
in front of a team, and that is most of what these are for — so they are built to be read on paper
and in a spreadsheet, not to be a screenshot of the page.

The same permission rule as the report itself, because a download is exactly where an agent would
try somebody else's name.
"""

import io

import frappe
from frappe import _

from excom.excom.api.chat import _check_excom_access
from excom.excom.api.reports import get_activity_report

# One accent, used the way the page uses it: headers, and the totals row that reads as the answer.
INK = "1F2933"
ACCENT = "0B6070"
ACCENT_SOFT = "DCEEF1"
BAND = "F4F7F8"
GOOD = "17603F"
WARN = "9C2F2F"
RULE = "CCD5DA"


def _mmss(seconds: int) -> str:
	seconds = int(seconds or 0)
	if seconds < 60:
		return f"{seconds}s"
	if seconds < 3600:
		return f"{seconds // 60}m {seconds % 60:02d}s"
	return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def _columns() -> list:
	"""What a person did, in the order somebody reads it: talking first, then following up."""
	return [
		("Person", 26, lambda r: r["full_name"]),
		("Messages sent", 14, lambda r: r["messages"]["total"]),
		("Calls out", 11, lambda r: r["calls"]["outbound"]),
		("Calls in", 10, lambda r: r["calls"]["inbound"]),
		("Connected", 11, lambda r: r["calls"]["connected"]),
		("Missed", 9, lambda r: r["calls"]["missed"]),
		("Talk time", 12, lambda r: _mmss(r["calls"]["talk_seconds"])),
		("Conversations closed", 20, lambda r: r["closures"]["total"]),
		("Tasks made", 12, lambda r: r["tasks"]["created"]),
		("Tasks done", 12, lambda r: r["tasks"]["completed"]),
		("New records", 13, lambda r: r["records"]["total"]),
		("Active days", 12, lambda r: r["active_days"]),
	]


def _xlsx(report: dict) -> bytes:
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
	from openpyxl.utils import get_column_letter

	wb = Workbook()
	ws = wb.active
	ws.title = "Activity"

	title_font = Font(name="Calibri", size=15, bold=True, color=INK)
	sub_font = Font(name="Calibri", size=10, color="6B7A82")
	head_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
	head_fill = PatternFill("solid", fgColor=ACCENT)
	band_fill = PatternFill("solid", fgColor=BAND)
	total_fill = PatternFill("solid", fgColor=ACCENT_SOFT)
	thin = Side(style="thin", color=RULE)
	edge = Border(bottom=thin)

	cols = _columns()

	ws.cell(row=1, column=1, value="Excom activity report").font = title_font
	ws.cell(
		row=2,
		column=1,
		value="%s · %s to %s · %d %s"
		% (
			report["period"].title(),
			report["from"],
			report["to"],
			report["totals"]["people"],
			"person" if report["totals"]["people"] == 1 else "people",
		),
	).font = sub_font
	ws.cell(row=3, column=1, value="Generated %s" % report["generated_at"]).font = sub_font

	HEAD = 5
	for i, (label, width, _fn) in enumerate(cols, start=1):
		c = ws.cell(row=HEAD, column=i, value=label)
		c.font = head_font
		c.fill = head_fill
		c.alignment = Alignment(horizontal="left" if i == 1 else "right", vertical="center", wrap_text=True)
		ws.column_dimensions[get_column_letter(i)].width = width
	ws.row_dimensions[HEAD].height = 28

	row_no = HEAD
	for n, r in enumerate(report["rows"]):
		row_no = HEAD + 1 + n
		for i, (_label, _w, fn) in enumerate(cols, start=1):
			c = ws.cell(row=row_no, column=i, value=fn(r))
			c.border = edge
			c.alignment = Alignment(horizontal="left" if i == 1 else "right")
			if n % 2:
				c.fill = band_fill
		# Missed calls are the one figure worth catching the eye.
		if r["calls"]["missed"]:
			ws.cell(row=row_no, column=6).font = Font(color=WARN, bold=True)
		if r["closures"]["total"]:
			ws.cell(row=row_no, column=8).font = Font(color=GOOD, bold=True)

	t = report["totals"]
	total_row = row_no + 1
	totals = [
		"All %d" % t["people"], t["messages"]["total"], t["calls"]["outbound"],
		t["calls"]["inbound"], t["calls"]["connected"], t["calls"]["missed"],
		_mmss(t["calls"]["talk_seconds"]), t["closures"]["total"],
		t["tasks"]["created"], t["tasks"]["completed"], t["records"]["total"], "",
	]
	for i, v in enumerate(totals, start=1):
		c = ws.cell(row=total_row, column=i, value=v)
		c.font = Font(bold=True, color=INK)
		c.fill = total_fill
		c.alignment = Alignment(horizontal="left" if i == 1 else "right")

	ws.freeze_panes = ws.cell(row=HEAD + 1, column=1)
	ws.auto_filter.ref = "A%d:%s%d" % (HEAD, get_column_letter(len(cols)), total_row - 1)

	# A second sheet for the breakdowns, so the first one stays readable.
	d = wb.create_sheet("Breakdown")
	d.column_dimensions["A"].width = 26
	d.column_dimensions["B"].width = 22
	d.column_dimensions["C"].width = 26
	d.column_dimensions["D"].width = 12
	for i, label in enumerate(("Person", "Kind", "Detail", "Count"), start=1):
		c = d.cell(row=1, column=i, value=label)
		c.font = head_font
		c.fill = head_fill
	line = 2
	for r in report["rows"]:
		for kind, mapping in (
			("Messages by channel", r["messages"]["by_channel"]),
			("Closed by outcome", r["closures"]["by_outcome"]),
			("New records", r["records"]["by_kind"]),
		):
			for key, value in sorted(mapping.items()):
				d.cell(row=line, column=1, value=r["full_name"])
				d.cell(row=line, column=2, value=kind)
				d.cell(row=line, column=3, value=str(key))
				d.cell(row=line, column=4, value=value).alignment = Alignment(horizontal="right")
				line += 1

	buf = io.BytesIO()
	wb.save(buf)
	return buf.getvalue()


def _html(report: dict) -> str:
	cols = _columns()
	t = report["totals"]

	head = "".join(
		'<th style="text-align:%s">%s</th>' % ("left" if i == 0 else "right", label)
		for i, (label, _w, _f) in enumerate(cols)
	)

	body = []
	for n, r in enumerate(report["rows"]):
		cells = []
		for i, (_label, _w, fn) in enumerate(cols):
			value = fn(r)
			style = "text-align:left" if i == 0 else "text-align:right"
			if i == 5 and r["calls"]["missed"]:
				style += ";color:#9c2f2f;font-weight:600"
			if i == 7 and r["closures"]["total"]:
				style += ";color:#17603f;font-weight:600"
			cells.append('<td style="%s">%s</td>' % (style, value))
		body.append('<tr class="%s">%s</tr>' % ("band" if n % 2 else "", "".join(cells)))

	totals = [
		"All %d" % t["people"], t["messages"]["total"], t["calls"]["outbound"],
		t["calls"]["inbound"], t["calls"]["connected"], t["calls"]["missed"],
		_mmss(t["calls"]["talk_seconds"]), t["closures"]["total"],
		t["tasks"]["created"], t["tasks"]["completed"], t["records"]["total"], "",
	]
	total_row = "".join(
		'<td style="text-align:%s">%s</td>' % ("left" if i == 0 else "right", v)
		for i, v in enumerate(totals)
	)

	return """<!doctype html><html><head><meta charset="utf-8"><style>
	body {{ font-family: Helvetica, Arial, sans-serif; color:#1f2933; font-size:11px; margin:24px; }}
	h1 {{ font-size:18px; margin:0 0 2px; letter-spacing:-.01em; }}
	.sub {{ color:#6b7a82; font-size:10px; margin:0 0 14px; }}
	table {{ border-collapse:collapse; width:100%; }}
	th {{ background:#0b6070; color:#fff; font-size:9px; text-transform:uppercase;
	      letter-spacing:.06em; padding:6px 7px; }}
	td {{ padding:5px 7px; border-bottom:1px solid #ccd5da; }}
	tr.band td {{ background:#f4f7f8; }}
	tr.total td {{ background:#dceef1; font-weight:700; border-bottom:none; }}
	</style></head><body>
	<h1>Excom activity report</h1>
	<p class="sub">{period} &middot; {start} to {end} &middot; {people} {word}<br>
	   Generated {at}</p>
	<table><thead><tr>{head}</tr></thead>
	<tbody>{body}<tr class="total">{total}</tr></tbody></table>
	</body></html>""".format(
		period=report["period"].title(),
		start=report["from"],
		end=report["to"],
		people=t["people"],
		word="person" if t["people"] == 1 else "people",
		at=report["generated_at"],
		head=head,
		body="".join(body),
		total=total_row,
	)


@frappe.whitelist()
def download_activity_report(
	user: str = "", period: str = "daily", on: str = "", fmt: str = "xlsx"
) -> None:
	"""Send the report back as a file.

	Guarded here as well as in `get_activity_report`, which this calls. The second check is
	redundant and stays anyway: a guard one call away cannot be seen by somebody reading the
	endpoint, and this is a download — the place a permission rule is most often left out.
	"""
	_check_excom_access()
	if fmt not in ("xlsx", "pdf"):
		frappe.throw(_("Unknown format: {0}").format(fmt))

	report = get_activity_report(user=user, period=period, on=on)
	who = (user or "everyone").split("@")[0].replace(".", "-")
	stem = "excom-activity-%s-%s-%s" % (period, report["from"], who)

	if fmt == "xlsx":
		frappe.response["filename"] = stem + ".xlsx"
		frappe.response["filecontent"] = _xlsx(report)
		frappe.response["type"] = "binary"
		return

	from frappe.utils.pdf import get_pdf

	frappe.response["filename"] = stem + ".pdf"
	frappe.response["filecontent"] = get_pdf(
		_html(report), options={"orientation": "Landscape", "page-size": "A4"}
	)
	frappe.response["type"] = "pdf"
