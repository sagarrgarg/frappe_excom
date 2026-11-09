"""HLD-008, the Template Steward: needs, the checks, drafting and submission (Meta mocked), Meta's
verdicts, the per-account stops, email templates in review, finding and filling, and the send rules
for templates (approved, on the agent's number, opted in)."""

import json
from unittest.mock import patch

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
		self.assertEqual(templates.dispatch(), [], "no Live agent needs it")
		self.assertEqual(frappe.db.get_value("Excom AI Template Need", n, "status"), "Open")
		live = self.agent(1, purpose="Replies")
		templates.need(live, "accounts.payment_reminder", "WhatsApp", "de", self.lines[1])
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 0)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.assertEqual(templates.dispatch(), [], "the daily cap")
		frappe.db.set_single_value("Excom AI Settings", "template_submissions_per_day", 10)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
		self.assertIn(n, templates.dispatch())
		self.assertTrue(frappe.db.exists("Excom AI Task", {"agent": live.name, "kind": "template_draft"}), "on the Live agent")
		self.assertFalse(templates._claim(n), "claimed once")

	def test_the_library_first(self):
		a = self.agent(0)
		n = templates.need(a, "accounts.payment_reminder", "WhatsApp", "en", self.lines[0])
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
