"""HLD-008, the Template Steward: needs, the checks, drafting and submission (Meta mocked), Meta's
verdicts, the per-account stops, email templates in review, finding and filling, and the send rules
for templates (approved, on the agent's number, opted in)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import requests

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import send_rules, templates
from excom.excom_ai.engine import ownership, proposals, runner

OWNER = "qa.tpl.owner@example.com"
BACKUP = "qa.tpl.backup@example.com"
TEAM = "QA Tpl Desk"
LINES = ("QA Tpl WA 1", "QA Tpl WA 2", "QA Tpl WA 3")  # 1 and 2 share a WhatsApp business account
WABA, OTHER_WABA = "100000123456", "200000654321"
PTYPE, PROVIDER = "QA Tpl Scripted", "QA Tpl Provider"
META = "excom.excom.doctype.whatsapp_templates.whatsapp_templates.make_post_request"
GOOD = {"subject": "", "body": "Hello {{1}}, a friendly note that {{2}} is overdue on invoices {{3}}. Thank you.",
	"samples": ["Maria", "1,250.00", "SINV-0001"], "back_translation": "Hello ..."}
CHECK_OK = {"ok": True, "problems": [], "back_translation": "Hello, a friendly note ..."}


def _cleanup():
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Tpl%"]}, pluck="name")
	needs = frappe.get_all("Excom AI Template Need", {"agent": ["in", agents or ["-"]]}, pluck="name")
	for t in frappe.get_all("WhatsApp Templates", {"xai_need": ["in", needs or ["-"]]}, pluck="name") + \
			frappe.get_all("WhatsApp Templates", {"template_name": ["like", "xai_%"]}, pluck="name"):
		frappe.db.delete("WhatsApp Template Linked Account", {"parent": t})
		frappe.db.delete("WhatsApp Templates", {"name": t})
	frappe.db.delete("Excom AI Template Need", {"name": ["in", needs or ["-"]]})
	for dt in ("Excom AI Proposal", "Excom AI Task"):
		frappe.db.delete(dt, {"agent": ["in", agents or ["-"]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [OWNER, BACKUP]]})
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Tpl%"]}, pluck="name"):
		frappe.db.delete("Excom Consent", {"omni_identity": oi})
		frappe.db.delete("Excom Message", {"omni_identity": oi})
		frappe.db.delete("Excom Thread", {"omni_identity": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for line in LINES:
		for acc in frappe.get_all("Excom Channel Account", {"account_name": line}, pluck="name"):
			frappe.delete_doc("Excom Channel Account", acc, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	purge_users(OWNER, BACKUP)
	frappe.db.commit()


class TestTemplateSteward(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent", "Accounts User")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.lines = []
		for i, (line, waba) in enumerate(zip(LINES, (WABA, WABA, OTHER_WABA), strict=True)):
			cls.lines.append(frappe.get_doc({"doctype": "Excom Channel Account", "account_name": line, "channel": "whatsapp",
				"status": "Active", "wa_phone_id": f"P{i}", "wa_business_id": waba, "wa_version": "v26.0",
				"wa_token": "qa-token-000"}).insert(ignore_permissions=True).name)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}],
				api_base_url="main")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("Excom AI Settings")
		saved = {k: s.get(k) for k in ("enabled", "templates_enabled", "whatsapp_auto_submit", "template_submissions_per_day",
			"template_first_use_review")}
		frappe.db.set_single_value("Excom AI Settings", {"enabled": 1, "templates_enabled": 1, "whatsapp_auto_submit": 1,
			"template_submissions_per_day": 10, "template_first_use_review": 1})
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.addCleanup(lambda: (frappe.db.set_single_value("Excom AI Settings", saved),
			frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")))
		type(self).n = getattr(type(self), "n", 0) + 1
		self.wipe()

	def wipe(self):
		"""Needs are shared per business account: each test starts with none."""
		ours = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Tpl%"]}, pluck="name")
		needs = frappe.get_all("Excom AI Template Need", {"wa_business_id": ["in", [WABA, OTHER_WABA]]}, pluck="name") + \
			frappe.get_all("Excom AI Template Need", {"agent": ["in", ours or ["-"]]}, pluck="name")
		for t in frappe.get_all("WhatsApp Templates", {"whatsapp_account": ["in", self.lines]}, pluck="name"):
			frappe.db.delete("WhatsApp Template Linked Account", {"parent": t})
			frappe.db.delete("WhatsApp Templates", {"name": t})
		frappe.db.delete("Excom AI Template Need", {"name": ["in", needs or ["-"]]})
		frappe.db.commit()

	def agent(self, line=0, status="Live", purpose="Outreach and replies"):
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Tpl {self.n}-{line}-{status}", "agent_type": "Accounts",
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM, "company": frappe.defaults.get_global_default("company"),
			"channels": [{"channel_account": self.lines[line], "purpose": purpose}]}).insert(ignore_permissions=True)
		a.db_set({"status": status, "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		self.addCleanup(lambda: frappe.db.set_value("Excom AI Agent", a.name, "status", "Retired") or frappe.db.commit())
		return frappe.get_doc("Excom AI Agent", a.name)

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def draft(self, agent, need_name, *answers, meta=None):
		"""Claim and draft one need through the real task path, Meta's create mocked."""
		self.assertTrue(templates._claim(need_name))
		from excom.excom_ai.engine import tasks

		t = tasks.add(agent.name, "template_draft", subject=("Excom AI Template Need", need_name), dedupe_key=f"t{self.n}{need_name}")
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		post = meta or (lambda *a, **k: {"id": f"MID{self.n}", "status": "PENDING"})
		with self.scripted(*answers), patch(META, side_effect=post), patch("requests.get") as get:
			get.return_value.json.return_value, get.return_value.content, get.return_value.status_code = {"data": []}, b"{}", 200
			runner.run_task(t)
		return frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)

	# ---- needs

	def test_one_need_per_business_account_and_language(self):
		a, b = self.agent(0), self.agent(1, purpose="Replies")
		n1 = templates.need(a, "accounts.payment_reminder", "WhatsApp", "es-MX", self.lines[0])
		self.assertEqual(templates.need(b, "accounts.payment_reminder", "WhatsApp", "es_MX", self.lines[1]), n1,
			"two numbers on one business account: one template")
		self.assertIn(b.name, frappe.db.get_value("Excom AI Template Need", n1, "agents"))
		c = self.agent(2)
		self.assertNotEqual(templates.need(c, "accounts.payment_reminder", "WhatsApp", "es_MX", self.lines[2]), n1)
		self.assertNotEqual(templates.need(a, "accounts.payment_reminder", "WhatsApp", "pt", self.lines[0]), n1)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", {"unique_key": ["like", "%|pt_BR"],
			"agent": a.name}, "language"), "pt_BR")
		self.assertIsNone(templates.need(a, "accounts.unknown", "WhatsApp", "en", self.lines[0]), "undeclared")
		self.assertIsNone(templates.need(a, "accounts.statement_ready", "Email", "en"), "not declared for email")

	def test_languages(self):
		self.assertEqual(templates.meta_language("pt"), "pt_BR")
		self.assertEqual(templates.meta_language("es-MX"), "es_MX")
		self.assertEqual(templates.meta_language("Spanish"), "es")
		self.assertEqual(templates.meta_language("zh"), "zh_CN")
		self.assertEqual(templates.meta_language("xx"), "en")
		self.assertEqual(templates.language_fallbacks("es-MX")[:2], ["es_MX", "es"])

	# ---- the checks

	def test_the_code_checks(self):
		spec = templates.NEEDS["accounts.payment_reminder"]
		ok = templates.check_body(spec, "WhatsApp", GOOD["body"], GOOD["samples"])
		self.assertEqual(ok, [])
		bad = {
			"price": ("Hello {{1}}, pay $50 for {{2}} on invoices {{3}} today please.", GOOD["samples"]),
			"starts": ("{{1}}, a note that {{2}} is overdue on invoices {{3}}. Thank you.", GOOD["samples"]),
			"side by side": ("Hello {{1}}, a note that {{2}} {{3}} is overdue now. Thank you.", GOOD["samples"]),
			"undeclared": ("Hello {{1}}, a note that {{2}} is overdue on {{3}} and {{4}}. Thanks.", [*GOOD["samples"], "x"]),
			"promotional": ("Hello {{1}}, special offer: {{2}} is overdue on invoices {{3}}. Thank you.", GOOD["samples"]),
			"samples missing": (GOOD["body"], ["Maria"]),
			"too long": ("Hello {{1}}, " + "word " * 210 + "{{2}} on {{3}}. Thanks.", GOOD["samples"]),
			"link": ("Hello {{1}}, see https://x.example about {{2}} on invoices {{3}}. Thanks.", GOOD["samples"]),
		}
		for why, (body, samples) in bad.items():
			self.assertTrue(templates.check_body(spec, "WhatsApp", body, samples), why)
		customer = frappe.db.get_value("Customer", {}, "customer_name")
		if customer and len(customer) >= 3:
			self.assertTrue(templates.check_body(spec, "WhatsApp", GOOD["body"], [customer, "1", "2"]), "a real customer")
		email = "Dear {{1}},\n\nThe following is open ({{2}}):\n\n{{content}}\n\nKind regards"
		self.assertEqual(templates.check_body(spec, "Email", email, []), [])
		self.assertTrue(templates.check_body(spec, "Email", email.replace("{{content}}", ""), []), "content once")
		self.assertEqual(templates.template_name("accounts.payment_reminder", "es_MX", WABA),
			"xai_accounts_payment_reminder_es_mx_123456")

	# ---- drafting and submission

	def test_a_need_becomes_a_template_on_meta(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		posts = []
		row = self.draft(a, n, GOOD, CHECK_OK, meta=lambda url, headers=None, data=None: posts.append(json.loads(data)) or
			{"id": "MID-1", "status": "PENDING"})
		self.assertEqual(row.status, "Done", row.error)
		need = frappe.get_doc("Excom AI Template Need", n)
		self.assertEqual(need.status, "Submitted", need.last_error)
		self.assertEqual(posts[0]["name"], "xai_accounts_payment_reminder_en_123456")
		self.assertEqual((posts[0]["category"], posts[0]["language"]), ("UTILITY", "en"))
		t = frappe.get_doc("WhatsApp Templates", need.template)
		self.assertEqual((t.whatsapp_account, t.xai_need, t.id), (self.lines[0], n, "MID-1"))
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "document_name": n}), "the owner is told")

	def test_meta_refusing_fails_the_need_not_the_agent(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		a = self.agent(0)
		n = templates.need(a, "accounts.statement_ready", "WhatsApp", "en", self.lines[0])

		def refuse(*args, **kw):
			raise Exception("(#100) Invalid parameter")

		body = {"subject": "", "body": "Hello {{1}}, your statement as of {{2}} is ready and will reach you by email.",
			"samples": ["Maria", "1 March"], "back_translation": "-"}
		row = self.draft(a, n, body, CHECK_OK, meta=refuse)
		self.assertEqual(row.status, "Done", "the task itself does not fail, so nothing is retried")
		need = frappe.get_doc("Excom AI Template Need", n)
		self.assertEqual(need.status, "Failed")
		self.assertIn("refused", need.last_error)
		self.assertFalse(frappe.db.get_value("Excom AI Agent", a.name, "consecutive_failures"))
		self.assertFalse(frappe.db.exists("WhatsApp Templates", {"xai_need": n}))

	def test_a_bad_draft_is_rewritten_once_then_fails(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		bad = dict(GOOD, body="{{1}} pay $50 now")
		row = self.draft(a, n, bad, bad)
		self.assertEqual(row.status, "Done")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Failed")
		n2 = templates.need(a, "accounts.payment_reminder", "WhatsApp", "fr", self.lines[0])
		row = self.draft(a, n2, bad, GOOD, CHECK_OK)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n2, "status"), "Submitted", "fixed on the second try")

	def test_shadow_agents_never_reach_meta_and_claims_are_single(self):
		shadow = self.agent(0, status="Shadow")
		n = templates.need(shadow, "accounts.payment_reminder", "WhatsApp", "de", self.lines[0])
		self.assertNotIn(n, templates.dispatch(), "no Live agent needs it")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Open")
		live = self.agent(1, purpose="Replies")
		templates.need(live, "accounts.payment_reminder", "WhatsApp", "de", self.lines[1])
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 0)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.assertNotIn(n, templates.dispatch(), "the daily cap")
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 10)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.assertIn(n, templates.dispatch())
		self.assertTrue(frappe.db.exists("Excom AI Task", {"agent": live.name, "kind": "template_draft"}), "on the Live agent")
		self.assertFalse(templates._claim(n), "claimed once")

	def test_the_library_first(self):
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		frappe.db.set_value("Excom AI Template Need", n, "status", "Drafting")  # claimed, as template_draft finds it
		entry = {"name": "payment_overdue_1", "language": "en", "body": "Hi {{1}}, your payment of {{2}} is overdue.",
			"button_inputs": []}
		created = {}

		def create(lib_name, name, language, account_name=""):
			doc = frappe.get_doc({"doctype": "WhatsApp Templates", "template_name": name, "template": entry["body"],
				"language": "en", "category": "UTILITY", "whatsapp_account": account_name, "status": "APPROVED", "id": "LIB1"})
			doc.flags.ignore_validate = True
			doc.db_insert()
			created["name"] = doc.name
			return {"name": doc.name, "status": "APPROVED", "category": "UTILITY"}

		task = frappe._dict(name=None, kind="template_draft", subject_name=n)
		mapped = frappe._dict(data={"usable": True, "map": [{"n": 1, "field": "contact_name"}, {"n": 2, "field": "total_due"}]})
		with patch("excom.excom.services.whatsapp_template_library.search", return_value={"data": [entry]}), \
				patch("excom.excom.services.whatsapp_template_library.create", side_effect=create), \
				patch.object(runner, "ai", return_value=mapped):
			out = templates._try_library(task, a, frappe.get_doc("Excom AI Template Need", n), templates.NEEDS[
				"accounts.payment_reminder"])
		self.assertIn("library", out)
		need = frappe.get_doc("Excom AI Template Need", n)
		self.assertEqual((need.status, need.source, need.template), ("Submitted", "Library", created["name"]))
		self.assertEqual([v["field"] for v in json.loads(need.variable_map)], ["contact_name", "total_due"])
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": OWNER, "document_name": n}),
			"the owner is told, with Withdraw, as for a written one")

	# ---- Meta's verdicts

	def submitted(self, agent, lang="en", line=0):
		n = templates.need(agent, "accounts.payment_reminder", "WhatsApp", lang, self.lines[line])
		frappe.db.set_value("Excom AI Template Need", n, {"status": "Drafting", "body": GOOD["body"],
			"samples": json.dumps(GOOD["samples"]), "variable_map": json.dumps(templates.NEEDS["accounts.payment_reminder"][
				"variables"])})
		with patch(META, return_value={"id": f"MID{self.n}{lang}", "status": "PENDING"}), patch("requests.get") as get:
			get.return_value.json.return_value, get.return_value.content, get.return_value.status_code = {"data": []}, b"{}", 200
			templates.submit(n)
		need = frappe.get_doc("Excom AI Template Need", n)
		self.assertEqual(need.status, "Submitted", need.last_error)
		return need

	def test_meta_verdicts(self):
		a = self.agent(0)
		ok = self.submitted(a, "en")
		frappe.db.set_value("WhatsApp Templates", ok.template, {"status": "approved", "category": "UTILITY"})
		moved = self.submitted(a, "es")
		frappe.db.set_value("WhatsApp Templates", moved.template, {"status": "APPROVED", "category": "MARKETING"})
		rejected = self.submitted(a, "fr")
		frappe.db.set_value("WhatsApp Templates", rejected.template, {"status": "REJECTED", "rejection_reason": "INVALID_FORMAT"})
		frappe.db.set_value("Excom AI Template Need", rejected.name, "attempts", 1)
		final = self.submitted(a, "de")
		frappe.db.set_value("WhatsApp Templates", final.template, {"status": "REJECTED"})
		frappe.db.set_value("Excom AI Template Need", final.name, "attempts", 2)
		old = self.submitted(a, "it")
		frappe.db.set_value("Excom AI Template Need", old.name, "submitted_on", add_to_date(now_datetime(), hours=-30))
		with patch.object(templates, "_poll", return_value=frappe._dict(status="APPROVED", category="UTILITY")) as poll:
			templates.statuses()
		poll.assert_called_once()
		status = lambda n: frappe.db.get_value("Excom AI Template Need", n.name, "status")  # noqa: E731
		self.assertEqual(status(ok), "Met", "approved, any case")
		self.assertEqual(status(moved), "Failed", "Meta moved it to marketing: never used")
		self.assertEqual(status(rejected), "Open", "reworked")
		self.assertEqual(status(final), "Failed", "rejected too often")
		self.assertEqual(status(old), "Met", "polled when the webhook never came")
		frappe.db.set_value("WhatsApp Templates", ok.template, "category", "MARKETING")
		templates.statuses()
		self.assertEqual(status(ok), "Failed", "a template in use that Meta moved is taken out of use")
		# a rework edits the same template: same id, no new slot
		frappe.db.set_value("Excom AI Template Need", rejected.name, {"status": "Drafting", "body": GOOD["body"].replace(
			"friendly", "short")})
		with patch(META, return_value={"success": True}) as post:
			templates.submit(rejected.name)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", rejected.name, "template"), rejected.template)
		self.assertTrue(post.called, "the edit is posted to the same Meta template")
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", rejected.template, "status"), "PENDING",
			"the old rejection no longer stands")
		templates.statuses()
		self.assertEqual(status(rejected), "Submitted", "waiting for Meta's new verdict, not failed by the old one")
		self.assertEqual(templates.submit(rejected.name), "another run is submitting it", "claimed once")

	def test_stops_per_business_account(self):
		a = self.agent(0)
		self.assertIsNone(templates.waba_stop(WABA))
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "RED")
		self.assertIn("quality", templates.waba_stop(WABA))
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "GREEN")
		for lang in ("en", "es", "fr"):
			t = self.submitted(a, lang).template
			frappe.db.set_value("WhatsApp Templates", t, {"status": "REJECTED", "rejected_at": now_datetime()})
		self.assertIn("rejections", templates.waba_stop(WABA))
		self.assertIsNone(templates.waba_stop(OTHER_WABA))

	# ---- email templates: a person approves

	def test_an_email_template_waits_for_a_person(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		from excom.excom_ai import needs_you

		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "Email", "en")
		email = {"subject": "Overdue invoices", "body": "Dear {{1}},\n\nThese invoices are open ({{2}}):\n\n{{content}}\n\n"
			"Kind regards", "samples": [], "back_translation": "-"}
		self.assertEqual(self.draft(a, n, email, CHECK_OK).status, "Done")
		need = frappe.get_doc("Excom AI Template Need", n)
		self.assertEqual(need.status, "In review")
		p = frappe.get_doc("Excom AI Proposal", need.proposal)
		self.assertEqual((p.action, p.status), ("approve_template", "Pending"))
		self.assertEqual(needs_you.decide_many([p.name], "approve")["done"], [], "never in bulk")
		self.assertIsNone(templates.find(a, "accounts.payment_reminder", "Email", "en", record=False))
		frappe.set_user(OWNER)
		try:
			proposals.decide(p.name, "approve", payload={"need": n, "subject": "Open invoices", "body": email["body"].replace(
				"Kind regards", "Best regards")})
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Met")
		found = templates.find(a, "accounts.payment_reminder", "Email", "en", record=False)
		self.assertEqual(templates.email_parts(found, {"contact_name": "Ana", "total_due": "USD 10"}),
			("Open invoices", "Dear Ana,\n\nThese invoices are open (USD 10):", "Best regards"))

	def test_an_approval_acts_on_its_own_current_need_only(self):
		from excom.excom_ai.engine import registry

		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "Email", "en")
		other = templates.need(a, "purchase.po_chase", "Email", "en")
		frappe.db.set_value("Excom AI Template Need", n, {"status": "In review", "proposal": "P-1"})
		good = "Dear {{1}},\n\nOpen ({{2}}):\n\n{{content}}\n\nKind regards"
		p = frappe._dict(name="P-1", subject_doctype="Excom AI Template Need", subject_name=n,
			payload=json.dumps({"need": other, "subject": "x", "body": good}))
		templates.approve_template(p, a)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Met", "its own need")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", other, "status"), "Open", "never the one in the payload")
		self.assertRaises(registry.Cancel, templates.approve_template, p, a)  # no longer in review: stale
		frappe.db.set_value("Excom AI Template Need", other, {"status": "In review", "proposal": "P-2"})
		bad = frappe._dict(name="P-2", subject_doctype="Excom AI Template Need", subject_name=other,
			payload=json.dumps({"subject": "x", "body": "Pay $50 now {{content}}"}))
		self.assertRaises(registry.Cancel, templates.approve_template, bad, a)  # an edit is checked again

	def test_a_review_that_ended_frees_the_need(self):
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "Email", "en")
		frappe.db.set_value("Excom AI Template Need", n, {"status": "In review", "proposal": None})
		templates.statuses()
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Failed")

	# ---- finding, filling and sending

	def test_find_fill_and_the_send_rules(self):
		a = self.agent(0)
		need = self.submitted(a, "en")
		self.assertIsNone(templates.find(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0]), "not approved yet")
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "APPROVED", "category": "UTILITY"})
		templates.statuses()
		found = templates.find(a, "accounts.payment_reminder", "WhatsApp", "en-GB", self.lines[0])
		self.assertEqual(found["template"], need.template, "en_GB falls back to en")
		values = templates.whatsapp_values(found, {"contact_name": "Ana\nMaria", "total_due": "USD   1,250.00",
			"invoices": "SINV-1,\tSINV-2"})
		self.assertEqual(values, ["Ana Maria", "USD 1,250.00", "SINV-1, SINV-2"])
		self.assertEqual(templates.whatsapp_values(found, {"total_due": "1", "invoices": "2"})[0], "there", "the fallback")
		self.assertRaises(ValueError, templates.whatsapp_values, found, {"contact_name": "A", "invoices": "2"})
		self.assertRaises(ValueError, templates.whatsapp_values, dict(found, text=found["text"] + " pad" * 240),
			{"contact_name": "A", "total_due": "1", "invoices": "x" * 199})  # over 1024 characters once filled
		# the send rules for a template
		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Tpl Buyer {self.n}",
			"primary_phone": f"+5215550{self.n:04d}"}).insert(ignore_permissions=True)
		thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi.name, "channel": "whatsapp",
			"account_doctype": "Excom Channel Account", "account": self.lines[0], "thread_key": f"qa-tpl-{self.n}",
			"status": "Open"}).insert(ignore_permissions=True)
		check = lambda: send_rules.check(thread.name, purpose="outreach", user=a.service_user, window=False,  # noqa: E731
			template=need.template)
		self.assertRaisesRegex(send_rules.AgentSendRefused, "opt-in", check)
		from excom.excom.services.consent import record_consent

		record_consent(oi.name, "Granted", channel="whatsapp", purpose="transactional", source="manual")
		check()
		frappe.db.set_value("WhatsApp Templates", need.template, "category", "MARKETING")
		self.assertRaisesRegex(send_rules.AgentSendRefused, "moved", check)
		frappe.db.set_value("WhatsApp Templates", need.template, {"category": "UTILITY", "status": "PAUSED"})
		self.assertRaisesRegex(send_rules.AgentSendRefused, "not approved", check)
		self.assertIsNone(templates.find(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0], record=False),
			"a paused template is never used")

	def test_the_first_send_waits_for_a_person(self):
		a = self.agent(0)
		need = self.submitted(a, "en")
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "APPROVED", "category": "UTILITY"})
		row = next((p for p in a.policies if p.action == "payment_reminder"), None) or a.append("policies",
			{"action": "payment_reminder"})
		row.mode = "Auto"
		a.save(ignore_permissions=True)
		task = frappe._dict(name=None)
		first = proposals.propose(task, a, "payment_reminder", subject=("Excom AI Agent", a.name), risk="Low",
			payload={"channel": "whatsapp", "template": need.template, "variables": ["a", "b", "c"], "n": 1})
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", first, "status"), "Pending", "first use: a person")
		frappe.db.set_value("WhatsApp Templates", need.template, "xai_first_use_done", 1)
		second = proposals.propose(task, a, "payment_reminder", subject=("Excom AI Agent", a.name), risk="Low",
			payload={"channel": "whatsapp", "template": need.template, "variables": ["a", "b", "c"], "n": 2})
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", second, "status"), "Approved", "then the policy")

	def test_withdraw(self):
		a = self.agent(0)
		need = self.submitted(a, "en")
		with patch("excom.excom.doctype.whatsapp_templates.whatsapp_templates.make_request") as delete:
			templates.withdraw(need.name)
		delete.assert_called_once()
		row = frappe.db.get_value("Excom AI Template Need", need.name, ["status", "version"], as_dict=True)
		self.assertEqual((row.status, row.version), ("Withdrawn", 2), "the next try gets a new name")
		self.assertFalse(frappe.db.exists("WhatsApp Templates", need.template))
		self.assertTrue(templates.template_name("accounts.payment_reminder", "en", WABA, 2).endswith("_v2"))

	def test_withdrawing_a_template_already_sent(self):
		a = self.agent(0)
		need = self.submitted(a, "es")
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "APPROVED", "category": "UTILITY"})
		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Tpl Sent {self.n}",
			"primary_phone": f"+5215551{self.n:04d}"}).insert(ignore_permissions=True)
		thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi.name, "channel": "whatsapp",
			"account_doctype": "Excom Channel Account", "account": self.lines[0], "thread_key": f"qa-tpl-sent-{self.n}",
			"status": "Open"}).insert(ignore_permissions=True)
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread.name, "channel": "whatsapp", "account": self.lines[0],
			"account_doctype": "Excom Channel Account", "omni_identity": oi.name, "direction": "Outbound",
			"message_type": "Template", "template": need.template, "content_text": "x", "delivery_status": "Sent"})
		msg.flags.ignore_validate = msg.flags.ignore_mandatory = True
		msg.insert(ignore_permissions=True)
		with patch("excom.excom.doctype.whatsapp_templates.whatsapp_templates.make_request") as delete:
			templates.withdraw(need.name)
		delete.assert_called_once()
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", need.template, "status"), "DELETED",
			"kept for the sent messages, gone on Meta, never used again")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", need.name, "status"), "Withdrawn")

	# ---- the Sales planner no longer proposes a follow-up on every tick

	def test_a_followed_up_quote_is_not_planned_again(self):
		from excom.excom_ai import deals
		from excom.excom_ai.engine import tasks

		a = self.agent(0)
		self.assertFalse(deals._followed_up(a.name, "Quotation", "QA-TPL-Q-1"))
		t = tasks.add(a.name, "order_update", dedupe_key="order:QA-TPL-SO-1:To Bill")
		for doctype, name in (("Quotation", "QA-TPL-Q-1"), ("Sales Order", "QA-TPL-SO-1")):
			p = frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "reply", "status": "Pending",
				"subject_doctype": doctype, "subject_name": name, "task": t})
			p.flags.ignore_validate = p.flags.ignore_links = True
			p.insert(ignore_permissions=True)
		self.assertTrue(deals._followed_up(a.name, "Quotation", "QA-TPL-Q-1", frappe.utils.add_days(frappe.utils.nowdate(), -5)))
		self.assertTrue(deals._followed_up(a.name, "Sales Order", "QA-TPL-SO-1", None, "To Bill"))
		self.assertFalse(deals._followed_up(a.name, "Sales Order", "QA-TPL-SO-1", None, "Completed"), "a new status is told")
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "subject_name": "QA-TPL-Q-1"})
		p.db_set("status", "Failed")
		# a failed send may have gone out before it broke: never proposed again, so the buyer never gets it twice
		self.assertTrue(deals._followed_up(a.name, "Quotation", "QA-TPL-Q-1", frappe.utils.add_days(frappe.utils.nowdate(), -5)),
			"a failed send counts as done")
		p.db_set("status", "Cancelled")
		self.assertFalse(deals._followed_up(a.name, "Quotation", "QA-TPL-Q-1", frappe.utils.add_days(frappe.utils.nowdate(), -5)),
			"a released proposal is not a follow-up done")

	# ---- review findings (2026-11-14)

	def library(self, agent, need_name, create):
		"""_try_library on a claimed need, Meta's library mocked with one fitting entry."""
		entry = {"name": "payment_overdue_1", "language": "en", "body": "Hi {{1}}, your payment of {{2}} is overdue.",
			"button_inputs": []}
		mapped = frappe._dict(data={"usable": True, "map": [{"n": 1, "field": "contact_name"}, {"n": 2, "field": "total_due"}]})
		task = frappe._dict(name=None, kind="template_draft", subject_name=need_name)
		with patch("excom.excom.services.whatsapp_template_library.search", return_value={"data": [entry]}), \
				patch("excom.excom.services.whatsapp_template_library.create", side_effect=create), \
				patch.object(runner, "ai", return_value=mapped), \
				patch("excom.excom.doctype.whatsapp_templates.whatsapp_templates.make_request"):
			return templates._try_library(task, agent, frappe.get_doc("Excom AI Template Need", need_name),
				templates.NEEDS["accounts.payment_reminder"])

	def lib_row(self, name, account):
		doc = frappe.get_doc({"doctype": "WhatsApp Templates", "template_name": name, "template": "Hi {{1}}, {{2}} is overdue.",
			"language": "en", "category": "UTILITY", "whatsapp_account": account, "status": "APPROVED", "id": f"LIB{self.n}"})
		doc.flags.ignore_validate = True
		doc.db_insert()
		return {"name": doc.name, "status": "APPROVED", "category": "UTILITY"}

	def test_dispatch_is_not_starved_by_needs_that_cannot_go(self):
		shadow = self.agent(0, status="Shadow")
		langs = ["en", "es", "fr", "de", "it", "nl", "pt_BR", "hi", "ar", "ja", "ru", "tr", "id", "bn", "gu", "mr", "ta", "te",
			"ur", "sv", "pl", "ko"]
		stuck = [templates.need(shadow, "accounts.payment_reminder", "WhatsApp", lang, self.lines[0]) for lang in langs]
		live = self.agent(2)
		newer = templates.need(live, "accounts.payment_reminder", "WhatsApp", "en", self.lines[2])
		self.assertIn(newer, templates.dispatch(limit=20), "22 older needs with no Live agent do not hold it up")
		self.assertEqual({frappe.db.get_value("Excom AI Template Need", n, "status") for n in stuck}, {"Open"})
		# the daily cap stops WhatsApp entirely; email needs still go
		frappe.db.set_value("Excom AI Template Need", newer, "status", "Open")
		email = templates.need(live, "accounts.payment_reminder", "Email", "en")
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 0)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		with patch.object(templates, "_working_agent", wraps=templates._working_agent) as working:
			self.assertIn(email, templates.dispatch())
		self.assertEqual({c.args[0].channel for c in working.call_args_list}, {"Email"},
			"WhatsApp needs are not even read once the cap is reached")
		# a paused business account is left out by the query, and its owners hear once
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 10)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		frappe.db.set_value("Excom Channel Account", self.lines[2], "wa_quality_rating", "RED")
		self.addCleanup(frappe.db.set_value, "Excom Channel Account", self.lines[2], "wa_quality_rating", "GREEN")
		self.assertNotIn(newer, templates.dispatch())
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", newer, "notified"), 1)
		# a retry that is not due yet waits
		frappe.db.set_value("Excom Channel Account", self.lines[2], "wa_quality_rating", "GREEN")
		frappe.db.set_value("Excom AI Template Need", newer, "retry_after", add_to_date(now_datetime(), hours=1))
		self.assertNotIn(newer, templates.dispatch())
		frappe.db.set_value("Excom AI Template Need", newer, "retry_after", add_to_date(now_datetime(), hours=-1))
		self.assertIn(newer, templates.dispatch())

	def test_the_library_uses_the_version_and_a_clash_falls_back_to_writing(self):
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		frappe.db.set_value("Excom AI Template Need", n, {"status": "Drafting", "version": 2})  # withdrawn once
		names = []
		self.assertIn("library", self.library(a, n, lambda lib, name, language, account_name="": names.append(name) or
			self.lib_row(name, account_name)))
		self.assertTrue(names[0].endswith("_v2"), names)
		self.wipe()
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		frappe.db.set_value("Excom AI Template Need", n, "status", "Drafting")

		def clash(*args, **kw):
			raise frappe.ValidationError("A template named xai_accounts_payment_reminder_en_123456 already exists here")

		self.assertIsNone(self.library(a, n, clash), "written instead")
		row = frappe.db.get_value("Excom AI Template Need", n, ["status", "version"], as_dict=True)
		self.assertEqual((row.status, row.version), ("Drafting", 2), "the written one gets the next name")

	def test_a_library_template_withdrawn_while_posting_is_taken_back(self):
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		frappe.db.set_value("Excom AI Template Need", n, "status", "Drafting")
		made = {}

		def create(lib, name, language, account_name=""):
			frappe.db.set_value("Excom AI Template Need", n, "status", "Withdrawn")  # a person, meanwhile
			made.update(self.lib_row(name, account_name))
			return made

		self.library(a, n, create)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, ["status", "template"]), ("Withdrawn", None))
		self.assertFalse(frappe.db.exists("WhatsApp Templates", made["name"]), "taken back on Meta too")

	def test_the_hourly_run_never_reads_the_old_rejection_during_a_rework(self):
		a = self.agent(0)
		need = self.submitted(a, "fr")
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "REJECTED", "rejection_reason": "INVALID_FORMAT",
			"rejected_at": add_to_date(now_datetime(), hours=-1)})
		frappe.db.set_value("Excom AI Template Need", need.name, {"status": "Drafting", "attempts": 1,
			"body": GOOD["body"].replace("friendly", "short")})  # reworded, so the edit goes to Meta
		seen = {}

		def post(*args, **kw):
			seen["template"] = frappe.db.get_value("WhatsApp Templates", need.template, "status")
			templates.statuses()  # the hourly run, while the edit is with Meta
			seen["need"] = frappe.db.get_value("Excom AI Template Need", need.name, "status")
			return {"success": True}

		with patch(META, side_effect=post):
			templates.submit(need.name)
		self.assertEqual(seen, {"template": "PENDING", "need": "Submitted"})
		# an old rejection arriving late is not this submission's verdict
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "REJECTED",
			"rejected_at": add_to_date(now_datetime(), hours=-1)})
		templates.statuses()
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", need.name, "status"), "Submitted")

	def test_an_approval_survives_a_stop(self):
		a = self.agent(0)
		frappe.db.set_single_value("Excom AI Settings", "whatsapp_auto_submit", 0)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		p = frappe.get_doc({"doctype": "Excom AI Proposal", "agent": a.name, "action": "approve_template", "status": "Executing",
			"subject_doctype": "Excom AI Template Need", "subject_name": n, "decided_by": OWNER})
		p.flags.ignore_validate = p.flags.ignore_links = True
		p.insert(ignore_permissions=True)
		frappe.db.set_value("Excom AI Template Need", n, {"status": "In review", "proposal": p.name, "samples": json.dumps(
			GOOD["samples"]), "variable_map": json.dumps(templates.NEEDS["accounts.payment_reminder"]["variables"])})
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "RED")
		self.addCleanup(frappe.db.set_value, "Excom Channel Account", self.lines[1], "wa_quality_rating", "GREEN")
		p.payload = json.dumps({"subject": "", "body": GOOD["body"]})
		templates.approve_template(p, a)
		row = frappe.db.get_value("Excom AI Template Need", n, ["status", "ready", "body"], as_dict=True)
		self.assertEqual((row.status, row.ready, row.body), ("Open", 1, GOOD["body"]), "the approved words wait")
		p.db_set("status", "Done")
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "GREEN")
		self.assertIn(n, templates.dispatch(), "the stop lifted")
		with patch(META, return_value={"id": f"MID{self.n}", "status": "PENDING"}), patch("requests.get") as get, \
				patch.object(runner, "ai") as ai:
			get.return_value.json.return_value, get.return_value.content, get.return_value.status_code = {"data": []}, b"{}", 200
			templates.template_draft(frappe._dict(name=None, subject_name=n), a)
		ai.assert_not_called()
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Submitted", "no new approval")
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"subject_name": n, "name": ["!=", p.name]}))

	def test_a_passing_meta_error_is_tried_again(self):
		a = self.agent(0)
		cases = {"en": requests.Timeout("read timed out"), "es": requests.HTTPError(response=SimpleNamespace(status_code=503)),
			"fr": requests.HTTPError(response=SimpleNamespace(status_code=429))}
		for lang, err in cases.items():
			n = templates.need(a, "accounts.payment_reminder", "WhatsApp", lang, self.lines[0])
			frappe.db.set_value("Excom AI Template Need", n, {"status": "Drafting", "body": GOOD["body"],
				"samples": json.dumps(GOOD["samples"]), "variable_map": json.dumps(templates.NEEDS["accounts.payment_reminder"][
					"variables"])})
			with patch(META, side_effect=err), patch("requests.get") as get:
				get.return_value.json.return_value, get.return_value.content, get.return_value.status_code = {"data": []}, b"{}", 200
				templates.submit(n)
			row = frappe.db.get_value("Excom AI Template Need", n, ["status", "ready", "retry_after", "submitted_on"], as_dict=True)
			self.assertEqual((row.status, row.ready), ("Open", 1), lang)
			self.assertGreater(row.retry_after, now_datetime())
			self.assertIsNone(row.submitted_on, "not counted against the daily cap")
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "de", self.lines[0])
		frappe.db.set_value("Excom AI Template Need", n, {"status": "Drafting", "body": GOOD["body"],
			"samples": json.dumps(GOOD["samples"])})
		with patch(META, side_effect=requests.HTTPError(response=SimpleNamespace(status_code=400))):
			templates.submit(n)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Failed", "a 4xx is Meta's refusal")

	def test_sales_checks_the_opt_in_when_it_writes(self):
		from excom.excom_ai import deals

		a = self.agent(0)
		need = self.submitted(a, "en")
		frappe.db.set_value("WhatsApp Templates", need.template, {"status": "APPROVED", "category": "UTILITY"})
		templates.statuses()
		frappe.db.set_value("Excom AI Template Need", need.name, "need_key", "sales.order_update")  # the same words will do
		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Tpl Deal {self.n}",
			"primary_phone": f"+5215552{self.n:04d}"}).insert(ignore_permissions=True)
		thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi.name, "channel": "whatsapp",
			"account_doctype": "Excom Channel Account", "account": self.lines[0], "thread_key": f"qa-tpl-deal-{self.n}",
			"status": "Open"}).insert(ignore_permissions=True)
		wa = frappe._dict(name=thread.name, account=self.lines[0])
		values = {"buyer_name": "Ana", "order": "SO-1", "status": "shipped"}
		with patch.object(templates, "find", return_value={"template": need.template, "text": "Hi {{1}}, order {{2}} is {{3}}.",
				"variables": templates.NEEDS["sales.order_update"]["variables"], "language": "en", "need": need.name}), \
				patch.object(proposals, "propose", return_value="P-OK") as propose:
			self.assertIsNone(deals.template_reply(None, a, "sales.order_update", wa, "en", values, subject=("Sales Order", "SO-1"),
				intent="order_update"), "no opt-in: the caller writes by email")
			propose.assert_not_called()
			from excom.excom.services.consent import record_consent

			record_consent(oi.name, "Granted", channel="whatsapp", purpose="transactional", source="manual")
			self.assertEqual(deals.template_reply(None, a, "sales.order_update", wa, "en", values, subject=("Sales Order", "SO-1"),
				intent="order_update"), "P-OK")

	def test_values_are_cleaned_and_rechecked_at_the_send(self):
		a = self.agent(0)
		need = self.submitted(a, "en")
		self.assertEqual(json.loads(send_rules.template_variables(need.template, json.dumps(["Ana\nMaria", "USD\t 10", "S-1"]),
			user=a.service_user)), ["Ana Maria", "USD 10", "S-1"], "a reviewer's newline would make Meta refuse")
		raw = json.dumps(["Ana\nMaria", "1", "2"])
		self.assertEqual(send_rules.template_variables(need.template, raw, user="Administrator"), raw, "a person's send untouched")
		frappe.db.set_value("WhatsApp Templates", need.template, "template", GOOD["body"] + " pad" * 220)
		self.assertRaisesRegex(send_rules.AgentSendRefused, "1024", send_rules.template_variables, need.template,
			["A", "x" * 150, "y" * 150], a.service_user)
		self.assertRaisesRegex(send_rules.AgentSendRefused, "empty", send_rules.template_variables, need.template,
			["A", "  ", "y"], a.service_user)

	def test_promotional_words_are_whole_phrases(self):
		spec = templates.NEEDS["accounts.payment_reminder"]
		ok = "Hello {{1}}, feel free to reply about {{2}} owed to XYZ Pvt Limited on invoices {{3}}. Thank you."
		self.assertEqual(templates.check_body(spec, "WhatsApp", ok, GOOD["samples"]), [])
		for promo in ("a limited time reminder", "a free gift with", "10% off if you pay"):
			problems = templates.check_body(spec, "WhatsApp", f"Hello {{{{1}}}}, {promo} {{{{2}}}} on invoices {{{{3}}}}. "
				"Thank you.", GOOD["samples"])
			self.assertIn("promotional", " ".join(problems), promo)

	def test_a_later_withdrawal_beats_an_earlier_message(self):
		from excom.excom.services.consent import record_consent

		oi = frappe.get_doc({"doctype": "Omni Identity", "display_name": f"QA Tpl Optin {self.n}",
			"primary_phone": f"+5215553{self.n:04d}"}).insert(ignore_permissions=True)
		thread = frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi.name, "channel": "whatsapp",
			"account_doctype": "Excom Channel Account", "account": self.lines[0], "thread_key": f"qa-tpl-optin-{self.n}",
			"status": "Open"}).insert(ignore_permissions=True)

		def inbound(when):
			m = frappe.get_doc({"doctype": "Excom Message", "thread": thread.name, "channel": "whatsapp", "account": self.lines[0],
				"account_doctype": "Excom Channel Account", "omni_identity": oi.name, "direction": "Inbound",
				"message_type": "Text", "content_text": "hi"})
			m.flags.ignore_validate = m.flags.ignore_mandatory = True
			m.insert(ignore_permissions=True)
			frappe.db.set_value("Excom Message", m.name, "creation", when, update_modified=False)

		inbound(add_to_date(now_datetime(), hours=-2))
		self.assertTrue(send_rules.whatsapp_opt_in([oi.name]), "they wrote to us")
		record_consent(oi.name, "Withdrawn", channel="whatsapp", purpose="transactional", source="manual")
		self.assertFalse(send_rules.whatsapp_opt_in([oi.name]), "a later withdrawal wins over an older message")
		self.assertFalse(send_rules.thread_opted_in(thread.name))
		inbound(add_to_date(now_datetime(), minutes=5))
		self.assertTrue(send_rules.whatsapp_opt_in([oi.name]), "they wrote again after it")
		record_consent(oi.name, "Withdrawn", channel="whatsapp", purpose="marketing", source="manual")
		self.assertTrue(send_rules.whatsapp_opt_in([oi.name]), "stopping promotions does not end utility messages")

	def test_a_shared_template_is_approved_by_an_admin_only(self):
		from excom.excom_ai import needs_you
		from excom.excom_ai.engine import registry

		a, b = self.agent(0), self.agent(1, purpose="Replies")
		frappe.db.set_value("Excom AI Agent", b.name, "owner_user", BACKUP)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		templates.need(b, "accounts.payment_reminder", "WhatsApp", "en", self.lines[1])
		frappe.db.set_value("Excom AI Template Need", n, {"status": "In review", "proposal": "P-S", "samples": json.dumps(
			GOOD["samples"])})
		p = frappe._dict(name="P-S", subject_doctype="Excom AI Template Need", subject_name=n, decided_by=OWNER,
			payload=json.dumps({"subject": "", "body": GOOD["body"]}), action="approve_template")
		self.assertRaisesRegex(registry.Cancel, "Excom Admin", templates.approve_template, p, a)
		frappe.set_user(OWNER)
		try:
			self.assertFalse(needs_you._may_approve_template(p), "not shown to one owner as theirs to approve")
		finally:
			frappe.set_user("Administrator")
		p.decided_by = "Administrator"
		with patch(META, return_value={"id": f"MID{self.n}", "status": "PENDING"}), patch("requests.get") as get:
			get.return_value.json.return_value, get.return_value.content, get.return_value.status_code = {"data": []}, b"{}", 200
			templates.approve_template(p, a)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Submitted")

	def test_polled_rejections_count_and_yellow_only_warns(self):
		a = self.agent(0)
		need = self.submitted(a, "en")
		with patch("requests.get") as get:
			get.return_value.status_code, get.return_value.content = 200, b"{}"
			get.return_value.json.return_value = {"status": "REJECTED", "category": "UTILITY", "rejected_reason": "INVALID_FORMAT"}
			templates._poll(need.template)
		self.assertTrue(frappe.db.get_value("WhatsApp Templates", need.template, "rejected_at"), "stamped as the webhook does")
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "YELLOW")
		self.addCleanup(frappe.db.set_value, "Excom Channel Account", self.lines[1], "wa_quality_rating", "GREEN")
		self.assertIsNone(templates.waba_stop(WABA), "YELLOW warns, it does not stop")
		self.assertIn("YELLOW", templates.waba_warning(WABA))
		frappe.db.set_value("Excom Channel Account", self.lines[1], "wa_quality_rating", "RED")
		self.assertIn("quality", templates.waba_stop(WABA))

	def test_try_again_is_capped_per_template(self):
		from excom.excom_ai import api

		a = self.agent(0)
		need = self.submitted(a, "en")
		frappe.db.set_value("Excom AI Template Need", need.name, {"status": "Failed", "attempts": 2,
			"total_attempts": templates.REOPEN_LIMIT})
		self.assertRaisesRegex(frappe.ValidationError, "withdraw", api.reopen_template_need, need.name)
		with patch("excom.excom.doctype.whatsapp_templates.whatsapp_templates.make_request"):
			api.withdraw_template(need.name)
		api.reopen_template_need(need.name)
		row = frappe.db.get_value("Excom AI Template Need", need.name, ["status", "attempts", "total_attempts"], as_dict=True)
		self.assertEqual((row.status, row.attempts, row.total_attempts), ("Open", 0, 0), "a new template starts afresh")
		frappe.db.set_value("Excom AI Template Need", need.name, {"status": "Failed", "total_attempts": 3})
		api.reopen_template_need(need.name)
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", need.name, "total_attempts"), 3, "never reset by Try again")

	def test_lookups_are_indexed(self):
		indexed = {r[0] for r in frappe.db.sql("""SELECT column_name FROM information_schema.statistics
			WHERE table_schema = DATABASE() AND table_name = 'tabExcom Message' AND seq_in_index = 1""")}
		self.assertTrue({"omni_identity", "template"} <= indexed, indexed)

	def test_the_templates_list_filters_and_decides_in_one_read(self):
		from excom.excom_ai import api

		a, b = self.agent(0), self.agent(1, purpose="Replies")
		frappe.db.set_value("Excom AI Agent", b.name, "owner_user", BACKUP)
		shared = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
		templates.need(b, "accounts.payment_reminder", "WhatsApp", "en", self.lines[1])
		own = templates.need(a, "accounts.payment_reminder", "Email", "en")
		decoy = templates.need(a, "purchase.po_chase", "Email", "en")
		self.addCleanup(lambda: frappe.db.delete("Excom AI Template Need", {"name": decoy}) or frappe.db.commit())
		frappe.db.set_value("Excom AI Template Need", decoy, {"agent": a.name + "9", "agents": f"{a.name}9, X{a.name}"})
		frappe.set_user(OWNER)
		real_get_doc = frappe.get_doc
		try:
			with patch("frappe.get_doc", side_effect=lambda *x, **k: self.fail("a read per row") if x and x[0] ==
					"Excom AI Template Need" else real_get_doc(*x, **k)):
				rows = {r.name: r for r in api.template_needs(agent=a.name)}
		finally:
			frappe.set_user("Administrator")
		self.assertEqual((rows[shared].can_change, rows[own].can_change), (False, True), "shared by two owners: an Excom Admin")
		self.assertNotIn(decoy, {r.name for r in api.template_needs(agent=a.name)},
			"an agent whose name merely contains this one's is not this one")
		frappe.db.set_value("Excom AI Template Need", decoy, {"agent": "X", "agents": f"X, {a.name}"})
		self.assertIn(decoy, {r.name for r in api.template_needs(agent=a.name)}, "named in the list of agents")
