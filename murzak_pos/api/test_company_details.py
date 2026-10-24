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
