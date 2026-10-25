# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for paying a credit note back. No database is needed."""

import unittest

from murzak_pos.api import refund_rules as rules


class OwedCase(unittest.TestCase):
	def test_a_credit_note_owes_the_size_of_its_negative_outstanding(self):
		self.assertEqual(rules.owed(-500), 500.0)
		self.assertEqual(rules.owed("-120.456"), 120.46)

	def test_nothing_owed_is_zero(self):
		for value in (0, None, "", "abc", 25, -0.001):
			with self.subTest(value=value):
				self.assertEqual(rules.owed(value), 0.0)


class AmountToPayCase(unittest.TestCase):
	def test_no_amount_asked_pays_everything_owed(self):
		self.assertEqual(rules.amount_to_pay(-500), 500.0)
		self.assertEqual(rules.amount_to_pay(-500, ""), 500.0)

	def test_a_part_may_be_paid(self):
		self.assertEqual(rules.amount_to_pay(-500, "200.5"), 200.5)

	def test_all_of_it_may_be_asked_for_by_amount(self):
		self.assertEqual(rules.amount_to_pay(-500, 500), 500.0)

	def test_more_than_is_owed_is_refused(self):
		with self.assertRaises(rules.RefundError) as caught:
			rules.amount_to_pay(-500, 500.01)
		self.assertEqual(caught.exception.code, "more_than_owed")
		self.assertIn("500.00", caught.exception.detail)

	def test_nothing_owed_is_refused(self):
		for outstanding in (0, 100, None):
			with self.subTest(outstanding=outstanding), self.assertRaises(rules.RefundError) as caught:
				rules.amount_to_pay(outstanding)
			self.assertEqual(caught.exception.code, "nothing_to_refund")

	def test_a_bad_amount_is_refused(self):
		for bad in (0, -5, "abc", float("nan")):
			with self.subTest(bad=bad), self.assertRaises(rules.RefundError) as caught:
				rules.amount_to_pay(-500, bad)
			self.assertEqual(caught.exception.code, "bad_amount")


class RemarkCase(unittest.TestCase):
	def test_the_remark_names_the_credit_note_and_is_recognised(self):
		remark = rules.remark_for("ACC-SINV-1")
		self.assertEqual(remark, "Till refund: ACC-SINV-1")
		self.assertTrue(rules.is_till_refund_remark(remark))

	def test_other_remarks_are_not_till_refunds(self):
		for other in (None, "", "Amount KES 500 paid to Cust", "A Till refund later"):
			with self.subTest(other=other):
				self.assertFalse(rules.is_till_refund_remark(other))
