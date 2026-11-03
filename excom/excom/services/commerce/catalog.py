"""ERPNext items → the WhatsApp number's Meta catalogue (HLD-005 §7).

One catalogue per WhatsApp account, one public price per item (the account's Catalogue Price
List). Updates go through POST /{catalog_id}/items_batch (the old /batch endpoint is gone from
v27): up to 3,000 requests per call, UPDATE with allow_upsert so a missing item is created.

retailer_id = Item.item_code (stable, never reused). Meta requires a product page link on your
own domain and an image URL Meta can fetch; a changed image needs a new URL, so the image link
carries the file's modified time.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import flt, get_url

BATCH = 3000


def _creds(account):
	from excom.excom.services.whatsapp_service import _resolve_credentials

	token, base_url, version, _phone = _resolve_credentials(account)
	return token, base_url, version


class NotListable(Exception):
	"""The item lacks something Meta requires (page link, public image, brand)."""


def item_payload(item_code: str, rate: float, currency: str) -> dict | None:
	"""Meta catalogue fields for one item (None when the item does not exist).

	items_batch PRODUCT_ITEM requires id, title, description, availability, condition, price,
	link, image_link and brand; an item missing one is refused here with NotListable instead of
	being rejected by Meta. Title ≤ 100, description ≤ 5000 characters.
	"""
	from excom.excom.services.commerce.rates import settings

	item = frappe.db.get_value("Item", item_code, ["item_name", "description", "image", "brand", "item_group", "disabled", "is_sales_item", "modified"], as_dict=True)
	if not item:
		return None
	s = settings()
	brand = (item.brand or s.brand or frappe.defaults.get_global_default("company") or "").strip()
	if not s.product_url_template:
		raise NotListable(_("set the Product page URL in Excom Commerce Settings"))
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
		"link": s.product_url_template.replace("{item_code}", quote(item_code, safe="")),
		"image_link": image,
		"internal_label": [item.item_group] if item.item_group else [],
		"wa_compliance_category": "DEFAULT",
		"origin_country": (s.origin_country or "IN").upper(),
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


def sync_items(account, items: list, price_list: str, rate_update: str = "") -> dict:
	"""UPDATE these (item_code, rate) pairs in the account's catalogue and wait for Meta's verdict.

	Returns {item_code: "ok" | "error: …", "_status": "Finished" | "Partly Failed" | "Failed"}.
	"""
	if not account.get("wa_catalog_id"):
		return {"_status": "Failed"}
	currency = frappe.db.get_value("Price List", price_list, "currency") or "INR"
	requests_, skipped = [], {}
	for item_code, rate in items:
		try:
			data = item_payload(item_code, rate, currency)
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
		out.update(_send_batch(account, chunk, rate_update, statuses))
	if not requests_:
		statuses.append("Failed")
	out["_status"] = "Failed" if statuses and all(s == "Failed" for s in statuses) else ("Partly Failed" if "Failed" in statuses or "Partly Failed" in statuses else "Finished")
	return out


def _send_batch(account, chunk: list, rate_update: str, statuses: list) -> dict:
	import requests

	token, base_url, version = _creds(account)
	url = f"{base_url}/{version}/{account.wa_catalog_id}/items_batch"
	log = frappe.get_doc({"doctype": "Excom Catalog Sync Log", "channel_account": account.name, "catalog_id": account.wa_catalog_id,
		"rate_update": rate_update or None, "status": "Sent", "items_sent": len(chunk)})
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
			# not finished in time: unknown, so not "ok" — the publish waits for a clear answer
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
	frappe.db.commit()  # kept even if the publish that asked for it fails and rolls back
	statuses.append(log.status)
	return result


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
	from excom.excom.services.commerce.rates import current_prices

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
	"""daily: full catalogue reconcile for every WhatsApp number with a catalogue."""
	if not frappe.db.exists("DocType", "Excom Commerce Settings") or not frappe.db.get_single_value("Excom Commerce Settings", "enabled"):
		return
	for name in frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp", "status": "Active", "wa_catalog_id": ["is", "set"], "catalog_price_list": ["is", "set"]}, pluck="name"):
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
	frappe.enqueue("excom.excom.services.commerce.catalog._sync_account", account=account, queue="long", timeout=3600, enqueue_after_commit=True)
	return {"queued": account}


def _sync_account(account: str) -> None:
	sync_all(frappe.get_doc("Excom Channel Account", account))
	frappe.db.commit()
