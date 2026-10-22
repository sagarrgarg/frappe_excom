"""Excom Ads Leads screen: ad-lead feed inside the ads scope and Excom's lead visibility, SLA states,
platform vs verified leads by campaign (never summed), test leads excluded, funnel availability.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_leads_dashboard'}"
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom.services import crm_gateway as gw
from excom.excom_ads import leads_dashboard as ld
from excom.excom_ads import permissions
from excom.excom_ads.tests.test_ads_permissions import COMPANY_B, _ensure_company_b

P = "QA LeadsDash"
U_A = "qa.leadsdash.a@example.com"
U_B = "qa.leadsdash.b@example.com"
U_NONE = "qa.leadsdash.none@example.com"
USERS = [U_A, U_B, U_NONE]
SOURCE = f"{P} Source"


def _lead(title: str, company: str, **ads) -> str:
	doc = frappe.get_doc({"doctype": gw.LEAD, "lead_name": title, "first_name": title, "company": company})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	# Test fixture only: production writes ads_* through attribution.py.
	frappe.db.set_value(gw.LEAD, doc.name, {"ads_touch_source": "Lead Form", "ads_link_status": "Linked", **ads}, update_modified=False)
	return doc.name


class TestAdsLeadsDashboard(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		_ensure_company_b()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS A", "company": cls.company})
		ins({"doctype": "Ads Workspace", "workspace_name": f"{P} WS B", "company": COMPANY_B})
		cls.acc_a = ins({"doctype": "Ads Account", "account_name": f"{P} Acc A", "provider": "Meta", "external_id": "act_6660001", "workspace": f"{P} WS A", "currency": cls.cur}).name
		cls.acc_b = ins({"doctype": "Ads Account", "account_name": f"{P} Acc B", "provider": "Meta", "external_id": "act_6660002", "workspace": f"{P} WS B", "currency": "INR"}).name
		sa = {"account": cls.acc_a, "workspace": f"{P} WS A", "company": cls.company, "provider": "Meta"}
		sb = {"account": cls.acc_b, "workspace": f"{P} WS B", "company": COMPANY_B, "provider": "Meta"}
		cls.camp_a = ins({"doctype": "Ads Campaign", **sa, "campaign_name": f"{P} Camp A", "external_id": "ldc1", "status": "Active"}).name
		cls.camp_b = ins({"doctype": "Ads Campaign", **sb, "campaign_name": f"{P} Camp B", "external_id": "ldc2", "status": "Active"}).name
		cls.today = now_datetime().date()
		for scope, camp, ext, spend, leads, cur in ((sa, cls.camp_a, "ldc1", 1000, 5, cls.cur), (sb, cls.camp_b, "ldc2", 900, 3, "INR")):
			ins({"doctype": "Ads Metric Snapshot", **scope, "level": "Campaign", "entity_external_id": ext, "date": cls.today, "campaign": camp, "currency": cur, "company_currency": cur, "exchange_rate": 1, "spend": spend, "spend_company_currency": spend, "clicks": 50, "leads": leads})
		if not frappe.db.exists("Excom Source", SOURCE):
			ins({"doctype": "Excom Source", "source_name": SOURCE, "source_type": "Website", "enabled": 0, "sla_first_response": 3600})
		for u in USERS:
			# A marketer who also works leads: Sales User reads Lead / Opportunity, inside Excom's visibility rule.
			roles = ["Excom Marketing"] + (["Sales User"] if u != U_NONE else [])
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "roles": [{"role": r} for r in roles]})
		ins({"doctype": "User Permission", "user": U_A, "allow": "Company", "for_value": cls.company, "apply_to_all_doctypes": 1})
		ins({"doctype": "User Permission", "user": U_B, "allow": "Company", "for_value": COMPANY_B, "apply_to_all_doctypes": 1})
		ads_a = {"ads_account": cls.acc_a, "ads_campaign": cls.camp_a, "ads_campaign_name": f"{P} Camp A", "ads_provider": "Meta", "intake_source": SOURCE}
		cls.breached = _lead(f"{P} Breached", cls.company, **ads_a, lead_owner=U_A)
		cls.met = _lead(f"{P} Met", cls.company, **ads_a)
		cls.pending = _lead(f"{P} Pending", cls.company, **ads_a)
		cls.test_lead = _lead(f"{P} Platform Test", cls.company, **ads_a, ads_is_test=1)
		cls.lead_b = _lead(f"{P} Company B", COMPANY_B, ads_account=cls.acc_b, ads_campaign=cls.camp_b, ads_campaign_name=f"{P} Camp B", ads_provider="Meta")
		now = now_datetime()
		frappe.db.set_value(gw.LEAD, cls.breached, {"creation": add_to_date(now, hours=-2), "auto_ack_sent_at": None}, update_modified=False)
		frappe.db.set_value(gw.LEAD, cls.met, {"creation": add_to_date(now, hours=-3), "auto_ack_sent_at": add_to_date(now, hours=-3, minutes=10)}, update_modified=False)
		frappe.db.set_value(gw.LEAD, cls.pending, {"creation": add_to_date(now, minutes=-5), "auto_ack_sent_at": None}, update_modified=False)
		for n in (cls.met, cls.pending, cls.test_lead, cls.lead_b):
			frappe.db.set_value(gw.LEAD, n, {"lead_owner": None}, update_modified=False)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		leads = frappe.get_all(gw.LEAD, {"lead_name": ["like", f"{P}%"]}, pluck="name")
		for o in frappe.get_all(gw.OPPORTUNITY, {"party_name": ["in", leads or ["-"]]}, pluck="name"):
			frappe.delete_doc(gw.OPPORTUNITY, o, force=True, ignore_permissions=True)
		for oi in frappe.get_all("Omni Identity", {"display_name": ["like", f"{P}%"]}, pluck="name"):
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
		for n in leads:
			frappe.db.delete("Dynamic Link", {"link_doctype": gw.LEAD, "link_name": n})
			frappe.delete_doc(gw.LEAD, n, force=True, ignore_permissions=True)
		for c in frappe.get_all("Contact", {"first_name": ["like", f"{P}%"]}, pluck="name"):
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		for dt in ("Ads Metric Snapshot", "Ads Campaign"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		if frappe.db.exists("Excom Source", SOURCE):
			frappe.delete_doc("Excom Source", SOURCE, force=True, ignore_permissions=True)
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _as(self, user):
		frappe.set_user(user)
		permissions.clear_cache()

	def _feed(self, **kw):
		r = ld.feed(str(self.today), str(self.today), page_length=200, **kw)
		self.assertTrue(r["available"], r)
		return {row["name"]: row for row in r["rows"] if row["lead_name"].startswith(P)}

	def test_feed_shows_ad_leads_and_hides_test_leads(self):
		rows = self._feed()
		self.assertEqual(set(rows), {self.breached, self.met, self.pending, self.lead_b})
		self.assertIn(self.test_lead, self._feed(include_test=1), "test leads only on request")
		r = rows[self.breached]
		self.assertEqual((r["ads_campaign_name"], r["account_name"], r["doctype"]), (f"{P} Camp A", f"{P} Acc A", gw.LEAD))

	def test_sla_states(self):
		rows = self._feed()
		self.assertEqual(rows[self.breached]["sla_status"], "Breached")
		self.assertIsNone(rows[self.breached]["first_response_at"])
		self.assertEqual(rows[self.met]["sla_status"], "Met")
		self.assertEqual(rows[self.met]["first_response_secs"], 600)
		self.assertEqual(rows[self.pending]["sla_status"], "Pending")
		self.assertTrue(rows[self.pending]["sla_due_at"])
		self.assertEqual(rows[self.lead_b]["sla_status"], "No SLA")
		s = ld.summary(str(self.today), str(self.today), workspace=f"{P} WS A")
		self.assertEqual(s["sla"]["counts"]["Breached"], 1)
		self.assertEqual([b["name"] for b in s["sla"]["breaches"]], [self.breached])

	def test_company_scope_and_no_cross_company_leads(self):
		self._as(U_A)
		rows = self._feed()
		self.assertEqual(set(rows), {self.breached, self.met, self.pending})
		self._as(U_B)
		self.assertEqual(set(self._feed()), {self.lead_b})
		s = ld.summary(str(self.today), str(self.today))
		self.assertEqual({c["campaign"] for c in s["by_campaign"]}, {self.camp_b})

	def test_excom_lead_visibility_applies(self):
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 1)
		frappe.db.commit()
		try:
			self._as(U_A)
			self.assertEqual(set(self._feed()), {self.breached}, "only the lead given to this user, as everywhere else in Excom")
			s = ld.summary(str(self.today), str(self.today))
			row = next(c for c in s["by_campaign"] if c["campaign"] == self.camp_a)
			self.assertEqual(row["verified_leads"], 1)
		finally:
			frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
			frappe.db.commit()

	def test_platform_and_verified_leads_side_by_side(self):
		s = ld.summary(str(self.today), str(self.today), workspace=f"{P} WS A")
		row = next(c for c in s["by_campaign"] if c["campaign"] == self.camp_a)
		self.assertEqual(row["platform_leads"], 5)
		self.assertEqual(row["verified_leads"], 3, "test lead excluded")
		self.assertAlmostEqual(row["cpl_platform"], 1000 / 5)
		self.assertAlmostEqual(row["cpl_verified"], 1000 / 3)
		self.assertEqual(s["totals"]["verified_leads"], 3)
		self.assertEqual(s["totals"]["test_leads_excluded"], 1)
		v = ld.verified_leads_by_campaign([self.camp_a, self.camp_b], self.today, self.today)
		self.assertEqual(v["counts"], {self.camp_a: 3, self.camp_b: 1})

	def test_funnel_follows_erpnext_links_and_says_what_is_not_available(self):
		opp = frappe.get_doc({"doctype": gw.OPPORTUNITY, "opportunity_from": gw.LEAD, "party_name": self.met, "company": self.company})
		opp.flags.ignore_mandatory = True
		opp.insert(ignore_permissions=True)
		frappe.db.commit()
		f = {s["stage"]: s["count"] for s in ld.funnel([self.breached, self.met, self.pending])}
		self.assertEqual((f["leads"], f["opportunities"], f["quotations"], f["orders"]), (3, 1, 0, 0))
		self._as(U_A)
		f = {s["stage"]: s["count"] for s in ld.funnel([self.breached, self.met])}
		self.assertEqual(ld.quotations_with_orders(["QA-LeadsDash-no-such-quote"]), set(), "the order lookup runs on this Frappe version")
		if not frappe.has_permission(gw.QUOTATION, "read"):
			self.assertIsNone(f["quotations"], "a stage the user cannot read is not available, not zero")
			self.assertIsNone(f["orders"])

	def test_no_ads_access_is_refused(self):
		self._as(U_NONE)
		self.assertRaises(frappe.PermissionError, ld.feed, str(self.today), str(self.today))
		self.assertRaises(frappe.PermissionError, ld.summary, str(self.today), str(self.today))
