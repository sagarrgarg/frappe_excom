"""The AI setup page (HLD-010): every step an Excom Admin takes before the agents work, in order, with
what is missing said plainly and the fix on the same page.

`status()` is computed from the real records every time (nothing is stored as "done", so it cannot
drift) and makes no network call; DNS, key tests and the voice health check are separate buttons.

Who may do what:
- the page and its buttons: Excom Admin or System Manager (`_check_admin_access`);
- the AI provider (ai_interface) is System Manager only: it carries every app's AI traffic, so an
  Excom Admin sees its state and a link, and a System Manager gets the buttons.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
from urllib.parse import urlsplit, urlunsplit

import frappe
from frappe import _
from frappe.utils import cint, flt, get_url_to_form, getdate, nowdate

from excom.excom.api.chat import _check_admin_access
from excom.excom.utils.ratelimit import user_rate_limit

DONE, ATTENTION, TODO, OPTIONAL, NOT_NEEDED = "done", "attention", "todo", "optional", "not_needed"
WORKING = ("Shadow", "Live", "On Hold")
OUTREACH_TYPES = ("Sales", "Accounts", "Purchase")
PINGABLE = ("hunter", "zerobounce", "prospeo", "trulyinbox")


def _item(label: str, ok: bool, detail: str = "", fix: str | None = None, severity: str = "required") -> dict:
	"""One check. severity: required (blocks the step), recommended (attention only), info."""
	return {"label": label, "ok": bool(ok), "detail": detail, "fix": fix, "severity": severity}


def _state(items: list[dict], empty: str = TODO) -> str:
	if not items:
		return empty
	required = [i for i in items if i["severity"] == "required"]
	if required and not any(i["ok"] for i in required):
		return TODO
	if any(not i["ok"] for i in required):
		return ATTENTION
	if any(not i["ok"] for i in items if i["severity"] == "recommended"):
		return ATTENTION
	return DONE


def _agents() -> list:
	return frappe.get_all("Excom AI Agent", {"status": ["!=", "Retired"]},
		["name", "agent_name", "agent_type", "status", "company", "readiness_checked_on"], order_by="creation")


def _is_system_manager() -> bool:
	return "System Manager" in frappe.get_roles()


def _channel_rows(channel: str) -> list:
	"""Agent channel rows on accounts of this channel (email / whatsapp), with the agent and account."""
	return frappe.db.sql("""SELECT c.name, c.parent AS agent, a.agent_name, a.status AS agent_status, c.channel_account,
		c.purpose, c.paused, c.pause_reason, c.warmup_start, c.health, c.health_checked_on, c.dkim_selector
		FROM `tabExcom AI Channel` c JOIN `tabExcom AI Agent` a ON a.name = c.parent
		JOIN `tabExcom Channel Account` acc ON acc.name = c.channel_account
		WHERE c.parenttype = 'Excom AI Agent' AND a.status != 'Retired' AND acc.channel = %s
		ORDER BY a.creation, c.idx""", channel, as_dict=True)


# ── steps ────────────────────────────────────────────────────────────────────


def _provider() -> dict:
	items = []
	if not frappe.db.exists("DocType", "AI Settings"):
		return {"items": [_item(_("The ai_interface app is installed"), False, _("Install ai_interface on this site."))],
			"system_manager_only": True}
	ai = frappe.get_single("AI Settings")
	provider = ai.default_provider
	items.append(_item(_("A default AI provider"), bool(provider), provider or _("None chosen in AI Settings."),
		get_url_to_form("AI Settings", "AI Settings")))
	if provider and frappe.db.exists("AI Provider", provider):
		p = frappe.get_doc("AI Provider", provider)
		key = p.get_password("api_key", raise_exception=False) or p.get_password("auth_token", raise_exception=False)
		items.append(_item(_("Provider switched on"), bool(p.enabled), "", get_url_to_form("AI Provider", provider)))
		items.append(_item(_("API key saved"), bool(key) or bool(p.type_no_credential), _("set") if key else _("not set"),
			get_url_to_form("AI Provider", provider)))
		models = [m.model_id for m in p.models or [] if m.enabled and m.model_id]
		items.append(_item(_("At least one model switched on"), bool(models), ", ".join(models[:4]) or
			_("Use Fetch models on the provider."), get_url_to_form("AI Provider", provider)))
		items.append(_item(_("Last connection test"), p.health_status in ("Healthy", "Degraded"),
			_("{0}; last success {1}").format(p.health_status or "Unknown", p.last_success or _("never"))
			+ (f" · {p.last_error}" if p.last_error and p.health_status == "Down" else ""), severity="recommended"))
	items.append(_item(_("Default model"), bool(ai.default_model), ai.default_model or _("Not set in AI Settings."),
		get_url_to_form("AI Settings", "AI Settings")))
	return {"items": items, "system_manager_only": True, "provider": provider}


def _budgets() -> dict:
	s = frappe.get_single("Excom AI Settings")
	items = [_item(_("A daily AI budget for all agents"), flt(s.daily_budget_total) > 0,
		_("{0} a day").format(flt(s.daily_budget_total)) if flt(s.daily_budget_total) else _("No limit set."))]
	if frappe.db.exists("DocType", "AI Settings"):
		ai = frappe.get_single("AI Settings")
		excom_row = next((r for r in ai.get("app_budgets") or [] if (r.get("calling_app") or "") == "excom"), None)
		items.append(_item(_("Budgets switched on in AI Settings"), bool(ai.enable_budget),
			_("on") if ai.enable_budget else _("off: no AI spend limit across apps"),
			get_url_to_form("AI Settings", "AI Settings"), severity="recommended"))
		items.append(_item(_("A budget row for Excom in AI Settings"), bool(excom_row), "",
			get_url_to_form("AI Settings", "AI Settings"), severity="recommended"))
	return {"items": items}


def _basics(agents: list) -> dict:
	s = frappe.get_single("Excom AI Settings")
	with_address = {r.company for r in s.postal_addresses or [] if r.company and (r.address or "").strip()}
	companies = sorted({a.company for a in agents if a.agent_type in OUTREACH_TYPES and a.company})
	items = []
	for company in companies:
		items.append(_item(_("Postal address for {0}").format(company), company in with_address,
			_("Every cold email carries it in its footer; without it nothing is sent.")))
	items.append(_item(_("People who hear when an agent needs help"), bool(s.alert_users), ", ".join(
		r.user for r in s.alert_users or [] if r.get("user")) or _("Nobody yet."), severity="recommended"))
	items.append(_item(_("Holidays (quiet dates)"), bool(s.quiet_dates), _("{0} dates").format(len(s.quiet_dates or [])),
		severity="info"))
	items.append(_item(_("Do-not-contact domains"), True, _("{0} domains").format(len(s.dnc_domains or [])), severity="info"))
	return {"items": items, "needed": bool(companies)}


def _email(agents: list) -> dict:
	from excom.excom_ai import mail

	rows = _channel_rows("email")
	items = []
	sending = {r.agent for r in rows if r.purpose in ("Outreach", "Outreach and replies")}
	for a in agents:
		if a.agent_type == "Sales":
			items.append(_item(_("{0} has a mailbox to write from").format(a.agent_name), a.name in sending,
				_("Add an email account with purpose Outreach on the agent's Channels tab.") if a.name not in sending else ""))
	accounts = {}
	for r in rows:
		if r.channel_account not in accounts:
			accounts[r.channel_account] = frappe.db.get_value("Excom Channel Account", r.channel_account,
				["status", "email_address", "email_provider", "email_authorized", "jmap_username"], as_dict=True) or {}
		acc = accounts[r.channel_account]
		where = f"{r.agent_name} · {acc.get('email_address') or r.channel_account}"
		connected = bool(acc.get("email_authorized") or acc.get("jmap_username"))
		health = frappe.parse_json(r.health) if r.health else {}
		items.append(_item(_("{0}: mailbox connected").format(where), acc.get("status") == "Active" and connected,
			_("account {0}; {1}").format(acc.get("status") or "?", _("connected") if connected else _("not connected"))))
		if r.purpose in ("Outreach", "Outreach and replies"):
			if not r.health_checked_on:
				detail = _("Never checked. Press Check DNS: the agent's readiness waits for it.")
			elif health.get("ok"):
				detail = _("SPF, DKIM, DMARC and MX found; not on a blocklist (checked {0}).").format(r.health_checked_on)
			else:
				missing = [k.upper() for k in ("spf", "dkim", "dmarc", "mx") if not health.get(k)]
				detail = _("Missing: {0}").format(", ".join(missing)) if missing else _("On a blocklist.") if health.get(
					"blocklisted") else _("Not passing.")
			items.append(_item(_("{0}: sending records (DNS)").format(where), bool(health.get("ok")), detail,
				fix=f"check_dns:{r.agent}:{r.name}"))
			items.append(_item(_("{0}: warm-up started").format(where), bool(r.warmup_start),
				_("Warmed up") if mail.warmed_up(r) else (_("started {0}; full sending after {1} days").format(
					r.warmup_start, mail.WARMUP_DAYS) if r.warmup_start else _("Without a start date the agent sends nothing."))))
		if r.paused:
			items.append(_item(_("{0}: not paused").format(where), False, r.pause_reason or _("Paused.")))
	return {"items": items, "needed": bool(rows) or any(a.agent_type == "Sales" for a in agents)}


def _whatsapp(agents: list) -> dict:
	s = frappe.get_single("Excom AI Settings")
	rows = _channel_rows("whatsapp")
	items = []
	for acc in sorted({r.channel_account for r in rows}):
		a = frappe.db.get_value("Excom Channel Account", acc, ["status", "wa_display_phone", "wa_quality_rating",
			"wa_health_checked_on"], as_dict=True) or {}
		where = a.get("wa_display_phone") or acc
		items.append(_item(_("{0}: account active").format(where), a.get("status") == "Active", a.get("status") or ""))
		items.append(_item(_("{0}: quality").format(where), (a.get("wa_quality_rating") or "").upper() not in ("RED", "LOW"),
			_("{0} (checked {1}). Diagnose under WhatsApp templates for a live check.").format(
				a.get("wa_quality_rating") or _("unknown"), a.get("wa_health_checked_on") or _("never")), severity="recommended"))
	needs_templates = any(a.agent_type in ("Accounts", "Purchase", "Sales") for a in agents) and bool(rows)
	if needs_templates:
		items.append(_item(_("Template Steward on"), bool(s.templates_enabled),
			_("Writes and submits the WhatsApp templates agents need.") if s.templates_enabled else _("Off: agents write by email only.")))
		items.append(_item(_("WhatsApp templates sent to Meta without a review"), True,
			_("on") if s.whatsapp_auto_submit else _("off: each one waits for an Excom Admin"), severity="info"))
	if rows:
		items.append(_item(_("Opt-in"), True, _("Agents send WhatsApp templates only to people who opted in, or who wrote "
			"to you on WhatsApp. Consent from before Excom can be added as Excom Consent records."), severity="info"))
	return {"items": items, "needed": bool(rows)}


def tool_groups(agents: list) -> list[dict]:
	"""What the agent types in use need, as groups of tools where one is enough (any_of) or each is
	needed (all_of). From the code lists the packs use, never from configuration that runtime ignores."""
	from excom.excom_ai import research
	from excom.excom_ai.packs import compliance, purchase

	types = {a.agent_type for a in agents}
	groups = []
	if "Sales" in types:
		groups += [
			{"label": _("Sales: finding companies"), "any_of": list(research.DISCOVERY), "severity": "required",
				"why": _("OpenStreetMap is free; the others are paid search.")},
			{"label": _("Sales: reading websites"), "all_of": ["web_reader"], "severity": "required", "why": _("Free.")},
			{"label": _("Sales: finding addresses"), "all_of": ["email_finder"], "severity": "required", "why": _("Free.")},
			{"label": _("Sales: checking addresses"), "any_of": list(research.VERIFIERS), "severity": "recommended",
				"why": _("Without a verifier, found addresses stay unverified and are not written to.")},
			{"label": _("Sales: consent check"), "any_of": ["precleared"], "severity": "recommended", "why": ""},
			{"label": _("Sales: named buyers (LinkedIn)"), "any_of": list(research.PEOPLE_PROVIDERS), "severity": "info",
				"why": _("Optional: Apollo or Prospeo.")},
		]
	if "Purchase" in types:
		groups.append({"label": _("Purchase: finding suppliers"), "any_of": list(purchase.DISCOVERY_SOURCES),
			"severity": "info", "why": _("Optional: the monthly supplier search.")})
	if "Compliance" in types:
		groups.append({"label": _("Compliance: regulatory watch"), "any_of": list(compliance.SEARCH_TOOLS),
			"severity": "recommended", "why": _("The weekly search for new rules.")})
	return groups


def _tools(agents: list) -> dict:
	rows = {t.name: t for t in frappe.get_all("Excom AI Tool", fields=["name", "label", "tier", "enabled", "monthly_budget",
		"used_this_month", "last_error", "notes"])}
	from excom.excom_ai import tools

	tools.load()

	# which tools hold a key, in one read (never the keys themselves)
	keyed = set(frappe.db.sql_list("""SELECT name FROM `__Auth` WHERE doctype = 'Excom AI Tool' AND fieldname = 'api_key'
		AND IFNULL(password, '') != ''"""))

	def ready(key: str) -> bool:
		t = rows.get(key)
		if not t or not t.enabled:
			return False
		cls = tools.TOOLS.get(key)
		return key in keyed if cls and cls.needs_key else True

	items, needed = [], set()
	for g in tool_groups(agents):
		keys = g.get("any_of") or g.get("all_of")
		needed |= set(keys)
		ok = any(ready(k) for k in keys) if g.get("any_of") else all(ready(k) for k in keys)
		names = ", ".join(rows[k].label if k in rows else k for k in keys)
		items.append(_item(g["label"], ok, (_("One of: {0}.") if g.get("any_of") else _("Needs: {0}.")).format(names)
			+ (" " + g["why"] if g.get("why") else ""), severity=g["severity"]))
	cards = []
	for key, t in sorted(rows.items(), key=lambda kv: (kv[0] not in needed, kv[1].label or kv[0])):
		cls = tools.TOOLS.get(key)
		cards.append({"key": key, "label": t.label or key, "tier": t.tier, "enabled": cint(t.enabled),
			"needs_key": bool(cls and cls.needs_key), "has_key": (key in keyed) if cls and cls.needs_key else None,
			"monthly_budget": flt(t.monthly_budget), "used_this_month": flt(t.used_this_month),
			"last_error": t.last_error, "notes": t.notes, "needed": key in needed, "can_test": key in PINGABLE})
	return {"items": items, "tools": cards, "needed": bool(items)}


def _voice(agents: list) -> dict:
	from excom.excom_ai.engine import proposals
	from excom.excom_ai.voice import MIN_KEY_LENGTH, stream_url

	s = frappe.get_single("Excom AI Settings")
	key = s.get_password("voice_service_key", raise_exception=False) or ""
	url = stream_url()
	items = [
		_item(_("Voice service address"), bool(url), url or _("Not set: the address of the voice service on its VM.")),
		_item(_("Shared key ({0}+ characters)").format(MIN_KEY_LENGTH), len(key) >= MIN_KEY_LENGTH,
			_("set") if len(key) >= MIN_KEY_LENGTH else (_("too short: AI calls stay off") if key else _("not set"))),
	]
	lines = []
	answering = set(frappe.db.sql_list("""SELECT DISTINCT parent FROM `tabExcom AI Channel` WHERE parenttype = 'Excom AI Agent'
		AND purpose = 'Calls' AND IFNULL(paused, 0) = 0"""))
	for a in agents:
		if a.status != "Live" or a.name not in answering:
			continue
		agent = frappe.get_doc("Excom AI Agent", a.name)
		calls = [c.channel_account for c in agent.channels or [] if c.purpose == "Calls" and not c.paused]
		if calls and proposals.mode_for(agent, "voice_call", policy_action="voice_call") == "Auto":
			lines += [f"{a.agent_name} · {c}" for c in calls]
	items.append(_item(_("A line answered by a Live agent"), bool(lines), ", ".join(lines) or
		_("Set a line's purpose to Calls on a Live agent and its voice_call policy to Auto.")))
	return {"items": items, "needed": False}


def _signals(agents: list) -> dict:
	if not any(a.agent_type == "Sales" for a in agents):
		return {"items": [], "needed": False}
	counts = dict(frappe.db.sql("SELECT status, COUNT(*) FROM `tabExcom AI Signal Code` GROUP BY status"))
	confirmed = cint(counts.get("Confirmed"))
	return {"items": [_item(_("Trade signals confirmed"), confirmed > 0,
		_("{0} confirmed, {1} proposed. Research only collects confirmed signals (what a buyer imports, their country…).")
		.format(confirmed, cint(counts.get("Proposed"))), "/app/excom-ai-signal-code?status=Proposed")], "needed": True}


def _agents_step(agents: list) -> dict:
	gaps = dict(frappe.db.sql("""SELECT agent, COUNT(*) FROM `tabExcom AI Readiness Gap`
		WHERE status = 'Open' AND severity = 'Blocker' GROUP BY agent"""))
	rows, items = [], []
	for a in agents:
		blockers = cint(gaps.get(a.name))
		rows.append({"name": a.name, "agent_name": a.agent_name, "agent_type": a.agent_type, "status": a.status,
			"blockers": blockers, "readiness_checked_on": a.readiness_checked_on})
		if a.status in WORKING:
			items.append(_item(_("{0} ({1}) is ready").format(a.agent_name, a.status), blockers == 0,
				_("{0} blockers open").format(blockers) if blockers else _("no blockers")))
	if not agents:
		items.append(_item(_("At least one agent"), False, _("Build one from the Agents page.")))
	elif not items:
		items.append(_item(_("An agent past Draft"), False, _("Finish building an agent and start it in Shadow.")))
	return {"items": items, "agents": rows}


@frappe.whitelist()
def status() -> dict:
	_check_admin_access()
	agents = _agents()
	steps = []

	def add(id_, title, body, optional=False):
		state = _state(body.get("items") or [])
		if optional and state in (TODO, ATTENTION):
			state = OPTIONAL  # nothing waits for it
		elif not body.get("needed", True) and state != DONE:
			state = NOT_NEEDED  # no agent uses this channel or tool
		steps.append({"id": id_, **body, "title": title, "state": state})

	add("provider", _("AI provider"), _provider())
	add("budgets", _("Budgets"), _budgets())
	add("basics", _("Company basics"), _basics(agents))
	add("email", _("Email sending"), _email(agents))
	add("whatsapp", _("WhatsApp"), _whatsapp(agents))
	add("tools", _("Data tools"), _tools(agents))
	add("signals", _("Trade signals"), _signals(agents))
	add("voice", _("AI phone answering"), _voice(agents), optional=True)
	add("agents", _("Agents"), _agents_step(agents))
	# Going live waits for every needed step that is not started, and for a provider that is not complete.
	blocking = [s["title"] for s in steps if (s["state"] == TODO and s["id"] not in ("voice", "agents"))
		or (s["id"] == "provider" and any(not i["ok"] and i["severity"] == "required" for i in s["items"]))]
	return {"steps": steps, "enabled": cint(frappe.db.get_single_value("Excom AI Settings", "enabled")),
		"missing": blocking, "ready_to_go_live": not blocking, "is_system_manager": _is_system_manager()}


# ── buttons ──────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=10, seconds=3600)
def test_provider() -> dict:
	"""One tiny real call on the default provider (a few hundredths of a cent; it is logged like any call).
	System Manager only, as on the provider form."""
	_check_admin_access()
	frappe.only_for("System Manager")
	from ai_interface.services.ai_client import test_provider as run

	provider = frappe.db.get_single_value("AI Settings", "default_provider")
	if not provider:
		frappe.throw(_("Choose a default AI provider in AI Settings first."))
	out = run(provider)
	frappe.db.commit()
	return {"ok": bool(out.get("ok")), "detail": (_("Answered in {0} ms using {1}.").format(out.get("latency_ms"),
		out.get("model")) if out.get("ok") else str(out.get("error") or out.get("error_type") or _("failed")))[:300]}


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=30, seconds=3600)
def check_dns(agent: str, row: str) -> dict:
	"""SPF, DKIM, DMARC, MX and the blocklist for one agent's mailbox (mail.check_channel: about a dozen
	DNS lookups). Like the nightly check, a domain found on a blocklist pauses that mailbox."""
	_check_admin_access()
	from excom.excom_ai import mail

	if not frappe.db.exists("Excom AI Channel", {"name": row, "parent": agent, "parenttype": "Excom AI Agent"}):
		frappe.throw(_("That mailbox is not on that agent."))
	health = mail.check_channel(agent, row) or {}
	frappe.db.commit()
	paused = cint(frappe.db.get_value("Excom AI Channel", row, "paused"))
	return {"ok": bool(health.get("ok")), "health": health, "paused": paused}


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=3600)
def test_tool(key: str) -> dict:
	"""A free check of a tool's key, where the provider has one. Never through tools.use(): that would
	count a paid tool's cost against its budget."""
	_check_admin_access()
	from excom.excom_ai import tools

	tools.load()
	cls = tools.TOOLS.get(key)
	if not cls or key not in PINGABLE or not frappe.db.exists("Excom AI Tool", key):
		return {"ok": None, "detail": _("No free check for this tool: it is tested on first use, and any error shows here.")}
	record = frappe.get_doc("Excom AI Tool", key)
	if not record.get_password("api_key", raise_exception=False):
		return {"ok": False, "detail": _("No key saved.")}
	try:
		out = cls(record).ping()
	except Exception as e:
		out = {"ok": False, "detail": f"{type(e).__name__}: {e!s}"[:300]}
	frappe.db.set_value("Excom AI Tool", key, "last_error", None if out.get("ok") else (out.get("detail") or "")[:500],
		update_modified=False)
	frappe.db.commit()
	return out


@frappe.whitelist(methods=["POST"])
def make_voice_key(confirm: int = 0) -> dict:
	"""A new 48-character shared key, saved and shown once. AI calls stop until the same key is in the
	voice service's settings (VOICE_KEY) and the service restarts."""
	_check_admin_access()
	if not cint(confirm):
		frappe.throw(_("Confirm first: AI calls stop until the voice service has the new key."))
	key = secrets.token_urlsafe(36)[:48]
	doc = frappe.get_single("Excom AI Settings")
	doc.voice_service_key = key
	doc.save(ignore_permissions=True)  # validate_settings checks the length
	frappe.db.commit()
	return {"key": key}


def _health_target(url: str) -> dict:
	"""Where the service's /health is, from the media-stream setting (wss://host/stream), and the one
	public address to reach it at. The server makes this request, so it must never reach inside the
	network: no IP literals, no user:password@, and every address the name resolves to must be public.
	The request then goes to the address checked here (not a second lookup), so a name that changes
	its answer between the check and the call cannot redirect it."""
	parts = urlsplit((url or "").strip())
	scheme = {"wss": "https", "https": "https"}.get(parts.scheme)
	if parts.scheme in ("ws", "http") and frappe.conf.get("developer_mode"):
		scheme = "http"
	if not scheme or not parts.hostname:
		frappe.throw(_("The voice service address must start with wss:// (or https://)."))
	if parts.username or parts.password:
		frappe.throw(_("The voice service address must not carry a user name or password."))
	try:
		port = parts.port or (443 if scheme == "https" else 80)
	except ValueError:
		frappe.throw(_("The voice service address has an invalid port."))
	host = parts.hostname
	try:
		ipaddress.ip_address(host)
		frappe.throw(_("Use the voice service's host name, not an IP address."))
	except ValueError:
		pass
	try:
		addresses = [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
	except socket.gaierror:
		frappe.throw(_("{0} does not resolve.").format(host))
	if not addresses:
		frappe.throw(_("{0} does not resolve.").format(host))
	if not frappe.conf.get("developer_mode") and not all(ipaddress.ip_address(a).is_global for a in addresses):
		frappe.throw(_("{0} points inside the network; the check only reaches public addresses.").format(host))
	path = (parts.path or "").rstrip("/")
	if path.endswith("/stream"):
		path = path[: -len("/stream")]
	return {"scheme": scheme, "host": host, "port": port, "ip": addresses[0], "path": f"{path}/health?frappe=1",
		"url": urlunsplit((scheme, f"{host}:{port}" if parts.port else host, f"{path}/health", "frappe=1", ""))}


def _health_url(url: str) -> str:
	return _health_target(url)["url"]


@frappe.whitelist(methods=["POST"])
@user_rate_limit(limit=20, seconds=3600)
def check_voice() -> dict:
	"""Is the voice service up, and can it reach this site? It does not prove the keys match: a call
	does (the service refuses one with the wrong key)."""
	_check_admin_access()
	import certifi
	import urllib3

	t = _health_target(frappe.db.get_single_value("Excom AI Settings", "voice_service_url") or "")
	where = f"{t['scheme']}://{t['host']}"
	if t["scheme"] == "https":
		pool = urllib3.HTTPSConnectionPool(t["ip"], port=t["port"], server_hostname=t["host"], assert_hostname=t["host"],
			cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), timeout=urllib3.Timeout(5), retries=False)
	else:
		pool = urllib3.HTTPConnectionPool(t["ip"], port=t["port"], timeout=urllib3.Timeout(5), retries=False)
	try:
		r = pool.urlopen("GET", t["path"], headers={"Host": t["host"], "Accept-Encoding": "identity"}, redirect=False,
			preload_content=False, decode_content=False)
		try:
			body = r.read(2048, decode_content=False).decode("utf-8", "replace")
		finally:
			r.release_conn()
	except urllib3.exceptions.HTTPError as e:
		return {"ok": False, "detail": _("No answer from {0} ({1}).").format(where, type(e).__name__)}
	finally:
		pool.close()
	first = (body.strip().splitlines() or [""])[0][:120]
	return {"ok": r.status == 200, "detail": _("HTTP {0}: {1}").format(r.status, first) + (
		"" if r.status == 200 else " " + _("(503 means the service cannot reach this site.)"))}


@frappe.whitelist(methods=["POST"])
def go_live(enable: int = 1, confirm: int = 0) -> dict:
	"""The master switch. Turning it on while steps are missing needs an explicit confirm, after the
	page has listed what is missing."""
	_check_admin_access()
	if cint(enable):
		missing = status()["missing"]
		if missing and not cint(confirm):
			return {"enabled": 0, "missing": missing}
	frappe.db.set_single_value("Excom AI Settings", "enabled", 1 if cint(enable) else 0)
	frappe.db.commit()
	return {"enabled": cint(enable), "missing": []}
