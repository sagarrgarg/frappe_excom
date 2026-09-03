"""
Gateway contract suite (P4 §4.2). Runs against the native backend by default; set
EXCOM_CRM_BACKEND=shadow (or pass backend='shadow' to run.run) to run the same assertions against
the fork-rehearsal doctype. Synthetic data only, cleaned up after each test.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_gateway_contract'}"
    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_gateway_contract', 'backend': 'shadow'}"
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from excom.excom.services import crm_compat, crm_gateway as gw
from excom.excom.tests.test_core_flows import _cleanup, _mk_identity, _mk_thread

BACKEND = os.environ.get("EXCOM_CRM_BACKEND", "native")


def _payload(**over):
	p = {"name": "QA Contract Person", "email": "qa.contract@example.com", "phone": "+919900000790", "company_name": "QA Contract Co", "customer_type": "Distributor", "source": "Organic WhatsApp", "campaign": "QA Campaign", "first_touch_channel": "WhatsApp", "source_reference": "qa:contract:1"}
	p.update(over)
	return p


class TestGatewayContract(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from excom.excom.services import crm_shadow
		crm_shadow.use(BACKEND)
		_cleanup()

	@classmethod
	def tearDownClass(cls):
		from excom.excom.services import crm_shadow
		_cleanup()
		crm_shadow.use("native")
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		self.oi = _mk_identity("QA Contract Person", "+919900000790")

	def tearDown(self):
		for dt in (gw.LEAD, gw.OPPORTUNITY, gw.PROSPECT):
			if not frappe.db.exists("DocType", dt):
				continue
			meta = frappe.get_meta(dt)
			names = set()
			if meta.has_field("source_reference"):
				names |= set(frappe.get_all(dt, filters={"source_reference": ["like", "qa:contract%"]}, pluck="name"))
			title = "customer_name" if dt == gw.OPPORTUNITY else "company_name"
			if meta.has_field(title):
				names |= set(frappe.get_all(dt, filters={title: ["like", "QA Contract%"]}, pluck="name"))
			for n in names:
				frappe.db.delete("Comment", {"reference_doctype": dt, "reference_name": n}); frappe.db.delete("Excom Stage Change Log", {"ref_name": n}); frappe.db.delete("ToDo", {"reference_name": n})
				for c in frappe.get_all("Dynamic Link", {"link_name": n, "link_doctype": dt, "parenttype": "Contact"}, pluck="parent"):
					frappe.db.delete("Dynamic Link", {"parent": c}); frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
				frappe.delete_doc(dt, n, force=True, ignore_permissions=True)
		_cleanup()

	# --- create / provenance / identity ---------------------------------------------------------
	def test_create_lead_stamps_provenance_and_attribution(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		self.assertEqual(r.doctype, gw.LEAD)
		doc = frappe.get_doc(r.doctype, r.name)
		self.assertEqual(doc.first_touch_channel, "WhatsApp")
		self.assertTrue(doc.first_touch_at)
		self.assertEqual(doc.source_reference, "qa:contract:1")
		self.assertEqual(doc.intake_stage, "Classified")  # customer_type given → the update hook classifies it
		bare = gw.create_lead(_payload(name="QA Contract Bare", email="qa.contract.bare@example.com", phone="+919900000791", customer_type="", source_reference="qa:contract:2"), ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(bare.doctype, bare.name, "intake_stage"), "Captured")
		attr = crm_compat.get_attribution(doc)
		self.assertEqual(attr["source"], "Organic WhatsApp")

	def test_link_identity_both_directions_and_precedence(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		gw.link_identity(r, self.oi.name)
		self.assertEqual(frappe.db.get_value(r.doctype, r.name, "omni_identity"), self.oi.name)
		found = gw.find_open_records_for_identity(self.oi.name)
		self.assertEqual([(x.doctype, x.name) for x in found], [(r.doctype, r.name)])
		kinds = gw.kinds_for_identities([self.oi.name])[self.oi.name]
		self.assertEqual(kinds[0]["doctype"], gw.LEAD)
		self.assertEqual(kinds[0]["customer_type"], "Distributor")

	def test_closed_lead_drops_out_of_precedence(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		gw.link_identity(r, self.oi.name)
		self.assertEqual(gw.close_record(r, "Lost", "Price", ""), "Do Not Contact")
		self.assertEqual(gw.find_open_records_for_identity(self.oi.name), [])
		self.assertEqual(gw.reopen_record(r), "Open")
		self.assertEqual(len(gw.find_open_records_for_identity(self.oi.name)), 1)

	def test_promote_thread_creates_and_links_once(self):
		t = _mk_thread(self.oi.name, key="qa-contract-1")
		from excom.excom.services.crm_flow import resolve_or_create_lead
		r1, created1 = resolve_or_create_lead(self.oi.name, "WhatsApp", _payload(), True)
		r2, created2 = resolve_or_create_lead(self.oi.name, "WhatsApp", _payload(), True)
		self.assertTrue(created1); self.assertFalse(created2)
		self.assertEqual(r1.name, r2.name)
		self.assertTrue(t.name)

	def test_find_lead_by_contact_prevents_duplicate_email(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		self.assertEqual(gw.find_lead_by_contact("QA.Contract@example.com", None).name, r.name)
		self.assertEqual(gw.find_lead_by_contact(None, "9900000790").name, r.name)

	# --- ownership --------------------------------------------------------------------------------
	def test_reassign_open_leads(self):
		r = gw.create_lead(_payload(owner="Administrator"), ignore_permissions=True)
		self.assertEqual(frappe.db.get_value(r.doctype, r.name, "lead_owner"), "Administrator")
		self.assertEqual(gw.reassign_open_leads("Administrator", None) >= 1, True)
		self.assertIsNone(frappe.db.get_value(r.doctype, r.name, "lead_owner"))
		self.assertEqual(gw.open_lead_counts_by_owner().get("Administrator", 0) >= 0, True)

	# --- lists ------------------------------------------------------------------------------------
	def test_list_intake_shows_unqualified_only(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		names = {x["name"] for x in gw.list_intake({})}
		self.assertIn(r.name, names)
		frappe.db.set_value(r.doctype, r.name, "intake_stage", "Qualified")
		names = {x["name"] for x in gw.list_intake({})}
		self.assertNotIn(r.name, names)

	# --- conversion + stages (Opportunity stays native in both backends) -----------------------
	def test_convert_to_opportunity_and_advance_stage(self):
		r = gw.create_lead(_payload(), ignore_permissions=True)
		gw.link_identity(r, self.oi.name)
		opp = gw.convert(r, gw.OPPORTUNITY)
		self.assertEqual(opp.doctype, gw.OPPORTUNITY)
		doc = frappe.get_doc(opp.doctype, opp.name)
		self.assertEqual(doc.pipeline_stage, "Qualified")
		self.assertEqual(doc.customer_type, "Distributor")
		self.assertEqual(doc.omni_identity, self.oi.name)
		gw.write_stage(opp, "Territory Check")
		doc.reload()
		self.assertEqual(doc.pipeline_stage, "Territory Check")
		self.assertEqual(doc.sales_stage, "Qualification")
		self.assertEqual(int(doc.probability), 20)
		self.assertIn(opp.name, {x["name"] for x in gw.list_pipeline("Distributor")})
		frappe.db.set_value(opp.doctype, opp.name, "source_reference", "qa:contract:opp")
		frappe.db.set_value(opp.doctype, opp.name, "customer_name", "QA Contract Co")

	def test_stage_map_covers_every_pipeline(self):
		for ct, stages in gw.PIPELINES.items():
			for s in stages:
				self.assertIn(s, gw.STAGES, f"{ct}: {s} not in STAGES")
				self.assertIn(ct, gw.STAGES[s]["types"], f"{ct}: {s} not allowed for type")
