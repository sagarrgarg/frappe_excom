"""Run Excom's test modules without Frappe's global test-record bootstrap (which trips on other apps' mandatory custom fields).

    bench --site <site> execute excom.excom.tests.run.run --kwargs "{'module': 'excom.excom.tests.test_core_flows'}"
"""

import sys
import unittest

import frappe


def run(module: str = "excom.excom.tests.test_core_flows,excom.excom.tests.test_gateway_contract,excom.excom.tests.test_meta_dm,excom.excom.tests.test_meta_connect,excom.excom.tests.test_whatsapp_media,excom.excom.tests.test_email_schedule", pattern: str = "", backend: str = ""):
	if backend:
		import os
		os.environ["EXCOM_CRM_BACKEND"] = backend
	frappe.flags.in_test = True
	frappe.flags.mute_emails = True
	suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromName(m.strip()) for m in module.split(",") if m.strip()])
	if pattern:
		suite = unittest.TestSuite([t for t in _iter(suite) if pattern in t.id()])
	result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
	frappe.flags.in_test = False
	return {"ran": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "ok": result.wasSuccessful()}


def _iter(suite):
	for t in suite:
		if isinstance(t, unittest.TestSuite):
			yield from _iter(t)
		else:
			yield t
