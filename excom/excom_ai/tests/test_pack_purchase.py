"""Excom AI Phase 2C: the Purchase pack (HLD-007 §8.3). Done when an RFQ is drafted, sent, and its
replies parsed into Supplier Quotation drafts; also PO chasers, comparison with price history,
supplier discovery, readiness and the builder's recipes. The model is ai_interface's scripted
adapter; sending, the mailbox and vision are patched, so nothing leaves the machine."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, formatdate, getdate, now_datetime, nowdate

from excom.excom.tests.fixtures import purge_users
from excom.excom_ai import templates
from excom.excom_ai.engine import ownership, proposals, registry, runner, tasks
from excom.excom_ai.packs import purchase

OWNER = "qa.pur.owner@example.com"
BACKUP = "qa.pur.backup@example.com"
TEAM = "QA Pur Desk"
BOX = "QA Pur Box"
WA = "QA Pur WhatsApp"
WABA = "qapurwaba0001"
INKS, MOBILE = "QA Pur Inks", "+91 90000 71234"  # a supplier with a WhatsApp number and no email
WA_TEXT = {  # approved WhatsApp templates (made up here, never sent to Meta)
	"purchase.po_chase": "Hello {{1}}, this is about our purchase order {{2}}, which was due on {{3}}. Could you share the "
		"dispatch date, please?",
	"purchase.rfq_reminder": "Hello {{1}}, our request for quotation {{2}} is still waiting for your quote. Could you send "
		"it by {{3}}, please?",
}
GROUP = "QA Pur Pigments"
SGROUP = "QA Pur Suppliers"
DOMAIN = "qa-pur.example"
ITEMS = {"QA-PUR-RED": "QA Pur Red Pigment", "QA-PUR-BLUE": "QA Pur Blue Pigment"}
SUPPLIERS = {"QA Pur Colours": f"sales@colours.{DOMAIN}", "QA Pur Dyes": f"info@dyes.{DOMAIN}"}
PTYPE, PROVIDER = "_TP Scripted", "_TP Purchase"
# ERPNext reads the supplier's payable account when a Supplier Quotation is saved; the pack role needs
# Select on Account for that (constants.PACK_PERMISSIONS; granted here too when a migrate has not run yet).
ACCOUNT_PERM = "qa-pur-account-select"
WORDS = {"intro": "We hope you are well and would welcome your best quotation for the items below.",
	"closing": "We look forward to hearing from you."}
PDF_TEXT = """COLOURS LTD QUOTATION
Item | Description | Qty | Unit | Unit price | Currency
Red pigment | 500 | Nos | 120.00 | INR (110.00 from 1000 Nos)
Blue pigment | 300 | pcs | 80.00 | INR
Mystery shade 7 | per kg 55.00
Payment 30 days. Valid 30 days. Freight extra."""


def _qa_suppliers() -> list[str]:
	return frappe.get_all("Supplier", {"supplier_name": ["like", "QA Pur%"]}, pluck="name")


def _cleanup(rules: list | None = None, tries: int = 4):
	"""Everything the tests made. Other suites write to the same site at the same time, so a
	snapshot clash (MariaDB 1020) or a deadlock is retried rather than left half done."""
	import time

	for attempt in range(tries):
		try:
			return _cleanup_once(rules)
		except (frappe.QueryDeadlockError, frappe.QueryTimeoutError):
			frappe.db.rollback()
			if attempt == tries - 1:
				raise
			time.sleep(3)


def _cleanup_once(rules: list | None = None):
	frappe.set_user("Administrator")
	agents = frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Pur%"]}, pluck="name")
	for dt in ("Excom AI Proposal", "Excom AI Task", "Excom AI Knowledge", "Excom AI Readiness Gap"):
		for agent in agents:
			frappe.db.delete(dt, {"agent": agent})
	suppliers = _qa_suppliers()
	items = list(ITEMS)
	for sq in set(frappe.get_all("Supplier Quotation", {"supplier": ["in", suppliers or ["-"]]}, pluck="name")):
		frappe.delete_doc("Supplier Quotation", sq, force=True, ignore_permissions=True)
	rfqs = set(frappe.get_all("Request for Quotation Supplier", {"supplier": ["in", suppliers or ["-"]]}, pluck="parent"))
	rfqs |= set(frappe.get_all("Request for Quotation Item", {"item_code": ["in", items]}, pluck="parent"))
	for rfq in rfqs:
		for f in frappe.get_all("File", {"attached_to_doctype": "Request for Quotation", "attached_to_name": rfq}, pluck="name"):
			frappe.delete_doc("File", f, force=True, ignore_permissions=True)
		frappe.db.delete("Comment", {"reference_name": rfq})
		frappe.delete_doc("Request for Quotation", rfq, force=True, ignore_permissions=True)
	for parent, child in (("Purchase Order", "Purchase Order Item"), ("Purchase Invoice", "Purchase Invoice Item"),
			("Material Request", "Material Request Item")):
		names = set(frappe.get_all(child, {"item_code": ["in", items]}, pluck="parent"))
		for n in names:
			frappe.db.delete(child, {"parent": n})
			for grandchild in ("Purchase Taxes and Charges", "Payment Schedule"):
				frappe.db.delete(grandchild, {"parent": n})
			frappe.db.delete("Comment", {"reference_name": n})
			frappe.db.delete("ToDo", {"reference_name": n})
			frappe.db.delete(parent, {"name": n})
	accs = frappe.get_all("Excom Channel Account", {"account_name": ["in", [BOX, WA]]}, pluck="name")
	_drop_templates(agents)
	ids = set(frappe.get_all("Omni Identity", {"normalized_phone": MOBILE_DIGITS}, pluck="name"))
	ids |= set(frappe.get_all("Omni Identity", {"primary_whatsapp": MOBILE_DIGITS}, pluck="name"))
	for t in frappe.get_all("Excom Thread", {"account": ["in", accs or ["-"]]}, ["name", "omni_identity"]):
		ids.add(t.omni_identity)
		frappe.db.delete("Excom Message", {"thread": t.name})
		frappe.db.delete("Comment", {"reference_name": t.name})
		frappe.delete_doc("Excom Thread", t.name, force=True, ignore_permissions=True)
	for oi in ids | set(frappe.get_all("Omni Identity", {"primary_email": ["like", f"%{DOMAIN}"]}, pluck="name")):
		if oi and frappe.db.exists("Omni Identity", oi):
			for child in ("Omni Identity Link", "Omni Identity Channel", "Omni Identity Alias"):
				frappe.db.delete(child, {"parent": oi})
			if frappe.db.table_exists("Excom Consent"):
				frappe.db.delete("Excom Consent", {"omni_identity": oi})
			frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	for c in set(frappe.get_all("Dynamic Link", {"link_doctype": "Supplier", "link_name": ["in", suppliers or ["-"]],
			"parenttype": "Contact"}, pluck="parent")):
		frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
	for s in suppliers:
		frappe.delete_doc("Supplier", s, force=True, ignore_permissions=True)
	frappe.db.delete("Excom AI Company", {"domain": ["like", f"%{DOMAIN}"]})
	frappe.db.delete("Item Price", {"item_code": ["in", items]})
	if frappe.db.table_exists("Cost Valuation Rule"):
		frappe.db.delete("Cost Valuation Rule", {"item_code": ["in", items]})
	for item in items:
		if frappe.db.exists("Item", item):
			frappe.delete_doc("Item", item, force=True, ignore_permissions=True)
	for dt, name in (("Item Group", GROUP), ("Supplier Group", SGROUP)):
		if frappe.db.exists(dt, name):
			frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	for name in agents:
		user = frappe.db.get_value("Excom AI Agent", name, "service_user")
		frappe.delete_doc("Excom AI Agent", name, force=True, ignore_permissions=True)
		if user and frappe.db.exists("User", user):
			frappe.db.delete("Excom Team Member", {"user": user})
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)
	for a in accs:
		if frappe.db.get_value("Excom Channel Account", a, "channel") == "whatsapp":
			frappe.db.delete("Excom Channel Account", a)  # no hooks: nothing may reach Meta
			frappe.db.sql("DELETE FROM `__Auth` WHERE doctype = 'Excom Channel Account' AND name = %s", a)
		else:
			frappe.delete_doc("Excom Channel Account", a, force=True, ignore_permissions=True)
	if frappe.db.exists("Excom Team", TEAM):
		frappe.delete_doc("Excom Team", TEAM, force=True, ignore_permissions=True)
	if frappe.db.get_value("Custom DocPerm", {"parent": "Account", "role": "Excom AI Purchase"}, "name") == ACCOUNT_PERM:
		frappe.delete_doc("Custom DocPerm", ACCOUNT_PERM, force=True, ignore_permissions=True)
		frappe.clear_cache(doctype="Account")
	for rule in rules or []:
		frappe.db.delete("Excom AI Readiness Gap", {"rule": rule})
		frappe.delete_doc("Excom AI Readiness Rule", rule, force=True, ignore_permissions=True)
	frappe.db.delete("Notification Log", {"for_user": ["in", [OWNER, BACKUP]]})
	frappe.db.delete("ToDo", {"allocated_to": ["in", [OWNER, BACKUP]]})
	purge_users(OWNER, BACKUP)
	if "ai_interface" in frappe.get_installed_apps():
		for dt, name in (("AI Provider", PROVIDER), ("AI Provider Type", PTYPE)):
			if frappe.db.exists(dt, name):
				frappe.delete_doc(dt, name, force=True, ignore_permissions=True)
	frappe.db.commit()


MOBILE_DIGITS = "".join(c for c in MOBILE if c.isdigit())


def _drop_templates(agents: list[str]) -> None:
	"""The steward's rows the tests made. WhatsApp Templates rows are deleted without hooks: their
	on_trash deletes the template on Meta."""
	frappe.db.delete("Excom AI Template Need", {"wa_business_id": WABA})
	for agent in agents:
		frappe.db.delete("Excom AI Template Need", {"agent": agent})
	for t in frappe.get_all("WhatsApp Templates", {"actual_name": ["like", "qa_pur_%"]}, pluck="name"):
		for child in ("WhatsApp Button", "WhatsApp Template Linked Account"):
			frappe.db.delete(child, {"parent": t})
		frappe.db.delete("WhatsApp Templates", t)


def blank_pdf() -> bytes:
	"""A real (one blank page) PDF: the File doctype reads what it stores."""
	import io

	from pypdf import PdfWriter

	w, buf = PdfWriter(), io.BytesIO()
	w.add_blank_page(width=72, height=72)
	w.write(buf)
	return buf.getvalue()


class FakeMailbox:
	"""A mail server that knows a few messages and their attachments."""

	def __init__(self, messages: dict, files: dict | None = None):
		self.messages, self.files = messages, files or {}

	def fetch_full(self, account, raw):
		return self.messages[raw]

	def fetch_attachment(self, account, raw, attachment_id):
		return self.files[attachment_id]


class TestPurchasePack(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.flags.mute_emails = True
		_cleanup()
		# Readiness rules the tests seed are removed again, even when setup itself fails part way
		# (class cleanups run then; tearDownClass does not).
		cls.rules_before = set(frappe.get_all("Excom AI Readiness Rule", {"agent_type": "Purchase"}, pluck="name"))
		cls.addClassCleanup(cls.final_cleanup)
		cls.company = frappe.db.get_value("Company", {"default_currency": "INR"}, "name")
		for email in (OWNER, BACKUP):
			u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
			u.insert(ignore_permissions=True)
			u.add_roles("Excom Agent", "Purchase Manager")
		frappe.get_doc({"doctype": "Excom Team", "team_name": TEAM, "members": [
			{"user": OWNER, "role": "Manager"}, {"user": BACKUP, "role": "Member"}]}).insert(ignore_permissions=True)
		cls.box = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": BOX, "channel": "email", "status": "Active",
			"email_address": f"buying@ours.{DOMAIN}"}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "Item Group", "item_group_name": GROUP, "parent_item_group": "All Item Groups"}).insert(
			ignore_permissions=True)
		frappe.get_doc({"doctype": "Supplier Group", "supplier_group_name": SGROUP, "parent_supplier_group": "All Supplier Groups"}
			).insert(ignore_permissions=True)
		hsn = frappe.db.get_value("GST HSN Code", {}) if frappe.db.table_exists("GST HSN Code") else None
		for code, name in ITEMS.items():
			frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": name, "item_group": GROUP, "stock_uom": "Nos",
				"is_stock_item": 0, "is_purchase_item": 1, "gst_hsn_code": hsn}).insert(ignore_permissions=True)
		cls.suppliers = {}
		for name, email in SUPPLIERS.items():
			s = frappe.get_doc({"doctype": "Supplier", "supplier_name": name, "supplier_type": "Company",
				"supplier_group": SGROUP, "country": "India"}).insert(ignore_permissions=True)
			frappe.get_doc({"doctype": "Contact", "first_name": name, "is_primary_contact": 1,
				"email_ids": [{"email_id": email, "is_primary": 1}], "links": [{"link_doctype": "Supplier", "link_name": s.name}]}
				).insert(ignore_permissions=True)
			cls.suppliers[s.name] = email
		cls.colours, cls.dyes = list(cls.suppliers)
		cls.inks = frappe.get_doc({"doctype": "Supplier", "supplier_name": INKS, "supplier_type": "Company",
			"supplier_group": SGROUP, "country": "India"}).insert(ignore_permissions=True).name
		frappe.get_doc({"doctype": "Contact", "first_name": INKS, "is_primary_contact": 1,
			"phone_nos": [{"phone": MOBILE, "is_primary_mobile_no": 1}],
			"links": [{"link_doctype": "Supplier", "link_name": cls.inks}]}).insert(ignore_permissions=True)
		# a WhatsApp number of ours, stored without a token so nothing asks Meta about it
		cls.wa = frappe.get_doc({"doctype": "Excom Channel Account", "account_name": WA, "channel": "whatsapp",
			"status": "Active", "wa_phone_id": "qa-pur-phone-1", "wa_business_id": WABA, "wa_display_phone": "+91 90000 00001"}
			).insert(ignore_permissions=True)
		# price history: Red was last bought at 100 (a received PO); Blue is 90 on the buying price list
		cls.history_po = cls.purchase_order(cls.colours, {"QA-PUR-RED": (400, 100)}, days_ago=40, received=True)
		frappe.get_doc({"doctype": "Item Price", "price_list": "Standard Buying", "item_code": "QA-PUR-BLUE",
			"price_list_rate": 90}).insert(ignore_permissions=True)
		cls.mpc = "material_price_control" in frappe.get_installed_apps() and frappe.db.table_exists("Cost Valuation Rule")
		if cls.mpc:
			frappe.get_doc({"doctype": "Cost Valuation Rule", "rule_for": "Item", "item_code": "QA-PUR-BLUE", "expected_rate": 85,
				"allowed_variance_pct": 5, "enabled": 1}).insert(ignore_permissions=True)
		if "ai_interface" in frappe.get_installed_apps():
			from ai_interface.tests.utils import make_provider, make_provider_type

			make_provider_type(PTYPE, adapter_path="ai_interface.tests.test_chat.ScriptedAdapter")
			make_provider(PROVIDER, PTYPE, models=[{"model_id": "m-1", "cost_in": 0.000004, "cost_out": 0.00002}], api_base_url="main")
		if not frappe.db.exists("Custom DocPerm", {"parent": "Account", "role": "Excom AI Purchase"}):
			perm = frappe.get_doc({"doctype": "Custom DocPerm", "parent": "Account", "parenttype": "DocType",
				"parentfield": "permissions", "role": "Excom AI Purchase", "permlevel": 0, "select": 1})
			perm.name = ACCOUNT_PERM
			perm.db_insert()
			frappe.clear_cache(doctype="Account")
		registry.load()
		purchase.seed()
		frappe.db.commit()

	@classmethod
	def final_cleanup(cls):
		import os

		if os.environ.get("QA_KEEP"):
			return
		frappe.db.rollback()
		made = set(frappe.get_all("Excom AI Readiness Rule", {"agent_type": "Purchase"}, pluck="name")) - cls.rules_before
		_cleanup(sorted(made))

	@classmethod
	def purchase_order(cls, supplier, lines: dict, days_ago: int = 0, schedule_in: int = 7, received: bool = False) -> str:
		"""A submitted Purchase Order for fixtures: priced by ERPNext, stored without the site's own
		validations (a site app here wants a Location on every order), then marked submitted in the
		database (no stock or accounting entries are needed for what the pack reads)."""
		date = add_days(nowdate(), -days_ago)
		po = frappe.get_doc({"doctype": "Purchase Order", "supplier": supplier, "company": cls.company, "transaction_date": date,
			"schedule_date": add_days(nowdate(), schedule_in), "currency": "INR", "conversion_rate": 1,
			"items": [{"item_code": code, "qty": qty, "rate": rate, "schedule_date": add_days(nowdate(), schedule_in)}
				for code, (qty, rate) in lines.items()]})
		po.run_method("set_missing_values")
		for it, (qty, rate) in zip(po.items, lines.values(), strict=True):
			it.update({"qty": qty, "rate": rate, "conversion_factor": it.conversion_factor or 1})
		po.calculate_taxes_and_totals()
		po.name = f"QA-PUR-PO-{frappe.generate_hash(length=8)}"
		po.flags.update(name_set=True, ignore_validate=True, ignore_mandatory=True, ignore_permissions=True)
		po.insert(ignore_permissions=True)
		frappe.db.set_value("Purchase Order", po.name, {"docstatus": 1, "per_received": 100 if received else 0,
			"status": "Completed" if received else "To Receive and Bill"}, update_modified=False)
		frappe.db.set_value("Purchase Order Item", {"parent": po.name}, "docstatus", 1, update_modified=False)
		if received:
			for it in po.items:
				frappe.db.set_value("Purchase Order Item", it.name, "received_qty", it.qty, update_modified=False)
		return po.name

	def setUp(self):
		if "ai_interface" not in frappe.get_installed_apps():
			self.skipTest("ai_interface is not installed")
		frappe.set_user("Administrator")
		frappe.db.set_single_value("Excom AI Settings", "enabled", 1)
		self.addCleanup(frappe.db.set_single_value, "Excom AI Settings", "enabled", 0)
		type(self).n = getattr(type(self), "n", 0) + 1

	def agent(self, channels: bool = True, whatsapp: bool = False, policies: dict | None = None):
		rows = [{"channel_account": self.box.name, "purpose": "Outreach and replies"}] if channels else []
		if whatsapp:
			rows.append({"channel_account": self.wa.name, "purpose": "Outreach and replies"})
		a = frappe.get_doc({"doctype": "Excom AI Agent", "agent_name": f"QA Pur {self.n}", "agent_type": "Purchase",
			"owner_user": OWNER, "backup_user": BACKUP, "team": TEAM, "company": self.company, "channels": rows}
			).insert(ignore_permissions=True)
		for action, mode in (policies or {}).items():
			frappe.db.set_value("Excom AI Policy", next(r.name for r in a.policies if r.action == action), "mode", mode)
		a.db_set({"status": "Live", "brief_approved_on": now_datetime(), "readiness_state": "Ready"})
		ownership.evaluate(frappe.get_doc("Excom AI Agent", a.name))
		frappe.db.commit()
		return frappe.get_doc("Excom AI Agent", a.name)

	def scripted(self, *answers):
		from ai_interface.tests.test_chat import ScriptedAdapter, answer
		from ai_interface.tests.utils import settings_for_test

		ScriptedAdapter.script = {"main": [answer(json.dumps(a)) for a in answers]}
		ScriptedAdapter.seen = []
		s = settings_for_test(default_provider=PROVIDER, default_model="m-1")
		real = frappe.get_single
		return patch("frappe.get_single", side_effect=lambda dt: s if dt == "AI Settings" else real(dt))

	def run_now(self, t, *answers):
		self.assertTrue(t, "the task was queued")
		frappe.db.set_value("Excom AI Task", t, "status", "Running")
		frappe.db.commit()
		with self.scripted(*answers):
			runner.run_task(t)
		row = frappe.db.get_value("Excom AI Task", t, ["status", "outcome", "error"], as_dict=True)
		self.assertEqual(row.status, "Done", row.error)
		return row.outcome

	def as_owner(self, fn, *args, **kwargs):
		frappe.set_user(OWNER)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def fake_send(self):
		"""send_email_reply, recording an outbound message on the conversation as the real one does."""
		def send(thread_name, to, subject, body_html, **kwargs):
			return frappe.get_doc({"doctype": "Excom Message", "thread": thread_name, "channel": "email",
				"account": self.box.name, "account_doctype": "Excom Channel Account", "direction": "Outbound",
				"message_type": "Email", "content_text": frappe.utils.strip_html_tags(body_html)[:500],
				"content_json": json.dumps({"subject": subject}), "delivery_status": "Sent",
				"provider_message_id": f"qa-pur-out-{frappe.generate_hash(length=10)}"}).insert(ignore_permissions=True).name

		return patch("excom.excom.channels.email.outbound.send_email_reply", side_effect=send)

	# ---- the template steward (HLD-008)

	def steward_on(self):
		"""Needs are recorded and the first send of a new template waits for a person."""
		before = {f: frappe.db.get_single_value("Excom AI Settings", f) for f in ("templates_enabled", "template_first_use_review")}
		for f in before:
			frappe.db.set_single_value("Excom AI Settings", f, 1)
		frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")

		def restore():
			for f, v in before.items():
				frappe.db.set_single_value("Excom AI Settings", f, v or 0)
			frappe.clear_document_cache("Excom AI Settings", "Excom AI Settings")
			_drop_templates(frappe.get_all("Excom AI Agent", {"agent_name": ["like", "QA Pur%"]}, pluck="name"))
			frappe.db.commit()

		self.addCleanup(restore)

	def wa_template(self, a, key: str) -> str:
		"""An approved WhatsApp template meeting the need, stored the way template sync stores rows
		(no hooks, so nothing goes to Meta), and its Met need."""
		from excom.excom.doctype.whatsapp_templates.whatsapp_templates import upsert_doc_without_hooks

		spec, actual = templates.NEEDS[key], f"qa_pur_{key.split('.')[1]}"
		doc = frappe.new_doc("WhatsApp Templates")
		doc.update({"template_name": actual, "actual_name": actual, "template": WA_TEXT[key], "language": "en",
			"language_code": "en", "category": "UTILITY", "status": "APPROVED", "template_type": "Standard",
			"whatsapp_account": self.wa.name, "xai_variable_map": json.dumps(spec["variables"]), "xai_first_use_done": 0,
			"xai_back_translation": WA_TEXT[key]})
		doc.name = f"{actual}-en"
		upsert_doc_without_hooks(doc, "WhatsApp Button", "buttons")
		need = frappe.get_doc({"doctype": "Excom AI Template Need", "need_key": key, "channel": "WhatsApp", "category": "UTILITY",
			"language": "en", "agent": a.name, "agents": a.name, "channel_account": self.wa.name, "wa_business_id": WABA,
			"unique_key": templates.unique_key(key, "WhatsApp", "en", a.name, self.wa.name), "status": "Met", "source": "Written",
			"template": doc.name, "body": WA_TEXT[key], "variable_map": json.dumps(spec["variables"])}).insert(ignore_permissions=True)
		frappe.db.set_value("WhatsApp Templates", doc.name, "xai_need", need.name, update_modified=False)
		frappe.db.commit()
		return doc.name

	def opt_in(self) -> str:
		"""The supplier's number opted in to WhatsApp (a Granted in the consent ledger)."""
		from excom.excom.services.consent import record_consent
		from excom.excom.services.identity_sync import _find_existing_identity

		identity = _find_existing_identity(phone=MOBILE) or frappe.get_doc({"doctype": "Omni Identity", "display_name": INKS,
			"primary_phone": MOBILE, "status": "Active", "is_master": 1}).insert(ignore_permissions=True).name
		consent = record_consent(identity, "Granted", "whatsapp", "transactional", source="manual")
		self.assertTrue(consent)
		frappe.db.commit()

		def withdraw():  # the next test starts with a supplier who never opted in
			frappe.db.delete("Excom Consent", {"name": consent})
			frappe.db.commit()

		self.addCleanup(withdraw)
		return identity

	def fake_wa_send(self):
		"""send_template_to_thread with its agent send rules (approved, our number, opted in), recording
		the outbound message instead of calling Meta."""
		from excom.excom_ai import send_rules

		def send(thread_id, template_name, variables="[]", **kwargs):
			send_rules.check(thread_id, purpose="outreach", window=False, template=template_name)
			return {"success": True, "message_name": frappe.get_doc({"doctype": "Excom Message", "thread": thread_id,
				"channel": "whatsapp", "account": self.wa.name, "account_doctype": "Excom Channel Account",
				"direction": "Outbound", "message_type": "Template", "content_text": variables, "delivery_status": "Sent",
				"provider_message_id": f"wamid.qa-pur-{frappe.generate_hash(length=10)}"}).insert(ignore_permissions=True).name}

		return patch("excom.excom.api.chat.send_template_to_thread", side_effect=send)

	def needs(self, key: str, channel: str = "WhatsApp") -> list:
		return frappe.get_all("Excom AI Template Need", {"need_key": key, "channel": channel, "status": ["!=", "Met"],
			"wa_business_id": WABA}, ["name", "status", "agents", "wa_business_id"])

	def supplier_writes(self, thread, text, attachments=None):
		identity = frappe.db.get_value("Excom Thread", thread, "omni_identity")
		raw = f"qa-pur-in-{frappe.generate_hash(length=10)}"
		msg = frappe.get_doc({"doctype": "Excom Message", "thread": thread, "channel": "email", "account": self.box.name,
			"account_doctype": "Excom Channel Account", "omni_identity": identity, "direction": "Inbound",
			"message_type": "Email", "content_text": text[:140], "content_json": json.dumps({"subject": "Re: RFQ"}),
			"delivery_status": "Delivered", "provider_message_id": raw}).insert(ignore_permissions=True)
		frappe.db.commit()
		return msg.name, {raw: {"body_text": text, "attachments": attachments or []}}

	def rfq_now(self, a, **params):
		t = tasks.add(a.name, "draft_rfq", dedupe_key=f"qa-pur-rfq-{frappe.generate_hash(length=6)}", params=params)
		return self.run_now(t, WORDS)

	# ---- the done-when: drafted, sent, replies into Supplier Quotation drafts

	def test_an_rfq_is_drafted_sent_and_its_replies_become_supplier_quotation_drafts(self):
		a = self.agent()
		out = self.rfq_now(a, items=[{"item_code": "QA-PUR-RED", "qty": 500}, {"item_code": "QA-PUR-BLUE", "qty": 300}],
			suppliers=[self.colours, self.dyes])
		self.assertIn("2 emails proposed", out)

		# drafted: the RFQ is a draft (create_record is Auto) and each supplier's email waits for a person
		made = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "create_rfq"})
		self.assertEqual(made.status, "Done", made.error)
		rfq = frappe.get_doc("Request for Quotation", made.executed_name)
		self.assertEqual(rfq.docstatus, 0, "never submitted by the agent")
		self.assertEqual({i.item_code: i.qty for i in rfq.items}, {"QA-PUR-RED": 500, "QA-PUR-BLUE": 300})
		self.assertEqual({s.supplier: (s.email_id, s.send_email) for s in rfq.suppliers},
			{s: (e, 0) for s, e in self.suppliers.items()}, "ERPNext will not email them again on submit")
		sends = {p.subject_name: p for p in frappe.get_all("Excom AI Proposal", {"agent": a.name, "action": "send_rfq"},
			["name", "subject_name", "status", "policy_action", "to_email", "payload", "checks"])}
		self.assertEqual(set(sends), set(self.suppliers))
		for s, p in sends.items():
			self.assertEqual((p.status, p.policy_action, p.to_email), ("Pending", "rfq", self.suppliers[s]))
			body = json.loads(p.payload)["body"]
			self.assertIn("QA Pur Red Pigment (QA-PUR-RED): 500 Nos", body, "quantities are written by code")
			self.assertIn("QA Pur Blue Pigment (QA-PUR-BLUE): 300 Nos", body)
			self.assertIn(WORDS["intro"], body)
			self.assertIn("not an order", body)
			self.assertTrue(json.loads(p.checks)["ok"], json.loads(p.checks))
		self.assertFalse(frappe.db.exists("Purchase Order", {"supplier": ["in", list(self.suppliers)], "owner": a.service_user}))

		# approved, then sent by the paced sender in the supplier's office hours
		for p in sends.values():
			self.assertEqual(self.as_owner(proposals.decide, p.name, "approve"), "Approved", "a send waits for the sender")
		self.assertIn({"kind": "send_due", "dedupe_key": f"send_due:{a.name}"}, purchase.plan(a))
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True), \
				patch("excom.excom_ai.mail.random.uniform", return_value=0):
			out = self.run_now(tasks.add(a.name, "send_due", dedupe_key=f"send_due:{a.name}"))
		self.assertIn("2 sent", out)
		self.assertEqual(send.call_count, 2)
		self.assertEqual({c.kwargs["to"] for c in send.call_args_list}, set(self.suppliers.values()))
		self.assertTrue(all(rfq.name in c.kwargs["subject"] for c in send.call_args_list), "the subject names the RFQ")
		threads = {}
		for s, p in sends.items():
			row = frappe.db.get_value("Excom AI Proposal", p.name, ["status", "thread", "message"], as_dict=True)
			self.assertEqual(row.status, "Sent")
			self.assertEqual(frappe.db.get_value("Excom Thread", row.thread, ["ai_agent", "ai_mode"]), (a.name, "Handle"))
			threads[s] = row.thread
		self.assertEqual(frappe.db.get_value("Request for Quotation Supplier", {"parent": rfq.name, "supplier": self.colours},
			"email_sent"), 1)

		# Colours answers with a PDF: two clear lines, a break above our quantity, and a line nobody asked for
		lines = {i.item_code: i.name for i in rfq.items}
		msg, box = self.supplier_writes(threads[self.colours], "Dear sir, please find our quotation attached.\n\nRegards",
			[{"filename": "quotation.pdf", "mimeType": "application/pdf", "size": 2000, "attachmentId": "att-1"}])
		mailbox = FakeMailbox(box, {"att-1": blank_pdf()})
		extracted = {"is_quotation": True, "currency": "INR", "valid_till": add_days(nowdate(), 30), "payment_terms": "30 days",
			"lead_time_days": 14, "notes": "Freight extra", "lines": [
				{"line_id": lines["QA-PUR-RED"], "supplier_text": "Red pigment", "rate": 120, "min_qty": 0, "uom": "Nos",
					"lead_time_days": 10, "unclear": False, "why": ""},
				{"line_id": lines["QA-PUR-RED"], "supplier_text": "Red pigment", "rate": 110, "min_qty": 1000, "uom": "Nos",
					"lead_time_days": 10, "unclear": False, "why": ""},
				{"line_id": lines["QA-PUR-BLUE"], "supplier_text": "Blue pigment", "rate": 80, "min_qty": 0, "uom": "pcs",
					"lead_time_days": 0, "unclear": False, "why": ""},
				{"line_id": "", "supplier_text": "Mystery shade 7", "rate": 55, "min_qty": 0, "uom": "kg", "lead_time_days": 0,
					"unclear": True, "why": "not one of the items asked for"}]}
		with patch("excom.excom.channels.email.providers.get", return_value=mailbox), \
				patch("ai_interface.services.vision.extract", return_value=PDF_TEXT) as vision:
			out = self.run_now(tasks.add(a.name, "inbound", thread=threads[self.colours], params={"message": msg, "channel": "email"}),
				extracted)
		self.assertIn("2 lines, 1 unclear", out)
		vision.assert_called_once()
		from ai_interface.tests.test_chat import ScriptedAdapter

		seen = json.dumps(ScriptedAdapter.seen[-1][1])
		self.assertIn("Unit price", seen, "the PDF's text reached the model")
		self.assertIn("untrusted", seen, "as fenced data")
		self.assertTrue(frappe.db.exists("File", {"attached_to_name": rfq.name, "file_name": "quotation.pdf"}))
		sq_prop = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "create_supplier_quotation",
			"thread": threads[self.colours]})
		self.assertEqual(sq_prop.status, "Pending", "an unclear line keeps it for a person even though create_record is Auto")
		p = json.loads(sq_prop.payload)
		self.assertEqual({ln["item_code"]: ln["rate"] for ln in p["lines"]}, {"QA-PUR-RED": 120, "QA-PUR-BLUE": 80},
			"the 1000+ break does not apply to 500")
		self.assertEqual([u["supplier_text"] for u in p["unclear"]], ["Mystery shade 7"], "flagged, never guessed")
		self.assertIn("Mystery shade 7", sq_prop.preview_html)
		self.assertIn("worse", sq_prop.preview_html, "Red at 120 against the last PO at 100")
		self.assertIn("better", sq_prop.preview_html, "Blue at 80 against the price list at 90")
		if self.mpc:
			self.assertIn("outside the cost rule", sq_prop.preview_html)
		self.assertEqual(self.as_owner(proposals.decide, sq_prop.name, "approve"), "Done",
			frappe.db.get_value("Excom AI Proposal", sq_prop.name, "error"))
		sq = frappe.get_doc("Supplier Quotation", frappe.db.get_value("Excom AI Proposal", sq_prop.name, "executed_name"))
		self.assertEqual((sq.docstatus, sq.supplier), (0, self.colours))
		self.assertEqual({i.item_code: i.rate for i in sq.items}, {"QA-PUR-RED": 120, "QA-PUR-BLUE": 80})
		self.assertEqual({i.request_for_quotation for i in sq.items}, {rfq.name})
		self.assertEqual({i.request_for_quotation_item for i in sq.items}, set(lines.values()))
		self.assertEqual({i.item_code: i.qty for i in sq.items}, {"QA-PUR-RED": 500, "QA-PUR-BLUE": 300}, "our quantities")

		# Dyes answers in the email body only, every line clear: the draft is made at once (create_record Auto)
		msg, box = self.supplier_writes(threads[self.dyes], "Red pigment Rs 95 per piece, Blue pigment Rs 85. Valid 15 days.")
		with patch("excom.excom.channels.email.providers.get", return_value=FakeMailbox(box)):
			self.run_now(tasks.add(a.name, "inbound", thread=threads[self.dyes], params={"message": msg, "channel": "email"}),
				{"is_quotation": True, "currency": "", "valid_till": "", "payment_terms": "", "lead_time_days": 0, "notes": "",
					"lines": [{"line_id": lines["QA-PUR-RED"], "supplier_text": "Red pigment", "rate": 95, "min_qty": 0,
						"uom": "piece", "lead_time_days": 0, "unclear": False, "why": ""},
						{"line_id": "", "supplier_text": "QA-PUR-BLUE", "rate": 85, "min_qty": 0, "uom": "", "lead_time_days": 0,
							"unclear": False, "why": ""}]})
		auto = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "create_supplier_quotation", "thread": threads[self.dyes]})
		self.assertEqual(auto.status, "Done", auto.error)
		sq2 = frappe.get_doc("Supplier Quotation", auto.executed_name)
		self.assertEqual({i.item_code: i.rate for i in sq2.items}, {"QA-PUR-RED": 95, "QA-PUR-BLUE": 85})
		self.assertEqual(sq2.docstatus, 0)
		self.assertIn("better", auto.preview_html)

		# both answered: no reminder is planned
		for p in sends.values():
			frappe.db.set_value("Excom AI Proposal", p.name, "sent_on", add_days(now_datetime(), -6))
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"])

	def test_a_supplier_who_does_not_answer_gets_one_reminder(self):
		a = self.agent()
		self.rfq_now(a, items=[{"item_code": "QA-PUR-RED", "qty": 200}], suppliers=[self.dyes])
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_rfq"})
		self.as_owner(proposals.decide, p.name, "approve")
		with self.fake_send(), patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		p.reload()
		self.assertEqual(p.status, "Sent")
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"], "not yet")
		# days pass (the conversation is shared with earlier tests' messages to this supplier: they age too)
		frappe.db.sql("""UPDATE `tabExcom Message` SET creation = DATE_SUB(creation, INTERVAL 10 DAY) WHERE thread = %s""",
			p.thread)
		frappe.db.set_value("Excom AI Proposal", p.name, "sent_on", add_days(now_datetime(), -(purchase.FOLLOW_UP_DAYS + 1)))
		specs = [s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"]
		self.assertEqual(specs, [{"kind": "rfq_follow_up", "subject": ("Excom AI Proposal", p.name)}])
		out = self.run_now(tasks.add(a.name, **specs[0]), {"intro": "We wanted to check whether our request reached you.",
			"closing": "A reply at your convenience would help us."})
		self.assertIn("reminder", out)
		r = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_rfq", "subject_doctype": "Excom AI Proposal"})
		payload = json.loads(r.payload)
		self.assertEqual((r.status, r.thread, payload["follow_up"]), ("Pending", p.thread, 1))
		self.assertTrue(payload["subject"].startswith("Re: "))
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"], "only one reminder")
		# the supplier answers before the reminder goes: it is cancelled
		self.as_owner(proposals.decide, r.name, "approve")
		self.supplier_writes(p.thread, "Will send prices tomorrow.")
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(r.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", r.name, "status"), "Cancelled")
		send.assert_not_called()

	def test_the_model_writes_only_words_and_never_numbers_or_commitments(self):
		a = self.agent()
		t = tasks.add(a.name, "draft_rfq", dedupe_key=f"qa-pur-words-{self.n}",
			params={"items": [{"item_code": "QA-PUR-BLUE", "qty": 50}], "suppliers": [self.colours]})
		self.run_now(t, {"intro": "We will place the order for 5000 pieces at your best price.", "closing": "Thanks"})
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_rfq"})
		body, results = json.loads(p.payload)["body"], json.loads(p.checks)
		self.assertNotIn("5000", body)
		self.assertNotIn("place the order", body)
		self.assertIn(purchase.FALLBACK["rfq"][0], body)
		self.assertFalse(results["wording"]["ok"], "the reviewer sees what the model tried")
		self.assertFalse(body.rstrip().endswith("Thanks"), "no sign-off: the mail server signs")
		self.assertEqual(p.status, "Pending")

	def test_outside_office_hours_a_send_waits(self):
		a = self.agent()
		self.rfq_now(a, items=[{"item_code": "QA-PUR-RED", "qty": 10}], suppliers=[self.colours])
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_rfq"})
		self.as_owner(proposals.decide, p.name, "approve")
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=False):
			proposals.execute(p.name)
		row = frappe.db.get_value("Excom AI Proposal", p.name, ["status", "send_at", "hold_reason"], as_dict=True)
		self.assertEqual(row.status, "Approved")
		self.assertTrue(row.send_at)
		self.assertIn("office hours", row.hold_reason)
		send.assert_not_called()

	def test_a_material_request_becomes_an_rfq(self):
		a = self.agent()
		mr = frappe.get_doc({"doctype": "Material Request", "material_request_type": "Purchase", "company": self.company,
			"transaction_date": nowdate(), "schedule_date": add_days(nowdate(), 10),
			"items": [{"item_code": "QA-PUR-RED", "qty": 250, "schedule_date": add_days(nowdate(), 10), "uom": "Nos",
				"conversion_factor": 1}]})
		mr.flags.ignore_permissions = True
		mr.insert(ignore_permissions=True)
		frappe.db.set_value("Material Request", mr.name, {"docstatus": 1, "status": "Pending"}, update_modified=False)
		frappe.db.set_value("Material Request Item", {"parent": mr.name}, "docstatus", 1, update_modified=False)
		specs = [s for s in purchase.plan(a) if s["kind"] == "draft_rfq"]
		self.assertIn({"kind": "draft_rfq", "subject": ("Material Request", mr.name), "params": {"material_request": mr.name}}, specs)
		t = tasks.add(a.name, "draft_rfq", subject=("Material Request", mr.name), params={"material_request": mr.name})
		self.assertEqual(purchase.suggest_suppliers(["QA-PUR-RED"], self.company)[0], self.colours, "Red was last bought there")
		out = self.run_now(t, WORDS)
		self.assertIn("emails proposed", out)
		rfq = frappe.get_doc("Request for Quotation", frappe.db.get_value("Excom AI Proposal",
			{"agent": a.name, "action": "create_rfq"}, "executed_name"))
		self.assertIn(self.colours, [s.supplier for s in rfq.suppliers])
		self.assertEqual([(i.item_code, i.qty, i.material_request) for i in rfq.items], [("QA-PUR-RED", 250, mr.name)])
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "draft_rfq" and s["subject"][1] == mr.name], "asked once")

	# ---- comparison

	def test_comparison_with_price_history(self):
		red = purchase.compare("QA-PUR-RED", 120, self.company)
		self.assertEqual((red["verdict"], red["delta_pct"]), ("worse", 20.0))
		self.assertEqual(red["history"]["last_po"]["name"], self.history_po)
		self.assertEqual(purchase.compare("QA-PUR-RED", 100.5, self.company)["verdict"], "about the same")
		blue = purchase.compare("QA-PUR-BLUE", 80, self.company)
		self.assertEqual(blue["verdict"], "better")
		self.assertIn("price list", blue["note"])
		if self.mpc:
			self.assertIn("outside the cost rule", blue["note"])
			self.assertIn("within the cost rule", purchase.compare("QA-PUR-BLUE", 86, self.company)["note"])
		self.assertEqual(purchase.compare("QA-PUR-NOPE", 5, self.company)["verdict"], "no history")

		# a draft someone entered by hand gets the comparison as a note
		a = self.agent()
		sq = frappe.get_doc({"doctype": "Supplier Quotation", "supplier": self.dyes, "company": self.company,
			"transaction_date": nowdate(), "currency": "INR", "conversion_rate": 1,
			"items": [{"item_code": "QA-PUR-RED", "qty": 10, "rate": 130, "uom": "Nos", "conversion_factor": 1}]})
		sq.insert(ignore_permissions=True)
		self.assertIn({"kind": "compare_quotation", "subject": ("Supplier Quotation", sq.name)}, purchase.plan(a))
		self.run_now(tasks.add(a.name, "compare_quotation", subject=("Supplier Quotation", sq.name)))
		note = frappe.db.get_value("Comment", {"reference_doctype": "Supplier Quotation", "reference_name": sq.name}, "content")
		self.assertIn("worse", note)

	# ---- PO chasers

	def test_a_late_order_is_chased_in_office_hours_and_the_chasing_stops_when_received(self):
		a = self.agent()
		po = self.purchase_order(self.dyes, {"QA-PUR-RED": (100, 99), "QA-PUR-BLUE": (40, 88)}, days_ago=60, schedule_in=-3)
		frappe.db.set_value("Purchase Order Item", {"parent": po, "item_code": "QA-PUR-BLUE"}, "received_qty", 40)
		self.assertIn({"kind": "chase_po", "subject": ("Purchase Order", po)}, purchase.plan(a))
		self.assertNotIn({"kind": "chase_po", "subject": ("Purchase Order", self.history_po)}, purchase.plan(a), "received")
		chase = {"intro": "We hope all is well and wanted to ask about our order below.",
			"closing": "An update on the dispatch would help us plan."}
		out = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po)), chase)
		self.assertIn("chaser for", out)
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "po_chase"})
		body = json.loads(p.payload)["body"]
		self.assertEqual((p.status, p.policy_action, p.to_email), ("Pending", "po_chase", self.suppliers[self.dyes]))
		self.assertIn("QA Pur Red Pigment (QA-PUR-RED): 100 Nos still to come", body)
		self.assertNotIn("QA-PUR-BLUE", body, "only what is still to come")
		self.assertNotIn({"kind": "chase_po", "subject": ("Purchase Order", po)}, purchase.plan(a), "once a week at most")

		self.as_owner(proposals.decide, p.name, "approve")
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=False):
			proposals.execute(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Approved", "deferred to office hours")
		send.assert_not_called()
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent")
		self.assertIn(po, send.call_args.kwargs["subject"])

		# the supplier answers the chaser: a note on the order
		thread = frappe.db.get_value("Excom AI Proposal", p.name, "thread")
		msg, box = self.supplier_writes(thread, f"Dear team, {po} will be dispatched on Friday.")
		with patch("excom.excom.channels.email.providers.get", return_value=FakeMailbox(box)):
			out = self.run_now(tasks.add(a.name, "inbound", thread=thread, params={"message": msg, "channel": "email"}),
				{"summary": "Dispatch on Friday.", "dispatch_date": add_days(nowdate(), 3)})
		self.assertIn("noted", out)
		self.assertIn("Dispatch on Friday", frappe.db.get_value("Comment", {"reference_doctype": "Purchase Order",
			"reference_name": po, "comment_type": "Comment"}, "content"))

		# two more chasers wait; then the goods arrive: neither goes, and no new one is planned
		second = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa2-{po}"), chase)
		third = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa3-{po}"), chase)
		names = frappe.get_all("Excom AI Proposal", {"agent": a.name, "action": "po_chase", "status": "Pending"}, pluck="name")
		self.assertEqual(len(names), 2, (second, third))
		self.as_owner(proposals.decide, names[0], "approve")
		frappe.db.set_value("Purchase Order", po, {"per_received": 100, "status": "Completed"})
		with self.fake_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(names[0])
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", names[0], "status"), "Cancelled")
		send.assert_not_called()
		self.assertNotIn({"kind": "chase_po", "subject": ("Purchase Order", po)}, purchase.plan(a))
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", names[1], "status"), "Cancelled", "the planner stopped it")
		self.assertIn("received", self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po),
			dedupe_key=f"qa4-{po}")))

	# ---- templates (HLD-008): WhatsApp for suppliers with no email, email words from approved templates

	def test_a_whatsapp_only_supplier_is_chased_with_the_approved_template(self):
		a = self.agent(whatsapp=True, policies={"po_chase": "Auto"})
		self.steward_on()
		tpl = self.wa_template(a, "purchase.po_chase")
		po = self.purchase_order(self.inks, {"QA-PUR-RED": (50, 99)}, days_ago=60, schedule_in=-2)
		self.assertIsNone(purchase.supplier_email(self.inks))
		self.assertEqual(purchase.supplier_mobile(self.inks), MOBILE)

		# the supplier never opted in: a template would be refused, so a person phones them
		out = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-wa0-{po}"))
		self.assertIn("a person chases", out)
		todo = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "create_todo", "subject_name": po})
		self.assertIn("opted in", json.loads(todo.payload)["text"])

		identity = self.opt_in()
		out = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-wa1-{po}"))
		self.assertIn("WhatsApp chaser", out)
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "po_chase", "subject_name": po})
		payload = json.loads(p.payload)
		due = formatdate(frappe.db.get_value("Purchase Order", po, "schedule_date"))
		self.assertEqual((payload["channel"], payload["template"], payload["mobile"]), ("whatsapp", tpl, MOBILE))
		self.assertEqual(payload["variables"], [INKS, po, due], "name, order and date; never the items")
		self.assertFalse(any("\n" in v for v in payload["variables"]))
		self.assertNotIn("QA-PUR-RED", payload["body"])
		self.assertEqual((p.channel_account, p.to_email, p.policy_action), (self.wa.name, None, "po_chase"))
		self.assertEqual(p.status, "Pending", "the template's first send waits for a person, though po_chase is Auto")
		self.assertIn("First use", p.preview_html)
		self.assertFalse(self.needs("purchase.po_chase"), "the need is met: nothing new recorded")

		self.as_owner(proposals.decide, p.name, "approve")
		with self.fake_wa_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		row = frappe.db.get_value("Excom AI Proposal", p.name, ["status", "thread", "message", "error"], as_dict=True)
		self.assertEqual(row.status, "Sent", row.error)
		self.assertEqual(send.call_args.args[:2], (row.thread, tpl))
		self.assertEqual(json.loads(send.call_args.kwargs["variables"]), [INKS, po, due])
		self.assertEqual(frappe.db.get_value("Excom Thread", row.thread, ["channel", "account", "omni_identity", "ai_agent"]),
			("whatsapp", self.wa.name, identity, a.name))
		self.assertEqual(frappe.db.get_value("WhatsApp Templates", tpl, "xai_first_use_done"), 1)

		# once a person approved one send, the policy applies (Auto)
		self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-wa2-{po}"))
		later = frappe.get_all("Excom AI Proposal", {"agent": a.name, "action": "po_chase", "name": ["!=", p.name]}, pluck="status")
		self.assertEqual(later, ["Approved"])

	def test_without_a_whatsapp_template_the_need_is_recorded_and_a_person_chases(self):
		a = self.agent(whatsapp=True)
		self.steward_on()
		self.opt_in()
		po = self.purchase_order(self.inks, {"QA-PUR-BLUE": (20, 90)}, days_ago=60, schedule_in=-1)
		for n in range(2):
			out = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-wa-none{n}-{po}"))
			self.assertIn("a person chases", out)
		needs = self.needs("purchase.po_chase")
		self.assertEqual(len(needs), 1, "one need, however often it is met with")
		self.assertEqual((needs[0].status, needs[0].wa_business_id, needs[0].agents), ("Open", WABA, a.name))
		todos = frappe.get_all("Excom AI Proposal", {"agent": a.name, "action": "create_todo", "subject_name": po}, ["payload"])
		self.assertEqual(len(todos), 2)
		self.assertIn("no approved WhatsApp template", json.loads(todos[0].payload)["text"])
		self.assertFalse(frappe.db.exists("Excom AI Proposal", {"agent": a.name, "action": "po_chase"}))
		# an agent with no WhatsApp number of its own records nothing
		b = self.agent()
		self.run_now(tasks.add(b.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-wa-nonum-{po}"))
		self.assertEqual(len(self.needs("purchase.po_chase")), 1)
		self.assertNotIn(b.name, frappe.db.get_value("Excom AI Template Need", needs[0].name, "agents"))

	def test_an_approved_email_template_gives_the_chasers_words_without_the_model(self):
		a = self.agent()
		self.steward_on()
		key = "purchase.po_chase"
		need = frappe.get_doc({"doctype": "Excom AI Template Need", "need_key": key, "channel": "Email", "language": "en",
			"agent": a.name, "agents": a.name, "unique_key": templates.unique_key(key, "Email", "en", a.name, None),
			"status": "Met", "source": "Written", "variable_map": json.dumps(templates.NEEDS[key]["variables"]),
			"subject": "Purchase order {{2}}: dispatch date",
			"body": "Dear {{1}},\n\nWe are writing about our purchase order {{2}}, which was due on {{3}}. These items are "
				"still to come:\n\n{{content}}\n\nCould you let us know when they will be dispatched?"}).insert(ignore_permissions=True)
		po = self.purchase_order(self.dyes, {"QA-PUR-RED": (30, 99)}, days_ago=60, schedule_in=-2)
		with patch("excom.excom_ai.engine.runner.ai", side_effect=AssertionError("the model is not asked")):
			out = self.run_now(tasks.add(a.name, "chase_po", subject=("Purchase Order", po), dedupe_key=f"qa-mailtpl-{po}"))
		self.assertIn("chaser for", out)
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "po_chase", "subject_name": po})
		payload, results = json.loads(p.payload), json.loads(p.checks)
		due = formatdate(frappe.db.get_value("Purchase Order", po, "schedule_date"))
		self.assertEqual(payload["subject"], f"Purchase order {po}: dispatch date")
		self.assertTrue(payload["body"].startswith(f"Dear QA Pur Dyes,\n\nWe are writing about our purchase order {po}, "
			f"which was due on {due}."), payload["body"])
		self.assertIn("QA Pur Red Pigment (QA-PUR-RED): 30 Nos still to come", payload["body"], "the items are written by code")
		self.assertTrue(payload["body"].endswith("Could you let us know when they will be dispatched?"))
		self.assertNotIn("{{", payload["body"])
		self.assertEqual(payload["email_template"], need.name)
		self.assertEqual((p.to_email, p.status), (self.suppliers[self.dyes], "Pending"))
		self.assertTrue(results["ok"], results)
		self.assertIn("approved email template", results["wording"]["why"])

	def test_a_supplier_asked_by_hand_gets_one_whatsapp_rfq_reminder(self):
		a = self.agent(whatsapp=True)
		self.steward_on()
		tpl = self.wa_template(a, "purchase.rfq_reminder")
		self.opt_in()
		out = self.rfq_now(a, items=[{"item_code": "QA-PUR-RED", "qty": 20}], suppliers=[self.inks])
		self.assertIn("a person sends it", out)
		rfq = frappe.db.get_value("Excom AI Proposal", {"agent": a.name, "action": "create_rfq"}, "executed_name")
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"], "not yet")
		frappe.db.set_value("Request for Quotation", rfq, "creation", add_days(now_datetime(), -(purchase.FOLLOW_UP_DAYS + 1)),
			update_modified=False)
		specs = [s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"]
		self.assertEqual(specs, [{"kind": "rfq_follow_up", "subject": ("Request for Quotation", rfq),
			"dedupe_key": f"rfq_follow_up:{rfq}:{self.inks}", "params": {"supplier": self.inks}}])
		with patch("excom.excom_ai.engine.runner.ai", side_effect=AssertionError("the model is not asked")):
			out = self.run_now(tasks.add(a.name, **specs[0]))
		self.assertIn("WhatsApp reminder", out)
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "send_rfq", "subject_name": rfq})
		payload = json.loads(p.payload)
		self.assertEqual((payload["template"], payload["rfq"], payload["follow_up"]), (tpl, rfq, 1))
		self.assertEqual(payload["variables"], [INKS, rfq, formatdate(add_days(getdate(nowdate()), purchase.REPLY_WITHIN_DAYS))])
		self.assertEqual((p.status, p.policy_action, p.channel_account), ("Pending", "rfq", self.wa.name))
		self.as_owner(proposals.decide, p.name, "approve")
		with self.fake_wa_send() as send, patch("excom.excom_ai.sequencer.in_window", return_value=True):
			proposals.execute(p.name)
		self.assertEqual(frappe.db.get_value("Excom AI Proposal", p.name, "status"), "Sent",
			frappe.db.get_value("Excom AI Proposal", p.name, "error"))
		self.assertEqual(send.call_args.args[1], tpl)
		self.assertFalse([s for s in purchase.plan(a) if s["kind"] == "rfq_follow_up"], "only one reminder")

	# ---- supplier discovery

	def test_supplier_discovery_proposes_suppliers_for_a_person(self):
		a = self.agent()
		found = [{"name": "QA Pur Pigment Works", "domain": f"pigmentworks.{DOMAIN}", "website": f"https://www.pigmentworks.{DOMAIN}/",
			"country": "India", "source_url": f"https://www.pigmentworks.{DOMAIN}/"},
			{"name": "QA Pur Colours Again", "domain": f"colours.{DOMAIN}", "website": f"https://colours.{DOMAIN}/", "country": "India"}]

		def use(key, method, cost=None, **kw):
			if method == "search":
				self.assertEqual(kw["query"]["keywords"], [GROUP])
				return found
			if method == "profile":
				return {"title": "Pigment Works", "description": "Manufacturer of pigments and dyes.",
					"emails": [f"sales@pigmentworks.{DOMAIN}"], "language": "en"}
			if method == "find":
				return {"addresses": [{"email": f"ravi.k@pigmentworks.{DOMAIN}", "source": "Pattern"},
					{"email": f"sales@pigmentworks.{DOMAIN}", "source": "Published"}]}
			raise AssertionError(f"{key}.{method}")

		frappe.flags.excom_ai_fake_tools = True
		self.addCleanup(setattr, frappe.flags, "excom_ai_fake_tools", False)
		self.assertTrue([s for s in purchase.plan(a) if s["kind"] == "discover_suppliers"], "a monthly search")
		with patch("excom.excom_ai.tools.use", side_effect=use):
			out = self.run_now(tasks.add(a.name, "discover_suppliers", dedupe_key=f"qa-disc-{self.n}", params={"groups": [GROUP]}))
			self.assertIn("1 new", out)
			works = frappe.get_doc("Excom AI Company", {"domain": f"pigmentworks.{DOMAIN}"})
			self.assertEqual((works.status, works.agent, works.industry), ("New", a.name, GROUP))
			self.assertEqual(frappe.db.get_value("Excom AI Company", {"domain": f"colours.{DOMAIN}"}, "status"), "Excluded",
				"already a supplier")
			t = frappe.db.get_value("Excom AI Task", {"agent": a.name, "kind": "profile_supplier", "subject_name": works.name})
			self.run_now(t)
		p = frappe.get_doc("Excom AI Proposal", {"agent": a.name, "action": "create_supplier"})
		self.assertEqual(p.status, "Pending", "never a real Supplier on Auto, even with create_record on Auto")
		self.assertEqual(json.loads(p.payload)["email"], f"sales@pigmentworks.{DOMAIN}", "published, never guessed")
		self.assertFalse(frappe.db.exists("Supplier", {"supplier_name": "QA Pur Pigment Works"}))
		with self.assertRaises(registry.Cancel):
			purchase.create_supplier(frappe._dict(decided_by="Autopilot", payload=p.payload), a)
		self.assertEqual(self.as_owner(proposals.decide, p.name, "approve"), "Done")
		s = frappe.db.get_value("Excom AI Proposal", p.name, "executed_name")
		self.assertEqual(frappe.db.get_value("Supplier", s, ["supplier_name", "supplier_group"]),
			("QA Pur Pigment Works", purchase._supplier_group_for(GROUP)))
		self.assertEqual(purchase.supplier_email(s), f"sales@pigmentworks.{DOMAIN}")
		self.assertEqual(frappe.db.get_value("Excom AI Company", works.name, "status"), "Promoted")

	# ---- readiness and recipes

	def test_readiness_rules_for_the_purchase_type(self):
		a = self.agent()
		keys = {r[0] for r in purchase.RULES}
		self.assertEqual(set(frappe.get_all("Excom AI Readiness Rule", {"agent_type": "Purchase"}, pluck="rule_key")) & keys, keys)
		self.assertEqual(purchase.rfq_mailbox(a, {})[0], True)
		none = self.agent(channels=False)
		ok, detail = purchase.rfq_mailbox(none, {})
		self.assertFalse(ok)
		self.assertIn("No mailbox", detail)
		for fn in (purchase.supplier_groups, purchase.supplier_contacts, purchase.price_history):
			ok, detail = fn(a, {})
			self.assertIn(ok, (True, False))
			self.assertTrue(detail)
		self.assertTrue(purchase.price_history(a, {})[0], "Standard Buying holds prices here")
		from excom.excom_ai.builder import readiness

		result = readiness.run(none.name)
		self.assertGreaterEqual(result["blockers"], 1)
		rule = frappe.db.get_value("Excom AI Readiness Rule", {"rule_key": "purchase.rfq_mailbox"})
		self.assertEqual(frappe.db.get_value("Excom AI Readiness Gap", {"agent": none.name, "rule": rule}, "status"), "Open")
		readiness.run(a.name)
		self.assertNotEqual(frappe.db.get_value("Excom AI Readiness Gap", {"agent": a.name, "rule": rule}, "status"), "Open")

	def test_knowledge_recipes_for_the_purchase_builder(self):
		from excom.excom_ai.builder import knowledge, recipes

		late = self.purchase_order(self.colours, {"QA-PUR-BLUE": (10, 90)}, days_ago=30, schedule_in=-10)
		keys = recipes.for_pack("purchase")
		self.assertEqual(keys, ["purchase_spend", "purchase_suppliers", "purchase_late_orders", "purchase_prices"])
		ctx = knowledge.context([], "purchase", self.company)
		sections = {k: recipes.run(k, ctx) for k in keys}
		self.assertIn(late, sections["purchase_late_orders"]["data"]["late"])
		self.assertIn("Standard Buying", [r["price_list"] for r in sections["purchase_prices"]["data"]["lists"]])
		for s in sections.values():
			self.assertTrue(s["facts"], s)
		self.assertIsInstance(sections["purchase_spend"]["data"]["groups"], list)
