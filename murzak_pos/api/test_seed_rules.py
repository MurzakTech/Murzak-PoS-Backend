import json
import os
import unittest

from murzak_pos.api.seed_rules import (
	is_service_group,
	must_be_whole_number,
	resolve_uom,
	starter_product,
	stock_flag,
	unit_candidates,
)

SEED_FILE = os.path.join(os.path.dirname(__file__), "..", "products_seed_data", "industry_products.json")


class TestIsServiceGroup(unittest.TestCase):
	def test_the_services_group_is_a_service_whatever_its_case_or_spacing(self):
		for group in ("Services", "services", " SERVICES "):
			self.assertTrue(is_service_group(group), group)

	def test_goods_and_blanks_are_not_services(self):
		for group in ("Products", "Consumable", "Service Parts", "", None):
			self.assertFalse(is_service_group(group), group)


class TestStarterProduct(unittest.TestCase):
	def test_a_service_says_so_and_keeps_its_group_and_unit(self):
		item = starter_product(
			{"item_code": "SALON-001", "item_name": "Haircut Service", "item_group": "Services", "uom": "Nos"}
		)
		self.assertEqual(
			item,
			{
				"sku": "SALON-001",
				"name": "Haircut Service",
				"status": "available",
				"item_group": "Services",
				"uom": "Nos",
				"is_service": True,
			},
		)

	def test_a_weighed_good_keeps_its_unit_and_is_not_a_service(self):
		item = starter_product(
			{"item_code": "GRO-011", "item_name": "Cheese 250g", "item_group": "Products", "uom": "Kg"}
		)
		self.assertEqual((item["uom"], item["is_service"]), ("Kg", False))

	def test_a_template_with_no_unit_or_group_reads_as_pieces_and_not_a_service(self):
		item = starter_product({"item_code": "X-1", "item_name": "Thing", "item_group": None, "uom": None})
		self.assertEqual((item["uom"], item["item_group"], item["is_service"]), ("Nos", "", False))
		self.assertEqual(
			starter_product({"item_code": "X-1", "item_name": "Thing", "uom": "  "})["uom"], "Nos"
		)

	def test_the_fields_the_till_already_reads_are_unchanged(self):
		item = starter_product(
			{"item_code": "REST-001", "item_name": "Veg Burger", "item_group": "Consumable", "uom": "Nos"}
		)
		self.assertEqual((item["sku"], item["name"], item["status"]), ("REST-001", "Veg Burger", "available"))


class TestStockFlag(unittest.TestCase):
	def test_what_the_till_asked_for_wins(self):
		self.assertEqual(stock_flag(0, "Products"), 0)
		self.assertEqual(stock_flag(1, "Services"), 1)
		self.assertEqual(stock_flag(False), 0)
		self.assertEqual(stock_flag(True), 1)
		self.assertEqual(stock_flag("0"), 0)
		self.assertEqual(stock_flag("false"), 0)
		self.assertEqual(stock_flag("1"), 1)

	def test_an_older_till_that_says_nothing_gets_services_without_stock_and_goods_with(self):
		for nothing in (None, "", "  "):
			self.assertEqual(stock_flag(nothing, "Services"), 0)
			self.assertEqual(stock_flag(nothing, "Products"), 1)
			self.assertEqual(stock_flag(nothing, None), 1)


class TestUnits(unittest.TestCase):
	def test_the_unit_asked_for_comes_first_then_the_names_a_site_may_use(self):
		self.assertEqual(unit_candidates("L"), ["L", "Litre", "Liter", "Ltr"])
		self.assertEqual(unit_candidates("ml"), ["ml", "Millilitre", "Milliliter", "Ml"])
		self.assertEqual(unit_candidates("Kg")[0], "Kg")

	def test_a_unit_with_no_alias_is_looked_up_as_it_is(self):
		self.assertEqual(unit_candidates("Box"), ["Box"])

	def test_no_unit_means_pieces(self):
		for blank in (None, "", "  "):
			self.assertEqual(unit_candidates(blank), ["Nos"])

	def test_measures_can_be_sold_in_parts_and_pieces_cannot(self):
		for unit in ("Kg", "L", "ml", "Litre", "Gram", "g"):
			self.assertEqual(must_be_whole_number(unit), 0, unit)
		for unit in ("Nos", "Box", "Pair", "", None):
			self.assertEqual(must_be_whole_number(unit), 1, unit)


class TestResolveUom(unittest.TestCase):
	def resolve(self, requested, on_site):
		made = []

		def create(name, whole):
			made.append((name, whole))
			return name

		return resolve_uom(requested, lambda name: name in on_site, create), made

	def test_a_unit_the_site_has_is_used_and_nothing_is_made(self):
		self.assertEqual(self.resolve("Kg", {"Kg", "Nos"}), ("Kg", []))
		self.assertEqual(self.resolve(None, {"Nos"}), ("Nos", []))

	def test_the_same_unit_under_another_name_is_used_instead_of_making_a_second(self):
		self.assertEqual(self.resolve("L", {"Litre"}), ("Litre", []))
		self.assertEqual(self.resolve("ml", {"Millilitre"}), ("Millilitre", []))

	def test_the_name_asked_for_wins_over_its_other_names(self):
		self.assertEqual(self.resolve("L", {"L", "Litre"}), ("L", []))

	def test_a_missing_measure_is_made_so_that_it_can_be_sold_in_parts(self):
		self.assertEqual(self.resolve("L", {"Nos"}), ("L", [("L", 0)]))
		self.assertEqual(self.resolve("ml", set()), ("ml", [("ml", 0)]))

	def test_a_missing_piece_unit_is_made_for_whole_numbers_only(self):
		self.assertEqual(self.resolve("Crate", {"Nos"}), ("Crate", [("Crate", 1)]))

	def test_every_unit_in_the_starter_lists_resolves_on_a_site_with_only_the_basics(self):
		with open(SEED_FILE, encoding="utf-8") as f:
			lists = json.load(f)
		for industry, items in lists.items():
			for item in items:
				name, _made = self.resolve(item["uom"], {"Nos", "Kg"})
				self.assertTrue(name, (industry, item))


class TestTheStarterListsThemselves(unittest.TestCase):
	"""The checks that matter for real clients, run on the real lists."""

	@classmethod
	def setUpClass(cls):
		with open(SEED_FILE, encoding="utf-8") as f:
			cls.lists = json.load(f)

	def test_every_industry_has_25_items_each_with_a_group_and_a_unit(self):
		for industry, items in self.lists.items():
			self.assertEqual(len(items), 25, industry)
			for item in items:
				self.assertTrue(item["item_group"] and item["uom"], (industry, item))

	def test_every_unit_in_the_lists_is_one_this_code_understands(self):
		for industry, items in self.lists.items():
			for item in items:
				first = unit_candidates(item["uom"])[0]
				self.assertEqual(first, item["uom"], (industry, item))
				if item["uom"] != "Nos":
					self.assertEqual(must_be_whole_number(item["uom"]), 0, (industry, item))

	def test_the_services_are_found_where_the_industry_has_them(self):
		counts = {k: sum(is_service_group(i["item_group"]) for i in v) for k, v in self.lists.items()}
		self.assertEqual(counts["SALON"], 10)
		self.assertEqual(counts["HOTEL"], 15)
		self.assertEqual(counts["ONLINE"], 24)
		self.assertEqual(sum(counts.values()), 61)
		for goods_only in ("REST", "RETAIL", "GROCERY", "PHARMACY", "FASHION", "ELECTRONICS"):
			self.assertEqual(counts[goods_only], 0, goods_only)


if __name__ == "__main__":
	unittest.main()
