# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the Team list (`team_api.list_team`, ticket P5-3) and the arithmetic behind it (`team_calc`). They need no
database: every Frappe call is mocked.

The first half tests the plain functions with plain rows: how each person is placed as Owner, Manager or Cashier,
that this agrees with `role_tiers.get_tier` for every role it knows, and the order and counts. The second half tests
the call: that a cashier and a guest are refused before anything is read, that an owner and a manager are let in,
that the company is the caller's own, and that nothing is written.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import role_tiers, team_api, team_calc

AUTOMATIC = ("Administrator", "Guest", "All", "Desk User")


def user(name, first="A", last="B", enabled=1, **extra):
	return {
		"name": name,
		"email": name,
		"first_name": first,
		"last_name": last,
		"full_name": f"{first} {last}".strip(),
		"mobile_no": "0712345678",
		"enabled": enabled,
		"creation": "2026-10-01 09:00:00",
		"last_login": None,
		**extra,
	}


class TestTierFor(unittest.TestCase):
	def test_a_system_manager_is_an_owner_even_with_other_roles(self):
		self.assertEqual(team_calc.tier_for(["Sales User", "System Manager", "Stock Manager"]), "owner")

	def test_the_administrator_is_an_owner_with_no_roles(self):
		self.assertEqual(team_calc.tier_for([], "Administrator"), "owner")

	def test_each_manager_role_makes_a_manager(self):
		for role in role_tiers.MANAGER_ROLES:
			with self.subTest(role=role):
				self.assertEqual(team_calc.tier_for([role, "Sales User"]), "manager")

	def test_everyone_else_is_a_cashier(self):
		for roles in (["Sales User"], ["Desk User"], ["Employee"], ["Stock User"], ["Quality Manager"], []):
			with self.subTest(roles=roles):
				self.assertEqual(team_calc.tier_for(roles), "cashier")

	def test_it_agrees_with_the_server_check_for_every_role_it_knows(self):
		"""The Team page and the server's own check must never place the same person differently."""
		samples = [
			[],
			["Sales User"],
			["System Manager"],
			["Sales User", "Accounts User"],
			*[[role] for role in role_tiers.MANAGER_ROLES],
			*[[role] for role in role_tiers.OWNER_ROLES],
		]
		for roles in samples:
			with self.subTest(roles=roles):
				with (
					patch.object(frappe, "get_roles", create=True, return_value=list(roles)),
					patch.object(frappe, "session", frappe._dict(user="x@shop.test")),
				):
					self.assertEqual(
						team_calc.tier_for(roles, "x@shop.test"), role_tiers.get_tier("x@shop.test")
					)

	def test_the_roles_given_for_each_kind_place_a_person_in_that_kind(self):
		for tier, roles in role_tiers.ROLES_TO_GRANT.items():
			with self.subTest(tier=tier):
				self.assertEqual(team_calc.tier_for(roles), tier)


class TestMember(unittest.TestCase):
	def test_roles_that_everybody_has_are_not_shown(self):
		person = team_calc.member(user("a@shop.test"), ["Sales User", "All", "Desk User", "Guest"], AUTOMATIC)
		self.assertEqual(person["roles"], ["Sales User"])
		self.assertEqual(person["tier"], "cashier")

	def test_a_missing_full_name_falls_back_to_the_parts_then_the_login(self):
		self.assertEqual(
			team_calc.member(user("a@shop.test", "Amina", "Otieno", full_name=""), [])["full_name"],
			"Amina Otieno",
		)
		bare = user("a@shop.test", "", "", full_name="")
		self.assertEqual(team_calc.member(bare, [])["full_name"], "a@shop.test")

	def test_the_shape_has_no_password_or_key(self):
		person = team_calc.member(user("a@shop.test"), ["Sales User"], AUTOMATIC)
		self.assertEqual(
			set(person),
			{
				"email",
				"first_name",
				"last_name",
				"full_name",
				"phone",
				"enabled",
				"tier",
				"roles",
				"created",
				"last_sign_in",
			},
		)

	def test_a_deactivated_person_is_marked_and_a_missing_login_time_is_none(self):
		person = team_calc.member(user("a@shop.test", enabled=0), [])
		self.assertFalse(person["enabled"])
		self.assertIsNone(person["last_sign_in"])


class TestOrderAndCounts(unittest.TestCase):
	def setUp(self):
		users = [
			user("c1@shop.test", "Zawadi", "C"),
			user("m1@shop.test", "Juma", "M"),
			user("o1@shop.test", "Wanjiru", "O"),
			user("c2@shop.test", "Baraka", "C", enabled=0),
			user("c3@shop.test", "Achieng", "C"),
		]
		roles = {
			"c1@shop.test": ["Sales User"],
			"m1@shop.test": ["Stock Manager", "Sales User"],
			"o1@shop.test": ["System Manager"],
			"c2@shop.test": ["Sales User"],
			"c3@shop.test": ["Sales User"],
		}
		self.team = team_calc.build_team(users, roles, AUTOMATIC)

	def test_owners_then_managers_then_active_cashiers_by_name_then_deactivated(self):
		self.assertEqual(
			[m["email"] for m in self.team],
			["o1@shop.test", "m1@shop.test", "c3@shop.test", "c1@shop.test", "c2@shop.test"],
		)

	def test_counts_leave_deactivated_people_out_of_the_kinds(self):
		self.assertEqual(
			team_calc.counts(self.team),
			{"total": 5, "active": 4, "owners": 1, "managers": 1, "cashiers": 2},
		)

	def test_a_person_without_a_roles_entry_is_a_cashier(self):
		self.assertEqual(team_calc.build_team([user("x@shop.test")], {}, AUTOMATIC)[0]["tier"], "cashier")


class TestTierSetup(unittest.TestCase):
	def test_it_gives_the_roles_of_each_kind_from_the_one_table(self):
		setup = team_calc.tier_setup()
		self.assertEqual(setup["grant"]["owner"], ["Business Owner"])
		self.assertEqual(setup["grant"]["cashier"], ["Sales User"])
		self.assertEqual(set(setup["grant"]["manager"]), set(role_tiers.ROLES_TO_GRANT["manager"]))
		self.assertEqual(setup["owner_roles"], ["Business Owner", "System Manager"])
		self.assertEqual(setup["manager_roles"], sorted(role_tiers.MANAGER_ROLES))


class TeamCase(unittest.TestCase):
	def as_user(self, session_user="boss@shop.test", roles=("System Manager",)):
		stack = [
			patch.object(frappe, "session", frappe._dict(user=session_user)),
			patch.object(frappe, "get_roles", create=True, side_effect=lambda user=None: list(roles)),
			patch("frappe.translate.get_all_translations", return_value={}),
		]
		for p in stack:
			p.start()
			self.addCleanup(p.stop)


class TestListTeam(TeamCase):
	def setUp(self):
		self.get_all = MagicMock(side_effect=self._get_all)
		self.resolve = MagicMock(return_value="Shop A")
		for p in (
			patch.object(team_api.frappe, "get_all", self.get_all),
			patch.object(team_api, "resolve_company", self.resolve),
		):
			p.start()
			self.addCleanup(p.stop)

	def _get_all(self, doctype, filters=None, fields=None, **kwargs):
		if doctype == "User":
			return [user("boss@shop.test", "Boss", "Owner"), user("till@shop.test", "Till", "Person")]
		if doctype == "Has Role":
			return [
				{"parent": "boss@shop.test", "role": "System Manager"},
				{"parent": "till@shop.test", "role": "Sales User"},
				{"parent": "till@shop.test", "role": "All"},
			]
		raise AssertionError(f"unexpected read of {doctype}")

	def test_a_guest_is_refused_before_anything_is_read(self):
		self.as_user("Guest", roles=())
		with self.assertRaises(frappe.AuthenticationError):
			team_api.list_team()
		self.get_all.assert_not_called()

	def test_a_cashier_is_refused_before_anything_is_read(self):
		self.as_user("till@shop.test", roles=("Sales User",))
		with self.assertRaises(frappe.PermissionError):
			team_api.list_team()
		self.get_all.assert_not_called()
		self.resolve.assert_not_called()

	def test_an_owner_gets_the_team_with_each_persons_kind(self):
		self.as_user()
		reply = team_api.list_team()["data"]
		self.assertEqual(reply["company"], "Shop A")
		self.assertEqual(
			[(m["email"], m["tier"]) for m in reply["members"]],
			[("boss@shop.test", "owner"), ("till@shop.test", "cashier")],
		)
		self.assertEqual(reply["members"][1]["roles"], ["Sales User"])
		self.assertEqual(reply["counts"]["total"], 2)
		self.assertEqual(reply["me"], "boss@shop.test")
		self.assertIn("grant", reply["tiers"])

	def test_a_manager_may_read_it(self):
		self.as_user("boss2@shop.test", roles=("Stock Manager", "Sales User"))
		self.assertTrue(team_api.list_team()["success"])

	def test_only_the_callers_own_business_is_read(self):
		self.as_user()
		team_api.list_team(company="Shop B")
		self.resolve.assert_called_once_with("Shop B")
		user_call = next(c for c in self.get_all.call_args_list if c.args[0] == "User")
		self.assertEqual(user_call.kwargs["filters"], {"custom_company": "Shop A"})

	def test_enabled_only_leaves_out_deactivated_people_in_the_query(self):
		self.as_user()
		for value, expected in ((1, True), ("1", True), ("true", True), (0, False), ("0", False)):
			with self.subTest(value=value):
				self.get_all.reset_mock()
				team_api.list_team(enabled_only=value)
				user_call = next(c for c in self.get_all.call_args_list if c.args[0] == "User")
				self.assertEqual("enabled" in user_call.kwargs["filters"], expected)

	def test_an_empty_business_gives_an_empty_team_without_reading_roles(self):
		self.as_user()
		self.get_all.side_effect = (
			lambda doctype, **kw: [] if doctype == "User" else self._get_all(doctype, **kw)
		)
		reply = team_api.list_team()["data"]
		self.assertEqual(reply["members"], [])
		self.assertEqual([c.args[0] for c in self.get_all.call_args_list], ["User"])

	def test_nothing_is_written(self):
		self.as_user()
		with (
			patch.object(frappe, "db", MagicMock()) as db,
			patch.object(frappe, "get_doc", create=True) as get_doc,
		):
			team_api.list_team()
		db.set_value.assert_not_called()
		db.sql.assert_not_called()
		get_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
