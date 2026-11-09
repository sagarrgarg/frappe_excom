"""Mail for agents (HLD-007 §11.1, Phase 1B).

- **Unsubscribe.** Every cold email from an agent carries `List-Unsubscribe` (a signed one-click
  link and a mailto) and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058), plus
  the same link in the footer. Using it writes the consent ledger, sets the person's email
  opt-out and stops every sequence to them.
- **Bounces.** A delivery report (channels/email/bounces.py) marks the address invalid on the
  Lead and the research record, stops the sequence, and a mailbox whose hard bounces pass 5% of
  what it sent in a week (20 sends or more) pauses for its agents.
- **Photo cards.** Product photos for emails 1 and 2, built from Item images.
- **Pacing.** A mailbox's daily allowance for an agent: a warm-up ramp (5 a day on the first
  day, 3 more each day) up to its daily cap, nothing while paused; sends in a run are at most 8,
  4 to 15 seconds apart.
- **Health.** SPF, DKIM, DMARC and MX by DNS (DNS over HTTPS, no extra library), a domain
  blocklist lookup, the bounce rate; stored on the agent's channel row, rechecked nightly.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import time
from urllib.parse import quote

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, cint, date_diff, get_datetime, get_url, getdate, now_datetime, nowdate

DOH = "https://cloudflare-dns.com/dns-query"
DKIM_SELECTORS = ("default", "stalwart", "mail", "dkim", "google", "selector1", "selector2", "s1", "k1")
WARMUP_START, WARMUP_STEP, WARMUP_DAYS = 5, 3, 14
RUN_MAX, JITTER = 8, (4, 15)
BOUNCE_PAUSE_RATE, BOUNCE_MIN_SENT = 0.05, 20


# ── unsubscribe ──────────────────────────────────────────────────────────────


def _token(identity: str) -> str:
	secret = frappe.utils.password.get_encryption_key()
	return hmac.new(secret.encode(), f"excom-ai-unsub:{identity}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(identity: str) -> str:
	return get_url(f"/api/method/excom.excom_ai.mail.unsubscribe?i={quote(identity)}&t={_token(identity)}")


def cold_headers(identity: str, account) -> dict:
	"""The unsubscribe headers for one cold email to this person."""
	mailto = f"mailto:{account.email_address}?subject=unsubscribe" if account.get("email_address") else ""
	value = ", ".join(f"<{u}>" for u in filter(None, (unsubscribe_url(identity), mailto)))
	return {"List-Unsubscribe": value, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def footer(identity: str, postal_address: str = "") -> str:
	"""The footer of a cold email: the company's postal address and the personal opt-out link."""
	link = unsubscribe_url(identity)
	address = frappe.utils.escape_html(postal_address or "")
	return (f'<p style="color:#888;font-size:12px;margin-top:24px">{address}{"<br>" if address else ""}'
		f'<a href="{link}" style="color:#888">{_("Unsubscribe")}</a></p>')


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
@rate_limit(limit=30, seconds=60)
def unsubscribe(i: str = "", t: str = "", **kwargs):
	"""The personal unsubscribe link. GET shows a confirm button; POST (the button, or a mail
	client's one-click request per RFC 8058) records the opt-out. Only a matching signature counts."""
	if not i or not t or not frappe.db.exists("Omni Identity", i) or not hmac.compare_digest(_token(i), t):
		frappe.respond_as_web_page(_("Link not valid"), _("This unsubscribe link is not valid."), http_status_code=400)
		return
	if frappe.request and frappe.request.method == "GET":
		frappe.respond_as_web_page(_("Unsubscribe"),
			f'<form method="post"><p>{_("Stop receiving these emails?")}</p>'
			f'<button type="submit" class="btn btn-primary">{_("Unsubscribe")}</button></form>',
			indicator_color="blue")
		return
	opt_out(i, source="ai_unsubscribe_link")
	frappe.db.commit()
	frappe.respond_as_web_page(_("Unsubscribed"), _("You will not receive these emails again."), indicator_color="green")


def opt_out(identity: str, source: str) -> None:
	"""Email opt-out for a person: the ledger, the flag, and every sequence to them stopped."""
	from excom.excom.doctype.omni_identity.omni_identity import set_opt_out
	from excom.excom.services.consent import record_consent

	set_opt_out(identity, channel="email", source=source)
	record_consent(identity, "Withdrawn", channel="email", purpose="marketing", source=source, method="link")
	for enr in _enrollments_of(identity):
		frappe.db.set_value("Excom AI Enrollment", enr, {"status": "Opted out", "stop_reason": _("Unsubscribed")},
			update_modified=False)


def _enrollments_of(identity: str, email: str | None = None) -> list[str]:
	threads = frappe.get_all("Excom Thread", {"omni_identity": identity}, pluck="name") if identity else []
	email = email or (frappe.db.get_value("Omni Identity", identity, "primary_email") if identity else None)
	leads = frappe.get_all("Lead", {"email_id": email}, pluck="name") if email else []
	out = set()
	for field, values in (("thread", threads), ("lead", leads)):
		if values:
			out |= set(frappe.get_all("Excom AI Enrollment", {field: ["in", values], "status": ["in", ("Active", "Paused")]},
				pluck="name"))
	return sorted(out)


# ── bounces ──────────────────────────────────────────────────────────────────


def on_bounce(event: dict) -> None:
	"""`excom_bounce_handlers`: a hard bounce makes the address invalid and stops sequences to it."""
	addr = (event.get("recipient") or "").lower()
	if event.get("kind") == "Hard" and addr:
		for lead in frappe.get_all("Lead", {"email_id": addr}, pluck="name"):
			if frappe.get_meta("Lead").has_field("ai_email_status"):
				frappe.db.set_value("Lead", lead, "ai_email_status", "Invalid", update_modified=False)
		for person in frappe.get_all("Excom AI Person", {"email": addr}, pluck="name"):
			frappe.db.set_value("Excom AI Person", person, "email_status", "Invalid", update_modified=False)
		for enr in _enrollments_of(event.get("identity"), addr):
			frappe.db.set_value("Excom AI Enrollment", enr, {"status": "Bounced", "stop_reason": _("The address bounced")},
				update_modified=False)
	check_bounce_rate(event.get("account"))


def check_bounce_rate(account: str | None) -> bool:
	"""Pause a mailbox for every agent when its hard bounces pass the limit. Returns True if paused."""
	from excom.excom.channels.email.bounces import bounce_rate

	if not account:
		return False
	hard, sent = bounce_rate(account)
	if sent < BOUNCE_MIN_SENT or hard / sent <= BOUNCE_PAUSE_RATE:
		return False
	reason = _("{0} hard bounces from {1} emails in 7 days").format(hard, sent)
	pause(account, reason)
	return True


def pause(account: str, reason: str) -> None:
	from excom.excom_ai.engine import notify

	for row in frappe.get_all("Excom AI Channel", {"channel_account": account, "parenttype": "Excom AI Agent", "paused": 0},
			["name", "parent"]):
		frappe.db.set_value("Excom AI Channel", row.name, {"paused": 1, "pause_reason": reason}, update_modified=False)
		agent = frappe.db.get_value("Excom AI Agent", row.parent, ["agent_name", "owner_user", "backup_user", "acting_owner"],
			as_dict=True)
		notify.alert([agent.acting_owner or agent.owner_user, agent.backup_user, *notify.admins()],
			_("{0} (AI): mailbox {1} paused: {2}").format(agent.agent_name, account, reason),
			doctype="Excom AI Agent", name=row.parent)


# ── photo cards ──────────────────────────────────────────────────────────────


def photo_cards(items: list[str], limit: int = 3) -> str:
	"""Up to `limit` product cards (photo, name, a line of facts) as email-safe HTML. Items without a
	photo are skipped: a card without a picture is not a photo card."""
	cards = []
	for code in items:
		it = frappe.db.get_value("Item", code, ["item_name", "image", "description", "weight_per_unit", "weight_uom"],
			as_dict=True)
		if not it or not it.image:
			continue
		src = it.image if it.image.startswith("http") else get_url(it.image)
		facts = frappe.utils.escape_html(frappe.utils.strip_html_tags(it.description or "")[:90])
		cards.append(
			f'<td style="padding:6px;vertical-align:top;width:{100 // limit}%">'
			f'<img src="{src}" alt="{frappe.utils.escape_html(it.item_name)}" width="160" '
			f'style="display:block;width:100%;max-width:160px;border-radius:6px">'
			f'<div style="font-size:13px;font-weight:600;margin-top:4px">{frappe.utils.escape_html(it.item_name)}</div>'
			f'<div style="font-size:12px;color:#666">{facts}</div></td>')
		if len(cards) >= limit:
			break
	return f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>{"".join(cards)}</tr></table>' if cards else ""


# ── pacing ───────────────────────────────────────────────────────────────────


def allowance(row, day=None) -> int:
	"""How many cold emails this agent may send from this mailbox on a day (today by default)."""
	if cint(row.get("paused")):
		return 0
	cap = cint(row.get("daily_cap")) or 40
	if not row.get("warmup_start"):
		return 0
	days = date_diff(day or nowdate(), getdate(row.warmup_start))
	if days < 0:
		return 0
	return min(cap, WARMUP_START + WARMUP_STEP * days)


def sent_today(account: str) -> int:
	"""Emails agents sent from this mailbox today (their own users wrote them)."""
	return cint(frappe.db.sql("""SELECT COUNT(*) FROM `tabExcom Message` WHERE account = %s AND direction = 'Outbound'
		AND channel = 'email' AND creation >= %s AND created_by_user LIKE %s""",
		(account, get_datetime(nowdate()), "%@ai-agents.invalid"))[0][0])


def room(agent, account: str) -> int:
	"""Cold emails left today for this agent on this mailbox (0 if not its outreach mailbox)."""
	row = next((r for r in agent.channels or [] if r.channel_account == account and r.purpose != "Replies"), None)
	if not row:
		return 0
	return max(0, allowance(row) - sent_today(account))


def send_paced(sends: list, sleep=time.sleep) -> list:
	"""Run up to RUN_MAX send callables, 4 to 15 seconds apart. Returns their results (an
	exception is returned, not raised, so one bad send does not stop the rest)."""
	out = []
	for k, send in enumerate(sends[:RUN_MAX]):
		if k:
			sleep(random.uniform(*JITTER))
		try:
			out.append(send())
		except Exception as e:
			frappe.log_error(title="Excom AI: a paced send failed", message=frappe.get_traceback())
			out.append(e)
	return out


# ── health ───────────────────────────────────────────────────────────────────


def dns_txt(name: str, rtype: str = "TXT") -> list[str]:
	"""Records by DNS over HTTPS. Raises on a network failure (so 'unknown' is not read as 'missing')."""
	r = requests.get(DOH, params={"name": name, "type": rtype}, headers={"accept": "application/dns-json"}, timeout=8)
	r.raise_for_status()
	return [a.get("data", "").strip('"').replace('" "', "") for a in r.json().get("Answer") or []]


def check_domain(domain: str, selector: str | None = None) -> dict:
	"""SPF, DKIM, DMARC, MX and the Spamhaus domain blocklist for a sending domain."""
	out = {"domain": domain, "checked_on": str(now_datetime())}
	try:
		txt = dns_txt(domain)
		out["spf"] = next((t for t in txt if t.lower().startswith("v=spf1")), None)
		dmarc = [t for t in dns_txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
		out["dmarc"] = dmarc[0] if dmarc else None
		out["mx"] = dns_txt(domain, "MX")
		out["dkim"] = None
		for sel in ([selector] if selector else []) + [s for s in DKIM_SELECTORS if s != selector]:
			keys = [t for t in dns_txt(f"{sel}._domainkey.{domain}") if "p=" in t]
			if keys:
				out["dkim"], out["dkim_selector"] = keys[0][:120], sel
				break
		listed = dns_txt(f"{domain}.dbl.spamhaus.org", "A")
		# 127.0.1.x: listed; 127.255.255.x: the blocklist refused a public resolver (unknown).
		out["blocklisted"] = None if any(a.startswith("127.255.255") for a in listed) else any(a.startswith("127.0.1") for a in listed)
	except Exception as e:
		out["error"] = str(e)[:200]
	out["ok"] = bool(out.get("spf") and out.get("dkim") and out.get("dmarc") and out.get("mx")) and not out.get("blocklisted")
	return out


def check_channel(agent_name: str, row_name: str) -> dict:
	row = frappe.get_doc("Excom AI Channel", row_name)
	email = frappe.db.get_value("Excom Channel Account", row.channel_account, "email_address") or ""
	if "@" not in email:
		return {}
	health = check_domain(email.split("@", 1)[1], row.get("dkim_selector"))
	from excom.excom.channels.email.bounces import bounce_rate

	health["hard_bounces_7d"], health["sent_7d"] = bounce_rate(row.channel_account)
	frappe.db.set_value("Excom AI Channel", row.name, {"health": frappe.as_json(health), "health_checked_on": now_datetime()},
		update_modified=False)
	if health.get("blocklisted"):
		pause(row.channel_account, _("the domain {0} is on a blocklist").format(health["domain"]))
	check_bounce_rate(row.channel_account)
	return health


def nightly_health():
	"""Every working agent's mailboxes, once a day. Nothing while the master switch is off."""
	if not frappe.db.get_single_value("Excom AI Settings", "enabled"):
		return
	for row in frappe.db.sql("""SELECT c.name, c.parent FROM `tabExcom AI Channel` c JOIN `tabExcom AI Agent` a
		ON a.name = c.parent JOIN `tabExcom Channel Account` acc ON acc.name = c.channel_account
		WHERE a.status IN ('Shadow', 'Live', 'On Hold') AND acc.channel = 'email'""", as_dict=True):
		try:
			check_channel(row.parent, row.name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Excom AI: mailbox health check failed ({row.parent})", message=frappe.get_traceback())


def warmed_up(row) -> bool:
	return bool(row.get("warmup_start")) and getdate(row.warmup_start) <= getdate(add_days(nowdate(), -WARMUP_DAYS))
