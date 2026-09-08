"""API endpoints for Excom Team management."""

import frappe
from frappe import _

from excom.excom.api.chat import _check_excom_access, _check_manager_access


@frappe.whitelist()
def get_my_teams() -> list:
    """Return teams where the current user is a member, with member count."""
    _check_excom_access()
    from excom.excom.doctype.excom_team.excom_team import get_user_teams

    user_roles = set(frappe.get_roles(frappe.session.user))
    is_manager = bool(user_roles & {"System Manager", "Excom Admin"})

    team_names = get_user_teams()

    if is_manager and "General" not in team_names:
        team_names.append("General")

    if not team_names:
        return []

    return frappe.get_all(
        "Excom Team",
        filters={"name": ["in", team_names]},
        fields=["name", "team_name", "description"],
        order_by="team_name asc",
    )


@frappe.whitelist()
def get_all_teams() -> list:
    """Return all teams with member count."""
    _check_excom_access()
    teams = frappe.get_all(
        "Excom Team",
        fields=["name", "team_name", "description"],
        order_by="team_name asc",
    )

    for team in teams:
        team["member_count"] = frappe.db.count(
            "Excom Team Member",
            {"parenttype": "Excom Team", "parent": team.name},
        )

    return teams


@frappe.whitelist()
def get_team_members(team: str) -> list:
    """Return members of a team with user details."""
    _check_excom_access()
    return frappe.db.sql(
        """
        SELECT tm.user, tm.role, u.full_name, u.user_image
        FROM `tabExcom Team Member` tm
        LEFT JOIN `tabUser` u ON u.name = tm.user
        WHERE tm.parent = %(team)s AND tm.parenttype = 'Excom Team'
        ORDER BY tm.role DESC, u.full_name ASC
        """,
        {"team": team},
        as_dict=True,
    )


@frappe.whitelist()
def create_team(team_name: str, description: str = "") -> dict:
    """Create a new Excom Team."""
    _check_manager_access()

    doc = frappe.get_doc({
        "doctype": "Excom Team",
        "team_name": team_name,
        "description": description,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True, "name": doc.name}


@frappe.whitelist()
def search_users(search: str = "", limit: int = 20) -> list:
    """Search system users by email or full name for the add-member picker."""
    _check_excom_access()
    limit = min(int(limit), 50)
    params: dict = {"limit": limit}

    where = "u.enabled = 1 AND u.user_type = 'System User'"
    if search:
        params["q"] = f"%{search}%"
        where += " AND (u.name LIKE %(q)s OR u.full_name LIKE %(q)s)"

    return frappe.db.sql(
        f"""
        SELECT u.name, u.full_name, u.user_image
        FROM `tabUser` u
        WHERE {where}
        ORDER BY u.full_name ASC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )


@frappe.whitelist()
def add_team_member(team: str, user: str, role: str = "Member") -> dict:
    """Add a member to a team."""
    _check_manager_access()

    doc = frappe.get_doc("Excom Team", team)

    for m in doc.members:
        if m.user == user:
            return {"success": False, "message": "User already in team"}

    doc.append("members", {"user": user, "role": role})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}


@frappe.whitelist()
def remove_team_member(team: str, user: str) -> dict:
    """Remove a member from a team."""
    _check_manager_access()

    doc = frappe.get_doc("Excom Team", team)
    doc.members = [m for m in doc.members if m.user != user]
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"success": True}
