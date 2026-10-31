# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Stock levels page calls (`stock_levels_api`) and the rules behind them (`stock_levels_calc`). They
need no database: every Frappe call is mocked, and the existing helpers that create the stock entries are replaced
by stand-ins so nothing is written.

The first half tests the plain functions with plain rows (Swahili product names are used throughout). The second
half tests the calls: who is let in, that the tiles are counted from the same rows as the list, that a cashier is
never given a cost figure, the 30-second cache, and that a write-off is refused without a reason before anything is
read or written (acceptance check AC-27).
"""

import ast
import inspect
import sys
import types
import unittest
from datetime import date, datetime, time, timedelta
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import stock_levels_api as api
from murzak_pos.api import stock_levels_calc as calc

TODAY = date(2026, 10, 13)
STORE = "Duka Kuu - SA"
STORE_2 = "Duka la Pili - SA"

# Made-up products. Maziwa is low in the main store; Sukari sold last week and is gone; Mkate has no stock and no
# sales; Sabuni is low in the main store but plentiful in the second store.
BINS = [
	{
		"item_code": "MLK",
		"item_name": "Maziwa 500ml",
		"item_group": "Maziwa",
		"stock_uom": "Nos",
		"warehouse": STORE,
		"actual_qty": 4,
		"stock_value": 240,
	},
	{
		"item_code": "UNG",
		"item_name": "Unga wa Ngano 2kg",
		"item_group": "Unga",
		"stock_uom": "Nos",
		"warehouse": STORE,
		"actual_qty": 40,
		"stock_value": 4000,
	},
	{
		"item_code": "SUK",
		"item_name": "Sukari 1kg",
		"item_group": "Sukari",
		"stock_uom": "Kg",
		"warehouse": STORE,
		"actual_qty": 0,
		"stock_value": 0,
	},
	{
		"item_code": "MCH",
		"item_name": "Mchele Pishori",
		"item_group": "Nafaka",
		"stock_uom": "Kg",
		"warehouse": STORE,
		"actual_qty": 25.5,
		"stock_value": 2550,
	},
	{
		"item_code": "MKT",
		"item_name": "Mkate wa Boflo",
		"item_group": "Mikate",
		"stock_uom": "Nos",
		"warehouse": STORE,
		"actual_qty": 0,
		"stock_value": 0,
	},
	{
		"item_code": "SAB",
		"item_name": "Sabuni ya Kipande",
		"item_group": "Sabuni",
		"stock_uom": "Nos",
		"warehouse": STORE,
		"actual_qty": 3,
		"stock_value": 90,
	},
	{
		"item_code": "SAB",
		"item_name": "Sabuni ya Kipande",
		"item_group": "Sabuni",
		"stock_uom": "Nos",
		"warehouse": STORE_2,
		"actual_qty": 20,
		"stock_value": 600,
	},
]
LOW = [
	{
		"item_code": "MLK",
		"item_name": "Maziwa 500ml",
		"warehouse": STORE,
		"actual_qty": 4,
		"reorder_level": 10,
	},
	{
		"item_code": "SAB",
		"item_name": "Sabuni ya Kipande",
		"warehouse": STORE,
		"actual_qty": 3,
		"reorder_level": 5,
	},
	{"item_code": "SUK", "item_name": "Sukari 1kg", "warehouse": STORE, "actual_qty": 0, "reorder_level": 5},
]
SOLD = [
	{"item_code": "SUK", "item_name": "Sukari 1kg"},
	{"item_code": "MLK", "item_name": "Maziwa 500ml"},
	{"item_code": "UNG", "item_name": "Unga wa Ngano 2kg"},
]


def rows():
	return calc.build_stock_rows(BINS, LOW, SOLD)


def by_code(result):
	return {r["item_code"]: r for r in result}


class TestNumbersAndText(unittest.TestCase):
	def test_quantities_keep_three_decimals_and_money_two(self):
		self.assertEqual(calc.qty(2.12345), 2.123)
		self.assertEqual(calc.money(10.005), 10.01 if round(10.005, 2) == 10.01 else 10.0)
		self.assertEqual(calc.money("1200.5"), 1200.5)

	def test_nothing_that_is_not_a_number_becomes_a_number(self):
		for bad in (None, "", "abc", float("nan"), float("inf")):
			with self.subTest(value=bad):
				self.assertEqual(calc.num(bad), 0.0)

	def test_minus_zero_is_zero(self):
		self.assertEqual(str(calc.qty(-0.0001)), "0.0")

	def test_text_is_one_tidy_line_cut_to_the_limit(self):
		self.assertEqual(calc.clean_text("  two   crates \n fell  "), "two crates fell")
		self.assertEqual(len(calc.clean_text("x" * 500)), calc.MAX_NOTE)
		self.assertEqual(calc.clean_text(None), "")

	def test_a_search_for_percent_or_underscore_is_taken_literally(self):
		self.assertEqual(calc.like_pattern("100%_"), "%100\\%\\_%")

	def test_times_from_the_database_read_as_hours_and_minutes(self):
		self.assertEqual(calc.format_time(timedelta(hours=14, minutes=5, seconds=9)), "14:05")
		self.assertEqual(calc.format_time(time(9, 7)), "09:07")
		self.assertEqual(calc.format_time("8:30:00"), "08:30")
		self.assertEqual(calc.format_time(None), "")

	def test_a_message_from_another_part_of_the_app_loses_its_tags_and_lead_in(self):
		self.assertEqual(
			calc.plain_message("Validation error: <b>Item</b> X is <br>not stock &amp; sold"),
			"Item X is not stock & sold",
		)
		self.assertEqual(calc.plain_message(None), "")


class TestTheRequest(unittest.TestCase):
	def test_the_default_tab_is_the_overview_and_a_bad_tab_is_refused(self):
		self.assertEqual(calc.clean_tab(None), "overview")
		self.assertEqual(calc.clean_tab(" Low "), "low")
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_tab("ledger")
		self.assertEqual(caught.exception.code, "bad_tab")

	def test_paging_is_page_one_and_twenty_rows_when_missing_or_wrong(self):
		self.assertEqual(calc.clean_paging(None, None), (1, 20))
		self.assertEqual(calc.clean_paging("x", "y"), (1, 20))
		self.assertEqual(calc.clean_paging(0, 0), (1, 20))
		self.assertEqual(calc.clean_paging(3, 50), (3, 50))

	def test_rows_per_page_are_capped_at_one_hundred(self):
		self.assertEqual(calc.clean_paging(1, 5000)[1], 100)

	def test_a_status_must_suit_the_tab(self):
		self.assertEqual(calc.clean_status("overview", "LOW"), "low")
		self.assertEqual(calc.clean_status("movements", "draft"), "draft")
		self.assertEqual(calc.clean_status("expiry", "expired"), "expired")
		self.assertIsNone(calc.clean_status("overview", ""))
		for tab, status in (("overview", "draft"), ("movements", "low"), ("expiry", "ok")):
			with self.subTest(tab=tab), self.assertRaises(calc.StockError):
				calc.clean_status(tab, status)

	def test_the_kind_of_movement(self):
		self.assertIsNone(calc.clean_kind(None))
		self.assertIsNone(calc.clean_kind("all"))
		self.assertEqual(calc.clean_kind("Receipt"), "receipt")
		with self.assertRaises(calc.StockError):
			calc.clean_kind("sale")


class TestTheMovementWindow(unittest.TestCase):
	def test_with_no_dates_it_is_the_last_thirty_days(self):
		self.assertEqual(calc.movement_window(None, None, TODAY), (date(2026, 9, 14), TODAY))

	def test_only_a_start_runs_thirty_days_on(self):
		self.assertEqual(
			calc.movement_window("2026-10-01", None, TODAY), (date(2026, 10, 1), date(2026, 10, 13))
		)
		self.assertEqual(
			calc.movement_window("2026-08-01", None, TODAY), (date(2026, 8, 1), date(2026, 8, 30))
		)

	def test_only_an_end_looks_thirty_days_back(self):
		self.assertEqual(
			calc.movement_window(None, "2026-10-10", TODAY), (date(2026, 9, 11), date(2026, 10, 10))
		)

	def test_bad_dates_a_wrong_order_and_a_long_span_are_refused_with_a_reason(self):
		cases = [
			(("13/10/2026", None), "bad_date"),
			(("2026-13-45", None), "bad_date"),
			(("2026-10-12", "2026-10-01"), "date_order"),
			(("2024-01-01", "2026-10-01"), "too_long"),
		]
		for args, code in cases:
			with self.subTest(args=args), self.assertRaises(calc.StockError) as caught:
				calc.movement_window(args[0], args[1], TODAY)
			self.assertEqual(caught.exception.code, code)


class TestPlaces(unittest.TestCase):
	WAREHOUSES: ClassVar[list] = [
		{"name": "Duka Kuu - SA", "warehouse_name": "Duka Kuu", "disabled": 0, "is_group": 0},
		{"name": "All Warehouses - SA", "warehouse_name": "All Warehouses", "disabled": 0, "is_group": 1},
		{"name": "Work In Progress - SA", "warehouse_name": "Work In Progress", "disabled": 0, "is_group": 0},
		{"name": "Finished Goods - SA", "warehouse_name": "Finished Goods", "disabled": 0, "is_group": 0},
		{"name": "Goods In Transit - SA", "warehouse_name": "Goods In Transit", "disabled": 0, "is_group": 0},
		{"name": "Zamani - SA", "warehouse_name": "Zamani", "disabled": 1, "is_group": 0},
	]

	def test_a_new_business_has_one_real_location_not_six(self):
		self.assertEqual(calc.selling_locations(self.WAREHOUSES), ["Duka Kuu - SA"])

	def test_a_second_store_makes_two(self):
		more = [
			*self.WAREHOUSES,
			{"name": STORE_2, "warehouse_name": "Duka la Pili", "disabled": 0, "is_group": 0},
		]
		self.assertEqual(len(calc.selling_locations(more)), 2)

	def test_the_store_for_a_change_is_the_one_asked_for_or_the_only_one(self):
		self.assertEqual(calc.pick_warehouse("B", ["A", "B"]), "B")
		self.assertEqual(calc.pick_warehouse(None, ["A"]), "A")

	def test_with_two_stores_and_none_chosen_the_person_must_choose(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.pick_warehouse(None, ["A", "B"])
		self.assertEqual(caught.exception.code, "need_store")

	def test_with_no_store_there_is_nowhere_to_put_stock(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.pick_warehouse(None, [])
		self.assertEqual(caught.exception.code, "no_store")

	def test_a_shop_tracks_expiry_when_a_batch_or_a_record_carries_a_date(self):
		self.assertTrue(calc.tracks_expiry(2, 0))
		self.assertTrue(calc.tracks_expiry(0, 1))
		self.assertFalse(calc.tracks_expiry(0, 0))
		self.assertFalse(calc.tracks_expiry(None, None))


class TestTheStockList(unittest.TestCase):
	def test_one_row_per_product_and_a_product_in_two_stores_is_added_up(self):
		found = by_code(rows())
		self.assertEqual(found["SAB"]["qty"], 23)
		self.assertEqual(found["SAB"]["value"], 690)
		self.assertEqual(found["SAB"]["stores"], 2)

	def test_a_product_with_no_stock_that_does_not_sell_is_not_listed(self):
		self.assertNotIn("MKT", by_code(rows()))

	def test_a_product_that_sold_recently_and_has_none_left_is_out(self):
		found = by_code(rows())
		self.assertEqual(found["SUK"]["level"], "out")
		self.assertEqual(found["SUK"]["qty"], 0)

	def test_a_sold_product_with_no_stock_record_at_all_is_out_and_named_from_the_item(self):
		sold = [*SOLD, {"item_code": "NEW", "item_name": "Bidhaa Mpya"}]
		info = {"NEW": {"item_name": "Bidhaa Mpya", "item_group": "Mpya", "stock_uom": "Nos"}}
		found = by_code(calc.build_stock_rows(BINS, LOW, sold, info))
		self.assertEqual(
			(found["NEW"]["level"], found["NEW"]["category"], found["NEW"]["unit"]), ("out", "Mpya", "Nos")
		)

	def test_low_follows_homes_rule_some_stock_at_or_below_the_warning_level(self):
		found = by_code(rows())
		self.assertEqual(found["MLK"]["level"], "low")
		self.assertEqual(found["MLK"]["warning_level"], 10)

	def test_a_product_low_in_one_store_is_low_even_when_another_store_has_plenty(self):
		self.assertEqual(by_code(rows())["SAB"]["level"], "low")

	def test_a_product_at_exactly_its_warning_level_is_low(self):
		low = [
			{
				"item_code": "UNG",
				"item_name": "Unga",
				"warehouse": STORE,
				"actual_qty": 40,
				"reorder_level": 40,
			}
		]
		self.assertEqual(by_code(calc.build_stock_rows(BINS, low, SOLD))["UNG"]["level"], "low")

	def test_a_product_with_no_warning_level_is_never_low(self):
		low = [
			{"item_code": "UNG", "item_name": "Unga", "warehouse": STORE, "actual_qty": 1, "reorder_level": 0}
		]
		self.assertEqual(by_code(calc.build_stock_rows(BINS, low, SOLD))["UNG"]["level"], "ok")

	def test_a_product_with_stock_above_its_level_is_in_stock(self):
		found = by_code(rows())
		self.assertEqual((found["UNG"]["level"], found["UNG"]["warning_level"]), ("ok", None))
		self.assertEqual(found["MCH"]["level"], "ok")

	def test_negative_stock_is_out(self):
		bins = [{**BINS[0], "item_code": "NEG", "actual_qty": -2, "stock_value": -120}]
		found = by_code(calc.build_stock_rows(bins, [], []))
		self.assertEqual(found["NEG"]["level"], "out")

	def test_the_out_count_is_the_out_count_on_home(self):
		from murzak_pos.api import overview_calc

		quantities = {b["item_code"]: 0.0 for b in BINS}
		for b in BINS:
			quantities[b["item_code"]] += b["actual_qty"]
		home_out = {i["item_code"] for i in overview_calc.out_of_stock_items(SOLD, quantities)}
		mine = {r["item_code"] for r in rows() if r["level"] == "out"}
		self.assertEqual(mine, home_out)

	def test_the_low_count_is_the_low_count_on_home(self):
		from murzak_pos.api import overview_calc

		home_low = {i["item_code"] for i in overview_calc.running_low_items(LOW)}
		self.assertEqual({r["item_code"] for r in rows() if r["level"] == "low"}, home_low)

	def test_rows_are_in_name_order_with_swahili_names(self):
		names = [r["item_name"] for r in rows()]
		self.assertEqual(names, sorted(names, key=str.lower))
		self.assertEqual(names[0], "Maziwa 500ml")

	def test_search_finds_a_name_or_a_code_in_any_case(self):
		self.assertEqual([r["item_code"] for r in calc.filter_stock_rows(rows(), search="UNGA")], ["UNG"])
		self.assertEqual([r["item_code"] for r in calc.filter_stock_rows(rows(), search="mch")], ["MCH"])

	def test_a_category_and_a_level_narrow_the_list(self):
		self.assertEqual([r["item_code"] for r in calc.filter_stock_rows(rows(), category="Nafaka")], ["MCH"])
		self.assertEqual(
			{r["item_code"] for r in calc.filter_stock_rows(rows(), status="low")}, {"MLK", "SAB"}
		)

	def test_the_low_stock_tab_lists_out_first_then_low_least_first(self):
		listed = calc.low_stock_rows(rows())
		# Sukari is out; Maziwa (4 left) is lower than Sabuni (23 in all stores)
		self.assertEqual([r["item_code"] for r in listed], ["SUK", "MLK", "SAB"])

	def test_the_categories_for_the_filter_are_sorted_and_unique(self):
		self.assertEqual(calc.categories(rows()), ["Maziwa", "Nafaka", "Sabuni", "Sukari", "Unga"])


class TestTheTilesMatchTheList(unittest.TestCase):
	def test_the_tiles_count_the_rows_of_the_list(self):
		listed = rows()
		tiles = calc.tiles(listed, True)
		self.assertEqual(tiles["items"], len(listed))
		self.assertEqual(tiles["low"], len(calc.filter_stock_rows(listed, status="low")))
		self.assertEqual(tiles["out_of_stock"], len(calc.filter_stock_rows(listed, status="out")))

	def test_the_stock_value_is_the_sum_of_the_values_in_the_list(self):
		self.assertEqual(calc.tiles(rows(), True)["stock_value"], sum(r["value"] for r in rows()))
		self.assertEqual(calc.tiles(rows(), True)["stock_value"], 7480.0)

	def test_after_a_search_the_tiles_follow_the_narrower_list(self):
		scoped = calc.filter_stock_rows(rows(), search="sabuni")
		self.assertEqual(
			calc.tiles(scoped, True), {"items": 1, "low": 1, "out_of_stock": 0, "stock_value": 690.0}
		)

	def test_every_tile_equals_the_list_for_every_search(self):
		for term in ("", "a", "u", "zzz", "kg", "maziwa"):
			scoped = calc.filter_stock_rows(rows(), search=term)
			tiles = calc.tiles(scoped, True)
			with self.subTest(term=term):
				self.assertEqual(tiles["items"], len(scoped))
				self.assertEqual(
					tiles["low"] + tiles["out_of_stock"] + len(calc.filter_stock_rows(scoped, status="ok")),
					tiles["items"],
				)

	def test_a_person_who_may_not_see_cost_gets_no_stock_value_key(self):
		self.assertNotIn("stock_value", calc.tiles(rows(), False))

	def test_an_empty_list_has_zero_tiles(self):
		self.assertEqual(calc.tiles([], True), {"items": 0, "low": 0, "out_of_stock": 0, "stock_value": 0.0})

	def test_strip_costs_removes_value_from_every_row_and_changes_nothing_else(self):
		stripped = calc.strip_costs(rows())
		self.assertTrue(all("value" not in r for r in stripped))
		self.assertEqual([r["qty"] for r in stripped], [r["qty"] for r in rows()])
		self.assertTrue(all("value" in r for r in rows()))


class TestPaging(unittest.TestCase):
	def test_page_info_counts_pages_and_is_never_zero_pages(self):
		self.assertEqual(calc.page_info(45, 1, 20), {"total": 45, "page": 1, "page_size": 20, "pages": 3})
		self.assertEqual(calc.page_info(0, 1, 20)["pages"], 1)

	def test_a_page_is_its_slice_and_a_page_past_the_end_is_empty(self):
		data = list(range(45))
		self.assertEqual(len(calc.page_of(data, 1, 20)), 20)
		self.assertEqual(calc.page_of(data, 3, 20), [40, 41, 42, 43, 44])
		self.assertEqual(calc.page_of(data, 9, 20), [])


class TestMovements(unittest.TestCase):
	def test_stock_entries_read_by_their_purpose(self):
		kind = calc.movement_kind
		self.assertEqual(kind("Stock Entry", "Material Receipt", 5), "stock_in")
		self.assertEqual(kind("Stock Entry", "Material Issue", -2), "stock_out")
		self.assertEqual(kind("Stock Entry", "Material Transfer", -2), "transfer")
		self.assertEqual(kind("Stock Entry", "Manufacture", 2), "made")
		self.assertEqual(kind("Stock Entry", "", 2), "entry")

	def test_sales_purchases_and_counts(self):
		kind = calc.movement_kind
		self.assertEqual(kind("POS Invoice", "", -1), "sale")
		self.assertEqual(kind("Delivery Note", "", -1), "sale")
		self.assertEqual(kind("Sales Invoice", "", 1), "sale_return")
		self.assertEqual(kind("Purchase Receipt", "", 10), "purchase")
		self.assertEqual(kind("Purchase Invoice", "", -3), "purchase_return")
		self.assertEqual(kind("Stock Reconciliation", "", 4), "count")
		self.assertEqual(kind("Something Else", "", 4), "other")

	def test_status_is_draft_submitted_or_cancelled(self):
		self.assertEqual(calc.movement_status(0, 0), "draft")
		self.assertEqual(calc.movement_status(1, 0), "submitted")
		self.assertEqual(calc.movement_status(1, 1), "cancelled")
		self.assertEqual(calc.movement_status(2, 0), "cancelled")

	def test_a_ledger_line_is_shaped_for_the_page(self):
		raw = {
			"source": "ledger",
			"id": "SLE-1",
			"posting_date": date(2026, 10, 12),
			"posting_time": timedelta(hours=9, minutes=10),
			"item_code": "MLK",
			"item_name": "Maziwa 500ml",
			"warehouse": STORE,
			"voucher_type": "Stock Entry",
			"voucher_no": "MAT-STE-2026-00004",
			"qty_change": 12,
			"qty_after": 16,
			"is_cancelled": 0,
			"purpose": "Material Receipt",
			"remarks": "Stock in: from the depot",
		}
		row = calc.shape_movement(raw)
		self.assertEqual(
			(row["date"], row["time"], row["kind"], row["status"], row["qty_change"], row["qty_after"]),
			("2026-10-12", "09:10", "stock_in", "submitted", 12.0, 16.0),
		)
		self.assertEqual(row["entry"], "MAT-STE-2026-00004")
		self.assertEqual(row["id"], "ledger:SLE-1")

	def test_a_draft_entry_keeps_its_status_and_has_no_quantity_after(self):
		raw = {
			"source": "entry",
			"id": "D1",
			"posting_date": "2026-10-13",
			"posting_time": "10:00:00",
			"item_code": "UNG",
			"voucher_type": "Stock Entry",
			"voucher_no": "MAT-STE-9",
			"qty_change": -3,
			"qty_after": None,
			"docstatus": 0,
			"purpose": "Material Issue",
			"remarks": "",
		}
		row = calc.shape_movement(raw)
		self.assertEqual(
			(row["status"], row["kind"], row["qty_after"], row["qty_change"]),
			("draft", "stock_out", None, -3.0),
		)
		self.assertEqual(row["entry"], "MAT-STE-9")

	def test_a_cancelled_entry_says_cancelled(self):
		raw = {
			"source": "ledger",
			"id": "S",
			"voucher_type": "Stock Entry",
			"voucher_no": "E",
			"qty_change": 1,
			"is_cancelled": 1,
			"purpose": "Material Receipt",
		}
		self.assertEqual(calc.shape_movement(raw)["status"], "cancelled")

	def test_a_sale_has_no_entry_to_open(self):
		raw = {
			"source": "ledger",
			"id": "S",
			"voucher_type": "POS Invoice",
			"voucher_no": "ACC-PSINV-1",
			"qty_change": -1,
		}
		self.assertIsNone(calc.shape_movement(raw)["entry"])

	def test_the_reason_of_a_write_off_is_read_back_from_the_remarks(self):
		for key in calc.WRITE_OFF_REASONS:
			remark = calc.reason_remark(key, "two crates")
			with self.subTest(reason=key):
				self.assertEqual(calc.reason_of(remark), key)
		self.assertIsNone(calc.reason_of("Stock in: depot"))
		self.assertIsNone(calc.reason_of(None))

	def test_the_remark_has_the_note_only_when_there_is_one(self):
		self.assertEqual(calc.reason_remark("damaged", ""), "Write-off (Damaged)")
		self.assertEqual(
			calc.reason_remark("used_in_shop", "for the tea"), "Write-off (Used in the shop): for the tea"
		)

	def test_the_query_joins_ledger_lines_and_draft_entries(self):
		count_sql, page_sql, values = calc.movements_query("Shop A", date(2026, 9, 14), TODAY)
		self.assertIn("tabStock Ledger Entry", page_sql)
		self.assertIn("tabStock Entry Detail", page_sql)
		self.assertIn("UNION ALL", page_sql)
		self.assertIn("LIMIT %(limit)s OFFSET %(offset)s", page_sql)
		self.assertNotIn("LIMIT", count_sql)
		self.assertEqual(values["company"], "Shop A")

	def test_the_ledger_kind_leaves_drafts_out(self):
		_c, page_sql, _v = calc.movements_query("Shop A", TODAY, TODAY, kind="ledger")
		self.assertNotIn("tabStock Entry Detail", page_sql)

	def test_draft_status_leaves_the_ledger_out_and_cancelled_leaves_drafts_out(self):
		_c, drafts, _v = calc.movements_query("Shop A", TODAY, TODAY, status="draft")
		self.assertNotIn("tabStock Ledger Entry", drafts)
		_c, cancelled, _v = calc.movements_query("Shop A", TODAY, TODAY, status="cancelled")
		self.assertNotIn("tabStock Entry Detail", cancelled)
		self.assertIn("sle.is_cancelled = 1", cancelled)

	def test_receipts_and_issues_filter_on_the_purpose(self):
		_c, _p, values = calc.movements_query("Shop A", TODAY, TODAY, kind="receipt")
		self.assertEqual(values["purpose"], "Material Receipt")
		_c, _p, values = calc.movements_query("Shop A", TODAY, TODAY, kind="issue")
		self.assertEqual(values["purpose"], "Material Issue")

	def test_a_store_and_a_search_become_values_never_text_in_the_sql(self):
		_c, page_sql, values = calc.movements_query(
			"Shop A", TODAY, TODAY, warehouse=STORE, search="maziwa'; DROP"
		)
		self.assertNotIn("DROP", page_sql)
		self.assertEqual(values["warehouse"], STORE)
		self.assertIn("DROP", values["like"])

	def test_asking_for_nothing_possible_still_gives_valid_sql(self):
		# draft entries that are also cancelled cannot exist; the page asks for a status that leaves no branch
		count_sql, _p, _v = calc.movements_query("Shop A", TODAY, TODAY, kind="ledger", status="draft")
		self.assertIn("WHERE 1 = 0", count_sql)


class TestExpiry(unittest.TestCase):
	RAW: ClassVar[dict] = {
		"item_code": "DWA",
		"item_name": "Dawa ya Kikohozi",
		"warehouse": STORE,
		"batch_no": "B-12",
		"qty": 8,
		"expiry_date": date(2026, 10, 20),
		"status": "Expires within 7 days",
		"days_left": 7,
		"stock_uom": "Nos",
		"stock_value": 960,
	}

	def test_a_line_is_shaped_with_its_urgency_as_a_key(self):
		row = calc.shape_expiry(self.RAW, True)
		self.assertEqual(
			(row["state"], row["expiry_date"], row["days_left"], row["value"]),
			("critical", "2026-10-20", 7, 960.0),
		)
		self.assertEqual(calc.shape_expiry({**self.RAW, "status": "Expired"}, True)["state"], "expired")
		self.assertEqual(calc.shape_expiry({**self.RAW, "status": "Expiring soon"}, True)["state"], "soon")

	def test_no_value_without_cost_permission(self):
		self.assertNotIn("value", calc.shape_expiry(self.RAW, False))

	def test_filter_by_search_and_urgency(self):
		listed = [
			calc.shape_expiry(self.RAW, True),
			calc.shape_expiry(
				{**self.RAW, "item_code": "MLK", "item_name": "Maziwa", "batch_no": "", "status": "Expired"},
				True,
			),
		]
		self.assertEqual(len(calc.filter_expiry_rows(listed, search="dawa")), 1)
		self.assertEqual(len(calc.filter_expiry_rows(listed, search="b-12")), 1)
		self.assertEqual([r["item_code"] for r in calc.filter_expiry_rows(listed, status="expired")], ["MLK"])


class TestWriteRules(unittest.TestCase):
	def test_a_quantity_must_be_above_zero(self):
		self.assertEqual(calc.clean_quantity("2.5"), 2.5)
		self.assertEqual(calc.clean_quantity("1,200"), 1200.0)
		self.assertEqual(calc.clean_quantity(3), 3.0)
		for bad in (0, -1, "0", "", None, "abc", True, float("nan"), float("inf"), 0.0001, 10**9):
			with self.subTest(value=bad), self.assertRaises(calc.StockError) as caught:
				calc.clean_quantity(bad)
			self.assertEqual(caught.exception.code, "bad_qty")

	def test_a_product_must_be_named(self):
		self.assertEqual(calc.clean_item(" MLK "), "MLK")
		for bad in (None, "", "   "):
			with self.subTest(value=bad), self.assertRaises(calc.StockError):
				calc.clean_item(bad)

	def test_a_write_off_without_a_reason_is_refused(self):
		for blank in (None, "", "   "):
			with self.subTest(reason=blank), self.assertRaises(calc.StockError) as caught:
				calc.clean_reason(blank, "a note")
			self.assertEqual(caught.exception.code, "need_reason")

	def test_a_reason_must_be_on_the_list(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_reason("stolen", "")
		self.assertEqual(caught.exception.code, "bad_reason")

	def test_every_listed_reason_is_accepted(self):
		for key in ("damaged", "expired", "lost", "used_in_shop"):
			self.assertEqual(calc.clean_reason(key, None), (key, ""))
		self.assertEqual(calc.clean_reason(" Damaged ", " two  crates "), ("damaged", "two crates"))

	def test_other_needs_a_note(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_reason("other", "  ")
		self.assertEqual(caught.exception.code, "need_note")
		self.assertEqual(calc.clean_reason("other", "gift to the school"), ("other", "gift to the school"))

	def test_the_reasons_are_the_five_in_the_spec(self):
		self.assertEqual(
			list(calc.WRITE_OFF_REASONS.values()), ["Damaged", "Expired", "Lost", "Used in the shop", "Other"]
		)

	def test_the_stock_in_remark(self):
		self.assertEqual(calc.receipt_remark("from the depot"), "Stock in: from the depot")
		self.assertEqual(calc.receipt_remark(""), "")


class TestCounting(unittest.TestCase):
	def test_a_sheet_lists_the_store_by_category_then_name(self):
		sheet, cut = calc.sheet_rows(BINS[:6], "all")
		self.assertFalse(cut)
		self.assertEqual(
			[r["category"] for r in sheet], sorted((r["category"] for r in sheet), key=str.lower)
		)
		self.assertEqual(by_code(sheet)["MCH"]["expected"], 25.5)

	def test_a_sheet_for_a_category_has_only_that_category(self):
		sheet, _cut = calc.sheet_rows(BINS, "category", "Nafaka")
		self.assertEqual([r["item_code"] for r in sheet], ["MCH"])

	def test_a_category_sheet_needs_a_category_and_the_scope_must_be_known(self):
		with self.assertRaises(calc.StockError) as caught:
			calc.sheet_rows(BINS, "category", None)
		self.assertEqual(caught.exception.code, "need_category")
		with self.assertRaises(calc.StockError) as caught:
			calc.sheet_rows(BINS, "shelf")
		self.assertEqual(caught.exception.code, "bad_scope")

	def test_a_very_long_sheet_is_cut_and_says_so(self):
		many = [
			{
				"item_code": f"P{n}",
				"item_name": f"Bidhaa {n}",
				"item_group": "G",
				"stock_uom": "Nos",
				"actual_qty": 1,
			}
			for n in range(calc.MAX_COUNT_ROWS + 5)
		]
		sheet, cut = calc.sheet_rows(many, "all")
		self.assertEqual((len(sheet), cut), (calc.MAX_COUNT_ROWS, True))

	def test_counted_quantities_are_checked(self):
		self.assertEqual(
			calc.clean_counts([{"item_code": "MLK", "counted": "4"}, {"item_code": "UNG", "counted": 0}]),
			[{"item_code": "MLK", "counted": 4.0}, {"item_code": "UNG", "counted": 0.0}],
		)
		self.assertEqual(calc.clean_counts('[{"item_code": "MLK", "counted": 2.5}]')[0]["counted"], 2.5)

	def test_bad_counts_are_refused_with_a_reason(self):
		cases = [
			([], "empty_count"),
			("[]", "empty_count"),
			("not json", "bad_count"),
			([{"item_code": "MLK", "counted": -1}], "bad_count"),
			([{"item_code": "MLK", "counted": ""}], "bad_count"),
			([{"item_code": "MLK", "counted": None}], "bad_count"),
			([{"item_code": "MLK", "counted": "x"}], "bad_count"),
			(["MLK"], "bad_count"),
			([{"item_code": "MLK", "counted": 1}, {"item_code": "MLK", "counted": 2}], "duplicate_item"),
			([{"item_code": "", "counted": 1}], "need_item"),
		]
		for counts, code in cases:
			with self.subTest(counts=counts), self.assertRaises(calc.StockError) as caught:
				calc.clean_counts(counts)
			self.assertEqual(caught.exception.code, code)

	def test_too_many_products_in_one_count_are_refused(self):
		counts = [{"item_code": f"P{n}", "counted": 1} for n in range(calc.MAX_COUNT_ROWS + 1)]
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_counts(counts)
		self.assertEqual(caught.exception.code, "too_many")

	def test_the_difference_is_counted_minus_expected_with_a_word(self):
		found = calc.count_differences(
			[
				{"item_code": "MLK", "counted": 3},
				{"item_code": "UNG", "counted": 42},
				{"item_code": "MCH", "counted": 25.5},
			],
			{"MLK": 4, "UNG": 40, "MCH": 25.5},
		)
		states = {r["item_code"]: (r["expected"], r["counted"], r["difference"], r["state"]) for r in found}
		self.assertEqual(states["MLK"], (4.0, 3.0, -1.0, "short"))
		self.assertEqual(states["UNG"], (40.0, 42.0, 2.0, "over"))
		self.assertEqual(states["MCH"], (25.5, 25.5, 0.0, "matches"))

	def test_a_product_the_system_does_not_know_is_expected_at_zero(self):
		found = calc.count_differences([{"item_code": "NEW", "counted": 5}], {})
		self.assertEqual((found[0]["expected"], found[0]["state"]), (0.0, "over"))

	def test_decimal_noise_does_not_make_a_difference(self):
		self.assertEqual(calc.difference_state(0.0004), "matches")
		self.assertEqual(calc.difference_state(-0.002), "short")

	def test_totals(self):
		found = calc.count_differences(
			[
				{"item_code": "A", "counted": 3},
				{"item_code": "B", "counted": 9},
				{"item_code": "C", "counted": 5},
			],
			{"A": 4, "B": 5, "C": 5},
		)
		self.assertEqual(
			calc.count_totals(found), {"lines": 3, "short": 1, "over": 1, "matches": 1, "net_difference": 3.0}
		)


# --- the calls ---------------------------------------------------------------------------------------------


def keys_of(value, found=None):
	"""Every key anywhere in a nested answer."""
	found = set() if found is None else found
	if isinstance(value, dict):
		for key, item in value.items():
			found.add(key)
			keys_of(item, found)
	elif isinstance(value, list):
		for item in value:
			keys_of(item, found)
	return found


class StockCase(unittest.TestCase):
	"""A signed-in user, every database read replaced by fixed data, and a dictionary standing in for the cache."""

	def setUp(self):
		self.cache = {}
		self.cleared = []
		self.reads = {}
		self.helpers = types.SimpleNamespace(
			create_material_receipt=MagicMock(
				return_value={"success": True, "data": {"name": "MAT-STE-2026-00010"}}
			),
			create_material_issue=MagicMock(
				return_value={"success": True, "data": {"name": "MAT-STE-2026-00011"}}
			),
			create_stock_reconciliation=MagicMock(
				return_value={"success": True, "data": {"name": "MAT-RECO-2026-00001"}}
			),
		)
		self.bin_quantities = {"MLK": 4.0, "UNG": 40.0}

	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, user="owner@shop-a.test", roles=("System Manager",), locations=(STORE,)):
		self.db = MagicMock()
		self.db.get_value.side_effect = self.get_value
		self.db.has_column.return_value = True
		self.db.sql.return_value = []
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start((frappe, "get_roles"), create=True, side_effect=lambda user=None: list(roles))
		self.start((frappe, "db"), new=self.db, create=True)
		self.start((frappe, "log_error"), create=True)
		self.start((frappe, "get_traceback"), create=True, return_value="traceback")
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "_today"), return_value=TODAY)
		self.start((api, "_cache_get"), side_effect=lambda key: self.cache.get(key))
		self.start((api, "_cache_set"), side_effect=self.cache.__setitem__)
		self.start((api, "_cache_clear"), side_effect=self.cleared.append)
		self.stub(
			"_bin_rows", lambda company, store: [b for b in BINS if not store or b["warehouse"] == store]
		)
		self.stub(
			"_low_rows", lambda company, store: [r for r in LOW if not store or r["warehouse"] == store]
		)
		self.stub("_sold_items", lambda company, store: list(SOLD))
		self.stub("_item_info", lambda codes: {})
		self.stub("_locations", lambda company: list(locations))
		self.stub("_features", lambda company: {"expiry": False, "locations": len(locations)})
		self.stub("_stock_of", lambda item, store: self.bin_quantities.get(item, 0.0))
		self.start("murzak_pos.api.access_control.frappe.db", new=self.db, create=True)
		modules = {"murzak_pos.api.inventory_api": self.helpers}
		patcher = patch.dict(sys.modules, modules)
		patcher.start()
		self.addCleanup(patcher.stop)

	def get_value(self, doctype, name=None, field=None, *args, **kwargs):
		if doctype == "Warehouse" and field == "company":
			return "Shop A"
		if field == "custom_company" or field == "company":
			return "Shop A"
		return None

	def stub(self, name, function):
		mock = self.start((api, name), side_effect=function)
		self.reads[name] = mock
		return mock

	def data(self, **kwargs):
		result = api.get_stock_levels(**kwargs)
		self.assertTrue(result["success"], result)
		return result["data"]


class TestTheOverview(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_the_overview_gives_the_list_and_four_tiles(self):
		data = self.data()
		self.assertEqual(data["tab"], "overview")
		self.assertEqual(data["tiles"], {"items": 5, "low": 2, "out_of_stock": 1, "stock_value": 7480.0})
		self.assertEqual(data["total"], 5)
		self.assertEqual(len(data["rows"]), 5)

	def test_the_tiles_equal_the_numbers_in_the_list(self):
		data = self.data(page_size=100)
		levels = [r["level"] for r in data["rows"]]
		self.assertEqual(data["tiles"]["items"], data["total"])
		self.assertEqual(data["tiles"]["low"], levels.count("low"))
		self.assertEqual(data["tiles"]["out_of_stock"], levels.count("out"))
		self.assertEqual(data["tiles"]["stock_value"], sum(r["value"] for r in data["rows"]))

	def test_a_tile_equals_its_filtered_list(self):
		overview = self.data()
		for status, tile in (("low", "low"), ("out", "out_of_stock")):
			listed = self.data(status=status)
			with self.subTest(status=status):
				self.assertEqual(listed["total"], overview["tiles"][tile])

	def test_a_search_narrows_the_list_and_the_tiles_together(self):
		data = self.data(search="sabuni")
		self.assertEqual((data["total"], data["tiles"]["items"], data["tiles"]["low"]), (1, 1, 1))

	def test_the_level_filter_does_not_change_the_tiles(self):
		self.assertEqual(self.data(status="low")["tiles"], self.data()["tiles"])

	def test_a_category_filter(self):
		self.assertEqual([r["item_code"] for r in self.data(category="Nafaka")["rows"]], ["MCH"])

	def test_a_store_filter_reads_only_that_store(self):
		self.stub("_sold_items", lambda company, store: [] if store == STORE_2 else list(SOLD))
		data = self.data(warehouse=STORE_2)
		self.assertEqual([r["item_code"] for r in data["rows"]], ["SAB"])
		self.assertEqual(self.reads["_bin_rows"].call_args.args, ("Shop A", STORE_2))
		self.assertEqual(data["rows"][0]["qty"], 20)

	def test_twenty_rows_to_a_page_with_an_exact_total(self):
		many = [
			{
				**BINS[1],
				"item_code": f"P{n:02d}",
				"item_name": f"Bidhaa {n:02d}",
				"actual_qty": 9,
				"stock_value": 90,
			}
			for n in range(45)
		]
		self.stub("_bin_rows", lambda company, store: many)
		self.stub("_low_rows", lambda company, store: [])
		self.stub("_sold_items", lambda company, store: [])
		first, third = self.data(), self.data(page=3)
		self.assertEqual(
			(len(first["rows"]), first["total"], first["pages"], first["page_size"]), (20, 45, 3, 20)
		)
		self.assertEqual(len(third["rows"]), 5)
		self.assertEqual(self.data(page=9)["rows"], [])

	def test_the_categories_for_the_filter_are_in_the_answer(self):
		self.assertIn("Nafaka", self.data()["categories"])

	def test_the_answer_says_what_the_shop_has_and_whether_this_person_may_change_stock(self):
		data = self.data()
		self.assertEqual(data["features"], {"expiry": False, "locations": 1})
		self.assertEqual(data["can"], {"change_stock": True})
		self.assertEqual(data["role"], "owner")

	def test_a_bad_tab_or_status_is_refused_in_words(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_stock_levels(tab="nonsense")
		with self.assertRaises(frappe.ValidationError):
			api.get_stock_levels(status="draft")

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user("Guest", roles=("Guest",))
		with self.assertRaises(frappe.AuthenticationError):
			api.get_stock_levels()


class TestTheLowStockTab(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_it_lists_what_needs_reordering_out_first(self):
		data = self.data(tab="low")
		self.assertEqual([r["level"] for r in data["rows"]], ["out", "low", "low"])
		self.assertEqual(data["total"], 3)
		self.assertNotIn("tiles", data)

	def test_it_can_be_narrowed_to_low_or_out(self):
		self.assertEqual(self.data(tab="low", status="out")["total"], 1)
		self.assertEqual(self.data(tab="low", status="low")["total"], 2)
		self.assertEqual(self.data(tab="low", status="ok")["total"], 0)

	def test_the_low_tab_total_is_the_low_and_out_tiles_together(self):
		tiles = self.data()["tiles"]
		self.assertEqual(self.data(tab="low")["total"], tiles["low"] + tiles["out_of_stock"])


class TestACashier(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user("till@shop-a.test", roles=("Sales User",))

	def test_no_cost_figure_is_anywhere_in_a_cashiers_answer(self):
		for tab in ("overview", "low"):
			with self.subTest(tab=tab):
				found = keys_of(self.data(tab=tab))
				self.assertNotIn("value", found)
				self.assertNotIn("stock_value", found)

	def test_the_tiles_still_count_but_have_no_value(self):
		tiles = self.data()["tiles"]
		self.assertEqual(tiles, {"items": 5, "low": 2, "out_of_stock": 1})

	def test_a_cashier_may_not_change_stock(self):
		self.assertFalse(self.data()["can"]["change_stock"])

	def test_an_owners_cached_answer_is_not_a_cashiers_answer(self):
		self.as_user()
		owners = self.data()
		self.assertIn("stock_value", owners["tiles"])
		self.as_user("till@shop-a.test", roles=("Sales User",))
		cashiers = self.data()
		self.assertTrue(cashiers["cached"])
		self.assertNotIn("stock_value", cashiers["tiles"])
		self.assertNotIn("value", keys_of(cashiers))

	def test_expiry_values_are_removed_for_a_cashier(self):
		self.stub("_expiry", lambda company, store, days: [calc.shape_expiry(TestExpiry.RAW, True)])
		found = keys_of(self.data(tab="expiry"))
		self.assertNotIn("value", found)

	def test_a_manager_sees_cost(self):
		self.as_user("boss@shop-a.test", roles=("Sales Manager",))
		self.assertIn("stock_value", self.data()["tiles"])


class TestTheCache(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def test_the_answer_is_kept_and_served_again_without_reading(self):
		first = self.data()
		reads = self.reads["_bin_rows"].call_count
		again = self.data()
		self.assertIs(first["cached"], False)
		self.assertIs(again["cached"], True)
		self.assertEqual(again["tiles"], first["tiles"])
		self.assertEqual(self.reads["_bin_rows"].call_count, reads)

	def test_filters_and_pages_are_cut_from_the_kept_list_not_read_again(self):
		self.data()
		reads = self.reads["_bin_rows"].call_count
		self.data(search="mchele")
		self.data(page=2)
		self.assertEqual(self.reads["_bin_rows"].call_count, reads)

	def test_the_key_differs_by_business_store_and_tab(self):
		keys = {
			api.cache_key("Shop A", None, "overview"),
			api.cache_key("Shop B", None, "overview"),
			api.cache_key("Shop A", STORE, "overview"),
			api.cache_key("Shop A", None, "low"),
			api.cache_key("Shop A", None, "expiry", "30"),
			api.cache_key("Shop A", None, "expiry", "60"),
		}
		self.assertEqual(len(keys), 6)

	def test_every_key_of_a_business_starts_the_same_so_one_clear_forgets_them_all(self):
		prefix = f"{api.CACHE_PREFIX}:Shop A:"
		for tab in ("overview", "low", "movements", "expiry"):
			self.assertTrue(api.cache_key("Shop A", STORE, tab, "x").startswith(prefix))

	def test_the_answer_is_kept_for_thirty_seconds(self):
		self.assertEqual(api.CACHE_SECONDS, 30)
		self.assertIn("expires_in_sec=CACHE_SECONDS", inspect.getsource(api))

	def test_a_failure_while_reading_is_not_kept(self):
		self.stub("_bin_rows", MagicMock(side_effect=RuntimeError("secret db detail")))
		with self.assertRaises(RuntimeError):
			api.get_stock_levels()
		self.assertEqual([k for k in self.cache if k.endswith(":overview")], [])


class TestMovementsTab(StockCase):
	RAW: ClassVar[dict] = {
		"source": "ledger",
		"id": "SLE-1",
		"posting_date": date(2026, 10, 12),
		"posting_time": timedelta(hours=9),
		"item_code": "MLK",
		"item_name": "Maziwa 500ml",
		"warehouse": STORE,
		"voucher_type": "Stock Entry",
		"voucher_no": "MAT-STE-1",
		"qty_change": 12,
		"qty_after": 16,
		"is_cancelled": 0,
		"purpose": "Material Receipt",
		"remarks": "",
	}
	DRAFT: ClassVar[dict] = {
		"source": "entry",
		"id": "D1",
		"posting_date": date(2026, 10, 13),
		"posting_time": "10:30:00",
		"item_code": "UNG",
		"item_name": "Unga wa Ngano 2kg",
		"warehouse": STORE,
		"voucher_type": "Stock Entry",
		"voucher_no": "MAT-STE-2",
		"qty_change": -3,
		"qty_after": None,
		"is_cancelled": 0,
		"docstatus": 0,
		"purpose": "Material Issue",
		"remarks": "Write-off (Lost)",
	}

	def setUp(self):
		super().setUp()
		self.as_user()
		self.answer_sql()

	def answer_sql(self):
		self.db.sql.side_effect = (
			lambda sql, values=None, as_dict=False: [{"n": 41}]
			if "COUNT(*)" in sql
			else [self.DRAFT, self.RAW]
		)

	def test_it_joins_ledger_lines_and_draft_entries_with_their_status(self):
		data = self.data(tab="movements")
		self.assertEqual(data["total"], 41)
		self.assertEqual([r["status"] for r in data["rows"]], ["draft", "submitted"])
		self.assertEqual([r["kind"] for r in data["rows"]], ["stock_out", "stock_in"])
		self.assertEqual(data["rows"][0]["reason"], "lost")
		self.assertEqual(data["rows"][0]["entry"], "MAT-STE-2")

	def test_the_page_is_asked_for_by_limit_and_offset(self):
		self.data(tab="movements", page=3)
		page_call = next(c for c in self.db.sql.call_args_list if "LIMIT" in c.args[0])
		self.assertEqual((page_call.args[1]["limit"], page_call.args[1]["offset"]), (20, 40))

	def test_with_no_dates_it_covers_the_last_thirty_days(self):
		data = self.data(tab="movements")
		self.assertEqual((data["from_date"], data["to_date"]), ("2026-09-14", "2026-10-13"))

	def test_filters_reach_the_query(self):
		self.data(
			tab="movements",
			search="maziwa",
			kind="receipt",
			status="submitted",
			warehouse=STORE,
			from_date="2026-10-01",
			to_date="2026-10-10",
		)
		values = self.db.sql.call_args_list[0].args[1]
		self.assertEqual(
			(values["warehouse"], values["purpose"], str(values["start"]), str(values["end"])),
			(STORE, "Material Receipt", "2026-10-01", "2026-10-10"),
		)
		self.assertIn("maziwa", values["like"])

	def test_a_bad_date_is_refused_in_words(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_stock_levels(tab="movements", from_date="soon")

	def test_movements_carry_no_cost_so_a_cashier_gets_the_same_rows(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.answer_sql()
		self.assertEqual(self.data(tab="movements")["total"], 41)


class TestExpiryTab(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()
		listed = [
			calc.shape_expiry(TestExpiry.RAW, True),
			calc.shape_expiry(
				{
					**TestExpiry.RAW,
					"item_code": "MLK",
					"item_name": "Maziwa",
					"batch_no": "B-9",
					"status": "Expired",
					"days_left": -2,
				},
				True,
			),
		]
		self.stub("_expiry", lambda company, store, days: list(listed))

	def test_it_lists_the_expiring_stock_with_value_for_an_owner(self):
		data = self.data(tab="expiry")
		self.assertEqual((data["total"], data["days"]), (2, 30))
		self.assertEqual(data["rows"][0]["value"], 960.0)

	def test_it_can_be_narrowed_to_expired(self):
		self.assertEqual([r["state"] for r in self.data(tab="expiry", status="expired")["rows"]], ["expired"])

	def test_the_window_is_kept_within_one_to_365_days(self):
		self.assertEqual(self.data(tab="expiry", days=9999)["days"], 365)
		self.assertEqual(self.data(tab="expiry", days="x")["days"], 30)

	def test_the_features_say_whether_the_shop_tracks_expiry(self):
		self.stub("_features", lambda company: {"expiry": True, "locations": 2})
		self.assertEqual(self.data(tab="expiry")["features"], {"expiry": True, "locations": 2})


class TestTheFeatures(unittest.TestCase):
	def test_features_come_from_the_database_and_are_kept(self):
		cache = {}
		with (
			patch.object(api, "_cache_get", side_effect=cache.get),
			patch.object(api, "_cache_set", side_effect=cache.__setitem__),
			patch.object(api, "_tracks_expiry", return_value=True) as expiry,
			patch.object(api, "_locations", return_value=["A", "B"]),
		):
			self.assertEqual(api._features("Shop A"), {"expiry": True, "locations": 2})
			self.assertEqual(api._features("Shop A"), {"expiry": True, "locations": 2})
		self.assertEqual(expiry.call_count, 1)

	def test_a_failure_gives_plain_defaults_and_does_not_fail_the_page(self):
		with (
			patch.object(api, "_cache_get", return_value=None),
			patch.object(api, "_cache_set"),
			patch.object(api, "_tracks_expiry", side_effect=RuntimeError("no table")),
			patch.object(frappe, "log_error", create=True) as logged,
			patch.object(frappe, "get_traceback", create=True, return_value="t"),
		):
			self.assertEqual(api._features("Shop A"), {"expiry": False, "locations": 0})
		logged.assert_called_once()


class TestStockOut(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def out(self, **kwargs):
		arguments = {"item_code": "MLK", "qty": 2, "reason": "damaged", "warehouse": STORE}
		arguments.update(kwargs)
		return api.stock_out(**arguments)

	def test_a_write_off_cannot_be_saved_without_a_reason(self):
		for blank in (None, "", "  "):
			with self.subTest(reason=blank), self.assertRaises(frappe.ValidationError):
				self.out(reason=blank)
		self.helpers.create_material_issue.assert_not_called()

	def test_the_missing_reason_is_refused_before_anything_is_read_or_written(self):
		with self.assertRaises(frappe.ValidationError):
			self.out(reason="")
		self.assertEqual(self.db.method_calls, [])
		self.helpers.create_material_issue.assert_not_called()
		self.assertEqual(self.cleared, [])

	def test_the_refusal_is_a_plain_sentence(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.out(reason="")
		self.assertIn("Please say why the stock is leaving", str(caught.exception))

	def test_a_reason_not_on_the_list_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.out(reason="stolen")
		self.helpers.create_material_issue.assert_not_called()

	def test_other_without_a_note_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.out(reason="other", note="")
		self.assertIn("Other", str(caught.exception))

	def test_a_write_off_with_a_reason_makes_a_material_issue_and_writes_the_reason_on_it(self):
		result = self.out(note="two crates fell")
		self.assertTrue(result["success"])
		call = self.helpers.create_material_issue.call_args.kwargs
		self.assertEqual(
			(call["items"], call["source_warehouse"], call["company"]),
			([{"item_code": "MLK", "qty": 2.0}], STORE, "Shop A"),
		)
		self.db.set_value.assert_called_once_with(
			"Stock Entry",
			"MAT-STE-2026-00011",
			"remarks",
			"Write-off (Damaged): two crates fell",
			update_modified=False,
		)
		self.assertEqual(result["data"]["entry"], "MAT-STE-2026-00011")
		self.assertEqual(result["data"]["reason"], "damaged")

	def test_every_listed_reason_saves(self):
		for key in ("damaged", "expired", "lost", "used_in_shop"):
			with self.subTest(reason=key):
				self.assertTrue(self.out(reason=key)["success"])
		self.assertTrue(self.out(reason="other", note="a gift")["success"])

	def test_the_business_cache_is_cleared_after_a_write(self):
		self.out()
		self.assertEqual(self.cleared, ["Shop A"])

	def test_more_than_is_in_stock_is_refused_in_words(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.out(qty=5)
		self.assertIn("There is only 4.0 in stock", str(caught.exception))
		self.helpers.create_material_issue.assert_not_called()

	def test_a_bad_quantity_is_refused(self):
		for bad in (0, -3, "abc", None):
			with self.subTest(qty=bad), self.assertRaises(frappe.ValidationError):
				self.out(qty=bad)

	def test_a_store_must_be_chosen_when_there_are_two(self):
		self.as_user(locations=(STORE, STORE_2))
		with self.assertRaises(frappe.ValidationError) as caught:
			self.out(warehouse=None)
		self.assertIn("choose which store", str(caught.exception))

	def test_the_only_store_is_used_when_none_is_named(self):
		self.out(warehouse=None)
		self.assertEqual(self.helpers.create_material_issue.call_args.kwargs["source_warehouse"], STORE)

	def test_when_the_helper_fails_nothing_is_left_behind_and_the_message_is_plain(self):
		self.helpers.create_material_issue.return_value = {
			"success": False,
			"message": "Validation error: <b>Insufficient</b> stock",
		}
		result = self.out()
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Insufficient stock")
		self.db.rollback.assert_called_once()
		self.db.set_value.assert_not_called()
		self.assertEqual(self.cleared, [])

	def test_a_cashier_is_refused_before_anything_is_read(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			self.out()
		self.helpers.create_material_issue.assert_not_called()

	def test_a_stock_user_may_write_off_as_on_the_current_screens(self):
		self.as_user("store@shop-a.test", roles=("Stock User",))
		self.assertTrue(self.out()["success"])

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: "Shop B"
		with self.assertRaises(frappe.PermissionError):
			self.out()
		self.helpers.create_material_issue.assert_not_called()


class TestStockIn(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()

	def into(self, **kwargs):
		arguments = {"item_code": "MLK", "qty": 12, "warehouse": STORE}
		arguments.update(kwargs)
		return api.stock_in(**arguments)

	def test_it_makes_a_material_receipt_into_the_store(self):
		result = self.into(note="from the depot")
		self.assertTrue(result["success"])
		call = self.helpers.create_material_receipt.call_args.kwargs
		self.assertEqual(
			(call["items"], call["target_warehouse"], call["company"]),
			([{"item_code": "MLK", "qty": 12.0}], STORE, "Shop A"),
		)
		self.db.set_value.assert_called_once_with(
			"Stock Entry", "MAT-STE-2026-00010", "remarks", "Stock in: from the depot", update_modified=False
		)
		self.assertEqual(self.cleared, ["Shop A"])
		self.assertEqual(result["data"]["in_stock"], 4.0)

	def test_no_note_writes_no_remark(self):
		self.into()
		self.db.set_value.assert_not_called()

	def test_the_cost_is_passed_on_for_an_owner(self):
		self.into(unit_cost="55.5")
		self.assertEqual(
			self.helpers.create_material_receipt.call_args.kwargs["items"][0]["basic_rate"], 55.5
		)

	def test_the_cost_is_ignored_for_a_person_who_may_not_see_cost(self):
		self.as_user("store@shop-a.test", roles=("Stock User",))
		self.into(unit_cost="55.5")
		self.assertNotIn("basic_rate", self.helpers.create_material_receipt.call_args.kwargs["items"][0])

	def test_a_bad_quantity_is_refused_before_anything_is_written(self):
		for bad in (0, "x", -4):
			with self.subTest(qty=bad), self.assertRaises(frappe.ValidationError):
				self.into(qty=bad)
		self.helpers.create_material_receipt.assert_not_called()

	def test_a_cashier_is_refused(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			self.into()
		self.helpers.create_material_receipt.assert_not_called()

	def test_two_stores_and_none_named_asks_which(self):
		self.as_user(locations=(STORE, STORE_2))
		with self.assertRaises(frappe.ValidationError):
			self.into(warehouse=None)

	def test_a_failed_receipt_leaves_nothing_and_says_so_plainly(self):
		self.helpers.create_material_receipt.return_value = {
			"success": False,
			"message": "Item 'X' does not exist",
		}
		result = self.into()
		self.assertEqual((result["success"], result["message"]), (False, "Item 'X' does not exist"))
		self.db.rollback.assert_called_once()


class TestCountCalls(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user()
		self.stub("_count_bins", lambda store: [b for b in BINS if b["warehouse"] == store])
		self.stub("_expected", lambda store, codes: {"MLK": 4, "UNG": 40, "MCH": 25.5})

	def test_the_sheet_shows_expected_quantities_for_the_whole_store(self):
		data = api.get_count_sheet()["data"]
		self.assertEqual(data["warehouse"], STORE)
		self.assertEqual(by_code(data["rows"])["MCH"]["expected"], 25.5)
		self.assertFalse(data["truncated"])
		self.assertNotIn("value", keys_of(data))

	def test_the_sheet_for_one_category(self):
		rows = api.get_count_sheet(scope="category", category="Unga")["data"]["rows"]
		self.assertEqual([r["item_code"] for r in rows], ["UNG"])

	def test_a_category_sheet_without_a_category_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_count_sheet(scope="category")

	def test_two_stores_need_a_choice(self):
		self.as_user(locations=(STORE, STORE_2))
		with self.assertRaises(frappe.ValidationError):
			api.get_count_sheet()

	def test_a_cashier_cannot_open_a_count_sheet(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			api.get_count_sheet()

	def test_a_count_that_matches_changes_nothing(self):
		result = api.submit_count([{"item_code": "MLK", "counted": 4}, {"item_code": "UNG", "counted": 40}])
		self.assertTrue(result["success"])
		self.assertIsNone(result["data"]["reconciliation"])
		self.assertEqual(result["data"]["totals"]["matches"], 2)
		self.helpers.create_stock_reconciliation.assert_not_called()
		self.assertEqual(self.cleared, [])

	def test_a_count_with_differences_makes_one_reconciliation_of_the_changed_lines_only(self):
		result = api.submit_count(
			[
				{"item_code": "MLK", "counted": 3},
				{"item_code": "UNG", "counted": 40},
				{"item_code": "MCH", "counted": 27},
			]
		)
		self.helpers.create_stock_reconciliation.assert_called_once()
		call = self.helpers.create_stock_reconciliation.call_args.kwargs
		self.assertEqual(call["items"], [{"item_code": "MLK", "qty": 3.0}, {"item_code": "MCH", "qty": 27.0}])
		self.assertEqual((call["warehouse"], call["company"]), (STORE, "Shop A"))
		data = result["data"]
		self.assertEqual(data["reconciliation"], "MAT-RECO-2026-00001")
		self.assertEqual(
			data["totals"], {"lines": 3, "short": 1, "over": 1, "matches": 1, "net_difference": 0.5}
		)
		self.assertEqual(self.cleared, ["Shop A"])

	def test_the_answer_lists_expected_counted_and_difference_for_every_product(self):
		data = api.submit_count([{"item_code": "MLK", "counted": 3}])["data"]
		self.assertEqual(
			data["differences"],
			[{"item_code": "MLK", "expected": 4.0, "counted": 3.0, "difference": -1.0, "state": "short"}],
		)

	def test_a_failed_reconciliation_leaves_nothing_and_does_not_show_developer_words(self):
		self.helpers.create_stock_reconciliation.return_value = {
			"success": False,
			"message": "Difference Account (expense_account) is required.",
		}
		result = api.submit_count([{"item_code": "MLK", "counted": 3}])
		self.assertFalse(result["success"])
		self.assertIn("The count was not saved", result["message"])
		self.assertNotIn("expense_account", result["message"])
		self.db.rollback.assert_called_once()

	def test_bad_counts_are_refused_before_anything_is_read(self):
		for counts in ([], [{"item_code": "MLK", "counted": -2}], "nonsense"):
			with self.subTest(counts=counts), self.assertRaises(frappe.ValidationError):
				api.submit_count(counts)
		self.helpers.create_stock_reconciliation.assert_not_called()

	def test_a_cashier_cannot_submit_a_count(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			api.submit_count([{"item_code": "MLK", "counted": 3}])
		self.helpers.create_stock_reconciliation.assert_not_called()


class TestFindProducts(StockCase):
	def test_it_searches_by_name_or_code_with_the_quantity(self):
		self.as_user()
		self.db.sql.return_value = [
			{"item_code": "MLK", "item_name": "Maziwa 500ml", "category": "Maziwa", "unit": "Nos", "qty": 4}
		]
		rows = api.find_products(search="mazi")["data"]["rows"]
		self.assertEqual(
			rows,
			[
				{
					"item_code": "MLK",
					"item_name": "Maziwa 500ml",
					"category": "Maziwa",
					"unit": "Nos",
					"qty": 4.0,
				}
			],
		)
		self.assertEqual(self.db.sql.call_args.args[1]["like"], "%mazi%")

	def test_the_number_of_results_is_capped(self):
		self.as_user()
		api.find_products(limit=500)
		self.assertEqual(self.db.sql.call_args.args[1]["limit"], 20)

	def test_a_cashier_is_refused(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			api.find_products()


class TestTheReadsKeepTheirRules(unittest.TestCase):
	def test_the_low_rows_are_homes_low_rows_narrowed_to_the_store(self):
		fake = types.SimpleNamespace(low_stock_rows=lambda company: list(LOW))
		with patch.dict(sys.modules, {"murzak_pos.api.alerts": fake}):
			self.assertEqual(len(api._low_rows("Shop A", None)), 3)
			self.assertEqual({r["item_code"] for r in api._low_rows("Shop A", STORE)}, {"MLK", "SAB", "SUK"})
			self.assertEqual(api._low_rows("Shop A", STORE_2), [])

	def test_the_sold_list_is_the_one_home_uses(self):
		seen = {}

		def sold(scope):
			seen["scope"] = scope
			return list(SOLD)

		fake = types.SimpleNamespace(_sold_recently=sold)
		with (
			patch.dict(sys.modules, {"murzak_pos.api.dashboard_overview": fake}),
			patch.object(api, "_today", return_value=TODAY),
		):
			self.assertEqual(api._sold_items("Shop A", STORE), SOLD)
		self.assertEqual(
			(seen["scope"].company, seen["scope"].store, seen["scope"].today), ("Shop A", STORE, TODAY)
		)

	def test_the_expiry_lines_come_from_the_existing_expiry_alerts(self):
		fake = types.SimpleNamespace(get_expiry_alerts=MagicMock(return_value={"data": [TestExpiry.RAW]}))
		with patch.dict(sys.modules, {"murzak_pos.api.expiry_api": fake}):
			rows = api._expiry("Shop A", STORE, 45)
		fake.get_expiry_alerts.assert_called_once_with(
			company="Shop A", days=45, warehouse=STORE, include_expired=1
		)
		self.assertEqual(rows[0]["state"], "critical")


class TestMoveRules(unittest.TestCase):
	def test_a_move_lists_each_product_once_with_a_quantity(self):
		self.assertEqual(
			calc.clean_move([{"item_code": " MLK ", "qty": "2"}, {"item_code": "UNG", "qty": 1.5}]),
			[{"item_code": "MLK", "qty": 2.0}, {"item_code": "UNG", "qty": 1.5}],
		)

	def test_a_move_may_arrive_as_json_text(self):
		self.assertEqual(
			calc.clean_move('[{"item_code": "MLK", "qty": 3}]'), [{"item_code": "MLK", "qty": 3.0}]
		)

	def test_a_bad_move_is_refused_with_its_reason(self):
		cases = {
			"empty_move": [None, [], "[]"],
			"bad_move": ["not json", ["MLK"], [{"item_code": "MLK", "qty": 1}, "x"]],
			"bad_qty": [
				[{"item_code": "MLK", "qty": 0}],
				[{"item_code": "MLK", "qty": -2}],
				[{"item_code": "MLK"}],
			],
			"need_item": [[{"qty": 1}], [{"item_code": " ", "qty": 1}]],
			"duplicate_move": [[{"item_code": "MLK", "qty": 1}, {"item_code": "MLK", "qty": 2}]],
		}
		for code, bad_moves in cases.items():
			for bad in bad_moves:
				with self.subTest(code=code, move=bad), self.assertRaises(calc.StockError) as caught:
					calc.clean_move(bad)
				self.assertEqual(caught.exception.code, code)

	def test_too_many_products_are_refused(self):
		lines = [{"item_code": f"P{n}", "qty": 1} for n in range(calc.MAX_MOVE_ROWS + 1)]
		with self.assertRaises(calc.StockError) as caught:
			calc.clean_move(lines)
		self.assertEqual(caught.exception.code, "too_many_moves")

	def test_a_move_needs_two_different_stores(self):
		self.assertEqual(calc.clean_move_stores(" A ", "B"), ("A", "B"))
		for source, target, code in (
			("A", "A", "same_store"),
			("", "B", "need_store"),
			("A", None, "need_store"),
		):
			with self.subTest(source=source, target=target), self.assertRaises(calc.StockError) as caught:
				calc.clean_move_stores(source, target)
			self.assertEqual(caught.exception.code, code)

	def test_only_the_lines_asking_for_more_than_is_held_are_short(self):
		lines = [
			{"item_code": "MLK", "qty": 5.0},
			{"item_code": "UNG", "qty": 40.0},
			{"item_code": "SUK", "qty": 1.0},
		]
		self.assertEqual(
			calc.shortages(lines, {"MLK": 4.0, "UNG": 40.0}),
			[
				{"item_code": "MLK", "wanted": 5.0, "held": 4.0},
				{"item_code": "SUK", "wanted": 1.0, "held": 0.0},
			],
		)

	def test_a_quantity_is_said_the_way_people_say_it(self):
		self.assertEqual(
			[calc.plain_qty(v) for v in (4.0, 2.5, 1000000, 0.125, 0)], ["4", "2.5", "1000000", "0.125", "0"]
		)

	def test_the_transfer_remark(self):
		self.assertEqual(calc.transfer_remark("for the weekend"), "Stock moved: for the weekend")
		self.assertEqual(calc.transfer_remark(""), "Stock moved between stores")

	def test_a_store_name_is_one_tidy_line(self):
		self.assertEqual(calc.clean_store_name("  Duka   la  Pili "), "Duka la Pili")
		for blank in (None, "", "   "):
			with self.subTest(name=blank), self.assertRaises(calc.StockError) as caught:
				calc.clean_store_name(blank)
			self.assertEqual(caught.exception.code, "need_store_name")
		for bad in ("A", "x" * 61, "Shop <b>", "a/b", "a\\b"):
			with self.subTest(name=bad), self.assertRaises(calc.StockError) as caught:
				calc.clean_store_name(bad)
			self.assertEqual(caught.exception.code, "bad_store_name")

	def test_transfer_is_a_kind_of_movement(self):
		self.assertEqual(calc.clean_kind("Transfer"), "transfer")
		count_sql, page_sql, values = calc.movements_query("Shop A", TODAY, TODAY, kind="transfer")
		self.assertEqual(values["transfer_purposes"], calc.TRANSFER_PURPOSES)
		self.assertIn("se.purpose IN %(transfer_purposes)s", page_sql)
		self.assertIn("se.purpose IN %(transfer_purposes)s", count_sql)


class TestMoveStock(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user(locations=(STORE, STORE_2))
		self.helpers.create_material_transfer = MagicMock(
			return_value={"success": True, "data": {"name": "MAT-STE-2026-00020"}}
		)
		self.bin_quantities = {"MLK": 4.0, "UNG": 40.0}

	def move(self, **kwargs):
		arguments = {
			"items": [{"item_code": "MLK", "qty": 2}, {"item_code": "UNG", "qty": 10}],
			"from_warehouse": STORE,
			"to_warehouse": STORE_2,
		}
		arguments.update(kwargs)
		return api.move_stock(**arguments)

	def test_a_move_makes_one_transfer_and_writes_the_note_on_it(self):
		result = self.move(note="for the weekend")
		self.assertTrue(result["success"])
		call = self.helpers.create_material_transfer.call_args.kwargs
		self.assertEqual(
			(call["items"], call["source_warehouse"], call["target_warehouse"], call["company"]),
			([{"item_code": "MLK", "qty": 2.0}, {"item_code": "UNG", "qty": 10.0}], STORE, STORE_2, "Shop A"),
		)
		self.db.set_value.assert_called_once_with(
			"Stock Entry",
			"MAT-STE-2026-00020",
			"remarks",
			"Stock moved: for the weekend",
			update_modified=False,
		)
		self.assertEqual(result["data"]["entry"], "MAT-STE-2026-00020")
		self.assertEqual(result["data"]["products"], 2)
		self.assertEqual(self.cleared, ["Shop A"])

	def test_moving_more_than_the_store_holds_is_refused_and_nothing_is_made(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.move(items=[{"item_code": "UNG", "qty": 10}, {"item_code": "MLK", "qty": 5}])
		self.assertIn(
			"There is only 4 of MLK in the first store, so 5 cannot be moved.", str(caught.exception)
		)
		self.helpers.create_material_transfer.assert_not_called()
		self.assertEqual(self.cleared, [])

	def test_moving_all_that_is_held_is_allowed(self):
		self.assertTrue(self.move(items=[{"item_code": "MLK", "qty": 4}])["success"])

	def test_the_same_store_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.move(to_warehouse=STORE)
		self.assertIn("two different stores", str(caught.exception))
		self.helpers.create_material_transfer.assert_not_called()

	def test_an_empty_or_bad_list_is_refused(self):
		for bad in ([], "nonsense", [{"item_code": "MLK", "qty": 0}]):
			with self.subTest(items=bad), self.assertRaises(frappe.ValidationError):
				self.move(items=bad)
		self.helpers.create_material_transfer.assert_not_called()

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: (
			"Shop B" if doctype == "Warehouse" and name == STORE_2 else self.get_value(doctype, name, field)
		)
		with self.assertRaises(frappe.PermissionError):
			self.move()
		self.helpers.create_material_transfer.assert_not_called()

	def test_a_failure_in_the_helper_is_put_back_and_said_plainly(self):
		self.helpers.create_material_transfer.return_value = {
			"success": False,
			"message": "<b>Validation error:</b> Item MLK needs a batch",
		}
		result = self.move()
		self.assertFalse(result["success"])
		self.assertEqual(result["message"], "Item MLK needs a batch")
		self.db.rollback.assert_called_once()
		self.db.set_value.assert_not_called()
		self.assertEqual(self.cleared, [])

	def test_a_cashier_cannot_move_stock(self):
		self.as_user(user="till@shop-a.test", roles=("Sales User",), locations=(STORE, STORE_2))
		with self.assertRaises(frappe.PermissionError):
			self.move()
		self.helpers.create_material_transfer.assert_not_called()


class TestAddStore(StockCase):
	def setUp(self):
		super().setUp()
		self.as_user(locations=(STORE,))
		self.db.exists.return_value = None
		self.document = MagicMock()
		self.document.name = "Duka la Pili - SA"
		self.document.warehouse_name = "Duka la Pili"
		self.get_doc = self.start((frappe, "get_doc"), return_value=self.document, create=True)
		self.parent = self.start((api, "_store_parent"), return_value="All Warehouses - SA")

	def test_an_owner_adds_a_store_under_the_shops_folder(self):
		result = api.add_store(" Duka   la Pili ")
		self.assertEqual(
			self.get_doc.call_args.args[0],
			{
				"doctype": "Warehouse",
				"warehouse_name": "Duka la Pili",
				"company": "Shop A",
				"parent_warehouse": "All Warehouses - SA",
			},
		)
		self.document.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(
			result, {"success": True, "data": {"name": "Duka la Pili - SA", "warehouse_name": "Duka la Pili"}}
		)
		self.assertEqual(self.cleared, ["Shop A"])

	def test_a_name_already_used_is_refused_in_plain_words(self):
		self.db.exists.return_value = "Duka la Pili - SA"
		with self.assertRaises(frappe.ValidationError) as caught:
			api.add_store("Duka la Pili")
		self.assertIn("You already have a store called Duka la Pili.", str(caught.exception))
		self.document.insert.assert_not_called()

	def test_a_missing_or_bad_name_is_refused_before_anything_is_read(self):
		for bad in (None, "", " ", "A", "a/b"):
			with self.subTest(name=bad), self.assertRaises(frappe.ValidationError):
				api.add_store(bad)
		self.document.insert.assert_not_called()
		self.db.exists.assert_not_called()

	def test_only_the_owner_may_add_a_store(self):
		self.as_user(user="mgr@shop-a.test", roles=("Stock Manager",))
		with self.assertRaises(frappe.PermissionError):
			api.add_store("Duka la Pili")
		self.document.insert.assert_not_called()


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def test_every_call_is_whitelisted(self):
		for function in (
			api.get_stock_levels,
			api.find_products,
			api.stock_in,
			api.stock_out,
			api.get_count_sheet,
			api.submit_count,
			api.move_stock,
			api.add_store,
		):
			self.assertIn(function, frappe.whitelisted)

	def test_the_writes_are_post_only(self):
		for function in (api.stock_in, api.stock_out, api.submit_count, api.move_stock, api.add_store):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])

	def test_the_pure_module_needs_no_database(self):
		self.assertNotIn("import frappe", inspect.getsource(calc))

	def test_the_reason_is_the_first_thing_stock_out_checks_after_the_role(self):
		tree = ast.parse(inspect.getsource(api))
		function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "stock_out")
		body = [
			s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		self.assertEqual(ast.unparse(body[0]), "require_stock_role()")
		self.assertIn("clean_reason", ast.unparse(body[1]))

	def test_the_existing_helpers_are_called_not_copied(self):
		source = inspect.getsource(api)
		for name in (
			"create_material_receipt",
			"create_material_issue",
			"create_material_transfer",
			"create_stock_reconciliation",
		):
			self.assertIn(f"import {name}", source)

	def test_the_existing_helpers_are_not_edited(self):
		# this module only imports them; it defines no function of those names
		defined = {n.name for n in ast.parse(inspect.getsource(api)).body if isinstance(n, ast.FunctionDef)}
		self.assertFalse(
			defined & {"create_material_receipt", "create_material_issue", "create_stock_reconciliation"}
		)

	def test_the_cache_functions_are_small_and_replaceable(self):
		self.assertTrue(callable(api._cache_get) and callable(api._cache_set))


if __name__ == "__main__":
	unittest.main()
