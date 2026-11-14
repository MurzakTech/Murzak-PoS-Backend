import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import restaurant_settings_api as api
from murzak_pos.api import restaurant_settings_rules as rules

HAPPY = {
	"name": "After work",
	"days": ["Mon", "tue", "Wednesday", "thu", "fri"],
	"start": "17:00",
	"end": "19:00",
	"percent": 20,
	"groups": ["Beer"],
}
LATE = {"name": "Late", "days": ["sat"], "start": "22:00", "end": "02:00", "percent": 10, "items": ["TUSKER"]}


class TestCleanSettings(unittest.TestCase):
	def test_settings_are_tidied_and_kept(self):
		s = rules.clean_settings(
			{
				"happy_hours": [HAPPY],
				"service_charge_percent": "10",
				"platforms": [{"name": "Glovo", "commission_percent": 25}, {"name": "glovo"}],
				"tables": {"Main": ["T1", " T1 ", "T2", ""]},
			}
		)
		self.assertEqual(s["happy_hours"][0]["days"], ["mon", "tue", "wed", "thu", "fri"])
		self.assertEqual(s["service_charge_percent"], 10.0)
		self.assertEqual(s["platforms"], [{"name": "Glovo", "commission_percent": 25.0}])
		self.assertEqual(s["tables"], {"Main": ["T1", "T2"]})

	def test_settings_that_cannot_be_kept_are_refused_in_plain_words(self):
		bad = [
			{"happy_hours": [{**HAPPY, "days": []}]},
			{"happy_hours": [{**HAPPY, "start": "5pm"}]},
			{"happy_hours": [{**HAPPY, "end": "17:00"}]},
			{"happy_hours": [{**HAPPY, "percent": 0}]},
			{"happy_hours": [{**HAPPY, "groups": []}]},
			{"service_charge_percent": 30},
			{"service_charge_percent": "ten"},
			{"platforms": [{"name": "Glovo", "commission_percent": 120}]},
			"{oops",
		]
		for raw in bad:
			with self.assertRaises(rules.SettingsError, msg=repr(raw)[:60]):
				rules.clean_settings(raw)

	def test_nothing_set_gives_the_kenyan_platforms_and_no_charges(self):
		s = rules.load_settings(None)
		self.assertEqual([p["name"] for p in s["platforms"]], ["Glovo", "Uber Eats", "Bolt Food"])
		self.assertEqual((s["service_charge_percent"], s["happy_hours"]), (0.0, []))
		self.assertEqual(rules.load_settings("{broken"), rules.default_settings())


class TestHappyHourFor(unittest.TestCase):
	def setUp(self):
		self.rules = rules.clean_happy_hours([HAPPY, LATE])

	def test_inside_the_window_on_a_listed_day(self):
		self.assertEqual(rules.happy_hour_for(self.rules, "GUINNESS", "Beer", 0, "17:30")["percent"], 20)

	def test_outside_the_hours_or_the_days_or_the_menu(self):
		self.assertIsNone(rules.happy_hour_for(self.rules, "GUINNESS", "Beer", 0, "19:00"))
		self.assertIsNone(rules.happy_hour_for(self.rules, "GUINNESS", "Beer", 5, "17:30"))
		self.assertIsNone(rules.happy_hour_for(self.rules, "CHIPS", "Food", 0, "17:30"))

	def test_a_window_past_midnight_belongs_to_the_day_it_started(self):
		self.assertEqual(rules.happy_hour_for(self.rules, "TUSKER", "Beer", 5, "23:00")["name"], "Late")
		self.assertEqual(rules.happy_hour_for(self.rules, "TUSKER", "Beer", 6, "01:30")["name"], "Late")
		self.assertIsNone(rules.happy_hour_for(self.rules, "TUSKER", "Beer", 6, "23:00"))

	def test_the_largest_discount_wins(self):
		both = rules.clean_happy_hours([HAPPY, {**HAPPY, "name": "Big", "percent": 50}])
		self.assertEqual(rules.happy_hour_for(both, "X", "Beer", 1, "18:00")["name"], "Big")


class TestSaveSettings(unittest.TestCase):
	def save(self, settings):
		db = MagicMock()
		with (
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api.role_tiers, "require_access") as require_access,
			patch.object(api.role_tiers, "can", return_value=True),
			patch.object(api, "resolve_company", return_value="Bar Ltd"),
			patch.object(api, "ensure_service_item") as ensure_item,
			patch.object(api, "_service_item_code", return_value="SERVICE-CHARGE-BL"),
			patch.object(api, "read_settings", side_effect=lambda c: rules.clean_settings(settings)),
		):
			result = api.save_restaurant_settings(settings)
		return result, db, require_access, ensure_item

	def test_owner_and_managers_save_and_a_service_charge_gets_its_item(self):
		result, db, require_access, ensure_item = self.save({"service_charge_percent": 10})
		require_access.assert_called_once_with("profit")
		ensure_item.assert_called_once_with("Bar Ltd")
		self.assertEqual(db.set_default.call_args.args[0], "murzak_restaurant:Bar Ltd")
		self.assertEqual(result["data"]["service_charge_item"], "SERVICE-CHARGE-BL")

	def test_no_service_charge_makes_no_item(self):
		_result, _db, _access, ensure_item = self.save({"service_charge_percent": 0})
		ensure_item.assert_not_called()

	def test_settings_that_cannot_be_kept_change_nothing(self):
		with self.assertRaises(frappe.ValidationError):
			self.save({"service_charge_percent": 99})


if __name__ == "__main__":
	unittest.main()
