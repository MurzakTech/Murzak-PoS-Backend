# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for what an owner is told when adding a person is refused for a permission reason.

The create-staff call used to turn every permission refusal into one fixed sentence ("You don't have permission to
create staff members. Please contact your administrator..."), so a refusal such as "You cannot give roles you do not
hold yourself: Sales User" looked like something only an administrator could fix. The real reason now reaches the
screen. No database is needed: every Frappe call is mocked.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import staff_api

REASON = "You cannot give roles you do not hold yourself: Sales User."


class TestWhatAnOwnerIsToldWhenAddingAPersonIsRefused(unittest.TestCase):
	def add_a_cashier(self, role_check):
		db = MagicMock()
		# the company exists, the person does not yet
		db.exists.side_effect = lambda doctype, *a, **k: doctype == "Company"
		with (
			patch.object(frappe, "session", frappe._dict(user="owner@shop-a.test")),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "log_error", create=True),
			patch.object(frappe, "local", MagicMock(response={})),
			patch.object(staff_api, "require_manager"),
			patch.object(staff_api, "resolve_company", return_value="Shop A"),
			patch.object(staff_api, "require_roles_held", side_effect=role_check),
		):
			return staff_api.create_staff_user(
				email="trench@example.com",
				first_name="Uncle",
				last_name="Trench",
				password="a-long-password",
				roles=["Sales User"],
				company="Shop A",
			)

	def test_the_real_reason_reaches_the_owner(self):
		def refuse(roles):
			frappe.throw(REASON, frappe.PermissionError)

		with self.assertRaises(frappe.PermissionError) as refused:
			self.add_a_cashier(refuse)
		self.assertIn("Sales User", str(refused.exception))
		self.assertNotIn("contact your administrator", str(refused.exception))

	def test_a_refusal_with_no_words_still_gets_the_fallback_sentence(self):
		def refuse(roles):
			raise frappe.PermissionError

		with self.assertRaises(frappe.PermissionError) as refused:
			self.add_a_cashier(refuse)
		self.assertIn("You don't have permission to create staff members", str(refused.exception))


if __name__ == "__main__":
	unittest.main()
