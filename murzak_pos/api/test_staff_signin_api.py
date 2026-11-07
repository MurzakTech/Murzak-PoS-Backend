# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for the calls that help an owner get a team member signed in (`staff_signin_api`). They need no database: every Frappe call is
mocked. What matters: only people of the owner's own business can be reached, never an owner or a protected account; a weak password
is refused before anything is written; and a failing email never stops anything and says why in words.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from murzak_pos.api import staff_signin_api as api

STRONG = "Sunrise-Shop7"


class TestWhoCanBeReached(unittest.TestCase):
	def test_an_unknown_or_protected_account_is_refused(self):
		with (
			patch.object(frappe, "db", MagicMock(**{"exists.return_value": False}), create=True),
			self.assertRaises(frappe.DoesNotExistError),
		):
			api._target("nobody@shop.test")
		with self.assertRaises(frappe.DoesNotExistError):
			api._target("Administrator")

	def test_a_person_of_another_business_is_refused(self):
		with (
			patch.object(frappe, "db", MagicMock(**{"exists.return_value": True}), create=True),
			patch.object(api, "_same_business", return_value=False),
			self.assertRaises(frappe.PermissionError),
		):
			api._target("amina@other.test")

	def test_an_owner_cannot_be_changed_by_someone_else(self):
		with (
			patch.object(frappe, "db", MagicMock(**{"exists.return_value": True}), create=True),
			patch.object(api, "_same_business", return_value=True),
			patch.object(api.frappe, "get_roles", return_value=["System Manager"]),
			self.assertRaises(frappe.PermissionError),
		):
			api._target("boss@shop.test")

	def test_a_team_member_of_the_same_business_is_reached_by_their_lowercase_address(self):
		with (
			patch.object(frappe, "db", MagicMock(**{"exists.return_value": True}), create=True),
			patch.object(api, "_same_business", return_value=True),
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
		):
			self.assertEqual(api._target(" Amina@Shop.Test "), "amina@shop.test")


class TestSetStaffPassword(unittest.TestCase):
	def test_a_weak_password_is_refused_before_anything_is_written(self):
		with (
			patch.object(api, "require_manager"),
			patch.object(api, "_target", return_value="amina@shop.test"),
			patch("frappe.utils.password.update_password") as update,
			self.assertRaises(frappe.ValidationError),
		):
			api.set_staff_password("amina@shop.test", "weak")
		update.assert_not_called()

	def test_a_good_password_is_set_ends_other_sign_ins_and_is_not_returned(self):
		with (
			patch.object(api, "require_manager"),
			patch.object(api, "_target", return_value="amina@shop.test"),
			patch("frappe.utils.password.update_password") as update,
			patch("murzak_pos.api.two_factor_api.clear_failures") as clear,
		):
			reply = api.set_staff_password("amina@shop.test", STRONG)
		update.assert_called_once_with("amina@shop.test", STRONG, logout_all_sessions=True)
		clear.assert_called_once_with("amina@shop.test")
		self.assertTrue(reply["success"])
		self.assertNotIn(STRONG, str(reply))


class TestWelcomeEmail(unittest.TestCase):
	def test_without_outgoing_email_it_says_so_and_sends_nothing(self):
		with (
			patch.object(api, "email_ready", return_value=False),
			patch.object(api.frappe, "sendmail") as send,
		):
			result = api.send_welcome("amina@shop.test", "Linda mwananchi")
		self.assertFalse(result["sent"])
		self.assertIn("cannot send email", result["reason"])
		send.assert_not_called()

	def test_a_failure_in_sending_never_raises_and_says_why(self):
		row = MagicMock(email="amina@shop.test", first_name="Amina")
		with (
			patch.object(api, "email_ready", return_value=True),
			patch.object(frappe, "db", MagicMock(**{"get_value.return_value": row}), create=True),
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
			patch.object(api.frappe, "get_request_header", return_value="https://shop.pos.example"),
			patch.object(api.frappe, "sendmail", side_effect=RuntimeError("smtp down")),
			patch.object(api.frappe, "log_error"),
		):
			result = api.send_welcome("amina@shop.test", "Linda mwananchi")
		self.assertFalse(result["sent"])
		self.assertIn("could not be sent", result["reason"])

	def test_when_it_works_it_goes_to_the_persons_address_without_a_password(self):
		row = MagicMock(email="amina@shop.test", first_name="Amina")
		with (
			patch.object(api, "email_ready", return_value=True),
			patch.object(frappe, "db", MagicMock(**{"get_value.return_value": row}), create=True),
			patch.object(api.frappe, "get_roles", return_value=["Sales User"]),
			patch.object(api.frappe, "get_request_header", return_value="https://shop.pos.example"),
			patch.object(api.frappe, "sendmail") as send,
		):
			result = api.send_welcome("amina@shop.test", "Linda mwananchi")
		self.assertTrue(result["sent"])
		kwargs = send.call_args.kwargs
		self.assertEqual(kwargs["recipients"], ["amina@shop.test"])
		self.assertIn("https://shop.pos.example", kwargs["message"])
		self.assertNotIn("password:", kwargs["message"].lower())
