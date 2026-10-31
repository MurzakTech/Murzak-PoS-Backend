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
		# Patched for each test and put back afterwards, so nothing leaks into other test files
		for p in (
			patch.object(frappe, "session", frappe._dict(user="manager@duka.test"), create=True),
			patch.object(frappe, "db", MagicMock(), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

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
			self.assertIn("Add at least one product", res["message"])

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
			self.assertIn("could not find the store", res["message"])

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
					frappe._dict(
						name="ITEM-1",
						item_name="Sugar 1kg",
						stock_uom="Nos",
						is_stock_item=1,
						valuation_rate=100,
					)
				],
			):
				mock_balances.return_value = {
					"success": True,
					"data": [{"item_code": "ITEM-1", "actual_qty": 15.0}],
				}
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
					frappe._dict(
						name="ITEM-1",
						item_name="Sugar 1kg",
						stock_uom="Nos",
						is_stock_item=1,
						valuation_rate=100,
					),
					frappe._dict(
						name="ITEM-2",
						item_name="Flour 2kg",
						stock_uom="Nos",
						is_stock_item=1,
						valuation_rate=150,
					),
				],
			):
				mock_balances.return_value = {
					"success": True,
					"data": [
						{"item_code": "ITEM-1", "actual_qty": 10.0},
						{"item_code": "ITEM-2", "actual_qty": 5.0},
					],
				}
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

	def test_balances_by_code_reads_the_rows_the_balance_api_returns(self):
		res = {
			"success": True,
			"data": [
				{"item_code": "A", "actual_qty": 4.0},
				{"item_code": "B", "error": "Warehouse does not exist"},
				{"item_code": "C", "actual_qty": 0.0},
			],
		}
		self.assertEqual(inventory_api._balances_by_code(res), {"A": 4.0, "C": 0.0})
		self.assertEqual(inventory_api._balances_by_code({"success": False, "message": "x"}), {})
		self.assertEqual(inventory_api._balances_by_code(None), {})

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.ensure_record_access")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	@patch("murzak_pos.api.stock_levels_api._locations", return_value=["Only Store - TC"])
	def test_the_only_store_is_used_when_none_is_named(self, mock_locations, mock_co, mock_acc, mock_role):
		with patch.object(frappe.db, "exists", return_value=False) as exists:
			res = inventory_api.bulk_update_stock(
				items=[{"item_code": "ITEM-1", "qty": 1}], company="Test Company"
			)
			# It went on to look for the only store, not for a store called "None"
			exists.assert_called_with("Warehouse", "Only Store - TC")
			self.assertFalse(res["success"])

	@patch("murzak_pos.api.inventory_api.require_stock_role")
	@patch("murzak_pos.api.inventory_api.resolve_company", return_value="Test Company")
	@patch("murzak_pos.api.stock_levels_api._locations", return_value=["A - TC", "B - TC"])
	def test_two_stores_and_none_named_asks_which(self, mock_locations, mock_co, mock_role):
		with self.assertRaises(frappe.ValidationError):
			inventory_api.bulk_update_stock(items=[{"item_code": "ITEM-1", "qty": 1}], company="Test Company")


if __name__ == "__main__":
	unittest.main()
