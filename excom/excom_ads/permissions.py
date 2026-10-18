"""Who sees which ads data.

Scope: User Permissions on Company, Ads Workspace, Ads Brand and Ads Account. Every Ads record
carries those four fields (or is one of those records), so one rule covers every list, form,
report, API method and, later, AI tool:

  System Manager            sees everything.
  Excom Admin               sees everything unless User Permissions narrow them.
  everyone else             sees only what User Permissions grant. No grant, no rows.

Within a scope, a brand restriction is strict: rows with no brand (not yet allocated) are hidden
from a brand-scoped user, because "unallocated" spend may belong to another brand.

Sensitive rights (finance, budgets, bills, export, connections) are per-user flags on Ads Access
Profile, on top of the scope. Admin roles hold them implicitly.

Called from hooks (permission_query_conditions / has_permission) and directly by API code via
`scope_filters()` and `require()`.
"""

import frappe
from frappe import _

SUPER_ROLES = {"System Manager"}
ADMIN_ROLES = {"System Manager", "Excom Admin"}
MARKETING_ROLE = "Excom Marketing"
FINANCE_ROLES = {"Accounts User", "Accounts Manager"}

# scope dimension -> the doctype a User Permission names
DIMENSIONS = {"company": "Company", "workspace": "Ads Workspace", "brand": "Ads Brand", "account": "Ads Account"}

# For the dimension records themselves, the dimension is the record's own name.
SELF_FIELD = {"Ads Workspace": "workspace", "Ads Brand": "brand", "Ads Account": "account"}

# Records that are finance data as a whole, not just a few fields: need view_finance on top of scope.
FINANCE_DOCTYPES = {"Ads Billing Snapshot", "Ads Billing Record", "Ads Bill Document", "Ads Bill Account Map", "Ads TDS Certificate Log", "Ads Bill Source"}

CAPABILITIES = ("view_finance", "manage_budgets", "approve_bills", "export_data", "manage_connections", "approve_actions", "enable_automation")
# Rights an admin role carries without a profile. Automation rights always need an explicit profile.
ADMIN_IMPLICIT = {"view_finance", "manage_budgets", "approve_bills", "export_data", "manage_connections"}


def _user(user=None) -> str:
	return user or frappe.session.user


def _roles(user: str) -> set:
	return set(frappe.get_roles(user))


def is_super(user=None) -> bool:
	user = _user(user)
	return user == "Administrator" or bool(SUPER_ROLES & _roles(user))


def is_admin(user=None) -> bool:
	user = _user(user)
	return user == "Administrator" or bool(ADMIN_ROLES & _roles(user))


# ─── scope ────────────────────────────────────────────────────────────────────

def get_scope(user=None) -> dict | None:
	"""{dimension: set(allowed names)} for the restricted dimensions, {} for unrestricted, None for no access.

	Cached per request: list views call this once per query, reports many times.
	"""
	user = _user(user)
	cache = getattr(frappe.local, "excom_ads_scope", None)
	if cache is None:
		cache = frappe.local.excom_ads_scope = {}
	if user in cache:
		return cache[user]
	if is_super(user):
		cache[user] = {}
		return {}
	from frappe.core.doctype.user_permission.user_permission import get_user_permissions

	perms = get_user_permissions(user) or {}
	scope = {}
	for dim, dt in DIMENSIONS.items():
		rows = perms.get(dt) or []
		if rows:
			scope[dim] = {r.get("doc") for r in rows if r.get("doc")}
	if not is_admin(user) and MARKETING_ROLE not in _roles(user) and not (FINANCE_ROLES & _roles(user)):
		# Only marketing staff (and finance users, for finance records on the Desk) reach ads data,
		# whatever User Permissions they hold for other reasons.
		scope = None
	elif not scope and not is_admin(user):
		scope = None  # deny by default: a non-admin with no grant sees no ads data
	cache[user] = scope
	return scope


def clear_cache(*_args, **_kwargs):
	frappe.local.excom_ads_scope = {}
	frappe.local.excom_ads_constraints = {}


def _fields_for(doctype: str) -> dict:
	"""dimension -> column on this doctype."""
	meta = frappe.get_meta(doctype)
	out = {dim: dim for dim in DIMENSIONS if meta.has_field(dim)}
	if doctype in SELF_FIELD:
		out[SELF_FIELD[doctype]] = "name"
	return out


def _constraints(doctype: str, scope: dict, user: str):
	"""[(column, allowed values)] expressing every restricted dimension of the caller on this doctype,
	or None when one of them cannot be expressed — then the answer is "no rows", never "all rows".

	A dimension the doctype has no column for is derived through its account (sync jobs) or, for
	workspaces and brands, through the records that belong to them. Cached per request."""
	cache = getattr(frappe.local, "excom_ads_constraints", None)
	if cache is None:
		cache = frappe.local.excom_ads_constraints = {}
	key = (user, doctype)
	if key in cache:
		return cache[key]
	cols = _fields_for(doctype)
	out = []
	for dim in DIMENSIONS:
		allowed = scope.get(dim)
		if allowed is None:
			continue
		allowed = set(allowed)
		if dim in cols:
			out.append((cols[dim], allowed))
		elif "account" in cols and dim in ("company", "workspace", "brand"):
			out.append((cols["account"], set(frappe.get_all("Ads Account", filters={dim: ["in", sorted(allowed) or ["-"]]}, pluck="name"))))
		elif doctype == "Ads Workspace" and dim in ("brand", "account"):
			src = "Ads Brand" if dim == "brand" else "Ads Account"
			out.append(("name", {w for w in frappe.get_all(src, filters={"name": ["in", sorted(allowed) or ["-"]]}, pluck="workspace") if w}))
		elif doctype == "Ads Brand" and dim == "account":
			out.append(("name", {b for b in frappe.get_all("Ads Account", filters={"name": ["in", sorted(allowed) or ["-"]]}, pluck="brand") if b}))
		else:
			out = None
			break
	cache[key] = out
	return out


def _profile_condition(user: str) -> str:
	# Rights are personal. Only an admin with no scope restriction manages everyone's profile; a
	# scoped admin must not grant rights to people of companies they cannot see.
	if is_super(user) or (is_admin(user) and get_scope(user) == {}):
		return ""
	return f"`tabAds Access Profile`.`user` = {frappe.db.escape(user)}"


def query_conditions(doctype: str, user=None) -> str:
	user = _user(user)
	if doctype == "Ads Access Profile":
		return _profile_condition(user)
	scope = get_scope(user)
	if scope is None:
		return "1=0"
	if doctype in FINANCE_DOCTYPES and not has_capability("view_finance", user):
		return "1=0"
	if not scope:
		return ""
	constraints = _constraints(doctype, scope, user)
	if constraints is None:
		return "1=0"
	table = f"`tab{doctype}`"
	parts = []
	for column, allowed in constraints:
		if not allowed:
			return "1=0"
		values = ", ".join(frappe.db.escape(v) for v in sorted(allowed))
		parts.append(f"{table}.`{column}` in ({values})")
	return " and ".join(parts)


def doc_allowed(doc, user=None) -> bool:
	user = _user(user)
	if doc.doctype == "Ads Access Profile":
		return _profile_condition(user) == "" or doc.get("user") == user
	scope = get_scope(user)
	if scope is None:
		return False
	if doc.doctype in FINANCE_DOCTYPES and not has_capability("view_finance", user):
		return False
	if not scope:
		return True
	constraints = _constraints(doc.doctype, scope, user)
	if constraints is None:
		return False
	derived = None
	for column, allowed in constraints:
		value = doc.get(column)
		if not value and column != "name":
			# A new record is checked before fetch_from fills its scope from its account / workspace.
			if derived is None:
				derived = _derived_scope(doc)
			value = derived.get(column)
		if value not in allowed:
			return False
	return True


def assert_in_scope(doc, user=None) -> None:
	"""For controllers that set scope fields from a link in validate(): Frappe checks create / write
	permission before that (and may pre-fill company from the user's own default), so check again
	once the record's real company / workspace / brand are known."""
	if doc.flags.ignore_permissions:
		return
	if not doc_allowed(doc, user):
		frappe.throw(_("That record is outside your ads scope."), frappe.PermissionError)


def _derived_scope(doc) -> dict:
	"""company / workspace / brand that fetch_from would set from the record's account or workspace."""
	out = {}
	if doc.get("account") and doc.doctype != "Ads Account":
		out = frappe.db.get_value("Ads Account", doc.get("account"), ["company", "workspace", "brand"], as_dict=True) or {}
	elif doc.get("workspace") and doc.doctype != "Ads Workspace":
		out = {"company": frappe.db.get_value("Ads Workspace", doc.get("workspace"), "company")}
	return {k: v for k, v in out.items() if v}


def scope_filters(doctype: str, user=None) -> dict:
	"""The same scope as frappe.get_all filters, for API code that reads with ignore_permissions."""
	user = _user(user)
	scope = get_scope(user)
	if scope is None:
		return {"name": ["in", []]}
	if doctype in FINANCE_DOCTYPES and not has_capability("view_finance", user):
		return {"name": ["in", []]}
	if not scope:
		return {}
	constraints = _constraints(doctype, scope, user)
	if constraints is None:
		return {"name": ["in", []]}
	out = {}
	for column, allowed in constraints:
		prior = set(out[column][1]) if column in out else None
		vals = allowed if prior is None else (prior & allowed)
		out[column] = ["in", sorted(vals) or [""]]
	return out


# ─── hooks ────────────────────────────────────────────────────────────────────

def get_permission_query_conditions(user=None, doctype=None):
	# Frappe passes doctype from v15 onwards for hooks registered per doctype.
	if not doctype:
		return ""
	return query_conditions(doctype, user)


def has_permission(doc, ptype=None, user=None, debug=False):
	# Controller hooks can only deny (v15 and v16). True = "no objection"; role permissions still
	# decide read vs write. Never return None: v16 treats any falsy result as a denial.
	return doc_allowed(doc, user)


# ─── capabilities ─────────────────────────────────────────────────────────────

def has_capability(capability: str, user=None) -> bool:
	if capability not in CAPABILITIES:
		raise ValueError(capability)
	user = _user(user)
	if user == "Administrator" or is_super(user):
		return True
	if capability == "view_finance" and FINANCE_ROLES & _roles(user):
		return True
	if capability in ADMIN_IMPLICIT and is_admin(user):
		return True
	profile = frappe.db.get_value("Ads Access Profile", {"user": user, "enabled": 1}, list(CAPABILITIES), as_dict=True)
	return bool(profile and profile.get(capability))


def require(capability: str, user=None) -> None:
	if not has_capability(capability, user):
		frappe.throw(_("You do not have the '{0}' right for ads. Ask an admin to set it on your Ads Access Profile.").format(capability.replace("_", " ")), frappe.PermissionError)


def require_access() -> None:
	"""Any ads screen or API: an admin, or an Excom Marketing user with some scope. A finance-only
	user (Accounts User) works on finance records in the Desk, not through these endpoints."""
	user = _user()
	if not (is_admin(user) or MARKETING_ROLE in _roles(user)) or get_scope() is None:
		frappe.throw(_("You have no access to ads data. Ask an admin for User Permissions on a company, workspace, brand or ad account."), frappe.PermissionError)
