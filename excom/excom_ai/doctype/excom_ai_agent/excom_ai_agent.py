"""One AI colleague (HLD-007 §5.1).

What the record itself guarantees, whoever edits it:
- An owner who is an active Excom user; from Shadow on, a different active backup too. If the
  agent has a team, both belong to it (or are Excom Admins).
- No action is ever allowed more than its ceiling (constants.CEILINGS): prices, terms and
  quotations stay with a person; submitting a document is never allowed.
- Live needs an approved brief and a readiness check without blockers; editing the brief clears
  its approval (and a Live agent goes back to Shadow).
- On Hold and Retired can always be saved, even after the owner has left.
- Its channels are active Excom numbers and mailboxes, used by one agent at a time for outreach.
- It acts as its own user (service_user.py), created with it and disabled when it retires.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from excom.excom_ai import service_user
from excom.excom_ai.constants import check_policy_rows

ACTIVE = ("Shadow", "Live")
STOPPED = ("On Hold", "Retired")
EXCOM_ROLES = {"Excom Agent", "Excom Admin", "System Manager"}
MANAGERS = {"Excom Admin", "System Manager"}
# Editing any of these after approval clears the approval: a changed brief is checked again.
BRIEF_FIELDS = ("what_it_is", "mission", "audience", "tone", "tone_examples", "rules_do", "rules_dont")


class ExcomAIAgent(Document):
	def before_insert(self):
		if not self.policies:
			self._copy_type_defaults()

	def validate(self):
		self._check_people()
		self._check_brief()
		self._check_policies()
		self._check_channels()
		self._check_status()
		self.keywords = ", ".join(k.strip() for k in (self.keywords or "").split(",") if k.strip())

	def after_insert(self):
		self.db_set("service_user", service_user.ensure(self), update_modified=False)

	def on_update(self):
		if not self.is_new() and (
			self.has_value_changed("agent_name") or self.has_value_changed("status")
			or self.has_value_changed("team") or self.has_value_changed("agent_type")
		):
			self.db_set("service_user", service_user.ensure(self), update_modified=False)

	def on_trash(self):
		service_user.disable(self)

	@property
	def budget_scope(self) -> str:
		"""The scope ai_interface charges this agent's calls to."""
		return f"Excom AI Agent:{self.name}"

	def budget_caps(self) -> dict:
		return {"daily": self.daily_ai_budget or 0, "monthly": self.monthly_ai_budget or 0, "action": "Block"}

	# ------------------------------------------------------------ checks

	def _copy_type_defaults(self):
		t = frappe.get_cached_doc("Excom AI Agent Type", self.agent_type)
		for row in t.default_policies:
			self.append("policies", {k: row.get(k) for k in ("action", "mode", "sample_percent", "daily_cap", "condition")})

	def _check_people(self):
		if self.status in STOPPED and not self.has_value_changed("owner_user") and not self.has_value_changed("backup_user"):
			# Stopping or retiring an agent must work even when its owner has left.
			return
		self._check_person(self.owner_user, _("Owner"))
		if self.backup_user:
			self._check_person(self.backup_user, _("Backup"))
			if self.backup_user == self.owner_user:
				frappe.throw(_("The backup must be someone other than the owner."))
		elif self.status in ACTIVE:
			frappe.throw(_("An agent needs a backup before it starts working (Shadow or Live)."))
		for user, label in ((self.escalation_user, _("Escalation")),):
			if user:
				self._check_person(user, label, need_team=False)

	def _check_person(self, user, label, need_team=True):
		if service_user.is_service_user(user):
			frappe.throw(_("{0} must be a person, not an AI agent.").format(label))
		row = frappe.db.get_value("User", user, ["enabled"], as_dict=True)
		if not row or not row.enabled:
			frappe.throw(_("{0} {1} is not an active user.").format(label, user))
		roles = set(frappe.get_roles(user))
		if not roles & EXCOM_ROLES:
			frappe.throw(_("{0} {1} has no Excom role.").format(label, user))
		if need_team and self.team and not roles & {"Excom Admin", "System Manager"}:
			from excom.excom.doctype.excom_team.excom_team import get_user_teams_with_ancestors

			if self.team not in get_user_teams_with_ancestors(user):
				frappe.throw(_("{0} {1} is not in the team {2}.").format(label, user, self.team))

	def _check_brief(self):
		if self.is_new() or not self.brief_approved_on:
			return
		if not any(self.has_value_changed(f) for f in BRIEF_FIELDS):
			return
		self.brief_approved_on = None
		self.brief_approved_by = None
		if self.status == "Live":
			self.status = "Shadow"
			frappe.msgprint(_("The brief changed, so it needs approving again. The agent is back in Shadow until then."))

	def _check_policies(self):
		check_policy_rows(self.policies, frappe.db.get_value("Excom AI Agent Type", self.agent_type, "pack"))

	def _check_channels(self):
		seen = set()
		for row in self.channels:
			if row.channel_account in seen:
				frappe.throw(_("{0} is listed twice.").format(row.channel_account))
			seen.add(row.channel_account)
			acc = frappe.db.get_value("Excom Channel Account", row.channel_account, ["status", "channel"], as_dict=True)
			if not acc:
				frappe.throw(_("Unknown channel account {0}").format(row.channel_account))
			if acc.status != "Active" and self.status in ACTIVE:
				frappe.throw(_("{0} is not active.").format(row.channel_account))
			if acc.channel not in ("email", "whatsapp", "instagram", "messenger"):
				frappe.throw(_("{0}: agents work by email, WhatsApp, Instagram and Messenger for now.").format(row.channel_account))
			if row.purpose != "Replies" and self.status in ACTIVE:
				other = frappe.db.sql(
					"""SELECT a.name FROM `tabExcom AI Channel` c JOIN `tabExcom AI Agent` a ON a.name = c.parent
					WHERE c.channel_account = %s AND IFNULL(c.purpose, '') != 'Replies' AND a.status IN ('Shadow', 'Live')
					AND a.name != %s LIMIT 1""",
					(row.channel_account, self.name or ""),
				)
				if other:
					frappe.throw(_("{0} already does outreach for agent {1}.").format(row.channel_account, other[0][0]))

	def _check_status(self):
		if self.status == "Live":
			if not self.brief_approved_on:
				frappe.throw(_("Approve the brief before the agent goes live."))
			if self.readiness_state not in ("Ready", "Warnings"):
				frappe.throw(_("The readiness check must pass (no blockers) before the agent goes live."))
		if self.status != "On Hold" and self.has_value_changed("status"):
			self.hold_reason = None
		if cint(self.daily_ai_budget) < 0 or cint(self.monthly_ai_budget) < 0:
			frappe.throw(_("Budgets cannot be negative."))


@frappe.whitelist()
def approve_brief(name: str):
	"""The owner (or an admin) approves the current brief; it gets a new version number."""
	doc = frappe.get_doc("Excom AI Agent", name)
	frappe.has_permission("Excom AI Agent", "read", doc, throw=True)
	user = frappe.session.user
	if user not in (doc.owner_user, doc.backup_user) and not set(frappe.get_roles(user)) & MANAGERS \
			and not _team_manager(doc.team, user):
		frappe.throw(_("Only the agent's owner, backup, its team's manager or an Excom Admin can approve its brief."),
			frappe.PermissionError)
	if doc.status == "Retired":
		frappe.throw(_("This agent is retired."))
	doc.brief_version = cint(doc.brief_version) + 1
	doc.brief_approved_by = user
	doc.brief_approved_on = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)  # who may approve was checked above; owners only read agents otherwise
	return {"version": doc.brief_version}


def _team_manager(team: str | None, user: str) -> bool:
	return bool(team) and bool(frappe.db.exists(
		"Excom Team Member", {"parenttype": "Excom Team", "parent": team, "user": user, "role": "Manager"}))
