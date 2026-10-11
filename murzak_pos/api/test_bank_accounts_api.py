# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the cash, bank and bank account endpoints. No database is needed."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import bank_accounts_api as api


class AccountTypeCase(unittest.TestCase):
	def test_spelling_is_tolerated(self):
		self.assertEqual(api.clean_account_type(" cash "), "Cash")
		self.assertEqual(api.clean_account_type("BANK"), "Bank")

	def test_other_types_cannot_be_created(self):
		for value in ("Stock", "Receivable", "", None):
			with self.assertRaises(frappe.ValidationError):
				api.clean_account_type(value)

	def test_receivable_can_be_listed(self):
		self.assertEqual(api.clean_account_type("receivable", api.LISTABLE_TYPES), "Receivable")


class ListCase(unittest.TestCase):
	def listed(self, account_type=None):
		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "get_all", return_value=[{"name": "A"}]) as get_all,
		):
			result = api.list_cash_and_bank_accounts("Acme", account_type)
		return result, get_all.call_args.kwargs["filters"]

	def test_default_lists_cash_and_bank_of_that_business_only(self):
		result, filters = self.listed()
		self.assertEqual(filters["company"], "Acme")
		self.assertEqual(filters["account_type"], ["in", ["Cash", "Bank"]])
		self.assertEqual(filters["is_group"], 0)
		self.assertEqual((result["success"], result["count"]), (True, 1))

	def test_receivable_is_listed_only_when_asked_for(self):
		_result, filters = self.listed("Receivable")
		self.assertEqual(filters["account_type"], ["in", ["Receivable"]])

	def test_a_made_up_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.listed("Stock")


class WriteAccessCase(unittest.TestCase):
	def test_every_change_needs_an_accounts_manager(self):
		denied = patch.object(api, "require_gateway_admin", side_effect=frappe.PermissionError)
		calls = (
			lambda: api.create_cash_or_bank_account("x", "Cash"),
			lambda: api.update_account("x"),
			lambda: api.create_bank("x"),
			lambda: api.create_bank_account("x", "Bank"),
		)
		with denied:
			for call in calls:
				with self.assertRaises(frappe.PermissionError):
					call()


class OwnershipCase(unittest.TestCase):
	def test_an_account_of_another_business_looks_missing(self):
		row = frappe._dict(name="X - B", company="B")
		with (
			patch.object(api.frappe, "db", MagicMock()) as db,
			patch.object(api, "resolve_company", side_effect=frappe.PermissionError),
		):
			db.get_value.return_value = row
			with self.assertRaises(frappe.PermissionError):
				api.get_account_details("X - B")

	def test_an_unknown_account_is_not_found(self):
		with patch.object(api.frappe, "db", MagicMock()) as db:
			db.get_value.return_value = None
			with self.assertRaises(frappe.DoesNotExistError):
				api.get_account_details("nope")

	def test_a_bank_account_with_no_business_is_hidden(self):
		with patch.object(api.frappe, "db", MagicMock()) as db:
			db.get_value.return_value = frappe._dict(name="BA", company=None)
			with self.assertRaises(frappe.DoesNotExistError):
				api.get_bank_account_details("BA")

	def test_only_cash_and_bank_accounts_can_be_changed(self):
		row = frappe._dict(
			name="D - A",
			company="A",
			account_type="Receivable",
			account_name="D",
			account_number=None,
			disabled=0,
		)
		with (
			patch.object(api, "require_gateway_admin"),
			patch.object(api, "_own_account", return_value=row),
			self.assertRaises(frappe.PermissionError),
		):
			api.update_account("D - A", disabled=1)


class CreateCase(unittest.TestCase):
	def test_a_nameless_account_is_refused(self):
		with (
			patch.object(api, "require_gateway_admin"),
			patch.object(api, "resolve_company", return_value="A"),
		):
			with self.assertRaises(frappe.ValidationError):
				api.create_cash_or_bank_account("  ", "Cash")

	def test_a_bank_account_needs_an_existing_bank(self):
		with (
			patch.object(api, "require_gateway_admin"),
			patch.object(api, "resolve_company", return_value="A"),
			patch.object(api.frappe, "db", MagicMock()) as db,
		):
			db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				api.create_bank_account("Ops", "No Such Bank")

	def test_an_existing_bank_is_returned_instead_of_copied(self):
		with patch.object(api, "require_gateway_admin"), patch.object(api.frappe, "db", MagicMock()) as db:
			db.get_value.return_value = "Equity"
			result = api.create_bank("Equity")
		self.assertEqual(result["data"]["name"], "Equity")


if __name__ == "__main__":
	unittest.main()
