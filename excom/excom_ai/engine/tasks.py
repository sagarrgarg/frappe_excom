"""Adding work to the queue. One open task per (agent, dedupe key), so planners can run every tick."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from excom.excom_ai.engine import registry

OPEN = ("Queued", "Running", "Held")


def add(
	agent: str,
	kind: str,
	*,
	subject: tuple[str, str] | None = None,
	thread: str | None = None,
	priority: int | None = None,
	dedupe_key: str | None = None,
	not_before=None,
	params: dict | None = None,
) -> str:
	"""Queue a task; returns its name, or the name of the open task with the same key."""
	spec = registry.kind(kind)
	key = dedupe_key or (f"{kind}:{subject[0]}:{subject[1]}" if subject else None)
	if key:
		existing = frappe.db.get_value("Excom AI Task", {"agent": agent, "dedupe_key": key, "status": ["in", OPEN]})
		if existing:
			return existing
	doc = frappe.get_doc({
		"doctype": "Excom AI Task",
		"agent": agent,
		"kind": kind,
		"priority": spec.priority if priority is None else priority,
		"status": "Queued",
		"dedupe_key": key,
		"subject_doctype": subject[0] if subject else None,
		"subject_name": subject[1] if subject else None,
		"thread": thread,
		"not_before": not_before or now_datetime(),
		"params": params,
	})
	doc.insert(ignore_permissions=True)
	return doc.name


def cancel_open(agent: str, reason: str, only_kinds: set | None = None, keep_on_hold_kinds: bool = True) -> int:
	"""Skip queued and held tasks of an agent (a stop or a hold). Running ones finish."""
	registry.load()
	n = 0
	for t in frappe.get_all("Excom AI Task", {"agent": agent, "status": ["in", ("Queued", "Held")]}, ["name", "kind"]):
		spec = registry.TASK_KINDS.get(t.kind)
		if keep_on_hold_kinds and spec and spec.on_hold:
			continue
		if only_kinds and t.kind not in only_kinds:
			continue
		frappe.db.set_value("Excom AI Task", t.name, {"status": "Skipped", "outcome": reason}, update_modified=False)
		n += 1
	return n
