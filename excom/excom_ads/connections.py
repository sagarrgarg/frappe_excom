"""Connections health: every link the Marketing OS depends on, in one place, with what to fix.

    health()  → sections of items {key, label, status, detail, hint, link}

status: "ok" | "warn" | "error" | "off" (not set up yet). Reads local records only — no platform
calls — so the page is fast and safe to refresh. The "Test" actions (Meta debug_token, Google
connection test) live in their own endpoints and update the records this reads.

Sections: Meta, Google, ad accounts, lead sources, apps (Webshop, India Compliance, ai_interface,
blockers), ad bills, and the attribution chain (ad → lead/chat → order → invoice → ad bill) over the
last 30 days, so a broken link shows up as a number that stays at zero.
"""

import frappe
from frappe.utils import add_days, cint, get_datetime, get_url_to_form, get_url_to_list, now_datetime

from excom.excom_ads import permissions

# Meta permissions each feature needs (Graph login permissions on the system-user token).
META_FEATURES = {
	"Ads reporting and billing": ["ads_read"],
	"Ad accounts and business assets": ["business_management"],
	"Lead ads": ["leads_retrieval", "pages_show_list", "pages_read_engagement", "pages_manage_ads"],
	"Lead ad → ad / ad set / campaign on the Lead": ["ads_management"],
	"Messenger inbox": ["pages_messaging", "pages_manage_metadata"],
	"Instagram DMs": ["instagram_basic", "instagram_manage_messages"],
	"WhatsApp": ["whatsapp_business_messaging", "whatsapp_business_management"],
}
# Which asset type makes a feature relevant, so an unused feature's missing scope is only a note.
FEATURE_ASSET = {"Lead ads": "Lead Form", "Lead ad → ad / ad set / campaign on the Lead": "Lead Form", "Messenger inbox": "Page", "Instagram DMs": "Instagram", "WhatsApp": "WhatsApp Number", "Ads reporting and billing": "Ad Account"}
TOKEN_WARN_DAYS = 14
QUIET_INBOUND_DAYS = 7
CHAIN_DAYS = 30
RANK = {"error": 3, "warn": 2, "off": 1, "ok": 0}


def _item(key, label, status, detail="", hint="", link=None, **extra):
	return {"key": key, "label": label, "status": status, "detail": detail, "hint": hint, "link": link, **extra}


def _section(key, title, items, description=""):
	worst = max((RANK[i["status"]] for i in items), default=0)
	status = next(s for s, r in RANK.items() if r == worst) if items else "off"
	return {"key": key, "title": title, "description": description, "status": status, "items": items}


def _exists(doctype) -> bool:
	return bool(frappe.db.exists("DocType", doctype))


def _companies() -> set | None:
	"""Companies the caller may see (None = all)."""
	scope = permissions.get_scope() or {}
	if scope.get("company") is not None:
		return set(scope["company"])
	if scope.get("workspace") is not None:
		return {r.company for r in frappe.get_all("Ads Workspace", filters={"name": ["in", list(scope["workspace"])]}, fields=["company"])}
	if scope.get("account") is not None or scope.get("brand") is not None:
		return {r.company for r in frappe.get_list("Ads Account", fields=["company"], limit_page_length=0)}
	return None


def _company_filter(companies, field="company") -> dict:
	return {} if companies is None else {field: ["in", sorted(companies) or ["-"]]}


def _days_ago(ts) -> float | None:
	if not ts:
		return None
	return (now_datetime() - get_datetime(ts)).total_seconds() / 86400


def _age(ts) -> str:
	d = _days_ago(ts)
	if d is None:
		return "never"
	if d < 1 / 24:
		return "just now"
	if d < 1:
		return f"{round(d * 24)} h ago"
	return f"{round(d)} d ago"


# ─── Meta ─────────────────────────────────────────────────────────────────────

def meta_section(companies) -> dict:
	if not _exists("Excom Meta Connection"):
		return _section("meta", "Meta (Facebook, Instagram, WhatsApp)", [])
	conns = frappe.get_all("Excom Meta Connection", filters=_company_filter(companies), fields=["name", "company", "status", "token_valid", "token_scopes", "last_synced_at", "app_id", "business_id", "webhook_verify_token", *(_optional_fields("Excom Meta Connection", ["token_expires_at", "data_access_expires_at", "token_checked_at"]))])
	items = []
	if not conns:
		items.append(_item("meta:none", "No Meta Business connection", "off", hint="Admin → Meta Business: add the business ID, app ID/secret and a system-user token, then Discover.", link="/excom/admin/meta"))
	for c in conns:
		doc = frappe.get_doc("Excom Meta Connection", c.name)
		has_token = bool(doc.get_password("system_user_token", raise_exception=False))
		has_secret = bool(doc.get_password("app_secret", raise_exception=False))
		assets = doc.assets or []
		enabled_types = {a.asset_type for a in assets if a.enabled}
		label = f"{c.name} · {c.company}"
		if c.status != "Active":
			items.append(_item(f"meta:{c.name}", label, "off", f"Connection is {c.status}.", link=get_url_to_form("Excom Meta Connection", c.name)))
			continue
		if not has_token:
			items.append(_item(f"meta:{c.name}:token", label, "error", "No system-user token.", "Paste a system-user token from Business Settings → System users.", "/excom/admin/meta"))
			continue
		token_status, token_detail = ("ok", "Token valid") if c.token_valid else ("error", "Token is not valid (last debug_token check)")
		exp = c.get("token_expires_at")
		if exp:
			left = -(_days_ago(exp) or 0)
			if left < 0:
				token_status, token_detail = "error", f"Token expired {_age(exp)}"
			elif left < TOKEN_WARN_DAYS and token_status == "ok":
				token_status, token_detail = "warn", f"Token expires in {round(left)} days"
			else:
				token_detail += f"; expires {str(exp)[:10]}"
		elif c.token_valid:
			token_detail += "; never expires"
		items.append(_item(f"meta:{c.name}:token", f"{label} · token", token_status, f"{token_detail}. Checked {_age(c.get('token_checked_at'))}.", "Run Test to re-check the token and its permissions." if token_status != "ok" else "", "/excom/admin/meta", action={"method": "excom.excom_ads.connections.test_meta", "args": {"name": c.name}, "label": "Test"}))
		if not has_secret:
			items.append(_item(f"meta:{c.name}:secret", f"{label} · app secret", "error", "No app secret: Meta webhooks cannot be verified and are refused.", "Add the app secret from the Meta app dashboard.", "/excom/admin/meta"))
		scopes = {s.strip() for s in (c.token_scopes or "").split(",") if s.strip()}
		for feature, needed in META_FEATURES.items():
			missing = [s for s in needed if s not in scopes]
			used = FEATURE_ASSET.get(feature) in enabled_types if FEATURE_ASSET.get(feature) else bool(assets)
			if not scopes:
				continue
			if missing:
				items.append(_item(f"meta:{c.name}:scope:{feature}", f"{label} · {feature}", "error" if used else "off", f"Token lacks {', '.join(missing)}.", "Grant these permissions to the system user's token (Business Settings → System users → Generate token) and Test again." if used else "Only needed if you use this feature.", "/excom/admin/meta"))
		counts = {}
		for a in assets:
			key = (a.asset_type, bool(a.enabled))
			counts[key] = counts.get(key, 0) + 1
		summary = ", ".join(f"{t}: {counts.get((t, True), 0)} of {counts.get((t, True), 0) + counts.get((t, False), 0)} enabled" for t in sorted({a.asset_type for a in assets}))
		stale = _days_ago(c.last_synced_at)
		items.append(_item(f"meta:{c.name}:assets", f"{label} · assets", "warn" if stale is None or stale > 30 else "ok", f"{summary or 'Nothing discovered yet'}. Discovered {_age(c.last_synced_at)}.", "Run Discover to pick up new pages, forms, numbers and ad accounts." if stale is None or stale > 30 else "", "/excom/admin/meta"))
		gone = [a.asset_name for a in assets if a.enabled and '"not_returned_by_meta": true' in (a.extra or "")]
		if gone:
			items.append(_item(f"meta:{c.name}:gone", f"{label} · assets Meta no longer returns", "warn", ", ".join(gone[:5]) + ("…" if len(gone) > 5 else ""), "The token lost access to these, or they were deleted. Re-share them with the system user or disable them.", "/excom/admin/meta"))
	items += _channel_accounts(companies)
	return _section("meta", "Meta (Facebook, Instagram, WhatsApp)", items, "One Meta app and token store, owned by Excom. Ads reads the same token.")


def _optional_fields(doctype, fields) -> list:
	meta = frappe.get_meta(doctype)
	return [f for f in fields if meta.has_field(f)]


def _channel_accounts(companies) -> list:
	if not _exists("Excom Channel Account"):
		return []
	items = []
	rows = frappe.get_all("Excom Channel Account", filters={"channel": ["in", ["whatsapp", "messenger", "instagram"]], "status": "Active", **_company_filter(companies)}, fields=["name", "account_name", "channel"])
	if not rows:
		return items
	last = {r.account: r.at for r in frappe.db.sql(
		"select account, max(creation) as at from `tabExcom Message` where direction = 'Inbound' and account in %(a)s group by account",
		{"a": tuple(r.name for r in rows)}, as_dict=True)}
	for r in rows:
		at = last.get(r.name)
		quiet = _days_ago(at)
		status = "ok" if quiet is not None and quiet <= QUIET_INBOUND_DAYS else "warn"
		hint = "" if status == "ok" else "No inbound message recently. If customers do message this number/page, check the webhook subscription (Meta app → Webhooks) and the app secret."
		items.append(_item(f"channel:{r.name}", f"{r.channel.title()} · {r.account_name}", status, f"Last inbound message {_age(at)}.", hint, "/excom/admin/meta"))
	return items


# ─── Google ───────────────────────────────────────────────────────────────────

def google_section(companies) -> dict:
	items = []
	if _exists("Ads Connection"):
		conns = frappe.get_all("Ads Connection", filters={"provider": "Google", **_company_filter(companies)}, fields=["name", "connection_name", "company", "status", "token_status", "last_checked_at", "last_ok_at", "last_error", "accessible_accounts", "login_customer_id", "scopes"])
		if not conns:
			items.append(_item("google:none", "No Google connection", "off", hint="Desk → Ads Connection → new: service-account key (added as a user on your Google Ads manager account) or an OAuth refresh token, then Test.", link=get_url_to_list("Ads Connection")))
		for c in conns:
			label = f"{c.connection_name or c.name} · {c.company}"
			if c.status == "Disabled":
				items.append(_item(f"google:{c.name}", label, "off", "Disabled.", link=get_url_to_form("Ads Connection", c.name)))
				continue
			bad = c.token_status in ("Token Problem", "Permission Missing", "Error") or c.status == "Error"
			never = c.token_status in (None, "", "Not Checked")
			status = "error" if bad else "warn" if never else "ok"
			detail = f"{c.token_status or 'Not checked'}; last OK {_age(c.last_ok_at)}; {cint(c.accessible_accounts)} accessible accounts" + (f"; manager {c.login_customer_id}" if c.login_customer_id else "")
			items.append(_item(f"google:{c.name}", label, status, detail, (c.last_error or "")[:300] if bad else ("Run Test to check the credential." if never else ""), get_url_to_form("Ads Connection", c.name), action={"method": "excom.excom_ads.api_google.test_connection", "args": {"connection": c.name}, "label": "Test"}))
	if _exists("Ads Analytics Property"):
		props = frappe.get_list("Ads Analytics Property", fields=["name", "property_name", "sync_enabled", "last_status", "last_success_at", "error_class", "last_error", "tokens_day_remaining"], limit_page_length=0)
		if not props:
			items.append(_item("ga4:none", "No GA4 property enabled", "off", hint="Open the Google Ads Connection → GA4 Properties, then enable your web property (needs the analytics.readonly scope).", link=get_url_to_list("Ads Connection")))
		for p in props:
			if not p.sync_enabled:
				continue
			stale = _days_ago(p.last_success_at)
			status = "error" if p.error_class in ("Token", "Permission", "Invalid") else "warn" if (p.error_class or stale is None or stale > 1.5) else "ok"
			items.append(_item(f"ga4:{p.name}", f"GA4 · {p.property_name or p.name}", status, f"Last good sync {_age(p.last_success_at)}" + (f"; {p.error_class}: {(p.last_error or '')[:200]}" if p.error_class else ""), "", get_url_to_form("Ads Analytics Property", p.name)))
	return _section("google", "Google (Ads, GA4)", items, "Service account or OAuth on Ads Connection; one credential serves Ads and GA4.")


# ─── ad accounts ──────────────────────────────────────────────────────────────

def accounts_section() -> dict:
	items = []
	rows = frappe.get_list("Ads Account", fields=["name", "account_name", "provider", "status", "sync_enabled", "error_class", "last_error", "last_metrics_sync", "last_structure_sync", "consecutive_failures"], limit_page_length=0)
	if not rows:
		items.append(_item("accounts:none", "No ad accounts enabled", "off", hint="Enable ad accounts from Meta Discover or the Google connection's account list.", link="/excom/admin/meta"))
	for r in rows:
		if not r.sync_enabled:
			items.append(_item(f"acc:{r.name}", f"{r.provider} · {r.account_name}", "off", "Sync is off.", link=get_url_to_form("Ads Account", r.name)))
			continue
		if r.provider == "Manual":
			items.append(_item(f"acc:{r.name}", f"Manual · {r.account_name}", "ok", f"Figures from CSV imports; last data {_age(r.last_metrics_sync)}.", link=get_url_to_form("Ads Account", r.name)))
			continue
		stale = _days_ago(r.last_metrics_sync)
		if r.error_class in ("Token", "Permission", "Invalid"):
			status, hint = "error", (r.last_error or "")[:300]
		elif r.status not in ("Active", "Unknown"):
			status, hint = "error", f"The platform reports this account as {r.status}."
		elif r.error_class or stale is None or stale > 0.25:
			status, hint = "warn", (r.last_error or "Results are older than 6 hours.")[:300]
		else:
			status, hint = "ok", ""
		items.append(_item(f"acc:{r.name}", f"{r.provider} · {r.account_name}", status, f"{r.status}; results {_age(r.last_metrics_sync)}; tree {_age(r.last_structure_sync)}", hint, get_url_to_form("Ads Account", r.name)))
	return _section("accounts", "Ad accounts", items, "Every enabled account syncs results every 30 minutes, its campaign tree hourly and a 28-day settle daily.")


# ─── lead sources ─────────────────────────────────────────────────────────────

def sources_section(companies) -> dict:
	items = []
	if not _exists("Excom Source"):
		return _section("sources", "Lead sources", items)
	rows = frappe.get_all("Excom Source", filters={"enabled": 1, **_company_filter(companies)}, fields=["name", "source_name", "source_type", "mode", "last_success_at", "form_id", "google_webhook_url"])
	# Manual sources are attribution labels for leads typed in by people, not feeds: one line for all.
	manual = [r for r in rows if r.source_type in ("Manual", "Exhibition")]
	rows = [r for r in rows if r.source_type not in ("Manual", "Exhibition")]
	if manual:
		items.append(_item("sources:manual", f"{len(manual)} manual / exhibition labels", "ok", "Used to tag leads entered by hand; nothing to connect.", link="/excom/admin/intake"))
	if not rows and not manual:
		items.append(_item("sources:none", "No lead sources enabled", "off", hint="Enable Meta lead forms from Meta Discover, or add sources under Admin → Sources.", link="/excom/admin/intake"))
	since = add_days(now_datetime(), -7)
	stats = {}
	if rows:
		for r in frappe.db.sql("select source, status, count(*) as n, max(received_at) as last from `tabExcom Source Log` where source in %(s)s and received_at >= %(since)s group by source, status", {"s": tuple(r.name for r in rows), "since": since}, as_dict=True):
			stats.setdefault(r.source, {})[r.status] = r
		last_any = {r.source: r.last for r in frappe.db.sql("select source, max(received_at) as last from `tabExcom Source Log` where source in %(s)s group by source", {"s": tuple(r.name for r in rows)}, as_dict=True)}
	for r in rows:
		s = stats.get(r.name, {})
		failed = cint((s.get("Failed") or {}).get("n"))
		got = sum(cint(v.n) for v in s.values())
		status = "error" if failed else "ok"
		detail = f"{got} received in 7 days" + (f", {failed} failed" if failed else "") + f"; last {_age(last_any.get(r.name))}"
		hint = "Open the source log to see why intake failed; failed entries are retried by the next copy of the lead." if failed else ""
		if r.source_type == "Google Lead Form":
			src = frappe.get_doc("Excom Source", r.name)
			if not src.get_password("google_key", raise_exception=False):
				status, hint = "error", "Set the webhook key and paste the same key and the webhook URL into the Google Ads lead form."
		items.append(_item(f"source:{r.name}", f"{r.source_type} · {r.source_name}", status, detail, hint, get_url_to_list("Excom Source Log") + f"?source={r.name}"))
	return _section("sources", "Lead sources", items, "Webhooks first, with scheduled recovery pulls. Every submission is stored raw before mapping.")


# ─── apps ─────────────────────────────────────────────────────────────────────

def apps_section() -> dict:
	installed = set(frappe.get_installed_apps())
	items = []
	# Webshop: orders carry campaign parameters onto the Sales Order.
	if "webshop" in installed:
		has = frappe.get_meta("Sales Order").has_field("storefront_utm_source")
		if not has:
			items.append(_item("app:webshop", "Webshop → Sales Order campaign fields", "error", "The storefront_* fields are missing on Sales Order.", "Run bench migrate so Webshop's custom fields are created."))
		else:
			n = frappe.db.count("Sales Order", {"docstatus": 1, "creation": [">=", add_days(now_datetime(), -CHAIN_DAYS)], "storefront_cart": ["is", "set"]})
			tagged = frappe.db.sql("select count(*) from `tabSales Order` where docstatus = 1 and creation >= %s and storefront_cart is not null and (coalesce(storefront_utm_source, '') != '' or coalesce(storefront_gclid, '') != '' or coalesce(storefront_fbclid, '') != '')", add_days(now_datetime(), -CHAIN_DAYS))[0][0] if frappe.get_meta("Sales Order").has_field("storefront_gclid") else 0
			items.append(_item("app:webshop", "Webshop → Sales Order campaign fields", "ok" if n == 0 or tagged else "warn", f"{n} storefront orders in {CHAIN_DAYS} days, {tagged} with UTMs or click IDs.", "" if n == 0 or tagged else "Check that your ads' links carry UTM parameters (and auto-tagging is on in Google Ads)."))
	else:
		items.append(_item("app:webshop", "Webshop", "off", "Not installed on this site: website orders are not attributed."))
	# India Compliance: GST on ad bills.
	if "india_compliance" in installed:
		gs = frappe.get_cached_doc("GST Settings") if _exists("GST Settings") else None
		overseas = cint(gs.get("enable_overseas_transactions")) if gs else 0
		api = bool(gs and cint(gs.get("enable_api")) and gs.get_password("api_secret", raise_exception=False))
		items.append(_item("app:ic:overseas", "India Compliance · overseas transactions", "ok" if overseas else "warn", "Enabled." if overseas else "Off: bills from Meta Ireland or Google Asia Pacific go to review.", "" if overseas else "GST Settings → enable Overseas Transactions (needed for reverse-charge ad bills).", get_url_to_form("GST Settings", "GST Settings")))
		items.append(_item("app:ic:2b", "India Compliance · GSTR-2B download", "ok" if api else "warn", "API account set." if api else "No India Compliance API account: GSTR-2B is not downloaded, so missing ad bills are not detected.", "" if api else "Subscribe to the India Compliance API and log in to GSTN per company GSTIN.", get_url_to_form("GST Settings", "GST Settings")))
	else:
		items.append(_item("app:ic", "India Compliance", "off", "Not installed: ad bills cannot get GST."))
	if "location_based_series" in installed and _exists("Accounting Dimension"):
		has_location = frappe.db.exists("Accounting Dimension", {"document_type": "Location"})
		if not has_location:
			items.append(_item("app:lbs", "Location Based Series blocks Purchase Invoices", "error", "It refuses every Purchase Invoice unless a 'Location' accounting dimension exists, so ad bills end in Error.", "Create the Location accounting dimension, or uninstall location_based_series from this site.", get_url_to_list("Accounting Dimension")))
	if "ai_interface" in installed:
		ok = _exists("AI Settings") and bool(frappe.db.get_single_value("AI Settings", "default_provider") if frappe.get_meta("AI Settings").has_field("default_provider") else True)
		items.append(_item("app:ai", "ai_interface (Phase 2 AI analyst)", "ok" if ok else "off", "Installed" + ("; a default provider is set." if ok else "; no default AI provider yet."), "" if ok else "Phase 2: set up an AI provider in AI Settings."))
	else:
		items.append(_item("app:ai", "ai_interface (Phase 2 AI analyst)", "off", "Not installed on this site."))
	return _section("apps", "Connected apps", items, "Excom owns people and conversations, Webshop owns orders, India Compliance owns GST.")


# ─── ad bills ─────────────────────────────────────────────────────────────────

def bills_section(companies) -> dict:
	items = []
	if not _exists("Ads Bill Source") or not permissions.has_capability("view_finance"):
		return _section("bills", "Ad bills", items)
	srcs = frappe.get_list("Ads Bill Source", fields=["name", "source_name", "provider", "enabled", "last_status", "last_fetched_at", "last_error"], limit_page_length=0)
	if not srcs:
		items.append(_item("bills:none", "No ad-bill sources", "off", hint="Only Meta credit-line and Google monthly-invoicing accounts have invoice APIs. Add an Ads Bill Source for each; other bills are uploaded as documents.", link=get_url_to_list("Ads Bill Source")))
	for s in srcs:
		if not s.enabled:
			continue
		st = {"Success": "ok", "Not On Invoicing": "off", "Partial": "warn", "Failed": "error"}.get(s.last_status or "", "warn")
		items.append(_item(f"billsrc:{s.name}", f"{s.provider} bills · {s.source_name}", st, f"{s.last_status or 'Never fetched'}; fetched {_age(s.last_fetched_at)}", (s.last_error or "")[:300], get_url_to_form("Ads Bill Source", s.name)))
	if _exists("Ads Bill Account Map"):
		mapped = set(frappe.get_list("Ads Bill Account Map", filters={"enabled": 1}, pluck="account", limit_page_length=0))
		unmapped = [a for a in frappe.get_list("Ads Account", filters={"sync_enabled": 1, "provider": ["!=", "Manual"]}, fields=["name", "account_name"], limit_page_length=0) if a.name not in mapped]
		if unmapped:
			items.append(_item("bills:unmapped", "Ad accounts without a bill map", "warn", ", ".join(a.account_name for a in unmapped[:5]) + ("…" if len(unmapped) > 5 else ""), "Map each account to its company GSTIN, supplier, expense account and TDS category, or its bills cannot become Purchase Invoices.", get_url_to_list("Ads Bill Account Map")))
		mismatch = frappe.get_list("Ads Bill Account Map", filters={"enabled": 1, "gstin_status": "Mismatch"}, pluck="name", limit_page_length=0)
		if mismatch:
			items.append(_item("bills:gstin", "GSTIN mismatch on ad accounts", "error", f"{len(mismatch)} account(s): the GSTIN the platform holds differs from the company's.", "Fix the GSTIN in the ad platform's billing settings now: Google and LinkedIn will not re-issue invoices, and the GST credit is lost.", get_url_to_list("Ads Bill Account Map")))
	if _exists("Ads Bill Document"):
		review = frappe.get_list("Ads Bill Document", filters={"status": ["in", ["Needs Review", "Error"]]}, pluck="name", limit_page_length=0)
		if review:
			items.append(_item("bills:review", "Ad bills waiting for review", "warn", f"{len(review)} bill(s) need a person.", "Open Marketing → Ad bills.", "/excom/marketing/bills"))
	return _section("bills", "Ad bills → Purchase Invoices", items, "API invoices become draft GST Purchase Invoices; drafts are submitted only when every check passes.")


# ─── the chain ────────────────────────────────────────────────────────────────

def chain_section() -> dict:
	"""Counts along ad → lead / chat → order → invoice → ad bill. A zero after a non-zero shows where it breaks."""
	since = add_days(now_datetime(), -CHAIN_DAYS)
	steps = []
	snap = frappe.get_list("Ads Metric Snapshot", filters={"level": "Account", "date": [">=", since.date()]}, fields=["spend", "leads", "messaging_conversations"], limit_page_length=0)
	spend = sum(r.spend or 0 for r in snap)
	steps.append(("Ad results synced", len(snap), "account-days with platform results"))
	platform_leads = sum(r.leads or 0 for r in snap)
	from excom.excom.services import crm_gateway as gw

	lead_meta = frappe.get_meta(gw.LEAD)
	ad_leads = linked = pending = 0
	if lead_meta.has_field("ads_link_status"):
		for r in frappe.get_list(gw.LEAD, filters={"creation": [">=", since], "ads_touch_source": ["is", "set"], "ads_is_test": 0}, fields=["ads_link_status"], limit_page_length=0):
			ad_leads += 1
			linked += r.ads_link_status == "Linked"
			pending += r.ads_link_status in ("Pending", "Partial")
	steps.append(("Leads from ads in ERPNext", ad_leads, f"{linked} linked to their campaign, {pending} waiting for the mirror; platforms report {round(platform_leads)}"))
	ctwa = frappe.db.count("Excom Thread", {"ad_source_type": ["is", "set"], "creation": [">=", since]}) if frappe.get_meta("Excom Thread").has_field("ad_source_type") else 0
	steps.append(("Chats from click-to-WhatsApp ads", ctwa, "threads with an ad referral"))
	orders = frappe.db.count("Ads Attribution Touch", {"touch_source": "Order", "touch_at": [">=", since]}) if _exists("Ads Attribution Touch") else 0
	steps.append(("Orders carrying campaign data", orders, "order touches"))
	if permissions.has_capability("view_finance") and _exists("Ads Bill Document"):
		bills = frappe.db.count("Ads Bill Document", {"creation": [">=", since]})
		steps.append(("Ad bills received", bills, "bill documents (API or uploaded)"))
	items = []
	prev_nonzero = False
	for i, (label, n, detail) in enumerate(steps):
		status = "ok" if n else ("warn" if prev_nonzero else "off")
		items.append(_item(f"chain:{i}", label, status, f"{n} in {CHAIN_DAYS} days — {detail}", "" if n or not prev_nonzero else "Something upstream has data but this step has none: check the connection that feeds it.", None, count=n))
		prev_nonzero = prev_nonzero or bool(n)
	return _section("chain", f"Attribution chain (last {CHAIN_DAYS} days)", items, f"Ad spend in the period: {round(spend):,} (company currency). Platform and verified figures stay separate.")


# ─── endpoints ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def health() -> dict:
	permissions.require_access()
	permissions.require("manage_connections")
	companies = _companies()
	sections = []
	for build in (lambda: meta_section(companies), lambda: google_section(companies), accounts_section, lambda: sources_section(companies), apps_section, lambda: bills_section(companies), chain_section):
		try:
			sections.append(build())
		except Exception:
			frappe.log_error(title="Excom Ads: connections health section failed", message=frappe.get_traceback())
			sections.append(_section("error", "A section could not be read", [_item("error", "See the Error Log", "error")]))
	counts = {s: 0 for s in RANK}
	for sec in sections:
		for it in sec["items"]:
			counts[it["status"]] += 1
	return {"sections": [s for s in sections if s["items"]], "counts": counts, "checked_at": now_datetime()}


@frappe.whitelist(methods=["POST"])
def test_meta(name: str) -> dict:
	"""debug_token on an Excom Meta Connection inside the caller's scope; records validity, scopes and expiry."""
	permissions.require_access()
	permissions.require("manage_connections")
	company = frappe.db.get_value("Excom Meta Connection", name, "company")
	allowed = _companies()
	if company is None or (allowed is not None and company not in allowed):
		raise frappe.PermissionError
	from excom.excom.services import meta_connect

	try:
		data = meta_connect.debug_token(name)
	except frappe.ValidationError as e:
		return {"ok": False, "error": str(e)[:300]}
	return {"ok": bool(data.get("is_valid")), "scopes": data.get("scopes") or [], "expires_at": data.get("expires_at")}


# ─── daily credential check ───────────────────────────────────────────────────

EXPIRY_STEPS = ((1, "Critical"), (7, "High"), (14, "Medium"))


def check_credentials() -> dict:
	"""Daily: debug_token every active Meta connection and test every Google connection; alert on an
	invalid credential and 14 / 7 / 1 days before a Meta token expires; resolve when fixed."""
	from excom.excom_ads.alerts import raise_alert, resolve_alert

	out = {"meta": 0, "google": 0}
	if _exists("Excom Meta Connection"):
		from excom.excom.services import meta_connect

		for c in frappe.get_all("Excom Meta Connection", filters={"status": "Active"}, fields=["name", "company"]):
			key, scope = f"meta_token:{c.name}", {"company": c.company}
			try:
				data = meta_connect.debug_token(c.name)
			except Exception as e:
				# A network blip is not a broken token: only a definite answer raises the alert.
				frappe.log_error(title=f"Excom Ads: Meta token check failed for {c.name}", message=frappe.get_traceback())
				if "Graph 4" in str(e):
					raise_alert("Token Problem", "Critical", f"Meta connection {c.name}: token check refused", str(e)[:500], scope=scope, dedupe_key=key)
				continue
			out["meta"] += 1
			if not data.get("is_valid"):
				raise_alert("Token Problem", "Critical", f"Meta connection {c.name}: token is not valid", "Generate a new system-user token and paste it on the connection. Inbox, lead ads and ads syncs for this business stop until then.", scope=scope, dedupe_key=key)
				continue
			resolve_alert(key, "Token valid again")
			exp_key = f"meta_token_expiry:{c.name}"
			expires = int(data.get("expires_at") or 0)
			left = (expires - frappe.utils.now_datetime().timestamp()) / 86400 if expires else None
			step = next(((d, sev) for d, sev in EXPIRY_STEPS if left is not None and left <= d), None)
			if step:
				raise_alert("Token Problem", step[1], f"Meta connection {c.name}: token expires in {max(0, round(left))} day(s)", "Generate a new token before it expires (system-user tokens can be made non-expiring).", scope=scope, dedupe_key=exp_key)
			else:
				resolve_alert(exp_key, "Token no longer close to expiry")
	if _exists("Ads Connection"):
		from excom.excom_ads import google_connect

		for c in frappe.get_all("Ads Connection", filters={"provider": "Google", "status": ["!=", "Disabled"]}, pluck="name"):
			try:
				google_connect.test(c)  # records health and raises / resolves its own alerts
				out["google"] += 1
			except Exception:
				frappe.log_error(title=f"Excom Ads: Google connection check failed for {c}", message=frappe.get_traceback())
	return out
