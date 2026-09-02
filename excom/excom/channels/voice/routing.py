import frappe
from frappe.utils import now_datetime
from .providers.exotel import ExotelAdapter

def resolve_voice_account(business_number: str):
	"""Fast cache-friendly resolution of Excom Channel Account by business DID."""
	clean_num = (business_number or "").strip().lstrip("+")
	# Query active voice channel accounts
	accounts = frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "voice", "status": "Active"},
		fields=["name", "voice_provider", "voice_phone_number", "voice_record_calls", "voice_recording_channels"]
	)
	for acc in accounts:
		acc_num = (acc.voice_phone_number or "").strip().lstrip("+")
		if acc_num and (clean_num.endswith(acc_num) or acc_num.endswith(clean_num)):
			return frappe.get_doc("Excom Channel Account", acc.name)
	if accounts:
		return frappe.get_doc("Excom Channel Account", accounts[0].name)
	return None

def resolve_ring_destination(caller_number: str, business_number: str, account_doc) -> list:
	"""
	Sticky-then-Team routing (<5s budget, cache/read-only).
	1. Resolve caller in Omni Identity -> check last assigned agent.
	2. If active, put sticky agent first.
	3. Fill remaining team members from account allowed_teams.
	"""
	ring_numbers = []
	sticky_agent = None

	clean_caller = (caller_number or "").strip().lstrip("+")

	# Step 1: Check Omni Identity & recent thread for Sticky Agent
	identity = frappe.db.get_value(
		"Omni Identity",
		{"primary_phone": ["like", f"%{clean_caller[-10:]}%"]},
		"name"
	)
	if identity:
		last_thread_agent = frappe.db.get_value(
			"Excom Thread",
			{"omni_identity": identity, "status": ["in", ["Open", "Pending", "Resolved"]]},
			"assigned_to",
			order_by="modified desc"
		)
		if last_thread_agent:
			agent_mobile = frappe.db.get_value("User", last_thread_agent, "mobile_no")
			is_enabled = frappe.db.get_value("User", last_thread_agent, "enabled")
			if agent_mobile and is_enabled:
				sticky_agent = agent_mobile.strip()
				ring_numbers.append(sticky_agent)

	# Step 2: Query team members from allowed_teams
	allowed_teams = [row.team for row in account_doc.get("allowed_teams") or [] if row.team]
	if allowed_teams:
		team_users = frappe.get_all(
			"Excom Team Member",
			filters={"parent": ["in", allowed_teams]},
			fields=["user"]
		)
		user_ids = list(set([tu.user for tu in team_users if tu.user]))
		if user_ids:
			user_mobiles = frappe.get_all(
				"User",
				filters={"name": ["in", user_ids], "enabled": 1},
				fields=["mobile_no"]
			)
			for u in user_mobiles:
				mob = (u.mobile_no or "").strip()
				if mob and mob not in ring_numbers:
					ring_numbers.append(mob)

	# Fallback if no specific team member found: check all enabled users with mobile_no/phone
	if not ring_numbers:
		sys_users = frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, fields=["parent"])
		mgr_ids = [s.parent for s in sys_users[:10]]
		if mgr_ids:
			mgr_mobiles = frappe.get_all("User", filters={"name": ["in", mgr_ids], "enabled": 1}, fields=["mobile_no", "phone"])
			for m in mgr_mobiles:
				mob = (m.mobile_no or m.phone or "").strip()
				if mob and mob not in ring_numbers:
					ring_numbers.append(mob)

	if not ring_numbers:
		all_users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, fields=["mobile_no", "phone"], limit=10)
		for u in all_users:
			mob = (u.mobile_no or u.phone or "").strip()
			if mob and mob not in ring_numbers:
				ring_numbers.append(mob)

	return ring_numbers

def build_exotel_routing_response(ring_numbers: list, business_number: str, account_doc) -> dict:
	"""
	Constructs high-speed Exotel Connect Dynamic URL JSON payload.
	Response shape confirmed per Exotel developer specifications.
	"""
	record = account_doc.voice_record_calls in ["All", "Inbound only"] if account_doc else True
	rec_channels = "dual" if (account_doc and account_doc.voice_recording_channels == "Dual") else "single"
	
	import re
	# Clean standard phone formatting for Exotel parallel ringing
	formatted_numbers = []
	for n in ring_numbers:
		raw_digits = re.sub(r"\D", "", n.strip())
		if len(raw_digits) == 10:
			num = "0" + raw_digits
		elif len(raw_digits) == 12 and raw_digits.startswith("91"):
			num = "0" + raw_digits[2:]
		elif len(raw_digits) == 11 and raw_digits.startswith("0"):
			num = raw_digits
		else:
			num = "+" + raw_digits
		if num not in formatted_numbers:
			formatted_numbers.append(num)

	return {
		"fetch_after_attempt": False,
		"destination": {
			"numbers": formatted_numbers
		},
		"outgoing_phone_number": business_number,
		"record": record,
		"recording_channels": rec_channels,
		"max_ringing_duration": 45,
		"parallel_ringing": {
			"activate": True,
			"max_parallel_attempts": min(len(formatted_numbers), 10) if formatted_numbers else 1
		}
	}