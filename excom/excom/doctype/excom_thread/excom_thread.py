import frappe
from frappe.model.document import Document

MANAGER_ROLES = {"System Manager", "Excom Manager"}

# ─── visibility ───────────────────────────────────────────────────────────────
# One rule, used by the doctype class, the has_permission hook, the list query and api/chat.py.
# These used to be three separate implementations that disagreed: the Excom API let a General
# member open an unclaimed chat while the permission hook and the Desk list denied the same row.

GENERAL_TEAM = "General"


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
	if "Excom User" not in roles:
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
	# Nobody owns it, so it belongs to the shared inbox, which is what the General team is for.
	return GENERAL_TEAM in teams




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

	def has_permission(self, permtype: str = "read", user: str | None = None) -> bool:
		"""Excom User can only access threads assigned to them, to their teams, or unclaimed ones
		if they are in the shared inbox. See can_access() below: this is the same rule everywhere."""
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

	if "Excom User" not in user_roles:
		return "1=0"

	table = "`tabExcom Thread`"
	conditions = [f"{table}.assigned_to = {frappe.db.escape(user)}"]
	teams = visible_teams(user)
	if teams:
		team_list = ", ".join(frappe.db.escape(t) for t in sorted(teams))
		conditions.append(f"{table}.assigned_team IN ({team_list})")
	if GENERAL_TEAM in teams:
		conditions.append(f"(COALESCE({table}.assigned_to, '') = '' AND COALESCE({table}.assigned_team, '') = '')")

	return f"({' OR '.join(conditions)})"


def on_doctype_update():
	frappe.db.add_index("Excom Thread", ["last_message_at"])
	frappe.db.add_index("Excom Thread", ["omni_identity", "channel", "account"])
	# Every inbox query and every permission check filters on these three.
	frappe.db.add_index("Excom Thread", ["assigned_to", "status"])
	frappe.db.add_index("Excom Thread", ["assigned_team", "status"])
