"""
Core flows that money depends on. Every test builds its own synthetic data (QA-… names) and
rolls back; nothing is sent to WhatsApp or email (the outbound client is not exercised).

    bench --site <site> run-tests --module excom.excom.tests.test_core_flows
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

PHONE = "+919900000777"


def _ref_thread():
	return frappe.get_all("Excom Thread", filters={"channel": "whatsapp"}, fields=["channel", "account_doctype", "account"], limit=1)[0]


def _mk_identity(name="QA Core Person", phone=PHONE):
	return frappe.get_doc({"doctype": "Omni Identity", "display_name": name, "primary_phone": phone}).insert(ignore_permissions=True)


def _mk_thread(oi, assigned=None, key="qa-core-1", **extra):
	ref = _ref_thread()
	return frappe.get_doc({"doctype": "Excom Thread", "omni_identity": oi, "channel": ref.channel, "account_doctype": ref.account_doctype, "account": ref.account, "thread_key": key, "status": "Open", "assigned_to": assigned, "last_message_at": now_datetime(), **extra}).insert(ignore_permissions=True)


def _cleanup():
	"""Endpoints commit, so FrappeTestCase's rollback cannot undo them — delete synthetic rows by name."""
	frappe.set_user("Administrator")
	for oi in frappe.get_all("Omni Identity", {"display_name": ["like", "QA %"]}, pluck="name"):
		frappe.db.delete("Excom Message", {"omni_identity": oi})
		for t in frappe.get_all("Excom Thread", {"omni_identity": oi}, pluck="name"):
			frappe.db.delete("Comment", {"reference_doctype": "Excom Thread", "reference_name": t}); frappe.delete_doc("Excom Thread", t, force=True, ignore_permissions=True)
		frappe.db.delete("Omni Identity Link", {"parent": oi}); frappe.db.delete("Omni Identity Channel", {"parent": oi}); frappe.db.delete("Omni Identity Alias", {"parent": oi})
		frappe.delete_doc("Omni Identity", oi, force=True, ignore_permissions=True)
	qa_leads = set(frappe.get_all("Lead", {"first_name": ["like", "QA %"]}, pluck="name")) | set(frappe.get_all("Lead", {"lead_name": ["like", "QA %"]}, pluck="name")) | set(frappe.get_all("Lead", {"email_id": ["like", "qa.%@example.com"]}, pluck="name"))
	for l in qa_leads:
		frappe.db.delete("ToDo", {"reference_name": l}); frappe.db.delete("Comment", {"reference_doctype": "Lead", "reference_name": l}); frappe.db.delete("Excom Stage Change Log", {"ref_name": l})
		for c in frappe.get_all("Dynamic Link", {"link_name": l, "link_doctype": "Lead", "parenttype": "Contact"}, pluck="parent"):
			frappe.db.delete("Dynamic Link", {"parent": c}); frappe.delete_doc("Contact", c, force=True, ignore_permissions=True)
		frappe.delete_doc("Lead", l, force=True, ignore_permissions=True)
	frappe.db.delete("Excom Intake Log", {"dedupe_key": ["like", "%:QA-%"]})
	for s in frappe.get_all("Excom Intake Source", {"source_name": ["like", "QA %"]}, pluck="name"):
		frappe.delete_doc("Excom Intake Source", s, force=True, ignore_permissions=True)
	for t in frappe.get_all("Excom Team", {"team_name": ["like", "QA %"]}, pluck="name"):
		frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
	for u in frappe.get_all("User", {"name": ["like", "qa.core.%@example.com"]}, pluck="name"):
		frappe.delete_doc("User", u, force=True, ignore_permissions=True)
	frappe.db.commit()


class _Base(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass(); _cleanup()

	def tearDown(self):
		_cleanup(); super().tearDown()

	@classmethod
	def tearDownClass(cls):
		_cleanup(); super().tearDownClass()


def _mk_user(email, enabled=1):
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "enabled": enabled, "send_welcome_email": 0, "roles": [{"role": "Excom User"}]}).insert(ignore_permissions=True)
	return email


class TestOwnership(_Base):
	def setUp(self):
		frappe.set_user("Administrator")
		self.oi = _mk_identity()

	def test_open_does_not_claim(self):
		from excom.excom.api.chat import get_messages
		t = _mk_thread(self.oi.name)
		get_messages(t.name)
		self.assertIsNone(frappe.db.get_value("Excom Thread", t.name, "assigned_to"))

	def test_talk_claims_thread_and_lead(self):
		from excom.excom.api.chat import _claim_on_talk
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA Core Person", "mobile_no": PHONE, "status": "Lead"}).insert(ignore_permissions=True)
		oi = frappe.get_doc("Omni Identity", self.oi.name)
		if not any(l.linked_doctype == "Lead" and l.linked_name == lead.name for l in oi.linked_entities):
			oi.append("linked_entities", {"linked_doctype": "Lead", "linked_name": lead.name}); oi.save(ignore_permissions=True)
		t = _mk_thread(self.oi.name)
		_claim_on_talk(t.name, "Administrator")
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, "assigned_to"), "Administrator")
		self.assertEqual(frappe.db.get_value("Lead", lead.name, "lead_owner"), "Administrator")

	def test_disabled_owner_counts_as_unassigned(self):
		from excom.excom.api.chat import _claim_on_talk, get_threads
		u = _mk_user("qa.core.disabled@example.com", enabled=0)
		t = _mk_thread(self.oi.name, assigned=u)
		row = next(r for r in get_threads(limit=1000) if r.name == t.name)
		self.assertEqual(row.assigned_to_enabled, 0)
		_claim_on_talk(t.name, "Administrator")
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, "assigned_to"), "Administrator")

	def test_reassign_user_work(self):
		from excom.excom.api.admin import reassign_user_work
		u = _mk_user("qa.core.leaver@example.com")
		t = _mk_thread(self.oi.name, assigned=u)
		r = reassign_user_work(u, "Administrator", 0)
		self.assertEqual(r["threads"], 1)
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, "assigned_to"), "Administrator")


class TestRetryWindow(_Base):
	def test_retry_refused_after_six_hours(self):
		from excom.excom.services.delivery_watchdog import retry_failed_message
		oi = _mk_identity("QA Retry Person", "+919900000778")
		t = _mk_thread(oi.name, key="qa-retry-1")
		old = add_to_date(now_datetime(), hours=-7)
		m = frappe.get_doc({"doctype": "Excom Message", "thread": t.name, "omni_identity": oi.name, "direction": "Outbound", "message_type": "Text", "channel": t.channel, "account_doctype": t.account_doctype, "account": t.account, "content_text": "hi", "delivery_status": "Failed", "provider_timestamp": old}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			retry_failed_message(m.name)
		self.assertEqual(frappe.db.get_value("Excom Message", m.name, "delivery_status"), "Failed")


class TestClosure(_Base):
	def test_close_lost_marks_lead_and_logs(self):
		from excom.excom.api.record import close_conversation, reopen_conversation, get_activity
		oi = _mk_identity("QA Close Person", "+919900000779")
		lead = frappe.get_doc({"doctype": "Lead", "first_name": "QA Close Person", "mobile_no": "+919900000779", "status": "Lead"}).insert(ignore_permissions=True)
		doc = frappe.get_doc("Omni Identity", oi.name)
		if not any(l.linked_doctype == "Lead" and l.linked_name == lead.name for l in doc.linked_entities):
			doc.append("linked_entities", {"linked_doctype": "Lead", "linked_name": lead.name}); doc.save(ignore_permissions=True)
		t = _mk_thread(oi.name, key="qa-close-1")
		r = close_conversation(oi.name, "Lost", "Price too high", "note", 1)
		self.assertEqual(r["crm"]["status"], "Do Not Contact")
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, ["status", "closure_outcome"]), ("Closed", "Lost"))
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Lead", "reference_name": lead.name, "comment_type": "Comment"}))
		kinds = {a["kind"] for a in get_activity("Lead", lead.name, json.dumps([t.name]))}
		self.assertIn("closure", kinds); self.assertIn("comment", kinds)
		reopen_conversation(oi.name)
		self.assertEqual(frappe.db.get_value("Lead", lead.name, "status"), "Open")
		self.assertEqual(frappe.db.get_value("Excom Thread", t.name, "status"), "Open")


class TestAccess(_Base):
	def test_admin_endpoints_need_manager(self):
		from excom.excom.api.admin import list_users
		u = _mk_user("qa.core.agent@example.com")
		frappe.set_user(u)
		try:
			with self.assertRaises(frappe.PermissionError):
				list_users()
		finally:
			frappe.set_user("Administrator")

	def test_thread_access_check_blocks_other_team(self):
		from excom.excom.api.chat import _user_can_access_thread
		oi = _mk_identity("QA Access Person", "+919900000780")
		team = frappe.get_doc({"doctype": "Excom Team", "team_name": "QA Core Team"}).insert(ignore_permissions=True)
		t = _mk_thread(oi.name, key="qa-access-1", assigned_team=team.name)
		u = _mk_user("qa.core.outsider@example.com")
		frappe.set_user(u)
		try:
			self.assertFalse(_user_can_access_thread(t.name))
		finally:
			frappe.set_user("Administrator")


class TestRateLimit(_Base):
	def test_user_rate_limit_is_per_user(self):
		from excom.excom.utils.ratelimit import user_rate_limit

		@user_rate_limit(limit=2, seconds=60)
		def f():
			return 1

		had_request = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(path="/api/method/qa_rl_probe")  # the limiter only runs inside a web request
		frappe.local.form_dict = frappe._dict(cmd="qa_rl_probe")
		frappe.cache.delete_keys("rl:user:qa_rl_probe")
		try:
			f(); f()
			with self.assertRaises(frappe.RateLimitExceededError):
				f()
		finally:
			frappe.cache.delete_keys("rl:user:qa_rl_probe")
			frappe.local.request = had_request
			frappe.local.form_dict = frappe._dict()


class TestIntakePayloads(_Base):
	"""Vendor payload shapes from the public docs, through map_payload + the sync pipeline."""

	def _source(self, stype, extra=None):
		acc = frappe.get_all("Excom Channel Account", filters={"channel": "whatsapp"}, pluck="name", limit=1)
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		return frappe.get_doc({"doctype": "Excom Intake Source", "source_name": f"QA {stype}", "source_type": stype, "enabled": 1, "company": company, "channel_account": acc[0] if acc else None, "mode": "Pull", "sla_first_response": 3600, **(extra or {})}).insert(ignore_permissions=True)

	def test_indiamart_row_maps_and_creates_lead(self):
		from excom.excom.services.intake import map_payload, ingest
		src = self._source("IndiaMART")
		row = {"UNIQUE_QUERY_ID": "QA-IM-1", "QUERY_TYPE": "B", "QUERY_TIME": "2026-09-03 10:00:00", "SENDER_NAME": "QA Ravi Kumar", "SENDER_MOBILE": "+91-9900000781", "SENDER_EMAIL": "qa.ravi@example.com", "SENDER_COMPANY": "QA Traders", "SENDER_CITY": "Agra", "SENDER_STATE": "Uttar Pradesh", "SENDER_COUNTRY_ISO": "IN", "QUERY_PRODUCT_NAME": "Ice cream cones", "QUERY_MESSAGE": "Need 500 boxes"}
		m = map_payload(src, row)
		self.assertEqual(m.get("phone"), "+919900000781")
		self.assertTrue(m.get("name") or m.get("first_name"))
		r = ingest(src, "indiamart:QA-IM-1", row, sync=True)
		log = frappe.get_doc("Excom Intake Log", r["log"])
		self.assertEqual(log.status, "Processed", log.get("error") or "")
		self.assertTrue(log.lead)
		r2 = ingest(src, "indiamart:QA-IM-1", row, sync=True)
		self.assertTrue(r2["duplicate"])

	def test_tradeindia_row_maps(self):
		from excom.excom.services.intake import map_payload
		src = self._source("TradeIndia")
		row = {"rfi_id": "QA-TI-1", "sender_name": "QA Meena", "sender_mobile": "9900000782", "sender_email": "qa.meena@example.com", "sender_co": "QA Foods", "sender_city": "Delhi", "product_name": "Kulfi sticks", "message": "Price list please", "generated_date": "2026-09-03", "generated_time": "10:00"}
		m = map_payload(src, row)
		self.assertEqual(m.get("phone"), "+919900000782")

	def test_meta_field_data_maps(self):
		from excom.excom.services.intake import map_payload
		src = self._source("Meta Lead Ads", {"form_id": "123", "page_id": "456"})
		row = {"id": "QA-FB-1", "created_time": "2026-09-03T10:00:00+0000", "field_data": [{"name": "full_name", "values": ["QA Arjun"]}, {"name": "phone_number", "values": ["+919900000783"]}, {"name": "email", "values": ["QA.Arjun@Example.com"]}], "campaign_name": "Diwali"}
		m = map_payload(src, row)
		self.assertEqual(m.get("phone"), "+919900000783")
		self.assertEqual(m.get("email"), "qa.arjun@example.com")


class TestManifest(_Base):
	def test_manifest_matches_installed_version(self):
		from excom.excom.services.crm_manifest import check
		r = check()
		self.assertTrue(r["ok"], "\n".join(r["problems"]))


class TestWebhookHelpers(_Base):
	def test_dedupe_key_prefers_ids_then_hash(self):
		from excom.excom.api.intake import _webhook_dedupe_key
		self.assertEqual(_webhook_dedupe_key("S", {"submission_id": "abc"}, ""), "web:S:abc")
		self.assertEqual(_webhook_dedupe_key("S", {"entry_id": "77"}, ""), "web:S:77")
		a = _webhook_dedupe_key("S", {"name": "x", "phone": "1"}, "")
		b = _webhook_dedupe_key("S", {"phone": "1", "name": "x"}, "")
		self.assertEqual(a, b); self.assertTrue(a.startswith("web:S:sha1:"))

	def test_embed_snippets(self):
		from excom.excom.api.admin import get_embed, regenerate_source_token
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		src = frappe.get_doc({"doctype": "Excom Intake Source", "source_name": "QA Embed Site", "source_type": "Website", "enabled": 1, "company": company, "mode": "Push", "sla_first_response": 3600}).insert(ignore_permissions=True)
		e = get_embed("Excom Intake Source", src.name)
		self.assertEqual(e["kind"], "website"); self.assertFalse(e["has_token"]); self.assertIn("submit_enquiry", e["form_endpoint"])
		regenerate_source_token(src.name)
		e = get_embed("Excom Intake Source", src.name)
		self.assertTrue(e["has_token"]); self.assertIn(e["token"], e["webhook_endpoint"]); self.assertIn(e["token"], e["html"])
		wc = frappe.get_all("Excom Channel Account", filters={"channel": "webchat"}, pluck="name", limit=1)
		if wc:
			w = get_embed("Excom Channel Account", wc[0])
			self.assertEqual(w["kind"], "webchat"); self.assertIn("excom-chat.js", w["script"]); self.assertIn(f'data-account="{wc[0]}"', w["script"])


class TestLeadVisibility(_Base):
	"""Source → team managers until assigned; members only their own; Excom Managers everything."""

	def test_visibility_filters(self):
		from excom.excom.api.crm import lead_visibility, create_lead_manual
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		mgr = _mk_user("qa.core.mgr@example.com"); mem = _mk_user("qa.core.mem@example.com")
		team = frappe.get_doc({"doctype": "Excom Team", "team_name": "QA Vis Team", "members": [{"user": mgr, "role": "Manager"}, {"user": mem, "role": "Member"}]}).insert(ignore_permissions=True)
		other = frappe.get_doc({"doctype": "Excom Team", "team_name": "QA Vis Other"}).insert(ignore_permissions=True)
		src_team = frappe.get_doc({"doctype": "Excom Intake Source", "source_name": "QA Vis Source", "source_type": "Website", "enabled": 1, "company": company, "mode": "Push", "sla_first_response": 3600, "allowed_teams": [{"team": team.name}]}).insert(ignore_permissions=True)
		src_other = frappe.get_doc({"doctype": "Excom Intake Source", "source_name": "QA Vis Foreign", "source_type": "Website", "enabled": 1, "company": company, "mode": "Push", "sla_first_response": 3600, "allowed_teams": [{"team": other.name}]}).insert(ignore_permissions=True)
		frappe.db.commit()
		# Excom Manager / System Manager → no filter
		self.assertIsNone(lead_visibility("Administrator"))
		# member → only own
		self.assertEqual(lead_visibility(mem), [["lead_owner", "=", mem]])
		# team manager → own + no-source + sources for their team (not the foreign one)
		ors = lead_visibility(mgr)
		self.assertIn(["lead_owner", "=", mgr], ors); self.assertIn(["intake_source", "is", "not set"], ors)
		src_list = next(o for o in ors if o[0] == "intake_source" and o[1] == "in")[2]
		self.assertIn(src_team.name, src_list); self.assertNotIn(src_other.name, src_list)
		# manual lead creation goes through the gateway and links an identity
		r = create_lead_manual("QA Vis Person", phone="9900000798", company_name="QA Vis Co", customer_type="Distributor", intake_source=src_team.name)
		self.assertTrue(r["created"]); self.assertEqual(r["ref"]["doctype"], "Lead")
		self.assertEqual(frappe.db.get_value("Lead", r["ref"]["name"], ["intake_source", "customer_type", "mobile_no"]), (src_team.name, "Distributor", "+919900000798"))
		self.assertTrue(frappe.db.exists("Omni Identity Link", {"parent": r["identity"], "linked_doctype": "Lead", "linked_name": r["ref"]["name"]}))
		r2 = create_lead_manual("QA Vis Person", phone="9900000798")
		self.assertFalse(r2["created"]); self.assertEqual(r2["ref"]["name"], r["ref"]["name"])
		for t in (team.name, other.name): frappe.delete_doc("Excom Team", t, force=True, ignore_permissions=True)
