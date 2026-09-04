"""A hostile drill against the visibility model.

The walk in flow.py follows the happy path. This one tries to break it: users in two branches at
once, disabled owners, deleted teams, a four-level tree, races, merges, and every read path into a
record that is not the Excom list — link search, the REST client API, comments, assignments, reports.

Two kinds of failure are reported:
  LEAK  — somebody saw a record they must not see. Security.
  BLIND — somebody could not see a record they must. Usability, and it is how work gets dropped.
"""

import time

import frappe
from frappe.utils import now_datetime

from excom.excom.services import crm_visibility as vis

PW = "excom-demo-2026"
RESULTS = []


def _r(kind, name, detail=""):
	RESULTS.append((kind, name, detail))
	mark = {"PASS": "  ok  ", "LEAK": " LEAK ", "BLIND": "BLIND ", "INFO": " info "}[kind]
	print(f"[{mark}] {name}" + (f"  |  {detail}" if detail else ""))


def _user(email, roles, teams=()):
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
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
	return email


def _team(name, parent=None):
	if frappe.db.exists("Excom Team", name):
		return name
	frappe.get_doc({"doctype": "Excom Team", "team_name": name, "parent_team": parent,
		"description": "drill"}).insert(ignore_permissions=True)
	return name


def _lead(title, rotate=False, **kw):
	# The intake rotation claims every lead with no customer type, this drill's fixtures included.
	# Only the volume drill wants that, so everything else arrives already classified.
	if not rotate:
		kw.setdefault("customer_type", "Distributor")
	d = frappe.get_doc({"doctype": "Lead", "lead_name": title, "first_name": title, **kw})
	d.flags.ignore_permissions = True
	d.insert(ignore_permissions=True)
	frappe.db.set_value("Lead", d.name, "lead_owner", kw.get("lead_owner"), update_modified=False)
	if kw.get("excom_team"):
		frappe.db.set_value("Lead", d.name, "excom_team", kw["excom_team"], update_modified=False)
	return d.name


def _as(user, fn, *a, **k):
	frappe.set_user(user)
	try:
		return fn(*a, **k)
	finally:
		frappe.set_user("Administrator")


def _list_sees(user, lead):
	def go():
		try:
			return bool(frappe.get_list("Lead", filters={"name": lead}, fields=["name"], limit_page_length=0))
		except Exception:
			return False
	return _as(user, go)


# ── every other way into a record ────────────────────────────────────────────
def _read_paths(user, lead, should_see):
	"""The Excom list is not the only way to read a lead. Each of these is a separate door."""
	label = "may see" if should_see else "must not see"
	doors = {}

	doors["get_list"] = _list_sees(user, lead)

	def _get_doc():
		try:
			frappe.get_doc("Lead", lead).check_permission("read")
			return True
		except Exception:
			return False
	doors["form view"] = _as(user, _get_doc)

	def _client_get():
		try:
			import frappe.client
			return bool(frappe.client.get(doctype="Lead", name=lead))
		except Exception:
			return False
	doors["client.get"] = _as(user, _client_get)

	def _client_get_value():
		try:
			import frappe.client
			return bool(frappe.client.get_value(doctype="Lead", fieldname="lead_name", filters={"name": lead}))
		except Exception:
			return False
	doors["client.get_value"] = _as(user, _client_get_value)

	def _link_search():
		try:
			from frappe.desk.search import search_widget
			frappe.response.clear()
			search_widget(doctype="Lead", txt=frappe.db.get_value("Lead", lead, "lead_name") or "", page_length=20)
			rows = frappe.response.get("values") or frappe.response.get("results") or []
			return any(lead in str(r) for r in rows)
		except Exception:
			return False
	doors["link search"] = _as(user, _link_search)

	def _reportview():
		try:
			from frappe.desk.reportview import get
			frappe.local.form_dict = frappe._dict({"doctype": "Lead", "fields": '["`tabLead`.`name`"]',
				"filters": f'[["Lead","name","=","{lead}"]]', "limit_page_length": 0})
			res = get()  # reads form_dict; it takes no arguments
			values = res.get("values") if isinstance(res, dict) else res
			return bool(values)
		except Exception:
			return False
	doors["report view"] = _as(user, _reportview)

	def _via_comment():
		try:
			rows = frappe.get_list("Comment", filters={"reference_doctype": "Lead", "reference_name": lead},
				fields=["content"], limit_page_length=0)
			return bool(rows)
		except Exception:
			return False
	doors["comments on it"] = _as(user, _via_comment)

	for door, saw in doors.items():
		if saw and not should_see:
			_r("LEAK", f"{door}: {user.split('@')[0]} {label}", lead)
		elif not saw and should_see and door not in ("comments on it", "link search"):
			_r("BLIND", f"{door}: {user.split('@')[0]} {label}", lead)
	ok = [d for d, saw in doors.items() if saw == should_see]
	if len(ok) == len(doors):
		_r("PASS", f"all {len(doors)} read paths agree for {user.split('@')[0]} ({label})")
	return doors


def build():
	"""A four-level tree with awkward people in it."""
	teardown(quiet=True)
	_team("Drill Group")
	_team("Drill West", "Drill Group")
	_team("Drill East", "Drill Group")
	_team("Drill West A", "Drill West")
	_team("Drill West B", "Drill West")
	_team("Drill Deep", "Drill West A")          # four levels down
	_team("Drill Lonely")                         # a team with no members at all

	people = {
		"drill.boss@example.com": ([], [("Drill Group", "Manager")]),           # runs everything, Excom-blind
		"drill.west@example.com": (["Excom User"], [("Drill West", "Manager")]),
		"drill.east@example.com": (["Excom User"], [("Drill East", "Manager")]),
		"drill.a@example.com": (["Excom User"], [("Drill West A", "Member")]),
		"drill.deep@example.com": (["Excom User"], [("Drill Deep", "Member")]),
		"drill.split@example.com": (["Excom User"], [("Drill West B", "Member"), ("Drill East", "Member")]),
		"drill.gone@example.com": (["Excom User"], [("Drill East", "Member")]),   # will be disabled
		"drill.norole@example.com": ([], [("Drill West A", "Member")]),           # in a team, no Excom role
		"drill.smm@example.com": (["Sales Master Manager"], []),
	}
	for email, (roles, teams) in people.items():
		_user(email, roles, teams)
	frappe.db.set_value("User", "drill.gone@example.com", "enabled", 0)
	frappe.db.commit()
	print("tree: Drill Group > {West > {West A > Deep, West B}, East}, plus Drill Lonely (empty)")
	print("people:", ", ".join(p.split("@")[0] for p in people))


def teardown(quiet=False):
	frappe.set_user("Administrator")
	for name in frappe.get_all("Lead", filters={"lead_name": ["like", "Drill %"]}, pluck="name"):
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": name})
		frappe.db.delete("Comment", {"reference_doctype": "Lead", "reference_name": name})
		for c in frappe.get_all("Dynamic Link", {"link_name": name, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c})
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", name, force=True, ignore_permissions=True, delete_permanently=True)
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "Drill %"]}, pluck="name"):
		frappe.db.delete("Excom Message", {"omni_identity": oi})
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.db.delete("Excom Thread Transfer Log", {"thread": t})
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for t in ["Drill Doomed", "Drill Deep", "Drill West A", "Drill West B", "Drill West", "Drill East", "Drill Lonely", "Drill Group"]:
		if frappe.db.exists("Excom Team", t):
			# the teams hold drill work by design; it is deleted above, so this is a clean delete
			frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
	for u in frappe.get_all("User", {"name": ["like", "drill.%@example.com"]}, pluck="name"):
		frappe.delete_doc("User", u, force=True, ignore_permissions=True)
	frappe.db.commit()
	if not quiet:
		print("drill data removed")


def run():
	frappe.set_user("Administrator")
	prior = frappe.db.get_single_value("Excom Settings", "enforce_crm_visibility")
	frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 1)
	frappe.db.commit()
	frappe.clear_cache()
	RESULTS.clear()
	import traceback

	try:
		for stage in (_drill_depth, _drill_two_branches, _drill_no_role, _drill_disabled_owner,
		              _drill_deleted_team, _drill_read_paths, _drill_race, _drill_reassign_loop,
		              _drill_volume):
			try:
				stage()
			except Exception:
				frappe.set_user("Administrator")
				_r("BLIND", f"{stage.__name__} could not finish", traceback.format_exc().strip().splitlines()[-1][:120])
	finally:
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", prior or 0)
		frappe.db.commit()
	leaks = [r for r in RESULTS if r[0] == "LEAK"]
	blind = [r for r in RESULTS if r[0] == "BLIND"]
	print(f"\n==== {len(RESULTS)} checks | {len(leaks)} LEAK | {len(blind)} BLIND ====")
	for k, n, d in leaks + blind:
		print(f"  {k}: {n} {d}")


def _drill_depth():
	print("\n--- 1. how far up the tree does sight reach ---")
	lead = _lead("Drill Deep Lead", excom_team="Drill Deep")
	frappe.db.commit()
	for user, should in [("drill.deep@example.com", True), ("drill.a@example.com", False),
	                     ("drill.west@example.com", True), ("drill.east@example.com", False),
	                     ("drill.smm@example.com", True)]:
		saw = _list_sees(user, lead)
		if saw and not should:
			_r("LEAK", f"{user.split('@')[0]} sees a lead four levels down another path")
		elif not saw and should:
			_r("BLIND", f"{user.split('@')[0]} cannot see a lead in their own branch")
		else:
			_r("PASS", f"depth: {user.split('@')[0]} {'sees' if should else 'does not see'} Drill Deep")
	# a member of West A is NOT a manager, so the team below theirs is not theirs to see
	_r("INFO", "membership does not cascade downward; only a Manager sees the teams beneath")


def _drill_two_branches():
	print("\n--- 2. a person in two branches at once ---")
	west = _lead("Drill Split West", excom_team="Drill West B")
	east = _lead("Drill Split East", excom_team="Drill East")
	frappe.db.commit()
	for lead, name in ((west, "West B"), (east, "East")):
		if _list_sees("drill.split@example.com", lead):
			_r("PASS", f"split member sees their {name} lead")
		else:
			_r("BLIND", f"split member cannot see their {name} lead")
	if _list_sees("drill.a@example.com", east):
		_r("LEAK", "West A member sees an East lead through the split member's other team")
	else:
		_r("PASS", "one person in two teams does not bridge the two teams")
	picked = vis.team_for_user("drill.split@example.com")
	_r("INFO", f"tie-break for a two-team person: {picked}",
	   "deepest team wins; both are one level deep here so it is the stable alphabetical pick")


def _drill_no_role():
	print("\n--- 3. a team member with no Excom role ---")
	lead = _lead("Drill Norole Target", excom_team="Drill West A")
	frappe.db.commit()
	if _list_sees("drill.norole@example.com", lead):
		_r("LEAK", "someone with no Excom role sees a lead through team membership alone")
	else:
		_r("PASS", "team membership without a role grants nothing")
	if _list_sees("drill.boss@example.com", lead):
		_r("INFO", "the group manager holds no Excom role and still sees leads",
		   "expected: they hold Sales roles from elsewhere, or none at all")
	else:
		_r("PASS", "a manager with no CRM role sees nothing, as their roles say")


def _drill_disabled_owner():
	print("\n--- 4. work owned by a disabled user ---")
	lead = _lead("Drill Orphan Work", lead_owner="drill.gone@example.com", excom_team="Drill East")
	frappe.db.commit()
	if _list_sees("drill.east@example.com", lead):
		_r("PASS", "a disabled owner's lead is still visible to their team manager")
	else:
		_r("BLIND", "a disabled user's work vanished from their own manager")
	def _login():
		try:
			return bool(frappe.get_list("Lead", filters={"name": lead}, fields=["name"]))
		except Exception:
			return "denied"
	got = _as("drill.gone@example.com", _login)
	_r("INFO", f"the disabled user themselves: {got}", "Frappe blocks the session, not the row")


def _drill_deleted_team():
	print("\n--- 5. the team on a record is deleted underneath it ---")
	_team("Drill Doomed", "Drill West")
	if not frappe.db.exists("Excom Team Member", {"parent": "Drill Doomed", "user": "drill.doomed@example.com"}):
		_user("drill.doomed@example.com", ["Excom User"], [("Drill Doomed", "Member")])
	lead = _lead("Drill Doomed Lead", excom_team="Drill Doomed")
	frappe.db.commit()
	try:
		frappe.delete_doc("Excom Team", "Drill Doomed", force=True, ignore_permissions=True)
		frappe.db.commit()
		deleted = True
	except Exception as e:
		deleted = False
		_r("PASS", "a desk still holding work refuses to be deleted", str(e)[:90])
	if deleted:
		still = frappe.db.get_value("Lead", lead, "excom_team")
		if still and not frappe.db.exists("Excom Team", still):
			_r("LEAK", "a lead points at a deleted team, so it is visible to nobody but the SMM",
			   f"{lead} -> {still}")
	# and a desk with a team under it must not vanish either
	frappe.db.set_value("Lead", lead, "excom_team", "Drill West", update_modified=False)
	frappe.db.commit()
	try:
		frappe.delete_doc("Excom Team", "Drill West", force=True, ignore_permissions=True)
		frappe.db.commit()
		_r("LEAK", "a parent team was deleted while teams still hang off it")
	except Exception as e:
		_r("PASS", "a desk with teams under it refuses to be deleted", str(e)[:80])
	frappe.db.set_value("Lead", lead, "excom_team", "Drill East", update_modified=False)
	frappe.db.commit()


def _drill_read_paths():
	print("\n--- 6. every other door into a record ---")
	lead = _lead("Drill Doors", excom_team="Drill East")
	frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Lead",
		"reference_name": lead, "content": "Drill: private note on an East lead"}).insert(ignore_permissions=True)
	frappe.db.commit()
	_read_paths("drill.east@example.com", lead, should_see=True)
	_read_paths("drill.a@example.com", lead, should_see=False)
	_read_paths("drill.norole@example.com", lead, should_see=False)


def _drill_race():
	print("\n--- 7. two agents claim the same conversation ---")
	ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
	# Let the app make the identity, the way it does for a real contact: inserting a Lead with a
	# phone creates and links one. Handing it an identity built by hand is not what happens in life.
	phone = "+9199000" + str(int(time.time()))[-5:]
	lead = _lead("Drill Race Lead", mobile_no=phone)
	frappe.db.commit()
	oi = frappe.db.get_value("Lead", lead, "omni_identity")
	if not oi:
		_r("BLIND", "a lead with a phone number did not get an identity", lead)
		return
	thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi, "channel": ref.channel,
		"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": f"drill-race-{phone}",
		"status": "Open", "last_message_at": now_datetime()}).insert(ignore_permissions=True).name
	frappe.db.commit()
	from excom.excom.services.crm_flow import claim_lead_for_identity
	first = _as("drill.a@example.com", claim_lead_for_identity, oi, "drill.a@example.com")
	second = _as("drill.east@example.com", claim_lead_for_identity, oi, "drill.east@example.com")
	frappe.db.commit()
	owner = frappe.db.get_value("Lead", lead, "lead_owner")
	if second is None and owner == "drill.a@example.com":
		_r("PASS", "the second claimant is refused; the first keeps it", f"owner={owner}")
	else:
		_r("LEAK", "a second agent took a lead that was already claimed", f"first={first} second={second} owner={owner}")
	team = frappe.db.get_value("Lead", lead, "excom_team")
	tteam = frappe.db.get_value("Excom Thread", thread, "assigned_team")
	if team and team == tteam:
		_r("PASS", "the chat and the lead landed on the same desk", team)
	else:
		_r("BLIND", "chat and lead ended on different desks after a contested claim", f"lead={team} chat={tteam}")


def _drill_reassign_loop():
	print("\n--- 8. work bounced between desks ---")
	lead = _lead("Drill Bounced", excom_team="Drill West A")
	frappe.db.commit()
	hops = []
	for user in ("drill.east@example.com", "drill.a@example.com", "drill.east@example.com"):
		vis.stamp_team("Lead", lead, user)
		hops.append(frappe.db.get_value("Lead", lead, "excom_team"))
	frappe.db.commit()
	if hops == ["Drill East", "Drill West A", "Drill East"]:
		_r("PASS", "the desk follows the work every hop", " -> ".join(hops))
	else:
		_r("BLIND", "bouncing work between desks did not track", " -> ".join(str(h) for h in hops))
	notes = frappe.get_all("Comment", filters={"reference_doctype": "Lead", "reference_name": lead}, pluck="content")
	moves = [n for n in notes if n and "Moved from" in n]
	if len(moves) == 3:
		_r("PASS", f"every move is on the record ({len(moves)} notes)")
	else:
		_r("BLIND", f"moves not fully recorded: {len(moves)} notes for 3 hops")
	if _list_sees("drill.a@example.com", lead):
		_r("LEAK", "the desk that lost the work can still see it")
	else:
		_r("PASS", "the desk that lost the work loses sight of it")


def _drill_volume():
	print("\n--- 9. a hundred leads through the rotation ---")
	from excom.setup.crm_schema import ensure_assignment_rules
	ensure_assignment_rules({"Excom Intake — Unclassified": [
		"drill.a@example.com", "drill.east@example.com", "drill.deep@example.com"]})
	frappe.db.commit()
	frappe.clear_cache()
	start = time.time()
	made = []
	for i in range(100):
		made.append(_lead(f"Drill Volume {i:03d}", rotate=True))
	frappe.db.commit()
	took = time.time() - start
	rows = frappe.get_all("Lead", filters={"name": ["in", made]}, fields=["name", "_assign", "excom_team"])
	spread = {}
	unassigned = 0
	for r in rows:
		if not r._assign:
			unassigned += 1
			continue
		spread[r._assign] = spread.get(r._assign, 0) + 1
	_r("INFO", f"100 leads in {took:.1f}s ({took*10:.0f}ms each)", str(spread))
	if unassigned:
		_r("BLIND", f"{unassigned} of 100 leads came out of the rotation unassigned")
	else:
		_r("PASS", "every lead in the batch was assigned")
	counts = sorted(spread.values())
	if counts and counts[-1] - counts[0] <= 2:
		_r("PASS", f"round robin is even ({counts})")
	else:
		_r("BLIND", f"round robin is lopsided ({counts})")
	teamed = [r for r in rows if r.excom_team]
	if len(teamed) == len(rows):
		_r("PASS", "every assigned lead carries a desk")
	else:
		_r("BLIND", f"{len(rows) - len(teamed)} of {len(rows)} leads have no desk after assignment")
