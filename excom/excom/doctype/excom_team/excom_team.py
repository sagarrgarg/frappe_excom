"""Excom Team controller."""

import frappe
from frappe import _
from frappe.model.document import Document

GENERAL_TEAM = "General"


class ExcomTeam(Document):
    def validate(self):
        self._check_duplicate_members()
        self._check_circular_parent()
        if self.get("__islocal"):
            return
        if self.name == GENERAL_TEAM and self.has_value_changed("team_name"):
            frappe.throw(_("The General team cannot be renamed"))

    def before_rename(self, old_name, new_name, merge=False):
        if old_name == GENERAL_TEAM:
            frappe.throw(_("The General team cannot be renamed"))

    def on_trash(self):
        if self.name == GENERAL_TEAM:
            frappe.throw(_("The General team cannot be deleted"))
        self._check_no_work_left()
        if frappe.db.exists("Excom Team", {"parent_team": self.name}):
            children = frappe.get_all("Excom Team", filters={"parent_team": self.name}, pluck="name")
            frappe.throw(
                _("{0} still has teams under it: {1}. Move or delete those first.").format(
                    self.name, ", ".join(children)
                )
            )

    def _check_no_work_left(self):
        """Refuse to delete a desk that still holds work.

        A lead pointing at a team that no longer exists matches nobody's visibility, so it silently
        stops being visible to every single person except a Sales Master Manager. Nothing errors and
        nothing appears in a list; the work simply stops existing for the people meant to do it.
        """
        from excom.excom.services.crm_visibility import TEAM_FIELDS

        holding = []
        for doctype, field in TEAM_FIELDS.items():
            if not frappe.db.exists("DocType", doctype) or not frappe.get_meta(doctype).has_field(field):
                continue
            filters = {field: self.name}
            if doctype == "Excom Thread":
                filters["status"] = ["!=", "Closed"]
            count = frappe.db.count(doctype, filters)
            if count:
                holding.append(f"{count} {doctype}")
        if holding:
            frappe.throw(
                _("{0} still holds {1}. Move that work to another team first.").format(
                    self.name, ", ".join(holding)
                ),
                title=_("Team is still in use"),
            )

    def _check_duplicate_members(self):
        """Prevent the same user from appearing twice in one team."""
        seen = set()
        for m in self.get("members", []):
            if m.user in seen:
                frappe.throw(_("User {0} is already in this team").format(m.user))
            seen.add(m.user)

    def _check_circular_parent(self):
        """Prevent circular parent_team references."""
        if not self.parent_team:
            return
        if self.parent_team == self.name:
            frappe.throw(_("A team cannot be its own parent"))
        visited = {self.name}
        current = self.parent_team
        while current:
            if current in visited:
                frappe.throw(_("Circular parent chain detected at team {0}").format(current))
            visited.add(current)
            current = frappe.db.get_value("Excom Team", current, "parent_team")


def get_user_teams(user: str = "") -> list[str]:
    """Return team names where the given user is a member."""
    if not user:
        user = frappe.session.user

    if user == "Administrator":
        return frappe.get_all("Excom Team", pluck="name")

    return frappe.get_all(
        "Excom Team Member",
        filters={"parenttype": "Excom Team", "user": user},
        pluck="parent",
    )


def get_user_teams_with_ancestors(user: str = "") -> list[str]:
    """Return team names where the user is a member, plus all ancestor teams.

    If a user belongs to "Sales Delhi" whose parent is "Sales", the result
    includes both "Sales Delhi" and "Sales". This is used for channel account
    visibility — an account visible to "Sales" is also visible to "Sales Delhi" members.
    """
    direct = get_user_teams(user)
    if not direct:
        return []

    all_teams = set(direct)
    to_resolve = list(direct)

    parent_map = {
        r.name: r.parent_team
        for r in frappe.get_all("Excom Team", fields=["name", "parent_team"])
        if r.parent_team
    }

    while to_resolve:
        team = to_resolve.pop()
        parent = parent_map.get(team)
        if parent and parent not in all_teams:
            all_teams.add(parent)
            to_resolve.append(parent)

    return list(all_teams)


def get_descendant_teams(team_name: str) -> list[str]:
    """Return all descendant team names (children, grandchildren, etc.)."""
    all_teams = frappe.get_all("Excom Team", fields=["name", "parent_team"])
    children_map: dict[str, list[str]] = {}
    for t in all_teams:
        if t.parent_team:
            children_map.setdefault(t.parent_team, []).append(t.name)

    result = []
    queue = [team_name]
    while queue:
        current = queue.pop(0)
        for child in children_map.get(current, []):
            result.append(child)
            queue.append(child)

    return result


def on_doctype_update():
	# Read on every permission check now that team membership decides lead and thread visibility.
	frappe.db.add_index("Excom Team Member", ["user"])
