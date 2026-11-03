"""Calling settings of one WhatsApp number: `GET|POST /{phone id}/settings`, the `calling` object.

Rules from Meta (calling/call-settings, 2026-11):
  status / callback_permission_status   ENABLED | DISABLED
  call_icon_visibility                   DEFAULT | DISABLE_ALL (buttons in messages still work)
  call_icons.restrict_to_user_countries  ISO codes; [] = no restriction. Sent only when edited.
  call_hours                             status, timezone_id and weekly_operating_hours are required,
                                         the week cannot be empty; ≤ 2 ranges a day, "HHMM", open <
                                         close, no overlap; holiday_schedule ≤ 20, no past dates.
  Every POST replaces the whole call_hours: leaving holiday_schedule out deletes the holidays, so
  the stored holidays are always sent back. Turning call hours off still sends the whole object
  (status DISABLED with the stored week and time zone).
  sip.status ENABLED means the number signals over SIP and Graph /calls answers 131055. Excom does
  not build SIP; it reads the status and says so.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today

from excom.excom.services.whatsapp_interactive import InteractiveLimitError

DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
MAX_RANGES_PER_DAY = 2
MAX_HOLIDAYS = 20
ON_OFF = ("ENABLED", "DISABLED")
ICON = ("DEFAULT", "DISABLE_ALL")
_HHMM = re.compile(r"^([01]\d|2[0-3]):?([0-5]\d)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
# The messaging tiers below 2,000 unique users a day, as Meta and older excom code store them.
_LOW_TIERS = ("TIER_50", "TIER_250", "TIER_1K", "50", "250", "1000", "1K")


class CallSettingsError(InteractiveLimitError):
	title = "WhatsApp calling settings"


def _fail(msg: str):
	raise CallSettingsError(msg)


def hhmm(value, what: str) -> str:
	"""'0930' or '09:30' → '0930'. 2359 is the latest minute."""
	match = _HHMM.match(str(value or "").strip())
	if not match:
		_fail(_("{0} must be a 24-hour time like 0930 (got {1}).").format(what, value or _("nothing")))
	return match.group(1) + match.group(2)


def validate_weekly_hours(rows) -> list[dict]:
	out, per_day = [], {}
	for row in rows or []:
		day = str((row or {}).get("day_of_week") or "").strip().upper()
		if day not in DAYS:
			_fail(_("{0} is not a day of the week (MONDAY … SUNDAY).").format(day or _("A blank day")))
		open_time = hhmm(row.get("open_time"), _("Opening time"))
		close_time = hhmm(row.get("close_time"), _("Closing time"))
		if open_time >= close_time:
			_fail(_("On {0} the opening time {1} must be before the closing time {2}.").format(day, open_time, close_time))
		per_day.setdefault(day, []).append((open_time, close_time))
		out.append({"day_of_week": day, "open_time": open_time, "close_time": close_time})
	for day, ranges in per_day.items():
		if len(ranges) > MAX_RANGES_PER_DAY:
			_fail(_("{0} has {1} time ranges; WhatsApp allows {2} a day.").format(day, len(ranges), MAX_RANGES_PER_DAY))
		ranges.sort()
		for (a_open, a_close), (b_open, b_close) in zip(ranges, ranges[1:]):
			if b_open < a_close:
				_fail(_("The time ranges on {0} overlap.").format(day))
	out.sort(key=lambda r: (DAYS.index(r["day_of_week"]), r["open_time"]))
	return out


def validate_holidays(rows, on_date=None) -> list[dict]:
	"""Holidays that are over are dropped (Meta refuses past dates, and they no longer matter)."""
	first = getdate(on_date or today())
	out = []
	for row in list(rows or []):
		date = str((row or {}).get("date") or "").strip()
		if not _ISO_DATE.match(date):
			_fail(_("A holiday date must look like 2026-12-25 (got {0}).").format(date or _("nothing")))
		if getdate(date) < first:
			continue
		start = hhmm(row.get("start_time") or "0000", _("Holiday start"))
		end = hhmm(row.get("end_time") or "2359", _("Holiday end"))
		if start >= end:
			_fail(_("On {0} the holiday start {1} must be before its end {2}.").format(date, start, end))
		out.append({"date": date, "start_time": start, "end_time": end})
	if len(out) > MAX_HOLIDAYS:
		_fail(_("{0} holidays; WhatsApp allows {1}.").format(len(out), MAX_HOLIDAYS))
	out.sort(key=lambda r: (r["date"], r["start_time"]))
	return out


def _choice(value, allowed, what: str) -> str:
	value = str(value or "").strip().upper()
	if value and value not in allowed:
		_fail(_("{0} must be one of {1}.").format(what, ", ".join(allowed)))
	return value


def _json_list(value) -> list:
	if not value:
		return []
	if isinstance(value, list):
		return value
	try:
		data = json.loads(value)
	except (TypeError, ValueError):
		return []
	return data if isinstance(data, list) else []


def build_payload(values: dict, stored_holidays=None, on_date=None, stored_hours=None, stored_timezone="") -> dict:
	"""The POST body. `values` holds only what the admin set; anything missing is left as Meta has it.

	Call hours go as one whole object (Meta replaces it on every POST, and refuses one without a
	time zone and a week, even to turn them off): the week and time zone come from `values`, else
	from what Excom stored; the holidays likewise. The call icon countries are sent only when the
	admin edited them (`call_icons_edited`), so a save never wipes a restriction set in WhatsApp
	Manager."""
	values = values or {}
	calling = {}
	status = _choice(values.get("status"), ON_OFF, _("Calling"))
	if status:
		calling["status"] = status
	icon = _choice(values.get("call_icon_visibility"), ICON, _("Call icon"))
	if icon:
		calling["call_icon_visibility"] = icon
	if values.get("call_icons_edited") and "call_icon_countries" in values:
		raw = values.get("call_icon_countries") or []
		codes = [c.strip().upper() for c in (raw.split(",") if isinstance(raw, str) else raw) if str(c).strip()]
		bad = [c for c in codes if not _COUNTRY.match(c)]
		if bad:
			_fail(_("Country codes are two letters, like IN or AE (got {0}).").format(", ".join(bad)))
		calling["call_icons"] = {"restrict_to_user_countries": codes}
	callback = _choice(values.get("callback_permission_status"), ON_OFF, _("Callback permission"))
	if callback:
		calling["callback_permission_status"] = callback

	hours_status = _choice(values.get("call_hours_status"), ON_OFF, _("Call hours"))
	if hours_status:
		weekly = validate_weekly_hours(_json_list(values.get("weekly_operating_hours")))
		if not weekly and hours_status == "DISABLED":
			weekly = validate_weekly_hours(_json_list(stored_hours))
		holidays = validate_holidays(_json_list(values["holiday_schedule"]) if "holiday_schedule" in values else _json_list(stored_holidays), on_date)
		timezone = str(values.get("timezone_id") or stored_timezone or "").strip()
		if hours_status == "ENABLED" and not timezone:
			_fail(_("Call hours need a time zone (for example Asia/Kolkata)."))
		if hours_status == "ENABLED" and not weekly:
			_fail(_("Call hours need at least one opening time; WhatsApp refuses an empty week."))
		if not (timezone and weekly):
			_fail(_("To turn call hours off WhatsApp still needs their time zone and at least one opening "
				"time. Read the settings from Meta first, or fill them in."))
		calling["call_hours"] = {"status": hours_status, "timezone_id": timezone,
			"weekly_operating_hours": weekly, "holiday_schedule": holidays}
	if not calling:
		_fail(_("Nothing to save."))
	return {"calling": calling}


def restrictions_of(data: dict) -> list[dict]:
	"""`restrictions` sits inside `calling` in one sample and beside it in another."""
	calling = (data or {}).get("calling") or {}
	box = calling.get("restrictions") or (data or {}).get("restrictions") or {}
	rows = box.get("restrictions_list") if isinstance(box, dict) else []
	return [r for r in rows or [] if isinstance(r, dict)]


def fields_from_remote(data: dict) -> dict:
	"""Excom Channel Account fields from a GET /settings response (or a sent `calling` object)."""
	calling = (data or {}).get("calling") or {}
	hours = calling.get("call_hours") or {}
	icons = calling.get("call_icons") or {}
	sip = calling.get("sip") or {}
	fields = {
		"wa_calling_status": calling.get("status") or "",
		"wa_call_icon_visibility": calling.get("call_icon_visibility") or "",
		"wa_callback_permission_status": calling.get("callback_permission_status") or "",
		"wa_call_hours_status": hours.get("status") or "",
		"wa_call_hours_timezone": hours.get("timezone_id") or "",
		"wa_call_hours": json.dumps(hours.get("weekly_operating_hours") or [], indent=1),
		"wa_call_holidays": json.dumps(hours.get("holiday_schedule") or [], indent=1),
		"wa_calling_sip_status": sip.get("status") or "",
		"wa_calling_restrictions": "\n".join(
			f"{r.get('type')}: {r.get('reason') or ''}".strip(": ") for r in restrictions_of(data))[:1000],
	}
	if "call_icons" in calling:
		fields["wa_call_icon_countries"] = ", ".join(icons.get("restrict_to_user_countries") or [])
	return fields


def stored_values(account) -> dict:
	"""What Excom last saw, in the shape the admin screen edits."""
	return {
		"status": account.get("wa_calling_status") or "",
		"call_icon_visibility": account.get("wa_call_icon_visibility") or "",
		"call_icon_countries": account.get("wa_call_icon_countries") or "",
		"callback_permission_status": account.get("wa_callback_permission_status") or "",
		"call_hours_status": account.get("wa_call_hours_status") or "",
		"timezone_id": account.get("wa_call_hours_timezone") or "",
		"weekly_operating_hours": _json_list(account.get("wa_call_hours")),
		"holiday_schedule": _json_list(account.get("wa_call_holidays")),
		"sip_status": account.get("wa_calling_sip_status") or "",
		"restrictions": account.get("wa_calling_restrictions") or "",
		"rate_per_minute": account.get("wa_call_rate_per_minute") or 0,
		"synced_on": str(account.get("wa_calling_synced_on") or ""),
	}


def low_tier(limit_label: str) -> bool | None:
	"""True when the stored messaging limit is below 2,000, None when we cannot tell."""
	label = str(limit_label or "").strip().upper().replace(",", "")
	if not label:
		return None
	if label in _LOW_TIERS:
		return True
	if label.startswith("TIER_") or label in ("UNLIMITED",):
		return False
	try:
		return int(float(label)) < 2000
	except ValueError:
		return None


def warnings_for(account) -> list[dict]:
	from excom.excom.channels.voice.providers.whatsapp import DEFAULT_ICE_SERVERS, ice_servers, inline_credentials

	out = []
	tier = low_tier(account.get("wa_messaging_limit"))
	if tier is not False:
		out.append({"level": "warning" if tier else "info", "text": _(
			"Calling needs this number at a messaging limit of at least 2,000 unique users a day "
			"(else Meta answers 138015). The stored limit is {0}.").format(account.get("wa_messaging_limit") or _("unknown"))})
	if (account.get("wa_calling_sip_status") or "").upper() == "ENABLED":
		out.append({"level": "warning", "text": _(
			"SIP is enabled on this number, so Meta routes its calls to the SIP server and the browser "
			"cannot place or answer them (Graph /calls answers 131055). Disable SIP in WhatsApp Manager to use Excom.")})
	if account.get("wa_calling_restrictions"):
		out.append({"level": "warning", "text": _("Meta has restricted calling: {0}").format(account.get("wa_calling_restrictions"))})
	if inline_credentials(frappe.db.get_single_value("Excom Settings", "whatsapp_ice_servers") or ""):
		out.append({"level": "warning", "text": _(
			"A TURN password is written in the ICE servers list and is ignored. Save Excom Settings once to "
			"move it into the encrypted TURN Credential field.")})
	if ice_servers() == DEFAULT_ICE_SERVERS:
		out.append({"level": "info", "text": _(
			"Only a public STUN server is configured. Agents behind a strict firewall need a TURN server "
			"(Excom Settings → Calls → WhatsApp calling).")})
	out.append({"level": "info", "text": _(
		"Calls are WebRTC only: no phone network (PSTN) leg is allowed, so they cannot be forwarded to a mobile.")})
	return out


def _account(name: str):
	account = frappe.get_doc("Excom Channel Account", name)
	if account.channel != "whatsapp":
		_fail(_("{0} is not a WhatsApp number.").format(name))
	return account


def fetch(name: str) -> dict:
	"""Read the settings back from Meta and keep a copy on the account."""
	from excom.excom.channels.voice.providers.whatsapp import graph

	account = _account(name)
	data = graph(account, "GET", "settings")
	apply_remote(name, data)
	account.reload()
	return {"account": name, "settings": stored_values(account), "warnings": warnings_for(account)}


def apply_remote(name: str, data: dict) -> None:
	"""Store a GET /settings response (or an `account_settings_update` webhook's settings)."""
	before = frappe.db.get_value("Excom Channel Account", name, "wa_calling_status") or ""
	fields = fields_from_remote(data)
	fields["wa_calling_synced_on"] = now_datetime()
	frappe.db.set_value("Excom Channel Account", name, fields, update_modified=False)
	_status_changed(name, before, fields.get("wa_calling_status") or "")


def _status_changed(name: str, before: str, after: str) -> None:
	"""Agents' browsers pick a number up (or drop it) when its calling status changes."""
	if (before or "").upper() != (after or "").upper():
		from excom.excom.channels.voice.whatsapp_calling import notify_lines_changed

		frappe.db.commit()
		notify_lines_changed(name)


def save(name: str, values: dict) -> dict:
	"""Validate, POST the whole `calling` object, then keep what was sent."""
	from excom.excom.channels.voice.providers.whatsapp import graph

	account = _account(name)
	values = dict(values or {})
	rate = values.pop("rate_per_minute", None)
	payload = build_payload(values, stored_holidays=account.get("wa_call_holidays"),
		stored_hours=account.get("wa_call_hours"), stored_timezone=account.get("wa_call_hours_timezone") or "")
	before = account.get("wa_calling_status") or ""
	graph(account, "POST", "settings", payload)
	sent = payload["calling"]
	fields = {k: v for k, v in fields_from_remote({"calling": sent}).items()
		if k not in ("wa_calling_sip_status", "wa_calling_restrictions")}
	if "call_hours" not in sent:
		# not sent, so not changed: keep what Excom last read
		for key in ("wa_call_hours_status", "wa_call_hours_timezone", "wa_call_hours", "wa_call_holidays"):
			fields.pop(key, None)
	for key in ("wa_calling_status", "wa_call_icon_visibility", "wa_callback_permission_status"):
		if not fields.get(key):
			fields.pop(key, None)
	if rate is not None:
		fields["wa_call_rate_per_minute"] = float(rate or 0)
	fields["wa_calling_synced_on"] = now_datetime()
	frappe.db.set_value("Excom Channel Account", name, fields)
	_status_changed(name, before, fields.get("wa_calling_status") or before)
	account.reload()
	return {"account": name, "settings": stored_values(account), "warnings": warnings_for(account), "sent": payload}
