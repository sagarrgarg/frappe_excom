"""Recover inbound messages a webhook received but never stored.

Every Meta webhook is written to the notification log (template "Webhook") before it is processed,
so a message lost to a handler failure is still on record. This finds the customer messages in
those payloads that have no Excom Message, and stores them through the normal webhook path.
Statuses, lead ads and comments are left out: only the messages that went missing are replayed.

	# list them (nothing is changed)
	bench --site <site> execute excom.excom.services.inbound_replay.replay --kwargs "{'since': '2026-10-12'}"
	# store them
	bench --site <site> execute excom.excom.services.inbound_replay.replay --kwargs "{'since': '2026-10-12', 'apply': 1}"

Storing is idempotent (by provider message id), so running it twice stores nothing the second time.
"""

import json

import frappe

from excom.excom.utils import _notification_log_doctype


def lost_messages(payload: dict, stored: set[str]) -> dict | None:
	"""The part of one webhook payload that carries customer messages not in `stored`, or None."""
	entries = payload.get("entry") or []
	if isinstance(entries, dict):
		entries = [entries]
	kept = []
	for entry in entries:
		messaging = [
			event
			for event in entry.get("messaging") or []
			if (event.get("message") or {}).get("mid")
			and not event["message"].get("is_echo")
			and event["message"]["mid"] not in stored
		]
		changes = []
		for change in entry.get("changes") or []:
			if (change.get("field") or "messages") != "messages":
				continue
			value = change.get("value") or {}
			messages = [m for m in value.get("messages") or [] if m.get("id") and m["id"] not in stored]
			if messages:
				changes.append({**change, "value": {**value, "messages": messages, "statuses": []}})
		if messaging or changes:
			kept.append({**entry, "messaging": messaging, "changes": changes})
	if not kept:
		return None
	return {**payload, "entry": kept}


def _summary(payload: dict) -> list[dict]:
	rows = []
	for entry in payload["entry"]:
		for event in entry.get("messaging") or []:
			message = event["message"]
			rows.append(
				{
					"channel": payload.get("object") or "meta",
					"sender": (event.get("sender") or {}).get("id"),
					"id": message["mid"],
					"text": (message.get("text") or "")[:120],
				}
			)
		for change in entry.get("changes") or []:
			for message in change["value"]["messages"]:
				rows.append(
					{
						"channel": "whatsapp",
						"sender": message.get("from"),
						"id": message["id"],
						"text": ((message.get("text") or {}).get("body") or message.get("type") or "")[:120],
					}
				)
	return rows


def replay(since: str, apply: int = 0) -> dict:
	"""List (or, with apply=1, store) the inbound messages lost since `since` (a date)."""
	frappe.only_for("System Manager")
	from excom.excom.utils.webhook import _process_webhook_payload

	logs = frappe.get_all(
		_notification_log_doctype(),
		filters={"template": "Webhook", "creation": [">=", since]},
		fields=["name", "creation", "meta_data"],
		order_by="creation asc",
	)
	found, stored_now = [], 0
	for log in logs:
		try:
			payload = json.loads(log.meta_data or "{}")
		except (TypeError, ValueError):
			continue
		if not isinstance(payload, dict):
			continue
		ids = _message_ids(payload)
		if not ids:
			continue
		stored = set(
			frappe.get_all(
				"Excom Message", {"provider_message_id": ["in", list(ids)]}, pluck="provider_message_id"
			)
		)
		lost = lost_messages(payload, stored)
		if not lost:
			continue
		rows = _summary(lost)
		for row in rows:
			row["received"] = str(log.creation)
		found.extend(rows)
		if int(apply):
			_process_webhook_payload(json.dumps(lost))
			stored_now += len(rows)
	return {"lost": len(found), "stored": stored_now if int(apply) else 0, "messages": found}


def _message_ids(payload: dict) -> set[str]:
	ids = set()
	entries = payload.get("entry") or []
	if isinstance(entries, dict):
		entries = [entries]
	for entry in entries:
		for event in entry.get("messaging") or []:
			mid = (event.get("message") or {}).get("mid")
			if mid:
				ids.add(mid)
		for change in entry.get("changes") or []:
			for message in (change.get("value") or {}).get("messages") or []:
				if message.get("id"):
					ids.add(message["id"])
	return ids
