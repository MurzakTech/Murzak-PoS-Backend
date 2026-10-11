# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for keeping price lists and modes of payment apart for each business.

No database is needed. Every Frappe lookup is mocked, so each test says plainly what the database
"contains" for the case in hand.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import business_lists, product_api, sales_api
from murzak_pos.patches import backfill_price_list_company as backfill

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")

# Who owns each price list ("" means shared by every business)
PRICE_LISTS = {"Standard Selling": "", "Retail": "Shop B", "Wholesale": "Shop A", "Old List": ""}
# The companies each mode of payment has an account row for
MODES = {"Cash": [], "M-Pesa": ["Shop A", "Shop B"], "A Paybill": ["Shop A"], "B Till": ["Shop B"]}
ABBR = {"Shop A": "SHA", "Shop B": "SHB"}


class ListCase(unittest.TestCase):
	def as_user(self, user="owner@shop-a.test", companies=("Shop A",), roles=OWNER_ROLES, owner_field=True):
		def get_all(doctype, filters=None, pluck=None, fields=None, **kwargs):
			filters = filters or {}
			if doctype == "Price List":
				rows = list(PRICE_LISTS)
				if "custom_company" in filters:
					wanted = filters["custom_company"]
					rows = [
						n
						for n in rows
						if (not PRICE_LISTS[n] if wanted == ["is", "not set"] else PRICE_LISTS[n] == wanted)
					]
				if "name" in filters:
					rows = [n for n in rows if n in filters["name"][1]]
				return rows if pluck else [frappe._dict(name=n) for n in rows]
			if doctype == "Mode of Payment":
				rows = sorted(MODES)
				if "name" in filters:
					rows = [n for n in rows if n in filters["name"][1]]
				return rows if pluck else [frappe._dict(name=n, type="Cash", enabled=1) for n in rows]
			if doctype == "Mode of Payment Account":
				rows = [
					frappe._dict(parent=m, company=c, default_account="1000")
					for m, cs in MODES.items()
					for c in cs
				]
				if "parent" in filters:
					rows = [r for r in rows if r.parent == filters["parent"]]
				if "company" in filters:
					rows = [r for r in rows if r.company == filters["company"]]
				return rows
			return []

		def get_value(doctype, name=None, field=None, *args, **kwargs):
			if doctype == "Price List" and field == "custom_company":
				return PRICE_LISTS.get(name)
			if doctype == "Company" and field == "abbr":
				return ABBR.get(name)
			return None

		db = MagicMock()
		db.has_column.return_value = owner_field
		db.exists.side_effect = lambda doctype, name=None: (
			name in PRICE_LISTS if doctype == "Price List" else name in ABBR if doctype == "Company" else True
		)
		db.get_value.side_effect = get_value
		db.count.return_value = 0
		self.db = db
		self.created = []
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_all", create=True, side_effect=get_all),
			patch.object(frappe, "log_error", create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
			patch("frappe.defaults.get_global_default", return_value="Supplier Name"),
			patch(
				"murzak_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
			patch("murzak_pos.api.access_control.get_user_companies", return_value=set(companies)),
		]
		frappe.local.response = {}  # where a call sets the HTTP status
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestPriceListVisibility(ListCase):
	def test_a_company_sees_its_own_lists_and_the_shared_ones(self):
		self.as_user()
		self.assertEqual(
			sorted(business_lists.visible_price_list_names("Shop A")),
			["Old List", "Standard Selling", "Wholesale"],
		)

	def test_another_companys_list_is_not_visible(self):
		self.as_user()
		self.assertNotIn("Retail", business_lists.visible_price_list_names("Shop A"))

	def test_the_administrator_with_no_company_is_not_limited(self):
		self.as_user(user="Administrator", companies=())
		self.assertIsNone(business_lists.visible_price_list_names(None))

	def test_nothing_is_limited_before_the_field_exists(self):
		self.as_user(owner_field=False)
		self.assertIsNone(business_lists.visible_price_list_names("Shop A"))


class TestGetPriceLists(ListCase):
	def test_only_visible_lists_are_asked_for(self):
		self.as_user()
		with patch.object(product_api.frappe, "get_all", wraps=frappe.get_all) as spy:
			product_api.get_price_lists()
		filters = spy.call_args.kwargs["filters"]
		self.assertEqual(sorted(filters["name"][1]), ["Old List", "Standard Selling", "Wholesale"])

	def test_naming_another_business_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			product_api.get_price_lists(company="Shop B")


class TestChangingPriceLists(ListCase):
	def test_another_businesss_list_cannot_be_deleted(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			product_api.delete_price_list("Retail")

	def test_a_shared_list_cannot_be_deleted_by_a_business(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			product_api.delete_price_list("Standard Selling")

	def test_the_administrator_may_delete_a_shared_list(self):
		self.as_user(user="Administrator", companies=())
		with patch.object(frappe, "delete_doc", create=True) as delete_doc:
			product_api.delete_price_list("Standard Selling")
		delete_doc.assert_called_once()

	def test_a_business_may_delete_its_own_list(self):
		self.as_user()
		with patch.object(frappe, "delete_doc", create=True) as delete_doc:
			product_api.delete_price_list("Wholesale")
		delete_doc.assert_called_once()

	def test_another_businesss_list_cannot_be_updated(self):
		self.as_user()
		with (
			patch.object(
				frappe,
				"get_doc",
				create=True,
				return_value=frappe._dict(name="Retail", price_list_name="Retail"),
			),
			self.assertRaises(frappe.PermissionError),
		):
			product_api.update_price_list(name="Retail", enabled=False)

	def test_a_shared_list_cannot_be_updated_by_a_business(self):
		self.as_user()
		doc = frappe._dict(name="Standard Selling", price_list_name="Standard Selling")
		with (
			patch.object(frappe, "get_doc", create=True, return_value=doc),
			self.assertRaises(frappe.PermissionError),
		):
			product_api.update_price_list(name="Standard Selling", enabled=False)

	def test_using_another_businesss_list_to_price_an_item_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			product_api.get_product_price("ITEM-1", price_list="Retail")


class TestCreatingAPriceList(ListCase):
	def create(self, name, **kwargs):
		doc = MagicMock()
		doc.price_list_name = None

		def set_name(key, value):
			setattr(doc, key, value)

		doc.set.side_effect = set_name
		self.doc = doc
		with patch.object(frappe, "new_doc", create=True, return_value=doc):
			return product_api.create_price_list(name, "KES", **kwargs)

	def test_the_new_list_belongs_to_the_creating_business(self):
		self.as_user()
		self.create("Loyalty")
		self.doc.set.assert_called_with("custom_company", "Shop A")
		self.assertEqual(self.doc.price_list_name, "Loyalty")

	def test_a_name_used_by_another_business_gets_the_company_code(self):
		self.as_user()
		result = self.create("Retail")
		self.assertEqual(self.doc.price_list_name, "Retail - SHA")
		self.assertIn("Retail - SHA", result["message"])

	def test_a_name_the_business_already_has_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			self.create("Wholesale")

	def test_a_shared_name_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.ValidationError):
			self.create("Standard Selling")

	def test_a_company_that_is_not_the_callers_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			self.create("Loyalty", company="Shop B")


class TestModesOfPayment(ListCase):
	def test_a_company_sees_its_own_shared_and_unset_modes(self):
		self.as_user()
		self.assertEqual(business_lists.modes_of_payment_for("Shop A"), ["A Paybill", "Cash", "M-Pesa"])

	def test_a_mode_set_up_only_for_another_business_is_hidden(self):
		self.as_user()
		self.assertNotIn("B Till", business_lists.modes_of_payment_for("Shop A"))

	def test_the_administrator_with_no_company_is_not_limited(self):
		self.as_user(user="Administrator", companies=())
		self.assertIsNone(business_lists.modes_of_payment_for(None))

	def test_the_first_mode_is_one_the_company_may_use(self):
		self.as_user()
		self.assertEqual(business_lists.first_mode_of_payment("Shop A"), "A Paybill")

	def test_a_hidden_mode_is_not_usable(self):
		self.as_user()
		self.assertFalse(business_lists.mode_of_payment_usable("B Till", "Shop A"))
		self.assertTrue(business_lists.mode_of_payment_usable("M-Pesa", "Shop A"))
		self.assertTrue(business_lists.mode_of_payment_usable("Cash", "Shop A"))

	def test_the_list_call_shows_only_the_callers_modes(self):
		self.as_user()
		result = sales_api.list_payment_methods()
		self.assertEqual([m["name"] for m in result["data"]], ["A Paybill", "Cash", "M-Pesa"])

	def test_the_list_call_shows_only_the_callers_accounts(self):
		self.as_user(companies=("Shop A", "Shop B"))
		result = sales_api.list_payment_methods(company="Shop B")
		by_name = {m["name"]: m for m in result["data"]}
		self.assertNotIn("A Paybill", by_name)
		self.assertEqual([a["company"] for a in by_name["M-Pesa"]["accounts"]], ["Shop B"])

	def test_naming_another_business_is_refused(self):
		self.as_user()
		with self.assertRaises(frappe.PermissionError):
			sales_api.list_payment_methods(company="Shop B")


class TestTheBackfillPatch(unittest.TestCase):
	def decide(self, used_by, owner="Administrator", owner_company=None, only_company=None):
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: (
			owner if doctype == "Price List" else owner_company
		)
		with (
			patch.object(frappe, "db", db, create=True),
			patch("frappe.defaults.get_user_default", return_value=None),
		):
			return backfill.decide_company("Some List", used_by, only_company)

	def test_a_list_used_by_one_company_belongs_to_it(self):
		self.assertEqual(self.decide({"Shop A"}), "Shop A")

	def test_a_list_used_by_several_companies_stays_shared(self):
		self.assertIsNone(self.decide({"Shop A", "Shop B"}, only_company="Shop A"))

	def test_an_unused_list_belongs_to_its_creators_company(self):
		self.assertEqual(self.decide(set(), owner="owner@shop-b.test", owner_company="Shop B"), "Shop B")

	def test_an_unused_list_made_by_the_administrator_stays_shared_on_several_companies(self):
		self.assertIsNone(self.decide(set()))

	def test_on_a_single_company_site_the_rest_belongs_to_that_company(self):
		self.assertEqual(self.decide(set(), only_company="Shop A"), "Shop A")

	def test_the_standard_lists_are_never_tagged(self):
		self.assertEqual(set(backfill.SHARED), {"Standard Selling", "Standard Buying"})


if __name__ == "__main__":
	unittest.main()
