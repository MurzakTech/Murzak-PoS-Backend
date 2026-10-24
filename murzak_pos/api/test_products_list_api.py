# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Products page calls (`products_list_api.list_products`, `list_categories`, `create_product_simple`) and the
rules behind them (`products_list_calc`). They need no database server: every Frappe call is mocked.

The first part tests the plain functions with plain values: paging, the status words, search, price and cost, who
sees cost, the categories, and what the New product form may send.

The second part runs the real SQL the list uses against a small in-memory SQLite database that has the same table and
column names (SQLite reads the backticks and the CASE and IFNULL used here). That is how "the count and the rows
always agree" is proved: every combination of filters is counted, then every page is read, and the two must match.

The last part tests the calls: who is let in, what a cashier does not receive, the answer shape, and what the
New product call does when the existing create call, the stock entry or a later step refuses.
"""

import ast
import csv
import itertools
import os
import re
import sqlite3
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import products_list_api as api
from murzak_pos.api import products_list_calc as rules

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))

OWNER, MANAGER, CASHIER = "beth@shop-a.test", "joy@shop-a.test", "amina@shop-a.test"
ROLES = {
	OWNER: ["System Manager"],
	MANAGER: ["Stock Manager"],
	CASHIER: ["Sales User"],
}


# ---------------------------------------------------------------------------------------------------
# A small database with the table and column names the list reads
# ---------------------------------------------------------------------------------------------------


def make_database():
	"""Shop A and Shop B with the products described in the contract's examples (see the comments)."""
	db = sqlite3.connect(":memory:")
	db.row_factory = sqlite3.Row
	db.executescript(
		"""
		CREATE TABLE `tabItem` (name TEXT, item_name TEXT, item_group TEXT, stock_uom TEXT, standard_rate REAL,
			disabled INT, is_stock_item INT, is_sales_item INT, custom_company TEXT, custom_pos_industry TEXT,
			creation INT);
		CREATE TABLE `tabBin` (item_code TEXT, warehouse TEXT, actual_qty REAL);
		CREATE TABLE `tabWarehouse` (name TEXT, company TEXT, is_group INT);
		CREATE TABLE `tabItem Reorder` (parent TEXT, parenttype TEXT, warehouse TEXT, warehouse_reorder_level REAL);
		CREATE TABLE `tabItem Barcode` (parent TEXT, barcode TEXT);
		"""
	)
	db.executemany(
		"INSERT INTO `tabWarehouse` VALUES (?, ?, ?)",
		[
			("Stores - A", "Shop A", 0),
			("Back - A", "Shop A", 0),
			("All Warehouses - A", "Shop A", 1),
			("Stores - B", "Shop B", 0),
		],
	)
	made = itertools.count(1)

	def item(code, name, group, *, stock=None, level=None, tracked=1, sellable=1, disabled=0, **more):
		company = more.get("company", "Shop A")
		db.execute(
			"INSERT INTO `tabItem` VALUES (?, ?, ?, 'Nos', 100, ?, ?, ?, ?, ?, ?)",
			(code, name, group, disabled, tracked, sellable, company, more.get("industry"), next(made)),
		)
		for store, qty in (stock or {}).items():
			db.execute("INSERT INTO `tabBin` VALUES (?, ?, ?)", (code, store, qty))
		for store, warn in (level or {}).items():
			db.execute("INSERT INTO `tabItem Reorder` VALUES (?, 'Item', ?, ?)", (code, store, warn))
		for barcode in more.get("barcodes", ()):
			db.execute("INSERT INTO `tabItem Barcode` VALUES (?, ?)", (code, barcode))

	item(
		"P-001",
		"Soap bar",
		"Toiletries",
		stock={"Stores - A": 10},
		level={"Stores - A": 5},
		barcodes=["6001234567890"],
	)
	item(
		"P-002",
		"Sugar 1kg",
		"Food",
		stock={"Stores - A": 5},
		level={"Stores - A": 5},
		barcodes=["5449000000996"],
	)
	item("P-003", "Rice 2kg", "Food", stock={"Stores - A": 0}, level={"Stores - A": 5})
	item("P-004", "Delivery fee", "Services", tracked=0)
	item("P-005", "Old bucket", "Household", stock={"Stores - A": 3}, disabled=1)
	item(
		"P-006",
		"Milk",
		"Food",
		stock={"Stores - A": 3, "Back - A": 4},
		level={"Stores - A": 2, "Back - A": 2},
	)
	item("P-007", "Tin_50% off", "Household", stock={"Stores - A": 20})
	item("P-008", "Raw flour", "Food", stock={"Stores - A": 8}, sellable=0)
	item("P-009", "Soap bar B", "Toiletries", stock={"Stores - B": 5}, company="Shop B")
	item("P-010", "Cough syrup", "Pharmacy", stock={"Stores - A": 9}, industry="pharmacy")
	item("P-011", "Bread", "Food")
	item("P-012", "Shared tin", "Household", stock={"Stores - A": 1, "Stores - B": 50})
	item("P-013", "Eggs", "Food", stock={"Back - A": 1}, level={"Back - A": 3})
	item("P-014", "Tin 500 g", "Household", stock={"Stores - A": 6})
	db.commit()
	return db


def run_sql(db, query, values=None, as_dict=False):
	"""Frappe's `frappe.db.sql` over SQLite: the `%(name)s` placeholders become `:name`."""
	text = re.sub(r"%\((\w+)\)s", r":\1", query)
	cursor = db.execute(text, dict(values or {}))
	rows = cursor.fetchall()
	if as_dict:
		return [dict(r) for r in rows]
	return [tuple(r) for r in rows]


def listed(db, **filters):
	"""(total, all rows on all pages of size 3) for the filters, read the way the call reads them."""
	options = {"company": "Shop A", **filters}
	parts = rules.build_query(**options)
	total = run_sql(db, rules.count_sql(parts), parts["params"])[0][0]
	rows, page = [], 1
	while True:
		numbers = rules.page_numbers(page, 3, total)
		chunk = run_sql(
			db,
			rules.rows_sql(parts),
			{**parts["params"], "limit": numbers["page_size"], "offset": numbers["offset"]},
			as_dict=True,
		)
		rows += chunk
		if page >= numbers["pages"]:
			return total, rows
		page += 1


def codes(rows):
	return [r["item_code"] for r in rows]


# ---------------------------------------------------------------------------------------------------
# The plain rules
# ---------------------------------------------------------------------------------------------------


class TestPaging(unittest.TestCase):
	def test_the_default_is_twenty_rows_from_page_one(self):
		self.assertEqual(
			rules.page_numbers(None, None, 135), {"page": 1, "page_size": 20, "pages": 7, "offset": 0}
		)

	def test_a_size_that_is_missing_or_wrong_is_twenty(self):
		for bad in (None, "", "abc", 0, -5, "0", True):
			with self.subTest(size=bad):
				self.assertEqual(rules.page_numbers(1, bad, 50)["page_size"], 20)

	def test_a_size_above_the_most_is_the_most(self):
		self.assertEqual(rules.page_numbers(1, 500, 1000)["page_size"], 100)
		self.assertEqual(rules.page_numbers(1, "100", 1000)["page_size"], 100)

	def test_the_size_is_never_below_one(self):
		self.assertEqual(rules.page_numbers(1, 1, 3)["page_size"], 1)

	def test_a_page_past_the_end_is_the_last_page(self):
		numbers = rules.page_numbers(99, 20, 45)
		self.assertEqual((numbers["page"], numbers["pages"], numbers["offset"]), (3, 3, 40))

	def test_a_page_before_the_start_or_not_a_number_is_page_one(self):
		for bad in (0, -3, "x", None):
			self.assertEqual(rules.page_numbers(bad, 20, 45)["page"], 1)

	def test_an_empty_list_has_one_empty_page(self):
		self.assertEqual(rules.page_numbers(1, 20, 0), {"page": 1, "page_size": 20, "pages": 1, "offset": 0})

	def test_an_exact_multiple_has_no_extra_page(self):
		self.assertEqual(rules.page_numbers(1, 20, 40)["pages"], 2)
		self.assertEqual(rules.page_numbers(1, 20, 41)["pages"], 3)

	def test_numbers_that_arrive_as_text_are_read(self):
		numbers = rules.page_numbers("2", "10", "35")
		self.assertEqual((numbers["page"], numbers["page_size"], numbers["pages"]), (2, 10, 4))


class TestStatusAndSearch(unittest.TestCase):
	def test_nothing_means_active(self):
		for value in (None, "", "  ", "ACTIVE"):
			self.assertEqual(rules.normalize_status(value), "active")

	def test_each_view_is_known(self):
		for view in rules.STATUSES:
			self.assertEqual(rules.normalize_status(view), view)

	def test_an_unknown_view_is_refused(self):
		with self.assertRaises(rules.ListError) as caught:
			rules.normalize_status("expensive")
		self.assertEqual(caught.exception.code, "bad_status")

	def test_search_text_is_tidied_and_cut(self):
		self.assertEqual(rules.clean_search("  red   soap "), "red soap")
		self.assertEqual(rules.clean_search(None), "")
		self.assertEqual(len(rules.clean_search("x" * 500)), rules.MAX_SEARCH_LENGTH)

	def test_percent_underscore_and_the_escape_character_are_taken_literally(self):
		self.assertEqual(rules.like_pattern("50%"), "%50|%%")
		self.assertEqual(rules.like_pattern("a_b"), "%a|_b%")
		self.assertEqual(rules.like_pattern("a|b"), "%a||b%")


class TestStockStatus(unittest.TestCase):
	def test_the_words(self):
		cases = [
			((1, 1, 10, 5), "disabled"),
			((0, 0, 0, 0), "not_tracked"),
			((0, 1, 0, 5), "out"),
			((0, 1, -2, 5), "out"),
			((0, 1, 5, 5), "low"),
			((0, 1, 3, 5), "low"),
			((0, 1, 6, 5), "ok"),
			((0, 1, 6, 0), "ok"),
			((0, 1, 1, 0), "ok"),
			((0, 1, None, None), "out"),
		]
		for args, word in cases:
			with self.subTest(args=args):
				self.assertEqual(rules.stock_status(*args), word)

	def test_a_service_is_never_low_or_out(self):
		self.assertEqual(rules.stock_status(0, 0, 0, 5), "not_tracked")


class TestPriceAndCost(unittest.TestCase):
	def test_the_price_in_the_products_own_unit_wins(self):
		prices = [{"uom": "Box", "price_list_rate": 900}, {"uom": "Nos", "price_list_rate": 80}]
		self.assertEqual(rules.pick_price(prices, "Nos", 50), (80.0, "price_list"))

	def test_any_price_in_the_list_when_none_is_in_the_unit(self):
		self.assertEqual(
			rules.pick_price([{"uom": "Box", "price_list_rate": 900}], "Nos", 50), (900.0, "price_list")
		)

	def test_the_standard_rate_when_the_list_has_no_price(self):
		self.assertEqual(rules.pick_price([], "Nos", 50), (50.0, "standard_rate"))
		self.assertEqual(rules.pick_price(None, "Nos", None), (0.0, "standard_rate"))

	def test_cost_is_the_stores_buying_price_first(self):
		self.assertEqual(rules.cost_price(40, 35, [30]), 40.0)

	def test_cost_falls_back_to_the_product_then_the_stores(self):
		self.assertEqual(rules.cost_price(None, 35, [30]), 35.0)
		self.assertEqual(rules.cost_price(0, 0, [30, 33]), 33.0)

	def test_a_cost_of_zero_is_not_known(self):
		self.assertIsNone(rules.cost_price(0, 0, [0]))
		self.assertIsNone(rules.cost_price())

	def test_the_preferred_stores_buying_price_wins(self):
		rows = [
			{"warehouse": "Back - A", "buying_price": 50},
			{"warehouse": "Stores - A", "buying_price": 40},
		]
		self.assertEqual(rules.choose_buying_price(rows, "Stores - A"), 40.0)

	def test_otherwise_the_highest_recorded_buying_price(self):
		rows = [{"warehouse": "Back - A", "buying_price": 50}, {"warehouse": "X", "buying_price": 0}]
		self.assertEqual(rules.choose_buying_price(rows, "Stores - A"), 50.0)
		self.assertIsNone(rules.choose_buying_price([], "Stores - A"))
		self.assertIsNone(rules.choose_buying_price(None))

	def test_profit_per_item(self):
		self.assertEqual(rules.profit_per_item(120, 80), 40.0)
		self.assertEqual(rules.profit_per_item(70, 80), -10.0)

	def test_profit_is_none_without_a_cost(self):
		self.assertIsNone(rules.profit_per_item(120, None))
		self.assertIsNone(rules.profit_per_item(120, 0))

	def test_margin_is_a_percentage_of_the_price(self):
		self.assertEqual(rules.margin_percent(120, 80), 33.3)
		self.assertIsNone(rules.margin_percent(0, 80))
		self.assertIsNone(rules.margin_percent(120, None))


RAW = {
	"item_code": "P-001",
	"item_name": "Soap bar",
	"category": "Toiletries",
	"unit": "Nos",
	"standard_rate": 100,
	"disabled": 0,
	"is_stock_item": 1,
	"stock": 10.0,
	"low_stock_level": 5.0,
}


class TestShapingARow(unittest.TestCase):
	def test_an_owner_row_has_cost_and_profit(self):
		row = rules.shape_row(
			RAW,
			prices=[{"uom": "Nos", "price_list_rate": 120}],
			barcodes=["600", "601"],
			cost=80,
			show_cost=True,
		)
		self.assertEqual(row["price"], 120.0)
		self.assertEqual(
			(row["cost_price"], row["profit_per_item"], row["margin_percent"]), (80.0, 40.0, 33.3)
		)
		self.assertEqual((row["barcode"], row["barcodes"]), ("600", ["600", "601"]))
		self.assertEqual((row["stock"], row["low_stock_level"], row["status"]), (10, 5, "ok"))

	def test_a_cashiers_row_has_no_cost_keys_at_all(self):
		row = rules.shape_row(RAW, cost=80, show_cost=False)
		for key in ("cost_price", "profit_per_item", "margin_percent"):
			self.assertNotIn(key, row)

	def test_an_unknown_cost_is_null_not_zero(self):
		row = rules.shape_row(RAW, cost=None, show_cost=True)
		self.assertIsNone(row["cost_price"])
		self.assertIsNone(row["profit_per_item"])

	def test_a_service_has_no_stock_figure(self):
		row = rules.shape_row({**RAW, "is_stock_item": 0, "stock": 0}, show_cost=False)
		self.assertIsNone(row["stock"])
		self.assertIsNone(row["low_stock_level"])
		self.assertEqual(row["status"], "not_tracked")

	def test_no_warning_set_is_null(self):
		self.assertIsNone(rules.shape_row({**RAW, "low_stock_level": 0})["low_stock_level"])

	def test_a_product_without_a_barcode_has_an_empty_one(self):
		row = rules.shape_row(RAW)
		self.assertEqual((row["barcode"], row["barcodes"]), ("", []))

	def test_a_whole_quantity_has_no_decimals_and_a_part_one_keeps_them(self):
		self.assertEqual(rules.shape_row({**RAW, "stock": 2.0})["stock"], 2)
		self.assertEqual(rules.shape_row({**RAW, "stock": 2.5})["stock"], 2.5)

	def test_a_disabled_product_says_so(self):
		row = rules.shape_row({**RAW, "disabled": 1})
		self.assertEqual((row["disabled"], row["status"]), (1, "disabled"))

	def test_the_summary_is_whole_numbers_even_from_nulls(self):
		self.assertEqual(
			rules.shape_summary({"total": 0, "low": None, "out_of_stock": None}),
			{"total": 0, "low_stock": 0, "out_of_stock": 0},
		)
		self.assertEqual(
			rules.shape_summary({"total": 11, "low": 2, "out_of_stock": 3.0}),
			{"total": 11, "low_stock": 2, "out_of_stock": 3},
		)
		self.assertEqual(rules.shape_summary(None)["total"], 0)


class TestCategories(unittest.TestCase):
	GROUPS: ClassVar[list] = [
		{"name": "All Item Groups", "parent_item_group": None, "is_group": 1},
		{"name": "Food", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "toiletries", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Empty", "parent_item_group": "All Item Groups", "is_group": 0},
	]

	def test_every_category_is_listed_with_its_count_a_to_z(self):
		result = rules.shape_categories(self.GROUPS, {"Food": 4, "toiletries": 1})
		self.assertEqual([c["name"] for c in result], ["Empty", "Food", "toiletries"])
		self.assertEqual([c["product_count"] for c in result], [0, 4, 1])

	def test_the_top_group_is_hidden_until_products_sit_in_it(self):
		self.assertNotIn("All Item Groups", [c["name"] for c in rules.shape_categories(self.GROUPS, {})])
		shown = rules.shape_categories(self.GROUPS, {"All Item Groups": 2})
		self.assertEqual(shown[-1]["name"], "All Item Groups")
		self.assertTrue(shown[-1]["is_default"])

	def test_a_category_with_no_products_has_a_zero_count(self):
		empty = next(c for c in rules.shape_categories(self.GROUPS, {}) if c["name"] == "Empty")
		self.assertEqual(empty["product_count"], 0)

	def test_the_parent_and_the_folder_flag_are_kept(self):
		food = next(c for c in rules.shape_categories(self.GROUPS, {"Food": 1}) if c["name"] == "Food")
		self.assertEqual((food["parent"], food["is_group"]), ("All Item Groups", 0))


class TestAmounts(unittest.TestCase):
	def test_digits_decimals_commas_and_spaces_are_read(self):
		for value, expected in (
			("350", 350.0),
			(350, 350.0),
			("1,200", 1200.0),
			("12.50", 12.5),
			(" 7 ", 7.0),
			(0, 0.0),
			(".5", 0.5),
		):
			with self.subTest(value=value):
				self.assertEqual(rules.parse_amount(value), expected)

	def test_nothing_is_none(self):
		for value in (None, "", "   "):
			self.assertIsNone(rules.parse_amount(value))

	def test_anything_else_is_refused(self):
		for value in ("abc", "-5", -5, "1e3", "12.3.4", float("inf"), float("nan"), True, "KES 5"):
			with self.subTest(value=value), self.assertRaises(rules.AmountError):
				rules.parse_amount(value)


class TestCheckingTheForm(unittest.TestCase):
	def check(self, **values):
		return rules.check_new_product(values)

	def fields(self, errors):
		return {(e["field"], e["code"]) for e in errors}

	def test_a_name_and_a_price_are_enough(self):
		clean, errors = self.check(item_name="Soap bar", price="120")
		self.assertEqual(errors, [])
		self.assertEqual((clean["item_name"], clean["price"]), ("Soap bar", 120.0))
		self.assertEqual(
			(clean["item_code"], clean["category"], clean["unit"], clean["barcode"]), ("", "", "", "")
		)
		self.assertEqual(
			(clean["cost_price"], clean["opening_stock"], clean["low_stock_warning"]), (None, None, None)
		)

	def test_a_blank_name_and_a_blank_price_are_both_reported(self):
		_clean, errors = self.check(item_name="  ", price="")
		self.assertEqual(self.fields(errors), {("item_name", "name_required"), ("price", "price_required")})

	def test_a_price_of_zero_is_allowed(self):
		clean, errors = self.check(item_name="Free bag", price="0")
		self.assertEqual(errors, [])
		self.assertEqual(clean["price"], 0.0)

	def test_a_bad_price_is_reported_on_the_price(self):
		_c, errors = self.check(item_name="X", price="abc")
		self.assertEqual(self.fields(errors), {("price", "price_invalid")})

	def test_a_huge_number_is_a_typing_mistake(self):
		_c, errors = self.check(item_name="X", price="99999999999")
		self.assertEqual(self.fields(errors), {("price", "too_big")})

	def test_names_are_tidied_and_cut_at_the_field_length(self):
		clean, _e = self.check(item_name="  Soap   bar ", price="1")
		self.assertEqual(clean["item_name"], "Soap bar")
		_c, errors = self.check(item_name="x" * 141, price="1")
		self.assertEqual(self.fields(errors), {("item_name", "name_too_long")})

	def test_optional_numbers_that_are_wrong_are_reported_on_their_own_field(self):
		_c, errors = self.check(
			item_name="X", price="1", cost_price="x", opening_stock="-1", low_stock_warning="y"
		)
		self.assertEqual(
			self.fields(errors),
			{
				("cost_price", "cost_invalid"),
				("opening_stock", "stock_invalid"),
				("low_stock_warning", "low_invalid"),
			},
		)

	def test_zero_stock_and_zero_warning_mean_none(self):
		clean, errors = self.check(item_name="X", price="1", opening_stock="0", low_stock_warning="0")
		self.assertEqual(errors, [])
		self.assertIsNone(clean["opening_stock"])
		self.assertIsNone(clean["low_stock_warning"])

	def test_a_cost_of_zero_is_kept_as_zero_not_as_missing(self):
		clean, _e = self.check(item_name="X", price="1", cost_price="0")
		self.assertEqual(clean["cost_price"], 0.0)

	def test_the_code_and_the_barcode_have_rules(self):
		_c, errors = self.check(item_name="X", price="1", item_code="bad/code", barcode="12 34")
		self.assertEqual(self.fields(errors), {("item_code", "code_invalid"), ("barcode", "barcode_invalid")})
		clean, errors = self.check(item_name="X", price="1", item_code=" SKU-1.a ", barcode=" 6001234567890 ")
		self.assertEqual(errors, [])
		self.assertEqual((clean["item_code"], clean["barcode"]), ("SKU-1.a", "6001234567890"))

	def test_a_code_cannot_start_with_a_dash_or_be_too_long(self):
		_c, errors = self.check(item_name="X", price="1", item_code="-A")
		self.assertEqual(self.fields(errors), {("item_code", "code_invalid")})
		_c, errors = self.check(item_name="X", price="1", item_code="A" * 141)
		self.assertEqual(self.fields(errors), {("item_code", "code_too_long")})

	def test_missing_values_in_the_request_are_not_a_crash(self):
		clean, errors = rules.check_new_product({})
		self.assertEqual(self.fields(errors), {("item_name", "name_required"), ("price", "price_required")})
		self.assertEqual(clean["item_code"], "")


class TestReadingARefusal(unittest.TestCase):
	def test_the_field_is_read_from_the_words(self):
		cases = {
			"Barcode 6001234567890 already used in Item P-001": "barcode",
			"A product with the code 'X' already exists. Please use a different product code.": "item_code",
			"The product category 'Z' does not exist.": "category",
			"The unit of measure 'Q' does not exist.": "unit",
			"Product name is required.": "item_name",
			"An error occurred while creating the product.": None,
			"": None,
			None: None,
		}
		for message, field in cases.items():
			with self.subTest(message=message):
				self.assertEqual(rules.classify_save_error(message), field)


class TestStoreChoiceAndLines(unittest.TestCase):
	def test_the_default_store_wins(self):
		self.assertEqual(rules.pick_warehouse("Shelf - A", ["Stores - A", "Back - A"]), "Shelf - A")

	def test_otherwise_the_one_called_stores_then_the_oldest(self):
		self.assertEqual(rules.pick_warehouse(None, ["Back - A", "Stores - A"]), "Stores - A")
		self.assertEqual(rules.pick_warehouse(None, ["Back - A", "Shelf - A"]), "Back - A")
		self.assertIsNone(rules.pick_warehouse(None, []))

	def test_the_warning_row_has_a_quantity_because_erpnext_refuses_one_without(self):
		row = rules.reorder_row("Stores - A", 12)
		self.assertEqual(row["warehouse_reorder_qty"], 12)
		self.assertGreater(row["warehouse_reorder_qty"], 0)

	def test_the_warning_is_a_row_the_alerts_can_read(self):
		self.assertEqual(
			rules.reorder_row("Stores - A", 5),
			{
				"warehouse": "Stores - A",
				"warehouse_reorder_level": 5,
				"warehouse_reorder_qty": 5,
				"material_request_type": "Purchase",
			},
		)

	def test_the_opening_stock_line_carries_the_cost_only_when_known(self):
		self.assertEqual(
			rules.opening_stock_item("P-1", 12, "Stores - A", 80),
			{"item_code": "P-1", "qty": 12, "t_warehouse": "Stores - A", "basic_rate": 80.0},
		)
		self.assertNotIn("basic_rate", rules.opening_stock_item("P-1", 12, "Stores - A", None))
		self.assertNotIn("basic_rate", rules.opening_stock_item("P-1", 12, "Stores - A", 0))


# ---------------------------------------------------------------------------------------------------
# The real SQL, on a small database
# ---------------------------------------------------------------------------------------------------


class SqlCase(unittest.TestCase):
	def setUp(self):
		self.db = make_database()
		self.addCleanup(self.db.close)


ACTIVE = ["P-014", "P-013", "P-012", "P-011", "P-010", "P-007", "P-006", "P-004", "P-003", "P-002", "P-001"]


class TestTheListSql(SqlCase):
	def test_the_default_list_is_the_enabled_sellable_products_of_the_business_newest_first(self):
		total, rows = listed(self.db)
		self.assertEqual(codes(rows), ACTIVE)
		self.assertEqual(total, len(ACTIVE))

	def test_another_businesses_products_never_show(self):
		_t, rows = listed(self.db)
		self.assertNotIn("P-009", codes(rows))
		_t, rows = listed(self.db, company="Shop B")
		self.assertEqual(codes(rows), ["P-009"])

	def test_a_product_that_is_not_for_sale_is_left_out(self):
		self.assertNotIn("P-008", codes(listed(self.db)[1]))

	def test_disabled_products_show_only_when_asked_for(self):
		self.assertNotIn("P-005", codes(listed(self.db)[1]))
		self.assertEqual(codes(listed(self.db, status="disabled")[1]), ["P-005"])
		everything = codes(listed(self.db, status="all")[1])
		self.assertIn("P-005", everything)
		self.assertEqual(len(everything), len(ACTIVE) + 1)

	def test_stock_adds_the_stores_of_the_business_only(self):
		_t, rows = listed(self.db)
		stock = {r["item_code"]: r["stock"] for r in rows}
		self.assertEqual(stock["P-006"], 7)  # 3 + 4 in two stores
		self.assertEqual(stock["P-012"], 1)  # the 50 held in another business's store do not count
		self.assertEqual(stock["P-011"], 0)  # no stock record at all

	def test_one_store_counts_only_that_store(self):
		_t, rows = listed(self.db, warehouse="Back - A")
		stock = {r["item_code"]: r["stock"] for r in rows}
		self.assertEqual(stock["P-006"], 4)
		self.assertEqual(stock["P-001"], 0)

	def test_the_warning_adds_up_over_the_same_stores_as_the_stock(self):
		_t, rows = listed(self.db)
		level = {r["item_code"]: r["low_stock_level"] for r in rows}
		self.assertEqual(level["P-006"], 4)  # 2 + 2
		self.assertEqual(level["P-001"], 5)
		self.assertEqual(level["P-011"], 0)
		_t, rows = listed(self.db, warehouse="Stores - A")
		self.assertEqual({r["item_code"]: r["low_stock_level"] for r in rows}["P-006"], 2)

	def test_low_means_some_left_but_no_more_than_the_warning(self):
		self.assertEqual(sorted(codes(listed(self.db, status="low")[1])), ["P-002", "P-013"])

	def test_out_means_none_left_and_leaves_services_alone(self):
		self.assertEqual(sorted(codes(listed(self.db, status="out")[1])), ["P-003", "P-011"])

	def test_the_views_follow_the_chosen_store(self):
		self.assertEqual(
			sorted(codes(listed(self.db, status="out", warehouse="Stores - A")[1])),
			["P-003", "P-011", "P-013"],
		)
		self.assertEqual(codes(listed(self.db, status="low", warehouse="Stores - A")[1]), ["P-002"])
		self.assertEqual(codes(listed(self.db, status="low", warehouse="Back - A")[1]), ["P-013"])

	def test_a_category_filter(self):
		self.assertEqual(sorted(codes(listed(self.db, category="Toiletries")[1])), ["P-001"])
		self.assertEqual(
			sorted(codes(listed(self.db, category="Food")[1])), ["P-002", "P-003", "P-006", "P-011", "P-013"]
		)

	def test_search_by_name_code_and_barcode(self):
		self.assertEqual(codes(listed(self.db, search="soap")[1]), ["P-001"])
		self.assertEqual(codes(listed(self.db, search="SOAP")[1]), ["P-001"])
		self.assertEqual(codes(listed(self.db, search="P-003")[1]), ["P-003"])
		self.assertEqual(codes(listed(self.db, search="6001234")[1]), ["P-001"])
		self.assertEqual(codes(listed(self.db, search="5449000000996")[1]), ["P-002"])

	def test_search_takes_percent_and_underscore_literally(self):
		self.assertEqual(codes(listed(self.db, search="%")[1]), ["P-007"])
		self.assertEqual(codes(listed(self.db, search="Tin_5")[1]), ["P-007"])
		self.assertEqual(sorted(codes(listed(self.db, search="Tin")[1])), ["P-007", "P-012", "P-014"])

	def test_search_with_no_match_is_an_empty_list_with_a_zero_count(self):
		total, rows = listed(self.db, search="nothing like this")
		self.assertEqual((total, rows), (0, []))

	def test_an_industry_hides_other_industries_products(self):
		self.assertIn("P-010", codes(listed(self.db)[1]))
		self.assertIn("P-010", codes(listed(self.db, industry="pharmacy", has_industry_column=True)[1]))
		self.assertNotIn("P-010", codes(listed(self.db, industry="retail", has_industry_column=True)[1]))
		self.assertIn("P-010", codes(listed(self.db, industry="retail", has_industry_column=False)[1]))

	def test_a_product_is_never_repeated_by_its_joins(self):
		_t, rows = listed(self.db, status="all")
		self.assertEqual(len(codes(rows)), len(set(codes(rows))))


class TestTheCountAlwaysAgreesWithTheRows(SqlCase):
	"""The reason this page exists: the old list counted without the search text. Here every filter is in both."""

	def test_every_combination_of_filters(self):
		combos = itertools.product(
			rules.STATUSES,
			(None, "Food", "Household", "Nothing"),
			("", "tin", "o", "P-0", "%", "zzz"),
			(None, "Stores - A", "Back - A"),
			((None, False), ("retail", True)),
		)
		checked = 0
		for status, category, search, store, (industry, has_column) in combos:
			total, rows = listed(
				self.db,
				status=status,
				category=category,
				search=search,
				warehouse=store,
				industry=industry,
				has_industry_column=has_column,
			)
			with self.subTest(
				status=status, category=category, search=search, store=store, industry=industry
			):
				self.assertEqual(total, len(rows))
				self.assertEqual(len(codes(rows)), len(set(codes(rows))))
			checked += 1
		self.assertEqual(checked, 5 * 4 * 6 * 3 * 2)

	def test_the_summary_total_is_the_default_lists_count(self):
		parts = rules.build_query(company="Shop A")
		summary = rules.shape_summary(
			run_sql(self.db, rules.summary_sql(parts), parts["params"], as_dict=True)[0]
		)
		total, _rows = listed(self.db)
		self.assertEqual(summary["total"], total)

	def test_the_summary_low_and_out_match_the_views(self):
		for store in (None, "Stores - A", "Back - A"):
			parts = rules.build_query(company="Shop A", warehouse=store)
			summary = rules.shape_summary(
				run_sql(self.db, rules.summary_sql(parts), parts["params"], as_dict=True)[0]
			)
			with self.subTest(store=store):
				self.assertEqual(summary["low_stock"], listed(self.db, status="low", warehouse=store)[0])
				self.assertEqual(summary["out_of_stock"], listed(self.db, status="out", warehouse=store)[0])

	def test_the_summary_ignores_the_search_and_the_category(self):
		parts = rules.build_query(company="Shop A", search="soap", category="Toiletries")
		summary = rules.shape_summary(
			run_sql(self.db, rules.summary_sql(parts), parts["params"], as_dict=True)[0]
		)
		self.assertEqual(summary["total"], len(ACTIVE))

	def test_the_summary_of_a_business_with_no_products_is_zero(self):
		parts = rules.build_query(company="Nobody")
		row = run_sql(self.db, rules.summary_sql(parts), parts["params"], as_dict=True)[0]
		self.assertEqual(rules.shape_summary(row), {"total": 0, "low_stock": 0, "out_of_stock": 0})

	def test_the_category_counts_add_up_to_the_summary_total(self):
		parts = rules.build_query(company="Shop A")
		counts = {
			r["category"]: r["n"]
			for r in run_sql(self.db, rules.category_counts_sql(parts), parts["params"], as_dict=True)
		}
		self.assertEqual(sum(counts.values()), len(ACTIVE))
		for category, n in counts.items():
			with self.subTest(category=category):
				self.assertEqual(n, listed(self.db, category=category)[0])

	def test_the_python_words_and_the_sql_words_say_the_same_thing(self):
		"""stock_status (Python) and OUT_SQL, LOW_SQL (SQL) are one rule written twice; try every case."""
		db = sqlite3.connect(":memory:")
		db.row_factory = sqlite3.Row
		db.executescript(
			"""
			CREATE TABLE `tabItem` (name TEXT, is_stock_item INT, disabled INT);
			CREATE TABLE s (qty REAL);
			CREATE TABLE r (level REAL);
			"""
		)
		cases = [(t, q, w) for t in (0, 1) for q in (None, -1, 0, 0.5, 1, 5, 6) for w in (None, 0, 5)]
		for t, q, w in cases:
			sql = (
				f"SELECT {rules.OUT_SQL} AS is_out, {rules.LOW_SQL} AS is_low "
				"FROM (SELECT :t AS is_stock_item) i, (SELECT :q AS qty) s, (SELECT :w AS level) r"
			)
			row = db.execute(sql, {"t": t, "q": q, "w": w}).fetchone()
			word = rules.stock_status(0, t, q, w)
			with self.subTest(tracked=t, qty=q, level=w):
				self.assertEqual(bool(row["is_out"]), word == "out")
				self.assertEqual(bool(row["is_low"]), word == "low")


class TestTheSqlSource(unittest.TestCase):
	def test_no_percent_sign_is_written_into_the_query_text(self):
		"""A literal percent in the text would be read as a placeholder; the pattern travels as a value."""
		parts = rules.build_query(
			company="A",
			warehouse="W",
			category="C",
			status="low",
			search="x",
			industry="i",
			has_industry_column=True,
		)
		for sql in (
			rules.rows_sql(parts),
			rules.count_sql(parts),
			rules.summary_sql(parts),
			rules.category_counts_sql(parts),
		):
			stripped = re.sub(r"%\(\w+\)s", "", sql)
			self.assertNotIn("%", stripped)

	def test_every_placeholder_has_a_value(self):
		parts = rules.build_query(
			company="A",
			warehouse="W",
			category="C",
			status="low",
			search="x",
			industry="i",
			has_industry_column=True,
		)
		values = {**parts["params"], "limit": 1, "offset": 0}
		for sql in (
			rules.rows_sql(parts),
			rules.count_sql(parts),
			rules.summary_sql(parts),
			rules.category_counts_sql(parts),
		):
			for name in re.findall(r"%\((\w+)\)s", sql):
				self.assertIn(name, values)

	def test_the_rows_and_the_count_share_one_from_and_one_where(self):
		parts = rules.build_query(company="A", category="C", search="x")
		self.assertIn(parts["from"] + " WHERE " + parts["where"], rules.rows_sql(parts))
		self.assertIn(parts["from"] + " WHERE " + parts["where"], rules.count_sql(parts))

	def test_the_filters_are_values_not_text(self):
		parts = rules.build_query(
			company="x'; DROP TABLE tabItem; --", category="y' OR '1'='1", search="z' OR 1=1 --"
		)
		self.assertNotIn("DROP", parts["where"] + parts["from"])
		self.assertNotIn("OR '1'", parts["where"])
		self.assertEqual(parts["params"]["company"], "x'; DROP TABLE tabItem; --")

	def test_the_source_has_no_em_dash(self):
		for name in ("products_list_api.py", "products_list_calc.py", "test_products_list_api.py"):
			with open(os.path.join(HERE, name), encoding="utf-8") as handle:
				self.assertNotIn(chr(0x2014), handle.read(), name)


# ---------------------------------------------------------------------------------------------------
# The calls
# ---------------------------------------------------------------------------------------------------


class CallCase(unittest.TestCase):
	"""A signed-in user, the small database behind frappe.db.sql, and the reads the calls make replaced by plain values."""

	def setUp(self):
		self.db = make_database()
		self.addCleanup(self.db.close)
		self.sql = []
		self.exists = set()
		self.saved_docs = []

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user=OWNER, warehouse_company="Shop A"):
		db = MagicMock()
		db.sql.side_effect = self.answer
		db.has_column.return_value = True
		db.exists.side_effect = (
			lambda kind, name=None: (kind, name if isinstance(name, str) else str(name)) in self.exists
		)
		db.get_value.return_value = warehouse_company
		self.frappe_db = db
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start(
			(frappe, "get_roles"),
			create=True,
			side_effect=lambda user=None: list(ROLES[user or frappe.session.user]),
		)
		self.start((frappe, "db"), new=db, create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="secret database detail")
		self.start((frappe, "clear_messages"), create=True)
		self.start((frappe, "local"), new=MagicMock(), create=True)
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_industry"), return_value=(None, True))
		self.start((api, "_selling_price_list"), return_value="Standard Selling")
		self.start((api, "_default_warehouse"), return_value="Stores - A")
		self.start(
			(api, "_prices"),
			side_effect=lambda codes, price_list: {
				c: [{"uom": "Nos", "price_list_rate": 120}] for c in codes
			},
		)
		self.start((api, "_barcodes"), side_effect=lambda codes: {"P-001": ["6001234567890"]})
		self.start((api, "_costs"), side_effect=lambda codes, company, store: {c: 80 for c in codes})

	def answer(self, query, values=None, as_dict=False):
		self.sql.append((" ".join(query.split()), dict(values or {})))
		return run_sql(self.db, query, values, as_dict)


class TestListProducts(CallCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def data(self, **kwargs):
		result = api.list_products(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]

	def test_the_answer_has_rows_the_exact_count_and_the_paging(self):
		data = self.data()
		self.assertEqual(data["total"], len(ACTIVE))
		self.assertEqual((data["page"], data["page_size"], data["pages"]), (1, 20, 1))
		self.assertEqual([r["item_code"] for r in data["rows"]], ACTIVE)
		self.assertEqual(data["summary"], {"total": len(ACTIVE), "low_stock": 2, "out_of_stock": 2})

	def test_the_count_is_asked_with_the_same_filters_as_the_rows(self):
		self.data(search="tin", category="Household", status="all", page_size=1)
		count = next(v for s, v in self.sql if s.startswith("SELECT COUNT(*) AS n"))
		rows = next(v for s, v in self.sql if s.startswith("SELECT i.name AS item_code"))
		for key in ("company", "category", "like"):
			self.assertEqual(count[key], rows[key])

	def test_the_search_text_reaches_the_count(self):
		"""The old list counted all 11 products for any search."""
		data = self.data(search="tin")
		self.assertEqual(data["total"], 3)
		self.assertEqual(len(data["rows"]), 3)

	def test_twenty_rows_a_page_by_default_and_a_bad_size_is_twenty(self):
		for size in (None, "", "abc", 0, -1):
			self.assertEqual(self.data(page_size=size)["page_size"], 20)

	def test_the_size_is_never_above_a_hundred(self):
		self.assertEqual(self.data(page_size=1000)["page_size"], 100)

	def test_a_page_reads_its_own_rows(self):
		data = self.data(page=2, page_size=3)
		self.assertEqual([r["item_code"] for r in data["rows"]], ACTIVE[3:6])
		self.assertEqual((data["page"], data["pages"]), (2, 4))

	def test_a_page_past_the_end_gives_the_last_page_not_nothing(self):
		data = self.data(page=50, page_size=3)
		self.assertEqual((data["page"], len(data["rows"])), (4, 2))

	def test_a_row_has_the_price_stock_unit_and_barcode(self):
		row = next(r for r in self.data()["rows"] if r["item_code"] == "P-001")
		self.assertEqual(
			(row["item_name"], row["category"], row["unit"], row["barcode"]),
			("Soap bar", "Toiletries", "Nos", "6001234567890"),
		)
		self.assertEqual((row["price"], row["stock"], row["status"]), (120.0, 10, "ok"))

	def test_an_owner_gets_cost_and_profit_per_item(self):
		data = self.data()
		self.assertTrue(data["can_see_cost"])
		row = data["rows"][0]
		self.assertEqual((row["cost_price"], row["profit_per_item"]), (80.0, 40.0))

	def test_a_manager_gets_cost_too(self):
		self.as_user(MANAGER)
		self.assertIn("cost_price", self.data()["rows"][0])

	def test_a_cashier_gets_the_list_but_no_cost_or_profit_anywhere(self):
		self.as_user(CASHIER)
		data = self.data()
		self.assertFalse(data["can_see_cost"])
		self.assertTrue(data["rows"])
		for row in data["rows"]:
			for key in ("cost_price", "profit_per_item", "margin_percent"):
				self.assertNotIn(key, row)
		self.assertNotIn("cost", repr(data["summary"]).lower())

	def test_a_cashiers_request_never_reads_the_cost_tables(self):
		self.as_user(CASHIER)
		costs = self.start((api, "_costs"), side_effect=AssertionError("cost was read for a cashier"))
		self.data()
		costs.assert_not_called()

	def test_the_answer_says_which_stock_it_is(self):
		self.assertEqual(self.data()["stock_scope"], {"kind": "all_stores", "warehouse": None})
		self.assertEqual(
			self.data(warehouse="Back - A")["stock_scope"], {"kind": "store", "warehouse": "Back - A"}
		)

	def test_one_store_changes_the_stock_numbers(self):
		rows = {r["item_code"]: r for r in self.data(warehouse="Back - A")["rows"]}
		self.assertEqual(rows["P-006"]["stock"], 4)

	def test_a_store_of_another_business_is_refused(self):
		self.as_user(warehouse_company="Shop B")
		with self.assertRaises(frappe.PermissionError):
			api.list_products(warehouse="Stores - B")

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			api.list_products()

	def test_an_unknown_view_is_refused_in_words(self):
		with self.assertRaises(frappe.ValidationError):
			api.list_products(status="expensive")

	def test_a_database_failure_is_a_plain_message_not_a_trace(self):
		self.frappe_db.sql.side_effect = RuntimeError("secret database detail")
		result = api.list_products()
		self.assertFalse(result["success"])
		self.assertNotIn("secret", result["message"])
		self.assertTrue(result["message"])

	def test_a_failing_summary_does_not_stop_the_list(self):
		def answer(query, values=None, as_dict=False):
			if "SUM(CASE" in query:
				raise RuntimeError("summary broke")
			return self.answer(query, values, as_dict)

		self.frappe_db.sql.side_effect = answer
		data = self.data()
		self.assertIsNone(data["summary"])
		self.assertEqual(data["total"], len(ACTIVE))

	def test_the_filters_are_passed_as_values(self):
		self.data(search="x' OR 1=1 --", category="Food' --")
		for text, _values in self.sql:
			self.assertNotIn("OR 1=1", text)
			self.assertNotIn("Food'", text)

	def test_an_empty_shop_has_an_empty_answer_not_an_error(self):
		self.start((api, "resolve_company"), return_value="Nobody")
		data = self.data()
		self.assertEqual((data["rows"], data["total"], data["pages"]), ([], 0, 1))
		self.assertEqual(data["summary"], {"total": 0, "low_stock": 0, "out_of_stock": 0})


class TestListCategories(CallCase):
	GROUPS: ClassVar[list] = [
		{"name": "All Item Groups", "parent_item_group": None, "is_group": 1},
		{"name": "Food", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Household", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Toiletries", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Services", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Pharmacy", "parent_item_group": "All Item Groups", "is_group": 0},
		{"name": "Pets", "parent_item_group": "All Item Groups", "is_group": 0},
	]

	def setUp(self):
		super().setUp()
		self.as_user()
		self.start((frappe, "get_all"), create=True, return_value=self.GROUPS)

	def test_each_category_has_its_count_and_they_add_up(self):
		data = api.list_categories()["data"]
		counts = {c["name"]: c["product_count"] for c in data["categories"]}
		self.assertEqual(counts["Food"], 5)
		self.assertEqual(counts["Household"], 3)
		self.assertEqual(counts["Pets"], 0)
		self.assertEqual(data["product_total"], len(ACTIVE))
		self.assertEqual(sum(counts.values()), len(ACTIVE))
		self.assertEqual(data["total"], len(data["categories"]))

	def test_the_count_matches_the_list_for_that_category(self):
		for category in api.list_categories()["data"]["categories"]:
			name = category["name"]
			self.assertEqual(
				category["product_count"], api.list_products(category=name)["data"]["total"], name
			)

	def test_a_cashier_may_read_the_categories(self):
		self.as_user(CASHIER)
		self.assertTrue(api.list_categories()["success"])

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user("Guest")
		with self.assertRaises(frappe.AuthenticationError):
			api.list_categories()

	def test_a_failure_is_a_plain_message(self):
		self.frappe_db.sql.side_effect = RuntimeError("secret database detail")
		result = api.list_categories()
		self.assertFalse(result["success"])
		self.assertNotIn("secret", result["message"])


class NewProductCase(CallCase):
	"""create_product_simple with the existing create call, the stock entry and the saved document replaced."""

	def setUp(self):
		super().setUp()
		self.as_user()
		self.exists = {("Item Group", "Food"), ("UOM", "Kg"), ("UOM", "Nos")}
		self.created = []
		self.entries = []
		self.buying = []
		self.docs = []
		self.start((api, "_create_product"), side_effect=self.create)
		self.start((api, "_stock_entry"), side_effect=self.entry)
		self.start((api, "_save_buying_price"), side_effect=lambda *a: self.buying.append(a))
		self.start((api, "_next_code"), return_value="PRD-00007")
		self.start((api, "_relax_etims"))
		self.start((frappe, "get_doc"), create=True, side_effect=self.get_doc)
		self.start((frappe, "get_all"), create=True, return_value=["Back - A", "Stores - A"])
		self.entry_reply = {"success": True, "data": {"name": "MAT-STE-2026-00001"}}
		self.create_error = None

	def create(self, **values):
		if self.create_error:
			raise self.create_error
		self.created.append(values)
		return {"message": "Product created successfully"}

	def entry(self, **values):
		self.entries.append(values)
		if isinstance(self.entry_reply, Exception):
			raise self.entry_reply
		return self.entry_reply

	def get_doc(self, kind, name):
		doc = MagicMock()
		doc.appended = []
		doc.append.side_effect = lambda table, row: doc.appended.append((table, row))
		self.docs.append(doc)
		return doc

	def save(self, **values):
		result = api.create_product_simple(**values)
		return result


class TestNewProductWithOnlyANameAndAPrice(NewProductCase):
	def test_a_name_and_a_price_save_the_product(self):
		result = self.save(item_name="Soap bar", price="120")
		self.assertTrue(result["success"], result)
		self.assertEqual(result["data"]["product"]["item_code"], "PRD-00007")
		self.assertTrue(result["data"]["code_made_automatically"])
		self.assertEqual(result["data"]["product"]["price"], 120.0)

	def test_the_existing_create_call_makes_the_product_with_defaults(self):
		self.save(item_name="Soap bar", price="120")
		self.assertEqual(
			self.created,
			[
				{
					"item_code": "PRD-00007",
					"item_name": "Soap bar",
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"standard_rate": 120.0,
					"barcode": None,
					"company": "Shop A",
					"is_purchase_item": True,
				}
			],
		)

	def test_nothing_extra_happens_without_stock_warning_or_cost(self):
		result = self.save(item_name="Soap bar", price="120")
		self.assertEqual((self.entries, self.buying, self.docs), ([], [], []))
		self.assertIsNone(result["data"]["opening_stock"])
		self.assertIsNone(result["data"]["low_stock_warning"])
		self.assertEqual(result["data"]["notes"], [])

	def test_the_category_unit_barcode_and_own_code_are_used_when_given(self):
		self.save(
			item_name="Rice", price="300", item_code="RICE-1", category="Food", unit="Kg", barcode="6001"
		)
		made = self.created[0]
		self.assertEqual(
			(made["item_code"], made["item_group"], made["stock_uom"], made["barcode"]),
			("RICE-1", "Food", "Kg", "6001"),
		)

	def test_the_changes_are_saved(self):
		self.save(item_name="Soap bar", price="120")
		self.frappe_db.commit.assert_called()


class TestNewProductErrors(NewProductCase):
	def test_a_blank_name_and_price_are_both_reported_with_their_fields(self):
		result = self.save(item_name="", price="")
		self.assertFalse(result["success"])
		self.assertEqual({e["field"] for e in result["errors"]}, {"item_name", "price"})
		self.assertTrue(all(e["message"] for e in result["errors"]))
		self.assertEqual(self.created, [])

	def test_nothing_is_saved_when_a_check_fails(self):
		self.save(item_name="Soap", price="x")
		self.assertEqual((self.created, self.entries, self.docs), ([], [], []))

	def test_a_barcode_in_use_is_reported_on_the_barcode_field(self):
		self.exists.add(("Item Barcode", "{'barcode': '6001'}"))
		self.frappe_db.exists.side_effect = (
			lambda kind, name=None: (kind == "Item Barcode" and name == {"barcode": "6001"})
			or (kind, name) in self.exists
		)
		result = self.save(item_name="Soap", price="1", barcode="6001")
		self.assertEqual([e["field"] for e in result["errors"]], ["barcode"])
		self.assertEqual(self.created, [])

	def test_a_code_in_use_is_reported_on_the_code_field(self):
		self.exists.add(("Item", "SOAP-1"))
		result = self.save(item_name="Soap", price="1", item_code="SOAP-1")
		self.assertEqual([e["field"] for e in result["errors"]], ["item_code"])

	def test_a_missing_category_or_unit_is_reported_on_its_field(self):
		result = self.save(item_name="Soap", price="1", category="Ghost", unit="Ghost")
		self.assertEqual({e["field"] for e in result["errors"]}, {"category", "unit"})

	def test_a_refusal_from_the_existing_call_is_read_for_its_field(self):
		self.create_error = frappe.ValidationError("Barcode 6001 already used in Item P-001")
		result = self.save(item_name="Soap", price="1", barcode="6001")
		self.assertFalse(result["success"])
		self.assertEqual([e["field"] for e in result["errors"]], ["barcode"])
		self.assertIn("6001", result["message"])

	def test_a_refusal_that_cannot_be_told_apart_has_no_field_but_keeps_its_words(self):
		self.create_error = frappe.ValidationError("An error occurred while creating the product.")
		result = self.save(item_name="Soap", price="1")
		self.assertEqual(result["errors"], [])
		self.assertIn("creating the product", result["message"])

	def test_a_refusal_clears_the_pop_up_messages_so_they_are_not_shown_twice(self):
		self.create_error = frappe.ValidationError("Barcode 6001 already used")
		clear = self.start((frappe, "clear_messages"), create=True)
		self.save(item_name="Soap", price="1", barcode="6001")
		clear.assert_called()

	def test_a_permission_error_from_the_existing_call_is_not_swallowed(self):
		self.create_error = frappe.PermissionError("no")
		with self.assertRaises(frappe.PermissionError):
			self.save(item_name="Soap", price="1")

	def test_a_cashier_cannot_save_a_product(self):
		self.as_user(CASHIER)
		with self.assertRaises(frappe.PermissionError):
			self.save(item_name="Soap", price="1")
		self.assertEqual(self.created, [])

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user("Guest")
		ROLES["Guest"] = []
		self.addCleanup(ROLES.pop, "Guest", None)
		with self.assertRaises(frappe.AuthenticationError):
			self.save(item_name="Soap", price="1")

	def test_a_store_of_another_business_is_refused(self):
		self.as_user(warehouse_company="Shop B")
		with self.assertRaises(frappe.PermissionError):
			self.save(item_name="Soap", price="1", warehouse="Stores - B")

	def test_a_person_who_may_not_see_cost_cannot_set_it(self):
		self.as_user(OWNER)
		ROLES["agri@shop-a.test"] = ["Agriculture Manager"]
		self.addCleanup(ROLES.pop, "agri@shop-a.test", None)
		self.as_user("agri@shop-a.test")
		with self.assertRaises(frappe.PermissionError):
			self.save(item_name="Soap", price="1", cost_price="50")
		self.assertEqual(self.created, [])


class TestNewProductExtras(NewProductCase):
	def test_the_low_stock_warning_goes_on_the_product_for_the_default_store(self):
		result = self.save(item_name="Soap", price="120", low_stock_warning="5")
		self.assertEqual(result["data"]["low_stock_warning"], {"saved": True, "level": 5})
		(doc,) = self.docs
		self.assertEqual(doc.appended, [("reorder_levels", rules.reorder_row("Stores - A", 5))])
		doc.save.assert_called_once()

	def test_opening_stock_goes_in_as_a_material_receipt(self):
		result = self.save(item_name="Soap", price="120", opening_stock="12", cost_price="80")
		self.assertEqual(
			self.entries,
			[
				{
					"stock_entry_type": "Material Receipt",
					"items": [
						{"item_code": "PRD-00007", "qty": 12, "t_warehouse": "Stores - A", "basic_rate": 80.0}
					],
					"company": "Shop A",
					"to_warehouse": "Stores - A",
				}
			],
		)
		self.assertEqual(
			result["data"]["opening_stock"],
			{"saved": True, "qty": 12, "store": "Stores - A", "stock_entry": "MAT-STE-2026-00001"},
		)

	def test_the_chosen_store_is_used_for_stock_and_warning(self):
		self.save(item_name="Soap", price="1", opening_stock="3", low_stock_warning="1", warehouse="Back - A")
		self.assertEqual(self.entries[0]["to_warehouse"], "Back - A")
		self.assertEqual(self.docs[0].appended[0][1]["warehouse"], "Back - A")

	def test_the_cost_is_kept_on_the_product_and_for_the_store(self):
		self.save(item_name="Soap", price="120", cost_price="80")
		self.assertEqual(self.docs[0].valuation_rate, 80.0)
		self.assertEqual(self.buying, [("PRD-00007", "Stores - A", "Shop A", 80.0)])

	def test_opening_stock_without_a_cost_is_sent_without_a_rate(self):
		self.save(item_name="Soap", price="120", opening_stock="4")
		self.assertNotIn("basic_rate", self.entries[0]["items"][0])

	def test_zero_stock_and_zero_warning_do_nothing(self):
		self.save(item_name="Soap", price="120", opening_stock="0", low_stock_warning="0")
		self.assertEqual((self.entries, self.docs), ([], []))

	def test_a_shop_with_no_store_cannot_take_stock_and_nothing_is_saved(self):
		self.start((api, "_default_warehouse"), return_value=None)
		self.start((frappe, "get_all"), create=True, return_value=[])
		result = self.save(item_name="Soap", price="1", opening_stock="3")
		self.assertEqual([e["field"] for e in result["errors"]], ["opening_stock"])
		self.assertEqual(self.created, [])

	def test_a_shop_with_no_store_can_still_save_a_plain_product(self):
		self.start((api, "_default_warehouse"), return_value=None)
		self.start((frappe, "get_all"), create=True, return_value=[])
		self.assertTrue(self.save(item_name="Soap", price="1")["success"])

	def test_a_stock_entry_that_is_refused_leaves_the_product_saved_and_says_what_to_do(self):
		self.entry_reply = {"success": False, "message": "Valuation Rate for the Item PRD-00007, is required"}
		result = self.save(item_name="Soap", price="120", opening_stock="12")
		self.assertTrue(result["success"])
		self.assertFalse(result["data"]["opening_stock"]["saved"])
		(note,) = result["data"]["notes"]
		self.assertEqual(note["step"], "opening_stock")
		self.assertIn("Stock in", note["message"])
		self.assertNotIn("Valuation", note["message"])

	def test_a_stock_entry_that_raises_leaves_the_product_saved(self):
		self.entry_reply = RuntimeError("secret database detail")
		result = self.save(item_name="Soap", price="120", opening_stock="12")
		self.assertTrue(result["success"])
		self.assertFalse(result["data"]["opening_stock"]["saved"])
		self.assertNotIn("secret", repr(result))

	def test_a_failing_warning_does_not_stop_the_cost_or_the_stock(self):
		def broken(kind, name):
			doc = self.get_doc(kind, name)
			if not self.docs[1:]:
				doc.save.side_effect = RuntimeError("no")
			return doc

		self.start((frappe, "get_doc"), create=True, side_effect=broken)
		result = self.save(
			item_name="Soap", price="120", low_stock_warning="5", cost_price="80", opening_stock="3"
		)
		self.assertTrue(result["success"])
		self.assertEqual([n["step"] for n in result["data"]["notes"]], ["low_stock_warning"])
		self.assertEqual(len(self.entries), 1)
		self.assertEqual(len(self.buying), 1)
		self.assertFalse(result["data"]["low_stock_warning"]["saved"])

	def test_an_automatic_code_is_not_made_when_a_code_is_given(self):
		next_code = self.start((api, "_next_code"), return_value="PRD-00099")
		self.save(item_name="Soap", price="1", item_code="MY-1")
		next_code.assert_not_called()

	def test_the_answer_for_a_plain_product_has_no_cost_fields(self):
		self.as_user(OWNER)
		result = self.save(item_name="Soap", price="1")
		self.assertNotIn("cost_price", result["data"]["product"])


# ---------------------------------------------------------------------------------------------------
# Promises kept by the source
# ---------------------------------------------------------------------------------------------------


def _read(name):
	with open(os.path.join(HERE, name), encoding="utf-8") as handle:
		return handle.read()


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def test_the_existing_product_calls_are_not_edited(self):
		"""The new file only imports them (inside functions); product_api.py has no mention of the new module."""
		self.assertNotIn("products_list", _read("product_api.py"))

	def test_the_calc_module_needs_no_frappe(self):
		tree = ast.parse(_read("products_list_calc.py"))
		imported = {
			(node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
			for node in ast.walk(tree)
			if isinstance(node, ast.Import | ast.ImportFrom)
			for alias in (node.names if isinstance(node, ast.Import) else [None])
			if not (isinstance(node, ast.ImportFrom) and node.module is None)
		}
		self.assertTrue(imported <= {"math", "re", "__future__"}, imported)

	def test_the_api_never_writes_inside_the_list_calls(self):
		source = _read("products_list_api.py")
		tree = ast.parse(source)
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.name in (
				"list_products",
				"list_categories",
				"_summary",
			):
				text = ast.unparse(node)
				for write in (".insert(", ".save(", ".submit(", ".delete(", "commit()", "set_value("):
					self.assertNotIn(write, text, node.name)

	def test_only_the_create_call_is_post_only(self):
		source = _read("products_list_api.py")
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef create_product_simple(', source)

	def test_every_new_message_has_a_swahili_row(self):
		with open(
			os.path.join(APP, "murzak_pos", "translations", "sw.csv"), encoding="utf-8", newline=""
		) as handle:
			swahili = {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2}
		tree = ast.parse(_read("products_list_api.py"))
		messages = {
			node.args[0].value
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and getattr(node.func, "id", "") == "_"
			and node.args
			and isinstance(node.args[0], ast.Constant)
		}
		self.assertGreater(len(messages), 20)
		self.assertEqual(sorted(messages - set(swahili)), [])
		for message in messages:
			self.assertTrue(swahili[message].strip())

	def test_the_messages_name_no_system_words_a_shop_owner_would_not_know(self):
		tree = ast.parse(_read("products_list_api.py"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_" and node.args:
				text = node.args[0].value.lower() if isinstance(node.args[0], ast.Constant) else ""
				for word in ("item group", "uom", "doctype", "traceback", "error 417", "stock entry"):
					self.assertNotIn(word, text, text)


if __name__ == "__main__":
	unittest.main()
