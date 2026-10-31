# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the rules behind editing a store and choosing who works at it. No database is needed."""

import inspect
import unittest

from murzak_pos.api import store_admin_calc as calc


class TestNames(unittest.TestCase):
	def test_a_name_is_one_tidy_line(self):
		self.assertEqual(calc.clean_name("  Duka   la  Pili "), "Duka la Pili")

	def test_a_missing_or_bad_name_is_refused_with_its_reason(self):
		for bad, code in (
			(None, "need_name"),
			("  ", "need_name"),
			("A", "bad_name"),
			("x" * 61, "bad_name"),
			("a<b", "bad_name"),
			("a/b", "bad_name"),
			("a\\b", "bad_name"),
		):
			with self.subTest(name=bad), self.assertRaises(calc.StoreAdminError) as caught:
				calc.clean_name(bad)
			self.assertEqual(caught.exception.code, code)

	def test_a_town_or_address_may_be_blank_but_not_hold_markup(self):
		self.assertEqual(calc.clean_text("  Moi   Avenue "), "Moi Avenue")
		self.assertEqual(calc.clean_text(None), "")
		with self.assertRaises(calc.StoreAdminError) as caught:
			calc.clean_text("<b>Nairobi</b>")
		self.assertEqual(caught.exception.code, "bad_text")


class TestPeople(unittest.TestCase):
	def test_people_may_arrive_as_a_list_or_as_json_text_and_each_counts_once(self):
		self.assertEqual(calc.clean_people(["a@x.test", " b@x.test ", "a@x.test"]), ["a@x.test", "b@x.test"])
		self.assertEqual(calc.clean_people('["a@x.test"]'), ["a@x.test"])

	def test_nobody_chosen_is_allowed(self):
		self.assertEqual(calc.clean_people([]), [])
		self.assertEqual(calc.clean_people(None or "[]"), [])

	def test_bad_lists_are_refused(self):
		for bad, code in (
			("not json", "bad_people"),
			({"a": 1}, "bad_people"),
			([1], "bad_people"),
			([""], "bad_people"),
			(["a"] * 0 + [f"u{n}@x.test" for n in range(201)], "too_many_people"),
		):
			with self.subTest(people=str(bad)[:20]), self.assertRaises(calc.StoreAdminError) as caught:
				calc.clean_people(bad)
			self.assertEqual(caught.exception.code, code)

	def test_the_plan_adds_the_new_and_removes_the_dropped(self):
		self.assertEqual(calc.plan_people({"a", "b"}, ["b", "c"]), (["c"], ["a"]))
		self.assertEqual(calc.plan_people(set(), ["a"]), (["a"], []))
		self.assertEqual(calc.plan_people({"a"}, []), ([], ["a"]))
		self.assertEqual(calc.plan_people({"a"}, ["a"]), ([], []))

	def test_the_chooser_marks_who_works_here_and_how_many_stores_each_has(self):
		users = [
			{"name": "z@x.test", "full_name": "Zawadi"},
			{"name": "a@x.test", "full_name": "amina"},
			{"name": "n@x.test", "full_name": ""},
		]
		perms = [
			{"user": "a@x.test", "for_value": "Duka Kuu - SA"},
			{"user": "a@x.test", "for_value": "Duka la Pili - SA"},
			{"user": "z@x.test", "for_value": "Duka la Pili - SA"},
		]
		rows = calc.members(users, perms, "Duka Kuu - SA")
		self.assertEqual([r["name"] for r in rows], ["amina", "n@x.test", "Zawadi"])
		self.assertEqual(rows[0], {"user": "a@x.test", "name": "amina", "assigned": True, "stores": 2})
		self.assertEqual(rows[1], {"user": "n@x.test", "name": "n@x.test", "assigned": False, "stores": 0})
		self.assertEqual(rows[2], {"user": "z@x.test", "name": "Zawadi", "assigned": False, "stores": 1})

	def test_someone_taken_off_their_last_store_can_use_every_store(self):
		self.assertEqual(calc.everywhere(["a", "b", "c"], {"b": 1}), ["a", "c"])
		self.assertEqual(calc.everywhere([], {}), [])


class TestThePureModuleNeedsNoDatabase(unittest.TestCase):
	def test_it_does_not_import_frappe(self):
		self.assertNotIn("import frappe", inspect.getsource(calc))


if __name__ == "__main__":
	unittest.main()
