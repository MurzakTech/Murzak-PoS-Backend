# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Who may change customers, draft invoices and the steps of a stock count. Needs no database.

These calls checked which business a record belonged to, but not who in that business was asking. Any signed-in
person, a stock clerk or an auditor included, could edit customers or rewrite a colleague's draft invoice. These tests
fix the rule in place: the right roles get through, and the others are refused before anything is read or written.
"""

import ast
import importlib
import inspect
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import access_control

API = os.path.dirname(os.path.abspath(__file__))
ME = "till@shop-a.test"


class RoleCase(unittest.TestCase):
	def as_user(self, roles, user=ME, owner_of_record=None):
		self.db = MagicMock()
		self.db.exists.return_value = True
		self.db.get_value.return_value = owner_of_record
		for p in (
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", self.db, create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def allowed(self, check, roles, *args):
		self.as_user(roles)
		try:
			check(*args)
		except frappe.PermissionError:
			return False
		return True


class TestWhoMayChangeCustomers(RoleCase):
	def test_the_people_who_work_with_customers_and_ring_up_sales_may(self):
		for role in (
			"Sales User",
			"Sales Manager",
			"Accounts User",
			"Agent",
			"Desk User",
			"Employee",
			"System Manager",
		):
			self.assertTrue(self.allowed(access_control.require_customer_role, (role,)), role)

	def test_everyone_else_may_not(self):
		for role in (
			"Stock User",
			"Purchase User",
			"Auditor",
			"Analytics",
			"Delivery User",
			"Customer",
			"Blogger",
		):
			self.assertFalse(self.allowed(access_control.require_customer_role, (role,)), role)

	def test_the_all_role_grants_nothing(self):
		self.assertFalse(self.allowed(access_control.require_customer_role, ("All", "Guest")))

	def test_the_calls_refuse_a_stock_clerk_before_doing_anything(self):
		customer_api = importlib.import_module("murzak_pos.api.customer_api")
		self.as_user(("Stock User",))
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			patch.object(frappe, "get_doc", create=True) as get_doc,
		):
			for name in ("create_customer", "update_customer"):
				function = inspect.unwrap(getattr(customer_api, name))
				arguments = {
					p.name: "x"
					for p in inspect.signature(function).parameters.values()
					if p.default is inspect.Parameter.empty
					and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
				}
				with self.subTest(call=name), self.assertRaises(frappe.PermissionError):
					function(**arguments)
		new_doc.assert_not_called()
		get_doc.assert_not_called()


class TestWhoMayAddAStockCount(RoleCase):
	def test_first_count_is_for_the_counter_the_stock_manager_and_the_owner(self):
		check = access_control.require_stock_take_first_step_role
		for role in ("Sales User", "Stock Manager", "System Manager", "Business Owner"):
			self.assertTrue(self.allowed(check, (role,)), role)
		for role in ("Quality Manager", "Purchase User", "Accounts Manager", "Auditor"):
			self.assertFalse(self.allowed(check, (role,)), role)

	def test_quality_check_is_for_the_quality_manager_the_stock_manager_and_the_owner(self):
		check = access_control.require_stock_take_second_step_role
		for role in ("Quality Manager", "Stock Manager", "System Manager"):
			self.assertTrue(self.allowed(check, (role,)), role)
		for role in ("Sales User", "Purchase User", "Auditor"):
			self.assertFalse(self.allowed(check, (role,)), role)

	def test_the_first_count_call_refuses_an_auditor_before_reading_the_count(self):
		inventory_api = importlib.import_module("murzak_pos.api.inventory_api")
		self.as_user(("Auditor",))
		with patch.object(frappe, "get_doc", create=True) as get_doc:
			with self.assertRaises(frappe.PermissionError):
				inspect.unwrap(inventory_api.add_sales_person_stock_take)(
					"SR-1", [{"item_code": "A", "qty": 1}]
				)
		get_doc.assert_not_called()


class TestWhoMayChangeADraftInvoice(RoleCase):
	def test_a_sales_manager_may_change_any_draft(self):
		self.as_user(("Sales Manager",), owner_of_record="someone.else@shop-a.test")
		access_control.require_invoice_editor("Sales Invoice", "SINV-1")

	def test_the_owner_may_change_any_draft(self):
		self.as_user(("System Manager",), owner_of_record="someone.else@shop-a.test")
		access_control.require_invoice_editor("Sales Invoice", "SINV-1")

	def test_a_cashier_may_change_the_draft_they_made(self):
		self.as_user(("Sales User",), owner_of_record=ME)
		access_control.require_invoice_editor("Sales Invoice", "SINV-1")

	def test_a_cashier_may_not_change_a_colleagues_draft(self):
		self.as_user(("Sales User",), owner_of_record="colleague@shop-a.test")
		with self.assertRaises(frappe.PermissionError):
			access_control.require_invoice_editor("Sales Invoice", "SINV-1")

	def test_the_all_role_grants_nothing(self):
		self.as_user(("All", "Guest"), owner_of_record="colleague@shop-a.test")
		with self.assertRaises(frappe.PermissionError):
			access_control.require_invoice_editor("POS Invoice", "POS-1")

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user((), user="Guest", owner_of_record=ME)
		with self.assertRaises(frappe.AuthenticationError):
			access_control.require_invoice_editor("Sales Invoice", "SINV-1")

	def test_the_edit_calls_refuse_before_touching_the_invoice(self):
		sales_api = importlib.import_module("murzak_pos.api.sales_api")
		self.as_user(("Sales User",), owner_of_record="colleague@shop-a.test")
		with patch.object(frappe, "get_doc", create=True) as get_doc:
			for name, doctype in (
				("update_sales_invoice", "Sales Invoice"),
				("update_pos_invoice", "POS Invoice"),
			):
				with self.subTest(call=name), self.assertRaises(frappe.PermissionError):
					inspect.unwrap(getattr(sales_api, name))(name="INV-1", discount_amount=100)
				self.db.get_value.assert_any_call(doctype, "INV-1", "owner")
		get_doc.assert_not_called()


class TestTheChecksAreNotForgotten(unittest.TestCase):
	"""Reads the source, so a check that is removed later fails here."""

	# call -> the check that must come first, straight after the docstring
	EXPECTED: dict[str, dict[str, str]] = {
		"customer_api.py": {
			"create_customer": "require_customer_role",
			"update_customer": "require_customer_role",
		},
		"inventory_api.py": {
			"add_sales_person_stock_take": "require_stock_take_first_step_role",
			"add_stock_controller_stock_take": "require_stock_take_second_step_role",
		},
		"sales_api.py": {
			"update_sales_invoice": "require_invoice_editor",
			"update_pos_invoice": "require_invoice_editor",
			"create_credit_mode_of_payment": "require_accounts_setup_role",
		},
		"account_provisioning_api.py": {
			"set_default_provisional_account": "require_accounts_setup_role",
			"auto_configure_provisional_account": "require_accounts_setup_role",
		},
		"onboarding_api.py": {
			"create_pos_profile": "require_manager",
			"update_pos_profile": "require_manager",
			"create_etims_settings": "require_manager",
		},
		"product_api.py": {
			"enable_product": "require_catalogue_role",
			"add_barcode": "require_catalogue_role",
			"remove_barcode": "require_catalogue_role",
			"set_product_warranty": "require_catalogue_role",
		},
		"purchase_receipt.py": {"reject_return_items": "require_stock_role"},
		"loyalty.py": {"earn_loyalty_points": "require_loyalty_role"},
	}

	def first_statement_call(self, node):
		body = node.body
		if (
			body
			and isinstance(body[0], ast.Expr)
			and isinstance(body[0].value, ast.Constant)
			and isinstance(body[0].value.value, str)
		):
			body = body[1:]
		first = body[0]
		if isinstance(first, ast.Expr) and isinstance(first.value, ast.Call):
			return ast.unparse(first.value.func)
		return None

	def test_each_call_starts_with_its_check(self):
		for filename, calls in self.EXPECTED.items():
			with open(os.path.join(API, filename)) as handle:
				tree = ast.parse(handle.read())
			found = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
			for name, check in calls.items():
				with self.subTest(call=f"{filename}:{name}"):
					self.assertIn(name, found)
					self.assertEqual(self.first_statement_call(found[name]), check)


if __name__ == "__main__":
	unittest.main()
