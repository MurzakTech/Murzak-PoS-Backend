# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the figures of a shift report (X and Z). No database is needed."""

import unittest

from murzak_pos.api import shift_report_calc as report


def line(
	mode="Cash",
	key="cash",
	opening=0.0,
	sales=0.0,
	paid_in=0.0,
	refunds=0.0,
	paid_out=0.0,
	expected=0.0,
	cash=True,
):
	return {
		"mode": mode,
		"key": key,
		"label": mode,
		"opening": opening,
		"sales": sales,
		"paid_in": paid_in,
		"refunds": refunds,
		"paid_out": paid_out,
		"expected": expected,
		"counts_cash": cash,
	}


class SalesBlockCase(unittest.TestCase):
	def test_the_average_is_the_gross_over_the_count(self):
		got = report.sales_block(4, 1000, 862.07, 137.93, 50)
		self.assertEqual(
			got,
			{"count": 4, "gross": 1000.0, "net": 862.07, "tax": 137.93, "discounts": 50.0, "average": 250.0},
		)

	def test_no_sales_has_no_average(self):
		got = report.sales_block(0, 0, 0, 0, 0)
		self.assertEqual((got["count"], got["average"]), (0, 0.0))

	def test_the_numbers_may_arrive_as_text_or_nothing(self):
		got = report.sales_block("2", "300.005", None, "", None)
		self.assertEqual((got["count"], got["net"], got["tax"]), (2, 0.0, 0.0))


class ReturnsBlockCase(unittest.TestCase):
	def test_the_total_is_always_a_positive_amount_of_money(self):
		self.assertEqual(report.returns_block(2, -450), {"count": 2, "total": 450.0})
		self.assertEqual(report.returns_block(1, 450), {"count": 1, "total": 450.0})


class MethodRowsCase(unittest.TestCase):
	def test_a_counted_way_of_being_paid_shows_its_difference(self):
		rows = report.method_rows([line(sales=2000, expected=2000)], {"Cash": 1950})
		self.assertEqual((rows[0]["counted"], rows[0]["difference"]), (1950.0, -50.0))

	def test_an_uncounted_one_has_no_difference(self):
		rows = report.method_rows([line(sales=2000, expected=2000)], {})
		self.assertEqual((rows[0]["counted"], rows[0]["difference"]), (None, None))

	def test_credit_is_not_a_way_of_being_paid_in_the_till(self):
		lines = [line(expected=100), line("On credit", "credit", sales=500, cash=False)]
		self.assertEqual([r["mode"] for r in report.method_rows(lines)], ["Cash"])
		self.assertEqual(report.credit_total(lines), 500.0)


class CashBlockCase(unittest.TestCase):
	def test_the_drawer_adds_up(self):
		lines = [line(opening=500, sales=2000, paid_in=1000, refunds=100, paid_out=800, expected=2600)]
		block = report.cash_block(lines, {"Paid In": 1000, "Paid Out": 300, "Safe Drop": 500})
		self.assertEqual(
			(
				block["opening"],
				block["sales"],
				block["paid_in"],
				block["paid_out"],
				block["safe_drops"],
				block["refunds"],
				block["expected"],
			),
			(500.0, 2000.0, 1000.0, 300.0, 500.0, 100.0, 2600.0),
		)
		self.assertEqual(
			block["opening"]
			+ block["sales"]
			+ block["paid_in"]
			- block["refunds"]
			- block["paid_out"]
			- block["safe_drops"],
			block["expected"],
		)

	def test_a_counted_drawer_shows_the_difference_in_words(self):
		lines = [line(sales=1000, expected=1000)]
		self.assertEqual(report.cash_block(lines, {}, counted=950)["word"], "short")
		self.assertEqual(report.cash_block(lines, {}, counted=1000)["word"], "matches")
		self.assertEqual(report.cash_block(lines, {}, counted=1020)["word"], "over")

	def test_an_uncounted_drawer_has_no_difference(self):
		block = report.cash_block([line(sales=1000, expected=1000)], {})
		self.assertEqual((block["counted"], block["difference"], block["word"]), (None, None, None))

	def test_no_cash_line_gives_nothing(self):
		self.assertIsNone(report.cash_block([line("M-Pesa", "mpesa", expected=10)], {}))
		self.assertIsNone(report.cash_block([], {}))

	def test_safe_drops_never_make_the_other_paid_out_negative(self):
		block = report.cash_block([line(paid_out=100, expected=-100)], {"Safe Drop": 500})
		self.assertEqual(block["paid_out"], 0.0)


class TotalsCase(unittest.TestCase):
	def rows(self, counted_cash, counted_mpesa):
		return [
			{"expected": 1000.0, "counted": counted_cash},
			{"expected": 500.0, "counted": counted_mpesa},
		]

	def test_everything_counted_adds_up(self):
		got = report.totals(self.rows(990.0, 500.0))
		self.assertEqual(
			(got["expected"], got["counted"], got["difference"], got["word"]),
			(1500.0, 1490.0, -10.0, "short"),
		)

	def test_one_uncounted_leaves_the_count_open(self):
		got = report.totals(self.rows(990.0, None))
		self.assertEqual((got["expected"], got["counted"], got["difference"]), (1500.0, None, None))

	def test_no_rows_has_nothing_counted(self):
		self.assertEqual(report.totals([])["counted"], None)
