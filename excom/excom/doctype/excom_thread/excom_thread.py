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


def open_accounts(user: str) -> set[str]:
	"""Channel accounts (numbers, mailboxes, pages) this user's teams are allowed on.

	An account with no allowed teams is open to every agent in a team; otherwise it is open to members of
	those teams, and of the teams beneath them (the same reading get_channel_accounts has always
	used for the new-conversation picker). Cached for the request: every row of an inbox asks.
	"""
	cache = getattr(frappe.local, "excom_open_accounts", None)
	if cache is None:
		cache = {}
		frappe.local.excom_open_accounts = cache
	if user in cache:
		return cache[user]
	from excom.excom.doctype.excom_team.excom_team import get_user_teams_with_ancestors

	teams = set(get_user_teams_with_ancestors(user)) | visible_teams(user)
	if not teams:
		# Somebody not placed in any team yet (a new hire) works on nothing until a manager places
		# them — the inbox tells them so — rather than on every open account at once.
		cache[user] = set()
		return cache[user]
	allowed: dict[str, set] = {}
	for r in frappe.get_all("Excom Account Team", filters={"parenttype": "Excom Channel Account"}, fields=["parent", "team"]):
		allowed.setdefault(r.parent, set()).add(r.team)
	out = {a for a in frappe.get_all("Excom Channel Account", pluck="name") if not allowed.get(a) or allowed[a] & teams}
	cache[user] = out
	return out


def can_access(doc, user: str | None = None) -> bool:
	"""The rule, one thread at a time. A person sees a conversation that is:

	1. assigned to them;
	2. on one of their desks (their teams, and the teams below any team they manage);
	3. owned by nobody, on a number or mailbox their team is allowed on — an account with no
	   allowed teams is open to every agent. Before this, an unowned chat was visible only to the
	   shared-inbox team, so on GGIL 1,652 WhatsApp chats parked on an empty desk and every unowned
	   Instagram, Messenger and mailbox conversation could be seen by admins alone;
	4. owned by nobody and on no desk, if they are in the shared-inbox team.

	A conversation somebody owns stays with them, their desk and admins.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & MANAGER_ROLES:
		return True
	if not (roles & EXCOM_ROLES):
		return False
	if isinstance(doc, str):
		doc = frappe.db.get_value("Excom Thread", doc, ["assigned_to", "assigned_team", "account"], as_dict=True)
		if not doc:
			return False
	if doc.get("assigned_to") == user:
		return True
	teams = visible_teams(user)
	from excom.excom.services.crm_visibility import get_team

	team = get_team(doc) if doc.get("doctype") else doc.get("assigned_team")
	if team and team in teams:
		return True
	if doc.get("assigned_to"):
		return False  # somebody owns it: it is theirs and their desk's
	if doc.get("account") and doc.get("account") in open_accounts(user):
		return True
	return not team and shared_inbox_team() in teams


def can_send_from(doc, user: str | None = None) -> bool:
	"""Replying needs the number or mailbox as well as the conversation: a desk can see a chat on
	an account it is not allowed on (routing put it there), but it answers from its own accounts.
	The person a conversation is assigned to may always reply — somebody gave it to them."""
	user = user or frappe.session.user
	if user == "Administrator" or set(frappe.get_roles(user)) & MANAGER_ROLES:
		return True
	if isinstance(doc, str):
		doc = frappe.db.get_value("Excom Thread", doc, ["assigned_to", "account"], as_dict=True)
		if not doc:
			return False
	return doc.get("assigned_to") == user or (doc.get("account") or "") in open_accounts(user)


def visibility_sql(user: str, alias: str) -> str:
	"""SQL form of can_access() for table alias `alias`; "" when the user sees everything."""
	if user == "Administrator":
		return ""
	user_roles = set(frappe.get_roles(user))
	if user_roles & MANAGER_ROLES:
		return ""
	if not (user_roles & EXCOM_ROLES):
		return "1=0"
	conditions = [f"{alias}.assigned_to = {frappe.db.escape(user)}"]
	teams = visible_teams(user)
	if teams:
		team_list = ", ".join(frappe.db.escape(t) for t in sorted(teams))
		conditions.append(f"{alias}.assigned_team IN ({team_list})")
	accounts = open_accounts(user)
	if accounts:
		acc_list = ", ".join(frappe.db.escape(a) for a in sorted(accounts))
		conditions.append(f"(COALESCE({alias}.assigned_to, '') = '' AND {alias}.account IN ({acc_list}))")
	if shared_inbox_team() in teams:
		conditions.append(f"(COALESCE({alias}.assigned_to, '') = '' AND COALESCE({alias}.assigned_team, '') = '')")
	return f"({' OR '.join(conditions)})"


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
	return visibility_sql(user or frappe.session.user, "`tabExcom Thread`")


def on_doctype_update():
	frappe.db.add_index("Excom Thread", ["last_message_at"])
	frappe.db.add_index("Excom Thread", ["omni_identity", "channel", "account"])
	# Every inbox query and every permission check filters on these three.
	frappe.db.add_index("Excom Thread", ["assigned_to", "status"])
	frappe.db.add_index("Excom Thread", ["assigned_team", "status"])
