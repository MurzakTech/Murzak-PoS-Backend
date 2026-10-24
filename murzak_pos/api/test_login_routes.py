# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Every sign-in address applies the lockout and the two-step code. Needs no database."""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import auth


class TestTheOlderSignInAddress(unittest.TestCase):
	def setUp(self):
		self.manager = MagicMock(user="owner@shop.test")
		for p in (
			patch.object(
				frappe, "auth", MagicMock(LoginManager=MagicMock(return_value=self.manager)), create=True
			),
			patch.object(frappe, "db", MagicMock(), create=True),
			patch.object(frappe, "local", MagicMock(response={}), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_a_locked_account_is_refused_before_the_password_is_checked(self):
		with (
			patch("murzak_pos.api.two_factor_api.check_not_locked", side_effect=frappe.AuthenticationError),
			self.assertRaises(frappe.AuthenticationError),
		):
			auth.login_user(email="owner@shop.test", password="whatever")
		self.manager.authenticate.assert_not_called()

	def test_an_account_with_two_step_sign_in_must_give_its_code(self):
		asked = {"two_factor_required": True}
		with (
			patch("murzak_pos.api.two_factor_api.check_not_locked"),
			patch("murzak_pos.api.two_factor_api.check_sign_in_code", return_value=asked) as check_code,
			patch("murzak_pos.api.auth_api.generate_oauth_bearer_token") as token,
		):
			self.assertEqual(auth.login_user(email="owner@shop.test", password="right"), asked)
		check_code.assert_called_once_with("owner@shop.test", None)
		token.assert_not_called()

	def test_a_wrong_password_counts_as_a_failure(self):
		self.manager.authenticate.side_effect = frappe.AuthenticationError
		with (
			patch("murzak_pos.api.two_factor_api.check_not_locked"),
			patch("murzak_pos.api.two_factor_api.note_failure") as note_failure,
			self.assertRaises(frappe.AuthenticationError),
		):
			auth.login_user(email="owner@shop.test", password="wrong")
		note_failure.assert_called_once_with("owner@shop.test")


if __name__ == "__main__":
	unittest.main()
