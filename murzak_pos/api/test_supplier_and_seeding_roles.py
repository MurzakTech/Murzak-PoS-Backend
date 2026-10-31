# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for who may change suppliers and who may run the bulk product loader.

Adding, editing or switching off a supplier used to check only the business, so a cashier who knew the address could
do it. Loading the starter products writes templates every business on the site shares, and had no check at all. Both
now refuse the wrong role first, before anything is read or changed. No database is needed: every Frappe call is mocked.
"""

import ast
import inspect
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import product_seeding, supplier_api


class Case(unittest.TestCase):
	def as_user(self, roles, user="someone@shop-a.test"):
		self.db = MagicMock()
		self.db.exists.return_value = True
		for target, kwargs in (
			((frappe, "session"), {"new": frappe._dict(user=user)}),
			((frappe, "get_roles"), {"create": True, "side_effect": lambda user=None: list(roles)}),
			((frappe, "db"), {"new": self.db, "create": True}),
			((frappe, "log_error"), {"create": True}),
			((frappe, "get_traceback"), {"create": True, "return_value": "trace"}),
			((frappe, "get_doc"), {"create": True}),
			((frappe, "new_doc"), {"create": True}),
		):
			patcher = patch.object(*target, **kwargs)
			patcher.start()
			self.addCleanup(patcher.stop)
		patcher = patch("frappe.translate.get_all_translations", return_value={})
		patcher.start()
		self.addCleanup(patcher.stop)


class TestWhoMayChangeSuppliers(Case):
	def test_a_cashier_cannot_add_a_supplier(self):
		self.as_user(["Sales User"])
		with self.assertRaises(frappe.PermissionError):
			supplier_api.create_supplier(supplier_name="Acme")
		self.assertEqual(self.db.method_calls, [])
		frappe.new_doc.assert_not_called()

	def test_a_cashier_cannot_edit_or_switch_off_a_supplier(self):
		self.as_user(["Sales User"])
		for kwargs in ({"supplier_name": "Renamed"}, {"tax_id": "P000000000X"}, {"disabled": True}):
			with self.subTest(kwargs=kwargs), self.assertRaises(frappe.PermissionError):
				supplier_api.update_supplier("Acme", **kwargs)
		self.assertEqual(self.db.method_calls, [])
		frappe.get_doc.assert_not_called()

	def test_a_stock_user_or_a_purchase_user_cannot_change_a_supplier_either(self):
		for roles in (["Stock User"], ["Purchase User"], ["Stock Manager"]):
			with self.subTest(roles=roles):
				self.as_user(roles)
				with self.assertRaises(frappe.PermissionError):
					supplier_api.create_supplier(supplier_name="Acme")
				with self.assertRaises(frappe.PermissionError):
					supplier_api.update_supplier("Acme", disabled=True)

	def test_the_refusal_names_what_the_role_cannot_do(self):
		self.as_user(["Sales User"])
		with self.assertRaises(frappe.PermissionError) as caught:
			supplier_api.create_supplier(supplier_name="Acme")
		self.assertIn("change suppliers", str(caught.exception))

	def test_the_owner_and_purchasing_managers_get_past_the_role_check(self):
		for roles in (["Business Owner"], ["System Manager"], ["Purchase Manager"]):
			with self.subTest(roles=roles):
				self.as_user(roles)
				for call in (
					lambda: supplier_api.create_supplier(supplier_name="Acme"),
					lambda: supplier_api.update_supplier("Acme", disabled=True),
				):
					try:
						call()
					except frappe.PermissionError as error:
						self.fail(f"{roles} should be let in: {error}")
					except Exception:
						pass  # it got past the role check; what the mocked code does next is not this test's business

	def test_the_role_check_is_the_first_thing_each_call_does(self):
		tree = ast.parse(inspect.getsource(supplier_api))
		for name in ("create_supplier", "update_supplier"):
			function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
			body = [
				s
				for s in function.body
				if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
			]
			self.assertEqual(ast.unparse(body[0]), "require_supplier_editor()", name)


class TestWhoMayLoadTheStarterProducts(Case):
	def run_loader(self):
		function = getattr(
			product_seeding.bulk_upload_products, "__wrapped__", product_seeding.bulk_upload_products
		)
		with patch.object(product_seeding.frappe, "form_dict", {"dry_run": "true"}, create=True):
			return function()

	def test_only_a_system_manager_may_run_it(self):
		for roles in (["Sales User"], ["Business Owner"], ["Stock Manager"], ["Purchase Manager"]):
			with self.subTest(roles=roles):
				self.as_user(roles)
				with self.assertRaises(frappe.PermissionError):
					self.run_loader()
				self.assertEqual(self.db.method_calls, [])

	def test_a_system_manager_gets_past_the_role_check(self):
		self.as_user(["System Manager"])
		try:
			self.run_loader()
		except frappe.PermissionError as error:
			self.fail(f"a System Manager should be let in: {error}")
		except Exception:
			pass  # it got past the role check; what the mocked code does next is not this test's business

	def test_the_role_check_is_the_first_thing_the_call_does(self):
		tree = ast.parse(inspect.getsource(product_seeding))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bulk_upload_products"
		)
		body = [
			s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		self.assertEqual(ast.unparse(body[0]), "require_system_manager()")


if __name__ == "__main__":
	unittest.main()
