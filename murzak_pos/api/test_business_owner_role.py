# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Business owners hold Business Owner, never System Manager. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import access_control, approval_rules, auth, auth_api, role_tiers


class TestTheOwnerRole(unittest.TestCase):
	def as_user(self, roles):
		for p in (
			patch.object(frappe, "session", frappe._dict(user="owner@shop.test"), create=True),
			patch.object(frappe, "get_roles", return_value=list(roles), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_a_business_owner_is_an_owner_everywhere(self):
		self.as_user(["Business Owner"])
		self.assertEqual(role_tiers.get_tier(), role_tiers.OWNER)
		access_control.require_manager()
		self.assertTrue(set(approval_rules.approver_roles(approval_rules.REFUND)) & {"Business Owner"})
		self.assertIsNone(
			approval_rules.decision_problem(
				approval_rules.REFUND, "owner@shop.test", "owner@shop.test", ["Business Owner"]
			)
		)

	def test_making_someone_an_owner_gives_business_owner(self):
		self.assertEqual(role_tiers.ROLES_TO_GRANT[role_tiers.OWNER], ("Business Owner",))

	def test_no_sign_up_or_onboarding_role_list_holds_system_manager(self):
		for module in (auth_api, auth):
			with self.subTest(module=module.__name__):
				self.assertIn("Business Owner", module.STANDARD_BUSINESS_ROLES)
				self.assertNotIn("System Manager", module.STANDARD_BUSINESS_ROLES)

	def test_the_role_is_made_once(self):
		db = MagicMock()
		db.exists.return_value = False
		with (
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "new_doc", create=True) as new_doc,
		):
			role_tiers.ensure_owner_role()
			db.exists.return_value = True
			role_tiers.ensure_owner_role()
		new_doc.assert_called_once_with("Role")
		self.assertEqual(new_doc.return_value.role_name, "Business Owner")


class TestMovingOwners(unittest.TestCase):
	def run_patch(self, holders, companies, operators=(), disabled=()):
		from murzak_pos.patches import move_owners_to_business_owner as patch_module

		docs = {}

		def get_doc(doctype, name):
			doc = MagicMock(flags=frappe._dict())
			doc.roles = [frappe._dict(role="System Manager"), frappe._dict(role="Sales Manager")]
			doc.append.side_effect = lambda field, row: doc.roles.append(frappe._dict(row))
			docs[name] = doc
			return doc

		db = MagicMock()
		db.get_value.side_effect = lambda doctype, name, field: 0 if name in disabled else 1
		with (
			patch.object(frappe, "get_all", return_value=list(holders), create=True),
			patch.object(frappe, "get_doc", side_effect=get_doc, create=True),
			patch.object(frappe, "db", db, create=True),
			patch.object(
				frappe, "conf", frappe._dict(murzak_platform_operators=list(operators)), create=True
			),
			patch.object(role_tiers, "ensure_owner_role"),
			patch(
				"murzak_pos.api.payment_gateway_common.get_user_companies",
				side_effect=lambda user: set(companies.get(user, ())),
			),
			patch("builtins.print"),
		):
			patch_module.execute()
		return docs

	def test_owners_bound_to_a_business_move_and_murzak_accounts_stay(self):
		docs = self.run_patch(
			holders=["owner@a.test", "ops@murzak.test", "listed@murzak.test", "left@a.test", "Administrator"],
			companies={"owner@a.test": ["A"], "listed@murzak.test": ["A"], "left@a.test": ["A"]},
			operators=["listed@murzak.test"],
			disabled=["left@a.test"],
		)
		self.assertEqual(sorted(docs), ["owner@a.test"])
		self.assertEqual(
			sorted(r["role"] for r in docs["owner@a.test"].roles), ["Business Owner", "Sales Manager"]
		)
		docs["owner@a.test"].save.assert_called_once_with()


if __name__ == "__main__":
	unittest.main()
