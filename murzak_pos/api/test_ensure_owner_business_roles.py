# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the patch that gives every business owner all the business roles. No database is needed: every Frappe
call is mocked.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api.role_tiers import BUSINESS_ROLES
from murzak_pos.patches import ensure_owner_business_roles as patch_module


class Person:
	"""A user document that remembers the roles appended to it and whether it was saved."""

	def __init__(self, roles):
		self.roles = [frappe._dict(role=r) for r in roles]
		self.flags = frappe._dict()
		self.saved = False

	def append(self, table, row):
		self.roles.append(frappe._dict(row))

	def save(self):
		self.saved = True

	def held(self):
		return {r.role for r in self.roles}


class TestTheOwnerRolesPatch(unittest.TestCase):
	def run_patch(self, owners, people, enabled=None, missing_roles=()):
		enabled = enabled or {name: 1 for name in owners}
		with (
			patch.object(frappe, "db", MagicMock(), create=True) as db,
			patch.object(frappe, "get_all", create=True, return_value=owners),
			patch.object(frappe, "get_doc", create=True, side_effect=lambda doctype, name: people[name]),
			patch("builtins.print"),
		):
			db.exists.side_effect = lambda doctype, name: name not in missing_roles
			db.get_value.side_effect = lambda doctype, name, field: enabled.get(name)
			patch_module.execute()

	def test_an_owner_short_of_business_roles_is_given_them_and_nothing_is_taken(self):
		owner = Person(["Business Owner", "Custom Role"])
		self.run_patch(["owner@shop-a.test"], {"owner@shop-a.test": owner})
		self.assertEqual(owner.held(), set(BUSINESS_ROLES) | {"Custom Role"})
		self.assertTrue(owner.saved)
		self.assertNotIn("System Manager", owner.held())

	def test_an_owner_who_has_them_all_is_left_alone(self):
		owner = Person(BUSINESS_ROLES)
		self.run_patch(["owner@shop-a.test"], {"owner@shop-a.test": owner})
		self.assertFalse(owner.saved)

	def test_a_role_the_site_does_not_have_is_skipped(self):
		owner = Person(["Business Owner"])
		self.run_patch(["owner@shop-a.test"], {"owner@shop-a.test": owner}, missing_roles=("Item Manager",))
		self.assertNotIn("Item Manager", owner.held())
		self.assertIn("Accounts Manager", owner.held())

	def test_a_deactivated_owner_and_the_administrator_are_skipped(self):
		off, admin = Person(["Business Owner"]), Person(["Business Owner"])
		self.run_patch(
			["off@shop-a.test", "Administrator"],
			{"off@shop-a.test": off, "Administrator": admin},
			enabled={"off@shop-a.test": 0, "Administrator": 1},
		)
		self.assertFalse(off.saved)
		self.assertFalse(admin.saved)

	def test_the_patch_is_listed_to_run_after_the_models_are_synced(self):
		path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "patches.txt")
		with open(path) as handle:
			lines = [line.strip() for line in handle if line.strip()]
		name = "murzak_pos.patches.ensure_owner_business_roles"
		self.assertIn(name, lines)
		self.assertGreater(lines.index(name), lines.index("[post_model_sync]"))


if __name__ == "__main__":
	unittest.main()
