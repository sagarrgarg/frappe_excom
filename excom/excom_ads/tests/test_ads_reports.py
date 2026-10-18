"""Excom Ads reports: figures equal the dashboard's, every recipient gets only their own scope,
stale / disconnected sources are stated, schedules are idempotent per period, failures are logged
and alerted, endpoints check scope and rights.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_reports'}"
"""

import csv
import io
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from excom.excom_ads import dashboard, permissions, report_schedule, reports
from excom.excom_ads.settings import DEFAULTS
from excom.excom_ads.tests.test_ads_permissions import COMPANY_B, _ensure_company_b

P = "QA Reports"
U_A = "qa.reports.a@example.com"
U_B = "qa.reports.b@example.com"
U_FIN = "qa.reports.fin@example.com"
U_BRAND = "qa.reports.brand@example.com"
U_NONE = "qa.reports.none@example.com"
USERS = [U_A, U_B, U_FIN, U_BRAND, U_NONE]
EXTERNAL = "qa.reports.outsider@example.org"
TODAY = "2026-10-10"  # Last 7 days = 2026-10-03 … 2026-10-09
DELIVERY = "Ads Report Delivery"


def _csv_rows(r) -> list[list[str]]:
	return list(csv.reader(io.StringIO(r.csv.decode("utf-8-sig"))))


def _row(rows, first: str, currency: str | None = None) -> list[str]:
	for row in rows:
		if row and row[0] == first and (currency is None or (len(row) > 1 and row[1] == currency)):
			return row
	raise AssertionError(f"no row {first!r}")


class TestAdsReports(FrappeTestCase):
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
		ins({"doctype": "Ads Brand", "brand_name": f"{P} Brand A", "workspace": f"{P} WS A"})
		now = now_datetime()
		cls.acc_a = ins({"doctype": "Ads Account", "account_name": f"{P} Acc A", "provider": "Google", "external_id": "555-000-0001", "workspace": f"{P} WS A", "currency": cls.cur, "last_metrics_sync": now, "timezone_name": "Asia/Kolkata"}).name
		cls.acc_stale = ins({"doctype": "Ads Account", "account_name": f"{P} Acc Stale", "provider": "Google", "external_id": "555-000-0002", "workspace": f"{P} WS A", "currency": cls.cur, "last_metrics_sync": add_to_date(now, hours=-30)}).name
		cls.acc_b = ins({"doctype": "Ads Account", "account_name": f"{P} Acc B", "provider": "Meta", "external_id": "act_5550003", "workspace": f"{P} WS B", "currency": "INR", "last_metrics_sync": now}).name
		frappe.db.set_value("Ads Account", cls.acc_stale, "error_class", "Token")
		sa = {"account": cls.acc_a, "workspace": f"{P} WS A", "company": cls.company, "provider": "Google"}
		cls.camp_a = ins({"doctype": "Ads Campaign", **sa, "campaign_name": "=HYPERLINK(\"http://x\")", "external_id": "qrc1", "status": "Active", "brand": f"{P} Brand A"}).name
		sb = {"account": cls.acc_b, "workspace": f"{P} WS B", "company": COMPANY_B, "provider": "Meta"}
		cls.camp_b = ins({"doctype": "Ads Campaign", **sb, "campaign_name": f"{P} Camp B secret", "external_id": "qrc2", "status": "Active"}).name

		def snap(scope, level, ext, date, spend, clicks, leads, campaign=None, brand=None, cur=None):
			cur = cur or cls.cur
			ins({"doctype": "Ads Metric Snapshot", **scope, "level": level, "entity_external_id": ext, "date": date, "campaign": campaign, "brand": brand, "currency": cur, "company_currency": cur, "exchange_rate": 1, "spend": spend, "spend_company_currency": spend, "impressions": clicks * 50, "clicks": clicks, "leads": leads})

		snap(sa, "Account", "555-000-0001", "2026-10-04", 1000, 40, 4)
		snap(sa, "Account", "555-000-0001", "2026-10-08", 500, 10, 1)
		snap(sa, "Account", "555-000-0001", "2026-09-30", 300, 6, 0)  # previous period
		snap(sa, "Campaign", "qrc1", "2026-10-04", 1000, 40, 4, campaign=cls.camp_a, brand=f"{P} Brand A")
		snap(sa, "Campaign", "qrc1", "2026-10-08", 500, 10, 1, campaign=cls.camp_a, brand=f"{P} Brand A")
		snap(sb, "Account", "act_5550003", "2026-10-05", 7777, 70, 7, cur="INR")
		snap(sb, "Campaign", "qrc2", "2026-10-05", 7777, 70, 7, campaign=cls.camp_b, cur="INR")

		for u in USERS:
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "user_type": "System User", "mobile_no": "+919900011122" if u == U_A else None, "roles": [{"role": "Excom Marketing"}]})
		for u, dt, v in ((U_A, "Company", cls.company), (U_B, "Company", COMPANY_B), (U_FIN, "Company", cls.company), (U_BRAND, "Ads Brand", f"{P} Brand A")):
			ins({"doctype": "User Permission", "user": u, "allow": dt, "for_value": v, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_FIN, "view_finance": 1, "export_data": 1})
		cls.t_a = cls._template(f"{P} Weekly A", cls.company, [("User", U_A), ("User", U_FIN), ("User", U_BRAND)], sec_billing=1, sec_brand=1, sec_budgets=1)
		cls.t_b = cls._template(f"{P} Weekly B", COMPANY_B, [("User", U_A), ("User", U_B)])
		frappe.db.commit()

	@classmethod
	def _template(cls, name, company, recipients, **kw):
		doc = frappe.get_doc({
			"doctype": "Ads Report Template", "template_name": name, "company": company, "date_preset": "Last 7 days", "schedule_enabled": 1, "frequency": "Weekly", "weekday": "Saturday", "send_time": "09:00:00",
			"time_zone": "Asia/Kolkata", "via_email": 1, "recipients": [{"recipient_type": t, "user": v if t == "User" else None, "email": v if t == "Email" else None} for t, v in recipients], **kw,
		})
		doc.insert(ignore_permissions=True)
		return doc.name

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		templates = frappe.get_all("Ads Report Template", {"template_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		dels = frappe.get_all(DELIVERY, {"template": ["in", templates]}, pluck="name") or ["-"]
		frappe.db.delete("Email Queue", {"reference_doctype": DELIVERY, "reference_name": ["in", dels]})
		frappe.db.delete(DELIVERY, {"template": ["in", templates]})
		frappe.db.delete("Ads Alert", {"reference_doctype": "Ads Report Template", "reference_name": ["in", templates]})
		frappe.db.delete("Ads Report Recipient", {"parent": ["in", templates]})
		frappe.db.delete("Ads Report Template", {"name": ["in", templates]})
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		for dt in ("Ads Metric Snapshot", "Ads Campaign", "Ads Alert", "Ads Sync Job", "Ads Billing Snapshot"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Brand", {"brand_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		# A bench without built assets (no sites/assets/assets.json, e.g. the v15 test bench) cannot
		# inline Frappe's email CSS, so every sendmail fails there. Not what these tests are about.
		if not frappe.utils.get_assets_json():
			p = patch("frappe.utils.get_assets_json", return_value={})
			p.start()
			self.addCleanup(p.stop)

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		templates = frappe.get_all("Ads Report Template", {"template_name": ["like", f"{P}%"]}, pluck="name")
		dels = frappe.get_all(DELIVERY, {"template": ["in", templates or ["-"]]}, pluck="name") or ["-"]
		frappe.db.delete("Email Queue", {"reference_doctype": DELIVERY, "reference_name": ["in", dels]})
		frappe.db.delete(DELIVERY, {"template": ["in", templates or ["-"]]})
		frappe.db.delete("Ads Alert", {"reference_doctype": "Ads Report Template"})
		frappe.db.sql("update `tabAds Report Template` set last_period_key = null where template_name like %s", (f"{P}%",))
		frappe.db.commit()

	def _render(self, template, user="Administrator", external=False):
		t = frappe.get_doc("Ads Report Template", template)
		(a, b) = reports.period(t.date_preset, TODAY)
		with reports.as_user(user):
			return reports.render(t, a, b, viewer=user, external=external, today=TODAY)

	def _emails(self, template) -> dict[str, str]:
		out = {}
		for d in frappe.get_all(DELIVERY, {"template": template, "status": "Sent", "channel": "Email"}, ["name", "recipient_email"]):
			msg = frappe.db.get_value("Email Queue", {"reference_doctype": DELIVERY, "reference_name": d.name}, "message")
			self.assertTrue(msg, f"an Email Queue row for {d.recipient_email}")
			out[d.recipient_email] = msg
		return out

	# ─── one source of truth ──────────────────────────────────────────────────

	def test_figures_match_the_dashboard(self):
		self.assertEqual(reports.period("Last 7 days", TODAY), (frappe.utils.getdate("2026-10-03"), frappe.utils.getdate("2026-10-09")))
		self.assertEqual(reports.period("Month to date", "2026-11-01")[0], frappe.utils.getdate("2026-10-01"), "MTD on the 1st reports the month that just ended")
		self.assertEqual(reports.period("Last month", TODAY), (frappe.utils.getdate("2026-09-01"), frappe.utils.getdate("2026-09-30")))
		r = self._render(self.t_a)
		ov = dashboard.overview("2026-10-03", "2026-10-09", company=self.company)
		tot = next(t for t in ov["totals"] if t["currency"] == self.cur)
		rows = _csv_rows(r)
		spend = _row(rows, "Spend", self.cur)
		self.assertEqual(float(spend[2]), tot["spend"], "report spend = dashboard spend")
		self.assertEqual(float(_row(rows, "Clicks", self.cur)[2]), tot["clicks"])
		self.assertAlmostEqual(float(_row(rows, "CPL (platform)", self.cur)[2]), tot["cpl"], places=3)
		self.assertEqual(float(spend[3]), ov["previous"]["totals"][0]["spend"], "previous period from the same function")
		self.assertIn("2026-10-04", {row[0] for row in rows if row}, "daily trend rows")
		# A campaign named like a formula is plain text in the CSV.
		self.assertTrue(any(row and row[0].startswith("'=HYPERLINK") for row in rows), "formula-looking text is neutralised in CSV")
		self.assertNotIn(f"{P} Camp B secret", r.html)
		self.assertTrue(r.xlsx[:2] == b"PK", "xlsx is a zip")
		self.assertIn("<table", r.html)
		self.assertNotIn("<script", r.html.lower())

	def test_qa_fix_verified_revenue_renders_for_finance_and_never_for_external(self):
		def fake_compute(filters, a, b, spend=None, **kw):
			row = {"currency": self.cur, "leads": 3.0, "qualified": 1.0, "opportunities": 1.0, "orders": 2.0, "order_value": 14000.0, "invoices": 2.0, "revenue": 12345.0, "gross_revenue": 13000.0, "returns": -655.0, "platform_spend": 1500.0, "verified_cpl": 500.0, "mer": 8.23}
			return {"source": "Verified", "totals": [row], "by_campaign": [dict(row, campaign=self.camp_a)], "available": {"leads": True, "opportunities": True, "orders": True, "revenue": True}}

		t = frappe.get_doc("Ads Report Template", self.t_a)
		(a, b) = reports.period(t.date_preset, TODAY)
		with patch("excom.excom_ads.verified.compute", side_effect=fake_compute):
			with reports.as_user(U_FIN):
				inside = reports.render(t, a, b, viewer=U_FIN, today=TODAY)
				outside = reports.render(t, a, b, viewer=EXTERNAL, external=True, today=TODAY)
				data = reports.collect(t, a, b, external=True, today=TODAY)
		row = _row(_csv_rows(inside), "Net revenue (verified)", self.cur)
		self.assertEqual(float(row[2]), 12345.0, "verified net revenue (verified.compute's `revenue`) is in the KPI summary")
		self.assertIn("Net revenue (verified)", inside.html)
		self.assertIn("Orders (verified)", outside.html, "verified counts still go out")
		self.assertNotIn("Net revenue (verified)", outside.html)
		self.assertFalse(any(r and r[0] == "Net revenue (verified)" for r in _csv_rows(outside)), "no verified revenue for an external address")
		self.assertNotIn("12,345", outside.html)
		self.assertNotIn("12345", outside.csv.decode("utf-8-sig"))
		ver = data["overview"]["verified"]
		for block in ("totals", "by_campaign"):
			for k in ("revenue", "gross_revenue", "returns", "order_value", "mer"):
				self.assertIsNone(ver[block][0][k], f"{block}.{k} is stripped for an external rendering")
		self.assertIsNone(data["overview"]["billing"])

	def test_stale_and_disconnected_sources_are_stated(self):
		r = self._render(self.t_a)
		self.assertFalse(r.disclosure["ok"])
		texts = " ".join(t for _l, t in r.disclosure["lines"])
		self.assertIn(f"Disconnected or failing: {P} Acc Stale", texts)
		self.assertIn(f"Stale: {P} Acc Stale", texts)
		self.assertIn("Some figures are stale, partial or disconnected", r.html)
		self.assertIn("(some data stale or partial)", r.subject)
		# A clean scope says so plainly too.
		clean = frappe.get_doc("Ads Report Template", self.t_b)
		with reports.as_user(U_B):
			rb = reports.render(clean, "2026-10-03", "2026-10-09", viewer=U_B, today="2026-12-31")
		self.assertTrue(rb.disclosure["ok"], rb.disclosure)
		self.assertIn("All ad sources are fresh", rb.html)

	# ─── scope ────────────────────────────────────────────────────────────────

	def test_recipient_never_receives_another_companys_data(self):
		out = report_schedule.run(self.t_b, "W2026-41", today=TODAY)
		self.assertEqual(out["sent"], 1)
		self.assertEqual(out["skipped"], 1)
		skipped = frappe.get_all(DELIVERY, {"template": self.t_b, "recipient_user": U_A}, ["status", "error"])
		self.assertEqual(skipped[0].status, "Skipped")
		self.assertIn("outside", skipped[0].error)
		mails = self._emails(self.t_b)
		self.assertEqual(list(mails), [U_B])
		self.assertIn(f"{P} Acc B", mails[U_B])
		self.assertNotIn(f"{P} Acc A", mails[U_B])
		report_schedule.run(self.t_a, "W2026-41", today=TODAY)
		mails = self._emails(self.t_a)
		self.assertIn(U_A, mails)
		for addr, msg in mails.items():
			self.assertNotIn(f"{P} Acc B", msg, f"{addr} got company B data")
			self.assertNotIn("Camp B secret", msg)

	def test_each_recipient_rendered_in_their_own_rights(self):
		report_schedule.run(self.t_a, "W2026-41", today=TODAY)
		rows = {d.recipient_user: d for d in frappe.get_all(DELIVERY, {"template": self.t_a}, ["recipient_user", "status", "error", "rendered_as"])}
		self.assertEqual(rows[U_A].rendered_as, U_A)
		self.assertEqual(rows[U_BRAND].status, "Skipped", "a brand-scoped user does not cover a company-wide report")
		mails = self._emails(self.t_a)
		self.assertIn("Amount spent (reported)", mails[U_FIN], "finance recipient gets billing")
		self.assertNotIn("Amount spent (reported)", mails[U_A], "no view_finance, no billing")

	def test_external_address_needs_owner_export_right_and_never_gets_billing(self):
		t = frappe.get_doc("Ads Report Template", self.t_a)
		frappe.set_user(U_A)
		permissions.clear_cache()
		t.append("recipients", {"recipient_type": "Email", "email": EXTERNAL})
		self.assertRaises(frappe.PermissionError, t.save)
		frappe.set_user("Administrator")
		permissions.clear_cache()
		t.reload()
		t.append("recipients", {"recipient_type": "Email", "email": EXTERNAL})
		t.save(ignore_permissions=True)
		try:
			frappe.db.set_value("Ads Report Template", t.name, "owner", U_A)
			report_schedule.run(t.name, "W2026-41", today=TODAY)
			ext = frappe.get_all(DELIVERY, {"template": t.name, "recipient_email": EXTERNAL}, ["status", "error"])
			self.assertEqual(ext[0].status, "Skipped")
			self.assertIn("export data", ext[0].error)
			frappe.db.set_value("Ads Report Template", t.name, "owner", U_FIN)
			report_schedule.run(t.name, "W2026-42", today=TODAY)
			row = frappe.get_all(DELIVERY, {"template": t.name, "recipient_email": EXTERNAL, "period_key": "W2026-42"}, ["name", "status", "rendered_as"])[0]
			self.assertEqual((row.status, row.rendered_as), ("Sent", U_FIN))
			msg = frappe.db.get_value("Email Queue", {"reference_doctype": DELIVERY, "reference_name": row.name}, "message")
			self.assertNotIn("Amount spent (reported)", msg, "billing never leaves to an external address")
			self.assertIn(f"{P} Acc A", msg)
		finally:
			frappe.db.set_value("Ads Report Template", t.name, "owner", "Administrator")
			t.reload()
			t.recipients = [r for r in t.recipients if r.recipient_type == "User"]
			t.save(ignore_permissions=True)
			frappe.db.commit()

	# ─── schedule ─────────────────────────────────────────────────────────────

	def test_due_periods_and_grace(self):
		t = frappe._dict(frequency="Weekly", weekday="Saturday", send_time="09:00:00", day_of_month=1)
		self.assertIsNone(report_schedule.due_period(t, datetime(2026, 10, 10, 8, 59)), "not before the send time")
		self.assertEqual(report_schedule.due_period(t, datetime(2026, 10, 10, 9, 0))[0], "W2026-41")
		self.assertEqual(report_schedule.due_period(t, datetime(2026, 10, 11, 20, 0))[0], "W2026-41", "inside the grace window")
		self.assertIsNone(report_schedule.due_period(t, datetime(2026, 10, 13, 9, 0)), "past the grace window: skipped, not sent late")
		t.frequency = "Daily"
		self.assertEqual(report_schedule.due_period(t, datetime(2026, 10, 10, 10, 0))[0], "D2026-10-10")
		t.update(frequency="Monthly", day_of_month=5)
		self.assertEqual(report_schedule.due_period(t, datetime(2026, 10, 6, 9, 30))[0], "M2026-10")
		self.assertIsNone(report_schedule.due_period(t, datetime(2026, 10, 4, 9, 30)))
		midnight = frappe._dict(frequency="Daily", send_time=timedelta(0))
		self.assertEqual(report_schedule.due_period(midnight, datetime(2026, 10, 10, 0, 5))[0], "D2026-10-10", "00:00 is a send time, not 'unset'")

	def test_schedule_is_idempotent_per_period(self):
		first = report_schedule.run(self.t_a, "W2026-41", today=TODAY)
		self.assertEqual(first["sent"], 2)
		queued = frappe.db.count("Email Queue", {"reference_doctype": DELIVERY, "reference_name": ["in", frappe.get_all(DELIVERY, {"template": self.t_a}, pluck="name")]})
		again = report_schedule.run(self.t_a, "W2026-41", today=TODAY)
		self.assertEqual((again["sent"], again["already"]), (0, 3), "a retry of the same period sends nothing")
		self.assertEqual(frappe.db.count("Email Queue", {"reference_doctype": DELIVERY, "reference_name": ["in", frappe.get_all(DELIVERY, {"template": self.t_a}, pluck="name")]}), queued)
		self.assertEqual(frappe.db.get_value("Ads Report Template", self.t_a, "last_period_key"), "W2026-41")
		# The scheduler does not queue a period that is done.
		with patch.object(reports, "local_now", return_value=datetime(2026, 10, 10, 9, 20)), patch("frappe.enqueue") as enq:
			report_schedule.enqueue_due()
		names = [c.kwargs.get("template") for c in enq.call_args_list]
		self.assertNotIn(self.t_a, names)
		self.assertIn(self.t_b, names, "a due template that has not run is queued")
		self.assertTrue(all(c.kwargs.get("deduplicate") and c.kwargs.get("job_id") for c in enq.call_args_list))

	def test_failure_is_logged_alerted_and_retried_once_fixed(self):
		with patch("frappe.sendmail", side_effect=Exception("SMTP queue unavailable")):
			out = report_schedule.run(self.t_b, "W2026-41", today=TODAY)
		self.assertEqual(out["failed"], 1)
		row = frappe.get_all(DELIVERY, {"template": self.t_b, "recipient_user": U_B}, ["status", "error", "attempts"])[0]
		self.assertEqual((row.status, row.attempts), ("Failed", 1))
		self.assertIn("SMTP queue unavailable", row.error)
		alert = frappe.get_all("Ads Alert", {"dedupe_key": report_schedule.failure_key(self.t_b), "status": "Open"}, ["alert_type", "company"])
		self.assertEqual(alert[0].alert_type, "Report Delivery Failed")
		self.assertEqual(alert[0].company, COMPANY_B, "the alert carries the report's scope")
		self.assertFalse(frappe.db.get_value("Ads Report Template", self.t_b, "last_period_key"), "a failed period stays open for a retry")
		out = report_schedule.run(self.t_b, "W2026-41", today=TODAY)
		self.assertEqual(out["sent"], 1)
		row = frappe.get_all(DELIVERY, {"template": self.t_b, "recipient_user": U_B}, ["status", "attempts"])[0]
		self.assertEqual((row.status, row.attempts), ("Sent", 2))
		self.assertFalse(frappe.db.exists("Ads Alert", {"dedupe_key": report_schedule.failure_key(self.t_b), "status": "Open"}), "resolved once delivered")

	def test_failures_stop_after_max_attempts(self):
		with patch("frappe.sendmail", side_effect=Exception("down")):
			for _i in range(report_schedule.MAX_ATTEMPTS + 1):
				report_schedule.run(self.t_b, "W2026-41", today=TODAY)
		row = frappe.get_all(DELIVERY, {"template": self.t_b, "recipient_user": U_B}, ["status", "attempts"])[0]
		self.assertEqual((row.status, row.attempts), ("Failed", report_schedule.MAX_ATTEMPTS))
		self.assertEqual(frappe.db.get_value("Ads Report Template", self.t_b, "last_period_key"), "W2026-41", "given up: the period is closed")

	def test_whatsapp_goes_through_excom_with_a_private_file(self):
		settings = frappe._dict({**DEFAULTS, "report_whatsapp_template": "qa_reports_tpl"})
		sent = []

		def fake_send(**kw):
			path = frappe.db.get_value("File", {"file_url": kw["whatsapp_header_media"]}, "is_private")
			sent.append({**kw, "_private": path})
			return {"ok": True, "message_id": "wamid.QA"}

		frappe.db.set_value("Ads Report Template", self.t_a, "via_whatsapp", 1)
		try:
			with patch("excom.excom_ads.settings.get_settings", return_value=settings), patch.object(report_schedule, "template_header_type", return_value="DOCUMENT"), patch("excom.excom.services.outbound.send_transactional", side_effect=fake_send):
				report_schedule.run(self.t_a, "W2026-41", today=TODAY)
		finally:
			frappe.db.set_value("Ads Report Template", self.t_a, "via_whatsapp", 0)
		self.assertEqual(len(sent), 1, "only the user with a mobile number")
		self.assertEqual(sent[0]["phone"], "+919900011122")
		self.assertEqual(sent[0]["channel"], "whatsapp")
		self.assertEqual(sent[0]["whatsapp_template"], "qa_reports_tpl")
		self.assertEqual(sent[0]["_private"], 1, "the attachment is a private file")
		self.assertEqual(sent[0]["whatsapp_variables"][1], "2026-10-03 to 2026-10-09")
		self.assertFalse(frappe.db.exists("File", {"file_url": sent[0]["whatsapp_header_media"]}), "the file is removed after the send")
		wa = frappe.get_all(DELIVERY, {"template": self.t_a, "channel": "WhatsApp"}, ["status", "recipient_user", "whatsapp_message_id"])
		self.assertEqual([(w.status, w.recipient_user, w.whatsapp_message_id) for w in wa if w.status == "Sent"], [("Sent", U_A, "wamid.QA")])
		self.assertTrue(all(w.status == "Skipped" for w in wa if w.recipient_user != U_A), "no mobile, or outside scope")

	# ─── endpoints ────────────────────────────────────────────────────────────

	def test_endpoints_check_scope_and_rights(self):
		frappe.set_user(U_A)
		permissions.clear_cache()
		names = {t["name"] for t in reports.list_templates()["templates"]}
		self.assertIn(self.t_a, names)
		self.assertNotIn(self.t_b, names, "a company B report is not listed to a company A user")
		self.assertRaises(frappe.PermissionError, reports.preview, self.t_b)
		p = reports.preview(self.t_a)
		self.assertIn(f"{P} Acc A", p["html"])
		self.assertRaises(frappe.PermissionError, reports.send_now, self.t_a)
		self.assertRaises(frappe.PermissionError, reports.download, self.t_a)
		frappe.set_user(U_NONE)
		permissions.clear_cache()
		self.assertRaises(frappe.PermissionError, reports.list_templates)
		frappe.set_user(U_FIN)
		permissions.clear_cache()
		with patch("frappe.enqueue") as enq:
			# U_FIN has export_data but Excom Marketing write is role-level; the report is in scope.
			r = reports.send_now(self.t_a)
		self.assertTrue(r["queued"])
		self.assertTrue(enq.call_args.kwargs["job_id"].endswith(r["period_key"]))

	def test_template_validation(self):
		bad = frappe.get_doc({"doctype": "Ads Report Template", "template_name": f"{P} Bad", "company": COMPANY_B, "workspace": f"{P} WS A", "date_preset": "Yesterday", "sec_kpi": 1})
		self.assertRaises(frappe.ValidationError, bad.insert, ignore_permissions=True)
		bad = frappe.get_doc({"doctype": "Ads Report Template", "template_name": f"{P} Bad TZ", "company": self.company, "time_zone": "Mars/Base", "date_preset": "Yesterday", "sec_kpi": 1})
		self.assertRaises(frappe.ValidationError, bad.insert, ignore_permissions=True)
		frappe.set_user(U_B)
		permissions.clear_cache()
		other = frappe.get_doc({"doctype": "Ads Report Template", "template_name": f"{P} Not Mine", "company": self.company, "date_preset": "Yesterday", "sec_kpi": 1})
		self.assertRaises(frappe.PermissionError, other.insert)

	def test_pdf_is_optional_and_stated(self):
		if reports.pdf_engine() is None:
			r = self._render(self.t_a)
			self.assertIsNone(r.pdf)
			self.assertEqual([a["fname"].rsplit(".", 1)[1] for a in r.attachments()], ["xlsx", "csv"])
			self.assertIn("No PDF", r.html)
		with patch.object(reports, "make_pdf", return_value=b"%PDF-1.4 qa"):
			r = self._render(self.t_a)
		self.assertEqual(r.attachments()[-1]["fname"].rsplit(".", 1)[1], "pdf")
