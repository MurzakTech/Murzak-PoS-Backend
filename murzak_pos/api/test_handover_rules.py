# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the rules of handing a till over (handover_rules.py). No database."""

import unittest

from murzak_pos.api import handover_rules as rules


class CashFloatCase(unittest.TestCase):
	def test_the_float_is_the_counted_cash(self):
		lines = [
			{"mode": "M-Pesa", "key": "mpesa", "counted": 800.0},
			{"mode": "Cash", "key": "cash", "counted": 7290.456},
		]
		self.assertEqual(rules.cash_float(lines), ("Cash", 7290.46))

	def test_a_counted_zero_is_a_float_of_zero(self):
		self.assertEqual(rules.cash_float([{"mode": "Cash", "key": "cash", "counted": 0}]), ("Cash", 0.0))

	def test_an_uncounted_cash_line_gives_no_float(self):
		self.assertEqual(rules.cash_float([{"mode": "Cash", "key": "cash", "counted": None}]), (None, 0.0))

	def test_no_cash_line_gives_no_float(self):
		self.assertEqual(rules.cash_float([{"mode": "M-Pesa", "key": "mpesa", "counted": 5}]), (None, 0.0))
		self.assertEqual(rules.cash_float(None), (None, 0.0))


class RecipientCase(unittest.TestCase):
	def refuse(self, **kw):
		args = {
			"outgoing": "a@x.test",
			"incoming": "b@x.test",
			"exists": True,
			"enabled": True,
			"same_business": True,
		}
		args.update(kw)
		return rules.refuse_recipient(**args)

	def test_a_colleague_may_take_over(self):
		self.assertIsNone(self.refuse())

	def test_nobody_named(self):
		self.assertEqual(self.refuse(incoming=""), "no_such_person")
		self.assertEqual(self.refuse(incoming=None), "no_such_person")

	def test_unknown_person(self):
		self.assertEqual(self.refuse(exists=False), "no_such_person")

	def test_not_the_same_cashier(self):
		self.assertEqual(self.refuse(incoming="a@x.test"), "same_person")

	def test_switched_off_account(self):
		self.assertEqual(self.refuse(enabled=False), "person_disabled")

	def test_other_business(self):
		self.assertEqual(self.refuse(same_business=False), "other_business")


class TextCase(unittest.TestCase):
	def test_note_is_tidied_and_cut(self):
		self.assertEqual(rules.clean_note("  a \n  b  "), "a b")
		self.assertEqual(len(rules.clean_note("x" * 500)), rules.NOTE_LIMIT)
		self.assertEqual(rules.clean_note(None), "")

	def test_line_for_a_matching_count(self):
		text = rules.handover_text("Amina", "Brian", 7350, 0)
		self.assertEqual(text, "Till handed over from Amina to Brian. Cash counted: 7,350.00.")

	def test_line_for_a_short_count_with_note(self):
		text = rules.handover_text("Amina", "Brian", 7300, -50, "lunch")
		self.assertIn("Count was short by 50.00.", text)
		self.assertTrue(text.endswith("Note: lunch"))

	def test_line_for_an_over_count(self):
		self.assertIn("over by 10.00", rules.handover_text("A", "B", 100, 10))
