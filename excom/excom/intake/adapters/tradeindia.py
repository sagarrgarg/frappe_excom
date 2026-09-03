"""TradeIndia — pull only. Host/path come from the seller panel (stored on the source), never hardcoded. [vendor]"""

import requests
import frappe
from frappe.utils import add_to_date, now_datetime, get_datetime

from excom.excom.services.intake import ingest


def pull(src) -> dict:
	if not src.api_url:
		frappe.throw("TradeIndia API URL missing on the intake source (copy it from 'My Inquiry API')")
	key = src.get_password("api_key", raise_exception=False) or ""
	end = now_datetime()
	start = add_to_date(get_datetime(src.last_success_at), minutes=-15) if src.last_success_at else add_to_date(end, days=-1)
	params = {"userid": src.user_id, "profile_id": src.profile_id, "key": key, "from_date": start.strftime("%Y-%m-%d"), "to_date": end.strftime("%Y-%m-%d"), "limit": 200}
	resp = requests.get(src.api_url, params=params, timeout=30)
	if resp.status_code != 200:
		raise frappe.ValidationError(f"TradeIndia HTTP {resp.status_code}: {resp.text[:300]}")
	data = resp.json()
	rows = data if isinstance(data, list) else data.get("data") or data.get("inquiries") or []
	n = dup = 0
	for row in rows:
		rfi = row.get("rfi_id") or row.get("inquiry_id") or row.get("id")
		if not rfi:
			continue
		r = ingest(src, f"tradeindia:{rfi}", row)
		n += 1
		dup += 1 if r["duplicate"] else 0
	return {"fetched": len(rows), "ingested": n, "duplicates": dup}
