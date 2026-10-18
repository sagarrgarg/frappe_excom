"""Meta lead ads — leadgen webhook (ids only → Graph fetch) + nightly reconciliation WITH pagination (RES-001 H2)."""

import json

import requests
import frappe
from frappe.utils import get_datetime

from excom.excom.services import intake
from excom.excom.services.intake import ingest

GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_VERSION = "v21.0"
# Lead fields read from Graph. ad / ad set / campaign IDs and names tie the lead to the Excom Ads mirrors;
# is_organic marks a lead from a form shared without an ad. Reading ad fields needs ads_management on the token.
LEAD_FIELDS = "id,created_time,field_data,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,is_organic,platform"


def _graph(src=None) -> str:
	"""Graph root at the version this site is configured for (Meta retires a version ~2 years after launch,
	so a hardcoded one eventually starts failing). Falls back to the app default."""
	version = ""
	if src is not None:
		conn = frappe.db.get_value("Excom Meta Connection", {"status": "Active"}, "api_version") if frappe.db.exists("DocType", "Excom Meta Connection") else None
		version = conn or ""
	version = version or DEFAULT_VERSION
	if not version.startswith("v"):
		version = "v" + version
	return f"{GRAPH_BASE}/{version}"


def _token(src) -> str:
	return src.get_password("access_token", raise_exception=False) or ""


def fetch_lead(src, leadgen_id: str) -> dict:
	resp = requests.get(f"{_graph(src)}/{leadgen_id}", params={"access_token": _token(src), "fields": LEAD_FIELDS}, timeout=30)
	if resp.status_code != 200:
		raise frappe.ValidationError(f"Graph {resp.status_code}: {resp.text[:300]}")
	return resp.json()


def handle_leadgen(change: dict) -> dict:
	"""Webhook value: {leadgen_id, page_id, form_id, ad_id, adgroup_id, created_time}."""
	v = change.get("value") or {}
	leadgen_id, page_id, form_id = v.get("leadgen_id"), str(v.get("page_id") or ""), str(v.get("form_id") or "")
	if not leadgen_id:
		return {"ignored": "no leadgen_id"}
	src_name = frappe.db.get_value("Excom Source", {"source_type": "Meta Lead Ads", "enabled": 1, "form_id": form_id}, "name") or frappe.db.get_value(
		"Excom Source", {"source_type": "Meta Lead Ads", "enabled": 1, "page_id": page_id}, "name"
	)
	if not src_name:
		return {"ignored": f"no source for page {page_id} / form {form_id}"}
	src = frappe.get_doc("Excom Source", src_name)
	# Meta retries and re-delivers: a lead already logged (and not failed) needs no second Graph call.
	known = frappe.db.get_value("Excom Source Log", {"dedupe_key": f"meta:{leadgen_id}"}, ["name", "status"], as_dict=True)
	if known and known.status not in ("Failed", "Ignored"):
		return {"log": known.name, "duplicate": True}
	raw = {"leadgen_id": leadgen_id, "page_id": page_id, "form_id": form_id, "created_time": v.get("created_time"), "_webhook": v}
	try:
		raw.update(fetch_lead(src, leadgen_id))
	except Exception:
		# log the stub; reconciliation fills field_data later, replay picks it up
		frappe.log_error(title="Excom Meta lead fetch failed", message=frappe.get_traceback())
	return _ingest(src, f"meta:{leadgen_id}", raw)


def _ingest(src, key: str, raw: dict) -> dict:
	"""ingest(), plus: a log that was Ignored because the Graph fetch failed (a stub without answers) is
	completed by the next copy that has field_data — the webhook retry or the nightly reconciliation —
	instead of being skipped as a duplicate forever."""
	log = frappe.db.get_value("Excom Source Log", {"dedupe_key": key}, ["name", "status", "raw_payload"], as_dict=True)
	if log and log.status == "Ignored" and raw.get("field_data"):
		try:
			merged = {**json.loads(log.raw_payload or "{}"), **raw}
		except ValueError:
			merged = raw
		frappe.db.set_value("Excom Source Log", log.name, {"raw_payload": json.dumps(merged, ensure_ascii=False, default=str), "status": "Received", "error": ""})
		frappe.db.commit()
		intake.enqueue(log.name)
		return {"log": log.name, "duplicate": False}
	return ingest(src, key, raw)


def reconcile(src) -> dict:
	"""Nightly: /{form_id}/leads?filtering=[time_created > watermark], paginated."""
	if not src.form_id:
		return {"skipped": "no form_id"}
	since = int(get_datetime(src.last_success_at).timestamp()) - 600 if src.last_success_at else 0  # 10-min overlap; dedupe by leadgen_id
	url = f"{_graph(src)}/{src.form_id}/leads"
	params = {"access_token": _token(src), "fields": LEAD_FIELDS, "limit": 100, "filtering": f'[{{"field":"time_created","operator":"GREATER_THAN","value":{since}}}]'}
	n = dup = pages = 0
	while url and pages < 200:
		resp = requests.get(url, params=params, timeout=30)
		if resp.status_code != 200:
			raise frappe.ValidationError(f"Graph {resp.status_code}: {resp.text[:300]}")
		data = resp.json()
		for row in data.get("data", []):
			row["leadgen_id"] = row.get("id")
			r = _ingest(src, f"meta:{row['id']}", row)
			n += 1
			dup += 1 if r["duplicate"] else 0
		url = (data.get("paging") or {}).get("next")
		params = None  # `next` carries the cursor + token
		pages += 1
	return {"ingested": n, "duplicates": dup, "pages": pages}
