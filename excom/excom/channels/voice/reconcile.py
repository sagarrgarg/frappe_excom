import frappe
from frappe.utils import now_datetime, add_to_date
from .providers.exotel import ExotelAdapter
from .providers.airtel import AirtelIQAdapter

def get_voice_provider_adapter(account_doc):
	if account_doc.voice_provider == "Exotel":
		return ExotelAdapter(account_doc)
	elif account_doc.voice_provider == "Airtel IQ":
		return AirtelIQAdapter(account_doc)
	return ExotelAdapter(account_doc)

def reconcile_pending_calls():
	"""
	Scheduled Background Cron Job:
	Fetches duration, talk_time, cost, and recording_url from provider Call Details API
	for calls completed in the last 24 hours that are missing duration / cost.
	"""
	# Find unreconciled calls created more than 90 seconds ago
	cutoff = add_to_date(now_datetime(), seconds=-90)
	calls = frappe.get_all(
		"Excom Call",
		filters={
			"status": ["in", ["Completed", "in-progress", "Ringing"]],
			"duration": 0,
			"creation": ["<", cutoff]
		},
		fields=["name", "provider_call_id", "channel_account", "status"],
		limit=50
	)

	for c in calls:
		if not c.provider_call_id or not c.channel_account:
			continue
		try:
			account = frappe.get_doc("Excom Channel Account", c.channel_account)
			adapter = get_voice_provider_adapter(account)
			details = adapter.fetch_call_details(c.provider_call_id)
			if details and details.status != "unknown":
				frappe.db.set_value("Excom Call", c.name, {
					"duration": details.duration,
					"talk_time": details.talk_time,
					"cost": details.cost,
					"recording_url": details.recording_url or frappe.db.get_value("Excom Call", c.name, "recording_url"),
					"status": "Completed" if details.status in ["completed", "Completed"] else c.status
				})
		except Exception as e:
			frappe.log_error(f"Reconciliation error for call {c.name}: {e}", "Excom Reconcile Job")

	frappe.db.commit()