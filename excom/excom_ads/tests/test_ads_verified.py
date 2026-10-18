"""Excom Ads, verified results: ERPNext leads, opportunities, orders and net revenue attributed to Ads
Campaigns; credit notes, cancellations, unattributed orders, scope, finance gating, never summed with
platform figures, and query cost at a few hundred rows.

Real Sales Orders / Sales Invoices (prefix QA Verified), cancelled and deleted afterwards. Hooks of
unrelated third-party apps on the fixture doctypes are switched off while the fixtures are made
(e.g. location_based_series refuses every invoice unless a Location dimension is configured).

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_verified'}"
"""

import time
from contextlib import contextmanager

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from excom.excom.services import crm_compat
from excom.excom.services import crm_gateway as gw
from excom.excom_ads import attribution, dashboard, permissions, verified
from excom.excom_ads.naming import touch_name

P = "QA Verified"
WS, WS_B = f"{P} WS", f"{P} WS B"
BRAND_1, BRAND_2 = f"{P} Brand 1", f"{P} Brand 2"
CUST = f"{P} Customer"
CUST_2 = f"{P} Customer Two"
ITEM = "QA-VERIFIED-ITEM"
SO, SI = "Sales Order", "Sales Invoice"
U_B = "qa_verified_company_b@example.com"
U_NOFIN = "qa_verified_nofin@example.com"
USERS = (U_B, U_NOFIN)
KEEP_HOOKS = ("frappe.", "erpnext.", "india_compliance.", "excom.", "webshop.", "payments.", "hrms.")
PERF_PREFIX = "QA-VERIFIED-PERF-"
SEPT = ("2026-09-01", "2026-09-30")


@contextmanager
def core_hooks_only(doctypes=(SO, SI, gw.QUOTATION, "Customer", "Item")):
	hooks = frappe.get_doc_hooks()
	saved = {dt: {ev: list(fns) for ev, fns in (hooks.get(dt) or {}).items()} for dt in doctypes}
	try:
		for dt in doctypes:
			for ev, fns in (hooks.get(dt) or {}).items():
				hooks[dt][ev] = [f for f in fns if f.startswith(KEEP_HOOKS)]
		yield
	finally:
		for dt, evs in saved.items():
			for ev, fns in evs.items():
				hooks[dt][ev] = fns


class TestAdsVerified(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.company_b = "_Test Ads Company B" if frappe.db.exists("Company", "_Test Ads Company B") else None
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		cls.v16_utm = frappe.get_meta(SI).has_field("utm_campaign")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS, "company": cls.company})
		ins({"doctype": "Ads Brand", "brand_name": BRAND_1, "workspace": WS})
		ins({"doctype": "Ads Brand", "brand_name": BRAND_2, "workspace": WS})
		acc = ins({"doctype": "Ads Account", "account_name": f"{P} Google", "provider": "Google", "external_id": "5550001111", "workspace": WS, "currency": cls.cur})
		cls.acc = acc.name
		scope = {"account": acc.name, "workspace": WS, "company": cls.company, "provider": "Google"}
		camp = lambda name, ext, brand: ins({"doctype": "Ads Campaign", **scope, "campaign_name": name, "external_id": ext, "status": "Active", "brand": brand}).name
		cls.c1 = camp(f"{P} Diwali", "qv1001", BRAND_1)
		cls.c2 = camp(f"{P} B2B", "qv1002", BRAND_2)
		cls.dup1 = camp(f"{P} Dup", "qv1003", BRAND_1)
		cls.dup2 = camp(f"{P} Dup", "qv1004", BRAND_1)
		cls.c_perf = camp(f"{P} Perf", "qv1009", BRAND_1)
		if cls.company_b:
			ins({"doctype": "Ads Workspace", "workspace_name": WS_B, "company": cls.company_b})
		# Platform figures for September: kept apart from everything verified.
		snap = lambda level, ext, date, spend, value, campaign=None, brand=None: ins({"doctype": "Ads Metric Snapshot", **scope, "level": level, "entity_external_id": ext, "date": date, "campaign": campaign, "brand": brand, "currency": cls.cur, "company_currency": cls.cur, "exchange_rate": 1, "spend": spend, "spend_company_currency": spend, "impressions": 1000, "clicks": 50, "leads": 9, "purchases": 4, "purchase_value": value})
		snap("Account", acc.external_id, "2026-09-10", 1000, 5000)
		snap("Campaign", "qv1001", "2026-09-10", 800, 5000, cls.c1, BRAND_1)
		snap("Campaign", "qv1002", "2026-09-10", 200, 0, cls.c2, BRAND_2)
		cls._crm()
		cls._orders()
		for u, roles in ((U_B, ["Excom Admin", "Sales User", "Accounts User"]), (U_NOFIN, ["Excom Marketing", "Sales User"])):
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "roles": [{"role": r} for r in roles]})
		if cls.company_b:
			ins({"doctype": "User Permission", "user": U_B, "allow": "Company", "for_value": cls.company_b, "apply_to_all_doctypes": 1})
		ins({"doctype": "User Permission", "user": U_NOFIN, "allow": "Ads Workspace", "for_value": WS, "apply_to_all_doctypes": 1})
		frappe.db.commit()

	# ─── fixtures ────────────────────────────────────────────────────────────

	@classmethod
	def _lead(cls, name, campaign, created, is_test=0, status=None):
		doc = frappe.get_doc({"doctype": gw.LEAD, "lead_name": name, "company": cls.company, "email_id": f"{name.lower().replace(' ', '.')}@example.com"})
		doc.insert(ignore_permissions=True)
		values = {"ads_campaign": campaign, "ads_account": cls.acc, "ads_is_test": is_test, "ads_first_touch": touch_name("Lead Form", f"qa-verified:{name}"), "creation": created}
		if status:
			values["status"] = status
		frappe.db.set_value(gw.LEAD, doc.name, values, update_modified=False)
		return doc.name

	@classmethod
	def _crm(cls):
		cls.l1 = cls._lead(f"{P} Lead One", cls.c1, "2026-09-05 10:00:00")
		cls.l_test = cls._lead(f"{P} Lead Test", cls.c1, "2026-09-05 11:00:00", is_test=1)
		cls.l3 = cls._lead(f"{P} Lead Three", cls.c1, "2026-09-06 09:00:00")
		cls.l4 = cls._lead(f"{P} Lead Four", cls.c2, "2026-09-07 09:00:00")
		cls.l_unlinked = cls._lead(f"{P} Lead Unlinked", None, "2026-09-07 10:00:00")
		opp = frappe.get_doc({"doctype": gw.OPPORTUNITY, "opportunity_from": gw.LEAD, "party_name": cls.l3, "company": cls.company})
		opp.insert(ignore_permissions=True)
		frappe.db.set_value(gw.OPPORTUNITY, opp.name, "creation", "2026-09-08 09:00:00", update_modified=False)
		cls.opp = opp.name

	@classmethod
	def _orders(cls):
		with core_hooks_only():
			for c in (CUST, CUST_2):
				frappe.get_doc({"doctype": "Customer", "customer_name": c, "customer_type": "Individual", "customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"), "territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"), **({"gst_category": "Unregistered"} if frappe.get_meta("Customer").has_field("gst_category") else {})}).insert(ignore_permissions=True)
			item = {"doctype": "Item", "item_code": ITEM, "item_name": f"{P} Item", "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"), "is_stock_item": 0, "stock_uom": "Nos"}
			if frappe.get_meta("Item").has_field("gst_hsn_code"):
				item["gst_hsn_code"] = frappe.db.get_value("GST HSN Code", {}, "name")
			frappe.get_doc(item).insert(ignore_permissions=True)
			# (a) Order touch → campaign 1. Invoiced 1,000 in September; a 400 credit note in October.
			cls.so1 = cls._so(CUST, "2026-09-08", 1000)
			attribution.record_touch({"touch_source": "Order", "provider": "Google", "campaign_external_id": "qv1001", "channel": "Webshop", "touch_at": now_datetime()}, f"so:{cls.so1}", reference_doctype=SO, reference_name=cls.so1, company=cls.company)
			cls.si1 = cls._si_from(cls.so1, "2026-09-10")
			cls.cn1 = cls._credit_note(cls.si1, "2026-10-05", 400)
			# A cancelled invoice of an attributed order never counts.
			cls.so2 = cls._so(CUST, "2026-09-09", 700)
			attribution.record_touch({"touch_source": "Order", "provider": "Google", "campaign_external_id": "qv1001", "channel": "Webshop", "touch_at": now_datetime()}, f"so:{cls.so2}", reference_doctype=SO, reference_name=cls.so2, company=cls.company)
			cls.si2 = cls._si_from(cls.so2, "2026-09-11")
			frappe.get_doc(SI, cls.si2).cancel()
			# No touch, no UTM, no chain: unattributed, never spread over campaigns.
			cls.so3 = cls._so(CUST_2, "2026-09-12", 300)
			cls.si3 = cls._si_from(cls.so3, "2026-09-13")
			# (c) Quotation for Lead Four (campaign 2) → Sales Order.
			q = frappe.get_doc({"doctype": gw.QUOTATION, "quotation_to": gw.LEAD, "party_name": cls.l4, "company": cls.company, "transaction_date": "2026-09-08", "valid_till": "2026-10-30", "items": [{"item_code": ITEM, "qty": 1, "rate": 2000}]})
			q.flags.ignore_mandatory = True
			q.insert(ignore_permissions=True)
			cls.quotation = q.name
			cls.so4 = cls._so(CUST_2, "2026-09-14", 2000)
			frappe.db.set_value("Sales Order Item", {"parent": cls.so4}, "prevdoc_docname", q.name, update_modified=False)
			cls.si4 = cls._si_from(cls.so4, "2026-09-15")
			cls.si_utm = cls.si_dup = None
			if cls.v16_utm:
				# (b) v16 native utm_campaign = campaign name → campaign 1; an ambiguous name → unattributed.
				for name in (f"{P} Diwali", f"{P} Dup"):
					crm_compat._ensure("UTM Campaign", name)
				cls.si_utm = cls._si(CUST_2, "2026-09-16", 250, {"utm_campaign": f"{P} Diwali"})
				cls.si_dup = cls._si(CUST_2, "2026-09-17", 90, {"utm_campaign": f"{P} Dup"})
		frappe.db.commit()

	@classmethod
	def _so(cls, customer, date, rate):
		so = frappe.get_doc({"doctype": SO, "customer": customer, "company": cls.company, "transaction_date": date, "delivery_date": "2026-10-30", "currency": cls.cur, "items": [{"item_code": ITEM, "qty": 1, "rate": rate, "delivery_date": "2026-10-30"}]})
		so.insert(ignore_permissions=True)
		so.submit()
		return so.name

	@classmethod
	def _si_from(cls, so_name, date):
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		si = make_sales_invoice(so_name)
		si.posting_date, si.set_posting_time, si.due_date = date, 1, "2026-10-30"
		si.insert(ignore_permissions=True)
		si.submit()
		return si.name

	@classmethod
	def _si(cls, customer, date, rate, extra=None):
		si = frappe.get_doc({"doctype": SI, "customer": customer, "company": cls.company, "posting_date": date, "set_posting_time": 1, "due_date": "2026-10-30", "currency": cls.cur, "items": [{"item_code": ITEM, "qty": 1, "rate": rate}], **(extra or {})})
		si.insert(ignore_permissions=True)
		si.submit()
		return si.name

	@classmethod
	def _credit_note(cls, against, date, amount):
		orig = frappe.get_doc(SI, against)
		cn = frappe.get_doc({"doctype": SI, "customer": orig.customer, "company": cls.company, "posting_date": date, "set_posting_time": 1, "is_return": 1, "return_against": against, "currency": cls.cur, "items": [{"item_code": ITEM, "qty": -1, "rate": amount}]})
		cn.insert(ignore_permissions=True)
		cn.submit()
		return cn.name

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		cls._clean_perf()
		customers = frappe.get_all("Customer", {"customer_name": ["like", f"{P}%"]}, pluck="name")
		with core_hooks_only():
			for dt, field in ((SI, "customer"), (SO, "customer")):
				# Credit notes first: they point at the invoices.
				for name in frappe.get_all(dt, {field: ["in", customers or ["-"]]}, pluck="name", order_by="is_return desc" if dt == SI else "creation desc"):
					doc = frappe.get_doc(dt, name)
					if doc.docstatus == 1:
						doc.flags.ignore_links = True
						doc.cancel()
					for ledger in ("GL Entry", "Payment Ledger Entry"):
						frappe.db.delete(ledger, {"voucher_no": name})
					frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
			for name in frappe.get_all(gw.QUOTATION, {"party_name": ["in", frappe.get_all(gw.LEAD, {"lead_name": ["like", f"{P}%"]}, pluck="name") or ["-"]]}, pluck="name"):
				frappe.delete_doc(gw.QUOTATION, name, force=True, ignore_permissions=True)
			for c in customers:
				frappe.delete_doc("Customer", c, force=True, ignore_permissions=True)
			if frappe.db.exists("Item", ITEM):
				frappe.delete_doc("Item", ITEM, force=True, ignore_permissions=True)
		leads = frappe.get_all(gw.LEAD, {"lead_name": ["like", f"{P}%"]}, pluck="name")
		cls._drop_people(gw.LEAD, leads)
		cls._drop_people("Customer", customers)
		for o in frappe.get_all(gw.OPPORTUNITY, {"party_name": ["in", leads or ["-"]]}, pluck="name"):
			frappe.delete_doc(gw.OPPORTUNITY, o, force=True, ignore_permissions=True)
		for lead in leads:
			frappe.delete_doc(gw.LEAD, lead, force=True, ignore_permissions=True)
		for name in (f"{P} Diwali", f"{P} Dup"):
			if frappe.db.exists("UTM Campaign", name):
				frappe.delete_doc("UTM Campaign", name, force=True, ignore_permissions=True)
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		frappe.db.delete("Ads Attribution Touch", {"account": ["in", accs]})
		for dt in ("Ads Metric Snapshot", "Ads Campaign", "Ads Alert"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def _drop_people(cls, doctype, names):
		"""Excom makes an Omni Identity and a Contact for every Lead / Customer: they go first."""
		if not names:
			return
		for oi in set(frappe.get_all("Omni Identity Link", {"linked_doctype": doctype, "linked_name": ["in", names], "parenttype": "Omni Identity"}, pluck="parent")):
			for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
				frappe.db.delete("Excom Thread", {"name": t})
			frappe.db.delete("Excom Message", {"omni_identity": oi})
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			frappe.db.delete("Omni Identity", {"name": oi})
		for c in set(frappe.get_all("Dynamic Link", {"link_doctype": doctype, "link_name": ["in", names], "parenttype": "Contact"}, pluck="parent")):
			for child in ("Dynamic Link", "Contact Email", "Contact Phone"):
				frappe.db.delete(child, {"parent": c})
			frappe.db.delete("Contact", {"name": c})
		frappe.db.delete("ToDo", {"reference_name": ["in", names]})
		frappe.db.delete("Comment", {"reference_doctype": doctype, "reference_name": ["in", names]})

	@classmethod
	def _clean_perf(cls):
		for dt, field in ((gw.LEAD, "name"), (SO, "name"), (SI, "name"), ("Sales Invoice Item", "parent"), ("Ads Attribution Touch", "reference_name")):
			frappe.db.delete(dt, {field: ["like", f"{PERF_PREFIX}%"]})
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	def _as(self, user):
		frappe.set_user(user)
		permissions.clear_cache()

	def _sept(self, **filters):
		return verified.compute({"workspace": WS, **filters} if "company" not in filters else filters, *SEPT)

	def _camp(self, block, name):
		return next((r for r in block["by_campaign"] if r["campaign"] == name), None)

	# ─── leads ───────────────────────────────────────────────────────────────

	def test_leads_qualified_opportunities(self):
		b = self._sept()
		c1, c2 = self._camp(b, self.c1), self._camp(b, self.c2)
		self.assertEqual((c1["leads"], c1["qualified"], c1["opportunities"]), (2, 1, 1), "test lead excluded; Lead Three reached an Opportunity")
		self.assertEqual((c2["leads"], c2["qualified"], c2["opportunities"]), (1, 0, 0))
		self.assertEqual(b["source"], "Verified")
		oct_ = verified.compute({"workspace": WS}, "2026-10-01", "2026-10-31")
		self.assertIsNone(self._camp(oct_, self.c1), "leads count on the day they were created")

	# ─── orders and revenue ──────────────────────────────────────────────────

	def test_credit_note_reduces_original_period_and_cancelled_is_excluded(self):
		"""Acceptance 15."""
		c1 = self._camp(self._sept(), self.c1)
		utm = 250 if self.v16_utm else 0
		self.assertEqual(c1["invoices"], 1 + (1 if self.v16_utm else 0), "the cancelled invoice is not counted")
		self.assertAlmostEqual(c1["revenue"], 1000 - 400 + utm, msg="net = invoice − its credit note, in the invoice's (September) period")
		self.assertAlmostEqual(c1["gross_revenue"], 1000 + utm)
		self.assertAlmostEqual(c1["returns"], -400)
		self.assertEqual(c1["orders"], 2, "both submitted orders count as orders; order 2's invoice was cancelled")
		self.assertAlmostEqual(c1["order_value"], 1700)
		oct_ = verified.compute({"workspace": WS}, "2026-10-01", "2026-10-31")
		c1_oct = self._camp(oct_, self.c1)
		self.assertTrue(c1_oct is None or not c1_oct.get("revenue"), "the October credit note does not show as negative October revenue")

	def test_chain_lead_quotation_order(self):
		c2 = self._camp(self._sept(), self.c2)
		self.assertEqual((c2["orders"], c2["invoices"]), (1, 1))
		self.assertAlmostEqual(c2["revenue"], 2000)
		self.assertEqual(verified.resolve_sales_orders([self.so4])[self.so4], (self.c2, "chain"))
		self.assertEqual(verified.resolve_sales_orders([self.so1])[self.so1], (self.c1, "touch"))
		self.assertEqual(verified.resolve_sales_orders([self.so3])[self.so3], (None, None))

	def test_utm_mapping_name_and_ambiguity(self):
		if not self.v16_utm:
			self.skipTest("v15 Sales Invoice has no utm_campaign")
		b = self._sept()
		self.assertIsNone(self._camp(b, self.dup1))
		self.assertIsNone(self._camp(b, self.dup2), "a UTM value matching two campaigns attributes to neither")
		m = verified._utm_map({(self.company, f"{P} diwali  "), (self.company, "qv1002"), (self.company, f"{P} Dup")})
		self.assertEqual(m[(self.company, f"{P} diwali".lower())], self.c1, "name match is case-insensitive and ignores trailing spaces")
		self.assertEqual(m[(self.company, "qv1002")], self.c2, "the platform campaign ID matches too")
		self.assertIsNone(m[(self.company, f"{P.lower()} dup")])

	def test_unattributed_orders_stay_unattributed(self):
		"""Acceptance 6."""
		b = verified.compute({"company": self.company}, *SEPT)
		un = next(u for u in b["unattributed"] if u["currency"] == self.cur)
		so_total = frappe.db.sql("select count(*), sum(base_net_total) from `tabSales Order` where docstatus=1 and company=%s and transaction_date between %s and %s", (self.company, *SEPT))[0]
		attributed_orders = sum(r["orders"] for r in b["by_campaign"] if r.get("orders"))
		self.assertEqual(un["orders"], so_total[0] - attributed_orders)
		self.assertGreaterEqual(un["orders"], 1, "order 3 is unattributed")
		every = {r["campaign"] for r in b["by_campaign"]}
		self.assertNotIn(None, every)
		# Order 3 / invoice 3 never appear under any campaign.
		total_attr_rev = sum(r["revenue"] or 0 for r in b["by_campaign"])
		dup = 90 if self.v16_utm else 0
		self.assertGreaterEqual(un["revenue"], 300 + dup - 0.001, "invoice 3 (and the ambiguous UTM invoice) are unattributed revenue")
		self.assertAlmostEqual(sum(t["revenue"] for t in b["totals"] if t["currency"] == self.cur), total_attr_rev)
		self.assertIsNone(self._sept()["unattributed"], "a workspace view shows no company-wide unattributed figures")

	def test_platform_and_verified_are_never_summed(self):
		"""Acceptance 7."""
		o = dashboard.overview(*SEPT, workspace=WS)
		t = o["totals"][0]
		self.assertEqual((t["spend"], t["purchase_value"], t["leads"]), (1000, 5000, 9), "platform totals hold only platform figures")
		v = next(x for x in o["verified"]["totals"] if x["currency"] == self.cur)
		self.assertEqual(o["verified"]["source"], "Verified")
		self.assertEqual(v["leads"], 3)
		self.assertNotEqual(v["revenue"], t["purchase_value"])
		self.assertEqual(v["platform_spend"], 1000)
		self.assertAlmostEqual(v["verified_cpl"], 1000 / 3, msg="platform spend ÷ verified leads, labelled as such")
		self.assertAlmostEqual(v["mer"], v["revenue"] / 1000)
		for k in ("revenue", "orders", "qualified"):
			self.assertNotIn(k, t, "no verified key leaks into the platform totals")

	def test_explorer_rows_carry_a_separate_verified_object(self):
		ex = dashboard.explorer(*SEPT, level="Campaign", workspace=WS, with_verified=1)
		row = next(r for r in ex["rows"] if r["name"] == self.c1)
		self.assertEqual(row["metrics"]["spend"], 800)
		self.assertEqual(row["verified"]["leads"], 2)
		idle = next(r for r in ex["rows"] if r["name"] == self.c_perf)
		self.assertEqual(idle["verified"]["leads"], 0, "nothing recorded = 0 verified, not unknown")
		self.assertNotIn("verified", dashboard.explorer(*SEPT, level="Campaign", workspace=WS)["rows"][0])

	# ─── scope and rights ────────────────────────────────────────────────────

	def test_company_b_user_sees_nothing_of_company_a(self):
		if not self.company_b:
			self.skipTest("no second company")
		self._as(U_B)
		b = verified.compute({}, *SEPT)
		self.assertEqual(b["by_campaign"], [])
		self.assertTrue(all(not t.get("leads") for t in b["totals"]))
		b_orders = frappe.db.sql("select count(*) from `tabSales Order` where docstatus=1 and company=%s and transaction_date between %s and %s", (self.company_b, *SEPT))[0][0]
		self.assertEqual(sum(u["orders"] for u in b["unattributed"] or []), b_orders, "only company B's own orders")
		self.assertEqual(verified.compute({"company": self.company}, *SEPT)["by_campaign"], [], "asking for company A does not widen the scope")

	def test_no_view_finance_no_revenue(self):
		self._as(U_NOFIN)
		self.assertFalse(permissions.has_capability("view_finance"))
		b = verified.compute({}, *SEPT)
		c1 = self._camp(b, self.c1)
		self.assertEqual((c1["leads"], c1["orders"]), (2, 2), "counts do not need the finance right")
		for k in ("revenue", "order_value", "gross_revenue", "returns"):
			self.assertIsNone(c1[k], k)
		self.assertFalse(b["available"]["revenue"])
		self.assertIsNone(b["unattributed"], "a workspace-scoped user sees no company-wide figures")
		self.assertTrue(all(t["mer"] is None for t in b["totals"]))

	def test_unlinked_ad_leads_are_reported(self):
		b = verified.compute({"company": self.company}, *SEPT)
		self.assertGreaterEqual(b["leads_unlinked"], 1)

	# ─── cost ────────────────────────────────────────────────────────────────

	def test_query_count_stays_flat_with_hundreds_of_rows(self):
		self._clean_perf()
		now = now_datetime()
		base = {"owner": "Administrator", "modified_by": "Administrator", "creation": now, "modified": now}
		leads = [(f"{PERF_PREFIX}L{i:04d}", f"{PERF_PREFIX}L{i}", "Lead", self.company, self.c_perf, 0, f"2026-07-{1 + i % 28:02d} 10:00:00", now, "Administrator", "Administrator", 0) for i in range(400)]
		frappe.db.bulk_insert(gw.LEAD, ["name", "lead_name", "status", "company", "ads_campaign", "ads_is_test", "creation", "modified", "owner", "modified_by", "docstatus"], leads)
		sos = [(f"{PERF_PREFIX}SO{i:04d}", self.company, CUST, f"2026-07-{1 + i % 28:02d}", 1, 100.0, now, now) for i in range(300)]
		frappe.db.bulk_insert(SO, ["name", "company", "customer", "transaction_date", "docstatus", "base_net_total", "creation", "modified"], sos)
		touches = [(touch_name("Order", f"so:{s[0]}"), "Order", f"so:{s[0]}", now, "Google", "Linked", SO, s[0], self.company, self.acc, self.c_perf, now, now) for s in sos[:200]]
		frappe.db.bulk_insert("Ads Attribution Touch", ["name", "touch_source", "observation_id", "touch_at", "provider", "link_status", "reference_doctype", "reference_name", "company", "account", "campaign", "creation", "modified"], touches)
		sis = [(f"{PERF_PREFIX}SI{i:04d}", self.company, CUST, f"2026-07-{1 + i % 28:02d}", 1, 0, 100.0, now, now) for i in range(300)]
		frappe.db.bulk_insert(SI, ["name", "company", "customer", "posting_date", "docstatus", "is_return", "base_net_total", "creation", "modified"], sis)
		items = [(f"{PERF_PREFIX}SII{i:04d}", f"{PERF_PREFIX}SI{i:04d}", SI, "items", 1, f"{PERF_PREFIX}SO{i:04d}", ITEM, now, now) for i in range(300)]
		frappe.db.bulk_insert("Sales Invoice Item", ["name", "parent", "parenttype", "parentfield", "idx", "sales_order", "item_code", "creation", "modified"], items)
		frappe.db.commit()
		calls = []
		real = frappe.db.sql

		def counting(*a, **k):
			calls.append(1)
			return real(*a, **k)

		frappe.db.sql = counting
		try:
			t0 = time.monotonic()
			b = verified.compute({"company": self.company}, "2026-07-01", "2026-07-31")
			elapsed = time.monotonic() - t0
		finally:
			frappe.db.sql = real
			self._clean_perf()
		c = self._camp(b, self.c_perf)
		self.assertEqual((c["leads"], c["orders"], c["invoices"]), (400, 200, 200))
		self.assertAlmostEqual(c["revenue"], 20000)
		un = next(u for u in b["unattributed"] if u["currency"] == self.cur)
		self.assertGreaterEqual(un["orders"], 100)
		self.assertLess(len(calls), 60, f"{len(calls)} queries: no N+1")
		self.assertLess(elapsed, 5, f"{elapsed:.2f}s")
