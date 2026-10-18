"""Excom Ads, Google: Ads Connection → discovery → enable → structure → metrics. Google is faked
(recorded REST shapes for API v26); no network.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_google_sync'}"
"""

import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from excom.excom_ads import api_google, google_auth, google_connect, sync, tasks
from excom.excom_ads.naming import mirror_name, snapshot_name
from excom.excom_ads.providers import google_ads
from excom.excom_ads.providers.base import INVALID, PERMISSION, RATE_LIMITED, RETRYABLE, TOKEN

P = "QA Google"
CONN = f"{P} MCC"
CONN_OAUTH = f"{P} OAuth"
WS = f"{P} WS"
WS2 = f"{P} WS Other"
MANAGER = "9990000001"
CID = "9990001234"
SUSPENDED_CID = "9990005678"
ACC = f"Google-{CID}"
SA_EMAIL = "excom-ads@qa-project.iam.gserviceaccount.com"
ACCESS_TOKEN = "ya29.QA_FAKE_ACCESS_TOKEN_0123456789"
CLIENT_SECRET = "GOCSPX-qa-client-secret-123456"
REFRESH_TOKEN = "1//0qa-refresh-token-abcdefghijklmnopqrstuvwxyz"
DEV_TOKEN = "qaDevToken0123456789AB"
U_MKT = "qa_google_mkt@example.com"
U_ADMIN_WS2 = "qa_google_admin_ws2@example.com"
USERS = (U_MKT, U_ADMIN_WS2)


def _make_key():
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric import rsa

	key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
	info = {
		"type": "service_account",
		"project_id": "qa-project",
		"private_key_id": "0123456789abcdef0123456789abcdef01234567",
		"private_key": pem,
		"client_email": SA_EMAIL,
		"client_id": "123456789012345678901",
		"auth_uri": "https://accounts.google.com/o/oauth2/auth",
		# A key file pointing its token_uri elsewhere must not redirect the signed assertion.
		"token_uri": "https://attacker.example.com/token",
		"auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
		"client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/excom-ads%40qa-project.iam.gserviceaccount.com",
		"universe_domain": "googleapis.com",
	}
	return key.public_key(), json.dumps(info, indent=2), pem


# ─── recorded shapes ──────────────────────────────────────────────────────────

def _customer(cid, name, manager=False, status="ENABLED"):
	return {"resourceName": f"customers/{cid}", "id": cid, "descriptiveName": name, "currencyCode": "INR", "timeZone": "Asia/Kolkata", "status": status, "manager": manager, "testAccount": False}


CAMPAIGNS = [
	({"id": "101", "name": "QA Google Search Brand", "status": "ENABLED", "servingStatus": "SERVING", "primaryStatus": "ELIGIBLE", "primaryStatusReasons": [], "advertisingChannelType": "SEARCH", "biddingStrategyType": "MANUAL_CPC", "startDateTime": "2026-10-01 00:00:00", "endDateTime": "2037-12-30 23:59:59"}, {"amountMicros": "1500000000", "period": "DAILY", "explicitlyShared": False}),
	({"id": "102", "name": "QA Google PMax", "status": "ENABLED", "servingStatus": "SERVING", "primaryStatus": "LIMITED", "primaryStatusReasons": ["BUDGET_CONSTRAINED"], "advertisingChannelType": "PERFORMANCE_MAX", "biddingStrategyType": "MAXIMIZE_CONVERSION_VALUE"}, {"amountMicros": "2500000000", "period": "DAILY", "explicitlyShared": False}),
	({"id": "103", "name": "QA Google Paused", "status": "PAUSED", "servingStatus": "SERVING", "primaryStatus": "PAUSED", "advertisingChannelType": "SEARCH", "biddingStrategyType": "TARGET_CPA"}, {"amountMicros": "900000000", "period": "DAILY", "explicitlyShared": True}),
	({"id": "104", "name": "QA Google YouTube", "status": "ENABLED", "servingStatus": "SERVING", "primaryStatus": "NOT_ELIGIBLE", "primaryStatusReasons": ["HAS_ADS_DISAPPROVED"], "advertisingChannelType": "VIDEO", "advertisingChannelSubType": "VIDEO_ACTION", "biddingStrategyType": "TARGET_CPM"}, {"totalAmountMicros": "30000000000", "period": "CUSTOM_PERIOD", "explicitlyShared": False}),
	({"id": "105", "name": "QA Google Ended", "status": "ENABLED", "servingStatus": "ENDED", "primaryStatus": "ENDED", "advertisingChannelType": "DISPLAY", "biddingStrategyType": "TARGET_CPA"}, {}),
	({"id": "106", "name": "QA Google Removed", "status": "REMOVED", "servingStatus": "NONE", "primaryStatus": "REMOVED", "advertisingChannelType": "SEARCH"}, {}),
]
# Performance Max (102) has no ad groups: asset groups only.
AD_GROUPS = [
	("101", "MANUAL_CPC", {"id": "201", "name": "QA Brand Exact", "status": "ENABLED", "primaryStatus": "ELIGIBLE", "type": "SEARCH_STANDARD", "cpcBidMicros": "25000000"}),
	("103", "TARGET_CPA", {"id": "202", "name": "QA Paused Group", "status": "ENABLED", "primaryStatus": "PAUSED", "primaryStatusReasons": ["CAMPAIGN_PAUSED"], "type": "SEARCH_STANDARD", "cpcBidMicros": "10000"}),
	("104", "TARGET_CPM", {"id": "203", "name": "QA Bumpers", "status": "ENABLED", "primaryStatus": "ELIGIBLE", "type": "VIDEO_BUMPER", "cpcBidMicros": "10000"}),
]
ADS = [
	("101", "201", {"status": "ENABLED", "primaryStatus": "ELIGIBLE", "ad": {"id": "301", "type": "RESPONSIVE_SEARCH_AD"}, "policySummary": {"approvalStatus": "APPROVED", "reviewStatus": "REVIEWED"}, "adStrength": "GOOD"}),
	("101", "201", {"status": "ENABLED", "primaryStatus": "NOT_ELIGIBLE", "primaryStatusReasons": ["AD_GROUP_AD_DISAPPROVED"], "ad": {"id": "302", "type": "RESPONSIVE_SEARCH_AD"}, "policySummary": {"approvalStatus": "DISAPPROVED", "reviewStatus": "REVIEWED"}}),
	("103", "202", {"status": "PAUSED", "primaryStatus": "PAUSED", "ad": {"id": "303", "type": "EXPANDED_TEXT_AD"}, "policySummary": {"approvalStatus": "APPROVED"}}),
	("104", "203", {"status": "ENABLED", "primaryStatus": "PENDING", "ad": {"id": "304", "name": "QA Bumper 6s", "type": "VIDEO_BUMPER_AD"}, "policySummary": {"approvalStatus": "UNKNOWN", "reviewStatus": "REVIEW_IN_PROGRESS"}}),
]
# Daily metrics per entity: (cost_micros, impressions, clicks, conversions, conversions_value, all_conversions)
CAMPAIGN_METRICS = {"101": (1000500000, 1000, 40, 4.0, 0.0, 5.0), "102": (2000250000, 5000, 100, 5.0, 9999.5, 6.0)}
AD_GROUP_METRICS = {("101", "201"): CAMPAIGN_METRICS["101"]}
AD_METRICS = {("101", "201", "301"): (600500000, 600, 25, 2.0, 0.0, 2.0), ("101", "201", "302"): (400000000, 400, 15, 2.0, 0.0, 3.0)}
# Conversions by category per entity. QUALIFIED_LEAD is a later stage of the same lead: not added.
CAMPAIGN_CATS = {"101": [("SUBMIT_LEAD_FORM", 2.0, 0.0), ("PHONE_CALL_LEAD", 1.0, 0.0), ("QUALIFIED_LEAD", 1.0, 0.0)], "102": [("PURCHASE", 5.0, 9999.5)]}
AD_GROUP_CATS = {("101", "201"): CAMPAIGN_CATS["101"]}
AD_CATS = {("101", "201", "301"): [("SUBMIT_LEAD_FORM", 2.0, 0.0)], ("101", "201", "302"): [("PHONE_CALL_LEAD", 1.0, 0.0), ("QUALIFIED_LEAD", 1.0, 0.0)]}


def _metrics(t, bump=0):
	return {"costMicros": str(t[0] + bump), "impressions": str(t[1]), "clicks": str(t[2]), "conversions": t[3], "conversionsValue": t[4], "allConversions": t[5]}


class FakeGoogle:
	"""Answers the token endpoint and the Google Ads REST calls the connector makes."""

	def __init__(self, public_key):
		self.public_key = public_key
		self.calls = []
		self.reset()

	def reset(self):
		self.token_fail = None
		self.search_fail = None
		self.fail_times = None  # fail only the next N searches
		self.cost_bump = 0
		self.granted_scope = None
		self.token_calls = 0
		self.last_headers = {}
		self.last_claims = None
		self.last_form = None

	def __call__(self, method, url, headers=None, json_body=None, form=None, timeout=60):
		self.calls.append((method, url))
		if url == google_auth.TOKEN_URI:
			return self._token(form)
		assert url.startswith(f"{google_ads.API}/v26/"), url
		self.last_headers = dict(headers or {})
		assert headers["Authorization"] == f"Bearer {ACCESS_TOKEN}", "every API call carries the bearer token"
		if url.endswith("customers:listAccessibleCustomers"):
			assert method == "GET" and "login-customer-id" not in headers
			return 200, {"resourceNames": [f"customers/{MANAGER}"]}, {}
		m = re.search(r"/customers/(\d+)/googleAds:search$", url)
		assert m and method == "POST", url
		if self.search_fail and (self.fail_times is None or self.fail_times > 0):
			if self.fail_times is not None:
				self.fail_times -= 1
			return self.search_fail
		return 200, self._search(m.group(1), json_body["query"], json_body.get("pageToken")), {}

	def _token(self, form):
		import jwt

		self.token_calls += 1
		self.last_form = dict(form)
		if self.token_fail:
			return self.token_fail
		if form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer":
			self.last_claims = jwt.decode(form["assertion"], self.public_key, algorithms=["RS256"], audience=google_auth.TOKEN_URI)
			return 200, {"access_token": ACCESS_TOKEN, "expires_in": 3599, "token_type": "Bearer"}, {}
		assert form["grant_type"] == "refresh_token"
		return 200, {"access_token": ACCESS_TOKEN, "expires_in": 3599, "token_type": "Bearer", "scope": self.granted_scope or google_auth.ADWORDS_SCOPE}, {}

	def _search(self, cid, q, page_token):
		resource = re.search(r"FROM (\w+)", q).group(1)
		dates = re.search(r"segments\.date BETWEEN '([\d-]+)' AND '([\d-]+)'", q)
		if resource == "customer_client":
			assert cid == MANAGER
			return {"results": [{"customerClient": {**_customer(MANAGER, "QA Google Manager", manager=True), "level": "0"}}, {"customerClient": {**_customer(CID, "QA Google Account"), "level": "1"}}, {"customerClient": {**_customer(SUSPENDED_CID, "QA Google Old", status="SUSPENDED"), "level": "1", "hidden": True}}]}
		if resource == "customer" and not dates:
			return {"results": [{"customer": _customer(cid, "QA Google Manager" if cid == MANAGER else "QA Google Account", manager=cid == MANAGER)}]}
		assert cid == CID, cid
		if dates:
			return {"results": self._metric_rows(resource, q, getdate(dates.group(1)), getdate(dates.group(2)))}
		if resource == "campaign":
			rows = [{"campaign": {"resourceName": f"customers/{CID}/campaigns/{c['id']}", **c}, "campaignBudget": b} for c, b in CAMPAIGNS]
			# Two pages, to prove nextPageToken is followed with the same query.
			if page_token == "p2":
				return {"results": rows[3:], "fieldMask": "campaign.id"}
			return {"results": rows[:3], "nextPageToken": "p2"}
		if resource == "ad_group":
			return {"results": [{"campaign": {"id": c, "biddingStrategyType": bid, "advertisingChannelType": "VIDEO" if c == "104" else "SEARCH"}, "adGroup": g} for c, bid, g in AD_GROUPS]}
		if resource == "ad_group_ad":
			return {"results": [{"campaign": {"id": c, "advertisingChannelType": "VIDEO" if c == "104" else "SEARCH"}, "adGroup": {"id": g}, "adGroupAd": a} for c, g, a in ADS]}
		raise AssertionError(q)

	def _metric_rows(self, resource, q, since, until):
		by_cat = "segments.conversion_action_category" in q
		if by_cat:
			assert "metrics.cost_micros" not in q, "conversion categories cannot be selected with cost"
		table = {
			"customer": ({(): (sum(v[0] for v in CAMPAIGN_METRICS.values()), 6000, 140, 9.0, 9999.5, 11.0)}, {(): CAMPAIGN_CATS["101"] + CAMPAIGN_CATS["102"]}),
			"campaign": ({(k,): v for k, v in CAMPAIGN_METRICS.items()}, {(k,): v for k, v in CAMPAIGN_CATS.items()}),
			"ad_group": (AD_GROUP_METRICS, AD_GROUP_CATS),
			"ad_group_ad": (AD_METRICS, AD_CATS),
		}[resource]
		out = []
		day = since
		while day <= until:
			for key, val in (table[1] if by_cat else table[0]).items():
				ids = {}
				if key:
					ids["campaign"] = {"id": key[0]}
				if len(key) > 1:
					ids["adGroup"] = {"id": key[1]}
				if len(key) > 2:
					ids["adGroupAd"] = {"ad": {"id": key[2]}}
				if by_cat:
					for cat, conv, value in val:
						out.append({**ids, "segments": {"date": str(day), "conversionActionCategory": cat}, "metrics": {"conversions": conv, "conversionsValue": value}})
				else:
					out.append({**ids, "segments": {"date": str(day)}, "metrics": _metrics(val, self.cost_bump)})
			day = add_days(day, 1)
		return out


class TestAdsGoogleSync(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		public_key, cls.key_json, pem = _make_key()
		cls.pem_fragment = pem.splitlines()[5]
		for ws in (WS, WS2):
			frappe.get_doc({"doctype": "Ads Workspace", "workspace_name": ws, "company": cls.company}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Ads Connection", "connection_name": CONN, "provider": "Google", "company": cls.company, "workspace": WS, "auth_mode": "Service Account", "service_account_key": cls.key_json, "login_customer_id": "999-000-0001"}).insert(ignore_permissions=True)
		for u, role in ((U_MKT, "Excom Marketing"), (U_ADMIN_WS2, "Excom Admin")):
			frappe.get_doc({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": role}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "User Permission", "user": U_MKT, "allow": "Company", "for_value": cls.company, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "User Permission", "user": U_ADMIN_WS2, "allow": "Ads Workspace", "for_value": WS2, "apply_to_all_doctypes": 1}).insert(ignore_permissions=True)
		frappe.db.commit()
		cls.google = FakeGoogle(public_key)
		cls._orig = (google_auth.http_request, google_ads._sleep, frappe.enqueue)
		google_auth.http_request = cls.google
		cls.sleeps = []
		google_ads._sleep = cls.sleeps.append
		cls.enqueued = []
		frappe.enqueue = lambda *a, **k: cls.enqueued.append(k) or object()

	@classmethod
	def tearDownClass(cls):
		google_auth.http_request, google_ads._sleep, frappe.enqueue = cls._orig
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		for dt in ("Ads Metric Snapshot", "Ads Advertisement", "Ads Ad Group", "Ads Campaign", "Ads Sync Job", "Ads Alert"):
			frappe.db.delete(dt, {"account": ACC})
		frappe.db.delete("Ads Alert", {"reference_doctype": "Ads Connection", "reference_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Account", {"name": ACC})
		for name in frappe.get_all("Ads Connection", {"name": ["like", f"{P}%"]}, pluck="name"):
			frappe.db.delete("Version", {"ref_doctype": "Ads Connection", "docname": name})
			frappe.delete_doc("Ads Connection", name, force=True, ignore_permissions=True)
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.google.reset()
		self.enqueued.clear()
		self.sleeps.clear()
		google_auth.GoogleCredentials.load(CONN).forget_token()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ─── helpers ──────────────────────────────────────────────────────────────

	def _account(self):
		if not frappe.db.exists("Ads Account", ACC):
			google_connect.enable(CONN, CID)
		return frappe.get_doc("Ads Account", ACC)

	def _count(self, dt, **f):
		return frappe.db.count(dt, {"account": ACC, **f})

	def _secrets(self):
		return [self.pem_fragment, ACCESS_TOKEN, CLIENT_SECRET, REFRESH_TOKEN, DEV_TOKEN, '"private_key"']

	def assertNoSecrets(self, text, where=""):
		for s in self._secrets():
			self.assertNotIn(s, str(text or ""), f"credential leaked into {where}")

	# ─── connection ───────────────────────────────────────────────────────────

	def test_01_connection_stores_the_key_encrypted_and_never_shows_it(self):
		conn = frappe.get_doc("Ads Connection", CONN)
		self.assertEqual((conn.service_account_email, conn.login_customer_id, conn.scopes, conn.api_version), (SA_EMAIL, MANAGER, google_auth.ADWORDS_SCOPE, "v26"))
		self.assertGreater(len(self.key_json), 1700, "a real-size key file")
		stored = conn.get_password("service_account_key")
		self.assertEqual(json.loads(stored)["private_key"], json.loads(self.key_json)["private_key"], "the full key survives the round trip")
		column = frappe.db.get_value("Ads Connection", CONN, "service_account_key")
		self.assertEqual(set(column), {"*"}, "the document column holds only asterisks")
		self.assertNoSecrets(frappe.as_json(conn.as_dict()), "as_dict")
		from frappe.client import get as client_get

		self.assertNoSecrets(frappe.as_json(client_get("Ads Connection", CONN)), "REST get")
		self.assertNoSecrets(frappe.as_json(frappe.get_all("Version", {"ref_doctype": "Ads Connection", "docname": CONN}, ["data"])), "Version")
		# Saving with the stars unchanged keeps the key.
		conn.workspace = WS
		conn.save(ignore_permissions=True)
		self.assertEqual(json.loads(frappe.get_doc("Ads Connection", CONN).get_password("service_account_key"))["client_email"], SA_EMAIL)
		# A broken key is refused without being echoed.
		bad = frappe.get_doc({"doctype": "Ads Connection", "connection_name": f"{P} Bad", "provider": "Google", "company": self.company, "auth_mode": "Service Account", "service_account_key": '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\\nSECRETSECRET\\n-----END PRIVATE KEY-----\\n", "client_email": "x@y"}'})
		with self.assertRaises(frappe.ValidationError) as ctx:
			bad.insert(ignore_permissions=True)
		self.assertNotIn("SECRETSECRET", str(ctx.exception))
		self.assertFalse(frappe.db.exists("Ads Connection", f"{P} Bad"))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Ads Connection", "connection_name": f"{P} Bad", "provider": "Google", "company": self.company, "auth_mode": "Service Account", "service_account_key": "not json"}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Ads Connection", "connection_name": f"{P} Bad", "provider": "Google", "company": self.company, "auth_mode": "Service Account", "service_account_key": self.key_json, "login_customer_id": "12-AB"}).insert(ignore_permissions=True)

	def test_02_connection_test_signs_a_jwt_and_records_health(self):
		r = google_connect.test(CONN)
		self.assertTrue(r["ok"], r)
		self.assertEqual(r["accessible_accounts"], 1)
		claims = self.google.last_claims
		self.assertEqual((claims["iss"], claims["scope"], claims["aud"]), (SA_EMAIL, google_auth.ADWORDS_SCOPE, google_auth.TOKEN_URI), "token_uri from the key file is never used")
		self.assertTrue(all(u.startswith(("https://oauth2.googleapis.com/", "https://googleads.googleapis.com/")) for _, u in self.google.calls))
		self.assertEqual(self.google.last_headers.get("login-customer-id"), MANAGER)
		self.assertNotIn("developer-token", self.google.last_headers, "developer tokens are retired: sent only if configured")
		conn = frappe.get_doc("Ads Connection", CONN)
		self.assertEqual((conn.status, conn.token_status, conn.accessible_accounts, conn.last_error), ("Active", "OK", 1, None))
		self.assertIn(google_auth.ADWORDS_SCOPE, conn.granted_scopes)
		self.assertIsNotNone(conn.last_checked_at)
		self.assertNoSecrets(frappe.as_json(r), "test response")

	def test_03_access_token_is_cached_and_reused(self):
		google_connect.test(CONN)
		self.assertEqual(self.google.token_calls, 1)
		google_connect.test(CONN)
		self.assertEqual(self.google.token_calls, 1, "a second job reuses the cached token")
		raw = [frappe.cache.get_value(k, expires=True) for k in [google_auth.GoogleCredentials.load(CONN)._cache_key((google_auth.ADWORDS_SCOPE,))]]
		self.assertTrue(raw[0])
		self.assertNotIn(ACCESS_TOKEN, str(raw[0]), "the cached token is encrypted")

	# ─── discovery and enabling ───────────────────────────────────────────────

	def test_04_discovery_lists_the_manager_tree_and_enable_creates_the_account(self):
		rows = google_connect.discover(CONN)
		by_id = {r["customer_id"]: r for r in rows}
		self.assertEqual(set(by_id), {MANAGER, CID, SUSPENDED_CID})
		self.assertTrue(by_id[MANAGER]["manager"])
		self.assertEqual((by_id[CID]["customer_id_display"], by_id[CID]["currency"], by_id[CID]["status"], by_id[CID]["level"]), ("999-000-1234", "INR", "Enabled", 1))
		self.assertEqual(by_id[SUSPENDED_CID]["status"], "Suspended")
		self.assertIsNone(by_id[CID]["enabled_as"])
		with self.assertRaises(frappe.ValidationError):
			google_connect.enable(CONN, MANAGER)
		r = google_connect.enable(CONN, "999-000-1234")
		self.assertEqual(r["account"], ACC)
		acc = frappe.get_doc("Ads Account", ACC)
		self.assertEqual((acc.provider, acc.external_id, acc.connection, acc.currency, acc.timezone_name, acc.workspace, acc.company, acc.sync_enabled), ("Google", CID, CONN, "INR", "Asia/Kolkata", WS, self.company, 1))
		self.assertTrue([k for k in self.enqueued if k.get("account") == ACC and k.get("job_type") == "Structure"], "enabling queues the first structure sync")
		self.assertEqual({r["customer_id"]: r["enabled_as"] for r in google_connect.discover(CONN)}[CID], ACC)
		google_connect.enable(CONN, CID, enabled=0)
		self.assertEqual(frappe.db.get_value("Ads Account", ACC, "sync_enabled"), 0)
		google_connect.enable(CONN, CID)
		self.assertEqual(frappe.db.get_value("Ads Account", ACC, "sync_enabled"), 1)
		self.assertEqual(frappe.db.count("Ads Account", {"external_id": CID}), 1)

	# ─── structure ────────────────────────────────────────────────────────────

	def test_05_structure_sync_maps_status_money_pmax_and_video(self):
		acc = self._account()
		r = sync.run_job(acc.name, "Structure", triggered_by="test")
		self.assertEqual(r["status"], "Success", r)
		acc.reload()
		self.assertEqual((acc.status, acc.platform_status, acc.billing_status, acc.can_read, acc.can_billing, acc.can_write), ("Active", "ENABLED", "Not Available", 1, 0, 0))
		self.assertEqual(self._count("Ads Campaign"), 6, "both pages of campaigns were read")
		camp = lambda i: frappe.get_doc("Ads Campaign", mirror_name(acc.name, i))
		c = camp("101")
		self.assertEqual((c.status, c.configured_status, c.effective_status, c.objective, c.bid_strategy, c.daily_budget), ("Active", "ENABLED", "ELIGIBLE", "SEARCH", "MANUAL_CPC", 1500.0))
		from datetime import datetime
		from zoneinfo import ZoneInfo

		start = datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)
		self.assertEqual(frappe.utils.get_datetime(c.start_time), start, "start time is read in the customer's time zone")
		self.assertEqual((camp("102").status, camp("102").objective), ("Active", "PERFORMANCE_MAX"))
		self.assertEqual((camp("103").status, camp("103").daily_budget or 0), ("Paused", 0), "a shared budget is not put on each campaign")
		yt = camp("104")
		self.assertEqual((yt.status, yt.lifetime_budget), ("Rejected", 30000.0))
		self.assertTrue(json.loads(yt.config_json).get("api_read_only"), "video campaigns are flagged read-only")
		self.assertEqual((camp("105").status, camp("106").status), ("Archived", "Deleted"))
		self.assertEqual(self._count("Ads Ad Group"), 3)
		self.assertEqual(self._count("Ads Ad Group", campaign=mirror_name(acc.name, "102")), 0, "Performance Max gets no invented ad groups")
		g = frappe.get_doc("Ads Ad Group", mirror_name(acc.name, "201"))
		self.assertEqual((g.status, g.campaign, g.bid_amount), ("Active", c.name, 25.0))
		self.assertEqual(frappe.db.get_value("Ads Ad Group", mirror_name(acc.name, "202"), "status"), "Paused")
		self.assertFalse(frappe.db.get_value("Ads Ad Group", mirror_name(acc.name, "203"), "bid_amount"), "a CPC bid ignored by automated bidding is not reported")
		ad = lambda k: frappe.get_doc("Ads Advertisement", mirror_name(acc.name, k))
		a1 = ad("201~301")
		self.assertEqual((a1.status, a1.ad_name, a1.ad_group, a1.campaign, a1.creative_id), ("Active", "Responsive Search Ad 301", g.name, c.name, "301"))
		a2 = ad("201~302")
		self.assertEqual(a2.status, "Rejected")
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"ad_rejected:{a2.name}", "alert_type": "Ad Disapproved", "status": "Open"}))
		self.assertEqual((ad("202~303").status, ad("203~304").status, ad("203~304").ad_name), ("Paused", "In Review", "QA Bumper 6s"))

	def test_06_structure_resync_never_duplicates(self):
		acc = self._account()
		sync.run_job(acc.name, "Structure")
		counts = [self._count(d) for d in ("Ads Campaign", "Ads Ad Group", "Ads Advertisement")]
		self.assertEqual(sync.run_job(acc.name, "Structure")["status"], "Success")
		self.assertEqual(counts, [self._count(d) for d in ("Ads Campaign", "Ads Ad Group", "Ads Advertisement")])
		self.assertEqual(counts, [6, 3, 4])

	# ─── metrics ──────────────────────────────────────────────────────────────

	def test_07_backfill_converts_micros_counts_leads_honestly_and_is_idempotent(self):
		acc = self._account()
		sync.run_job(acc.name, "Structure")
		since, until = "2026-10-01", "2026-10-03"
		r = sync.run_job(acc.name, "Backfill", since=since, until=until)
		self.assertEqual(r["status"], "Success", r)
		n = self._count("Ads Metric Snapshot")
		self.assertEqual(n, 18, "3 days × (1 account + 2 campaigns + 1 ad group + 2 ads)")
		sync.run_job(acc.name, "Backfill", since=since, until=until)
		self.assertEqual(self._count("Ads Metric Snapshot"), n, "re-sync never duplicates")
		day = frappe.get_doc("Ads Metric Snapshot", snapshot_name(acc.name, "Account", CID, since))
		self.assertAlmostEqual(day.spend, 3000.75)
		self.assertEqual((day.impressions, day.clicks, day.currency), (6000, 140, "INR"))
		self.assertEqual(day.leads, 3, "lead-form + call leads; the qualified-lead stage of the same leads is not added")
		self.assertEqual((day.purchases, day.purchase_value), (5, 9999.5))
		m = json.loads(day.metrics_json)
		self.assertEqual((m["cost_micros"], m["conversions"], m["conversions_value"], m["all_conversions"]), (3000750000, 9.0, 9999.5, 11.0))
		self.assertEqual(m["by_category"]["QUALIFIED_LEAD"]["conversions"], 1.0, "kept raw for later reading")
		self.assertIn("reach", m["not_reported"])
		pmax = frappe.get_doc("Ads Metric Snapshot", snapshot_name(acc.name, "Campaign", "102", since))
		self.assertEqual((pmax.campaign, pmax.ad_group, pmax.spend, pmax.purchases), (mirror_name(acc.name, "102"), None, 2000.25, 5))
		self.assertEqual(self._count("Ads Metric Snapshot", level="Ad Group", campaign=mirror_name(acc.name, "102")), 0)
		ad = frappe.get_doc("Ads Metric Snapshot", snapshot_name(acc.name, "Ad", "201~302", since))
		self.assertEqual((ad.advertisement, ad.ad_group, ad.campaign, ad.spend, ad.leads), (mirror_name(acc.name, "201~302"), mirror_name(acc.name, "201"), mirror_name(acc.name, "101"), 400.0, 1))
		# A settle re-pull with revised cost changes the same row.
		self.google.cost_bump = 10_000_000
		sync.run_job(acc.name, "Settle", since=since, until=until)
		self.assertEqual(self._count("Ads Metric Snapshot"), n)
		self.assertAlmostEqual(frappe.db.get_value("Ads Metric Snapshot", snapshot_name(acc.name, "Campaign", "101", since), "spend"), 1010.5)
		acc.reload()
		self.assertEqual((str(acc.metrics_synced_from), str(acc.metrics_synced_until)), (since, until))

	# ─── failures ─────────────────────────────────────────────────────────────

	def test_08_revoked_credential_is_a_token_error_with_alerts_and_recovery(self):
		acc = self._account()
		self.google.token_fail = (400, {"error": "invalid_grant", "error_description": "Invalid JWT Signature."}, {})
		r = sync.run_job(acc.name, "Structure")
		self.assertEqual((r["status"], r["error_class"]), ("Failed", "Token"), r)
		acc.reload()
		self.assertEqual(acc.error_class, "Token")
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"token:{acc.name}", "status": "Open", "alert_type": "Token Problem"}))
		tasks.enqueue_intraday()
		self.assertFalse([k for k in self.enqueued if k.get("account") == acc.name], "no metric pulls while the token is broken")
		t = google_connect.test(CONN)
		self.assertEqual((t["ok"], t["error_class"]), (False, "Token"))
		conn = frappe.get_doc("Ads Connection", CONN)
		self.assertEqual((conn.status, conn.token_status), ("Error", "Token Problem"))
		self.assertIn("invalid_grant", conn.last_error)
		self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"connection_token:{CONN}", "status": "Open", "company": self.company}))
		# Nothing that came from a credential appears in any error, alert or response.
		job = frappe.get_all("Ads Sync Job", {"account": acc.name}, ["error"], order_by="creation desc", limit=1)[0]
		alerts = frappe.get_all("Ads Alert", {"dedupe_key": ["in", [f"token:{acc.name}", f"connection_token:{CONN}"]]}, ["title", "message"])
		for text, where in ((job.error, "Ads Sync Job"), (acc.last_error, "Ads Account"), (conn.last_error, "Ads Connection"), (frappe.as_json(alerts), "Ads Alert"), (frappe.as_json(t), "test response")):
			self.assertTrue(text)
			self.assertNoSecrets(text, where)
		# Recovery clears every alert.
		self.google.token_fail = None
		self.assertEqual(sync.run_job(acc.name, "Structure")["status"], "Success")
		self.assertTrue(google_connect.test(CONN)["ok"])
		acc.reload()
		self.assertEqual((acc.error_class or "", acc.consecutive_failures), ("", 0))
		for key in (f"token:{acc.name}", f"connection_token:{CONN}"):
			self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": key, "status": ["!=", "Resolved"]}), key)
		self.assertEqual(frappe.db.get_value("Ads Connection", CONN, "token_status"), "OK")

	def test_09_expired_access_token_is_refreshed_once(self):
		acc = self._account()
		unauth = (401, {"error": {"code": 401, "message": "Request had invalid authentication credentials.", "status": "UNAUTHENTICATED", "details": [{"@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure", "errors": [{"errorCode": {"authenticationError": "OAUTH_TOKEN_INVALID"}, "message": "Oauth token is invalid."}], "requestId": "qa-req-1"}]}}, {})
		self.google.search_fail, self.google.fail_times = unauth, 1
		self.assertEqual(sync.run_job(acc.name, "Structure")["status"], "Success", "one fresh token exchange recovers")
		self.assertEqual(self.google.token_calls, 2)
		self.google.search_fail, self.google.fail_times = unauth, None
		r = sync.run_job(acc.name, "Structure")
		self.assertEqual((r["status"], r["error_class"]), ("Failed", "Token"))
		self.assertIn("qa-req-1", frappe.db.get_value("Ads Account", acc.name, "last_error"), "Google's request ID is kept for support")

	def test_10_error_classes(self):
		failure = lambda code, msg="x", **extra: {"@type": "type.googleapis.com/google.ads.googleads.v26.errors.GoogleAdsFailure", "errors": [{"errorCode": code, "message": msg, **extra}], "requestId": "r1"}
		quota = google_ads.classify(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Too many requests", "details": [failure({"quotaError": "RESOURCE_EXHAUSTED"}, details={"quotaErrorDetails": {"rateScope": "ACCOUNT", "rateName": "Requests per account", "retryDelay": "30s"}})]}})
		self.assertEqual((quota.error_class, quota.retry_after, quota.code), (RATE_LIMITED, 30, "quotaError.RESOURCE_EXHAUSTED"))
		self.assertEqual(google_ads.classify(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"}]}}).retry_after, 13)
		cases = [
			(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [failure({"authorizationError": "USER_PERMISSION_DENIED"})]}}, PERMISSION),
			(401, {"error": {"code": 401, "status": "UNAUTHENTICATED", "details": [failure({"authenticationError": "NOT_ADS_USER"})]}}, PERMISSION),
			(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [failure({"authorizationError": "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION"})]}}, PERMISSION),
			(403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [failure({"authorizationError": "CUSTOMER_NOT_ENABLED"})]}}, INVALID),
			(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "details": [failure({"queryError": "UNRECOGNIZED_FIELD"})]}}, INVALID),
			(500, {"error": {"code": 500, "status": "INTERNAL", "details": [failure({"internalError": "TRANSIENT_ERROR"})]}}, RETRYABLE),
			(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "The service is currently unavailable."}}, RETRYABLE),
			(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}, TOKEN),
			# searchStream wraps errors in an array; the doc example has no "error" wrapper at all.
			(400, [{"error": {"code": 400, "status": "INVALID_ARGUMENT", "details": [failure({"requestError": "INVALID_CUSTOMER_ID"})]}}], INVALID),
			(400, {"code": 3, "message": "The request was invalid.", "details": [failure({"fieldError": "REQUIRED"})]}, INVALID),
			(502, {"error": {"code": 502, "message": "<html>Bad gateway</html>"}}, RETRYABLE),
		]
		for status, body, cls in cases:
			self.assertEqual(google_ads.classify(status, body).error_class, cls, body)
		self.assertIn("login-customer-id", str(google_ads.classify(*cases[0][:2])))

	def test_11_transient_errors_retry_in_call_and_rate_limits_stop_the_job(self):
		acc = self._account()
		sync.run_job(acc.name, "Structure")
		self.google.search_fail, self.google.fail_times = (503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "Try again"}}, {}), 1
		self.assertEqual(sync.run_job(acc.name, "Backfill", since="2026-10-01", until="2026-10-01")["status"], "Success")
		self.assertEqual(self.sleeps, [2], "one backoff, then the call worked")
		self.google.search_fail, self.google.fail_times = (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Too many requests"}}, {"Retry-After": "60"}), None
		r = sync.run_job(acc.name, "Backfill", since="2026-10-01", until="2026-10-01")
		self.assertEqual((r["status"], r["error_class"]), ("Failed", "Rate Limited"))

	def test_12_status_mapping(self):
		cs = google_ads.campaign_status
		self.assertEqual([cs("ENABLED", "ELIGIBLE"), cs("ENABLED", "LEARNING"), cs("ENABLED", "LIMITED"), cs("PAUSED", "PAUSED"), cs("REMOVED"), cs("ENABLED", "ENDED"), cs("ENABLED", "MISCONFIGURED"), cs("ENABLED", "PENDING", reasons=["MOST_ADS_UNDER_REVIEW"]), cs("ENABLED", "PENDING", reasons=["CAMPAIGN_PENDING"]), cs("ENABLED", "ELIGIBLE", "SUSPENDED"), cs("ENABLED", None, "SERVING"), cs("UNKNOWN")], ["Active", "Active", "Active", "Paused", "Deleted", "Archived", "Error", "In Review", "Active", "Error", "Active", "Unknown"])
		gs = google_ads.ad_group_status
		self.assertEqual([gs("ENABLED", "ELIGIBLE"), gs("ENABLED", "NOT_ELIGIBLE", ["HAS_ADS_DISAPPROVED"]), gs("ENABLED", "NOT_ELIGIBLE", ["CAMPAIGN_ENDED"]), gs("ENABLED", "PENDING", ["MOST_ADS_UNDER_REVIEW"]), gs("REMOVED")], ["Active", "Rejected", "Archived", "In Review", "Deleted"])
		ads = google_ads.ad_status
		self.assertEqual([ads("ENABLED", "ELIGIBLE", approval="APPROVED_LIMITED"), ads("ENABLED", "NOT_ELIGIBLE", approval="DISAPPROVED", review="UNDER_APPEAL"), ads("ENABLED", "PENDING", review="REVIEW_IN_PROGRESS"), ads("ENABLED", "PAUSED", ["AD_GROUP_PAUSED"]), ads("ENABLED", "NOT_ELIGIBLE", ["CAMPAIGN_ENDED"]), ads("REMOVED")], ["Active", "Rejected", "In Review", "Paused", "Archived", "Deleted"])
		self.assertEqual((google_ads.micros("1234567"), google_ads.micros(None)), (1.234567, None))

	# ─── OAuth mode and endpoints ─────────────────────────────────────────────

	def test_13_oauth_refresh_token_mode_and_optional_developer_token(self):
		frappe.get_doc({"doctype": "Ads Connection", "connection_name": CONN_OAUTH, "provider": "Google", "company": self.company, "workspace": WS, "auth_mode": "OAuth Refresh Token", "oauth_client_id": "qa.apps.googleusercontent.com", "oauth_client_secret": CLIENT_SECRET, "refresh_token": REFRESH_TOKEN, "developer_token": DEV_TOKEN, "login_customer_id": MANAGER}).insert(ignore_permissions=True)
		try:
			r = google_connect.test(CONN_OAUTH)
			self.assertTrue(r["ok"], r)
			self.assertEqual((self.google.last_form["grant_type"], self.google.last_form["refresh_token"]), ("refresh_token", REFRESH_TOKEN))
			self.assertEqual(self.google.last_headers.get("developer-token"), DEV_TOKEN, "sent when configured")
			# A consent that did not include the Ads scope is a permission problem, said plainly.
			google_auth.GoogleCredentials.load(CONN_OAUTH).forget_token()
			self.google.granted_scope = "https://www.googleapis.com/auth/analytics.readonly"
			r = google_connect.test(CONN_OAUTH)
			self.assertEqual((r["ok"], r["error_class"]), (False, "Permission"))
			self.assertIn("adwords", r["error"])
			self.assertNoSecrets(frappe.as_json(r), "test response")
			self.assertEqual(frappe.db.get_value("Ads Connection", CONN_OAUTH, "token_status"), "Permission Missing")
			self.assertTrue(frappe.db.exists("Ads Alert", {"dedupe_key": f"connection_permission:{CONN_OAUTH}", "status": "Open", "alert_type": "Permission Missing"}))
		finally:
			google_auth.GoogleCredentials.load(CONN_OAUTH).forget_token()

	def test_14_endpoints_are_admin_only_scoped_and_leak_nothing(self):
		self._account()
		out = api_google.list_customers(CONN)
		self.assertTrue(out)
		self.assertNoSecrets(frappe.as_json(out), "list_customers")
		self.assertNoSecrets(frappe.as_json(api_google.enable_customer(CONN, CID)), "enable_customer")
		self.assertNoSecrets(frappe.as_json(api_google.test_connection(CONN)), "test_connection")
		from excom.excom_ads import permissions

		for user in (U_MKT, U_ADMIN_WS2):
			frappe.set_user(user)
			permissions.clear_cache()
			self.assertRaises(frappe.PermissionError, api_google.list_customers, CONN)
			self.assertRaises(frappe.PermissionError, api_google.test_connection, CONN)
			self.assertRaises(frappe.PermissionError, api_google.enable_customer, CONN, CID)
			if user == U_MKT:
				self.assertRaises(frappe.PermissionError, frappe.get_list, "Ads Connection", filters={"name": CONN})
			else:
				self.assertEqual(frappe.get_list("Ads Connection", filters={"name": CONN}, pluck="name"), [], "an admin scoped to another workspace does not see it")
		frappe.set_user(U_MKT)
		permissions.clear_cache()
		self.assertEqual(frappe.get_list("Ads Account", filters={"name": ACC}, pluck="name"), [ACC], "marketing still reads the ad account itself")
		self.assertFalse(frappe.has_permission("Ads Connection", "read", user=U_MKT))
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def test_15_account_and_connection_must_agree(self):
		acc = self._account()
		acc.connection = CONN
		acc.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Ads Account", "account_name": f"{P} Wrong", "provider": "Meta", "external_id": "act_99901", "workspace": WS, "connection": CONN}).insert(ignore_permissions=True)
		self.assertFalse(google_ads.GoogleAdsProvider.capability("read_billing").supported)
		# A disabled connection stops its accounts' syncs with a clear reason, without a network call.
		frappe.db.set_value("Ads Connection", CONN, "status", "Disabled")
		try:
			calls = len(self.google.calls)
			r = sync.run_job(acc.name, "Structure")
			self.assertEqual((r["status"], r["error_class"]), ("Failed", "Invalid"))
			self.assertIn("disabled", r["error"])
			self.assertEqual(len(self.google.calls), calls)
		finally:
			frappe.db.set_value("Ads Connection", CONN, "status", "Active")
		self.assertEqual(sync.run_job(acc.name, "Structure")["status"], "Success")
