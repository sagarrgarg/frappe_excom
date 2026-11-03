"""Meta's WhatsApp template library (HLD-005 Phase 4): search it and create templates from it.

Library templates are pre-written utility templates (order, delivery, payment, account updates).
Created unchanged, Meta approves them at once, so they can be sent straight away.

	search   GET  /message_template_library?search=&topic=&usecase=&industry=&language=
	         (no node id in the path; Meta's guide shows /{WABA}/message_templates, which is a doc bug)
	create   POST /{WABA}/message_templates
	         {name, language, category: "UTILITY", library_template_name,
	          library_template_button_inputs: [...], library_template_body_inputs: {...}}

The created template is stored as a WhatsApp Templates row with hooks off (the same path as
template sync), so after_insert never posts it to Meta a second time.
"""

import re

import frappe
import requests as http_requests
from frappe import _

from excom.excom.services import whatsapp_errors as wae
from excom.excom.utils.graph import graph_version

TOPICS = ("ACCOUNT_UPDATE", "CUSTOMER_FEEDBACK", "ORDER_MANAGEMENT", "PAYMENTS")
INDUSTRIES = ("E_COMMERCE", "FINANCIAL_SERVICES")
USECASES = (
	"ACCOUNT_CREATION_CONFIRMATION", "AUTO_PAY_REMINDER", "DELIVERY_CONFIRMATION", "DELIVERY_FAILED",
	"DELIVERY_UPDATE", "FEEDBACK_SURVEY", "FRAUD_ALERT", "LOW_BALANCE_WARNING", "ORDER_ACTION_NEEDED",
	"ORDER_CONFIRMATION", "ORDER_DELAY", "ORDER_OR_TRANSACTION_CANCEL", "ORDER_PICK_UP",
	"PAYMENT_ACTION_REQUIRED", "PAYMENT_CONFIRMATION", "PAYMENT_DUE_REMINDER", "PAYMENT_OVERDUE",
	"PAYMENT_REJECT_FAIL", "PAYMENT_SCHEDULED", "RECEIPT_ATTACHMENT", "RETURN_CONFIRMATION",
	"SHIPMENT_CONFIRMATION", "STATEMENT_ATTACHMENT", "STATEMENT_AVAILABLE", "TRANSACTION_ALERT",
)
BUTTON_INPUT_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER", "OTP", "MPM", "CATALOG", "FLOW", "VOICE_CALL", "APP")
BODY_INPUT_KEYS = ("add_contact_number", "add_learn_more_link", "add_security_recommendation",
	"add_track_package_link", "code_expiration_minutes")
# Meta: "Must be UTILITY". Whether AUTHENTICATION library templates are accepted is UNVERIFIED.
LIBRARY_CATEGORY = "UTILITY"
TEMPLATE_FIELDS = "name,status,language,category,id,components,library_template_name"
_NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")


def _account(account_name: str = ""):
	if account_name:
		return frappe.get_doc("Excom Channel Account", account_name)
	from excom.excom.utils import get_channel_account

	acc = get_channel_account(channel="whatsapp", account_type="outgoing")
	if not acc:
		frappe.throw(_("Set a default outgoing WhatsApp Channel Account first."))
	return acc


def _api(account) -> tuple[str, dict, str]:
	"""(base url with version, auth headers, WABA id)."""
	from excom.excom.utils import get_wa_credentials

	creds = get_wa_credentials(account)
	version = graph_version(creds["version"])
	base = f"{(creds['url'] or 'https://graph.facebook.com').rstrip('/')}/{version}"
	return base, {"Authorization": f"Bearer {creds['token']}"}, creds.get("business_id") or ""


def _call(method: str, url: str, account, **kw) -> dict:
	try:
		resp = getattr(http_requests, method)(url, timeout=30, **kw)
	except http_requests.exceptions.RequestException as e:
		frappe.throw(_("Could not reach Meta: {0}").format(str(e)[:200]), title=_("WhatsApp template library"))
	try:
		data = resp.json() if resp.content else {}
	except ValueError:
		data = {}
	if resp.status_code >= 400 or (isinstance(data, dict) and data.get("error")):
		try:
			wae.raise_for_error((data or {}).get("error") or {}, resp.status_code, account.name)
		except wae.WhatsAppAPIError as e:
			frappe.throw(e.message, title=_("WhatsApp template library"))
	return data or {}


def search(account_name: str = "", search: str = "", topic: str = "", usecase: str = "", industry: str = "",
	language: str = "", after: str = "", limit: int = 50) -> dict:
	"""One page of library templates matching the filters (all optional)."""
	for value, allowed, what in ((topic, TOPICS, "topic"), (usecase, USECASES, "use case"), (industry, INDUSTRIES, "industry")):
		if value and value not in allowed:
			frappe.throw(_("Unknown {0}: {1}").format(what, value))
	account = _account(account_name)
	base, headers, _waba = _api(account)
	params = {k: v for k, v in {"search": (search or "").strip(), "topic": topic, "usecase": usecase,
		"industry": industry, "language": language, "after": after}.items() if v}
	params["limit"] = max(1, min(int(limit or 50), 100))
	data = _call("get", f"{base}/message_template_library", account, params=params, headers=headers)
	return {
		"data": [normalise(item) for item in data.get("data") or []],
		"after": ((data.get("paging") or {}).get("cursors") or {}).get("after") if (data.get("paging") or {}).get("next") else "",
	}


def normalise(item: dict) -> dict:
	"""A library entry for the browser, plus the button inputs a create call must supply."""
	buttons = item.get("buttons") or []
	needs = []
	for i, b in enumerate(buttons):
		kind = (b.get("type") or "").upper()
		if kind == "URL":
			needs.append({"index": i, "type": "URL", "text": b.get("text") or "", "default": b.get("url") or ""})
		elif kind == "PHONE_NUMBER":
			needs.append({"index": i, "type": "PHONE_NUMBER", "text": b.get("text") or "", "default": b.get("phone_number") or ""})
		else:
			needs.append({"index": i, "type": kind, "text": b.get("text") or ""})
	industry = item.get("industry")
	return {
		"id": item.get("id"),
		"name": item.get("name"),
		"language": item.get("language"),
		"category": item.get("category"),
		"topic": item.get("topic"),
		"usecase": item.get("usecase"),
		"industry": industry if isinstance(industry, list) else ([industry] if industry else []),
		"header": item.get("header") or "",
		"body": item.get("body") or "",
		"body_params": item.get("body_params") or [],
		"body_param_types": item.get("body_param_types") or [],
		"buttons": buttons,
		"button_inputs": needs,
	}


def build_button_inputs(inputs: list) -> list:
	"""Validated `library_template_button_inputs` (sent as a JSON array; Meta's guide shows a string, UNVERIFIED)."""
	out = []
	for i, b in enumerate(inputs or []):
		if not isinstance(b, dict):
			frappe.throw(_("Button input {0} must be an object").format(i + 1))
		kind = (b.get("type") or "").upper()
		if kind not in BUTTON_INPUT_TYPES:
			frappe.throw(_("Button input {0}: unknown type {1}").format(i + 1, kind or "—"))
		row = {"type": kind}
		if kind == "URL":
			url = b.get("url") or {}
			if isinstance(url, str):
				url = {"base_url": url}
			base_url = (url.get("base_url") or "").strip()
			if not base_url.startswith(("http://", "https://")):
				frappe.throw(_("Button {0}: the website address must start with https://").format(i + 1))
			row["url"] = {"base_url": base_url}
			if "{{" in base_url:
				example = (url.get("url_suffix_example") or "").strip()
				if not example:
					frappe.throw(_("Button {0}: give an example of the full link for the {{{{1}}}} part.").format(i + 1))
				row["url"]["url_suffix_example"] = example
		elif kind == "PHONE_NUMBER":
			phone = (b.get("phone_number") or "").strip()
			if not re.match(r"^\+?\d{6,20}$", phone.replace(" ", "")):
				frappe.throw(_("Button {0}: enter the phone number with country code, e.g. +919812345678").format(i + 1))
			row["phone_number"] = phone.replace(" ", "")
		for key in ("otp_type", "zero_tap_terms_accepted", "supported_apps"):
			if key in b:
				row[key] = b[key]
		out.append(row)
	return out


def build_body_inputs(inputs: dict) -> dict:
	if not inputs:
		return {}
	if not isinstance(inputs, dict):
		frappe.throw(_("Body inputs must be an object"))
	unknown = [k for k in inputs if k not in BODY_INPUT_KEYS]
	if unknown:
		frappe.throw(_("Unknown body inputs: {0}").format(", ".join(unknown)))
	return {k: v for k, v in inputs.items() if v not in (None, "")}


def template_name(raw: str) -> str:
	name = (raw or "").strip().lower().replace(" ", "_").replace("-", "_")
	if not _NAME_RE.match(name):
		frappe.throw(_("Template name: lowercase letters, digits and _ only (got {0}).").format(raw or "—"))
	return name


def create(library_template_name: str, name: str, language: str, account_name: str = "",
	button_inputs: list = None, body_inputs: dict = None, category: str = LIBRARY_CATEGORY) -> dict:
	"""Create a template from a library entry on Meta, then store it here without posting again."""
	if (category or "").upper() != LIBRARY_CATEGORY:
		frappe.throw(_("Templates from Meta's library are created in the Utility category."))
	if not (library_template_name or "").strip():
		frappe.throw(_("Pick a library template."))
	if not (language or "").strip():
		frappe.throw(_("Pick the template language."))
	name = template_name(name)
	# template sync matches rows by name alone, so a second language of an existing name would overwrite it
	if frappe.db.exists("WhatsApp Templates", {"actual_name": name}):
		frappe.throw(_("A template named {0} already exists here; pick another name.").format(name))

	account = _account(account_name)
	base, headers, waba = _api(account)
	if not waba:
		frappe.throw(_("The WhatsApp account {0} has no Business Account ID.").format(account.name))
	payload = {"name": name, "language": language, "category": LIBRARY_CATEGORY,
		"library_template_name": library_template_name.strip()}
	buttons = build_button_inputs(button_inputs or [])
	if buttons:
		payload["library_template_button_inputs"] = buttons
	body = build_body_inputs(body_inputs or {})
	if body:
		payload["library_template_body_inputs"] = body
	result = _call("post", f"{base}/{waba}/message_templates", account, json=payload, headers=headers)
	if not result.get("id"):
		frappe.throw(_("Meta did not return a template id."), title=_("WhatsApp template library"))

	# Read the template back so the stored copy matches Meta exactly (body, header, buttons).
	try:
		template = _call("get", f"{base}/{result['id']}", account, params={"fields": TEMPLATE_FIELDS}, headers=headers)
	except frappe.ValidationError:
		template = {}
	if not template.get("name"):
		# the next sync fills in the components
		template = {"name": name, "language": language, "category": result.get("category") or LIBRARY_CATEGORY,
			"id": result["id"], "status": result.get("status") or "PENDING", "components": []}
	template.setdefault("status", result.get("status") or "PENDING")
	template.setdefault("library_template_name", library_template_name.strip())

	from excom.excom.doctype.whatsapp_templates.whatsapp_templates import _store_templates

	accounts = frappe.get_all("Excom Channel Account", filters={"status": "Active", "channel": ["in", ["whatsapp", "WhatsApp"]],
		"wa_business_id": waba}, pluck="name") or [account.name]
	if account.name not in accounts:
		accounts.insert(0, account.name)
	_store_templates({"data": [template]}, accounts)
	doc_name = frappe.db.get_value("WhatsApp Templates", {"actual_name": template["name"]}, "name")
	return {"name": doc_name, "id": result["id"], "status": template.get("status"), "category": template.get("category")}
