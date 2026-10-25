# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""
Tests for an expense paid in cash coming out of the till's drawer (expenses_api._take_from_till and _own_open_shift)
that need no database. Every Frappe call is replaced.
"""

import unittest
from unittest.mock import MagicMock, patch

from murzak_pos.api import expenses_api


def entry():
	doc = MagicMock()
	doc.name = "ACC-JV-1"
	return doc


class TakeFromTillCase(unittest.TestCase):
	def take(
		self,
		key="cash",
		from_till=None,
		session=None,
		shift="POS-OPE-1",
		result=None,
		text="Cleaning",
		category="Cleaning Expenses - A",
	):
		result = result or {"success": True, "data": {"cash_in_till": 1900.0}}
		with (
			patch.object(expenses_api, "_own_open_shift", return_value=shift),
			patch("murzak_pos.api.shift_api.record_movement", return_value=result) as record,
		):
			got = expenses_api._take_from_till(
				"Shop A", 100.0, entry(), category, text, key, from_till, session
			)
		return got, record

	def test_cash_comes_out_of_the_drawer_of_the_persons_open_shift(self):
		(till, refused), record = self.take()
		self.assertIsNone(refused)
		self.assertEqual(till, {"shift": "POS-OPE-1", "cash_in_till": 1900.0})
		args = record.call_args.args
		self.assertEqual(args, ("POS-OPE-1", "Paid Out", 100.0, "Expense", "Cleaning", "expense-ACC-JV-1"))
		self.assertTrue(record.call_args.kwargs["system"])

	def test_the_category_is_the_note_when_no_note_was_typed(self):
		_got, record = self.take(text="")
		self.assertEqual(record.call_args.args[4], "Cleaning Expenses - A")

	def test_only_cash_comes_out_of_a_drawer(self):
		for key in ("mpesa", "bank"):
			with self.subTest(key=key):
				got, record = self.take(key=key)
				self.assertEqual(got, (None, None))
				record.assert_not_called()

	def test_a_clear_no_keeps_it_out_of_the_drawer(self):
		for value in (False, 0, "false", "no"):
			with self.subTest(value=value):
				got, record = self.take(from_till=value)
				self.assertEqual(got, (None, None))
				record.assert_not_called()

	def test_no_open_shift_means_nothing_comes_out_of_a_drawer(self):
		got, record = self.take(shift=None)
		self.assertEqual(got, (None, None))
		record.assert_not_called()

	def test_a_shift_named_by_the_manager_is_used(self):
		(till, _refused), record = self.take(session="POS-OPE-9")
		self.assertEqual(till["shift"], "POS-OPE-9")
		self.assertEqual(record.call_args.args[0], "POS-OPE-9")

	def test_a_drawer_that_holds_less_is_a_refusal_and_nothing_is_recorded_as_taken(self):
		refusal = {
			"success": False,
			"error_type": "more_than_in_till",
			"message": "That is more than the till holds.",
		}
		(till, refused), _record = self.take(result=refusal)
		self.assertIsNone(till)
		self.assertEqual(refused["error_type"], "more_than_in_till")


class OwnOpenShiftCase(unittest.TestCase):
	def find(self, rows):
		with (
			patch.object(expenses_api.frappe, "session", MagicMock(user="boss@shop-a.test")),
			patch.object(expenses_api.role_tiers, "get_tier", return_value="owner"),
			patch("murzak_pos.api.till_api._open_sessions", return_value=rows),
		):
			return expenses_api._own_open_shift("Shop A")

	def test_the_persons_own_open_shift_is_found(self):
		rows = [{"name": "A", "user": "amina@shop-a.test"}, {"name": "B", "user": "boss@shop-a.test"}]
		self.assertEqual(self.find(rows), "B")

	def test_another_persons_shift_is_never_taken_by_itself(self):
		self.assertIsNone(self.find([{"name": "A", "user": "amina@shop-a.test"}]))
		self.assertIsNone(self.find([]))
