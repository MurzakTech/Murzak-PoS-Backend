# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for reading an M-Pesa statement. No database is needed."""

import unittest
from datetime import date

from murzak_pos.api import statement_rules as rules

HEAD = "Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"


class AmountCase(unittest.TestCase):
	def test_amounts_are_read_the_way_people_write_them(self):
		for text, expected in (
			("1,200.50", 1200.5),
			("KES 300", 300.0),
			("45", 45.0),
			("(45.00)", -45.0),
			("-45.00", -45.0),
			("", 0.0),
			("  ", 0.0),
			(None, 0.0),
		):
			with self.subTest(text=text):
				self.assertEqual(rules.parse_amount(text), expected)

	def test_nonsense_is_not_an_amount(self):
		for text in ("abc", "1.2.3", "--"):
			with self.subTest(text=text):
				self.assertIsNone(rules.parse_amount(text))


class MomentCase(unittest.TestCase):
	def test_the_formats_the_portal_and_spreadsheets_use(self):
		expected = date(2026, 10, 9)
		for text in (
			"2026-10-09 14:32:10",
			"2026-10-09 14:32",
			"09-10-2026 14:32:10",
			"09/10/2026 14:32:10",
			"2026-10-09",
			"09/10/2026",
			"09-Oct-2026",
		):
			with self.subTest(text=text):
				self.assertEqual(rules.parse_moment(text), expected)

	def test_a_date_that_is_not_one(self):
		for text in ("", "yesterday", "32/13/2026"):
			self.assertIsNone(rules.parse_moment(text))


class HeadingCase(unittest.TestCase):
	def test_headings_are_compared_by_their_words(self):
		self.assertEqual(rules.normalize_heading("  Receipt No. "), "receipt no")
		self.assertEqual(rules.normalize_heading("Paid In"), "paid in")

	def test_the_columns_are_found_by_heading(self):
		columns = rules.find_columns(
			["Receipt No.", "Completion Time", "Details", "Status", "PAID IN", "withdrawn", "Balance"]
		)
		self.assertEqual(
			columns,
			{"receipt": 0, "completed": 1, "details": 2, "status": 4 - 1, "paid_in": 4, "withdrawn": 5},
		)
		self.assertTrue(rules.is_heading_row(columns))

	def test_a_row_without_a_receipt_column_is_not_the_heading(self):
		self.assertFalse(rules.is_heading_row(rules.find_columns(["Date", "Details", "Paid In"])))
		self.assertFalse(rules.is_heading_row(rules.find_columns(["M-PESA Statement"])))


class ReadCase(unittest.TestCase):
	def test_a_simple_statement(self):
		text = (
			HEAD
			+ 'AB1,2026-10-09 10:00:00,Customer payment,Completed,"1,200.00",,5000\nAB2,2026-10-09 11:00:00,Settlement,Completed,,2000.00,3000\n'
		)
		result = rules.read_statement(text)
		self.assertEqual(result["problems"], [])
		self.assertEqual(
			[(r["receipt"], r["date"], r["deposit"], r["withdrawal"]) for r in result["rows"]],
			[("AB1", date(2026, 10, 9), 1200.0, 0.0), ("AB2", date(2026, 10, 9), 0.0, 2000.0)],
		)

	def test_lines_above_the_heading_are_ignored(self):
		text = "M-PESA Statement\nAccount,174379\n\n" + HEAD + "AB1,2026-10-09,x,Completed,10,,0\n"
		result = rules.read_statement(text)
		self.assertEqual([r["receipt"] for r in result["rows"]], ["AB1"])

	def test_receipt_numbers_are_upper_case_and_a_repeat_is_kept_once(self):
		text = HEAD + "ab1,2026-10-09,x,Completed,10,,0\nAB1,2026-10-09,x,Completed,10,,0\n"
		result = rules.read_statement(text)
		self.assertEqual([r["receipt"] for r in result["rows"]], ["AB1"])
		self.assertEqual(result["repeated_in_file"], 1)

	def test_a_payment_that_did_not_complete_is_left_out(self):
		text = HEAD + "AB1,2026-10-09,x,Failed,10,,0\nAB2,2026-10-09,x,Completed,10,,0\n"
		result = rules.read_statement(text)
		self.assertEqual([r["receipt"] for r in result["rows"]], ["AB2"])
		self.assertEqual(result["skipped_not_completed"], 1)

	def test_lines_that_cannot_be_read_are_reported_in_plain_words(self):
		text = (
			HEAD
			+ ",2026-10-09,x,Completed,10,,0\nAB2,not a date,x,Completed,10,,0\nAB3,2026-10-09,x,Completed,abc,,0\nAB4,2026-10-09,x,Completed,,,0\n"
		)
		result = rules.read_statement(text)
		self.assertEqual(result["rows"], [])
		self.assertEqual(len(result["problems"]), 4)
		self.assertIn("receipt number", result["problems"][0])
		self.assertIn("AB2", result["problems"][1])

	def test_withdrawals_written_as_negative_numbers_are_still_withdrawals(self):
		result = rules.read_statement(HEAD + "AB1,2026-10-09,x,Completed,,-500.00,0\n")
		self.assertEqual((result["rows"][0]["deposit"], result["rows"][0]["withdrawal"]), (0.0, 500.0))

	def test_a_semicolon_file_with_other_column_names(self):
		text = "Transaction ID;Date;Description;Credit;Debit\nXY1;09/10/2026 10:00:00;Pay;300,00;\n".replace(
			"300,00", "300.00"
		)
		result = rules.read_statement(text)
		self.assertEqual([(r["receipt"], r["deposit"]) for r in result["rows"]], [("XY1", 300.0)])

	def test_an_empty_file_or_one_without_the_columns(self):
		for text in ("", "   \n", "a,b,c\n1,2,3\n"):
			with self.subTest(text=text):
				result = rules.read_statement(text)
				self.assertEqual(result["rows"], [])
				self.assertTrue(result["problems"])

	def test_a_byte_order_mark_does_not_hide_the_heading(self):
		result = rules.read_statement("﻿" + HEAD + "AB1,2026-10-09,x,Completed,10,,0\n")
		self.assertEqual(len(result["rows"]), 1)


if __name__ == "__main__":
	unittest.main()
