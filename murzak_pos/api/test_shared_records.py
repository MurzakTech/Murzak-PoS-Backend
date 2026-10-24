# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Units, categories, brands and supplier groups are shared by every business on a site. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import access_control, product_api, supplier_api


class SharedCase(unittest.TestCase):
	def given(self, user="owner@a.test", creator="owner@b.test", companies=None, businesses_on_site=2):
		companies = companies or {"owner@a.test": {"A"}, "staff@a.test": {"A"}, "owner@b.test": {"B"}}
		db = MagicMock()
		db.get_value.return_value = creator
		db.count.return_value = businesses_on_site
		for p in (
			patch.object(frappe, "session", frappe._dict(user=user), create=True),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_roles", return_value=["System Manager"], create=True),
			patch.object(
				access_control,
				"get_user_companies",
				side_effect=lambda u=None: set(companies.get(u or user, ())),
			),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)


class TestWhoMayChangeASharedRecord(SharedCase):
	def check(self):
		access_control.require_shared_record_manageable("Brand", "Acme")

	def test_another_business_may_not(self):
		self.given()
		with self.assertRaises(frappe.PermissionError):
			self.check()

	def test_a_record_the_site_came_with_is_left_to_the_administrator(self):
		self.given(creator="Administrator")
		with self.assertRaises(frappe.PermissionError):
			self.check()

	def test_the_person_and_the_business_that_added_it_may(self):
		self.given(user="owner@b.test")
		self.check()
		self.given(user="staff@a.test", creator="owner@a.test")
		self.check()

	def test_on_a_shops_own_site_its_people_may(self):
		self.given(creator="Administrator", businesses_on_site=1)
		self.check()

	def test_the_administrator_may_and_a_missing_record_is_left_to_the_call(self):
		self.given(user="Administrator")
		self.check()
		self.given(creator=None)
		self.check()


class TestTheCallsAsk(SharedCase):
	def test_renaming_or_deleting_another_businesss_record_is_refused(self):
		self.given()
		frappe.db.exists.return_value = True
		calls = [
			(product_api.delete_brand, {"brand_name": "Acme"}),
			(product_api.update_brand, {"brand_name": "Acme", "new_brand_name": "Acme 2"}),
			(product_api.delete_item_group, {"name": "Drinks"}),
			(product_api.update_item_group, {"name": "Drinks", "item_group_name": "Juices"}),
			(product_api.delete_uom, {"uom_name": "Crate"}),
			(supplier_api.update_supplier_group, {"name": "Wholesale", "supplier_group_name": "Bulk"}),
		]
		with (
			patch.object(frappe, "get_doc", create=True),
			patch.object(frappe, "delete_doc", create=True) as delete_doc,
			patch.object(frappe, "get_all", return_value=[], create=True),
		):
			for call, args in calls:
				with self.subTest(call=call.__name__), self.assertRaises(frappe.PermissionError):
					call(**args)
		delete_doc.assert_not_called()

	def test_a_cashier_may_not_change_the_catalogue_or_supplier_groups(self):
		self.given(user="owner@b.test")
		with patch.object(frappe, "get_roles", return_value=["Sales User"], create=True):
			for call, args in (
				(product_api.create_brand, {"brand_name": "New"}),
				(product_api.create_uom, {"uom_name": "Tray"}),
				(supplier_api.create_supplier_group, {"supplier_group_name": "Local"}),
			):
				with self.subTest(call=call.__name__), self.assertRaises(frappe.PermissionError):
					call(**args)


if __name__ == "__main__":
	unittest.main()
