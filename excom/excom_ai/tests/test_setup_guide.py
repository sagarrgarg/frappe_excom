"""HLD-010: the AI setup page. Steps computed from real records, the admin editor's field allow-lists,
the buttons (free key checks, a voice key shown once, the voice health address that never reaches
inside the network), going live, and who may use any of it."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.api import admin
from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import setup_guide

PLAIN = "qa.setup.plain@example.com"
COMPANY = None


def _steps():
	return {s["id"]: s for s in setup_guide.status()["steps"]}


class TestSetupGuide(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		purge_users(PLAIN)
		u = frappe.get_doc({"doctype": "User", "email": PLAIN, "first_name": "Plain", "send_welcome_email": 0})
		u.insert(ignore_permissions=True)
		u.add_roles("Excom Agent")
		cls.settings = frappe.get_single("Excom AI Settings").as_dict()
		cls.tools = {t.name: (t.enabled, t.last_error) for t in frappe.get_all("Excom AI Tool", fields=["name", "enabled", "last_error"])}
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Setup%"]}, pluck="name"):
			user = frappe.db.get_value("Excom AI Agent", name, "service_user")
			frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
			if user and frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=True, ignore_permissions=True)
		s = cls.settings
		frappe.db.set_single_value("Excom AI Settings", {"enabled": s.enabled, "daily_budget_total": s.daily_budget_total})
		doc = frappe.get_single("Excom AI Settings")
		doc.set("postal_addresses", [{"company": r.company, "address": r.address} for r in s.postal_addresses or []])
		doc.save(ignore_permissions=True)
		for name, (enabled, err) in cls.tools.items():
			frappe.db.set_value("Excom AI Tool", name, {"enabled": enabled, "last_error": err})
		purge_users(PLAIN)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Setup%"]}, pluck="name"):
			user = frappe.db.get_value("Excom AI Agent", name, "service_user")
			frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
			if user and frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	def agent(self, agent_type="Sales"):
		company = frappe.db.get_value("Company", {}, "name")
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Setup {agent_type}", "agent_type": agent_type,
			"owner_user": "Administrator", "company": company, "markets": [{"country": "India", "language": "en"}]}).insert(
			ignore_permissions=True)
		a.db_set("status", "Shadow")
		return frappe.get_doc("Excom AI Agent", a.name)

	# ---- access

	def test_only_admins(self):
		frappe.set_user(PLAIN)
		for fn, kwargs in ((setup_guide.status, {}), (setup_guide.test_tool, {"key": "hunter"}),
				(setup_guide.go_live, {"enable": 1}), (setup_guide.make_voice_key, {"confirm": 1})):
			with self.subTest(fn=fn.__name__), self.assertRaises(frappe.PermissionError):
				fn(**kwargs)
		with self.assertRaises(frappe.PermissionError):
			admin.get_schema("Excom AI Settings")

	def test_the_provider_test_is_for_system_managers(self):
		admin_user = "qa.setup.admin@example.com"
		purge_users(admin_user)
		u = frappe.get_doc({"doctype": "User", "email": admin_user, "first_name": "Adm", "send_welcome_email": 0}).insert(
			ignore_permissions=True)
		u.add_roles("Excom Admin")
		self.addCleanup(purge_users, admin_user)
		frappe.set_user(admin_user)
		self.assertFalse(setup_guide.status()["is_system_manager"])
		with self.assertRaises(frappe.PermissionError):
			setup_guide.test_provider()

	# ---- steps

	def test_steps_follow_the_agents_in_use(self):
		steps = _steps()
		self.assertEqual(list(steps), ["provider", "budgets", "basics", "email", "whatsapp", "tools", "signals", "voice",
			"agents"])
		self.assertIn(steps["voice"]["state"], ("optional", "done"))  # never blocks
		a = self.agent("Sales")
		steps = _steps()
		basics = steps["basics"]
		self.assertIn(f"Postal address for {a.company}", [i["label"] for i in basics["items"]])
		tools = steps["tools"]
		self.assertTrue(tools["needed"])
		self.assertIn("Sales: finding companies", [i["label"] for i in tools["items"]])
		self.assertTrue(steps["signals"]["needed"])
		self.assertIn("QA Setup Sales", [r["agent_name"] for r in steps["agents"]["agents"]])

	def test_postal_address_and_free_tools_make_their_items_pass(self):
		a = self.agent("Sales")
		doc = frappe.get_single("Excom AI Settings")
		doc.set("postal_addresses", [r for r in doc.postal_addresses if r.company != a.company])
		doc.append("postal_addresses", {"company": a.company, "address": "1 QA Street, Gurugram"})
		doc.save(ignore_permissions=True)
		for key in ("osm", "web_reader", "email_finder"):
			frappe.db.set_value("Excom AI Tool", key, "enabled", 0)
		items = {i["label"]: i for s in _steps().values() for i in s["items"]}
		self.assertTrue(items[f"Postal address for {a.company}"]["ok"])
		self.assertFalse(items["Sales: finding companies"]["ok"])  # free tools ship switched off
		for key in ("osm", "web_reader", "email_finder"):
			frappe.db.set_value("Excom AI Tool", key, "enabled", 1)
		items = {i["label"]: i for s in _steps().values() for i in s["items"]}
		self.assertTrue(items["Sales: finding companies"]["ok"])
		self.assertTrue(items["Sales: reading websites"]["ok"])

	def test_status_makes_no_network_call(self):
		self.agent("Sales")
		with patch("requests.get", side_effect=AssertionError("network")), patch("requests.post",
				side_effect=AssertionError("network")), patch("socket.getaddrinfo", side_effect=AssertionError("dns")):
			setup_guide.status()

	# ---- the admin editor's allow-lists

	def test_the_editor_reads_and_writes_only_the_listed_fields(self):
		fields = {f["fieldname"] for s in admin.get_schema("Excom AI Settings")["sections"] for f in s["fields"]}
		self.assertIn("postal_addresses", fields)
		for hidden in ("enabled", "ai_identity_policy", "tasks_per_tick"):
			self.assertNotIn(hidden, fields)
		self.assertNotIn("enabled", admin.get_doc("Excom AI Settings"))
		frappe.db.set_single_value("Excom AI Settings", "enabled", 0)
		admin.save_doc("Excom AI Settings", {"enabled": 1, "daily_budget_total": 7})
		self.assertEqual(frappe.db.get_single_value("Excom AI Settings", "enabled"), 0)
		self.assertEqual(frappe.db.get_single_value("Excom AI Settings", "daily_budget_total"), 7)
		before = frappe.db.get_value("Excom AI Tool", "hunter", "used_this_month")
		admin.save_doc("Excom AI Tool", {"used_this_month": 999, "monthly_budget": 5}, name="hunter")
		self.assertEqual(frappe.db.get_value("Excom AI Tool", "hunter", "used_this_month"), before)  # spend is never set here
		self.assertEqual(admin.get_doc("Excom AI Tool", "hunter").get("api_key"), admin.get_doc("Excom AI Tool", "hunter")
			.get("api_key"))  # masked either way: "" or "__SET__"
		self.assertIn(admin.get_doc("Excom AI Tool", "hunter").get("api_key"), ("", "__SET__"))

	# ---- buttons

	def test_free_key_check_never_counts_against_the_budget(self):
		doc = frappe.get_doc("Excom AI Tool", "zerobounce")
		doc.api_key = "qa-key"
		doc.save(ignore_permissions=True)
		used = frappe.db.get_value("Excom AI Tool", "zerobounce", "used_this_month")
		resp = MagicMock(status_code=200)
		resp.json.return_value = {"Credits": "-1"}
		with patch("requests.get", return_value=resp):
			out = setup_guide.test_tool("zerobounce")
		self.assertFalse(out["ok"])  # ZeroBounce answers a bad key with 200 and Credits -1
		resp.json.return_value = {"Credits": "1500"}
		with patch("requests.get", return_value=resp):
			out = setup_guide.test_tool("zerobounce")
		self.assertTrue(out["ok"])
		self.assertIn("1500", out["detail"])
		self.assertEqual(frappe.db.get_value("Excom AI Tool", "zerobounce", "used_this_month"), used)
		self.assertIsNone(setup_guide.test_tool("serper")["ok"])  # no free check: tested on first use

	def test_a_voice_key_is_made_once_and_saved(self):
		with self.assertRaises(frappe.ValidationError):
			setup_guide.make_voice_key()
		key = setup_guide.make_voice_key(confirm=1)["key"]
		self.assertEqual(len(key), 48)
		self.assertEqual(frappe.get_single("Excom AI Settings").get_password("voice_service_key"), key)

	def test_the_voice_health_address_stays_public(self):
		public = [(None, None, None, None, ("93.184.216.34", 443))]
		private = [(None, None, None, None, ("10.0.0.5", 443))]
		with patch.dict(frappe.conf, {"developer_mode": 0}):
			with patch("socket.getaddrinfo", return_value=public):
				self.assertEqual(setup_guide._health_url("wss://voice.example.com/stream"),
					"https://voice.example.com/health?frappe=1")
				with self.assertRaises(frappe.ValidationError):
					setup_guide._health_url("ws://voice.example.com/stream")  # plain ws only for local development
			with patch("socket.getaddrinfo", return_value=private), self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("wss://voice.example.com/stream")
			with self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("wss://127.0.0.1/stream")
			with self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("file:///etc/passwd")

	def test_going_live_lists_what_is_missing_first(self):
		frappe.db.set_single_value("Excom AI Settings", "enabled", 0)
		with patch.object(setup_guide, "status", return_value={"missing": ["AI provider"]}):
			out = setup_guide.go_live(enable=1)
			self.assertEqual((out["enabled"], out["missing"]), (0, ["AI provider"]))
			self.assertEqual(frappe.db.get_single_value("Excom AI Settings", "enabled"), 0)
			self.assertEqual(setup_guide.go_live(enable=1, confirm=1)["enabled"], 1)
		self.assertEqual(frappe.db.get_single_value("Excom AI Settings", "enabled"), 1)
		setup_guide.go_live(enable=0)
		self.assertEqual(frappe.db.get_single_value("Excom AI Settings", "enabled"), 0)

	def test_dns_check_only_for_a_mailbox_on_that_agent(self):
		a = self.agent("Sales")
		with self.assertRaises(frappe.ValidationError):
			setup_guide.check_dns(a.name, "not-a-row")

	def test_hunter_and_prospeo_key_checks(self):
		from excom.excom_ai.tools import email as em
		from excom.excom_ai.tools import people as pt

		record = frappe._dict(name="hunter", get_password=lambda f, raise_exception=False: "secret-key")
		ok = MagicMock(status_code=200)
		ok.json.return_value = {"data": {"plan_name": "Starter", "requests": {"searches": {"used": 10, "available": 500}}}}
		with patch.object(em.requests, "get", return_value=ok) as get:
			out = em.Hunter(record).ping()
		self.assertTrue(out["ok"])
		self.assertIn("490", out["detail"])
		self.assertEqual(get.call_args.args[0], "https://api.hunter.io/v2/account")
		bad = MagicMock(status_code=401)
		bad.json.return_value = {"errors": [{"details": "secret-key is invalid"}]}
		with patch.object(em.requests, "get", return_value=bad):
			out = em.Hunter(record).ping()
		self.assertFalse(out["ok"])
		self.assertNotIn("secret-key", out["detail"])
		resp = MagicMock(status_code=200, content=b"{}")
		resp.json.return_value = {"error": False, "response": {"remaining_credits": 1234}}
		with patch.object(pt.requests, "get", return_value=resp) as get:
			out = pt.Prospeo(record).ping()
		self.assertTrue(out["ok"])
		self.assertIn("1234", out["detail"])
		self.assertEqual(get.call_args.kwargs["headers"]["X-KEY"], "secret-key")
		resp.status_code = 400
		resp.json.return_value = {"error": True, "error_code": "INVALID_API_KEY"}
		with patch.object(pt.requests, "get", return_value=resp):
			self.assertFalse(pt.Prospeo(record).ping()["ok"])

	def test_the_editor_cannot_delete_or_create_ai_records(self):
		with self.assertRaises(frappe.PermissionError):
			admin.delete_doc("Excom AI Settings", "Excom AI Settings")
		with self.assertRaises(frappe.PermissionError):
			admin.delete_doc("Excom AI Tool", "hunter")
		with self.assertRaises(frappe.PermissionError):
			admin.save_doc("Excom AI Tool", {"tool_key": "qa-junk", "label": "Junk"})
		self.assertTrue(frappe.db.exists("Excom AI Tool", "hunter"))

	def test_the_voice_check_refuses_odd_addresses(self):
		cgnat = [(None, None, None, None, ("100.100.100.200", 443))]  # a cloud metadata address, not private
		with patch.dict(frappe.conf, {"developer_mode": 0}):
			with patch("socket.getaddrinfo", return_value=cgnat), self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("wss://voice.example.com/stream")
			with self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("wss://user:pw@voice.example.com/stream")
			with self.assertRaises(frappe.ValidationError):
				setup_guide._health_url("wss://voice.example.com:99999/stream")

	def test_the_voice_check_connects_to_the_address_it_checked(self):
		import urllib3

		frappe.db.set_single_value("Excom AI Settings", "voice_service_url", "wss://voice.example.com/stream")
		public = [(None, None, None, None, ("93.184.216.34", 443))]
		resp = MagicMock(status=200)
		resp.read.return_value = b"ok\ncalls: 0/20"
		with patch.dict(frappe.conf, {"developer_mode": 0}), patch("socket.getaddrinfo", return_value=public), \
				patch.object(urllib3, "HTTPSConnectionPool") as pool:
			pool.return_value.urlopen.return_value = resp
			out = setup_guide.check_voice()
		self.assertTrue(out["ok"])
		self.assertEqual(pool.call_args.args[0], "93.184.216.34")  # the checked address, never a second lookup
		self.assertEqual(pool.call_args.kwargs["server_hostname"], "voice.example.com")
		self.assertEqual(pool.return_value.urlopen.call_args.kwargs["headers"]["Host"], "voice.example.com")
		self.assertFalse(pool.return_value.urlopen.call_args.kwargs["redirect"])

	def test_a_sales_agent_without_a_mailbox_is_flagged(self):
		self.agent("Sales")
		email = _steps()["email"]
		self.assertNotEqual(email["state"], "not_needed")
		self.assertFalse(next(i for i in email["items"] if "mailbox to write from" in i["label"])["ok"])
