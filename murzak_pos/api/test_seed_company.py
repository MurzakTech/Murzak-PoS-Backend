# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Adding products can only be done for your own business, by the people who manage the catalogue. Needs no database.

create_seed_item checked the `company` it was handed as an argument, then read the company again from the raw
request body and used that one. Frappe fills the argument from the body only when the request is JSON; for any other
content type it comes from the query string. So a request could be checked as "Own Co" and then write into
"Victim Co". These tests replay that, with the real permission functions and only the database mocked.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import product_seeding as api

USER = "staff@owncompany.test"
ITEM = {"item_code": "SODA-1", "item_name": "Soda", "price": 100}


class SeedCompanyCase(unittest.TestCase):
	def setUp(self):
		self.roles = ["Stock Manager"]  # holds both the catalogue and the stock job
		self.my_companies = {"Own Co"}
		self.owners = {}  # (doctype, name) -> the company that owns it
		self.db = MagicMock()
		self.db.exists.return_value = True
		self.db.has_column.return_value = True
		self.db.get_value.side_effect = self._get_value
		self.new_doc = MagicMock(side_effect=AssertionError("a record was created"))
		for p in (
			patch.object(frappe, "session", MagicMock(user=USER), create=True),
			patch.object(frappe, "db", self.db, create=True),
			patch.object(frappe, "get_roles", lambda user=None: list(self.roles)),
			patch.object(frappe, "new_doc", self.new_doc),
			patch.object(frappe, "request", MagicMock(), create=True),
			patch.object(frappe, "form_dict", frappe._dict(), create=True),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", lambda *a, **k: set(self.my_companies)),
			patch("murzak_pos.api.access_control.get_user_companies", lambda *a, **k: set(self.my_companies)),
		):
			p.start()
			self.addCleanup(p.stop)

	def _get_value(self, doctype, name=None, field=None, *args, **kwargs):
		if (doctype, name) in self.owners:
			return self.owners[(doctype, name)]
		if field == "abbr":
			return "OC"
		return None

	def send(self, body, company_argument=None):
		"""Call create_seed_item as Frappe would: the body is the raw request, the argument is what it filled in."""
		frappe.request.data = json.dumps(body).encode()
		return api.create_seed_item(company=company_argument)

	def payload(self, **over):
		base = {"company": "Own Co", "price_list": "Standard Selling", "items": [dict(ITEM)]}
		base.update(over)
		return base

	def assertRefused(self, reply):
		self.assertEqual(reply.get("status"), "error", reply)
		self.assertEqual(reply.get("error_type"), "PermissionError", reply)
		self.new_doc.assert_not_called()


class TestWhichCompanyIsUsed(SeedCompanyCase):
	def test_another_business_named_in_the_body_is_refused(self):
		"""Sent so Frappe fills no argument (not JSON, no query string): the early check never ran."""
		self.assertRefused(self.send(self.payload(company="Victim Co"), company_argument=None))

	def test_checked_as_my_own_but_writing_to_another_is_refused(self):
		"""The argument is my own company (from the query string); the body, which is what gets used, is not."""
		self.assertRefused(self.send(self.payload(company="Victim Co"), company_argument="Own Co"))

	def test_my_own_company_in_the_body_is_not_refused(self):
		with patch.object(api, "resolve_company", wraps=api.resolve_company) as resolve:
			reply = self.send(self.payload(company="Own Co"), company_argument="Own Co")
		self.assertIn("Own Co", [call.args[0] for call in resolve.call_args_list])
		self.assertNotEqual(reply.get("error_type"), "PermissionError", reply)

	def test_the_company_in_the_body_is_the_one_checked(self):
		with patch.object(api, "resolve_company", side_effect=lambda c: c) as resolve:
			self.send(self.payload(company="Victim Co"), company_argument="Own Co")
		self.assertIn("Victim Co", [call.args[0] for call in resolve.call_args_list])


class TestWhatItMayWriteInto(SeedCompanyCase):
	def test_another_businesss_price_list_is_refused(self):
		self.owners[("Price List", "Their Prices")] = "Victim Co"
		self.assertRefused(self.send(self.payload(price_list="Their Prices")))

	def test_another_businesss_buying_price_list_is_refused(self):
		self.owners[("Price List", "Their Buying")] = "Victim Co"
		self.assertRefused(self.send(self.payload(buying_price_list="Their Buying")))

	def test_another_businesss_warehouse_is_refused(self):
		self.owners[("Warehouse", "Their Store - V")] = "Victim Co"
		self.assertRefused(self.send(self.payload(warehouse="Their Store - V")))

	def test_another_businesss_warehouse_on_a_single_row_is_refused(self):
		self.owners[("Warehouse", "Their Store - V")] = "Victim Co"
		row = dict(ITEM, warehouse="Their Store - V", qty=5)
		self.assertRefused(self.send(self.payload(items=[row])))


class TestWhoMayDoIt(SeedCompanyCase):
	def test_a_cashier_may_not_add_products(self):
		self.roles = ["Sales User"]
		with self.assertRaises(frappe.PermissionError):
			self.send(self.payload())
		self.new_doc.assert_not_called()

	def test_someone_with_no_business_role_may_not(self):
		self.roles = ["All", "Guest"]
		with self.assertRaises(frappe.PermissionError):
			self.send(self.payload())

	def test_a_catalogue_role_may_add_products_but_not_receive_stock(self):
		self.roles = ["Sales Manager"]  # may change the catalogue; is not a stock role
		self.assertRefused(self.send(self.payload(items=[dict(ITEM, warehouse="Stores - OC", qty=10)])))

	def test_a_stock_role_may_receive_opening_stock(self):
		self.roles = ["Stock Manager"]
		with patch.object(api, "require_stock_role", wraps=api.require_stock_role) as stock_check:
			self.send(self.payload(items=[dict(ITEM, warehouse="Stores - OC", qty=10)]))
		stock_check.assert_called_once()


if __name__ == "__main__":
	unittest.main()
