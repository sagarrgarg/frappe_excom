"""Excom Ads, Meta: discovery → Ads Account, structure mirrors, daily metrics. Graph is faked; no network.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_meta_sync'}"
"""

import json
import unittest
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from excom.excom.services import meta_connect as mc
from excom.excom_ads import sync, tasks
from excom.excom_ads.naming import mirror_name, snapshot_name
from excom.excom_ads.providers import meta as meta_provider

ACT = "act_9990001"
CONN = "QA Ads BM"
WS_PREFIX = "QA Ads"

ACCOUNT = {"id": ACT, "name": "QA Ads Account", "account_id": "9990001", "account_status": 1, "disable_reason": 0, "currency": "INR", "timezone_name": "Asia/Kolkata", "amount_spent": "1234550", "spend_cap": "0", "balance": "50000"}
CAMPAIGNS = [
	{"id": "c1", "name": "QA Diwali Sale", "objective": "OUTCOME_SALES", "status": "ACTIVE", "configured_status": "ACTIVE", "effective_status": "ACTIVE", "daily_budget": "150000", "created_time": "2026-10-01T10:00:00+0530", "updated_time": "2026-10-10T09:30:00+0530"},
	{"id": "c2", "name": "QA Leads B2B", "objective": "OUTCOME_LEADS", "status": "PAUSED", "configured_status": "PAUSED", "effective_status": "PAUSED", "lifetime_budget": "5000000"},
]
ADSETS = [
	{"id": "s1", "name": "QA Diwali · Delhi", "campaign_id": "c1", "status": "ACTIVE", "effective_status": "ACTIVE", "optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS", "targeting": {"geo_locations": {"cities": [{"key": "1"}]}}},
	{"id": "s2", "name": "QA Leads · Distributors", "campaign_id": "c2", "status": "ACTIVE", "effective_status": "CAMPAIGN_PAUSED"},
]
ADS = [
	{"id": "a1", "name": "QA Diwali Reel", "campaign_id": "c1", "adset_id": "s1", "status": "ACTIVE", "effective_status": "ACTIVE", "creative": {"id": "cr1", "name": "Reel v1"}},
	{"id": "a2", "name": "QA Distributor Form", "campaign_id": "c2", "adset_id": "s2", "status": "ACTIVE", "effective_status": "DISAPPROVED"},
]


class FakeGraph:
	"""Answers the Graph calls the provider makes. `spend_bump` changes reported spend to simulate a settle re-pull."""

	def __init__(self):
		self.calls = []
		self.spend_bump = 0.0
		self.fail_with = None
		self.paged_campaigns = True
		self.fail_level = None  # e.g. "ad": insights at that level answer with an error
		self.omit_campaigns = set()  # campaigns left out of insights (as Meta does for deleted objects)

	def __call__(self, url, params):
		self.calls.append(url)
		if self.fail_with:
			return self.fail_with
		path = urlparse(url).path
		q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
		q.update(params or {})
		if path.endswith(f"/{ACT}"):
			return 200, ACCOUNT, {}
		if path.endswith("/campaigns"):
			# Two pages, to prove paging follows `next` without re-sending params.
			if q.get("after") == "p2":
				return 200, {"data": CAMPAIGNS[1:]}, {}
			return 200, {"data": CAMPAIGNS[:1], "paging": {"next": f"{url}?after=p2&access_token=x"}}, {}
		if path.endswith("/adsets"):
			return 200, {"data": ADSETS}, {}
		if path.endswith("/ads"):
			return 200, {"data": ADS}, {}
		if path.endswith("/insights"):
			if self.fail_level and q.get("level") == self.fail_level:
				return 400, {"error": {"code": 1, "message": "Please reduce the amount of data you're asking for"}}, {}
			return 200, {"data": self._insights(q)}, {}
		raise AssertionError(f"unexpected GET {url}")

	def _insights(self, q):
		tr = json.loads(q["time_range"])
		level = q["level"]
		out = []
		day = getdate(tr["since"])
		while day <= getdate(tr["until"]):
			for camp, adset, ad, spend in (("c1", "s1", "a1", 1000.0), ("c2", "s2", "a2", 250.5)):
				if camp in self.omit_campaigns:
					continue
				row = {"date_start": str(day), "date_stop": str(day), "account_id": "9990001", "campaign_id": camp, "adset_id": adset, "ad_id": ad, "spend": str(spend + self.spend_bump), "impressions": "1000", "reach": "800", "clicks": "40", "inline_link_clicks": "30", "actions": [{"action_type": "lead", "value": "3"}, {"action_type": "onsite_conversion.lead_grouped", "value": "3"}, {"action_type": "omni_purchase", "value": "2"}, {"action_type": "purchase", "value": "2"}], "action_values": [{"action_type": "omni_purchase", "value": "4999"}]}
				out.append(row)
			day = add_days(day, 1)
		if level == "account":
			# One row per day: the account total.
			by_day = {}
			for r in out:
				d = by_day.setdefault(r["date_start"], {**r, "spend": "0", "impressions": "0"})
				d["spend"] = str(float(d["spend"]) + float(r["spend"]))
				d["impressions"] = str(int(d["impressions"]) + int(r["impressions"]))
			return list(by_day.values())
		if level == "campaign":
			return [{k: v for k, v in r.items() if k not in ("adset_id", "ad_id")} for r in out]
		if level == "adset":
			return [{k: v for k, v in r.items() if k != "ad_id"} for r in out]
		return out


def fake_connect_get(url, params):
	if url.endswith("/me/accounts") or url.endswith("/owned_pages") or url.endswith("/client_pages"):
		return {"data": []}
	if url.endswith("/me/adaccounts"):
		return {"data": [{"id": ACT, "name": "QA Ads Account", "account_id": "9990001", "currency": "INR", "timezone_name": "Asia/Kolkata", "account_status": 1}]}
	if url.endswith("/owned_ad_accounts") or url.endswith("/client_ad_accounts"):
		return {"data": [{"id": ACT, "name": "QA Ads Account"}]}
	if "whatsapp_business_accounts" in url:
		return {"data": []}
	raise AssertionError(f"unexpected GET {url}")


class TestAdsMetaSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		conn = frappe.get_doc({"doctype": "Excom Meta Connection", "connection_name": CONN, "business_id": "888", "company": cls.company, "app_id": "app1", "app_secret": "secret1", "system_user_token": "SYS_TOKEN", "token_scopes": "ads_read, leads_retrieval"})
		conn.insert(ignore_permissions=True)
		# A workspace made for this test, so nothing lands in a real one.
		frappe.get_doc({"doctype": "Ads Workspace", "workspace_name": f"{WS_PREFIX} {cls.company}", "company": cls.company}).insert(ignore_permissions=True)
		frappe.db.commit()
		cls._orig = (mc._get, meta_provider.http_get, frappe.enqueue)
		mc._get = fake_connect_get
		cls.graph = FakeGraph()
		meta_provider.http_get = cls.graph
		cls.enqueued = []
		frappe.enqueue = lambda *a, **k: cls.enqueued.append(k) or object()

	@classmethod
	def tearDownClass(cls):
		mc._get, meta_provider.http_get, frappe.enqueue = cls._orig
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		acc = f"Meta-{ACT}"
		for dt in ("Ads Metric Snapshot", "Ads Advertisement", "Ads Ad Group", "Ads Campaign", "Ads Sync Job", "Ads Alert", "Ads Billing Snapshot"):
			frappe.db.delete(dt, {"account": acc})
		frappe.db.delete("Ads Account", {"name": acc})
		for ws in frappe.get_all("Ads Workspace", {"workspace_name": ["like", f"{WS_PREFIX}%"]}, pluck="name"):
			frappe.db.delete("Ads Workspace", {"name": ws})
		if frappe.db.exists("Excom Meta Connection", CONN):
			frappe.delete_doc("Excom Meta Connection", CONN, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		self.graph.spend_bump = 0.0
		self.graph.fail_with = None
		self.graph.fail_level = None
		self.graph.omit_campaigns = set()
		self.enqueued.clear()

	# ─── helpers ──────────────────────────────────────────────────────────────

	def _account(self):
		name = f"Meta-{ACT}"
		if not frappe.db.exists("Ads Account", name):
			mc.discover(CONN)
			r = mc.enable_asset(CONN, "Ad Account", ACT)
			self.assertEqual(r["linked_name"], name)
			frappe.db.set_value("Ads Account", name, "workspace", f"{WS_PREFIX} {self.company}")
		return frappe.get_doc("Ads Account", name)

	def _count(self, dt):
		return frappe.db.count(dt, {"account": f"Meta-{ACT}"})

	# ─── tests ────────────────────────────────────────────────────────────────

	def test_01_discovery_lists_ad_accounts_once_and_enable_creates_account(self):
		mc.discover(CONN)
		conn = frappe.get_doc("Excom Meta Connection", CONN)
		rows = [a for a in conn.assets if a.asset_type == "Ad Account"]
		self.assertEqual(len(rows), 1, "the same account from /me and the business edges is one asset")
		acc = self._account()
		self.assertEqual((acc.provider, acc.external_id, acc.meta_connection, acc.currency, acc.sync_enabled), ("Meta", ACT, CONN, "INR", 1))
		self.assertEqual(acc.company, self.company)
		# Disabling the asset stops syncing but keeps the account and its history.
		mc.enable_asset(CONN, "Ad Account", ACT, 0)
		self.assertEqual(frappe.db.get_value("Ads Account", acc.name, "sync_enabled"), 0)
		mc.enable_asset(CONN, "Ad Account", ACT, 1)
		self.assertEqual(frappe.db.get_value("Ads Account", acc.name, "sync_enabled"), 1)

	def test_02_structure_sync_mirrors_tree_with_money_in_rupees(self):
		acc = self._account()
		r = sync.run_job(acc.name, "Structure", triggered_by="test")
		self.assertEqual(r["status"], "Success", r)
		acc.reload()
		self.assertEqual(acc.status, "Active")
		self.assertAlmostEqual(acc.reported_amount_spent, 12345.50)
		self.assertAlmostEqual(acc.reported_amount_due, 500.0)
		self.assertFalse(acc.reported_spend_cap, "spend_cap '0' means no cap, never a zero cap")
		self.assertEqual((acc.can_read, acc.can_leads, acc.can_write), (1, 1, 0))
		c1 = frappe.get_doc("Ads Campaign", mirror_name(acc.name, "c1"))
		self.assertEqual((c1.status, c1.objective, c1.daily_budget), ("Active", "OUTCOME_SALES", 1500.0))
		c2 = frappe.get_doc("Ads Campaign", mirror_name(acc.name, "c2"))
		self.assertEqual((c2.status, c2.lifetime_budget), ("Paused", 50000.0))
		s2 = frappe.get_doc("Ads Ad Group", mirror_name(acc.name, "s2"))
		self.assertEqual((s2.campaign, s2.status), (c2.name, "Paused"))
		a2 = frappe.get_doc("Ads Advertisement", mirror_name(acc.name, "a2"))
		self.assertEqual((a2.ad_group, a2.campaign, a2.status), (s2.name, c2.name, "Rejected"))
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"ad_rejected:{a2.name}", "alert_type": "Ad Disapproved", "status": "Open"}))
		self.assertEqual(c1.workspace, acc.workspace)
		self.assertIsNotNone(c1.platform_updated)

	def test_03_structure_resync_never_duplicates_and_flags_missing(self):
		acc = self._account()
		sync.run_job(acc.name, "Structure")
		counts = [self._count(d) for d in ("Ads Campaign", "Ads Ad Group", "Ads Advertisement")]
		sync.run_job(acc.name, "Structure")
		self.assertEqual(counts, [self._count(d) for d in ("Ads Campaign", "Ads Ad Group", "Ads Advertisement")])
		self.assertEqual(counts, [2, 2, 2])
		saved = list(ADS)
		try:
			ADS[:] = ADS[:1]
			sync.run_job(acc.name, "Structure")
		finally:
			ADS[:] = saved
		gone = frappe.db.get_value("Ads Advertisement", mirror_name(acc.name, "a2"), "not_returned_since")
		self.assertIsNotNone(gone, "an ad the platform stopped returning is kept and flagged, not deleted")
		sync.run_job(acc.name, "Structure")
		self.assertIsNone(frappe.db.get_value("Ads Advertisement", mirror_name(acc.name, "a2"), "not_returned_since"))

	def test_04_metrics_are_idempotent_and_settle_updates_history(self):
		acc = self._account()
		sync.run_job(acc.name, "Structure")
		since, until = "2026-10-01", "2026-10-03"
		r = sync.run_job(acc.name, "Backfill", since=since, until=until)
		self.assertEqual(r["status"], "Success", r)
		n = self._count("Ads Metric Snapshot")
		# 3 days × (1 account + 2 campaigns + 2 ad groups + 2 ads)
		self.assertEqual(n, 21)
		sync.run_job(acc.name, "Backfill", since=since, until=until)
		self.assertEqual(self._count("Ads Metric Snapshot"), n, "re-sync never duplicates")
		day_total = frappe.db.get_value("Ads Metric Snapshot", snapshot_name(acc.name, "Account", ACT, since), ["spend", "impressions", "leads", "purchases", "purchase_value", "spend_company_currency", "exchange_rate"], as_dict=True)
		self.assertAlmostEqual(day_total.spend, 1250.5)
		self.assertEqual(day_total.impressions, 2000)
		self.assertEqual(day_total.leads, 3, "overlapping lead action types are not added together")
		self.assertEqual(day_total.purchases, 2)
		self.assertAlmostEqual(day_total.purchase_value, 4999)
		self.assertEqual((day_total.exchange_rate, day_total.spend_company_currency), (1.0, 1250.5)) if frappe.get_cached_value("Company", self.company, "default_currency") == "INR" else None
		camp = frappe.get_doc("Ads Metric Snapshot", snapshot_name(acc.name, "Campaign", "c1", since))
		self.assertEqual((camp.campaign, camp.level), (mirror_name(acc.name, "c1"), "Campaign"))
		ad = frappe.get_doc("Ads Metric Snapshot", snapshot_name(acc.name, "Ad", "a2", since))
		self.assertEqual((ad.advertisement, ad.ad_group, ad.campaign), (mirror_name(acc.name, "a2"), mirror_name(acc.name, "s2"), mirror_name(acc.name, "c2")))
		# The platform revises a past day: the same row changes, no new row appears.
		self.graph.spend_bump = 10.0
		sync.run_job(acc.name, "Settle", since=since, until=until)
		self.assertEqual(self._count("Ads Metric Snapshot"), n)
		self.assertAlmostEqual(frappe.db.get_value("Ads Metric Snapshot", snapshot_name(acc.name, "Account", ACT, since), "spend"), 1270.5)
		acc.reload()
		self.assertEqual((str(acc.metrics_synced_from), str(acc.metrics_synced_until)), (since, until))

	def test_05_revoked_token_fails_visibly_and_pauses_intraday(self):
		acc = self._account()
		self.graph.fail_with = (400, {"error": {"code": 190, "message": "Error validating access token"}}, {})
		r = sync.run_job(acc.name, "Structure")
		self.assertEqual((r["status"], r["error_class"]), ("Failed", "Token"))
		acc.reload()
		self.assertEqual(acc.error_class, "Token")
		self.assertGreaterEqual(acc.consecutive_failures, 1)
		job = frappe.get_all("Ads Sync Job", {"account": acc.name}, ["status", "error_class"], order_by="creation desc", limit=1)[0]
		self.assertEqual((job.status, job.error_class), ("Failed", "Token"))
		self.assertNotIn("SYS_TOKEN", acc.last_error or "")
		alert = frappe.db.get_value("Ads Alert", {"dedupe_key": f"token:{acc.name}", "status": "Open"}, ["severity", "alert_type", "company"], as_dict=True)
		self.assertEqual((alert.severity, alert.alert_type, alert.company), ("Critical", "Token Problem", acc.company))
		tasks.enqueue_intraday()
		self.assertFalse([k for k in self.enqueued if k.get("account") == acc.name], "no metrics jobs for an account with a revoked token")
		tasks.enqueue_structure()
		self.assertTrue([k for k in self.enqueued if k.get("account") == acc.name and k.get("job_type") == "Structure"], "structure keeps checking so recovery is noticed")
		self.graph.fail_with = None
		self.assertEqual(sync.run_job(acc.name, "Structure")["status"], "Success")
		acc.reload()
		self.assertEqual((acc.error_class or "", acc.consecutive_failures), ("", 0))
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"token:{acc.name}", "status": ["!=", "Resolved"]}), "recovery resolves the token alert")

	def test_06_rate_limit_header_stops_job_cleanly(self):
		acc = self._account()
		busy = {"X-Business-Use-Case-Usage": json.dumps({"888": [{"type": "ads_management", "call_count": 99, "total_cputime": 10, "total_time": 10, "estimated_time_to_regain_access": 7}]})}
		self.graph.fail_with = (400, {"error": {"code": 80004, "message": "There have been too many calls"}}, busy)
		r = sync.run_job(acc.name, "Backfill", since="2026-10-01", until="2026-10-01")
		self.assertEqual(r["status"], "Failed")
		self.assertEqual(r["error_class"], "Rate Limited")

	# ─── review fixes ─────────────────────────────────────────────────────────

	def _fresh(self):
		"""An account whose mirrors and metrics are wiped, so ordering effects can be tested."""
		acc = self._account()
		for dt in ("Ads Metric Snapshot", "Ads Advertisement", "Ads Ad Group", "Ads Campaign"):
			frappe.db.delete(dt, {"account": acc.name})
		frappe.db.set_value("Ads Account", acc.name, {"metrics_synced_from": None, "metrics_synced_until": None, "brand": None})
		frappe.db.commit()
		acc.reload()
		return acc

	def test_08_rows_synced_before_their_campaign_get_linked_and_follow_its_brand(self):
		acc = self._fresh()
		ws = acc.workspace
		for b in ("QA Ads Brand X", "QA Ads Brand Y"):
			if not frappe.db.exists("Ads Brand", b):
				frappe.get_doc({"doctype": "Ads Brand", "brand_name": b, "workspace": ws}).insert(ignore_permissions=True)
		try:
			# First backfill runs before the first structure sync: no mirrors yet.
			sync.run_job(acc.name, "Backfill", since="2026-09-01", until="2026-09-01")
			camp_row = snapshot_name(acc.name, "Campaign", "c1", "2026-09-01")
			ad_row = snapshot_name(acc.name, "Ad", "a2", "2026-09-01")
			self.assertIsNone(frappe.db.get_value("Ads Metric Snapshot", camp_row, "campaign"))
			sync.run_job(acc.name, "Structure")
			c1 = mirror_name(acc.name, "c1")
			self.assertEqual(frappe.db.get_value("Ads Metric Snapshot", camp_row, "campaign"), c1, "linked by the structure sync")
			self.assertEqual(frappe.db.get_value("Ads Metric Snapshot", ad_row, ["advertisement", "ad_group", "campaign"]), (mirror_name(acc.name, "a2"), mirror_name(acc.name, "s2"), mirror_name(acc.name, "c2")))
			# A person allocates c1 to Brand X: it survives the next sync, and its rows follow.
			camp = frappe.get_doc("Ads Campaign", c1)
			camp.brand = "QA Ads Brand X"
			camp.save(ignore_permissions=True)
			self.assertEqual(frappe.db.get_value("Ads Metric Snapshot", camp_row, "brand"), "QA Ads Brand X")
			sync.run_job(acc.name, "Structure")
			self.assertEqual(frappe.db.get_value("Ads Campaign", c1, "brand"), "QA Ads Brand X", "a sync never overwrites an allocated brand")
			sync.run_job(acc.name, "Settle", since="2026-09-01", until="2026-09-01")
			self.assertEqual(frappe.db.get_value("Ads Metric Snapshot", camp_row, "brand"), "QA Ads Brand X", "re-pulled rows keep their campaign's brand")
			self.assertIsNone(frappe.db.get_value("Ads Metric Snapshot", snapshot_name(acc.name, "Campaign", "c2", "2026-09-01"), "brand"), "an unallocated campaign on an unbranded account stays unallocated")
		finally:
			frappe.db.set_value("Ads Campaign", mirror_name(acc.name, "c1"), "brand", None)
			for b in ("QA Ads Brand X", "QA Ads Brand Y"):
				frappe.db.delete("Ads Brand", {"name": b})
			frappe.db.commit()

	def test_09_long_names_do_not_break_the_structure_sync(self):
		acc = self._fresh()
		saved = [dict(c) for c in CAMPAIGNS]
		try:
			CAMPAIGNS[0]["name"] = "QA " + "Very long auto-generated campaign name " * 6
			r = sync.run_job(acc.name, "Structure")
			self.assertEqual(r["status"], "Success", r)
			self.assertTrue(frappe.db.get_value("Ads Campaign", mirror_name(acc.name, "c1"), "campaign_name").startswith("QA Very long"))
		finally:
			CAMPAIGNS[:] = saved

	def test_10_settle_keeps_history_of_objects_the_platform_stops_reporting(self):
		acc = self._fresh()
		sync.run_job(acc.name, "Structure")
		sync.run_job(acc.name, "Backfill", since="2026-09-01", until="2026-09-02")
		row = snapshot_name(acc.name, "Campaign", "c2", "2026-09-01")
		self.assertAlmostEqual(frappe.db.get_value("Ads Metric Snapshot", row, "spend"), 250.5)
		# c2 is deleted on the platform and drops out of insights entirely: its history stays.
		self.graph.omit_campaigns = {"c2"}
		sync.run_job(acc.name, "Settle", since="2026-09-01", until="2026-09-02")
		self.assertAlmostEqual(frappe.db.get_value("Ads Metric Snapshot", row, "spend"), 250.5)

	def test_11_a_failing_lower_level_does_not_wedge_the_account(self):
		acc = self._fresh()
		sync.run_job(acc.name, "Structure")
		self.graph.fail_level = "ad"
		r = sync.run_job(acc.name, "Backfill", since="2026-09-01", until="2026-09-02")
		self.assertEqual(r["status"], "Partial")
		acc.reload()
		self.assertEqual((str(acc.metrics_synced_from), str(acc.metrics_synced_until)), ("2026-09-01", "2026-09-02"), "account totals arrived, so the window counts as synced")
		tasks_enqueued = []
		orig = sync.enqueue
		sync.enqueue = lambda a, t, **k: tasks_enqueued.append((a, t))
		try:
			tasks.enqueue_intraday()
		finally:
			sync.enqueue = orig
		self.assertIn((acc.name, "Metrics"), tasks_enqueued, "next run is an intraday refresh, not another full backfill")

	def test_12_network_errors_never_store_the_token(self):
		import requests

		acc = self._account()
		token = "EAAB" + "x" * 40
		conn = frappe.get_doc("Excom Meta Connection", CONN)
		conn.system_user_token = token
		conn.save(ignore_permissions=True)

		def boom(url, params=None, timeout=None):
			raise requests.ConnectionError(f"HTTPSConnectionPool(host='graph.facebook.com'): Max retries exceeded with url: /v26.0/act_1?access_token={token}&fields=name")

		real_get = requests.get
		meta_provider.http_get = self._orig[1]  # the module's real transport, so its own error handling runs
		requests.get = boom
		try:
			r = sync.run_job(acc.name, "Structure")
		finally:
			requests.get = real_get
			meta_provider.http_get = self.graph
			conn.reload()
			conn.system_user_token = "SYS_TOKEN"
			conn.save(ignore_permissions=True)
		self.assertEqual(r["status"], "Failed")
		acc.reload()
		job = frappe.get_all("Ads Sync Job", {"account": acc.name}, ["error"], order_by="creation desc", limit=1)[0]
		for text in (acc.last_error, job.error, r.get("error")):
			self.assertNotIn(token, text or "")
		self.assertIn("ConnectionError", job.error or "")

	def test_07_unsupported_operations_are_declared_with_a_reason(self):
		from excom.excom_ads.providers.meta import MetaAdsProvider

		cap = MetaAdsProvider.capability("change_budget")
		self.assertFalse(cap.supported)
		self.assertIn("Action Request", cap.reason)
		self.assertFalse(MetaAdsProvider.capability("something_undeclared").supported)


class TestAdDestination(unittest.TestCase):
	"""Where an ad sends people (Ads Advertisement.destination_url): one page, or none."""

	PAGE = "https://shop.example/shop-now/rangoli-kit"

	def test_the_link_cards_page_with_its_utm_tags(self):
		from excom.excom_ads.providers.meta import ad_destination

		creative = {"object_story_spec": {"link_data": {"link": self.PAGE + "?utm_source=fb", "call_to_action": {"value": {"link": self.PAGE}}}}}
		self.assertEqual(ad_destination(creative), self.PAGE + "?utm_source=fb")

	def test_a_carousel_of_one_page_has_it(self):
		from excom.excom_ads.providers.meta import ad_destination

		cards = [{"link": self.PAGE}, {"link": "https://www.shop.example/shop-now/rangoli-kit/?c=2"}]
		self.assertEqual(ad_destination({"object_story_spec": {"link_data": {"child_attachments": cards}}}), self.PAGE)

	def test_a_carousel_of_several_pages_has_none(self):
		from excom.excom_ads.providers.meta import ad_destination

		cards = [{"link": self.PAGE}, {"link": "https://shop.example/shop-now/gulal"}]
		self.assertIsNone(ad_destination({"object_story_spec": {"link_data": {"child_attachments": cards}}}))

	def test_dynamic_creative_urls_count(self):
		from excom.excom_ads.providers.meta import ad_destination

		self.assertEqual(ad_destination({"asset_feed_spec": {"link_urls": [{"website_url": self.PAGE}]}}), self.PAGE)

	def test_no_link_no_destination(self):
		from excom.excom_ads.providers.base import single_destination
		from excom.excom_ads.providers.meta import ad_destination

		self.assertIsNone(ad_destination({"name": "Lead form"}))
		self.assertIsNone(ad_destination(None))
		self.assertIsNone(single_destination(["", "not a url"]))
