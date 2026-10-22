"""Excom Ads billing visibility, top-up ledger and budget plans. Providers are faked; no network.

  - reported figures stored exactly as reported, with an availability status each; Unsupported /
    Permission Missing / Not Set never come out of the API as 0 (acceptance test 3)
  - headroom, 7-day average spend, days left and exhaustion date
  - one snapshot row per account per day across repeated hourly reads
  - a failed read keeps the last good value as Stale; an old reading turns Stale
  - ledger estimate; Low Headroom (cap, month-end exhaustion, ledger) raised and resolved
  - budget plans: maker-checker approval, amendments, overlap, plan vs actual, Overspend alert
  - scope: company A users never see company B billing or plans; no view_finance → no billing

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_billing'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_first_day, get_last_day, getdate, now_datetime

from excom.excom_ads import billing, budgets, dashboard, permissions
from excom.excom_ads.naming import billing_snapshot_name
from excom.excom_ads.providers import meta as meta_provider
from excom.excom_ads.providers.base import AdsProvider, AdsProviderError, PERMISSION
from excom.excom_ads.providers.google_ads import GoogleAdsProvider
from excom.excom_ads.providers.meta import MetaAdsProvider
from excom.excom_ads.sync import account_today
from excom.excom_ads.tests.test_ads_permissions import COMPANY_B, _ensure_company_b

P = "QA Billing"
WS_A, WS_B = f"{P} WS A", f"{P} WS B"
BRAND_A = f"{P} Brand A"
META_ACT, META_ACT_B, GOOGLE_CID = "act_8880001", "act_8880002", "8880000001"
U_FIN = "qa_billing_fin@example.com"  # marketing + view_finance, company A
U_MKT = "qa_billing_mkt@example.com"  # marketing, company A, no finance right
U_MAKER = "qa_billing_maker@example.com"  # marketing + manage_budgets, company A
U_CHECKER = "qa_billing_checker@example.com"  # marketing + manage_budgets, company A
U_ACC = "qa_billing_accounts@example.com"  # Accounts User, company A
U_B = "qa_billing_b@example.com"  # marketing + view_finance + manage_budgets, company B
USERS = [U_FIN, U_MKT, U_MAKER, U_CHECKER, U_ACC, U_B]
AUG = "2026-08"


class FakeGraph:
	"""Answers the two Graph reads read_billing makes: the money fields and the funding fields."""

	def __init__(self):
		self.reset()

	def reset(self):
		self.account = {"amount_spent": "500000", "spend_cap": "1000000", "balance": "12345", "currency": None}
		self.funding = "ok"  # ok | permission | prepay_only | error
		self.calls = []

	def __call__(self, url, params):
		fields = (params or {}).get("fields", "")
		self.calls.append(fields)
		if fields == meta_provider.BILLING_CORE_FIELDS:
			return 200, dict(self.account), {}
		denied = (400, {"error": {"code": 200, "message": "(#200) Requires MANAGE task on the ad account"}}, {})
		if fields == meta_provider.FUNDING_FIELDS:
			if self.funding in ("permission", "prepay_only"):
				return denied
			if self.funding == "error":
				return 500, {"error": {"code": 2, "message": "Service temporarily unavailable", "is_transient": True}}, {}
			return 200, {"is_prepay_account": True, "funding_source_details": {"id": "fs1", "display_string": "Stored balance", "type": 20}}, {}
		if fields == "is_prepay_account":
			if self.funding == "permission":
				return denied
			return 200, {"is_prepay_account": False}, {}
		raise AssertionError(f"unexpected Graph read {url} {fields}")


class FakeGoogleClient:
	def __init__(self):
		self.billing_setup, self.account_budget, self.fail = [], [], None

	def search(self, customer_id, query):
		if self.fail:
			raise self.fail
		if "FROM billing_setup" in query:
			return [{"billingSetup": r} for r in self.billing_setup]
		if "FROM account_budget" in query:
			return [{"accountBudget": r} for r in self.account_budget]
		raise AssertionError(query)


def _meta(acc, graph) -> MetaAdsProvider:
	p = MetaAdsProvider.__new__(MetaAdsProvider)
	AdsProvider.__init__(p, acc)
	p._token, p.scopes, p.version, p.max_pages, p.currency = "QA_TOKEN", set(), "v26.0", 10, acc.currency
	meta_provider.http_get = graph
	return p


def _google(acc, client) -> GoogleAdsProvider:
	p = GoogleAdsProvider.__new__(GoogleAdsProvider)
	AdsProvider.__init__(p, acc)
	p.client, p.customer_id, p.currency, p.timezone = client, acc.external_id, acc.currency, acc.timezone_name
	return p


def _all_figures(row: dict):
	for group in ("reported", "calculated"):
		for k, f in (row.get(group) or {}).items():
			if isinstance(f, dict) and "status" in f:
				yield f"{group}.{k}", f


class BillingFixtures(FrappeTestCase):
	"""Accounts, metric snapshots, users and rights shared by the billing and budget tests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		_ensure_company_b()
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS_A, "company": cls.company})
		ins({"doctype": "Ads Workspace", "workspace_name": WS_B, "company": COMPANY_B})
		ins({"doctype": "Ads Brand", "brand_name": BRAND_A, "workspace": WS_A})
		common = {"timezone_name": "Asia/Kolkata", "currency": cls.cur, "sync_enabled": 0}
		cls.meta = ins({"doctype": "Ads Account", "account_name": f"{P} Meta", "provider": "Meta", "external_id": META_ACT, "workspace": WS_A, **common}).name
		cls.google = ins({"doctype": "Ads Account", "account_name": f"{P} Google", "provider": "Google", "external_id": GOOGLE_CID, "workspace": WS_A, **common}).name
		cls.meta_b = ins({"doctype": "Ads Account", "account_name": f"{P} Meta B", "provider": "Meta", "external_id": META_ACT_B, "workspace": WS_B, **common}).name
		cls.today = account_today(frappe.get_doc("Ads Account", cls.meta))
		for acc in (cls.meta, cls.google, cls.meta_b):
			frappe.db.set_value("Ads Account", acc, {"metrics_synced_from": add_days(cls.today, -30), "metrics_synced_until": cls.today})

		def snap(acc, date, spend, level="Account", ext=None, brand=None, leads=0):
			a = frappe.get_doc("Ads Account", acc)
			ins({"doctype": "Ads Metric Snapshot", "account": acc, "workspace": a.workspace, "company": a.company, "provider": a.provider, "brand": brand, "level": level,
				"entity_external_id": ext or a.external_id, "date": date, "currency": cls.cur, "company_currency": cls.cur, "exchange_rate": 1.0, "spend": spend, "spend_company_currency": spend, "leads": leads})

		# Last 7 complete days: 100 a day on the Meta account (company B's account too).
		for i in range(1, 8):
			snap(cls.meta, add_days(cls.today, -i), 100)
			snap(cls.meta_b, add_days(cls.today, -i), 100)
		# A fixed past month for plan vs actual: Meta 1000 on Aug 5 and 1500 on Aug 20.
		snap(cls.meta, f"{AUG}-05", 1000, leads=20)
		snap(cls.meta, f"{AUG}-20", 1500, leads=5)
		snap(cls.meta_b, f"{AUG}-05", 9999)
		# September: account total 900, of which a Brand A campaign 400 and an unallocated one 500.
		snap(cls.meta, "2026-09-10", 900)
		snap(cls.meta, "2026-09-10", 400, level="Campaign", ext="c-brand", brand=BRAND_A)
		snap(cls.meta, "2026-09-10", 500, level="Campaign", ext="c-none")

		for u in USERS:
			roles = ["Accounts User"] if u == U_ACC else ["Excom Marketing"]
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": r} for r in roles]})
			ins({"doctype": "User Permission", "user": u, "allow": "Company", "for_value": COMPANY_B if u == U_B else cls.company, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_FIN, "view_finance": 1})
		ins({"doctype": "Ads Access Profile", "user": U_MAKER, "manage_budgets": 1})
		ins({"doctype": "Ads Access Profile", "user": U_CHECKER, "manage_budgets": 1})
		ins({"doctype": "Ads Access Profile", "user": U_B, "view_finance": 1, "manage_budgets": 1})
		frappe.db.commit()
		cls.graph = FakeGraph()
		cls._orig_http = meta_provider.http_get

	@classmethod
	def tearDownClass(cls):
		meta_provider.http_get = cls._orig_http
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		plans = frappe.get_all("Ads Budget Plan", {"plan_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		for dt in ("Ads Metric Snapshot", "Ads Billing Snapshot", "Ads Billing Record", "Ads Alert", "Ads Sync Job"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Alert", {"reference_name": ["in", plans]})
		frappe.db.delete("Ads Budget Plan Line", {"parent": ["in", plans]})
		frappe.db.delete("Ads Budget Plan", {"name": ["in", plans]})
		frappe.db.delete("Version", {"ref_doctype": ["in", ["Ads Budget Plan", "Ads Billing Record", "Ads Account"]], "docname": ["in", plans + accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", USERS]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _as(self, user):
		frappe.set_user(user)
		permissions.clear_cache()


class TestAdsBilling(BillingFixtures):
	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		self.graph.reset()
		frappe.db.delete("Ads Billing Snapshot", {"account": ["in", [self.meta, self.google, self.meta_b]]})
		frappe.db.delete("Ads Billing Record", {"account": ["in", [self.meta, self.google, self.meta_b]]})
		frappe.db.delete("Ads Alert", {"account": ["in", [self.meta, self.google, self.meta_b]]})
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _acc(self, name):
		return frappe.get_doc("Ads Account", name)

	def _read_meta(self, name=None):
		acc = self._acc(name or self.meta)
		return billing.read_and_record(acc, _meta(acc, self.graph))

	def _snap(self, name=None):
		return frappe.get_doc("Ads Billing Snapshot", billing_snapshot_name(name or self.meta, self.today))

	def _row(self, name=None, **filters):
		return {r["account"]: r for r in billing.summary(filters or {"workspace": WS_A})}[name or self.meta]

	def _open(self, key):
		return frappe.db.get_value("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}, ["name", "severity", "alert_type", "title"], as_dict=True)

	# ─── reported + calculated ────────────────────────────────────────────────

	def test_01_meta_reading_is_stored_as_reported_with_calculations(self):
		v = self._read_meta()
		self.assertIsNotNone(v)
		s = self._snap()
		self.assertEqual((s.reported_amount_spent, s.reported_spend_cap, s.reported_amount_due), (5000.0, 10000.0, 123.45), "minor units → rupees")
		self.assertEqual((s.amount_spent_avail, s.spend_cap_avail, s.amount_due_avail, s.prepay_avail, s.funding_avail), ("Available",) * 5)
		self.assertEqual((s.reported_is_prepay, s.reported_funding_type, s.payment_model, s.payment_model_avail), (1, "STORED_BALANCE", "Prepaid", "Available"))
		self.assertEqual((s.account_budget_avail, s.billing_setup_avail), ("Unsupported", "Unsupported"), "Google-only figures are Unsupported on Meta")
		self.assertEqual((s.calc_headroom, s.headroom_avail), (5000.0, "Available"))
		self.assertEqual((s.calc_avg_daily_spend, s.calc_avg_days, s.avg_spend_avail), (100.0, 7, "Available"))
		self.assertEqual((s.calc_days_left, s.days_left_avail), (50.0, "Available"))
		self.assertEqual(getdate(s.calc_exhaustion_date), add_days(self.today, 50))
		self.assertEqual((s.company, s.workspace, s.provider, s.read_status), (self.company, WS_A, "Meta", "OK"))
		acc = self._acc(self.meta)
		self.assertEqual((acc.reported_amount_spent, acc.reported_spend_cap, acc.has_spend_cap, acc.reported_amount_due, acc.billing_status, acc.payment_model, acc.can_billing), (5000.0, 10000.0, 1, 123.45, "Reported", "Prepaid", 1), "Ads Account keeps the latest reported figures")
		self.assertNotIn("QA_TOKEN", s.raw_json or "")
		row = self._row()
		self.assertEqual(row["reported"]["amount_due"], {"value": 123.45, "status": "Available"})
		self.assertEqual(row["calculated"]["days_left"]["value"], 50.0)
		self.assertEqual((row["headroom"], row["spend_cap"], row["amount_due"]), (5000.0, 10000.0, 123.45))

	def test_02_hourly_reads_update_one_row_per_day(self):
		self._read_meta()
		first = self._snap()
		self._read_meta()
		self._read_meta()
		self.assertEqual(frappe.db.count("Ads Billing Snapshot", {"account": self.meta}), 1, "an unchanged reading never adds a row")
		again = self._snap()
		self.assertEqual(again.read_count, 3)
		self.assertEqual(str(again.modified), str(first.modified), "identical readings do not touch modified")
		self.graph.account["amount_spent"] = "510000"
		self._read_meta()
		changed = self._snap()
		self.assertEqual((changed.reported_amount_spent, changed.calc_headroom, changed.read_count), (5100.0, 4900.0, 4))
		self.assertEqual(frappe.db.count("Ads Billing Snapshot", {"account": self.meta}), 1)

	def test_03_permission_missing_and_unsupported_are_never_zero(self):
		"""Acceptance test 3: a figure that is not available is never shown as 0."""
		self.graph.funding = "permission"
		self._read_meta()
		s = self._snap()
		self.assertEqual((s.prepay_avail, s.funding_avail, s.payment_model_avail), ("Permission Missing",) * 3)
		self.assertEqual((s.amount_spent_avail, s.read_status), ("Available", "OK"), "a field-level permission error does not fail the read")
		client = FakeGoogleClient()  # not on monthly invoicing: no billing setup, no budget
		g = self._acc(self.google)
		billing.read_and_record(g, _google(g, client))
		gs = self._snap(self.google)
		self.assertEqual((gs.billing_setup_avail, gs.account_budget_avail, gs.payment_model_avail, gs.headroom_avail, gs.read_status), ("Unsupported",) * 4 + ("OK",), "non-invoiced Google accounts are Unsupported, never an error")
		self.assertEqual(self._acc(self.google).billing_status, "Not Available")
		self._as(U_FIN)
		out = billing.get_billing(workspace=WS_A)
		rows = {r["account"]: r for r in out["accounts"]}
		m, gr = rows[self.meta], rows[self.google]
		self.assertEqual(m["reported"]["is_prepay"], {"value": None, "status": "Permission Missing"})
		self.assertEqual(m["reported"]["funding_type"], {"value": None, "status": "Permission Missing"})
		self.assertEqual(gr["reported"]["amount_due"], {"value": None, "status": "Unsupported"})
		self.assertIsNone(gr["reported"]["account_budget"]["value"])
		for row in (m, gr):
			for key, f in _all_figures(row):
				if f["status"] not in ("Available", "Stale"):
					self.assertIsNone(f["value"], f"{row['account']} {key}: {f['status']} must not carry a value")
		self.assertEqual((gr["amount_spent"], gr["spend_cap"], gr["headroom"], gr["amount_due"]), (None, None, None, None))
		self.assertNotIn('"value": 0', json.dumps(gr["reported"], default=str), "nothing unavailable leaks as a zero")
		self.assertEqual(gr["billing_status"], "Not Available", "finance fields reach a view_finance user")
		self.assertEqual(gr["calculated"]["avg_daily_spend"], {"value": 0.0, "status": "Available", "days": 7}, "synced days without spend are a real zero")
		# The flag alone needs less access than funding_source_details: it is still read.
		self.graph.funding = "prepay_only"
		frappe.set_user("Administrator")
		self._read_meta()
		s = self._snap()
		self.assertEqual((s.prepay_avail, s.reported_is_prepay, s.funding_avail, s.payment_model_avail), ("Available", 0, "Permission Missing", "Permission Missing"))

	def test_04_no_cap_is_not_set_not_zero(self):
		self.graph.account["spend_cap"] = "0"
		self._read_meta()
		s = self._snap()
		self.assertEqual((s.spend_cap_avail, s.headroom_avail, s.days_left_avail, s.exhaustion_avail), ("Not Set",) * 4)
		row = self._row()
		self.assertEqual((row["spend_cap"], row["headroom"]), (None, None))
		self.assertEqual(row["calculated"]["days_left"], {"value": None, "status": "Not Set"})
		self.assertFalse(self._acc(self.meta).has_spend_cap)

	def test_05_google_monthly_invoicing_account_budget(self):
		client = FakeGoogleClient()
		client.billing_setup = [{"id": "5", "status": "CANCELLED"}, {"id": "6", "status": "APPROVED"}]
		client.account_budget = [
			{"id": "70", "name": "QA old", "status": "APPROVED", "approvedSpendingLimitMicros": "1000000000", "adjustedSpendingLimitMicros": "1000000000", "amountServedMicros": "1000000000", "approvedStartDateTime": "2025-01-01 00:00:00", "approvedEndDateTime": "2025-12-31 23:59:59", "approvedEndTimeType": None},
			{"id": "71", "name": "QA pending", "status": "PENDING", "approvedStartDateTime": "2026-01-01 00:00:00"},
			{"id": "72", "name": "QA running", "status": "APPROVED", "approvedSpendingLimitMicros": "60000000000", "adjustedSpendingLimitMicros": "55000000000", "amountServedMicros": "54800000000", "totalAdjustmentsMicros": "-5000000000", "approvedStartDateTime": "2026-01-01 00:00:00", "approvedEndTimeType": "FOREVER"},
		]
		g = self._acc(self.google)
		billing.read_and_record(g, _google(g, client))
		s = self._snap(self.google)
		self.assertEqual((s.reported_budget_id, s.reported_budget_status, s.reported_budget_end, s.account_budget_avail), ("72", "APPROVED", "FOREVER", "Available"), "the running approved budget is picked")
		self.assertEqual((s.reported_approved_limit, s.reported_adjusted_limit, s.reported_amount_served, s.reported_total_adjustments), (60000.0, 55000.0, 54800.0, -5000.0))
		self.assertEqual((s.reported_billing_setup_status, s.billing_setup_avail, s.payment_model, s.payment_model_avail), ("APPROVED", "Available", "Monthly Invoicing", "Available"))
		self.assertEqual((s.calc_headroom, s.headroom_avail), (200.0, "Available"), "adjusted limit − amount served")
		self.assertEqual((s.amount_spent_avail, s.amount_due_avail, s.prepay_avail), ("Unsupported",) * 3)
		self.assertEqual(s.days_left_avail, "No Data", "no synced results for this account → no projection")
		acc = self._acc(self.google)
		self.assertEqual((acc.billing_status, acc.reported_spend_cap, acc.reported_amount_spent, acc.has_spend_cap, acc.can_billing, acc.payment_model), ("Reported", 55000.0, 54800.0, 1, 1, "Monthly Invoicing"))
		# Unlimited budget: a budget exists, its limit does not.
		client.account_budget = [{"id": "73", "name": "QA unlimited", "status": "APPROVED", "approvedSpendingLimitType": "INFINITE", "adjustedSpendingLimitType": "INFINITE", "amountServedMicros": "100000000", "approvedStartDateTime": "2026-01-01 00:00:00", "approvedEndTimeType": "FOREVER"}]
		billing.read_and_record(acc, _google(acc, client))
		s = self._snap(self.google)
		self.assertEqual((s.account_budget_avail, s.headroom_avail), ("Available", "Not Set"))
		self.assertTrue(self._row(self.google)["reported"]["account_budget"]["value"]["unlimited"])
		# A permission error on billing resources: Permission Missing, the read goes on.
		client.fail = AdsProviderError("Google Ads HTTP 403 PERMISSION_DENIED", PERMISSION)
		billing.read_and_record(acc, _google(acc, client))
		s = self._snap(self.google)
		self.assertEqual((s.billing_setup_avail, s.account_budget_avail, s.payment_model_avail), ("Permission Missing",) * 3)

	def test_06_failed_read_keeps_last_value_as_stale_and_old_reading_turns_stale(self):
		self._read_meta()
		self.graph.funding = "error"
		self._read_meta()
		s = self._snap()
		self.assertEqual((s.prepay_avail, s.reported_is_prepay, s.funding_avail, s.reported_funding_type), ("Stale", 1, "Stale", "STORED_BALANCE"), "a failed read keeps the last good value, marked Stale")
		self.assertEqual(s.read_status, "Partial")
		self.assertIn("temporarily unavailable", s.last_error)
		self.assertEqual(self._row()["reported"]["is_prepay"], {"value": True, "status": "Stale"})
		frappe.db.set_value("Ads Billing Snapshot", s.name, "reported_as_of", add_to_date(now_datetime(), hours=-10))
		row = self._row()
		self.assertTrue(row["stale"])
		self.assertEqual(row["reported"]["amount_spent"]["status"], "Stale", "older than billing_stale_hours → Stale")
		self.assertEqual(row["reported"]["amount_spent"]["value"], 5000.0, "a stale value is still shown, labelled")

	# ─── alerts ───────────────────────────────────────────────────────────────

	def test_07_low_headroom_alert_raised_and_resolved(self):
		self.graph.account["spend_cap"] = "520000"  # 200 left at 100 a day: 2 days
		self._read_meta()
		a = self._open(billing.low_headroom_key(self.meta))
		self.assertEqual((a.alert_type, a.severity), ("Low Headroom", "Medium"))
		self.assertFalse(self._open(billing.cap_exhaustion_key(self.meta)), "the low-headroom alert covers month-end exhaustion")
		self.graph.account["spend_cap"] = "500000"  # cap reached
		self._read_meta()
		self.assertEqual(self._open(billing.low_headroom_key(self.meta)).severity, "Critical")
		self.assertEqual(frappe.db.count("Ads Alert", {"dedupe_key": billing.low_headroom_key(self.meta)}), 1, "deduped")
		# A failed read does not clear an alert it cannot check.
		acc = self._acc(self.meta)
		broken = _meta(acc, self.graph)
		broken.read_billing = lambda: (_ for _ in ()).throw(AdsProviderError("Meta 500 (code 2): try later", "Retryable"))
		billing.read_and_record(acc, broken)
		self.assertEqual(self._snap().headroom_avail, "Stale")
		self.assertTrue(self._open(billing.low_headroom_key(self.meta)), "unknown is not cleared")
		self.graph.account["spend_cap"] = "5000000"
		self._read_meta()
		self.assertFalse(self._open(billing.low_headroom_key(self.meta)), "a fresh cap reading with 450 days left clears it")

	def test_08_cap_exhaustion_before_month_end(self):
		self.graph.account["spend_cap"] = "540000"  # 400 left: 4 days
		self._read_meta()
		before_end = add_days(self.today, 4) <= get_last_day(self.today)
		self.assertEqual(bool(self._open(billing.cap_exhaustion_key(self.meta))), before_end)
		self.assertFalse(self._open(billing.low_headroom_key(self.meta)))
		self.graph.account["spend_cap"] = "0"
		self._read_meta()
		self.assertFalse(self._open(billing.cap_exhaustion_key(self.meta)), "no cap any more: resolved")

	# ─── ledger ───────────────────────────────────────────────────────────────

	def _record(self, record_type, amount, days_ago, reference=None, user=None, account=None):
		if user:
			self._as(user)
		doc = frappe.get_doc({"doctype": "Ads Billing Record", "account": account or self.meta, "record_type": record_type, "amount": amount, "posting_date": add_days(self.today, -days_ago), "reference": reference}).insert()
		frappe.set_user("Administrator")
		permissions.clear_cache()
		return doc

	def test_09_ledger_estimate_and_its_alert(self):
		self.graph.account["spend_cap"] = "0"
		self._read_meta()
		self._record("Top-up", 1000, 5, "RCPT-1", user=U_ACC)
		self._record("Top-up", 500, 3, "RCPT-2", user=U_ACC)
		self._record("Refund", 100, 2, user=U_ACC)
		led = self._row()["ledger"]
		self.assertEqual(led["label"], "Estimated from our ledger")
		self.assertEqual((led["topups"], led["refunds"], led["spend_since"], led["balance"], led["records"]), (1500.0, 100.0, 500.0, 900.0, 3), "1500 − 100 − 5 days × 100")
		self.assertEqual((led["avail"], led["days_left"], led["since"]), ("Available", 9.0, str(add_days(self.today, -5))))
		self.assertFalse(self._open(billing.ledger_low_key(self.meta)))
		adj = self._record("Adjustment", -700, 1, user=U_ACC)
		a = self._open(billing.ledger_low_key(self.meta))
		self.assertTrue(a, "a top-up change re-checks the ledger alert at once")
		self.assertIn("estimated from our ledger", a.title)
		frappe.delete_doc("Ads Billing Record", adj.name)
		self.assertFalse(self._open(billing.ledger_low_key(self.meta)), "resolved when the estimate is back above the threshold")
		# Platform-reported and ledger figures stay apart.
		row = self._row()
		self.assertEqual(row["reported"]["amount_due"]["value"], 123.45)
		self.assertNotIn("balance", row["reported"])

	def test_10_billing_record_rules_and_rights(self):
		with self.assertRaises(frappe.ValidationError):
			self._record("Top-up", 0, 1)
		with self.assertRaises(frappe.ValidationError):
			self._record("Refund", -5, 1)
		r = self._record("Top-up", 100, 1, "DUP-1")
		self.assertEqual((r.currency, r.company, r.workspace, r.provider), (self.cur, self.company, WS_A, "Meta"), "currency and scope come from the ad account")
		with self.assertRaises(frappe.ValidationError):
			self._record("Top-up", 100, 1, "DUP-1")
		with self.assertRaises(frappe.PermissionError):
			self._record("Top-up", 100, 1, user=U_MKT)
		with self.assertRaises(frappe.PermissionError):
			self._record("Top-up", 100, 1, user=U_FIN)  # read-only finance right, no create role
		with self.assertRaises(frappe.PermissionError):
			self._record("Top-up", 100, 1, user=U_ACC, account=self.meta_b)  # another company's account
		self._as(U_ACC)
		self.assertEqual(frappe.get_list("Ads Billing Record", filters={"account": ["in", [self.meta, self.meta_b]]}, pluck="name"), [r.name])
		moved = frappe.get_doc("Ads Billing Record", r.name)
		moved.account = self.meta_b
		self.assertRaises(frappe.PermissionError, moved.save)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Ads Billing Record", r.name, "account"), self.meta)

	# ─── scope and rights ─────────────────────────────────────────────────────

	def test_11_billing_scope_and_finance_right(self):
		self._read_meta()
		self._read_meta(self.meta_b)
		self._as(U_FIN)
		got = {r["account"] for r in billing.get_billing()["accounts"]}
		self.assertTrue({self.meta, self.google} <= got)
		self.assertNotIn(self.meta_b, got, "company A never sees company B billing")
		self.assertEqual(set(frappe.get_list("Ads Billing Snapshot", filters={"account": ["in", [self.meta, self.meta_b]]}, pluck="account")), {self.meta})
		self._as(U_B)
		self.assertEqual({r["account"] for r in billing.get_billing()["accounts"]} & {self.meta, self.google, self.meta_b}, {self.meta_b})
		self._as(U_MKT)
		self.assertRaises(frappe.PermissionError, billing.get_billing)
		self.assertRaises(frappe.PermissionError, billing.get_records, self.meta)
		self.assertEqual(frappe.get_list("Ads Billing Snapshot", filters={"account": self.meta}, pluck="name"), [], "no view_finance → no billing rows")
		self.assertIsNone(dashboard.overview(str(add_days(self.today, -7)), str(self.today))["billing"])
		self.assertFalse(frappe.has_permission("Ads Billing Snapshot", "read", doc=self._snap()))

	def test_12_structure_sync_records_billing_without_failing(self):
		"""The sync path: a billing failure that is not a token error never fails the structure job."""
		from excom.excom_ads import sync

		acc = self._acc(self.meta)
		provider = _meta(acc, self.graph)
		provider.read_billing = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
		provider.read_account = lambda: {"account_name": acc.account_name, "currency": acc.currency, "status": "Active", "platform_status": "1"}
		for method in ("list_campaigns", "list_ad_groups", "list_ads"):
			setattr(provider, method, lambda: [])
		r = sync.sync_structure(acc, provider)
		self.assertEqual(r["errors"], [], "a billing failure is not a structure error")
		s = self._snap()
		self.assertEqual((s.read_status, s.amount_spent_avail, s.headroom_avail), ("Error", "Error", "Error"))
		self.assertEqual(s.read_count, 1)
		# A provider without read_billing is skipped, not an error.
		from excom.excom_ads.providers.base import Capability

		provider.capability = lambda op: Capability(False, reason="QA") if op == "read_billing" else MetaAdsProvider.capability(op)
		self.assertIsNone(billing.read_and_record(acc, provider))


class TestAdsBudgets(BillingFixtures):
	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		plans = frappe.get_all("Ads Budget Plan", {"plan_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Ads Alert", {"reference_name": ["in", plans]})
		frappe.db.delete("Ads Budget Plan Line", {"parent": ["in", plans]})
		frappe.db.delete("Ads Budget Plan", {"name": ["in", plans]})
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _plan(self, name, lines, user=None, company=None, workspace=WS_A, period=(f"{AUG}-01", f"{AUG}-31"), brand=None):
		if user:
			self._as(user)
		doc = frappe.get_doc({"doctype": "Ads Budget Plan", "plan_name": f"{P} {name}", "company": company or self.company, "workspace": workspace, "brand": brand, "period_from": period[0], "period_to": period[1], "lines": lines}).insert()
		frappe.set_user("Administrator")
		permissions.clear_cache()
		return doc

	def _submit(self, name, user):
		self._as(user)
		try:
			doc = frappe.get_doc("Ads Budget Plan", name)
			doc.submit()
			return doc
		finally:
			frappe.set_user("Administrator")
			permissions.clear_cache()

	def _aug_lines(self):
		return [
			{"month": f"{AUG}-15", "provider": "Meta", "account": self.meta, "planned_spend": 3100, "target_leads": 31, "target_revenue": 9300},
			{"month": f"{AUG}-01", "provider": "Google", "planned_spend": 2000, "target_cpl": 50},
		]

	def test_01_maker_checker(self):
		plan = self._plan("Aug", self._aug_lines(), user=U_MAKER)
		self.assertEqual((plan.status, plan.currency, plan.total_planned, plan.owner), ("Draft", self.cur, 5100, U_MAKER))
		self.assertEqual(str(plan.lines[0].month), f"{AUG}-01", "months are stored as the 1st")
		self.assertEqual((plan.lines[0].target_cpl, plan.lines[0].target_roas, plan.lines[1].target_leads), (100, 3, 40), "missing targets are derived")
		self.assertRaises(frappe.PermissionError, self._submit, plan.name, U_MAKER)
		self.assertRaises(frappe.PermissionError, self._submit, plan.name, U_MKT)
		self.assertEqual(frappe.db.get_value("Ads Budget Plan", plan.name, "docstatus"), 0)
		done = self._submit(plan.name, U_CHECKER)
		self.assertEqual((done.docstatus, done.status, done.approved_by, done.approval_override), (1, "Approved", U_CHECKER, 0))
		# Editing a draft makes you a maker too.
		other = self._plan("Sep", [{"month": "2026-09-01", "provider": "Meta", "planned_spend": 10}], user=U_MAKER, period=("2026-09-01", "2026-09-30"))
		self._as(U_CHECKER)
		d = frappe.get_doc("Ads Budget Plan", other.name)
		d.lines[0].planned_spend = 20
		d.save()
		self.assertRaises(frappe.PermissionError, self._submit, other.name, U_CHECKER)
		self.assertRaises(frappe.PermissionError, self._submit, other.name, U_MAKER)
		approved = self._submit(other.name, "Administrator")
		self.assertEqual((approved.approved_by, approved.approval_override), ("Administrator", 0), "a third user approves; no override needed")
		# Creating a plan needs the right as well.
		self.assertRaises(frappe.PermissionError, self._plan, "No right", self._aug_lines(), U_MKT, period=("2026-10-01", "2026-10-31"))

	def test_02_system_manager_override_is_recorded_and_amend_keeps_history(self):
		plan = self._plan("Admin", self._aug_lines())
		plan.submit()
		self.assertEqual((plan.approval_override, plan.approved_by), (1, "Administrator"))
		self.assertIn("override", plan.approval_note)
		self._as(U_CHECKER)
		frappe.get_doc("Ads Budget Plan", plan.name).cancel()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Ads Budget Plan", plan.name, ["docstatus", "status"]), (2, "Cancelled"))
		self._as(U_MAKER)
		amended = frappe.copy_doc(frappe.get_doc("Ads Budget Plan", plan.name))
		amended.amended_from = plan.name
		amended.docstatus = 0  # as the Desk's Amend does (v15 copy_doc keeps it)
		amended.lines[0].planned_spend = 4000
		amended.insert()
		frappe.set_user("Administrator")
		self.assertEqual((amended.name, amended.status, amended.approved_by, amended.approval_override), (f"{plan.name}-1", "Draft", None, 0))
		self._submit(amended.name, U_CHECKER)
		self.assertEqual(frappe.db.get_value("Ads Budget Plan Line", {"parent": plan.name, "idx": 1}, "planned_spend"), 3100, "the approved original is unchanged")

	def test_03_overlapping_approved_plans_are_refused(self):
		self._submit(self._plan("First", self._aug_lines(), user=U_MAKER).name, U_CHECKER)
		second = self._plan("Second", [{"month": f"{AUG}-01", "provider": "Meta", "planned_spend": 1}], user=U_MAKER)
		self.assertRaises(frappe.ValidationError, self._submit, second.name, U_CHECKER)
		with self.assertRaises(frappe.ValidationError):
			self._plan("Bad brand", [{"month": f"{AUG}-01", "provider": "Google", "account": self.meta, "planned_spend": 1}], user=U_MAKER)
		with self.assertRaises(frappe.ValidationError):
			self._plan("Outside", [{"month": "2026-09-01", "provider": "Meta", "planned_spend": 1}], user=U_MAKER)
		with self.assertRaises(frappe.ValidationError):
			self._plan("Wrong ws", [{"month": f"{AUG}-01", "provider": "Meta", "planned_spend": 1}], user=U_MAKER, workspace=WS_B)

	def test_04_plan_vs_actual(self):
		approved = self._plan("Aug", self._aug_lines(), user=U_MAKER)
		self._submit(approved.name, U_CHECKER)
		self._plan("Draft Sep", [{"month": "2026-09-01", "provider": "Meta", "planned_spend": 99999}], user=U_MAKER, period=("2026-09-01", "2026-09-30"))
		r = budgets.plan_vs_actual({"workspace": WS_A}, f"{AUG}-10", "2026-09-05")
		self.assertEqual((r["from_date"], r["to_date"], r["level"], r["by_brand"], r["plans"]), (f"{AUG}-01", "2026-09-30", "Account", False, [approved.name]), "whole months; only approved plans")
		rows = {(x["month"], x["provider"]): x for x in r["rows"]}
		m = rows[(AUG, "Meta")]
		self.assertEqual((m["planned_spend"], m["actual_spend"], m["variance"], m["actual_leads"], m["target_leads"]), (3100, 2500, -600, 25, 31))
		self.assertAlmostEqual(m["variance_pct"], -600 / 3100 * 100)
		self.assertEqual((m["planned_to_date"], m["target_cpl"], m["actual_cpl"], m["target_roas"]), (3100, 100, 100, 3))
		g = rows[(AUG, "Google")]
		self.assertEqual((g["planned_spend"], g["actual_spend"], g["target_leads"], g["actual_cpl"]), (2000, 0, 40, None), "no Google spend in a past month is a real 0; CPL with no leads is not available")
		sep = rows[("2026-09", "Meta")]
		self.assertEqual((sep["planned_spend"], sep["actual_spend"]), (None, 900), "the draft plan does not count")
		t = r["totals"][0]
		self.assertEqual((t["planned_spend"], t["actual_spend"]), (5100, 3400))
		# A brand cut reads campaign rows.
		self._submit(self._plan("Sep brand", [{"month": "2026-09-01", "provider": "Meta", "brand": BRAND_A, "planned_spend": 1000}], user=U_MAKER, period=("2026-09-01", "2026-09-30"), brand=BRAND_A).name, U_CHECKER)
		rb = budgets.plan_vs_actual({"workspace": WS_A}, "2026-09-01", "2026-09-30")
		self.assertEqual((rb["level"], rb["by_brand"]), ("Campaign", True))
		by = {(x["provider"], x["brand"]): x for x in rb["rows"]}
		self.assertEqual((by[("Meta", BRAND_A)]["planned_spend"], by[("Meta", BRAND_A)]["actual_spend"]), (1000, 400))
		self.assertEqual((by[("Meta", "")]["planned_spend"], by[("Meta", "")]["actual_spend"]), (None, 500), "unallocated spend is its own row")
		only = budgets.plan_vs_actual({"workspace": WS_A, "brand": BRAND_A}, "2026-09-01", "2026-09-30")
		self.assertEqual([(x["planned_spend"], x["actual_spend"]) for x in only["rows"]], [(1000, 400)])
		self.assertRaises(frappe.ValidationError, budgets.plan_vs_actual, {}, "2026-09-01", "2026-08-01")

	def test_05_plan_scope(self):
		plan = self._plan("Aug", self._aug_lines(), user=U_MAKER)
		self._submit(plan.name, U_CHECKER)
		self._as(U_B)
		self.assertEqual([p for p in budgets.get_plans()["plans"] if p["plan_name"].startswith(P)], [], "company B sees no company A plans")
		r = budgets.get_plan_vs_actual(f"{AUG}-01", f"{AUG}-31")
		self.assertEqual(r["plans"], [])
		self.assertEqual({x["actual_spend"] for x in r["rows"] if x["provider"] == "Meta"}, {9999}, "only company B's own spend")
		self.assertFalse(frappe.has_permission("Ads Budget Plan", "read", doc=frappe.get_doc("Ads Budget Plan", plan.name)))
		self._as(U_MKT)
		got = budgets.get_plans()
		self.assertEqual([p["name"] for p in got["plans"] if p["plan_name"].startswith(P)], [plan.name])
		self.assertFalse(got["can_manage"])
		self._as(U_MAKER)
		mine = next(p for p in budgets.get_plans()["plans"] if p["name"] == plan.name)
		self.assertFalse(mine["can_approve"], "an approved plan is not approvable again")

	def test_06_overspend_alert(self):
		plan = self._plan("Aug", self._aug_lines(), user=U_MAKER)
		plan = self._submit(plan.name, U_CHECKER)
		meta_line = next(r for r in plan.lines if r.provider == "Meta")
		google_line = next(r for r in plan.lines if r.provider == "Google")
		key = budgets.overspend_key(plan.name, meta_line.name)
		# Aug 5: 1000 spent against 3100 × 5/31 = 500 prorated (+15%).
		out = budgets.check_overspend(f"{AUG}-05")
		self.assertEqual(out["raised"], 1)
		a = frappe.db.get_value("Ads Alert", {"dedupe_key": key, "status": "Open"}, ["alert_type", "severity", "company", "workspace", "account"], as_dict=True)
		self.assertEqual((a.alert_type, a.severity, a.company, a.workspace, a.account), ("Overspend", "Medium", self.company, WS_A, self.meta))
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": budgets.overspend_key(plan.name, google_line.name)}), "no Google spend, no alert")
		budgets.check_overspend(f"{AUG}-06")
		self.assertEqual(frappe.db.count("Ads Alert", {"dedupe_key": key}), 1, "deduped")
		# By Aug 31 spend (2500) is within the full plan (3100): resolved.
		budgets.check_overspend(f"{AUG}-31")
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}))
		budgets.check_overspend(f"{AUG}-05")
		self._as(U_B)
		self.assertEqual(frappe.get_list("Ads Alert", filters={"dedupe_key": key}, pluck="name"), [], "company B does not see it")
		frappe.set_user("Administrator")
		self._as(U_CHECKER)
		frappe.get_doc("Ads Budget Plan", plan.name).cancel()
		frappe.set_user("Administrator")
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}), "cancelling the plan resolves its alerts")

	# ─── QA Fix regressions ───────────────────────────────────────────────────

	def test_qa_fix_checker_cannot_change_the_plan_in_the_submit_request(self):
		plan = self._plan("QA Fix submit", self._aug_lines(), user=U_MAKER)
		self._as(U_CHECKER)
		try:
			d = frappe.get_doc("Ads Budget Plan", plan.name)
			d.lines[0].planned_spend = 99999  # Desk's Submit sends the form's values with the submit
			self.assertRaises(frappe.ValidationError, d.submit)
			d = frappe.get_doc("Ads Budget Plan", plan.name)
			d.plan_name = f"{P} QA Fix renamed"
			self.assertRaises(frappe.ValidationError, d.submit)
			d = frappe.get_doc("Ads Budget Plan", plan.name)
			d.append("lines", {"month": f"{AUG}-01", "provider": "Google", "account": self.google, "planned_spend": 5})
			self.assertRaises(frappe.ValidationError, d.submit)
		finally:
			frappe.set_user("Administrator")
			permissions.clear_cache()
		self.assertEqual(frappe.db.get_value("Ads Budget Plan", plan.name, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value("Ads Budget Plan Line", {"parent": plan.name, "idx": 1}, "planned_spend"), 3100, "the saved draft is unchanged")
		done = self._submit(plan.name, U_CHECKER)
		self.assertEqual((done.docstatus, done.approved_by), (1, U_CHECKER), "the saved draft, unchanged, is approved")

	def test_qa_fix_overspend_measures_lines_as_plan_vs_actual_does(self):
		# September: Meta account rows 900 = Brand A campaign 400 + unbranded campaign 500.
		sep = ("2026-09-01", "2026-09-30")
		line = lambda brand=None: frappe._dict(company=self.company, workspace=WS_A, provider="Meta", account=None, brand=brand)
		only = self._plan("QA Fix Sep all", [{"month": sep[0], "provider": "Meta", "planned_spend": 1000}], user=U_MAKER, period=sep)
		self._submit(only.name, U_CHECKER)
		pva = {(r["provider"], r["brand"]): r for r in budgets.plan_vs_actual({"workspace": WS_A, "provider": "Meta"}, *sep)["rows"]}
		self.assertEqual(pva[("Meta", "")]["actual_spend"], 900)
		self.assertEqual(budgets._line_actual(line(), getdate(sep[1])), 900, "no branded lines: the whole network (account rows)")
		self._as(U_CHECKER)
		frappe.get_doc("Ads Budget Plan", only.name).cancel()
		frappe.set_user("Administrator")
		split = self._plan("QA Fix Sep split", [{"month": sep[0], "provider": "Meta", "brand": BRAND_A, "planned_spend": 1000}, {"month": sep[0], "provider": "Meta", "planned_spend": 520}], user=U_MAKER, period=sep)
		split = self._submit(split.name, U_CHECKER)
		rows = budgets.plan_vs_actual({"workspace": WS_A, "provider": "Meta"}, *sep)
		pva = {(r["provider"], r["brand"]): r for r in rows["rows"]}
		self.assertTrue(rows["by_brand"])
		self.assertEqual((pva[("Meta", "")]["planned_spend"], pva[("Meta", "")]["actual_spend"]), (520, 500))
		self.assertEqual(budgets._line_actual(line(), getdate(sep[1])), pva[("Meta", "")]["actual_spend"], "the unbranded line is the unbranded bucket, not the account total")
		self.assertEqual(budgets._line_actual(line(BRAND_A), getdate(sep[1])), pva[("Meta", BRAND_A)]["actual_spend"])
		budgets.check_overspend(sep[1])
		unbranded = next(r for r in split.lines if not r.brand)
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": budgets.overspend_key(split.name, unbranded.name), "status": ["!=", "Resolved"]}), "500 against 520 is within the plan; the account total 900 would not have been")
