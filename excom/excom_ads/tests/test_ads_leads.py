"""Excom Ads, leads and attribution: Meta lead ads, Google Ads lead forms, click-to-WhatsApp referrals,
website and order touches → Ads Attribution Touch rows + the Lead's ad fields. Payloads follow the
platforms' published shapes; no network (Graph fetch is replaced, webhooks are called in-process).

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_leads'}"
"""

import json
from unittest.mock import MagicMock

import frappe
from frappe.tests.utils import FrappeTestCase

from excom.excom.intake.adapters import meta as meta_adapter
from excom.excom.services import crm_compat
from excom.excom.services import crm_gateway as gw
from excom.excom.services import intake
from excom.excom.utils import webhook
from excom.excom_ads import attribution, permissions, sync
from excom.excom_ads.naming import mirror_name, touch_name

P = "QA Leads"
TOUCH = "Ads Attribution Touch"
META_EXT = "act_7770001"
GOOGLE_EXT = "7770002222"
META_ACC = f"Meta-{META_EXT}"
GOOGLE_ACC = f"Google-{GOOGLE_EXT}"
WA_PHONE_ID = "qaleads-phone-1"
GOOGLE_KEY = "qaleads-google-key-0123456789"
U_NONE = "qa.leads.none@example.com"
U_META = "qa.leads.meta@example.com"
USERS = (U_NONE, U_META)
PHONES = [f"+9199000088{i:02d}" for i in range(20)]

META_TREE = {
	"campaigns": [{"external_id": "qlc1", "campaign_name": "QA Leads Diwali B2B", "status": "Active"}],
	"groups": [{"external_id": "qls1", "ad_group_name": "QA Leads Distributors", "campaign_external_id": "qlc1", "status": "Active"}],
	"ads": [{"external_id": "qla1", "ad_name": "QA Leads Form Ad", "campaign_external_id": "qlc1", "ad_group_external_id": "qls1", "status": "Active"}],
}
GOOGLE_TREE = {
	"campaigns": [{"external_id": "88001", "campaign_name": "QA Leads Search Brand", "status": "Active"}],
	"groups": [{"external_id": "88002", "ad_group_name": "QA Leads Brand Terms", "campaign_external_id": "88001", "status": "Active"}],
	"ads": [{"external_id": "88003", "ad_name": "QA Leads RSA", "campaign_external_id": "88001", "ad_group_external_id": "88002", "status": "Active"}],
}


def _company():
	return frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]


def _mirrors(account: str, tree: dict) -> None:
	"""The structure sync's own upsert, as a structure job would write the mirrors."""
	acc = frappe.get_doc("Ads Account", account)
	known = {}
	known["Ads Campaign"] = sync._upsert_mirrors(acc, "Ads Campaign", tree["campaigns"], known)
	known["Ads Ad Group"] = sync._upsert_mirrors(acc, "Ads Ad Group", tree["groups"], known)
	sync._upsert_mirrors(acc, "Ads Advertisement", tree["ads"], known)
	frappe.db.commit()


def _graph_lead(leadgen_id, phone, *, ad="qla1", adset="qls1", campaign="qlc1", campaign_name="QA Leads Diwali B2B", name="QA Leads Arjun", test=False, organic=False):
	"""GET /{leadgen_id}?fields=… as Graph returns it."""
	dummy = lambda f, v: f"<test lead: dummy data for {f}>" if test else v
	row = {
		"id": leadgen_id, "created_time": "2026-10-17T10:00:00+0000", "form_id": "qaleads-form-1", "platform": "fb", "is_organic": organic,
		"field_data": [{"name": "full_name", "values": [dummy("full_name", name)]}, {"name": "phone_number", "values": [phone]}, {"name": "email", "values": [dummy("email", f"qa.leads.{leadgen_id}@example.com")]}],
	}
	if not organic:
		row.update({"ad_id": ad, "adset_id": adset, "campaign_id": campaign, "campaign_name": campaign_name})
		if ad == "qla1":
			row.update({"ad_name": "QA Leads Form Ad", "adset_name": "QA Leads Distributors"})
	return row


def _leadgen_webhook(leadgen_id, ad="qla1"):
	"""Page webhook, field 'leadgen' (Meta: Retrieving Leads → webhooks)."""
	return {"object": "page", "entry": [{"id": "qaleads-page-1", "time": 1760695200, "changes": [{"field": "leadgen", "value": {"leadgen_id": leadgen_id, "page_id": "qaleads-page-1", "form_id": "qaleads-form-1", "ad_id": ad, "adgroup_id": ad, "created_time": 1760695200}}]}]}


def _google_payload(lead_id, phone, key=GOOGLE_KEY, test=False, key_name="google_key"):
	"""Google Ads lead form webhook body (developers.google.com/google-ads/webhook/docs/implementation)."""
	body = {
		"lead_id": lead_id, "api_version": "1.0", "form_id": 4455667788, "campaign_id": 88001, "adgroup_id": 88002, "creative_id": 88003,
		"gcl_id": f"EAIaIQobChMI-{lead_id}", "lead_submit_time": "2026-10-17T12:30:00Z", "lead_source": "LEAD_FORM", key_name: key,
		"user_column_data": [
			{"column_name": "Full Name", "string_value": "QA Leads Meena", "column_id": "FULL_NAME"},
			{"column_name": "User Phone", "string_value": phone, "column_id": "PHONE_NUMBER"},
			{"column_name": "User Email", "string_value": f"qa.leads.{lead_id}@example.com", "column_id": "EMAIL"},
			{"column_name": "Postal Code", "string_value": "110001", "column_id": "POSTAL_CODE"},
			{"column_name": "How many units per month?", "string_value": "500", "column_id": "qaleads_q1"},
		],
	}
	if test:
		body["is_test"] = True
	return body


def _wa_message(wamid, phone, referral, text="Hi, I saw your ad"):
	"""WhatsApp Cloud API messages webhook with a click-to-WhatsApp referral (webhooks reference → messages → text)."""
	return {"object": "whatsapp_business_account", "entry": [{"id": "qaleads-waba", "changes": [{"field": "messages", "value": {
		"messaging_product": "whatsapp", "metadata": {"display_phone_number": "919900000000", "phone_number_id": WA_PHONE_ID},
		"contacts": [{"profile": {"name": "QA Leads Ctwa Person"}, "wa_id": phone.lstrip("+")}],
		"messages": [{"from": phone.lstrip("+"), "id": wamid, "timestamp": "1760700000", "type": "text", "text": {"body": text}, "referral": referral}],
	}}]}]}


def _referral(ad_id="qla1", clid="ARAkLkA8rmlFeiCktEJQ-QTwRiyYHAFDLMNDBH0CD3qpjd0HR4irJ6LEkR7JwFF4XvnO2E4Nx0-eM-GABDLOPaOdRMv-_zfUQ2a"):
	r = {"source_url": "https://fb.me/qaleads", "source_id": ad_id, "source_type": "ad", "headline": "QA Leads: wholesale cones", "body": "Message us for the price list", "media_type": "image", "image_url": "https://example.com/qa.jpg"}
	if clid:
		r["ctwa_clid"] = clid
	return r


class TestAdsLeads(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		# The site's own choice is put back afterwards; the suite runs with the default (on).
		cls.prior_ctwa = frappe.db.sql("select value from `tabSingles` where doctype='Ads Settings' and field='ctwa_create_lead'")
		frappe.db.set_single_value("Ads Settings", "ctwa_create_lead", 1)
		cls.company = _company()
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS", "company": cls.company})
		ins({"doctype": "Ads Account", "account_name": f"{P} Meta", "provider": "Meta", "external_id": META_EXT, "workspace": f"{P} WS"})
		ins({"doctype": "Ads Account", "account_name": f"{P} Google", "provider": "Google", "external_id": GOOGLE_EXT, "workspace": f"{P} WS"})
		_mirrors(META_ACC, META_TREE)
		_mirrors(GOOGLE_ACC, GOOGLE_TREE)
		ins({"doctype": "Excom Channel Account", "account_name": f"{P} WA", "channel": "whatsapp", "status": "Active", "company": cls.company, "wa_phone_id": WA_PHONE_ID})
		base = {"doctype": "Excom Source", "enabled": 1, "company": cls.company, "sla_first_response": 3600}
		ins({**base, "source_name": f"{P} Meta Form", "source_type": "Meta Lead Ads", "form_id": "qaleads-form-1", "page_id": "qaleads-page-1"})
		ins({**base, "source_name": f"{P} Google Form", "source_type": "Google Lead Form", "google_key": GOOGLE_KEY})
		ins({**base, "source_name": f"{P} Website", "source_type": "Website"})
		for u in USERS:
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": "Excom Marketing"}]})
		ins({"doctype": "User Permission", "user": U_META, "allow": "Ads Account", "for_value": META_ACC, "apply_to_all_doctypes": 1})
		frappe.db.commit()
		cls._orig = (meta_adapter.fetch_lead, intake.enqueue, frappe.enqueue)
		cls.graph = {}
		cls.fetches = []
		cls.enqueued = []

		def fake_fetch(src, leadgen_id):
			cls.fetches.append(leadgen_id)
			if leadgen_id not in cls.graph:
				raise frappe.ValidationError("Graph 500: down")
			return cls.graph[leadgen_id]

		meta_adapter.fetch_lead = fake_fetch
		# No worker runs: process the log where the worker would.
		intake.enqueue = lambda log_name: intake.process_log(log_name)
		frappe.enqueue = lambda *a, **k: cls.enqueued.append((a, k)) or object()

	@classmethod
	def tearDownClass(cls):
		meta_adapter.fetch_lead, intake.enqueue, frappe.enqueue = cls._orig
		cls._clean()
		if cls.prior_ctwa:
			frappe.db.set_single_value("Ads Settings", "ctwa_create_lead", cls.prior_ctwa[0][0])
		else:  # never saved on this site: leave it unsaved (the default applies)
			frappe.db.sql("delete from `tabSingles` where doctype='Ads Settings' and field='ctwa_create_lead'")
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		frappe.db.delete(TOUCH, {"observation_id": ["like", "%qaleads%"]})
		identities = set(frappe.get_all("Omni Identity", {"display_name": ["like", f"{P}%"]}, pluck="name"))
		identities |= set(frappe.get_all("Omni Identity", {"primary_phone": ["in", PHONES]}, pluck="name"))
		leads = set(frappe.get_all(gw.LEAD, {"lead_name": ["like", f"{P}%"]}, pluck="name")) | set(frappe.get_all(gw.LEAD, {"email_id": ["like", "qa.leads.%"]}, pluck="name")) | set(frappe.get_all(gw.LEAD, {"mobile_no": ["in", PHONES]}, pluck="name"))
		for name in leads:
			frappe.db.delete(TOUCH, {"reference_name": name})
			frappe.db.delete("ToDo", {"reference_name": name})
			frappe.db.delete("Comment", {"reference_doctype": gw.LEAD, "reference_name": name})
			for c in frappe.get_all("Dynamic Link", {"link_name": name, "link_doctype": gw.LEAD, "parenttype": "Contact"}, pluck="parent"):
				frappe.db.delete("Dynamic Link", {"parent": c})
				frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
			frappe.delete_doc(gw.LEAD, name, force=True, ignore_permissions=True)
		for oi in identities:
			frappe.db.delete("Excom Message", {"omni_identity": oi})
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t})
				frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		frappe.db.delete("Excom Source Log", {"dedupe_key": ["like", "%qaleads%"]})
		for s in frappe.get_all("Excom Source", {"source_name": ["like", f"{P}%"]}, pluck="name"):
			frappe.db.delete("Excom Source Log", {"source": s})
			frappe.delete_doc("Excom Source", s, force=True, ignore_permissions=True)
		for acc in (META_ACC, GOOGLE_ACC):
			frappe.db.delete(TOUCH, {"account": acc})
			for dt in ("Ads Advertisement", "Ads Ad Group", "Ads Campaign", "Ads Sync Job", "Ads Alert", "Ads Metric Snapshot"):
				frappe.db.delete(dt, {"account": acc})
			frappe.db.delete("Ads Account", {"name": acc})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		if frappe.db.exists("Excom Channel Account", f"{P} WA"):
			frappe.delete_doc("Excom Channel Account", f"{P} WA", force=True, ignore_permissions=True)
		frappe.db.delete("User Permission", {"user": ["in", list(USERS)]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		for dt in ("UTM Campaign", "Campaign"):
			if frappe.db.exists("DocType", dt):
				for n in frappe.get_all(dt, or_filters=[["name", "like", f"{P}%"]] + ([["campaign_name", "like", f"{P}%"]] if frappe.get_meta(dt).has_field("campaign_name") else []), pluck="name"):
					frappe.delete_doc(dt, n, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		self.fetches.clear()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	# ─── helpers ──────────────────────────────────────────────────────────────

	def _lead_of_log(self, key):
		log = frappe.db.get_value("Excom Source Log", {"dedupe_key": key}, ["name", "status", "lead", "error"], as_dict=True)
		self.assertTrue(log, key)
		self.assertEqual(log.status, "Processed", log.error)
		return frappe.get_doc(gw.LEAD, log.lead)

	def _campaign_title(self, lead) -> str:
		value = crm_compat.get_attribution(lead).get("campaign")
		if not value:
			return ""
		dt = crm_compat.attribution_doctypes()["campaign"]
		title = frappe.db.get_value(dt, value, "campaign_name") if frappe.get_meta(dt).has_field("campaign_name") else None
		return title or value

	def _google_post(self, body, raw=None):
		from excom.excom.api import intake as intake_api

		req = MagicMock()
		req.method = "POST"
		req.headers = {"Content-Type": "application/json"}
		req.get_data = lambda as_text=False: raw if raw is not None else json.dumps(body)
		prior = getattr(frappe.local, "request", None)
		frappe.local.request = req
		frappe.local.request_ip = "203.0.113.77"
		frappe.local.form_dict = frappe._dict(cmd="excom.excom.api.intake.google_lead_form")
		try:
			return intake_api.google_lead_form()
		finally:
			frappe.cache.delete_keys("rl:excom.excom.api.intake.google_lead_form")
			frappe.local.request = prior
			frappe.local.form_dict = frappe._dict()

	def _counts(self):
		return (frappe.db.count("Excom Source Log"), frappe.db.count(gw.LEAD), frappe.db.count(TOUCH), frappe.db.count("Omni Identity"))

	# ─── Meta lead ads ────────────────────────────────────────────────────────

	def test_01_meta_lead_replayed_five_times_is_one_log_one_lead_traced_to_its_campaign(self):
		self.graph["qaleads-m1"] = _graph_lead("qaleads-m1", PHONES[0])
		for _ in range(5):
			webhook._process_webhook_payload(json.dumps(_leadgen_webhook("qaleads-m1")))
		self.assertEqual(frappe.db.count("Excom Source Log", {"dedupe_key": "meta:qaleads-m1"}), 1)
		self.assertEqual(self.fetches, ["qaleads-m1"], "a replay of a logged lead makes no second Graph call")
		lead = self._lead_of_log("meta:qaleads-m1")
		self.assertEqual(frappe.db.count(gw.LEAD, {"email_id": "qa.leads.qaleads-m1@example.com"}), 1)
		# Lead → form → ad → ad set → campaign → account
		self.assertEqual(lead.ads_form_id, "qaleads-form-1")
		self.assertEqual(lead.ads_advertisement, mirror_name(META_ACC, "qla1"))
		self.assertEqual(lead.ads_ad_group, mirror_name(META_ACC, "qls1"))
		self.assertEqual(lead.ads_campaign, mirror_name(META_ACC, "qlc1"))
		self.assertEqual(lead.ads_account, META_ACC)
		self.assertEqual((lead.ads_ad_id, lead.ads_ad_group_id, lead.ads_campaign_id, lead.ads_provider), ("qla1", "qls1", "qlc1", "Meta"))
		self.assertEqual((lead.ads_link_status, lead.ads_is_test, lead.ads_is_organic), ("Linked", 0, 0))
		self.assertEqual(self._campaign_title(lead), "QA Leads Diwali B2B", "campaign attribution goes through crm_compat")
		touches = frappe.get_all(TOUCH, {"observation_id": "meta:qaleads-m1"}, ["name", "reference_name", "account", "workspace", "company", "touch_source", "page_id"])
		self.assertEqual(len(touches), 1)
		t = touches[0]
		self.assertEqual((t.reference_name, t.account, t.workspace, t.company, t.touch_source, t.page_id), (lead.name, META_ACC, f"{P} WS", self.company, "Lead Form", "qaleads-page-1"))
		self.assertEqual(t.name, touch_name("Lead Form", "meta:qaleads-m1"))
		self.assertEqual(lead.ads_first_touch, t.name)

	def test_02_lead_before_its_mirror_is_linked_after_the_structure_sync(self):
		self.graph["qaleads-m2"] = _graph_lead("qaleads-m2", PHONES[1], ad="qla9", adset="qls9", campaign="qlc9", campaign_name=None, name="QA Leads Late")
		webhook._process_webhook_payload(json.dumps(_leadgen_webhook("qaleads-m2", ad="qla9")))
		lead = self._lead_of_log("meta:qaleads-m2")
		self.assertEqual((lead.ads_ad_id, lead.ads_ad_group_id, lead.ads_campaign_id), ("qla9", "qls9", "qlc9"), "platform IDs are kept until the mirror exists")
		self.assertEqual(lead.ads_link_status, "Pending")
		self.assertFalse(lead.ads_campaign or lead.ads_account)
		self.assertEqual(self._campaign_title(lead), "")

		class FakeProvider:
			def read_account(self):
				return {}

			def list_campaigns(self):
				return [{"external_id": "qlc9", "campaign_name": "QA Leads Late Campaign", "status": "Active"}]

			def list_ad_groups(self):
				return [{"external_id": "qls9", "ad_group_name": "QA Leads Late Set", "campaign_external_id": "qlc9", "status": "Active"}]

			def list_ads(self):
				return [*META_TREE["ads"], {"external_id": "qla9", "ad_name": "QA Leads Late Ad", "campaign_external_id": "qlc9", "ad_group_external_id": "qls9", "status": "Active"}]

		res = sync.sync_structure(frappe.get_doc("Ads Account", META_ACC), FakeProvider())
		self.assertFalse(res["errors"])
		lead.reload()
		self.assertEqual((lead.ads_advertisement, lead.ads_ad_group, lead.ads_campaign, lead.ads_account), (mirror_name(META_ACC, "qla9"), mirror_name(META_ACC, "qls9"), mirror_name(META_ACC, "qlc9"), META_ACC))
		self.assertEqual((lead.ads_link_status, lead.ads_campaign_name, lead.ads_ad_name), ("Linked", "QA Leads Late Campaign", "QA Leads Late Ad"))
		self.assertEqual(self._campaign_title(lead), "QA Leads Late Campaign", "an empty campaign attribution is filled once known")
		t = frappe.db.get_value(TOUCH, lead.ads_first_touch, ["link_status", "workspace", "account"], as_dict=True)
		self.assertEqual((t.link_status, t.workspace, t.account), ("Linked", f"{P} WS", META_ACC))
		# Re-running is a no-op.
		self.assertEqual(attribution.relink_pending(provider="Meta")["linked"], 0)

	def test_03_meta_test_and_organic_leads_are_flagged_and_kept_out_of_kpis(self):
		self.graph["qaleads-m3"] = _graph_lead("qaleads-m3", PHONES[2], test=True)
		self.graph["qaleads-m4"] = _graph_lead("qaleads-m4", PHONES[3], organic=True, name="QA Leads Organic")
		for lid in ("qaleads-m3", "qaleads-m4"):
			webhook._process_webhook_payload(json.dumps(_leadgen_webhook(lid, ad=None)))
		test_lead = self._lead_of_log("meta:qaleads-m3")
		organic = self._lead_of_log("meta:qaleads-m4")
		self.assertEqual(test_lead.ads_is_test, 1)
		self.assertEqual((organic.ads_is_organic, organic.ads_link_status, organic.ads_campaign), (1, "Organic", None))
		camp = mirror_name(META_ACC, "qlc1")
		counted = attribution.verified_lead_counts([camp]).get(camp, 0)
		real = frappe.db.count(gw.LEAD, {"ads_campaign": camp, "ads_is_test": 0})
		self.assertEqual(counted, real)
		self.assertEqual(test_lead.ads_campaign, camp)
		self.assertEqual(frappe.db.count(gw.LEAD, {"ads_campaign": camp}), counted + 1, "the test lead is the one left out")

	def test_04_a_stub_logged_while_graph_was_down_is_completed_by_reconciliation(self):
		self.graph.pop("qaleads-m5", None)
		webhook._process_webhook_payload(json.dumps(_leadgen_webhook("qaleads-m5")))
		self.assertEqual(frappe.db.get_value("Excom Source Log", {"dedupe_key": "meta:qaleads-m5"}, "status"), "Ignored")
		row = _graph_lead("qaleads-m5", PHONES[4], name="QA Leads Stub")
		row["leadgen_id"] = row["id"]
		r = meta_adapter._ingest(frappe.get_doc("Excom Source", f"{P} Meta Form"), "meta:qaleads-m5", row)
		self.assertFalse(r["duplicate"])
		lead = self._lead_of_log("meta:qaleads-m5")
		self.assertEqual((lead.ads_campaign, lead.ads_link_status), (mirror_name(META_ACC, "qlc1"), "Linked"))
		self.assertEqual(frappe.db.count("Excom Source Log", {"dedupe_key": "meta:qaleads-m5"}), 1)

	# ─── Google Ads lead form webhook ────────────────────────────────────────

	def test_05_google_wrong_or_missing_key_is_refused_before_anything_is_stored(self):
		before = self._counts()
		for body in (_google_payload("qaleads-g0", PHONES[5], key="wrong-key"), _google_payload("qaleads-g0", PHONES[5], key=""), {k: v for k, v in _google_payload("qaleads-g0", PHONES[5]).items() if k != "google_key"}):
			resp = self._google_post(body)
			self.assertEqual(resp.status_code, 403)
		self.assertEqual(self._counts(), before)
		self.assertFalse(frappe.db.exists("Excom Source Log", {"dedupe_key": "google:qaleads-g0"}))
		self.assertFalse(frappe.db.get_value("Excom Source", f"{P} Google Form", "form_id"), "a refused call teaches nothing")

	def test_06_google_lead_replayed_five_times_is_one_log_one_lead_with_its_ad(self):
		for _ in range(5):
			resp = self._google_post(_google_payload("qaleads-g1", PHONES[6]))
			self.assertEqual(resp.status_code, 200)
			self.assertEqual(json.loads(resp.get_data(as_text=True)), {})
		self.assertEqual(frappe.db.count("Excom Source Log", {"dedupe_key": "google:qaleads-g1"}), 1)
		log = frappe.db.get_value("Excom Source Log", {"dedupe_key": "google:qaleads-g1"}, ["raw_payload", "mapped_payload"], as_dict=True)
		self.assertNotIn(GOOGLE_KEY, log.raw_payload, "the key is never stored")
		self.assertNotIn("google_key", log.raw_payload)
		lead = self._lead_of_log("google:qaleads-g1")
		self.assertEqual(frappe.db.count(gw.LEAD, {"email_id": "qa.leads.qaleads-g1@example.com"}), 1)
		self.assertEqual((lead.lead_name, lead.mobile_no), ("QA Leads Meena", PHONES[6]))
		self.assertEqual((lead.ads_campaign, lead.ads_ad_group, lead.ads_advertisement, lead.ads_account), (mirror_name(GOOGLE_ACC, "88001"), mirror_name(GOOGLE_ACC, "88002"), mirror_name(GOOGLE_ACC, "88003"), GOOGLE_ACC))
		self.assertEqual((lead.ads_gclid, lead.ads_form_id, lead.ads_link_status, lead.ads_is_test), ("EAIaIQobChMI-qaleads-g1", "4455667788", "Linked", 0))
		self.assertEqual(self._campaign_title(lead), "QA Leads Search Brand")
		self.assertEqual(frappe.db.get_value("Excom Source", f"{P} Google Form", "form_id"), "4455667788", "form_id learned from the first verified lead")
		mapped = json.loads(log.mapped_payload)
		self.assertIn("How many units per month?: 500", mapped.get("message", ""))
		self.assertIn("Postal code: 110001", mapped.get("message", ""))
		self.assertEqual(frappe.db.count(TOUCH, {"observation_id": "google:qaleads-g1"}), 1)

	def test_07_google_test_lead_with_docs_spelling_of_the_key_is_flagged(self):
		resp = self._google_post(_google_payload("qaleads-g2", PHONES[7], test=True, key_name="Google_key"))
		self.assertEqual(resp.status_code, 200)
		lead = self._lead_of_log("google:qaleads-g2")
		self.assertEqual(lead.ads_is_test, 1)
		# Google's sample bodies carry a trailing comma; the endpoint accepts that shape.
		raw = json.dumps(_google_payload("qaleads-g3", PHONES[8]))[:-1] + ",}"
		self.assertEqual(self._google_post(None, raw=raw).status_code, 200)
		self.assertTrue(frappe.db.exists("Excom Source Log", {"dedupe_key": "google:qaleads-g3"}))

	# ─── click-to-WhatsApp ────────────────────────────────────────────────────

	def test_08_ctwa_referral_lands_on_thread_and_lead(self):
		payload = _wa_message("wamid.qaleads-w1", PHONES[9], _referral())
		for _ in range(3):
			webhook._process_webhook_payload(json.dumps(payload))
		msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.qaleads-w1"}, ["name", "thread", "omni_identity"], as_dict=True)
		self.assertTrue(msg)
		th = frappe.get_doc("Excom Thread", msg.thread)
		self.assertEqual((th.ad_source_type, th.ad_source_id, th.ads_advertisement, th.ad_headline, th.ad_media_type), ("ad", "qla1", mirror_name(META_ACC, "qla1"), "QA Leads: wholesale cones", "image"))
		self.assertTrue(th.ctwa_clid.startswith("ARAkLk"))
		self.assertEqual(th.ad_source_url, "https://fb.me/qaleads")
		refs = gw.find_open_records_for_identity(msg.omni_identity)
		self.assertEqual(len(refs), 1)
		lead = frappe.get_doc(gw.LEAD, refs[0].name)
		self.assertEqual((lead.ads_touch_source, lead.ads_ctwa_clid, lead.ads_advertisement, lead.ads_campaign, lead.ads_link_status), ("CTWA", th.ctwa_clid, mirror_name(META_ACC, "qla1"), mirror_name(META_ACC, "qlc1"), "Linked"))
		self.assertEqual(lead.first_touch_channel, "WhatsApp")
		self.assertEqual(crm_compat.get_attribution(lead)["source"], "Click to WhatsApp")
		self.assertEqual(self._campaign_title(lead), "QA Leads Diwali B2B")
		touches = frappe.get_all(TOUCH, {"thread": msg.thread}, ["name", "reference_name", "message", "touch_source"])
		self.assertEqual(len(touches), 1, "a redelivered message adds nothing")
		self.assertEqual((touches[0].reference_name, touches[0].message, touches[0].touch_source), (lead.name, msg.name, "CTWA"))

	def test_09_status_ad_without_click_id_is_an_ad_with_no_click_id(self):
		webhook._process_webhook_payload(json.dumps(_wa_message("wamid.qaleads-w2", PHONES[10], _referral(clid=None))))
		thread = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.qaleads-w2"}, "thread")
		th = frappe.get_doc("Excom Thread", thread)
		self.assertEqual((th.ad_source_type, th.ctwa_clid, th.ad_attribution_note), ("ad", None, "ad (no click ID)"))
		t = frappe.get_all(TOUCH, {"thread": thread}, ["attribution_note", "ctwa_clid", "advertisement"])[0]
		self.assertEqual((t.attribution_note, t.ctwa_clid, t.advertisement), ("ad (no click ID)", None, mirror_name(META_ACC, "qla1")))

	def test_10_without_auto_leads_the_referral_is_attached_when_the_chat_is_promoted(self):
		frappe.db.set_single_value("Ads Settings", "ctwa_create_lead", 0)
		try:
			webhook._process_webhook_payload(json.dumps(_wa_message("wamid.qaleads-w3", PHONES[11], _referral())))
			msg = frappe.db.get_value("Excom Message", {"provider_message_id": "wamid.qaleads-w3"}, ["thread", "omni_identity"], as_dict=True)
			self.assertEqual(gw.find_open_records_for_identity(msg.omni_identity), [])
			from excom.excom.api.crm import promote_thread

			r = promote_thread(msg.thread)
			lead = frappe.get_doc(gw.LEAD, r["ref"]["name"])
			self.assertEqual((lead.ads_touch_source, lead.ads_campaign), ("CTWA", mirror_name(META_ACC, "qlc1")))
			self.assertEqual(crm_compat.get_attribution(lead)["source"], "Click to WhatsApp")
		finally:
			frappe.db.set_single_value("Ads Settings", "ctwa_create_lead", 1)

	# ─── website and orders ───────────────────────────────────────────────────

	def test_11_website_enquiry_with_click_ids_is_a_website_touch(self):
		src = frappe.get_doc("Excom Source", f"{P} Website")
		raw = {"name": "QA Leads Web", "phone": PHONES[12], "message": "Quote please", "page_url": "https://shop.example/contact?gclid=Cj0KCQ-qaleads&utm_source=google&utm_medium=cpc&utm_campaign=QA+Leads+Brand&utm_term=cones&utm_id=88001", "utm": {"utm_content": "hero"}}
		intake.ingest(src, "web:qaleads-web-1", raw, sync=True)
		lead = self._lead_of_log("web:qaleads-web-1")
		t = frappe.get_doc(TOUCH, lead.ads_first_touch)
		self.assertEqual((t.touch_source, t.provider, t.gclid, t.utm_term, t.utm_content, t.utm_campaign), ("Website", "Google", "Cj0KCQ-qaleads", "cones", "hero", "QA Leads Brand"))
		self.assertEqual((t.campaign, t.link_status), (mirror_name(GOOGLE_ACC, "88001"), "Linked"), "utm_id names the campaign")
		self.assertEqual(lead.ads_gclid, "Cj0KCQ-qaleads")
		self.assertEqual(self._campaign_title(lead), "QA Leads Brand", "UTM campaign keeps priority over the mirror name")

	def test_12_order_with_storefront_click_ids_is_an_order_touch(self):
		doc = frappe._dict(doctype="Sales Order", name="QA-SO-qaleads-1", company=self.company, customer=None, storefront_utm_source="facebook", storefront_utm_medium="paid_social", storefront_fbclid="IwAR-qaleads", storefront_fbc="fb.1.1760700000000.IwAR-qaleads", storefront_fbp="fb.1.1760700000000.123", storefront_landing_page="https://shop.example/?fbclid=IwAR-qaleads")
		for _ in range(2):
			attribution.on_sales_order_submit(doc)
		rows = frappe.get_all(TOUCH, {"observation_id": "so:QA-SO-qaleads-1"}, ["touch_source", "provider", "fbclid", "fbc", "fbp", "reference_doctype", "company", "link_status"])
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].touch_source, rows[0].provider, rows[0].fbclid, rows[0].reference_doctype, rows[0].company, rows[0].link_status), ("Order", "Meta", "IwAR-qaleads", "Sales Order", self.company, "No Ad IDs"))
		self.assertIsNone(attribution.order_context(frappe._dict(doctype="Sales Order", name="x")), "an order without marketing values writes nothing")

	# ─── scope ────────────────────────────────────────────────────────────────

	def test_13_a_user_without_access_to_the_account_cannot_read_its_touches(self):
		self.graph["qaleads-m6"] = _graph_lead("qaleads-m6", PHONES[13], name="QA Leads Scope")
		webhook._process_webhook_payload(json.dumps(_leadgen_webhook("qaleads-m6")))
		self._google_post(_google_payload("qaleads-g4", PHONES[14]))
		meta_t = frappe.db.get_value(TOUCH, {"observation_id": "meta:qaleads-m6"}, "name")
		google_t = frappe.db.get_value(TOUCH, {"observation_id": "google:qaleads-g4"}, "name")
		self.assertTrue(meta_t and google_t)
		mine = {meta_t, google_t}

		frappe.set_user(U_NONE)
		permissions.clear_cache()
		self.assertEqual(set(frappe.get_list(TOUCH, filters={"name": ["in", list(mine)]}, pluck="name")), set())
		self.assertFalse(frappe.has_permission(TOUCH, "read", doc=frappe.get_doc(TOUCH, meta_t)))

		frappe.set_user(U_META)
		permissions.clear_cache()
		self.assertEqual(set(frappe.get_list(TOUCH, filters={"name": ["in", list(mine)]}, pluck="name")), {meta_t})
		self.assertTrue(frappe.has_permission(TOUCH, "read", doc=frappe.get_doc(TOUCH, meta_t)))
		self.assertFalse(frappe.has_permission(TOUCH, "read", doc=frappe.get_doc(TOUCH, google_t)))
		self.assertFalse(frappe.has_permission(TOUCH, "write", doc=frappe.get_doc(TOUCH, meta_t)), "touches are written by the system only")

	def test_14_deleting_the_lead_deletes_its_touches(self):
		self.graph["qaleads-m7"] = _graph_lead("qaleads-m7", PHONES[15], name="QA Leads Erase")
		webhook._process_webhook_payload(json.dumps(_leadgen_webhook("qaleads-m7")))
		lead = self._lead_of_log("meta:qaleads-m7")
		self.assertTrue(frappe.db.exists(TOUCH, {"reference_name": lead.name}))
		frappe.db.delete("Excom Source Log", {"dedupe_key": "meta:qaleads-m7"})
		# (the lead's Contact is held by its Omni Identity; free it the way an erasure would)
		frappe.db.delete("Omni Identity Link", {"parent": lead.omni_identity})
		frappe.delete_doc(gw.LEAD, lead.name, force=True, ignore_permissions=True)
		self.assertFalse(frappe.db.exists(TOUCH, {"reference_name": lead.name}))

	def test_16_the_ad_copy_follows_the_lead_into_its_opportunity(self):
		lead = self._lead_of_log("meta:qaleads-m1")
		frappe.db.set_value(gw.LEAD, lead.name, "customer_type", "Distributor")
		opp = gw.convert(gw.lead_ref(lead.name), gw.OPPORTUNITY)
		try:
			row = frappe.db.get_value(gw.OPPORTUNITY, opp.name, ["ads_campaign", "ads_advertisement", "ads_first_touch", "ads_link_status"], as_dict=True)
			self.assertEqual((row.ads_campaign, row.ads_advertisement, row.ads_first_touch, row.ads_link_status), (lead.ads_campaign, lead.ads_advertisement, lead.ads_first_touch, "Linked"))
		finally:
			frappe.db.delete("Omni Identity Link", {"linked_doctype": gw.OPPORTUNITY, "linked_name": opp.name})
			frappe.db.delete("Comment", {"reference_doctype": gw.OPPORTUNITY, "reference_name": opp.name})
			frappe.delete_doc(gw.OPPORTUNITY, opp.name, force=True, ignore_permissions=True)
			frappe.db.set_value(gw.LEAD, lead.name, "status", "Lead")
			frappe.db.commit()

	def test_15_every_qa_lead_traces_to_a_campaign_or_is_flagged(self):
		rows = frappe.get_all(gw.LEAD, {"ads_touch_source": ["is", "set"], "lead_name": ["like", f"{P}%"]}, ["name", "ads_campaign", "ads_is_organic", "ads_link_status", "ads_first_touch"])
		self.assertTrue(rows)
		for r in rows:
			self.assertTrue(r.ads_first_touch, r.name)
			self.assertTrue(r.ads_campaign or r.ads_is_organic or r.ads_link_status in ("Pending", "Partial", "No Ad IDs"), r)
