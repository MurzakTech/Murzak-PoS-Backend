# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for who may set the main store. Setting it decides where new sales and stock in go, so a cashier is
refused before anything is read or changed. No database is needed: every Frappe call is mocked.
"""

import ast
import inspect
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import warehouse_api as api


class TestSetDefaultWarehouse(unittest.TestCase):
	def as_user(self, roles):
		db = MagicMock()
		db.exists.return_value = True
		db.get_value.return_value = "Shop A"
		for target, kwargs in (
			((frappe, "session"), {"new": frappe._dict(user="someone@shop-a.test")}),
			((frappe, "get_roles"), {"create": True, "side_effect": lambda user=None: list(roles)}),
			((frappe, "db"), {"new": db, "create": True}),
			((frappe, "log_error"), {"create": True}),
			((api, "resolve_company"), {"return_value": "Shop A"}),
			((api, "ensure_record_access"), {}),
			((api, "set_default_warehouse_for_company"), {"return_value": True}),
		):
			patcher = patch.object(*target, **kwargs)
			patcher.start()
			self.addCleanup(patcher.stop)
		self.db = db
		patcher = patch("murzak_pos.api.access_control.frappe.db", new=db, create=True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_a_cashier_cannot_set_the_main_store(self):
		self.as_user(["Sales User"])
		with self.assertRaises(frappe.PermissionError):
			api.set_default_warehouse("Shop A", "Duka Kuu - SA")
		api.set_default_warehouse_for_company.assert_not_called()

	def test_the_refusal_comes_before_anything_is_read(self):
		self.as_user(["Sales User"])
		with self.assertRaises(frappe.PermissionError):
			api.set_default_warehouse("Shop A", "Duka Kuu - SA")
		self.assertEqual(self.db.method_calls, [])
		api.resolve_company.assert_not_called()

	def test_someone_who_may_change_stock_can(self):
		for role in ("Stock Manager", "Stock User", "Business Owner", "System Manager"):
			with self.subTest(role=role):
				self.as_user([role])
				result = api.set_default_warehouse("Shop A", "Duka Kuu - SA")
				self.assertTrue(result["success"], result)
				api.set_default_warehouse_for_company.assert_called_once_with("Shop A", "Duka Kuu - SA")

	def test_the_role_check_is_the_first_thing_the_call_does(self):
		tree = ast.parse(inspect.getsource(api))
		function = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "set_default_warehouse"
		)
		body = [
			s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
		]
		self.assertEqual(ast.unparse(body[0]), "require_stock_role()")


if __name__ == "__main__":
	unittest.main()
