# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for Owner, Manager and Cashier and the one check that protects profit, cost and money owed. They need no
database: every Frappe call is mocked.

What they pin down: how each person is placed from the roles they already hold; that a cashier is refused a
profit-only call before anything is read; that an owner and a manager are let through; that a guest is refused;
that an unknown capability is refused rather than allowed; and that the profit call checks first.
"""

import ast
import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import profit_api, role_tiers

OWNER_ROLES = ("System Manager", "Sales Manager", "Sales User", "Accounts Manager")


class TierCase(unittest.TestCase):
	def as_user(self, user="someone@shop-a.test", roles=("Sales User",)):
		stack = [
			patch.object(frappe, "session", frappe._dict(user=user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch("frappe.translate.get_all_translations", return_value={}),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestWhoIsWho(TierCase):
	def test_the_administrator_is_an_owner(self):
		self.as_user("Administrator", roles=())
		self.assertEqual(role_tiers.get_tier(), role_tiers.OWNER)

	def test_a_system_manager_is_an_owner_even_with_other_roles(self):
		self.as_user(roles=OWNER_ROLES)
		self.assertEqual(role_tiers.get_tier(), role_tiers.OWNER)

	def test_each_manager_role_makes_a_manager(self):
		for role in role_tiers.MANAGER_ROLES:
			with self.subTest(role=role):
				with patch.object(frappe, "get_roles", create=True, return_value=[role, "Sales User"]):
					self.as_user(roles=[role, "Sales User"])
					self.assertEqual(role_tiers.get_tier(), role_tiers.MANAGER)

	def test_the_everyday_till_roles_are_cashiers(self):
		for roles in (
			["Sales User"],
			["Desk User"],
			["Employee"],
			["Stock User"],
			["Purchase User"],
			["Quality Manager"],
			[],
		):
			with self.subTest(roles=roles):
				self.as_user(roles=roles)
				self.assertEqual(role_tiers.get_tier(), role_tiers.CASHIER)

	def test_the_all_role_that_every_user_has_does_not_make_anyone_a_manager(self):
		self.as_user(roles=["All", "Guest", "Sales User"])
		self.assertEqual(role_tiers.get_tier(), role_tiers.CASHIER)

	def test_a_guest_is_nobody(self):
		self.as_user("Guest", roles=["Guest"])
		self.assertIsNone(role_tiers.get_tier())

	def test_another_user_can_be_asked_about(self):
		self.as_user(roles=("Sales User",))
		with patch.object(
			frappe,
			"get_roles",
			create=True,
			side_effect=lambda user=None: ["System Manager"]
			if user == "boss@shop-a.test"
			else ["Sales User"],
		):
			self.assertEqual(role_tiers.get_tier("boss@shop-a.test"), role_tiers.OWNER)
			self.assertEqual(role_tiers.get_tier(), role_tiers.CASHIER)

	def test_the_manager_roles_are_not_owner_roles_and_every_grant_makes_that_tier(self):
		self.assertFalse(role_tiers.MANAGER_ROLES & role_tiers.OWNER_ROLES)
		for tier, roles in role_tiers.ROLES_TO_GRANT.items():
			with self.subTest(tier=tier):
				self.as_user(roles=roles)
				self.assertEqual(role_tiers.get_tier(), tier)


class TestWhoMaySee(TierCase):
	def test_profit_cost_and_money_owed_are_for_owners_and_managers(self):
		for capability in ("profit", "cost_of_goods", "money_owed", "all_sales"):
			with self.subTest(capability=capability):
				self.as_user(roles=("System Manager",))
				self.assertTrue(role_tiers.can(capability))
				self.as_user(roles=("Accounts Manager",))
				self.assertTrue(role_tiers.can(capability))
				self.as_user(roles=("Sales User",))
				self.assertFalse(role_tiers.can(capability))

	def test_the_team_is_for_owners_only(self):
		self.as_user(roles=("Sales Manager",))
		self.assertFalse(role_tiers.can("team"))
		self.as_user(roles=("System Manager",))
		self.assertTrue(role_tiers.can("team"))

	def test_an_unknown_capability_is_refused_not_allowed(self):
		self.as_user(roles=("System Manager",))
		with self.assertRaises(ValueError):
			role_tiers.can("anything")
		with self.assertRaises(ValueError):
			role_tiers.require_access("anything")


class TestTheCheck(TierCase):
	def test_a_cashier_is_refused(self):
		self.as_user(roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			role_tiers.require_access("profit")

	def test_a_cashier_is_refused_for_every_protected_figure(self):
		self.as_user(roles=("Sales User",))
		for capability in ("profit", "cost_of_goods", "money_owed", "all_sales", "team"):
			with self.subTest(capability=capability), self.assertRaises(frappe.PermissionError):
				role_tiers.require_access(capability)

	def test_an_owner_and_a_manager_are_let_through(self):
		for roles in (("System Manager",), ("Sales Manager",), ("Accounts User",)):
			with self.subTest(roles=roles):
				self.as_user(roles=roles)
				role_tiers.require_access("profit")

	def test_a_manager_is_refused_what_only_an_owner_may_do(self):
		self.as_user(roles=("Sales Manager",))
		with self.assertRaises(frappe.PermissionError):
			role_tiers.require_access("team")

	def test_a_guest_is_asked_to_sign_in(self):
		self.as_user("Guest", roles=["Guest"])
		with self.assertRaises(frappe.AuthenticationError):
			role_tiers.require_access("profit")


class TestMyAccess(TierCase):
	def test_a_cashier_is_told_they_cannot_see_profit(self):
		self.as_user(roles=("Sales User",))
		reply = role_tiers.get_my_access()
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"]["tier"], "cashier")
		self.assertFalse(reply["data"]["can"]["profit"])
		self.assertFalse(reply["data"]["can"]["team"])

	def test_a_manager_sees_profit_but_not_the_team(self):
		self.as_user(roles=("Stock Manager",))
		data = role_tiers.get_my_access()["data"]
		self.assertEqual(data["tier"], "manager")
		self.assertTrue(data["can"]["profit"])
		self.assertFalse(data["can"]["team"])

	def test_an_owner_can_do_everything_listed(self):
		self.as_user(roles=("System Manager",))
		data = role_tiers.get_my_access()["data"]
		self.assertEqual(data["tier"], "owner")
		self.assertTrue(all(data["can"].values()))

	def test_a_guest_is_refused(self):
		self.as_user("Guest", roles=["Guest"])
		with self.assertRaises(frappe.AuthenticationError):
			role_tiers.get_my_access()


class TestProfitCall(TierCase):
	"""A request made as Cashier is refused by the server for a profit-only call (first half of AC-14)."""

	def run_call(self, roles, user="someone@shop-a.test"):
		self.as_user(user, roles=roles)
		reading = {
			"stats": patch.object(profit_api, "_get_profit_stats", return_value={"net_profit": 100.0}),
			"expenses": patch.object(profit_api, "_get_operating_expenses", return_value=40.0),
			"filters": patch.object(
				profit_api, "_build_base_filters", return_value=({"company": "Shop A"}, "Shop A")
			),
			"warehouse": patch.object(profit_api, "ensure_record_access"),
		}
		self.reads = {name: p.start() for name, p in reading.items()}
		for p in reading.values():
			self.addCleanup(p.stop)
		return profit_api.get_profit_summary(from_date="2026-10-01", to_date="2026-10-12")

	def test_a_cashier_is_refused_and_nothing_is_read(self):
		with self.assertRaises(frappe.PermissionError):
			self.run_call(("Sales User",))
		self.reads["stats"].assert_not_called()
		self.reads["expenses"].assert_not_called()
		self.reads["filters"].assert_not_called()

	def test_a_guest_is_refused_and_nothing_is_read(self):
		with self.assertRaises(frappe.AuthenticationError):
			self.run_call(["Guest"], user="Guest")
		self.reads["stats"].assert_not_called()

	def test_a_manager_gets_the_figures(self):
		reply = self.run_call(("Accounts Manager",))
		self.assertTrue(reply["success"])
		self.assertEqual(reply["data"], {"net_profit": 100.0})
		self.reads["expenses"].assert_called_once()
		self.assertEqual(reply["filters"]["from_date"], "2026-10-01")

	def test_an_owner_gets_the_figures(self):
		self.assertEqual(self.run_call(("System Manager",))["data"], {"net_profit": 100.0})

	def test_the_expenses_are_passed_to_the_profit_working(self):
		self.run_call(("System Manager",))
		self.assertEqual(self.reads["stats"].call_args.args[2], 40.0)

	def test_no_dates_means_the_last_thirty_days(self):
		self.as_user(roles=("System Manager",))
		with (
			patch.object(profit_api, "_get_profit_stats", return_value={}),
			patch.object(profit_api, "_get_operating_expenses", return_value=0.0),
			patch.object(profit_api, "_build_base_filters", return_value=({}, "Shop A")) as filters,
			patch.object(profit_api, "ensure_record_access"),
			patch.object(profit_api, "nowdate", return_value="2026-10-12"),
		):
			profit_api.get_profit_summary()
		self.assertEqual(filters.call_args.args[3:5], ("2026-09-12", "2026-10-12"))


class TestTheCheckIsNotForgotten(unittest.TestCase):
	"""Source checks, so a later edit cannot quietly move the check below a database read."""

	def test_the_profit_call_checks_access_before_anything_else(self):
		path = os.path.join(os.path.dirname(profit_api.__file__), "profit_api.py")
		with open(path, encoding="utf-8") as handle:
			tree = ast.parse(handle.read())
		call = next(
			n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_profit_summary"
		)
		body = [
			n for n in call.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
		]  # skip the docstring
		first = body[0]
		self.assertIsInstance(first, ast.Expr)
		self.assertEqual(first.value.func.id, "require_access")
		self.assertEqual(first.value.args[0].value, "profit")

	def test_the_profit_call_is_open_to_the_browser_only_through_the_check(self):
		self.assertIn(profit_api.get_profit_summary, frappe.whitelisted)
