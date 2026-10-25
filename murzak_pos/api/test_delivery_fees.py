# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for delivery fees: each way a shop can charge (free, flat, by area, by distance), the "free above" rule,
and the checks on what an owner types. No database is needed.
"""

from __future__ import annotations

import unittest

from murzak_pos.api import delivery_fees as df

# Two real places about 1 degree of latitude apart: 111.19 km in a straight line
SHOP = {"shop_latitude": 0.0, "shop_longitude": 36.0}


def rules(**over):
	base = {
		"delivery_mode": "free",
		"delivery_flat_fee": 0,
		"delivery_areas": [],
		"free_delivery_above": 0,
		"distance_base_km": 0,
		"distance_base_fee": 0,
		"distance_per_km": 0,
		"distance_max_km": 0,
		"road_factor": 1.0,
		**SHOP,
	}
	base.update(over)
	return base


class TestDistance(unittest.TestCase):
	def test_one_degree_of_latitude_is_about_111_km(self):
		self.assertAlmostEqual(df.distance_km(0, 36, 1, 36), 111.19, places=1)

	def test_same_place_is_zero_and_it_is_symmetric(self):
		self.assertEqual(df.distance_km(-1.28, 36.82, -1.28, 36.82), 0)
		self.assertAlmostEqual(
			df.distance_km(-1.28, 36.82, -1.33, 36.9), df.distance_km(-1.33, 36.9, -1.28, 36.82)
		)

	def test_coordinates_are_checked(self):
		self.assertEqual(df.coordinates("-1.28", "36.82"), (-1.28, 36.82))
		self.assertIsNone(df.coordinates(None, 36))
		self.assertIsNone(df.coordinates("", ""))
		for lat, lng in ((91, 0), (0, 181), ("abc", 0), ("nan", 0), ("inf", 0)):
			with self.assertRaises(df.DeliveryRuleError, msg=f"{lat},{lng}"):
				df.coordinates(lat, lng)


class TestQuote(unittest.TestCase):
	def test_free_delivery_costs_nothing_and_needs_nothing(self):
		self.assertEqual(
			df.quote(rules(), 500), {"deliverable": True, "fee": 0.0, "distance_km": None, "message": None}
		)

	def test_flat_fee(self):
		self.assertEqual(df.quote(rules(delivery_mode="flat", delivery_flat_fee=150), 500)["fee"], 150.0)

	def test_area_fee_matches_the_name_whatever_the_case_or_spaces(self):
		r = rules(
			delivery_mode="area",
			delivery_areas=[{"name": "Westlands", "fee": 200}, {"name": "Kasarani", "fee": 300}],
		)
		self.assertEqual(df.quote(r, 500, area="westlands ")["fee"], 200.0)
		self.assertEqual(df.quote(r, 500, area="KASARANI")["fee"], 300.0)

	def test_an_area_not_on_the_list_is_not_delivered_to(self):
		r = rules(delivery_mode="area", delivery_areas=[{"name": "Westlands", "fee": 200}])
		for area in ("Mombasa", "", None):
			out = df.quote(r, 500, area=area)
			self.assertFalse(out["deliverable"], area)
			self.assertTrue(out["message"])

	def test_distance_fee_is_the_start_fee_plus_each_extra_kilometre(self):
		# 0.1 degree is about 11.1 km. 3 km are included in a 100 start fee, then 20 for each extra km.
		r = rules(
			delivery_mode="distance",
			distance_base_km=3,
			distance_base_fee=100,
			distance_per_km=20,
			distance_max_km=50,
		)
		out = df.quote(r, 500, latitude=0.1, longitude=36.0)
		self.assertAlmostEqual(out["distance_km"], 11.1, places=1)
		expected = 100 + (11.119 - 3) * 20
		self.assertEqual(out["fee"], float(-(-round(expected, 2) // 1)))

	def test_inside_the_included_distance_pays_only_the_start_fee(self):
		r = rules(
			delivery_mode="distance",
			distance_base_km=15,
			distance_base_fee=100,
			distance_per_km=20,
			distance_max_km=50,
		)
		self.assertEqual(df.quote(r, 500, latitude=0.1, longitude=36.0)["fee"], 100.0)

	def test_the_road_factor_makes_the_distance_longer(self):
		r = rules(delivery_mode="distance", distance_per_km=10, distance_max_km=500, road_factor=1.5)
		out = df.quote(r, 0, latitude=1, longitude=36)
		self.assertAlmostEqual(out["distance_km"], 111.19 * 1.5, places=0)

	def test_beyond_the_longest_distance_is_not_delivered(self):
		r = rules(delivery_mode="distance", distance_per_km=10, distance_max_km=50)
		out = df.quote(r, 500, latitude=1, longitude=36)
		self.assertFalse(out["deliverable"])
		self.assertIn("50", out["message"])

	def test_distance_needs_the_customers_location_and_the_shops(self):
		r = rules(delivery_mode="distance", distance_max_km=50)
		self.assertFalse(df.quote(r, 500)["deliverable"])
		r = rules(delivery_mode="distance", distance_max_km=50, shop_latitude=None, shop_longitude=None)
		self.assertFalse(df.quote(r, 500, latitude=0.1, longitude=36)["deliverable"])

	def test_an_impossible_location_is_an_error_not_a_fee(self):
		r = rules(delivery_mode="distance", distance_max_km=50)
		with self.assertRaises(df.DeliveryRuleError):
			df.quote(r, 500, latitude=999, longitude=0)

	def test_free_above_a_basket_total(self):
		r = rules(delivery_mode="flat", delivery_flat_fee=150, free_delivery_above=1000)
		self.assertEqual(df.quote(r, 999.99)["fee"], 150.0)
		self.assertEqual(df.quote(r, 1000)["fee"], 0.0)
		r = rules(delivery_mode="area", delivery_areas=[{"name": "A", "fee": 200}], free_delivery_above=1000)
		self.assertEqual(df.quote(r, 5000, area="A")["fee"], 0.0)

	def test_fees_round_up_to_whole_shillings(self):
		self.assertEqual(df.quote(rules(delivery_mode="flat", delivery_flat_fee=150.2), 0)["fee"], 151.0)

	def test_an_unknown_mode_is_not_delivered(self):
		self.assertFalse(df.quote(rules(delivery_mode="drone"), 0)["deliverable"])


class TestValidation(unittest.TestCase):
	def test_a_good_form(self):
		out = df.validate_rules(
			{
				"delivery_mode": "area",
				"delivery_areas": [{"name": " Westlands ", "fee": "200"}],
				"shop_latitude": "-1.2",
				"shop_longitude": "36.8",
			},
			{},
		)
		self.assertEqual(out["delivery_areas"], [{"name": "Westlands", "fee": 200.0}])
		self.assertEqual((out["shop_latitude"], out["shop_longitude"]), (-1.2, 36.8))
		self.assertEqual(out["road_factor"], df.DEFAULT_ROAD_FACTOR)

	def test_anything_left_out_is_kept_as_it_was(self):
		out = df.validate_rules({}, {"delivery_mode": "flat", "delivery_flat_fee": 150})
		self.assertEqual((out["delivery_mode"], out["delivery_flat_fee"]), ("flat", 150.0))

	def test_bad_forms_are_refused(self):
		bad = [
			{"delivery_mode": "drone"},
			{"delivery_flat_fee": -1},
			{"delivery_flat_fee": "lots"},
			{"delivery_flat_fee": "nan"},
			{"delivery_flat_fee": 10**9},
			{"road_factor": 0.5},
			{"road_factor": 10},
			{"distance_max_km": 10000},
			{"delivery_areas": "Westlands"},
			{"delivery_areas": ["Westlands"]},
			{"delivery_areas": [{"name": "", "fee": 10}]},
			{"delivery_areas": [{"name": "A", "fee": -5}]},
			{"delivery_areas": [{"name": "A", "fee": 1}, {"name": "a", "fee": 2}]},
			{"delivery_areas": [{"name": f"A{n}", "fee": 1} for n in range(df.MAX_AREAS + 1)]},
			{"shop_latitude": 95, "shop_longitude": 0},
		]
		for form in bad:
			with self.assertRaises(df.DeliveryRuleError, msg=str(form)[:60]):
				df.validate_rules(form, {})

	def test_what_is_missing_before_a_store_can_open(self):
		self.assertEqual(df.problems_before_opening(df.validate_rules({"delivery_mode": "free"}, {})), [])
		self.assertEqual(df.problems_before_opening(df.validate_rules({"delivery_mode": "flat"}, {})), [])
		self.assertEqual(len(df.problems_before_opening(df.validate_rules({"delivery_mode": "area"}, {}))), 1)
		self.assertEqual(
			len(df.problems_before_opening(df.validate_rules({"delivery_mode": "distance"}, {}))), 2
		)
		ready = df.validate_rules(
			{"delivery_mode": "distance", "distance_max_km": 20, "shop_latitude": 0, "shop_longitude": 36}, {}
		)
		self.assertEqual(df.problems_before_opening(ready), [])


if __name__ == "__main__":
	unittest.main()
