"""Excom Ads, GA4: discovery → enable → daily web snapshots → Website read API, quota and errors.
Google Analytics is faked (Data API v1beta / Admin API v1beta REST shapes); no network.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_ga4'}"
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from excom.excom_ads import analytics, api_analytics, google_auth, permissions, tasks
from excom.excom_ads.naming import web_snapshot_name
from excom.excom_ads.providers import google_analytics as ga
from excom.excom_ads.providers.base import PERMISSION, RATE_LIMITED

P = "QA GA4"
CONN = f"{P} Conn"
CONN_NOSCOPE = f"{P} NoScope"
WS, WS_B = f"{P} WS", f"{P} WS B"
BRAND = f"{P} Brand"
PID, PID2 = "4410001", "4410002"
PROP = f"GA4-{PID}"
TOKEN = "ya29.QA_GA4_FAKE_TOKEN_0123456789"
SECRET = "GOCSPX-qa-ga4-secret-778899"
REFRESH = "1//0qa-ga4-refresh-token-abcdefghijklmnopqrstuv"
U_B = "qa_ga4_company_b@example.com"
U_BRAND = "qa_ga4_brand@example.com"
USERS = (U_B, U_BRAND)
GCAMP_ID = "8812345"  # a Google Ads campaign ID, as auto-tagging puts it in sessionCampaignId


def _src(date, source, medium, campaign, cid, s, u, n, e, k, t, rev):
	return {"dimensionValues": [{"value": date}, {"value": source}, {"value": medium}, {"value": campaign}, {"value": cid}], "metricValues": [{"value": str(v)} for v in (s, u, n, e, k, t, rev)]}


def _lp(date, page, s, u, n, e, k, t, rev):
	return {"dimensionValues": [{"value": date}, {"value": page}], "metricValues": [{"value": str(v)} for v in (s, u, n, e, k, t, rev)]}


SOURCE_ROWS = [
	_src("20261005", "google", "cpc", "QA Diwali Search", GCAMP_ID, 100, 90, 60, 70, 8, 3, "4500.5"),
	_src("20261005", "facebook", "paid_social", "QA Diwali Reels", "(not set)", 50, 48, 40, 20, 2, 1, "999"),
	_src("20261006", "google", "cpc", "QA Diwali Search", GCAMP_ID, 120, 100, 70, 90, 10, 4, "6000"),
	_src("20261006", "(direct)", "(none)", "(direct)", "(not set)", 30, 30, 10, 15, 0, 0, "0"),
]
LANDING_ROWS = [_lp("20261005", "/diwali", 120, 110, 80, 75, 9, 3, "4500.5"), _lp("20261006", "/", 150, 120, 75, 105, 10, 4, "6000")]


class FakeGA:
	def __init__(self):
		self.reset()

	def reset(self):
		self.calls = []
		self.bodies = []
		self.source_rows = list(SOURCE_ROWS)
		self.landing_rows = list(LANDING_ROWS)
		self.fail = None  # (status, body) for every report call
		self.quota_hour = 39000
		self.page_cap = None  # rows per page, to exercise offset paging
		self.granted = f"{google_auth.ADWORDS_SCOPE} {google_auth.ANALYTICS_SCOPE}"

	def __call__(self, method, url, headers=None, json_body=None, form=None, timeout=60):
		self.calls.append((method, url))
		if url == google_auth.TOKEN_URI:
			return 200, {"access_token": TOKEN, "expires_in": 3599, "token_type": "Bearer", "scope": self.granted}, {}
		assert headers and headers.get("Authorization") == f"Bearer {TOKEN}", "every GA call carries the bearer token"
		if url.startswith(f"{ga.ADMIN_API}/accountSummaries"):
			if "pageToken" not in url:
				return 200, {"accountSummaries": [{"name": "accountSummaries/77", "account": "accounts/77", "displayName": "QA GA Account", "propertySummaries": [{"property": f"properties/{PID}", "displayName": "QA Shop", "propertyType": "PROPERTY_TYPE_ORDINARY", "parent": "accounts/77"}]}], "nextPageToken": "p2/=="}, {}
			assert "pageToken=p2%2F%3D%3D" in url, "page tokens are URL-encoded"
			return 200, {"accountSummaries": [{"name": "accountSummaries/78", "account": "accounts/78", "displayName": "QA GA Other", "propertySummaries": [{"property": f"properties/{PID2}", "displayName": "QA Blog", "propertyType": "PROPERTY_TYPE_ORDINARY"}]}]}, {}
		if url == f"{ga.ADMIN_API}/properties/{PID}":
			return 200, {"name": f"properties/{PID}", "displayName": "QA Shop", "timeZone": "Asia/Kolkata", "currencyCode": "INR", "propertyType": "PROPERTY_TYPE_ORDINARY", "account": "accounts/77", "parent": "accounts/77"}, {}
		if url.startswith(f"{ga.ADMIN_API}/properties/{PID}/dataStreams"):
			return 200, {"dataStreams": [{"name": "properties/x/dataStreams/1", "type": "IOS_APP_DATA_STREAM"}, {"name": "properties/x/dataStreams/2", "type": "WEB_DATA_STREAM", "displayName": "Shop", "webStreamData": {"measurementId": "G-QA12345", "defaultUri": "https://shop.example"}}]}, {}
		if url == f"{ga.DATA_API}/properties/{PID}:runReport":
			return self._report(json_body)
		raise AssertionError(f"unexpected GA call {url}")

	def _report(self, body):
		self.bodies.append(json.loads(json.dumps(body)))
		if self.fail:
			return self.fail
		dims = [d["name"] for d in body["dimensions"]]
		a, b = body["dateRanges"][0]["startDate"].replace("-", ""), body["dateRanges"][0]["endDate"].replace("-", "")
		pool = self.source_rows if "sessionSource" in dims else self.landing_rows
		rows = [r for r in pool if a <= r["dimensionValues"][0]["value"] <= b]
		offset, limit = int(body.get("offset") or 0), int(body["limit"])
		if self.page_cap:
			limit = min(limit, self.page_cap)
		page = rows[offset : offset + limit]
		self.quota_hour -= 10
		out = {
			"dimensionHeaders": [{"name": d} for d in dims],
			"metricHeaders": [{"name": m["name"], "type": "TYPE_INTEGER"} for m in body["metrics"]],
			"rows": page,
			"rowCount": len(rows),
			"metadata": {"currencyCode": "INR", "timeZone": "Asia/Kolkata", **({"dataLossFromOtherRow": True} if "landingPage" in dims else {})},
			"propertyQuota": {"tokensPerDay": {"consumed": 10, "remaining": 190000}, "tokensPerHour": {"consumed": 10, "remaining": self.quota_hour}, "tokensPerProjectPerHour": {"consumed": 10, "remaining": 13000}},
			"kind": "analyticsData#runReport",
		}
		if not page:
			out.pop("rows")
		return 200, out, {}


class TestAdsGA4(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.company_b = "_Test Ads Company B" if frappe.db.exists("Company", "_Test Ads Company B") else None
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS, "company": cls.company})
		ins({"doctype": "Ads Brand", "brand_name": BRAND, "workspace": WS})
		oauth = {"provider": "Google", "company": cls.company, "workspace": WS, "auth_mode": "OAuth Refresh Token", "oauth_client_id": "qa-ga4.apps.googleusercontent.com", "oauth_client_secret": SECRET, "refresh_token": REFRESH}
		ins({"doctype": "Ads Connection", "connection_name": CONN, **oauth, "scopes": f"{google_auth.ADWORDS_SCOPE}\n{google_auth.ANALYTICS_SCOPE}"})
		ins({"doctype": "Ads Connection", "connection_name": CONN_NOSCOPE, **oauth, "refresh_token": REFRESH + "x"})
		acc = ins({"doctype": "Ads Account", "account_name": f"{P} Ads", "provider": "Google", "external_id": "4419990001", "workspace": WS, "currency": "INR"})
		cls.acc = acc.name
		cls.camp = ins({"doctype": "Ads Campaign", "account": acc.name, "workspace": WS, "company": cls.company, "provider": "Google", "campaign_name": "QA Diwali Search", "external_id": GCAMP_ID, "status": "Active"}).name
		for u, role in ((U_B, "Excom Admin"), (U_BRAND, "Excom Marketing")):
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "roles": [{"role": role}]})
		if cls.company_b:
			ins({"doctype": "User Permission", "user": U_B, "allow": "Company", "for_value": cls.company_b, "apply_to_all_doctypes": 1})
		other_brand = ins({"doctype": "Ads Brand", "brand_name": f"{P} Other Brand", "workspace": WS}).name
		ins({"doctype": "User Permission", "user": U_BRAND, "allow": "Ads Brand", "for_value": other_brand, "apply_to_all_doctypes": 1})
		frappe.db.commit()
		cls.fake = FakeGA()
		cls._orig = (google_auth.http_request, ga._sleep, frappe.enqueue)
		google_auth.http_request = cls.fake
		ga._sleep = lambda s: None
		cls.enqueued = []
		frappe.enqueue = lambda *a, **k: cls.enqueued.append(k) or object()

	@classmethod
	def tearDownClass(cls):
		google_auth.http_request, ga._sleep, frappe.enqueue = cls._orig
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		props = frappe.get_all("Ads Analytics Property", {"property_id": ["in", [PID, PID2]]}, pluck="name")
		for p in props:
			frappe.db.delete("Ads Web Snapshot", {"property": p})
			frappe.db.delete("Ads Alert", {"reference_name": p})
			frappe.db.delete("Version", {"ref_doctype": "Ads Analytics Property", "docname": p})
			frappe.db.delete("Ads Analytics Property", {"name": p})
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Ads Campaign", {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		for name in frappe.get_all("Ads Connection", {"name": ["like", f"{P}%"]}, pluck="name"):
			frappe.db.delete("Version", {"ref_doctype": "Ads Connection", "docname": name})
			frappe.delete_doc("Ads Connection", name, force=True, ignore_permissions=True)
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		self.fake.reset()
		self.enqueued.clear()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _enable(self):
		if not frappe.db.exists("Ads Analytics Property", PROP):
			api_analytics.enable_property(CONN, PID, 1, WS, BRAND)
		return frappe.get_doc("Ads Analytics Property", PROP)

	def _sync(self, a="2026-10-05", b="2026-10-06", job="Metrics"):
		return analytics.run_job(PROP, job, a, b)

	# ─── discovery / enabling ─────────────────────────────────────────────────

	def test_01_discover_pages_through_account_summaries(self):
		found = api_analytics.list_properties(CONN)
		self.assertEqual([f["property_id"] for f in found], [PID, PID2])
		self.assertEqual(found[0]["account_name"], "QA GA Account")
		self.assertNotIn(SECRET, frappe.as_json(found))

	def test_02_enable_reads_property_and_queues_backfill(self):
		doc = self._enable()
		self.assertEqual((doc.property_name, doc.time_zone, doc.currency, doc.website, doc.measurement_id, doc.company, doc.brand), ("QA Shop", "Asia/Kolkata", "INR", "https://shop.example", "G-QA12345", self.company, BRAND))
		jobs = [k for k in self.enqueued if k.get("job_id", "").startswith("excom_ads_ga4::")]
		self.assertTrue(jobs and jobs[0]["job_type"] == "Backfill" and jobs[0]["queue"] == "long" and jobs[0]["deduplicate"])

	def test_03_connection_without_analytics_scope_is_refused(self):
		self.assertRaises(frappe.ValidationError, api_analytics.list_properties, CONN_NOSCOPE)

	# ─── sync ─────────────────────────────────────────────────────────────────

	def test_10_sync_writes_snapshots_and_is_idempotent(self):
		self._enable()
		r = self._sync()
		self.assertEqual(r["status"], "Success", r)
		rows = frappe.get_all("Ads Web Snapshot", {"property": PROP, "report": "Source"}, ["name", "date", "session_source", "session_medium", "session_campaign_id", "campaign", "sessions", "key_events", "purchase_revenue", "currency", "company", "brand"])
		self.assertEqual(len(rows), 4)
		g = next(x for x in rows if str(x.date) == "2026-10-05" and x.session_source == "google")
		self.assertEqual((g.sessions, g.key_events, g.purchase_revenue, g.currency, g.company, g.brand), (100, 8, 4500.5, "INR", self.company, BRAND))
		self.assertEqual(g.campaign, self.camp, "sessionCampaignId = the Google Ads campaign ID links the mirror")
		self.assertIsNone(next(x for x in rows if x.session_source == "facebook").campaign, "(not set) links nothing")
		self.assertEqual(g.name, web_snapshot_name(PROP, "Source", "2026-10-05", "google", "cpc", "QA Diwali Search", GCAMP_ID, None))
		self.assertEqual(frappe.db.count("Ads Web Snapshot", {"property": PROP, "report": "Landing Page"}), 2)
		prop = frappe.get_doc("Ads Analytics Property", PROP)
		self.assertEqual((prop.last_status, str(prop.synced_from), str(prop.synced_until), prop.tokens_hour_remaining, prop.data_loss_other_row), ("Success", "2026-10-05", "2026-10-06", self.fake.quota_hour, 1))
		names = {x.name for x in rows}
		# Re-sync with revised numbers and one combination gone: same rows, new values, nothing stale.
		self.fake.source_rows = [_src("20261005", "google", "cpc", "QA Diwali Search", GCAMP_ID, 110, 95, 61, 75, 9, 3, "4600")] + SOURCE_ROWS[2:]
		self.assertEqual(self._sync()["status"], "Success")
		after = frappe.get_all("Ads Web Snapshot", {"property": PROP, "report": "Source"}, ["name", "sessions"])
		self.assertEqual(len(after), 3)
		self.assertTrue({x.name for x in after} <= names, "a re-run writes the same deterministic rows")
		self.assertEqual(next(x.sessions for x in after if x.name == g.name), 110)
		body = self.fake.bodies[0]
		self.assertEqual([m["name"] for m in body["metrics"]], list(ga.METRICS))
		self.assertIn("keyEvents", [m["name"] for m in body["metrics"]], "keyEvents replaced the deprecated conversions metric")
		self.assertTrue(body["returnPropertyQuota"])

	def test_11_offset_paging(self):
		self._enable()
		self.fake.page_cap = 1
		orig = ga.PAGE_ROWS
		try:
			ga.PAGE_ROWS = 1
			self.assertEqual(self._sync()["status"], "Success")
		finally:
			ga.PAGE_ROWS = orig
		self.assertEqual(frappe.db.count("Ads Web Snapshot", {"property": PROP, "report": "Source", "date": ["between", ["2026-10-05", "2026-10-06"]]}), 4)
		self.assertEqual([b["offset"] for b in self.fake.bodies if "sessionSource" in [d["name"] for d in b["dimensions"]]], ["0", "1", "2", "3"])

	def test_12_quota_exhausted_is_rate_limited_and_alerts(self):
		self._enable()
		self.fake.fail = (429, {"error": {"code": 429, "message": "Exhausted property tokens per hour for a project per property.", "status": "RESOURCE_EXHAUSTED"}}, {})
		r = self._sync()
		self.assertEqual((r["status"], r["error_class"]), ("Failed", RATE_LIMITED))
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"ga4_rate:{PROP}", "status": "Open", "alert_type": "Rate Limited"}))
		self.fake.fail = None
		self.assertEqual(self._sync()["status"], "Success")
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"ga4_rate:{PROP}", "status": "Open"}), "a successful run resolves it")

	def test_13_low_remaining_tokens_stop_before_the_next_request(self):
		self._enable()
		self.fake.quota_hour = ga.MIN_HOURLY_TOKENS + 5  # the first answer leaves fewer than the floor
		r = self._sync()
		self.assertEqual((r["status"], r["error_class"]), ("Partial", RATE_LIMITED), "the Source report landed, the next request was not started")
		self.assertEqual(len(self.fake.bodies), 1)
		self.assertEqual(frappe.db.get_value("Ads Analytics Property", PROP, "last_status"), "Partial")

	def test_14_permission_denied_fails_and_alerts(self):
		self._enable()
		self.fake.fail = (403, {"error": {"code": 403, "message": "User does not have sufficient permissions for this property.", "status": "PERMISSION_DENIED"}}, {})
		r = self._sync()
		self.assertEqual((r["status"], r["error_class"]), ("Failed", PERMISSION))
		self.assertIn("Viewer", r["error"], "the error says what to do")
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"ga4_permission:{PROP}", "status": "Open"}))
		self.assertNotIn(TOKEN, frappe.db.get_value("Ads Analytics Property", PROP, "last_error") or "")
		hourly = []
		orig = analytics.enqueue
		analytics.enqueue = lambda name, job, **k: hourly.append((name, job))
		try:
			tasks.enqueue_analytics()
			self.assertNotIn(PROP, [n for n, _j in hourly], "hourly skips a property that needs a person")
			tasks.enqueue_analytics_settle()
			self.assertIn((PROP, "Settle"), hourly, "the daily settle keeps trying, so it recovers by itself")
		finally:
			analytics.enqueue = orig
		self.fake.fail = None
		self.assertEqual(self._sync()["status"], "Success")
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": f"ga4_permission:{PROP}", "status": "Open"}))

	def test_15_windows(self):
		prop = self._enable()
		today = analytics.property_today(prop)
		self.assertEqual(analytics.window(prop, "Settle"), (getdate(add_days(today, -14)), getdate(add_days(today, -1))))
		self.assertEqual(analytics.window(prop, "Metrics"), (getdate(add_days(today, -2)), today))
		prop.synced_from = getdate("2026-09-01")
		a, b = analytics.window(prop, "Backfill")
		self.assertEqual((a, b), (getdate(add_days(today, -480)), getdate("2026-08-31")), "an interrupted backfill resumes before what it already has")
		chunks = analytics._chunks("2026-01-01", "2026-03-15")
		self.assertEqual(chunks[0][1], getdate("2026-03-15"), "newest window first")
		self.assertTrue(all((getdate(b) - getdate(a)).days < analytics.WINDOW_DAYS for a, b in chunks))
		self.assertEqual(chunks[-1][0], getdate("2026-01-01"))

	# ─── reading ──────────────────────────────────────────────────────────────

	def test_20_web_report_totals_groups_and_source(self):
		self._enable()
		self._sync()
		r = analytics.web_report("2026-10-05", "2026-10-06", property=PROP)
		self.assertEqual(r["source"], "Analytics")
		t = r["totals"][0]
		self.assertEqual((t["sessions"], t["engaged_sessions"], t["key_events"], t["transactions"], t["purchase_revenue"], t["currency"]), (300, 195, 20, 8, 11499.5, "INR"))
		self.assertAlmostEqual(t["engagement_rate"], 195 / 300 * 100, msg="ratios from summed bases")
		self.assertIn("more than once", r["users_note"])
		self.assertEqual(r["rows"][0]["session_source"], "google", "sorted by sessions")
		by_camp = analytics.web_report("2026-10-05", "2026-10-06", property=PROP, group_by="campaign")
		row = next(x for x in by_camp["rows"] if x["campaign"] == self.camp)
		self.assertEqual((row["sessions"], row["campaign_title"]), (220, "QA Diwali Search"))
		lp = analytics.web_report("2026-10-05", "2026-10-06", property=PROP, group_by="landing_page")
		self.assertEqual({x["landing_page"] for x in lp["rows"]}, {"/diwali", "/"})
		days = analytics.web_report("2026-10-05", "2026-10-06", property=PROP, group_by="date")
		self.assertEqual([x["date"] for x in days["rows"]], ["2026-10-05", "2026-10-06"])
		self.assertRaises(frappe.ValidationError, analytics.web_report, "2026-10-05", "2026-10-06", group_by="nope")

	def test_21_scope(self):
		self._enable()
		self._sync()
		if self.company_b:
			frappe.set_user(U_B)
			permissions.clear_cache()
			r = analytics.web_report("2026-10-05", "2026-10-06")
			self.assertEqual((r["properties"], r["totals"]), ([], []), "a company B user sees nothing of company A")
			self.assertRaises(frappe.PermissionError, analytics.web_report, "2026-10-05", "2026-10-06", property=PROP)
			self.assertEqual(frappe.get_list("Ads Web Snapshot", filters={"property": PROP}), [])
		frappe.set_user(U_BRAND)
		permissions.clear_cache()
		self.assertEqual(analytics.web_report("2026-10-05", "2026-10-06")["properties"], [], "another brand's user does not see this brand's property")
		self.assertRaises(frappe.PermissionError, api_analytics.enable_property, CONN, PID, 0)

	def test_22_errors_are_scrubbed(self):
		e = ga.classify(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": f"bad {TOKEN}", "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}]}}, scrub=lambda s: s.replace(TOKEN, "***"))
		self.assertEqual(e.error_class, PERMISSION)
		self.assertNotIn(TOKEN, str(e))
		self.assertIn("Enable the Google Analytics Data API", str(e))
		e = ga.classify(429, {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}})
		self.assertEqual((e.error_class, e.retry_after), (RATE_LIMITED, 30))


class TestRealtimeRows(unittest.TestCase):
	"""runRealtimeReport rows (GA4Client.run_realtime): GA4's own names, metrics as floats."""

	def test_rows_carry_ga4_names(self):
		from excom.excom_ads.providers.google_analytics import realtime_rows

		data = {
			"dimensionHeaders": [{"name": "country"}, {"name": "city"}],
			"metricHeaders": [{"name": "activeUsers"}],
			"rows": [
				{"dimensionValues": [{"value": "India"}, {"value": "Jaipur"}], "metricValues": [{"value": "7"}]},
				{"dimensionValues": [{"value": "India"}, {"value": "(not set)"}], "metricValues": [{"value": "bad"}]},
			],
		}
		self.assertEqual(
			realtime_rows(data),
			[{"country": "India", "city": "Jaipur", "activeUsers": 7.0}, {"country": "India", "city": "(not set)", "activeUsers": 0.0}],
		)

	def test_no_rows(self):
		from excom.excom_ads.providers.google_analytics import realtime_rows

		self.assertEqual(realtime_rows({}), [])
