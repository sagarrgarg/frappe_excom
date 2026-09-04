"""A permission drill against the whitelisted API surface, as every kind of user.

The visibility drill asks the ORM what a user can see. This one asks the endpoints, because that is
what a browser actually calls, and an endpoint that forgets its guard is not visible from a list
query at all.

It discovers the endpoints rather than listing them, so an endpoint added next week is drilled
without anybody remembering to add it here. For each one it fills the signature with harmless
arguments and calls it as each persona, then reads the outcome:

  PermissionError            -> the guard refused. Good, if that persona should be refused.
  anything else, or success  -> the guard let this persona through.

Endpoints that change something are only ever called as a persona who *should be refused*: if the
call is refused, nothing happened. Nothing here sends a message.
"""

import ast
import inspect
import os

import frappe

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_DIR = os.path.join(APP, "api")
PW = "excom-demo-2026"

# Who should get through the door of each module.
MANAGER_MODULES = {"admin", "meta", "subscriber_rules", "mobile", "identity_sync",
                   "broadcast", "merge_suggestions"}
GUEST_MODULES = {"webchat", "intake", "flow_endpoint", "unsubscribe"}
AGENT_MODULES = {"chat", "record", "crm", "email", "subscribers", "teams", "notification", "analytics"}

# Reading is safe to call as anybody. Everything else is only called where we expect a refusal.
READ_PREFIXES = ("get_", "list_", "search_", "preview_", "diagnose_", "check_", "fetch_config", "app_urls")

PERSONAS = {
	"smm":        (["Sales Master Manager"], []),
	"exmgr":      (["Excom Manager"], [("API Drill Desk", "Manager")]),
	"head":       (["Excom User"], [("API Drill Group", "Manager")]),
	"agent_in":   (["Excom User"], [("API Drill Desk", "Member")]),
	"agent_out":  (["Excom User"], [("API Drill Other", "Member")]),
	"agent_solo": (["Excom User"], []),
	"norole":     ([], []),
}
RESULTS = []


def _r(kind, name, detail=""):
	RESULTS.append((kind, name, detail))
	if kind not in ("PASS", "SKIP"):
		print(f"[{kind:^6}] {name}" + (f"  |  {detail}" if detail else ""))


def _endpoints():
	"""Every whitelisted function in the API layer, with its module and whether it only reads."""
	for fname in sorted(os.listdir(API_DIR)):
		if not fname.endswith(".py") or fname == "__init__.py":
			continue
		mod = fname[:-3]
		src = open(os.path.join(API_DIR, fname), errors="ignore").read()
		try:
			tree = ast.parse(src)
		except SyntaxError:
			continue
		for node in tree.body:
			if not isinstance(node, ast.FunctionDef):
				continue
			decorators = ast.dump(ast.Module(body=node.decorator_list, type_ignores=[]))
			if "whitelist" not in decorators:
				continue
			guest_ok = "allow_guest" in decorators
			reads = node.name.startswith(READ_PREFIXES)
			yield mod, node.name, reads, guest_ok


# The fixture conversation belongs to API Drill Desk. Who may touch it:
#   exmgr    — Excom Manager, sees every conversation
#   head     — manages API Drill Group, and the Desk hangs off it
#   agent_in — a member of the Desk
# and nobody else, however senior in sales they are.
THREAD_ALLOWED = {"exmgr", "head", "agent_in"}

# Endpoints that take a contact or a thread but are open to any agent on purpose, each with the
# reason. Anything not listed here and record-shaped is expected to refuse an outsider.
OPEN_TO_ANY_AGENT = {
	"chat.initiate_outbound": "starting the first conversation with a contact nobody owns yet",
	"notification.are_push_notifications_enabled": "a site-level yes/no, no contact in it",
	"notification.get_frappe_relay_push_config": "the browser's own push config: web keys and a VAPID public key, no secret",
	"subscribers.add_subscriber_by_contact": "looks a contact up by phone or email, then guards",
}

# A handful of endpoints in an otherwise agent-level module are site configuration.
CONFIG_ENDPOINTS = {"notification.register_site_on_excom_cloud"}


def _expected(mod, guest_ok, thread_scoped=False):
	"""Which personas should get through. Two different doors, so two answers."""
	if guest_ok or mod in GUEST_MODULES:
		return set(PERSONAS)                      # open by design; other checks gate the payload
	if mod in MANAGER_MODULES:
		return {"exmgr"}                          # a Sales Master Manager is not an Excom admin
	if mod == "notification":
		return set(PERSONAS)                      # push registration is per-user, not per-desk
	if mod in AGENT_MODULES:
		if thread_scoped:
			return THREAD_ALLOWED                 # the record decides, not the role
		return {"exmgr", "head", "agent_in", "agent_out", "agent_solo"}
	return {"exmgr"}


# Real fixtures, so a refusal means "not you" rather than "no such record". Passing an empty id
# makes every thread-scoped endpoint refuse everybody, which looks like a wall of failures and
# proves nothing at all.
FIXTURES = {}


def _fill(pname, annotation):
	name = pname.lower()
	if name in ("thread_id", "thread"):
		return FIXTURES.get("thread", "")
	if name == "reference_doctype":
		return "Excom Thread"
	if name == "doctype":
		return "Excom Source"   # a doctype the admin editor actually manages
	if name in ("reference_name",):
		return FIXTURES.get("thread", "")
	if name in ("omni_identity", "identity"):
		return FIXTURES.get("identity", "")
	if name == "team":
		return FIXTURES.get("team", "")
	if name == "user":
		return frappe.session.user
	if "int" in str(annotation):
		return 0
	if "list" in str(annotation):
		return []
	return ""


RECORD_PARAMS = ("thread_id", "thread", "omni_identity", "reference_name")
ACCOUNT_PARAMS = ("account_name",)


def _thread_scoped(target):
	"""Does this endpoint act on one record? Then the record decides, not the role."""
	return any(p in inspect.signature(target).parameters for p in RECORD_PARAMS)


def _account_scoped(target):
	"""Endpoints that need a mailbox cannot be judged with a made-up mailbox name."""
	return any(p in inspect.signature(target).parameters for p in ACCOUNT_PARAMS)


def _call(mod, fn):
	"""Call an endpoint with real arguments filled from its own signature."""
	target = frappe.get_attr(f"excom.excom.api.{mod}.{fn}")
	sig = inspect.signature(target)
	kwargs = {}
	for pname, p in sig.parameters.items():
		if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
			continue
		# Optional parameters are filled too when they name a record. get_activity() takes only
		# optional arguments, so calling it bare returned an empty feed and looked like a leak.
		if p.default is not inspect.Parameter.empty and pname.lower() not in RECORD_PARAMS + ("reference_doctype",):
			continue
		kwargs[pname] = _fill(pname, p.annotation)
	return target(**kwargs)


def build():
	frappe.set_user("Administrator")
	teardown(quiet=True)
	for name, parent in (("API Drill Group", None), ("API Drill Desk", "API Drill Group"), ("API Drill Other", "API Drill Group")):
		frappe.get_doc({"doctype": "Excom Team", "team_name": name, "parent_team": parent}).insert(ignore_permissions=True)
	for persona, (roles, teams) in PERSONAS.items():
		email = f"api.{persona}@example.com"
		u = frappe.get_doc({"doctype": "User", "email": email, "first_name": f"API {persona}",
			"send_welcome_email": 0, "new_password": PW, "user_type": "System User"})
		u.flags.ignore_permissions = True
		u.insert(ignore_permissions=True)
		if roles:
			u.add_roles(*roles)
		for team, role in teams:
			doc = frappe.get_doc("Excom Team", team)
			doc.append("members", {"user": email, "role": role})
			doc.flags.ignore_permissions = True
			doc.save()
	frappe.db.commit()
	print(f"{len(PERSONAS)} personas, 3 teams (Group > Desk, Other)")


def teardown(quiet=False):
	frappe.set_user("Administrator")
	for u in frappe.get_all("User", {"name": ["like", "api.%@example.com"]}, pluck="name"):
		frappe.db.delete("ToDo", {"allocated_to": u})
		try:
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		except Exception:
			frappe.db.rollback()
	for t in ("API Drill Desk", "API Drill Other", "API Drill Group"):
		if frappe.db.exists("Excom Team", t):
			try:
				frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
			except Exception as e:
				if not quiet:
					print(f"kept {t}: {str(e)[:70]}")
	frappe.db.commit()
	if not quiet:
		print("api drill data removed")


def run(verbose: int = 0):
	frappe.set_user("Administrator")
	RESULTS.clear()
	_make_fixtures()
	endpoints = list(_endpoints())
	skipped_writes = 0
	print(f"drilling {len(endpoints)} whitelisted endpoints x {len(PERSONAS)} personas\n")

	for mod, fn, reads, guest_ok in endpoints:
		try:
			target = frappe.get_attr(f"excom.excom.api.{mod}.{fn}")
		except Exception:
			continue
		if _account_scoped(target):
			_r("SKIP", f"{mod}.{fn}", "needs a real mailbox; a dummy one is refused for everybody")
			continue
		key = f"{mod}.{fn}"
		if key in CONFIG_ENDPOINTS:
			expected = {"exmgr"}
		elif key in OPEN_TO_ANY_AGENT:
			# "any agent" means anybody holding an Excom role, not literally every account
			expected = {"exmgr", "head", "agent_in", "agent_out", "agent_solo"}
		else:
			expected = _expected(mod, guest_ok, _thread_scoped(target))
		for persona in PERSONAS:
			email = f"api.{persona}@example.com"
			should_pass = persona in expected
			if should_pass and not reads:
				skipped_writes += 1
				continue  # never call a write as somebody allowed to do it
			frappe.set_user(email)
			try:
				_call(mod, fn)
				got_through = True
				why = "returned"
			except frappe.PermissionError as e:
				got_through = False
				why = str(e)[:60]
			except Exception as e:
				# the guard let us in and the body then complained about our dummy arguments
				got_through = True
				why = type(e).__name__
			finally:
				frappe.set_user("Administrator")
				frappe.db.rollback()
			label = f"{mod}.{fn} as {persona}"
			if got_through and not should_pass:
				_r("LEAK", label, why)
			elif not got_through and should_pass:
				_r("BLIND", label, why)
			else:
				_r("PASS", label)
				if verbose:
					print(f"[ ok   ] {label}")

	leaks = [r for r in RESULTS if r[0] == "LEAK"]
	blind = [r for r in RESULTS if r[0] == "BLIND"]
	skips = [r for r in RESULTS if r[0] == "SKIP"]
	print(f"\n==== {len(RESULTS) - len(skips)} calls | {len(leaks)} LEAK | {len(blind)} BLIND"
	      f" | {skipped_writes} writes not called | {len(skips)} endpoints not judgeable ====")
	by_mod = {}
	for kind, name, detail in leaks + blind:
		by_mod.setdefault((kind, name.split(".")[0]), []).append(name.split(" as ")[-1])
	for (kind, mod), who in sorted(by_mod.items()):
		print(f"  {kind:<6} {mod:<18} {len(who)} calls: {', '.join(sorted(set(who)))}")
	return {"calls": len(RESULTS), "leaks": len(leaks), "blind": len(blind)}


def _make_fixtures():
	"""One conversation and one contact, owned by API Drill Desk."""
	from frappe.utils import now_datetime

	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "API Drill%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
	identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "API Drill Buyer",
		"primary_phone": "+919900000971"}).insert(ignore_permissions=True).name
	thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": identity, "channel": ref.channel,
		"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": "api-drill-1",
		"status": "Open", "assigned_team": "API Drill Desk", "last_message_at": now_datetime()}).insert(ignore_permissions=True).name
	frappe.db.commit()
	FIXTURES.update({"thread": thread, "identity": identity, "team": "API Drill Desk"})
	print(f"fixture conversation {thread} on API Drill Desk")
