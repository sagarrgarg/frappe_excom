"""Run Excom's test modules without Frappe's global test-record bootstrap (which trips on other apps' mandatory custom fields).

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_core_flows'}"
"""

import sys
import unittest

import frappe


def run(module: str = "excom.excom.tests.test_core_flows,excom.excom.tests.test_gateway_contract,excom.excom.tests.test_meta_dm,excom.excom.tests.test_meta_connect,excom.excom.tests.test_whatsapp_media,excom.excom.tests.test_email_schedule,excom.excom.tests.test_template_sync,excom.excom.tests.test_endpoint_guards,excom.excom.tests.test_agent_onboarding,excom.excom.tests.test_crm_visibility,excom.excom.tests.test_thread_visibility,excom.excom.tests.test_team_registry,excom.excom.tests.test_team_lifecycle,excom.excom.tests.test_agent_rights,excom.excom.tests.test_identity_access,excom.excom.tests.test_role_tiers,excom.excom.tests.test_auto_ack,excom.excom.tests.test_patches,excom.excom.tests.test_identity_naming,excom.excom_ads.tests.test_ads_meta_sync,excom.excom_ads.tests.test_ads_google_sync,excom.excom_ads.tests.test_ads_leads,excom.excom_ads.tests.test_ads_permissions,excom.excom_ads.tests.test_ads_dashboard,excom.excom_ads.tests.test_ads_billing,excom.excom_ads.tests.test_ads_bills,excom.excom_ads.tests.test_ads_ga4,excom.excom_ads.tests.test_ads_verified,excom.excom_ads.tests.test_ads_quality,excom.excom_ads.tests.test_ads_import,excom.excom_ads.tests.test_ads_reports,excom.excom_ads.tests.test_ads_escalation,excom.excom_ads.tests.test_ads_leads_dashboard,excom.excom_ads.tests.test_ads_connections,excom.excom_ads.tests.test_ads_hardening", pattern: str = "", backend: str = ""):
	if backend:
		import os
		os.environ["EXCOM_CRM_BACKEND"] = backend
	frappe.flags.in_test = True
	# No worker runs during tests, so jobs the suite enqueues (contact creation for test users,
	# dynamic-link cleanup after deletes, …) would pile up until the queue refuses new jobs. Remember
	# what was queued before the run and drop only what the run added.
	queued_before = _queued_job_ids()
	# The full suite queues more jobs than Frappe's queue-size guard allows; lift it for this test
	# process only (restored below) — the jobs are removed again when the run ends.
	import frappe.utils.background_jobs as _bj

	_real_check_queue_size = _bj._check_queue_size
	_bj._check_queue_size = lambda *a, **k: None
	frappe.flags.mute_emails = True
	# A test must not depend on how this site happens to be configured. Two ambient settings rewrite
	# a fixture underneath a test: a live Assignment Rule claims every lead the suite creates and
	# moves its team, and the visibility switch changes what the CRM helpers return. So assignment
	# is stubbed out for the run (frappe.flags.in_patch would do it too, but it also disables the
	# permission loading these tests are about), and the switch is pinned off here and turned on by
	# the tests that are about it.
	import frappe.automation.doctype.assignment_rule.assignment_rule as _assignment_rule

	_real_apply = _assignment_rule.apply
	_assignment_rule.apply = lambda *a, **k: None
	prior_enforcement = frappe.db.get_single_value("Excom Settings", "enforce_crm_visibility")
	frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", 0)
	frappe.db.commit()
	frappe.clear_cache()
	suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromName(m.strip()) for m in module.split(",") if m.strip()])
	if pattern:
		suite = unittest.TestSuite([t for t in _iter(suite) if pattern in t.id()])
	try:
		result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
	finally:
		_assignment_rule.apply = _real_apply
		_bj._check_queue_size = _real_check_queue_size
		_drop_jobs_added_since(queued_before)
		frappe.db.set_single_value("Excom Settings", "enforce_crm_visibility", prior_enforcement or 0)
		frappe.db.commit()
	frappe.flags.in_test = False
	return {"ran": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "ok": result.wasSuccessful()}


def _iter(suite):
	for t in suite:
		if isinstance(t, unittest.TestSuite):
			yield from _iter(t)
		else:
			yield t


def _site_queues():
	from frappe.utils.background_jobs import get_queues

	try:
		return get_queues()
	except Exception:
		return []


def _queued_job_ids() -> set:
	ids = set()
	for q in _site_queues():
		try:
			ids |= set(q.job_ids)
		except Exception:
			pass
	return ids


def _drop_jobs_added_since(before: set) -> int:
	"""Remove jobs this run queued for this site. Jobs that were already waiting are left alone."""
	dropped = 0
	for q in _site_queues():
		try:
			for jid in set(q.job_ids) - before:
				job = q.fetch_job(jid)
				if job is None or (job.kwargs or {}).get("site") != frappe.local.site:
					continue
				job.delete()
				dropped += 1
		except Exception:
			pass
	return dropped

