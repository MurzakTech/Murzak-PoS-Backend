import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import menu_options_api as api
from murzak_pos.api import menu_options_rules as rules

GROUPS = [
	{"name": "How cooked", "required": True, "multiple": False, "choices": ["Rare", "Medium", "Well done"]},
	{"name": "Extras", "required": False, "multiple": True, "choices": ["Extra cheese +50", "Bacon + 80.5"]},
	{"name": "Leave out", "multiple": True, "choices": ["No onions", "No chilli", ""]},
]


class TestCleanGroups(unittest.TestCase):
	def test_choices_are_read_as_owners_type_them(self):
		self.assertEqual(rules.parse_choice("Extra cheese +50"), {"name": "Extra cheese", "price": 50.0})
		self.assertEqual(rules.parse_choice("Bacon + 80.5"), {"name": "Bacon", "price": 80.5})
		self.assertEqual(rules.parse_choice("  No onions "), {"name": "No onions", "price": 0.0})
		self.assertIsNone(rules.parse_choice("   "))

	def test_groups_are_tidied_and_kept(self):
		groups = rules.clean_groups(GROUPS)
		self.assertEqual([g["name"] for g in groups], ["How cooked", "Extras", "Leave out"])
		self.assertTrue(groups[0]["required"])
		self.assertFalse(groups[0]["multiple"])
		self.assertEqual(groups[1]["choices"][1], {"name": "Bacon", "price": 80.5})
		self.assertEqual(len(groups[2]["choices"]), 2)  # the empty entry is dropped
		# What is kept can be read back exactly
		self.assertEqual(rules.load_groups(rules.dump_groups(groups)), groups)

	def test_nothing_means_no_options(self):
		for raw in (None, "", "[]", []):
			self.assertEqual(rules.clean_groups(raw), [])
		self.assertEqual(rules.dump_groups([]), "")

	def test_options_that_cannot_be_kept_are_refused_in_plain_words(self):
		bad = [
			"{not json",
			{"name": "x"},
			[{"name": "", "choices": ["A"]}],
			[{"name": "Extras", "choices": []}],
			[{"name": "Extras", "choices": ["A", "a"]}],
			[{"name": "Extras", "choices": ["A"]}, {"name": "extras", "choices": ["B"]}],
			[{"name": "Extras", "choices": [{"name": "A", "price": -5}]}],
			[{"name": f"G{i}", "choices": ["A"]} for i in range(rules.MAX_GROUPS + 1)],
			[{"name": "Extras", "choices": [f"C{i}" for i in range(rules.MAX_CHOICES + 1)]}],
		]
		for raw in bad:
			with self.assertRaises(rules.OptionsError, msg=repr(raw)[:60]):
				rules.clean_groups(raw)

	def test_unreadable_kept_options_never_break_the_till(self):
		self.assertEqual(rules.load_groups("{broken"), [])


class TestChoose(unittest.TestCase):
	def setUp(self):
		self.groups = rules.clean_groups(GROUPS)

	def test_a_choice_adds_its_price_and_says_what_the_kitchen_needs(self):
		chosen, extra, words = rules.choose(
			self.groups,
			{"How cooked": ["Well done"], "Extras": ["Extra cheese", "Bacon"], "Leave out": ["No onions"]},
		)
		self.assertEqual(extra, 130.5)
		self.assertEqual(words, "Well done; Extra cheese (+50); Bacon (+80.5); No onions")
		self.assertEqual(chosen[0], {"group": "How cooked", "name": "Well done", "price": 0.0})

	def test_a_required_group_must_be_chosen_and_choose_one_means_one(self):
		with self.assertRaises(rules.OptionsError):
			rules.choose(self.groups, {})
		with self.assertRaises(rules.OptionsError):
			rules.choose(self.groups, {"How cooked": ["Rare", "Medium"]})

	def test_a_choice_not_on_the_menu_is_refused(self):
		with self.assertRaises(rules.OptionsError):
			rules.choose(self.groups, {"How cooked": ["Burnt"]})


class TestSetMenuOptions(unittest.TestCase):
	def run_set(self, groups, has_field=True):
		db = MagicMock()
		db.exists.return_value = True
		with (
			patch.object(frappe, "db", db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch.object(api, "require_catalogue_role"),
			patch.object(api, "resolve_company", return_value="Bar Ltd"),
			patch.object(api, "ensure_record_access"),
			patch.object(api, "_has_field", return_value=has_field),
		):
			return api.set_menu_options("BURGER", groups), db

	def test_options_are_kept_on_the_dish(self):
		result, db = self.run_set(GROUPS)
		self.assertEqual(db.set_value.call_args.args[:3], ("Item", "BURGER", "custom_menu_options"))
		self.assertEqual(len(result["data"]["groups"]), 3)

	def test_options_that_cannot_be_kept_change_nothing(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_set([{"name": "Extras", "choices": []}])

	def test_a_site_without_the_field_says_so(self):
		with self.assertRaises(frappe.ValidationError):
			self.run_set(GROUPS, has_field=False)


class TestListMenuOptions(unittest.TestCase):
	def test_only_dishes_with_readable_options_are_listed(self):
		rows = [
			frappe._dict(name="BURGER", custom_menu_options=rules.dump_groups(rules.clean_groups(GROUPS))),
			frappe._dict(name="BROKEN", custom_menu_options="{oops"),
		]
		with (
			patch.object(api, "resolve_company", return_value="Bar Ltd"),
			patch.object(api, "_has_field", return_value=True),
			patch.object(api.frappe, "get_all", return_value=rows),
		):
			options = api.list_menu_options()["data"]["options"]
		self.assertEqual(list(options), ["BURGER"])


if __name__ == "__main__":
	unittest.main()
