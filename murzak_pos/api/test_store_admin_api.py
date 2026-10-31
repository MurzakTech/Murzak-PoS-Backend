# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for editing a store and choosing who works at it. No database is needed: every Frappe call is mocked. They check
who is let in, that nothing is read or changed for someone who is refused, that only people of the business can be
chosen, and that a person taken off their last store is reported.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import store_admin_api as api

STORE = "Duka Kuu - SA"
STORE_2 = "Duka la Pili - SA"
PEOPLE = [
	{"name": "amina@shop-a.test", "full_name": "Amina"},
	{"name": "juma@shop-a.test", "full_name": "Juma"},
]


class Case(unittest.TestCase):
	def start(self, target, **kwargs):
		patcher = patch.object(*target, **kwargs) if isinstance(target, tuple) else patch(target, **kwargs)
		mock = patcher.start()
		self.addCleanup(patcher.stop)
		return mock

	def as_user(self, roles=("System Manager",), user="owner@shop-a.test", permissions=()):
		self.db = MagicMock()
		self.db.get_value.return_value = "Shop A"
		self.db.exists.return_value = None
		self.db.has_column.return_value = True
		self.permissions = list(permissions)
		self.start((frappe, "session"), new=frappe._dict(user=user))
		self.start((frappe, "get_roles"), create=True, side_effect=lambda user=None: list(roles))
		self.start((frappe, "db"), new=self.db, create=True)
		self.start("frappe.translate.get_all_translations", return_value={})
		self.start("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		self.start("murzak_pos.api.access_control.frappe.db", new=self.db, create=True)
		self.start((api, "resolve_company"), return_value="Shop A")
		self.start((api, "get_allowed_warehouses_for_user"), return_value=None)

		def get_all(doctype, **kwargs):
			return list(PEOPLE) if doctype == "User" else list(self.permissions)

		self.start((frappe, "get_all"), create=True, side_effect=get_all)
		self.deleted, self.created = [], []
		self.start((frappe, "delete_doc"), create=True, side_effect=lambda *a, **k: self.deleted.append(a))

		def new_doc(doctype):
			doc = MagicMock()
			doc.insert.side_effect = lambda **k: self.created.append(doc)
			return doc

		self.start((frappe, "new_doc"), create=True, side_effect=new_doc)


class TestEditStore(Case):
	def setUp(self):
		self.as_user(roles=("Stock Manager",))
		self.doc = MagicMock()
		self.doc.name = STORE
		self.doc.warehouse_name = "Duka Kuu"
		self.doc.city = "Nairobi"
		self.doc.address_line_1 = "Moi Avenue"
		self.start((frappe, "get_doc"), create=True, return_value=self.doc)

	def test_a_stock_manager_changes_the_name_town_and_address(self):
		result = api.edit_store(STORE, store_name=" Duka  Kuu Mpya ", city="Mombasa", address="Digo Road")
		self.assertEqual(
			(self.doc.warehouse_name, self.doc.city, self.doc.address_line_1),
			("Duka Kuu Mpya", "Mombasa", "Digo Road"),
		)
		self.doc.save.assert_called_once_with(ignore_permissions=True)
		self.assertTrue(result["success"])

	def test_what_is_left_out_stays_as_it_is(self):
		api.edit_store(STORE, city="Mombasa")
		self.assertEqual((self.doc.warehouse_name, self.doc.address_line_1), ("Duka Kuu", "Moi Avenue"))

	def test_a_blank_town_clears_it(self):
		api.edit_store(STORE, city="  ")
		self.assertEqual(self.doc.city, "")

	def test_a_name_another_store_uses_is_refused_in_plain_words(self):
		self.db.exists.return_value = STORE_2
		with self.assertRaises(frappe.ValidationError) as caught:
			api.edit_store(STORE, store_name="Duka la Pili")
		self.assertIn("You already have a store called Duka la Pili.", str(caught.exception))
		self.doc.save.assert_not_called()

	def test_a_bad_name_or_text_is_refused(self):
		for kwargs in ({"store_name": "A"}, {"store_name": "a/b"}, {"city": "<b>x"}):
			with self.subTest(kwargs=kwargs), self.assertRaises(frappe.ValidationError):
				api.edit_store(STORE, **kwargs)
		self.doc.save.assert_not_called()

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.return_value = "Shop B"
		with self.assertRaises(frappe.PermissionError):
			api.edit_store(STORE, store_name="Duka Kuu")
		self.doc.save.assert_not_called()

	def test_someone_limited_to_other_stores_cannot_edit_this_one(self):
		self.start((api, "get_allowed_warehouses_for_user"), return_value=[STORE_2])
		with self.assertRaises(frappe.PermissionError):
			api.edit_store(STORE, store_name="Duka Kuu")
		self.doc.save.assert_not_called()

	def test_a_cashier_or_a_stock_user_cannot_edit_a_store(self):
		for roles in (("Sales User",), ("Stock User",)):
			with self.subTest(roles=roles):
				self.as_user(roles=roles)
				with self.assertRaises(frappe.PermissionError):
					api.edit_store(STORE, store_name="Duka Kuu")
				self.doc.save.assert_not_called()


class TestWhoWorksHere(Case):
	def setUp(self):
		self.as_user(
			permissions=[
				{"name": "UP-1", "user": "amina@shop-a.test", "for_value": STORE},
				{"name": "UP-2", "user": "amina@shop-a.test", "for_value": STORE_2},
			]
		)

	def test_the_chooser_lists_the_people_and_marks_who_works_here(self):
		data = api.get_store_people(STORE)["data"]
		self.assertEqual(data["warehouse"], STORE)
		self.assertEqual(
			data["members"],
			[
				{"user": "amina@shop-a.test", "name": "Amina", "assigned": True, "stores": 2},
				{"user": "juma@shop-a.test", "name": "Juma", "assigned": False, "stores": 0},
			],
		)

	def test_adding_a_person_makes_one_permission_and_nothing_else_changes(self):
		result = api.set_store_people(STORE, ["amina@shop-a.test", "juma@shop-a.test"])["data"]
		self.assertEqual(
			(result["added"], result["removed"], result["everywhere"]), (["juma@shop-a.test"], [], [])
		)
		self.assertEqual(len(self.created), 1)
		made = self.created[0]
		self.assertEqual((made.user, made.allow, made.for_value), ("juma@shop-a.test", "Warehouse", STORE))
		self.assertEqual(self.deleted, [])

	def test_taking_someone_off_one_of_two_stores_leaves_them_limited(self):
		result = api.set_store_people(STORE, [])["data"]
		self.assertEqual((result["removed"], result["everywhere"]), (["amina@shop-a.test"], []))
		self.assertEqual([d[1] for d in self.deleted], ["UP-1"])

	def test_taking_someone_off_their_last_store_is_reported(self):
		self.as_user(permissions=[{"name": "UP-1", "user": "amina@shop-a.test", "for_value": STORE}])
		result = api.set_store_people(STORE, [])["data"]
		self.assertEqual(result["everywhere"], ["amina@shop-a.test"])

	def test_someone_not_in_this_business_cannot_be_chosen(self):
		with self.assertRaises(frappe.PermissionError):
			api.set_store_people(STORE, ["stranger@shop-b.test"])
		self.assertEqual((self.created, self.deleted), ([], []))

	def test_a_bad_list_is_refused_before_anything_changes(self):
		with self.assertRaises(frappe.ValidationError):
			api.set_store_people(STORE, "not json")
		self.assertEqual((self.created, self.deleted), ([], []))

	def test_a_store_of_another_business_is_refused(self):
		self.db.get_value.return_value = "Shop B"
		for call in (lambda: api.get_store_people(STORE), lambda: api.set_store_people(STORE, [])):
			with self.subTest(), self.assertRaises(frappe.PermissionError):
				call()
		self.assertEqual((self.created, self.deleted), ([], []))

	def test_only_the_owner_may_see_or_set_who_works_here(self):
		for roles in (("Stock Manager",), ("Sales User",), ("Stock User",)):
			with self.subTest(roles=roles):
				self.as_user(roles=roles)
				with self.assertRaises(frappe.PermissionError):
					api.get_store_people(STORE)
				with self.assertRaises(frappe.PermissionError):
					api.set_store_people(STORE, ["amina@shop-a.test"])
				self.assertEqual((self.created, self.deleted), ([], []))


class TestTheSourceKeepsItsPromises(unittest.TestCase):
	def test_the_calls_are_whitelisted_and_the_writes_are_post_only(self):
		for function in (api.edit_store, api.get_store_people, api.set_store_people):
			self.assertIn(function, frappe.whitelisted)
		for function in (api.edit_store, api.set_store_people):
			self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])


if __name__ == "__main__":
	unittest.main()
