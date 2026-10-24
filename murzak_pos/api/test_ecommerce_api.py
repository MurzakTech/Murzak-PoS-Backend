# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Unit tests for ecommerce_api.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import ecommerce_api


class TestEcommerceApi(unittest.TestCase):
	"""Test e-commerce / WooCommerce configuration and sync endpoints."""

	def setUp(self):
		frappe.session = MagicMock()
		frappe.session.user = "manager@duka.test"
		frappe.local = MagicMock()
		frappe.local.response = {}
		# These tests run with no database: a stand-in takes the place of frappe.db for each test
		patcher = patch.object(frappe, "db", MagicMock(), create=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	@patch("murzak_pos.api.ecommerce_api.require_catalogue_role")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_get_settings_defaults(self, mock_co, mock_role):
		with patch.object(frappe.db, "get_default", return_value=None):
			with patch.object(frappe.db, "count", return_value=12):
				with patch.object(frappe.db, "get_value", return_value="Main - TC"):
					res = ecommerce_api.get_ecommerce_settings(company="Test Company")
					self.assertTrue(res["success"])
					self.assertFalse(res["data"]["enabled"])
					self.assertEqual(res["data"]["platform"], "woocommerce")
					self.assertEqual(res["data"]["catalog_count"], 12)
					self.assertEqual(res["data"]["status"], "not_configured")

	@patch("murzak_pos.api.ecommerce_api.require_manager")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_save_settings_validation(self, mock_co, mock_role):
		res = ecommerce_api.save_ecommerce_settings(
			settings={"store_url": "invalid-url"},
			company="Test Company",
		)
		self.assertFalse(res["success"])
		self.assertIn("valid website URL", res["message"])

	@patch("murzak_pos.api.ecommerce_api.require_manager")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_save_settings_success(self, mock_co, mock_role):
		with patch.object(frappe.db, "exists", return_value=True):
			with patch.object(frappe.db, "get_default", return_value=None):
				with patch.object(frappe.db, "set_default") as mock_set:
					with patch.object(frappe.db, "commit"):
						res = ecommerce_api.save_ecommerce_settings(
							settings={
								"enabled": True,
								"store_url": "https://myshop.com",
								"consumer_key": "ck_test12345678",
								"consumer_secret": "cs_test12345678",
								"warehouse": "Main - TC",
							},
							company="Test Company",
						)
						self.assertTrue(res["success"])
						self.assertTrue(mock_set.called)

	@patch("murzak_pos.api.ecommerce_api.require_manager")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_test_connection_requires_credentials(self, mock_co, mock_role):
		with patch.object(frappe.db, "get_default", return_value=None):
			res = ecommerce_api.test_woocommerce_connection(
				store_url="",
				company="Test Company",
			)
			self.assertFalse(res["success"])
			self.assertIn("required", res["message"])


if __name__ == "__main__":
	unittest.main()
