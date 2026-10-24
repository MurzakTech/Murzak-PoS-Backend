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
		# Patched for each test and put back afterwards, so nothing leaks into other test files
		for p in (
			patch.object(frappe, "session", frappe._dict(user="manager@duka.test"), create=True),
			patch.object(frappe, "db", MagicMock(), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

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

	@patch("murzak_pos.api.product_import_images._resolve", return_value=["93.184.216.34"])
	@patch("murzak_pos.api.ecommerce_api.now_datetime", return_value="2026-10-24 10:00:00")
	@patch("murzak_pos.api.ecommerce_api.require_manager")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_save_settings_success(self, mock_co, mock_role, mock_now, mock_resolve):
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

	@patch("murzak_pos.api.ecommerce_api.require_manager")
	@patch("murzak_pos.api.ecommerce_api.resolve_company", return_value="Test Company")
	def test_an_address_inside_the_servers_network_is_refused(self, mock_co, mock_role):
		for address, url in (
			("127.0.0.1", "http://localhost:8000"),
			("169.254.169.254", "http://metadata.internal"),
			("10.0.0.5", "https://shop.example.com"),
		):
			with (
				self.subTest(url=url),
				patch("murzak_pos.api.product_import_images._resolve", return_value=[address]),
			):
				with patch.object(frappe.db, "get_default", return_value=None), patch("requests.get") as get:
					saved = ecommerce_api.save_ecommerce_settings(
						settings={"store_url": url}, company="Test Company"
					)
					tested = ecommerce_api.test_woocommerce_connection(
						store_url=url,
						consumer_key="ck_test12345678",
						consumer_secret="cs_test12345678",
						company="Test Company",
					)
				self.assertFalse(saved["success"])
				self.assertFalse(tested["success"])
				get.assert_not_called()


class TestKeysAreKeptSealed(unittest.TestCase):
	def setUp(self):
		self.saved = {}
		db = MagicMock()
		db.get_default.side_effect = lambda key: self.saved.get(key)
		db.set_default.side_effect = lambda key, value: self.saved.__setitem__(key, value)
		for p in (
			patch.object(frappe, "db", db, create=True),
			patch("frappe.utils.password.encrypt", side_effect=lambda text: f"<{text[::-1]}>"),
			patch("frappe.utils.password.decrypt", side_effect=lambda text: text[1:-1][::-1]),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_keys_are_stored_sealed_and_read_back_plain(self):
		ecommerce_api.write_settings(
			"Shop A", {"store_url": "https://a.test", "consumer_secret": "cs_live_123"}
		)
		raw = self.saved["murzak_ecommerce_Shop A"]
		self.assertNotIn("cs_live_123", raw)
		self.assertIn("sealed:", raw)
		self.assertEqual(ecommerce_api.read_settings("Shop A")["consumer_secret"], "cs_live_123")

	def test_keys_saved_before_sealing_still_work_and_are_sealed_on_the_next_save(self):
		self.saved["murzak_ecommerce_Shop A"] = json.dumps({"consumer_key": "ck_old"})
		settings = ecommerce_api.read_settings("Shop A")
		self.assertEqual(settings["consumer_key"], "ck_old")
		ecommerce_api.write_settings("Shop A", settings)
		self.assertNotIn("ck_old", self.saved["murzak_ecommerce_Shop A"])

	def test_sealing_twice_does_not_seal_again(self):
		once = ecommerce_api.seal("ck_1")
		self.assertEqual(ecommerce_api.seal(once), once)


if __name__ == "__main__":
	unittest.main()
