"""What a person actually did, over a day, a week or a month.

Excom could say how many messages went out and little else. A manager asking "what did this desk do
last week" had to open six screens and add up, and an agent had no way to show their own week at
all.

Two rules hold this together. An agent may only ever see themselves — enforced here rather than in
the page, because a report is exactly the kind of thing somebody tries by changing the URL. And
every figure is counted from the record that already exists, never from a tally kept alongside it;
a counter that drifts from the thing it counts is worse than no counter.
"""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, get_first_day, get_last_day, getdate, today

from excom.excom.api.chat import _check_excom_access, _is_manager
from excom.excom.utils.ratelimit import user_rate_limit

PERIODS = ("daily", "weekly", "monthly")


# ── the window ────────────────────────────────────────────────────────────────


def _window(period: str, on: str = "") -> tuple:
	"""The day, week or month a date falls in, as (start, end) inclusive.

	Weeks run Monday to Sunday, which is how everybody here talks about a week.
	"""
	day = getdate(on or today())
	if period == "weekly":
		start = add_days(day, -day.weekday())
		return start, add_days(start, 6)
	if period == "monthly":
		return get_first_day(day), get_last_day(day)
	return day, day


def _bounds(period: str, on: str = "") -> tuple:
	"""The same window as datetimes, so a row at 11pm on the last day still counts."""
	start, end = _window(period, on)
	return f"{start} 00:00:00", f"{end} 23:59:59"


# ── who may be reported on ────────────────────────────────────────────────────


def _people() -> list[str]:
	"""Everyone the caller may see a report for.

	An agent is one person: their own. Letting a report widen quietly is how a "read-only" screen
	turns into a way of reading other people's work.
	"""
	if not _is_manager():
		return [frappe.session.user]

	rows = frappe.get_all(
		"Has Role",
		filters={"role": ["in", ["Excom Agent", "Excom Admin"]], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	enabled = frappe.get_all(
		"User", filters={"name": ["in", rows], "enabled": 1}, pluck="name"
	)
	return sorted(enabled)


def _resolve(user: str) -> list[str]:
	allowed = _people()
	if not user:
		return allowed
	if user not in allowed:
		frappe.throw(
			_("You can only see your own report."), frappe.PermissionError
		)
	return [user]


# ── the figures ───────────────────────────────────────────────────────────────


def _messages(users: list[str], start: str, end: str) -> dict:
	rows = frappe.db.sql(
		"""
		SELECT created_by_user AS user, channel, COUNT(*) AS n
		FROM `tabExcom Message`
		WHERE direction = 'Outbound'
		  AND created_by_user IN %(users)s
		  AND creation BETWEEN %(start)s AND %(end)s
		GROUP BY created_by_user, channel
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	out: dict = {}
	for r in rows:
		bucket = out.setdefault(r.user, {"total": 0, "by_channel": {}})
		bucket["total"] += r.n
		bucket["by_channel"][r.channel or "other"] = r.n
	return out


def _calls(users: list[str], start: str, end: str) -> dict:
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(answered_by, agent) AS user, direction, status,
		       COUNT(*) AS n, COALESCE(SUM(duration), 0) AS seconds
		FROM `tabExcom Call`
		WHERE COALESCE(answered_by, agent) IN %(users)s
		  AND creation BETWEEN %(start)s AND %(end)s
		GROUP BY COALESCE(answered_by, agent), direction, status
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	out: dict = {}
	for r in rows:
		b = out.setdefault(
			r.user,
			{"outbound": 0, "inbound": 0, "connected": 0, "missed": 0, "talk_seconds": 0},
		)
		key = "outbound" if r.direction == "Outbound" else "inbound"
		b[key] += r.n
		b["talk_seconds"] += int(r.seconds or 0)
		if r.status == "Completed":
			b["connected"] += r.n
		elif r.status in ("Missed", "No Answer", "Busy", "Failed", "Canceled"):
			b["missed"] += r.n
	return out


def _closures(users: list[str], start: str, end: str) -> dict:
	rows = frappe.db.sql(
		"""
		SELECT closed_by AS user, closure_outcome AS outcome, COUNT(*) AS n
		FROM `tabExcom Thread`
		WHERE closed_by IN %(users)s
		  AND closed_at BETWEEN %(start)s AND %(end)s
		GROUP BY closed_by, closure_outcome
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	out: dict = {}
	for r in rows:
		b = out.setdefault(r.user, {"total": 0, "by_outcome": {}})
		b["total"] += r.n
		b["by_outcome"][r.outcome or "Unstated"] = r.n
	return out


def _tasks(users: list[str], start: str, end: str) -> dict:
	"""Made and finished. A task made and finished in the same week counts in both, which is right
	— they are two different things somebody did."""
	made = frappe.db.sql(
		"""
		SELECT owner AS user, COUNT(*) AS n FROM `tabToDo`
		WHERE owner IN %(users)s AND creation BETWEEN %(start)s AND %(end)s
		GROUP BY owner
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	done = frappe.db.sql(
		"""
		SELECT COALESCE(allocated_to, owner) AS user, COUNT(*) AS n FROM `tabToDo`
		WHERE COALESCE(allocated_to, owner) IN %(users)s
		  AND status = 'Closed' AND modified BETWEEN %(start)s AND %(end)s
		GROUP BY COALESCE(allocated_to, owner)
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	out: dict = {}
	for r in made:
		out.setdefault(r.user, {"created": 0, "completed": 0})["created"] = r.n
	for r in done:
		out.setdefault(r.user, {"created": 0, "completed": 0})["completed"] = r.n
	return out


def _records(users: list[str], start: str, end: str) -> dict:
	"""New CRM records, through the gateway — the one place that knows which doctypes those are."""
	from excom.excom.services import crm_gateway as gw

	out: dict = {}
	for doctype in gw.crm_doctypes():
		if not frappe.db.exists("DocType", doctype):
			continue
		rows = frappe.db.sql(
			f"""
			SELECT owner AS user, COUNT(*) AS n FROM `tab{doctype}`
			WHERE owner IN %(users)s AND creation BETWEEN %(start)s AND %(end)s
			GROUP BY owner
			""",
			{"users": users, "start": start, "end": end},
			as_dict=True,
		)
		for r in rows:
			b = out.setdefault(r.user, {"total": 0, "by_kind": {}})
			b["total"] += r.n
			b["by_kind"][doctype] = r.n
	return out


def _active_days(users: list[str], start: str, end: str) -> dict:
	"""Days the person did anything at all, which is what turns a total into an average."""
	rows = frappe.db.sql(
		"""
		SELECT created_by_user AS user, COUNT(DISTINCT DATE(creation)) AS days
		FROM `tabExcom Message`
		WHERE direction = 'Outbound' AND created_by_user IN %(users)s
		  AND creation BETWEEN %(start)s AND %(end)s
		GROUP BY created_by_user
		""",
		{"users": users, "start": start, "end": end},
		as_dict=True,
	)
	return {r.user: r.days for r in rows}


def _blank() -> dict:
	return {
		"messages": {"total": 0, "by_channel": {}},
		"calls": {"outbound": 0, "inbound": 0, "connected": 0, "missed": 0, "talk_seconds": 0},
		"closures": {"total": 0, "by_outcome": {}},
		"tasks": {"created": 0, "completed": 0},
		"records": {"total": 0, "by_kind": {}},
		"active_days": 0,
	}


# ── the report ────────────────────────────────────────────────────────────────


@frappe.whitelist()
@user_rate_limit(limit=60, seconds=60)
def get_activity_report(user: str = "", period: str = "daily", on: str = "") -> dict:
	"""What each person did in the day, week or month containing `on`."""
	_check_excom_access()
	if period not in PERIODS:
		frappe.throw(_("Unknown period: {0}").format(period))

	users = _resolve(user)
	start, end = _bounds(period, on)
	window_start, window_end = _window(period, on)

	messages = _messages(users, start, end)
	calls = _calls(users, start, end)
	closures = _closures(users, start, end)
	tasks = _tasks(users, start, end)
	records = _records(users, start, end)
	days = _active_days(users, start, end)

	names = {
		u.name: u.full_name or u.name
		for u in frappe.get_all("User", filters={"name": ["in", users]},
								fields=["name", "full_name"])
	}

	rows = []
	for u in users:
		row = _blank()
		row.update(
			{
				"user": u,
				"full_name": names.get(u, u),
				"messages": messages.get(u, row["messages"]),
				"calls": calls.get(u, row["calls"]),
				"closures": closures.get(u, row["closures"]),
				"tasks": tasks.get(u, row["tasks"]),
				"records": records.get(u, row["records"]),
				"active_days": days.get(u, 0),
			}
		)
		row["did_anything"] = bool(
			row["messages"]["total"] or row["calls"]["outbound"] or row["calls"]["inbound"]
			or row["closures"]["total"] or row["tasks"]["created"] or row["tasks"]["completed"]
			or row["records"]["total"]
		)
		rows.append(row)

	# Busiest first, because a report is read from the top.
	rows.sort(key=lambda r: -(r["messages"]["total"] + r["calls"]["outbound"] + r["calls"]["inbound"]))

	return {
		"period": period,
		"from": str(window_start),
		"to": str(window_end),
		"generated_at": frappe.utils.now(),
		"generated_for": frappe.session.user,
		"scope": "everyone" if len(users) > 1 else users[0],
		"can_see_everyone": _is_manager(),
		"rows": rows,
		"totals": _totals(rows),
	}


def _totals(rows: list) -> dict:
	t = _blank()
	del t["active_days"]
	for r in rows:
		t["messages"]["total"] += r["messages"]["total"]
		for k, v in r["messages"]["by_channel"].items():
			t["messages"]["by_channel"][k] = t["messages"]["by_channel"].get(k, 0) + v
		for k in ("outbound", "inbound", "connected", "missed", "talk_seconds"):
			t["calls"][k] += r["calls"][k]
		t["closures"]["total"] += r["closures"]["total"]
		for k, v in r["closures"]["by_outcome"].items():
			t["closures"]["by_outcome"][k] = t["closures"]["by_outcome"].get(k, 0) + v
		t["tasks"]["created"] += r["tasks"]["created"]
		t["tasks"]["completed"] += r["tasks"]["completed"]
		t["records"]["total"] += r["records"]["total"]
		for k, v in r["records"]["by_kind"].items():
			t["records"]["by_kind"][k] = t["records"]["by_kind"].get(k, 0) + v
	t["people"] = len(rows)
	return t


@frappe.whitelist()
def reportable_users() -> list:
	"""Who the caller may ask about. An agent gets a list of one — themselves."""
	_check_excom_access()
	users = _people()
	return [
		{"name": u.name, "full_name": u.full_name or u.name}
		for u in frappe.get_all(
			"User", filters={"name": ["in", users]}, fields=["name", "full_name"],
			order_by="full_name asc",
		)
	]
