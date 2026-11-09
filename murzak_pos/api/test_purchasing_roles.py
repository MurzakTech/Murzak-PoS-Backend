"""
Buying and paying are checked on the server, not only hidden on the screens.

Ordering, receiving goods and returning them take a stock role (the same rule the newer purchase screens already use).
Supplier bills and payments take a billing role. A cashier, who holds Sales User only, is refused by both. No database is
used. The list below is checked against the source, so a new buying call that skips the check, or a check that is
removed, fails here.
"""

import ast
import os
import unittest
from unittest.mock import patch

import frappe

from murzak_pos.api import access_control as access

API = os.path.dirname(os.path.abspath(__file__))

# module -> function -> the role check that must be the first thing it does
GUARDED = {
	"purchase": {
		"create_purchase_order": "require_stock_role",
		"submit_purchase_order": "require_stock_role",
		"create_grn": "require_stock_role",
		"create_stock_receipt": "require_stock_role",
		"create_delivery_note_from_po": "require_stock_role",
		"create_purchase_invoice": "require_billing_role",
		"create_purchase_invoice_from_grn": "require_billing_role",
		"update_purchase_invoice": "require_billing_role",
		"pay_purchase_invoice": "require_billing_role",
	},
	"stock": {
		"cancel_purchase_order": "require_stock_role",
		"create_pr_from_po": "require_stock_role",
	},
	"items": {"return_items": "require_stock_role"},
}


def first_statement(path, name):
	tree = ast.parse(open(path).read())
	for node in tree.body:
		if isinstance(node, ast.FunctionDef) and node.name == name:
			body = node.body
			if isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
				body = body[1:]  # skip the docstring
			return body[0]
	raise AssertionError(f"{name} not found in {path}")


class TestEveryBuyingCallChecksTheRole(unittest.TestCase):
	def test_the_role_check_comes_first_in_each_call(self):
		for module, functions in GUARDED.items():
			path = os.path.join(API, f"{module}.py")
			for name, check in functions.items():
				stmt = first_statement(path, name)
				ok = (
					isinstance(stmt, ast.Expr)
					and isinstance(stmt.value, ast.Call)
					and getattr(stmt.value.func, "id", "") == check
				)
				self.assertTrue(ok, f"{module}.{name} must start with {check}()")

	def test_nothing_is_whitelisted_for_guests_among_them(self):
		for module, functions in GUARDED.items():
			tree = ast.parse(open(os.path.join(API, f"{module}.py")).read())
			for node in tree.body:
				if isinstance(node, ast.FunctionDef) and node.name in functions:
					for deco in node.decorator_list:
						self.assertNotIn("allow_guest=True", ast.unparse(deco).replace(" ", ""))


class TestWhoPasses(unittest.TestCase):
	def run_as(self, roles, check):
		with (
			patch.object(
				access.frappe, "session", type("S", (), {"user": "someone@shop.test"})(), create=True
			),
			patch.object(access.frappe, "get_roles", return_value=roles),
		):
			check()

	def test_a_cashier_cannot_pay_or_receive(self):
		for check in (access.require_billing_role, access.require_stock_role):
			with self.assertRaises(frappe.PermissionError):
				self.run_as(["Sales User", "Desk User"], check)

	def test_an_owner_and_the_managers_can_pay(self):
		for role in ("Business Owner", "System Manager", "Accounts Manager", "Purchase Manager"):
			self.run_as([role], access.require_billing_role)

	def test_a_storekeeper_can_receive_but_not_pay(self):
		self.run_as(["Stock User"], access.require_stock_role)
		with self.assertRaises(frappe.PermissionError):
			self.run_as(["Stock User"], access.require_billing_role)

	def test_the_billing_roles_are_the_ones_allowed_to_cancel_a_bill(self):
		from murzak_pos.api import purchase_orders_api

		self.assertEqual(purchase_orders_api.CANCEL_PURCHASE_ROLES, access.BILLING_ROLES)
