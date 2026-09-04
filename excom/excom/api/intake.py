"""
Guest intake endpoints (P3 §3.5 push, §3.6 website). Security contract RES-001 §8:
token per source · origin allowlist · IP rate limit · honeypot/min-fill/captcha · idempotent by submission_id ·
minimal response (no enumeration oracle).
"""

import ipaddress
import json
import re
import time

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit

from excom.excom.services.intake import ingest

ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d+)?$")


def _source_by_token(token: str, source_type: str | None = None):
	if not token or len(token) < 16:
		return None
	filters = {"enabled": 1}
	if source_type:
		filters["source_type"] = source_type
	for name in frappe.get_all("Excom Intake Source", filters=filters, pluck="name"):
		doc = frappe.get_doc("Excom Intake Source", name)
		if doc.get_password("push_token", raise_exception=False) == token:
			return doc
	return None


def _origin_ok(src, origin: str) -> bool:
	allowed = [o.strip() for o in (src.allowed_origins or "").splitlines() if o.strip()]
	if not allowed:
		return True
	if not origin or not ORIGIN_RE.match(origin):
		return False
	return origin in allowed


def _caller_ok(src, origin: str) -> tuple[bool, str]:
	"""Who may call this source, beyond the token:
	- no domain list and no IP list → the token alone is the fence;
	- domain list set → the request must carry an Origin (or Referer) on the list;
	- IP list set → the caller's IP must be on it;
	- both set → either is enough (browser form OR trusted server).
	Requests without an Origin are NOT waved through when a domain list exists."""
	origins = [o.strip() for o in (src.allowed_origins or "").splitlines() if o.strip()]
	ips = [o.strip() for o in (src.allowed_ips or "").splitlines() if o.strip()]
	if not origins and not ips:
		return True, ""
	if not origin:
		ref = frappe.get_request_header("Referer") or ""
		m = ORIGIN_RE.match("/".join(ref.split("/")[:3])) if ref else None
		origin = m.group(0) if m else ""
	origin_ok = bool(origins) and bool(origin) and origin in origins
	ip_ok = bool(ips) and _ip_ok(src)
	if origin_ok or ip_ok:
		return True, ""
	if origins and not origin:
		return False, "origin"
	return False, "origin" if origins else "ip"


def _ip_ok(src) -> bool:
	allowed = [o.strip() for o in (src.allowed_ips or "").splitlines() if o.strip()]
	if not allowed:
		return True
	ip = frappe.local.request_ip or ""
	try:
		addr = ipaddress.ip_address(ip)
		return any(addr in ipaddress.ip_network(a, strict=False) for a in allowed)
	except ValueError:
		return False


def _cors(origin: str) -> None:
	frappe.local.response.headers = frappe.local.response.get("headers") or {}
	frappe.local.response.headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Allow-Methods": "POST, OPTIONS"})


@frappe.whitelist(allow_guest=True, methods=["POST", "OPTIONS"])
@rate_limit(limit=10, seconds=60)
def submit_enquiry():
	"""Headless website form endpoint. Body: see P3 §3.6."""
	body = frappe.local.form_dict
	if frappe.request.method == "OPTIONS":
		return {"ok": True}
	origin = frappe.get_request_header("Origin") or ""
	src = _source_by_token(body.get("source_token") or "", "Website")
	if not src:
		frappe.throw(_("Unknown source"), frappe.AuthenticationError)
	ok, why = _caller_ok(src, origin)
	if not ok:
		frappe.throw(_("Origin not allowed") if why == "origin" else _("IP not allowed"), frappe.PermissionError)
	if origin:
		_cors(origin)
	# spam controls
	if body.get("website") or body.get("_hp"):  # honeypot
		return {"ok": True, "ref": "hp"}
	try:
		started = float(body.get("started_at") or 0)
		if started and time.time() - started < (src.min_fill_seconds or 0):
			return {"ok": True, "ref": "fast"}
	except ValueError:
		pass
	secret = src.get_password("captcha_secret", raise_exception=False)
	if secret:
		tok = body.get("captcha_token") or ""
		if not tok or not _verify_captcha(secret, tok):
			frappe.throw(_("Captcha failed"), frappe.PermissionError)
	submission_id = (body.get("submission_id") or "").strip()
	if not submission_id or len(submission_id) > 80:
		frappe.throw(_("submission_id required"))
	raw = {k: body.get(k) for k in ("name", "email", "phone", "message", "company", "city", "country", "page_url", "utm", "extra") if body.get(k) is not None}
	if isinstance(raw.get("utm"), str):
		try:
			raw["utm"] = json.loads(raw["utm"])
		except ValueError:
			raw["utm"] = {}
	if isinstance(raw.get("extra"), str):
		try:
			raw["extra"] = json.loads(raw["extra"])
		except ValueError:
			raw["extra"] = {}
	r = ingest(src, f"web:{src.name}:{submission_id}", raw)
	return {"ok": True, "ref": r["log"]}


def _verify_captcha(secret: str, token: str) -> bool:
	for url in ("https://challenges.cloudflare.com/turnstile/v0/siteverify", "https://hcaptcha.com/siteverify"):
		try:
			resp = requests.post(url, data={"secret": secret, "response": token, "remoteip": frappe.local.request_ip}, timeout=10)
			if resp.ok and resp.json().get("success"):
				return True
		except Exception:
			continue
	return False


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=60, seconds=60)
def indiamart_push(token: str = ""):
	"""Seller-panel push URL: /api/method/excom.excom.api.intake.indiamart_push?token=<push_token>. Poller is authoritative."""
	src = _source_by_token(token or frappe.local.form_dict.get("token") or "", "IndiaMART")
	if not src:
		frappe.throw(_("Unknown source"), frappe.AuthenticationError)
	if not _ip_ok(src):
		frappe.throw(_("IP not allowed"), frappe.PermissionError)
	try:
		payload = json.loads(frappe.request.get_data(as_text=True) or "{}")
	except ValueError:
		payload = dict(frappe.local.form_dict)
	from excom.excom.intake.adapters.indiamart import push

	r = push(src, payload)
	return {"ok": True, "ref": r["log"]}


# ─── generic token webhook (form builders, partners, server-to-server) ────────

def _webhook_dedupe_key(src_name: str, body: dict, raw_text: str) -> str:
	"""Prefer an id the sender gives us; otherwise hash the payload so a retry never creates a second lead."""
	import hashlib
	for k in ("submission_id", "id", "entry_id", "response_id", "event_id", "uuid"):
		v = body.get(k) if isinstance(body, dict) else None
		if v:
			return f"web:{src_name}:{str(v)[:80]}"
	return f"web:{src_name}:sha1:{hashlib.sha1((raw_text or json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()}"


@frappe.whitelist(allow_guest=True, methods=["POST", "OPTIONS"])
@rate_limit(limit=60, seconds=60)
def website_webhook(token: str = ""):
	"""
	Token-in-URL webhook for any website form / form builder / partner system:

	    POST /api/method/excom.excom.api.intake.website_webhook?token=<push_token>
	    Content-Type: application/json   (or form-encoded)
	    {"name": "...", "email": "...", "phone": "...", "message": "...", "company": "...", "city": "...", "submission_id": "..."}

	Also accepts the token in header X-Excom-Token or body field `token`. Keys are mapped through the
	source's Field Map (defaults: name, email, phone, message, company, city, country, page_url, utm).
	Restrictions: token, optional Origin allowlist (browser callers), optional IP allowlist (servers),
	60 req/min per IP, idempotent by submission_id / id / payload hash. Response never reveals whether a
	token exists (generic 401).
	"""
	if frappe.request.method == "OPTIONS":
		origin = frappe.get_request_header("Origin") or ""
		if origin:
			_cors(origin)
		return {"ok": True}
	# Frappe drops query args for JSON bodies, so read ?token= straight from the request too.
	tok = token or (frappe.request.args.get("token") if frappe.request else "") or frappe.local.form_dict.get("token") or frappe.get_request_header("X-Excom-Token") or ""
	src = _source_by_token(tok, "Website")
	if not src:
		frappe.throw(_("Unknown source"), frappe.AuthenticationError)
	origin = frappe.get_request_header("Origin") or ""
	ok, why = _caller_ok(src, origin)
	if not ok:
		frappe.throw(_("Origin not allowed") if why == "origin" else _("IP not allowed"), frappe.PermissionError)
	if origin:
		_cors(origin)
	raw_text = frappe.request.get_data(as_text=True) or ""
	try:
		body = json.loads(raw_text) if raw_text.strip().startswith("{") else dict(frappe.local.form_dict)
	except ValueError:
		body = dict(frappe.local.form_dict)
	body = {k: v for k, v in body.items() if k not in ("cmd", "token")}
	if body.get("website") or body.get("_hp"):  # honeypot
		return {"ok": True, "ref": "hp"}
	r = ingest(src, _webhook_dedupe_key(src.name, body, raw_text if raw_text.strip().startswith("{") else ""), body)
	return {"ok": True, "ref": r["log"], "duplicate": r["duplicate"]}
