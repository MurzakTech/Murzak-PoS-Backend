"""
Tests for the industry units calls. They need no database: every Frappe call is mocked.

What they pin down: the units of the right industry come back, adding is idempotent and creates only what is
missing, an existing unit is never changed, "whole numbers only" is set on new units only, custom units are
checked, and only an owner or a manager may write.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import unit_sets as sets
from murzak_pos.api import units_api

OWNER_ROLES = ("System Manager",)
MANAGER_ROLES = ("Sales Manager", "Sales User")
CASHIER_ROLES = ("Sales User",)


class UnitsCase(unittest.TestCase):
	def as_user(
		self, user="owner@shop-a.test", roles=OWNER_ROLES, uoms=("Nos", "Kg"), stored=None, signup=None
	):
		self.stored = dict(stored or {})
		self.uoms = list(uoms)
		self.inserted = []
		db = MagicMock()
		db.exists.return_value = True
		db.get_default.side_effect = lambda key, *a, **k: self.stored.get(key)
		db.set_default.side_effect = lambda key, value, *a, **k: self.stored.__setitem__(key, value)
		db.get_value.side_effect = lambda *a, **k: signup
		self.db = db

		def new_doc(doctype):
			doc = MagicMock()
			doc.doctype = doctype

			def insert(**kwargs):
				if doc.uom_name.lower() in {u.lower() for u in self.uoms}:
					raise frappe.DuplicateEntryError("exists")
				doc.name = doc.uom_name
				self.uoms.append(doc.uom_name)
				self.inserted.append((doc.uom_name, doc.must_be_whole_number))

			doc.insert.side_effect = insert
			return doc

		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_all", create=True, side_effect=lambda *a, **k: list(self.uoms)),
			patch.object(frappe, "new_doc", create=True, side_effect=new_doc),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", return_value={"Shop A"}),
			patch("murzak_pos.api.units_api._business_kind", return_value=None),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestReading(UnitsCase):
	def test_a_shop_with_nothing_chosen_gets_the_general_list(self):
		self.as_user()
		data = units_api.get_industry_units()["data"]
		self.assertEqual(data["industry"], "retail")
		self.assertEqual(data["units"][0]["name"], "Nos")
		self.assertTrue(data["units"][0]["exists"])
		self.assertGreater(data["missing"], 0)
		self.assertEqual(data["industries"], list(sets.INDUSTRIES))

	def test_the_signup_industry_is_used(self):
		self.as_user(signup="PHARMACY")
		self.assertEqual(units_api.get_industry_units()["data"]["industry"], "pharmacy")

	def test_the_owners_choice_beats_signup(self):
		self.as_user(
			signup="PHARMACY", stored={units_api._key(units_api.INDUSTRY_PREFIX, "Shop A"): "hardware"}
		)
		self.assertEqual(units_api.get_industry_units()["data"]["industry"], "hardware")

	def test_an_industry_can_be_asked_for_and_a_bad_one_is_refused(self):
		self.as_user()
		self.assertEqual(units_api.get_industry_units("agriculture")["data"]["industry"], "agriculture")
		with self.assertRaises(frappe.ValidationError):
			units_api.get_industry_units("mining")

	def test_reading_changes_nothing_and_a_cashier_may_read(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		data = units_api.get_industry_units()["data"]
		self.assertFalse(data["can_manage"])
		self.assertEqual(self.inserted, [])

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			units_api.get_industry_units()

	def test_custom_units_are_listed_only_while_the_unit_exists(self):
		saved = sets.dump_custom(
			[{"name": "Jerrycan", "plural": "jerrycans"}, {"name": "Gone", "plural": "gones"}]
		)
		self.as_user(
			uoms=("Nos", "Jerrycan"), stored={units_api._key(units_api.CUSTOM_PREFIX, "Shop A"): saved}
		)
		self.assertEqual(
			units_api.get_industry_units()["data"]["custom"], [{"name": "Jerrycan", "plural": "jerrycans"}]
		)


class TestEnsure(UnitsCase):
	def test_missing_units_are_created_and_a_second_call_creates_nothing(self):
		self.as_user(signup="PHARMACY", uoms=("Nos",))
		first = units_api.ensure_industry_units()["data"]
		self.assertIn("Tablet", first["created"])
		self.assertNotIn("Nos", first["created"])
		self.assertEqual(first["missing"], 0)
		count = len(self.inserted)
		second = units_api.ensure_industry_units()["data"]
		self.assertEqual(second["created"], [])
		self.assertEqual(len(self.inserted), count)

	def test_whole_number_flag_follows_the_unit_and_only_new_units_are_touched(self):
		self.as_user(uoms=("Nos",))
		units_api.ensure_industry_units("hardware")
		flags = dict(self.inserted)
		self.assertEqual(flags["Tonne"], 0)
		self.assertEqual(flags["Meter"], 0)
		self.assertEqual(flags["Bag"], 1)
		self.assertNotIn("Nos", flags)

	def test_an_existing_unit_under_another_name_is_not_duplicated(self):
		self.as_user(uoms=("Piece", "Metre"))
		units_api.ensure_industry_units("hardware")
		names = [n.lower() for n, _ in self.inserted]
		self.assertNotIn("nos", names)
		self.assertNotIn("meter", names)

	def test_a_unit_added_by_someone_else_a_moment_ago_is_fine(self):
		self.as_user(uoms=("Nos",))
		original = frappe.new_doc

		def racing(doctype):
			doc = original(doctype)
			insert = doc.insert.side_effect

			def insert_after_race(**kwargs):
				if doc.uom_name == "Tablet":
					raise frappe.DuplicateEntryError("exists")
				insert(**kwargs)

			doc.insert.side_effect = insert_after_race
			return doc

		with patch.object(frappe, "new_doc", create=True, side_effect=racing):
			data = units_api.ensure_industry_units("pharmacy")["data"]
		self.assertNotIn("Tablet", data["created"])

	def test_a_cashier_cannot_add_units_and_nothing_is_written(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			units_api.ensure_industry_units()
		self.assertEqual(self.inserted, [])

	def test_a_manager_may(self):
		self.as_user("boss@shop-a.test", roles=MANAGER_ROLES, uoms=())
		self.assertTrue(units_api.ensure_industry_units()["data"]["created"])

	def test_a_bad_industry_is_refused_before_writing(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			units_api.ensure_industry_units("mining")
		self.assertEqual(self.inserted, [])


class TestIndustryChoice(UnitsCase):
	def test_it_is_kept_and_read_back(self):
		self.as_user()
		self.assertEqual(units_api.set_units_industry("Wholesale")["data"]["industry"], "wholesale")
		self.assertEqual(units_api.get_industry_units()["data"]["industry"], "wholesale")

	def test_nonsense_and_cashiers_are_refused(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			units_api.set_units_industry("mining")
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			units_api.set_units_industry("wholesale")
		self.db.set_default.assert_not_called()


class TestCustomUnit(UnitsCase):
	def test_a_good_unit_is_made_whole_by_default_and_remembered(self):
		self.as_user()
		data = units_api.add_custom_unit("jerrycan", "jerrycans")["data"]
		self.assertEqual(data["added"], "Jerrycan")
		self.assertEqual(self.inserted, [("Jerrycan", 1)])
		self.assertEqual(data["custom"], [{"name": "Jerrycan", "plural": "jerrycans"}])

	def test_parts_can_be_allowed(self):
		self.as_user()
		units_api.add_custom_unit("Scoop", "scoops", allow_parts="1")
		self.assertEqual(self.inserted, [("Scoop", 0)])

	def test_a_repeat_ignoring_case_is_refused(self):
		self.as_user()
		units_api.add_custom_unit("Jerrycan", "jerrycans")
		for again in ("jerrycan", " JERRYCAN ", "kg", "Tablets"):
			with self.subTest(again=again), self.assertRaises(frappe.ValidationError):
				units_api.add_custom_unit(again, "xx")
		self.assertEqual(len(self.inserted), 1)

	def test_bad_names_and_plurals_are_refused_and_nothing_is_written(self):
		self.as_user()
		for name, plural in (
			("", "x"),
			("a", "as"),
			("x" * 31, "xs"),
			("Ok<b>", "oks"),
			("Okay", ""),
			("Okay", "<i>"),
		):
			with self.subTest(name=name, plural=plural), self.assertRaises(frappe.ValidationError):
				units_api.add_custom_unit(name, plural)
		self.assertEqual(self.inserted, [])

	def test_a_cashier_is_refused(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			units_api.add_custom_unit("Jerrycan", "jerrycans")
		self.assertEqual(self.inserted, [])

	def test_the_most_is_enforced(self):
		rows = [
			{"name": f"Unit{chr(97 + i % 26)}{chr(97 + i // 26)}", "plural": "xx"}
			for i in range(sets.MAX_CUSTOM_UNITS)
		]
		self.as_user(stored={units_api._key(units_api.CUSTOM_PREFIX, "Shop A"): sets.dump_custom(rows)})
		with self.assertRaises(frappe.ValidationError):
			units_api.add_custom_unit("Another", "anothers")


class TestWiring(unittest.TestCase):
	def test_writes_are_post_only_and_reads_are_not(self):
		for fn in (units_api.ensure_industry_units, units_api.set_units_industry, units_api.add_custom_unit):
			self.assertIn(fn, frappe.whitelisted)
