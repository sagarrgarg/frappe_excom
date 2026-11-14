"""Excom AI Phase 1A: the builder (HLD-007 §6, §7). Keywords → what the ERP shows → a Draft agent
with its brief, scope, playbook skeleton, knowledge pack and readiness.

Fixtures make a small export business: an item group whose items are named "squirtle", a buyer in
Mexico, and a USD price list that prices half the items. §17's real numbers are checked against a
copy of GGIL by hand; here every rule is checked on data the test controls.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai.builder import api, knowledge, readiness, resolve, service

OWNER = "qa.build.owner@example.com"
BACKUP = "qa.build.backup@example.com"
OUTSIDER = "qa.build.outsider@example.com"
TEAM = "QA Builder Desk"
OTHER_TEAM = "QA Builder Other Desk"
GROUP = "QA Squirters"
PRICE_LIST = "QA Export USD"
BUYER = "QA Comprador Mexicano"
KEYWORDS = "export, squirtles, latin america, spanish"


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Build%"]}, pluck="name")
	for dt in ("Excom AI Knowledge", "Excom AI Readiness Gap", "Excom AI Proposal", "Excom AI Task"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	frappe.db.delete("ToDo", {"reference_type": "Excom AI Readiness Gap", "allocated_to": ["in", [OWNER, BACKUP]]})
	for name in agents:
		pbs = [r.playbook for r in frappe.get_doc("Excom AI Agent", name).playbooks]
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		for pb in pbs:
			frappe.delete_doc("Excom AI Playbook", pb, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	frappe.db.delete("Excom AI Glossary", {"term": ["like", "squirtle%"]})
	frappe.db.delete("Item Price", {"price_list": PRICE_LIST})
	for item in frappe.get_all("Item", {"item_group": GROUP}, pluck="name"):
		frappe.delete_doc("Item", item, force=True, ignore_permissions=True)
	for dt, name in (("Price List", PRICE_LIST), ("Item Group", GROUP)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	for addr in frappe.get_all("Dynamic Link", {"link_doctype": "Customer", "link_name": BUYER, "parenttype": "Address"}, pluck="parent"):
		frappe.delete_doc("Address", addr, force=True, ignore_permissions=True)
	if frappe.db.exists("Customer", BUYER):
		frappe.delete_doc("Customer", BUYER, force=True, ignore_permissions=True)
	for team in (TEAM, OTHER_TEAM):
		if frappe.db.exists("Excom Team", team):
			frappe.delete_doc("Excom Team", team, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP, OUTSIDER)
	frappe.db.commit()


class TestBuilder(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP, OUTSIDER):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent")
		# The builder reads as the person building: a sales manager sees sales, stock and buying records.
		frappe.get_doc("User", OWNER).add_roles("Sales Manager", "Stock User", "Sales Master Manager", "Purchase User", "Accounts User")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Excom Team", "team_name": OTHER_TEAM, "members": [
			{"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Item Group", "item_group_name": GROUP, "parent_item_group": "All Item Groups"}).insert(
			ignore_permissions=True)
		cls.items = []
		hsn = frappe.db.get_value("GST HSN Code", {"name": ["like", "9503%"]}) if frappe.db.table_exists("GST HSN Code") else None
		hsn = hsn or (frappe.db.get_value("GST HSN Code", {}) if frappe.db.table_exists("GST HSN Code") else None)
		for i in range(4):
			it = frappe.get_doc({"doctype": "Item", "item_code": f"QA-SQUIRTLE-{i}", "item_name": f"QA Squirtle {i}",
				"item_group": GROUP, "stock_uom": "Nos", "is_stock_item": 0, "image": "/files/x.png" if i < 2 else None,
				"weight_per_unit": 0.2 if i < 2 else 0, "gst_hsn_code": hsn}).insert(ignore_permissions=True)
			cls.items.append(it.name)
		frappe.get_doc({"doctype": "Price List", "price_list_name": PRICE_LIST, "currency": "USD", "selling": 1,
			"enabled": 1}).insert(ignore_permissions=True)
		for item in cls.items[:2]:
			frappe.get_doc({"doctype": "Item Price", "price_list": PRICE_LIST, "item_code": item, "price_list_rate": 1.5}).insert(
				ignore_permissions=True)
		cust = {"doctype": "Customer", "customer_name": BUYER, "customer_type": "Company"}
		if frappe.get_meta("Customer").has_field("gst_category"):
			cust["gst_category"] = "Overseas"
		frappe.get_doc(cust).insert(ignore_permissions=True)
		addr = {"doctype": "Address", "address_title": BUYER, "address_line1": "Av. Reforma 1", "city": "CDMX",
			"country": "Mexico", "links": [{"link_doctype": "Customer", "link_name": BUYER}]}
		if frappe.get_meta("Address").has_field("gst_category"):
			addr["gst_category"] = "Overseas"
		frappe.get_doc(addr).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		import os

		if not os.environ.get("QA_KEEP"):
			_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	def build(self, **kw):
		frappe.set_user(OWNER)
		rows = api.resolve_keywords("Sales", KEYWORDS)
		for r in rows:
			if r["resolved_as"] == "AI proposal":
				r["resolved_as"] = "Vocabulary"  # what Confirm does on screen
		name = api.create_agent("Sales", KEYWORDS, rows=frappe.as_json(rows), persona=kw.pop("persona", "QA Build Lucía"),
			backup=BACKUP, team=TEAM, **kw)["name"]
		frappe.set_user("Administrator")
		return frappe.get_doc("Excom AI Agent", name)

	# ---- keywords

	def test_keywords_are_understood(self):
		rows = {r.keyword: r for r in resolve.resolve(KEYWORDS)}
		self.assertEqual(rows["export"].resolved_as, "Concept")
		self.assertEqual((rows["squirtles"].resolved_as, rows["squirtles"].link_name, rows["squirtles"].matches),
			("AI proposal", GROUP, 4), "item names point at the group: proposed for a person to confirm")
		self.assertEqual((rows["latin america"].resolved_as, rows["spanish"].resolved_as, rows["spanish"].language),
			("Region", "Language", "es"))
		scope = resolve.scope_from(list(rows.values()))
		self.assertIn("Mexico", scope.countries)
		self.assertNotIn("Brazil", scope.countries)
		self.assertEqual(scope.left_out, ["Brazil"], "Brazil speaks Portuguese: left out, and said so")
		self.assertEqual(resolve.resolve_one("zzqx nothing").resolved_as, "Unmatched")

	def test_a_confirmed_glossary_entry_is_reused(self):
		self.addCleanup(frappe.db.delete, "Excom AI Glossary", {"term": ["like", "squirtle%"]})
		frappe.set_user(OWNER)
		api.confirm_glossary("squirtles", "Item Group", GROUP)
		frappe.set_user("Administrator")
		row = resolve.resolve_one("squirtle")
		self.assertEqual((row.resolved_as, row.link_name), ("Vocabulary", GROUP))
		self.assertIn("glossary", row.detail)

	def test_what_the_erp_shows(self):
		rows = resolve.resolve(KEYWORDS)
		for r in rows:
			if r.resolved_as == "AI proposal":
				r.resolved_as = "Vocabulary"
		ctx = knowledge.context(rows, "sales", team=TEAM)
		by = {s["section"]: s for s in knowledge.build(ctx)}
		self.assertIn(BUYER, by["customers"]["data"]["in_markets"])
		self.assertEqual((by["products"]["data"]["count"], by["products"]["data"]["with_photo"]), (4, 2))
		usd = next(p for p in by["prices"]["data"]["lists"] if p["price_list"] == PRICE_LIST)
		self.assertEqual((usd["covered"], usd["currency"]), (2, "USD"))
		self.assertTrue(by["exclusions"]["data"]["customers"] >= 1)
		self.assertNotIn("Never contacted", knowledge.summary(list(by.values())), "the exclusion list never reaches a prompt")

	def test_unticked_records_are_left_out(self):
		rows = resolve.resolve(KEYWORDS)
		for r in rows:
			if r.resolved_as == "AI proposal":
				r.resolved_as = "Vocabulary"
		ctx = knowledge.context(rows, "sales", team=TEAM, excluded={"Item": self.items[2:], "Customer": [BUYER]})
		by = {s["section"]: s for s in knowledge.build(ctx)}
		self.assertEqual(by["products"]["data"]["count"], 2)
		self.assertNotIn(BUYER, by["customers"]["data"]["customers"])

	# ---- the draft agent

	def test_a_draft_agent_with_brief_scope_knowledge_and_readiness(self):
		a = self.build()
		self.assertEqual((a.status, a.owner_user, a.team, a.agent_name), ("Draft", OWNER, TEAM, "QA Build Lucía"))
		self.assertIn("squirtles (QA Squirters)", a.what_it_is)
		self.assertIn("Spanish", a.what_it_is)
		self.assertIn("usted", a.tone)
		sources = frappe.parse_json(a.brief_sources)
		self.assertIn("AI suggestion", sources["mission"])
		mexico = next(m for m in a.markets if m.country == "Mexico")
		self.assertEqual((mexico.language, mexico.timezone), ("es", "America/Mexico_City"))
		self.assertEqual([(p.item_group, p.price_list) for p in a.products], [(GROUP, PRICE_LIST)])
		pb = frappe.get_doc("Excom AI Playbook", a.playbooks[0].playbook)
		kinds = {s.kind: s.content for s in pb.sections}
		self.assertEqual(pb.status, "Draft")
		self.assertIn("4 items", kinds["Facts"])
		self.assertEqual(kinds["Banned claims"], "", "left for people to write")
		k = frappe.get_doc("Excom AI Knowledge", a.knowledge)
		self.assertEqual(k.version, 1)
		self.assertTrue(k.summary)
		self.assertEqual(a.readiness_state, "Blocked")
		gaps = {frappe.db.get_value("Excom AI Readiness Rule", g.rule, "rule_key"): g for g in
			frappe.get_all("Excom AI Readiness Gap", {"agent": a.name, "status": "Open"}, ["rule", "severity", "detail"])}
		self.assertIn("2 of the 4 items", gaps["sales.price_basis"].detail)
		self.assertIn("sales.product_facts", gaps)
		self.assertIn("sales.playbook", gaps)
		self.assertNotIn("sales.people", gaps, "owner, backup and team are set")

	def test_refresh_reports_what_changed_and_closes_fixed_gaps(self):
		a = self.build(persona="QA Build Refresh")
		for item in self.items[2:]:
			frappe.get_doc({"doctype": "Item Price", "price_list": PRICE_LIST, "item_code": item, "price_list_rate": 2}).insert(
				ignore_permissions=True)
		try:
			frappe.set_user(OWNER)
			out = api.refresh(a.name)
			frappe.set_user("Administrator")
			k = frappe.get_doc("Excom AI Knowledge", out["knowledge"])
			self.assertEqual(k.version, 2)
			self.assertIn("price lists", k.changes_since_last)
			rule = frappe.db.get_value("Excom AI Readiness Rule", {"rule_key": "sales.price_basis"})
			self.assertEqual(frappe.db.get_value("Excom AI Readiness Gap", {"agent": a.name, "rule": rule}, "status"), "Fixed")
		finally:
			frappe.db.delete("Item Price", {"price_list": PRICE_LIST, "item_code": ["in", self.items[2:]]})

	# ---- who may do what

	def test_who_builds_sees_and_changes(self):
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			api.types()
		frappe.set_user("Administrator")
		a = self.build(persona="QA Build Perms")
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			api.agent(a.name)
		self.assertNotIn(a.name, [r.name for r in api.agents()])
		frappe.set_user(BACKUP)
		page = api.agent(a.name)
		self.assertTrue(page["can_change"])
		self.assertTrue(page["gaps"] and page["knowledge"]["sections"])
		api.save_brief(a.name, frappe.as_json({"mission": "QA mission", "status": "Live"}))
		self.assertEqual(frappe.db.get_value("Excom AI Agent", a.name, ["mission", "status"]), ("QA mission", "Draft"),
			"only brief fields are saved")
		with self.assertRaises(frappe.ValidationError):
			api.set_policy(a.name, "quotation", "Auto")
		api.set_status(a.name, "Shadow")
		with self.assertRaises(frappe.ValidationError, msg="readiness blockers keep it from Live"):
			api.set_status(a.name, "Live")
		frappe.set_user("Administrator")

	def test_gaps_become_todos_and_only_admins_waive_blockers(self):
		a = self.build(persona="QA Build Gaps")
		gaps = {frappe.db.get_value("Excom AI Readiness Rule", g.rule, "severity"): g.name for g in
			frappe.get_all("Excom AI Readiness Gap", {"agent": a.name, "status": "Open"}, ["name", "rule"])}
		frappe.set_user(OWNER)
		todo = api.gap_todo(gaps["Blocker"], BACKUP)["todo"]
		self.assertEqual(frappe.db.get_value("ToDo", todo, "allocated_to"), BACKUP)
		with self.assertRaises(frappe.PermissionError):
			api.waive_gap(gaps["Blocker"], "We send prices by hand")
		frappe.set_user("Administrator")
		frappe.db.rollback()
		api.waive_gap(gaps["Blocker"], "We send prices by hand")
		self.assertEqual(frappe.db.get_value("Excom AI Readiness Gap", gaps["Blocker"], "status"), "Waived")

	def create(self, **kw):
		rows = api.resolve_keywords("Sales", KEYWORDS)
		for r in rows:
			if r["resolved_as"] == "AI proposal":
				r["resolved_as"] = "Vocabulary"
		return api.create_agent("Sales", KEYWORDS, rows=frappe.as_json(rows), **kw)["name"]

	def no_team_agent(self, persona):
		"""An agent an Excom Admin made without a team, owned by the backup; the owner reviews it."""
		name = self.create(persona=persona, owner=BACKUP)
		frappe.db.set_value("Excom AI Agent", name, "reviewers", OWNER)
		frappe.db.commit()
		return name

	def test_a_team_manager_names_only_members_of_their_team(self):
		frappe.set_user(OWNER)
		try:
			for kw, why in (({"owner": "Administrator"}, "no team: nobody to manage"),
					({"team": TEAM, "owner": "Administrator"}, "an admin is not a member"),
					({"team": TEAM, "owner": OUTSIDER}, "not in the team"),
					({"team": OTHER_TEAM}, "a team they do not manage"),
					({"backup": BACKUP}, "a backup needs the team too")):
				with self.assertRaises(frappe.PermissionError, msg=why):
					self.create(persona="QA Build Naming", **kw)
			name = self.create(persona="QA Build Naming", team=TEAM, owner=BACKUP, backup=OWNER)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Excom AI Agent", name, ["owner_user", "team"]), (BACKUP, TEAM))

	def test_a_team_manager_changes_only_their_teams_agents(self):
		name = self.no_team_agent("QA Build No Team")
		frappe.set_user(OWNER)  # a team's manager, and a reviewer here
		try:
			self.assertFalse(api.agent(name)["can_change"], "an agent without a team has no team manager")
			with self.assertRaises(frappe.PermissionError):
				api.save_brief(name, frappe.as_json({"mission": "QA taken over"}))
		finally:
			frappe.set_user("Administrator")

	def test_who_names_the_backup_and_reviewers(self):
		a = self.build(persona="QA Build People")
		same = {"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM, "escalation_user": "", "reviewers": ""}
		frappe.set_user(BACKUP)
		try:
			self.assertFalse(api.agent(a.name)["can_change_people"])
			api.set_people(a.name, **same)  # the screen sends everything: nothing changed, nothing checked
			with self.assertRaises(frappe.PermissionError, msg="the backup does not choose reviewers"):
				api.set_people(a.name, **{**same, "reviewers": OWNER})
			with self.assertRaises(frappe.PermissionError, msg="nor the escalation"):
				api.set_people(a.name, **{**same, "escalation_user": OUTSIDER})
			with self.assertRaises(frappe.PermissionError, msg="nor the backup"):
				api.set_people(a.name, **{**same, "backup_user": ""})
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(OWNER)
		try:
			with self.assertRaises(frappe.PermissionError, msg="not in the team"):
				api.set_people(a.name, **{**same, "reviewers": OUTSIDER})
			with self.assertRaises(frappe.PermissionError, msg="a team they do not manage"):
				api.set_people(a.name, **{**same, "team": OTHER_TEAM})
		finally:
			frappe.set_user("Administrator")
		name = self.no_team_agent("QA Build No Team People")
		frappe.set_user(BACKUP)  # its owner
		try:
			with self.assertRaises(frappe.PermissionError, msg="no team: an admin names its people"):
				api.set_people(name, escalation_user=OWNER)
		finally:
			frappe.set_user("Administrator")

	def test_only_an_admin_lifts_the_ai_budget(self):
		a = self.build(persona="QA Build Budget")
		frappe.set_user(BACKUP)
		try:
			with self.assertRaises(frappe.PermissionError, msg="0 is no limit"):
				api.set_limits(a.name, daily_ai_budget=0)
			with self.assertRaises(frappe.ValidationError):
				api.set_limits(a.name, monthly_ai_budget="-0.5")
			api.set_limits(a.name, daily_ai_budget="", monthly_ai_budget=40)
			self.assertEqual(frappe.db.get_value("Excom AI Agent", a.name, ["daily_ai_budget", "monthly_ai_budget"]), (5, 40),
				"an empty box leaves the budget alone")
		finally:
			frappe.set_user("Administrator")
		api.set_limits(a.name, daily_ai_budget=0)
		frappe.set_user(BACKUP)
		try:
			api.set_limits(a.name, daily_ai_budget=0, monthly_ai_budget=30)  # what an admin chose, sent back unchanged
		finally:
			frappe.set_user("Administrator")
		doc = frappe.get_doc("Excom AI Agent", a.name)
		doc.daily_ai_budget = -0.5
		self.assertRaises(frappe.ValidationError, doc.save, ignore_permissions=True)

	# ---- recipes and the nightly refresh

	def test_a_query_recipe_runs_with_v16_aggregates(self):
		from excom.excom_ai.doctype.excom_ai_recipe.excom_ai_recipe import run_spec

		rows = run_spec({"doctype": "Customer", "fields": ["customer_group", "count(name) as n"], "group_by": "customer_group",
			"filters": [["customer_name", "=", BUYER]]})
		self.assertEqual(rows[0].n, 1)

	def test_nightly_refresh(self):
		a = self.build(persona="QA Build Nightly")
		a.db_set("status", "Shadow")
		was = frappe.db.get_single_value("Excom AI Settings", "enabled")
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", was)
		frappe.db.set_single_value("Excom AI Settings", "enabled", 0)
		with patch.object(service, "refresh") as refresh:
			service.nightly()
		refresh.assert_not_called()
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		seen = []
		with patch.object(service, "refresh", side_effect=lambda name, notify_changes: seen.append((name, frappe.session.user))):
			service.nightly()
		self.assertIn((a.name, OWNER), seen, "rebuilt as its owner, who sees what they may see")
		self.assertEqual(frappe.session.user, "Administrator")

	def test_rules_are_seeded_once(self):
		readiness.seed("Sales")
		self.assertEqual(frappe.db.count("Excom AI Readiness Rule", {"rule_key": ["like", "sales.%"]}), len(readiness.SALES_RULES))
		self.assertTrue(frappe.db.exists("Excom AI Recipe", {"recipe_key": "sales.customers", "kind": "Built-in"}))

	def test_sections_the_person_cannot_read_are_skipped(self):
		frappe.set_user(BACKUP)  # an Excom Agent only: no sales records
		try:
			rows = resolve.resolve(KEYWORDS)
			sections = knowledge.build(knowledge.context(rows, "sales", team=TEAM))
		finally:
			frappe.set_user("Administrator")
		prices = next(s for s in sections if s["section"] == "prices")
		self.assertTrue(prices.get("skipped"))
		self.assertIn("cannot read", prices["facts"][0]["text"])

	def test_an_unknown_keyword_gets_a_proposed_recipe(self):
		from types import SimpleNamespace

		from excom.excom_ai.builder import brief

		proposal = {"meaning": "Customers abroad buying squirtles", "doctype": "Customer", "fields": ["name", "customer_name"],
			"filters": [["customer_name", "like", "QA Comprador%"]], "group_by": "", "order_by": ""}
		frappe.set_user(OWNER)
		try:
			with patch.object(brief, "ai_ready", return_value=False):
				self.assertFalse(api.suggest_recipe("pool shops")["ok"], "no provider: says so")
			with patch.object(brief, "ai_ready", return_value=True), \
					patch("ai_interface.services.chat.run", return_value=SimpleNamespace(data=proposal)):
				out = api.suggest_recipe("pool shops")
			self.assertTrue(out["ok"])
			self.assertEqual(out["count"], 1)
			bad = dict(proposal, doctype="User")
			with patch.object(brief, "ai_ready", return_value=True), \
					patch("ai_interface.services.chat.run", return_value=SimpleNamespace(data=bad)):
				self.assertFalse(api.suggest_recipe("pool shops")["ok"], "an unsafe proposal is refused")
			name = api.accept_recipe("pool shops", out["meaning"], frappe.as_json(out["spec"]))["name"]
		finally:
			frappe.set_user("Administrator")
		self.addCleanup(frappe.delete_doc, "Excom AI Recipe", name, force=True, ignore_permissions=True)
		row = resolve.resolve_one("pool shops")
		self.assertEqual(row.recipe, name, "the next agent reuses it")
		sections = knowledge.build(knowledge.context([row], "sales", team=TEAM))
		custom = next(s for s in sections if s["section"] == f"recipe:{name}")
		self.assertEqual(custom["data"]["count"], 1)
