"""Excom Ads, manual import: CSV / XLSX validation with row-level errors, campaign matching, idempotent
upsert of campaign- and account-level snapshots, re-import replacing dates, rights, file ownership,
and Manual accounts skipped by sync and the scheduler.

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom_ads.tests.test_ads_import'}"
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate

from excom.excom_ads import dashboard, imports, permissions, sync, tasks
from excom.excom_ads.naming import mirror_name, snapshot_name
from excom.excom_ads.providers.base import get_provider, provider_class

P = "QA Import"
WS = f"{P} WS"
U_RIGHT = "qa_import_right@example.com"
U_NORIGHT = "qa_import_noright@example.com"
USERS = (U_RIGHT, U_NORIGHT)
HEADER = "date,campaign,campaign_id,spend,impressions,clicks,leads,purchases,purchase_value,currency"


class TestAdsImport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls._clean()
		cls.company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.get_all("Company", pluck="name", limit=1)[0]
		cls.cur = frappe.get_cached_value("Company", cls.company, "default_currency")
		ins = lambda d: frappe.get_doc(d).insert(ignore_permissions=True)
		ins({"doctype": "Ads Workspace", "workspace_name": WS, "company": cls.company})
		cls.acc = ins({"doctype": "Ads Account", "account_name": f"{P} Network", "provider": "Manual", "external_id": "qa-import-net", "workspace": WS, "currency": cls.cur, "sync_enabled": 1}).name
		cls.meta = ins({"doctype": "Ads Account", "account_name": f"{P} Meta", "provider": "Meta", "external_id": "act_8880001", "workspace": WS, "currency": cls.cur}).name
		cls.existing = ins({"doctype": "Ads Campaign", "account": cls.acc, "workspace": WS, "company": cls.company, "provider": "Manual", "campaign_name": f"{P} Search", "external_id": "srch-1", "status": "Unknown"}).name
		for u in USERS:
			ins({"doctype": "User", "email": u, "first_name": u.split("@")[0], "send_welcome_email": 0, "roles": [{"role": "Excom Marketing"}]})
			ins({"doctype": "User Permission", "user": u, "allow": "Ads Workspace", "for_value": WS, "apply_to_all_doctypes": 1})
		ins({"doctype": "Ads Access Profile", "user": U_RIGHT, "enabled": 1, "manage_connections": 1})
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._clean()
		super().tearDownClass()

	@classmethod
	def _clean(cls):
		frappe.set_user("Administrator")
		accs = frappe.get_all("Ads Account", {"account_name": ["like", f"{P}%"]}, pluck="name") or ["-"]
		imps = frappe.get_all("Ads Metric Import", {"account": ["in", accs]}, pluck="name")
		for f in frappe.get_all("File", {"attached_to_doctype": "Ads Metric Import", "attached_to_name": ["in", imps or ["-"]]}, pluck="name") + frappe.get_all("File", {"file_name": ["like", "qa_import_%"]}, pluck="name"):
			frappe.delete_doc("File", f, force=True, ignore_permissions=True)
		frappe.db.delete("Version", {"ref_doctype": "Ads Metric Import", "docname": ["in", imps or ["-"]]})
		for dt in ("Ads Metric Import", "Ads Metric Snapshot", "Ads Campaign", "Ads Sync Job", "Ads Alert"):
			frappe.db.delete(dt, {"account": ["in", accs]})
		frappe.db.delete("Ads Account", {"name": ["in", accs]})
		frappe.db.delete("Ads Workspace", {"workspace_name": ["like", f"{P}%"]})
		frappe.db.delete("Ads Access Profile", {"user": ["in", USERS]})
		frappe.db.delete("User Permission", {"user": ["in", USERS]})
		for u in USERS:
			if frappe.db.exists("User", u):
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()
		frappe.db.delete("Ads Metric Snapshot", {"account": self.acc})
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		permissions.clear_cache()

	_n = 0

	def _import(self, content, ext="csv", account=None):
		TestAdsImport._n += 1
		f = frappe.get_doc({"doctype": "File", "file_name": f"qa_import_{TestAdsImport._n}.{ext}", "content": content, "is_private": 1})
		f.insert(ignore_permissions=True)
		doc = frappe.get_doc({"doctype": "Ads Metric Import", "account": account or self.acc, "import_file": f.file_url})
		doc.insert()
		return doc.name

	def _rows(self, level=None):
		f = {"account": self.acc}
		if level:
			f["level"] = level
		return frappe.get_all("Ads Metric Snapshot", f, ["name", "level", "date", "campaign", "entity_external_id", "spend", "impressions", "clicks", "leads", "purchases", "purchase_value", "spend_company_currency", "provider", "company"], order_by="date, level, campaign")

	# ─── validation ──────────────────────────────────────────────────────────

	def test_row_level_errors_and_nothing_written(self):
		future = add_days(getdate(), 3)
		csv = "\n".join([
			HEADER,
			f"2026-10-01,{P} Search,,100,1000,10,1,0,0,{self.cur}",  # 2 good
			"05/10/2026,Brand,,10,1,1,0,0,0,",  # 3 date format
			"2026-10-01,Brand,,-5,1,1,0,0,0,",  # 4 negative spend
			"2026-10-01,Display,,,1,1,0,0,0,",  # 5 missing spend
			"2026-10-01,Video,,10,100,12.5,0,0,0,",  # 6 fractional clicks
			"2026-10-01,Other,,10,100,1,0,0,0,USD",  # 7 wrong currency
			f"2026-10-01,{P} Search,,5,1,1,0,0,0,",  # 8 duplicate of row 2
			f"{future},Brand,,10,1,1,0,0,0,",  # 9 future
			"2026-10-01,,,10,1,1,0,0,0,",  # 10 no campaign
			"2026-10-01,Brand,,abc,1,1,0,0,0,",  # 11 not a number
			",,,,,,,,,",  # blank line: ignored
		])
		name = self._import(csv)
		r = imports.validate_import(name)
		self.assertEqual(r["status"], "Failed")
		by_row = {}
		for e in r["errors"]:
			by_row.setdefault(e["row"], set()).add(e["column"])
		self.assertEqual(by_row, {3: {"date"}, 4: {"spend"}, 5: {"spend"}, 6: {"clicks"}, 7: {"currency"}, 8: {"campaign"}, 9: {"date"}, 10: {"campaign"}, 11: {"spend"}})
		doc = frappe.get_doc("Ads Metric Import", name)
		self.assertEqual((doc.rows_total, doc.rows_valid, doc.rows_error), (10, 1, 9))
		self.assertIn("nothing was imported", doc.summary)
		self.assertEqual(json.loads(doc.errors)[0]["row"], 3)
		r = imports.run_import(name)
		self.assertEqual(r["status"], "Failed", "import re-validates and refuses")
		self.assertEqual(self._rows(), [])

	def test_missing_and_unknown_columns(self):
		r = imports.validate_import(self._import("day,spend\n2026-10-01,10\n"))
		self.assertEqual(r["status"], "Failed")
		self.assertIn("campaign", r["errors"][0]["column"])
		r = imports.validate_import(self._import("date,campaign,spend,ctr\n2026-10-01,A,10,1.2\n"))
		self.assertEqual([e["column"] for e in r["errors"]], ["ctr"])

	# ─── import ──────────────────────────────────────────────────────────────

	def test_import_is_idempotent_and_builds_account_totals(self):
		csv = "\n".join([
			HEADER,
			f"2026-10-01,{P.lower()} search,,100.50,1000,10,1,0,0,",  # name match, case-insensitive
			f"2026-10-01,{P} Reels,rl-9,\"1,200.25\",5000,40,3,1,900,{self.cur}",
			f"2026-10-02,{P} Reels,rl-9,50,500,5,0,0,0,",
		])
		name = self._import(csv)
		self.assertEqual(imports.validate_import(name)["status"], "Validated")
		r = imports.run_import(name)
		self.assertEqual((r["status"], r["campaigns_created"], r["snapshots_written"]), ("Imported", 1, 5))
		reels = mirror_name(self.acc, "rl-9")
		self.assertEqual(frappe.db.get_value("Ads Campaign", reels, ["campaign_name", "provider", "company"]), (f"{P} Reels", "Manual", self.company))
		rows = self._rows()
		acc_rows = [x for x in rows if x.level == "Account"]
		self.assertEqual([(str(x.date), x.spend, x.impressions, x.leads, x.purchase_value) for x in acc_rows], [("2026-10-01", 1300.75, 6000, 4, 900), ("2026-10-02", 50, 500, 0, 0)])
		search = next(x for x in rows if x.campaign == self.existing)
		self.assertEqual(search.name, snapshot_name(self.acc, "Campaign", "srch-1", "2026-10-01"))
		self.assertEqual((search.provider, search.company, search.spend_company_currency), ("Manual", self.company, 100.5))
		names = sorted(x.name for x in rows)
		# Again: same rows, nothing new.
		r = imports.run_import(name)
		self.assertEqual((r["status"], r["campaigns_created"]), ("Imported", 0))
		self.assertEqual(sorted(x.name for x in self._rows()), names)
		self.assertEqual(self._rows()[0].spend, rows[0].spend)
		acc = frappe.get_doc("Ads Account", self.acc)
		self.assertEqual((str(acc.metrics_synced_from), str(acc.metrics_synced_until)), ("2026-10-01", "2026-10-02"))
		o = dashboard.overview("2026-10-01", "2026-10-02", account=self.acc)
		self.assertEqual(o["totals"][0]["spend"], 1350.75, "imported figures are platform-reported figures")

	def test_reimport_replaces_only_the_files_dates(self):
		first = "\n".join([HEADER, f"2026-10-01,{P} Search,,100,1,1,0,0,0,", "2026-10-01,Gone,g-1,30,1,1,0,0,0,", f"2026-10-02,{P} Search,,70,1,1,0,0,0,"])
		imports.run_import(self._import(first))
		fix = "\n".join([HEADER, f"2026-10-01,{P} Search,,110,1,1,0,0,0,"])
		r = imports.run_import(self._import(fix))
		self.assertEqual(r["status"], "Imported")
		rows = self._rows()
		day1 = [x for x in rows if str(x.date) == "2026-10-01"]
		self.assertEqual(sorted((x.level, x.spend) for x in day1), [("Account", 110), ("Campaign", 110)], "the corrected day replaces the old one; a campaign no longer in it is gone")
		day2 = [x for x in rows if str(x.date) == "2026-10-02"]
		self.assertEqual(sorted((x.level, x.spend) for x in day2), [("Account", 70), ("Campaign", 70)], "dates not in the file are untouched")

	def test_xlsx(self):
		from datetime import datetime

		from frappe.utils.xlsxutils import make_xlsx

		data = [HEADER.split(","), [datetime(2026, 10, 3), f"{P} Search", "", 12.5, 100, 3, 0, 0, 0, ""], ["2026-10-04", "", 774411223344.0, 7, 70, 1, 0, 0, 0, ""]]
		content = make_xlsx(data, "Import").getvalue()
		r = imports.run_import(self._import(content, ext="xlsx"))
		self.assertEqual(r["status"], "Imported", r.get("errors"))
		self.assertTrue(frappe.db.exists("Ads Campaign", mirror_name(self.acc, "774411223344")), "a numeric ID cell becomes the digits, not 7.74e11")
		self.assertEqual({str(x.date) for x in self._rows("Account")}, {"2026-10-03", "2026-10-04"})

	# ─── rights and safety ───────────────────────────────────────────────────

	def test_rights(self):
		name = self._import("\n".join([HEADER, f"2026-10-01,{P} Search,,1,1,1,0,0,0,"]))
		frappe.set_user(U_NORIGHT)
		permissions.clear_cache()
		self.assertRaises(frappe.PermissionError, imports.validate_import, name)
		self.assertRaises(frappe.PermissionError, frappe.get_doc({"doctype": "Ads Metric Import", "account": self.acc, "import_file": "/private/files/x.csv"}).insert)
		frappe.set_user(U_RIGHT)
		permissions.clear_cache()
		self.assertEqual(imports.validate_import(name)["status"], "Validated")
		frappe.set_user("Administrator")
		self.assertRaises(frappe.ValidationError, frappe.get_doc({"doctype": "Ads Metric Import", "account": self.meta, "import_file": "/private/files/x.csv"}).insert)

	def test_someone_elses_file_is_not_read(self):
		f = frappe.get_doc({"doctype": "File", "file_name": "qa_import_secret.csv", "content": HEADER + "\n2026-10-01,Secret,,1,1,1,0,0,0,\n", "is_private": 1, "attached_to_doctype": "Ads Account", "attached_to_name": self.meta})
		f.insert(ignore_permissions=True)
		frappe.set_user(U_RIGHT)
		permissions.clear_cache()
		doc = frappe.get_doc({"doctype": "Ads Metric Import", "account": self.acc, "import_file": f.file_url})
		doc.insert()
		self.assertRaises((frappe.PermissionError, frappe.ValidationError), imports.validate_import, doc.name)
		self.assertEqual(frappe.db.get_value("Ads Metric Import", doc.name, "rows_total") or 0, 0, "nothing was read from it")

	def test_template(self):
		imports.download_template()
		self.assertEqual(frappe.response["type"], "download")
		self.assertTrue(frappe.response["filecontent"].startswith(HEADER))
		for k in ("type", "filecontent", "filename"):
			frappe.response.pop(k, None)

	def test_manual_accounts_are_skipped_by_sync(self):
		r = sync.run_job(self.acc, "Metrics")
		self.assertEqual(r["status"], "Skipped")
		self.assertFalse(frappe.db.exists("Ads Sync Job", {"account": self.acc}))
		self.assertNotIn(self.acc, [x.name for x in tasks._accounts()])
		cls = provider_class("Manual")
		self.assertFalse(cls.capability("daily_metrics").supported)
		self.assertIn("Ads Metric Import", cls.capability("daily_metrics").reason)
		self.assertRaises(Exception, get_provider(frappe.get_doc("Ads Account", self.acc)).daily_metrics, "Account", "2026-10-01", "2026-10-02")
