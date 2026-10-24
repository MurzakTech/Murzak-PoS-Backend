# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""People see messages written for them, never the inside of an unexpected error. Needs no database."""

import unittest
from unittest.mock import patch

import frappe

from murzak_pos.api.error_handler import safe_error_message


class TestSafeErrorMessage(unittest.TestCase):
	def setUp(self):
		for p in (
			patch.object(frappe, "log_error", create=True),
			patch.object(frappe, "get_traceback", return_value="Traceback ...", create=True),
			patch("frappe.translate.get_all_translations", return_value={}),
		):
			p.start()
			self.addCleanup(p.stop)

	def test_messages_written_for_people_are_shown_as_they_are(self):
		for error in (
			frappe.ValidationError("Item Soap has no stock in Main Store"),
			frappe.PermissionError("You do not have access to this Sales Invoice."),
			frappe.DoesNotExistError("Sales Invoice SINV-9 not found"),
		):
			with self.subTest(error=type(error).__name__):
				self.assertEqual(safe_error_message(error), str(error))
		frappe.log_error.assert_not_called()

	def test_an_unexpected_error_is_logged_and_not_shown(self):
		error = KeyError("(1054, \"Unknown column 'tabItem.secret' in 'field list'\")")
		message = safe_error_message(error)
		self.assertNotIn("tabItem", message)
		self.assertIn("Something went wrong", message)
		frappe.log_error.assert_called_once()

	def test_a_duplicate_names_no_database_details(self):
		error = frappe.DuplicateEntryError(
			"Item", "SOAP", "(1062, \"Duplicate entry 'SOAP' for key 'PRIMARY'\")"
		)
		self.assertEqual(safe_error_message(error), "That record already exists.")


if __name__ == "__main__":
	unittest.main()
