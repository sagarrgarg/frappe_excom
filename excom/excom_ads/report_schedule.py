"""Scheduled report delivery: due periods, one Ads Report Delivery per report × period × recipient ×
channel, idempotent sends, Report Delivery Failed alerts.

Idempotency. A delivery row is named from (template, period key, recipient, channel) and is
*claimed* (inserted, or moved Failed → Sending with a conditional UPDATE) and committed before
anything is sent. Whoever wins the claim sends; a second job, a retry or a double click finds the
row and stops. An email is queued (Email Queue) in the same transaction that marks the row Sent, so
a crash in between sends nothing and the row can be retried. A WhatsApp send is an external call:
a row found stuck in Sending is marked Failed ("outcome unknown") and is not retried, because
sending twice is worse than asking a person to check.

Period keys: daily `D2026-10-18`, weekly `W2026-42`, monthly `M2026-10`, in the template's time
zone; "send now" uses `now:<minute>`. The template's `last_period_key` is set once every delivery
for the period is final (Sent / Skipped / Failed after MAX_ATTEMPTS), so the 15-minute scheduler
retries failures inside the period's grace window and never resends what went out.

Recipient scope (see reports.py): users get the report rendered as themselves and are skipped
when their ads scope does not cover the template; external addresses get the owner's view without
billing, only while the owner holds export_data; WhatsApp only goes to users.
"""

import hashlib
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_time, getdate, now_datetime, validate_email_address

from excom.excom_ads import permissions, reports
from excom.excom_ads.alerts import raise_alert, resolve_alert

TEMPLATE = reports.TEMPLATE
DELIVERY = reports.DELIVERY
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
GRACE = {"Daily": timedelta(hours=12), "Weekly": timedelta(days=2), "Monthly": timedelta(days=3)}
MAX_ATTEMPTS = 3
STUCK_AFTER = timedelta(minutes=30)
MAX_EMAIL_BYTES = 15 * 1024 * 1024
FINAL = ("Sent", "Skipped")
# send_transactional "skipped" reasons that a retry cannot fix and that need nobody's attention.
WA_SKIP = {"no_contact", "do_not_contact", "no_reachable_channel", "no_whatsapp_number"}


def failure_key(template: str) -> str:
	return f"report_delivery:{template}"


def delivery_name(template: str, period_key: str, recipient: str, channel: str) -> str:
	h = hashlib.sha1(f"{template}\x1f{period_key}\x1f{(recipient or '').lower()}\x1f{channel}".encode()).hexdigest()[:20]
	return f"ADS-RPT-{h}"


def manual_period_key(now) -> str:
	return "now:" + get_datetime(now).strftime("%Y-%m-%dT%H:%M")


# ─── when ─────────────────────────────────────────────────────────────────────

def _time_of(t):
	# Midnight is a real send time: a Time of 00:00:00 reads back as timedelta(0), which is falsy.
	v = t.get("send_time")
	return get_time(v if v not in (None, "") else "09:00:00")


def _send_at(t, day) -> datetime:
	return datetime.combine(getdate(day), _time_of(t))


def due_period(t, now_local: datetime) -> tuple[str, datetime] | None:
	"""(period key, local due time) when the template is due at `now_local`, else None.

	Only the current period is ever due, and only inside its grace window: a scheduler that was
	down for a week sends this week's report, not seven daily ones."""
	freq = t.get("frequency") or "Weekly"
	today = now_local.date()
	if freq == "Daily":
		day = today
		key = f"D{day.isoformat()}"
	elif freq == "Weekly":
		wd = WEEKDAYS.index(t.get("weekday") or "Monday")
		day = today - timedelta(days=(today.weekday() - wd) % 7)
		y, w, _d = day.isocalendar()
		key = f"W{y}-{w:02d}"
	elif freq == "Monthly":
		dom = min(max(cint(t.get("day_of_month")) or 1, 1), 28)
		day = today.replace(day=dom) if today.day >= dom else (today.replace(day=1) - timedelta(days=1)).replace(day=dom)
		key = f"M{day.year}-{day.month:02d}"
	else:
		return None
	at = _send_at(t, day)
	if not (at <= now_local < at + GRACE[freq]):
		return None
	return key, at


def next_run_label(t) -> str | None:
	"""Human hint for the screens: the next local send time."""
	try:
		tz = reports.template_tz(t)
		now = reports.local_now(tz)
	except Exception:
		return None
	due = due_period(t, now)
	if due and due[0] != t.get("last_period_key"):
		return _("due now ({0})").format(tz)
	for days in range(0, 40):
		probe = datetime.combine(now.date() + timedelta(days=days), _time_of(t))
		if probe < now:
			continue
		p = due_period(t, probe)
		if p and p[0] != t.get("last_period_key"):
			return f"{probe:%Y-%m-%d %H:%M} {tz}"
	return None


def enqueue_due() -> int:
	"""Every 15 minutes: queue each report whose period is due and not yet fully delivered."""
	if not frappe.db.exists("DocType", TEMPLATE):
		return 0
	rows = frappe.get_all(TEMPLATE, filters={"enabled": 1, "schedule_enabled": 1}, fields=["name", "company", "workspace", "frequency", "send_time", "weekday", "day_of_month", "time_zone", "last_period_key"])
	queued = 0
	for t in rows:
		try:
			p = due_period(t, reports.local_now(reports.template_tz(t)))
		except Exception:
			frappe.log_error(title=f"Excom Ads: report schedule check failed for {t.name}", message=frappe.get_traceback())
			continue
		if not p or p[0] == t.last_period_key:
			continue
		frappe.enqueue("excom.excom_ads.report_schedule.run", queue="long", template=t.name, period_key=p[0], job_id=f"excom_ads_report::{frappe.local.site}::{t.name}::{p[0]}", deduplicate=True)
		queued += 1
	return queued


# ─── who ──────────────────────────────────────────────────────────────────────

def targets(t) -> list[frappe._dict]:
	"""Distinct recipients. An external address that belongs to an enabled system user is that
	user (their own scope), never the owner's view."""
	out, seen = [], set()
	for r in t.get("recipients") or []:
		if r.recipient_type == "User" and r.user:
			key = ("user", r.user)
			item = frappe._dict(kind="user", user=r.user, id=r.user)
		elif r.recipient_type == "Email" and r.email:
			email = r.email.strip().lower()
			user = frappe.db.get_value("User", {"email": email, "enabled": 1, "user_type": "System User"}, "name")
			key = ("user", user) if user else ("email", email)
			item = frappe._dict(kind="user", user=user, id=user) if user else frappe._dict(kind="email", email=email, id=email)
		else:
			continue
		if key in seen:
			continue
		seen.add(key)
		out.append(item)
	return out


def channels(t) -> list[str]:
	from excom.excom_ads.settings import get_settings

	out = []
	if cint(t.get("via_email")):
		out.append("Email")
	if cint(t.get("via_whatsapp")) and get_settings().report_whatsapp_template:
		out.append("WhatsApp")
	return out


def _skip_reason(t, target, channel: str) -> tuple[str | None, str | None, str | None]:
	"""(reason to skip, address, user to render as)."""
	if target.kind == "email":
		if channel != "Email":
			return _("WhatsApp goes to users only"), None, None
		try:
			validate_email_address(target.email, throw=True)
		except Exception:
			return _("Not a valid email address"), target.email, None
		owner = t.owner
		enabled = frappe.db.get_value("User", owner, "enabled") if owner != "Administrator" else 1
		if not enabled:
			return _("The report's owner {0} is disabled; external recipients are not sent").format(owner), target.email, None
		if not permissions.has_capability("export_data", owner):
			return _("The report's owner {0} no longer has the export data right").format(owner), target.email, None
		if permissions.get_scope(owner) is None or not permissions.doc_allowed(t, owner):
			return _("The report is outside its owner's ads scope"), target.email, None
		return None, target.email, owner
	u = frappe.db.get_value("User", target.user, ["enabled", "user_type", "email", "mobile_no"], as_dict=True)
	if not u or not u.enabled or (u.user_type != "System User" and target.user != "Administrator"):
		return _("User {0} is disabled or not a system user").format(target.user), None, None
	if permissions.get_scope(target.user) is None:
		return _("User {0} has no ads access").format(target.user), None, None
	if not permissions.doc_allowed(t, target.user):
		return _("The report's scope is outside {0}'s ads scope").format(target.user), None, None
	address = u.email if channel == "Email" else u.mobile_no
	if not address:
		return (_("User has no email address") if channel == "Email" else _("User has no mobile number")), None, None
	return None, address, target.user


# ─── claim ────────────────────────────────────────────────────────────────────

def _claim(name: str, values: dict) -> bool:
	"""True when this job owns the delivery now (committed)."""
	row = frappe.db.get_value(DELIVERY, name, ["status", "attempts", "modified", "channel"], as_dict=True)
	now = now_datetime()
	if not row:
		doc = frappe.get_doc({"doctype": DELIVERY, **values, "status": "Sending", "attempts": 1})
		doc.name = name
		try:
			doc.insert(ignore_permissions=True, set_name=name)
		except frappe.DuplicateEntryError:
			frappe.db.rollback()
			return False
		frappe.db.commit()
		return True
	if row.status in FINAL:
		return False
	if row.status == "Sending":
		if now - get_datetime(row.modified) < STUCK_AFTER:
			return False  # another job is on it
		if row.channel == "WhatsApp":
			frappe.db.set_value(DELIVERY, name, {"status": "Failed", "attempts": MAX_ATTEMPTS, "error": _("Outcome unknown: the job stopped mid-send. Not retried, to avoid sending twice; check with the recipient.")})
			frappe.db.commit()
			return False
	if row.status == "Failed" and cint(row.attempts) >= MAX_ATTEMPTS:
		return False
	frappe.db.sql(f"update `tab{DELIVERY}` set status = 'Sending', attempts = attempts + 1, error = null, modified = %s where name = %s and status = %s and modified = %s", (now, name, row.status, row.modified))
	won = bool(frappe.db.sql("select row_count()")[0][0])
	frappe.db.commit()
	return won


def _finish(name: str, status: str, **values) -> None:
	frappe.db.set_value(DELIVERY, name, {"status": status, **values})


# ─── send ─────────────────────────────────────────────────────────────────────

def run(template: str, period_key: str, triggered_by: str = "Scheduler", today=None) -> dict:
	"""Deliver one period of one report to everyone. Safe to run again: rows already Sent or
	Skipped are left alone."""
	t = frappe.get_doc(TEMPLATE, template)
	out = {"sent": 0, "skipped": 0, "failed": 0, "already": 0}
	if not cint(t.enabled):
		return out
	tz = reports.template_tz(t)
	today = getdate(today) if today else reports.local_now(tz).date()
	a, b = reports.period(t.date_preset or "Last 7 days", today)
	cache: dict = {}
	chans = channels(t)
	for target in targets(t):
		for ch in chans:
			if target.kind == "email" and ch != "Email":
				continue
			name = delivery_name(t.name, period_key, target.id, ch)
			values = {"template": t.name, "period_key": period_key, "period_from": a, "period_to": b, "channel": ch, "recipient_user": target.get("user"), "recipient_email": target.get("email"), "company": t.company, "workspace": t.workspace, "brand": t.brand, "triggered_by": triggered_by}
			if not _claim(name, values):
				out["already"] += 1
				continue
			try:
				status = _deliver(t, target, ch, name, a, b, today, cache)
			except Exception as e:
				frappe.db.rollback()
				frappe.log_error(title=f"Excom Ads: report delivery failed ({t.name})", message=frappe.get_traceback())
				_finish(name, "Failed", error=str(e)[:1000] or e.__class__.__name__)
				status = "Failed"
			frappe.db.commit()
			out[{"Sent": "sent", "Skipped": "skipped", "Failed": "failed"}[status]] += 1
	_settle(t, period_key)
	frappe.db.commit()
	return out


def _deliver(t, target, channel: str, name: str, a, b, today, cache: dict) -> str:
	reason, address, render_as = _skip_reason(t, target, channel)
	if reason:
		_finish(name, "Skipped", error=reason[:1000], recipient_email=address)
		return "Skipped"
	external = target.kind == "email"
	key = (render_as, external)
	if key not in cache:
		with reports.as_user(render_as):
			cache[key] = reports.render(t, a, b, viewer=address if external else render_as, external=external, today=today)
	r: reports.Rendered = cache[key]
	common = {"rendered_as": render_as, "recipient_email": address, "size_bytes": r.size, "data_note": (r.disclosure["headline"] + "\n" + "\n".join(x for _l, x in r.disclosure["lines"]))[:2000]}
	if channel == "Email":
		atts = r.attachments()
		if r.size > MAX_EMAIL_BYTES:
			atts = []
			common["error"] = _("Attachments left out: the report is larger than {0} MB.").format(MAX_EMAIL_BYTES // (1024 * 1024))
		q = frappe.sendmail(recipients=[address], subject=r.subject, message=r.html, attachments=atts, reference_doctype=DELIVERY, reference_name=name, delayed=True)
		if not q:
			# Frappe drops unsubscribed / blocked addresses before queueing.
			_finish(name, "Skipped", error=_("Email not queued: the address is unsubscribed or blocked"), **{k: v for k, v in common.items() if k != "error"})
			return "Skipped"
		_finish(name, "Sent", sent_at=now_datetime(), email_queue=getattr(q, "name", None) or "", attachments=", ".join(x["fname"] for x in atts), **common)
		return "Sent"
	return _whatsapp(t, r, address, name, common)


def template_header_type(tpl: str) -> str:
	if not tpl or not frappe.db.exists("DocType", "WhatsApp Templates"):
		return ""
	return (frappe.db.get_value("WhatsApp Templates", tpl, "header_type") or frappe.db.get_value("WhatsApp Templates", {"template_name": tpl}, "header_type") or "").upper()


def _whatsapp(t, r, phone: str, name: str, common: dict) -> str:
	"""Through Excom's consent-checked sender with the approved report template. A DOCUMENT header
	carries the PDF (else the Excel file); the private File is uploaded to Meta by id and deleted
	right after."""
	from excom.excom.services import outbound
	from excom.excom_ads.settings import get_settings

	tpl = get_settings().report_whatsapp_template
	header_type = template_header_type(tpl)
	file_doc = None
	header = ""
	if header_type == "DOCUMENT":
		fname, content = (f"{r.file_base}.pdf", r.pdf) if r.pdf else (f"{r.file_base}.xlsx", r.xlsx)
		file_doc = frappe.get_doc({"doctype": "File", "file_name": fname, "content": content, "is_private": 1, "attached_to_doctype": DELIVERY, "attached_to_name": name}).insert(ignore_permissions=True)
		header = file_doc.file_url
	try:
		res = outbound.send_transactional(purpose="ads_report", phone=phone, channel="whatsapp", whatsapp_template=tpl, whatsapp_variables=[t.template_name[:60], f"{r.from_date} to {r.to_date}", r.summary[:900]], whatsapp_header_media=header, reference=(DELIVERY, name))
	finally:
		if file_doc is not None:
			frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
	res = res or {}
	if res.get("ok"):
		_finish(name, "Sent", sent_at=now_datetime(), whatsapp_message_id=res.get("message_id") or "", attachments=file_doc.file_name if file_doc else "", **common)
		return "Sent"
	reason = res.get("skipped") or "not sent"
	if reason in WA_SKIP:
		_finish(name, "Skipped", error=_("WhatsApp: {0}").format(reason), **common)
		return "Skipped"
	raise frappe.ValidationError(_("WhatsApp send failed: {0}").format(reason))


def _settle(t, period_key: str) -> None:
	"""Mark the period done when every delivery is final; alert on failures, clear the alert after
	a clean period."""
	rows = frappe.get_all(DELIVERY, filters={"template": t.name, "period_key": period_key}, fields=["name", "status", "attempts", "error", "recipient_user", "recipient_email", "channel"])
	failed = [r for r in rows if r.status == "Failed"]
	pending = [r for r in rows if r.status == "Sending" or (r.status == "Failed" and cint(r.attempts) < MAX_ATTEMPTS)]
	if failed:
		who = ", ".join(f"{r.recipient_user or r.recipient_email} ({r.channel})" for r in failed[:10])
		raise_alert("Report Delivery Failed", "High", _("Report {0} could not be delivered to {1} recipient(s)").format(t.template_name, len(failed)), _("Period {0}. Failed: {1}. First error: {2}").format(period_key, who, (failed[0].error or "")[:300]), ref=t, dedupe_key=failure_key(t.name))
	elif rows:
		resolve_alert(failure_key(t.name), "Delivered on a later attempt" if any(cint(r.attempts) > 1 for r in rows) else "Delivered")
	if not period_key.startswith("now:") and not pending:
		frappe.db.set_value(TEMPLATE, t.name, {"last_period_key": period_key, "last_sent_at": now_datetime()}, update_modified=False)
