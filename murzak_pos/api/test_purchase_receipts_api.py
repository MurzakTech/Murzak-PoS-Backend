# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for purchase receipts and purchase returns. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import purchase
from murzak_pos.api import purchase_receipts_api as api


class QuantityCase(unittest.TestCase):
	def test_quantities_are_added_per_item(self):
		wanted = api.requested_quantities(
			[{"item_code": "A", "qty": 1}, {"item_code": "A", "qty": 2}, {"item_code": "B", "qty": 5}]
		)
		self.assertEqual(wanted, {"A": 3, "B": 5})

	def test_a_line_without_an_item_or_quantity_is_refused(self):
		for line in (
			{"qty": 1},
			{"item_code": "A", "qty": 0},
			{"item_code": "A", "qty": -2},
			{"item_code": "A"},
		):
			with self.assertRaises(frappe.ValidationError):
				api.requested_quantities([line])

	def test_nothing_to_return_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api._lines([])


class ShareCase(unittest.TestCase):
	LINES = ((1, "A", 2.0), (2, "B", 5.0), (3, "A", 4.0))

	def test_a_quantity_fills_the_first_line_then_the_next(self):
		self.assertEqual(api.share_over_lines({"A": 5}, list(self.LINES)), {1: 2.0, 3: 3.0})

	def test_a_small_quantity_stays_on_the_first_line(self):
		self.assertEqual(api.share_over_lines({"A": 1}, list(self.LINES)), {1: 1.0})

	def test_asking_for_more_than_is_left_is_refused_and_says_how_much_is_left(self):
		with self.assertRaises(frappe.ValidationError) as error:
			api.share_over_lines({"A": 7}, list(self.LINES))
		self.assertIn("6.0", str(error.exception))

	def test_lines_already_fully_returned_give_nothing(self):
		self.assertEqual(api.share_over_lines({"A": 1}, [(1, "A", 0.0), (2, "A", 3.0)]), {2: 1.0})

	def test_several_items_are_shared_independently(self):
		self.assertEqual(api.share_over_lines({"A": 6, "B": 1}, list(self.LINES)), {1: 2.0, 3: 4.0, 2: 1.0})


class CreateReceiptCase(unittest.TestCase):
	def create(self, items, supplier_exists=True, warehouse_company="Acme", **kwargs):
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "ensure_record_access"),
			patch.object(api.frappe, "db", MagicMock()) as db,
			patch.object(api.frappe, "get_doc") as get_doc,
		):
			db.exists.return_value = supplier_exists
			db.get_value.return_value = warehouse_company
			get_doc.return_value = MagicMock(name="PR-1", grand_total=100)
			result = api.create_purchase_receipt("Sup", items, "Acme", **kwargs)
		return result, get_doc

	def test_a_good_receipt_is_inserted_and_submitted(self):
		result, get_doc = self.create([{"item_code": "A", "qty": 2, "rate": 10}], warehouse="Stores")
		doc = get_doc.return_value
		doc.insert.assert_called_once()
		doc.submit.assert_called_once()
		self.assertTrue(result["success"])
		row = get_doc.call_args.args[0]["items"][0]
		self.assertEqual((row["qty"], row["rate"], row["warehouse"]), (2.0, 10.0, "Stores"))

	def test_an_unknown_supplier_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.create([{"item_code": "A", "qty": 1}], supplier_exists=False, warehouse="Stores")

	def test_a_line_needs_a_warehouse_from_somewhere(self):
		with self.assertRaises(frappe.ValidationError):
			self.create([{"item_code": "A", "qty": 1}])

	def test_a_warehouse_of_another_business_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			self.create([{"item_code": "A", "qty": 1}], warehouse="Theirs", warehouse_company="Other")

	def test_the_line_warehouse_wins_over_the_default(self):
		_result, get_doc = self.create([{"item_code": "A", "qty": 1, "warehouse": "Bar"}], warehouse="Stores")
		self.assertEqual(get_doc.call_args.args[0]["items"][0]["warehouse"], "Bar")


class ListCase(unittest.TestCase):
	def listed(self, **kwargs):
		with (
			patch.object(api, "require_stock_role"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "get_all", return_value=[]) as get_all,
			patch.object(api.frappe, "db", MagicMock()) as db,
		):
			db.count.return_value = 42
			result = api.list_purchase_receipts("Acme", **kwargs)
		return result, get_all.call_args_list[0].kwargs

	def test_the_list_is_one_business_and_excludes_returns(self):
		_result, kwargs = self.listed()
		self.assertEqual(kwargs["filters"]["company"], "Acme")
		self.assertEqual(kwargs["filters"]["is_return"], 0)

	def test_the_total_is_returned_with_the_page(self):
		result, _kwargs = self.listed()
		self.assertEqual(result["data"], {"data": [], "count": 42})

	def test_pages_are_counted_from_one_and_capped(self):
		_result, kwargs = self.listed(page=3, page_size=5000)
		self.assertEqual((kwargs["limit_start"], kwargs["limit_page_length"]), (200, 100))

	def test_filters_for_supplier_dates_and_status(self):
		_result, kwargs = self.listed(
			supplier="Sup", from_date="2026-01-01", to_date="2026-02-01", docstatus="0"
		)
		filters = kwargs["filters"]
		self.assertEqual(filters["supplier"], ["like", "%Sup%"])
		self.assertEqual(filters["posting_date"], ["between", ["2026-01-01", "2026-02-01"]])
		self.assertEqual(filters["docstatus"], 0)


class ReachabilityCase(unittest.TestCase):
	def test_the_screens_find_the_calls_where_they_look(self):
		for name in (
			"create_purchase_receipt",
			"get_purchase_receipt",
			"list_purchase_receipts",
			"create_purchase_return",
		):
			self.assertIs(getattr(purchase, name), getattr(api, name))


if __name__ == "__main__":
	unittest.main()
