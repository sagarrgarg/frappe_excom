"""Excom Ads dashboard read API: totals, ratios recomputed from bases, previous period, unconverted
spend kept apart, brand cuts from campaign rows, scope, explorer paging, finance gating, freshness.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_dashboard'}"
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom_ads import dashboard, permissions
from excom.excom_ads.naming import mirror_name

P = "QA Dash"
U_BRAND = "qa_dash_brand@example.com"
U_MKT = "qa_dash_mkt@example.com"
USERS = [U_BRAND, U_MKT]


class TestAdsDashboard(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS", "company": cls.company})
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand X", "workspace": f"{P} WS"})
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand Y", "workspace": f"{P} WS"})
		acc = ins({"doctype": "Ads Account", "account_name": f"{P} Acc", "provider": "Google", "external_id": "999-000-0001", "workspace": f"{P} WS", "currency": cls.cur, "last_metrics_sync": now_datetime()})
		cls.acc = acc.name
		stale = ins({"doctype": "Ads Account", "account_name": f"{P} Stale", "provider": "Google", "external_id": "999-000-0002", "workspace": f"{P} WS", "currency": cls.cur, "last_metrics_sync": add_to_date(now_datetime(), hours=-9)})
		cls.stale = stale.name
		scope = {"account": cls.acc, "workspace": f"{P} WS", "company": cls.company, "provider": "Google"}
		cls.cx = ins({"doctype": "Ads Campaign", **scope, "campaign_name": f"{P} Camp X", "external_id": "cx", "status": "Active", "brand": f"{P} Brand X"}).name
		cls.cy = ins({"doctype": "Ads Campaign", **scope, "campaign_name": f"{P} Camp Y", "external_id": "cy", "status": "Paused", "brand": f"{P} Brand Y"}).name
		cls.cz = ins({"doctype": "Ads Campaign", **scope, "campaign_name": f"{P} Camp Z idle", "external_id": "cz", "status": "Active", "brand": f"{P} Brand X"}).name

		def snap(level, ext, date, spend, impressions, clicks, leads, purchases, value, campaign=None, brand=None, rate=1.0):
			ins({"doctype": "Ads Metric Snapshot", **scope, "level": level, "entity_external_id": ext, "date": date, "campaign": campaign, "brand": brand,
				"currency": cls.cur, "company_currency": cls.cur, "exchange_rate": rate, "spend": spend, "spend_company_currency": spend * rate,
				"impressions": impressions, "clicks": clicks, "leads": leads, "purchases": purchases, "purchase_value": value})

		# Current period 2026-10-01..02; previous 2026-09-29..30.
		snap("Account", acc.external_id, "2026-10-01", 300, 3000, 30, 3, 1, 900)
		snap("Account", acc.external_id, "2026-10-02", 100, 1000, 10, 1, 0, 0)
		snap("Account", acc.external_id, "2026-09-30", 50, 500, 5, 0, 0, 0)
		snap("Campaign", "cx", "2026-10-01", 250, 2500, 25, 3, 1, 900, campaign=cls.cx, brand=f"{P} Brand X")
		snap("Campaign", "cy", "2026-10-01", 50, 500, 5, 0, 0, 0, campaign=cls.cy, brand=f"{P} Brand Y")
		snap("Campaign", "cx", "2026-10-02", 100, 1000, 10, 1, 0, 0, campaign=cls.cx, brand=f"{P} Brand X")
		# A row with no exchange rate: counted apart, never as zero spend.
		snap("Account", stale.external_id, "2026-10-01", 70, 700, 7, 0, 0, 0, rate=0)
		for u in USERS:
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "roles": [{"role": "Excom Marketing"}]})
		ins({"doctype": "User Permission", "user": U_BRAND, "allow": "Ads Brand", "for_value": f"{P} Brand X", "apply_to_all_doctypes": 1})
		ins({"doctype": "User Permission", "user": U_MKT, "allow": "Ads Workspace", "for_value": f"{P} WS", "apply_to_all_doctypes": 1})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		for dt in ("Ads Metric Snapshot", "Ads Campaign", "Ads Alert", "Ads Sync Job"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _as(self, u):
		frappe.set_user(u)
		permissions.clear_cache()

	def test_totals_ratios_previous_and_unconverted(self):
		r = dashboard.overview("2026-10-01", "2026-10-02", workspace=f"{P} WS")
		self.assertEqual(r["level"], "Account")
		t = r["totals"][0]
		self.assertEqual(t["spend"], 400)
		self.assertEqual(t["spend_unconverted"], 70, "spend without a rate is reported apart")
		self.assertEqual(t["rows_unconverted"], 1)
		self.assertAlmostEqual(t["ctr"], (40 + 7) / (4000 + 700) * 100)
		self.assertAlmostEqual(t["cpc"], 400 / 47)
		self.assertAlmostEqual(t["roas"], 900 / 400, msg="ROAS is summed value / summed spend, not an average of daily ROAS")
		self.assertEqual(r["previous"]["from_date"], "2026-09-29")
		self.assertEqual(r["previous"]["totals"][0]["spend"], 50)
		self.assertEqual([x["date"] for x in r["series"]], ["2026-10-01", "2026-10-02"])
		self.assertEqual(r["source"], "Platform")

	def test_zero_base_ratio_is_not_available(self):
		r = dashboard.overview("2026-10-02", "2026-10-02", account=self.acc)
		self.assertIsNone(r["totals"][0]["roas"] if r["totals"][0]["purchase_value"] == 0 and r["totals"][0]["spend"] == 0 else None)
		self.assertIsNone(r["totals"][0]["cost_per_purchase"], "no purchases → cost per purchase is not available, not 0")

	def test_brand_cut_uses_campaign_rows(self):
		r = dashboard.overview("2026-10-01", "2026-10-02", workspace=f"{P} WS", brand=f"{P} Brand X")
		self.assertEqual(r["level"], "Campaign")
		self.assertEqual(r["totals"][0]["spend"], 350)
		brands = {b["brand"]: b["spend"] for b in r["by_brand"]}
		self.assertEqual(brands, {f"{P} Brand X": 350})
		r_all = dashboard.overview("2026-10-01", "2026-10-02", workspace=f"{P} WS")
		self.assertEqual({b["brand"]: b["spend"] for b in r_all["by_brand"]}, {f"{P} Brand X": 350, f"{P} Brand Y": 50})

	def test_brand_scoped_user_sees_only_their_brand(self):
		self._as(U_BRAND)
		r = dashboard.overview("2026-10-01", "2026-10-02")
		self.assertEqual(r["level"], "Campaign", "a brand-scoped user always gets campaign-level figures")
		self.assertEqual(sum(t["spend"] for t in r["totals"]), 350)
		self.assertIsNone(r["billing"], "no finance right, no billing figures")
		ex = dashboard.explorer("2026-10-01", "2026-10-02", level="Campaign")
		self.assertEqual({row["name"] for row in ex["rows"]}, {self.cx, self.cz})

	def test_explorer_sorts_pages_and_searches(self):
		ex = dashboard.explorer("2026-10-01", "2026-10-02", level="Campaign", account=self.acc, page_length=2)
		self.assertEqual(ex["total"], 3)
		self.assertEqual([r["name"] for r in ex["rows"]], [self.cx, self.cy], "sorted by spend, campaigns without spend last")
		self.assertEqual(ex["rows"][0]["metrics"]["spend"], 350)
		page2 = dashboard.explorer("2026-10-01", "2026-10-02", level="Campaign", account=self.acc, page_length=2, start=2)
		self.assertEqual([r["name"] for r in page2["rows"]], [self.cz])
		self.assertIsNone(page2["rows"][0]["metrics"], "no rows in range → no metrics, not zeros")
		found = dashboard.explorer("2026-10-01", "2026-10-02", level="Campaign", search="idle")
		self.assertEqual([r["name"] for r in found["rows"]], [self.cz])
		self.assertRaises(frappe.ValidationError, dashboard.explorer, "2026-10-01", "2026-10-02", level="Nope")

	def test_freshness_flags_stale_accounts(self):
		f = dashboard.freshness({"workspace": f"{P} WS"})
		self.assertIn(self.stale, [s["account"] for s in f["stale"]])
		self.assertNotIn(self.acc, [s["account"] for s in f["stale"]])

	def test_brand_filter_keeps_shared_accounts(self):
		# Accounts carry no brand here (brand is per campaign): a brand cut must still report the
		# freshness of the account that runs the brand's campaigns, not "never".
		f = dashboard.freshness({"workspace": f"{P} WS", "brand": f"{P} Brand X"})
		self.assertEqual(f["accounts"], 1)
		self.assertIsNotNone(f["as_of"])
		self.assertEqual([a["name"] for a in dashboard.accounts(workspace=f"{P} WS", brand=f"{P} Brand Y")], [self.acc])

	def test_scope_and_access(self):
		self._as(U_MKT)
		self.assertEqual({a["name"] for a in dashboard.accounts()} & {self.acc, self.stale}, {self.acc, self.stale})
		self.assertTrue(all(a["billing"] is None for a in dashboard.accounts()))
		frappe.set_user("Guest")
		permissions.clear_cache()
		self.assertRaises(frappe.PermissionError, dashboard.overview, "2026-10-01", "2026-10-02")

	def test_range_validation(self):
		self.assertRaises(frappe.ValidationError, dashboard.overview, "2026-10-02", "2026-10-01")
		self.assertRaises(frappe.ValidationError, dashboard.overview, "2024-01-01", "2026-10-01")

	def test_filter_options_are_scoped(self):
		self._as(U_BRAND)
		o = dashboard.filter_options()
		self.assertEqual({b["name"] for b in o["brands"]}, {f"{P} Brand X"})
		self.assertFalse(o["rights"]["view_finance"])
