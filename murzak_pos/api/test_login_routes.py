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


class TestRefreshTokens(unittest.TestCase):
	def given_a_token(self, age_days, enabled=1):
		from datetime import datetime, timedelta

		now = datetime(2026, 10, 24, 12, 0)
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, *args, **kwargs: (
			"TOKEN-1" if doctype == "OAuth Bearer Token" else enabled
		)
		token = frappe._dict(user="cashier@shop.test", creation=now - timedelta(days=age_days))
		for p in (
			patch("frappe.utils.now_datetime", return_value=now),
			patch.object(frappe, "session", frappe._dict(user="Guest"), create=True),
			patch.object(frappe, "db", db, create=True),
			patch.object(frappe, "get_doc", return_value=token, create=True),
			patch.object(frappe, "local", MagicMock(response={}), create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)
		self.db = db

	def refresh(self, module):
		return module.refresh_token(refresh_token="r-1")

	def test_a_token_unused_for_more_than_thirty_days_is_refused_and_stays_revoked(self):
		from murzak_pos.api import auth_api

		for module in (auth_api, auth):
			with self.subTest(module=module.__name__):
				self.given_a_token(age_days=31)
				with patch.object(module, "generate_oauth_bearer_token") as new_token:
					with self.assertRaises(frappe.AuthenticationError):
						self.refresh(module)
				new_token.assert_not_called()
				self.db.set_value.assert_called_once_with(
					"OAuth Bearer Token", "TOKEN-1", "status", "Revoked"
				)

	def test_a_disabled_account_gets_no_new_sign_in(self):
		from murzak_pos.api import auth_api

		for module in (auth_api, auth):
			with self.subTest(module=module.__name__):
				self.given_a_token(age_days=1, enabled=0)
				with patch.object(module, "generate_oauth_bearer_token") as new_token:
					with self.assertRaises(frappe.AuthenticationError):
						self.refresh(module)
				new_token.assert_not_called()

	def test_a_recent_token_gives_a_new_sign_in(self):
		from murzak_pos.api import auth_api

		fresh = {
			"access_token": "a",
			"token_type": "Bearer",
			"expires_in": 1,
			"expires_at": "x",
			"refresh_token": "r-2",
		}
		for module in (auth_api, auth):
			with self.subTest(module=module.__name__):
				self.given_a_token(age_days=29)
				with patch.object(module, "generate_oauth_bearer_token", return_value=fresh) as new_token:
					self.assertEqual(self.refresh(module)["refresh_token"], "r-2")
				new_token.assert_called_once_with("cashier@shop.test")
