# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for two businesses using the same supplier or customer name. Frappe lets a name exist once on the
whole site, so the second business's record carries its company code ("Bidco - SHB") instead of failing.

No database is needed. Every Frappe lookup is mocked, and each test says what the "database" holds.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos.api import customer_api, party_names, supplier_api


class FakeRecord:
	"""A supplier or customer as it is created: takes any field, and gets its name when inserted."""

	def __init__(self, name_field):
		self._name_field = name_field
		self.inserted = False

	def insert(self, **kwargs):
		self.inserted = True
		self.name = getattr(self, self._name_field)

	def append(self, *args, **kwargs):
		pass

	def save(self, **kwargs):
		pass

	def __getattr__(self, item):
		return None


class PartyCase(unittest.TestCase):
	def world(self, taken=(), mine=(), naming="Supplier Name", abbr="SHB", companies=("Shop A",)):
		"""
		`taken`: record names that exist on the site (any business). `mine`: display names this business
		already has. `naming`: how the site names these records.
		"""

		def exists(doctype, filters=None):
			if doctype in ("Company", "Supplier Group", "Customer Group", "Territory", "Currency"):
				return True
			if isinstance(filters, str):  # looked up by record name
				return filters in taken
			if isinstance(filters, dict) and "customer_name" in filters:
				return filters["customer_name"] in mine
			return False

		db = MagicMock()
		db.exists.side_effect = exists
		db.has_column.return_value = True
		db.get_value.side_effect = lambda doctype, name=None, field=None, *a, **k: (
			abbr if (doctype, field) == ("Company", "abbr") else "Local"
		)
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user="buyer@shop-a.test")),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "log_error", create=True),
			patch.object(frappe, "logger", create=True),
			patch("frappe.defaults.get_global_default", return_value=naming),
			patch(
				"techsavanna_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
			patch.object(supplier_api, "get_user_companies", return_value=set(companies)),
			patch.object(
				supplier_api,
				"_supplier_name_taken_by_this_business",
				side_effect=lambda n, c, exclude=None: n in mine,
			),
		]
		frappe.local.response = {}  # where a call sets the HTTP status
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestTheNamingRule(PartyCase):
	def name(self, doctype="Supplier", wanted="Bidco", company="Shop A", **world):
		self.world(**world)
		return party_names.name_for_new_record(doctype, wanted, company)

	def test_a_free_name_is_used_as_it_is(self):
		self.assertEqual(self.name(taken=()), "Bidco")

	def test_a_name_another_business_uses_gets_this_businesss_code(self):
		self.assertEqual(self.name(taken=("Bidco",)), "Bidco - SHB")

	def test_customers_follow_the_same_rule(self):
		self.assertEqual(
			self.name("Customer", "Amina Store", taken=("Amina Store",), naming="Customer Name"),
			"Amina Store - SHB",
		)

	def test_a_site_that_names_records_by_number_series_never_needs_a_code(self):
		self.assertEqual(self.name(taken=("Bidco",), naming="Naming Series"), "Bidco")

	def test_with_no_company_code_the_name_is_left_for_the_callers_duplicate_handling(self):
		self.assertEqual(self.name(taken=("Bidco",), abbr=None), "Bidco")
		self.assertEqual(self.name(taken=("Bidco",), company=None), "Bidco")


class TestCreatingASupplierWhoseNameAnotherBusinessUses(PartyCase):
	def create(self, **world):
		self.world(**world)
		record = FakeRecord("supplier_name")
		with patch.object(frappe, "new_doc", create=True, return_value=record):
			reply = supplier_api.create_supplier(supplier_name="Bidco")
		return record, reply

	def test_a_free_name_is_kept_and_the_message_is_unchanged(self):
		record, reply = self.create(taken=())
		self.assertEqual(record.supplier_name, "Bidco")
		self.assertEqual(reply["message"], "Supplier created successfully")
		self.assertEqual(reply["name"], "Bidco")

	def test_a_name_another_business_uses_becomes_the_company_coded_name(self):
		record, reply = self.create(taken=("Bidco",))
		self.assertEqual(record.supplier_name, "Bidco - SHB")
		self.assertEqual(reply["name"], "Bidco - SHB")
		self.assertTrue(record.inserted)
		self.assertIn("Bidco - SHB", reply["message"])
		self.assertIn("company code", reply["message"])
		self.assertEqual(record.custom_company, "Shop A")

	def test_a_supplier_the_business_already_has_is_a_duplicate(self):
		self.world(taken=("Bidco",), mine=("Bidco",))
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			self.assertRaises(frappe.ValidationError),
		):
			supplier_api.create_supplier(supplier_name="Bidco")
		new_doc.assert_not_called()

	def test_asking_again_for_a_name_the_business_already_holds_with_its_code_is_a_duplicate(self):
		# the first time made "Bidco - SHB"; asking for "Bidco" again must not try to create it twice
		self.world(taken=("Bidco", "Bidco - SHB"))
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			self.assertRaises(frappe.ValidationError),
		):
			supplier_api.create_supplier(supplier_name="Bidco")
		new_doc.assert_not_called()

	def test_on_a_site_that_names_suppliers_by_series_the_name_is_kept(self):
		record, reply = self.create(taken=("Bidco",), naming="Naming Series")
		self.assertEqual(record.supplier_name, "Bidco")
		self.assertEqual(reply["message"], "Supplier created successfully")


class TestCreatingACustomerWhoseNameAnotherBusinessUses(PartyCase):
	def create(self, **world):
		world.setdefault("naming", "Customer Name")
		self.world(**world)
		record = FakeRecord("customer_name")
		with patch.object(frappe, "new_doc", create=True, return_value=record):
			reply = customer_api.create_customer(customer_name="Amina Store", company="Shop A")
		return record, reply

	def test_a_free_name_is_kept(self):
		record, reply = self.create(taken=())
		self.assertEqual((record.customer_name, reply["success"]), ("Amina Store", True))
		self.assertEqual(reply["message"], "Customer created successfully")

	def test_a_name_another_business_uses_becomes_the_company_coded_name(self):
		record, reply = self.create(taken=("Amina Store",))
		self.assertTrue(reply["success"])
		self.assertEqual(record.customer_name, "Amina Store - SHB")
		self.assertEqual(reply["data"]["customer_name"], "Amina Store - SHB")
		self.assertIn("Amina Store - SHB", reply["message"])
		self.assertEqual(record.custom_company, "Shop A")

	def test_a_customer_the_business_already_has_is_reported_as_a_duplicate(self):
		record, reply = self.create(taken=("Amina Store",), mine=("Amina Store",))
		self.assertFalse(reply["success"])
		self.assertEqual(reply["error_type"], "duplicate")
		self.assertFalse(record.inserted)

	def test_asking_again_after_the_coded_name_was_made_is_a_duplicate_not_a_crash(self):
		record, reply = self.create(taken=("Amina Store", "Amina Store - SHB"))
		self.assertFalse(reply["success"])
		self.assertEqual((reply["error_type"], reply["name"]), ("duplicate", "Amina Store - SHB"))
		self.assertFalse(record.inserted)


class TestRenamingASupplier(PartyCase):
	def test_a_display_name_another_business_uses_is_allowed_but_the_businesss_own_is_not(self):
		self.world(mine=("Own Co",))
		supplier = MagicMock()
		with (
			patch.object(frappe, "get_doc", create=True, return_value=supplier),
			patch.object(supplier_api, "_ensure_supplier_visible"),
			patch.object(supplier_api, "_has_company_field", return_value=True),
		):
			self.db.get_value.side_effect = lambda *a, **k: "Shop A"
			self.db.exists.side_effect = lambda doctype, filters=None: True
			reply = supplier_api.update_supplier(
				name="Acme", supplier_name="Bidco"
			)  # only another business has it
			self.assertTrue(reply["success"], reply)
			with self.assertRaises(frappe.ValidationError):
				supplier_api.update_supplier(name="Acme", supplier_name="Own Co")


if __name__ == "__main__":
	unittest.main()
