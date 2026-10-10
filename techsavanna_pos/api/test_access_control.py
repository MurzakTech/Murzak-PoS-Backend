# Copyright (c) 2026, Techsavanna POS and Contributors
# See license.txt
"""
Tests for who may manage staff and roles, and which company a caller may ask about. They need no
database: every Frappe call is mocked, so they run under `bench run-tests` and in plain Python alike.

The last class reads the source files rather than running them, so a call added later without the
right guard (an open call with no rate limit, a staff or role change with no owner check) fails here.
"""

import ast
import importlib
import inspect
import os
import types
import unittest
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe

from techsavanna_pos.api import (
	access_control,
	auth,
	auth_api,
	customer_api,
	dashboard_api,
	role_api,
	staff_api,
	supplier_api,
)

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")
CASHIER_ROLES = ("Sales User",)


class AccessCase(unittest.TestCase):
	def as_user(self, user="owner@shop-a.test", roles=OWNER_ROLES, companies=("Shop A",), role_row=None):
		"""Run as `user` holding `roles`, who belongs to `companies`. `role_row` is what a Role lookup finds."""
		db = MagicMock()
		db.exists.return_value = True
		db.get_value.side_effect = self._lookup(role_row)
		self.db = db
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "log_error", create=True),
			patch(
				"frappe.translate.get_all_translations", return_value={}
			),  # messages are not translated in tests
			patch(
				"techsavanna_pos.api.payment_gateway_common.get_user_companies", return_value=set(companies)
			),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)

	def _lookup(self, role_row):
		def get_value(doctype, name=None, fieldname=None, *args, **kwargs):
			if doctype == "Role":
				return role_row
			if doctype == "User" and fieldname == "custom_company":
				return self.companies_by_user.get(name)
			return None

		self.companies_by_user = {}
		return get_value


class TestRequireManager(AccessCase):
	def test_guest_is_refused(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			access_control.require_manager()

	def test_cashier_is_refused(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError):
			access_control.require_manager()

	def test_a_sales_manager_is_not_enough(self):
		self.as_user("sales@shop-a.test", roles=("Sales Manager", "Sales User"))
		with self.assertRaises(frappe.PermissionError):
			access_control.require_manager()

	def test_business_owner_and_administrator_pass(self):
		self.as_user("owner@shop-a.test", roles=OWNER_ROLES)
		access_control.require_manager()
		self.as_user("Administrator", roles=())
		access_control.require_manager()


class TestRequireRolesHeld(AccessCase):
	def test_an_owner_may_give_roles_they_hold(self):
		self.as_user(roles=OWNER_ROLES)
		access_control.require_roles_held(["Sales User", "Accounts Manager"])

	def test_a_role_the_caller_lacks_is_refused_and_named(self):
		self.as_user(roles=("System Manager", "Sales User"))
		with self.assertRaises(frappe.PermissionError) as refused:
			access_control.require_roles_held(["Sales User", "Auditor"])
		self.assertIn("Auditor", str(refused.exception))

	def test_nothing_to_give_is_fine(self):
		self.as_user(roles=("System Manager",))
		access_control.require_roles_held([])
		access_control.require_roles_held(None)

	def test_administrator_may_give_any_role(self):
		self.as_user("Administrator", roles=())
		access_control.require_roles_held(["System Manager", "Auditor"])


class TestRequireRoleManageable(AccessCase):
	def test_a_standard_role_is_refused_to_an_owner(self):
		self.as_user(role_row=(0, "Administrator"))
		with self.assertRaises(frappe.PermissionError) as refused:
			access_control.require_role_manageable("Sales User")
		self.assertIn("standard role", str(refused.exception))

	def test_administrator_may_change_a_standard_role(self):
		self.as_user("Administrator", roles=(), role_row=(0, "Administrator"))
		access_control.require_role_manageable("Sales User")

	def test_the_creator_may_change_their_own_role(self):
		self.as_user("owner@shop-a.test", role_row=(1, "owner@shop-a.test"))
		access_control.require_role_manageable("Barista")

	def test_a_colleague_from_the_same_business_may_change_it(self):
		self.as_user("deputy@shop-a.test", role_row=(1, "owner@shop-a.test"))
		self.companies_by_user = {"deputy@shop-a.test": "Shop A", "owner@shop-a.test": "Shop A"}
		access_control.require_role_manageable("Barista")

	def test_another_business_may_not_change_it(self):
		self.as_user("boss@shop-b.test", role_row=(1, "owner@shop-a.test"))
		self.companies_by_user = {"boss@shop-b.test": "Shop B", "owner@shop-a.test": "Shop A"}
		with self.assertRaises(frappe.PermissionError):
			access_control.require_role_manageable("Barista")

	def test_a_user_with_no_business_never_matches_another_with_none(self):
		self.as_user("loner@x.test", role_row=(1, "other@x.test"))
		with self.assertRaises(frappe.PermissionError):
			access_control.require_role_manageable("Barista")

	def test_a_role_that_does_not_exist_is_left_for_the_call_to_report(self):
		self.as_user(role_row=None)
		access_control.require_role_manageable("Nothing")


class TestStaffCalls(AccessCase):
	def calls(self):
		return [
			lambda: staff_api.create_staff_user(
				email="new@shop-a.test", first_name="A", last_name="B", password="longenough1", roles=[]
			),
			lambda: staff_api.assign_roles_to_staff(user_email="till@shop-a.test", roles=["Sales User"]),
			lambda: staff_api.update_staff_user(user_email="till@shop-a.test", first_name="Z"),
			lambda: staff_api.remove_roles_from_staff(user_email="till@shop-a.test", roles=["Sales User"]),
			lambda: staff_api.disable_staff_user(user_email="till@shop-a.test"),
			lambda: staff_api.enable_staff_user(user_email="till@shop-a.test"),
		]

	def test_a_cashier_may_not_manage_staff(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			patch.object(frappe, "get_doc", create=True) as get_doc,
		):
			for call in self.calls():
				with self.assertRaises(frappe.PermissionError):
					call()
		new_doc.assert_not_called()
		get_doc.assert_not_called()
		self.db.set_value.assert_not_called()

	def test_a_guest_may_not_manage_staff(self):
		self.as_user("Guest", roles=())
		for call in self.calls():
			with self.assertRaises((frappe.AuthenticationError, frappe.PermissionError)):
				call()

	def test_an_owner_may_not_create_staff_in_another_business(self):
		self.as_user("owner@shop-a.test", roles=OWNER_ROLES, companies=("Shop A",))
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			self.assertRaises(frappe.PermissionError),
		):
			staff_api.create_staff_user(
				email="new@shop-b.test",
				first_name="A",
				last_name="B",
				password="longenough1",
				roles=[],
				company="Shop B",
			)
		new_doc.assert_not_called()

	def test_an_owner_may_not_give_staff_a_role_the_owner_lacks(self):
		self.as_user("owner@shop-a.test", roles=("System Manager", "Sales User"))
		self.companies_by_user = {"till@shop-a.test": "Shop A"}
		with (
			patch.object(staff_api, "get_current_user_company", return_value="Shop A"),
			patch.object(staff_api, "validate_roles", return_value=True),
			patch.object(frappe, "get_doc", create=True) as get_doc,
			self.assertRaises(frappe.PermissionError) as refused,
		):
			staff_api.assign_roles_to_staff(user_email="till@shop-a.test", roles=["Auditor"])
		self.assertIn("Auditor", str(refused.exception))
		get_doc.assert_not_called()

	def test_an_owner_may_not_create_staff_with_a_role_the_owner_lacks(self):
		self.as_user("owner@shop-a.test", roles=("System Manager", "Sales User"), companies=("Shop A",))
		self.db.exists.side_effect = lambda doctype, name=None: (
			doctype != "User"
		)  # the new email is not taken yet
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			self.assertRaises(frappe.PermissionError),
		):
			staff_api.create_staff_user(
				email="new@shop-a.test",
				first_name="A",
				last_name="B",
				password="longenough1",
				roles=["Auditor"],
				company="Shop A",
			)
		new_doc.assert_not_called()


class TestRoleCalls(AccessCase):
	def calls(self):
		return [
			lambda: role_api.create_role(role_name="Barista"),
			lambda: role_api.update_role(role_name="Barista", desk_access=True),
			lambda: role_api.delete_role(role_name="Barista"),
			lambda: role_api.disable_role(role_name="Barista"),
			lambda: role_api.enable_role(role_name="Barista"),
			lambda: role_api.assign_permissions_to_role(
				role_name="Barista", doctype="User", permissions={"read": 1, "write": 1}
			),
			lambda: role_api.remove_permissions_from_role(role_name="Barista", doctype="User"),
		]

	def test_a_cashier_may_not_change_roles_or_permissions(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES, role_row=(1, "till@shop-a.test"))
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			patch.object(frappe, "delete_doc", create=True) as delete_doc,
		):
			for call in self.calls():
				with self.assertRaises(frappe.PermissionError):
					call()
		new_doc.assert_not_called()
		delete_doc.assert_not_called()

	def test_an_owner_may_not_touch_a_standard_role_shared_by_every_business(self):
		self.as_user("owner@shop-a.test", roles=OWNER_ROLES, role_row=(0, "Administrator"))
		calls = self.calls()[1:]  # create_role makes a new role, so it has no standard role to protect
		with patch.object(frappe, "delete_doc", create=True) as delete_doc:
			for call in calls:
				with self.assertRaises(frappe.PermissionError):
					call()
		delete_doc.assert_not_called()

	def test_an_owner_may_not_touch_another_businesss_role(self):
		self.as_user("boss@shop-b.test", roles=OWNER_ROLES, role_row=(1, "owner@shop-a.test"))
		self.companies_by_user = {"boss@shop-b.test": "Shop B", "owner@shop-a.test": "Shop A"}
		with self.assertRaises(frappe.PermissionError):
			role_api.delete_role(role_name="Barista")


class TestGrantAllPermissions(AccessCase):
	def test_only_an_owner_may_use_it_in_either_copy_of_the_auth_module(self):
		for module in (auth, auth_api):
			with self.subTest(module=module.__name__):
				self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
				with patch.object(module, "assign_all_business_roles") as assign:
					with self.assertRaises(frappe.PermissionError):
						module.grant_all_permissions()
				assign.assert_not_called()

	def test_a_guest_is_refused(self):
		for module in (auth, auth_api):
			self.as_user("Guest", roles=())
			with (
				patch.object(module, "assign_all_business_roles") as assign,
				self.assertRaises(frappe.AuthenticationError),
			):
				module.grant_all_permissions()
			assign.assert_not_called()

	def test_an_owner_still_can(self):
		for module in (auth, auth_api):
			self.as_user("owner@shop-a.test", roles=OWNER_ROLES)
			user_doc = types.SimpleNamespace(roles=[types.SimpleNamespace(role="System Manager")])
			with (
				patch.object(module, "assign_all_business_roles") as assign,
				patch.object(frappe, "get_doc", create=True, return_value=user_doc),
			):
				reply = module.grant_all_permissions()
			assign.assert_called_once_with("owner@shop-a.test")
			self.assertTrue(reply["success"])


class TestOnlyYourOwnCompany(AccessCase):
	"""Naming another business's company in the request is refused, and nothing is read."""

	def setUp(self):
		# A sales manager passes the role checks (credit limits need one), so only the company is in question
		self.as_user("manager@shop-a.test", roles=("Sales Manager", "Sales User"), companies=("Shop A",))
		get_all = patch.object(frappe, "get_all", create=True)
		self.get_all = get_all.start()
		self.addCleanup(get_all.stop)

	def assert_refused(self, reply):
		self.assertFalse(reply["success"])
		self.assertIn("access to company Shop B", reply["message"])
		self.get_all.assert_not_called()

	def test_customers(self):
		self.assert_refused(customer_api.list_customers(company="Shop B"))
		self.assert_refused(customer_api.get_customer(name="CUST-1", company="Shop B"))
		self.assert_refused(customer_api.update_customer(name="CUST-1", company="Shop B"))
		self.assert_refused(customer_api.create_customer(customer_name="X", company="Shop B"))

	def test_customer_credit_limits(self):
		self.assert_refused(
			customer_api.set_customer_credit_limit(customer="CUST-1", company="Shop B", credit_limit=100)
		)
		self.assert_refused(customer_api.get_customer_credit_limit(customer="CUST-1", company="Shop B"))
		self.assert_refused(customer_api.get_customer_credit_history(customer="CUST-1", company="Shop B"))
		self.assert_refused(customer_api.remove_customer_credit_limit(customer="CUST-1", company="Shop B"))

	def test_dashboard(self):
		# explicit dates, so the call does not read the system timezone from the database first
		self.assert_refused(
			dashboard_api.get_dashboard_metrics(
				company="Shop B", period="custom", from_date="2026-01-01", to_date="2026-01-31"
			)
		)

	def test_dashboard_filters_for_the_callers_own_company(self):
		filters, company = dashboard_api._build_base_filters("Shop A")
		self.assertEqual(company, "Shop A")
		self.assertEqual(filters["company"], "Shop A")

	def test_suppliers(self):
		self.assert_refused(supplier_api.get_suppliers(company="Shop B"))


# The sensitive calls and the check each one must make first. The roles behind each check are
# copied from the frontend's role table, so the server and the screens agree.
SENSITIVE_CALLS = {
	"require_catalogue_role": {
		"product_api": [
			"create_product",
			"update_product",
			"delete_product",
			"set_product_price",
			"bulk_update_prices",
			"bulk_create_products",
			"bulk_import_products",
			"bulk_import_opening_stock",
			"create_price_list",
			"update_price_list",
			"delete_price_list",
			"create_product_variant",
		],
	},
	"require_stock_role": {
		"inventory_api": [
			"create_stock_entry",
			"create_material_issue",
			"create_material_receipt",
			"create_material_transfer",
			"create_stock_reconciliation",
			"update_stock_entry",
			"submit_stock_entry",
			"cancel_stock_entry",
		],
	},
	"require_stock_count_role": {"inventory_api": ["create_multi_level_stock_reconciliation"]},
	"require_credit_role": {"customer_api": ["set_customer_credit_limit", "remove_customer_credit_limit"]},
	"require_loyalty_role": {"loyalty": ["create_loyalty_program", "assign_loyalty_program"]},
	"require_manager": {
		"inventory_api": [
			"create_inventory_discount_rule",
			"update_inventory_discount_rule",
			"delete_inventory_discount_rule",
		]
	},
}


class TestRoleSets(AccessCase):
	"""Which roles each kind of change takes, read from the frontend's role table."""

	def allowed(self, check, roles):
		self.as_user("someone@shop-a.test", roles=roles)
		try:
			check()
		except frappe.PermissionError:
			return False
		return True

	def test_a_cashier_may_do_none_of_them(self):
		for name in SENSITIVE_CALLS:
			with self.subTest(check=name):
				self.assertFalse(self.allowed(getattr(access_control, name), CASHIER_ROLES))

	def test_the_all_role_never_grants_anything(self):
		# Frappe gives every user the "All" role, so it must not count as a manager role
		for name in SENSITIVE_CALLS:
			with self.subTest(check=name):
				self.assertFalse(self.allowed(getattr(access_control, name), ("All", "Guest")))

	def test_the_owner_may_do_all_of_them(self):
		for name in SENSITIVE_CALLS:
			with self.subTest(check=name):
				self.assertTrue(self.allowed(getattr(access_control, name), ("System Manager",)))

	def test_a_guest_is_refused_with_a_sign_in_error(self):
		self.as_user("Guest", roles=())
		for name in SENSITIVE_CALLS:
			with self.subTest(check=name), self.assertRaises(frappe.AuthenticationError):
				getattr(access_control, name)()

	def test_prices_and_stock_are_separate_jobs(self):
		prices, stock = access_control.require_catalogue_role, access_control.require_stock_role
		self.assertTrue(self.allowed(prices, ("Sales Manager",)))
		self.assertFalse(self.allowed(stock, ("Sales Manager",)))
		self.assertTrue(self.allowed(stock, ("Stock User",)))
		self.assertFalse(self.allowed(prices, ("Stock User",)))

	def test_a_stock_user_may_move_stock_but_not_start_a_stock_count(self):
		self.assertTrue(self.allowed(access_control.require_stock_role, ("Stock User",)))
		self.assertFalse(self.allowed(access_control.require_stock_count_role, ("Stock User",)))
		self.assertTrue(self.allowed(access_control.require_stock_count_role, ("Stock Manager",)))

	def test_credit_limits_follow_the_frontends_credit_screen(self):
		credit = access_control.require_credit_role
		for role in ("Sales Manager", "Accounts Manager", "Accounts User"):
			self.assertTrue(self.allowed(credit, (role,)), role)
		for role in ("Sales User", "Stock Manager", "Purchase Manager"):
			self.assertFalse(self.allowed(credit, (role,)), role)

	def test_loyalty_programs_are_for_owners_and_accountants(self):
		loyalty_check = access_control.require_loyalty_role
		self.assertTrue(self.allowed(loyalty_check, ("Accounts Manager",)))
		self.assertFalse(self.allowed(loyalty_check, ("Sales Manager",)))

	def test_the_refusal_names_what_was_refused_and_who_may(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		with self.assertRaises(frappe.PermissionError) as refused:
			access_control.require_credit_role()
		self.assertIn("credit limits", str(refused.exception))
		self.assertIn("Sales Manager", str(refused.exception))


class TestSensitiveCallsRefuseACashier(AccessCase):
	"""Each call refuses a cashier before doing anything. Runs where the module can be imported."""

	def test_every_sensitive_call_refuses_a_cashier(self):
		self.as_user("till@shop-a.test", roles=CASHIER_ROLES)
		skipped = []
		with (
			patch.object(frappe, "new_doc", create=True) as new_doc,
			patch.object(frappe, "get_doc", create=True) as get_doc,
		):
			for modules in SENSITIVE_CALLS.values():
				for module_name, names in modules.items():
					try:
						module = importlib.import_module(f"techsavanna_pos.api.{module_name}")
					except ImportError:  # these older modules need ERPNext, which is only on a full bench
						skipped.append(module_name)
						continue
					for name in names:
						# Call the function itself, past Frappe's argument-type wrapper: the check runs first
						function = inspect.unwrap(getattr(module, name))
						arguments = {
							parameter.name: "x"
							for parameter in inspect.signature(function).parameters.values()
							if parameter.default is inspect.Parameter.empty
							and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
						}
						with (
							self.subTest(call=f"{module_name}.{name}"),
							self.assertRaises(frappe.PermissionError),
						):
							function(**arguments)
		new_doc.assert_not_called()
		get_doc.assert_not_called()
		self.db.set_value.assert_not_called()
		if skipped:
			self.skipTest(f"ran the rest; not importable without ERPNext: {sorted(set(skipped))}")


class TestGuardsAreNotForgotten(unittest.TestCase):
	"""Reads the source files, so a new call that skips a guard fails here."""

	API = os.path.dirname(os.path.abspath(__file__))

	# Open to the world on purpose, and why
	OPEN_WITHOUT_LIMIT: ClassVar[dict[str, str]] = {
		"payment_callbacks.py": "Safaricom, Pesapal and PayPal call these, not people",
		"industry_api.py": "a public read-only list of business types",
		"product_seeding.py": "a public read-only list of business types",
	}

	# Calls that change who may do what: owners only
	OWNER_ONLY: ClassVar[dict[str, list[str]]] = {
		"staff_api.py": [
			"create_staff_user",
			"assign_roles_to_staff",
			"update_staff_user",
			"remove_roles_from_staff",
			"disable_staff_user",
			"enable_staff_user",
		],
		"role_api.py": [
			"create_role",
			"update_role",
			"delete_role",
			"disable_role",
			"enable_role",
			"assign_permissions_to_role",
			"remove_permissions_from_role",
		],
		"auth.py": ["grant_all_permissions"],
		"auth_api.py": ["grant_all_permissions"],
	}

	def functions(self, filename):
		path = os.path.join(self.API, filename)
		with open(path) as handle:
			source = handle.read()
		for node in ast.parse(source).body:
			if isinstance(node, ast.FunctionDef):
				yield (
					node,
					[ast.unparse(d) for d in node.decorator_list],
					ast.get_source_segment(source, node),
				)

	def test_every_call_open_to_guests_has_a_rate_limit(self):
		unlimited = []
		for filename in sorted(os.listdir(self.API)):
			if (
				not filename.endswith(".py")
				or filename.startswith("test_")
				or filename in self.OPEN_WITHOUT_LIMIT
			):
				continue
			for node, decorators, _source in self.functions(filename):
				if any("allow_guest=True" in d for d in decorators) and not any(
					"rate_limit" in d for d in decorators
				):
					unlimited.append(f"{filename}:{node.name}")
		self.assertEqual(
			unlimited, [], "Calls anyone can reach need @rate_limit (or a reason in OPEN_WITHOUT_LIMIT)"
		)

	def test_every_staff_and_role_change_checks_for_an_owner(self):
		unguarded = []
		for filename, names in self.OWNER_ONLY.items():
			found = {node.name: source for node, _d, source in self.functions(filename)}
			for name in names:
				if name not in found:
					unguarded.append(f"{filename}:{name} (missing)")
				elif "require_manager(" not in found[name]:
					unguarded.append(f"{filename}:{name}")
		self.assertEqual(unguarded, [])

	def test_every_sensitive_call_makes_its_check_first(self):
		missing = []
		for check, modules in SENSITIVE_CALLS.items():
			for module_name, names in modules.items():
				found = {node.name: source for node, _d, source in self.functions(f"{module_name}.py")}
				for name in names:
					source = found.get(name)
					if source is None:
						missing.append(f"{module_name}.{name} (not found)")
						continue
					call = source.find(f"{check}()")
					risky = [
						source.find(word) for word in ("frappe.db", "frappe.get_", "frappe.new_doc", "try:")
					]
					risky = [position for position in risky if position != -1]
					if call == -1 or (risky and call > min(risky)):
						missing.append(f"{module_name}.{name} (needs {check}() before it touches anything)")
		self.assertEqual(missing, [])

	def test_every_role_change_checks_the_role_is_theirs_to_change(self):
		found = {node.name: source for node, _d, source in self.functions("role_api.py")}
		for name in self.OWNER_ONLY["role_api.py"]:
			if name != "create_role":
				self.assertIn("require_role_manageable(role_name)", found[name], name)


if __name__ == "__main__":
	unittest.main()
