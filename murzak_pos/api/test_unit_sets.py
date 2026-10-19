"""Tests for the industry unit lists and rules. They need no database."""

import unittest

from murzak_pos.api import unit_sets as sets


class TestLists(unittest.TestCase):
	def test_every_industry_has_units_and_names_are_unique_in_a_list(self):
		for key, units in sets.INDUSTRY_UNITS.items():
			names = [u["name"].lower() for u in units]
			self.assertTrue(units, key)
			self.assertEqual(len(names), len(set(names)), key)

	def test_the_nine_industries_are_there(self):
		self.assertEqual(
			set(sets.INDUSTRIES),
			{
				"pharmacy",
				"restaurant",
				"hotel",
				"butchery_groceries",
				"hardware",
				"services",
				"wholesale",
				"retail",
				"agriculture",
			},
		)

	def test_the_examples_of_the_brief_are_present(self):
		def names(key):
			return {u["singular"] for u in sets.units_for(key)}

		self.assertTrue(
			{"tablet", "capsule", "strip", "bottle", "vial", "sachet", "box"} <= names("pharmacy")
		)
		self.assertTrue({"piece", "metre", "bag", "tonne", "roll", "litre"} <= names("hardware"))
		self.assertTrue({"kilogram", "gram", "portion", "carcass"} <= names("butchery_groceries"))

	def test_no_text_has_an_em_dash(self):
		for units in sets.INDUSTRY_UNITS.values():
			for u in units:
				for field in ("name", "singular", "plural", "note"):
					self.assertNotIn("—", u[field])

	def test_parts_only_for_measures(self):
		by_name = {u["name"]: u for units in sets.INDUSTRY_UNITS.values() for u in units}
		for name in ("Nos", "Tablet", "Capsule", "Strip", "Carcass", "Box"):
			self.assertTrue(by_name[name]["whole"], name)
		for name in ("Kg", "Gram", "Litre", "Meter", "Tonne", "Millilitre"):
			self.assertFalse(by_name[name]["whole"], name)

	def test_the_tonne_note_gives_the_conversion(self):
		self.assertIn("1,000 kilograms", sets._unit.__globals__["TONNE"]["note"])


class TestIndustryFor(unittest.TestCase):
	def test_the_owners_choice_wins(self):
		self.assertEqual(sets.industry_for("Hardware", "PHARMACY", "pharmacy"), "hardware")

	def test_sign_up_then_kind_then_general(self):
		self.assertEqual(sets.industry_for(None, "rest", None), "restaurant")
		self.assertEqual(sets.industry_for(None, None, "pharmacy"), "pharmacy")
		self.assertEqual(sets.industry_for(None, "FASHION", "retail"), "retail")
		self.assertEqual(sets.industry_for("nonsense", "", ""), "retail")

	def test_clean_industry(self):
		self.assertEqual(sets.clean_industry("  Butchery_Groceries "), "butchery_groceries")
		self.assertIsNone(sets.clean_industry("x"))
		self.assertIsNone(sets.clean_industry(None))


class TestPlan(unittest.TestCase):
	def test_an_empty_site_creates_everything(self):
		plan = sets.plan_units("pharmacy", [])
		self.assertTrue(all(p["create"] for p in plan))

	def test_existing_units_are_used_not_added_again_ignoring_case(self):
		plan = sets.plan_units("pharmacy", ["nos", "TABLET", "Bottle"])
		by = {p["unit"]["name"]: p for p in plan}
		self.assertFalse(by["Nos"]["create"])
		self.assertEqual(by["Nos"]["stored"], "nos")
		self.assertFalse(by["Tablet"]["create"])
		self.assertEqual(by["Tablet"]["stored"], "TABLET")
		self.assertTrue(by["Capsule"]["create"])

	def test_a_usual_other_name_counts(self):
		plan = sets.plan_units("hardware", ["Piece", "Metre", "Ltr"])
		by = {p["unit"]["name"]: p for p in plan}
		self.assertEqual(by["Nos"]["stored"], "Piece")
		self.assertEqual(by["Meter"]["stored"], "Metre")
		self.assertEqual(by["Litre"]["stored"], "Ltr")
		self.assertFalse(by["Litre"]["create"])

	def test_planning_twice_is_the_same(self):
		self.assertEqual(sets.plan_units("retail", ["Nos"]), sets.plan_units("retail", ["Nos"]))


class TestCustomWords(unittest.TestCase):
	def test_good_words(self):
		for text in ("Jerrycan", "half-dozen", "Mt. Kenya bag", "20L can"):
			self.assertIsNone(sets.check_word(text), text)

	def test_bad_words(self):
		self.assertEqual(sets.check_word(""), "empty")
		self.assertEqual(sets.check_word("   "), "empty")
		self.assertEqual(sets.check_word("a"), "short")
		self.assertEqual(sets.check_word("x" * 31), "long")
		self.assertEqual(sets.check_word("box<script>"), "characters")
		self.assertEqual(sets.check_word("12-34"), "characters")
		self.assertEqual(sets.check_word("a—b"), "characters")

	def test_stored_name(self):
		self.assertEqual(sets.stored_name("  jerry   can "), "Jerry can")

	def test_duplicates_ignore_case_and_know_the_curated_words(self):
		self.assertTrue(sets.is_duplicate("KG", ["Kg"]))
		self.assertTrue(sets.is_duplicate(" jerrycan ", ["Jerrycan"]))
		self.assertTrue(sets.is_duplicate("tablets", []))
		self.assertTrue(sets.is_duplicate("Cans", []))
		self.assertTrue(sets.is_duplicate("Tin", [], custom=[{"name": "x", "plural": "tin"}]))
		self.assertFalse(sets.is_duplicate("Jerrycan", ["Kg"]))

	def test_saved_text_round_trips_and_bad_text_is_nothing(self):
		rows = [{"name": "Jerrycan", "plural": "jerrycans"}]
		self.assertEqual(sets.parse_custom(sets.dump_custom(rows)), rows)
		for bad in (None, "", "not json", "{}", "[1,2]", '[{"name":""}]'):
			self.assertEqual(sets.parse_custom(bad), [], bad)

	def test_saved_rows_with_a_repeat_or_a_bad_plural_are_repaired(self):
		text = '[{"name":"Jerrycan","plural":"<b>"},{"name":"jerrycan","plural":"x"}]'
		self.assertEqual(sets.parse_custom(text), [{"name": "Jerrycan", "plural": "Jerrycan"}])
