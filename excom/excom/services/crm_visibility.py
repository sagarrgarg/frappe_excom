"""One rule for who may see a CRM record.

The company's model, stated by the owner: a lead nobody has been given to is visible only to a
Sales Master Manager. It reaches anyone else exactly three ways — a Sales Master Manager hands it
over, a sales head hands it to their team, or auto-assignment does. Nothing else grants sight of it.

So a scoped user sees a record only when one of these is true:
  * they own it            (lead_owner / opportunity_owner)
  * it is assigned to them (ToDo, i.e. the _assign column)
  * it belongs to a team they are in, or, if they run a team, to any team beneath theirs

Everything in the app that answers "which leads may this user see" goes through here, so the Excom
UI, the Desk list, a report and the REST API cannot drift apart.
"""

import frappe

from excom.excom.services import crm_gateway as gw
from excom.excom.doctype.excom_team.excom_team import get_descendant_teams, get_user_teams

# Bypass the scope entirely. Deliberately short: the owner asked that a fresh lead be visible to a
# Sales Master Manager and nobody else, and every extra role here is another person who sees it.
# Excom Admin is here because it owns the system and needs to be able to support it; Excom Manager
# is not, because running desks and people is a capability, not a licence to read every lead.
BYPASS_ROLES = {"System Manager", "Excom Admin", "Sales Master Manager"}

# The doctypes this rule governs, mapped to their owner field.
OWNER_FIELD = {gw.LEAD: "lead_owner", gw.OPPORTUNITY: "opportunity_owner"}

# One idea, two field names, both correct where they sit. On Excom Thread the field is
# `assigned_team`, which reads properly beside `assigned_to` on a doctype we own. On Lead and
# Opportunity it is `excom_team`, because an unprefixed custom field on a doctype we do NOT own is
# how our `customer_type` once shadowed ERPNext's and broke every Customer save, and v16's native
# CRM may well introduce an `assigned_team` of its own.
#
# So the names stay and the drift goes: nothing outside this registry may spell either of them.
# test_team_registry.py checks the registry against the live schema, so the day somebody renames a
# field, a test says so instead of a query quietly returning nothing a year from now.
TEAM_FIELDS = {
	"Excom Thread": "assigned_team",
	gw.LEAD: "excom_team",
	gw.OPPORTUNITY: "excom_team",
}

def team_field(doctype: str) -> str | None:
	"""The name of the "which team owns this" field on a doctype, or None if it has none."""
	field = TEAM_FIELDS.get(doctype)
	if field and frappe.get_meta(doctype).has_field(field):
		return field
	return None


def get_team(doc) -> str | None:
	"""Read the owning team off any record that has one, without naming the field."""
	field = team_field(doc.get("doctype") if hasattr(doc, "get") else doc.doctype)
	return doc.get(field) if field else None


def set_team(doctype: str, name: str, team: str | None) -> None:
	"""Write the owning team on any record that has one, without naming the field."""
	field = team_field(doctype)
	if field:
		frappe.db.set_value(doctype, name, field, team, update_modified=False)


def is_enforced() -> bool:
	"""The switch. Off = the pre-existing ERPNext behaviour, unchanged."""
	try:
		return bool(frappe.db.get_single_value("Excom Settings", "enforce_crm_visibility"))
	except Exception:
		return False


def bypasses(user: str) -> bool:
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & BYPASS_ROLES)


def visible_teams(user: str) -> list[str]:
	"""Teams whose records this user may see: the ones they belong to, plus everything below any
	team they manage. A sales head therefore sees their whole branch of the tree, a member sees
	only their own team."""
	teams = set(get_user_teams(user))
	managed = frappe.get_all(
		"Excom Team Member",
		filters={"parenttype": "Excom Team", "user": user, "role": "Manager"},
		pluck="parent",
	)
	for team in managed:
		teams.update(get_descendant_teams(team))
	return sorted(teams)


def _conditions(doctype: str, user: str) -> list[str]:
	table = f"`tab{doctype}`"
	esc = frappe.db.escape(user)
	ors = [f"{table}.`owner` = {esc}", f"{table}.`_assign` LIKE {frappe.db.escape('%' + user + '%')}"]
	owner_field = OWNER_FIELD.get(doctype)
	if owner_field and frappe.get_meta(doctype).has_field(owner_field):
		ors.insert(0, f"{table}.`{owner_field}` = {esc}")
	field = team_field(doctype)
	if field:
		teams = visible_teams(user)
		if teams:
			team_list = ", ".join(frappe.db.escape(t) for t in teams)
			ors.append(f"{table}.`{field}` IN ({team_list})")
	return ors


def query_conditions(doctype: str, user: str | None = None) -> str:
	"""SQL added to every list query. Empty string = no restriction."""
	user = user or frappe.session.user
	if not is_enforced() or bypasses(user):
		return ""
	return "(" + " OR ".join(_conditions(doctype, user)) + ")"


def can_read(doc, user: str | None = None) -> bool:
	"""Same rule, one document at a time (form view, API get, links)."""
	user = user or frappe.session.user
	if not is_enforced() or bypasses(user):
		return True
	if doc.get("owner") == user:
		return True
	owner_field = OWNER_FIELD.get(doc.doctype)
	if owner_field and doc.get(owner_field) == user:
		return True
	if user in (doc.get("_assign") or ""):
		return True
	team = get_team(doc)
	return bool(team and team in visible_teams(user))


def lead_query_conditions(user: str | None = None) -> str:
	return query_conditions(gw.LEAD, user)


def opportunity_query_conditions(user: str | None = None) -> str:
	return query_conditions(gw.OPPORTUNITY, user)


def lead_has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return can_read(doc, user)


def opportunity_has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	return can_read(doc, user)


def team_for_user(user: str) -> str | None:
	"""The team a record should carry when it is handed to this user. A user in one team is
	unambiguous; a user in several keeps the record on the deepest team, which is the most specific
	claim on it. Returns None when the user is in no team at all."""
	if user in ("Administrator", "Guest"):
		return None
	# Deliberately not get_user_teams(): that reports every team for Administrator, which would
	# stamp a team on any lead the system itself touches.
	teams = frappe.get_all("Excom Team Member", filters={"parenttype": "Excom Team", "user": user}, pluck="parent")
	if not teams:
		return None
	if len(teams) == 1:
		return teams[0]
	depth = {}
	for t in teams:
		d, cur = 0, t
		while cur and d < 20:
			cur = frappe.db.get_value("Excom Team", cur, "parent_team")
			d += 1 if cur else 0
		depth[t] = d
	return max(sorted(teams), key=lambda t: depth[t])


def branch(team: str) -> set[str]:
	"""A team and everything under it. Work handed to somebody inside this branch stays put."""
	if not team:
		return set()
	return {team} | set(get_descendant_teams(team))


def stamp_team(doctype: str, name: str, user: str) -> str | None:
	"""Record which desk a record belongs to, on every path that hands it over: the owner field
	changing, claim-on-talk, a manual Desk assignment and an Assignment Rule.

	A team already on the record is kept while the new person sits inside that branch, so a sales
	head's placement outranks a later claim by one of their own people. But handing the record to
	somebody in a different part of the tree means the work has actually moved, and the desk has to
	move with it: leaving it behind is how a lead ends up visible to a desk that is not working it
	and invisible to the head of the desk that is.
	"""
	field = team_field(doctype)
	if doctype not in OWNER_FIELD or not field:
		return None
	new_team = team_for_user(user)
	if not new_team:
		return None
	current = frappe.db.get_value(doctype, name, field)
	if current:
		if current == new_team or new_team in branch(current) or current in branch(new_team):
			return None
		set_team(doctype, name, new_team)
		frappe.get_doc({
			"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
			"reference_name": name, "content": _team_move_note(current, new_team, user),
		}).insert(ignore_permissions=True)
		return new_team
	set_team(doctype, name, new_team)
	return new_team


def _team_move_note(old: str, new: str, user: str) -> str:
	return f"Moved from <b>{frappe.utils.escape_html(old)}</b> to <b>{frappe.utils.escape_html(new)}</b> because it was assigned to {frappe.utils.escape_html(user)}."


def backfill_teams(doctype: str | None = None, limit: int = 0) -> dict:
	"""Put a team on the records that already have an owner.

	Without this, switching enforcement on hides every historical lead from everyone except a Sales
	Master Manager, including leads their current owner has been working for months. Run it once
	before turning the switch on. Records with no owner are left alone on purpose: those are the
	ones that are meant to go back to the top and be handed out.
	"""
	doctypes = [doctype] if doctype else list(OWNER_FIELD)
	stamped, skipped = 0, 0
	for dt in doctypes:
		owner_field = OWNER_FIELD[dt]
		rows = frappe.get_all(
			dt,
			filters={owner_field: ["is", "set"], team_field(dt): ["is", "not set"]},
			fields=["name", owner_field],
			limit_page_length=limit or 0,
		)
		for r in rows:
			team = team_for_user(r.get(owner_field))
			if team:
				set_team(dt, r.name, team)
				stamped += 1
			else:
				skipped += 1
	frappe.db.commit()
	return {"stamped": stamped, "skipped_owner_in_no_team": skipped}


def impact_report() -> dict:
	"""What turning the switch on would change, before anyone turns it on."""
	out = {"enforced": is_enforced(), "doctypes": {}}
	for dt in OWNER_FIELD:
		owner_field = OWNER_FIELD[dt]
		total = frappe.db.count(dt)
		out["doctypes"][dt] = {
			"total": total,
			"with_owner": frappe.db.count(dt, {owner_field: ["is", "set"]}),
			"with_team": frappe.db.count(dt, {team_field(dt): ["is", "set"]}),
			"visible_to_sales_master_manager_only": frappe.db.count(dt, {owner_field: ["is", "not set"], team_field(dt): ["is", "not set"]}),
		}
	readers = frappe.get_all("Has Role", filters={"role": "Sales Master Manager", "parenttype": "User"}, pluck="parent")
	out["sales_master_managers"] = sorted(u for u in readers if frappe.db.get_value("User", u, "enabled"))
	losing = set()
	for role in ("Sales Manager", "Sales User"):
		for u in frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"):
			if frappe.db.get_value("User", u, "enabled") and not bypasses(u):
				losing.add(u)
	out["users_whose_reach_narrows"] = sorted(losing)
	return out


def move_party_to_team(identity: str, team: str, reason: str = "", actor: str | None = None) -> dict:
	"""Move everything about one contact to one desk: their open CRM records and their open
	conversations together.

	A conversation transferred on its own leaves the lead behind, and then the desk that answers the
	customer cannot see what was sold to them while the desk that holds the lead cannot see what the
	customer just said. One contact belongs to one desk at a time.
	"""
	actor = actor or frappe.session.user
	moved = {"records": [], "threads": []}
	if not identity or not team:
		return moved
	from excom.excom.services import crm_gateway as gw

	for r in gw.find_open_records_for_identity(identity):
		field = team_field(r.doctype)
		if not field or frappe.db.get_value(r.doctype, r.name, field) == team:
			continue
		old = frappe.db.get_value(r.doctype, r.name, field)
		set_team(r.doctype, r.name, team)
		frappe.get_doc({
			"doctype": "Comment", "comment_type": "Info", "reference_doctype": r.doctype,
			"reference_name": r.name,
			"content": f"Moved to <b>{frappe.utils.escape_html(team)}</b> with the conversation" + (f" ({frappe.utils.escape_html(reason)})" if reason else "") + ".",
		}).insert(ignore_permissions=True)
		moved["records"].append(f"{r.doctype} {r.name}" + (f" (was {old})" if old else ""))

	for t in frappe.get_all("Excom Thread", filters={"omni_identity": identity, "status": ["!=", "Closed"]}, pluck="name"):
		if frappe.db.get_value("Excom Thread", t, "assigned_team") != team:
			frappe.db.set_value("Excom Thread", t, "assigned_team", team, update_modified=False)
			moved["threads"].append(t)
	return moved
