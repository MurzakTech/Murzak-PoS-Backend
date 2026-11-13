import unittest

from murzak_pos.api import industry_profile as calc
from murzak_pos.api.brand_api import ACCENTS


class TestResolve(unittest.TestCase):
	def test_the_outlet_wins_over_the_business(self):
		self.assertEqual(calc.resolve("REST", "PHARMACY", "pharmacy"), ("restaurant", "outlet"))

	def test_an_outlet_set_to_an_industry_with_no_profile_is_a_general_shop_on_purpose(self):
		# The owner chose Retail for this store: the business's pharmacy shop type must not leak into it
		self.assertEqual(calc.resolve("RETAIL", "PHARMACY", "pharmacy"), ("general", "outlet"))

	def test_with_no_outlet_industry_the_shop_type_then_the_business_kind_decide(self):
		self.assertEqual(calc.resolve(None, "REST", "pharmacy"), ("restaurant", "shop_type"))
		self.assertEqual(calc.resolve("", "RETAIL", "pharmacy"), ("pharmacy", "business_kind"))
		self.assertEqual(calc.resolve("  ", None, "Pharmacy "), ("pharmacy", "business_kind"))

	def test_nothing_known_is_a_general_shop(self):
		self.assertEqual(calc.resolve(None, None, None), ("general", "default"))
		self.assertEqual(calc.resolve(None, "SALON", "services"), ("general", "default"))

	def test_codes_are_read_whatever_their_case(self):
		self.assertEqual(calc.key_for_industry(" pharmacy "), "pharmacy")
		self.assertEqual(calc.key_for_industry("rest"), "restaurant")
		self.assertIsNone(calc.key_for_industry("HOTEL"))


class TestProfile(unittest.TestCase):
	def test_every_profile_has_every_feature_and_every_word(self):
		for key in calc.KEYS:
			p = calc.profile(key)
			self.assertEqual(set(p["features"]), set(calc.FEATURE_NAMES), key)
			self.assertEqual(set(p["words"]), set(calc.WORD_KEYS), key)
			self.assertTrue(all(p["words"].values()), key)

	def test_a_general_shop_switches_nothing_on(self):
		self.assertFalse(any(calc.profile("general")["features"].values()))
		self.assertEqual(calc.profile("nonsense")["key"], "general")

	def test_a_pharmacy_gets_the_dispensing_features_and_not_the_restaurant_ones(self):
		f = calc.profile("pharmacy")["features"]
		self.assertTrue(
			f["prescription_only"] and f["dispensing_register"] and f["antibiotics_prescription_only"]
		)
		self.assertFalse(f["tables"] or f["void_approval"])

	def test_a_restaurant_gets_the_floor_features_and_not_the_pharmacy_ones(self):
		f = calc.profile("restaurant")["features"]
		self.assertTrue(f["tables"] and f["kitchen_tickets"] and f["void_approval"] and f["waiter_cashup"])
		self.assertFalse(f["prescription_only"] or f["dispensing_register"])

	def test_each_trade_speaks_its_own_words(self):
		self.assertEqual(calc.profile("pharmacy")["words"]["customer"], "Patient")
		self.assertEqual(calc.profile("restaurant")["words"]["charge"], "Settle bill")
		self.assertEqual(calc.profile("general")["words"]["charge"], "Charge")

	def test_the_starting_accent_is_one_the_brand_settings_allow(self):
		for key in calc.KEYS:
			self.assertIn(calc.profile(key)["look"]["accent"], ACCENTS)

	def test_a_profile_is_a_copy_so_changing_it_changes_nothing_shared(self):
		calc.profile("pharmacy")["words"]["customer"] = "Changed"
		calc.profile("pharmacy")["look"]["accent"] = "red"
		self.assertEqual(calc.profile("pharmacy")["words"]["customer"], "Patient")
		self.assertEqual(calc.profile("pharmacy")["look"]["accent"], "green")


class TestAnyHas(unittest.TestCase):
	def test_a_business_with_one_pharmacy_counter_has_the_pharmacy_features(self):
		self.assertTrue(calc.any_has({"general", "pharmacy"}, "antibiotics_prescription_only"))
		self.assertFalse(calc.any_has({"general", "restaurant"}, "antibiotics_prescription_only"))
		self.assertFalse(calc.any_has(set(), "tables"))


if __name__ == "__main__":
	unittest.main()
