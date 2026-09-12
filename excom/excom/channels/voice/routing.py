"""The decision engine. Provider-agnostic, cache-backed, and it writes nothing.

This module sits on the critical path of a live call. The caller is listening to silence for every
millisecond it takes. Plivo will wait 2s to connect and 40s to read, but that budget is not ours to
spend — the target is p99 under 800ms, which means:

    no record creation, no identity writes, no outbound provider calls, no unindexed scans

Everything that needs writing is enqueued by the handler after the response has gone back.

The chain that decides who rings already exists and is not duplicated here:

    Excom Channel Account.allowed_teams -> Excom Team.members -> User
                                                                  |- Excom Voice Endpoint (browser)
                                                                  |- User.mobile_no        (phone)

Add someone to a team and they start ringing. Remove them and they stop. One place.
"""

import frappe
from frappe.utils import cint

from excom.excom.channels.voice import presence
from excom.excom.channels.voice.providers.base import CallDecision, Destination
from excom.excom.utils.phone import normalize_phone, phone_variants

CACHE_TTL = 300
_ACCOUNT_BY_DID = "excom:voice:did"
_LINE_AGENTS = "excom:voice:agents"

DEFAULT_STICKY_SECONDS = 20
DEFAULT_TEAM_SECONDS = 30


# ── account ───────────────────────────────────────────────────────────────────


def resolve_account(business_number: str, account_hint: str = "") -> str | None:
	"""Which line was dialled.

	The account name is passed in the webhook query string, so the hint is the normal path and the
	DID lookup is the fallback for a provider that drops query strings. There is deliberately no
	"just use the first voice account" fallback: answering on the wrong line means the wrong caller
	id, the wrong teams and the wrong recording policy.
	"""
	if account_hint and frappe.db.exists(
		"Excom Channel Account", {"name": account_hint, "channel": "voice"}
	):
		return account_hint

	normalized = normalize_phone(business_number).lstrip("+")
	if not normalized:
		return None

	index = _did_index()
	if normalized in index:
		return index[normalized]
	# Providers vary on whether they send the country code. Compare on the national significant
	# number, which is what actually identifies the line.
	tail = normalized[-10:]
	return index.get(tail)


def _did_index() -> dict[str, str]:
	cached = frappe.cache.get_value(_ACCOUNT_BY_DID)
	if cached is not None:
		return cached

	index: dict[str, str] = {}
	for row in frappe.get_all(
		"Excom Channel Account",
		filters={"channel": "voice", "status": "Active"},
		fields=["name", "voice_number"],
	):
		number = normalize_phone(row.voice_number or "").lstrip("+")
		if not number:
			continue
		index[number] = row.name
		index.setdefault(number[-10:], row.name)

	frappe.cache.set_value(_ACCOUNT_BY_DID, index, expires_in_sec=CACHE_TTL)
	return index


def clear_caches(account: str = "") -> None:
	"""Called from doctype hooks when a line, a team or an endpoint changes."""
	frappe.cache.delete_value(_ACCOUNT_BY_DID)
	if account:
		frappe.cache.delete_value(f"{_LINE_AGENTS}:{account}")
	else:
		for row in frappe.get_all(
			"Excom Channel Account", filters={"channel": "voice"}, pluck="name"
		):
			frappe.cache.delete_value(f"{_LINE_AGENTS}:{row}")


# ── cache invalidation hooks ──────────────────────────────────────────────────
# Deliberately blunt: a team edit clears every line rather than working out which lines that team
# serves. Team edits are rare, calls are not, and a stale ring set is a call going to the wrong
# person — much worse than one extra cache rebuild.


def on_team_changed(doc, method=None) -> None:
	clear_caches()


def on_account_changed(doc, method=None) -> None:
	if getattr(doc, "channel", None) == "voice":
		clear_caches(doc.name)


def on_endpoint_changed(doc, method=None) -> None:
	clear_caches(getattr(doc, "channel_account", ""))


# ── identity ──────────────────────────────────────────────────────────────────


def resolve_identity_readonly(caller_number: str) -> str | None:
	"""Find the contact behind this number without creating one.

	`resolve_identity()` is the real entry point everywhere else, but it inserts records and runs an
	access check — neither belongs on a synchronous telephony path. If there is no match we return
	None and the enqueued handler creates the identity properly, off the critical path.

	The lookup is an equality match on the indexed `normalized_phone`. The Exotel version used
	`primary_phone LIKE %<last 10 digits>%`, which is a leading-wildcard scan and also matches the
	wrong contact whenever two numbers share a tail.
	"""
	normalized = normalize_phone(caller_number)
	if not normalized:
		return None

	for candidate in phone_variants(normalized):
		found = frappe.db.get_value(
			"Omni Identity",
			{"normalized_phone": candidate, "status": ["!=", "Merged"]},
			"name",
		)
		if found:
			return found

	for candidate in phone_variants(normalized):
		alias_parent = frappe.db.get_value(
			"Omni Identity Alias",
			{
				"alias_value_normalized": candidate,
				"parenttype": "Omni Identity",
				"alias_type": ["in", ["Phone", "WhatsApp"]],
			},
			"parent",
		)
		if alias_parent:
			return alias_parent
	return None


def sticky_agent(identity: str) -> str | None:
	"""The agent who last handled this contact, on any channel.

	Resolution is by identity rather than phone number, so a customer calling from a different
	handset — or last spoken to over WhatsApp — still reaches the person who knows them. That is
	the whole point of having an identity layer.
	"""
	if not identity:
		return None
	rows = frappe.get_all(
		"Excom Thread",
		filters={"omni_identity": identity, "assigned_to": ["is", "set"]},
		fields=["assigned_to"],
		order_by="last_message_at desc, modified desc",
		limit=1,
	)
	return rows[0].assigned_to if rows else None


# ── who is on this line ───────────────────────────────────────────────────────


def line_agents(account: str) -> list[dict]:
	"""Every agent who works this line, with the destinations each can be reached on.

	Cached, because this is three joins and it changes when somebody edits a team — not when a call
	arrives. Invalidated from the team, account and endpoint hooks.
	"""
	key = f"{_LINE_AGENTS}:{account}"
	cached = frappe.cache.get_value(key)
	if cached is not None:
		return cached

	teams = frappe.get_all(
		"Excom Account Team",
		filters={"parent": account, "parenttype": "Excom Channel Account"},
		fields=["team"],
		order_by="idx asc",
	)
	team_names = [row.team for row in teams if row.team]

	if team_names:
		expanded: list[str] = []
		for team in team_names:
			expanded.append(team)
			expanded.extend(_descendants(team))
		team_names = list(dict.fromkeys(expanded))
		members = frappe.get_all(
			"Excom Team Member",
			filters={"parent": ["in", team_names], "parenttype": "Excom Team"},
			fields=["user", "parent"],
		)
	else:
		# A line with no team restriction is worked by every Excom agent, which is the same rule
		# `allowed_teams` already uses elsewhere: empty means everyone, not nobody.
		members = [
			{"user": u, "parent": None} for u in _all_excom_agents()
		]

	first_team: dict[str, str] = {}
	for row in members:
		user = row.get("user") if isinstance(row, dict) else row.user
		parent = row.get("parent") if isinstance(row, dict) else row.parent
		if user and user not in first_team:
			first_team[user] = parent

	users = list(first_team)
	if not users:
		frappe.cache.set_value(key, [], expires_in_sec=CACHE_TTL)
		return []

	enabled = {
		row.name: row.mobile_no
		for row in frappe.get_all(
			"User",
			filters={"name": ["in", users], "enabled": 1},
			fields=["name", "mobile_no"],
		)
	}
	endpoints = {
		row.user: row.sip_uri
		for row in frappe.get_all(
			"Excom Voice Endpoint",
			filters={"channel_account": account, "status": "Active", "user": ["in", users]},
			fields=["user", "sip_uri"],
		)
	}

	agents = [
		{
			"user": user,
			"team": first_team.get(user),
			"sip_uri": endpoints.get(user) or "",
			"mobile": normalize_phone(enabled.get(user) or ""),
		}
		for user in users
		if user in enabled
	]
	frappe.cache.set_value(key, agents, expires_in_sec=CACHE_TTL)
	return agents


def _descendants(team: str) -> list[str]:
	from excom.excom.doctype.excom_team.excom_team import get_descendant_teams

	return list(get_descendant_teams(team) or [])


def _all_excom_agents() -> list[str]:
	return frappe.get_all(
		"Has Role",
		filters={"role": ["in", ["Excom Agent", "Excom Admin"]], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)


# ── the decision ──────────────────────────────────────────────────────────────


def build_decision(
	account_name: str,
	caller_number: str,
	identity: str | None = None,
	stage: str = "",
) -> tuple[CallDecision, dict]:
	"""Decide who rings. Returns the decision and the context the handler needs to persist it.

	There is no fallback to "some System Managers" or "ten enabled users". A line nobody works
	plays a message and logs a missed call, loudly. Ringing finance because a team was misconfigured
	is worse than not ringing at all.
	"""
	account = frappe.get_cached_doc("Excom Channel Account", account_name)
	identity = identity or resolve_identity_readonly(caller_number)
	sticky = sticky_agent(identity) if identity else None

	agents = line_agents(account_name)
	by_user = {a["user"]: a for a in agents}

	allow_browser = bool(account.get("voice_allow_browser_calls"))
	allow_phone = bool(account.get("voice_allow_phone_calls"))
	ring_both = bool(account.get("voice_ring_both"))
	strategy = account.get("voice_ring_strategy") or "Sticky then Team"

	sticky_first = strategy in ("Sticky then Team", "Sticky only") and sticky in by_user
	team_allowed = strategy in ("Sticky then Team", "Team only")

	ordered: list[dict] = []
	if sticky_first:
		ordered.append(by_user[sticky])
	if team_allowed and stage != "sticky":
		ordered.extend(a for a in agents if a["user"] != sticky)

	destinations: list[Destination] = []
	ring_set: list[str] = []
	for agent in ordered:
		user = agent["user"]
		reachable = _destinations_for(
			agent, allow_browser, allow_phone, account_name, ring_both
		)
		if not reachable:
			continue
		destinations.extend(reachable)
		if user not in ring_set:
			ring_set.append(user)

	record_policy = account.get("voice_record_policy") or "All"
	decision = CallDecision(
		destinations=destinations,
		ring_set=ring_set,
		parallel=True,
		ring_seconds=_ring_seconds(account, sticky_first and len(ring_set) == 1),
		record=record_policy in ("All", "Inbound only"),
		record_channels="stereo"
		if (account.get("voice_recording_channels") or "Dual") == "Dual"
		else "mono",
		consent_prompt=account.get("voice_consent_prompt") or "",
		caller_id=normalize_phone(account.get("voice_number") or ""),
		max_conversation_seconds=cint(account.get("voice_max_call_seconds")) or 3600,
	)

	context = {
		"account": account_name,
		"identity": identity,
		"sticky_agent": sticky if sticky_first else None,
		"caller_number": normalize_phone(caller_number),
		"business_number": normalize_phone(account.get("voice_number") or ""),
	}
	return decision, context


def _destinations_for(
	agent: dict, allow_browser: bool, allow_phone: bool, account: str, ring_both: bool = False
) -> list[Destination]:
	"""How to reach one agent.

	The phone is a fallback, not a second doorbell. Ringing a connected agent's browser AND their
	personal mobile on every call means their own phone goes off all day for work they are already
	sitting in front of — which is what happened on the first day of real inbound traffic.

	So: if their softphone is connected, that is where the call goes. The mobile is added only when
	the browser cannot take it — mic denied, tab closed, laptop asleep, UDP blocked — which is
	exactly the set of failures the two-transport model exists for. A line that genuinely wants both
	at once can say so with `voice_ring_both`.
	"""
	user = agent["user"]
	out: list[Destination] = []

	browser_ready = bool(
		allow_browser and agent.get("sip_uri") and presence.can_ring_browser(user, account)
	)
	if browser_ready:
		out.append(Destination(kind="sip", ref=agent["sip_uri"], user=user, label="browser"))

	if allow_phone and agent.get("mobile") and presence.can_ring_phone(user):
		if not browser_ready or ring_both:
			out.append(Destination(kind="pstn", ref=agent["mobile"], user=user, label="phone"))
	return out


def _ring_seconds(account, sticky_alone: bool) -> int:
	if sticky_alone:
		return cint(account.get("voice_sticky_ring_seconds")) or DEFAULT_STICKY_SECONDS
	return cint(account.get("voice_team_ring_seconds")) or DEFAULT_TEAM_SECONDS


def fallback_decision(account_name: str) -> CallDecision:
	"""Used when the main route errored or timed out. Cache-only: every agent on the line who has
	any destination at all, no sticky lookup, no identity resolution."""
	try:
		account = frappe.get_cached_doc("Excom Channel Account", account_name)
		agents = line_agents(account_name)
	except Exception:
		return CallDecision()

	destinations, ring_set = [], []
	for agent in agents:
		if agent.get("sip_uri"):
			destinations.append(
				Destination(kind="sip", ref=agent["sip_uri"], user=agent["user"], label="browser")
			)
		if agent.get("mobile"):
			destinations.append(
				Destination(kind="pstn", ref=agent["mobile"], user=agent["user"], label="phone")
			)
		if agent["user"] not in ring_set:
			ring_set.append(agent["user"])

	return CallDecision(
		destinations=destinations,
		ring_set=ring_set,
		ring_seconds=DEFAULT_TEAM_SECONDS,
		record=False,
		caller_id=normalize_phone(account.get("voice_number") or ""),
	)
