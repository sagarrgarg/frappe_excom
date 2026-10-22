"""Excom Ads, data quality: account-level vs campaign-level spend reconciliation (Spend Mismatch,
acceptance tests 1 and 16) and Data Stale alerts, raised and resolved; the Overview note and its scope.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_quality'}"
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, now_datetime

from excom.excom_ads import dashboard, permissions, quality, tasks
from excom.excom_ads.sync import account_today

P = "QA Quality"
WS = f"{P} WS"
BRAND = f"{P} Brand"
U_BRAND = "qa_quality_brand@example.com"


class TestAdsQuality(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS, "company": cls.company})
		ins({"doctype": "Ads Brand", "brand_name": BRAND, "workspace": WS})
		acc = ins({"doctype": "Ads Account", "account_name": f"{P} Meta", "provider": "Meta", "external_id": "act_7770001", "workspace": WS, "currency": cls.cur, "timezone_name": "Asia/Kolkata", "sync_enabled": 1, "last_metrics_sync": now_datetime()})
		cls.acc = acc.name
		cls.stale = ins({"doctype": "Ads Account", "account_name": f"{P} Stale", "provider": "Meta", "external_id": "act_7770002", "workspace": WS, "currency": cls.cur, "sync_enabled": 1, "last_metrics_sync": add_to_date(now_datetime(), hours=-10)}).name
		cls.manual = ins({"doctype": "Ads Account", "account_name": f"{P} Manual", "provider": "Manual", "external_id": "qa-quality-manual", "workspace": WS, "currency": cls.cur, "sync_enabled": 1, "last_metrics_sync": add_to_date(now_datetime(), days=-20)}).name
		cls.fresh_new = ins({"doctype": "Ads Account", "account_name": f"{P} New", "provider": "Meta", "external_id": "act_7770003", "workspace": WS, "currency": cls.cur, "sync_enabled": 1}).name
		scope = {"account": acc.name, "workspace": WS, "company": cls.company, "provider": "Meta"}
		cls.ca = ins({"doctype": "Ads Campaign", **scope, "campaign_name": f"{P} A", "external_id": "qa1", "status": "Active", "brand": BRAND}).name
		cls.cb = ins({"doctype": "Ads Campaign", **scope, "campaign_name": f"{P} B", "external_id": "qa2", "status": "Active", "brand": BRAND}).name
		today = account_today(acc)
		cls.days = [str(add_days(today, -n)) for n in range(2, 7)]
		d_ok, d_gap, d_tiny, d_missing, d_small_pct = cls.days

		def snap(level, ext, date, spend, campaign=None):
			ins({"doctype": "Ads Metric Snapshot", **scope, "level": level, "entity_external_id": ext, "date": date, "campaign": campaign, "brand": BRAND if campaign else None, "currency": cls.cur, "company_currency": cls.cur, "exchange_rate": 1, "spend": spend, "spend_company_currency": spend})

		for d, account_spend, a, b in ((d_ok, 1000, 600, 400), (d_gap, 1000, 600, 390), (d_tiny, 1000, 600, 399.5), (d_small_pct, 10000, 6000, 3960)):
			snap("Account", acc.external_id, d, account_spend)
			snap("Campaign", "qa1", d, a, cls.ca)
			snap("Campaign", "qa2", d, b, cls.cb)
		snap("Account", acc.external_id, d_missing, 100)  # campaign pull missing for that day
		ins({"doctype": "User", "email": U_BRAND, "first_name": "qa_quality_brand", "send_welcome_email": 0, "roles": [{"role": "Excom Marketing"}]})
		ins({"doctype": "User Permission", "user": U_BRAND, "allow": "Ads Brand", "for_value": BRAND, "apply_to_all_doctypes": 1})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		for dt in ("Ads Metric Snapshot", "Ads Campaign", "Ads Alert"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": U_BRAND})
		if frappe.db.exists("User", U_BRAND):
			frappe.delete_doc("User", U_BRAND, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _open(self, key):
		return frappe.get_all("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}, ["name", "message", "occurrences", "alert_type", "account", "company"])

	def test_rule(self):
		self.assertFalse(quality.is_mismatch(1000, 1000, 0.5, 1))
		self.assertTrue(quality.is_mismatch(1000, 990, 0.5, 1), "1% gap")
		self.assertFalse(quality.is_mismatch(1000, 999.5, 0.5, 1), "tiny absolute gap is ignored")
		self.assertFalse(quality.is_mismatch(10000, 9960, 0.5, 1), "0.4% is under the threshold")
		self.assertTrue(quality.is_mismatch(100, None, 0.5, 1), "a missing level is a mismatch")
		self.assertTrue(quality.is_mismatch(None, 50, 0.5, 1))
		self.assertFalse(quality.is_mismatch(None, None, 0.5, 1))
		self.assertFalse(quality.is_mismatch(0, None, 0.5, 1), "a zero-spend day with no campaign rows is fine")

	def test_spend_mismatch_raised_listing_days_then_resolved(self):
		d_ok, d_gap, d_tiny, d_missing, d_small_pct = self.days
		days = quality.check_spend(self.acc)
		self.assertEqual(sorted(d["date"] for d in days), sorted([d_gap, d_missing]))
		alerts = self._open(f"spend_mismatch:{self.acc}")
		self.assertEqual(len(alerts), 1)
		a = alerts[0]
		self.assertEqual((a.alert_type, a.account, a.company), ("Spend Mismatch", self.acc, self.company))
		self.assertIn(d_gap, a.message)
		self.assertIn(d_missing, a.message)
		self.assertNotIn(d_tiny, a.message)
		quality.check_spend(self.acc)
		self.assertEqual(self._open(f"spend_mismatch:{self.acc}")[0].occurrences, 2, "a repeat counts up, no second alert")
		# The next sync fixes both days.
		frappe.db.set_value("Ads Metric Snapshot", {"account": self.acc, "level": "Campaign", "campaign": self.cb, "date": d_gap}, {"spend": 400, "spend_company_currency": 400})
		frappe.db.delete("Ads Metric Snapshot", {"account": self.acc, "level": "Account", "date": d_missing})
		try:
			self.assertEqual(quality.check_spend(self.acc), [])
			self.assertEqual(self._open(f"spend_mismatch:{self.acc}"), [], "resolved once the window is clean")
		finally:
			frappe.db.set_value("Ads Metric Snapshot", {"account": self.acc, "level": "Campaign", "campaign": self.cb, "date": d_gap}, {"spend": 390, "spend_company_currency": 390})
			frappe.get_doc({"doctype": "Ads Metric Snapshot", "account": self.acc, "workspace": WS, "company": self.company, "provider": "Meta", "level": "Account", "entity_external_id": "act_7770001", "date": d_missing, "currency": self.cur, "company_currency": self.cur, "exchange_rate": 1, "spend": 100, "spend_company_currency": 100}).insert(ignore_permissions=True)
			frappe.db.commit()

	def test_threshold_is_a_setting(self):
		key = ("Ads Settings", "spend_mismatch_pct")
		found = frappe.db.sql("select value from `tabSingles` where doctype = %s and field = %s", key)
		prior = found[0][0] if found else None
		frappe.db.set_single_value("Ads Settings", "spend_mismatch_pct", 2)
		try:
			days = quality.spend_days([self.acc], self.days[-1], self.days[0])[self.acc]
			self.assertEqual([d["date"] for d in days], [self.days[3]], "a 1% gap passes a 2% threshold; the missing level still counts")
		finally:
			# Put back exactly what was there (often nothing: the default applies).
			frappe.db.sql("delete from `tabSingles` where doctype = %s and field = %s", key)
			if prior is not None:
				frappe.db.sql("insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)", (*key, prior))
			frappe.clear_document_cache("Ads Settings", "Ads Settings")
			frappe.db.commit()

	def test_data_stale_raised_and_resolved(self):
		names = [self.acc, self.stale, self.manual, self.fresh_new]
		r = quality.run_stale_checks(names=names)
		self.assertEqual(r["stale"], 1)
		self.assertTrue(self._open(f"data_stale:{self.stale}"))
		self.assertFalse(self._open(f"data_stale:{self.manual}"), "a Manual account has no sync clock")
		self.assertFalse(self._open(f"data_stale:{self.fresh_new}"), "an account enabled minutes ago is not stale yet")
		self.assertFalse(self._open(f"data_stale:{self.acc}"))
		frappe.db.set_value("Ads Account", self.stale, "last_metrics_sync", now_datetime())
		r = quality.run_stale_checks(names=names)
		self.assertEqual(r["resolved"], 1)
		self.assertFalse(self._open(f"data_stale:{self.stale}"), "resolved on recovery")
		frappe.db.set_value("Ads Account", self.stale, "last_metrics_sync", add_to_date(now_datetime(), hours=-10))
		frappe.db.commit()

	def test_scheduler_entries_only_enqueue(self):
		queued = []
		orig = frappe.enqueue
		frappe.enqueue = lambda *a, **k: queued.append((a, k))
		try:
			tasks.enqueue_spend_checks()
			tasks.check_stale_data()
		finally:
			frappe.enqueue = orig
		methods = [a[0] for a, k in queued]
		self.assertIn("excom.excom_ads.quality.run_spend_checks", methods)
		self.assertIn("excom.excom_ads.quality.run_stale_checks", methods)
		self.assertTrue(all(k.get("deduplicate") and k.get("job_id") for a, k in queued))
		self.assertNotIn(self.manual, [r.name for r in tasks._accounts()], "Manual accounts are never synced")

	def test_overview_note_and_scope(self):
		a, b = self.days[-1], self.days[0]
		o = dashboard.overview(a, b, workspace=WS)
		dq = o["data_quality"]
		row = next(x for x in dq["spend_mismatch"] if x["account"] == self.acc)
		self.assertEqual(row["days"], 2)
		self.assertEqual((row["worst"]["date"], row["worst"]["gap"], row["worst"]["campaign_spend"]), (self.days[3], 100, None), "the largest gap: the day without campaign rows")
		f = o["freshness"]
		self.assertIn(self.stale, [s["account"] for s in f["stale"]])
		self.assertNotIn(self.manual, [s["account"] for s in f["stale"]])
		self.assertIn(self.manual, [m["account"] for m in f["manual"]])
		frappe.set_user(U_BRAND)
		permissions.clear_cache()
		dq = dashboard.overview(a, b)["data_quality"]
		self.assertIsNone(dq["spend_mismatch"], "a brand-scoped user does not see account-level totals")
