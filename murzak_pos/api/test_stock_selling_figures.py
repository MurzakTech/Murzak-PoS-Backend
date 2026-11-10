"""
The selling side of the Stock levels overview: price, selling value, margin and the warnings on a product's figures.

No database is used. The first half tests the plain rules with plain rows; the second half tests the call: that an owner
or manager gets the figures, that a cashier never sees any cost-derived one, and that the warnings filter works.
"""

import unittest

from murzak_pos.api import stock_levels_calc as calc
from murzak_pos.api.test_stock_levels import BINS, LOW, SOLD, StockCase, keys_of


class TestTheWarnings(unittest.TestCase):
	def test_a_healthy_product_has_none(self):
		self.assertEqual(calc.stock_flags(10, 100, 15), [])

	def test_stock_below_zero(self):
		self.assertEqual(calc.stock_flags(-2, -20, 15), ["negative"])

	def test_stock_with_no_cost_recorded(self):
		self.assertEqual(calc.stock_flags(10, 0, 15), ["no_cost"])

	def test_stock_with_no_selling_price(self):
		self.assertEqual(calc.stock_flags(10, 100, 0), ["no_price"])

	def test_priced_below_what_the_stock_cost(self):
		self.assertEqual(calc.stock_flags(10, 200, 15), ["below_cost"])  # costs 20 each, sells at 15

	def test_a_product_with_no_stock_has_no_cost_or_price_warning(self):
		self.assertEqual(calc.stock_flags(0, 0, 0), [])

	def test_a_missing_cost_is_not_also_called_below_cost(self):
		self.assertEqual(calc.stock_flags(10, 0, 5), ["no_cost"])


class TestTheSellingFigures(unittest.TestCase):
	def test_value_and_margin(self):
		found = calc.selling_figures(10, 100, 15)  # cost 10 each, sells at 15
		self.assertEqual(found["price"], 15.0)
		self.assertEqual(found["retail_value"], 150.0)
		self.assertEqual(found["margin_pct"], 33.3)
		self.assertEqual(found["flags"], [])

	def test_no_cost_means_no_margin_not_a_made_up_one(self):
		found = calc.selling_figures(10, 0, 15)
		self.assertIsNone(found["margin_pct"])
		self.assertEqual(found["retail_value"], 150.0)

	def test_no_price_means_no_selling_value(self):
		found = calc.selling_figures(10, 100, None)
		self.assertEqual(found["price"], 0.0)
		self.assertEqual(found["retail_value"], 0.0)
		self.assertIsNone(found["margin_pct"])

	def test_a_negative_margin_is_shown_when_priced_below_cost(self):
		self.assertEqual(calc.selling_figures(10, 200, 15)["margin_pct"], -33.3)

	def test_nothing_in_stock_is_worth_nothing(self):
		self.assertEqual(calc.selling_figures(0, 0, 15)["retail_value"], 0.0)


class TestTheRows(unittest.TestCase):
	def test_rows_are_exactly_as_before_without_prices(self):
		for row in calc.build_stock_rows(BINS, LOW, SOLD):
			self.assertNotIn("flags", row)
			self.assertNotIn("retail_value", row)

	def test_rows_carry_the_selling_figures_when_prices_are_given(self):
		rows = {r["item_code"]: r for r in calc.build_stock_rows(BINS, LOW, SOLD, prices={"MCH": 150})}
		self.assertEqual(rows["MCH"]["price"], 150.0)
		self.assertEqual(rows["MCH"]["retail_value"], 3825.0)  # 25.5 at 150
		self.assertEqual(rows["MCH"]["margin_pct"], 33.3)  # costs 100 each
		self.assertEqual(rows["SAB"]["flags"], ["no_price"])  # stock but no selling price

	def test_the_tiles_add_selling_value_profit_and_the_number_to_check(self):
		rows = calc.build_stock_rows(BINS, LOW, SOLD, prices={"MCH": 150})
		tiles = calc.tiles(rows, True)
		self.assertEqual(tiles["retail_value"], 3825.0)
		self.assertEqual(tiles["potential_profit"], 1275.0)  # 3825 less the 2550 it cost
		self.assertEqual(tiles["check"], sum(1 for r in rows if r["flags"]))

	def test_the_tiles_have_none_of_it_for_a_person_who_may_not_see_cost(self):
		rows = calc.build_stock_rows(BINS, LOW, SOLD, prices={"MCH": 150})
		self.assertEqual(set(calc.tiles(rows, False)), {"items", "low", "out_of_stock"})

	def test_the_tiles_are_as_before_for_rows_without_the_figures(self):
		self.assertEqual(
			set(calc.tiles(calc.build_stock_rows(BINS, LOW, SOLD), True)),
			{"items", "low", "out_of_stock", "stock_value"},
		)

	def test_cost_derived_figures_are_stripped_but_the_price_stays(self):
		rows = calc.build_stock_rows(BINS, LOW, SOLD, prices={"MCH": 150})
		kept = calc.strip_costs(rows)
		found = set().union(*(r.keys() for r in kept))
		for hidden in ("value", "retail_value", "margin_pct", "flags"):
			self.assertNotIn(hidden, found)
		self.assertIn("price", found)

	def test_the_check_filter_lists_only_products_with_a_warning(self):
		rows = calc.build_stock_rows(BINS, LOW, SOLD, prices={"MCH": 150})
		listed = calc.filter_stock_rows(rows, status=calc.CHECK)
		self.assertTrue(listed)
		self.assertTrue(all(r["flags"] for r in listed))
		self.assertNotIn("MCH", {r["item_code"] for r in listed})

	def test_check_is_a_filter_on_the_overview_only(self):
		self.assertEqual(calc.clean_status("overview", "check"), "check")
		with self.assertRaises(calc.StockError):
			calc.clean_status("low", "check")


class TestTheCall(StockCase):
	def test_an_owner_gets_the_figures(self):
		self.as_user()
		self.stub("_selling_prices", lambda codes: {"MCH": 150})
		data = self.data()
		self.assertIn("retail_value", data["tiles"])
		self.assertIn("flags", data["rows"][0])

	def test_a_cashier_gets_the_price_but_nothing_derived_from_cost(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.stub("_selling_prices", lambda codes: {"MCH": 150})
		found = keys_of(self.data())
		for hidden in ("value", "retail_value", "margin_pct", "flags", "potential_profit", "check"):
			self.assertNotIn(hidden, found)
		self.assertIn("price", found)

	def test_a_cashier_cannot_use_the_warnings_filter_to_learn_about_cost(self):
		self.as_user("till@shop-a.test", roles=("Sales User",))
		self.stub("_selling_prices", lambda codes: {"MCH": 150})
		everything = self.data()
		filtered = self.data(status="check")
		self.assertEqual(filtered["total"], everything["total"])

	def test_the_owner_can_filter_to_the_products_to_check(self):
		self.as_user()
		self.stub("_selling_prices", lambda codes: {"MCH": 150})
		data = self.data(status="check")
		self.assertTrue(data["rows"])
		self.assertTrue(all(r["flags"] for r in data["rows"]))
