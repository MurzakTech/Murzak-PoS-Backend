# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Unit tests for bulk_update_stock in inventory_api.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import inventory_api


class TestBulkStockUpdate(unittest.TestCase):
	"""Test bulk stock update functionality."""

	def setUp(self):
		frappe.session = MagicMock()
		frappe.session.user = "manager@duka.test"
		frappe.local = MagicMock()
		frappe.local.response = {}
		# These tests run with no database: a stand-in takes the place of frappe.db for each test
		patcher = patch.object(frappe, "db", MagicMock(), create=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.ensure_record_access")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	def test_empty_items_refused(self, mock_co, mock_acc, mock_role):
		with patch.object(frappe.db, "exists", return_value=True):
			res = inventory_api.bulk_update_stock(
				items=[],
				warehouse="Main - TC",
				company="Test Company",
			)
			self.assertFalse(res["success"])
			self.assertIn("non-empty list", res["message"])

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.ensure_record_access")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	def test_nonexistent_warehouse_refused(self, mock_co, mock_acc, mock_role):
		with patch.object(frappe.db, "exists", return_value=False):
			res = inventory_api.bulk_update_stock(
				items=[{"item_code": "ITEM-1", "qty": 10}],
				warehouse="Nonexistent - TC",
				company="Test Company",
			)
			self.assertFalse(res["success"])
			self.assertIn("does not exist", res["message"])

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.ensure_record_access")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	@patch("murzak_pos.api.inventory_api.get_stock_balance_multiple")
	def test_unchanged_stock_creates_no_reconciliation(self, mock_balances, mock_co, mock_acc, mock_role):
		with patch.object(frappe.db, "exists", return_value=True):
			with patch.object(
				frappe,
				"get_all",
				return_value=[
					frappe._dict({
						"name": "ITEM-1",
						"item_name": "Sugar 1kg",
						"stock_uom": "Nos",
						"is_stock_item": 1,
						"valuation_rate": 100,
					})
				],
			):
				mock_balances.return_value = {"success": True, "data": {"ITEM-1": 15.0}}
				res = inventory_api.bulk_update_stock(
					items=[{"item_code": "ITEM-1", "qty": 15.0}],
					warehouse="Main - TC",
					company="Test Company",
					mode="set",
				)
				self.assertTrue(res["success"])
				self.assertEqual(res["data"]["updated_count"], 0)
				self.assertEqual(res["data"]["unchanged_count"], 1)
				self.assertIsNone(res["data"]["reconciliation"])

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.ensure_record_access")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	@patch("murzak_pos.api.inventory_api.get_stock_balance_multiple")
	@patch("murzak_pos.api.inventory_api.create_stock_reconciliation")
	def test_changed_stock_reconciles_successfully(
		self, mock_reco, mock_balances, mock_co, mock_acc, mock_role
	):
		with patch.object(frappe.db, "exists", return_value=True):
			with patch.object(
				frappe,
				"get_all",
				return_value=[
					frappe._dict({
						"name": "ITEM-1",
						"item_name": "Sugar 1kg",
						"stock_uom": "Nos",
						"is_stock_item": 1,
						"valuation_rate": 100,
					}),
					frappe._dict({
						"name": "ITEM-2",
						"item_name": "Flour 2kg",
						"stock_uom": "Nos",
						"is_stock_item": 1,
						"valuation_rate": 150,
					}),
				],
			):
				mock_balances.return_value = {"success": True, "data": {"ITEM-1": 10.0, "ITEM-2": 5.0}}
				mock_reco.return_value = {"success": True, "data": {"name": "MAT-RECO-2026-00009"}}

				res = inventory_api.bulk_update_stock(
					items=[
						{"item_code": "ITEM-1", "qty": 25.0},
						{"item_code": "ITEM-2", "qty": 5.0},  # unchanged
					],
					warehouse="Main - TC",
					company="Test Company",
					mode="set",
				)
				self.assertTrue(res["success"])
				self.assertEqual(res["data"]["updated_count"], 1)
				self.assertEqual(res["data"]["unchanged_count"], 1)
				self.assertEqual(res["data"]["reconciliation"], "MAT-RECO-2026-00009")
				self.assertEqual(res["data"]["items"][0]["difference"], 15.0)


if __name__ == "__main__":
	unittest.main()
