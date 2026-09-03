"""IndiaMART — pull (authoritative, 5 min, 5-min overlap) + push accelerator. [vendor] shapes; map via field_map."""

import requests
import frappe
from frappe.utils import add_to_date, now_datetime, get_datetime

from excom.excom.services.intake import ingest

API = "https://mapi.indiamart.com/wservce/crm/crmListing/v2/"


def _key(src) -> str:
	return src.get_password("api_key", raise_exception=False) or ""


def pull(src) -> dict:
	key = _key(src)
	if not key:
		frappe.throw("IndiaMART CRM key missing on the intake source")
	end = now_datetime()
	start = add_to_date(get_datetime(src.last_success_at), minutes=-5) if src.last_success_at else add_to_date(end, days=-1)
	fmt = "%d-%b-%Y %H:%M:%S"
	url = (src.api_url or API).strip()
	resp = requests.get(url, params={"glusr_crm_key": key, "start_time": start.strftime(fmt), "end_time": end.strftime(fmt)}, timeout=30)
	if resp.status_code != 200:
		raise frappe.ValidationError(f"IndiaMART HTTP {resp.status_code}: {resp.text[:300]}")
	data = resp.json()
	rows = data.get("RESPONSE") if isinstance(data, dict) else data
	if isinstance(rows, dict):
		rows = [rows]
	n = dup = 0
	for row in rows or []:
		if not isinstance(row, dict):
			continue
		qid = row.get("UNIQUE_QUERY_ID") or row.get("QUERY_ID")
		if not qid:
			continue
		r = ingest(src, f"indiamart:{qid}", row)
		n += 1
		dup += 1 if r["duplicate"] else 0
	return {"fetched": len(rows or []), "ingested": n, "duplicates": dup}


def push(src, payload: dict) -> dict:
	"""Guest POST body from the seller-panel push URL. Same pipeline; the poller self-heals misses."""
	row = payload.get("RESPONSE", payload) if isinstance(payload, dict) else {}
	qid = row.get("UNIQUE_QUERY_ID") or row.get("QUERY_ID")
	if not qid:
		frappe.throw("UNIQUE_QUERY_ID missing")
	return ingest(src, f"indiamart:{qid}", row)
