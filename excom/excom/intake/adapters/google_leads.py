"""Google Ads lead form webhook — push only. [vendor] Google Ads webhook docs:
https://developers.google.com/google-ads/webhook/docs/implementation (WebhookLead: lead_id,
user_column_data[{column_id, string_value, column_name}], api_version, form_id, campaign_id,
google_key, is_test, gcl_id, adgroup_id, creative_id, asset_group_id, lead_submit_time, lead_source).

The advertiser sets a key per form; every POST carries it as `google_key`. It is checked (constant
time) before anything is stored, and never stored. Delivery is at-least-once: dedupe on lead_id.
"""

import hmac

import frappe

from excom.excom.services.intake import ingest

SOURCE_TYPE = "Google Lead Form"


def _id(value) -> str:
	if value in (None, "", 0, "0"):
		return ""
	return str(int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value).strip()


def provided_key(payload: dict) -> str:
	"""Google's own test samples spell it `Google_key`; production sends `google_key`."""
	for k, v in payload.items():
		if isinstance(k, str) and k.lower() == "google_key":
			return str(v or "")
	return ""


def find_source(payload: dict):
	"""The enabled Google Lead Form source whose key matches. Reads only.

	Sources pinned to this form_id are tried first, then sources with no form_id yet (the id is
	learned from the first lead whose key matches). None for a missing or wrong key."""
	key = provided_key(payload)
	if not key or len(key) > 512:
		return None
	form_id = _id(payload.get("form_id"))
	base = {"source_type": SOURCE_TYPE, "enabled": 1}
	names = frappe.get_all("Excom Source", filters={**base, "form_id": form_id}, pluck="name", order_by="creation asc") if form_id else []
	names += [n for n in frappe.get_all("Excom Source", filters={**base, "form_id": ["is", "not set"]}, pluck="name", order_by="creation asc") if n not in names]
	match = None
	for name in names:
		try:
			stored = frappe.get_doc("Excom Source", name).get_password("google_key", raise_exception=False) or ""
		except Exception:
			stored = ""
		if stored and hmac.compare_digest(stored.encode(), key.encode()) and match is None:
			match = name  # keep comparing: the time taken does not say which source matched
	# a key that cannot be decrypted (restored backup) must not leak a msgprint into the response
	frappe.local.message_log = []
	return frappe.get_doc("Excom Source", match) if match else None


def handle(src, payload: dict) -> dict:
	"""Verified payload → raw log (without the key) → the usual intake pipeline."""
	lead_id = _id(payload.get("lead_id"))
	if not lead_id:
		frappe.throw("lead_id missing")
	form_id = _id(payload.get("form_id"))
	if form_id and not src.form_id:
		src.db_set("form_id", form_id, update_modified=False)
	raw = {k: v for k, v in payload.items() if not (isinstance(k, str) and k.lower() == "google_key")}
	return ingest(src, f"google:{lead_id}", raw)
