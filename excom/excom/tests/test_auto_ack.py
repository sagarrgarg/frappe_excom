"""Auto-acknowledgement fires on the template being set, not on the source's type.

A Website source could be given an Auto Ack Template in the form and would silently never send it:
the gate required source_type in {IndiaMART, TradeIndia, Meta Lead Ads}, so somebody configured an
acknowledgement, expected acknowledgements, and got nothing with no error anywhere. Repeat enquiries
are a separate question, answered per source, because a customer who writes in twice should not be
messaged twice unless you say so.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime


def _cleanup():
	frappe.set_user("Administrator")
	frappe.db.delete("Excom Source Log", {"dedupe_key": ["like", "%QA-ACK-%"]})
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA Ack%"]}, pluck="name"):
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.db.delete("Excom Message", {"thread": t})
			frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		for c in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
			frappe.db.delete(c, {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for lead in frappe.get_all("Lead", {"lead_name": ["like", "QA Ack%"]}, pluck="name"):
		frappe.db.delete("ToDo", {"reference_name": lead})
		for c in frappe.get_all("Dynamic Link", {"link_name": lead, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c})
			frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True, delete_permanently=True)
	for s in frappe.get_all("Excom Source", {"source_name": ["like", "QA Ack%"]}, pluck="name"):
		frappe.delete_doc("Excom Source", s, force=True, ignore_permissions=True)
	frappe.db.commit()


class TestAutoAck(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_cleanup()
		cls.account = (frappe.get_all("Excom Channel Account", {"channel": "whatsapp"}, pluck="name", limit=1) or [None])[0]
		cls.template = (frappe.get_all("WhatsApp Templates", pluck="name", limit=1) or [None])[0]

	@classmethod
	def tearDownClass(cls):
		_cleanup()
		super().tearDownClass()

	def tearDown(self):
		_cleanup()

	def _source(self, name, stype="Website", **extra):
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		return frappe.get_doc({
			"doctype": "Excom Source", "source_name": name, "source_type": stype, "enabled": 1,
			"company": company, "channel_account": self.account, "sla_first_response": 3600,
			"auto_ack_template": self.template, **extra,
		}).insert(ignore_permissions=True)

	def _ingest(self, src, key, phone="9900000961", name="QA Ack Person"):
		from excom.excom.services.intake import ingest

		with patch("frappe.enqueue") as enq:
			r = ingest(src, key, {"name": name, "phone": phone, "message": "Need a quote"}, sync=True)
		acked = [c for c in enq.call_args_list if "send_auto_ack" in str(c)]
		return r, bool(acked)

	def test_a_website_source_with_a_template_now_acknowledges(self):
		"""The bug: the form let you set a template on a Website source and never sent it."""
		if not (self.account and self.template):
			self.skipTest("site has no WhatsApp account or template to point at")
		src = self._source("QA Ack Website")
		_, acked = self._ingest(src, "web:QA-ACK-1")
		self.assertTrue(acked, "a Website source with a template must acknowledge")

	def test_a_repeat_enquiry_is_silent_unless_the_source_asks(self):
		if not (self.account and self.template):
			self.skipTest("site has no WhatsApp account or template to point at")
		src = self._source("QA Ack Once")
		_, first = self._ingest(src, "web:QA-ACK-2a")
		self.assertTrue(first)
		# same person, new enquiry: dedupes onto the open lead, so created is False
		_, second = self._ingest(src, "web:QA-ACK-2b")
		self.assertFalse(second, "a repeat must not be acknowledged when the source has not asked for it")

	def test_with_the_toggle_on_a_repeat_is_acknowledged(self):
		if not (self.account and self.template):
			self.skipTest("site has no WhatsApp account or template to point at")
		src = self._source("QA Ack Repeat", auto_ack_repeat=1, auto_ack_repeat_cooldown_hours=0)
		_, first = self._ingest(src, "web:QA-ACK-3a")
		_, second = self._ingest(src, "web:QA-ACK-3b")
		self.assertTrue(first and second, "with the toggle on, every enquiry is acknowledged")

	def test_the_cooldown_stops_five_enquiries_becoming_five_templates(self):
		from excom.excom.services.intake import _ack_cooldown_passed

		src = self._source("QA Ack Cooldown", auto_ack_repeat=1, auto_ack_repeat_cooldown_hours=24)
		lead = frappe.get_doc({"doctype": "Lead", "lead_name": "QA Ack Cooled", "first_name": "QA Ack Cooled",
		                       "customer_type": "Distributor"})
		lead.flags.ignore_permissions = True
		lead.insert(ignore_permissions=True)
		log = frappe._dict({"lead": lead.name, "lead_doctype": "Lead", "thread": None})

		frappe.db.set_value("Lead", lead.name, "auto_ack_sent_at", now_datetime(), update_modified=False)
		self.assertFalse(_ack_cooldown_passed(log, src), "a second ack inside the window is refused")

		frappe.db.set_value("Lead", lead.name, "auto_ack_sent_at", add_to_date(now_datetime(), hours=-25), update_modified=False)
		self.assertTrue(_ack_cooldown_passed(log, src), "past the window it is allowed again")

		src.auto_ack_repeat_cooldown_hours = 0
		frappe.db.set_value("Lead", lead.name, "auto_ack_sent_at", now_datetime(), update_modified=False)
		self.assertTrue(_ack_cooldown_passed(log, src), "zero hours means no wait")

	def test_switching_a_source_to_manual_clears_a_template_the_form_stops_showing(self):
		src = self._source("QA Ack Switcher")
		self.assertTrue(src.auto_ack_template)
		src.source_type = "Manual"
		src.flags.ignore_permissions = True
		src.save()
		self.assertIsNone(frappe.db.get_value("Excom Source", src.name, "auto_ack_template") or None,
		                  "a stored template the form no longer shows would send messages nobody configured")
