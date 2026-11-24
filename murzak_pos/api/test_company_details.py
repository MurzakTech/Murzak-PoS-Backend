# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for reading and changing a business's default warehouse in its company details. No database is needed."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from murzak_pos.api import onboarding_api as api

# ERPNext 15's Company has no default_warehouse field, so this stand-in must not have one either
FIELDS = (
	"name company_name abbr country default_currency tax_id company_logo phone_no email website "
	"date_of_establishment date_of_incorporation is_group parent_company "
	"enable_provisional_accounting_for_non_stock_items default_provisional_account default_bank_account "
	"default_cash_account default_receivable_account default_payable_account default_expense_account "
	"default_income_account cost_center"
).split()


def make_company():
	return SimpleNamespace(**{field: None for field in FIELDS}, save=MagicMock(), reload=MagicMock())


class CompanyDetailsCase(unittest.TestCase):
	def test_reading_a_company_without_a_default_warehouse_field_works(self):
		company = make_company()
		with (
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api.frappe, "db", MagicMock(), create=True) as db,
			patch.object(api.frappe, "get_all", return_value=[]),
			patch.object(api.frappe, "get_doc", return_value=company),
			patch.object(api, "get_default_warehouse_for_company", return_value="Stores - A"),
		):
			db.exists.return_value = True
			result = api.get_company("Acme")
		self.assertTrue(result["success"])
		self.assertEqual(result["data"]["default_warehouse"], "Stores - A")

	def test_changing_the_default_warehouse_goes_through_the_helper(self):
		company = make_company()
		company.name = "Acme"
		with (
			patch.object(api, "require_manager"),
			patch.object(api, "resolve_company", return_value="Acme"),
			patch.object(api, "ensure_record_access"),
			patch.object(api.frappe, "db", MagicMock(), create=True) as db,
			patch.object(api.frappe, "get_doc", return_value=company),
			patch.object(api, "set_default_warehouse_for_company") as setter,
			patch.object(api.frappe, "session", MagicMock(user="owner@acme.test")),
		):
			db.exists.return_value = True
			result = api.update_company("Acme", default_warehouse="Stores - A")
		self.assertTrue(result["success"], result)
		setter.assert_called_once_with("Acme", "Stores - A")
		self.assertFalse(hasattr(company, "default_warehouse"))


class TestOnlyTheOwnerOfACompanyMayChangeIt(unittest.TestCase):
	"""update_company saves with permissions bypassed, so who may ask has to be checked first. Needs no database."""

	def setUp(self):
		self.roles = ["Business Owner"]
		self.my_companies = {"Own Co"}
		self.company = make_company()
		self.get_doc = MagicMock(return_value=self.company)
		self.user = MagicMock(user="owner@own.test")
		for p in (
			patch.object(api.frappe, "session", self.user, create=True),
			patch.object(api.frappe, "db", MagicMock(), create=True),
			patch.object(api.frappe, "get_roles", lambda user=None: list(self.roles)),
			patch.object(api.frappe, "get_doc", self.get_doc),
			patch("murzak_pos.api.payment_gateway_common.get_user_companies", lambda *a, **k: set(self.my_companies)),
			patch("murzak_pos.api.access_control.get_user_companies", lambda *a, **k: set(self.my_companies)),
		):
			p.start()
			self.addCleanup(p.stop)
		api.frappe.db.exists.return_value = True

	def test_an_owner_cannot_change_another_businesss_company(self):
		"""The attack: any signed-in user names another business and changes its tax ID, e-mail and accounts."""
		with self.assertRaises(api.frappe.PermissionError):
			api.update_company("Victim Co", tax_id="P000000000X", email="thief@evil.test")
		self.get_doc.assert_not_called()
		self.company.save.assert_not_called()

	def test_a_cashier_cannot_change_even_their_own_company(self):
		self.roles = ["Sales User"]
		with self.assertRaises(api.frappe.PermissionError):
			api.update_company("Own Co", phone_no="0700000000")
		self.get_doc.assert_not_called()
		self.company.save.assert_not_called()

	def test_a_manager_who_is_not_the_owner_cannot_either(self):
		self.roles = ["Accounts Manager"]  # the frontend keeps business settings from this role too
		with self.assertRaises(api.frappe.PermissionError):
			api.update_company("Own Co", phone_no="0700000000")
		self.company.save.assert_not_called()

	def test_a_signed_out_caller_is_refused(self):
		self.user.user = "Guest"
		with self.assertRaises(api.frappe.AuthenticationError):
			api.update_company("Own Co", phone_no="0700000000")
		self.get_doc.assert_not_called()

	def test_the_owner_can_change_their_own_company(self):
		result = api.update_company("Own Co", phone_no="0700000000")
		self.assertTrue(result["success"], result)
		self.assertEqual(self.company.phone_no, "0700000000")
		self.company.save.assert_called()
