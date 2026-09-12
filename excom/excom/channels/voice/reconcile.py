"""Reading the call detail record back.

Duration, billed duration and cost arrive at the provider a minute or two after a call ends, so the
hangup webhook alone leaves rows with `duration = 0` on calls that plainly lasted four minutes. That
is the visible symptom in every Frappe telephony integration that ships without this job, and it is
why `reconciled` is a field rather than an assumption.

Also the safety net for webhooks that never arrived at all: a call still marked Ringing an hour
later did not stay ringing, we simply never heard how it ended.
"""

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from excom.excom.channels.voice import providers
from excom.excom.doctype.excom_call.excom_call import CLOSED_STATUSES

# The CDR is not written the instant the call drops. Asking too early just burns an API call.
SETTLE_SECONDS = 120
# A call still open long after the provider's own time limit was never going to close itself.
STUCK_MINUTES = 90
# How long to keep promising a recording before admitting none is coming.
RECORDING_GIVE_UP_MINUTES = 20
BATCH = 50


def reconcile_pending_calls() -> dict:
	"""Scheduled. Backfill anything the webhooks did not deliver."""
	cutoff = add_to_date(now_datetime(), seconds=-SETTLE_SECONDS)
	rows = frappe.get_all(
		"Excom Call",
		filters={
			"reconciled": 0,
			"creation": ["<", cutoff],
			"provider_call_id": ["is", "set"],
			"channel_account": ["is", "set"],
		},
		fields=["name", "provider_call_id", "channel_account", "status", "agent"],
		order_by="creation asc",
		limit=BATCH,
	)

	done = failed = 0
	for row in rows:
		try:
			if _reconcile_one(row):
				done += 1
		except Exception as exc:
			failed += 1
			frappe.log_error(
				title="Excom Voice: reconcile failed", message=f"{row.name}: {exc}"
			)
	frappe.db.commit()

	stuck = close_stuck_calls()
	return {"reconciled": done, "failed": failed, "closed_stuck": stuck}


def _reconcile_one(row) -> bool:
	provider = providers.for_account(row.channel_account)
	details = provider.fetch_call_details(row.provider_call_id)
	if not details.found:
		# Not in the CDR store yet. Leave `reconciled` alone and try again next sweep.
		return False

	updates = {"reconciled": 1}
	if details.duration:
		updates["duration"] = details.duration
		updates["talk_time"] = details.bill_duration or details.duration
	if details.cost:
		updates["cost"] = details.cost
	if details.hangup_cause:
		updates["hangup_cause"] = details.hangup_cause

	# Only the CDR can settle a call we never got a hangup for. A call the webhooks already closed
	# keeps the status they gave it — the CDR's own vocabulary is coarser than ours.
	if row.status not in CLOSED_STATUSES:
		updates["status"] = "Completed" if details.duration else "Missed"

	# Nothing was said, so nothing was recorded. Without this the card keeps promising a recording
	# that is never coming.
	if not details.duration:
		current = frappe.db.get_value("Excom Call", row.name, "recording_status")
		if current == "Pending":
			updates["recording_status"] = "None"

	frappe.db.set_value("Excom Call", row.name, updates)

	if row.agent and details.duration:
		_count_international_minutes(row.name, row.agent, details.duration)
	return True


def _count_international_minutes(call: str, agent: str, seconds: int) -> None:
	"""Spend caps are enforced against real durations, not estimates — so the counter is fed here
	rather than at dial time."""
	try:
		from excom.excom.channels.voice.outbound import _is_international, record_international_minutes

		call_doc = frappe.db.get_value(
			"Excom Call", call, ["customer_number", "direction", "channel_account"], as_dict=True
		)
		if not call_doc or call_doc.direction != "Outbound":
			return
		account_doc = frappe.get_cached_doc("Excom Channel Account", call_doc.channel_account)
		if _is_international(call_doc.customer_number or "", account_doc):
			record_international_minutes(agent, seconds)
	except Exception:
		# A spend counter must never be the reason a reconcile fails.
		pass


def close_stuck_calls() -> int:
	"""Close calls whose end we never heard about.

	Without this a dropped webhook leaves an agent marked busy forever and their browser out of
	every ring set, which looks exactly like "the softphone stopped working".
	"""
	from excom.excom.channels.voice import presence

	cutoff = add_to_date(now_datetime(), minutes=-STUCK_MINUTES)
	# A recording that has not arrived long after the call ended is not coming. A very short call
	# often produces none at all, and leaving the row Pending shows the agent a spinner promising
	# audio for ever.
	abandoned = frappe.get_all(
		"Excom Call",
		filters={
			"recording_status": "Pending",
			"status": ["in", list(CLOSED_STATUSES)],
			"creation": ["<", add_to_date(now_datetime(), minutes=-RECORDING_GIVE_UP_MINUTES)],
		},
		pluck="name",
		limit=BATCH,
	)
	for name in abandoned:
		frappe.db.set_value("Excom Call", name, "recording_status", "None", update_modified=False)

	stuck = frappe.get_all(
		"Excom Call",
		filters={"status": ["in", ["Ringing", "In Progress"]], "creation": ["<", cutoff]},
		fields=["name", "agent", "answered_by", "duration", "ring_set"],
		limit=BATCH,
	)

	for row in stuck:
		frappe.db.set_value(
			"Excom Call",
			row.name,
			{
				"status": "Completed" if cint(row.duration) else "Failed",
				"hangup_cause": "No end-of-call event received",
			},
		)
		for user in (row.agent, row.answered_by):
			if user:
				presence.clear_busy(user)
	if stuck:
		frappe.db.commit()
	return len(stuck)
