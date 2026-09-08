import frappe
from frappe.model.document import Document

# The only blanket bypass: the tier that owns the system can see all of it, because it has to be
# able to support it. Everybody else — Excom Agent — sees what their team memberships give them.
MANAGER_ROLES = {"System Manager", "Excom Admin"}

# Anybody who may open Excom at all. Checking for "Excom Agent" alone locked out an admin who was
# not also given the agent role — the tiers are cumulative in capability, so they must be
# cumulative here too.
EXCOM_ROLES = {"Excom Admin", "Excom Agent"}

# ─── visibility ───────────────────────────────────────────────────────────────
# One rule, used by the doctype class, the has_permission hook, the list query and api/chat.py.
# These used to be three separate implementations that disagreed: the Excom API let a General
# member open an unclaimed chat while the permission hook and the Desk list denied the same row.

# Which team acts as the shared inbox. It was hardcoded to "General", so an organisation that names
# its teams anything else had no shared inbox at all and unclaimed chats were invisible to every
# agent. Configurable in Excom Settings; "General" stays the default because that is what exists.
DEFAULT_SHARED_INBOX = "General"


def shared_inbox_team() -> str:
	try:
		return frappe.db.get_single_value("Excom Settings", "shared_inbox_team") or DEFAULT_SHARED_INBOX
	except Exception:
		return DEFAULT_SHARED_INBOX


def visible_teams(user: str) -> set[str]:
	"""Teams whose threads this user may see: the ones they are in, plus everything below any team
	they manage. Same shape as the CRM rule in services/crm_visibility.py, so a sales head sees the
	conversations belonging to the teams whose leads they can already see."""
	from excom.excom.services.crm_visibility import visible_teams as crm_visible_teams

	return set(crm_visible_teams(user))


def can_access(doc, user: str | None = None) -> bool:
	"""The rule, one thread at a time."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & MANAGER_ROLES:
		return True
	if not (roles & EXCOM_ROLES):
		return False
	if isinstance(doc, str):
		doc = frappe.db.get_value("Excom Thread", doc, ["assigned_to", "assigned_team"], as_dict=True)
		if not doc:
			return False
	if doc.get("assigned_to") == user:
		return True
	teams = visible_teams(user)
	from excom.excom.services.crm_visibility import get_team

	team = get_team(doc) if doc.get("doctype") else doc.get("assigned_team")
	if team:
		return team in teams
	if doc.get("assigned_to"):
		# Somebody has claimed it, so it has left the shared inbox even though it carries no team.
		return False
	# Nobody owns it, so it belongs to whichever team acts as the shared inbox.
	return shared_inbox_team() in teams




class ExcomThread(Document):
	def before_insert(self):
		if not self.thread_key:
			self.compute_thread_key()
		self.denormalize_identity()

	def validate(self):
		if not self.thread_key:
			self.compute_thread_key()
		if not self.display_name:
			self.denormalize_identity()

	def has_permission(self, permtype: str = "read", user: str | None = None, **kwargs) -> bool:
		"""Excom Agent can only access threads assigned to them, to their teams, or unclaimed ones
		if they are in the shared inbox. See can_access() below: this is the same rule everywhere.

		Two things this override must not forget, because Document.has_permission does them and
		overriding it silently dropped both:

		1. `ignore_permissions`. Every internal writer — inbound webhooks, upsert_thread, the
		   broadcast runner — inserts with ignore_permissions=True and expects that to be honoured.
		   Without this check an agent starting a conversation was refused by their own new thread:
		   it is not assigned to anybody yet at insert time, so can_access() fell through to the
		   shared-inbox rule and said no.
		2. Creating. A thread being created has no owner and no team, so there is nothing to scope
		   against; the right question is whether this person may work in Excom at all. Who ends up
		   owning it is decided immediately after insert.
		"""
		if self.flags.ignore_permissions or frappe.flags.ignore_permissions:
			return True
		if permtype == "create" or self.get("__islocal") or not self.name:
			return bool(set(frappe.get_roles(user or frappe.session.user)) & (EXCOM_ROLES | MANAGER_ROLES))
		return can_access(self, user)

	def compute_thread_key(self):
		self.thread_key = f"{self.channel}:{self.account}:{self.omni_identity}"

	def denormalize_identity(self):
		if self.omni_identity:
			oi = frappe.db.get_value(
				"Omni Identity",
				self.omni_identity,
				["display_name", "primary_phone"],
				as_dict=True,
			)
			if oi:
				self.display_name = oi.display_name
				self.primary_phone = oi.primary_phone


def has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Hook function for frappe's has_permission system. Delegates to the single rule."""
	return can_access(doc, user)


def get_permission_query_conditions(user: str | None = None) -> str:
	"""SQL form of can_access(). Kept beside it so the list and the form cannot drift apart."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	user_roles = set(frappe.get_roles(user))
	if user_roles & MANAGER_ROLES:
		return ""

	if not (user_roles & EXCOM_ROLES):
		return "1=0"

	table = "`tabExcom Thread`"
	conditions = [f"{table}.assigned_to = {frappe.db.escape(user)}"]
	teams = visible_teams(user)
	if teams:
		team_list = ", ".join(frappe.db.escape(t) for t in sorted(teams))
		conditions.append(f"{table}.assigned_team IN ({team_list})")
	if shared_inbox_team() in teams:
		conditions.append(f"(COALESCE({table}.assigned_to, '') = '' AND COALESCE({table}.assigned_team, '') = '')")

	return f"({' OR '.join(conditions)})"


def on_doctype_update():
	frappe.db.add_index("Excom Thread", ["last_message_at"])
	frappe.db.add_index("Excom Thread", ["omni_identity", "channel", "account"])
	# Every inbox query and every permission check filters on these three.
	frappe.db.add_index("Excom Thread", ["assigned_to", "status"])
	frappe.db.add_index("Excom Thread", ["assigned_team", "status"])
