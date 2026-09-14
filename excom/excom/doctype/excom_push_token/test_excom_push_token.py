"""Tests for Excom Push Token doctype."""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestExcomPushToken(FrappeTestCase):
	def test_create_push_token(self) -> None:
		token = frappe.get_doc({
			"doctype": "Excom Push Token",
			"user": "Administrator",
			"fcm_token": "test-fcm-token-12345",
			"environment": "Web",
		})
		token.insert(ignore_permissions=True)
		self.assertTrue(token.name)
		self.assertEqual(token.user, "Administrator")
		token.delete(ignore_permissions=True)
