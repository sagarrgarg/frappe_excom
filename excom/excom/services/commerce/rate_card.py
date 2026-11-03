"""The rate card: the image header of the rate update template (HLD-005 D9).

WhatsApp template variables cannot contain line breaks, so the table of changes travels as an
image: title, date, then up to 12 items with old → new rate and the change. 1080 px wide
(WhatsApp shows headers at about 1.91:1 or taller; the height grows with the rows).
"""

import io
import os

import frappe
from frappe.utils import flt, formatdate, nowdate

WIDTH = 1080
ROW = 64
MAX_ROWS = 12
_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF")


def _font(size: int, bold: bool = False):
	from PIL import ImageFont

	name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
	for d in _FONT_DIRS:
		path = os.path.join(d, name)
		if os.path.exists(path):
			return ImageFont.truetype(path, size), True
	return ImageFont.load_default(), False


def _money(value: float, symbol: str, unicode_ok: bool) -> str:
	if symbol == "₹" and not unicode_ok:
		symbol = "Rs."
	return f"{symbol}{flt(value):,.2f}"


def render(doc) -> str:
	"""Draw the card for an Excom Rate Update and attach it. Returns the file URL ('' on failure)."""
	try:
		from PIL import Image, ImageDraw
	except Exception:
		frappe.log_error(title="Excom: Pillow missing — rate card not drawn")
		return ""
	from excom.excom.services.commerce.rates import _symbol

	items = sorted(doc.items, key=lambda i: abs(flt(i.change)), reverse=True)[:MAX_ROWS]
	more = max(len(doc.items) - len(items), 0)
	symbol = _symbol(doc.currency or frappe.db.get_value("Price List", doc.price_list, "currency") or "")
	title_font, ok = _font(46, bold=True)
	head_font, _ = _font(28, bold=True)
	body_font, _ = _font(30)
	small_font, _ = _font(24)

	height = 200 + ROW * (len(items) + 1) + (60 if more else 0) + 40
	img = Image.new("RGB", (WIDTH, height), "white")
	draw = ImageDraw.Draw(img)
	company = frappe.defaults.get_global_default("company") or ""
	draw.rectangle([0, 0, WIDTH, 150], fill=(18, 64, 120))
	draw.text((48, 30), "Rates updated", font=title_font, fill="white")
	draw.text((48, 96), f"{formatdate(nowdate(), 'd MMMM yyyy')}  ·  {company}".strip(" ·"), font=small_font, fill=(210, 225, 245))

	cols = (48, 560, 760, 930)
	y = 180
	for x, label in zip(cols, ("Item", "Was", "Now", "Change")):
		draw.text((x, y), label, font=head_font, fill=(90, 90, 90))
	y += ROW
	for idx, i in enumerate(items):
		if idx % 2 == 0:
			draw.rectangle([24, y - 10, WIDTH - 24, y + ROW - 14], fill=(244, 247, 251))
		name = (i.item_name or i.item_code)
		name = name if len(name) <= 26 else name[:25] + "…"
		draw.text((cols[0], y), name, font=body_font, fill=(20, 20, 20))
		draw.text((cols[1], y), "new" if i.is_new else _money(i.old_rate, symbol, ok), font=body_font, fill=(110, 110, 110))
		draw.text((cols[2], y), _money(i.new_rate, symbol, ok), font=body_font, fill=(20, 20, 20))
		if not i.is_new:
			up = flt(i.change) > 0
			arrow = ("▲" if up else "▼") if ok else ("+" if up else "-")
			draw.text((cols[3], y), f"{arrow}{abs(flt(i.change)):,.2f}", font=body_font, fill=(190, 40, 40) if up else (20, 130, 60))
		y += ROW
	if more:
		draw.text((cols[0], y + 4), f"+ {more} more item(s) — tap View & Order", font=small_font, fill=(90, 90, 90))

	buf = io.BytesIO()
	img.save(buf, format="PNG", optimize=True)
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": f"{doc.name}-rate-card.png",
		"attached_to_doctype": "Excom Rate Update",
		"attached_to_name": doc.name,
		"attached_to_field": "rate_card_image",
		"is_private": 1,
		"content": buf.getvalue(),
	}).insert(ignore_permissions=True)
	return f.file_url
