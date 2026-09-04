"""Walk the whole sales flow on the demo org, printing who can see what at each step."""

import frappe
from frappe.utils import now_datetime

WATCHERS = ["demo.smm@example.com", "demo.head@example.com", "demo.north.mgr@example.com",
	"demo.delhi.a@example.com", "demo.delhi.b@example.com", "demo.agra.a@example.com",
	"demo.export.mgr@example.com", "demo.export.a@example.com"]
SHORT = {e: e.split("@")[0].replace("demo.", "") for e in WATCHERS}


def _see(lead):
	out = []
	for u in WATCHERS:
		frappe.set_user(u)
		try:
			ok = bool(frappe.get_list("Lead", filters={"name": lead}, fields=["name"], limit_page_length=0))
		except Exception:
			ok = False
		frappe.set_user("Administrator")
		if ok:
			out.append(SHORT[u])
	return ", ".join(out) or "(nobody but Administrator)"


def _lead(title, owner=None, **kw):
	d = frappe.get_doc({"doctype": "Lead", "lead_name": title, "first_name": title, "company_name": title, **kw})
	d.flags.ignore_permissions = True
	d.insert(ignore_permissions=True)
	if not owner:
		# ERPNext stamps lead_owner with whoever inserted the row; a web-form lead has nobody.
		# excom_team is deliberately left alone: an assignment rule may already have set it.
		frappe.db.set_value("Lead", d.name, "lead_owner", None, update_modified=False)
	else:
		frappe.db.set_value("Lead", d.name, "lead_owner", owner, update_modified=False)
	frappe.db.commit()
	return d.name


def _state(lead):
	r = frappe.db.get_value("Lead", lead, ["lead_owner", "excom_team", "_assign", "status"], as_dict=True)
	return f"owner={r.lead_owner or '-'} team={r.excom_team or '-'} assign={r._assign or '-'}"


def stage1():
	prior = frappe.db.get_single_value("Excom Settings", "enforce_crm_visibility")
	frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 1)
	frappe.db.commit()
	frappe.clear_cache()
	print(f"enforcement was {prior}, now ON for this walk")

	print("\n=== assignment rules that exist ===")
	for r in frappe.get_all("Assignment Rule", fields=["name", "document_type", "disabled", "rule"], order_by="name"):
		users = frappe.get_all("Assignment Rule User", filters={"parent": r.name}, pluck="user")
		days = frappe.get_all("Assignment Rule Day", filters={"parent": r.name}, pluck="day")
		flag = "" if users and days and not r.disabled else "   <-- CANNOT FIRE"
		print(f"  {r.name:<32} {r.document_type:<12} disabled={r.disabled} {r.rule:<14} users={len(users)} days={len(days)}{flag}")

	print("\n=== 1. a lead arrives from the website, nobody assigned ===")
	web = _lead("Demo Web Enquiry", email_id="demo.web@example.com", mobile_no="+919900000901")
	print(f"  {web}  {_state(web)}")
	print(f"  visible to: {_see(web)}")

	print("\n=== 2. the Sales Master Manager hands it to the Delhi desk ===")
	frappe.set_user("demo.smm@example.com")
	try:
		from frappe.desk.form.assign_to import add
		add({"assign_to": ["demo.delhi.a@example.com"], "doctype": "Lead", "name": web, "description": "Delhi, please take this"})
		frappe.db.commit()
		print("  assigned by SMM: ok")
	except Exception as e:
		print("  assignment by SMM FAILED:", type(e).__name__, str(e)[:120])
	frappe.set_user("Administrator")
	print(f"  {web}  {_state(web)}")
	print(f"  visible to: {_see(web)}")

	print("\n=== 3. a second lead, handed straight to the Export desk ===")
	exp = _lead("Demo Export Enquiry", email_id="demo.export.buyer@example.com", mobile_no="+919900000902")
	from frappe.desk.form.assign_to import add
	add({"assign_to": ["demo.export.a@example.com"], "doctype": "Lead", "name": exp, "description": "export"}, ignore_permissions=True)
	frappe.db.commit()
	print(f"  {exp}  {_state(exp)}")
	print(f"  visible to: {_see(exp)}")

	print("\n=== 4. a lead nobody ever touches ===")
	orphan = _lead("Demo Untouched Enquiry", email_id="demo.orphan@example.com", mobile_no="+919900000903")
	print(f"  {orphan}  {_state(orphan)}")
	print(f"  visible to: {_see(orphan)}")
	frappe.cache().set_value("demo_leads", {"web": web, "exp": exp, "orphan": orphan})


def stage2():
	"""Auto-assignment: the rules the app ships a seeder for, but which nobody has ever run."""
	from excom.setup.crm_schema import ensure_assignment_rules

	result = ensure_assignment_rules({
		"Excom Intake — Unclassified": ["demo.delhi.a@example.com", "demo.delhi.b@example.com", "demo.agra.a@example.com"],
		"Excom Export Desk": ["demo.export.a@example.com"],
	})
	frappe.db.commit()
	frappe.clear_cache()
	print("rules:", result)
	for name in result["created"] + result["repaired"]:
		r = frappe.get_doc("Assignment Rule", name)
		print(f"  {name}: {r.document_type} | {r.rule} | condition: {r.assign_condition} | users: {[u.user for u in r.users]}")

	# each run starts from a clean slate for its own leads
	for old in frappe.get_all("Lead", filters={"lead_name": ["like", "Demo Auto Lead%"]}, pluck="name"):
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": old})
		for c in frappe.get_all("Dynamic Link", {"link_name": old, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c})
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", old, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()

	print("\n=== three unclassified leads arrive with nobody watching ===")
	for i in range(3):
		name = _lead(f"Demo Auto Lead {i + 1}", email_id=f"demo.auto{i + 1}@example.com", mobile_no=f"+91990000091{i}")
		frappe.db.commit()
		row = frappe.db.get_value("Lead", name, ["_assign", "excom_team", "lead_owner"], as_dict=True)
		print(f"  {name}  assign={row._assign or '-'}  team={row.excom_team or '-'}  owner={row.lead_owner or '-'}")
		print(f"    visible to: {_see(name)}")


def _see_thread(thread):
	from excom.excom.doctype.excom_thread.excom_thread import can_access
	out = []
	for u in WATCHERS:
		if can_access(thread, u):
			out.append(SHORT[u])
	return ", ".join(out) or "(nobody but Administrator)"


def stage3():
	"""The conversation side: an unclaimed chat, someone answers it, and what follows."""
	ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "Demo Buyer%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	frappe.db.commit()

	identity = frappe.get_doc({"doctype": "Omni Identity", "display_name": "Demo Buyer Anil",
		"primary_phone": "+919900000921"}).insert(ignore_permissions=True).name
	thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": identity, "channel": ref.channel,
		"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": "demo-flow-1",
		"status": "Open", "last_message_at": now_datetime()}).insert(ignore_permissions=True).name
	frappe.db.commit()
	print("=== 5. a stranger messages in; nobody owns the chat ===")
	print(f"  thread {thread}  assigned_to=- assigned_team=-")
	print(f"  chat visible to: {_see_thread(thread)}")

	lead = _lead("Demo Buyer Anil", email_id="demo.anil@example.com", mobile_no="+919900000921")
	frappe.db.set_value("Lead", lead, {"omni_identity": identity, "excom_team": None, "_assign": None}, update_modified=False)
	frappe.db.delete("ToDo", {"reference_name": lead})
	frappe.db.commit()
	print(f"  their lead {lead}: {_state(lead)}")
	print(f"  lead visible to: {_see(lead)}")

	print("\n=== 6. an Export desk agent answers the chat (claim on talk) ===")
	from excom.excom.services.crm_flow import claim_lead_for_identity
	frappe.set_user("demo.export.a@example.com")
	claimed = claim_lead_for_identity(identity, "demo.export.a@example.com")
	frappe.set_user("Administrator")
	frappe.db.set_value("Excom Thread", thread, "assigned_to", "demo.export.a@example.com", update_modified=False)
	frappe.db.commit()
	print(f"  claimed lead: {claimed}")
	print(f"  lead now: {_state(lead)}")
	print(f"  lead visible to: {_see(lead)}")
	print(f"  chat visible to: {_see_thread(thread)}")
	frappe.cache().set_value("demo_thread", thread)
	frappe.cache().set_value("demo_lead", lead)


def stage4():
	thread = frappe.cache().get_value("demo_thread")
	lead = frappe.cache().get_value("demo_lead")
	print("=== 7. the chat is transferred to the Delhi desk ===")
	frappe.set_user("demo.export.a@example.com")
	try:
		from excom.excom.api.chat import transfer_thread
		out = transfer_thread(thread_id=thread, target_team="Demo Delhi Desk", note="wrong desk, sending to Delhi")
		print("  transfer ok:", out)
	except Exception as e:
		print("  transfer FAILED:", type(e).__name__, str(e)[:160])
	frappe.set_user("Administrator")
	row = frappe.db.get_value("Excom Thread", thread, ["assigned_to", "assigned_team"], as_dict=True)
	print(f"  thread now: assigned_to={row.assigned_to or '-'} team={row.assigned_team or '-'}")
	print(f"  chat visible to: {_see_thread(thread)}")
	print(f"  their lead still: {_state(lead)}")
	print(f"  lead visible to: {_see(lead)}")

	print("\n=== 8. the lead is qualified into an Opportunity ===")
	from excom.excom.services import crm_gateway as gw
	try:
		from excom.excom.api.crm import convert

		frappe.db.set_value("Lead", lead, "customer_type", "Export Importer", update_modified=False)
		frappe.db.commit()
		frappe.set_user("demo.export.a@example.com")
		opp = convert(doctype="Lead", name=lead, target="Opportunity")
		frappe.set_user("Administrator")
		print("  opportunity:", opp)
		name = opp if isinstance(opp, str) else ((opp or {}).get("ref") or {}).get("name")
		if name:
			r = frappe.db.get_value("Opportunity", name, ["opportunity_owner", "excom_team", "_assign"], as_dict=True)
			print(f"  opportunity state: owner={r.opportunity_owner or '-'} team={r.excom_team or '-'} assign={r._assign or '-'}")
			seen = []
			for u in WATCHERS:
				frappe.set_user(u)
				try:
					ok = bool(frappe.get_list("Opportunity", filters={"name": name}, fields=["name"], limit_page_length=0))
				except Exception:
					ok = False
				frappe.set_user("Administrator")
				if ok:
					seen.append(SHORT[u])
			print("  opportunity visible to:", ", ".join(seen) or "(nobody)")
	except Exception as e:
		print("  conversion FAILED:", type(e).__name__, str(e)[:200])


def stage5():
	"""Where the Opportunity landed, and whether the conversation followed it."""
	opp = frappe.get_all("Opportunity", filters={"party_name": ["like", "%"], "customer_type": "Export Importer"},
		fields=["name", "opportunity_owner", "excom_team", "_assign", "omni_identity"], order_by="creation desc", limit=1)
	if not opp:
		print("no demo opportunity found")
		return
	o = opp[0]
	print(f"opportunity {o.name}: owner={o.opportunity_owner or '-'} team={o.excom_team or '-'} assign={o._assign or '-'}")
	seen = []
	for u in WATCHERS:
		frappe.set_user(u)
		try:
			ok = bool(frappe.get_list("Opportunity", filters={"name": o.name}, fields=["name"], limit_page_length=0))
		except Exception:
			ok = False
		frappe.set_user("Administrator")
		if ok:
			seen.append(SHORT[u])
	print("  visible to:", ", ".join(seen) or "(nobody but Administrator)")
	if o.omni_identity:
		for t in frappe.get_all("Excom Thread", filters={"omni_identity": o.omni_identity}, fields=["name", "assigned_to", "assigned_team"]):
			print(f"  their chat {t.name}: to={t.assigned_to or '-'} team={t.assigned_team or '-'} | visible to: {_see_thread(t.name)}")


def stage6():
	"""The shared inbox: who sees a chat nobody has claimed."""
	thread = frappe.get_all("Excom Thread", filters={"assigned_to": ["is", "not set"], "assigned_team": ["is", "not set"], "status": ["!=", "Closed"]}, pluck="name", limit=1)
	if not thread:
		ref = frappe.get_all("Excom Thread", fields=["channel", "account_doctype", "account"], limit=1)[0]
		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": "Demo Walk-in", "primary_phone": "+919900000931"}).insert(ignore_permissions=True).name
		thread = [frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi, "channel": ref.channel,
			"account_doctype": ref.account_doctype, "account": ref.account, "thread_key": "demo-shared-1",
			"status": "Open", "last_message_at": now_datetime()}).insert(ignore_permissions=True).name]
		frappe.db.commit()
	t = thread[0]
	print(f"unclaimed chat {t}")
	print(f"  shared inbox = 'General' (the default, which has no members here)")
	print(f"    visible to: {_see_thread(t)}")

	if not frappe.db.exists("Excom Team", "Demo Front Desk"):
		doc = frappe.get_doc({"doctype": "Excom Team", "team_name": "Demo Front Desk", "parent_team": "Demo Sales",
			"description": "Everyone who triages what nobody has claimed."})
		for u in ("demo.delhi.a@example.com", "demo.agra.a@example.com", "demo.export.a@example.com"):
			doc.append("members", {"user": u, "role": "Member"})
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	frappe.db.set_single_value("Excom Settings", "shared_inbox_team", "Demo Front Desk")
	frappe.db.commit()
	frappe.clear_cache()
	print("  shared inbox = 'Demo Front Desk'")
	print(f"    visible to: {_see_thread(t)}")
	frappe.db.set_single_value("Excom Settings", "shared_inbox_team", "General")
	frappe.db.commit()
