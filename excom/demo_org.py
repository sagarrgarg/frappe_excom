"""Build a demo sales organisation to walk the whole flow end to end.

Everything created here is prefixed "Demo " or uses an @example.com address, so teardown() removes
it all and nothing touches the real contacts on this site. No message is ever sent.
"""

import frappe
from frappe.utils import now_datetime

PW = "excom-demo-2026"

# email -> (full name, excom roles, sales roles)
PEOPLE = {
	"demo.smm@example.com":       ("Demo Sales Master", [],                ["Sales Master Manager", "Sales User"]),
	"demo.head@example.com":      ("Demo Sales Head",   ["Excom Manager"], ["Sales User"]),
	"demo.north.mgr@example.com": ("Demo North Head",   ["Excom User"],    ["Sales User"]),
	"demo.delhi.a@example.com":   ("Demo Delhi Agent A",["Excom User"],    ["Sales User"]),
	"demo.delhi.b@example.com":   ("Demo Delhi Agent B",["Excom User"],    ["Sales User"]),
	"demo.agra.a@example.com":    ("Demo Agra Agent",   ["Excom User"],    ["Sales User"]),
	"demo.export.mgr@example.com":("Demo Export Head",  ["Excom User"],    ["Sales User"]),
	"demo.export.a@example.com":  ("Demo Export Agent", ["Excom User"],    ["Sales User"]),
}

# team -> (parent, [(user, role)])
TEAMS = {
	"Demo Sales": (None, [("demo.head@example.com", "Manager")]),
	"Demo North": ("Demo Sales", [("demo.north.mgr@example.com", "Manager")]),
	"Demo Delhi Desk": ("Demo North", [("demo.delhi.a@example.com", "Member"), ("demo.delhi.b@example.com", "Member")]),
	"Demo Agra Desk": ("Demo North", [("demo.agra.a@example.com", "Member")]),
	"Demo Export Desk": ("Demo Sales", [("demo.export.mgr@example.com", "Manager"), ("demo.export.a@example.com", "Member")]),
}


def build():
	frappe.set_user("Administrator")
	teardown(quiet=True)
	for email, (name, ex_roles, sales_roles) in PEOPLE.items():
		u = frappe.get_doc({
			"doctype": "User", "email": email, "first_name": name, "send_welcome_email": 0,
			"new_password": PW, "user_type": "System User",
		})
		u.flags.ignore_permissions = True
		u.insert(ignore_permissions=True)
		u.add_roles(*(ex_roles + sales_roles))
	frappe.db.commit()

	for team, (parent, members) in TEAMS.items():
		doc = frappe.get_doc({"doctype": "Excom Team", "team_name": team, "parent_team": parent,
		                      "description": "Demo org for walking the assignment flow."})
		for user, role in members:
			doc.append("members", {"user": user, "role": role})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	frappe.db.commit()

	tree = []
	for team, (parent, _) in TEAMS.items():
		members = frappe.get_all("Excom Team Member", filters={"parent": team}, fields=["user", "role"])
		tree.append({"team": team, "parent": parent, "members": [f"{m.user.split('@')[0]}:{m.role}" for m in members]})
	print("teams:")
	for t in tree:
		print(f"  {t['team']:<18} parent={str(t['parent']):<12} {t['members']}")
	print(f"\n{len(PEOPLE)} demo users created, password for all of them: {PW}")


def teardown(quiet=False):
	frappe.set_user("Administrator")
	for name in frappe.get_all("Lead", filters={"lead_name": ["like", "Demo %"]}, pluck="name"):
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": name})
		frappe.db.delete("Comment", {"reference_doctype": "Lead", "reference_name": name})
		frappe.db.delete("Excom Stage Change Log", {"ref_name": name})
		for c in frappe.get_all("Dynamic Link", {"link_name": name, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c})
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", name, force=True, ignore_permissions=True, delete_permanently=True)
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "Demo %"]}, pluck="name"):
		frappe.db.delete("Excom Message", {"omni_identity": oi})
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t})
			frappe.db.delete("Excom Thread Transfer Log", {"thread": t})
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(child, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for team in ["Demo Delhi Desk", "Demo Agra Desk", "Demo North", "Demo Export Desk", "Demo Sales"]:
		if frappe.db.exists("Excom Team", team):
			frappe.delete_doc("Excom Team", team, force=True, ignore_permissions=True)
	for email in PEOPLE:
		if frappe.db.exists("User", email):
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	frappe.db.commit()
	if not quiet:
		print("demo org removed")
