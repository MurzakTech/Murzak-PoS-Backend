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


class TestTheOtherStoreCalls(TestSetDefaultWarehouse):
	"""The older store calls refuse the wrong role first, before anything is read or changed."""

	def calls(self):
		return {
			"create_warehouse": lambda: api.create_warehouse("Duka", "Shop A"),
			"update_warehouse": lambda: api.update_warehouse("Duka Kuu - SA", warehouse_name="Duka"),
			"assign_warehouses_to_staff": lambda: api.assign_warehouses_to_staff(
				"a@shop-a.test", ["Duka Kuu - SA"]
			),
			"remove_warehouse_from_staff": lambda: api.remove_warehouse_from_staff(
				"a@shop-a.test", "Duka Kuu - SA"
			),
			"get_warehouse_staff": lambda: api.get_warehouse_staff("Duka Kuu - SA"),
			"get_staff_warehouses (another person)": lambda: api.get_staff_warehouses("a@shop-a.test"),
		}

	def test_a_cashier_is_refused_by_every_one_of_them(self):
		for name, call in self.calls().items():
			with self.subTest(call=name):
				self.as_user(["Sales User"])
				with self.assertRaises(frappe.PermissionError):
					call()
				self.assertEqual(self.db.method_calls, [])

	def test_a_stock_manager_may_add_and_change_a_store_but_not_choose_staff(self):
		for name, call in self.calls().items():
			self.as_user(["Stock Manager"])
			with self.subTest(call=name):
				if name in ("create_warehouse", "update_warehouse"):
					try:
						call()
					except frappe.PermissionError:
						self.fail(f"{name} should let a stock manager in")
				else:
					with self.assertRaises(frappe.PermissionError):
						call()

	def test_the_owner_cannot_choose_the_stores_of_a_person_of_another_business(self):
		self.as_user(["Business Owner"])
		self.db.get_value.return_value = "Shop B"
		patcher = patch("murzak_pos.api.access_control.get_user_companies", return_value={"Shop A"})
		patcher.start()
		self.addCleanup(patcher.stop)
		for call in (
			lambda: api.assign_warehouses_to_staff("x@shop-b.test", ["Duka Kuu - SA"]),
			lambda: api.remove_warehouse_from_staff("x@shop-b.test", "Duka Kuu - SA"),
			lambda: api.get_staff_warehouses("x@shop-b.test"),
		):
			with self.subTest(), self.assertRaises(frappe.PermissionError):
				call()

	def test_anyone_may_see_their_own_stores(self):
		self.as_user(["Sales User"])
		frappe.session.user = "someone@shop-a.test"
		try:
			api.get_staff_warehouses()
			api.get_staff_warehouses("someone@shop-a.test")
		except frappe.PermissionError:
			self.fail("a person may see their own stores")


if __name__ == "__main__":
	unittest.main()
