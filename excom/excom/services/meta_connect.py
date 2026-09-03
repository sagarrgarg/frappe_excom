"""
Meta Business connection (one credential set) → discover pages, Instagram accounts, lead forms and
WhatsApp numbers → enable each into an Excom Channel Account / Intake Source with one call.

Token: a Business Manager **System User** token (never expires) is the intended credential. A
short-lived user token can be exchanged with `exchange_token()` (needs app id + secret).
"""

import json

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

GRAPH = "https://graph.facebook.com"
PAGE_FIELDS = "id,name,category,access_token,instagram_business_account{id,username,name}"
FORM_FIELDS = "id,name,status,leads_count,created_time"


def _conn(name):
	return frappe.get_doc("Excom Meta Connection", name)


def _token(conn) -> str:
	t = conn.get_password("system_user_token", raise_exception=False) or ""
	if not t:
		frappe.throw(_("System user token missing on {0}").format(conn.name))
	return t


def _get(url: str, params: dict) -> dict:
	resp = requests.get(url, params=params, timeout=30)
	data = resp.json() if resp.content else {}
	if resp.status_code != 200:
		err = (data.get("error") or {}).get("message") or resp.text[:300]
		raise frappe.ValidationError(f"Graph {resp.status_code}: {err}")
	return data


def _post(url: str, params: dict, payload: dict | None = None) -> dict:
	resp = requests.post(url, params=params, json=payload or {}, timeout=30)
	data = resp.json() if resp.content else {}
	if resp.status_code != 200:
		err = (data.get("error") or {}).get("message") or resp.text[:300]
		raise frappe.ValidationError(f"Graph {resp.status_code}: {err}")
	return data


def _paged(url: str, params: dict) -> list[dict]:
	out, pages = [], 0
	while url and pages < 50:
		data = _get(url, params)
		out.extend(data.get("data") or [])
		url = (data.get("paging") or {}).get("next")
		params = None
		pages += 1
	return out


# ─── token ────────────────────────────────────────────────────────────────────

def exchange_token(name: str, short_lived_token: str) -> dict:
	"""User token (hours) → long-lived user token (60 days). System user tokens don't need this."""
	conn = _conn(name)
	secret = conn.get_password("app_secret", raise_exception=False)
	if not conn.app_id or not secret:
		frappe.throw(_("App id and app secret are required to exchange a token"))
	data = _get(f"{GRAPH}/{conn.api_version or 'v21.0'}/oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": conn.app_id, "client_secret": secret, "fb_exchange_token": short_lived_token})
	conn.system_user_token = data["access_token"]
	conn.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "expires_in": data.get("expires_in")}


def debug_token(name: str) -> dict:
	conn = _conn(name)
	tok = _token(conn)
	secret = conn.get_password("app_secret", raise_exception=False)
	app_token = f"{conn.app_id}|{secret}" if conn.app_id and secret else tok
	data = _get(f"{GRAPH}/{conn.api_version or 'v21.0'}/debug_token", {"input_token": tok, "access_token": app_token}).get("data") or {}
	frappe.db.set_value("Excom Meta Connection", name, {"token_valid": 1 if data.get("is_valid") else 0, "token_scopes": ", ".join(data.get("scopes") or [])}, update_modified=False)
	return data


# ─── discovery ────────────────────────────────────────────────────────────────

def discover(name: str) -> dict:
	"""Refresh the asset table from Graph: pages (+ IG accounts), lead forms per page, WhatsApp numbers."""
	conn = _conn(name)
	tok = _token(conn)
	v = conn.api_version or "v21.0"
	found: list[dict] = []
	pages = _paged(f"{GRAPH}/{v}/me/accounts", {"access_token": tok, "fields": PAGE_FIELDS, "limit": 100})
	if conn.business_id:
		for edge in ("owned_pages", "client_pages"):
			try:
				seen = {x.get("id") for x in pages}
				pages += [p for p in _paged(f"{GRAPH}/{v}/{conn.business_id}/{edge}", {"access_token": tok, "fields": PAGE_FIELDS, "limit": 100}) if p.get("id") not in seen]
			except frappe.ValidationError:
				pass  # edge needs business_management; /me/accounts already covered what the token can see
	for p in pages:
		found.append({"asset_type": "Page", "asset_id": p["id"], "asset_name": p.get("name"), "page_id": p["id"], "extra": {"category": p.get("category"), "access_token": p.get("access_token")}})
		ig = p.get("instagram_business_account") or {}
		if ig.get("id"):
			found.append({"asset_type": "Instagram", "asset_id": ig["id"], "asset_name": f"@{ig.get('username')}" if ig.get("username") else ig.get("name"), "page_id": p["id"], "extra": {"page_name": p.get("name")}})
		page_tok = p.get("access_token") or tok
		try:
			for f in _paged(f"{GRAPH}/{v}/{p['id']}/leadgen_forms", {"access_token": page_tok, "fields": FORM_FIELDS, "limit": 100}):
				found.append({"asset_type": "Lead Form", "asset_id": f["id"], "asset_name": f.get("name"), "page_id": p["id"], "extra": {"status": f.get("status"), "leads_count": f.get("leads_count"), "page_name": p.get("name")}})
		except frappe.ValidationError as e:
			found.append({"asset_type": "Lead Form", "asset_id": f"-{p['id']}", "asset_name": f"(cannot list forms for {p.get('name')}: {str(e)[:80]})", "page_id": p["id"], "extra": {}})
	if conn.business_id:
		for edge in ("owned_whatsapp_business_accounts", "client_whatsapp_business_accounts"):
			try:
				for waba in _paged(f"{GRAPH}/{v}/{conn.business_id}/{edge}", {"access_token": tok, "fields": "id,name", "limit": 50}):
					for n in _paged(f"{GRAPH}/{v}/{waba['id']}/phone_numbers", {"access_token": tok, "fields": "id,display_phone_number,verified_name,quality_rating", "limit": 50}):
						found.append({"asset_type": "WhatsApp Number", "asset_id": n["id"], "asset_name": f"{n.get('verified_name') or ''} {n.get('display_phone_number') or ''}".strip(), "page_id": waba["id"], "extra": {"waba_id": waba["id"], "waba_name": waba.get("name"), "quality": n.get("quality_rating")}})
			except frappe.ValidationError:
				pass
	# Merge into the child table. Rows Meta did not return this time are KEPT (manually added numbers, assets the
	# token cannot see yet) and flagged, never dropped — enabling/disabling is the only thing that changes state.
	existing = {(a.asset_type, a.asset_id): a for a in conn.assets}
	returned = {(f["asset_type"], f["asset_id"]) for f in found}
	rows = []
	for f in found:
		old = existing.get((f["asset_type"], f["asset_id"]))
		row = {"asset_type": f["asset_type"], "asset_id": f["asset_id"], "asset_name": f["asset_name"], "page_id": f["page_id"], "extra": json.dumps(f["extra"], default=str)}
		if old:
			row.update({"enabled": old.enabled, "linked_doctype": old.linked_doctype, "linked_name": old.linked_name})
		rows.append(row)
	for key, old in existing.items():
		if key in returned:
			continue
		extra = json.loads(old.extra or "{}") if old.extra else {}
		extra["not_returned_by_meta"] = True
		rows.append({"asset_type": old.asset_type, "asset_id": old.asset_id, "asset_name": old.asset_name, "page_id": old.page_id, "enabled": old.enabled, "linked_doctype": old.linked_doctype, "linked_name": old.linked_name, "extra": json.dumps(extra, default=str)})
	conn.assets = []
	for row in rows:
		conn.append("assets", row)
	conn.last_synced_at = now_datetime()
	conn.flags.ignore_permissions = True
	conn.save()
	frappe.db.commit()
	counts: dict = {}
	for f in found:
		counts[f["asset_type"]] = counts.get(f["asset_type"], 0) + 1
	return {"found": counts, "total": len(found)}


# ─── enabling ─────────────────────────────────────────────────────────────────

def _page_token(conn, page_id: str) -> str:
	for a in conn.assets:
		if a.asset_type == "Page" and a.asset_id == page_id:
			return (json.loads(a.extra or "{}").get("access_token")) or _token(conn)
	return _token(conn)


def _upsert_account(conn, channel: str, account_name: str, values: dict) -> str:
	key = {k: v for k, v in values.items() if k in ("meta_page_id", "wa_phone_id", "meta_ig_user_id")}
	name = frappe.db.get_value("Excom Channel Account", {"channel": channel, **key}, "name")
	doc = frappe.get_doc("Excom Channel Account", name) if name else frappe.new_doc("Excom Channel Account")
	if not name:
		doc.account_name = account_name
		doc.channel = channel
		doc.company = conn.company
	for k, val in values.items():
		doc.set(k, val)
	doc.flags.ignore_permissions = True
	doc.save()
	return doc.name


def _subscribe_page(conn, page_id: str, fields: str) -> None:
	"""Webhook accelerator: subscribe the page to our app. Polling works without it."""
	try:
		_post(f"{GRAPH}/{conn.api_version or 'v21.0'}/{page_id}/subscribed_apps", {"access_token": _page_token(conn, page_id), "subscribed_fields": fields})
	except Exception:
		frappe.log_error(title=f"Excom Meta: page subscribe failed {page_id}", message=frappe.get_traceback())


def enable_asset(name: str, asset_type: str, asset_id: str, enable: int = 1) -> dict:
	conn = _conn(name)
	row = next((a for a in conn.assets if a.asset_type == asset_type and a.asset_id == asset_id), None)
	if not row:
		frappe.throw(_("Asset not found — run Discover first"))
	extra = json.loads(row.extra or "{}")
	linked_dt = linked_name = None
	if not int(enable or 0):
		if row.linked_doctype and row.linked_name and frappe.db.exists(row.linked_doctype, row.linked_name):
			if row.linked_doctype == "Excom Channel Account":
				frappe.db.set_value(row.linked_doctype, row.linked_name, "status", "Inactive")
			else:
				frappe.db.set_value(row.linked_doctype, row.linked_name, "enabled", 0)
		row.enabled = 0
	elif asset_type == "Page":
		linked_dt = "Excom Channel Account"
		linked_name = _upsert_account(conn, "messenger", f"Messenger · {row.asset_name}", {"meta_page_id": asset_id, "meta_page_token": _page_token(conn, asset_id), "meta_api_version": conn.api_version or "v21.0", "meta_poll_interval_minutes": 1, "status": "Active"})
		_subscribe_page(conn, asset_id, "messages,messaging_postbacks,message_deliveries,message_reads")
	elif asset_type == "Instagram":
		linked_dt = "Excom Channel Account"
		linked_name = _upsert_account(conn, "instagram", f"Instagram · {row.asset_name}", {"meta_ig_user_id": asset_id, "meta_page_id": row.page_id, "meta_page_token": _page_token(conn, row.page_id), "meta_api_version": conn.api_version or "v21.0", "meta_poll_interval_minutes": 1, "status": "Active"})
		_subscribe_page(conn, row.page_id, "messages")
	elif asset_type == "Lead Form":
		linked_dt = "Excom Intake Source"
		src_name = frappe.db.get_value("Excom Intake Source", {"source_type": "Meta Lead Ads", "form_id": asset_id}, "name")
		src = frappe.get_doc("Excom Intake Source", src_name) if src_name else frappe.new_doc("Excom Intake Source")
		if not src_name:
			src.source_name = f"Meta · {row.asset_name}"[:140]
			src.source_type = "Meta Lead Ads"
			src.mode = "Both"
			src.pull_frequency = "Every 15 Minutes"
			src.sla_first_response = 3600
			src.company = conn.company
			src.channel_account = frappe.db.get_value("Excom Channel Account", {"channel": "whatsapp", "status": "Active", "company": conn.company}, "name") or frappe.db.get_value("Excom Channel Account", {"channel": "whatsapp", "status": "Active"}, "name")
		src.enabled = 1
		src.page_id = row.page_id
		src.form_id = asset_id
		src.access_token = _page_token(conn, row.page_id)
		src.flags.ignore_permissions = True
		src.save()
		linked_name = src.name
		_subscribe_page(conn, row.page_id, "leadgen")
	elif asset_type == "WhatsApp Number":
		linked_dt = "Excom Channel Account"
		linked_name = _upsert_account(conn, "whatsapp", f"WhatsApp · {row.asset_name}", {"wa_phone_id": asset_id, "wa_business_id": extra.get("waba_id"), "wa_token": _token(conn), "wa_app_id": conn.app_id, "wa_app_secret": conn.get_password("app_secret", raise_exception=False) or "", "wa_webhook_verify_token": conn.webhook_verify_token, "wa_url": "https://graph.facebook.com", "wa_version": conn.api_version or "v21.0", "status": "Active"})
	if int(enable or 0):
		row.enabled = 1
		row.linked_doctype = linked_dt
		row.linked_name = linked_name
	conn.flags.ignore_permissions = True
	conn.save()
	frappe.db.commit()
	return {"enabled": int(row.enabled), "linked_doctype": row.linked_doctype, "linked_name": row.linked_name}


def app_secrets() -> list[str]:
	"""For webhook HMAC: every active connection's app secret."""
	out = []
	if not frappe.db.exists("DocType", "Excom Meta Connection"):
		return out
	for n in frappe.get_all("Excom Meta Connection", filters={"status": "Active"}, pluck="name"):
		try:
			s = frappe.get_doc("Excom Meta Connection", n).get_password("app_secret", raise_exception=False)
			if s:
				out.append(s)
		except Exception:
			pass
	return out
