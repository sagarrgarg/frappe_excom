"""What is waiting for this agent, counted in one place.

Excom had no such place. The inbox badge was a sum the browser worked out over the hundred
conversations it happened to have loaded, so on a busy desk it was simply wrong; a task fell due
and said nothing; a call was missed and only the Calls page knew. Three kinds of waiting, three
different ways of finding out, and no way to see them together.

Counted on the server, because the browser only ever holds a page of the data, and through the same
visibility rule the inbox itself uses. A number that promises something the list then refuses to
open is worse than no number.
"""

import frappe
from frappe.utils import now_datetime, today

from excom.excom.api.chat import _check_excom_access, visible_threads_clause
from excom.excom.utils.ratelimit import user_rate_limit

# How many of each to send back with the counts. Enough to show what is waiting without turning
# this into a second inbox.
PREVIEW = 5


def _unread_by_channel() -> dict:
	"""Unread conversations per channel, under the rule the inbox itself obeys."""
	params: dict = {}
	clause = visible_threads_clause(params, alias="t")
	where = "t.status NOT IN ('Closed', 'Spam') AND t.unread_count > 0"
	if clause:
		where += " AND " + clause

	rows = frappe.db.sql(
		f"""
		SELECT t.channel, COUNT(*) AS threads, COALESCE(SUM(t.unread_count), 0) AS messages
		FROM `tabExcom Thread` t
		WHERE {where}
		GROUP BY t.channel
		""",
		params,
		as_dict=True,
	)
	return {r.channel: {"threads": r.threads, "messages": int(r.messages or 0)} for r in rows}


def _my_task_filters(base: dict) -> list:
	"""The two ways a task can be this agent's.

	Frappe puts the creator in `owner` and the assignee in `allocated_to`, and a task made from the
	Tasks tab has no assignee at all — so a person's own tasks are both kinds, and asking for only
	one of them loses exactly the tasks they made for themselves.
	"""
	return [
		{**base, "allocated_to": frappe.session.user},
		{**base, "owner": frappe.session.user, "allocated_to": ["is", "not set"]},
	]


def _tasks(base: dict, limit: int, order: str = "date asc") -> list:
	rows: dict = {}
	for filters in _my_task_filters(base):
		for r in frappe.get_all(
			"ToDo",
			filters=filters,
			fields=[
				"name", "description", "status", "date", "priority", "allocated_to", "owner",
				"reference_type", "reference_name", "modified",
			],
			order_by=order,
			limit=limit,
		):
			rows.setdefault(r.name, r)
	return sorted(rows.values(), key=lambda r: (str(r.get("date") or "9999-12-31"), str(r.modified)))


def _strip(rows: list) -> list:
	for r in rows:
		r["description"] = frappe.utils.strip_html(r.get("description") or "").strip()[:140]
		r["date"] = str(r["date"]) if r.get("date") else None
		r["modified"] = str(r.get("modified") or "")
	return rows


def _missed_calls(limit: int = PREVIEW) -> dict:
	"""A missed call is a customer who rang and got nothing."""
	from excom.excom.api.voice import UNANSWERED

	# get_list, so the permission query runs and an agent only counts calls they could open.
	rows = frappe.get_list(
		"Excom Call",
		filters={"status": ["in", list(UNANSWERED)], "direction": "Inbound"},
		fields=["name", "customer_number", "display_name", "omni_identity", "creation"],
		order_by="creation desc",
		limit=200,
	)
	return {
		"count": len(rows),
		"items": [
			{
				"name": r.name,
				"who": r.display_name or r.customer_number or "Unknown",
				"omni_identity": r.omni_identity,
				"at": str(r.creation),
			}
			for r in rows[:limit]
		],
	}


@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def get_worklist() -> dict:
	"""Everything waiting on this agent, by kind rather than as one number.

	Kept apart on purpose: "four people are waiting for a reply" and "four tasks are overdue" ask
	different things of the person reading it, and adding them together says neither.
	"""
	_check_excom_access()

	unread = _unread_by_channel()
	empty = {"threads": 0, "messages": 0}
	whatsapp = unread.get("whatsapp", empty)
	email = unread.get("email", empty)
	other = {
		"threads": sum(v["threads"] for k, v in unread.items() if k not in ("whatsapp", "email")),
		"messages": sum(v["messages"] for k, v in unread.items() if k not in ("whatsapp", "email")),
	}

	overdue = _tasks({"status": "Open", "date": ["<", today()]}, limit=200)
	calls = _missed_calls()

	unread_threads = whatsapp["threads"] + email["threads"] + other["threads"]
	return {
		"whatsapp": whatsapp,
		"email": email,
		"other": other,
		"tasks": {"count": len(overdue), "items": _strip(overdue[:PREVIEW])},
		"calls": calls,
		# Conversations waiting, not messages: one person sending six lines is still one person
		# waiting, and a tab title that says 6 when one customer wrote is just noise.
		"unread_threads": unread_threads,
		"needs_attention": unread_threads + len(overdue) + calls["count"],
		"at": str(now_datetime()),
	}


@frappe.whitelist()
@user_rate_limit(limit=120, seconds=60)
def get_my_tasks(view: str = "open", limit: int = 100) -> list:
	"""Every task of this agent's, wherever it was made.

	Until now a task could only be reached from inside the conversation it was attached to, so an
	agent who could not remember which customer it was about had no way to find it at all.
	"""
	_check_excom_access()
	limit = min(frappe.utils.cint(limit) or 100, 200)

	base: dict = {"status": "Open"}
	if view == "done":
		base = {"status": ["!=", "Open"]}
	elif view == "overdue":
		base["date"] = ["<", today()]
	elif view == "today":
		base["date"] = today()
	elif view == "upcoming":
		base["date"] = [">", today()]

	rows = _tasks(base, limit, order="modified desc" if view == "done" else "date asc")
	_label_records(rows)
	return _strip(rows[:limit])


def _label_records(rows: list) -> None:
	"""Say which contact each task is about, so the list reads without opening every row.

	Titles come from the gateway, which is the one place that knows what each kind of CRM record is
	called. A second copy of that mapping here is how the two come to disagree.
	"""
	from excom.excom.services import crm_gateway as gw

	wanted: dict = {}
	for r in rows:
		r["reference_label"] = ""
		r["omni_identity"] = None
		if r.get("reference_type") and r.get("reference_name"):
			wanted.setdefault(r["reference_type"], set()).add(r["reference_name"])
	if not wanted:
		return

	labels: dict = {}
	for doctype, names in wanted.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for name in names:
			if not frappe.db.exists(doctype, name):
				continue
			labels[(doctype, name)] = (
				frappe.db.get_value(doctype, name, "display_name")
				if doctype == "Omni Identity"
				else gw.get_title(gw.ref(doctype, name))
			) or name

	# The conversation to open when the task is clicked, which is the contact behind the record.
	all_names = [n for names in wanted.values() for n in names]
	links = frappe.get_all(
		"Omni Identity Link",
		filters={
			"parenttype": "Omni Identity",
			"linked_doctype": ["in", list(wanted)],
			"linked_name": ["in", all_names],
		},
		fields=["parent", "linked_doctype", "linked_name"],
	)
	by_record = {(l.linked_doctype, l.linked_name): l.parent for l in links}

	for r in rows:
		key = (r.get("reference_type"), r.get("reference_name"))
		r["reference_label"] = labels.get(key) or r.get("reference_name") or ""
		r["omni_identity"] = (
			r["reference_name"] if r.get("reference_type") == "Omni Identity" else by_record.get(key)
		)
