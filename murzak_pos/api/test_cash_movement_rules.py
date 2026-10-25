# Copyright (c) 2026, Murzak POS and Contributors
# See license.txt
"""Tests for the rules for cash put into or taken out of the till. No database is needed."""

import unittest

from murzak_pos.api import cash_movement_rules as rules


class CleanCase(unittest.TestCase):
	def test_a_paid_out_for_petty_cash_is_kept_as_asked(self):
		got = rules.clean("Paid Out", "250.50", "Petty cash", "  Tea   and sugar ", "t-1")
		self.assertEqual(
			got,
			{
				"kind": "Paid Out",
				"amount": 250.5,
				"reason": "Petty cash",
				"note": "Tea and sugar",
				"client_id": "t-1",
			},
		)

	def test_a_missing_reason_takes_the_first_one_for_the_kind(self):
		self.assertEqual(rules.clean("Paid In", 100, None)["reason"], "Float top-up")
		self.assertEqual(rules.clean("Paid Out", 100, "")["reason"], "Staff advance")
		self.assertEqual(rules.clean("Safe Drop", 100, None)["reason"], "Bank deposit")

	def test_amounts_must_be_above_zero_and_a_number(self):
		for bad in (0, -5, "abc", None, float("nan"), ""):
			with self.subTest(bad=bad), self.assertRaises(rules.MovementError) as caught:
				rules.clean("Paid In", bad, None)
			self.assertEqual(caught.exception.code, "bad_amount")

	def test_a_huge_amount_is_refused(self):
		with self.assertRaises(rules.MovementError) as caught:
			rules.clean("Paid In", rules.MAX_AMOUNT + 1, None)
		self.assertEqual(caught.exception.code, "too_large")

	def test_amounts_are_rounded_to_cents(self):
		self.assertEqual(rules.clean("Paid In", "10.006", None)["amount"], 10.01)

	def test_an_unknown_kind_is_refused(self):
		with self.assertRaises(rules.MovementError) as caught:
			rules.clean("Refund", 10, None)
		self.assertEqual(caught.exception.code, "bad_kind")

	def test_the_reasons_the_system_writes_itself_are_refused_to_a_request_and_accepted_from_the_system(self):
		for reason in ("Expense", "Customer refund"):
			with self.subTest(reason=reason):
				with self.assertRaises(rules.MovementError) as caught:
					rules.clean("Paid Out", 100, reason)
				self.assertEqual(caught.exception.code, "bad_reason")
				self.assertEqual(rules.clean("Paid Out", 100, reason, allow_system=True)["reason"], reason)

	def test_petty_cash_and_suppliers_are_expenses_so_the_till_does_not_offer_them(self):
		offered = rules.OFFERED_BY_KIND["Paid Out"]
		self.assertNotIn("Petty cash", offered)
		self.assertNotIn("Supplier payment", offered)
		self.assertNotIn("Expense", offered)
		self.assertNotIn("Customer refund", offered)
		# Older recordings with those reasons stay valid
		self.assertEqual(rules.clean("Paid Out", 100, "Petty cash")["reason"], "Petty cash")

	def test_a_reason_that_does_not_fit_the_kind_is_refused(self):
		with self.assertRaises(rules.MovementError) as caught:
			rules.clean("Safe Drop", 10, "Petty cash")
		self.assertEqual(caught.exception.code, "bad_reason")

	def test_other_needs_a_note(self):
		with self.assertRaises(rules.MovementError) as caught:
			rules.clean("Paid Out", 10, "Other", "  ")
		self.assertEqual(caught.exception.code, "note_required")
		self.assertEqual(rules.clean("Paid Out", 10, "Other", "Taxi fare")["note"], "Taxi fare")

	def test_a_long_note_is_cut(self):
		self.assertEqual(len(rules.clean("Paid Out", 10, "Petty cash", "x" * 500)["note"]), rules.MAX_NOTE)

	def test_the_till_reference_must_be_plain(self):
		self.assertIsNone(rules.clean("Paid In", 10, None)["client_id"])
		for bad in ("has space", "semi;colon", "x" * 65):
			with self.subTest(bad=bad), self.assertRaises(rules.MovementError) as caught:
				rules.clean("Paid In", 10, None, None, bad)
			self.assertEqual(caught.exception.code, "bad_client_id")


class WantsTillCase(unittest.TestCase):
	def test_not_saying_means_yes(self):
		for value in (None, "", True, 1, "1", "true", "yes"):
			with self.subTest(value=value):
				self.assertTrue(rules.wants_till(value))

	def test_only_a_clear_no_keeps_an_expense_out_of_the_drawer(self):
		for value in (False, 0, "0", "false", "False", "no", " off "):
			with self.subTest(value=value):
				self.assertFalse(rules.wants_till(value))


class ExceedsTillCase(unittest.TestCase):
	def test_money_in_is_never_blocked(self):
		self.assertFalse(rules.exceeds_till("Paid In", 1_000_000, 10))

	def test_money_out_up_to_the_cash_in_the_till_is_allowed(self):
		self.assertFalse(rules.exceeds_till("Paid Out", 500, 500))
		self.assertFalse(rules.exceeds_till("Safe Drop", 499.99, 500))

	def test_money_out_beyond_the_cash_in_the_till_is_refused(self):
		self.assertTrue(rules.exceeds_till("Paid Out", 500.01, 500))
		self.assertTrue(rules.exceeds_till("Safe Drop", 1, 0))

	def test_a_till_with_no_known_cash_is_not_blocked(self):
		self.assertFalse(rules.exceeds_till("Paid Out", 500, None))


class MovementRowsCase(unittest.TestCase):
	def test_paid_in_is_money_in_and_the_rest_is_money_out(self):
		rows = rules.movement_rows(
			[
				{"kind": "Paid In", "mode_of_payment": "Cash", "amount": 100},
				{"kind": "Paid Out", "mode_of_payment": "Cash", "amount": 30},
				{"kind": "Safe Drop", "mode_of_payment": "Cash", "amount": 50},
				{"kind": "Surprise", "mode_of_payment": "Cash", "amount": 9},
			]
		)
		self.assertEqual([r["payment_type"] for r in rows], ["Receive", "Pay", "Pay"])

	def test_nothing_gives_nothing(self):
		self.assertEqual(rules.movement_rows(None), [])
