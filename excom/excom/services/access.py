"""One way to refuse, and one way to say why.

Every Excom guard used to throw a bare sentence — "Not permitted", "You do not have access to
Excom" — which tells the person nothing they can act on. A refusal is only useful if it names the
action, the role or team that would allow it, and what the person actually holds today, so the
message they paste to an administrator is the whole ticket.

Use `deny()` from any guard. It raises frappe.PermissionError, so callers that catch that keep
working, and the text arrives at the browser in _server_messages like any other frappe.throw.
"""

from collections.abc import Iterable

import frappe
from frappe import _

# The roles a person can hold that mean anything inside Excom. Listing what somebody has is only
# helpful if the list is short: "you have Excom Agent" is an answer, the 40 roles of a real ERP user
# are noise.
EXCOM_ROLE_NAMES = ("System Manager", "Excom Admin", "Excom Admin", "Excom Agent")


def excom_roles_of(user: str | None = None) -> list[str]:
	"""The Excom-relevant roles this user holds, in tier order."""
	held = set(frappe.get_roles(user or frappe.session.user))
	return [r for r in EXCOM_ROLE_NAMES if r in held]


def deny(
	what: str,
	*,
	needs_roles: Iterable[str] = (),
	needs_team: str = "",
	detail: str = "",
	user: str | None = None,
) -> None:
	"""Refuse an action and explain it. Always raises frappe.PermissionError.

	what        — what was refused, as a sentence: "You cannot open this conversation."
	needs_roles — any one of these roles would allow it.
	needs_team  — membership of this team would allow it.
	detail      — the specific fact that decided it: who owns the record, which desk it sits on.
	"""
	user = user or frappe.session.user
	lines = [what]

	if needs_roles:
		roles = list(needs_roles)
		lines.append(
			_("Needs one of these roles: {0}.").format(", ".join(roles))
			if len(roles) > 1
			else _("Needs the {0} role.").format(roles[0])
		)
		held = excom_roles_of(user)
		lines.append(_("You have: {0}.").format(", ".join(held)) if held else _("You have no Excom role."))

	if needs_team:
		lines.append(_("Or membership of the {0} team.").format(needs_team))

	if detail:
		lines.append(detail)

	lines.append(_("Signed in as {0}.").format(user))

	frappe.throw("<br>".join(lines), frappe.PermissionError, title=_("Permission denied"))
