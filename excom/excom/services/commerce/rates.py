"""Rate changes → one WhatsApp rate update per price list (HLD-005 D3–D9).

Flow:
  scan()            every 5 min: Item Price rows modified since the last scan, on a catalogue
                    price list, compared with the last rate customers were told (Excom Published
                    Rate). Differences collect in one Draft Excom Rate Update per price list.
  auto_publish()    a draft quiet for `auto_publish_delay_min`, and ≥ `min_hours_between_broadcasts`
                    after the last one on that price list, is published.
  publish()         catalogue sync (Meta shows the new price) → rate card image → one Excom
                    Broadcast per subscriber list on those WhatsApp numbers → Published Rates move on.

The scan reads `modified`, not save hooks, so every way a price changes is seen — the desk,
data import, POS, Webshop, and ERPNext's own db.set_value — and a bulk import of 500 prices is
one batch, not 500 events. A price changed and changed back announces nothing.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, flt, get_datetime, getdate, now_datetime, nowdate

DRAFT = "Draft"


def settings():
	return frappe.get_cached_doc("Excom Commerce Settings")


def catalogue_price_lists() -> list[str]:
	"""Price lists some active WhatsApp number shows in its catalogue."""
	return sorted({pl for pl in frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "whatsapp", "status": "Active", "catalog_price_list": ["is", "set"]},
		pluck="catalog_price_list",
	) if pl})


# ── scheduler ────────────────────────────────────────────────────────────────

def run_scheduler():
	"""cron */5: scan, then publish whatever is due. Cheap no-op when commerce is off."""
	if not frappe.db.exists("DocType", "Excom Commerce Settings"):
		return
	s = settings()
	if not s.enabled:
		return
	try:
		scan()
	except Exception:
		frappe.log_error(title="Excom: rate scan failed", message=frappe.get_traceback())
	try:
		auto_publish()
	except Exception:
		frappe.log_error(title="Excom: rate auto-publish failed", message=frappe.get_traceback())


SCAN_OVERLAP_MINUTES = 15


def scan(since=None) -> dict:
	"""Collect rate changes into Draft Rate Updates. Returns {price_list: changed item codes}.

	Candidates are items whose Item Price was modified since the last scan (with an overlap, so
	a long import that committed late is not missed — comparing again is harmless), plus items
	whose price became valid or expired since then (a scheduled price change has no new
	`modified`). For each candidate the price ERPNext would use today is worked out and compared
	with the last rate customers were told.
	"""
	started = now_datetime()
	price_lists = catalogue_price_lists()
	if not price_lists:
		return {}
	s = settings()
	since = get_datetime(since or s.last_scan_at) if (since or s.last_scan_at) else None
	out = {}
	for pl in price_lists:
		if not frappe.db.exists("Excom Published Rate", {"price_list": pl}):
			# First time this price list is watched: today's prices are the baseline, nothing is
			# announced (otherwise the first scan would broadcast every item).
			_baseline(pl)
			continue
		changed = _collect(pl, _candidates(pl, since))
		if changed:
			out[pl] = changed
	frappe.db.set_single_value("Excom Commerce Settings", "last_scan_at", started)
	frappe.db.commit()
	return out


def _candidates(price_list: str, since) -> set:
	if not since:
		return set(frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1}, pluck="item_code"))
	window = add_to_date(since, minutes=-SCAN_OVERLAP_MINUTES)
	items = set(frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1, "modified": [">", window]}, pluck="item_code"))
	last_day, today = getdate(since), getdate(nowdate())
	if today > last_day:
		# became valid, or stopped being valid, on a day since the last scan
		items |= set(frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1,
			"valid_from": ["between", [add_days(last_day, 1), today]]}, pluck="item_code"))
		items |= set(frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1,
			"valid_upto": ["between", [last_day, add_days(today, -1)]]}, pluck="item_code"))
	return items


def _applies(row) -> bool:
	"""A general price (not for one customer, supplier or batch) that is valid today."""
	today = getdate(nowdate())
	if row.get("customer") or row.get("supplier") or row.get("batch_no"):
		return False
	if row.get("valid_from") and getdate(row.valid_from) > today:
		return False
	if row.get("valid_upto") and getdate(row.valid_upto) < today:
		return False
	return True


def current_prices(price_list: str, items) -> dict:
	"""{(item_code, uom): (rate, item_name)} — the price ERPNext would use today, per UOM.

	Several rows can apply at once (no valid_from, and one from today): like ERPNext's
	get_item_price, the one with the latest valid_from wins.
	"""
	items = list(items or [])
	if not items:
		return {}
	rows = frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1, "item_code": ["in", items]},
		fields=["item_code", "item_name", "uom", "price_list_rate", "customer", "supplier", "batch_no", "valid_from", "valid_upto"])
	best = {}
	for r in rows:
		if not _applies(r):
			continue
		key = (r.item_code, _uom(r))
		vf = getdate(r.valid_from) if r.valid_from else getdate("1900-01-01")
		if key not in best or vf > best[key][0]:
			best[key] = (vf, flt(r.price_list_rate), r.item_name)
	return {k: (v[1], v[2]) for k, v in best.items()}


def _uom(row) -> str:
	return row.get("uom") or frappe.db.get_value("Item", row.item_code, "stock_uom") or ""


def _baseline(price_list: str) -> None:
	now = now_datetime()
	items = frappe.get_all("Item Price", filters={"price_list": price_list, "selling": 1}, pluck="item_code")
	prices = current_prices(price_list, set(items))
	for n, ((code, uom), (rate, _name)) in enumerate(prices.items(), start=1):
		_set_published(code, price_list, uom, rate, now, None)
		if n % 500 == 0:
			frappe.db.commit()  # a large price list must not time out and restart forever


def _published(item_code: str, price_list: str, uom: str):
	return frappe.db.get_value("Excom Published Rate", {"item_code": item_code, "price_list": price_list, "uom": uom}, ["name", "rate"], as_dict=True)


def _set_published(item_code, price_list, uom, rate, when, rate_update) -> None:
	existing = _published(item_code, price_list, uom)
	if existing:
		frappe.db.set_value("Excom Published Rate", existing.name, {"rate": rate, "published_on": when, "rate_update": rate_update}, update_modified=False)
	else:
		frappe.get_doc({"doctype": "Excom Published Rate", "item_code": item_code, "price_list": price_list, "uom": uom,
			"rate": rate, "published_on": when, "rate_update": rate_update}).insert(ignore_permissions=True)


def _draft(price_list: str):
	name = frappe.db.get_value("Excom Rate Update", {"price_list": price_list, "status": DRAFT}, "name")
	if name:
		return frappe.get_doc("Excom Rate Update", name)
	return frappe.get_doc({"doctype": "Excom Rate Update", "price_list": price_list, "status": DRAFT})


def _collect(price_list: str, items) -> list:
	"""Add / update / drop rows of the price list's draft for these items. Returns changed item codes."""
	items = set(items or [])
	if not items:
		return []
	prices = current_prices(price_list, items)
	doc = _draft(price_list)
	by_key = {(i.item_code, i.uom or ""): i for i in doc.items}
	touched = []
	for (code, uom), (new, item_name) in prices.items():
		pub = _published(code, price_list, uom)
		old = flt(pub.rate) if pub else None
		row = by_key.get((code, uom))
		if old is not None and abs(new - old) < 0.005:
			if row:  # changed and changed back: nothing to announce
				doc.remove(row)
				by_key.pop((code, uom), None)
				touched.append(code)
			continue
		if not row:
			row = doc.append("items", {"item_code": code, "uom": uom})
			by_key[(code, uom)] = row
		row.item_name = item_name or frappe.db.get_value("Item", code, "item_name")
		row.old_rate = old or 0
		row.new_rate = new
		row.change = new - (old or 0)
		row.is_new = 1 if old is None else 0
		touched.append(code)
	if touched:
		doc.last_change_on = now_datetime()
		if doc.items or not doc.is_new():
			doc.save(ignore_permissions=True)
	return touched


# ── publishing ───────────────────────────────────────────────────────────────

def auto_publish() -> list[str]:
	s = settings()
	delay = int(s.auto_publish_delay_min or 0)
	if delay <= 0:
		return []
	queued = []
	for d in frappe.get_all("Excom Rate Update", filters={"status": DRAFT}, fields=["name", "price_list", "last_change_on"]):
		if not d.last_change_on or get_datetime(d.last_change_on) > add_to_date(now_datetime(), minutes=-delay):
			continue
		if not frappe.db.count("Excom Rate Update Item", {"parent": d.name}):
			continue
		if _too_soon(d.price_list):
			continue
		queue_publish(d.name)
		queued.append(d.name)
	return queued


def _too_soon(price_list: str) -> bool:
	hours = flt(settings().min_hours_between_broadcasts)
	if hours <= 0:
		return False
	last = frappe.db.get_value("Excom Rate Update", {"price_list": price_list, "status": "Published"}, "published_on", order_by="published_on desc")
	return bool(last) and get_datetime(last) > add_to_date(now_datetime(), hours=-hours)


def queue_publish(name: str, manual: bool = False) -> dict:
	"""Move Draft (or Failed, to retry) → Syncing exactly once, then run publish in the background."""
	frappe.db.sql("""UPDATE `tabExcom Rate Update` SET status = 'Syncing', error = NULL
		WHERE name = %s AND status IN ('Draft', 'Failed')""", name)
	if not frappe.db.sql("SELECT ROW_COUNT()")[0][0]:
		return {"queued": "", "warning": _("{0} is already being published.").format(name)}
	frappe.db.commit()
	frappe.enqueue("excom.excom.services.commerce.rates.publish", name=name, manual=manual,
		queue="long", timeout=3600, now=frappe.flags.in_test, enqueue_after_commit=not frappe.flags.in_test)
	price_list = frappe.db.get_value("Excom Rate Update", name, "price_list")
	warning = _("The last rate update on this price list went out less than {0} hours ago.").format(settings().min_hours_between_broadcasts) if manual and _too_soon(price_list) else ""
	return {"queued": name, "warning": warning}


def accounts_for(price_list: str) -> list:
	return [frappe.get_doc("Excom Channel Account", n) for n in frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "whatsapp", "status": "Active", "catalog_price_list": price_list},
		pluck="name",
	)]


def publish(name: str, manual: bool = False) -> None:
	"""Catalogue first (the carousel and cart read prices from Meta), then the broadcast."""
	from excom.excom.services.commerce import catalog, rate_card

	doc = frappe.get_doc("Excom Rate Update", name)
	if doc.status != "Syncing":
		return  # queue_publish moves it to Syncing exactly once
	try:
		accounts = accounts_for(doc.price_list)
		if not accounts:
			raise frappe.ValidationError(_("No active WhatsApp number shows price list {0} in its catalogue.").format(doc.price_list))

		failed = []
		synced_any = False
		lists = []
		for acc in accounts:
			for lst in frappe.get_all("Excom Subscriber List", filters={"channel_account": acc.name}, fields=["name", "item_group"]):
				if not lst.item_group or any(_in_group(i.item_code, lst.item_group) for i in doc.items):
					lists.append((acc, lst.name))
		if not lists:
			raise frappe.ValidationError(_("No subscriber list is set up for the WhatsApp number(s) showing {0}; nothing was sent.").format(doc.price_list))

		statuses = {}
		for acc in accounts:
			if acc.get("wa_catalog_id"):
				synced_any = True
				result = catalog.sync_items(acc, catalogue_rates(doc), doc.price_list, rate_update=doc.name)
				for i in doc.items:
					st = result.get(i.item_code, "ok")
					if not str(statuses.get(i.item_code, "")).startswith("error"):  # an error on any number sticks
						statuses[i.item_code] = st
				if result.get("_status") == "Failed":
					failed.append(acc.name)
		for i in doc.items:
			i.catalog_status = statuses.get(i.item_code, "")
		if failed and len(failed) == len([a for a in accounts if a.get("wa_catalog_id")]):
			raise frappe.ValidationError(_("Catalogue sync failed for {0}; nothing was sent. See Excom Catalog Sync Log.").format(", ".join(failed)))
		if synced_any and int(settings().catalogue_settle_seconds or 0) and not frappe.flags.in_test:
			import time

			time.sleep(int(settings().catalogue_settle_seconds))  # let Meta apply the new prices before people tap

		doc.summary, doc.more_count = summary(doc)
		doc.rate_card_image = rate_card.render(doc)
		doc.status = "Sending"
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		broadcasts = [_broadcast(doc, acc, lst, wait=idx > 0) for idx, (acc, lst) in enumerate(lists)]

		when = now_datetime()
		for i in doc.items:
			_set_published(i.item_code, doc.price_list, i.uom or "", flt(i.new_rate), when, doc.name)
		_rebase_open_draft(doc)
		doc.reload()
		doc.status = "Published"
		doc.published_on = when
		doc.broadcasts = ", ".join(b for b in broadcasts if b)
		doc.error = ""
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.db.set_value("Excom Rate Update", name, {"status": "Failed", "error": str(e)[:1000]})
		frappe.db.commit()
		frappe.log_error(title=f"Excom: rate update {name} failed", message=frappe.get_traceback())


def _rebase_open_draft(published) -> None:
	"""After a publish, measure the open Draft against what was just told — and catch a price that
	changed again (or back) while this update was publishing, which no scan would see otherwise."""
	just = {(i.item_code, i.uom or ""): flt(i.new_rate) for i in published.items}
	now = current_prices(published.price_list, {k[0] for k in just})
	drifted = [code for (code, uom), rate in just.items() if (code, uom) in now and abs(now[(code, uom)][0] - rate) >= 0.005]
	name = frappe.db.get_value("Excom Rate Update", {"price_list": published.price_list, "status": DRAFT}, "name")
	if name:
		draft = frappe.get_doc("Excom Rate Update", name)
		changed = False
		for row in list(draft.items):
			key = (row.item_code, row.uom or "")
			if key not in just:
				continue
			row.old_rate, row.is_new = just[key], 0
			row.change = flt(row.new_rate) - just[key]
			if abs(row.change) < 0.005:
				draft.remove(row)
			changed = True
		if changed:
			draft.save(ignore_permissions=True)
	if drifted:
		_collect(published.price_list, drifted)


def _in_group(item_code: str, item_group: str) -> bool:
	group = frappe.db.get_value("Item", item_code, "item_group")
	if not group:
		return False
	if group == item_group:
		return True
	lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (0, 0)
	g = frappe.db.get_value("Item Group", group, ["lft", "rgt"]) or (0, 0)
	return lft <= g[0] and g[1] <= rgt


def _symbol(currency: str) -> str:
	return frappe.db.get_value("Currency", currency, "symbol") or (currency + " " if currency else "")


def summary(doc) -> tuple[str, int]:
	"""One line for the template body (WhatsApp variables cannot contain line breaks)."""
	n = max(int(settings().summary_items or 3), 1)
	items = sorted(doc.items, key=lambda i: abs(flt(i.change)), reverse=True)
	sym = _symbol(doc.currency or frappe.db.get_value("Price List", doc.price_list, "currency") or "")
	parts = []
	for i in items[:n]:
		if i.is_new:
			parts.append(f"{i.item_name or i.item_code} {sym}{flt(i.new_rate):,.2f} (new)")
		else:
			arrow = "▲" if flt(i.change) > 0 else "▼"
			parts.append(f"{i.item_name or i.item_code} {sym}{flt(i.new_rate):,.2f} ({arrow}{abs(flt(i.change)):,.2f})")
	text = " · ".join(parts)
	shown = len(parts)
	while len(text) > 700 and shown > 1:  # the body is ≤ 1024 with the template's own words
		shown -= 1
		text = " · ".join(parts[:shown])
	return text, max(len(items) - shown, 0)


def button_payloads(name: str) -> list[str]:
	"""Quick-reply payloads in template button order: View & Order, Full Catalogue, Ask Best Price."""
	return [f"RU:{name}:view", f"RU:{name}:catalog", f"RU:{name}:ask"]


def catalogue_rates(doc) -> list:
	"""One price per item for Meta (the catalogue has no UOMs): the stock-UOM row, else the first."""
	out = {}
	for i in doc.items:
		stock = frappe.db.get_value("Item", i.item_code, "stock_uom")
		if i.item_code not in out or (i.uom or "") == stock:
			out[i.item_code] = flt(i.new_rate)
	return list(out.items())


def _broadcast(doc, account, subscriber_list: str, wait: bool = False) -> str:
	s = settings()
	if not s.rate_update_template:
		raise frappe.ValidationError(_("Set the Rate Update Template in Excom Commerce Settings."))
	template = frappe.get_doc("WhatsApp Templates", s.rate_update_template)
	date = frappe.utils.formatdate(nowdate(), "d MMM")
	# never empty or whitespace-only: Meta refuses an empty parameter
	more = _("and {0} more").format(doc.more_count) if doc.more_count else _("all changes shown")
	if template.uses_named_parameters():
		variables = {"date": date, "summary": doc.summary, "more": more}
	else:
		from excom.excom.whatsapp_template_utils import body_variable_slot_count

		variables = [date, doc.summary, more][: body_variable_slot_count(template)]
	bc = frappe.get_doc({
		"doctype": "Excom Broadcast",
		"broadcast_name": f"{doc.name} · {subscriber_list}",
		"subscriber_list": subscriber_list,
		"channel": "WhatsApp",
		"wa_channel_account": account.name,
		"wa_template": template.name,
		"wa_language_code": template.language_code,
		"wa_variable_mode": "same_for_all",
		"wa_template_variables": json.dumps(variables, ensure_ascii=False),
		"wa_header_media": doc.rate_card_image if (template.header_type or "").upper() == "IMAGE" else "",
		"wa_button_payloads": json.dumps(button_payloads(doc.name)),
		"reference_doctype": "Excom Rate Update",
		"reference_name": doc.name,
		"wave_size": s.wave_size or 0,
		"wave_pause_seconds": s.wave_pause_seconds or 0,
	})
	bc.insert(ignore_permissions=True)
	bc.flags.excom_wait = wait  # later lists start when the previous one is done (no duplicates)
	bc.submit()
	return bc.name


# ── desk actions ─────────────────────────────────────────────────────────────

@frappe.whitelist()
def scan_now() -> dict:
	from excom.excom.api.chat import _check_manager_access

	_check_manager_access()
	return scan()
