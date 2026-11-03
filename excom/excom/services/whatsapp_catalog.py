"""ERPNext items → the WhatsApp number's Meta catalogue.

One catalogue per WhatsApp account, one public price per item (the account's Catalogue Price
List). Product page URL, brand and nightly sync are set on the Channel Account. Updates go through POST /{catalog_id}/items_batch (the old /batch endpoint is gone from
v27): up to 3,000 requests per call, UPDATE with allow_upsert so a missing item is created.

retailer_id = Item.item_code (stable, never reused). Meta requires a product page link on your
own domain and an image URL Meta can fetch; a changed image needs a new URL, so the image link
carries the file's modified time.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import flt, get_url, getdate, nowdate

BATCH = 3000


def _creds(account):
	from excom.excom.services.whatsapp_service import _resolve_credentials

	token, base_url, version, _phone = _resolve_credentials(account)
	return token, base_url, version


class NotListable(Exception):
	"""The item lacks something Meta requires (page link, public image, brand)."""


def item_payload(item_code: str, rate: float, currency: str, account=None) -> dict | None:
	"""Meta catalogue fields for one item (None when the item does not exist).

	items_batch PRODUCT_ITEM requires id, title, description, availability, condition, price,
	link, image_link and brand; an item missing one is refused here with NotListable instead of
	being rejected by Meta. Title ≤ 100, description ≤ 5000 characters.
	"""
	item = frappe.db.get_value("Item", item_code, ["item_name", "description", "image", "brand", "item_group", "disabled", "is_sales_item", "modified"], as_dict=True)
	if not item:
		return None
	s = account or frappe._dict()
	brand = (item.brand or s.get("catalog_brand") or s.get("company") or frappe.defaults.get_global_default("company") or "").strip()
	url_template = s.get("catalog_product_url")
	if not url_template:
		raise NotListable(_("set the Product page URL on the WhatsApp Channel Account"))
	image = _public_image(item)
	if not image:
		raise NotListable(_("needs a public image (Meta must be able to fetch it)"))
	if not brand:
		raise NotListable(_("needs a brand"))
	from urllib.parse import quote

	data = {
		"id": item_code,
		"title": (item.item_name or item_code)[:100],
		"description": (frappe.utils.strip_html(item.description or "") or item.item_name or item_code)[:5000],
		"availability": "out of stock" if (item.disabled or not item.is_sales_item) else "in stock",
		"condition": "new",
		"price": f"{flt(rate):.2f} {currency}",
		"brand": brand[:100],
		"link": url_template.replace("{item_code}", quote(item_code, safe="")),
		"image_link": image,
		"internal_label": [item.item_group] if item.item_group else [],
		"wa_compliance_category": "DEFAULT",
		"origin_country": (s.get("catalog_origin_country") or "IN").upper(),
	}
	return data


def _public_image(item) -> str:
	"""A URL Meta can fetch, changing whenever the image changes ('' for private or missing images)."""
	url = item.image or ""
	if not url or url.startswith("/private/"):
		return ""
	if url.startswith("/"):
		url = get_url(url)
	stamp = frappe.utils.get_datetime(item.modified).strftime("%Y%m%d%H%M%S")
	return f"{url}{'&' if '?' in url else '?'}v={stamp}"


def sync_items(account, items: list, price_list: str) -> dict:
	"""UPDATE these (item_code, rate) pairs in the account's catalogue and wait for Meta's verdict.

	Returns {item_code: "ok" | "error: …", "_status": "Finished" | "Partly Failed" | "Failed"}.
	"""
	if not account.get("wa_catalog_id"):
		return {"_status": "Failed"}
	currency = frappe.db.get_value("Price List", price_list, "currency") or "INR"
	requests_, skipped = [], {}
	for item_code, rate in items:
		try:
			data = item_payload(item_code, rate, currency, account)
		except NotListable as e:
			skipped[item_code] = f"error: {e}"
			continue
		if data is None:
			skipped[item_code] = "error: item not found"
			continue
		requests_.append({"method": "UPDATE", "data": data})
	out = dict(skipped)
	statuses = []
	for start in range(0, len(requests_), BATCH):
		chunk = requests_[start:start + BATCH]
		out.update(_send_batch(account, chunk, statuses))
	if not requests_:
		statuses.append("Failed")
	out["_status"] = "Failed" if statuses and all(s == "Failed" for s in statuses) else ("Partly Failed" if "Failed" in statuses or "Partly Failed" in statuses else "Finished")
	return out


def _send_batch(account, chunk: list, statuses: list) -> dict:
	import requests

	token, base_url, version = _creds(account)
	url = f"{base_url}/{version}/{account.wa_catalog_id}/items_batch"
	log = frappe.get_doc({"doctype": "Excom Catalog Sync Log", "channel_account": account.name, "catalog_id": account.wa_catalog_id,
		"status": "Sent", "items_sent": len(chunk)})
	result = {}
	try:
		# form-encoded with `requests` as a JSON string, as in every Meta example
		form = {"item_type": "PRODUCT_ITEM", "allow_upsert": "true", "requests": json.dumps(chunk, ensure_ascii=False)}
		for attempt in (1, 2):
			resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, timeout=(10, 120), data=form)
			body = resp.json() if resp.content else {}
			if (body.get("error") or {}).get("code") == 80014 and attempt == 1:
				_pause(60)  # too many catalogue batch calls: back off once
				continue
			break
		if resp.status_code >= 400:
			raise frappe.ValidationError((body.get("error") or {}).get("message") or resp.text[:300])
		handles = body.get("handles") or []
		for v in body.get("validation_status") or []:
			errors = v.get("errors") or []
			if errors:
				result[v.get("retailer_id")] = "error: " + "; ".join(e.get("message", "") for e in errors)[:200]
		finished = _wait(account, handles, token, base_url, version)
		for rid, err in finished.get("errors", {}).items():
			result[rid] = "error: " + err
		for item in chunk:
			# not finished in time: unknown, so not "ok"
			result.setdefault(item["data"]["id"], "ok" if finished.get("done") else "error: Meta is still processing the batch")
		batch_error = result.pop("_batch", None)
		if batch_error:
			for item in chunk:
				result[item["data"]["id"]] = f"error: {batch_error}"
		failed = sum(1 for k, v in result.items() if not k.startswith("_") and str(v).startswith("error"))
		log.status = "Failed" if failed == len(chunk) else ("Partly Failed" if failed else "Finished")
		log.items_failed = failed
		log.handles = ", ".join(handles)
		log.result = json.dumps({"response": body, "status": finished.get("raw")}, ensure_ascii=False)[:60000]
	except Exception as e:
		log.status = "Failed"
		log.items_failed = len(chunk)
		log.result = json.dumps({"error": str(e)})
		for item in chunk:
			result[item["data"]["id"]] = f"error: {str(e)[:150]}"
	log.insert(ignore_permissions=True)
	frappe.db.commit()  # kept even if the caller fails and rolls back
	statuses.append(log.status)
	return result


# ── today's prices ───────────────────────────────────────────────────────────

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
		key = (r.item_code, r.get("uom") or frappe.db.get_value("Item", r.item_code, "stock_uom") or "")
		vf = getdate(r.valid_from) if r.valid_from else getdate("1900-01-01")
		if key not in best or vf > best[key][0]:
			best[key] = (vf, flt(r.price_list_rate), r.item_name)
	return {k: (v[1], v[2]) for k, v in best.items()}


_TERMINAL = {"finished", "error", "canceled", "cancelled"}


def _pause(seconds: int) -> None:
	if not frappe.flags.in_test:
		time.sleep(seconds)


def _wait(account, handles: list, token: str, base_url: str, version: str, tries: int = 36, pause: int = 5) -> dict:
	"""Poll check_batch_request_status until every handle is Finished / Error / Canceled (≤ 3 min).

	`ids_of_invalid_requests` is the full list of rejected retailer ids; `errors` is only a
	sample (entries carry id / line / message). "Finished" still means rejected rows were not saved.
	"""
	import requests

	if not handles:
		return {"done": True, "errors": {}, "raw": []}
	raw, errors = [], {}
	for _attempt in range(tries):
		raw, done = [], True
		for h in handles:
			try:
				r = requests.get(f"{base_url}/{version}/{account.wa_catalog_id}/check_batch_request_status",
					params={"handle": h, "load_ids_of_invalid_requests": "true",
						"fields": "handle,status,errors,errors_total_count,warnings,ids_of_invalid_requests"},
					headers={"Authorization": f"Bearer {token}"}, timeout=(10, 30))
				data = ((r.json() or {}).get("data") or [{}])[0] if r.content else {}
			except Exception:
				data = {}
			raw.append(data)
			status = (data.get("status") or "").lower()
			if status not in _TERMINAL:
				done = False
				continue
			if status in ("error", "canceled", "cancelled"):
				errors["_batch"] = f"batch {status}"
			sample = {str(e.get("id") or e.get("retailer_id") or ""): (e.get("message") or "rejected")[:200] for e in data.get("errors") or []}
			for rid in data.get("ids_of_invalid_requests") or []:
				errors[str(rid)] = sample.get(str(rid), "rejected by Meta")
			for rid, msg in sample.items():
				if rid:
					errors.setdefault(rid, msg)
		if done:
			return {"done": True, "errors": errors, "raw": raw}
		_pause(pause)
	return {"done": False, "errors": errors, "raw": raw}


def sync_all(account) -> dict:
	"""Every general, currently valid price on the account's Catalogue Price List."""
	pl = account.get("catalog_price_list")
	if not pl or not account.get("wa_catalog_id"):
		return {}
	codes = set(frappe.get_all("Item Price", filters={"price_list": pl, "selling": 1}, pluck="item_code"))
	items = {}
	for (code, uom), (rate, _name) in current_prices(pl, codes).items():
		# one price per item in the catalogue: the stock-UOM price when there are several
		if code not in items or uom == frappe.db.get_value("Item", code, "stock_uom"):
			items[code] = rate
	return sync_items(account, list(items.items()), pl)


def nightly_sync():
	"""daily: full catalogue reconcile for every WhatsApp number that asks for it."""
	for name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "status": "Active", "catalog_nightly_sync": 1,
			"wa_catalog_id": ["is", "set"], "catalog_price_list": ["is", "set"]}, pluck="name"):
		try:
			sync_all(frappe.get_doc("Excom Channel Account", name))
			frappe.db.commit()
		except Exception:
			frappe.log_error(title=f"Excom: catalogue sync failed for {name}", message=frappe.get_traceback())


@frappe.whitelist()
def sync_now(account: str) -> dict:
	"""Admin: push the whole catalogue price list to Meta now."""
	from excom.excom.api.chat import _check_admin_access

	_check_admin_access()
	# minutes of Meta round trips: a background job, not a web request
	frappe.enqueue("excom.excom.services.whatsapp_catalog._sync_account", account=account, queue="long", timeout=3600, enqueue_after_commit=True)
	return {"queued": account}


def _sync_account(account: str) -> None:
	sync_all(frappe.get_doc("Excom Channel Account", account))
	frappe.db.commit()
