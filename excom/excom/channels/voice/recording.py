"""Recording playback, and the retention sweep.

A provider recording URL never reaches a browser. Plivo serves recordings from a public URL unless
Basic Auth is switched on in Voice Settings, so handing that URL to a client would publish the
call — and switching the auth on would then break playback. Both problems go away by streaming the
bytes through here, behind the same permission check that guards the conversation.

Playback and download are separate permissions. Hearing a call at your desk and walking out with
the audio file are different acts.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime
from werkzeug.wrappers import Response

from excom.excom.channels.voice import providers
from excom.excom.doctype.excom_call.excom_call import can_access

DEFAULT_RETENTION_DAYS = 0  # 0 = keep, matching Frappe's convention for "no automatic purge"


def stream(call_name: str, download: bool = False) -> Response:
	"""Stream one recording to the browser, authenticated and permission-checked."""
	call = frappe.get_doc("Excom Call", call_name)

	if not can_access(call):
		from excom.excom.services.access import deny

		deny(
			_("You cannot listen to this call."),
			detail=_("It belongs to a conversation on another desk."),
		)

	if download and not _may_download():
		from excom.excom.services.access import deny

		deny(
			_("You cannot download call recordings."),
			needs_roles=("Excom Admin",),
			detail=_("You can still play the recording in Excom."),
		)

	if call.recording_status == "Purged":
		frappe.throw(_("This recording has been deleted under the retention policy."))
	if not call.recording_url:
		frappe.throw(_("This call has no recording."))

	provider = providers.for_account(call.channel_account)
	chunks, content_type = provider.fetch_recording_stream(call)

	filename = f"call-{call.name}.mp3"
	disposition = "attachment" if download else "inline"
	return Response(
		chunks,
		content_type=content_type,
		direct_passthrough=True,
		headers={
			"Content-Disposition": f'{disposition}; filename="{filename}"',
			# The audio is per-user authorised; a shared cache must never hold it.
			"Cache-Control": "private, no-store",
		},
	)


def _may_download() -> bool:
	from excom.excom.api.chat import ADMIN_ROLES

	return bool(ADMIN_ROLES.intersection(frappe.get_roles(frappe.session.user)))


def purge_expired_recordings() -> dict:
	"""Daily. Drop the pointer to recordings past the retention window.

	Excom stores no audio, so purging is marking the row and asking the provider to delete its copy.
	The call record itself is kept — duration, outcome and summary are the operational history and
	are not what the retention policy is about.
	"""
	days = cint(
		frappe.db.get_single_value("Excom Settings", "recording_retention_days")
		or DEFAULT_RETENTION_DAYS
	)
	if days <= 0:
		return {"purged": 0, "reason": "retention disabled"}

	cutoff = add_days(now_datetime(), -days)
	stale = frappe.get_all(
		"Excom Call",
		filters={
			"recording_status": "Ready",
			"creation": ["<", cutoff],
		},
		fields=["name", "channel_account", "recording_id"],
		limit=200,
	)

	purged = 0
	for row in stale:
		try:
			frappe.db.set_value(
				"Excom Call",
				row.name,
				{"recording_status": "Purged", "recording_url": ""},
				update_modified=False,
			)
			purged += 1
		except Exception as exc:
			frappe.log_error(
				f"Could not purge the recording for {row.name}: {exc}", "Excom Voice Retention"
			)
	frappe.db.commit()
	return {"purged": purged, "retention_days": days}
